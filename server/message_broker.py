"""PM-Leroy Message Broker.

Manages bidirectional communication between the PM (Claude CLI session) and
Leroy subprocess instances.

Design:
- Messages are persisted to SQLite via task_db.PersistentMessageStore.
- Blocking messages use threading.Event for subprocess-side polling.
  threading.Event objects are in-memory only -- not persisted (transient).
- If PM webhook sidecar is running, messages are forwarded for macOS notification.
- If PM is offline, messages sit in pending state and are readable via MCP poll.
- No external dependencies: file-based discovery, HTTP-only forwarding.

Persistence:
- All messages and responses are written to SQLite immediately.
- On server restart, messages are reloaded from DB so MCP poll tools work.
- threading.Events for blocking messages are NOT reloaded (subprocesses die on restart).

PM webhook discovery: ~/.forge/pm_webhook.json
Content: {"url": "http://127.0.0.1:9802", "started_at": "...", "pid": ...}

Stale registry detection: if the registered PID is dead OR the HTTP health
endpoint is unreachable, the registry file is removed and None is returned.
This prevents the broker from wasting time trying to forward to a dead sidecar
and prevents the health endpoint from falsely reporting webhook=registered.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

import task_db

logger = logging.getLogger("leroy-msgbroker")

# Background flush interval in seconds -- retry unforwarded messages
_FLUSH_INTERVAL = 30

# Webhook reachability cache: avoid HTTP call on every message store
# Format: {"url": str, "reachable": bool, "checked_at": float}
_webhook_cache: dict = {}
_WEBHOOK_CACHE_TTL = 15.0  # seconds

# Where PM webhook sidecar registers itself
PM_WEBHOOK_REGISTRY = Path.home() / ".forge" / "pm_webhook.json"

# threading.Event store for blocking messages -- in-memory only, not persisted.
# Subprocess polling uses these Events; they die when the server restarts (as do
# the subprocesses). Old events from persisted messages are never recreated.
_event_lock = threading.Lock()
_response_events: dict[str, threading.Event] = {}  # message_id -> event

# Persistent message store -- initialized by init_store() in main().
# Fallback to an in-memory stub until init_store() is called (for tests).
_store: task_db.PersistentMessageStore | None = None


def init_store(store: task_db.PersistentMessageStore) -> None:
    """Wire in the persistent message store. Call once from server main()."""
    global _store
    _store = store
    logger.info("Message broker: persistent store attached (%d message(s) loaded)", len(store._messages))


# ---------------------------------------------------------------------------
# PM Webhook discovery
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int) -> bool:
    """Return True if the given PID is a running process."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _verify_webhook_reachable(url: str) -> bool:
    """GET /health on the webhook sidecar. Returns True if HTTP 200."""
    try:
        resp = httpx.get(f"{url}/health", timeout=1.5)
        return resp.status_code == 200
    except Exception:
        return False


def _get_pm_webhook_url() -> str | None:
    """Read PM webhook URL from registry file, with stale-registry detection.

    Validates that:
    1. The registry file exists.
    2. The registered PID is still alive.
    3. The sidecar health endpoint responds.

    If validation fails, removes the stale registry file and returns None.
    Uses a short-lived cache to avoid repeated HTTP calls on every message.
    """
    global _webhook_cache

    try:
        if not PM_WEBHOOK_REGISTRY.exists():
            _webhook_cache = {}
            return None

        data = json.loads(PM_WEBHOOK_REGISTRY.read_text())
        url = data.get("url", "").rstrip("/")
        pid = data.get("pid")

        if not url:
            return None

        # Fast path: cached result is fresh
        now = time.monotonic()
        if (
            _webhook_cache.get("url") == url
            and now - _webhook_cache.get("checked_at", 0) < _WEBHOOK_CACHE_TTL
        ):
            return url if _webhook_cache.get("reachable") else None

        # Validate: PID alive (cheap) then HTTP health (slightly more expensive)
        if pid and not _is_pid_alive(int(pid)):
            logger.warning(
                "PM webhook sidecar PID %d is dead -- removing stale registry %s",
                pid, PM_WEBHOOK_REGISTRY,
            )
            try:
                PM_WEBHOOK_REGISTRY.unlink(missing_ok=True)
            except Exception:
                pass
            _webhook_cache = {}
            return None

        reachable = _verify_webhook_reachable(url)
        _webhook_cache = {"url": url, "reachable": reachable, "checked_at": now}

        if not reachable:
            logger.warning(
                "PM webhook sidecar at %s is not responding -- treating as offline", url
            )
            return None

        return url

    except Exception as e:
        logger.debug("Failed to read PM webhook registry: %s", e)
    return None


def _forward_to_pm_webhook(msg: dict) -> bool:
    """POST message to PM webhook sidecar. Returns True if delivered."""
    url = _get_pm_webhook_url()
    if not url:
        logger.debug("Message %s: PM webhook not registered, skipping forward", msg["message_id"])
        return False
    try:
        resp = httpx.post(
            f"{url}/messages",
            json=msg,
            timeout=5.0,
        )
        if resp.status_code == 200:
            logger.info("Message %s: forwarded to PM webhook (%s)", msg["message_id"], url)
            return True
        else:
            logger.warning(
                "Message %s: PM webhook returned %d", msg["message_id"], resp.status_code
            )
            return False
    except Exception as e:
        logger.warning("Message %s: PM webhook forward failed: %s", msg["message_id"], e)
        return False


# ---------------------------------------------------------------------------
# Core broker API
# ---------------------------------------------------------------------------

def store_message(msg_dict: dict) -> str:
    """Store a message from a Leroy subprocess. Returns message_id.

    For blocking message types (question, decision_gate, blocker), creates
    a threading.Event so the subprocess can poll for the response.

    Attempts to forward to PM webhook sidecar. If unavailable, message sits
    in pending state for PM to read via MCP poll.
    """
    message_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()

    requires_response = msg_dict.get("requires_response", False)
    # Enforce requires_response based on type if not explicitly set
    msg_type = msg_dict.get("type", "status_update")
    if msg_type in ("question", "decision_gate", "blocker"):
        requires_response = True

    msg = {
        "message_id": message_id,
        "type": msg_type,
        "task_id": msg_dict.get("task_id", "unknown"),
        "leroy_instance": msg_dict.get("leroy_instance", "subprocess"),
        "content": msg_dict.get("content", ""),
        "options": msg_dict.get("options", []),
        "context": msg_dict.get("context", ""),
        "timestamp": msg_dict.get("timestamp", now),
        "received_at": now,
        "requires_response": requires_response,
        "responded": False,
        "forwarded_to_pm": False,
    }

    # Persist to store (SQLite + memory cache)
    _store.store(message_id, msg)

    # Create threading.Event for blocking message types
    if requires_response:
        with _event_lock:
            _response_events[message_id] = threading.Event()

    # Forward to PM webhook (outside lock to avoid blocking)
    forwarded = _forward_to_pm_webhook(msg)
    _store.set_forwarded(message_id, forwarded)

    logger.info(
        "Message %s stored (type=%s, task=%s, requires_response=%s, forwarded=%s)",
        message_id, msg_type, msg.get("task_id"), requires_response, forwarded,
    )
    return message_id


def store_response(message_id: str, response: str) -> bool:
    """Store PM's response to a blocking message. Returns True if message existed."""
    ok = _store.store_response(message_id, response)
    if not ok:
        logger.warning("store_response: message %s not found", message_id)
        return False

    # Signal any waiting subprocess thread
    with _event_lock:
        event = _response_events.get(message_id)

    if event:
        event.set()
        logger.info("Message %s: response stored and event signaled", message_id)
    else:
        logger.info("Message %s: response stored (non-blocking message)", message_id)

    return True


def get_response(message_id: str, timeout: float = 600.0) -> str | None:
    """Block until PM responds to a message, or timeout expires.

    Called by the blocking-message poll endpoint. Returns response string
    or None if timed out.

    Note: This is called from an async handler via run_in_executor to avoid
    blocking the event loop.
    """
    # Already responded? (covers post-restart case where Event is gone)
    existing = _store.get_response(message_id)
    if existing is not None:
        return existing

    with _event_lock:
        event = _response_events.get(message_id)

    if event is None:
        logger.warning("get_response: no event for message %s (not a blocking message?)", message_id)
        return _store.get_response(message_id)

    logger.debug("Message %s: blocking wait for PM response (timeout=%ds)", message_id, timeout)
    signaled = event.wait(timeout=timeout)
    if signaled:
        return _store.get_response(message_id)
    else:
        logger.warning("Message %s: timed out waiting for PM response (%ds)", message_id, timeout)
        return None


def poll_response(message_id: str) -> str | None:
    """Non-blocking check for PM response. Returns response or None if not yet available."""
    return _store.get_response(message_id)


def list_pending() -> list[dict]:
    """Return all messages not yet responded to (for PM MCP poll)."""
    return _store.list_pending()


def list_all(limit: int = 50) -> list[dict]:
    """Return all messages (most recent first, up to limit)."""
    return _store.list_all(limit=limit)


def get_message(message_id: str) -> dict | None:
    """Return a specific message by ID."""
    return _store.get(message_id)


def pending_count() -> int:
    """Return count of messages awaiting PM response."""
    return _store.pending_count()


def pm_webhook_registered() -> bool:
    """Return True if PM webhook sidecar is registered AND reachable."""
    return _get_pm_webhook_url() is not None


def flush_unforwarded() -> int:
    """Forward any messages that were not delivered when PM was offline.

    Called by the background flush thread AND by the A2A server on demand.
    Returns count of messages successfully forwarded.
    """
    url = _get_pm_webhook_url()
    if not url:
        return 0

    unforwarded = _store.list_unforwarded()
    if not unforwarded:
        return 0

    logger.info("Flushing %d unforwarded messages to PM webhook", len(unforwarded))
    forwarded_count = 0
    for msg in unforwarded:
        try:
            resp = httpx.post(f"{url}/messages", json=msg, timeout=5.0)
            if resp.status_code == 200:
                _store.set_forwarded(msg["message_id"], True)
                forwarded_count += 1
                logger.info("Flushed message %s to PM webhook", msg["message_id"])
            else:
                logger.warning(
                    "Flush: PM webhook returned %d for message %s",
                    resp.status_code, msg["message_id"],
                )
        except Exception as e:
            logger.debug("Flush: failed to forward message %s: %s", msg["message_id"], e)
            # Stop trying -- PM webhook may have gone offline again
            break

    return forwarded_count


def _flush_loop() -> None:
    """Background thread: periodically flush unforwarded messages to PM webhook."""
    while True:
        time.sleep(_FLUSH_INTERVAL)
        try:
            n = flush_unforwarded()
            if n > 0:
                logger.info("Background flush: delivered %d message(s) to PM", n)
        except Exception as e:
            logger.debug("Background flush error: %s", e)


def start_flush_thread() -> None:
    """Start the background flush thread. Call once at server startup."""
    t = threading.Thread(target=_flush_loop, name="msg-flush", daemon=True)
    t.start()
    logger.info("Message broker flush thread started (interval=%ds)", _FLUSH_INTERVAL)
