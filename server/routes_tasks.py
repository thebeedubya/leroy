"""Task management route handlers.

Extracted from server.py -- handles task CRUD, subtasks, logs, streaming.
"""
import asyncio
import json
import os
import re
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

import agent_bus
import task_db
import server_state as state
from state_machine import TaskState
from execution import _active_pids, LOGS_DIR, _TERMINAL_STATUSES, WORK_DIR


async def tasks_pending(request: Request) -> JSONResponse:
    """GET /tasks/pending -- Returns all pending tasks for Leroy pickup."""
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    pending = [
        t for t in state._task_meta.values()
        if t["status"] == "pending"
    ]
    return JSONResponse({"tasks": pending, "count": len(pending)})


async def tasks_complete(request: Request) -> JSONResponse:
    """POST /tasks/complete -- Leroy reports task completion.

    Body: {"task_id": "...", "result": "..."}
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    task_id = body.get("task_id")
    result = body.get("result")

    if not task_id:
        return JSONResponse({"error": "task_id required"}, status_code=400)

    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    is_qa_review = bool(body.get("qa_review", False))
    new_status = "qa_review" if is_qa_review else "completed"
    if not is_qa_review:
        try:
            if state._state_machine:
                state._state_machine.transition(task_id, TaskState.COMPLETED_UNVERIFIED, reason="builder_reported_complete")
        except Exception as _sm_err:
            state.logger.warning("State machine transition failed for %s: %s", task_id, _sm_err)
    state._task_meta[task_id]["status"] = new_status  # fallback / legacy compat (qa_review has no state machine state)
    state._task_meta[task_id]["result"] = result
    state._task_meta[task_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
    if is_qa_review:
        state._task_meta[task_id]["qa_review_requested_at"] = datetime.now(timezone.utc).isoformat()

    state.logger.info("Task %s %s", task_id, "queued for qa_review" if is_qa_review else "completed")
    state._broadcast_task_update_sync(task_id)

    # Emit activity event
    event_label = "qa_review" if is_qa_review else "task_complete"
    state._emit_activity("leroy", event_label,
                   f"Task {'queued for QA review' if is_qa_review else 'completed'}: {task_id[:8]}",
                   task_id=task_id)

    # Notify PM via message broker
    result_str = result or ""
    result_preview = (result_str[:400] + "...") if len(result_str) > 400 else result_str
    agent_bus.send({
        "from": "leroy", "to": "pm",
        "type": "deliverable_ready",
        "task_id": task_id,
        "content": (
            f"Task {task_id} {'AWAITING QA REVIEW' if is_qa_review else 'COMPLETED'} successfully.\n\n"
            f"Result preview:\n{result_preview}"
        ),
        "context": f"Spec preview: {state._task_meta[task_id].get('spec', '')[:120]}",
        "requires_response": False,
    })

    # Persist task outcome to Aianna -- non-blocking, handles brain unavailability
    try:
        state._persist_manager.persist_task(task_id, state._task_meta[task_id])
    except Exception as _pe:
        state.logger.error("Task %s: persist_manager raised unexpectedly: %s", task_id, _pe)

    return JSONResponse({"status": "ok", "task_id": task_id})


async def task_accept(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/accept -- Leroy claims a pending task for execution.

    Transitions task from pending -> working so it no longer appears
    in /tasks/pending for other callers.
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    if state._task_meta[task_id]["status"] != "pending":
        return JSONResponse(
            {"error": f"task {task_id} cannot be accepted (status: {state._task_meta[task_id]['status']})"},
            status_code=409,
        )

    try:
        if state._state_machine:
            state._state_machine.transition(task_id, TaskState.RUNNING, reason="task_accepted_via_api")
    except Exception as _sm_err:
        state.logger.warning("State machine transition failed for %s: %s", task_id, _sm_err)
    state._task_meta[task_id]["status"] = "working"  # fallback / legacy compat
    state._task_meta[task_id]["accepted_at"] = datetime.now(timezone.utc).isoformat()
    state.logger.info("Task %s accepted for execution", task_id)
    state._broadcast_task_update_sync(task_id)
    return JSONResponse({"status": "ok", "task_id": task_id, "spec": state._task_meta[task_id]["spec"]})


def _compute_pipeline_stage(task: dict) -> dict:
    """Compute pipeline stage and metadata for a task.

    Returns a dict of pipeline_ fields to merge into the task response.
    Uses task_db.plan_store for lifecycle metadata (retro_text, brain_persisted, pass_rate).
    Fast: single DB lookup per task via plan_store.get_plan_by_task().
    """
    status = task.get("status", "pending")
    task_id = task.get("task_id", "")
    created = task.get("created_at", "")

    # Check plan record for lifecycle fields
    retro_text = None
    brain_persisted = False
    pass_rate = None

    plan_store = task_db.plan_store
    if plan_store and task_id:
        try:
            plan = plan_store.get_plan_by_task(task_id)
            if plan:
                retro_text = plan.get("retro_text") or None
                brain_persisted = bool(plan.get("brain_persisted"))
                pass_rate = plan.get("pass_rate") or None
        except Exception:
            pass

    # Detect QA tasks by spec subject pattern
    spec = task.get("spec", "")
    subject_line = spec.split("\n")[0] if spec else ""
    is_qa_task = bool(re.match(r"^#\s*QA[:\s]", subject_line, re.IGNORECASE))

    # Compute age in seconds
    age_seconds = None
    if created:
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - created_dt).total_seconds()
        except Exception:
            pass

    # Zombie detection: working > 4 hours with no recent activity
    is_zombie = False
    if status == "working" and age_seconds and age_seconds > 14400:
        last_activity = task.get("last_activity")
        if last_activity:
            try:
                la_dt = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
                inactive_seconds = (datetime.now(timezone.utc) - la_dt).total_seconds()
                if inactive_seconds > 14400:
                    is_zombie = True
            except Exception:
                is_zombie = True
        else:
            is_zombie = True

    # Stage mapping
    if status == "idea":
        stage = "draft"
    elif status == "pending":
        stage = "sent"
    elif status in ("working", "waiting_for_pm"):
        stage = "zombie" if is_zombie else "building"
    elif status in ("qa_review", "completed_unverified"):
        stage = "qa"
    elif status == "completed":
        # Vehicle tasks (dispatcher sub-tasks with a parent_id) are sub-units of a parent spec.
        # They don't have their own spec files and can't be retro'd independently.
        # The parent's retro covers the full work -- skip retro/persist for vehicles.
        if task.get("parent_id"):
            stage = "done"
        elif not retro_text and not pass_rate:
            stage = "retro"
        elif not brain_persisted:
            stage = "persist"
        else:
            stage = "done"
    elif status in ("failed", "cancelled"):
        stage = "done"  # Failed/cancelled go to done (with failure indicator)
    else:
        stage = "sent"

    return {
        "pipeline_stage": stage,
        "pipeline_is_zombie": is_zombie,
        "pipeline_is_qa": is_qa_task,
        "pipeline_age_seconds": int(age_seconds) if age_seconds is not None else None,
        "pipeline_has_retro": bool(retro_text),
        "pipeline_brain_persisted": brain_persisted,
        "pipeline_pass_rate": pass_rate,
    }


async def tasks_list(request: Request) -> JSONResponse:
    """GET /tasks -- Returns all tasks with their status."""
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    status_filter = request.query_params.get("status")
    target_filter = request.query_params.get("target")  # e.g. ?target=kush
    # Default: hide archived tasks. Pass ?include_archived=true to see them.
    include_archived = request.query_params.get("include_archived", "").lower() in ("1", "true", "yes")
    tasks = list(state._task_meta.values())
    if status_filter:
        tasks = [t for t in tasks if t["status"] == status_filter]
    if target_filter:
        tasks = [t for t in tasks if t.get("target") == target_filter]
    if not include_archived:
        tasks = [t for t in tasks if not t.get("archived", False)]

    # Enrich each task with computed pipeline stage fields
    enriched_tasks = []
    for task in tasks:
        enriched = dict(task)
        enriched.update(_compute_pipeline_stage(task))
        enriched_tasks.append(enriched)

    return JSONResponse({"tasks": enriched_tasks, "count": len(enriched_tasks)})


async def task_detail(request: Request) -> JSONResponse:
    """GET /tasks/{task_id} -- Returns a single task by ID."""
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    task = state._task_meta[task_id]
    enriched = dict(task)
    enriched.update(_compute_pipeline_stage(task))
    return JSONResponse(enriched)


async def task_cancel(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/cancel -- Cancel a pending task."""
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    if state._task_meta[task_id]["status"] not in ("pending", "working", "idea"):
        return JSONResponse(
            {"error": f"task {task_id} cannot be cancelled (status: {state._task_meta[task_id]['status']})"},
            status_code=409,
        )

    # NOTE: TaskState enum has no CANCELLED state; no state machine transition available.
    # State machine gap: cancelled tasks bypass event handlers by design limitation.
    state._task_meta[task_id]["status"] = "cancelled"
    state.logger.info("Task %s cancelled via REST", task_id)
    return JSONResponse({"status": "ok", "task_id": task_id})


async def task_force_fail(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/force-fail -- Force-fail a stuck task regardless of current state.

    Body: {"reason": "optional explanation"}  (reason defaults to "force_failed_by_operator")

    Behavior:
    - Accepts any task status (dispatched, working, pending, etc.)
    - Sets status to "failed" and v2_state to "escalated" (terminal)
    - Sends SIGTERM to the process group if the task has an active PID
    - Marks all non-terminal vehicle tasks (parent_id == task_id) as failed
    - Broadcasts SSE update and sends agent bus notification
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    # Parse optional reason from body
    reason = "force_failed_by_operator"
    try:
        body = await request.json()
        if body.get("reason"):
            reason = str(body["reason"])
    except Exception:
        pass  # No body or invalid JSON -- use default reason

    meta = state._task_meta[task_id]
    previous_status = meta.get("status", "unknown")
    now_iso = datetime.now(timezone.utc).isoformat()

    # --- Kill active process if any ---
    pid = _active_pids.get(task_id)
    kill_note = ""
    if pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            kill_note = f" (sent SIGTERM to PID {pid})"
            state.logger.info("Task %s force-fail: sent SIGTERM to process group (PID %d)", task_id, pid)
        except (ProcessLookupError, OSError) as kill_err:
            kill_note = f" (SIGTERM skipped: {kill_err})"
            state.logger.info("Task %s force-fail: SIGTERM skipped (%s)", task_id, kill_err)
        _active_pids.pop(task_id, None)

    # --- Force task to failed state (bypass state machine validation) ---
    meta["status"] = "failed"
    meta["completed_at"] = now_iso
    meta["force_failed_at"] = now_iso
    meta["force_fail_reason"] = reason
    meta["result"] = f"[Force-failed by operator: {reason}]{kill_note}"

    # Directly write terminal v2_state (bypasses transition validation intentionally)
    meta["v2_state"] = TaskState.ESCALATED.value
    history = meta.get("v2_state_history", [])
    history.append({
        "state": TaskState.ESCALATED.value,
        "timestamp": now_iso,
        "reason": f"force_fail: {reason}",
        "from_state": meta.get("v2_state", "unknown"),
    })
    meta["v2_state_history"] = history

    # --- Fail all non-terminal vehicle tasks (containers with parent_id == task_id) ---
    vehicle_ids = [
        vid for vid, vm in state._task_meta.items()
        if vm.get("parent_id") == task_id
    ]
    failed_vehicles = []
    for vid in vehicle_ids:
        v_meta = state._task_meta[vid]
        v_status = v_meta.get("status", "unknown")
        if v_status not in _TERMINAL_STATUSES:
            # Kill vehicle process if active
            v_pid = _active_pids.get(vid)
            if v_pid:
                try:
                    os.killpg(os.getpgid(v_pid), signal.SIGTERM)
                    state.logger.info("Task %s force-fail: sent SIGTERM to vehicle %s PID %d", task_id, vid, v_pid)
                except (ProcessLookupError, OSError):
                    pass
                _active_pids.pop(vid, None)
            v_meta["status"] = "failed"
            v_meta["completed_at"] = now_iso
            v_meta["result"] = f"[Parent container {task_id} force-failed: {reason}]"
            v_meta["v2_state"] = TaskState.ESCALATED.value
            v_hist = v_meta.get("v2_state_history", [])
            v_hist.append({
                "state": TaskState.ESCALATED.value,
                "timestamp": now_iso,
                "reason": f"parent_force_fail: {reason}",
            })
            v_meta["v2_state_history"] = v_hist
            state._broadcast_task_update_sync(vid)
            failed_vehicles.append(vid)

    # --- Broadcast SSE update for container task ---
    state._broadcast_task_update_sync(task_id)

    # --- Agent bus notification ---
    agent_bus.send({
        "from": "leroy",
        "to": "pm",
        "type": "status_update",
        "task_id": task_id,
        "content": f"Task {task_id} force-failed by operator: {reason}",
        "context": (
            f"Previous status: {previous_status}. "
            f"Vehicles failed: {len(failed_vehicles)} ({', '.join(failed_vehicles) if failed_vehicles else 'none'})."
            f"{kill_note}"
        ),
        "requires_response": False,
    })

    state.logger.info(
        "Task %s force-failed by %s: reason=%r previous_status=%s vehicles_failed=%d",
        task_id, client.get("client_id"), reason, previous_status, len(failed_vehicles),
    )

    return JSONResponse({
        "task_id": task_id,
        "previous_status": previous_status,
        "new_status": "failed",
        "reason": reason,
        "vehicles_failed": failed_vehicles,
        "pid_killed": pid,
    })


async def task_archive(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/archive -- Archive a task (hide from default list view).

    Archived tasks are still queryable via ?include_archived=true or status filter.
    This does NOT delete the task.
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    state._task_meta[task_id]["archived"] = True
    state._task_meta[task_id]["archived_at"] = datetime.now(timezone.utc).isoformat()
    state.logger.info("Task %s archived", task_id)
    return JSONResponse({"status": "ok", "task_id": task_id, "archived": True})


async def task_unarchive(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/unarchive -- Restore an archived task to default views."""
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    state._task_meta[task_id]["archived"] = False
    state._task_meta[task_id].pop("archived_at", None)
    state.logger.info("Task %s unarchived", task_id)
    return JSONResponse({"status": "ok", "task_id": task_id, "archived": False})


async def task_review(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/review -- PM approves or rejects a QA review task.

    Body: {"decision": "approved" | "rejected", "reason": "optional rejection reason"}
    Auth: Bearer token required.
    Validates task is in qa_review status.
    Transitions to completed (approved) or failed (rejected).
    Broadcasts SSE update.
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    if state._task_meta[task_id]["status"] != "qa_review":
        return JSONResponse(
            {"error": f"task {task_id} is not in qa_review status (current: {state._task_meta[task_id]['status']})"},
            status_code=409,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    decision = body.get("decision")
    if decision not in ("approved", "rejected"):
        return JSONResponse({"error": "decision must be 'approved' or 'rejected'"}, status_code=400)

    reason = body.get("reason", "")
    now = datetime.now(timezone.utc).isoformat()

    if decision == "approved":
        try:
            if state._state_machine:
                state._state_machine.transition(task_id, TaskState.COMPLETED_VERIFIED, reason="qa_approved")
        except Exception as _sm_err:
            state.logger.warning("State machine transition failed for %s: %s", task_id, _sm_err)
        state._task_meta[task_id]["status"] = "completed"  # fallback / legacy compat
    else:
        try:
            if state._state_machine:
                state._state_machine.transition(task_id, TaskState.FAILED_RETRYABLE, reason="qa_rejected")
        except Exception as _sm_err:
            state.logger.warning("State machine transition failed for %s: %s", task_id, _sm_err)
        state._task_meta[task_id]["status"] = "failed"  # fallback / legacy compat

    state._task_meta[task_id]["review_decision"] = decision
    state._task_meta[task_id]["reviewed_at"] = now
    if reason:
        state._task_meta[task_id]["review_reason"] = reason

    state.logger.info("Task %s review: %s by %s", task_id, decision, client.get("client_id"))
    state._broadcast_task_update_sync(task_id)

    return JSONResponse({
        "status": "ok",
        "task_id": task_id,
        "decision": decision,
        "new_status": state._task_meta[task_id]["status"],
    })


async def task_delete(request: Request) -> JSONResponse:
    """DELETE /tasks/{task_id} -- Hard delete a task (admin only, requires confirmation).

    Body: {"confirm": true, "reason": "why deleting this task"}
    This permanently removes the task and its subtasks from the database.
    Task messages are retained (they may be relevant to other audit purposes).
    NEVER call this on accident -- there is no undo.
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON body with confirm=true required"}, status_code=400)

    if not body.get("confirm"):
        return JSONResponse(
            {
                "error": "Deletion requires confirm=true in request body. "
                         "Tasks are permanent records. Use archive instead for hiding from views.",
                "hint": "POST /tasks/{task_id}/archive to hide without deleting.",
            },
            status_code=400,
        )

    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    reason = body.get("reason", "(no reason given)")
    deleted = state._task_meta.delete(task_id)
    if deleted:
        state.logger.warning("Task %s HARD DELETED by %s. Reason: %s", task_id, client.get("client_id"), reason)
        return JSONResponse({"status": "deleted", "task_id": task_id})
    else:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)


# ---------------------------------------------------------------------------
# Sub-task endpoints
# ---------------------------------------------------------------------------

async def subtask_update(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/subtasks -- Leroy subprocess reports a sub-task update.

    Body: {
        "subtask_id": "string (required)",
        "name": "string (required)",
        "agent": "string (optional)",
        "status": "pending|running|completed|failed",
        "output": "string (optional)",
        "started_at": "ISO string (optional)",
        "completed_at": "ISO string (optional)"
    }
    If subtask_id exists in the task's list, updates it. Otherwise appends.
    """
    task_id = request.path_params["task_id"]
    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    subtask_id = body.get("subtask_id")
    if not subtask_id:
        return JSONResponse({"error": "subtask_id required"}, status_code=400)

    name = body.get("name", "")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)

    now = datetime.now(timezone.utc).isoformat()
    subtask = {
        "subtask_id": subtask_id,
        "task_id": task_id,
        "name": name,
        "agent": body.get("agent", ""),
        "status": body.get("status", "pending"),
        "output": body.get("output", None),
        "started_at": body.get("started_at", None),
        "completed_at": body.get("completed_at", None),
        "updated_at": now,
    }

    action = state._subtask_store.upsert_subtask(task_id, subtask)
    state.logger.info("Task %s: subtask %s %s (status=%s)", task_id, subtask_id, action, subtask["status"])
    state._broadcast_task_update_sync(task_id)
    return JSONResponse({"status": action, "subtask_id": subtask_id})


async def subtask_list(request: Request) -> JSONResponse:
    """GET /tasks/{task_id}/subtasks -- Returns subtasks for a task."""
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    subtasks = state._subtask_store.get(task_id, [])
    return JSONResponse({"subtasks": subtasks, "count": len(subtasks)})


async def task_messages(request: Request) -> JSONResponse:
    """GET /tasks/{task_id}/messages -- Returns PM messages for a specific task."""
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    all_messages = agent_bus.list_messages(limit=200)
    task_msgs = [m for m in all_messages if m.get("task_id") == task_id]
    return JSONResponse({"messages": task_msgs, "count": len(task_msgs)})


async def tasks_stream(request: Request) -> StreamingResponse:
    """GET /tasks/stream -- SSE stream of task updates.

    Sends:
    - Initial snapshot of all tasks on connect
    - Task updates as they happen
    - Heartbeat every 15 seconds
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    state._sse_subscribers.add(queue)

    async def event_generator():
        try:
            # Send initial snapshot
            snapshot = json.dumps({
                "type": "snapshot",
                "tasks": list(state._task_meta.values()),
            })
            yield f"data: {snapshot}\n\n"

            while True:
                try:
                    # Wait for update or timeout for heartbeat
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat
                    heartbeat = json.dumps({
                        "type": "heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    yield f"data: {heartbeat}\n\n"
        except Exception:
            pass
        finally:
            state._sse_subscribers.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Task log helpers and endpoints
# ---------------------------------------------------------------------------

def _parse_target_from_task(task_id: str) -> str:
    """Return 'kush' or 'haze' for a task.

    Priority:
    1. typed_ir JSON field from plan_store (plan.typed_ir -> parse -> .target)
    2. Spec text search for '## target: kush'
    Defaults to 'haze'.
    """
    try:
        store = task_db.plan_store
        if store:
            plan = store.get_plan_by_task(task_id)
            if plan:
                tir_raw = plan.get("typed_ir")
                if tir_raw and isinstance(tir_raw, str):
                    try:
                        tir = json.loads(tir_raw)
                        tgt = (tir.get("target") or "").lower()
                        if tgt in ("kush", "haze"):
                            return tgt
                    except Exception:
                        pass
                # typed_ir may also be stored as dict directly
                elif tir_raw and isinstance(tir_raw, dict):
                    tgt = (tir_raw.get("target") or "").lower()
                    if tgt in ("kush", "haze"):
                        return tgt
    except Exception:
        pass

    # Fallback: scan spec text
    spec = (state._task_meta.get(task_id) or {}).get("spec", "") or ""
    for line in spec.split("\n")[:20]:
        if line.strip().lower().startswith("## target:"):
            t = line.split(":", 1)[1].strip().lower()
            if t in ("kush", "haze"):
                return t
    return "haze"


def _compute_elapsed_seconds(task_id: str) -> float | None:
    """Compute elapsed_seconds for a task.

    Non-terminal: now - created_at.
    Terminal: completed_at - created_at.
    Returns None if timestamps are missing/invalid.
    """
    meta = state._task_meta.get(task_id) or {}
    created_raw = meta.get("created_at", "")
    if not created_raw:
        return None
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except Exception:
        return None

    status = meta.get("status", "")
    if status in _TERMINAL_STATUSES:
        completed_raw = meta.get("completed_at", "")
        if completed_raw:
            try:
                completed = datetime.fromisoformat(completed_raw.replace("Z", "+00:00"))
                return round((completed - created).total_seconds(), 1)
            except Exception:
                pass
        # Terminal but no completed_at -- use now
        return round((datetime.now(timezone.utc) - created).total_seconds(), 1)
    else:
        return round((datetime.now(timezone.utc) - created).total_seconds(), 1)


async def _get_cpu_percent(pid: int) -> float | None:
    """Return CPU% for a PID using asyncio subprocess (non-blocking).

    Uses 'ps -p <pid> -o %cpu=' on macOS/Linux.
    Returns None on any error or if PID not found.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ps", "-p", str(pid), "-o", "%cpu=",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        output = stdout.decode().strip()
        if output:
            return float(output)
    except Exception:
        pass
    return None


async def task_logs(request: Request) -> JSONResponse:
    """GET /tasks/{task_id}/logs -- Tail the task log file with enhanced observability fields."""
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    tail_lines = int(request.query_params.get("tail", "50"))
    log_file = LOGS_DIR / f"{task_id}.log"

    # --- Collect vehicle/container info ---
    vehicle_ids = [
        vid for vid, vm in (state._task_meta or {}).items()
        if vm.get("parent_id") == task_id
    ]
    is_container = len(vehicle_ids) > 0 and not log_file.exists()

    # --- Task status and metadata ---
    meta = (state._task_meta or {}).get(task_id, {})
    status = meta.get("status", "unknown")
    elapsed_seconds = _compute_elapsed_seconds(task_id)

    # --- Target and remote log hint ---
    target = _parse_target_from_task(task_id)
    remote_log_hint = None
    if target == "kush":
        remote_log_hint = f"Log is on kush.local at ~/Projects/ops-agent/logs/{task_id}.log"

    if not log_file.exists():
        base_response = {
            "task_id": task_id,
            "status": status,
            "elapsed_seconds": elapsed_seconds,
            "target": target,
            "remote_log_hint": remote_log_hint,
            "is_container": is_container,
            "vehicle_ids": vehicle_ids if vehicle_ids else None,
            "last_activity": meta.get("last_activity"),
            "stuck_detected": meta.get("_stuck_detected_at"),
        }
        if is_container:
            base_response["error"] = "container task has no log file; see vehicle_ids"
            return JSONResponse(base_response, status_code=200)
        base_response["error"] = "no log file for this task"
        return JSONResponse(base_response, status_code=404)

    try:
        lines = log_file.read_text().splitlines()
        tail = lines[-tail_lines:] if len(lines) > tail_lines else lines
        pid = _active_pids.get(task_id)
        pid_alive = False
        if pid:
            try:
                os.kill(pid, 0)
                pid_alive = True
            except ProcessLookupError:
                pass

        # CPU percent via async subprocess (only if process is alive)
        cpu_percent = None
        if pid and pid_alive:
            cpu_percent = await _get_cpu_percent(pid)

        return JSONResponse({
            "task_id": task_id,
            "status": status,
            "elapsed_seconds": elapsed_seconds,
            "cpu_percent": cpu_percent,
            "target": target,
            "remote_log_hint": remote_log_hint,
            "is_container": is_container,
            "vehicle_ids": vehicle_ids if vehicle_ids else None,
            "log_lines": tail,
            "total_lines": len(lines),
            "showing": len(tail),
            "log_file": str(log_file),
            "process": {"pid": pid, "alive": pid_alive} if pid else None,
            "last_activity": meta.get("last_activity"),
            "stuck_detected": meta.get("_stuck_detected_at"),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def task_logs_stream(request: Request):
    """GET /tasks/{task_id}/logs/stream -- SSE stream of live log lines.

    Events:
    - snapshot: initial last N lines (one event per line)
    - line: new log line appended to file
    - heartbeat: every 10s with status/elapsed
    - waiting: no log file yet (pre-execution); every 2s
    - closed: task deleted or stream terminating
    - error: container task or other error (JSON body)
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    snapshot_lines = int(request.query_params.get("snapshot", "20"))
    log_file = LOGS_DIR / f"{task_id}.log"

    # --- Pre-check: task existence ---
    if (state._task_meta or {}).get(task_id) is None:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    # --- Pre-check: container task ---
    vehicle_ids = [
        vid for vid, vm in (state._task_meta or {}).items()
        if vm.get("parent_id") == task_id
    ]
    if vehicle_ids and not log_file.exists():
        return JSONResponse({
            "error": "container task has no log stream; tail vehicle tasks instead",
            "task_id": task_id,
            "vehicle_ids": vehicle_ids,
        }, status_code=409)

    async def _sse_generator():
        _NO_GROWTH_STALL_SECONDS = 300  # 5 minutes of no growth + dead PID = close
        _HEARTBEAT_INTERVAL = 10.0
        _WAITING_INTERVAL = 2.0
        _POLL_INTERVAL = 0.25  # how often to check for new lines
        _SNAPSHOT_SENT = False

        last_line_count = 0
        last_growth_time = time.monotonic()
        last_heartbeat_time = time.monotonic()

        def _sse_event(event: str, data: str) -> str:
            return f"event: {event}\ndata: {data}\n\n"

        def _heartbeat_data() -> str:
            meta = (state._task_meta or {}).get(task_id) or {}
            return json.dumps({
                "task_id": task_id,
                "status": meta.get("status", "unknown"),
                "elapsed_seconds": _compute_elapsed_seconds(task_id),
            })

        try:
            while True:
                # --- Check task still exists ---
                if (state._task_meta or {}).get(task_id) is None:
                    yield _sse_event("closed", json.dumps({"reason": "task deleted", "task_id": task_id}))
                    return

                meta = (state._task_meta or {}).get(task_id) or {}
                status = meta.get("status", "unknown")
                now = time.monotonic()

                # --- No log file yet: waiting phase ---
                if not log_file.exists():
                    if status in _TERMINAL_STATUSES:
                        yield _sse_event("closed", json.dumps({
                            "reason": "task terminal with no log file",
                            "status": status,
                        }))
                        return

                    yield _sse_event("waiting", json.dumps({
                        "task_id": task_id,
                        "status": status,
                        "message": "waiting for log file to appear",
                    }))
                    await asyncio.sleep(_WAITING_INTERVAL)
                    continue

                # --- Log file exists: read lines ---
                try:
                    all_lines = log_file.read_text().splitlines()
                except Exception as e:
                    yield _sse_event("error", json.dumps({"error": str(e)}))
                    await asyncio.sleep(1.0)
                    continue

                current_count = len(all_lines)

                # --- Snapshot: send last N lines on first read ---
                if not _SNAPSHOT_SENT:
                    snap = all_lines[-snapshot_lines:] if len(all_lines) > snapshot_lines else all_lines
                    for line in snap:
                        yield _sse_event("snapshot", json.dumps({"line": line}))
                    _SNAPSHOT_SENT = True
                    last_line_count = current_count
                    last_growth_time = now
                elif current_count > last_line_count:
                    # New lines appeared
                    new_lines = all_lines[last_line_count:]
                    for line in new_lines:
                        yield _sse_event("line", json.dumps({"line": line}))
                    last_line_count = current_count
                    last_growth_time = now

                # --- Heartbeat (time-based, every 10s) ---
                if now - last_heartbeat_time >= _HEARTBEAT_INTERVAL:
                    yield _sse_event("heartbeat", _heartbeat_data())
                    last_heartbeat_time = now

                # --- Terminal status: close stream ---
                if status in _TERMINAL_STATUSES:
                    yield _sse_event("closed", json.dumps({
                        "reason": "task reached terminal status",
                        "status": status,
                        "task_id": task_id,
                    }))
                    return

                # --- Stall detection: no growth + dead PID for 5min ---
                pid = _active_pids.get(task_id)
                pid_alive = False
                if pid:
                    try:
                        os.kill(pid, 0)
                        pid_alive = True
                    except ProcessLookupError:
                        pass

                if not pid_alive and (now - last_growth_time) >= _NO_GROWTH_STALL_SECONDS:
                    yield _sse_event("closed", json.dumps({
                        "reason": "no log growth and process dead for 5 minutes",
                        "task_id": task_id,
                    }))
                    return

                await asyncio.sleep(_POLL_INTERVAL)

        except asyncio.CancelledError:
            # Client disconnected
            return

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
