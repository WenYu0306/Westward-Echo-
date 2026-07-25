"""Editor API — paragraph-level CRUD for post-translation human-in-the-loop editing.

Stores edits in per-job SQLite tables so they survive restarts. The CN originals
are read from the same OUTPUT_DIR and chapter-splitter pipeline used for translation.
"""

import re
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..config import OUTPUT_DIR, DATA_DIR
from ..chapter_splitter import split_chapters
from ..job_store import job_store

app = FastAPI(title="Westward Echo Editor API", version="0.15.0")

# ── SQLite for edits (per-worker thread-local connection) ────────────────

EDITOR_DB_PATH = str(DATA_DIR / "editor_edits.db")
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        Path(EDITOR_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(EDITOR_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


def _ensure_table(job_id: str):
    """Create the per-job edits table if it doesn't exist."""
    # Sanitize job_id for use as table name suffix
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", job_id)
    table = f"edits_{safe}"
    conn = _get_conn()
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{table}" (
            chapter_num INTEGER NOT NULL,
            paragraph_index INTEGER NOT NULL,
            edited_text TEXT NOT NULL,
            edited_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (chapter_num, paragraph_index)
        )
    """)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS edit_meta (
            job_id TEXT PRIMARY KEY,
            last_edited_at TEXT
        )
    """)
    conn.commit()
    return table


# ── Helpers ───────────────────────────────────────────────────────────────


def _load_cn_paragraphs(text: str) -> list[str]:
    """Split a chapter's Chinese text into non-empty paragraphs."""
    paras = text.strip().split("\n")
    return [p.strip() for p in paras if p.strip()]


def _load_en_paragraphs(text: str) -> list[str]:
    """Split a chapter's English text into non-empty paragraphs."""
    # English Markdown may use double-newline for paragraph breaks
    # but for alignment we want single-line paragraphs
    paras = text.strip().split("\n")
    return [p.strip() for p in paras if p.strip()]


def _get_en_chapter_text(job_id: str, chapter_num: int) -> Optional[str]:
    """Read a single chapter's English translation from the Markdown output."""
    for lang in ["en-US", "es-ES", "ar-SA"]:
        path = OUTPUT_DIR / f"{job_id}_full_novel_{lang}.md"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            # Parse out the specific chapter
            pattern = re.compile(
                rf"^#\s+Chapter\s+{chapter_num}:?\s*.*$", re.IGNORECASE | re.MULTILINE
            )
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            start = matches[0].end()
            # Find the next chapter header after this one
            next_pattern = re.compile(
                rf"^#\s+Chapter\s+{chapter_num + 1}:?\s*",
                re.IGNORECASE | re.MULTILINE,
            )
            next_match = next_pattern.search(text, start)
            end = next_match.start() if next_match else len(text)
            return text[start:end].strip()
        return None
    return None


def _get_cn_chapter(job_id: str, chapter_num: int, total_chapters: int) -> Optional[str]:
    """Read the Chinese original for a specific chapter.

    Strategy: try to load from the same source used during translation.
    The job's original text may be stored or we fall back to re-splitting
    from a cached file.
    """
    # First, try stored original in output dir
    src_path = OUTPUT_DIR / f"{job_id}_source.txt"
    if src_path.exists():
        text = src_path.read_text(encoding="utf-8")
        chapters = split_chapters(text)
        for ch in chapters:
            if ch.index == chapter_num:
                return ch.content
    return None


def _split_into_paragraph_pairs(cn_text: str, en_text: str) -> tuple[list[str], list[str]]:
    """Split CN and EN text into aligned paragraph arrays.

    Paragraphs are split on newlines. Both sides may have different counts
    (translations can be longer/shorter). We present them as-is.
    """
    cn_paras = [p.strip() for p in cn_text.strip().split("\n") if p.strip()]
    en_paras = [p.strip() for p in en_text.strip().split("\n") if p.strip()]
    return cn_paras, en_paras


# ── Endpoints ─────────────────────────────────────────────────────────────


@app.get("/editor/{job_id}/chapters")
def list_chapters(job_id: str) -> list[dict]:
    """Return all chapter numbers and titles for a completed job."""
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    if job["status"] != "complete":
        return JSONResponse(
            status_code=400,
            content={"error": "Editing is only available for completed translations"},
        )

    total = job["total_chapters"]
    chapters = []
    for i in range(1, total + 1):
        chapter_title = f"Chapter {i}"
        # Try to extract title from translation if available
        en_text = _get_en_chapter_text(job_id, i)
        if en_text:
            first_line = en_text.split("\n")[0].strip()
            if first_line.startswith("#"):
                chapter_title = first_line.lstrip("#").strip()
        chapters.append({"chapter_num": i, "title": chapter_title})

    return chapters


@app.get("/editor/{job_id}/chapters/{chapter_num}")
def get_chapter(job_id: str, chapter_num: int) -> dict:
    """Return {chapter_num, title, cn_paragraphs, en_paragraphs, edits}.

    Loads the Chinese original and English translation, splits both into
    paragraphs. Also returns any saved edits for this chapter.
    """
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    total = job["total_chapters"]
    if chapter_num < 1 or chapter_num > total:
        return JSONResponse(status_code=404, content={"error": "Chapter out of range"})

    # Get Chinese original
    cn_text = _get_cn_chapter(job_id, chapter_num, total)
    cn_paragraphs: list[str] = []
    if cn_text:
        cn_paragraphs = _load_cn_paragraphs(cn_text)

    # Get English translation
    en_text = _get_en_chapter_text(job_id, chapter_num)
    en_paragraphs: list[str] = []
    chapter_title = f"Chapter {chapter_num}"
    if en_text:
        en_paragraphs = _load_en_paragraphs(en_text)
        # Extract title from first line if it's a header
        first = en_paragraphs[0] if en_paragraphs else ""
        if first.startswith("#"):
            chapter_title = first.lstrip("#").strip()
            en_paragraphs = en_paragraphs[1:]  # Remove title from paragraph list

    # Get any saved edits
    table = _ensure_table(job_id)
    conn = _get_conn()
    rows = conn.execute(
        f'SELECT paragraph_index, edited_text FROM "{table}" WHERE chapter_num = ? ORDER BY paragraph_index',
        (chapter_num,),
    ).fetchall()
    edits = {row["paragraph_index"]: row["edited_text"] for row in rows}

    # Merge edits into the en_paragraphs
    display_paragraphs = []
    for idx, para in enumerate(en_paragraphs):
        display_paragraphs.append(edits.get(idx, para))

    return {
        "chapter_num": chapter_num,
        "title": chapter_title,
        "cn_paragraphs": cn_paragraphs,
        "en_paragraphs": display_paragraphs,
        "edits": {str(k): v for k, v in edits.items()},
    }


@app.put("/editor/{job_id}/chapters/{chapter_num}")
def update_chapter(job_id: str, chapter_num: int, data: dict) -> dict:
    """Save edited English paragraphs.

    Data: {paragraphs: [{index, text}, ...]}

    Persists edits to a per-job SQLite table so they survive restarts.
    """
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    paragraphs = data.get("paragraphs", [])
    if not paragraphs:
        return {"status": "no_changes", "saved_count": 0}

    table = _ensure_table(job_id)
    conn = _get_conn()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    saved = 0

    for item in paragraphs:
        idx = item.get("index", -1)
        text = item.get("text", "")
        if idx < 0:
            continue
        conn.execute(
            f'INSERT OR REPLACE INTO "{table}" (chapter_num, paragraph_index, edited_text, edited_at) '
            "VALUES (?, ?, ?, ?)",
            (chapter_num, idx, text, now),
        )
        saved += 1

    # Update last-edited timestamp
    conn.execute(
        "INSERT OR REPLACE INTO edit_meta (job_id, last_edited_at) VALUES (?, ?)",
        (job_id, now),
    )
    conn.commit()

    return {"status": "saved", "saved_count": saved, "chapter_num": chapter_num}


@app.post("/editor/{job_id}/batch-replace")
def batch_replace(job_id: str, data: dict) -> dict:
    """Apply a term replacement across all chapters.

    Data: {term_en_old, term_en_new}
    Returns the count of replacements made.
    """
    term_old = data.get("term_en_old", "")
    term_new = data.get("term_en_new", "")

    if not term_old:
        return JSONResponse(status_code=400, content={"error": "term_en_old is required"})

    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    table = _ensure_table(job_id)
    conn = _get_conn()
    total_replaced = 0

    # We need to apply replacements to the source EN text + saved edits
    total = job["total_chapters"]
    for chapter_num in range(1, total + 1):
        en_text = _get_en_chapter_text(job_id, chapter_num)
        if not en_text:
            continue

        en_paragraphs = _load_en_paragraphs(en_text)
        # Remove title if present
        if en_paragraphs and en_paragraphs[0].startswith("#"):
            en_paragraphs = en_paragraphs[1:]

        for idx, para in enumerate(en_paragraphs):
            # Check if there's a saved edit first
            row = conn.execute(
                f'SELECT edited_text FROM "{table}" WHERE chapter_num = ? AND paragraph_index = ?',
                (chapter_num, idx),
            ).fetchone()

            current_text = row["edited_text"] if row else para
            if term_old in current_text:
                new_text = current_text.replace(term_old, term_new)
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    f'INSERT OR REPLACE INTO "{table}" (chapter_num, paragraph_index, edited_text, edited_at) '
                    "VALUES (?, ?, ?, ?)",
                    (chapter_num, idx, new_text, now),
                )
                total_replaced += 1

    if total_replaced > 0:
        conn.execute(
            "INSERT OR REPLACE INTO edit_meta (job_id, last_edited_at) VALUES (?, ?)",
            (job_id, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
        )
    conn.commit()

    return {"status": "ok", "replacements": total_replaced, "term_old": term_old, "term_new": term_new}


@app.get("/editor/{job_id}/stats")
def get_stats(job_id: str) -> dict:
    """Return {total_chapters, edited_chapters, last_edited_at}."""
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    total = job["total_chapters"]
    table = _ensure_table(job_id)
    conn = _get_conn()

    # Count distinct chapters with edits
    row = conn.execute(
        f'SELECT COUNT(DISTINCT chapter_num) as cnt FROM "{table}"'
    ).fetchone()
    edited_count = row["cnt"] if row else 0

    meta_row = conn.execute(
        "SELECT last_edited_at FROM edit_meta WHERE job_id = ?", (job_id,)
    ).fetchone()
    last_edited = meta_row["last_edited_at"] if meta_row else None

    return {
        "total_chapters": total,
        "edited_chapters": edited_count,
        "last_edited_at": last_edited,
    }
