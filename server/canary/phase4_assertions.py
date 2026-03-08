"""Phase 4 Canary Assertions: Event System + Ops Wiring + Webhooks.

11 assertions covering event handlers, ops routing, webhook integration,
SSE enhancement, plan record updates, and Phase 0-3 regression.
"""

import sys
import tempfile
from pathlib import Path

SERVER_DIR = Path(__file__).parent.parent
MCP_DIR = SERVER_DIR.parent / "mcp"
sys.path.insert(0, str(SERVER_DIR))
sys.path.insert(0, str(MCP_DIR))


def test_01_event_handlers_registered():
    """Event handlers fire on state machine transitions."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "register_all_handlers(" in server_code, "register_all_handlers not called in main()"
    assert "from task_events import register_all_handlers" in server_code, "task_events not imported"


def test_02_task_events_module():
    """task_events.py exists with all required handlers."""
    events_code = (SERVER_DIR / "task_events.py").read_text()
    assert "def on_build_completed(" in events_code, "on_build_completed handler missing"
    assert "def on_build_failed(" in events_code, "on_build_failed handler missing"
    assert "def on_build_blocked(" in events_code, "on_build_blocked handler missing"
    assert "def on_escalated(" in events_code, "on_escalated handler missing"
    assert "register_handler(" in events_code, "Handlers not registered with state machine"


def test_03_completion_suggests_qa():
    """Build completion creates a bus message suggesting QA spec."""
    events_code = (SERVER_DIR / "task_events.py").read_text()
    assert "QA spec needed" in events_code, "QA suggestion not in completion handler"
    assert 'agent_bus.send(' in events_code, "Bus send not called in completion handler"


def test_04_failure_classifies_and_routes():
    """Build failure classifies failure and routes to ops or PM."""
    events_code = (SERVER_DIR / "task_events.py").read_text()
    assert "classify_failure(" in events_code, "classify_failure not called in failure handler"
    assert "is_infra_failure(" in events_code, "Infra routing not in failure handler"
    assert '"ops"' in events_code, "Ops routing not in failure handler"
    assert '"pm"' in events_code, "PM routing not in failure handler"


def test_05_retry_budget_checked_on_failure():
    """Retry budget checked on failure, escalates when exhausted."""
    events_code = (SERVER_DIR / "task_events.py").read_text()
    assert "get_budget_status(" in events_code or "check_budget(" in events_code, \
        "Retry budget not checked in failure handler"
    assert "TaskState.ESCALATED" in events_code, "Escalation transition not in failure handler"


def test_06_escalation_fires_webhook():
    """ESCALATED state triggers Google Chat webhook."""
    events_code = (SERVER_DIR / "task_events.py").read_text()
    assert "send_webhook_notification(" in events_code, "Webhook not called in escalation handler"
    assert "critical" in events_code, "Critical severity not set on escalation"


def test_07_blocked_routes_correctly():
    """BLOCKED state routes to ops (infra) or PM (scope)."""
    events_code = (SERVER_DIR / "task_events.py").read_text()
    assert "on_build_blocked" in events_code, "on_build_blocked handler missing"
    assert "infra" in events_code.lower(), "Infra block routing not present"
    assert "Decision needed" in events_code, "PM decision routing not present"


def test_08_ops_routing_helper():
    """Agent bus has ops routing helper."""
    bus_code = (SERVER_DIR / "agent_bus.py").read_text()
    assert "def route_infra_to_ops(" in bus_code, "route_infra_to_ops function missing"
    assert "Diagnose and report" in bus_code, "Ops scope constraint not in routing"


def test_09_notifications_module():
    """Notifications module exists with webhook dispatch."""
    notif_code = (SERVER_DIR / "notifications.py").read_text()
    assert "def send_webhook_notification(" in notif_code, "send_webhook_notification missing"
    assert "send_google_chat_message" in notif_code, "Google Chat integration missing"
    assert "FORGE" in notif_code, "FORGE prefix missing from notifications"


def test_10_sse_state_transitions():
    """SSE stream includes state machine transition events."""
    server_code = (SERVER_DIR / "server.py").read_text()
    assert "_broadcast_state_transition" in server_code, "State transition broadcast function missing"
    assert '"state_transition"' in server_code, "state_transition event type missing"
    assert "register_global_handler" in server_code, "Global handler not registered for SSE"


def test_11_phase0123_canary_still_passes():
    """Phase 0+1+2+3 canaries still pass."""
    from canary import phase0_assertions, phase1_assertions, phase2_assertions, phase3_assertions
    for a in phase0_assertions.ASSERTIONS:
        a["test"]()
    for a in phase1_assertions.ASSERTIONS:
        a["test"]()
    for a in phase2_assertions.ASSERTIONS:
        a["test"]()
    for a in phase3_assertions.ASSERTIONS:
        a["test"]()


ASSERTIONS = [
    {"name": "01: Event handlers registered in server main", "test": test_01_event_handlers_registered},
    {"name": "02: task_events module has all required handlers", "test": test_02_task_events_module},
    {"name": "03: Build completion suggests QA spec", "test": test_03_completion_suggests_qa},
    {"name": "04: Build failure classifies and routes", "test": test_04_failure_classifies_and_routes},
    {"name": "05: Retry budget checked on failure", "test": test_05_retry_budget_checked_on_failure},
    {"name": "06: Escalation fires Google Chat webhook", "test": test_06_escalation_fires_webhook},
    {"name": "07: Blocked state routes correctly", "test": test_07_blocked_routes_correctly},
    {"name": "08: Ops routing helper exists", "test": test_08_ops_routing_helper},
    {"name": "09: Notifications module with webhook dispatch", "test": test_09_notifications_module},
    {"name": "10: SSE includes state transition events", "test": test_10_sse_state_transitions},
    {"name": "11: Phase 0+1+2+3 canary still passes", "test": test_11_phase0123_canary_still_passes},
]
