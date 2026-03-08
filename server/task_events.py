"""Leroy v2 Task Event Handlers.

Populates the state machine's event handler registry with side effects
that fire on state transitions. Wired in from server.py main().

Event-driven side effects:
- Build completion -> bus message to PM suggesting QA
- Build failure -> classify failure, route to ops (infra) or PM (other)
- Build blocked -> route to ops (infra blocks) or PM (scope blocks)
- Escalation -> Google Chat webhook to Brad
- Plan record updates on completion/failure
"""

import json
import logging
import time

from state_machine import TaskStateMachine, TaskState
from failure_taxonomy import classify_failure, is_infra_failure
from retry_budget import RetryBudget
from notifications import send_webhook_notification
import agent_bus
import task_db

logger = logging.getLogger("leroy-task-events")


def register_all_handlers(state_machine: TaskStateMachine,
                          retry_budget: RetryBudget,
                          task_meta,
                          broadcast_fn=None) -> None:
    """Register all event handlers on the state machine.

    Args:
        state_machine: The TaskStateMachine instance.
        retry_budget: The RetryBudget instance.
        task_meta: PersistentTaskDict for reading task data.
        broadcast_fn: Optional callable(task_id) for SSE broadcast.
    """
    def on_build_completed(event: dict) -> None:
        """RUNNING -> COMPLETED_UNVERIFIED"""
        task_id = event["task_id"]
        logger.info("Event: build completed for %s", task_id)

        # 1. Bus message to PM suggesting QA
        agent_bus.send({
            "from": "leroy",
            "to": "pm",
            "type": "action_required",
            "task_id": task_id,
            "content": f"Build {task_id} completed. QA spec needed.",
            "context": "Builder finished execution. Review output and create QA spec.",
            "requires_response": False,
        })

        # 2. Update plan record with duration
        _update_plan_on_completion(task_id, task_meta)

        # 3. SSE broadcast with state event
        _broadcast_state_event(broadcast_fn, task_id, event)

    def on_build_failed(event: dict) -> None:
        """RUNNING -> FAILED_RETRYABLE"""
        task_id = event["task_id"]
        reason = event.get("reason", "")
        logger.info("Event: build failed for %s (reason: %s)", task_id, reason)

        # 1. Classify failure
        meta = task_meta.get(task_id) or {}
        result_text = meta.get("result", "") or meta.get("partial_result", "")
        categories = classify_failure(result_text, meta)
        categories_str = ", ".join(c.value for c in categories) if categories else "unknown"

        # Store failure categories in meta
        meta_update = task_meta.get(task_id)
        if meta_update:
            meta_update["failure_categories"] = [c.value for c in categories]
            task_meta[task_id] = dict(meta_update)

        # 2. Route based on failure type
        if any(is_infra_failure(c) for c in categories):
            agent_bus.send({
                "from": "leroy",
                "to": "ops",
                "type": "infra_alert",
                "task_id": task_id,
                "content": f"Task {task_id} failed with infra issue: {categories_str}. Diagnose and report.",
                "requires_response": False,
            })
        else:
            # 3. Check retry budget
            budget_status = retry_budget.get_budget_status(task_id)
            remaining = budget_status.get("remaining", 0)
            used = budget_status.get("used", 0)

            if remaining > 0:
                agent_bus.send({
                    "from": "leroy",
                    "to": "pm",
                    "type": "action_required",
                    "task_id": task_id,
                    "content": (f"Task {task_id} failed ({categories_str}). "
                                f"{remaining} retries remaining. Respec recommended."),
                    "requires_response": False,
                })
            else:
                # Budget exhausted -> escalate
                try:
                    state_machine.transition(task_id, TaskState.ESCALATED,
                                             reason=f"Retry budget exhausted after {used} attempts")
                except Exception as e:
                    logger.warning("Failed to escalate %s: %s", task_id, e)

        # 4. Update plan record
        _update_plan_on_failure(task_id, task_meta, categories)

        # 5. SSE broadcast
        _broadcast_state_event(broadcast_fn, task_id, event)

    def on_build_blocked(event: dict) -> None:
        """RUNNING -> BLOCKED"""
        task_id = event["task_id"]
        block_reason = event.get("reason", "Unknown")
        logger.info("Event: build blocked for %s (reason: %s)", task_id, block_reason)

        # Route based on block type
        if any(kw in block_reason.lower() for kw in ("infra", "service", "port", "unreachable", "connection")):
            agent_bus.send({
                "from": "leroy",
                "to": "ops",
                "type": "infra_alert",
                "task_id": task_id,
                "content": f"Task {task_id} blocked: {block_reason}. Diagnose.",
                "requires_response": False,
            })
        else:
            agent_bus.send({
                "from": "leroy",
                "to": "pm",
                "type": "action_required",
                "task_id": task_id,
                "content": f"Task {task_id} blocked: {block_reason}. Decision needed.",
                "requires_response": False,
            })

        _broadcast_state_event(broadcast_fn, task_id, event)

    def on_escalated(event: dict) -> None:
        """FAILED_RETRYABLE -> ESCALATED"""
        task_id = event["task_id"]
        reason = event.get("reason", "Retry budget exhausted")
        logger.info("Event: task %s ESCALATED (%s)", task_id, reason)

        # 1. Google Chat webhook to Brad
        send_webhook_notification(
            f"ESCALATED: Task {task_id} — {reason}. Human action required.",
            task_id=task_id,
            severity="critical",
        )

        # 2. Bus message to PM
        agent_bus.send({
            "from": "leroy",
            "to": "pm",
            "type": "escalation",
            "task_id": task_id,
            "content": f"ESCALATED: Task {task_id}. {reason}. All retries exhausted.",
            "requires_response": False,
        })

        # 3. SSE broadcast
        _broadcast_state_event(broadcast_fn, task_id, event, severity="critical")

    # Register all handlers
    state_machine.register_handler(TaskState.RUNNING, TaskState.COMPLETED_UNVERIFIED, on_build_completed)
    state_machine.register_handler(TaskState.RUNNING, TaskState.FAILED_RETRYABLE, on_build_failed)
    state_machine.register_handler(TaskState.RUNNING, TaskState.BLOCKED, on_build_blocked)
    state_machine.register_handler(TaskState.FAILED_RETRYABLE, TaskState.ESCALATED, on_escalated)

    logger.info("v2 event handlers registered (4 transitions)")


def _update_plan_on_completion(task_id: str, task_meta) -> None:
    """Update plan record with completion data."""
    try:
        store = task_db.plan_store
        if store is None:
            return
        plan = store.get_plan_by_task(task_id)
        if not plan:
            return
        meta = task_meta.get(task_id) or {}
        created = meta.get("created_at", "")
        duration = None
        if created and meta.get("completed_at"):
            try:
                from datetime import datetime
                t0 = datetime.fromisoformat(created)
                t1 = datetime.fromisoformat(meta["completed_at"])
                duration = int((t1 - t0).total_seconds())
            except Exception:
                pass
        token = meta.get("token_usage", {})
        store.update_outcome(
            plan["plan_id"],
            status="completed_unverified",
            duration_seconds=duration,
            token_usage_input=token.get("input"),
            token_usage_output=token.get("output"),
            estimated_cost_usd=token.get("estimated_cost_usd"),
        )
    except Exception as e:
        logger.warning("Failed to update plan on completion: %s", e)


def _update_plan_on_failure(task_id: str, task_meta, categories: list) -> None:
    """Update plan record with failure data."""
    try:
        store = task_db.plan_store
        if store is None:
            return
        plan = store.get_plan_by_task(task_id)
        if not plan:
            return
        store.update_outcome(
            plan["plan_id"],
            status="failed",
            failure_categories=[c.value for c in categories],
        )
    except Exception as e:
        logger.warning("Failed to update plan on failure: %s", e)


def _broadcast_state_event(broadcast_fn, task_id: str, event: dict,
                           severity: str = "info") -> None:
    """Broadcast a state machine event via SSE."""
    if broadcast_fn:
        try:
            broadcast_fn(task_id)
        except Exception:
            pass
