"""Leroy A2A Server.

Google A2A protocol server for PM-to-Leroy task lifecycle.
When a spec arrives via A2A, spawns `claude -p` to execute it automatically.
Custom endpoints for task status and management.
Separate health server on HEALTH_PORT.

Route handlers are organized into separate modules:
  - routes_tasks.py    -- Task CRUD, subtasks, logs, streaming
  - routes_messages.py -- PM messaging and generic agent bus
  - routes_ops.py      -- Agents, activity, proposals, plans, quality, infra
  - routes_admin.py    -- Health, persistence gateway, hooks
  - server_state.py    -- Shared globals, broadcast helpers, auth
"""
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler

import config
import auth
import agent_bus
import task_db
import server_state as state
from state_machine import TaskStateMachine, TaskState, IllegalTransitionError
from retry_budget import RetryBudget
from task_events import register_all_handlers
from pm_autonomy import PMActionStore
from task_queue import TaskQueue
from agent_bus import WebhookRegistry
from task_analytics import (
    validate_criteria, detect_hallucination,
    make_verification_decision,
)
from dispatcher import Dispatcher
from container_store import ContainerStatus
from execution import (
    _execute_task, LeroyExecutor, agent_card, spec_skill,
    _active_pids, LOGS_DIR, _TERMINAL_STATUSES, WORK_DIR,
    _STUCK_CHECK_INTERVAL, _STUCK_THRESHOLD,
)
import execution

# Route handler imports
from routes_tasks import (
    tasks_pending, tasks_complete, task_accept, tasks_list, task_detail,
    task_cancel, task_force_fail, task_archive, task_unarchive, task_review,
    task_delete, subtask_update, subtask_list, task_messages, tasks_stream,
    task_logs, task_logs_stream,
)
from routes_messages import (
    pm_messages_receive, pm_messages_response_poll, pm_messages_respond,
    pm_messages_pending, pm_messages_all, bus_send, bus_list, bus_get,
    bus_respond, bus_read, bus_agents, bus_poll_response,
)
from routes_ops import (
    agents_list, agent_heartbeat, agent_delete, activity_create,
    activity_list, activity_stream, proposals_create, proposals_list,
    proposals_approve, proposals_reject, ideas_create, ideas_promote,
    specs_list, plans_list, plans_detail, plans_report, plans_cost,
    plans_subsystem_health, plans_brain_gaps, validate_task_criteria,
    drift_detection, improvement_full, improvement_patterns,
    improvement_thresholds, improvement_templates, improvement_suggestions,
    improvement_baseline, quality_score_task, quality_metrics_endpoint,
    queue_status, queue_tasks, webhook_register, webhook_unregister,
    webhook_metrics, webhook_list, pm_auto_approve_check, pm_actions_outcome,
    pm_actions_list, pm_autonomy_evaluate, pm_autonomy_status,
    knowledge_governance_stats, knowledge_prune, brain_health, infra_status,
    _SEED_AGENTS,
)
from routes_admin import (
    health, admin_circuit_reset, http_persist, http_persist_append,
    http_persist_last_get, http_persist_last_post, hooks_tool_use,
    hooks_subagent, hooks_events_list, hooks_events_stream,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log_level = os.environ.get("LEROY_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    stream=sys.stderr,
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("leroy-a2a")


# ---------------------------------------------------------------------------
# Health server (separate port)
# ---------------------------------------------------------------------------
health_app = Starlette(routes=[
    Route("/health", health),
    Route("/persist/last", http_persist_last_get, methods=["GET"]),
    Route("/persist/last", http_persist_last_post, methods=["POST"]),
    Route("/persist/append", http_persist_append, methods=["POST"]),
    Route("/persist", http_persist, methods=["POST"]),
])


# ---------------------------------------------------------------------------
# Stuck task detector (background thread)
# ---------------------------------------------------------------------------
def _stuck_task_detector() -> None:
    """Background thread: detect tasks stuck in 'working' after all subtasks complete."""
    logger.info("Stuck task detector running")
    while True:
        time.sleep(_STUCK_CHECK_INTERVAL)
        try:
            for task_id, meta in list(state._task_meta.items()):
                if meta.get("status") != "working":
                    continue

                # Check 1: all subtasks completed but parent still working
                subtasks = state._subtask_store.get(task_id) if state._subtask_store else []
                if subtasks and all(st.get("status") in ("completed", "failed") for st in subtasks):
                    # Skip if we already tried to auto-complete this task
                    if meta.get("_stuck_resolved"):
                        continue
                    last_subtask_time = max(
                        (st.get("completed_at", "") for st in subtasks),
                        default=""
                    )
                    if last_subtask_time:
                        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_subtask_time)).total_seconds()
                        if elapsed > _STUCK_THRESHOLD:
                            logger.warning(
                                "STUCK TASK DETECTED: %s -- all %d subtasks done, parent still working for %ds. "
                                "PID: %s, last_activity: %s",
                                task_id, len(subtasks), int(elapsed),
                                _active_pids.get(task_id, "none (interactive session)"),
                                meta.get("last_activity", "unknown"),
                            )
                            now_iso = datetime.now(timezone.utc).isoformat()
                            meta["_stuck_detected_at"] = now_iso
                            meta["_stuck_reason"] = f"All {len(subtasks)} subtasks done, parent working for {int(elapsed)}s"

                            # Auto-resolve: kill the stuck process (if any) and
                            # mark the task completed. All subtasks finished
                            # successfully -- the stall is an infrastructure bug
                            # (e.g. orphaned pipe holder), not a work failure.
                            pid = _active_pids.get(task_id)
                            if pid:
                                try:
                                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                                    logger.info("STUCK TASK %s: sent SIGTERM to process group (PID %d)", task_id, pid)
                                except (ProcessLookupError, OSError) as kill_err:
                                    logger.info("STUCK TASK %s: SIGTERM skipped (%s)", task_id, kill_err)
                                _active_pids.pop(task_id, None)

                            # Write back through PersistentTaskDict so it persists to SQLite.
                            # meta from items() is a plain dict copy -- must use state._task_meta[task_id].
                            tracked = state._task_meta[task_id]
                            tracked["status"] = "completed"
                            tracked["completed_at"] = now_iso
                            if not tracked.get("result"):
                                tracked["result"] = (
                                    f"[Auto-completed by stuck detector after {int(elapsed)}s. "
                                    f"All {len(subtasks)} subtasks finished. "
                                    f"See logs/{task_id}.log for full output.]"
                                )
                            state._broadcast_task_update_sync(task_id)
                            agent_bus.send({
                                "from": "leroy", "to": "pm",
                                "type": "deliverable_ready",
                                "task_id": task_id,
                                "content": (
                                    f"Task {task_id} AUTO-COMPLETED by stuck detector. "
                                    f"All {len(subtasks)} subtasks finished {int(elapsed)}s ago. "
                                    f"Parent process was stuck (likely orphaned pipe). "
                                    f"Work is done -- check logs/{task_id}.log for full output."
                                ),
                                "requires_response": False,
                            })
                            state._broadcast_task_update_sync(task_id)
                            logger.info("STUCK TASK %s: auto-completed successfully", task_id)

                # Check 2: subprocess PID liveness (only for server-spawned tasks)
                pid = _active_pids.get(task_id)
                if pid:
                    try:
                        os.kill(pid, 0)  # signal 0 = check if alive
                    except ProcessLookupError:
                        logger.error(
                            "DEAD PROCESS: task %s has PID %d but process is gone. Auto-failing.",
                            task_id, pid
                        )
                        _active_pids.pop(task_id, None)
                        try:
                            if state._state_machine:
                                state._state_machine.transition(task_id, TaskState.FAILED_RETRYABLE, reason=f"process_{pid}_died_unexpectedly")
                        except Exception as _sm_err:
                            logger.warning("State machine transition failed for %s: %s", task_id, _sm_err)
                        meta["status"] = "failed"  # fallback / legacy compat
                        meta["result"] = f"Process {pid} died unexpectedly. Check logs/{task_id}.log"
                        state._broadcast_task_update_sync(task_id)
                        agent_bus.send({
                            "from": "leroy", "to": "pm",
                            "type": "deliverable_ready",
                            "task_id": task_id,
                            "content": f"Task {task_id} FAILED -- subprocess PID {pid} died unexpectedly.",
                            "requires_response": False,
                        })
                    continue  # Already handled by PID check

                # Check 3: orphan detection -- task is working, no PID tracked,
                # no subtasks, and no activity for a long time. This catches tasks
                # that lost their PID on server restart or where the builder crashed
                # before producing any output.
                #
                # SKIP for remote-target tasks: they execute on another machine
                # (kush, halo, studio, etc.) and will never have a local PID.
                # Status comes back via POST /tasks/{id}/result from the worker.
                _ORPHAN_THRESHOLD = 600  # 10 minutes with no activity and no PID
                _task_target = meta.get("target", "haze")
                if _task_target not in ("haze",):
                    continue  # Remote task -- worker manages lifecycle

                last_activity = meta.get("last_activity", meta.get("created_at", ""))
                if last_activity and not subtasks:
                    try:
                        activity_time = datetime.fromisoformat(last_activity)
                        orphan_elapsed = (datetime.now(timezone.utc) - activity_time).total_seconds()
                        if orphan_elapsed > _ORPHAN_THRESHOLD:
                            logger.warning(
                                "ORPHAN TASK DETECTED: %s -- no PID, no subtasks, no activity for %ds. Auto-failing.",
                                task_id, int(orphan_elapsed)
                            )
                            now_iso = datetime.now(timezone.utc).isoformat()
                            meta["_stuck_detected_at"] = now_iso
                            meta["_stuck_reason"] = f"Orphan: no PID, no subtasks, no activity for {int(orphan_elapsed)}s"
                            tracked = state._task_meta[task_id]
                            tracked["status"] = "failed"
                            tracked["completed_at"] = now_iso
                            tracked["result"] = (
                                f"[Auto-failed by stuck detector: orphan task with no PID, no subtasks, "
                                f"no activity for {int(orphan_elapsed)}s. Builder likely crashed on launch "
                                f"or PID lost on server restart. Check logs/{task_id}.log]"
                            )
                            try:
                                if state._state_machine:
                                    state._state_machine.transition(task_id, TaskState.FAILED_RETRYABLE, reason="orphan_no_pid_no_activity")
                            except Exception as _sm_err:
                                logger.warning("State machine transition failed for %s: %s", task_id, _sm_err)
                            state._broadcast_task_update_sync(task_id)
                            agent_bus.send({
                                "from": "leroy", "to": "pm",
                                "type": "deliverable_ready",
                                "task_id": task_id,
                                "content": (
                                    f"Task {task_id} FAILED -- orphan detected. No active process, no subtasks, "
                                    f"no activity for {int(orphan_elapsed)}s. Builder likely crashed on launch. "
                                    f"Check logs/{task_id}.log"
                                ),
                                "requires_response": False,
                            })
                            logger.info("ORPHAN TASK %s: auto-failed successfully", task_id)
                    except (ValueError, TypeError) as parse_err:
                        logger.debug("Orphan check skipped for %s: %s", task_id, parse_err)

            # --- Dispatched task recovery ---
            # Tasks in "dispatched" status have been handed to the dispatcher which
            # creates vehicle sub-tasks.  If the builder was unavailable when vehicles
            # were enqueued, nothing retries them and the container sits in "dispatched"
            # forever.  This block detects and recovers those stalls.
            _DISPATCH_STALL_THRESHOLD = 300  # 5 minutes

            for task_id, meta in list(state._task_meta.items()):
                if meta.get("status") != "dispatched":
                    continue

                # Parse created_at to determine age
                created_raw = meta.get("created_at") or meta.get("created", "")
                if not created_raw:
                    continue
                try:
                    created_time = datetime.fromisoformat(created_raw)
                    now_dt = datetime.now(timezone.utc)
                    age_seconds = (now_dt - created_time).total_seconds()
                except (ValueError, TypeError) as _parse_err:
                    logger.debug("Dispatched recovery: skipping %s, bad timestamp: %s", task_id, _parse_err)
                    continue

                if age_seconds < _DISPATCH_STALL_THRESHOLD:
                    continue  # Too young, not stalled yet

                # Find vehicle tasks whose parent_id == this container task
                vehicle_ids = [
                    vid for vid, vm in state._task_meta.items()
                    if vm.get("parent_id") == task_id
                ]

                if not vehicle_ids:
                    # Dispatcher returned a container_id but never created vehicles
                    logger.warning(
                        "DISPATCH STALL: task %s has been dispatched for %.0fs with no vehicles. "
                        "Falling back to direct enqueue.",
                        task_id, age_seconds,
                    )
                    spec_text = meta.get("spec", "")
                    state._task_meta[task_id]["status"] = "pending"
                    _recover_target = meta.get("target", "haze")
                    if _recover_target in ("haze",) and state._task_queue:
                        state._task_queue.enqueue(task_id, spec_text, priority="normal", target_machine=_recover_target)
                    else:
                        logger.info("Recovery: task %s left pending for remote worker (target=%s)", task_id, _recover_target)
                    state._broadcast_task_update_sync(task_id)
                    continue

                # Check if any vehicle is actively running (working = in progress)
                any_working = any(
                    state._task_meta.get(vid, {}).get("status") == "working"
                    for vid in vehicle_ids
                )
                if any_working:
                    continue  # Container has a vehicle actively running -- leave it alone

                # Check for blocked vehicles whose deps are all satisfied.
                # This catches the case where a vehicle completed but the unblock
                # handler didn't fire (e.g. server restarted between completion and unblock).
                any_blocked = any(
                    state._task_meta.get(vid, {}).get("status") == "blocked"
                    for vid in vehicle_ids
                )
                if any_blocked and state._dispatcher:
                    # Re-trigger the dispatcher's completion handler for each done vehicle.
                    # This is idempotent -- it only unblocks vehicles whose deps are satisfied.
                    done_vehicles = [
                        vid for vid in vehicle_ids
                        if state._task_meta.get(vid, {}).get("status") in ("completed", "completed_unverified")
                        or state._task_meta.get(vid, {}).get("v2_state", "") in state._dispatcher._DONE_STATES
                    ]
                    if done_vehicles:
                        logger.warning(
                            "DISPATCH STALL: task %s has %d blocked vehicles with %d done -- "
                            "re-triggering unblock",
                            task_id, sum(1 for v in vehicle_ids if state._task_meta.get(v,{}).get("status")=="blocked"),
                            len(done_vehicles),
                        )
                        for vid in done_vehicles:
                            try:
                                state._dispatcher.handle_vehicle_completed(vid)
                            except Exception as _hvc_err:
                                logger.warning("Recovery: handle_vehicle_completed(%s) failed: %s", vid[:8], _hvc_err)
                        state._broadcast_task_update_sync(task_id)
                        continue  # Let the unblock take effect; next sweep will check again

                # No vehicle is active.  Re-enqueue vehicles that are still pending/dispatched.
                ready_vehicles = [
                    vid for vid in vehicle_ids
                    if state._task_meta.get(vid, {}).get("status") in ("pending", "dispatched")
                ]
                if ready_vehicles:
                    logger.warning(
                        "DISPATCH STALL: task %s stalled for %.0fs -- %d/%d vehicles idle. "
                        "Re-enqueuing %d vehicle(s).",
                        task_id, age_seconds, len(ready_vehicles), len(vehicle_ids), len(ready_vehicles),
                    )
                    for vid in ready_vehicles:
                        v_meta = state._task_meta.get(vid, {})
                        v_spec = v_meta.get("spec", "")
                        v_target = v_meta.get("target", meta.get("target", "haze"))
                        if v_target in ("haze",) and state._task_queue:
                            state._task_queue.enqueue(vid, v_spec, priority="normal", target_machine=v_target)
                        else:
                            # Remote: ensure status is pending for worker polling
                            state._task_meta[vid]["status"] = "pending"
                            logger.info("Recovery: vehicle %s set pending for remote worker (target=%s)", vid[:8], v_target)
                    state._broadcast_task_update_sync(task_id)
                else:
                    # All vehicles failed/cancelled and none are recoverable
                    logger.error(
                        "DISPATCH STALL: task %s has been dispatched for %.0fs and has no recoverable vehicles "
                        "(%d total, none pending/dispatched/working). Marking failed.",
                        task_id, age_seconds, len(vehicle_ids),
                    )
                    now_iso = datetime.now(timezone.utc).isoformat()
                    tracked = state._task_meta[task_id]
                    tracked["status"] = "failed"
                    tracked["completed_at"] = now_iso
                    tracked["result"] = (
                        f"[Auto-failed by dispatch recovery: task dispatched {int(age_seconds)}s ago with "
                        f"{len(vehicle_ids)} vehicle(s), none recoverable. "
                        f"Check logs/{task_id}.log]"
                    )
                    state._broadcast_task_update_sync(task_id)
                    agent_bus.send({
                        "from": "leroy", "to": "pm",
                        "type": "deliverable_ready",
                        "task_id": task_id,
                        "content": (
                            f"Task {task_id} FAILED -- dispatch stall with no recoverable vehicles after "
                            f"{int(age_seconds)}s. Check logs/{task_id}.log"
                        ),
                        "requires_response": False,
                    })

        except Exception:
            logger.exception("Stuck task detector error")


# ---------------------------------------------------------------------------
# Build combined ASGI app
# ---------------------------------------------------------------------------
def build_app():
    """Build the main Starlette app with A2A + custom routes."""
    # A2A protocol handler
    request_handler = DefaultRequestHandler(
        agent_executor=LeroyExecutor(),
        task_store=state._task_store,
    )

    a2a_app_builder = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    # Get the base A2A starlette app
    a2a_starlette = a2a_app_builder.build()

    # Custom routes for Leroy pickup
    # Order matters: specific paths before parameterized ones
    custom_routes = [
        Route("/health", health, methods=["GET"]),
        Route("/tasks/pending", tasks_pending, methods=["GET"]),
        Route("/tasks/complete", tasks_complete, methods=["POST"]),
        Route("/tasks/stream", tasks_stream, methods=["GET"]),
        Route("/tasks/{task_id}/accept", task_accept, methods=["POST"]),
        Route("/tasks/{task_id}/cancel", task_cancel, methods=["POST"]),
        Route("/tasks/{task_id}/force-fail", task_force_fail, methods=["POST"]),
        Route("/tasks/{task_id}/archive", task_archive, methods=["POST"]),
        Route("/tasks/{task_id}/unarchive", task_unarchive, methods=["POST"]),
        Route("/tasks/{task_id}/review", task_review, methods=["POST"]),
        Route("/tasks/{task_id}", task_delete, methods=["DELETE"]),
        Route("/tasks/{task_id}/logs/stream", task_logs_stream, methods=["GET"]),
        Route("/tasks/{task_id}/logs", task_logs, methods=["GET"]),
        Route("/tasks/{task_id}/subtasks", subtask_list, methods=["GET"]),
        Route("/tasks/{task_id}/subtasks", subtask_update, methods=["POST"]),
        Route("/tasks/{task_id}/messages", task_messages, methods=["GET"]),
        Route("/tasks/{task_id}", task_detail, methods=["GET"]),
        Route("/tasks", tasks_list, methods=["GET"]),
        # Generic agent message bus
        Route("/messages/agents", bus_agents, methods=["GET"]),
        Route("/messages/{message_id}/respond", bus_respond, methods=["POST"]),
        Route("/messages/{message_id}/read", bus_read, methods=["POST"]),
        Route("/messages/{message_id}/response", bus_poll_response, methods=["GET"]),
        Route("/messages/{message_id}", bus_get, methods=["GET"]),
        Route("/messages", bus_send, methods=["POST"]),
        Route("/messages", bus_list, methods=["GET"]),
        # Legacy PM endpoints (backward compat -- Leroy subprocesses still use these)
        Route("/pm/messages/pending", pm_messages_pending, methods=["GET"]),
        Route("/pm/messages/{message_id}/respond", pm_messages_respond, methods=["POST"]),
        Route("/pm/messages/{message_id}/response", pm_messages_response_poll, methods=["GET"]),
        Route("/pm/messages", pm_messages_receive, methods=["POST"]),
        Route("/pm/messages", pm_messages_all, methods=["GET"]),
        # Agent registry
        Route("/agents", agents_list, methods=["GET"]),
        Route("/agents/{name}/heartbeat", agent_heartbeat, methods=["POST"]),
        Route("/agents/{name}", agent_delete, methods=["DELETE"]),
        # Activity feed
        Route("/activity/stream", activity_stream, methods=["GET"]),
        Route("/activity", activity_list, methods=["GET"]),
        Route("/activity", activity_create, methods=["POST"]),
        # PM Proposals (headless PM approval queue)
        Route("/pm/proposals/{proposal_id}/approve", proposals_approve, methods=["POST"]),
        Route("/pm/proposals/{proposal_id}/reject", proposals_reject, methods=["POST"]),
        Route("/pm/proposals", proposals_create, methods=["POST"]),
        Route("/pm/proposals", proposals_list, methods=["GET"]),
        # Ideas (backlog placeholders)
        Route("/ideas/{task_id}/promote", ideas_promote, methods=["POST"]),
        Route("/ideas", ideas_create, methods=["POST"]),
        # Specs pipeline
        Route("/specs", specs_list, methods=["GET"]),
        # Plans (v2 Phase 3)
        Route("/plans/report", plans_report, methods=["GET"]),
        Route("/plans/cost", plans_cost, methods=["GET"]),
        Route("/plans/subsystem-health", plans_subsystem_health, methods=["GET"]),
        Route("/plans/brain-gaps", plans_brain_gaps, methods=["GET"]),
        Route("/plans/{plan_id}", plans_detail, methods=["GET"]),
        Route("/plans", plans_list, methods=["GET"]),
        # Criteria Validation (v2 Phase 11)
        Route("/validate/drift/{plan_id}", drift_detection, methods=["GET"]),
        Route("/validate/{task_id}", validate_task_criteria, methods=["POST"]),
        # Improvement Engine (v2 Phase 10)
        Route("/improvement/analysis", improvement_full, methods=["GET"]),
        Route("/improvement/patterns", improvement_patterns, methods=["GET"]),
        Route("/improvement/thresholds", improvement_thresholds, methods=["GET"]),
        Route("/improvement/templates", improvement_templates, methods=["GET"]),
        Route("/improvement/suggestions", improvement_suggestions, methods=["GET"]),
        Route("/improvement/baseline", improvement_baseline, methods=["GET"]),
        # Quality Scoring (v2 Phase 9)
        Route("/quality/score/{task_id}", quality_score_task, methods=["POST"]),
        Route("/quality/metrics", quality_metrics_endpoint, methods=["GET"]),
        # Task Queue (v2 Phase 8A)
        Route("/queue/status", queue_status, methods=["GET"]),
        Route("/queue/tasks", queue_tasks, methods=["GET"]),
        # Webhooks (v2 Phase 8B)
        Route("/webhooks/register", webhook_register, methods=["POST"]),
        Route("/webhooks/{webhook_id}/unregister", webhook_unregister, methods=["POST"]),
        Route("/webhooks/metrics", webhook_metrics, methods=["GET"]),
        Route("/webhooks", webhook_list, methods=["GET"]),
        # PM Autonomy (v2 Phase 7)
        Route("/pm/actions/auto-approve", pm_auto_approve_check, methods=["POST"]),
        Route("/pm/actions/{decision_id}/outcome", pm_actions_outcome, methods=["POST"]),
        Route("/pm/actions", pm_actions_list, methods=["GET"]),
        Route("/pm/autonomy/evaluate", pm_autonomy_evaluate, methods=["POST"]),
        Route("/pm/autonomy", pm_autonomy_status, methods=["GET"]),
        # Knowledge governance (v2 Phase 6)
        Route("/knowledge/governance", knowledge_governance_stats, methods=["GET"]),
        Route("/knowledge/prune", knowledge_prune, methods=["POST"]),
        # Brain health proxy
        Route("/brain/health", brain_health, methods=["GET"]),
        # Infrastructure status
        Route("/infra/status", infra_status, methods=["GET"]),
        # Admin
        Route("/admin/circuit-reset", admin_circuit_reset, methods=["POST"]),
        # HTTP persist gateway (no auth -- localhost only, for shell hooks)
        Route("/persist/last", http_persist_last_get, methods=["GET"]),
        Route("/persist/last", http_persist_last_post, methods=["POST"]),
        Route("/persist/append", http_persist_append, methods=["POST"]),
        Route("/persist", http_persist, methods=["POST"]),
        # Claude Code hook receivers (no auth -- localhost only)
        Route("/hooks/tool-use", hooks_tool_use, methods=["POST"]),
        Route("/hooks/subagent", hooks_subagent, methods=["POST"]),
        Route("/hooks/events/stream", hooks_events_stream, methods=["GET"]),
        Route("/hooks/events", hooks_events_list, methods=["GET"]),
    ]

    # Prepend custom routes before A2A routes
    for route in reversed(custom_routes):
        a2a_starlette.router.routes.insert(0, route)

    return a2a_starlette


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    """Start Leroy A2A server + health server."""
    auth.load_tokens()

    # Initialize SQLite-backed task, subtask, and message stores (loads from DB on startup)
    task_db.init(config.TASK_DB_PATH)
    state._task_meta = task_db.task_meta
    state._subtask_store = task_db.subtask_store
    state._task_store = task_db.sqlite_task_store
    agent_bus.init(task_db.msg_store, task_db.agent_store)
    state._agent_store = task_db.agent_store
    state._activity_store = task_db.activity_store
    state._proposal_store = task_db.proposal_store

    # v2 State machine + retry budget
    state._state_machine = TaskStateMachine(state._task_meta)
    state._retry_budget = RetryBudget(state._task_meta)

    # v2 Phase 7: PM action store
    state._action_store = PMActionStore(task_db._db)
    logger.info("v2 state machine, retry budget, and PM action store initialized")

    # Wire execution module dependencies (module-level attribute injection)
    execution._task_meta = state._task_meta
    execution._state_machine = state._state_machine
    execution._retry_budget = state._retry_budget
    execution._persist_manager = state._persist_manager
    execution._broadcast_task_update_sync = state._broadcast_task_update_sync
    execution._emit_activity = state._emit_activity

    # v2 Phase 8A: Concurrency-controlled task queue
    state._task_queue = TaskQueue(execute_fn=_execute_task, concurrency=None)
    execution._task_queue = state._task_queue

    # Dispatcher Phase 3a: Routing + Dependency Gating
    if task_db.container_store is not None:
        state._dispatcher = Dispatcher(
            container_store=task_db.container_store,
            task_queue=state._task_queue,
            task_meta=state._task_meta,
            state_machine=state._state_machine,
            broadcast_fn=state._broadcast_task_update_sync,
        )
        execution._dispatcher = state._dispatcher
        logger.info("Dispatcher Phase 3a initialized (routing + dependency gating)")
    else:
        logger.warning("Dispatcher Phase 3a: container_store not available -- dispatcher disabled")

    # Dispatcher recovery sweep (IC-9, IC-13): runs after dispatcher is ready so
    # reconvergence callbacks can be wired in.  Failure callback marks the container
    # NEEDS_DECISION directly on the store (vehicle-level retries are handled by the
    # normal handle_vehicle_failed path on next dispatch cycle).
    if task_db.container_store is not None:
        try:
            def _recovery_failure_cb(cid: str) -> None:
                task_db.container_store.set_status(cid, ContainerStatus.NEEDS_DECISION)

            task_db.container_store.recovery_sweep_containers(
                task_meta_getter=state._task_meta.get,
                state_machine_ref=state._state_machine,
                reconverge_callback=state._dispatcher.reconverge,
                failure_callback=_recovery_failure_cb,
            )
        except Exception as _e:
            logger.warning("Dispatcher recovery sweep failed (non-fatal): %s", _e)

    # v2 Phase 8B: Webhook registry for bus push delivery
    state._webhook_registry = WebhookRegistry(db=task_db._db)
    agent_bus.set_webhook_registry(state._webhook_registry)

    # v2 Phase 4: Register event handlers on state machine transitions
    register_all_handlers(state._state_machine, state._retry_budget, state._task_meta,
                          broadcast_fn=state._broadcast_task_update_sync,
                          persist_manager=state._persist_manager,
                          action_store=state._action_store)

    # v2 Phase 11: Auto-validate on COMPLETED_UNVERIFIED entry
    def _auto_validate_handler(event: dict) -> None:
        """RUNNING -> COMPLETED_UNVERIFIED: Run criteria validation and auto-transition.

        Calls validate_criteria -> detect_hallucination -> make_verification_decision.
        Promotes to COMPLETED_VERIFIED or demotes to FAILED_RETRYABLE based on result.
        If decision is 'review' or validation errors out, leaves task as COMPLETED_UNVERIFIED.
        """
        task_id = event["task_id"]
        try:
            store = task_db.plan_store
            plan = store.get_plan_by_task(task_id) if store else None
            if not plan:
                logger.info("Auto-validate: no plan for %s, leaving as COMPLETED_UNVERIFIED", task_id)
                return

            meta = state._task_meta.get(task_id) or {}
            typed_ir = plan.get("typed_ir")
            if typed_ir and isinstance(typed_ir, str):
                try:
                    typed_ir = json.loads(typed_ir)
                except Exception:
                    typed_ir = {}
            typed_ir = typed_ir or {}

            builder_sections = meta.get("builder_sections", {})
            result_text = meta.get("result", "") or meta.get("partial_result", "") or ""

            # Run criteria validation
            validation = validate_criteria(
                typed_ir, builder_sections, result_text,
                task_id=task_id, plan_id=plan.get("plan_id", ""),
            )

            # Hallucination check (builder_claimed_pass=False for auto-validation path)
            pass_rate = plan.get("pass_rate")
            validation = detect_hallucination(validation, False, pass_rate)

            decision = make_verification_decision(validation, result_text=result_text)
            validation.recommendation = decision

            logger.info(
                "Auto-validate %s: decision=%s, verification_rate=%.0%%",
                task_id, decision, validation.verification_rate,
            )

            if decision == "promote":
                try:
                    state._state_machine.transition(
                        task_id, TaskState.COMPLETED_VERIFIED,
                        reason=f"auto-validate: {validation.verification_rate:.0%} verified",
                    )
                except Exception as e:
                    logger.warning("Auto-validate promote failed for %s: %s", task_id, e)
            elif decision == "fail":
                try:
                    state._state_machine.transition(
                        task_id, TaskState.FAILED_RETRYABLE,
                        reason=f"auto-validate fail: {validation.hallucination_reason or 'low verification rate'}",
                    )
                except Exception as e:
                    logger.warning("Auto-validate fail-transition failed for %s: %s", task_id, e)
            # decision == "review": leave as COMPLETED_UNVERIFIED for PM manual review

        except Exception as e:
            logger.error("Auto-validate handler error for %s: %s", task_id, e, exc_info=True)
            # Leave as COMPLETED_UNVERIFIED -- do not crash the task flow

    state._state_machine.register_handler(TaskState.RUNNING, TaskState.COMPLETED_UNVERIFIED, _auto_validate_handler)
    logger.info("Auto-validate handler registered for RUNNING -> COMPLETED_UNVERIFIED")

    # v2 Phase 4: SSE broadcast on every state transition
    def _sse_state_handler(event: dict) -> None:
        state._broadcast_state_transition(
            event["task_id"], event["from_state"], event["to_state"],
            reason=event.get("reason", ""),
        )
    state._state_machine.register_global_handler(_sse_state_handler)

    # Ops task lifecycle notifications: auto-notify ops (and PM on failures)
    # when tasks reach terminal or significant states.
    def _ops_lifecycle_handler(event: dict) -> None:
        """Send bus notifications to ops (and PM on failures) for terminal state transitions."""
        to_state = event.get("to_state", "")
        task_id = event["task_id"]
        reason = event.get("reason", "")

        # Only act on states we care about
        _notify_states = {
            TaskState.COMPLETED_VERIFIED.value,
            TaskState.COMPLETED_UNVERIFIED.value,
            TaskState.FAILED_RETRYABLE.value,
            TaskState.ESCALATED.value,
        }
        if to_state not in _notify_states:
            return

        # Extract subject: first H1/H2 heading line from spec, else first 80 chars
        meta = state._task_meta.get(task_id) or {}
        spec = meta.get("spec", "")
        subject = ""
        for _ln in spec.splitlines():
            _stripped = _ln.strip().lstrip("#").strip()
            if _stripped:
                subject = _stripped[:80]
                break
        if not subject:
            subject = "(no subject)"

        error_summary = reason[:120] if reason else "unknown error"

        if to_state == TaskState.COMPLETED_VERIFIED.value:
            agent_bus.send({
                "from": "leroy",
                "to": "ops",
                "type": "task_completion",
                "task_id": task_id,
                "content": f"Task {task_id} completed and verified. Subject: {subject}. No action needed.",
                "requires_response": False,
            })
        elif to_state == TaskState.COMPLETED_UNVERIFIED.value:
            agent_bus.send({
                "from": "leroy",
                "to": "ops",
                "type": "task_completion",
                "task_id": task_id,
                "content": f"Task {task_id} completed but unverified. Subject: {subject}. May need manual review.",
                "requires_response": False,
            })
        elif to_state == TaskState.FAILED_RETRYABLE.value:
            agent_bus.send({
                "from": "leroy",
                "to": "ops",
                "type": "task_completion",
                "task_id": task_id,
                "content": f"Task {task_id} failed (retryable). Subject: {subject}. Error: {error_summary}.",
                "requires_response": False,
            })
            agent_bus.send({
                "from": "leroy",
                "to": "pm",
                "type": "task_completion",
                "task_id": task_id,
                "content": f"Task {task_id} failed (retryable). Subject: {subject}. Error: {error_summary}.",
                "requires_response": False,
            })
        elif to_state == TaskState.ESCALATED.value:
            agent_bus.send({
                "from": "leroy",
                "to": "ops",
                "type": "task_completion",
                "task_id": task_id,
                "content": f"Task {task_id} failed (terminal). Subject: {subject}. Error: {error_summary}. PM notified.",
                "requires_response": False,
            })
            agent_bus.send({
                "from": "leroy",
                "to": "pm",
                "type": "task_completion",
                "task_id": task_id,
                "content": f"Task {task_id} failed (terminal/escalated). Subject: {subject}. Error: {error_summary}.",
                "requires_response": False,
            })

    state._state_machine.register_global_handler(_ops_lifecycle_handler)
    logger.info("Ops lifecycle notification handler registered for terminal state transitions")

    logger.info(
        "Task store loaded: %d task(s), %d subtask group(s), %d message(s)",
        len(state._task_meta),
        len(task_db.subtask_store._cache),
        len(task_db.msg_store._messages),
    )

    # Seed known agents (Phase 1 -- no heartbeat integration yet)
    now = datetime.now(timezone.utc).isoformat()
    for seed in _SEED_AGENTS:
        existing = state._agent_store.get(seed["name"])
        if existing is None:
            # Only seed if not already registered (preserves heartbeat data on restart)
            agent_record = dict(seed)
            agent_record["seeded_at"] = now
            state._agent_store.upsert(agent_record)
            logger.info("Seeded agent: %s", seed["name"])
        else:
            logger.info("Agent %s already registered, skipping seed", seed["name"])

    # Emit startup activity event
    state._emit_activity("leroy", "status_update", "Leroy A2A server started",
                   detail=f"Port {config.PORT}, {len(state._task_meta)} task(s) loaded")

    # Notify ops that the server has started (restart detection)
    agent_bus.send({
        "from": "leroy",
        "to": "ops",
        "type": "status_update",
        "content": f"Leroy A2A server started. Uptime: 0s. Version: {config.AGENT_VERSION}.",
        "requires_response": False,
    })
    logger.info("Startup notification sent to ops via agent bus")

    # Start persistence manager (background retry thread + startup queue flush)
    state._persist_manager.start()

    # v2 Phase 8B: Start webhook delivery background thread
    state._webhook_registry.start()

    # Legacy broker flush thread removed -- webhook is dead, agent_bus handles messaging

    app = build_app()

    # v2 Phase 8A: Start task queue dispatcher on uvicorn's event loop via startup hook
    async def _start_task_queue():
        loop = asyncio.get_running_loop()
        state._task_queue.start(loop)
        logger.info("v2 task queue started on uvicorn event loop")

    app.add_event_handler("startup", _start_task_queue)

    logger.info(
        "Starting Leroy A2A server on %s:%d (health on %d)",
        config.HOST, config.PORT, config.HEALTH_PORT,
    )

    # Run health server in background thread
    import threading

    def run_health():
        uvicorn.run(
            health_app,
            host=config.HOST,
            port=config.HEALTH_PORT,
            log_level="warning",
        )

    health_thread = threading.Thread(target=run_health, daemon=True)
    health_thread.start()

    # Start stuck task detector
    stuck_thread = threading.Thread(target=_stuck_task_detector, daemon=True)
    stuck_thread.start()
    logger.info("Stuck task detector started (check every %ds, threshold %ds)", _STUCK_CHECK_INTERVAL, _STUCK_THRESHOLD)

    # Run main A2A server
    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
