"""Leroy v2 Unified Event Bus (KISS consolidation: was agent_bus.py + bus_webhooks.py).

Single module for all agent-to-agent messaging and webhook push delivery.
Replaces three separate systems:
  - message_broker.py  (PM-specific, legacy — deleted)
  - bus_webhooks.py    (WebhookRegistry — inlined here)
  - agent_bus.py       (generic message bus — this file)

Design:
- Messages persisted to SQLite via task_db.PersistentMessageStore.
- Blocking messages use threading.Event for subprocess-side polling.
- Agent registry auto-populated on first send/receive.
- Read status is explicit (POST /messages/{id}/read), never auto-marked.
- Webhook push delivery: agents register URLs; messages are POSTed immediately.
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

import json
import logging
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

import httpx

import task_db

logger = logging.getLogger("leroy-agentbus")


# ---------------------------------------------------------------------------
# EventType enum — covers all message types used across the system
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    # Non-blocking informational
    STATUS_UPDATE    = "status_update"
    DELIVERABLE_READY = "deliverable_ready"
    INFRA_ALERT      = "infra_alert"
    ACTION_REQUIRED  = "action_required"

    # Blocking (require response)
    QUESTION         = "question"
    DECISION_GATE    = "decision_gate"
    BLOCKER          = "blocker"

    # Generic
    STATUS           = "status"
    REQUEST          = "request"
    RESPONSE         = "response"
    ALERT            = "alert"
    TASK_UPDATE      = "task_update"
    MESSAGE          = "message"


BLOCKING_TYPES = {EventType.QUESTION, EventType.DECISION_GATE, EventType.BLOCKER,
                  "question", "decision_gate", "blocker"}


# ---------------------------------------------------------------------------
# WebhookRegistry — push delivery for any agent subscriber
# (previously bus_webhooks.py)
# ---------------------------------------------------------------------------

MAX_DELIVERY_ATTEMPTS    = 3
DELIVERY_TIMEOUT         = 5.0   # seconds per attempt
DELIVERY_BACKOFF         = 2.0   # seconds between retries
MAX_REGISTRATIONS_PER_AGENT = 5


class WebhookRegistry:
    """Manages webhook registrations and push delivery.

    Thread-safe. Backed by SQLite for persistence across restarts.
    Agents register URLs; when a message arrives for them, an immediate
    POST is sent. Eliminates polling lag.

    Registration:
      POST /webhooks/register {agent: "pm", url: "http://...", events: ["message"]}
    """

    def __init__(self, db=None):
        self._registrations: dict[str, dict] = {}  # webhook_id -> registration
        self._by_agent: dict[str, list[str]] = {}   # agent_name -> [webhook_ids]
        self._lock = threading.Lock()
        self._db = db
        self._delivery_thread: threading.Thread | None = None
        self._delivery_queue: list[dict] = []
        self._delivery_lock = threading.Lock()
        self._stop_event = threading.Event()

        if db:
            self._ensure_table()
            self._load_from_db()

    def _ensure_table(self):
        with self._db._write_lock:
            self._db._conn.executescript("""
                CREATE TABLE IF NOT EXISTS webhooks (
                    webhook_id TEXT PRIMARY KEY,
                    agent TEXT NOT NULL,
                    url TEXT NOT NULL,
                    events TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_delivery_at TEXT,
                    delivery_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    active BOOLEAN DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_webhooks_agent ON webhooks(agent);
                CREATE INDEX IF NOT EXISTS idx_webhooks_active ON webhooks(active);
            """)
            self._db._conn.commit()

    def _load_from_db(self):
        rows = self._db._conn.execute(
            "SELECT * FROM webhooks WHERE active = 1"
        ).fetchall()
        for row in rows:
            reg = dict(row)
            reg["events"] = json.loads(reg["events"]) if isinstance(reg["events"], str) else reg["events"]
            webhook_id = reg["webhook_id"]
            agent = reg["agent"]
            with self._lock:
                self._registrations[webhook_id] = reg
                self._by_agent.setdefault(agent, []).append(webhook_id)
        logger.info("WebhookRegistry: loaded %d registration(s) from DB", len(self._registrations))

    def start(self):
        self._delivery_thread = threading.Thread(
            target=self._delivery_loop, daemon=True, name="webhook-delivery"
        )
        self._delivery_thread.start()
        logger.info("Webhook delivery thread started")

    def stop(self):
        self._stop_event.set()

    def register(self, agent: str, url: str, events: list[str] | None = None) -> dict:
        """Register a webhook URL for an agent. Returns registration dict."""
        events = events or ["message"]
        webhook_id = uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()

        reg = {
            "webhook_id": webhook_id,
            "agent": agent,
            "url": url,
            "events": events,
            "created_at": now,
            "last_delivery_at": None,
            "delivery_count": 0,
            "failure_count": 0,
            "active": True,
        }

        with self._lock:
            existing = self._by_agent.get(agent, [])
            if len(existing) >= MAX_REGISTRATIONS_PER_AGENT:
                return {"error": f"max {MAX_REGISTRATIONS_PER_AGENT} webhooks per agent", "registered": False}
            self._registrations[webhook_id] = reg
            self._by_agent.setdefault(agent, []).append(webhook_id)

        if self._db:
            with self._db._write_lock:
                self._db._conn.execute(
                    """INSERT OR REPLACE INTO webhooks
                       (webhook_id, agent, url, events, created_at, active)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (webhook_id, agent, url, json.dumps(events), now),
                )
                self._db._conn.commit()

        logger.info("Webhook registered: %s for agent %s -> %s (events: %s)",
                    webhook_id, agent, url, events)
        return {"webhook_id": webhook_id, "registered": True, **reg}

    def unregister(self, webhook_id: str) -> bool:
        with self._lock:
            reg = self._registrations.pop(webhook_id, None)
            if reg is None:
                return False
            ids = self._by_agent.get(reg["agent"], [])
            if webhook_id in ids:
                ids.remove(webhook_id)

        if self._db:
            with self._db._write_lock:
                self._db._conn.execute(
                    "UPDATE webhooks SET active = 0 WHERE webhook_id = ?", (webhook_id,)
                )
                self._db._conn.commit()

        logger.info("Webhook unregistered: %s (agent=%s)", webhook_id, reg["agent"])
        return True

    def list_registrations(self, agent: str | None = None) -> list[dict]:
        with self._lock:
            regs = list(self._registrations.values())
        if agent:
            regs = [r for r in regs if r["agent"] == agent]
        return regs

    def notify(self, agent: str, event_type: str, payload: dict) -> int:
        """Queue push delivery for all registered hooks matching agent + event_type.

        Returns number of webhooks queued.
        """
        with self._lock:
            targets = [
                dict(self._registrations[wid])
                for wid in self._by_agent.get(agent, [])
                if (reg := self._registrations.get(wid))
                and reg.get("active")
                and event_type in reg.get("events", [])
            ]

        if not targets:
            return 0

        delivery = {
            "event_type": event_type,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "targets": targets,
        }
        with self._delivery_lock:
            self._delivery_queue.append(delivery)

        return len(targets)

    def _delivery_loop(self):
        while not self._stop_event.is_set():
            with self._delivery_lock:
                batch = list(self._delivery_queue)
                self._delivery_queue.clear()

            for delivery in batch:
                for target in delivery["targets"]:
                    self._deliver_one(target, delivery["event_type"],
                                      delivery["payload"], delivery["timestamp"])

            self._stop_event.wait(timeout=0.5)

    def _deliver_one(self, target: dict, event_type: str, payload: dict, timestamp: str) -> bool:
        url = target["url"]
        webhook_id = target["webhook_id"]
        body = {"event_type": event_type, "payload": payload,
                "timestamp": timestamp, "webhook_id": webhook_id}

        for attempt in range(1, MAX_DELIVERY_ATTEMPTS + 1):
            try:
                with httpx.Client(timeout=DELIVERY_TIMEOUT) as client:
                    resp = client.post(url, json=body)
                    if resp.status_code < 400:
                        self._record_delivery(webhook_id, success=True)
                        return True
                    logger.warning("Webhook %s delivery to %s failed (status=%d, attempt=%d/%d)",
                                   webhook_id, url, resp.status_code, attempt, MAX_DELIVERY_ATTEMPTS)
            except Exception as e:
                logger.warning("Webhook %s delivery to %s error (attempt=%d/%d): %s",
                               webhook_id, url, attempt, MAX_DELIVERY_ATTEMPTS, e)

            if attempt < MAX_DELIVERY_ATTEMPTS:
                time.sleep(DELIVERY_BACKOFF * attempt)

        self._record_delivery(webhook_id, success=False)
        logger.error("Webhook %s delivery to %s FAILED after %d attempts",
                     webhook_id, url, MAX_DELIVERY_ATTEMPTS)
        return False

    def _record_delivery(self, webhook_id: str, success: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            reg = self._registrations.get(webhook_id)
            if reg:
                reg["last_delivery_at"] = now
                reg["delivery_count"] = reg.get("delivery_count", 0) + 1
                if not success:
                    reg["failure_count"] = reg.get("failure_count", 0) + 1

        if self._db:
            try:
                col = "delivery_count" if success else "failure_count"
                with self._db._write_lock:
                    self._db._conn.execute(
                        f"UPDATE webhooks SET last_delivery_at = ?, {col} = {col} + 1 WHERE webhook_id = ?",
                        (now, webhook_id),
                    )
                    self._db._conn.commit()
            except Exception:
                pass

    def metrics(self) -> dict:
        with self._lock:
            regs = list(self._registrations.values())
        with self._delivery_lock:
            pending = len(self._delivery_queue)
        return {
            "registrations": len(regs),
            "pending_deliveries": pending,
            "total_deliveries": sum(r.get("delivery_count", 0) for r in regs),
            "total_failures": sum(r.get("failure_count", 0) for r in regs),
            "by_agent": {a: len(ids) for a, ids in self._by_agent.items()},
        }


# ---------------------------------------------------------------------------
# Module-level state (message bus)
# ---------------------------------------------------------------------------

# Persistent stores — initialized by init() from server main()
_store: task_db.PersistentMessageStore | None = None
_agent_store: task_db.AgentStore | None = None

# threading.Event store for blocking messages — in-memory only
_event_lock = threading.Lock()
_response_events: dict[str, threading.Event] = {}

# Webhook registry for push delivery — set by set_webhook_registry()
_webhook_registry: WebhookRegistry | None = None


def init(msg_store: task_db.PersistentMessageStore, agent_store: task_db.AgentStore) -> None:
    """Wire in persistent stores. Call once from server main()."""
    global _store, _agent_store
    _store = msg_store
    _agent_store = agent_store
    logger.info("Agent bus initialized (%d messages loaded)", len(msg_store._messages))


def set_webhook_registry(registry: WebhookRegistry) -> None:
    """Wire in webhook registry for push delivery. Call from server main()."""
    global _webhook_registry
    _webhook_registry = registry
    logger.info("Agent bus webhook push enabled")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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

def send(msg_dict: dict) -> dict:
    """Send a message from one agent to another. Returns the stored message.

    Required: from, to, content
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

    if requires_response:
        with _event_lock:
            _response_events[message_id] = threading.Event()

    _touch_agent(sender)
    _touch_agent(recipient)

    logger.info(
        "Message %s: %s -> %s (type=%s, blocking=%s)",
        message_id, sender, recipient, msg_type, requires_response,
    )

    # Push to registered webhooks (fire-and-forget)
    if _webhook_registry is not None:
        try:
            queued = _webhook_registry.notify(recipient, msg_type, msg)
            if queued:
                logger.debug("Message %s: queued push to %d webhook(s)", message_id, queued)
        except Exception as e:
            logger.warning("Message %s: webhook notify failed: %s", message_id, e)

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
    """Mark a message as read. Returns True if message existed."""
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
    """List known agents with unread/pending message counts."""
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


def route_infra_to_ops(task_id: str, failure_description: str,
                       categories: list[str] | None = None) -> dict:
    """Route an infrastructure failure to ops for diagnosis.

    Ops scope: Diagnose and report findings. Do NOT remediate autonomously.
    """
    content = f"INFRA ALERT: Task {task_id} — {failure_description}"
    if categories:
        content += f"\nFailure categories: {', '.join(categories)}"
    content += "\nScope: Diagnose and report findings. Do NOT remediate autonomously."

    return send({
        "from": "leroy",
        "to": "ops",
        "type": "infra_alert",
        "task_id": task_id,
        "content": content,
        "requires_response": False,
    })
