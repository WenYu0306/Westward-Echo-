"""SQLite-backed job store for translation job persistence.

Each translation run is a "job" tracked from creation through progress
updates to completion or failure.  Jobs persist across server restarts.
"""

import sqlite3
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

DB_PATH = str(DATA_DIR / "jobs.db")

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (autocommit via isolation_level=None)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id              TEXT PRIMARY KEY,
    filename            TEXT NOT NULL,
    target_lang         TEXT NOT NULL,
    total_chapters      INTEGER NOT NULL,
    completed_chapters  INTEGER NOT NULL DEFAULT 0,
    current_chapter_title TEXT,
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued','translating','complete','failed')),
    output_path         TEXT,
    glossary_count      INTEGER,
    error_message       TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at        TEXT
);
"""


class JobStore:
    """Persistent store for translation jobs."""

    def __init__(self):
        """Ensure the schema exists on first use."""
        conn = _get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return dict(row)

    # ── public API ───────────────────────────────────────────

    def create_job(
        self,
        filename: str,
        target_lang: str,
        total_chapters: int,
    ) -> str:
        """Create a new job record and return its job_id."""
        job_id = str(uuid.uuid4())[:8]
        conn = _get_conn()
        conn.execute(
            """INSERT INTO jobs (job_id, filename, target_lang, total_chapters, status)
               VALUES (?, ?, ?, ?, 'queued')""",
            (job_id, filename, target_lang, total_chapters),
        )
        conn.commit()
        return job_id

    def update_progress(
        self,
        job_id: str,
        current: int,
        total: int,
        chapter_title: str,
    ):
        """Update progress counters and status to 'translating'."""
        conn = _get_conn()
        conn.execute(
            """UPDATE jobs
               SET completed_chapters = ?,
                   total_chapters = ?,
                   current_chapter_title = ?,
                   status = 'translating'
               WHERE job_id = ?""",
            (current, total, chapter_title, job_id),
        )
        conn.commit()

    def complete_job(
        self,
        job_id: str,
        output_path: str,
        glossary_count: int,
    ):
        """Mark a job as complete."""
        conn = _get_conn()
        conn.execute(
            """UPDATE jobs
               SET status = 'complete',
                   output_path = ?,
                   glossary_count = ?,
                   completed_at = ?
               WHERE job_id = ?""",
            (output_path, glossary_count, self._now(), job_id),
        )
        conn.commit()

    def fail_job(self, job_id: str, error_message: str):
        """Mark a job as failed with an error message."""
        conn = _get_conn()
        conn.execute(
            """UPDATE jobs
               SET status = 'failed',
                   error_message = ?,
                   completed_at = ?
               WHERE job_id = ?""",
            (error_message, self._now(), job_id),
        )
        conn.commit()

    def get_job(self, job_id: str) -> Optional[dict]:
        """Return a single job by id, or None."""
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[dict]:
        """Return recent jobs, newest first."""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete_job(self, job_id: str):
        """Remove a job record."""
        conn = _get_conn()
        conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        conn.commit()

    def get_incomplete_jobs(self) -> list[dict]:
        """Return jobs with status 'translating' that may need recovery."""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'translating' ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]


# Module-level singleton
job_store = JobStore()

# Convenience function for external callers
get_incomplete_jobs = job_store.get_incomplete_jobs
