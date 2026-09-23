"""SQLite-backed job store for translation job persistence.

Each translation run is a "job" tracked from creation through progress
updates to completion or failure.  Jobs persist across server restarts.
"""

import sqlite3
import threading
import uuid
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
    return conn  # type: ignore[no-any-return]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id              TEXT PRIMARY KEY,
    project_id          TEXT,
    filename            TEXT NOT NULL,
    target_lang         TEXT NOT NULL,
    content_type        TEXT NOT NULL DEFAULT 'novel'
                        CHECK(content_type IN ('novel')),
    total_chapters      INTEGER NOT NULL,
    completed_chapters  INTEGER NOT NULL DEFAULT 0,
    current_chapter_title TEXT,
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued','translating','complete','failed')),
    output_path         TEXT,
    glossary_count      INTEGER,
    tokens_input        INTEGER NOT NULL DEFAULT 0,
    tokens_output       INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS glossary_presets (
    preset_name         TEXT PRIMARY KEY,
    description         TEXT,
    created_from_job_id TEXT,
    glossary_json       TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class JobStore:
    """Persistent store for translation jobs."""

    def __init__(self):
        """Ensure the schema exists on first use."""
        conn = _get_conn()
        conn.executescript(_SCHEMA)
        # ── Migrations for existing databases ──
        self._migrate_add_column(conn, "jobs", "tokens_input", "INTEGER NOT NULL DEFAULT 0")
        self._migrate_add_column(conn, "jobs", "tokens_output", "INTEGER NOT NULL DEFAULT 0")
        self._migrate_add_column(conn, "jobs", "content_type", "TEXT NOT NULL DEFAULT 'novel'")
        conn.commit()

    @staticmethod
    def _migrate_add_column(conn, table: str, column: str, col_type: str):
        """Add a column if it doesn't already exist (safe no-op on existing)."""
        existing = [col[1] for col in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

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
        content_type: str = "novel",
        project_id: Optional[str] = None,
    ) -> str:
        """Create a new job record and return its job_id."""
        job_id = str(uuid.uuid4())[:8]
        conn = _get_conn()
        conn.execute(
            """INSERT INTO jobs (job_id, project_id, filename, target_lang, content_type, \
total_chapters, status)
               VALUES (?, ?, ?, ?, ?, ?, 'queued')""",
            (job_id, project_id, filename, target_lang, content_type, total_chapters),
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

    # ── token / cost accounting ──────────────────────────────────────
    #
    # update_token_usage() and get_job_cost() were deleted here. update_token_usage
    # had ZERO callers in the entire codebase, so the jobs table's tokens_input /
    # tokens_output columns were permanently 0; get_job_cost then read those
    # always-zero columns and priced them at DeepSeek's $0.14/$0.28 per M — a
    # provider abandoned in 2026-09. The result was an endpoint that always
    # reported "$0.0000" for every job.
    #
    # The columns are left in the schema (dropping them needs a migration, and
    # _row_to_dict returns dict(row) so their presence is harmless). They are
    # inert. Authoritative per-job usage and cost come from
    # src/token_ledger.py: token_ledger.summary(book_id), which records the real
    # model per call and prices it in CNY from config.MODEL_PRICES_CNY_PER_M.
    # GET /api/jobs/{job_id}/cost already reads that, not these columns.

    # ── glossary presets (translation memory across books) ──

    def save_glossary_as_preset(
        self, job_id: str, preset_name: str, description: str = "", glossary_json: str = ""
    ):
        """Save a completed job's glossary as a reusable preset.

        If glossary_json is omitted, the preset is created empty — callers
        should populate it after creating the preset.
        """
        conn = _get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO glossary_presets
               (preset_name, description, created_from_job_id, glossary_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (preset_name, description, job_id, glossary_json, self._now()),
        )
        conn.commit()

    def load_glossary_preset(self, preset_name: str) -> dict:
        """Load a preset glossary. Returns {term_cn: term_en} or empty dict."""
        conn = _get_conn()
        row = conn.execute(
            "SELECT glossary_json FROM glossary_presets WHERE preset_name = ?",
            (preset_name,),
        ).fetchone()
        if not row:
            return {}
        try:
            import json as _json
            return _json.loads(row["glossary_json"]) or {}
        except Exception:
            return {}

    def load_glossary_preset_raw(self, preset_name: str) -> Optional[str]:
        """Return the raw JSON string of a preset, or None if not found."""
        conn = _get_conn()
        row = conn.execute(
            "SELECT glossary_json FROM glossary_presets WHERE preset_name = ?",
            (preset_name,),
        ).fetchone()
        return row["glossary_json"] if row else None  # type: ignore[no-any-return]

    def list_glossary_presets(self) -> list[dict]:
        """List all available presets."""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT preset_name, description, created_from_job_id, created_at "
            "FROM glossary_presets ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete_glossary_preset(self, preset_name: str):
        """Remove a glossary preset."""
        conn = _get_conn()
        conn.execute("DELETE FROM glossary_presets WHERE preset_name = ?", (preset_name,))
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
                        filename: str, total_chapters: int,
                        content_type: str = "novel") -> str:
        """Create a job within a project for a specific target language."""
        return self.create_job(filename, target_lang, total_chapters,
                              content_type=content_type, project_id=project_id)

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

        Returns {} when the glossary table does not exist yet. That table is
        created by glossary/exact_store.py, not by this module, so it is absent
        on a fresh database until the first ExactGlossary is constructed — and
        write_node calls this before that is guaranteed. "No human-confirmed
        terms yet" is a legitimate state, not an error: the old code raised
        sqlite3.OperationalError there, which propagated out of the LangGraph
        task and failed the chapter.
        """
        from .config import CHECKPOINT_DB_PATH
        conn = sqlite3.connect(CHECKPOINT_DB_PATH)
        try:
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='exact_glossary'"
            ).fetchone()
            if not exists:
                return {}
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
