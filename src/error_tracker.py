"""Error tracking & analytics — lightweight SQLite-backed event store.

Records quality-related events (guard warnings, parse fallbacks, circuit
breaker trips, empty outputs) so the team can see where problems cluster
and iterate on prompts / guard rules / model selection.
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from .config import DATA_DIR

DB_PATH = str(DATA_DIR / "translation_events.db")

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
CREATE TABLE IF NOT EXISTS translation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    chapter_number INTEGER,
    event_type TEXT,
    detail TEXT,
    target_lang TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_type ON translation_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_job ON translation_events(job_id);
"""

# Ensure schema exists at import time
_conn = _get_conn()
_conn.executescript(_SCHEMA)
_conn.commit()


# ── Public API ───────────────────────────────────────────────────


def record_event(
    job_id: Optional[str],
    chapter_number: Optional[int],
    event_type: str,
    detail: str,
    target_lang: str = "en-US",
) -> None:
    """Persist a single quality event to the event store.

    Args:
        job_id: Translation job identifier (may be None for system events).
        chapter_number: Chapter where the event occurred (may be None).
        event_type: Category — 'guard_warning', 'parse_fallback',
                    'circuit_breaker', 'empty_output', 'chatter_detected',
                    'json_residue', 'qa_low_score'.
        detail: Human-readable description (the actual warning / error message).
        target_lang: Target language code, default ``"en-US"``.
    """
    conn = _get_conn()
    conn.execute(
        """INSERT INTO translation_events
           (job_id, chapter_number, event_type, detail, target_lang)
           VALUES (?, ?, ?, ?, ?)""",
        (job_id, chapter_number, event_type, detail, target_lang),
    )
    conn.commit()


def get_event_summary(days: int = 7) -> dict:
    """Return ``{event_type: count}`` for the last N days, plus total events.

    Example:
        >>> get_event_summary(7)
        {'guard_warning': 12, 'parse_fallback': 3, 'total': 15}
    """
    conn = _get_conn()
    rows = conn.execute(
        """SELECT event_type, COUNT(*) AS cnt
           FROM translation_events
           WHERE created_at >= datetime('now', ?)
           GROUP BY event_type
           ORDER BY cnt DESC""",
        (f"-{days} days",),
    ).fetchall()

    summary = {row["event_type"]: row["cnt"] for row in rows}
    summary["total"] = sum(summary.values())
    return summary


def get_recent_issues(limit: int = 20) -> list[dict]:
    """Return the most recent events, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT id, job_id, chapter_number, event_type, detail, target_lang, created_at
           FROM translation_events
           ORDER BY created_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_job_health(job_id: str) -> dict:
    """Per-job health: total chapters processed, chapters with warnings, warning rate.

    Returns:
        {'job_id': str, 'total_chapters': int, 'chapters_with_warnings': int,
         'warning_rate_pct': float, 'total_warnings': int}
    """
    conn = _get_conn()

    # Count distinct chapters that have events for this job
    row = conn.execute(
        """SELECT COUNT(DISTINCT chapter_number) AS chapters_with_warnings
           FROM translation_events
           WHERE job_id = ? AND chapter_number IS NOT NULL""",
        (job_id,),
    ).fetchone()
    chapters_with_warnings = row["chapters_with_warnings"] if row else 0

    total_warnings = conn.execute(
        "SELECT COUNT(*) AS cnt FROM translation_events WHERE job_id = ?",
        (job_id,),
    ).fetchone()["cnt"]

    # Total chapters: try to get from jobs table, fall back to distinct chapters
    try:
        from .job_store import DB_PATH as JOBS_DB
        jconn = sqlite3.connect(JOBS_DB)
        jconn.row_factory = sqlite3.Row
        jrow = jconn.execute(
            "SELECT total_chapters FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        total_chapters = jrow["total_chapters"] if jrow else chapters_with_warnings
        jconn.close()
    except Exception:
        total_chapters = chapters_with_warnings

    warning_rate = (
        (chapters_with_warnings / total_chapters * 100)
        if total_chapters > 0
        else 0.0
    )

    return {
        "job_id": job_id,
        "total_chapters": total_chapters,
        "chapters_with_warnings": chapters_with_warnings,
        "warning_rate_pct": round(warning_rate, 1),
        "total_warnings": total_warnings,
    }


def get_editor_stats() -> dict:
    """Return editor-usage stats from editor_edits.db and checkpoints.db.

    Returns:
        {'edited_chapters': int, 'jobs_edited': int,
         'glossary_confirmed': int, 'glossary_rejected': int,
         'batch_replace_count': int}
    """
    from .config import CHECKPOINT_DB_PATH

    result = {
        "edited_chapters": 0,
        "jobs_edited": 0,
        "glossary_confirmed": 0,
        "glossary_rejected": 0,
        "batch_replace_count": 0,
    }

    # ── Editor edits ──
    from .api.editor import EDITOR_DB_PATH, _get_conn as _editor_conn
    try:
        econn = _editor_conn()
        # Count all chapters that have edits across all per-job tables
        tables = econn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'edits_%'"
        ).fetchall()
        total_edits = 0
        for (tname,) in tables:
            cnt = econn.execute(
                f'SELECT COUNT(DISTINCT chapter_num) FROM "{tname}"'
            ).fetchone()[0]
            total_edits += cnt
        result["edited_chapters"] = total_edits
    except Exception:
        pass

    # Count jobs that have been edited
    try:
        from .api.editor import _get_conn as _editor_conn_2
        econn2 = _editor_conn_2()
        row = econn2.execute(
            "SELECT COUNT(*) AS cnt FROM edit_meta"
        ).fetchone()
        if row:
            result["jobs_edited"] = row["cnt"]
    except Exception:
        pass

    # ── Glossary status counts ──
    try:
        gconn = sqlite3.connect(CHECKPOINT_DB_PATH)
        gconn.row_factory = sqlite3.Row
        confirmed = gconn.execute(
            "SELECT COUNT(*) AS cnt FROM exact_glossary WHERE status = 'confirmed'"
        ).fetchone()
        result["glossary_confirmed"] = confirmed["cnt"] if confirmed else 0

        rejected = gconn.execute(
            "SELECT COUNT(*) AS cnt FROM exact_glossary WHERE status = 'rejected'"
        ).fetchone()
        result["glossary_rejected"] = rejected["cnt"] if rejected else 0
        gconn.close()
    except Exception:
        pass

    # ── Batch replace count (not tracked separately, estimate from tables) ──
    try:
        from .api.editor import _get_conn as _editor_conn_3
        econn3 = _editor_conn_3()
        tables = econn3.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'edits_%'"
        ).fetchall()
        total_replaces = 0
        for (tname,) in tables:
            cnt = econn3.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
            total_replaces += cnt
        result["batch_replace_count"] = total_replaces
    except Exception:
        pass

    return result


def get_all_jobs_health() -> list[dict]:
    """Return health summary for all jobs that have events recorded.

    Sorted by warning rate descending (worst first).
    """
    conn = _get_conn()
    rows = conn.execute(
        """SELECT DISTINCT job_id
           FROM translation_events
           WHERE job_id IS NOT NULL"""
    ).fetchall()

    results = []
    for (job_id,) in rows:
        results.append(get_job_health(job_id))

    results.sort(key=lambda x: x["warning_rate_pct"], reverse=True)
    return results
