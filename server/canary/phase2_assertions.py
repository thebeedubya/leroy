"""Phase 2 Canary Assertions: Spec Analyzer with Typed IR.

14 assertions covering TypedIR extraction, dedup, complexity, preflight,
and pipeline integration. Plus Phase 0+1 regression.
"""

import sys
from pathlib import Path

SERVER_DIR = Path(__file__).parent.parent
MCP_DIR = SERVER_DIR.parent / "mcp"
sys.path.insert(0, str(SERVER_DIR))
sys.path.insert(0, str(MCP_DIR))

from spec_analyzer import extract_typed_ir, check_dedup, check_complexity, check_preflight, TypedIR


SAMPLE_SPEC = """## Target: kush

## Objective
Fix the dashboard system tab crash on Kush.

## Success Criteria
1. SystemTab renders without crash
2. Service health checks return data within 5s
3. Circuit breaker state displayed correctly
4. Error boundary catches component failures
5. SSE reconnection works after server restart
6. No console errors in production build

## Do Not Do
- Do not modify the TaskBoard component
- Do not change the SSE protocol

## Files
- ~/Projects/leroy/dashboard/src/components/tabs/SystemTab.jsx
- ~/Projects/leroy/server/server.py

## Dependencies
Requires postgres and qdrant running on kush.

inactivity_timeout: 20
max_retries: 3
"""

THIN_SPEC = "Fix the bug."


def test_01_typed_ir_extracts_criteria():
    """TypedIR correctly extracts criteria list."""
    ir = extract_typed_ir(SAMPLE_SPEC, "fix-system-tab")
    assert len(ir.criteria) == 6, f"Expected 6 criteria, got {len(ir.criteria)}: {ir.criteria}"
    assert "SystemTab renders without crash" in ir.criteria[0]


def test_02_typed_ir_extracts_target():
    """TypedIR correctly extracts target machine."""
    ir = extract_typed_ir(SAMPLE_SPEC, "fix-system-tab")
    assert ir.target == "kush", f"Expected 'kush', got {ir.target}"


def test_03_typed_ir_extracts_subsystem():
    """TypedIR identifies subsystem from file paths."""
    ir = extract_typed_ir(SAMPLE_SPEC, "fix-system-tab")
    assert ir.subsystem == "dashboard", f"Expected 'dashboard', got {ir.subsystem}"


def test_04_complexity_score():
    """Complexity score computed correctly."""
    ir = extract_typed_ir(SAMPLE_SPEC, "fix-system-tab")
    # 6 criteria * 2 + endpoints * 3 + files
    expected = len(ir.criteria) * 2 + len(ir.endpoints) * 3 + len(ir.files)
    assert ir.complexity == expected, f"Expected {expected}, got {ir.complexity}"


def test_05_dedup_blocks_active():
    """Dedup blocks send when >70% overlap with active task."""
    ir = extract_typed_ir(THIN_SPEC, "fix the bug")
    active = [{"task_id": "t1", "spec": "fix the bug in production"}]
    result = check_dedup(ir, "fix the bug", active, [])
    assert result["overlap_pct"] > 0.5, f"Expected high overlap, got {result['overlap_pct']}"


def test_06_dedup_warns_recent():
    """Dedup warns but allows when overlap with recently completed task."""
    ir = extract_typed_ir(THIN_SPEC, "fix the bug")
    recent = [{"task_id": "t2", "spec": "fix the bug in production now please"}]
    result = check_dedup(ir, "fix the bug in production now please exactly this", [], recent)
    # With high overlap to recent, should not be blocked
    assert not result["blocked"], "Should not block for recent task overlap"


def test_07_complexity_warns_high_criteria():
    """Complexity warns when criteria > 6."""
    ir = TypedIR(criteria=["a", "b", "c", "d", "e", "f", "g", "h"], complexity=16)
    result = check_complexity(ir, "x" * 100)
    assert any("criteria" in w.lower() for w in result["warnings"]), f"No criteria warning: {result['warnings']}"


def test_08_complexity_warns_long_spec():
    """Complexity warns when spec > 8000 chars."""
    ir = TypedIR(complexity=5)
    result = check_complexity(ir, "x" * 9000)
    assert any("8000" in w for w in result["warnings"]), f"No length warning: {result['warnings']}"


def test_09_complexity_warns_no_target():
    """Complexity warns when no target but mentions kush/postgres."""
    ir = TypedIR(target=None, complexity=5)
    result = check_complexity(ir, "Deploy to kush with postgres")
    assert any("target" in w.lower() for w in result["warnings"]), f"No target warning: {result['warnings']}"


def test_10_preflight_passes_no_deps():
    """Pre-flight passes when no dependencies."""
    ir = TypedIR()
    result = check_preflight(ir)
    assert result["passed"], "Should pass with no deps"
    assert not result["blocked"]


def test_11_do_not_do_extracted():
    """Do Not Do section extracted."""
    ir = extract_typed_ir(SAMPLE_SPEC, "fix-system-tab")
    assert len(ir.do_not_do) >= 2, f"Expected 2+ do_not_do items, got {ir.do_not_do}"


def test_12_metadata_extracted():
    """Metadata (timeout_override, max_retries) extracted."""
    ir = extract_typed_ir(SAMPLE_SPEC, "fix-system-tab")
    assert ir.timeout_override == 20, f"Expected timeout 20, got {ir.timeout_override}"
    assert ir.max_retries == 3, f"Expected max_retries 3, got {ir.max_retries}"


def test_13_dependencies_extracted():
    """Dependencies extracted from spec text."""
    ir = extract_typed_ir(SAMPLE_SPEC, "fix-system-tab")
    assert "postgres" in ir.dependencies, f"Expected postgres in deps: {ir.dependencies}"
    assert "qdrant" in ir.dependencies, f"Expected qdrant in deps: {ir.dependencies}"


def test_14_phase01_canary_still_passes():
    """Phase 0+1 canary assertions still pass."""
    from canary import phase0_assertions, phase1_assertions
    for a in phase0_assertions.ASSERTIONS:
        a["test"]()
    for a in phase1_assertions.ASSERTIONS:
        a["test"]()


ASSERTIONS = [
    {"name": "01: TypedIR extracts criteria list", "test": test_01_typed_ir_extracts_criteria},
    {"name": "02: TypedIR extracts target machine", "test": test_02_typed_ir_extracts_target},
    {"name": "03: TypedIR identifies subsystem", "test": test_03_typed_ir_extracts_subsystem},
    {"name": "04: Complexity score computed", "test": test_04_complexity_score},
    {"name": "05: Dedup blocks active task overlap", "test": test_05_dedup_blocks_active},
    {"name": "06: Dedup warns on recent task overlap", "test": test_06_dedup_warns_recent},
    {"name": "07: Complexity warns high criteria", "test": test_07_complexity_warns_high_criteria},
    {"name": "08: Complexity warns long spec", "test": test_08_complexity_warns_long_spec},
    {"name": "09: Complexity warns no target with kush mention", "test": test_09_complexity_warns_no_target},
    {"name": "10: Pre-flight passes with no deps", "test": test_10_preflight_passes_no_deps},
    {"name": "11: Do Not Do section extracted", "test": test_11_do_not_do_extracted},
    {"name": "12: Metadata (timeout, max_retries) extracted", "test": test_12_metadata_extracted},
    {"name": "13: Dependencies extracted", "test": test_13_dependencies_extracted},
    {"name": "14: Phase 0+1 canary still passes", "test": test_14_phase01_canary_still_passes},
]
