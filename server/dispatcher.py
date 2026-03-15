"""dispatcher.py -- Core dispatcher module for FORGE Leroy.

Phase 3a of the Dispatcher implementation: Routing + Dependency Gating.

Intercepts large specs, creates containers with vehicles, and manages
dependency-gated execution. Decides when to slice, creates vehicles,
holds dependents, and releases them on predecessor completion.

IC-7: Per-container locks prevent concurrent mutation of the same container.
"""

import json
import logging
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add mcp/ to sys.path so spec_analyzer is importable without package name collision
# (mirrors the pattern used in spec_slicer.py and mcp/leroy_client.py)
_LEROY_ROOT = Path(__file__).parent.parent
_MCP_DIR = str(_LEROY_ROOT / "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from spec_analyzer import TypedIR, extract_typed_ir  # noqa: E402  # type: ignore[import]

from container_store import ContainerStore, ContainerStatus
from spec_slicer import slice_spec, VehicleSpec

logger = logging.getLogger("leroy-dispatcher")

# Vehicle states considered "terminal done" (mirrors container_store._DONE_STATES)
_DONE_STATES = {"COMPLETED_UNVERIFIED", "COMPLETED_VERIFIED", "PERSISTED", "ARCHIVED"}


class Dispatcher:
    """Core dispatcher: slices large specs into vehicle tasks and manages
    dependency-gated execution across containers.

    Thread-safety: per-container locks (IC-7) prevent concurrent mutation
    of the same container's vehicle list and dependency graph.
    """

    # Machines where the A2A server runs locally and can use the in-process task queue.
    # All other targets are remote -- vehicles stay as 'pending' for remote workers to poll.
    LOCAL_MACHINES = {"haze"}

    def __init__(self, container_store, task_queue, task_meta, state_machine,
                 broadcast_fn=None):
        self._store = container_store        # ContainerStore instance
        self._queue = task_queue             # TaskQueue instance
        self._meta = task_meta               # PersistentTaskDict
        self._sm = state_machine             # TaskStateMachine
        self._locks: dict[str, threading.Lock] = {}  # per-container locks (IC-7)
        self._broadcast_fn = broadcast_fn    # Optional: _broadcast_task_update_sync from server.py

    # ------------------------------------------------------------------
    # Lock management (IC-7)
    # ------------------------------------------------------------------

    def _get_lock(self, container_id: str) -> threading.Lock:
        """Get or create the per-container lock."""
        if container_id not in self._locks:
            self._locks[container_id] = threading.Lock()
        return self._locks[container_id]

    # ------------------------------------------------------------------
    # Target-aware vehicle activation
    # ------------------------------------------------------------------

    def _make_vehicle_runnable(self, vid: str, spec: str, target: str,
                                priority: str = "normal") -> None:
        """Make a vehicle runnable: enqueue locally or set pending for remote worker.

        Local machines (haze): push into the in-process TaskQueue for immediate execution.
        Remote machines (kush, halo, studio, etc.): set status to 'pending' in task_meta
        so the remote worker can poll GET /tasks?status=pending&target=<machine> and pick it up.
        """
        if target in self.LOCAL_MACHINES:
            self._queue.enqueue(vid, spec, priority=priority, target_machine=target)
        else:
            # Remote: flip status to pending for worker polling
            if vid in self._meta:
                meta = dict(self._meta[vid])
                meta["status"] = "pending"
                self._meta[vid] = meta
                logger.info(
                    "Dispatcher: vehicle %s set pending for remote worker (target=%s)",
                    vid[:8], target,
                )
                if self._broadcast_fn:
                    try:
                        self._broadcast_fn(vid)
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Threshold gate
    # ------------------------------------------------------------------

    def should_dispatch(self, typed_ir: TypedIR, spec_text: str) -> bool:
        """Return True if this spec should be sliced into vehicles.

        Threshold: complexity > 15 OR spec_text length > 5000 chars.
        """
        return typed_ir.complexity > 15 or len(spec_text) > 5000

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def dispatch(
        self,
        spec_text: str,
        typed_ir: TypedIR,
        task_id: str,
        priority: str = "normal",
        target_machine: str = "haze",
    ) -> str | None:
        """Main entry point. Slice spec and create container with vehicles.

        Steps:
        1. slice_spec -> list[VehicleSpec]
        2. If empty list: return None (fail-open, caller proceeds normally)
        3. Create container (task_id == container_id)
        4. Create per-container lock
        5. Create vehicle task metadata with parent_id and vehicle_index
        6. Build and store dependency map
        7. Enqueue vehicles with empty depends_on at container priority
        8. Store pending vehicles (those with deps) in container
        9. Set container status IN_FLIGHT
        10. Return container_id

        Returns:
            container_id on success, None to fall back to normal enqueue.
        """
        # Step 1: Slice
        vehicles = slice_spec(spec_text, typed_ir)

        # Step 2: Fail-open if slicer returns no vehicles
        if not vehicles:
            logger.info(
                "Dispatcher: slice_spec returned 0 vehicles for task %s -- fail-open",
                task_id,
            )
            return None

        logger.info(
            "Dispatcher: sliced task %s into %d vehicles", task_id, len(vehicles)
        )

        # Step 3: Create container
        container_id = task_id
        self._store.create_container(
            container_id=container_id,
            spec_text=spec_text,
            typed_ir={"complexity": typed_ir.complexity},
            priority=1,
        )

        # Step 4: Per-container lock
        lock = self._get_lock(container_id)

        with lock:
            # Step 5: Create vehicle task metadata
            # Maps vehicle_index -> vehicle_task_id for dependency resolution
            index_to_task_id: dict[int, str] = {}

            for vehicle in vehicles:
                vehicle_task_id = str(uuid.uuid4())
                index_to_task_id[vehicle.vehicle_index] = vehicle_task_id

                # Vehicles with deps start as 'blocked' to prevent remote workers
                # from picking them up before dependencies complete.
                # Vehicles with no deps start as 'pending' (ready to run).
                has_deps = bool(vehicle.depends_on)
                self._meta[vehicle_task_id] = {
                    "task_id": vehicle_task_id,
                    "spec": vehicle.spec_text,
                    "status": "blocked" if has_deps else "pending",
                    "result": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "completed_at": None,
                    "parent_id": container_id,
                    "vehicle_index": vehicle.vehicle_index,
                    "target": target_machine,
                    "builder_prompt_version": self._meta.get(task_id, {}).get(
                        "builder_prompt_version", ""
                    ),
                }

                # Register vehicle in container
                self._store.add_vehicle(container_id, vehicle_task_id)

                logger.debug(
                    "Dispatcher: created vehicle %s (index=%d, deps=%s) for container %s",
                    vehicle_task_id,
                    vehicle.vehicle_index,
                    vehicle.depends_on,
                    container_id,
                )

            # Step 6: Build dependency graph
            # Structure: {vehicle_task_id: [vehicle_indexes it depends on]}
            dep_graph: dict[str, list[int]] = {}
            for vehicle in vehicles:
                vid = index_to_task_id[vehicle.vehicle_index]
                dep_graph[vid] = list(vehicle.depends_on)

            # Also store: vehicle_index -> vehicle_task_id mapping for lookup
            # (persisted as part of the container metadata via dependency_graph_json)
            # We embed index_to_task_id in the graph under a sentinel key.
            serializable_graph = {
                "__index_map__": {str(k): v for k, v in index_to_task_id.items()},
                **{vid: deps for vid, deps in dep_graph.items()},
            }
            self._store.update_container(
                container_id,
                dependency_graph_json=serializable_graph,
            )

            # Step 7: Enqueue vehicles with no dependencies (ready to run)
            ready_ids: list[str] = []
            pending_ids: list[str] = []

            for vehicle in vehicles:
                vid = index_to_task_id[vehicle.vehicle_index]
                if not vehicle.depends_on:
                    ready_ids.append(vid)
                else:
                    pending_ids.append(vid)

            for vid in ready_ids:
                vehicle_spec = next(
                    v for v in vehicles if index_to_task_id[v.vehicle_index] == vid
                )
                self._make_vehicle_runnable(
                    vid, vehicle_spec.spec_text, target_machine, priority=priority,
                )
                logger.info(
                    "Dispatcher: ready vehicle %s activated (target=%s, container %s)",
                    vid, target_machine, container_id,
                )

            # Step 8: Store pending vehicles
            if pending_ids:
                self._store.update_container(
                    container_id,
                    pending_vehicle_ids_json=pending_ids,
                )
                logger.info(
                    "Dispatcher: %d vehicle(s) pending deps in container %s",
                    len(pending_ids),
                    container_id,
                )

            # Step 9: Set container status to IN_FLIGHT
            self._store.set_status(container_id, ContainerStatus.IN_FLIGHT)

        logger.info(
            "Dispatcher: container %s IN_FLIGHT (%d ready, %d pending)",
            container_id,
            len(ready_ids),
            len(pending_ids),
        )

        # Step 10: Return container_id
        return container_id

    # ------------------------------------------------------------------
    # Vehicle completion handler
    # ------------------------------------------------------------------

    def handle_vehicle_completed(self, vehicle_task_id: str) -> None:
        """Called when a vehicle transitions to COMPLETED_UNVERIFIED.

        Steps:
        1. Look up container via container_store
        2. If None: return (standalone task)
        3. Under per-container lock:
           a. Find vehicles whose depends_on included this vehicle's index
           b. For each unblocked: enqueue at container priority
           c. Check if ALL vehicles are done; if yes, log reconvergence note
        """
        # Step 1: Look up container
        container = self._store.get_container_for_vehicle(vehicle_task_id)
        if container is None:
            return  # standalone task, not managed by dispatcher

        container_id = container["container_id"]
        lock = self._get_lock(container_id)

        with lock:
            # Reload container for freshest state
            container = self._store.get_container(container_id)
            if container is None:
                return

            # Get vehicle metadata to find this vehicle's index
            vehicle_meta = self._meta.get(vehicle_task_id) or {}
            completed_index = vehicle_meta.get("vehicle_index")
            if completed_index is None:
                logger.warning(
                    "Dispatcher: vehicle %s has no vehicle_index in meta -- skipping dep check",
                    vehicle_task_id,
                )
                return

            logger.info(
                "Dispatcher: vehicle %s (index=%d) completed in container %s",
                vehicle_task_id,
                completed_index,
                container_id,
            )

            # Load dependency graph
            dep_graph = container.get("dependency_graph_json") or {}
            if isinstance(dep_graph, str):
                try:
                    dep_graph = json.loads(dep_graph)
                except json.JSONDecodeError:
                    dep_graph = {}

            # Extract index_to_task_id map
            index_map_raw = dep_graph.get("__index_map__", {})
            index_to_task_id: dict[int, str] = {
                int(k): v for k, v in index_map_raw.items()
            }

            # Get all vehicle task_ids
            vehicle_ids = container.get("vehicle_ids_json") or []
            if isinstance(vehicle_ids, str):
                try:
                    vehicle_ids = json.loads(vehicle_ids)
                except json.JSONDecodeError:
                    vehicle_ids = []

            # Find pending vehicles whose depends_on includes completed_index
            pending_ids = container.get("pending_vehicle_ids_json") or []
            if isinstance(pending_ids, str):
                try:
                    pending_ids = json.loads(pending_ids)
                except json.JSONDecodeError:
                    pending_ids = []

            newly_enqueued: list[str] = []

            for vid in list(pending_ids):
                vid_deps = dep_graph.get(vid, [])
                if completed_index not in vid_deps:
                    continue

                # Check if ALL of this vehicle's deps are now done
                all_deps_done = True
                for dep_index in vid_deps:
                    dep_task_id = index_to_task_id.get(dep_index)
                    if dep_task_id is None:
                        logger.warning(
                            "Dispatcher: no task_id for dep_index=%d in container %s",
                            dep_index,
                            container_id,
                        )
                        all_deps_done = False
                        break
                    dep_meta = self._meta.get(dep_task_id) or {}
                    dep_state = dep_meta.get("v2_state", "") or dep_meta.get("status", "")
                    if dep_state not in _DONE_STATES:
                        all_deps_done = False
                        break

                if all_deps_done:
                    # Activate this vehicle on its target machine
                    vid_meta = self._meta.get(vid) or {}
                    vid_spec = vid_meta.get("spec", "")
                    vid_target = vid_meta.get("target", "haze")
                    self._make_vehicle_runnable(vid, vid_spec, vid_target)
                    newly_enqueued.append(vid)
                    logger.info(
                        "Dispatcher: unblocked vehicle %s in container %s (target=%s, deps satisfied)",
                        vid, container_id, vid_target,
                    )

            # Remove newly enqueued from pending list
            if newly_enqueued:
                updated_pending = [v for v in pending_ids if v not in newly_enqueued]
                self._store.update_container(
                    container_id,
                    pending_vehicle_ids_json=updated_pending,
                )

            # Check if ALL vehicles are done (reconvergence gate)
            all_done = True
            for vid in vehicle_ids:
                vmeta = self._meta.get(vid) or {}
                vstate = vmeta.get("v2_state", "") or vmeta.get("status", "")
                if vstate not in _DONE_STATES:
                    all_done = False
                    break

            if all_done:
                self.reconverge(container_id)

    # ------------------------------------------------------------------
    # Reconvergence
    # ------------------------------------------------------------------

    def reconverge(self, container_id: str) -> None:
        """Collect vehicle results, aggregate metrics, persist, notify PM.

        Runs under the per-container lock (caller already holds it).
        """
        print(f"STARTING RECONVERGE for container {container_id[:8]}", flush=True)
        vehicle_ids = self._store.get_vehicles(container_id)
        results = []
        any_failed = False

        for vid in vehicle_ids:
            meta = self._meta.get(vid, {})
            v2_state = meta.get("v2_state", "")
            if v2_state in ("FAILED_RETRYABLE", "ESCALATED", "BLOCKED"):
                any_failed = True
            results.append({
                "task_id": vid,
                "state": v2_state,
                "result": meta.get("result", ""),
                "vehicle_index": meta.get("vehicle_index", 0),
            })
        results.sort(key=lambda r: r["vehicle_index"])

        print(f"RECONVERGE: {len(results)} vehicles, any_failed={any_failed}", flush=True)

        if any_failed:
            self._store.set_status(container_id, ContainerStatus.NEEDS_DECISION)
            failed_vids = [
                r["task_id"][:8]
                for r in results
                if r["state"] in ("FAILED_RETRYABLE", "ESCALATED", "BLOCKED")
            ]
            failure_summary = (
                f"Reconvergence blocked: {len(failed_vids)} vehicle(s) failed: {failed_vids}"
            )
            logger.warning(
                "Reconvergence blocked: container %s has %d failed vehicles: %s",
                container_id[:8],
                len(failed_vids),
                failed_vids,
            )
            # Gap 1: Promote parent task to failed so dashboard reflects reality
            parent_meta = self._meta.get(container_id) or {}
            parent_meta["status"] = "failed"
            parent_meta["result"] = failure_summary
            parent_meta["completed_at"] = datetime.now(timezone.utc).isoformat()
            self._meta[container_id] = parent_meta
            if self._broadcast_fn:
                try:
                    self._broadcast_fn(container_id)
                except Exception as _be:
                    logger.warning("Reconvergence: broadcast failed: %s", _be)
            try:
                import agent_bus  # noqa: PLC0415
                agent_bus.send({
                    "from": "leroy",
                    "to": "pm",
                    "type": "action_required",
                    "task_id": container_id,
                    "content": (
                        f"Container {container_id} reconvergence blocked. "
                        f"{len(failed_vids)} vehicle(s) failed: {failed_vids}. "
                        "Manual decision needed."
                    ),
                    "requires_response": True,
                })
            except Exception as e:
                logger.warning("Reconvergence: failed to notify PM: %s", e)
            print("RECONVERGE: blocked -- NEEDS_DECISION set, parent task marked failed", flush=True)
            return

        # All vehicles passed -- aggregate metrics
        total_duration = 0
        total_cost = 0.0
        total_input = 0
        total_output = 0

        try:
            from task_db import plan_store  # noqa: PLC0415
            if plan_store:
                for r in results:
                    plan = plan_store.get_plan_by_task(r["task_id"])
                    if plan:
                        total_duration += (plan.get("duration_seconds") or 0)
                        total_cost += (plan.get("estimated_cost_usd") or 0)
                        total_input += (plan.get("token_usage_input") or 0)
                        total_output += (plan.get("token_usage_output") or 0)

                # Update master plan for the container
                plan_store.update_outcome(
                    task_id=container_id,
                    status="completed_unverified",
                    duration_seconds=total_duration,
                    estimated_cost_usd=total_cost,
                    token_usage_input=total_input,
                    token_usage_output=total_output,
                )
        except Exception as e:
            logger.warning("Reconvergence: metrics aggregation failed: %s", e)

        print(
            f"RECONVERGE: metrics aggregated -- duration={total_duration}s cost=${total_cost:.4f}",
            flush=True,
        )

        # Gap 2: Git merge for parallel vehicles with worktrees
        # Runs BEFORE marking container COMPLETED (spec requirement)
        vehicles_with_worktrees = []
        for r in results:
            vmeta = self._meta.get(r["task_id"]) or {}
            wp = vmeta.get("worktree_path")
            branch = vmeta.get("worktree_branch")
            if wp and branch and os.path.exists(wp):
                vehicles_with_worktrees.append({
                    "vehicle_index": r["vehicle_index"],
                    "task_id": r["task_id"],
                    "worktree_path": wp,
                    "branch": branch,
                })

        if vehicles_with_worktrees:
            import subprocess  # noqa: PLC0415
            vehicles_with_worktrees.sort(key=lambda v: v["vehicle_index"])
            repo_root = str(Path(__file__).parent.parent)
            merge_errors = []
            merged_paths: list[str] = []

            for v in vehicles_with_worktrees:
                merge_result = subprocess.run(
                    [
                        "git", "merge", "--no-ff", v["branch"],
                        "-m", f"Merge vehicle {v['vehicle_index']}",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=repo_root,
                )
                if merge_result.returncode != 0:
                    merge_errors.append({
                        "vehicle_index": v["vehicle_index"],
                        "branch": v["branch"],
                        "error": merge_result.stderr[:500],
                    })
                    logger.warning(
                        "Reconvergence: merge conflict for vehicle %d (branch %s) in container %s",
                        v["vehicle_index"],
                        v["branch"],
                        container_id[:8],
                    )
                else:
                    merged_paths.append(v["worktree_path"])
                    logger.info(
                        "Reconvergence: merged vehicle %d branch %s in container %s",
                        v["vehicle_index"],
                        v["branch"],
                        container_id[:8],
                    )

            # If any merge conflicts: abort to NEEDS_DECISION and return early
            if merge_errors:
                conflict_detail = json.dumps(merge_errors)
                self._store.set_status(container_id, ContainerStatus.NEEDS_DECISION)
                parent_meta = self._meta.get(container_id) or {}
                parent_meta["status"] = "failed"
                parent_meta["result"] = f"Git merge conflicts during reconvergence: {conflict_detail}"
                parent_meta["completed_at"] = datetime.now(timezone.utc).isoformat()
                self._meta[container_id] = parent_meta
                if self._broadcast_fn:
                    try:
                        self._broadcast_fn(container_id)
                    except Exception as _be:
                        logger.warning("Reconvergence: broadcast failed: %s", _be)
                logger.warning(
                    "Reconvergence: merge conflicts in container %s -- NEEDS_DECISION: %s",
                    container_id[:8],
                    conflict_detail,
                )
                print(
                    f"RECONVERGE: merge conflicts -- NEEDS_DECISION set, parent task marked failed",
                    flush=True,
                )
                return

            # Clean up successfully merged worktrees
            for wp in merged_paths:
                try:
                    subprocess.run(
                        ["git", "worktree", "remove", wp],
                        capture_output=True,
                        cwd=repo_root,
                    )
                except Exception as e:
                    logger.warning("Reconvergence: failed to remove worktree %s: %s", wp, e)

            logger.info(
                "Reconvergence: merged and cleaned %d worktree branches for container %s",
                len(merged_paths),
                container_id[:8],
            )
            print(
                f"RECONVERGE: merged {len(merged_paths)} worktree branch(es) for container {container_id[:8]}",
                flush=True,
            )

        # Combine results for single brain persist
        combined = "\n\n---\n\n".join([
            f"## Vehicle {r['vehicle_index'] + 1}\n{(r['result'] or '')[:3000]}"
            for r in results
        ])
        self._meta[container_id] = self._meta.get(container_id, {})
        self._meta[container_id]["result"] = combined
        self._meta[container_id]["status"] = "completed"
        self._meta[container_id]["completed_at"] = datetime.now(timezone.utc).isoformat()

        # Gap 1: Broadcast SSE so dashboard sees the parent task status change in real-time
        if self._broadcast_fn:
            try:
                self._broadcast_fn(container_id)
            except Exception as _be:
                logger.warning("Reconvergence: broadcast failed: %s", _be)

        # Single PM notification
        try:
            from agent_bus import agent_bus  # noqa: PLC0415
            agent_bus.send({
                "from": "leroy",
                "to": "pm",
                "type": "deliverable_ready",
                "task_id": container_id,
                "content": (
                    f"Container {container_id} completed. "
                    f"{len(results)} vehicles all passed. "
                    f"Duration: {total_duration}s, Cost: ${total_cost:.4f}"
                ),
                "requires_response": False,
            })
        except Exception as e:
            logger.warning("Reconvergence: failed to notify PM: %s", e)

        # Update container status
        self._store.set_status(container_id, ContainerStatus.COMPLETED)
        self._store.update_container(
            container_id,
            reconvergence_record_json=json.dumps({
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "vehicle_count": len(results),
                "total_duration": total_duration,
                "total_cost": total_cost,
            }),
        )
        logger.info(
            "Reconvergence complete: container %s, %d vehicles, %ds, $%.4f",
            container_id[:8],
            len(results),
            total_duration,
            total_cost,
        )
        print(
            f"RECONVERGE COMPLETE: container {container_id[:8]}, "
            f"{len(results)} vehicles, {total_duration}s, ${total_cost:.4f}",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Container abort
    # ------------------------------------------------------------------

    def abort_container(self, container_id: str) -> None:
        """Cancel container and clean up all child worktrees."""
        print(f"ABORT CONTAINER: {container_id[:8]}", flush=True)
        self._store.set_status(container_id, ContainerStatus.FAILED)
        vehicle_ids = self._store.get_vehicles(container_id)
        cleaned = 0
        for vid in vehicle_ids:
            meta = self._meta.get(vid, {})
            wp = meta.get("worktree_path")
            if wp and os.path.exists(wp):
                try:
                    import subprocess  # noqa: PLC0415
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", wp],
                        capture_output=True,
                        cwd=str(Path(__file__).parent.parent),
                    )
                    cleaned += 1
                except Exception as e:
                    logger.warning("Abort: failed to remove worktree %s: %s", wp, e)
        logger.info(
            "Container %s aborted. %d worktrees cleaned.", container_id[:8], cleaned
        )
        print(f"ABORT COMPLETE: container {container_id[:8]}, {cleaned} worktrees cleaned", flush=True)

    # ------------------------------------------------------------------
    # Vehicle failure handler
    # ------------------------------------------------------------------

    def handle_vehicle_failed(self, vehicle_task_id: str, reason: str = "") -> None:
        """Called when a vehicle transitions to FAILED_RETRYABLE.

        Steps:
        1. Look up container
        2. If None: return
        3. Under lock: increment_retry
        4. If count <= 1: re-enqueue (silent auto-retry)
        5. If count > 1: set container NEEDS_DECISION, log
        """
        container = self._store.get_container_for_vehicle(vehicle_task_id)
        if container is None:
            return

        container_id = container["container_id"]
        lock = self._get_lock(container_id)

        with lock:
            retry_count = self._store.increment_retry(container_id, vehicle_task_id)

            if retry_count <= 1:
                # Silent auto-retry: re-activate the vehicle on its target machine
                vid_meta = self._meta.get(vehicle_task_id) or {}
                vid_spec = vid_meta.get("spec", "")
                vid_target = vid_meta.get("target", "haze")
                self._make_vehicle_runnable(vehicle_task_id, vid_spec, vid_target)
                logger.info(
                    "Dispatcher: vehicle %s auto-retry (attempt %d) in container %s (reason: %s)",
                    vehicle_task_id,
                    retry_count,
                    container_id,
                    reason,
                )
            else:
                # Second failure: needs human decision
                self._store.set_status(container_id, ContainerStatus.NEEDS_DECISION)
                logger.warning(
                    "Dispatcher: vehicle %s failed %d times in container %s -- "
                    "container NEEDS_DECISION (reason: %s)",
                    vehicle_task_id,
                    retry_count,
                    container_id,
                    reason,
                )
                # Gap 1: Promote parent task to failed so dashboard reflects reality
                failure_detail = (
                    f"Vehicle {vehicle_task_id[:8]} failed {retry_count} times "
                    f"(reason: {reason or 'unknown'})"
                )
                parent_meta = self._meta.get(container_id) or {}
                parent_meta["status"] = "failed"
                parent_meta["result"] = failure_detail
                parent_meta["completed_at"] = datetime.now(timezone.utc).isoformat()
                self._meta[container_id] = parent_meta
                if self._broadcast_fn:
                    try:
                        self._broadcast_fn(container_id)
                    except Exception as _be:
                        logger.warning("Dispatcher: broadcast failed: %s", _be)

    # ------------------------------------------------------------------
    # Vehicle blocked handler
    # ------------------------------------------------------------------

    def handle_vehicle_blocked(self, vehicle_task_id: str, reason: str = "") -> None:
        """Called when a vehicle transitions to BLOCKED.

        Steps:
        1. Look up container
        2. If None: return
        3. Under lock: set container NEEDS_DECISION, log
        """
        container = self._store.get_container_for_vehicle(vehicle_task_id)
        if container is None:
            return

        container_id = container["container_id"]
        lock = self._get_lock(container_id)

        with lock:
            self._store.set_status(container_id, ContainerStatus.NEEDS_DECISION)
            logger.warning(
                "Dispatcher: vehicle %s BLOCKED in container %s -- "
                "container NEEDS_DECISION (reason: %s)",
                vehicle_task_id,
                container_id,
                reason,
            )
            # Gap 1: Promote parent task to failed so dashboard reflects reality
            block_detail = (
                f"Vehicle {vehicle_task_id[:8]} BLOCKED "
                f"(reason: {reason or 'unknown'})"
            )
            parent_meta = self._meta.get(container_id) or {}
            parent_meta["status"] = "failed"
            parent_meta["result"] = block_detail
            parent_meta["completed_at"] = datetime.now(timezone.utc).isoformat()
            self._meta[container_id] = parent_meta
            if self._broadcast_fn:
                try:
                    self._broadcast_fn(container_id)
                except Exception as _be:
                    logger.warning("Dispatcher: broadcast failed: %s", _be)
