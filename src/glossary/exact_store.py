"""Exact-match glossary layer.

O(1) lookup via Python dict, persisted to SQLite for crash recovery.
This layer guarantees that character names, place names, and proper nouns
are translated identically across all chapters — vector search alone cannot
provide this guarantee.
"""

import json
import sqlite3
import os
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

    def __init__(self, db_path: typing.Optional[str] = None):
        self._dict: dict[str, str] = {}          # {term_cn: term_en}
        self._db_path = db_path or CHECKPOINT_DB_PATH
        self._init_db()

    # ------------------------------------------------------------------
    # SQLite persistence
    # ------------------------------------------------------------------

    def _init_db(self):
        os.makedirs(Path(self._db_path).parent, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exact_glossary (
                    term_cn TEXT PRIMARY KEY,
                    term_en TEXT NOT NULL,
                    category TEXT DEFAULT 'culture',
                    context TEXT DEFAULT '',
                    chapter_first_seen INTEGER DEFAULT 0,
                    note TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending_review',
                    target_lang TEXT DEFAULT 'en-US'
                )
            """)
            conn.commit()

    def load_from_db(self, target_lang: str = "en-US"):
        """Restore in-memory dict from SQLite on startup / resume."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT term_cn, term_en FROM exact_glossary WHERE target_lang = ?",
                (target_lang,),
            ).fetchall()
        self._dict = {row[0]: row[1] for row in rows}

    def _persist_term(self, term_cn: str, term_en: str, category: str = "culture",
                      context: str = "", chapter: int = 0, note: str = "",
                      status: str = "pending_review", target_lang: str = "en-US"):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO exact_glossary
                   (term_cn, term_en, category, context, chapter_first_seen, note, status, target_lang)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (term_cn, term_en, category, context, chapter, note, status, target_lang),
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
                       WHERE status = ? AND target_lang = ?
                       ORDER BY term_cn""",
                    (status_filter, target_lang),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT term_cn, term_en, category, context, chapter_first_seen,
                              note, status, target_lang
                       FROM exact_glossary
                       WHERE target_lang = ?
                       ORDER BY status, term_cn""",
                    (target_lang,),
                ).fetchall()
        return [dict(row) for row in rows]

    def confirm_term(self, term_cn: str, target_lang: str = "en-US"):
        """Set a term's status to 'confirmed'."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE exact_glossary SET status = 'confirmed' WHERE term_cn = ? AND target_lang = ?",
                (term_cn, target_lang),
            )
            conn.commit()

    def reject_term(self, term_cn: str, target_lang: str = "en-US"):
        """Delete a term from the exact_store entirely."""
        self._dict.pop(term_cn, None)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM exact_glossary WHERE term_cn = ? AND target_lang = ?",
                (term_cn, target_lang),
            )
            conn.commit()

    def stats(self) -> dict:
        return {"total_terms": len(self._dict)}

    def __len__(self) -> int:
        return len(self._dict)

    def __contains__(self, term_cn: str) -> bool:
        return term_cn in self._dict
