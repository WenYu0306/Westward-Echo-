"""Exact-match glossary layer.

O(1) lookup via Python dict, persisted to SQLite for crash recovery.
This layer guarantees that character names, place names, and proper nouns
are translated identically across all chapters — vector search alone cannot
provide this guarantee.
"""

import json
import os
import sqlite3
import typing
from pathlib import Path

from ..config import CHECKPOINT_DB_PATH


class ExactGlossary:
    """
    Order-1 exact-match glossary.

    - In-memory dict for O(1) lookup during translation
    - SQLite-backed persistence so crash recovery keeps the glossary intact
    - String-contains matching: if a term_cn appears anywhere in the source
      text, it is injected into the LLM prompt as a mandatory constraint.
    """

    def __init__(self, db_path: typing.Optional[str] = None, book_id: str = "default"):
        self._dict: dict[str, str] = {}          # {term_cn: term_en}
        self._db_path = db_path or CHECKPOINT_DB_PATH
        self._book_id = book_id
        self._init_db()

    # ------------------------------------------------------------------
    # SQLite persistence
    # ------------------------------------------------------------------

    def _init_db(self):
        os.makedirs(Path(self._db_path).parent, exist_ok=True)
        with sqlite3.connect(self._db_path, timeout=30) as conn:
            # WAL allows concurrent readers/writers across worker processes.
            # Setting it is NOT idempotent: multiple processes initialising
            # the same fresh DB at once can race on this pragma and raise
            # "database is locked". Retry-safe: if it's already WAL, or the
            # DB is momentarily locked, fall through — WAL is persistent
            # once set, so a later connection inherits it.
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass  # already WAL (persistent) or transient lock — retry later
            # ── Migration: the pre-book_id table used term_cn as PRIMARY KEY.
            #    Rename it out of the way (it holds undifferentiated test data
            #    from before books were isolated). New schema uses a composite
            #    key (book_id, term_cn, target_lang).
            existing = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='exact_glossary'"
            ).fetchone()
            if existing:
                cols = [c[1] for c in conn.execute("PRAGMA table_info(exact_glossary)").fetchall()]
                if "book_id" not in cols:
                    conn.execute("ALTER TABLE exact_glossary RENAME TO exact_glossary_legacy")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exact_glossary (
                    book_id TEXT NOT NULL DEFAULT 'default',
                    term_cn TEXT NOT NULL,
                    term_en TEXT NOT NULL,
                    category TEXT DEFAULT 'culture',
                    context TEXT DEFAULT '',
                    chapter_first_seen INTEGER DEFAULT 0,
                    note TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending_review',
                    target_lang TEXT DEFAULT 'en-US',
                    PRIMARY KEY (book_id, term_cn, target_lang)
                )
            """)
            conn.commit()

    def load_from_db(self, target_lang: str = "en-US"):
        """Restore in-memory dict from SQLite on startup / resume."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT term_cn, term_en FROM exact_glossary "
                "WHERE book_id = ? AND target_lang = ?",
                (self._book_id, target_lang),
            ).fetchall()
        self._dict = {row[0]: row[1] for row in rows}

    def _persist_term(self, term_cn: str, term_en: str, category: str = "culture",
                      context: str = "", chapter: int = 0, note: str = "",
                      status: str = "pending_review", target_lang: str = "en-US"):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO exact_glossary
                   (book_id, term_cn, term_en, category, context, chapter_first_seen, note, status, \
target_lang)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self._book_id, term_cn, term_en, category, context, chapter, note, status,
                 target_lang),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, term_cn: str, term_en: str, category: str = "culture",
            context: str = "", chapter: int = 0, note: str = "",
            target_lang: str = "en-US"):
        """Add a term to the exact layer (memory + SQLite)."""
        self._dict[term_cn] = term_en
        self._persist_term(term_cn, term_en, category, context, chapter, note,
                           "pending_review", target_lang)

    def add_batch(self, terms: list[dict], chapter: int = 0, target_lang: str = "en-US"):
        """Bulk-add terms extracted from a chapter."""
        for t in terms:
            self.add(
                term_cn=t["term_cn"],
                term_en=t["term_en"],
                category=t.get("category", "culture"),
                context=t.get("context", ""),
                chapter=chapter,
                note=t.get("note", ""),
                target_lang=target_lang,
            )

    def match_in_text(self, text: str) -> dict[str, str]:
        """
        Scan source text for all known terms via string-contains.
        Returns {term_cn: term_en} for every term found in the text.

        Complexity: O(G * L) where G = glossary size, L = average term length.
        For a glossary of ~500 terms this takes < 1ms on typical hardware.
        """
        matched = {}
        for cn, en in self._dict.items():
            if cn in text:
                matched[cn] = en
        return matched

    def get(self, term_cn: str) -> typing.Optional[str]:
        return self._dict.get(term_cn)

    def get_term_info(self, term_cn: str, target_lang: str = "en-US") -> typing.Optional[dict]:
        """Return full metadata for a term (category, context, chapter, status, etc.)."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT term_cn, term_en, category, context, chapter_first_seen,
                          note, status, target_lang
                   FROM exact_glossary
                   WHERE book_id = ? AND term_cn = ? AND target_lang = ?""",
                (self._book_id, term_cn, target_lang),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_status(self, term_cn: str, target_lang: str = "en-US") -> typing.Optional[str]:
        """Return the status of a term ('confirmed', 'pending_review', or None if absent)."""
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT status FROM exact_glossary "
                "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                (self._book_id, term_cn, target_lang),
            ).fetchone()
        if row is None:
            return None
        return row[0]  # type: ignore[no-any-return]

    def find_chapters_with_term(self, term_cn: str, target_lang: str = "en-US") -> list[int]:
        """
        Return the list of chapter numbers where this term appears.
        This is a simplified version — in production this would query a
        chapter-term mapping table. For now it returns the chapter_first_seen.

        Returns a list so the caller can format it for display.
        """
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT chapter_first_seen FROM exact_glossary "
                "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                (self._book_id, term_cn, target_lang),
            ).fetchone()
        if row is None:
            return []
        return [row[0]] if row[0] else []

    def to_dict(self) -> dict[str, str]:
        return dict(self._dict)

    def to_formatted_text(self, matched_terms: typing.Optional[dict[str, str]] = None) -> str:
        """
        Format glossary as a markdown table for prompt injection.
        If matched_terms is provided, only those terms are listed.
        """
        terms = matched_terms or self._dict
        if not terms:
            return "(No glossary terms matched for this chapter.)"

        lines = ["| Chinese | English |", "|----------|---------|"]
        for cn, en in sorted(terms.items(), key=lambda x: len(x[0]), reverse=True):
            # Sort by length descending so longer terms appear first in prompt
            lines.append(f"| {cn} | {en} |")
        return "\n".join(lines)

    def to_formatted_text_with_notes(
        self, matched_terms: typing.Optional[dict[str, str]] = None,
        target_lang: str = "en-US",
    ) -> str:
        """Format glossary as a contextualized list with cultural notes.

        Each entry includes the translation AND the accumulated cultural
        understanding from the chapter where it was first established.
        This gives future WRITE agents not just the WHAT but the WHY.

        If matched_terms is provided, only those terms are listed.

        Confusable pairs (terms sharing a Chinese prefix or an English first
        word, e.g. 苏沐橙/苏沐秋 or Wei Cao/Wei Chen) get an explicit
        "do not confuse" warning appended — the LLM otherwise misattributes
        one character's name to the other when both appear in a chapter.
        """
        terms = matched_terms or self._dict
        if not terms:
            return "(No glossary terms matched for this chapter.)"

        # Fetch notes from the DB for each matched term
        notes_map: dict[str, str] = {}
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            for cn in terms:
                row = conn.execute(
                    "SELECT note FROM exact_glossary "
                    "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                    (self._book_id, cn, target_lang),
                ).fetchone()
                if row and row["note"]:
                    notes_map[cn] = row["note"]

        lines = [
            "| Chinese | English | Context |",
            "|----------|---------|---------|",
        ]
        for cn, en in sorted(terms.items(), key=lambda x: len(x[0]), reverse=True):
            note = notes_map.get(cn, "")
            if note:
                lines.append(f"| {cn} | {en} | {note} |")
            else:
                lines.append(f"| {cn} | {en} | (see prior chapters) |")

        # ── Confusable-pair warnings ──
        confusable = self._detect_confusable_pairs(terms)
        if confusable:
            lines.append("")
            lines.append("**DO NOT CONFUSE — these are DIFFERENT entities.** "
                         "Match each name to the exact source character:")
            for cn1, en1, cn2, en2 in confusable:
                lines.append(f"- {cn1} = {en1}  ≠  {cn2} = {en2}")

        return "\n".join(lines)

    @staticmethod
    def _detect_confusable_pairs(terms: dict[str, str]) -> list[tuple[str, str, str, str]]:
        """Find term pairs the LLM is likely to confuse.

        Two heuristics, kept conservative:
          1. Shared 2-char Chinese prefix (苏沐橙/苏沐秋 → "苏沐",
             百花战队/百花缭乱 → "百花")
          2. Same English first word, excluding stopwords
             (微草战队 "Wei Cao" / 魏琛 "Wei Chen" → "Wei")

        Returns [(cn1, en1, cn2, en2), ...] sorted by Chinese.
        """
        _STOPWORDS = frozenset({"the", "a", "an"})
        items = sorted(terms.items())
        pairs: list[tuple[str, str, str, str]] = []
        for i in range(len(items)):
            cn1, en1 = items[i]
            for j in range(i + 1, len(items)):
                cn2, en2 = items[j]
                # Shared 2-char Chinese prefix
                if len(cn1) >= 2 and len(cn2) >= 2 and cn1[:2] == cn2[:2]:
                    pairs.append((cn1, en1, cn2, en2))
                    continue
                # Same English first word (excluding stopwords)
                w1 = en1.split()[0].lower() if en1.split() else ""
                w2 = en2.split()[0].lower() if en2.split() else ""
                if w1 and w1 == w2 and w1 not in _STOPWORDS:
                    pairs.append((cn1, en1, cn2, en2))
        return pairs

    def snapshot(self) -> str:
        """JSON snapshot for checkpoint persistence."""
        return json.dumps(self._dict, ensure_ascii=False)

    def restore_snapshot(self, snapshot: str):
        """Restore from a JSON snapshot string."""
        self._dict = json.loads(snapshot)

    # ------------------------------------------------------------------
    # Review API — human-in-the-loop glossary curation
    # ------------------------------------------------------------------

    def get_all_terms(self, status_filter: typing.Optional[str] = None,
                      target_lang: str = "en-US") -> list[dict]:
        """List all terms, optionally filtered by status."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            if status_filter:
                rows = conn.execute(
                    """SELECT term_cn, term_en, category, context, chapter_first_seen,
                              note, status, target_lang
                       FROM exact_glossary
                       WHERE book_id = ? AND status = ? AND target_lang = ?
                       ORDER BY term_cn""",
                    (self._book_id, status_filter, target_lang),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT term_cn, term_en, category, context, chapter_first_seen,
                              note, status, target_lang
                       FROM exact_glossary
                       WHERE book_id = ? AND target_lang = ?
                       ORDER BY status, term_cn""",
                    (self._book_id, target_lang),
                ).fetchall()
        return [dict(row) for row in rows]

    def confirm_term(self, term_cn: str, target_lang: str = "en-US"):
        """Set a term's status to 'confirmed'."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE exact_glossary SET status = 'confirmed' "
                "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                (self._book_id, term_cn, target_lang),
            )
            conn.commit()

    def reject_term(self, term_cn: str, target_lang: str = "en-US"):
        """Delete a term from the exact_store entirely."""
        self._dict.pop(term_cn, None)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM exact_glossary "
                "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                (self._book_id, term_cn, target_lang),
            )
            conn.commit()

    def stats(self) -> dict:
        return {"total_terms": len(self._dict)}

    def __len__(self) -> int:
        return len(self._dict)

    def __contains__(self, term_cn: str) -> bool:
        return term_cn in self._dict
