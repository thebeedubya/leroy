"""Leroy Persistence Manager.

Persists task completion context to Aianna (forge-brain) after every task.
When forge-brain is unreachable, queues payloads to a local file and retries.

Queue file:  ~/Projects/leroy/content/logs/persist-queue.json
Persist log: ~/Projects/leroy/content/logs/persist-log.json

Retry: starting at 60s with exponential backoff up to 30 minutes.
Max queue depth: 100 entries. Alerts via logger if exceeded.
"""

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

import config

logger = logging.getLogger("leroy-persist")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
LOGS_DIR = Path(os.environ.get("LEROY_LOGS_DIR", str(Path(__file__).parent.parent / "content" / "logs")))
QUEUE_FILE = LOGS_DIR / "persist-queue.json"
LOG_FILE = LOGS_DIR / "persist-log.json"

# ---------------------------------------------------------------------------
# Queue limits
# ---------------------------------------------------------------------------
MAX_QUEUE_DEPTH = 100
MAX_PERSIST_ATTEMPTS = 10
RETRY_BASE_INTERVAL = 60       # 1 minute
RETRY_MAX_INTERVAL = 1800      # 30 minutes
RETRY_BACKOFF_FACTOR = 2
DEAD_LETTER_FILE = LOGS_DIR / "persist-deadletter.json"

# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------
CIRCUIT_BREAKER_THRESHOLD = 3   # failures before opening circuit
CIRCUIT_BREAKER_COOLDOWN = 300  # seconds before half-open attempt (5 minutes)


# ---------------------------------------------------------------------------
# Forge-brain MCP client
# ---------------------------------------------------------------------------

async def _async_health_check(url: str, token: str, timeout: float = 5.0) -> bool:
    """Quick HTTP reachability check. Returns True if forge-brain responds.

    Any HTTP response (even 404 or 401) means the server is up.
    Only connection errors indicate the server is down.
    Strategy: GET the base URL (strip /mcp or /sse path).
    """
    base_url = url.rsplit("/mcp", 1)[0] if "/mcp" in url else url.rsplit("/sse", 1)[0] if "/sse" in url else url
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                base_url + "/",
                headers={"Authorization": f"Bearer {token}"},
            )
            # Any HTTP response (200, 401, 404) = server is up
            return True
    except Exception:
        return False


async def _async_persist(content: str, session_title: str, session_tags: list[str], source: str,
                         url: str, token: str, timeout: float = 30.0) -> dict:
    """Call forge-brain persist_on via MCP Streamable HTTP client. Returns result dict."""
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers, timeout=timeout) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "persist_on",
                {
                    "conversation_content": content,
                    "session_title": session_title,
                    "session_tags": session_tags,
                    "source": source,
                },
            )
            # Extract session_id from result content if present
            session_id = None
            if result and result.content:
                for item in result.content:
                    if hasattr(item, "text"):
                        try:
                            data = json.loads(item.text)
                            session_id = data.get("session_id")
                        except Exception:
                            pass
            return {"session_id": session_id, "raw": str(result)[:500]}


def _sync_health_check(url: str, token: str) -> bool:
    """Synchronous wrapper for health check. Safe to call from any thread."""
    try:
        return asyncio.run(_async_health_check(url, token))
    except Exception as e:
        logger.debug("Health check error: %s", e)
        return False


def _sync_persist(content: str, session_title: str, session_tags: list[str], source: str,
                  url: str, token: str) -> dict:
    """Synchronous wrapper for persist call. Safe to call from any thread."""
    return asyncio.run(_async_persist(content, session_title, session_tags, source, url, token))


# ---------------------------------------------------------------------------
# Queue file I/O
# ---------------------------------------------------------------------------

def _load_queue() -> list[dict]:
    """Load queue from disk. Returns empty list if file missing or corrupt."""
    if not QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(QUEUE_FILE.read_text())
        return data.get("entries", [])
    except Exception as e:
        logger.error("Failed to load persist queue from %s: %s", QUEUE_FILE, e)
        return []


def _save_queue(entries: list[dict]) -> None:
    """Atomically write queue to disk."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps({"version": "1.0", "entries": entries}, indent=2))
        tmp.replace(QUEUE_FILE)
    except Exception as e:
        logger.error("Failed to save persist queue: %s", e)


def _load_log() -> list[dict]:
    """Load persist log from disk."""
    if not LOG_FILE.exists():
        return []
    try:
        return json.loads(LOG_FILE.read_text())
    except Exception:
        return []


def _append_log(entry: dict) -> None:
    """Append a log entry to persist-log.json."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log = _load_log()
    log.append(entry)
    # Keep last 1000 entries to prevent unbounded growth
    if len(log) > 1000:
        log = log[-1000:]
    tmp = LOG_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(log, indent=2))
        tmp.replace(LOG_FILE)
    except Exception as e:
        logger.error("Failed to write persist log: %s", e)


# ---------------------------------------------------------------------------
# Dead letter file I/O
# ---------------------------------------------------------------------------

def _load_dead_letter() -> list[dict]:
    """Load dead letter entries from disk. Returns empty list if file missing or corrupt."""
    if not DEAD_LETTER_FILE.exists():
        return []
    try:
        data = json.loads(DEAD_LETTER_FILE.read_text())
        return data.get("entries", [])
    except Exception as e:
        logger.error("Failed to load dead letter file from %s: %s", DEAD_LETTER_FILE, e)
        return []


def _save_dead_letter(entries: list[dict]) -> None:
    """Atomically write dead letter entries to disk."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DEAD_LETTER_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps({"version": "1.0", "entries": entries}, indent=2))
        tmp.replace(DEAD_LETTER_FILE)
    except Exception as e:
        logger.error("Failed to save dead letter file: %s", e)


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

def _build_payload(task_id: str, task_meta: dict, spec_subject: str = "") -> dict:
    """Build a self-contained persist payload from task metadata."""
    status = task_meta.get("status", "unknown")
    result = task_meta.get("result", "") or ""
    spec = task_meta.get("spec", "") or ""
    created_at = task_meta.get("created_at", "")
    completed_at = task_meta.get("completed_at", "")

    # Derive subject from spec subject line or first line of spec
    if not spec_subject:
        first_line = spec.split("\n")[0].strip("#").strip()
        spec_subject = first_line[:100] if first_line else f"Task {task_id}"

    # Calculate duration
    duration_str = "unknown"
    try:
        from datetime import datetime, timezone
        start = datetime.fromisoformat(created_at)
        end = datetime.fromisoformat(completed_at)
        delta = end - start
        duration_str = f"{round(delta.total_seconds())}s"
    except Exception:
        pass

    # Truncate spec and result for the persist content (brain chunks at ~2000 chars)
    spec_preview = spec[:1500] if len(spec) > 1500 else spec
    result_preview = result[:2000] if len(result) > 2000 else result

    content = f"""Leroy completed task {task_id} - {spec_subject} at {completed_at}.

Status: {status}
Duration: {duration_str}
Source: leroy/haze

=== SPEC SUMMARY ===
{spec_preview}

=== RESULT ===
{result_preview}

=== METADATA ===
Task ID: {task_id}
Created: {created_at}
Completed: {completed_at}
Spec length: {len(spec)} chars
Result length: {len(result)} chars
"""

    # Ensure minimum 1500 chars (Aianna's quality standard -- under 1000 is summarizing, not remembering)
    if len(content) < 1500:
        needed = 1500 - len(content)
        padding = f"""

=== SYSTEM CONTEXT ===
This record was generated automatically by the Leroy A2A server (Engineering Lead, FORGE ecosystem).
Leroy receives specs from PM via A2A protocol, executes them via claude -p subprocess with full tool access,
and persists task outcomes to Aianna (forge-brain) for long-term knowledge retention.

Infrastructure: Leroy runs on Haze (Brad's daily driver). forge-brain runs on Kush (192.168.1.100:8300).
Persistence pipeline: task completion -> persist_manager.persist_task() -> MCP Streamable HTTP client -> forge-brain persist_on.
When Kush is under MLX classification load, forge-brain may be temporarily unreachable. In that case,
the persist payload is serialized to ~/Projects/leroy/content/logs/persist-queue.json and retried every
5 minutes by the background retry thread. No engineering context is lost even during brain outages.

Queue file: ~/Projects/leroy/content/logs/persist-queue.json
Persist log: ~/Projects/leroy/content/logs/persist-log.json
Source tag: leroy/haze
Max queue depth: 100 entries (alert threshold)
Retry interval: 60 seconds base, exponential backoff to 30 minutes max.

This persistence record is intended for use by future Leroy sessions, PM review, and Aianna's
long-term knowledge accumulation about FORGE engineering patterns, decisions, and outcomes.
"""
        content += padding

    return {
        "id": uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "attempt_count": 0,
        "last_attempt": None,
        "task_id": task_id,
        "content": content,
        "session_title": f"Leroy Task: {spec_subject}",
        "session_tags": ["leroy", "task-completion", "engineering", status],
        "source": "leroy/haze",
    }


# ---------------------------------------------------------------------------
# Persistence Manager
# ---------------------------------------------------------------------------

class PersistenceManager:
    """Manages task persistence to forge-brain with local queue and retry."""

    def __init__(self):
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._url = config.FORGE_BRAIN_URL
        self._token = config.FORGE_BRAIN_TOKEN
        self._retry_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._consecutive_failures = 0
        # Circuit breaker state
        self._circuit_state = "closed"   # "closed", "open", "half-open"
        self._circuit_failures = 0
        self._circuit_opened_at = 0.0

    def _check_circuit(self) -> tuple[bool, str]:
        """Check circuit breaker state. Must be called with self._lock held.

        Returns (should_attempt, reason).
        """
        if self._circuit_state == "closed":
            return True, "circuit closed"
        if self._circuit_state == "open":
            elapsed = time.time() - self._circuit_opened_at
            remaining = CIRCUIT_BREAKER_COOLDOWN - elapsed
            if remaining <= 0:
                self._circuit_state = "half-open"
                return True, "circuit half-open, attempting probe"
            return False, f"circuit open, {int(remaining)}s until probe"
        # half-open
        return True, "circuit half-open"

    def _record_circuit_result(self, success: bool) -> None:
        """Record a health check or persist result for circuit breaker. Must be called with self._lock held."""
        if success:
            if self._circuit_state == "half-open":
                logger.info("Circuit breaker closed -- brain is back")
                self._circuit_failures = 0
                self._circuit_state = "closed"
            elif self._circuit_state == "closed":
                self._circuit_failures = 0
        else:
            self._circuit_failures += 1
            if self._circuit_state == "half-open":
                self._circuit_state = "open"
                self._circuit_opened_at = time.time()
                logger.warning("Circuit breaker re-opened after probe failure")
            elif self._circuit_state == "closed" and self._circuit_failures >= CIRCUIT_BREAKER_THRESHOLD:
                self._circuit_state = "open"
                self._circuit_opened_at = time.time()
                logger.error(
                    "ALERT: Circuit breaker opened after %d consecutive health check failures. "
                    "Skipping brain for %ds",
                    self._circuit_failures, CIRCUIT_BREAKER_COOLDOWN,
                )

    def start(self) -> None:
        """Start background retry thread. Call once on server startup."""
        # Flush any queued entries from previous run
        self._trigger_flush("startup")

        self._retry_thread = threading.Thread(
            target=self._retry_loop,
            daemon=True,
            name="persist-retry",
        )
        self._retry_thread.start()
        logger.info("PersistenceManager started. Queue file: %s", QUEUE_FILE)

    def stop(self) -> None:
        """Signal retry thread to stop."""
        self._stop_event.set()

    def persist_task(self, task_id: str, task_meta: dict, spec_subject: str = "") -> None:
        """Persist task completion to forge-brain. Non-blocking. Called after task completes."""
        try:
            payload = _build_payload(task_id, task_meta, spec_subject)
        except Exception as e:
            logger.error("Task %s: failed to build persist payload: %s", task_id, e)
            return

        logger.info("Task %s: attempting persist to forge-brain (%d chars)", task_id, len(payload["content"]))

        start_ms = time.monotonic() * 1000

        # Check circuit breaker before attempting health check
        with self._lock:
            should_attempt, circuit_reason = self._check_circuit()

        if not should_attempt:
            logger.info("Task %s: circuit breaker skip (%s), queuing directly", task_id, circuit_reason)
            self._enqueue(payload)
            duration_ms = round(time.monotonic() * 1000 - start_ms)
            _append_log({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id,
                "queue_id": payload["id"],
                "success": False,
                "error": f"circuit breaker: {circuit_reason}",
                "duration_ms": duration_ms,
                "attempt": 1,
                "queued": True,
            })
            return

        # Circuit allows attempt -- try health check
        try:
            alive = _sync_health_check(self._url, self._token)
        except Exception:
            alive = False

        with self._lock:
            self._record_circuit_result(alive)

        if alive:
            success, error = self._attempt_persist(payload)
            with self._lock:
                self._record_circuit_result(success)
        else:
            success = False
            error = "forge-brain unreachable (health check failed)"

        duration_ms = round(time.monotonic() * 1000 - start_ms)

        if success:
            logger.info("Task %s: persisted to forge-brain (%dms)", task_id, duration_ms)
            _append_log({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id,
                "queue_id": payload["id"],
                "success": True,
                "error": None,
                "duration_ms": duration_ms,
                "attempt": 1,
            })
        else:
            logger.warning("Task %s: forge-brain persist failed, queuing. Reason: %s", task_id, error)
            self._enqueue(payload)
            _append_log({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id,
                "queue_id": payload["id"],
                "success": False,
                "error": error,
                "duration_ms": duration_ms,
                "attempt": 1,
                "queued": True,
            })

    def _attempt_persist(self, payload: dict) -> tuple[bool, str | None]:
        """Try to persist one payload entry. Returns (success, error_string)."""
        try:
            _sync_persist(
                content=payload["content"],
                session_title=payload["session_title"],
                session_tags=payload["session_tags"],
                source=payload["source"],
                url=self._url,
                token=self._token,
            )
            return True, None
        except Exception as e:
            return False, str(e)

    def _move_to_dead_letter(self, entry: dict) -> None:
        """Move a failed entry to the dead letter file. Must be called with self._lock held."""
        entry["dead_letter_at"] = datetime.now(timezone.utc).isoformat()
        dl_entries = _load_dead_letter()
        dl_entries.append(entry)
        _save_dead_letter(dl_entries)

    def _enqueue(self, payload: dict) -> None:
        """Add payload to the local queue file."""
        with self._lock:
            entries = _load_queue()
            if len(entries) >= MAX_QUEUE_DEPTH:
                logger.error(
                    "ALERT: Persist queue at max depth (%d). Dropping oldest entry to make room. "
                    "Investigate forge-brain connectivity immediately.",
                    MAX_QUEUE_DEPTH,
                )
                entries = entries[1:]  # Drop oldest
            entries.append(payload)
            _save_queue(entries)
        logger.debug("Queued persist payload %s (queue depth: %d)", payload["id"], len(entries))

    def _flush_queue(self) -> None:
        """Drain the queue, persisting each entry. Stops if brain becomes unavailable."""
        if not self._flush_lock.acquire(blocking=False):
            return
        try:
            self._flush_queue_inner()
        finally:
            self._flush_lock.release()

    def _flush_queue_inner(self) -> None:
        """Inner flush logic. Only called from _flush_queue() which holds _flush_lock."""
        with self._lock:
            entries = _load_queue()

        if not entries:
            return

        logger.info("Flushing persist queue (%d entries)", len(entries))

        # Check circuit breaker before health check
        with self._lock:
            should_attempt, circuit_reason = self._check_circuit()

        if not should_attempt:
            logger.debug("Flush skipped: %s", circuit_reason)
            return

        # Health check first
        try:
            alive = _sync_health_check(self._url, self._token)
        except Exception:
            alive = False

        with self._lock:
            self._record_circuit_result(alive)

        if not alive:
            logger.debug("Flush skipped: forge-brain unreachable")
            return

        remaining = []
        flushed = 0
        for entry in entries:
            success, error = self._attempt_persist(entry)
            if success:
                flushed += 1
                _append_log({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "task_id": entry.get("task_id"),
                    "queue_id": entry.get("id"),
                    "success": True,
                    "error": None,
                    "duration_ms": None,
                    "attempt": entry.get("attempt_count", 0) + 1,
                    "flushed_from_queue": True,
                })
            else:
                entry["attempt_count"] = entry.get("attempt_count", 0) + 1
                entry["last_attempt"] = datetime.now(timezone.utc).isoformat()
                _append_log({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "task_id": entry.get("task_id"),
                    "queue_id": entry.get("id"),
                    "success": False,
                    "error": error,
                    "duration_ms": None,
                    "attempt": entry.get("attempt_count", 0),
                    "flushed_from_queue": True,
                })
                if entry["attempt_count"] >= MAX_PERSIST_ATTEMPTS:
                    logger.error(
                        "ALERT: Entry %s for task %s moved to dead letter after %d attempts",
                        entry.get("id"), entry.get("task_id"), MAX_PERSIST_ATTEMPTS,
                    )
                    self._move_to_dead_letter(entry)
                else:
                    remaining.append(entry)
                # Brain went down mid-flush -- stop trying remaining entries
                break

        with self._lock:
            _save_queue(remaining)

        if flushed:
            logger.info("Flushed %d entries from persist queue. %d remaining.", flushed, len(remaining))

    def flush_if_ready(self) -> None:
        """Non-blocking flush trigger for task pickup events.

        Checks circuit breaker and queue depth before spawning a daemon
        thread to drain the queue. Safe to call from any context.
        """
        with self._lock:
            should_attempt, _ = self._check_circuit()
            if not should_attempt:
                return
            queue_len = len(_load_queue())

        if queue_len == 0:
            return

        logger.info("Task pickup trigger: attempting queue flush (%d entries)", queue_len)
        threading.Thread(target=self._flush_queue, daemon=True).start()

    def _trigger_flush(self, reason: str = "manual") -> None:
        """Trigger a queue flush. Called on startup or when triggering a retry."""
        logger.debug("Persist queue flush triggered (%s)", reason)
        try:
            self._flush_queue()
        except Exception as e:
            logger.error("Flush failed: %s", e)

    def _retry_loop(self) -> None:
        """Background thread: flush queue with exponential backoff."""
        logger.info("Persist retry loop started (base interval: %ds, max: %ds)",
                     RETRY_BASE_INTERVAL, RETRY_MAX_INTERVAL)
        while True:
            interval = min(
                RETRY_BASE_INTERVAL * (RETRY_BACKOFF_FACTOR ** self._consecutive_failures),
                RETRY_MAX_INTERVAL,
            )
            if self._stop_event.wait(timeout=interval):
                break
            try:
                entries_before = _load_queue()
                if entries_before:
                    logger.info("Retry loop: %d queued entries, attempting flush", len(entries_before))
                    self._flush_queue()
                    entries_after = _load_queue()
                    if len(entries_after) < len(entries_before):
                        # Progress was made
                        self._consecutive_failures = 0
                    else:
                        self._consecutive_failures += 1
                else:
                    # Nothing in queue, reset backoff
                    self._consecutive_failures = 0
            except Exception as e:
                logger.error("Retry loop error: %s", e)
                self._consecutive_failures += 1

            if self._consecutive_failures > 0:
                next_interval = min(
                    RETRY_BASE_INTERVAL * (RETRY_BACKOFF_FACTOR ** self._consecutive_failures),
                    RETRY_MAX_INTERVAL,
                )
                logger.info("Retry backoff: next attempt in %ds (consecutive failures: %d)",
                            next_interval, self._consecutive_failures)
        logger.info("Persist retry loop stopped")

    def queue_depth(self) -> int:
        """Return current queue depth (for health endpoint)."""
        try:
            return len(_load_queue())
        except Exception:
            return -1

    def dead_letter_depth(self) -> int:
        """Return current dead letter queue depth (for health endpoint)."""
        try:
            return len(_load_dead_letter())
        except Exception:
            return -1

    def recent_log(self, n: int = 10) -> list[dict]:
        """Return last N log entries (for health endpoint)."""
        try:
            log = _load_log()
            return log[-n:]
        except Exception:
            return []

    def reset_circuit(self) -> dict:
        """Force-reset circuit breaker to closed. Returns previous state. Triggers queue flush."""
        with self._lock:
            prev = self._circuit_state
            prev_failures = self._circuit_failures
            self._circuit_state = "closed"
            self._circuit_failures = 0
            self._circuit_opened_at = 0.0
            logger.info("Circuit breaker force-reset: %s (failures=%d) -> closed", prev, prev_failures)
        self._trigger_flush("circuit-reset")
        return {"previous_state": prev, "previous_failures": prev_failures, "new_state": "closed"}

    @property
    def circuit_state(self) -> dict:
        """Return circuit breaker status for health/status reporting."""
        with self._lock:
            state = {
                "state": self._circuit_state,
                "failures": self._circuit_failures,
                "threshold": CIRCUIT_BREAKER_THRESHOLD,
                "cooldown_seconds": CIRCUIT_BREAKER_COOLDOWN,
            }
            if self._circuit_state == "open":
                elapsed = time.time() - self._circuit_opened_at
                state["open_for_seconds"] = round(elapsed)
                state["probe_in_seconds"] = max(0, round(CIRCUIT_BREAKER_COOLDOWN - elapsed))
            return state
