"""Tests for the double-layer glossary."""

import os
import tempfile

import pytest

from src.glossary.exact_store import ExactGlossary


class TestExactGlossary:

    @pytest.fixture
    def store(self):
        """Create an ExactGlossary with a temp SQLite database."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        store = ExactGlossary(db_path=path)
        yield store
        os.unlink(path)

    def test_add_and_match(self, store):
        store.add("林小满", "Lin Xiaoman", category="character")
        store.add("八零年代", "80s rural America", category="era")

        text = "林小满走到窗前，窗外是八零年代的景象。"
        matches = store.match_in_text(text)

        assert matches["林小满"] == "Lin Xiaoman"
        assert matches["八零年代"] == "80s rural America"

    def test_no_false_match(self, store):
        store.add("裴衍舟", "Pei Yanzhou", category="character")

        text = "楚淮开着车来到了公司。"
        matches = store.match_in_text(text)

        assert "裴衍舟" not in matches  # Different character

    def test_snapshot_and_restore(self, store):
        store.add("苏念", "Su Nian", category="character")
        store.add("裴氏集团", "Pei Group", category="location")

        snapshot = store.snapshot()
        assert "苏念" in snapshot
        assert "裴氏集团" in snapshot

        # Create a fresh store and restore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path2 = f.name
        store2 = ExactGlossary(db_path=path2)
        store2.restore_snapshot(snapshot)

        assert store2.get("苏念") == "Su Nian"
        assert store2.get("裴氏集团") == "Pei Group"
        assert len(store2) == 2

        os.unlink(path2)

    def test_persistence_across_instances(self, store):
        """Terms should survive store re-creation (SQLite persistence)."""
        store.add("青云山", "Mount Qingyun", category="location")

        # Re-create store from the same DB file
        store2 = ExactGlossary(db_path=store._db_path)
        store2.load_from_db()

        assert store2.get("青云山") == "Mount Qingyun"

    def test_add_batch(self, store):
        terms = [
            {"term_cn": "金丹期", "term_en": "Golden Core stage", "category": "technique"},
            {"term_cn": "元婴期", "term_en": "Nascent Soul stage", "category": "technique"},
        ]
        store.add_batch(terms, chapter=5)
        assert len(store) == 2
        assert store.get("金丹期") == "Golden Core stage"

    def test_exact_match_not_fuzzy(self, store):
        """Exact layer must NOT do fuzzy matching — only exact string containment."""
        store.add("林小满", "Lin Xiaoman", category="character")

        # "林晓曼" is similar to "林小满" but should NOT match
        text = "林晓曼走进了房间。"
        matches = store.match_in_text(text)
        assert "林小满" not in matches



class TestConfusablePairs:
    """v0.17: detect near-identical terms the LLM would confuse."""

    def test_shared_chinese_prefix(self):
        terms = {"苏沐橙": "Su Mucheng", "苏沐秋": "Su Muqiu"}
        pairs = ExactGlossary._detect_confusable_pairs(terms)
        assert ("苏沐橙", "Su Mucheng", "苏沐秋", "Su Muqiu") in pairs

    def test_shared_english_first_word(self):
        terms = {"微草战队": "Wei Cao", "魏琛": "Wei Chen"}
        pairs = ExactGlossary._detect_confusable_pairs(terms)
        assert ("微草战队", "Wei Cao", "魏琛", "Wei Chen") in pairs

    def test_stopword_first_word_ignored(self):
        """English terms starting with 'the'/'a' should not match on stopwords."""
        terms = {"孤独的城主": "the Lonely Lord", "魔术师": "the Magician"}
        pairs = ExactGlossary._detect_confusable_pairs(terms)
        assert pairs == []

    def test_distinct_terms_not_flagged(self):
        terms = {"苏念": "Su Nian", "裴衍舟": "Pei Yanzhou", "霸总": "Alpha CEO"}
        pairs = ExactGlossary._detect_confusable_pairs(terms)
        assert pairs == []

    def test_two_char_term_sharing_a_two_char_prefix(self):
        # A 2-char term IS eligible (the rule is "at least 2 chars"), and the
        # shared prefix is exactly 2 chars — an off-by-one either way would
        # miss this pair, which the English-first-word rule cannot rescue
        # because the renderings share no first word.
        terms = {"百花": "Baihua", "百花缭乱": "Hundred Flowers Blossom"}
        pairs = ExactGlossary._detect_confusable_pairs(terms)
        assert ("百花", "Baihua", "百花缭乱", "Hundred Flowers Blossom") in pairs

    def test_three_char_prefix_match_also_detected(self):
        terms = {"苏沐橙": "Su Mucheng", "苏沐秋": "Su Muqiu"}
        pairs = ExactGlossary._detect_confusable_pairs(terms)
        assert len(pairs) == 1

    def test_prefix_rule_is_not_rescued_by_the_english_rule(self):
        # A 3-char pair sharing a 2-char Chinese prefix, but with DIFFERENT
        # English first words — so a broken prefix comparison cannot be masked
        # by the English fallback.
        terms = {"苏沐橙": "Su Mucheng", "苏沐秋": "Muqiu"}
        pairs = ExactGlossary._detect_confusable_pairs(terms)
        assert ("苏沐橙", "Su Mucheng", "苏沐秋", "Muqiu") in pairs

    def test_formatted_text_includes_warning(self):
        store = ExactGlossary(db_path=tempfile.NamedTemporaryFile(suffix=".db").name)
        store.add("苏沐橙", "Su Mucheng", category="character")
        store.add("苏沐秋", "Su Muqiu", category="character")
        text = store.to_formatted_text_with_notes()
        assert "DO NOT CONFUSE" in text
        assert "Su Mucheng" in text and "Su Muqiu" in text


class TestBookIsolation:
    """book_id isolation: same term in two books must not overwrite each other."""

    def _db(self):
        return tempfile.NamedTemporaryFile(suffix=".db", delete=False).name

    def test_same_term_two_books_do_not_clobber(self):
        db = self._db()
        book_a = ExactGlossary(db_path=db, book_id="book_a")
        book_b = ExactGlossary(db_path=db, book_id="book_b")

        book_a.add("苏念", "Su Nian (A)", category="character")
        book_b.add("苏念", "Su Nian (B)", category="character")

        # Each book's in-memory dict is independent
        assert book_a.get("苏念") == "Su Nian (A)"
        assert book_b.get("苏念") == "Su Nian (B)"

        # On reload from DB, each book still sees its own term
        book_a2 = ExactGlossary(db_path=db, book_id="book_a")
        book_a2.load_from_db()
        book_b2 = ExactGlossary(db_path=db, book_id="book_b")
        book_b2.load_from_db()
        assert book_a2.get("苏念") == "Su Nian (A)"
        assert book_b2.get("苏念") == "Su Nian (B)"

    def test_legacy_table_renamed(self):
        """The pre-book_id table (term_cn PRIMARY KEY) is renamed on migration."""
        import sqlite3
        db = self._db()
        # Simulate a legacy table
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE exact_glossary (
            term_cn TEXT PRIMARY KEY, term_en TEXT NOT NULL,
            category TEXT DEFAULT 'culture', context TEXT DEFAULT '',
            chapter_first_seen INTEGER DEFAULT 0, note TEXT DEFAULT '',
            status TEXT DEFAULT 'pending_review', target_lang TEXT DEFAULT 'en-US'
        )""")
        conn.execute("INSERT INTO exact_glossary (term_cn, term_en) VALUES ('旧词','old term')")
        conn.commit()
        conn.close()

        # Initializing a new store should rename legacy → legacy, create new schema
        ExactGlossary(db_path=db, book_id="book_x")

        conn = sqlite3.connect(db)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        conn.close()
        assert "exact_glossary_legacy" in tables
        assert "exact_glossary" in tables


class TestWalConcurrency:
    """WAL-pragma race: multiple instances init the same fresh DB at once."""

    def test_concurrent_init_same_fresh_db(self):
        import threading
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        errors = []

        def worker(book_id):
            try:
                s = ExactGlossary(db_path=db, book_id=book_id)
                s.add("苏念", f"Su Nian ({book_id})", category="character")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"book_{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"concurrent init raised: {errors}"

    def test_concurrent_init_no_term_loss(self):
        import threading
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name

        def worker(book_id):
            s = ExactGlossary(db_path=db, book_id=book_id)
            s.add("苏念", f"Su Nian ({book_id})", category="character")

        threads = [threading.Thread(target=worker, args=(f"book_{i}",)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 每个 book 独立读回自己的术语
        for i in range(6):
            s = ExactGlossary(db_path=db, book_id=f"book_{i}")
            s.load_from_db()
            assert s.get("苏念") == f"Su Nian (book_{i})", f"book_{i} 术语丢失或串"


# ── Query surface ─────────────────────────────────────────────────

class _TempStore:
    """Build an ExactGlossary on a throwaway DB (fast, no fixtures needed)."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "glossary.db")
        self.store = ExactGlossary(db_path=self.path, book_id="query-surface")

    def close(self):
        self.tmp.cleanup()


@pytest.fixture
def temp_store():
    holder = _TempStore()
    yield holder.store
    holder.close()


class TestTermInfoQueries:
    def test_get_term_info_missing_returns_none(self, temp_store):
        assert temp_store.get_term_info("不存在的词") is None

    def test_get_term_info_returns_the_full_row(self, temp_store):
        temp_store.add("聋婆婆", "Deaf Granny", category="character",
                       context="老槐树下", chapter=1)
        info = temp_store.get_term_info("聋婆婆")
        assert isinstance(info, dict)
        assert info["term_cn"] == "聋婆婆"
        assert info["term_en"] == "Deaf Granny"
        assert info["category"] == "character"

    def test_get_status_missing_returns_none(self, temp_store):
        assert temp_store.get_status("不存在的词") is None

    def test_new_term_is_pending_review(self, temp_store):
        temp_store.add("聋婆婆", "Deaf Granny")
        assert temp_store.get_status("聋婆婆") == "pending_review"

    def test_confirmed_term_reports_confirmed(self, temp_store):
        temp_store.add("聋婆婆", "Deaf Granny")
        temp_store.confirm_term("聋婆婆")
        assert temp_store.get_status("聋婆婆") == "confirmed"

    def test_rejected_term_is_removed_entirely(self, temp_store):
        # reject_term deletes the row — it does not store a "rejected" status.
        temp_store.add("聋婆婆", "Deaf Granny")
        temp_store.reject_term("聋婆婆")
        assert temp_store.get_status("聋婆婆") is None
        assert temp_store.get_term_info("聋婆婆") is None


class TestFindChaptersWithTerm:
    def test_missing_term_returns_empty_list(self, temp_store):
        assert temp_store.find_chapters_with_term("不存在的词") == []

    def test_term_seen_in_a_chapter_returns_that_chapter(self, temp_store):
        temp_store.add("聋婆婆", "Deaf Granny", chapter=7)
        assert temp_store.find_chapters_with_term("聋婆婆") == [7]

    def test_term_without_a_chapter_returns_empty_list(self, temp_store):
        # chapter 0 means "unknown chapter" — it must not be reported as ch 0.
        temp_store.add("聋婆婆", "Deaf Granny", chapter=0)
        assert temp_store.find_chapters_with_term("聋婆婆") == []


class TestFormattedText:
    def test_empty_glossary_returns_the_placeholder(self, temp_store):
        assert temp_store.to_formatted_text({}) == (
            "(No glossary terms matched for this chapter.)"
        )

    def test_matched_terms_restrict_the_table(self, temp_store):
        temp_store.add("聋婆婆", "Deaf Granny")
        temp_store.add("王三", "Wang San")
        table = temp_store.to_formatted_text({"王三": "Wang San"})
        assert "Wang San" in table
        assert "Deaf Granny" not in table

    def test_longer_terms_are_listed_first(self, temp_store):
        # Prompt ordering is deliberate: longer (more specific) terms first so
        # the model matches 九道沟村 before 村.
        temp_store.add("村", "Village")
        temp_store.add("九道沟村", "Jiudaogou Village")
        table = temp_store.to_formatted_text()
        assert table.index("Jiudaogou Village") < table.index("| Village |")

    def test_notes_table_lists_longer_terms_first(self, temp_store):
        temp_store.add("村", "Village")
        temp_store.add("九道沟村", "Jiudaogou Village")
        table = temp_store.to_formatted_text_with_notes()
        assert table.index("Jiudaogou Village") < table.index("| Village |")

    def test_notes_table_marks_terms_without_a_note(self, temp_store):
        temp_store.add("王三", "Wang San")
        table = temp_store.to_formatted_text_with_notes()
        assert "| 王三 | Wang San | (see prior chapters) |" in table


class TestInventoryAccessors:
    def test_get_all_terms_returns_dicts(self, temp_store):
        temp_store.add("聋婆婆", "Deaf Granny", category="character")
        rows = temp_store.get_all_terms()
        assert isinstance(rows, list)
        assert rows and isinstance(rows[0], dict)
        assert rows[0]["term_cn"] == "聋婆婆"

    def test_stats_counts_terms(self, temp_store):
        assert temp_store.stats() == {"total_terms": 0}
        temp_store.add("聋婆婆", "Deaf Granny")
        temp_store.add("王三", "Wang San")
        assert temp_store.stats() == {"total_terms": 2}

    def test_contains_reports_membership(self, temp_store):
        temp_store.add("聋婆婆", "Deaf Granny")
        assert "聋婆婆" in temp_store
        assert "不存在的词" not in temp_store

    def test_len_counts_terms(self, temp_store):
        assert len(temp_store) == 0
        temp_store.add("聋婆婆", "Deaf Granny")
        assert len(temp_store) == 1


class TestSqliteLockTimeout:
    def test_connections_wait_thirty_seconds_for_the_lock(self, tmp_path, monkeypatch):
        # The translation workers share this DB; a short timeout would surface
        # "database is locked" as a chapter failure.
        import sqlite3

        import src.glossary.exact_store as exact_store

        seen = []
        real_connect = sqlite3.connect

        def spy(*args, **kwargs):
            seen.append(kwargs)
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(exact_store.sqlite3, "connect", spy)
        ExactGlossary(db_path=str(tmp_path / "lock.db"), book_id="lock-timeout")
        assert seen, "the store must open a connection"
        assert all(kwargs.get("timeout") == 30 for kwargs in seen)
