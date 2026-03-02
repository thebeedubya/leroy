"""Generic Agent Message Bus.

Replaces the PM-specific message_broker.py with a generic bus where any agent
can message any other agent by name. One endpoint, any sender, any recipient.

Design:
- Messages persisted to SQLite via task_db.PersistentMessageStore (same table).
- Blocking messages use threading.Event for subprocess-side polling.
- Agent registry auto-populated on first send/receive.
- Read status is explicit (POST /messages/{id}/read), never auto-marked on GET.
  This lets monitor daemons poll without consuming messages.
- Distribution-ready: agents on remote machines hit the same HTTP endpoints.

Message schema:
{
    "message_id": "uuid",
    "from": "leroy",
    "to": "pm",
    "type": "request|response|status|alert|question|decision_gate|blocker",
    "content": "message text",
    "context": "optional background",
    "task_id": "optional, links to a task",
    "requires_response": bool,
    "responded": bool,
    "response": null | "response text",
    "response_from": null | "agent name",
    "responded_at": null | "ISO8601",
    "created_at": "ISO8601",
    "read": false,
    "read_at": null | "ISO8601"
}
"""

import logging
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

import task_db

logger = logging.getLogger("leroy-agentbus")

# Persistent store -- initialized by init() from server main()
_store: task_db.PersistentMessageStore | None = None
_agent_store: task_db.AgentStore | None = None

# threading.Event store for blocking messages -- in-memory only
_event_lock = threading.Lock()
_response_events: dict[str, threading.Event] = {}


def init(msg_store: task_db.PersistentMessageStore, agent_store: task_db.AgentStore) -> None:
    """Wire in persistent stores. Call once from server main()."""
    global _store, _agent_store
    _store = msg_store
    _agent_store = agent_store
    logger.info("Agent bus initialized (%d messages loaded)", len(msg_store._messages))


def _touch_agent(name: str, machine: str = "haze") -> None:
    """Auto-register or update last_seen for an agent."""
    if _agent_store is None:
        return
    existing = _agent_store.get(name)
    agent = existing or {"name": name, "type": "unknown", "machine": machine}
    agent["last_seen"] = datetime.now(timezone.utc).isoformat()
    _agent_store.upsert(agent)


# ---------------------------------------------------------------------------
# Core bus API
# ---------------------------------------------------------------------------

BLOCKING_TYPES = {"question", "decision_gate", "blocker"}


def send(msg_dict: dict) -> dict:
    """Send a message from one agent to another. Returns the stored message.

    Required fields: from, to, content
    Optional: type, task_id, context, requires_response
    """
    sender = msg_dict.get("from", "unknown")
    recipient = msg_dict.get("to", "unknown")
    msg_type = msg_dict.get("type", "status")
    requires_response = msg_dict.get("requires_response", msg_type in BLOCKING_TYPES)

    message_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()

    msg = {
        "message_id": message_id,
        "from": sender,
        "to": recipient,
        "type": msg_type,
        "content": msg_dict.get("content", ""),
        "context": msg_dict.get("context", ""),
        "task_id": msg_dict.get("task_id"),
        "requires_response": requires_response,
        "responded": False,
        "response": None,
        "response_from": None,
        "responded_at": None,
        "created_at": now,
        "read": False,
        "read_at": None,
    }

    _store.store(message_id, msg)

    # Create threading.Event for blocking messages
    if requires_response:
        with _event_lock:
            _response_events[message_id] = threading.Event()

    # Touch agent registry
    _touch_agent(sender)
    _touch_agent(recipient)

    logger.info(
        "Message %s: %s -> %s (type=%s, blocking=%s)",
        message_id, sender, recipient, msg_type, requires_response,
    )
    return msg


def respond(message_id: str, responder: str, content: str) -> bool:
    """Respond to a message. Returns True if message existed."""
    with _store._lock:
        if message_id not in _store._messages:
            logger.warning("respond: message %s not found", message_id)
            return False

        msg = _store._messages[message_id]
        msg["responded"] = True
        msg["response"] = content
        msg["response_from"] = responder
        msg["responded_at"] = datetime.now(timezone.utc).isoformat()
        _store._responses[message_id] = content
        msg_copy = dict(msg)

    _store._db.upsert_message(message_id, msg_copy.get("task_id", "unknown"), msg_copy)

    # Signal any waiting thread
    with _event_lock:
        event = _response_events.get(message_id)
    if event:
        event.set()
        logger.info("Message %s: response from %s, event signaled", message_id, responder)
    else:
        logger.info("Message %s: response from %s stored", message_id, responder)

    _touch_agent(responder)
    return True


def mark_read(message_id: str, agent: str) -> bool:
    """Explicitly mark a message as read. Returns True if message existed."""
    with _store._lock:
        if message_id not in _store._messages:
            return False
        msg = _store._messages[message_id]
        msg["read"] = True
        msg["read_at"] = datetime.now(timezone.utc).isoformat()
        msg_copy = dict(msg)

    _store._db.upsert_message(message_id, msg_copy.get("task_id", "unknown"), msg_copy)
    logger.debug("Message %s marked read by %s", message_id, agent)
    return True


def get_message(message_id: str) -> dict | None:
    """Get a single message by ID."""
    return _store.get(message_id)


def wait_for_response(message_id: str, timeout: float = 600.0) -> str | None:
    """Block until a response arrives or timeout. For subprocess polling."""
    # Already responded?
    existing = _store.get_response(message_id)
    if existing is not None:
        return existing

    with _event_lock:
        event = _response_events.get(message_id)

    if event is None:
        return _store.get_response(message_id)

    logger.debug("Message %s: blocking wait (timeout=%ds)", message_id, timeout)
    signaled = event.wait(timeout=timeout)
    if signaled:
        return _store.get_response(message_id)
    else:
        logger.warning("Message %s: timed out (%ds)", message_id, timeout)
        return None


def poll_response(message_id: str) -> str | None:
    """Non-blocking check for response."""
    return _store.get_response(message_id)


def list_messages(
    to: str | None = None,
    from_agent: str | None = None,
    pending: bool = False,
    unread: bool = False,
    msg_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List messages with optional filters. Never auto-marks as read."""
    with _store._lock:
        msgs = sorted(
            _store._messages.values(),
            key=lambda m: m.get("created_at", ""),
            reverse=True,
        )

    result = []
    for m in msgs:
        if to and m.get("to") != to:
            continue
        if from_agent and m.get("from") != from_agent:
            continue
        if pending and (not m.get("requires_response") or m.get("responded")):
            continue
        if unread and m.get("read"):
            continue
        if msg_type and m.get("type") != msg_type:
            continue
        result.append(dict(m))
        if len(result) >= limit:
            break

    return result


def agent_summary() -> list[dict]:
    """List known agents with their unread/pending message counts."""
    if _agent_store is None:
        return []

    agents = _agent_store.list_all()
    with _store._lock:
        all_msgs = list(_store._messages.values())

    for agent in agents:
        name = agent["name"]
        agent["unread_count"] = sum(
            1 for m in all_msgs
            if m.get("to") == name and not m.get("read")
        )
        agent["pending_response_count"] = sum(
            1 for m in all_msgs
            if m.get("to") == name and m.get("requires_response") and not m.get("responded")
        )

    return agents


def pending_count(agent: str | None = None) -> int:
    """Count messages awaiting response, optionally for a specific agent."""
    with _store._lock:
        return sum(
            1 for m in _store._messages.values()
            if m.get("requires_response") and not m.get("responded")
            and (agent is None or m.get("to") == agent)
        )
