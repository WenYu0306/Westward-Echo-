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

from ..config import CHECKPOINT_DB_PATH, target_language_label


class ExactGlossary:
    """
    Order-1 exact-match glossary.

    - In-memory dict for O(1) lookup during translation
    - SQLite-backed persistence so crash recovery keeps the glossary intact
    - String-contains matching: if a term_cn appears anywhere in the source
      text, it is injected into the LLM prompt as a mandatory constraint.
    """

    def __init__(self, db_path: typing.Optional[str] = None, book_id: str = "default",
                 target_lang: str = "en-US"):
        # Terms are bucketed per target language: {target_lang: {term_cn: term_en}}.
        #
        # This used to be a single flat ``_dict``, which could only ever hold one
        # language at a time even though the SQLite primary key has carried
        # ``target_lang`` since the composite-key migration. Every reader then had
        # to guess which language the instance happened to be holding, and the
        # review API could only cope by keeping one store instance per
        # ``(book_id, target_lang)`` — a workaround, not a fix.
        self._by_lang: dict[str, dict[str, str]] = {}
        self._active_lang = target_lang
        self._db_path = db_path or CHECKPOINT_DB_PATH
        self._book_id = book_id
        self._init_db()

    # ------------------------------------------------------------------
    # Language scoping
    # ------------------------------------------------------------------

    @property
    def active_lang(self) -> str:
        """The language used by calls that do not name one explicitly."""
        return self._active_lang

    def set_active_lang(self, target_lang: str):
        """Point the instance at another already-loaded language.

        Switching languages must not require a reload or a second instance: that
        is what forced the review API to cache one store per namespace.
        """
        self._active_lang = target_lang

    def loaded_languages(self) -> list[str]:
        """Languages currently held in memory, sorted for deterministic output."""
        return sorted(self._by_lang)

    def _bucket(self, target_lang: typing.Optional[str] = None) -> dict[str, str]:
        """Read-only view of one language's terms.

        Deliberately does *not* create the bucket: a read against an unloaded
        language must return empty rather than silently materialise a namespace
        that then shows up in ``loaded_languages()`` and ``snapshot()``.
        """
        return self._by_lang.get(target_lang or self._active_lang, {})

    def _writable_bucket(self, target_lang: typing.Optional[str] = None) -> dict[str, str]:
        lang = target_lang or self._active_lang
        return self._by_lang.setdefault(lang, {})

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

    def load_from_db(self, target_lang: typing.Optional[str] = None):
        """Restore one language's terms from SQLite into memory.

        Loading a language makes it active, so callers that follow the historical
        ``load_from_db()`` → ``match_in_text()`` pattern keep working unchanged.
        Terms already held for *other* languages are left alone: this is a
        per-namespace load, not a reset of the whole instance.
        """
        lang = target_lang or self._active_lang
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT term_cn, term_en FROM exact_glossary "
                "WHERE book_id = ? AND target_lang = ?",
                (self._book_id, lang),
            ).fetchall()
        # Replace this language's bucket wholesale (a term deleted from the DB
        # must not survive in memory), but keep the other languages' buckets.
        self._by_lang[lang] = {row[0]: row[1] for row in rows}
        self._active_lang = lang

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
            target_lang: typing.Optional[str] = None):
        """Add a term to the exact layer (memory + SQLite).

        ``target_lang=None`` means the active language. The term lands in that
        language's bucket *and* in the DB row for the same language, so memory
        and persistence cannot disagree about which namespace was written — the
        disagreement that made ``confirm_term`` return success while updating
        zero rows.
        """
        lang = target_lang or self._active_lang
        self._writable_bucket(lang)[term_cn] = term_en
        self._persist_term(term_cn, term_en, category, context, chapter, note,
                           "pending_review", lang)

    def add_batch(self, terms: list[dict], chapter: int = 0,
                  target_lang: typing.Optional[str] = None):
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

    def match_in_text(self, text: str,
                      target_lang: typing.Optional[str] = None) -> dict[str, str]:
        """
        Scan source text for all known terms via string-contains.
        Returns {term_cn: term_en} for every term found in the text.

        Scans one language's bucket, so a store holding both en-US and es-ES
        terms cannot leak Spanish renderings into an English prompt.

        Complexity: O(G * L) where G = glossary size, L = average term length.
        For a glossary of ~500 terms this takes < 1ms on typical hardware.
        """
        matched = {}
        for cn, en in self._bucket(target_lang).items():
            if cn in text:
                matched[cn] = en
        return matched

    def get(self, term_cn: str, target_lang: typing.Optional[str] = None
            ) -> typing.Optional[str]:
        return self._bucket(target_lang).get(term_cn)

    def get_term_info(self, term_cn: str, target_lang: typing.Optional[str] = None
                    ) -> typing.Optional[dict]:
        """Return full metadata for a term (category, context, chapter, status, etc.)."""
        lang = target_lang or self._active_lang
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT term_cn, term_en, category, context, chapter_first_seen,
                          note, status, target_lang
                   FROM exact_glossary
                   WHERE book_id = ? AND term_cn = ? AND target_lang = ?""",
                (self._book_id, term_cn, lang),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_status(self, term_cn: str, target_lang: typing.Optional[str] = None
                   ) -> typing.Optional[str]:
        """Return the status of a term ('confirmed', 'pending_review', or None if absent)."""
        lang = target_lang or self._active_lang
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT status FROM exact_glossary "
                "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                (self._book_id, term_cn, lang),
            ).fetchone()
        if row is None:
            return None
        return row[0]  # type: ignore[no-any-return]

    def find_chapters_with_term(self, term_cn: str, target_lang: typing.Optional[str] = None
                                  ) -> list[int]:
        """
        Return the list of chapter numbers where this term appears.
        This is a simplified version — in production this would query a
        chapter-term mapping table. For now it returns the chapter_first_seen.

        Returns a list so the caller can format it for display.
        """
        lang = target_lang or self._active_lang
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT chapter_first_seen FROM exact_glossary "
                "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                (self._book_id, term_cn, lang),
            ).fetchone()
        if row is None:
            return []
        return [row[0]] if row[0] else []

    def to_dict(self, target_lang: typing.Optional[str] = None) -> dict[str, str]:
        """One language's ``{term_cn: term_en}`` map — the active one by default.

        Deliberately returns a single language: callers write this to
        ``*_glossary.json`` and to the state's ``exact_glossary``, both of which
        are per-job (one target language per job_id). Flattening every loaded
        language into one dict would silently mix Spanish and English renderings
        of the same Chinese term under a single key.
        """
        return dict(self._bucket(target_lang))

    def to_formatted_text(self, matched_terms: typing.Optional[dict[str, str]] = None,
                          target_lang: typing.Optional[str] = None) -> str:
        """
        Format glossary as a markdown table for prompt injection.
        If matched_terms is provided, only those terms are listed.

        ``None`` means "no filter — dump the whole glossary"; ``{}`` means "this
        chapter matched nothing". The two used to be conflated by
        ``matched_terms or self._dict``, so a chapter with zero matches
        received the entire glossary instead of the placeholder — measured at
        41,824 characters for a 2,000-term book, injected into every such
        chapter's WRITE prompt. The existing placeholder test did not catch it
        because it ran against an *empty* glossary.

        The target-language column is labelled from ``target_lang``: it used to
        be hard-coded "English", which told the model the Spanish column was
        English. The unfiltered branch reads the *same* ``target_lang`` bucket,
        so the label and the terms cannot come from two different languages.
        """
        lang = target_lang or self._active_lang
        terms = self._bucket(lang) if matched_terms is None else matched_terms
        if not terms:
            return "(No glossary terms matched for this chapter.)"

        target = target_language_label(lang)
        lines = [f"| Chinese | {target} |", "|----------|---------|"]
        for cn, en in sorted(terms.items(), key=lambda x: len(x[0]), reverse=True):
            # Sort by length descending so longer terms appear first in prompt
            lines.append(f"| {cn} | {en} |")
        return "\n".join(lines)

    def to_formatted_text_with_notes(
        self, matched_terms: typing.Optional[dict[str, str]] = None,
        target_lang: typing.Optional[str] = None,
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

        ``None`` vs ``{}`` is distinguished exactly as in ``to_formatted_text``:
        this is the path graph.py actually takes for a chapter with matches, but
        it is public API and must not carry the same whole-glossary dump on an
        empty match set.
        """
        lang = target_lang or self._active_lang
        terms = self._bucket(lang) if matched_terms is None else matched_terms
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
                    (self._book_id, cn, lang),
                ).fetchone()
                if row and row["note"]:
                    notes_map[cn] = row["note"]

        lines = [
            f"| Chinese | {target_language_label(lang)} | Context |",
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
                # Shared 2-char Chinese prefix. The slice comparison alone is
                # sufficient — no length guard is needed, and adding one creates
                # an unkillable mutant. Proof: keys are unique so cn1 != cn2; if
                # cn1[:2] == cn2[:2] then either both are >= 2 chars, or one is
                # shorter and a shorter slice can only equal a longer one when
                # both are the same string, which uniqueness forbids. Verified by
                # exhaustive enumeration: 79,092,650 term dicts over a 4-char
                # alphabet and 10,726,600 over a 4,433-word mixed CJK/ASCII
                # pool — zero behavioural differences with the guards removed.
                #
                # `len(cn2) >= 2 -> >= 3` survived mutation testing precisely
                # because the guard was unreachable-but-true: when len(cn2) == 2
                # and the prefix matches, cn1 must equal cn2, impossible for
                # distinct dict keys. A condition that cannot be false cannot be
                # killed, so it was removed rather than registered as equivalent.
                if cn1[:2] == cn2[:2]:
                    pairs.append((cn1, en1, cn2, en2))
                    continue
                # Same English first word (excluding stopwords)
                w1 = en1.split()[0].lower() if en1.split() else ""
                w2 = en2.split()[0].lower() if en2.split() else ""
                if w1 and w1 == w2 and w1 not in _STOPWORDS:
                    pairs.append((cn1, en1, cn2, en2))
        return pairs

    def snapshot(self, target_lang: typing.Optional[str] = None) -> str:
        """JSON snapshot for checkpoint persistence.

        Emits the *flat* ``{term_cn: term_en}`` shape for one language, not the
        nested per-language structure. Every existing ``_checkpoint.json`` in
        production holds that flat shape (the difu one has 1,467 entries), and
        checkpoints are written per job — one job is one target language — so
        nesting would break resume for every in-flight job while adding nothing.
        """
        return json.dumps(self._bucket(target_lang), ensure_ascii=False)

    def restore_snapshot(self, snapshot: str,
                         target_lang: typing.Optional[str] = None):
        """Restore one language's terms from a JSON snapshot string.

        Accepts the flat shape produced by ``snapshot`` (including the historical
        checkpoints written before terms were bucketed per language) and loads it
        into the given language, or the active one. A nested
        ``{lang: {term: translation}}`` mapping is also accepted so a caller that
        did snapshot every language can restore all of them in one call.
        """
        data = json.loads(snapshot)
        if not isinstance(data, dict):
            raise TypeError(f"glossary snapshot must be a JSON object, got {type(data).__name__}")
        lang = target_lang or self._active_lang
        nested = data and all(isinstance(v, dict) for v in data.values())
        if nested:
            for loaded_lang, terms in data.items():
                self._by_lang[loaded_lang] = dict(terms)
            # Active lang is honoured as given. If it is not among the restored
            # languages, reads return empty rather than silently falling back to
            # some other language's terms.
            self._active_lang = lang
        else:
            self._by_lang[lang] = {
                cn: en for cn, en in data.items() if isinstance(en, str)
            }
            self._active_lang = lang

    # ------------------------------------------------------------------
    # Review API — human-in-the-loop glossary curation
    # ------------------------------------------------------------------

    @classmethod
    def list_namespaces(cls, db_path: typing.Optional[str] = None) -> list[dict]:
        """List every ``(book_id, target_lang)`` namespace that has terms.

        Class method on purpose: an instance is already scoped to one book_id,
        and the review UI needs to see *all* of them to offer a choice. Without
        this the UI could only ever show whichever book_id its store was
        constructed with — measured as 28 of 131 rows (21.4%) on the real
        checkpoints.db, which is why its confirm/reject loop never touched a
        production book and `status='confirmed'` matched zero rows.

        Returns [{"book_id", "target_lang", "term_count"}] ordered by size, so
        the most-used namespace is the default choice.
        """
        path = db_path or CHECKPOINT_DB_PATH
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                """SELECT book_id, target_lang, COUNT(*) AS n
                   FROM exact_glossary
                   GROUP BY book_id, target_lang
                   ORDER BY n DESC, book_id, target_lang""",
            ).fetchall()
        return [{"book_id": b, "target_lang": lang, "term_count": n}
                for b, lang, n in rows]

    def get_all_terms(self, status_filter: typing.Optional[str] = None,
                      target_lang: typing.Optional[str] = None) -> list[dict]:
        """List all terms, optionally filtered by status."""
        lang = target_lang or self._active_lang
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            if status_filter:
                rows = conn.execute(
                    """SELECT term_cn, term_en, category, context, chapter_first_seen,
                              note, status, target_lang
                       FROM exact_glossary
                       WHERE book_id = ? AND status = ? AND target_lang = ?
                       ORDER BY term_cn""",
                    (self._book_id, status_filter, lang),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT term_cn, term_en, category, context, chapter_first_seen,
                              note, status, target_lang
                       FROM exact_glossary
                       WHERE book_id = ? AND target_lang = ?
                       ORDER BY status, term_cn""",
                    (self._book_id, lang),
                ).fetchall()
        return [dict(row) for row in rows]

    def confirm_term(self, term_cn: str, target_lang: typing.Optional[str] = None) -> int:
        """Set a term's status to 'confirmed'. Returns the rows updated.

        The count matters: terms are keyed by (book_id, term_cn, target_lang),
        so a call naming the wrong namespace matches nothing. Returning 0 lets
        the caller say "no such term" instead of reporting success for an
        update that never happened — which is exactly what the review API did
        before it started checking this.
        """
        lang = target_lang or self._active_lang
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "UPDATE exact_glossary SET status = 'confirmed' "
                "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                (self._book_id, term_cn, lang),
            )
            conn.commit()
            return cur.rowcount

    def reject_term(self, term_cn: str, target_lang: typing.Optional[str] = None) -> int:
        """Delete a term from the exact_store entirely. Returns the rows deleted.

        See confirm_term: a namespace mismatch deletes nothing, and the caller
        must be able to tell that apart from a successful rejection.

        The in-memory eviction is scoped to the same language as the DELETE.
        Against the old flat ``_dict`` it removed the term from whatever language
        the instance happened to hold, so rejecting a Spanish term could evict
        the English rendering of the same Chinese source while leaving the
        Spanish one in memory.
        """
        lang = target_lang or self._active_lang
        self._by_lang.get(lang, {}).pop(term_cn, None)
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM exact_glossary "
                "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                (self._book_id, term_cn, lang),
            )
            conn.commit()
            return cur.rowcount

    def stats(self, target_lang: typing.Optional[str] = None) -> dict:
        return {"total_terms": len(self._bucket(target_lang))}

    def __len__(self) -> int:
        """Term count for the active language.

        Not the sum across languages: ``routes.py`` writes this to a per-job
        glossary file and reports it as that job's term count, so counting
        languages the job never targeted would overstate it.
        """
        return len(self._bucket())

    def __contains__(self, term_cn: str) -> bool:
        return term_cn in self._bucket()
