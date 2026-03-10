"""ContainerStore -- SQLite-backed persistence for Dispatcher containers.

A Container is NOT a task. It is the top-level dispatch unit that wraps one or more
Vehicle tasks. Phase 1 of the Dispatcher implementation (IC-1, IC-9, IC-13).

Tables:
  containers -- one row per dispatched spec, with JSON-packed vehicle/dependency fields.

Thread-safety: single threading.Lock guards all reads and writes.
Recovery: recovery_sweep_containers() runs at startup to detect stale vehicles.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger("leroy-containers")


# ---------------------------------------------------------------------------
# ContainerStatus enum
# ---------------------------------------------------------------------------

class ContainerStatus(str, Enum):
    DISPATCHING = "dispatching"
    IN_FLIGHT = "in_flight"
    RECONVERGING = "reconverging"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_DECISION = "needs_decision"


# Active statuses used for recovery sweep and get_active_containers()
_ACTIVE_STATUSES = (
    ContainerStatus.DISPATCHING,
    ContainerStatus.IN_FLIGHT,
    ContainerStatus.RECONVERGING,
)

# Vehicle states considered "terminal done"
_DONE_STATES = {"COMPLETED_UNVERIFIED", "COMPLETED_VERIFIED", "PERSISTED", "ARCHIVED"}

# Vehicle states considered "failed / needs attention"
_FAILED_STATES = {"FAILED_RETRYABLE", "ESCALATED", "BLOCKED"}

# Vehicle states still in progress (not yet running, not failed)
_PENDING_STATES = {"NEW", "ANALYZED", "PLANNED"}

# Stale RUNNING threshold in seconds (IC-13)
_STALE_RUNNING_THRESHOLD = 600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO timestamp string to a timezone-aware datetime."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        # Fallback: try replacing space with T
        return datetime.fromisoformat(ts.replace(" ", "T"))


# ---------------------------------------------------------------------------
# ContainerStore
# ---------------------------------------------------------------------------

class ContainerStore:
    """SQLite-backed store for Dispatcher containers.

    Uses its own SQLite connection to data/tasks.db (WAL mode, same file as TaskDB).
    All public methods are thread-safe via a threading.Lock.
    """

    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()
        logger.info("ContainerStore initialized at %s", db_path)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS containers (
                    container_id TEXT PRIMARY KEY,
                    spec_text TEXT NOT NULL,
                    typed_ir_json TEXT,
                    master_plan_id TEXT,
                    status TEXT NOT NULL DEFAULT 'dispatching',
                    priority INTEGER NOT NULL DEFAULT 1,
                    vehicle_ids_json TEXT NOT NULL DEFAULT '[]',
                    dependency_graph_json TEXT NOT NULL DEFAULT '{}',
                    pending_vehicle_ids_json TEXT NOT NULL DEFAULT '[]',
                    retry_counts_json TEXT NOT NULL DEFAULT '{}',
                    failure_record_json TEXT,
                    reconvergence_record_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_containers_status ON containers(status);
            """)
            self._conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_dict(self, row) -> dict:
        """Convert a sqlite3.Row to a plain dict with JSON fields parsed."""
        d = dict(row)
        for field in (
            "vehicle_ids_json",
            "dependency_graph_json",
            "pending_vehicle_ids_json",
            "retry_counts_json",
            "failure_record_json",
            "reconvergence_record_json",
            "typed_ir_json",
        ):
            if d.get(field) is not None:
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass  # leave raw string if unparseable
        return d

    def _serialize_field(self, value) -> str | None:
        """Serialize a value to JSON string for storage, or None if value is None."""
        if value is None:
            return None
        if isinstance(value, str):
            return value  # already serialized
        return json.dumps(value)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_container(
        self,
        container_id: str,
        spec_text: str,
        typed_ir: dict | None = None,
        priority: int = 1,
    ) -> str:
        """Insert a new container row. Returns container_id."""
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO containers (
                    container_id, spec_text, typed_ir_json, status, priority,
                    vehicle_ids_json, dependency_graph_json,
                    pending_vehicle_ids_json, retry_counts_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    container_id,
                    spec_text,
                    json.dumps(typed_ir) if typed_ir is not None else None,
                    ContainerStatus.DISPATCHING.value,
                    priority,
                    "[]",   # vehicle_ids_json
                    "{}",   # dependency_graph_json
                    "[]",   # pending_vehicle_ids_json
                    "{}",   # retry_counts_json
                    now,
                    now,
                ),
            )
            self._conn.commit()
        logger.info("ContainerStore: created container %s (priority=%d)", container_id, priority)
        return container_id

    def get_container(self, container_id: str) -> dict | None:
        """Return full container row as dict with JSON fields parsed, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM containers WHERE container_id = ?",
                (container_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def update_container(self, container_id: str, **fields) -> None:
        """Update specified fields on a container. Auto-sets updated_at."""
        if not fields:
            return
        fields["updated_at"] = _now_iso()

        # JSON-serialize any list/dict values
        set_clauses = []
        values = []
        for key, val in fields.items():
            set_clauses.append(f"{key} = ?")
            if isinstance(val, (dict, list)):
                values.append(json.dumps(val))
            elif isinstance(val, Enum):
                values.append(val.value)
            else:
                values.append(val)
        values.append(container_id)

        sql = f"UPDATE containers SET {', '.join(set_clauses)} WHERE container_id = ?"
        with self._lock:
            self._conn.execute(sql, values)
            self._conn.commit()

    def set_status(self, container_id: str, status: ContainerStatus) -> None:
        """Convenience wrapper to update just the status field."""
        self.update_container(container_id, status=status.value)
        logger.info("ContainerStore: container %s -> %s", container_id, status.value)

    # ------------------------------------------------------------------
    # Vehicle management
    # ------------------------------------------------------------------

    def add_vehicle(self, container_id: str, task_id: str) -> None:
        """Append task_id to the container's vehicle_ids_json list."""
        with self._lock:
            row = self._conn.execute(
                "SELECT vehicle_ids_json FROM containers WHERE container_id = ?",
                (container_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Container {container_id} not found")
            vehicle_ids = json.loads(row["vehicle_ids_json"] or "[]")
            if task_id not in vehicle_ids:
                vehicle_ids.append(task_id)
            now = _now_iso()
            self._conn.execute(
                "UPDATE containers SET vehicle_ids_json = ?, updated_at = ? WHERE container_id = ?",
                (json.dumps(vehicle_ids), now, container_id),
            )
            self._conn.commit()
        logger.debug("ContainerStore: added vehicle %s to container %s", task_id, container_id)

    def get_vehicles(self, container_id: str) -> list[str]:
        """Return list of vehicle task_ids for a container."""
        with self._lock:
            row = self._conn.execute(
                "SELECT vehicle_ids_json FROM containers WHERE container_id = ?",
                (container_id,),
            ).fetchone()
        if row is None:
            return []
        return json.loads(row["vehicle_ids_json"] or "[]")

    def get_container_for_vehicle(self, task_id: str) -> dict | None:
        """Find and return the parent container for a vehicle task_id.

        Scans vehicle_ids_json for all active (and recently completed) containers.
        Returns the full parsed container dict, or None if not found.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM containers",
            ).fetchall()
        for row in rows:
            d = self._row_to_dict(row)
            vehicle_ids = d.get("vehicle_ids_json", [])
            if isinstance(vehicle_ids, list) and task_id in vehicle_ids:
                return d
        return None

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_active_containers(self) -> list[dict]:
        """Return containers with status in (dispatching, in_flight, reconverging)."""
        status_values = tuple(s.value for s in _ACTIVE_STATUSES)
        placeholders = ",".join("?" * len(status_values))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM containers WHERE status IN ({placeholders})",
                status_values,
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Retry tracking
    # ------------------------------------------------------------------

    def increment_retry(self, container_id: str, vehicle_task_id: str) -> int:
        """Increment per-vehicle retry count and return new value."""
        with self._lock:
            row = self._conn.execute(
                "SELECT retry_counts_json FROM containers WHERE container_id = ?",
                (container_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Container {container_id} not found")
            retry_counts = json.loads(row["retry_counts_json"] or "{}")
            retry_counts[vehicle_task_id] = retry_counts.get(vehicle_task_id, 0) + 1
            new_count = retry_counts[vehicle_task_id]
            now = _now_iso()
            self._conn.execute(
                "UPDATE containers SET retry_counts_json = ?, updated_at = ? WHERE container_id = ?",
                (json.dumps(retry_counts), now, container_id),
            )
            self._conn.commit()
        logger.info(
            "ContainerStore: retry count for vehicle %s in container %s -> %d",
            vehicle_task_id, container_id, new_count,
        )
        return new_count

    # ------------------------------------------------------------------
    # Recovery sweep (IC-9, IC-13)
    # ------------------------------------------------------------------

    def recovery_sweep_containers(self, task_meta_getter, state_machine_ref) -> None:
        """Level-triggered recovery sweep. Run once at server startup.

        Implements IC-9 steps 1-3, 5, 8 from the Dispatcher design doc.

        Args:
            task_meta_getter: callable(task_id) -> dict | None
                Returns task metadata for a given task_id (e.g. _task_meta.get).
            state_machine_ref: TaskStateMachine instance for calling transition().
                May be None in test environments; transitions are skipped if None.
        """
        active = self.get_active_containers()
        if not active:
            logger.info("Recovery sweep: no active containers")
            return

        logger.info("Recovery sweep: found %d active container(s)", len(active))
        now = datetime.now(timezone.utc)

        for container in active:
            cid = container["container_id"]
            vehicle_ids = container.get("vehicle_ids_json") or []
            if isinstance(vehicle_ids, str):
                try:
                    vehicle_ids = json.loads(vehicle_ids)
                except json.JSONDecodeError:
                    vehicle_ids = []

            all_done = True
            any_failed = False

            for vid in vehicle_ids:
                meta = task_meta_getter(vid)
                if not meta:
                    logger.warning(
                        "Recovery: vehicle %s not found in task store (container %s)", vid, cid
                    )
                    continue

                v2_state = meta.get("v2_state", "")
                last_activity = meta.get("last_activity") or meta.get("created_at")

                # IC-13: stale RUNNING -> FAILED_RETRYABLE
                if v2_state == "RUNNING":
                    if last_activity:
                        try:
                            elapsed = (now - _parse_iso(last_activity)).total_seconds()
                        except Exception:
                            elapsed = 0
                    else:
                        elapsed = 0

                    if elapsed > _STALE_RUNNING_THRESHOLD:
                        logger.warning(
                            "Recovery: vehicle %s stale RUNNING (%.0fs) in container %s,"
                            " marking FAILED_RETRYABLE",
                            vid, elapsed, cid,
                        )
                        if state_machine_ref is not None:
                            try:
                                state_machine_ref.transition(
                                    vid, "FAILED_RETRYABLE", "recovery_sweep_stale"
                                )
                            except Exception as e:
                                logger.warning(
                                    "Recovery: transition failed for vehicle %s: %s", vid, e
                                )
                        any_failed = True
                        continue
                    else:
                        # Still running and not stale
                        all_done = False
                        continue

                # Step 5: terminal done states
                if v2_state in _DONE_STATES:
                    continue  # counts as done, no action

                # Step 4 placeholder: failed/blocked vehicles
                if v2_state in _FAILED_STATES:
                    any_failed = True
                    logger.info(
                        "Recovery: vehicle %s in state %s (container %s),"
                        " needs dispatcher action (Phase 3)",
                        vid, v2_state, cid,
                    )
                    continue

                # Pending / not yet running states
                if v2_state in _PENDING_STATES or v2_state == "":
                    all_done = False

            # Log container outcome summary
            if all_done and not any_failed:
                logger.info(
                    "Recovery: container %s all vehicles complete,"
                    " reconvergence needed (Phase 3)",
                    cid,
                )
            elif any_failed:
                logger.info(
                    "Recovery: container %s has failed vehicles,"
                    " needs decision (Phase 3)",
                    cid,
                )
            else:
                logger.info(
                    "Recovery: container %s still in flight, monitoring resumed", cid
                )

            # Step 8: per-container lock reconstruction is handled by ContainerStore's
            # own threading.Lock. Per-vehicle locks will be managed by dispatcher (Phase 3).

        logger.info("Recovery sweep: complete")
