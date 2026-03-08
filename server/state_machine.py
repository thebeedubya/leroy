"""Leroy v2 Task State Machine.

Defines all valid task states, legal transitions, and an event-driven
state machine that enforces transition rules, records history, and
emits events for downstream handlers.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Callable
import logging
import threading

logger = logging.getLogger("leroy-state-machine")


class TaskState(str, Enum):
    NEW = "new"
    ANALYZED = "analyzed"
    PLANNED = "planned"
    RUNNING = "running"
    BLOCKED = "blocked"
    FAILED_RETRYABLE = "failed_retryable"
    ESCALATED = "escalated"
    COMPLETED_UNVERIFIED = "completed_unverified"
    COMPLETED_VERIFIED = "completed_verified"
    PERSISTED = "persisted"
    ARCHIVED = "archived"


VALID_TRANSITIONS: dict[TaskState, list[TaskState]] = {
    TaskState.NEW: [TaskState.ANALYZED, TaskState.RUNNING],  # RUNNING for Phase 0 simplified flow
    TaskState.ANALYZED: [TaskState.PLANNED],
    TaskState.PLANNED: [TaskState.RUNNING],
    TaskState.RUNNING: [TaskState.BLOCKED, TaskState.FAILED_RETRYABLE, TaskState.COMPLETED_UNVERIFIED],
    TaskState.BLOCKED: [TaskState.RUNNING, TaskState.FAILED_RETRYABLE],
    TaskState.FAILED_RETRYABLE: [TaskState.ANALYZED, TaskState.RUNNING, TaskState.ESCALATED],  # RUNNING for Phase 0 simplified retry
    TaskState.COMPLETED_UNVERIFIED: [TaskState.COMPLETED_VERIFIED, TaskState.FAILED_RETRYABLE, TaskState.ARCHIVED],  # ARCHIVED for Phase 0
    TaskState.COMPLETED_VERIFIED: [TaskState.PERSISTED],
    TaskState.PERSISTED: [TaskState.ARCHIVED],
    TaskState.ESCALATED: [],  # terminal
    TaskState.ARCHIVED: [],   # terminal
}


class IllegalTransitionError(Exception):
    """Raised when a state transition is not allowed."""

    def __init__(self, task_id: str, from_state: TaskState, to_state: TaskState):
        self.task_id = task_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Illegal transition for task {task_id}: {from_state.value} -> {to_state.value}. "
            f"Valid targets: {[s.value for s in VALID_TRANSITIONS.get(from_state, [])]}"
        )


class TaskStateMachine:
    """Validates and executes state transitions for tasks.

    Stores state and history in the task metadata dict (backed by SQLite
    via PersistentTaskDict). Emits event dicts on every transition.
    Supports registering handlers for specific transitions.
    """

    def __init__(self, task_meta):
        """Initialize with a task_meta dict-like object (PersistentTaskDict)."""
        self._task_meta = task_meta
        self._handlers: dict[tuple[TaskState, TaskState], list[Callable]] = {}
        self._global_handlers: list[Callable] = []
        self._lock = threading.Lock()

    def initialize_task(self, task_id: str) -> dict:
        """Set a task's initial state to NEW. Returns the event dict.

        Call this when a new task is created. Sets up state tracking fields
        in task metadata.
        """
        now = datetime.now(timezone.utc).isoformat()
        meta = self._task_meta.get(task_id)
        if meta is None:
            raise KeyError(f"Task {task_id} not found in task_meta")

        meta["v2_state"] = TaskState.NEW.value
        meta["v2_state_history"] = [
            {"state": TaskState.NEW.value, "timestamp": now, "reason": "task_created"}
        ]
        self._task_meta[task_id] = dict(meta)

        event = {
            "task_id": task_id,
            "from_state": None,
            "to_state": TaskState.NEW.value,
            "reason": "task_created",
            "timestamp": now,
        }
        logger.info("Task %s: initialized to NEW", task_id)
        return event

    def transition(self, task_id: str, to_state: TaskState, reason: str = "") -> dict:
        """Validate and execute a state transition.

        Returns event dict: {task_id, from_state, to_state, reason, timestamp}.
        Raises IllegalTransitionError if the transition is not valid.
        """
        with self._lock:
            meta = self._task_meta.get(task_id)
            if meta is None:
                raise KeyError(f"Task {task_id} not found in task_meta")

            current_state_str = meta.get("v2_state", TaskState.NEW.value)
            try:
                from_state = TaskState(current_state_str)
            except ValueError:
                from_state = TaskState.NEW

            # Validate transition
            valid_targets = VALID_TRANSITIONS.get(from_state, [])
            if to_state not in valid_targets:
                raise IllegalTransitionError(task_id, from_state, to_state)

            # Execute transition
            now = datetime.now(timezone.utc).isoformat()
            meta["v2_state"] = to_state.value
            # Also update legacy status field for backward compat
            meta["status"] = self._map_to_legacy_status(to_state)

            # Append to history
            history = meta.get("v2_state_history", [])
            history.append({
                "state": to_state.value,
                "timestamp": now,
                "reason": reason,
            })
            meta["v2_state_history"] = history
            self._task_meta[task_id] = dict(meta)

        event = {
            "task_id": task_id,
            "from_state": from_state.value,
            "to_state": to_state.value,
            "reason": reason,
            "timestamp": now,
        }
        logger.info(
            "Task %s: %s -> %s (reason: %s)",
            task_id, from_state.value, to_state.value, reason or "none",
        )

        # Fire registered handlers (outside lock)
        self._fire_handlers(from_state, to_state, event)

        # Fire global handlers (e.g., SSE broadcast)
        for handler in self._global_handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error("Global handler error: %s", e, exc_info=True)

        return event

    def get_state(self, task_id: str) -> TaskState:
        """Get current state for a task."""
        meta = self._task_meta.get(task_id)
        if meta is None:
            raise KeyError(f"Task {task_id} not found in task_meta")
        state_str = meta.get("v2_state", TaskState.NEW.value)
        try:
            return TaskState(state_str)
        except ValueError:
            return TaskState.NEW

    def get_history(self, task_id: str) -> list[dict]:
        """Get full transition history for a task."""
        meta = self._task_meta.get(task_id)
        if meta is None:
            raise KeyError(f"Task {task_id} not found in task_meta")
        return list(meta.get("v2_state_history", []))

    def register_handler(self, from_state: TaskState, to_state: TaskState, handler: Callable) -> None:
        """Register an event handler for a specific transition.

        Handler receives the event dict as its only argument.
        Multiple handlers per transition are allowed.
        """
        key = (from_state, to_state)
        if key not in self._handlers:
            self._handlers[key] = []
        self._handlers[key].append(handler)
        logger.debug("Registered handler for %s -> %s", from_state.value, to_state.value)

    def register_global_handler(self, handler: Callable) -> None:
        """Register a handler that fires on every state transition.

        Used for SSE broadcasting and activity logging.
        """
        self._global_handlers.append(handler)
        logger.debug("Registered global handler")

    def _fire_handlers(self, from_state: TaskState, to_state: TaskState, event: dict) -> None:
        """Fire all registered handlers for a transition."""
        key = (from_state, to_state)
        handlers = self._handlers.get(key, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(
                    "Handler error for %s -> %s: %s",
                    from_state.value, to_state.value, e,
                    exc_info=True,
                )

    @staticmethod
    def _map_to_legacy_status(state: TaskState) -> str:
        """Map v2 state to legacy status string for backward compatibility."""
        mapping = {
            TaskState.NEW: "pending",
            TaskState.ANALYZED: "pending",
            TaskState.PLANNED: "pending",
            TaskState.RUNNING: "working",
            TaskState.BLOCKED: "working",
            TaskState.FAILED_RETRYABLE: "failed",
            TaskState.ESCALATED: "failed",
            TaskState.COMPLETED_UNVERIFIED: "completed",
            TaskState.COMPLETED_VERIFIED: "completed",
            TaskState.PERSISTED: "completed",
            TaskState.ARCHIVED: "completed",
        }
        return mapping.get(state, "pending")
