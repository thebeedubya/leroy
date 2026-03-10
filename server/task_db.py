"""SQLite-backed persistent store for Leroy task, subtask, and message data.

Design:
- Single SQLite DB file (WAL mode) at LEROY_TASK_DB_PATH (default: ../data/tasks.db).
- All writes are synchronous and immediate -- no buffering.
- In-memory cache in PersistentTaskDict/PersistentSubtaskStore for fast reads.
- Thread-safe: RLock for cache, write_lock for SQLite writes.
- On startup, loads all rows from DB into memory (cold start recovery).

Tables:
  tasks     -- task_id PK, data JSON blob
  subtasks  -- (subtask_id, task_id) PK, data JSON blob
  messages  -- message_id PK, task_id, data JSON blob

Usage:
  import task_db
  task_db.init()                          # call once at server startup
  task_meta = task_db.task_meta           # PersistentTaskDict
  subtask_store = task_db.subtask_store   # PersistentSubtaskStore
  msg_store = task_db.msg_store           # PersistentMessageStore
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("leroy-taskdb")

# ---------------------------------------------------------------------------
# DB path config
# ---------------------------------------------------------------------------
_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "tasks.db"
DB_PATH = Path(os.environ.get("LEROY_TASK_DB_PATH", str(_DEFAULT_DB_PATH)))


# ---------------------------------------------------------------------------
# TrackedDict -- dict subclass that triggers a persist callback on mutation
# ---------------------------------------------------------------------------
class TrackedDict(dict):
    """A dict that calls on_change(full_copy) whenever a key is set or popped.

    Created by PersistentTaskDict.__getitem__ so mutations to task dicts
    automatically persist to SQLite without changing call sites in server.py.
    """

    def __init__(self, data: dict, on_change):
        super().__init__(data)
        self._on_change = on_change

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._on_change(dict(self))

    def pop(self, key, *args):
        result = super().pop(key, *args)
        self._on_change(dict(self))
        return result

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self._on_change(dict(self))


# ---------------------------------------------------------------------------
# Core SQLite layer
# ---------------------------------------------------------------------------
class TaskDB:
    """Thread-safe SQLite database for tasks, subtasks, and messages."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        # Single shared connection with check_same_thread=False + write lock.
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        logger.info("TaskDB initialized at %s", db_path)

    def _init_schema(self):
        with self._write_lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id   TEXT PRIMARY KEY,
                    data      TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subtasks (
                    subtask_id TEXT NOT NULL,
                    task_id    TEXT NOT NULL,
                    data       TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (subtask_id, task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_subtasks_task ON subtasks(task_id);

                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    task_id    TEXT,
                    data       TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);

                CREATE TABLE IF NOT EXISTS agents (
                    name       TEXT PRIMARY KEY,
                    data       TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS activity_events (
                    id         TEXT PRIMARY KEY,
                    timestamp  TEXT NOT NULL,
                    agent      TEXT NOT NULL,
                    type       TEXT NOT NULL,
                    summary    TEXT NOT NULL,
                    detail     TEXT,
                    task_id    TEXT,
                    severity   TEXT NOT NULL DEFAULT 'info'
                );
                CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_events(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_activity_agent ON activity_events(agent);

                CREATE TABLE IF NOT EXISTS proposals (
                    proposal_id TEXT PRIMARY KEY,
                    data        TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(json_extract(data, '$.status'));
            """)
            self._conn.commit()

    # --- Tasks ---

    def upsert_task(self, task_id: str, data: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._write_lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO tasks (task_id, data, updated_at) VALUES (?, ?, ?)",
                (task_id, json.dumps(data), now),
            )
            self._conn.commit()

    def load_all_tasks(self) -> list[dict]:
        rows = self._conn.execute("SELECT data FROM tasks").fetchall()
        return [json.loads(r["data"]) for r in rows]

    def delete_task(self, task_id: str) -> bool:
        """Hard delete a task and its subtasks from SQLite. Returns True if task existed."""
        with self._write_lock:
            cursor = self._conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            self._conn.execute("DELETE FROM subtasks WHERE task_id = ?", (task_id,))
            self._conn.commit()
        return cursor.rowcount > 0

    # --- Subtasks ---

    def upsert_subtask(self, subtask_id: str, task_id: str, data: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._write_lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO subtasks (subtask_id, task_id, data, updated_at) VALUES (?, ?, ?, ?)",
                (subtask_id, task_id, json.dumps(data), now),
            )
            self._conn.commit()

    def load_subtasks(self, task_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data FROM subtasks WHERE task_id = ? ORDER BY updated_at",
            (task_id,),
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def load_all_subtasks(self) -> list[dict]:
        rows = self._conn.execute("SELECT data FROM subtasks ORDER BY task_id, updated_at").fetchall()
        return [json.loads(r["data"]) for r in rows]

    # --- Messages ---

    def upsert_message(self, message_id: str, task_id: str, data: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._write_lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO messages (message_id, task_id, data, updated_at) VALUES (?, ?, ?, ?)",
                (message_id, task_id, json.dumps(data), now),
            )
            self._conn.commit()

    def load_all_messages(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data FROM messages ORDER BY json_extract(data, '$.received_at') ASC"
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]


# ---------------------------------------------------------------------------
# PersistentTaskDict -- drop-in replacement for _task_meta dict in server.py
# ---------------------------------------------------------------------------
class PersistentTaskDict:
    """Dict-like interface backed by SQLite + in-memory cache.

    All reads go through the in-memory cache (fast).
    All writes persist to SQLite immediately (durable).

    Returns TrackedDict instances from __getitem__/__get__ so that
    field-level mutations (e.g., task["status"] = "working") are
    automatically persisted without changing call sites.
    """

    def __init__(self, db: TaskDB):
        self._db = db
        self._lock = threading.RLock()
        self._cache: dict[str, dict] = {}
        for task in db.load_all_tasks():
            self._cache[task["task_id"]] = task
        logger.info("PersistentTaskDict: loaded %d task(s) from DB", len(self._cache))

    def _on_change(self, task_id: str, updated: dict) -> None:
        with self._lock:
            self._cache[task_id] = dict(updated)
        self._db.upsert_task(task_id, dict(updated))

    def __setitem__(self, task_id: str, value: dict) -> None:
        with self._lock:
            self._cache[task_id] = dict(value)
        self._db.upsert_task(task_id, dict(value))

    def __getitem__(self, task_id: str) -> TrackedDict:
        with self._lock:
            data = dict(self._cache[task_id])
        return TrackedDict(data, lambda updated: self._on_change(task_id, updated))

    def __contains__(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._cache

    def get(self, task_id: str, default=None):
        with self._lock:
            if task_id not in self._cache:
                return default
            data = dict(self._cache[task_id])
        return TrackedDict(data, lambda updated: self._on_change(task_id, updated))

    def values(self) -> list[dict]:
        with self._lock:
            return [dict(v) for v in self._cache.values()]

    def items(self):
        with self._lock:
            return [(k, dict(v)) for k, v in self._cache.items()]

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def delete(self, task_id: str) -> bool:
        """Remove a task from cache and SQLite. Returns True if it existed."""
        with self._lock:
            if task_id not in self._cache:
                return False
            del self._cache[task_id]
        return self._db.delete_task(task_id)


# ---------------------------------------------------------------------------
# PersistentSubtaskStore -- drop-in replacement for _subtask_store dict
# ---------------------------------------------------------------------------
class PersistentSubtaskStore:
    """Dict-like interface for subtask storage backed by SQLite + memory cache.

    _subtask_store maps task_id -> list[subtask_dict].
    Subtasks are updated by subtask_id within a task's list.
    """

    def __init__(self, db: TaskDB):
        self._db = db
        self._lock = threading.RLock()
        self._cache: dict[str, list] = {}  # task_id -> [subtask, ...]
        for st in db.load_all_subtasks():
            tid = st["task_id"]
            self._cache.setdefault(tid, []).append(st)
        total = sum(len(v) for v in self._cache.values())
        logger.info("PersistentSubtaskStore: loaded %d subtask(s) from DB", total)

    def __contains__(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._cache

    def __setitem__(self, task_id: str, subtasks: list) -> None:
        """Bulk-set subtasks for a task (used when initializing empty list)."""
        with self._lock:
            self._cache[task_id] = list(subtasks)

    def __getitem__(self, task_id: str) -> list:
        with self._lock:
            return self._cache[task_id]

    def get(self, task_id: str, default=None):
        with self._lock:
            return self._cache.get(task_id, default)

    def setdefault(self, task_id: str, default: list) -> list:
        with self._lock:
            if task_id not in self._cache:
                self._cache[task_id] = list(default)
            return self._cache[task_id]

    def upsert_subtask(self, task_id: str, subtask: dict) -> str:
        """Upsert a subtask (add or update by subtask_id). Returns 'created' or 'updated'."""
        subtask_id = subtask["subtask_id"]
        with self._lock:
            if task_id not in self._cache:
                self._cache[task_id] = []
            existing = self._cache[task_id]
            for i, st in enumerate(existing):
                if st["subtask_id"] == subtask_id:
                    existing[i] = subtask
                    self._db.upsert_subtask(subtask_id, task_id, subtask)
                    return "updated"
            existing.append(subtask)
        self._db.upsert_subtask(subtask_id, task_id, subtask)
        return "created"


# ---------------------------------------------------------------------------
# PersistentMessageStore -- drop-in replacement for _messages dict in broker
# ---------------------------------------------------------------------------
class PersistentMessageStore:
    """In-memory message store backed by SQLite.

    threading.Event objects (_response_events) are NOT persisted -- they are
    transient and only valid for the lifetime of a subprocess connection.
    On startup, persisted messages are loaded (for MCP read access).
    Responses stored in persisted messages are re-seeded into the in-memory
    _responses dict so poll_response() works after a restart.
    """

    def __init__(self, db: TaskDB):
        self._db = db
        self._lock = threading.Lock()
        self._messages: dict[str, dict] = {}
        self._responses: dict[str, str] = {}   # message_id -> response text
        # _response_events is NOT here -- managed by message_broker directly.
        # Load all persisted messages.
        for msg in db.load_all_messages():
            mid = msg["message_id"]
            self._messages[mid] = msg
            # Re-seed in-memory response cache for already-responded messages.
            if msg.get("responded") and msg.get("pm_response"):
                self._responses[mid] = msg["pm_response"]
        logger.info("PersistentMessageStore: loaded %d message(s) from DB", len(self._messages))

    def store(self, message_id: str, msg: dict) -> None:
        with self._lock:
            self._messages[message_id] = msg
        self._db.upsert_message(message_id, msg.get("task_id", "unknown"), msg)

    def store_response(self, message_id: str, response: str) -> bool:
        with self._lock:
            if message_id not in self._messages:
                return False
            self._messages[message_id]["responded"] = True
            self._messages[message_id]["responded_at"] = datetime.now(timezone.utc).isoformat()
            self._messages[message_id]["pm_response"] = response
            self._responses[message_id] = response
            msg = dict(self._messages[message_id])
        self._db.upsert_message(message_id, msg.get("task_id", "unknown"), msg)
        return True

    def get(self, message_id: str) -> dict | None:
        with self._lock:
            return self._messages.get(message_id)

    def get_response(self, message_id: str) -> str | None:
        with self._lock:
            return self._responses.get(message_id)

    def set_forwarded(self, message_id: str, forwarded: bool) -> None:
        with self._lock:
            if message_id not in self._messages:
                return
            self._messages[message_id]["forwarded_to_pm"] = forwarded
            msg = dict(self._messages[message_id])
        self._db.upsert_message(message_id, msg.get("task_id", "unknown"), msg)

    def list_all(self, limit: int = 50) -> list[dict]:
        with self._lock:
            msgs = sorted(
                self._messages.values(),
                key=lambda m: m.get("received_at", ""),
                reverse=True,
            )
            return [dict(m) for m in msgs[:limit]]

    def list_pending(self) -> list[dict]:
        with self._lock:
            return [
                dict(m) for m in self._messages.values()
                if m.get("requires_response") and not m.get("responded")
            ]

    def list_unforwarded(self) -> list[dict]:
        with self._lock:
            return [
                dict(m) for m in self._messages.values()
                if not m.get("forwarded_to_pm", False)
            ]

    def pending_count(self) -> int:
        with self._lock:
            return sum(
                1 for m in self._messages.values()
                if m.get("requires_response") and not m.get("responded")
            )

    def __contains__(self, message_id: str) -> bool:
        with self._lock:
            return message_id in self._messages


# ---------------------------------------------------------------------------
# AgentStore -- agent registry backed by SQLite
# ---------------------------------------------------------------------------
class AgentStore:
    """In-memory agent registry backed by SQLite.

    Agents are keyed by name. Heartbeat updates are reflected immediately.
    """

    def __init__(self, db: "TaskDB"):
        self._db = db
        self._lock = threading.Lock()
        self._agents: dict[str, dict] = {}
        rows = db._conn.execute("SELECT data FROM agents").fetchall()
        for r in rows:
            a = json.loads(r["data"])
            self._agents[a["name"]] = a
        logger.info("AgentStore: loaded %d agent(s) from DB", len(self._agents))

    def upsert(self, agent: dict) -> None:
        name = agent["name"]
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._agents[name] = agent
        with self._db._write_lock:
            self._db._conn.execute(
                "INSERT OR REPLACE INTO agents (name, data, updated_at) VALUES (?, ?, ?)",
                (name, json.dumps(agent), now),
            )
            self._db._conn.commit()

    def get(self, name: str) -> dict | None:
        with self._lock:
            return dict(self._agents[name]) if name in self._agents else None

    def list_all(self) -> list[dict]:
        with self._lock:
            return [dict(a) for a in self._agents.values()]

    def __contains__(self, name: str) -> bool:
        with self._lock:
            return name in self._agents


# ---------------------------------------------------------------------------
# ActivityStore -- activity event log backed by SQLite (7-day rolling window)
# ---------------------------------------------------------------------------
class ActivityStore:
    """Append-only activity event store backed by SQLite with 7-day rolling window.

    Events are stored in SQLite and replicated in an in-memory deque (capped at 1000).
    SSE subscribers are notified on each new event.
    """

    RETENTION_DAYS = 7
    MAX_MEMORY = 1000

    def __init__(self, db: "TaskDB"):
        self._db = db
        self._lock = threading.Lock()
        self._events: list[dict] = []
        self._sse_subscribers: list = []  # list of callables
        # Load last MAX_MEMORY events from DB
        rows = db._conn.execute(
            "SELECT id, timestamp, agent, type, summary, detail, task_id, severity "
            "FROM activity_events ORDER BY timestamp DESC LIMIT ?",
            (self.MAX_MEMORY,),
        ).fetchall()
        for r in reversed(rows):
            self._events.append({
                "id": r["id"],
                "timestamp": r["timestamp"],
                "agent": r["agent"],
                "type": r["type"],
                "summary": r["summary"],
                "detail": r["detail"],
                "task_id": r["task_id"],
                "severity": r["severity"],
            })
        logger.info("ActivityStore: loaded %d event(s) from DB", len(self._events))

    def append(self, agent: str, event_type: str, summary: str,
               detail: str | None = None, task_id: str | None = None,
               severity: str = "info") -> dict:
        """Append an activity event and notify SSE subscribers."""
        import uuid
        evt = {
            "id": f"evt-{uuid.uuid4().hex[:12]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "type": event_type,
            "summary": summary,
            "detail": detail,
            "task_id": task_id,
            "severity": severity,
        }
        with self._db._write_lock:
            self._db._conn.execute(
                "INSERT OR REPLACE INTO activity_events "
                "(id, timestamp, agent, type, summary, detail, task_id, severity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (evt["id"], evt["timestamp"], evt["agent"], evt["type"],
                 evt["summary"], evt["detail"], evt["task_id"], evt["severity"]),
            )
            self._db._conn.commit()
        with self._lock:
            self._events.append(evt)
            if len(self._events) > self.MAX_MEMORY:
                self._events = self._events[-self.MAX_MEMORY:]
            subscribers = list(self._sse_subscribers)
        for cb in subscribers:
            try:
                cb(evt)
            except Exception:
                pass
        return evt

    def list_recent(self, limit: int = 100, since: str | None = None,
                    agent: str | None = None) -> list[dict]:
        with self._lock:
            evts = list(reversed(self._events))  # newest first
        if since:
            evts = [e for e in evts if e["timestamp"] > since]
        if agent:
            evts = [e for e in evts if e["agent"].lower() == agent.lower()]
        return evts[:limit]

    def add_sse_subscriber(self, callback) -> None:
        with self._lock:
            self._sse_subscribers.append(callback)

    def remove_sse_subscriber(self, callback) -> None:
        with self._lock:
            if callback in self._sse_subscribers:
                self._sse_subscribers.remove(callback)

    def purge_old(self) -> int:
        """Delete events older than RETENTION_DAYS. Returns count deleted."""
        cutoff = (datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat())
        # Simple date subtraction
        from datetime import timedelta
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=self.RETENTION_DAYS)
        cutoff_iso = cutoff_dt.isoformat()
        with self._db._write_lock:
            cursor = self._db._conn.execute(
                "DELETE FROM activity_events WHERE timestamp < ?", (cutoff_iso,)
            )
            self._db._conn.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info("ActivityStore: purged %d old event(s)", deleted)
        return deleted


# ---------------------------------------------------------------------------
# ProposalStore -- PM headless proposal queue backed by SQLite
# ---------------------------------------------------------------------------
class ProposalStore:
    """Proposal queue for headless PM tier-2 items awaiting Brad's approval.

    Proposals are specs or decisions that headless PM cannot execute autonomously.
    Brad reviews them on the dashboard Decisions tab and approves or rejects.

    Schema:
    {
        "proposal_id": "uuid",
        "status": "pending|approved|rejected",
        "trigger_event": "task_completed|task_failed|...",
        "trigger_task_id": "optional task that triggered this",
        "proposal_type": "build_spec|respec|decision",
        "title": "short description",
        "content": "full spec or decision text",
        "reasoning": "why headless PM is proposing this",
        "created_at": "ISO8601",
        "reviewed_at": null | "ISO8601",
        "reviewer_feedback": null | "text",
    }
    """

    def __init__(self, db: "TaskDB"):
        self._db = db
        self._lock = threading.Lock()
        self._proposals: dict[str, dict] = {}
        rows = db._conn.execute("SELECT data FROM proposals").fetchall()
        for r in rows:
            p = json.loads(r["data"])
            self._proposals[p["proposal_id"]] = p
        logger.info("ProposalStore: loaded %d proposal(s) from DB", len(self._proposals))

    def create(self, proposal: dict) -> dict:
        """Store a new proposal. Returns the stored proposal."""
        pid = proposal["proposal_id"]
        now = datetime.now(timezone.utc).isoformat()
        proposal.setdefault("status", "pending")
        proposal.setdefault("created_at", now)
        proposal.setdefault("reviewed_at", None)
        proposal.setdefault("reviewer_feedback", None)
        with self._lock:
            self._proposals[pid] = proposal
        with self._db._write_lock:
            self._db._conn.execute(
                "INSERT OR REPLACE INTO proposals (proposal_id, data, updated_at) VALUES (?, ?, ?)",
                (pid, json.dumps(proposal), now),
            )
            self._db._conn.commit()
        return proposal

    def update(self, proposal_id: str, updates: dict) -> dict | None:
        """Update fields on a proposal. Returns updated proposal or None."""
        with self._lock:
            if proposal_id not in self._proposals:
                return None
            self._proposals[proposal_id].update(updates)
            proposal = dict(self._proposals[proposal_id])
        now = datetime.now(timezone.utc).isoformat()
        with self._db._write_lock:
            self._db._conn.execute(
                "INSERT OR REPLACE INTO proposals (proposal_id, data, updated_at) VALUES (?, ?, ?)",
                (proposal_id, json.dumps(proposal), now),
            )
            self._db._conn.commit()
        return proposal

    def get(self, proposal_id: str) -> dict | None:
        with self._lock:
            p = self._proposals.get(proposal_id)
            return dict(p) if p else None

    def list_by_status(self, status: str = "pending", limit: int = 50) -> list[dict]:
        with self._lock:
            results = [
                dict(p) for p in self._proposals.values()
                if p.get("status") == status
            ]
        results.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return results[:limit]

    def list_all(self, limit: int = 50) -> list[dict]:
        with self._lock:
            results = [dict(p) for p in self._proposals.values()]
        results.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return results[:limit]


# ---------------------------------------------------------------------------
# PlanStore -- plan database for spec tracking, metrics, and lineage
# ---------------------------------------------------------------------------
class PlanStore:
    """SQLite-backed plan store for spec lifecycle tracking.

    Plans are created when specs are sent (via leroy_send_spec) and updated
    with outcomes when tasks complete. Supports v1 imports, lineage tracking,
    aggregate reporting, and brain compliance auditing.
    """

    def __init__(self, db: "TaskDB"):
        self._db = db
        self._lock = threading.Lock()
        self._init_plans_schema()
        logger.info("PlanStore initialized")

    def _init_plans_schema(self) -> None:
        with self._db._write_lock:
            self._db._conn.executescript("""
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    spec_text TEXT NOT NULL,
                    typed_ir TEXT,
                    subject TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source TEXT DEFAULT 'v2',
                    complexity_score INTEGER,
                    criteria_count INTEGER,
                    target_machine TEXT,
                    subsystem TEXT,
                    brain_queried BOOLEAN DEFAULT 0,
                    brain_lessons_attached TEXT,
                    brain_persisted BOOLEAN DEFAULT 0,
                    brain_persist_payload TEXT,
                    builder_context_injected BOOLEAN DEFAULT 0,
                    preflight_passed BOOLEAN,
                    preflight_details TEXT,
                    dedup_checked BOOLEAN DEFAULT 0,
                    dedup_similar_task_id TEXT,
                    status TEXT DEFAULT 'draft',
                    pass_rate TEXT,
                    duration_seconds INTEGER,
                    outcome TEXT,
                    failure_categories TEXT,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 2,
                    token_usage_input INTEGER,
                    token_usage_output INTEGER,
                    estimated_cost_usd REAL,
                    quality_score REAL,
                    retro_text TEXT,
                    parent_plan_id TEXT,
                    respec_count INTEGER DEFAULT 0,
                    version INTEGER DEFAULT 1,
                    builder_prompt_version TEXT,
                    builder_prompt_snapshot TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_plans_task_id ON plans(task_id);
                CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status);
                CREATE INDEX IF NOT EXISTS idx_plans_source ON plans(source);
                CREATE INDEX IF NOT EXISTS idx_plans_subsystem ON plans(subsystem);
                CREATE INDEX IF NOT EXISTS idx_plans_created_at ON plans(created_at);
            """)
            self._db._conn.commit()

    def create_plan(self, spec_text: str, subject: str, typed_ir: dict | None = None,
                    complexity_score: int | None = None, criteria_count: int | None = None,
                    target_machine: str | None = None, subsystem: str | None = None,
                    preflight_passed: bool | None = None, preflight_details: str | None = None,
                    dedup_checked: bool = False, dedup_similar_task_id: str | None = None,
                    source: str = "v2", outcome: str | None = None,
                    builder_prompt_version: str | None = None,
                    builder_prompt_snapshot: str | None = None,
                    parent_plan_id: str | None = None,
                    brain_queried: bool = False,
                    brain_lessons_attached: str | None = None) -> str:
        """Create a new plan record. Returns plan_id."""
        import uuid
        plan_id = f"plan-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        with self._db._write_lock:
            self._db._conn.execute(
                """INSERT INTO plans (plan_id, spec_text, subject, typed_ir, created_at,
                   source, complexity_score, criteria_count, target_machine, subsystem,
                   preflight_passed, preflight_details, dedup_checked, dedup_similar_task_id,
                   outcome, builder_prompt_version, builder_prompt_snapshot, parent_plan_id,
                   brain_queried, brain_lessons_attached)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (plan_id, spec_text, subject, json.dumps(typed_ir) if typed_ir else None,
                 now, source, complexity_score, criteria_count, target_machine, subsystem,
                 preflight_passed, preflight_details, dedup_checked, dedup_similar_task_id,
                 outcome, builder_prompt_version, builder_prompt_snapshot, parent_plan_id,
                 brain_queried, brain_lessons_attached),
            )
            self._db._conn.commit()
        return plan_id

    def link_task(self, plan_id: str, task_id: str) -> None:
        """Link a plan to its executing task."""
        with self._db._write_lock:
            self._db._conn.execute(
                "UPDATE plans SET task_id = ?, status = 'sent' WHERE plan_id = ?",
                (task_id, plan_id),
            )
            self._db._conn.commit()

    def update_outcome(self, plan_id: str, status: str | None = None,
                       pass_rate: str | None = None,
                       failure_categories: list[str] | None = None,
                       duration_seconds: int | None = None,
                       token_usage_input: int | None = None,
                       token_usage_output: int | None = None,
                       estimated_cost_usd: float | None = None,
                       retro_text: str | None = None,
                       retry_count: int | None = None,
                       outcome: str | None = None) -> None:
        """Update plan with execution outcome."""
        updates = []
        params = []
        for col, val in [
            ("status", status), ("pass_rate", pass_rate),
            ("failure_categories", json.dumps(failure_categories) if failure_categories else None),
            ("duration_seconds", duration_seconds),
            ("token_usage_input", token_usage_input), ("token_usage_output", token_usage_output),
            ("estimated_cost_usd", estimated_cost_usd), ("retro_text", retro_text),
            ("retry_count", retry_count), ("outcome", outcome),
        ]:
            if val is not None:
                updates.append(f"{col} = ?")
                params.append(val)
        if not updates:
            return
        params.append(plan_id)
        with self._db._write_lock:
            self._db._conn.execute(
                f"UPDATE plans SET {', '.join(updates)} WHERE plan_id = ?",
                params,
            )
            self._db._conn.commit()

    def update_brain_fields(self, plan_id: str,
                            brain_queried: bool | None = None,
                            brain_lessons_attached: str | None = None,
                            brain_persisted: bool | None = None,
                            brain_persist_payload: str | None = None,
                            builder_context_injected: bool | None = None) -> None:
        """v2 Phase 5: Update brain integration fields on a plan record."""
        updates = []
        params = []
        for col, val in [
            ("brain_queried", brain_queried),
            ("brain_lessons_attached", brain_lessons_attached),
            ("brain_persisted", brain_persisted),
            ("brain_persist_payload", brain_persist_payload),
            ("builder_context_injected", builder_context_injected),
        ]:
            if val is not None:
                updates.append(f"{col} = ?")
                params.append(val)
        if not updates:
            return
        params.append(plan_id)
        with self._db._write_lock:
            self._db._conn.execute(
                f"UPDATE plans SET {', '.join(updates)} WHERE plan_id = ?",
                params,
            )
            self._db._conn.commit()

    def get_plan(self, plan_id: str) -> dict | None:
        """Get a single plan by plan_id."""
        row = self._db._conn.execute(
            "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_plan_by_task(self, task_id: str) -> dict | None:
        """Get plan linked to a task_id."""
        row = self._db._conn.execute(
            "SELECT * FROM plans WHERE task_id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_plans(self, status: str | None = None, since_date: str | None = None,
                   limit: int = 50, subsystem: str | None = None,
                   source: str | None = None) -> list[dict]:
        """List plans with optional filters."""
        query = "SELECT * FROM plans WHERE 1=1"
        params: list = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if since_date:
            query += " AND created_at >= ?"
            params.append(since_date)
        if subsystem:
            query += " AND subsystem = ?"
            params.append(subsystem)
        if source:
            query += " AND source = ?"
            params.append(source)
        else:
            # Exclude v1 imports from default queries
            query += " AND source != 'v1_import'"
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_lineage(self, plan_id: str) -> list[dict]:
        """Get parent chain for a plan (oldest first)."""
        chain = []
        current_id = plan_id
        seen = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            plan = self.get_plan(current_id)
            if not plan:
                break
            chain.append(plan)
            current_id = plan.get("parent_plan_id")
        chain.reverse()
        return chain

    def plan_report(self) -> dict:
        """Aggregate stats across all plans, with separate v1/v2 baselines."""
        rows = self._db._conn.execute("SELECT * FROM plans").fetchall()
        plans = [dict(r) for r in rows]
        v2_plans = [p for p in plans if p["source"] == "v2"]
        v1_plans = [p for p in plans if p["source"] == "v1_import"]

        def _stats(subset: list[dict]) -> dict:
            total = len(subset)
            if total == 0:
                return {"total": 0}
            completed = [p for p in subset if p.get("outcome") in ("verified", "completed")]
            failed = [p for p in subset if p.get("status") == "failed"]
            costs = [p["estimated_cost_usd"] for p in subset if p.get("estimated_cost_usd")]
            respec = sum(1 for p in subset if (p.get("respec_count") or 0) > 0)
            timeouts = sum(1 for p in subset if p.get("failure_categories") and "TIMEOUT" in p["failure_categories"])
            brain_queried = sum(1 for p in subset if p.get("brain_queried"))
            brain_persisted = sum(1 for p in subset if p.get("brain_persisted"))
            return {
                "total": total,
                "completed": len(completed),
                "failed": len(failed),
                "total_cost_usd": round(sum(costs), 4) if costs else 0,
                "avg_cost_usd": round(sum(costs) / len(costs), 4) if costs else 0,
                "respec_count": respec,
                "timeout_count": timeouts,
                "brain_queried": brain_queried,
                "brain_persisted": brain_persisted,
            }

        return {
            "v2": _stats(v2_plans),
            "v1_import": _stats(v1_plans),
            "combined": _stats(plans),
        }

    def brain_gaps(self) -> list[dict]:
        """Find plans where brain was not queried or results not persisted."""
        rows = self._db._conn.execute(
            "SELECT plan_id, task_id, subject, created_at, brain_queried, brain_persisted "
            "FROM plans WHERE source = 'v2' AND (brain_queried = 0 OR brain_persisted = 0) "
            "ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def cost_report(self, since_date: str | None = None) -> dict:
        """Token usage and cost breakdown by subsystem and day."""
        query = "SELECT * FROM plans WHERE token_usage_input IS NOT NULL"
        params: list = []
        if since_date:
            query += " AND created_at >= ?"
            params.append(since_date)
        rows = self._db._conn.execute(query, params).fetchall()
        plans = [dict(r) for r in rows]

        by_subsystem: dict[str, dict] = {}
        by_day: dict[str, dict] = {}
        total_cost = 0.0

        for p in plans:
            sub = p.get("subsystem") or "unknown"
            day = (p.get("created_at") or "")[:10]
            cost = p.get("estimated_cost_usd") or 0
            inp = p.get("token_usage_input") or 0
            out = p.get("token_usage_output") or 0
            total_cost += cost

            if sub not in by_subsystem:
                by_subsystem[sub] = {"cost": 0, "input_tokens": 0, "output_tokens": 0, "count": 0}
            by_subsystem[sub]["cost"] += cost
            by_subsystem[sub]["input_tokens"] += inp
            by_subsystem[sub]["output_tokens"] += out
            by_subsystem[sub]["count"] += 1

            if day not in by_day:
                by_day[day] = {"cost": 0, "count": 0}
            by_day[day]["cost"] += cost
            by_day[day]["count"] += 1

        return {
            "total_cost_usd": round(total_cost, 4),
            "by_subsystem": {k: {**v, "cost": round(v["cost"], 4)} for k, v in by_subsystem.items()},
            "by_day": {k: {**v, "cost": round(v["cost"], 4)} for k, v in sorted(by_day.items())},
        }

    def subsystem_health(self) -> dict:
        """Per-subsystem pass rate and respec count."""
        rows = self._db._conn.execute(
            "SELECT * FROM plans WHERE source = 'v2'"
        ).fetchall()
        plans = [dict(r) for r in rows]

        by_sub: dict[str, dict] = {}
        for p in plans:
            sub = p.get("subsystem") or "unknown"
            if sub not in by_sub:
                by_sub[sub] = {"total": 0, "completed": 0, "failed": 0, "respec_count": 0}
            by_sub[sub]["total"] += 1
            if p.get("outcome") in ("verified", "completed"):
                by_sub[sub]["completed"] += 1
            if p.get("status") == "failed":
                by_sub[sub]["failed"] += 1
            by_sub[sub]["respec_count"] += p.get("respec_count") or 0

        for sub, stats in by_sub.items():
            stats["pass_rate"] = round(stats["completed"] / stats["total"], 2) if stats["total"] > 0 else 0

        return by_sub


# ---------------------------------------------------------------------------
# Module-level singletons -- init() must be called before use
# ---------------------------------------------------------------------------
_db: TaskDB | None = None
task_meta: PersistentTaskDict | None = None
subtask_store: PersistentSubtaskStore | None = None
msg_store: PersistentMessageStore | None = None
agent_store: AgentStore | None = None
activity_store: ActivityStore | None = None
proposal_store: ProposalStore | None = None
plan_store: PlanStore | None = None
container_store: "ContainerStore | None" = None


def init(db_path: Path | None = None) -> None:
    """Initialize the module-level singletons. Call once at server startup."""
    global _db, task_meta, subtask_store, msg_store, agent_store, activity_store, proposal_store, plan_store, container_store
    from container_store import ContainerStore
    path = db_path or DB_PATH
    _db = TaskDB(path)
    task_meta = PersistentTaskDict(_db)
    subtask_store = PersistentSubtaskStore(_db)
    msg_store = PersistentMessageStore(_db)
    agent_store = AgentStore(_db)
    activity_store = ActivityStore(_db)
    proposal_store = ProposalStore(_db)
    plan_store = PlanStore(_db)
    container_store = ContainerStore(db_path=path)
    logger.info("task_db initialized (path=%s)", path)
