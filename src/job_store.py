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
    project_id          TEXT,
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

CREATE INDEX IF NOT EXISTS idx_jobs_project_id ON jobs(project_id);

CREATE TABLE IF NOT EXISTS rejected_terms (
    term_cn     TEXT NOT NULL,
    rejected_en TEXT NOT NULL,
    target_lang TEXT NOT NULL DEFAULT 'en-US',
    rejected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (term_cn, rejected_en, target_lang)
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
        project_id: Optional[str] = None,
    ) -> str:
        """Create a new job record and return its job_id."""
        job_id = str(uuid.uuid4())[:8]
        conn = _get_conn()
        conn.execute(
            """INSERT INTO jobs (job_id, project_id, filename, target_lang, total_chapters, status)
               VALUES (?, ?, ?, ?, ?, 'queued')""",
            (job_id, project_id, filename, target_lang, total_chapters),
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

    # ── project / multi-language ────────────────────────────

    def create_project(self, source_filename: str) -> str:
        """Create a project grouping for multi-language translations.

        Returns a project_id that all language variants share.
        """
        return str(uuid.uuid4())[:8]

    def add_language_job(self, project_id: str, target_lang: str,
                        filename: str, total_chapters: int) -> str:
        """Create a job within a project for a specific target language."""
        return self.create_job(filename, target_lang, total_chapters, project_id=project_id)

    def get_project_jobs(self, project_id: str) -> list[dict]:
        """Return all jobs (language variants) belonging to a project."""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM jobs WHERE project_id = ? ORDER BY target_lang",
            (project_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_projects(self, limit: int = 20) -> list[dict]:
        """Return recent projects with their language-variant jobs grouped.

        Each entry: {project_id, filename, created_at, jobs: [{lang, job_id, status, ...}]}
        """
        conn = _get_conn()
        # Get distinct projects, ordered by most recent job creation
        rows = conn.execute(
            """SELECT project_id, filename, MAX(created_at) AS created_at
               FROM jobs
               WHERE project_id IS NOT NULL
               GROUP BY project_id
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

        projects = []
        for row in rows:
            pid = row["project_id"]
            job_rows = conn.execute(
                "SELECT * FROM jobs WHERE project_id = ? ORDER BY target_lang",
                (pid,),
            ).fetchall()
            projects.append({
                "project_id": pid,
                "filename": row["filename"],
                "created_at": row["created_at"],
                "jobs": [self._row_to_dict(j) for j in job_rows],
            })
        return projects

    # ── rejected terms ──────────────────────────────────────

    def reject_term_with_feedback(self, term_cn: str, rejected_en: str,
                                  target_lang: str = "en-US"):
        """Record a rejected translation so the Agent can avoid it.

        Called by the /review reject endpoint when a human reviewer
        rejects a glossary term.  The Agent reads this table before
        each translation so it never re-proposes the same bad term.
        """
        conn = _get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO rejected_terms
               (term_cn, rejected_en, target_lang, rejected_at)
               VALUES (?, ?, ?, ?)""",
            (term_cn, rejected_en, target_lang, self._now()),
        )
        conn.commit()

    def get_rejected_terms(self, target_lang: str = "en-US") -> list[dict]:
        """Return all rejected translations for injection into prompts."""
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT term_cn, rejected_en, rejected_at, target_lang
               FROM rejected_terms
               WHERE target_lang = ?
               ORDER BY rejected_at DESC""",
            (target_lang,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_confirmed_terms(self, target_lang: str = "en-US") -> dict:
        """Return only confirmed terms (higher authority than auto-extracted).

        These are terms a human reviewer has explicitly approved.
        They should NEVER be overwritten by the Agent and MUST be
        used as-is in translations.
        """
        from .config import CHECKPOINT_DB_PATH
        conn = sqlite3.connect(CHECKPOINT_DB_PATH)
        try:
            rows = conn.execute(
                """SELECT term_cn, term_en
                   FROM exact_glossary
                   WHERE status = 'confirmed' AND target_lang = ?""",
                (target_lang,),
            ).fetchall()
            return {row[0]: row[1] for row in rows}
        finally:
            conn.close()


# Module-level singleton
job_store = JobStore()

# Convenience function for external callers
get_incomplete_jobs = job_store.get_incomplete_jobs
