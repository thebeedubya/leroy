"""PM Webhook Sidecar.

Lightweight HTTP server that runs alongside PM's Claude CLI session.
Started by pm.sh before launching claude. Shuts down when pm.sh exits.

Responsibilities:
1. Receive forwarded messages from the Leroy A2A server (port 9800)
2. Write messages to ~/.forge/pm_messages.json for persistence
3. Send macOS desktop notifications so PM sees incoming messages immediately
4. Provide a health endpoint for A2A server discovery

Port: 9802 (hardcoded, not in use by any other FORGE service)

Discovery registration:
  On startup: write {"url": "http://127.0.0.1:9802", "started_at": "...", "pid": ...}
              to ~/.forge/pm_webhook.json
  On shutdown: remove ~/.forge/pm_webhook.json

This sidecar does NOT handle PM responses -- those go directly to the A2A server
at port 9800 via the MCP tool leroy_reply_to_message. This keeps routing simple:
  - Outbound (Leroy→PM): A2A → this sidecar → notification
  - Inbound  (PM→Leroy): PM MCP tool → A2A server directly
"""

import atexit
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PORT = int(os.environ.get("PM_WEBHOOK_PORT", "9802"))
HOST = os.environ.get("PM_WEBHOOK_HOST", "127.0.0.1")

FORGE_DIR = Path.home() / ".forge"
REGISTRY_FILE = FORGE_DIR / "pm_webhook.json"
MESSAGES_FILE = FORGE_DIR / "pm_messages.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s pm-webhook %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pm-webhook")

# ---------------------------------------------------------------------------
# In-memory message queue (complements the file store)
# ---------------------------------------------------------------------------
_messages: list[dict] = []
_START_TIME = time.time()


# ---------------------------------------------------------------------------
# macOS notification
# ---------------------------------------------------------------------------
def _send_notification(title: str, body: str, subtitle: str = "") -> None:
    """Send macOS desktop notification via osascript."""
    try:
        # Escape double quotes in strings to avoid AppleScript injection
        safe_body = body.replace('"', '\\"')
        safe_title = title.replace('"', '\\"')
        safe_subtitle = subtitle.replace('"', '\\"')
        subtitle_clause = f'subtitle "{safe_subtitle}"' if subtitle else ""
        script = (
            f'display notification "{safe_body}" '
            f'with title "{safe_title}" '
            f'{subtitle_clause}'
            f' sound name "Glass"'
        )
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
        logger.info("Desktop notification sent: %s", title)
    except Exception as e:
        logger.warning("Failed to send desktop notification: %s", e)


def _read_pm_session() -> dict | None:
    """Read PM session info from ~/.forge/pm_session.json.

    Written by pm.sh at launch. Contains Terminal window_id and shell PID.
    Returns None if not present or unreadable.
    """
    session_file = FORGE_DIR / "pm_session.json"
    try:
        if session_file.exists():
            return json.loads(session_file.read_text())
    except Exception as e:
        logger.debug("Failed to read pm_session.json: %s", e)
    return None


def _inject_into_pm_terminal(msg_type: str, content: str, task_id: str) -> bool:
    """Inject a message prompt into PM's terminal window via System Events keystrokes.

    This causes Claude Code (PM) to receive the text as user input without Brad
    having to manually relay the message. Fires for blocking messages
    (question, decision_gate, blocker) AND for deliverable_ready (task completion).

    Mechanism:
    1. pm.sh writes the Terminal window ID to ~/.forge/pm_session.json at launch.
    2. This function reads that window ID and targets the exact window regardless
       of how the title has changed (Claude Code overwrites the title via ANSI).
    3. System Events keystrokes inject the alert text as if Brad typed it.

    Requires Accessibility access granted for Terminal in System Preferences.
    Returns True if injection succeeded, False otherwise (non-fatal: desktop
    notification is still active as a fallback).
    """
    session = _read_pm_session()
    if not session or not session.get("window_id"):
        logger.warning(
            "No PM session file found at ~/.forge/pm_session.json. "
            "Was pm.sh used to start PM? Terminal injection skipped."
        )
        return False

    window_id = int(session["window_id"])

    # Build a terse injection prompt -- enough to trigger PM but not spammy
    type_verb = {
        "question": "has a QUESTION",
        "decision_gate": "needs a DECISION",
        "blocker": "is BLOCKED",
        "deliverable_ready": "COMPLETED a task",
    }.get(msg_type, f"sent a {msg_type}")

    short_task = task_id[:8] if len(task_id) > 8 else task_id

    if msg_type == "deliverable_ready":
        prompt = (
            f"[LEROY ALERT] Leroy {type_verb} (task {short_task}). "
            f"Use leroy_check_task('{task_id}') to review the result, then send QA spec."
        )
    else:
        prompt = (
            f"[LEROY ALERT] Leroy {type_verb} (task {short_task}). "
            f"Use leroy_read_messages to read it and leroy_reply_to_message to respond."
        )
    # Escape for AppleScript string literal (backslash and double-quote)
    safe_prompt = prompt.replace("\\", "\\\\").replace('"', '\\"')

    # AppleScript: target the PM window by its stable window ID (captured at pm.sh launch).
    # Window IDs survive title changes caused by Claude Code ANSI escape sequences.
    script = f'''
set targetWindowId to {window_id}
set injected to false
tell application "Terminal"
    repeat with w in windows
        if id of w is targetWindowId then
            set index of w to 1
            activate
            delay 0.3
            tell application "System Events"
                tell process "Terminal"
                    keystroke "{safe_prompt}"
                    key code 36
                end tell
            end tell
            set injected to true
            exit repeat
        end if
    end repeat
end tell
return injected
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            logger.info(
                "Terminal injection succeeded for %s message (task %s)", msg_type, task_id
            )
            return True
        else:
            # Log but don't error -- desktop notification is still in flight
            logger.warning(
                "Terminal injection did not find PM window id=%d "
                "(returncode=%d stdout=%s stderr=%s). "
                "Desktop notification is still active.",
                window_id,
                result.returncode,
                result.stdout.strip()[:100],
                result.stderr.strip()[:200],
            )
            return False
    except subprocess.TimeoutExpired:
        logger.warning("Terminal injection timed out (Accessibility prompt may be pending?)")
        return False
    except Exception as e:
        logger.warning("Terminal injection failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Message file store
# ---------------------------------------------------------------------------
def _load_messages() -> list[dict]:
    """Load messages from disk. Returns empty list if missing/corrupt."""
    try:
        if MESSAGES_FILE.exists():
            return json.loads(MESSAGES_FILE.read_text())
    except Exception as e:
        logger.warning("Failed to load messages file: %s", e)
    return []


def _save_messages(messages: list[dict]) -> None:
    """Atomically save messages to disk."""
    FORGE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MESSAGES_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(messages, indent=2))
        tmp.replace(MESSAGES_FILE)
    except Exception as e:
        logger.error("Failed to save messages: %s", e)


def _append_message(msg: dict) -> None:
    """Append message to file store, keep last 200."""
    messages = _load_messages()
    messages.append(msg)
    if len(messages) > 200:
        messages = messages[-200:]
    _save_messages(messages)


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------

async def receive_message(request: Request) -> JSONResponse:
    """POST /messages -- A2A server forwards a Leroy message here.

    Writes to queue, sends desktop notification.
    No auth: localhost-only, same machine as A2A server.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    msg_type = body.get("type", "unknown")
    task_id = body.get("task_id", "unknown")
    content = body.get("content", "")
    message_id = body.get("message_id", "unknown")
    requires_response = body.get("requires_response", False)

    # Add received_at if missing
    if "received_at" not in body:
        body["received_at"] = datetime.now(timezone.utc).isoformat()

    # Store in memory + file
    _messages.append(body)
    if len(_messages) > 200:
        _messages.pop(0)
    _append_message(body)

    logger.info(
        "Message received: type=%s task=%s id=%s requires_response=%s",
        msg_type, task_id, message_id, requires_response,
    )

    # Build notification
    type_labels = {
        "question": "Leroy has a QUESTION",
        "decision_gate": "Leroy needs a DECISION",
        "blocker": "Leroy is BLOCKED",
        "status_update": "Leroy status update",
        "deliverable_ready": "Leroy: deliverable ready for review",
    }
    title = type_labels.get(msg_type, f"Leroy: {msg_type}")
    preview = content[:120] + "..." if len(content) > 120 else content
    subtitle = f"Task: {task_id[:12]}..." if len(task_id) > 12 else f"Task: {task_id}"

    if requires_response:
        # Higher urgency notification for blocking messages
        title = f"ACTION REQUIRED: {title}"

    _send_notification(title, preview, subtitle)

    # Terminal injection for:
    # - Blocking messages (question/decision_gate/blocker): PM must respond.
    # - deliverable_ready: task completed/failed; PM should kick off QA.
    # status_update is intentionally excluded -- informational, no action required.
    should_inject = requires_response or msg_type == "deliverable_ready"
    if should_inject:
        # Run in a background thread -- injection can take up to 8s (osascript)
        # and we don't want to block the HTTP response to Leroy.
        import threading
        threading.Thread(
            target=_inject_into_pm_terminal,
            args=(msg_type, content, task_id),
            daemon=True,
        ).start()

    return JSONResponse({"status": "ok", "message_id": message_id})


async def list_pending(request: Request) -> JSONResponse:
    """GET /messages/pending -- List messages not yet responded to."""
    pending = [m for m in _messages if m.get("requires_response") and not m.get("responded")]
    return JSONResponse({"messages": pending, "count": len(pending)})


async def list_all_messages(request: Request) -> JSONResponse:
    """GET /messages -- List recent messages (newest first)."""
    limit = int(request.query_params.get("limit", "20"))
    msgs = list(reversed(_messages[-limit:]))
    return JSONResponse({"messages": msgs, "count": len(msgs)})


async def health(request: Request) -> JSONResponse:
    """GET /health -- Sidecar health check."""
    uptime = time.time() - _START_TIME
    pending_count = sum(
        1 for m in _messages if m.get("requires_response") and not m.get("responded")
    )
    return JSONResponse({
        "status": "ok",
        "service": "pm-webhook",
        "port": PORT,
        "uptime_seconds": round(uptime, 1),
        "messages_in_memory": len(_messages),
        "pending_pm_response": pending_count,
        "messages_file": str(MESSAGES_FILE),
    })


# ---------------------------------------------------------------------------
# Registry management
# ---------------------------------------------------------------------------

def _register() -> None:
    """Write webhook URL to registry file so A2A server can discover it."""
    FORGE_DIR.mkdir(parents=True, exist_ok=True)
    registry = {
        "url": f"http://{HOST}:{PORT}",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
    }
    REGISTRY_FILE.write_text(json.dumps(registry, indent=2))
    logger.info("Registered PM webhook at %s (registry: %s)", registry["url"], REGISTRY_FILE)


def _deregister() -> None:
    """Remove registry file on shutdown."""
    try:
        if REGISTRY_FILE.exists():
            REGISTRY_FILE.unlink()
            logger.info("PM webhook deregistered (removed %s)", REGISTRY_FILE)
    except Exception as e:
        logger.warning("Failed to deregister PM webhook: %s", e)


# ---------------------------------------------------------------------------
# App with lifespan for clean shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app):
    """ASGI lifespan: startup and shutdown hooks."""
    # startup -- nothing needed (registration happens in main() before uvicorn.run())
    yield
    # shutdown -- deregister so A2A server stops forwarding to stale URL
    _deregister()


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/messages/pending", list_pending, methods=["GET"]),
        Route("/messages", receive_message, methods=["POST"]),
        Route("/messages", list_all_messages, methods=["GET"]),
    ],
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _kill_stale_webhook() -> None:
    """Kill any orphaned pm_webhook process holding our port.

    When pm.sh is force-closed or the terminal is killed, the cleanup trap
    doesn't fire and the sidecar orphans (reparented to PID 1). This guard
    runs on every startup to reclaim the port.
    """
    # Check registry file first -- fastest path
    if REGISTRY_FILE.exists():
        try:
            reg = json.loads(REGISTRY_FILE.read_text())
            stale_pid = reg.get("pid")
            if stale_pid and stale_pid != os.getpid():
                # Verify it's actually a pm_webhook process before killing
                import signal
                try:
                    os.kill(stale_pid, 0)  # check if alive
                    os.kill(stale_pid, signal.SIGTERM)
                    logger.info(
                        "Killed stale pm_webhook (PID %d) from previous session",
                        stale_pid,
                    )
                    # Brief wait for port release
                    time.sleep(0.3)
                except ProcessLookupError:
                    pass  # already dead, just stale registry
                except PermissionError:
                    logger.warning(
                        "Cannot kill stale PID %d (permission denied)", stale_pid
                    )
        except Exception as e:
            logger.debug("Failed to check stale registry: %s", e)

    # Belt-and-suspenders: also check lsof in case registry was deleted but process lives
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{PORT}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            for pid_str in result.stdout.strip().split("\n"):
                pid = int(pid_str.strip())
                if pid != os.getpid():
                    import signal
                    try:
                        os.kill(pid, signal.SIGTERM)
                        logger.info(
                            "Killed process PID %d holding port %d", pid, PORT
                        )
                        time.sleep(0.3)
                    except (ProcessLookupError, PermissionError):
                        pass
    except Exception:
        pass  # lsof not available or failed -- non-fatal


def main():
    """Start PM webhook sidecar."""
    _kill_stale_webhook()
    _register()

    logger.info("PM webhook sidecar starting on %s:%d", HOST, PORT)
    logger.info("Messages file: %s", MESSAGES_FILE)

    # Cleanup is handled by the lifespan shutdown hook (most reliable with uvicorn).
    # atexit as belt-and-suspenders for edge cases (e.g., SIGKILL from launchd).
    atexit.register(_deregister)

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="warning",  # suppress uvicorn access logs; our handlers log what matters
    )


if __name__ == "__main__":
    main()
