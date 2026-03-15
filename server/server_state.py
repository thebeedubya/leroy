"""Shared server state -- module-level globals set by main() in server.py.

All route handler modules import from here instead of referencing server.py globals directly.
This is the same pattern used by execution.py.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import agent_bus
import task_db
import persist_manager as pm
from state_machine import TaskStateMachine, TaskState
from retry_budget import RetryBudget
from pm_autonomy import PMActionStore
from task_queue import TaskQueue
from agent_bus import WebhookRegistry

logger = logging.getLogger("leroy-a2a")

# ---------------------------------------------------------------------------
# Task storage (A2A SDK store + persistent custom metadata)
# ---------------------------------------------------------------------------
_task_store = None  # set in main() after task_db.init()
_START_TIME = time.time()

# Custom task metadata: task_id -> {spec, status, result, created_at, ...}
_task_meta: task_db.PersistentTaskDict | None = None

# Sub-task tracking: task_id -> list of subtask dicts
_subtask_store: task_db.PersistentSubtaskStore | None = None

# Agent registry and activity event store
_agent_store: task_db.AgentStore | None = None
_activity_store: task_db.ActivityStore | None = None

# v2 State machine + retry budget
_action_store: PMActionStore | None = None
_task_queue: TaskQueue | None = None
_webhook_registry: WebhookRegistry | None = None
_state_machine: TaskStateMachine | None = None
_retry_budget: RetryBudget | None = None

# Dispatcher Phase 3a
_dispatcher = None

# SSE subscribers
_sse_subscribers: set = set()
_sse_lock = asyncio.Lock()
_activity_sse_subscribers: set = set()

# Hook event storage
_HOOK_EVENTS_MAX = 5000
_hook_events: list[dict] = []
_task_hook_events: dict[str, list[dict]] = {}
_session_to_task: dict[str, str] = {}
_hook_sse_subscribers: list[asyncio.Queue] = []

# Proposal store
_proposal_store: task_db.ProposalStore | None = None

# Persistence manager
_persist_manager = pm.PersistenceManager()


# ---------------------------------------------------------------------------
# Broadcast helpers
# ---------------------------------------------------------------------------
async def _broadcast_task_update(task_id: str) -> None:
    """Broadcast a task update to all SSE subscribers."""
    if not _sse_subscribers:
        return
    task = _task_meta.get(task_id)
    if not task:
        return
    event_data = json.dumps({"type": "task_update", "task": dict(task)})
    dead = set()
    for queue in list(_sse_subscribers):
        try:
            queue.put_nowait(event_data)
        except asyncio.QueueFull:
            dead.add(queue)
    for q in dead:
        _sse_subscribers.discard(q)


def _broadcast_task_update_sync(task_id: str) -> None:
    """Thread-safe broadcast from sync context."""
    if not _sse_subscribers:
        return
    task = _task_meta.get(task_id)
    if not task:
        return
    event_data = json.dumps({"type": "task_update", "task": dict(task)})
    dead = set()
    for queue in list(_sse_subscribers):
        try:
            queue.put_nowait(event_data)
        except Exception:
            dead.add(queue)
    for q in dead:
        _sse_subscribers.discard(q)

def _broadcast_state_transition(task_id: str, from_state: str, to_state: str,
                                reason: str = "", failure_categories: list | None = None) -> None:
    """Broadcast a state machine transition event via SSE with full metadata."""
    if not _sse_subscribers:
        return
    # IC-12: Include parent_id so dashboard can filter vehicle events
    parent_id = (_task_meta.get(task_id) or {}).get("parent_id") if _task_meta else None
    event_data = json.dumps({
        "type": "state_transition",
        "task_id": task_id,
        "from_state": from_state,
        "to_state": to_state,
        "reason": reason,
        "failure_categories": failure_categories or [],
        "parent_id": parent_id,
    })
    dead = set()
    for queue in list(_sse_subscribers):
        try:
            queue.put_nowait(event_data)
        except Exception:
            dead.add(queue)
    for q in dead:
        _sse_subscribers.discard(q)


def _emit_activity(agent: str, event_type: str, summary: str,
                   detail: str | None = None, task_id: str | None = None,
                   severity: str = "info") -> None:
    """Emit an activity event and broadcast to SSE subscribers."""
    if _activity_store is None:
        return
    evt = _activity_store.append(agent, event_type, summary, detail, task_id, severity)
    evt_data = json.dumps({"type": "activity_event", "event": evt})
    dead = set()
    for queue in list(_activity_sse_subscribers):
        try:
            queue.put_nowait(evt_data)
        except Exception:
            dead.add(queue)
    for q in dead:
        _activity_sse_subscribers.discard(q)


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------
def _check_auth(request) -> dict | None:
    """Validate bearer token from request. Returns client meta or None.

    Returns None (auth passes) if auth is disabled (no tokens loaded).
    """
    import auth
    if not auth.is_auth_enabled():
        return {"client_id": "anonymous", "source": "unknown"}

    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    return auth.validate_token(token)
