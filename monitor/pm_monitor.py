"""PM Monitor Daemon.

Lightweight polling loop that watches the agent message bus and task lifecycle
between PM sessions. Detects trigger events and spawns headless PM sessions
via `claude -p` for autonomous tier-1 operations.

Design:
- Read-only on the bus. NEVER marks messages as read. NEVER modifies state
  (except spawning headless PM subprocesses).
- Tracks seen message IDs, task statuses, and spawn history in a JSON state file.
- Posts activity events via POST /activity (when endpoint is live) or writes
  to ~/Projects/leroy/data/pm-monitor.log as fallback.
- Rate-limits headless PM spawns to MAX_SPAWNS_PER_HOUR.
- Runs under launchd with KeepAlive for automatic restart on failure.

Usage:
    python3 monitor/pm_monitor.py
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
A2A_URL = os.environ.get("LEROY_A2A_URL", "http://127.0.0.1:9800")
A2A_TOKEN = os.environ.get("LEROY_A2A_TOKEN", "")
POLL_INTERVAL = int(os.environ.get("PM_MONITOR_INTERVAL", "30"))
MAX_SPAWNS_PER_HOUR = int(os.environ.get("PM_MONITOR_MAX_SPAWNS", "10"))
MAX_TURNS = int(os.environ.get("PM_HEADLESS_MAX_TURNS", "20"))
HEADLESS_ENABLED = os.environ.get("PM_HEADLESS_ENABLED", "true").lower() == "true"

PROJECT_ROOT = Path(os.environ.get(
    "PM_PROJECT_ROOT",
    str(Path.home() / "Projects" / "leroy"),
))
STATE_FILE = Path(os.environ.get(
    "PM_MONITOR_STATE",
    str(PROJECT_ROOT / "data" / "pm-monitor-state.json"),
))
FALLBACK_LOG = Path(os.environ.get(
    "PM_MONITOR_LOG",
    str(PROJECT_ROOT / "data" / "pm-monitor.log"),
))
PERSONA_FILE = PROJECT_ROOT / "personas" / "pm_headless.md"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [pm-monitor] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(FALLBACK_LOG, encoding="utf-8"),
    ],
)
logger = logging.getLogger("pm-monitor")

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    """Load persisted state (seen message IDs, task statuses, spawn history)."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to load state file, starting fresh: %s", e)
    return {
        "seen_message_ids": [],
        "task_statuses": {},
        "spawn_history": [],       # list of ISO timestamps of recent spawns
        "handled_triggers": [],    # trigger IDs that headless PM already handled
        "last_poll": None,
    }


def _save_state(state: dict) -> None:
    """Persist state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_poll"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Activity event posting
# ---------------------------------------------------------------------------

def _post_activity(event: dict) -> bool:
    """Post an activity event to the dashboard. Returns True on success."""
    try:
        resp = httpx.post(
            f"{A2A_URL}/activity",
            json=event,
            timeout=5.0,
        )
        if resp.status_code == 200:
            logger.info("Activity posted: %s", event.get("summary", ""))
            return True
        else:
            logger.debug("POST /activity returned %d, using fallback log", resp.status_code)
            return False
    except httpx.ConnectError:
        logger.debug("POST /activity unreachable, using fallback log")
        return False
    except Exception as e:
        logger.debug("POST /activity failed: %s", e)
        return False


def _leave_note_for_pm(summary: str, detail: str = "",
                       task_id: str = "", msg_type: str = "status_update") -> None:
    """Leave a sticky note on PM's desk (bus message).

    PM reads these at session startup via leroy_read_messages.
    This is how the executive assistant tells PM what happened overnight.
    """
    payload = {
        "from": "pm-monitor",
        "to": "pm",
        "type": msg_type,
        "content": summary if not detail else f"{summary}\n\n{detail}",
    }
    if task_id:
        payload["task_id"] = task_id

    try:
        resp = httpx.post(
            f"{A2A_URL}/messages",
            json=payload,
            headers=_auth_headers(),
            timeout=5.0,
        )
        if resp.status_code == 200:
            logger.info("Note left for PM: %s", summary)
        else:
            logger.warning("Failed to leave note for PM (%d): %s",
                          resp.status_code, summary)
    except Exception as e:
        logger.warning("Failed to leave note for PM: %s -- %s", summary, e)


def _emit(agent: str, event_type: str, summary: str,
          severity: str = "info", task_id: str | None = None) -> None:
    """Emit an activity event via HTTP or fallback log."""
    event = {
        "agent": agent,
        "type": event_type,
        "summary": summary,
        "severity": severity,
    }
    if task_id:
        event["task_id"] = task_id

    posted = _post_activity(event)
    if not posted:
        # Fallback: write structured event to log file
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        logger.info("ACTIVITY EVENT: %s", json.dumps(event))


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def _can_spawn(state: dict) -> bool:
    """Check if we're under the spawn rate limit."""
    if not HEADLESS_ENABLED:
        return False

    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - 3600  # 1 hour ago

    # Prune old entries
    recent = []
    for ts in state.get("spawn_history", []):
        try:
            dt = datetime.fromisoformat(ts)
            if dt.timestamp() > cutoff:
                recent.append(ts)
        except (ValueError, TypeError):
            pass

    state["spawn_history"] = recent

    if len(recent) >= MAX_SPAWNS_PER_HOUR:
        logger.warning("Spawn rate limit reached (%d/%d in last hour)",
                       len(recent), MAX_SPAWNS_PER_HOUR)
        return False

    return True


def _record_spawn(state: dict) -> None:
    """Record a spawn event for rate limiting."""
    state.setdefault("spawn_history", []).append(
        datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Trigger deduplication
# ---------------------------------------------------------------------------

def _trigger_key(trigger_type: str, identifier: str) -> str:
    """Build a unique key for a trigger to prevent duplicate handling."""
    return f"{trigger_type}:{identifier}"


def _is_handled(state: dict, key: str) -> bool:
    """Check if a trigger has already been handled."""
    return key in state.get("handled_triggers", [])


def _mark_handled(state: dict, key: str) -> None:
    """Mark a trigger as handled."""
    handled = state.setdefault("handled_triggers", [])
    handled.append(key)
    # Keep only last 500 entries to prevent unbounded growth
    if len(handled) > 500:
        state["handled_triggers"] = handled[-500:]


# ---------------------------------------------------------------------------
# Headless PM spawning
# ---------------------------------------------------------------------------

def _read_spec_for_task(task_id: str) -> str:
    """Fetch the spec content for a task from the A2A server."""
    try:
        resp = httpx.get(
            f"{A2A_URL}/tasks/{task_id}",
            headers=_auth_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("spec", data.get("task", {}).get("spec", ""))
    except Exception as e:
        logger.warning("Failed to fetch spec for task %s: %s", task_id, e)
        return ""


def _read_task_result(task_id: str) -> str:
    """Fetch the result/output for a completed task."""
    try:
        resp = httpx.get(
            f"{A2A_URL}/tasks/{task_id}",
            headers=_auth_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        task = data.get("task", data)
        result = task.get("result", task.get("output", ""))
        if isinstance(result, dict):
            return json.dumps(result, indent=2)
        return str(result) if result else ""
    except Exception as e:
        logger.warning("Failed to fetch result for task %s: %s", task_id, e)
        return ""


def _check_existing_qa_spec(task_id: str) -> bool:
    """Check if a QA spec already exists for a build task (duplicate prevention)."""
    try:
        resp = httpx.get(
            f"{A2A_URL}/tasks",
            headers=_auth_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        tasks = resp.json().get("tasks", [])
        for t in tasks:
            spec = t.get("spec", "")
            # Look for QA specs that reference this task ID
            if f"QA" in spec and task_id in spec:
                return True
            # Also check subject line pattern
            subject = t.get("subject", "")
            if subject.startswith("QA:") and task_id in (t.get("spec", "") + subject):
                return True
        return False
    except Exception:
        return False


def _spawn_headless_pm(trigger_type: str, context: dict) -> bool:
    """Spawn a headless PM session via `claude -p`.

    Returns True if the spawn was initiated successfully.
    """
    if not PERSONA_FILE.exists():
        logger.error("Headless PM persona file not found: %s", PERSONA_FILE)
        return False

    persona = PERSONA_FILE.read_text(encoding="utf-8")

    # Build the prompt with trigger context
    prompt_lines = [
        f"TRIGGER TYPE: {trigger_type}",
    ]
    for key, value in context.items():
        if value:
            prompt_lines.append(f"{key.upper()}: {value}")

    prompt = "\n".join(prompt_lines)

    logger.info("Spawning headless PM: trigger=%s, context_keys=%s",
                trigger_type, list(context.keys()))

    _emit("pm-monitor", "headless_spawn",
          f"Spawning headless PM: {trigger_type}",
          severity="info",
          task_id=context.get("task_id"))

    try:
        # Spawn as a detached subprocess -- we don't wait for it
        cmd = [
            "claude",
            "-p", prompt,
            "--system-prompt", persona,
            "--allowedTools",
            "mcp__leroy__leroy_send_spec,"
            "mcp__leroy__leroy_check_task,"
            "mcp__leroy__leroy_list_tasks,"
            "mcp__leroy__leroy_read_messages,"
            "mcp__leroy__leroy_reply_to_message,"
            "mcp__leroy__leroy_update_spec,"
            "mcp__leroy__leroy_read_recent_specs,"
            "mcp__leroy__leroy_archive_task,"
            "mcp__leroy__leroy_health,"
            "mcp__aianna__persist_on,"
            "mcp__aianna__persist_append,"
            "mcp__aianna__query_memory,"
            "mcp__aianna__check_before_act,"
            "mcp__aianna__record_lesson,"
            "mcp__aianna__query_lessons,"
            "mcp__aianna__get_forge_state,"
            "mcp__aianna__update_forge_state,"
            "mcp__aianna__memory_status,"
            "Read,Glob,Grep",
            "--max-turns", str(MAX_TURNS),
        ]

        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # Detach from monitor process group
        )

        logger.info("Headless PM spawned: pid=%d, trigger=%s", proc.pid, trigger_type)
        return True

    except FileNotFoundError:
        logger.error("claude CLI not found in PATH")
        return False
    except Exception as e:
        logger.error("Failed to spawn headless PM: %s", e)
        return False


# ---------------------------------------------------------------------------
# Polling functions
# ---------------------------------------------------------------------------

def _auth_headers() -> dict[str, str]:
    """Build request headers with auth token if configured."""
    headers = {}
    if A2A_TOKEN:
        headers["Authorization"] = f"Bearer {A2A_TOKEN}"
    return headers


def _fetch_json(endpoint: str) -> dict | list | None:
    """GET a JSON endpoint. Returns parsed response or None on failure."""
    try:
        resp = httpx.get(f"{A2A_URL}{endpoint}", headers=_auth_headers(), timeout=10.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        logger.warning("A2A server unreachable at %s", A2A_URL)
        return None
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", endpoint, e)
        return None


def _check_pending_messages(state: dict) -> list[dict]:
    """Check for messages addressed to PM that need a response.

    Returns a list of trigger dicts for messages that should spawn headless PM.
    """
    data = _fetch_json("/messages?to=pm&pending=true")
    if data is None:
        return []

    messages = data.get("messages", [])
    seen = set(state.get("seen_message_ids", []))
    triggers = []

    for msg in messages:
        msg_id = msg.get("message_id", "")
        if msg_id in seen:
            continue

        # Skip our own notes (prevent feedback loop)
        if msg.get("from") == "pm-monitor":
            seen.add(msg_id)
            continue

        msg_type = msg.get("type", "unknown")
        content = msg.get("content", "")[:120]
        task_id = msg.get("task_id")
        sender = msg.get("from", "unknown")

        # Determine severity and emit activity
        if msg_type in ("question", "decision_gate", "blocker"):
            severity = "warn"
            event_type = "decision_requested"
            summary = f"{sender} is blocked: {content}"
        else:
            severity = "info"
            event_type = "status_update"
            summary = f"Pending message from {sender}: {content}"

        _emit("pm-monitor", event_type, summary, severity=severity, task_id=task_id)
        seen.add(msg_id)

        # Build trigger for headless PM
        trigger_key = _trigger_key(f"message_{msg_type}", msg_id)
        if not _is_handled(state, trigger_key):
            if msg_type == "question":
                # Tier 1: headless PM can try to answer routine questions
                triggers.append({
                    "type": "question",
                    "key": trigger_key,
                    "context": {
                        "task_id": task_id or "",
                        "message_id": msg_id,
                        "sender": sender,
                        "question": msg.get("content", ""),
                        "spec_content": _read_spec_for_task(task_id) if task_id else "",
                    },
                })
            elif msg_type in ("blocker", "decision_gate"):
                # Tier 2: queue for Brad, don't spawn headless PM
                # But DO create a proposal so it shows on dashboard
                triggers.append({
                    "type": msg_type,
                    "key": trigger_key,
                    "tier2": True,  # Signal: queue, don't auto-act
                    "context": {
                        "task_id": task_id or "",
                        "message_id": msg_id,
                        "sender": sender,
                        "content": msg.get("content", ""),
                    },
                })

    state["seen_message_ids"] = list(seen)
    return triggers


def _check_unread_messages(state: dict) -> None:
    """Check for unread messages addressed to PM (lower severity than pending)."""
    data = _fetch_json("/messages?to=pm&unread=true")
    if data is None:
        return

    messages = data.get("messages", [])
    seen = set(state.get("seen_message_ids", []))

    for msg in messages:
        msg_id = msg.get("message_id", "")
        if msg_id in seen:
            continue
        # Skip if it requires response (already handled by pending check)
        if msg.get("requires_response") and not msg.get("responded"):
            continue
        # Skip our own notes (prevent feedback loop)
        if msg.get("from") == "pm-monitor":
            seen.add(msg_id)
            continue

        content = msg.get("content", "")[:120]
        task_id = msg.get("task_id")
        sender = msg.get("from", "unknown")

        _emit("pm-monitor", "status_update",
              f"Unread message from {sender}: {content}",
              severity="info", task_id=task_id)
        seen.add(msg_id)

    state["seen_message_ids"] = list(seen)


def _check_task_changes(state: dict) -> list[dict]:
    """Check for task status changes (completed, failed, cancelled).

    Returns a list of trigger dicts for tasks that should spawn headless PM.
    """
    data = _fetch_json("/tasks")
    if data is None:
        return []

    tasks = data.get("tasks", [])
    prev_statuses = state.get("task_statuses", {})
    triggers = []

    for task in tasks:
        task_id = task.get("task_id", "")
        status = task.get("status", "")
        prev_status = prev_statuses.get(task_id)

        # Only trigger on transitions TO terminal states
        if status != prev_status:
            spec_preview = task.get("spec", "")[:80]

            if status == "completed" and prev_status != "completed":
                _emit("pm-monitor", "task_complete",
                      f"Task completed: {spec_preview}",
                      severity="info", task_id=task_id)

                trigger_key = _trigger_key("task_completed", task_id)
                if not _is_handled(state, trigger_key):
                    # Check if this is a build task or QA task
                    spec = task.get("spec", "")
                    subject = task.get("subject", "")
                    is_qa = subject.startswith("QA:") or spec.startswith("# QA")

                    if is_qa:
                        # QA completed: headless PM writes retro
                        triggers.append({
                            "type": "qa_completed",
                            "key": trigger_key,
                            "context": {
                                "task_id": task_id,
                                "spec_content": spec,
                                "results": _read_task_result(task_id),
                            },
                        })
                    else:
                        # Build completed: headless PM sends QA spec
                        if not _check_existing_qa_spec(task_id):
                            triggers.append({
                                "type": "task_completed",
                                "key": trigger_key,
                                "context": {
                                    "task_id": task_id,
                                    "spec_content": spec,
                                    "results": _read_task_result(task_id),
                                },
                            })
                        else:
                            logger.info("QA spec already exists for task %s, skipping", task_id)
                            _mark_handled(state, trigger_key)

            elif status == "failed" and prev_status != "failed":
                _emit("pm-monitor", "task_failed",
                      f"Task FAILED: {spec_preview}",
                      severity="error", task_id=task_id)

                trigger_key = _trigger_key("task_failed", task_id)
                if not _is_handled(state, trigger_key):
                    triggers.append({
                        "type": "task_failed",
                        "key": trigger_key,
                        "context": {
                            "task_id": task_id,
                            "spec_content": task.get("spec", ""),
                            "results": _read_task_result(task_id),
                        },
                    })

            elif status == "cancelled" and prev_status != "cancelled":
                _emit("pm-monitor", "task_cancelled",
                      f"Task cancelled: {spec_preview}",
                      severity="warn", task_id=task_id)

        prev_statuses[task_id] = status

    state["task_statuses"] = prev_statuses
    return triggers


def _check_approved_proposals(state: dict) -> list[dict]:
    """Check for proposals Brad approved that need headless PM to execute."""
    data = _fetch_json("/pm/proposals?status=approved")
    if data is None:
        return []

    proposals = data.get("proposals", [])
    triggers = []

    for prop in proposals:
        proposal_id = prop.get("proposal_id", "")
        trigger_key = _trigger_key("approval", proposal_id)

        if not _is_handled(state, trigger_key):
            triggers.append({
                "type": "approval",
                "key": trigger_key,
                "context": {
                    "proposal_id": proposal_id,
                    "title": prop.get("title", ""),
                    "spec_content": prop.get("content", ""),
                    "trigger_task_id": prop.get("trigger_task_id", ""),
                    "reviewer_feedback": prop.get("reviewer_feedback", ""),
                },
            })

    return triggers


# ---------------------------------------------------------------------------
# Trigger processing
# ---------------------------------------------------------------------------

def _process_triggers(state: dict, triggers: list[dict]) -> None:
    """Process collected triggers: spawn headless PM or queue for Brad."""
    if not triggers:
        return

    for trigger in triggers:
        trigger_type = trigger["type"]
        trigger_key = trigger["key"]

        # Tier 2 items: log but don't spawn (dashboard shows them via proposals)
        if trigger.get("tier2"):
            logger.info("Tier 2 trigger (queued for Brad): %s", trigger_key)
            _emit("pm-monitor", "tier2_queued",
                  f"Queued for Brad: {trigger_type} from {trigger['context'].get('sender', 'unknown')}",
                  severity="warn",
                  task_id=trigger["context"].get("task_id"))
            _leave_note_for_pm(
                f"While you were out: {trigger['context'].get('sender', 'unknown')} sent a {trigger_type}. Queued for Brad on the dashboard.",
                detail=trigger["context"].get("content", ""),
                task_id=trigger["context"].get("task_id", ""),
            )
            _mark_handled(state, trigger_key)
            continue

        # Rate limit check
        if not _can_spawn(state):
            logger.warning("Skipping trigger %s: rate limit reached", trigger_key)
            _emit("pm-monitor", "rate_limited",
                  f"Rate limited: skipping {trigger_type} trigger",
                  severity="warn")
            break  # Don't process any more triggers this cycle

        # Spawn headless PM if enabled, otherwise just leave a note
        if HEADLESS_ENABLED:
            success = _spawn_headless_pm(trigger_type, trigger["context"])
            if success:
                _record_spawn(state)
                _mark_handled(state, trigger_key)
                _leave_note_for_pm(
                    f"While you were out: {trigger_type} detected. Spawned headless PM to handle it.",
                    task_id=trigger["context"].get("task_id", ""),
                )
            else:
                logger.error("Failed to spawn headless PM for trigger: %s", trigger_key)
                _emit("pm-monitor", "spawn_failed",
                      f"Failed to spawn headless PM for {trigger_type}",
                      severity="error",
                      task_id=trigger["context"].get("task_id"))
                _leave_note_for_pm(
                    f"While you were out: {trigger_type} detected but headless PM failed to spawn. Needs your attention.",
                    task_id=trigger["context"].get("task_id", ""),
                    msg_type="alert",
                )
        else:
            # Headless disabled -- just leave the note, PM handles it next session
            _mark_handled(state, trigger_key)
            _leave_note_for_pm(
                f"While you were out: {trigger_type} on task {trigger['context'].get('task_id', 'unknown')}. Headless PM is disabled, needs your attention.",
                task_id=trigger["context"].get("task_id", ""),
            )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def poll_once(state: dict) -> None:
    """Run one poll cycle: check events, collect triggers, process."""
    triggers = []

    # Collect triggers from all sources
    msg_triggers = _check_pending_messages(state)
    triggers.extend(msg_triggers)

    _check_unread_messages(state)  # Activity only, no triggers

    task_triggers = _check_task_changes(state)
    triggers.extend(task_triggers)

    proposal_triggers = _check_approved_proposals(state)
    triggers.extend(proposal_triggers)

    # Process all collected triggers
    if triggers:
        logger.info("Collected %d trigger(s) this cycle", len(triggers))
        _process_triggers(state, triggers)

    _save_state(state)


def main() -> None:
    """Main entry point. Polls forever."""
    logger.info("PM Monitor starting (interval=%ds, a2a=%s, headless=%s, max_spawns=%d/hr)",
                POLL_INTERVAL, A2A_URL, HEADLESS_ENABLED, MAX_SPAWNS_PER_HOUR)

    state = _load_state()

    # On first run, snapshot current task statuses to avoid alerting on old tasks
    if not state.get("task_statuses"):
        logger.info("First run: snapshotting current task statuses")
        data = _fetch_json("/tasks")
        if data:
            for task in data.get("tasks", []):
                state["task_statuses"][task.get("task_id", "")] = task.get("status", "")
            _save_state(state)

    # On first run, snapshot current message IDs to avoid alerting on old messages
    if not state.get("seen_message_ids"):
        logger.info("First run: snapshotting existing messages")
        data = _fetch_json("/messages?to=pm&limit=200")
        if data:
            for msg in data.get("messages", []):
                mid = msg.get("message_id", "")
                if mid:
                    state.setdefault("seen_message_ids", []).append(mid)
            _save_state(state)

    logger.info("PM Monitor running. State: %d seen msgs, %d tracked tasks, headless=%s",
                len(state.get("seen_message_ids", [])),
                len(state.get("task_statuses", {})),
                HEADLESS_ENABLED)

    while True:
        try:
            poll_once(state)
        except KeyboardInterrupt:
            logger.info("PM Monitor shutting down (keyboard interrupt)")
            _save_state(state)
            break
        except Exception:
            logger.exception("Poll cycle error (will retry in %ds)", POLL_INTERVAL)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
