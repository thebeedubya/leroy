"""SQLite-backed store for content dashboard.

Tables:
  briefs          -- one per day, caches parsed markdown
  angles          -- individual content angles extracted from briefs
  platform_drafts -- per-angle per-platform drafts
  runs            -- pipeline run history from agent-runs.json

WAL mode, thread-safe write lock, same patterns as Leroy task_db.py.
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("content-db")

_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "content.db"
DB_PATH = Path(os.environ.get("CONTENT_DB_PATH", str(_DEFAULT_DB_PATH)))


SCHEMA = """
CREATE TABLE IF NOT EXISTS briefs (
    date        TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    file_mtime  REAL NOT NULL,
    summary     TEXT,
    angles_count INTEGER DEFAULT 0,
    parsed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS angles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_date      TEXT NOT NULL REFERENCES briefs(date),
    angle_index     INTEGER NOT NULL,
    title           TEXT NOT NULL,
    score           INTEGER,
    target_angle    TEXT,
    source_sessions TEXT,
    confidence      TEXT,
    status          TEXT DEFAULT 'draft',
    rejected_reason TEXT,
    approved_at     TEXT,
    rejected_at     TEXT,
    UNIQUE(brief_date, angle_index)
);

CREATE TABLE IF NOT EXISTS platform_drafts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    angle_id        INTEGER NOT NULL REFERENCES angles(id),
    platform        TEXT NOT NULL,
    content         TEXT NOT NULL,
    front_matter    TEXT,
    carousel_slides TEXT,
    posted_url      TEXT,
    posted_at       TEXT,
    UNIQUE(angle_id, platform)
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    target_date     TEXT,
    status          TEXT NOT NULL,
    reason          TEXT,
    duration_seconds REAL,
    angles_found    INTEGER,
    log_file        TEXT
);
"""


class ContentDB:
    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        logger.info("ContentDB initialized at %s", db_path)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Briefs
    # ------------------------------------------------------------------

    def get_brief_mtime(self, date: str) -> float | None:
        row = self._conn.execute(
            "SELECT file_mtime FROM briefs WHERE date = ?", (date,)
        ).fetchone()
        return row["file_mtime"] if row else None

    def upsert_brief(self, date: str, file_path: str, file_mtime: float,
                     summary: str, angles_count: int):
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO briefs (date, file_path, file_mtime, summary, angles_count, parsed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    file_path = excluded.file_path,
                    file_mtime = excluded.file_mtime,
                    summary = excluded.summary,
                    angles_count = excluded.angles_count,
                    parsed_at = excluded.parsed_at
                """,
                (date, file_path, file_mtime, summary, angles_count, self._now()),
            )
            self._conn.commit()

    def get_brief(self, date: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM briefs WHERE date = ?", (date,)
        ).fetchone()
        return dict(row) if row else None

    def list_briefs(self, limit: int = 30, offset: int = 0) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM briefs ORDER BY date DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Angles
    # ------------------------------------------------------------------

    def upsert_angle(self, brief_date: str, angle_index: int, title: str,
                     score: int | None, target_angle: str | None,
                     source_sessions: str | None, confidence: str | None) -> int:
        """Insert or update angle metadata (does NOT touch status/approval fields)."""
        with self._lock:
            existing = self._conn.execute(
                "SELECT id, status, rejected_reason, approved_at, rejected_at "
                "FROM angles WHERE brief_date = ? AND angle_index = ?",
                (brief_date, angle_index),
            ).fetchone()

            if existing:
                self._conn.execute(
                    """
                    UPDATE angles SET
                        title = ?, score = ?, target_angle = ?,
                        source_sessions = ?, confidence = ?
                    WHERE brief_date = ? AND angle_index = ?
                    """,
                    (title, score, target_angle, source_sessions, confidence,
                     brief_date, angle_index),
                )
                self._conn.commit()
                return existing["id"]
            else:
                cur = self._conn.execute(
                    """
                    INSERT INTO angles
                        (brief_date, angle_index, title, score, target_angle,
                         source_sessions, confidence, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')
                    """,
                    (brief_date, angle_index, title, score, target_angle,
                     source_sessions, confidence),
                )
                self._conn.commit()
                return cur.lastrowid

    def get_angles_for_date(self, date: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM angles WHERE brief_date = ? ORDER BY angle_index",
            (date,),
        ).fetchall()
        return [dict(r) for r in rows]

    def approve_angle(self, brief_date: str, angle_index: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE angles SET status = 'approved', approved_at = ?, rejected_at = NULL,
                rejected_reason = NULL
                WHERE brief_date = ? AND angle_index = ?
                """,
                (self._now(), brief_date, angle_index),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def reject_angle(self, brief_date: str, angle_index: int, reason: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE angles SET status = 'rejected', rejected_at = ?,
                rejected_reason = ?, approved_at = NULL
                WHERE brief_date = ? AND angle_index = ?
                """,
                (self._now(), reason, brief_date, angle_index),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Platform drafts
    # ------------------------------------------------------------------

    def upsert_platform_draft(self, angle_id: int, platform: str, content: str,
                              front_matter: str | None = None,
                              carousel_slides: str | None = None):
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO platform_drafts (angle_id, platform, content, front_matter, carousel_slides)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(angle_id, platform) DO UPDATE SET
                    content = excluded.content,
                    front_matter = excluded.front_matter,
                    carousel_slides = excluded.carousel_slides
                """,
                (angle_id, platform, content, front_matter, carousel_slides),
            )
            self._conn.commit()

    def mark_posted(self, angle_id: int, platform: str, url: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE platform_drafts SET posted_url = ?, posted_at = ? "
                "WHERE angle_id = ? AND platform = ?",
                (url, self._now(), angle_id, platform),
            )
            # Also update angle status to posted if not already approved/posted
            self._conn.execute(
                "UPDATE angles SET status = 'posted' WHERE id = ? AND status IN ('draft', 'approved')",
                (angle_id,),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def get_drafts_for_angle(self, angle_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM platform_drafts WHERE angle_id = ? ORDER BY platform",
            (angle_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def sync_runs(self, runs: list[dict]):
        """Sync run history from agent-runs.json. Idempotent."""
        with self._lock:
            for run in runs:
                ts = run.get("timestamp", "")
                target_date = run.get("target_date")
                status = run.get("status", "unknown")
                reason = run.get("reason")
                duration = run.get("duration_seconds") or (
                    _duration(run.get("timestamp"), run.get("completed_at"))
                )
                angles_found = run.get("angles_found")
                log_file = run.get("log_file")
                # Upsert by timestamp
                existing = self._conn.execute(
                    "SELECT id FROM runs WHERE timestamp = ?", (ts,)
                ).fetchone()
                if not existing:
                    self._conn.execute(
                        """
                        INSERT INTO runs (timestamp, target_date, status, reason,
                            duration_seconds, angles_found, log_file)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (ts, target_date, status, reason, duration, angles_found, log_file),
                    )
            self._conn.commit()

    def get_latest_run_for_date(self, date: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE target_date = ? ORDER BY timestamp DESC LIMIT 1",
            (date,),
        ).fetchone()
        return dict(row) if row else None

    def get_all_runs(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY timestamp DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def _duration(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        from datetime import datetime
        fmt = "%Y-%m-%dT%H:%M:%S"
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return (e - s).total_seconds()
    except Exception:
        return None


# Module-level singleton
_db: ContentDB | None = None


def init(db_path: Path = DB_PATH) -> ContentDB:
    global _db
    _db = ContentDB(db_path)
    return _db


def get() -> ContentDB:
    if _db is None:
        raise RuntimeError("ContentDB not initialized. Call content_db.init() first.")
    return _db
