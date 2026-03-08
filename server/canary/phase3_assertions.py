"""Phase 3 Canary Assertions: Plan Database + v1 Migration.

14 assertions covering PlanStore CRUD, MCP tool wiring, prompt versioning,
v1 migration, and Phase 0+1+2 regression.
"""

import json
import sys
import tempfile
from pathlib import Path

SERVER_DIR = Path(__file__).parent.parent
MCP_DIR = SERVER_DIR.parent / "mcp"
sys.path.insert(0, str(SERVER_DIR))
sys.path.insert(0, str(MCP_DIR))

import task_db


def _setup_plan_store() -> task_db.PlanStore:
    """Create a PlanStore backed by a temp SQLite DB."""
    tmp = tempfile.mktemp(suffix=".db")
    task_db.init(Path(tmp))
    return task_db.plan_store


def test_01_plans_table_schema():
    """Plans table has all required columns."""
    store = _setup_plan_store()
    # Insert a minimal plan and read it back
    plan_id = store.create_plan(spec_text="test spec", subject="test")
    plan = store.get_plan(plan_id)
    assert plan is not None, "Plan not found after create"
    required_cols = [
        "plan_id", "task_id", "spec_text", "typed_ir", "subject", "created_at",
        "source", "complexity_score", "criteria_count", "target_machine", "subsystem",
        "brain_queried", "brain_lessons_attached", "brain_persisted", "brain_persist_payload",
        "builder_context_injected", "preflight_passed", "preflight_details",
        "dedup_checked", "dedup_similar_task_id", "status", "pass_rate",
        "duration_seconds", "outcome", "failure_categories", "retry_count", "max_retries",
        "token_usage_input", "token_usage_output", "estimated_cost_usd",
        "quality_score", "retro_text", "parent_plan_id", "respec_count", "version",
        "builder_prompt_version", "builder_prompt_snapshot",
    ]
    for col in required_cols:
        assert col in plan, f"Missing column: {col}"


def test_02_plan_crud():
    """PlanStore create, link_task, update_outcome, get_plan work."""
    store = _setup_plan_store()
    plan_id = store.create_plan(
        spec_text="Fix the dashboard",
        subject="fix-dashboard",
        typed_ir={"criteria": ["renders without crash"]},
        complexity_score=5,
        criteria_count=1,
        target_machine="kush",
        subsystem="dashboard",
    )
    assert plan_id.startswith("plan-"), f"Bad plan_id format: {plan_id}"

    # Link task
    store.link_task(plan_id, "task-abc123")
    plan = store.get_plan(plan_id)
    assert plan["task_id"] == "task-abc123"
    assert plan["status"] == "sent"

    # Update outcome
    store.update_outcome(plan_id, status="completed", pass_rate="5/5",
                         outcome="verified", retro_text="Worked great")
    plan = store.get_plan(plan_id)
    assert plan["status"] == "completed"
    assert plan["pass_rate"] == "5/5"
    assert plan["retro_text"] == "Worked great"


def test_03_get_plan_by_task():
    """get_plan_by_task finds plan linked to a task_id."""
    store = _setup_plan_store()
    plan_id = store.create_plan(spec_text="test", subject="test")
    store.link_task(plan_id, "task-xyz")
    plan = store.get_plan_by_task("task-xyz")
    assert plan is not None, "Plan not found by task_id"
    assert plan["plan_id"] == plan_id


def test_04_list_plans_filters():
    """list_plans filters by status, subsystem, source."""
    store = _setup_plan_store()
    store.create_plan(spec_text="a", subject="a", subsystem="dashboard", source="v2")
    store.create_plan(spec_text="b", subject="b", subsystem="server", source="v2")
    store.create_plan(spec_text="c", subject="c", subsystem="dashboard", source="v1_import")

    # Default excludes v1
    plans = store.list_plans()
    assert len(plans) == 2, f"Expected 2 v2 plans, got {len(plans)}"

    # Filter by subsystem
    plans = store.list_plans(subsystem="dashboard")
    assert len(plans) == 1, f"Expected 1 dashboard v2 plan, got {len(plans)}"

    # Explicit source includes v1
    plans = store.list_plans(source="v1_import")
    assert len(plans) == 1, f"Expected 1 v1 plan, got {len(plans)}"


def test_05_plan_report():
    """plan_report returns aggregate stats with v1/v2 baselines."""
    store = _setup_plan_store()
    p1 = store.create_plan(spec_text="a", subject="a", source="v2")
    store.update_outcome(p1, status="completed", outcome="verified",
                         estimated_cost_usd=0.05)
    p2 = store.create_plan(spec_text="b", subject="b", source="v1_import")
    store.update_outcome(p2, status="completed", outcome="verified")

    report = store.plan_report()
    assert report["v2"]["total"] == 1
    assert report["v1_import"]["total"] == 1
    assert report["combined"]["total"] == 2
    assert report["v2"]["total_cost_usd"] == 0.05


def test_06_cost_report():
    """cost_report returns per-subsystem and per-day breakdowns."""
    store = _setup_plan_store()
    p1 = store.create_plan(spec_text="a", subject="a", subsystem="dashboard")
    store.update_outcome(p1, token_usage_input=1000, token_usage_output=500,
                         estimated_cost_usd=0.01)
    report = store.cost_report()
    assert report["total_cost_usd"] == 0.01
    assert "dashboard" in report["by_subsystem"]


def test_07_subsystem_health():
    """subsystem_health returns per-subsystem pass rate."""
    store = _setup_plan_store()
    p1 = store.create_plan(spec_text="a", subject="a", subsystem="dashboard")
    store.update_outcome(p1, status="completed", outcome="verified")
    p2 = store.create_plan(spec_text="b", subject="b", subsystem="dashboard")
    store.update_outcome(p2, status="failed")

    health = store.subsystem_health()
    assert "dashboard" in health
    assert health["dashboard"]["total"] == 2
    assert health["dashboard"]["completed"] == 1
    assert health["dashboard"]["pass_rate"] == 0.5


def test_08_lineage():
    """get_lineage returns parent chain oldest-first."""
    store = _setup_plan_store()
    p1 = store.create_plan(spec_text="v1", subject="original")
    p2 = store.create_plan(spec_text="v2", subject="respec", parent_plan_id=p1)
    chain = store.get_lineage(p2)
    assert len(chain) == 2, f"Expected 2 in lineage, got {len(chain)}"
    assert chain[0]["plan_id"] == p1
    assert chain[1]["plan_id"] == p2


def test_09_brain_gaps():
    """brain_gaps returns plans where brain not queried or not persisted."""
    store = _setup_plan_store()
    store.create_plan(spec_text="a", subject="a", source="v2")
    gaps = store.brain_gaps()
    assert len(gaps) >= 1, "Should find at least 1 brain gap"


def test_10_send_spec_creates_plan():
    """leroy_send_spec code creates plan record before sending."""
    client_code = (MCP_DIR / "leroy_client.py").read_text()
    assert "create_plan(" in client_code, "create_plan not called in leroy_send_spec"
    assert "link_task(" in client_code, "link_task not called in leroy_send_spec"


def test_11_update_spec_updates_plan():
    """leroy_update_spec updates plan with outcome."""
    client_code = (MCP_DIR / "leroy_client.py").read_text()
    assert "update_outcome(" in client_code, "update_outcome not called in leroy_update_spec"
    assert "get_plan_by_task(" in client_code, "get_plan_by_task not used in leroy_update_spec"


def test_12_prompt_version_hash():
    """Builder prompt version hash stored on task start."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "_BUILDER_PROMPT_VERSION" in server_code, "Prompt version hash not defined"
    assert "hashlib.sha256" in server_code, "sha256 not used for prompt hash"
    assert "builder_prompt_version" in server_code, "builder_prompt_version not stored in task meta"


def test_13_v1_migration_script():
    """v1 migration script exists and has correct structure."""
    migrate_path = SERVER_DIR / "migrate_v1_specs.py"
    assert migrate_path.exists(), "migrate_v1_specs.py not found"
    code = migrate_path.read_text()
    assert "def migrate(" in code, "migrate function not found"
    assert "v1_import" in code, "v1_import source not used"
    assert "outcome" in code, "outcome field not set"
    assert "dry_run" in code, "dry_run mode not supported"


def test_14_phase012_canary_still_passes():
    """Phase 0+1+2 canary assertions still pass."""
    from canary import phase0_assertions, phase1_assertions, phase2_assertions
    for a in phase0_assertions.ASSERTIONS:
        a["test"]()
    for a in phase1_assertions.ASSERTIONS:
        a["test"]()
    for a in phase2_assertions.ASSERTIONS:
        a["test"]()


ASSERTIONS = [
    {"name": "01: Plans table has full schema", "test": test_01_plans_table_schema},
    {"name": "02: PlanStore CRUD operations work", "test": test_02_plan_crud},
    {"name": "03: get_plan_by_task finds linked plan", "test": test_03_get_plan_by_task},
    {"name": "04: list_plans filters by status/subsystem/source", "test": test_04_list_plans_filters},
    {"name": "05: plan_report returns v1/v2 baselines", "test": test_05_plan_report},
    {"name": "06: cost_report per-subsystem and per-day", "test": test_06_cost_report},
    {"name": "07: subsystem_health per-subsystem pass rate", "test": test_07_subsystem_health},
    {"name": "08: get_lineage returns parent chain", "test": test_08_lineage},
    {"name": "09: brain_gaps finds non-compliant plans", "test": test_09_brain_gaps},
    {"name": "10: leroy_send_spec creates plan record", "test": test_10_send_spec_creates_plan},
    {"name": "11: leroy_update_spec updates plan outcome", "test": test_11_update_spec_updates_plan},
    {"name": "12: Builder prompt version hash in server", "test": test_12_prompt_version_hash},
    {"name": "13: v1 migration script exists and structured", "test": test_13_v1_migration_script},
    {"name": "14: Phase 0+1+2 canary still passes", "test": test_14_phase012_canary_still_passes},
]
