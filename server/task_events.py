"""Leroy v2 Task Event Handlers.

Populates the state machine's event handler registry with side effects
that fire on state transitions. Wired in from server.py main().

Event-driven side effects:
- Build completion -> bus message to PM suggesting QA
- Build failure -> classify failure, route to ops (infra) or PM (other)
- Build blocked -> route to ops (infra blocks) or PM (scope blocks)
- Escalation -> Google Chat webhook to Brad
- Plan record updates on completion/failure
- v2 Phase 5C: Auto-persist on COMPLETED_UNVERIFIED, failure pattern persist on FAILED_RETRYABLE
- v2 Phase 6A: Knowledge governance gate before persist (novelty, specificity, non-contradiction)
- v2 Phase 7A: Autonomous PM decision tree (classify, track, route by confidence tier)
"""

import json
import logging

from state_machine import TaskStateMachine, TaskState
from failure_taxonomy import classify_failure, is_infra_failure
from retry_budget import RetryBudget
from notifications import send_webhook_notification
from knowledge_governance import KnowledgeCandidate, evaluate_candidate
from pm_autonomy import (
    classify_decision, should_auto_execute, should_propose, should_escalate,
    PMDecision, ActionType, ConfidenceTier,
)
import agent_bus
import task_db

logger = logging.getLogger("leroy-task-events")


def register_all_handlers(state_machine: TaskStateMachine,
                          retry_budget: RetryBudget,
                          task_meta,
                          broadcast_fn=None,
                          persist_manager=None,
                          action_store=None) -> None:
    """Register all event handlers on the state machine.

    Args:
        state_machine: The TaskStateMachine instance.
        retry_budget: The RetryBudget instance.
        task_meta: PersistentTaskDict for reading task data.
        broadcast_fn: Optional callable(task_id) for SSE broadcast.
        persist_manager: Optional PersistenceManager for brain persist (Phase 5C).
        action_store: Optional PMActionStore for tracking PM decisions (Phase 7B).
    """
    def on_build_completed(event: dict) -> None:
        """RUNNING -> COMPLETED_UNVERIFIED"""
        task_id = event["task_id"]
        # IC-8: Vehicle completion bypass -- dispatcher handles container-level notification
        _vehicle_meta = task_meta.get(task_id) or {}
        if _vehicle_meta.get("parent_id"):
            logger.debug("Task %s: skipping on_build_completed (vehicle of %s)", task_id, _vehicle_meta["parent_id"])
            return
        logger.info("Event: build completed for %s", task_id)

        # v2 Phase 7A: Classify as autonomous PM decision
        decision = classify_decision("build_completed", task_id=task_id)
        if action_store:
            action_store.record(decision)

        # 1. Bus message to PM (always -- even if auto-QA will fire)
        if should_auto_execute(decision):
            # HIGH confidence: notify PM that auto-QA will handle it
            agent_bus.send({
                "from": "leroy",
                "to": "pm",
                "type": "status_update",
                "task_id": task_id,
                "content": f"Build {task_id} completed. Auto-QA triggered (HIGH confidence).",
                "context": "Autonomous PM will generate QA spec. No action needed.",
                "requires_response": False,
            })
            if action_store:
                action_store.update_status(decision.decision_id, "executing")
        elif should_propose(decision):
            # MEDIUM confidence: create proposal, auto-approve in 30 min
            agent_bus.send({
                "from": "leroy",
                "to": "pm",
                "type": "action_required",
                "task_id": task_id,
                "content": f"Build {task_id} completed. QA spec proposed (auto-approve in 30 min).",
                "context": "Review or let auto-approve fire.",
                "requires_response": False,
            })
            if action_store:
                action_store.update_status(decision.decision_id, "pending")
        else:
            # LOW confidence: escalate, require human
            agent_bus.send({
                "from": "leroy",
                "to": "pm",
                "type": "action_required",
                "task_id": task_id,
                "content": f"Build {task_id} completed. QA spec needed (human decision required).",
                "context": "Builder finished execution. Review output and create QA spec.",
                "requires_response": False,
            })

        # 2. Update plan record with duration
        _update_plan_on_completion(task_id, task_meta)

        # 3. SSE broadcast with state event
        _broadcast_state_event(broadcast_fn, task_id, event)

        # 4. v2 Phase 5C + 6A: Evaluate and persist task outcome to brain
        _persist_task_to_brain(task_id, task_meta, persist_manager)

    def on_build_failed(event: dict) -> None:
        """RUNNING -> FAILED_RETRYABLE"""
        task_id = event["task_id"]
        # IC-2: Vehicle failure bypass -- dispatcher handles retry/escalation at container level
        _vehicle_meta = task_meta.get(task_id) or {}
        if _vehicle_meta.get("parent_id"):
            logger.debug("Task %s: skipping on_build_failed (vehicle of %s)", task_id, _vehicle_meta["parent_id"])
            return
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

        # v2 Phase 7A: Classify failure type for autonomous routing
        is_infra = any(is_infra_failure(c) for c in categories)
        is_timeout = "timeout" in reason.lower()

        if is_infra:
            trigger = "build_failed_infra"
        elif is_timeout:
            trigger = "build_failed_timeout"
        else:
            trigger = "build_failed_noninfra"

        decision = classify_decision(trigger, task_id=task_id, meta=meta)
        if action_store:
            action_store.record(decision)

        # 2. Route based on failure type and confidence
        if is_infra:
            agent_bus.send({
                "from": "leroy",
                "to": "ops",
                "type": "infra_alert",
                "task_id": task_id,
                "content": f"Task {task_id} failed with infra issue: {categories_str}. Diagnose and report.",
                "requires_response": False,
            })
            if action_store:
                action_store.update_status(decision.decision_id, "executing")
        else:
            # 3. Check retry budget
            budget_status = retry_budget.get_budget_status(task_id)
            remaining = budget_status.get("remaining", 0)
            used = budget_status.get("used", 0)

            if remaining > 0:
                if should_auto_execute(decision):
                    agent_bus.send({
                        "from": "leroy",
                        "to": "pm",
                        "type": "status_update",
                        "task_id": task_id,
                        "content": (f"Task {task_id} failed ({categories_str}). "
                                    f"Auto-respec triggered. {remaining} retries remaining."),
                        "requires_response": False,
                    })
                elif should_propose(decision):
                    agent_bus.send({
                        "from": "leroy",
                        "to": "pm",
                        "type": "action_required",
                        "task_id": task_id,
                        "content": (f"Task {task_id} failed ({categories_str}). "
                                    f"Respec proposed (auto-approve 30 min). {remaining} retries remaining."),
                        "requires_response": False,
                    })
                else:
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
                # Budget exhausted -> escalate (always LOW)
                escalation = classify_decision("budget_exhausted", task_id=task_id, meta=meta)
                if action_store:
                    action_store.record(escalation)
                try:
                    state_machine.transition(task_id, TaskState.ESCALATED,
                                             reason=f"Retry budget exhausted after {used} attempts")
                except Exception as e:
                    logger.warning("Failed to escalate %s: %s", task_id, e)

        # 4. Update plan record
        _update_plan_on_failure(task_id, task_meta, categories)

        # 5. SSE broadcast
        _broadcast_state_event(broadcast_fn, task_id, event)

        # 6. v2 Phase 5C: Persist failure pattern immediately (bypasses 6A scoring)
        _persist_failure_to_brain(task_id, task_meta, categories, persist_manager)

    def on_build_blocked(event: dict) -> None:
        """RUNNING -> BLOCKED"""
        task_id = event["task_id"]
        # IC-11: Vehicle blocked bypass -- dispatcher handles block at container level
        _vehicle_meta = task_meta.get(task_id) or {}
        if _vehicle_meta.get("parent_id"):
            logger.debug("Task %s: skipping on_build_blocked (vehicle of %s)", task_id, _vehicle_meta["parent_id"])
            return
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
        # IC-16: Vehicle escalation bypass -- dispatcher sends container-level notifications only
        _vehicle_meta = task_meta.get(task_id) or {}
        if _vehicle_meta.get("parent_id"):
            logger.debug("Task %s: skipping on_escalated (vehicle of %s)", task_id, _vehicle_meta["parent_id"])
            return
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

    logger.info("v2 event handlers registered (4 transitions, brain persist=%s, "
                "knowledge governance=enabled, pm autonomy=%s)",
                "enabled" if persist_manager else "disabled",
                "enabled" if action_store else "disabled")


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


def _persist_task_to_brain(task_id: str, task_meta, persist_manager) -> None:
    """v2 Phase 5C + 6A: Evaluate knowledge candidate, then persist if promoted."""
    if persist_manager is None:
        return
    try:
        meta = task_meta.get(task_id) or {}
        result_text = meta.get("result", "") or ""

        # v2 Phase 6A: Knowledge governance gate
        candidate = KnowledgeCandidate(
            content=result_text[:2000],
            source="task_completion",
            task_id=task_id,
        )

        # Look up plan_id for audit trail
        store = task_db.plan_store
        plan = None
        if store:
            plan = store.get_plan_by_task(task_id)
            if plan:
                candidate.plan_id = plan["plan_id"]

        evaluated = evaluate_candidate(candidate, persist_manager)

        if evaluated.decision in ("promoted", "bypassed"):
            success = persist_manager.persist_task(task_id, meta)
        else:
            success = False
            logger.info("Task %s: knowledge candidate discarded (score=%.3f, reason=%s)",
                        task_id, evaluated.composite_score, evaluated.discard_reason)

        # Update plan record with governance decision
        if store and plan:
            store.update_brain_fields(
                plan["plan_id"],
                brain_persisted=success,
                brain_persist_payload=json.dumps({
                    "governance": {
                        "decision": evaluated.decision,
                        "composite_score": evaluated.composite_score,
                        "novelty": evaluated.novelty_score,
                        "specificity": evaluated.specificity_score,
                        "non_contradiction": evaluated.non_contradiction_score,
                        "discard_reason": evaluated.discard_reason,
                        "evaluation_ms": evaluated.evaluation_ms,
                    }
                }),
            )
            logger.info("Task %s: brain_persisted=%s, governance=%s (score=%.3f) on plan %s",
                        task_id, success, evaluated.decision,
                        evaluated.composite_score, plan["plan_id"])
    except Exception as e:
        logger.warning("Task %s: brain persist event handler failed: %s", task_id, e)


def _persist_failure_to_brain(task_id: str, task_meta, categories: list, persist_manager) -> None:
    """v2 Phase 5C: Persist failure pattern immediately.

    Failures bypass Phase 6A scoring -- failure patterns are always novel
    and always worth learning from.
    """
    if persist_manager is None:
        return
    try:
        meta = task_meta.get(task_id) or {}
        success = persist_manager.persist_task(task_id, meta)

        store = task_db.plan_store
        if store:
            plan = store.get_plan_by_task(task_id)
            if plan:
                store.update_brain_fields(
                    plan["plan_id"],
                    brain_persisted=success,
                    brain_persist_payload=json.dumps({
                        "governance": {
                            "decision": "bypassed",
                            "reason": "failure_pattern_always_promoted",
                            "categories": [c.value for c in categories],
                        }
                    }),
                )
    except Exception as e:
        logger.warning("Task %s: failure persist failed: %s", task_id, e)


def _broadcast_state_event(broadcast_fn, task_id: str, event: dict,
                           severity: str = "info") -> None:
    """Broadcast a state machine event via SSE."""
    if broadcast_fn:
        try:
            broadcast_fn(task_id)
        except Exception:
            pass
