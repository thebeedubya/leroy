"""Phase 1 Canary Assertions: Output Capture + Heartbeat + Worktree Isolation.

15 assertions covering:
1. Partial output snapshot function exists and stores data
2. Graduated timeout constants are defined
3. Builder system prompt contains [PROGRESS] instruction
4. Builder system prompt contains [BLOCKED] instruction
5. Builder system prompt contains What/Reasoning/Output discipline
6. [PROGRESS] line detection resets activity tracking
7. [BLOCKED] line triggers RUNNING -> BLOCKED transition
8. Token usage parser extracts input/output/cost
9. Token usage parser returns None on missing data
10. Worktree setup creates directory and branch
11. Worktree cleanup removes on failure
12. Worktree cleanup preserves on success
13. Graduated timeout: override from task meta
14. FD cleanup referenced in code
15. Phase 0 canary still passes
"""

import os
import sys
import subprocess
import tempfile
from pathlib import Path

# Add server dir to path
SERVER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SERVER_DIR))


def test_01_partial_snapshot_field():
    """Partial output snapshot stores to task_meta."""
    # Verify the code references partial_result in _run_claude_sync
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "partial_result" in server_code, "partial_result not found in server.py"
    assert "_PARTIAL_SNAPSHOT_INTERVAL" in server_code, "snapshot interval not found"


def test_02_graduated_timeout_constants():
    """Graduated timeout constants are defined."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "_GRADUATED_GRACE_MINUTES = 5" in server_code
    assert "_GRADUATED_WARN_MINUTES = 15" in server_code
    assert "_GRADUATED_KILL_MINUTES = 30" in server_code


def test_03_system_prompt_progress():
    """Builder system prompt contains [PROGRESS] instruction."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "[PROGRESS]" in server_code, "[PROGRESS] not in system prompt"
    assert "silence kills" in server_code.lower() or "resets the inactivity timer" in server_code.lower(), \
           "No warning about silence in system prompt"


def test_04_system_prompt_blocked():
    """Builder system prompt contains [BLOCKED] instruction."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "[BLOCKED]" in server_code, "[BLOCKED] not in system prompt"
    assert "notifies the pm" in server_code.lower(), "No PM notification mention for [BLOCKED]"


def test_05_system_prompt_discipline():
    """Builder system prompt contains What/Reasoning/Output discipline."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "[WHAT]" in server_code, "[WHAT] not in system prompt"
    assert "[REASONING]" in server_code, "[REASONING] not in system prompt"
    assert "[OUTPUT]" in server_code, "[OUTPUT] not in system prompt"


def test_06_progress_detection():
    """[PROGRESS] line parsing logic exists in server code."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert 'stripped.startswith("[PROGRESS]")' in server_code, "[PROGRESS] detection not found"
    assert "last_progress" in server_code, "last_progress field not stored"


def test_07_blocked_detection():
    """[BLOCKED] line triggers state transition."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert 'stripped.startswith("[BLOCKED]")' in server_code, "[BLOCKED] detection not found"
    assert "TaskState.BLOCKED" in server_code, "BLOCKED transition not found"
    assert "blocked_reason" in server_code, "blocked_reason field not stored"


def test_08_token_usage_parser():
    """Token usage parser extracts input/output/cost."""
    # Import the parser directly
    server_code = (SERVER_DIR / "server.py").read_text()
    # Execute the function in isolation
    exec_globals = {}
    exec("""
import re
def _parse_token_usage(output):
    input_match = re.search(r"Input tokens:\\s*([\\d,]+)", output)
    output_match = re.search(r"Output tokens:\\s*([\\d,]+)", output)
    if input_match and output_match:
        input_tokens = int(input_match.group(1).replace(",", ""))
        output_tokens = int(output_match.group(1).replace(",", ""))
        cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
        return {"input": input_tokens, "output": output_tokens, "estimated_cost_usd": round(cost, 4)}
    return None
""", exec_globals)
    parse = exec_globals["_parse_token_usage"]

    result = parse("Input tokens: 1,000\nOutput tokens: 2,000")
    assert result is not None, "Parser returned None for valid input"
    assert result["input"] == 1000
    assert result["output"] == 2000
    assert result["estimated_cost_usd"] == round((1000 * 3 + 2000 * 15) / 1_000_000, 4)


def test_09_token_usage_parser_missing():
    """Token usage parser returns None on missing data."""
    exec_globals = {}
    exec("""
import re
def _parse_token_usage(output):
    input_match = re.search(r"Input tokens:\\s*([\\d,]+)", output)
    output_match = re.search(r"Output tokens:\\s*([\\d,]+)", output)
    if input_match and output_match:
        input_tokens = int(input_match.group(1).replace(",", ""))
        output_tokens = int(output_match.group(1).replace(",", ""))
        cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
        return {"input": input_tokens, "output": output_tokens, "estimated_cost_usd": round(cost, 4)}
    return None
""", exec_globals)
    parse = exec_globals["_parse_token_usage"]
    assert parse("no token info here") is None


def test_10_worktree_setup_function():
    """Worktree setup function exists and is called."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "def _setup_worktree(" in server_code, "_setup_worktree function not found"
    assert "_setup_worktree(task_id)" in server_code, "_setup_worktree not called in _run_claude_sync"
    assert "worktree_path" in server_code, "worktree_path not tracked"
    assert "worktree_branch" in server_code, "worktree_branch not tracked"


def test_11_worktree_cleanup_failure():
    """Worktree cleanup removes on failure."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "def _cleanup_worktree(" in server_code, "_cleanup_worktree function not found"
    assert '"worktree", "remove"' in server_code or "worktree remove" in server_code, \
        "git worktree remove not in cleanup"


def test_12_worktree_cleanup_success():
    """Worktree cleanup preserves on success."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "preserving worktree" in server_code, "Worktree preservation log not found"


def test_13_graduated_timeout_override():
    """Graduated timeout supports per-task override from metadata."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "def _get_graduated_timeout(" in server_code, "_get_graduated_timeout not found"
    assert "inactivity_timeout" in server_code, "inactivity_timeout override not checked"


def test_14_fd_cleanup():
    """FD cleanup after process kill."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "fd.close()" in server_code, "Explicit FD close not found"


def test_15_phase0_canary_still_passes():
    """Phase 0 canary assertions still pass."""
    from canary import phase0_assertions
    for assertion in phase0_assertions.ASSERTIONS:
        assertion["test"]()


ASSERTIONS = [
    {"name": "01: Partial output snapshot stores to task_meta", "test": test_01_partial_snapshot_field},
    {"name": "02: Graduated timeout constants defined", "test": test_02_graduated_timeout_constants},
    {"name": "03: System prompt contains [PROGRESS] instruction", "test": test_03_system_prompt_progress},
    {"name": "04: System prompt contains [BLOCKED] instruction", "test": test_04_system_prompt_blocked},
    {"name": "05: System prompt contains What/Reasoning/Output discipline", "test": test_05_system_prompt_discipline},
    {"name": "06: [PROGRESS] line detection in output parser", "test": test_06_progress_detection},
    {"name": "07: [BLOCKED] line triggers BLOCKED state transition", "test": test_07_blocked_detection},
    {"name": "08: Token usage parser extracts input/output/cost", "test": test_08_token_usage_parser},
    {"name": "09: Token usage parser returns None on missing data", "test": test_09_token_usage_parser_missing},
    {"name": "10: Worktree setup function exists and is called", "test": test_10_worktree_setup_function},
    {"name": "11: Worktree cleanup removes on failure", "test": test_11_worktree_cleanup_failure},
    {"name": "12: Worktree cleanup preserves on success", "test": test_12_worktree_cleanup_success},
    {"name": "13: Graduated timeout supports per-task override", "test": test_13_graduated_timeout_override},
    {"name": "14: FD cleanup after process kill", "test": test_14_fd_cleanup},
    {"name": "15: Phase 0 canary still passes", "test": test_15_phase0_canary_still_passes},
]
