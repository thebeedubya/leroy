"""Leroy v2 Phase 7: Autonomous PM (Constrained Coordinator).

Three confidence tiers for PM decisions:
  HIGH   = auto-execute (no approval): auto-QA, auto-retro, auto-persist
  MEDIUM = proposal + 30-min auto-approve: respec on timeout, respec on non-infra failure
  LOW    = always escalate to Brad: unknown failure, budget exhausted, contradiction

Decision outcomes tracked in pm_actions table. Autonomy envelope is data-driven:
  >90% correct over 20 instances = promote confidence tier
  <70% correct over 20 instances = demote confidence tier

7A: Decision tree with confidence tiers
7B: Tracked PM actions in SQLite (pm_actions table)
7C: Autonomy expansion protocol
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

logger = logging.getLogger("leroy-pm-autonomy")


# ---------------------------------------------------------------------------
# 7A: Decision Tree
# ---------------------------------------------------------------------------

class ConfidenceTier(str, Enum):
    HIGH = "high"       # Auto-execute, no approval needed
    MEDIUM = "medium"   # Proposal + 30-min auto-approve
    LOW = "low"         # Always escalate to Brad


class ActionType(str, Enum):
    AUTO_QA = "auto_qa"                   # Generate QA spec after build completion
    AUTO_RETRO = "auto_retro"             # Write retrospective after QA
    AUTO_PERSIST = "auto_persist"         # Persist to brain (handled by Phase 5C/6A)
    RESPEC_TIMEOUT = "respec_timeout"     # Respec after inactivity timeout
    RESPEC_FAILURE = "respec_failure"     # Respec after non-infra failure
    ROUTE_TO_OPS = "route_to_ops"         # Route infra failure to ops
    ESCALATE = "escalate"                 # Escalate to Brad


# Default confidence assignments (can be promoted/demoted by 7C)
_DEFAULT_CONFIDENCE = {
    ActionType.AUTO_QA: ConfidenceTier.HIGH,
    ActionType.AUTO_RETRO: ConfidenceTier.HIGH,
    ActionType.AUTO_PERSIST: ConfidenceTier.HIGH,
    ActionType.ROUTE_TO_OPS: ConfidenceTier.HIGH,
    ActionType.RESPEC_TIMEOUT: ConfidenceTier.MEDIUM,
    ActionType.RESPEC_FAILURE: ConfidenceTier.MEDIUM,
    ActionType.ESCALATE: ConfidenceTier.LOW,
}

# Runtime confidence map (modified by autonomy expansion)
_confidence_map: dict[ActionType, ConfidenceTier] = dict(_DEFAULT_CONFIDENCE)

# Auto-approve window for MEDIUM tier (seconds)
MEDIUM_AUTO_APPROVE_SECONDS = 1800  # 30 minutes


@dataclass
class PMDecision:
    """A decision made by the autonomous PM."""
    decision_id: str = ""
    action_type: ActionType = ActionType.ESCALATE
    confidence: ConfidenceTier = ConfidenceTier.LOW
    task_id: str = ""
    plan_id: str = ""
    trigger_event: str = ""       # What triggered this decision
    reasoning: str = ""           # Why PM chose this action
    payload: dict = field(default_factory=dict)  # Action-specific data
    status: str = "pending"       # pending, approved, executing, completed, failed, rejected
    outcome_correct: bool | None = None  # Set after human review or auto-evaluation
    created_at: str = ""
    resolved_at: str = ""

    def __post_init__(self):
        if not self.decision_id:
            self.decision_id = uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


def classify_decision(trigger: str, task_id: str = "", meta: dict | None = None) -> PMDecision:
    """Classify a trigger event into a PM decision with confidence tier.

    Args:
        trigger: Event type (e.g., "build_completed", "build_failed_timeout", "build_failed_noninfra")
        task_id: Related task ID.
        meta: Optional task metadata for context.

    Returns:
        PMDecision with action_type and confidence populated.
    """
    meta = meta or {}

    if trigger == "build_completed":
        return PMDecision(
            action_type=ActionType.AUTO_QA,
            confidence=_confidence_map[ActionType.AUTO_QA],
            task_id=task_id,
            trigger_event=trigger,
            reasoning="Build completed successfully. QA spec should be generated automatically.",
        )

    elif trigger == "qa_completed":
        return PMDecision(
            action_type=ActionType.AUTO_RETRO,
            confidence=_confidence_map[ActionType.AUTO_RETRO],
            task_id=task_id,
            trigger_event=trigger,
            reasoning="QA completed. Retrospective should be written automatically.",
        )

    elif trigger == "build_failed_infra":
        return PMDecision(
            action_type=ActionType.ROUTE_TO_OPS,
            confidence=_confidence_map[ActionType.ROUTE_TO_OPS],
            task_id=task_id,
            trigger_event=trigger,
            reasoning="Infrastructure failure detected. Routing to ops for diagnosis.",
        )

    elif trigger == "build_failed_timeout":
        return PMDecision(
            action_type=ActionType.RESPEC_TIMEOUT,
            confidence=_confidence_map[ActionType.RESPEC_TIMEOUT],
            task_id=task_id,
            trigger_event=trigger,
            reasoning="Build timed out. Respec with smaller scope or explicit output discipline.",
            payload={"respec_hint": "break into smaller phases, add stdout frequency warning"},
        )

    elif trigger == "build_failed_noninfra":
        categories = meta.get("failure_categories", [])
        return PMDecision(
            action_type=ActionType.RESPEC_FAILURE,
            confidence=_confidence_map[ActionType.RESPEC_FAILURE],
            task_id=task_id,
            trigger_event=trigger,
            reasoning=f"Non-infra failure ({', '.join(categories)}). Respec with failure context.",
            payload={"failure_categories": categories},
        )

    elif trigger == "budget_exhausted":
        return PMDecision(
            action_type=ActionType.ESCALATE,
            confidence=ConfidenceTier.LOW,  # Always LOW regardless of expansion
            task_id=task_id,
            trigger_event=trigger,
            reasoning="Retry budget exhausted. Human decision required.",
        )

    else:
        return PMDecision(
            action_type=ActionType.ESCALATE,
            confidence=ConfidenceTier.LOW,
            task_id=task_id,
            trigger_event=trigger,
            reasoning=f"Unknown trigger '{trigger}'. Escalating to Brad.",
        )


def should_auto_execute(decision: PMDecision) -> bool:
    """Whether this decision should execute without approval."""
    return decision.confidence == ConfidenceTier.HIGH


def should_propose(decision: PMDecision) -> bool:
    """Whether this decision should create a proposal for Brad."""
    return decision.confidence == ConfidenceTier.MEDIUM


def should_escalate(decision: PMDecision) -> bool:
    """Whether this decision should escalate immediately."""
    return decision.confidence == ConfidenceTier.LOW


# ---------------------------------------------------------------------------
# 7B: PM Actions Store
# ---------------------------------------------------------------------------

class PMActionStore:
    """SQLite-backed store for tracking all PM decisions and their outcomes.

    Schema added to task_db via migration.
    """

    def __init__(self, db):
        self._db = db
        self._ensure_table()

    def _ensure_table(self):
        """Create pm_actions table if not exists."""
        with self._db._write_lock:
            self._db._conn.executescript("""
                CREATE TABLE IF NOT EXISTS pm_actions (
                    decision_id TEXT PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    task_id TEXT,
                    plan_id TEXT,
                    trigger_event TEXT,
                    reasoning TEXT,
                    payload TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    outcome_correct BOOLEAN,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    proposal_id TEXT,
                    auto_approve_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_pm_actions_task ON pm_actions(task_id);
                CREATE INDEX IF NOT EXISTS idx_pm_actions_type ON pm_actions(action_type);
                CREATE INDEX IF NOT EXISTS idx_pm_actions_status ON pm_actions(status);
                CREATE INDEX IF NOT EXISTS idx_pm_actions_created ON pm_actions(created_at DESC);
            """)
            self._db._conn.commit()

    def record(self, decision: PMDecision, proposal_id: str | None = None) -> dict:
        """Record a PM decision."""
        auto_approve_at = None
        if decision.confidence == ConfidenceTier.MEDIUM:
            from datetime import timedelta
            auto_at = datetime.fromisoformat(decision.created_at) + timedelta(seconds=MEDIUM_AUTO_APPROVE_SECONDS)
            auto_approve_at = auto_at.isoformat()

        with self._db._write_lock:
            self._db._conn.execute(
                """INSERT OR REPLACE INTO pm_actions
                   (decision_id, action_type, confidence, task_id, plan_id,
                    trigger_event, reasoning, payload, status, outcome_correct,
                    created_at, resolved_at, proposal_id, auto_approve_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.decision_id,
                    decision.action_type.value,
                    decision.confidence.value,
                    decision.task_id,
                    decision.plan_id,
                    decision.trigger_event,
                    decision.reasoning,
                    json.dumps(decision.payload) if decision.payload else None,
                    decision.status,
                    decision.outcome_correct,
                    decision.created_at,
                    decision.resolved_at or None,
                    proposal_id,
                    auto_approve_at,
                ),
            )
            self._db._conn.commit()

        logger.info("PM action recorded: %s (type=%s, confidence=%s, task=%s)",
                     decision.decision_id, decision.action_type.value,
                     decision.confidence.value, decision.task_id[:8] if decision.task_id else "?")
        return self.get(decision.decision_id)

    def update_status(self, decision_id: str, status: str,
                      outcome_correct: bool | None = None) -> None:
        """Update action status and optional outcome."""
        now = datetime.now(timezone.utc).isoformat()
        resolved = now if status in ("completed", "failed", "rejected") else None
        with self._db._write_lock:
            self._db._conn.execute(
                """UPDATE pm_actions SET status = ?, outcome_correct = ?,
                   resolved_at = COALESCE(?, resolved_at)
                   WHERE decision_id = ?""",
                (status, outcome_correct, resolved, decision_id),
            )
            self._db._conn.commit()

    def get(self, decision_id: str) -> dict | None:
        """Get a single action by ID."""
        row = self._db._conn.execute(
            "SELECT * FROM pm_actions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_actions(self, action_type: str | None = None, status: str | None = None,
                     limit: int = 50) -> list[dict]:
        """List actions with optional filters."""
        query = "SELECT * FROM pm_actions WHERE 1=1"
        params = []
        if action_type:
            query += " AND action_type = ?"
            params.append(action_type)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def pending_auto_approvals(self) -> list[dict]:
        """Find MEDIUM-tier proposals past their auto-approve deadline."""
        now = datetime.now(timezone.utc).isoformat()
        rows = self._db._conn.execute(
            """SELECT * FROM pm_actions
               WHERE confidence = 'medium' AND status = 'pending'
               AND auto_approve_at IS NOT NULL AND auto_approve_at <= ?
               ORDER BY created_at""",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]

    def action_stats(self, action_type: str | None = None) -> dict:
        """Get outcome statistics for autonomy expansion.

        Returns: {total, correct, incorrect, pending, accuracy}
        """
        query = "SELECT outcome_correct, COUNT(*) as cnt FROM pm_actions WHERE 1=1"
        params = []
        if action_type:
            query += " AND action_type = ?"
            params.append(action_type)
        query += " GROUP BY outcome_correct"
        rows = self._db._conn.execute(query, params).fetchall()

        stats = {"total": 0, "correct": 0, "incorrect": 0, "pending": 0}
        for row in rows:
            cnt = row["cnt"]
            stats["total"] += cnt
            if row["outcome_correct"] is True:
                stats["correct"] += cnt
            elif row["outcome_correct"] is False:
                stats["incorrect"] += cnt
            else:
                stats["pending"] += cnt

        evaluated = stats["correct"] + stats["incorrect"]
        stats["accuracy"] = round(stats["correct"] / evaluated, 3) if evaluated > 0 else None
        stats["evaluated"] = evaluated
        return stats


# ---------------------------------------------------------------------------
# 7C: Autonomy Expansion Protocol
# ---------------------------------------------------------------------------

# Thresholds for promotion/demotion
PROMOTE_THRESHOLD = 0.90     # >90% correct over 20 instances = promote
DEMOTE_THRESHOLD = 0.70      # <70% correct over 20 instances = demote
MIN_INSTANCES_FOR_CHANGE = 20  # Minimum evaluated instances before tier change

# Promotion/demotion paths
_PROMOTION_PATH = {
    ConfidenceTier.LOW: ConfidenceTier.MEDIUM,
    ConfidenceTier.MEDIUM: ConfidenceTier.HIGH,
    ConfidenceTier.HIGH: ConfidenceTier.HIGH,  # Already max
}
_DEMOTION_PATH = {
    ConfidenceTier.HIGH: ConfidenceTier.MEDIUM,
    ConfidenceTier.MEDIUM: ConfidenceTier.LOW,
    ConfidenceTier.LOW: ConfidenceTier.LOW,  # Already min
}

# Actions that cannot be promoted past MEDIUM (always need some oversight)
_PROMOTION_CEILING = {
    ActionType.ESCALATE: ConfidenceTier.LOW,  # Escalation is always LOW
}


def evaluate_autonomy(action_store: PMActionStore) -> dict:
    """Evaluate all action types and adjust confidence tiers.

    Returns: {changes: [{action_type, old_tier, new_tier, accuracy, evaluated}]}
    """
    changes = []

    for action_type in ActionType:
        # Skip actions with hard ceilings
        if action_type in _PROMOTION_CEILING:
            continue

        stats = action_store.action_stats(action_type.value)
        evaluated = stats["evaluated"]
        accuracy = stats["accuracy"]

        if evaluated < MIN_INSTANCES_FOR_CHANGE or accuracy is None:
            continue

        current_tier = _confidence_map.get(action_type, ConfidenceTier.LOW)

        if accuracy >= PROMOTE_THRESHOLD:
            new_tier = _PROMOTION_PATH[current_tier]
            if new_tier != current_tier:
                _confidence_map[action_type] = new_tier
                changes.append({
                    "action_type": action_type.value,
                    "old_tier": current_tier.value,
                    "new_tier": new_tier.value,
                    "accuracy": accuracy,
                    "evaluated": evaluated,
                    "direction": "promoted",
                })
                logger.info("AUTONOMY PROMOTED: %s from %s to %s (accuracy=%.1f%% over %d)",
                            action_type.value, current_tier.value, new_tier.value,
                            accuracy * 100, evaluated)

        elif accuracy < DEMOTE_THRESHOLD:
            new_tier = _DEMOTION_PATH[current_tier]
            if new_tier != current_tier:
                _confidence_map[action_type] = new_tier
                changes.append({
                    "action_type": action_type.value,
                    "old_tier": current_tier.value,
                    "new_tier": new_tier.value,
                    "accuracy": accuracy,
                    "evaluated": evaluated,
                    "direction": "demoted",
                })
                logger.warning("AUTONOMY DEMOTED: %s from %s to %s (accuracy=%.1f%% over %d)",
                               action_type.value, current_tier.value, new_tier.value,
                               accuracy * 100, evaluated)

    return {"changes": changes, "current_tiers": {k.value: v.value for k, v in _confidence_map.items()}}


def get_confidence_map() -> dict:
    """Return current confidence tier assignments."""
    return {k.value: v.value for k, v in _confidence_map.items()}


def reset_confidence_map() -> None:
    """Reset to defaults (testing)."""
    _confidence_map.clear()
    _confidence_map.update(_DEFAULT_CONFIDENCE)
