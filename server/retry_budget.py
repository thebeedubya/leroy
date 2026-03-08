"""Leroy v2 Retry Budget Engine.

Tracks retry attempts per task. Infra failures bypass the budget.
Budget exhaustion triggers escalation.
"""

import logging
from failure_taxonomy import FailureCategory, INFRA_CATEGORIES

logger = logging.getLogger("leroy-retry-budget")


class RetryBudget:
    """Manages retry budgets for tasks.

    Budget state is stored in task metadata (backed by SQLite).
    """

    DEFAULT_MAX_RETRIES = 2  # 3 total attempts

    def __init__(self, task_meta):
        """Initialize with a task_meta dict-like object (PersistentTaskDict)."""
        self._task_meta = task_meta

    def _get_budget_fields(self, task_id: str) -> dict:
        """Get or initialize budget fields from task metadata."""
        meta = self._task_meta.get(task_id)
        if meta is None:
            raise KeyError(f"Task {task_id} not found in task_meta")

        if "retry_max" not in meta:
            max_retries = meta.get("max_retries", self.DEFAULT_MAX_RETRIES)
            meta["retry_max"] = max_retries
            meta["retry_used"] = 0
            meta["retry_infra_bypassed"] = 0
            self._task_meta[task_id] = dict(meta)

        return {
            "max_retries": meta["retry_max"],
            "used": meta["retry_used"],
            "infra_bypassed": meta["retry_infra_bypassed"],
        }

    def check_budget(self, task_id: str) -> bool:
        """Returns True if retry is allowed, False if budget exhausted."""
        fields = self._get_budget_fields(task_id)
        return fields["used"] < fields["max_retries"]

    def consume_retry(self, task_id: str, failure_categories: list[FailureCategory]) -> int:
        """Consume a retry. Infra failures don't consume budget.

        Returns remaining retries.
        """
        meta = self._task_meta.get(task_id)
        if meta is None:
            raise KeyError(f"Task {task_id} not found in task_meta")

        # Initialize if needed
        if "retry_max" not in meta:
            self._get_budget_fields(task_id)
            meta = self._task_meta.get(task_id)

        is_infra = bool(set(failure_categories) & INFRA_CATEGORIES)

        if is_infra:
            meta["retry_infra_bypassed"] = meta.get("retry_infra_bypassed", 0) + 1
            self._task_meta[task_id] = dict(meta)
            remaining = meta["retry_max"] - meta["retry_used"]
            logger.info(
                "Task %s: infra failure, budget NOT consumed (%d remaining)",
                task_id, remaining,
            )
            return remaining

        meta["retry_used"] = meta.get("retry_used", 0) + 1
        self._task_meta[task_id] = dict(meta)
        remaining = meta["retry_max"] - meta["retry_used"]
        logger.info(
            "Task %s: retry consumed (%d/%d used, %d remaining)",
            task_id, meta["retry_used"], meta["retry_max"], remaining,
        )
        return remaining

    def reset_budget(self, task_id: str) -> None:
        """Reset budget (called on manual respec by Brad)."""
        meta = self._task_meta.get(task_id)
        if meta is None:
            raise KeyError(f"Task {task_id} not found in task_meta")
        meta["retry_used"] = 0
        meta["retry_infra_bypassed"] = 0
        self._task_meta[task_id] = dict(meta)
        logger.info("Task %s: retry budget reset", task_id)

    def get_budget_status(self, task_id: str) -> dict:
        """Returns {max_retries, used, remaining, infra_bypassed}."""
        fields = self._get_budget_fields(task_id)
        return {
            "max_retries": fields["max_retries"],
            "used": fields["used"],
            "remaining": fields["max_retries"] - fields["used"],
            "infra_bypassed": fields["infra_bypassed"],
        }
