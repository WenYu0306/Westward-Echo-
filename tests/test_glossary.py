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

    def test_formatted_text_includes_warning(self):
        store = ExactGlossary(db_path=tempfile.NamedTemporaryFile(suffix=".db").name)
        store.add("苏沐橙", "Su Mucheng", category="character")
        store.add("苏沐秋", "Su Muqiu", category="character")
        text = store.to_formatted_text_with_notes()
        assert "DO NOT CONFUSE" in text
        assert "Su Mucheng" in text and "Su Muqiu" in text
