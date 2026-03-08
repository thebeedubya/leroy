"""Phase 0 Canary Assertions: State Machine + Failure Taxonomy + Retry Budget.

10 assertions covering:
1. Task initialized to NEW
2. Legal transition NEW -> RUNNING, history recorded
3. Legal transition RUNNING -> COMPLETED_UNVERIFIED
4. Legal transition COMPLETED_UNVERIFIED -> ARCHIVED (terminal)
5. Illegal transition ARCHIVED -> RUNNING raises
6. Failure classification assigns correct category
7. Retry budget consumption
8. Budget exhaustion (used == max)
9. Infra failures bypass budget
10. Full history chain is append-only and complete
"""

from state_machine import TaskStateMachine, TaskState, IllegalTransitionError
from failure_taxonomy import classify_failure, FailureCategory, is_infra_failure
from retry_budget import RetryBudget


class MockTaskMeta:
    """Simple dict-backed mock for PersistentTaskDict in tests."""

    def __init__(self):
        self._store: dict[str, dict] = {}

    def __setitem__(self, task_id: str, value: dict):
        self._store[task_id] = dict(value)

    def __getitem__(self, task_id: str):
        return dict(self._store[task_id])

    def get(self, task_id: str, default=None):
        if task_id in self._store:
            return dict(self._store[task_id])
        return default

    def __contains__(self, task_id: str):
        return task_id in self._store

    def values(self):
        return [dict(v) for v in self._store.values()]


def _fresh_sm() -> tuple[TaskStateMachine, MockTaskMeta]:
    """Create a fresh state machine and mock task meta."""
    meta = MockTaskMeta()
    sm = TaskStateMachine(meta)
    return sm, meta


def _create_task(sm: TaskStateMachine, meta: MockTaskMeta, task_id: str = "test-001") -> str:
    """Helper: create a task and initialize it."""
    meta[task_id] = {"task_id": task_id, "status": "pending", "spec": "test spec"}
    sm.initialize_task(task_id)
    return task_id


# ---------------------------------------------------------------------------
# Assertion 1: Task initialized to NEW
# ---------------------------------------------------------------------------
def test_01_initialize_to_new():
    sm, meta = _fresh_sm()
    tid = _create_task(sm, meta)
    state = sm.get_state(tid)
    assert state == TaskState.NEW, f"Expected NEW, got {state}"
    history = sm.get_history(tid)
    assert len(history) == 1, f"Expected 1 history entry, got {len(history)}"
    assert history[0]["state"] == "new"
    assert history[0]["reason"] == "task_created"


# ---------------------------------------------------------------------------
# Assertion 2: Legal transition NEW -> RUNNING with history
# ---------------------------------------------------------------------------
def test_02_transition_new_to_running():
    sm, meta = _fresh_sm()
    tid = _create_task(sm, meta)
    event = sm.transition(tid, TaskState.RUNNING, reason="builder_launched")
    assert event["from_state"] == "new"
    assert event["to_state"] == "running"
    assert event["task_id"] == tid
    assert event["reason"] == "builder_launched"
    assert "timestamp" in event
    state = sm.get_state(tid)
    assert state == TaskState.RUNNING
    history = sm.get_history(tid)
    assert len(history) == 2, f"Expected 2 history entries, got {len(history)}"


# ---------------------------------------------------------------------------
# Assertion 3: RUNNING -> COMPLETED_UNVERIFIED
# ---------------------------------------------------------------------------
def test_03_transition_to_completed():
    sm, meta = _fresh_sm()
    tid = _create_task(sm, meta)
    sm.transition(tid, TaskState.RUNNING)
    event = sm.transition(tid, TaskState.COMPLETED_UNVERIFIED, reason="builder_done")
    assert event["to_state"] == "completed_unverified"
    assert sm.get_state(tid) == TaskState.COMPLETED_UNVERIFIED


# ---------------------------------------------------------------------------
# Assertion 4: COMPLETED_UNVERIFIED -> ARCHIVED (terminal)
# ---------------------------------------------------------------------------
def test_04_transition_to_archived():
    sm, meta = _fresh_sm()
    tid = _create_task(sm, meta)
    sm.transition(tid, TaskState.RUNNING)
    sm.transition(tid, TaskState.COMPLETED_UNVERIFIED)
    event = sm.transition(tid, TaskState.ARCHIVED, reason="task_archived")
    assert event["to_state"] == "archived"
    assert sm.get_state(tid) == TaskState.ARCHIVED


# ---------------------------------------------------------------------------
# Assertion 5: Illegal transition ARCHIVED -> RUNNING raises
# ---------------------------------------------------------------------------
def test_05_illegal_transition_raises():
    sm, meta = _fresh_sm()
    tid = _create_task(sm, meta)
    sm.transition(tid, TaskState.RUNNING)
    sm.transition(tid, TaskState.COMPLETED_UNVERIFIED)
    sm.transition(tid, TaskState.ARCHIVED)
    try:
        sm.transition(tid, TaskState.RUNNING)
        raise AssertionError("Expected IllegalTransitionError, got none")
    except IllegalTransitionError as e:
        assert "archived" in str(e).lower()
        assert e.from_state == TaskState.ARCHIVED
        assert e.to_state == TaskState.RUNNING


# ---------------------------------------------------------------------------
# Assertion 6: Failure classification assigns correct categories
# ---------------------------------------------------------------------------
def test_06_failure_classification():
    # Timeout with no output
    cats = classify_failure("", {"_stuck_detected_at": "2026-03-01T00:00:00Z"})
    assert FailureCategory.TIMEOUT_NO_OUTPUT in cats, f"Expected TIMEOUT_NO_OUTPUT, got {cats}"

    # Infra unreachable
    cats = classify_failure("connection refused to kush.local:5432", {})
    assert FailureCategory.INFRA_UNREACHABLE in cats, f"Expected INFRA_UNREACHABLE, got {cats}"

    # Clean pass
    cats = classify_failure("All tests passed successfully. Done.", {})
    assert FailureCategory.CLEAN_PASS in cats, f"Expected CLEAN_PASS, got {cats}"

    # Auth failure
    cats = classify_failure("SSH key permission denied for bradwood@kush.local", {})
    assert FailureCategory.INFRA_AUTH in cats, f"Expected INFRA_AUTH, got {cats}"


# ---------------------------------------------------------------------------
# Assertion 7: Retry budget consumption
# ---------------------------------------------------------------------------
def test_07_retry_budget_consumption():
    meta = MockTaskMeta()
    meta["t1"] = {"task_id": "t1", "status": "failed", "spec": "test"}
    budget = RetryBudget(meta)

    status = budget.get_budget_status("t1")
    assert status["max_retries"] == 2, f"Expected max 2, got {status}"
    assert status["remaining"] == 2

    remaining = budget.consume_retry("t1", [FailureCategory.CODE_ERROR])
    assert remaining == 1, f"Expected 1 remaining, got {remaining}"

    status = budget.get_budget_status("t1")
    assert status["used"] == 1
    assert status["remaining"] == 1


# ---------------------------------------------------------------------------
# Assertion 8: Budget exhaustion
# ---------------------------------------------------------------------------
def test_08_budget_exhaustion():
    meta = MockTaskMeta()
    meta["t2"] = {"task_id": "t2", "status": "failed", "spec": "test"}
    budget = RetryBudget(meta)

    budget.consume_retry("t2", [FailureCategory.CODE_ERROR])
    budget.consume_retry("t2", [FailureCategory.CODE_ERROR])

    assert not budget.check_budget("t2"), "Budget should be exhausted"
    status = budget.get_budget_status("t2")
    assert status["remaining"] == 0
    assert status["used"] == 2


# ---------------------------------------------------------------------------
# Assertion 9: Infra failures bypass budget
# ---------------------------------------------------------------------------
def test_09_infra_bypass():
    meta = MockTaskMeta()
    meta["t3"] = {"task_id": "t3", "status": "failed", "spec": "test"}
    budget = RetryBudget(meta)

    # Infra failure should not consume budget
    remaining = budget.consume_retry("t3", [FailureCategory.INFRA_UNREACHABLE])
    assert remaining == 2, f"Expected 2 remaining (infra bypass), got {remaining}"

    status = budget.get_budget_status("t3")
    assert status["used"] == 0, "Infra failure should not consume budget"
    assert status["infra_bypassed"] == 1

    # Verify infra detection
    assert is_infra_failure([FailureCategory.INFRA_UNREACHABLE])
    assert is_infra_failure([FailureCategory.INFRA_AUTH])
    assert not is_infra_failure([FailureCategory.CODE_ERROR])


# ---------------------------------------------------------------------------
# Assertion 10: Full history chain is append-only and complete
# ---------------------------------------------------------------------------
def test_10_history_chain():
    sm, meta = _fresh_sm()
    tid = _create_task(sm, meta)
    sm.transition(tid, TaskState.RUNNING, reason="start")
    sm.transition(tid, TaskState.FAILED_RETRYABLE, reason="timeout")
    sm.transition(tid, TaskState.RUNNING, reason="retry_1")
    sm.transition(tid, TaskState.COMPLETED_UNVERIFIED, reason="done")
    sm.transition(tid, TaskState.ARCHIVED, reason="archived")

    history = sm.get_history(tid)
    assert len(history) == 6, f"Expected 6 entries, got {len(history)}"

    expected_states = ["new", "running", "failed_retryable", "running", "completed_unverified", "archived"]
    actual_states = [h["state"] for h in history]
    assert actual_states == expected_states, f"Expected {expected_states}, got {actual_states}"

    # Verify timestamps are monotonically non-decreasing
    timestamps = [h["timestamp"] for h in history]
    assert timestamps == sorted(timestamps), "Timestamps should be monotonically increasing"

    # Verify reasons are preserved
    assert history[1]["reason"] == "start"
    assert history[2]["reason"] == "timeout"
    assert history[3]["reason"] == "retry_1"


# ---------------------------------------------------------------------------
# Assertion list for canary runner
# ---------------------------------------------------------------------------
ASSERTIONS = [
    {"name": "01: Task initialized to NEW", "test": test_01_initialize_to_new},
    {"name": "02: Legal transition NEW -> RUNNING with history", "test": test_02_transition_new_to_running},
    {"name": "03: RUNNING -> COMPLETED_UNVERIFIED", "test": test_03_transition_to_completed},
    {"name": "04: COMPLETED_UNVERIFIED -> ARCHIVED (terminal)", "test": test_04_transition_to_archived},
    {"name": "05: Illegal transition ARCHIVED -> RUNNING raises", "test": test_05_illegal_transition_raises},
    {"name": "06: Failure classification assigns correct categories", "test": test_06_failure_classification},
    {"name": "07: Retry budget consumption", "test": test_07_retry_budget_consumption},
    {"name": "08: Budget exhaustion", "test": test_08_budget_exhaustion},
    {"name": "09: Infra failures bypass budget", "test": test_09_infra_bypass},
    {"name": "10: Full history chain append-only and complete", "test": test_10_history_chain},
]
