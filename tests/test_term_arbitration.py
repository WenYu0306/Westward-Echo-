"""Tests for the term conflict arbitration system.

Covers:
- Conflict detection in update_glossary_node (_detect_term_conflicts)
- Arbitration node (arbitrate_terms_node) with LLM mocked
- Graph routing (_has_term_conflicts)
- ExactGlossary new methods (get_status, get_term_info, find_chapters_with_term)
"""

import sys
import os
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.nodes.update_glossary import _detect_term_conflicts, update_glossary_node
from src.agent.nodes.arbitrate_terms import (
    arbitrate_terms_node,
    _arbitrate_single_conflict,
    _format_chapter_list,
)
from src.agent.graph import _has_term_conflicts
from src.agent.state import TranslatorState
from src.glossary.exact_store import ExactGlossary
from src.glossary.semantic_store import SemanticGlossary


# ------------------------------------------------------------------
# _detect_term_conflicts tests
# ------------------------------------------------------------------

class TestDetectTermConflicts:

    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        s = ExactGlossary(db_path=path)
        s.add("苏念", "Su Nian", category="character", chapter=1)
        s.add("八零年代", "80s rural America", category="era", chapter=3)
        s.add("青云宗", "Azure Cloud Sect", category="location", chapter=1)
        yield s
        os.unlink(path)

    def test_no_conflict_when_term_does_not_exist(self, store):
        """New term not in glossary → no conflict."""
        validated = [
            {"term_cn": "林小满", "term_en": "Lin Xiaoman", "category": "character"},
        ]
        conflicts = _detect_term_conflicts(validated, store, chapter_number=5, target_lang="en-US")
        assert conflicts == []

    def test_no_conflict_when_same_translation(self, store):
        """Same term_cn and same term_en → no conflict."""
        validated = [
            {"term_cn": "苏念", "term_en": "Su Nian", "category": "character"},
        ]
        conflicts = _detect_term_conflicts(validated, store, chapter_number=5, target_lang="en-US")
        assert conflicts == []

    def test_no_conflict_when_case_differs_only(self, store):
        """Case-insensitive match → no conflict (same translation)."""
        validated = [
            {"term_cn": "苏念", "term_en": "su nian", "category": "character"},
        ]
        conflicts = _detect_term_conflicts(validated, store, chapter_number=5, target_lang="en-US")
        assert conflicts == []

    def test_conflict_detected_when_translation_differs(self, store):
        """Different translation for same term → conflict."""
        validated = [
            {"term_cn": "八零年代", "term_en": "the 1980s", "category": "era"},
        ]
        conflicts = _detect_term_conflicts(validated, store, chapter_number=27, target_lang="en-US")
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c["term_cn"] == "八零年代"
        assert c["existing_en"] == "80s rural America"
        assert c["proposed_en"] == "the 1980s"
        assert c["chapter_proposed"] == 27

    def test_conflict_skipped_when_term_is_confirmed(self, store):
        """Confirmed terms should NOT be flagged as conflicts."""
        store.confirm_term("八零年代")
        validated = [
            {"term_cn": "八零年代", "term_en": "the 1980s", "category": "era"},
        ]
        conflicts = _detect_term_conflicts(validated, store, chapter_number=27, target_lang="en-US")
        assert conflicts == []

    def test_multiple_conflicts(self, store):
        """Multiple conflicting terms are all detected."""
        validated = [
            {"term_cn": "苏念", "term_en": "Sue Nian", "category": "character"},
            {"term_cn": "八零年代", "term_en": "1980s", "category": "era"},
        ]
        conflicts = _detect_term_conflicts(validated, store, chapter_number=10, target_lang="en-US")
        assert len(conflicts) == 2


# ------------------------------------------------------------------
# _format_chapter_list tests
# ------------------------------------------------------------------

class TestFormatChapterList:

    def test_single_int(self):
        assert _format_chapter_list(3) == "3"

    def test_list_of_ints(self):
        assert _format_chapter_list([1, 2, 3]) == "1, 2, 3"

    def test_list_of_ints_range_format(self):
        """When > 3 chapters, use range format."""
        assert _format_chapter_list([1, 2, 3, 4, 5]) == "1-5"

    def test_string(self):
        assert _format_chapter_list("unknown") == "unknown"


# ------------------------------------------------------------------
# _has_term_conflicts graph routing tests
# ------------------------------------------------------------------

class TestHasTermConflicts:

    def test_no_conflicts_routes_to_quality_check(self):
        state = {}
        assert _has_term_conflicts(state) == "quality_check"

    def test_empty_conflicts_routes_to_quality_check(self):
        state = {"term_conflicts": []}
        assert _has_term_conflicts(state) == "quality_check"

    def test_non_empty_conflicts_routes_to_arbitration(self):
        state = {"term_conflicts": [{"term_cn": "八零年代"}]}
        assert _has_term_conflicts(state) == "arbitrate_terms"


# ------------------------------------------------------------------
# _arbitrate_single_conflict tests
# ------------------------------------------------------------------

class TestArbitrateSingleConflict:

    def test_arbitration_picks_existing(self):
        """When LLM picks the existing translation, it's the winner."""
        conflict = {
            "term_cn": "八零年代",
            "existing_en": "80s rural America",
            "proposed_en": "the 1980s",
            "chapter_existing": [3],
            "chapter_proposed": 27,
        }
        state = {
            "genre": "romance_ceo",
            "target_lang": "en-US",
            "chapter_content": "八零年代的乡村生活",
        }

        with patch("src.agent.nodes.arbitrate_terms.ChatOpenAI") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value.content = json.dumps({
                "winner_en": "80s rural America",
                "reason": "Better fits the rural romance genre."
            })
            mock_llm_class.return_value = mock_llm

            result = _arbitrate_single_conflict(conflict, state)

        assert result["term_cn"] == "八零年代"
        assert result["winner_en"] == "80s rural America"
        assert result["loser_en"] == "the 1980s"
        assert "reason" in result

    def test_arbitration_picks_proposed(self):
        """When LLM picks the proposed translation, it wins."""
        conflict = {
            "term_cn": "八零年代",
            "existing_en": "80s rural America",
            "proposed_en": "the 1980s",
            "chapter_existing": [3],
            "chapter_proposed": 27,
        }
        state = {"genre": "general", "target_lang": "en-US", "chapter_content": ""}

        with patch("src.agent.nodes.arbitrate_terms.ChatOpenAI") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value.content = json.dumps({
                "winner_en": "the 1980s",
                "reason": "More neutral, fits the general genre."
            })
            mock_llm_class.return_value = mock_llm

            result = _arbitrate_single_conflict(conflict, state)

        assert result["winner_en"] == "the 1980s"
        assert result["loser_en"] == "80s rural America"

    def test_arbitration_fallback_on_json_error(self):
        """When LLM returns invalid JSON, fall back to existing translation."""
        conflict = {
            "term_cn": "八零年代",
            "existing_en": "80s rural America",
            "proposed_en": "the 1980s",
            "chapter_existing": [3],
            "chapter_proposed": 27,
        }
        state = {"genre": "general", "target_lang": "en-US", "chapter_content": ""}

        with patch("src.agent.nodes.arbitrate_terms.ChatOpenAI") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value.content = "not json at all"
            mock_llm_class.return_value = mock_llm

            result = _arbitrate_single_conflict(conflict, state)

        # Falls back to keeping existing
        assert result["winner_en"] == "80s rural America"
        assert result["loser_en"] == "the 1980s"

    def test_arbitration_unexpected_winner_falls_back(self):
        """When LLM returns a translation that matches neither, fall back."""
        conflict = {
            "term_cn": "八零年代",
            "existing_en": "80s rural America",
            "proposed_en": "the 1980s",
            "chapter_existing": [3],
            "chapter_proposed": 27,
        }
        state = {"genre": "general", "target_lang": "en-US", "chapter_content": ""}

        with patch("src.agent.nodes.arbitrate_terms.ChatOpenAI") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value.content = json.dumps({
                "winner_en": "completely different translation",
                "reason": "I made up something new."
            })
            mock_llm_class.return_value = mock_llm

            result = _arbitrate_single_conflict(conflict, state)

        assert result["winner_en"] == "80s rural America"  # Fallback
        assert result["loser_en"] == "the 1980s"


# ------------------------------------------------------------------
# arbitrate_terms_node tests
# ------------------------------------------------------------------

class TestArbitrateTermsNode:

    @pytest.fixture
    def exact_store(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        s = ExactGlossary(db_path=path)
        s.add("八零年代", "80s rural America", category="era", chapter=3)
        s.add("苏念", "Su Nian", category="character", chapter=1)
        yield s
        os.unlink(path)

    @pytest.fixture
    def base_state(self):
        return {
            "chapter_title": "Chapter 27",
            "chapter_content": "八零年代的记忆涌上心头。",
            "chapter_number": 27,
            "target_lang": "en-US",
            "genre": "romance_ceo",
            "exact_glossary": {},
            "semantic_terms": [],
            "exact_matches_text": "",
            "semantic_matches_text": "",
            "translated_text": "Memories of the 1980s flooded back.",
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "Flashback begins.",
            "previous_chapter_summary": "",
            "quality_score": 0.0,
            "quality_issues": [],
            "retranslation_count": 0,
            "glossary_snapshot_json": "{}",
            "term_conflicts": [],
            "resolved_conflicts": [],
        }

    def test_no_conflicts_returns_early(self, exact_store, base_state):
        """When term_conflicts is empty, node returns empty list."""
        result = arbitrate_terms_node(base_state, exact_store)
        assert result["resolved_conflicts"] == []
        assert "glossary_snapshot_json" in result

    def test_single_conflict_resolved(self, exact_store, base_state):
        """A single conflict is resolved and recorded."""
        base_state["term_conflicts"] = [{
            "term_cn": "八零年代",
            "existing_en": "80s rural America",
            "proposed_en": "the 1980s",
            "chapter_existing": [3],
            "chapter_proposed": 27,
        }]

        with patch("src.agent.nodes.arbitrate_terms.ChatOpenAI") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value.content = json.dumps({
                "winner_en": "80s rural America",
                "reason": "Better genre fit for romance."
            })
            mock_llm_class.return_value = mock_llm

            result = arbitrate_terms_node(base_state, exact_store)

        assert len(result["resolved_conflicts"]) == 1
        rc = result["resolved_conflicts"][0]
        assert rc["term_cn"] == "八零年代"
        assert rc["correct_en"] == "80s rural America"
        assert rc["wrong_en"] == "the 1980s"
        assert rc["reason"] == "Better genre fit for romance."

    def test_conflict_where_winner_differs_from_stored(self, exact_store, base_state):
        """When arbiter picks proposed over existing, the store is updated."""
        base_state["term_conflicts"] = [{
            "term_cn": "八零年代",
            "existing_en": "80s rural America",
            "proposed_en": "the 1980s",
            "chapter_existing": [3],
            "chapter_proposed": 27,
        }]

        with patch("src.agent.nodes.arbitrate_terms.ChatOpenAI") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value.content = json.dumps({
                "winner_en": "the 1980s",
                "reason": "More neutral and accurate."
            })
            mock_llm_class.return_value = mock_llm

            result = arbitrate_terms_node(base_state, exact_store)

        # The exact_store should now have the winner
        assert exact_store.get("八零年代") == "the 1980s"


# ------------------------------------------------------------------
# ExactGlossary new method tests
# ------------------------------------------------------------------

class TestExactGlossaryNewMethods:

    @pytest.fixture
    def store(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        s = ExactGlossary(db_path=path)
        s.add("苏念", "Su Nian", category="character", chapter=1)
        s.add("八零年代", "80s rural America", category="era", chapter=3)
        s.add("裴氏集团", "Pei Group", category="location", chapter=2,
              note="Re-check in later chapters")
        yield s
        os.unlink(path)

    def test_get_status(self, store):
        assert store.get_status("苏念") == "pending_review"
        assert store.get_status("nonexistent") is None

    def test_confirm_term_changes_status(self, store):
        store.confirm_term("苏念")
        assert store.get_status("苏念") == "confirmed"

    def test_get_term_info(self, store):
        info = store.get_term_info("苏念")
        assert info is not None
        assert info["term_cn"] == "苏念"
        assert info["term_en"] == "Su Nian"
        assert info["category"] == "character"
        assert info["chapter_first_seen"] == 1

    def test_get_term_info_nonexistent(self, store):
        assert store.get_term_info("nonexistent") is None

    def test_find_chapters_with_term(self, store):
        chapters = store.find_chapters_with_term("八零年代")
        assert chapters == [3]

    def test_find_chapters_with_term_nonexistent(self, store):
        assert store.find_chapters_with_term("nonexistent") == []


# ------------------------------------------------------------------
# Smoke test: update_glossary returns term_conflicts
# ------------------------------------------------------------------

class TestUpdateGlossaryReturnsConflicts:

    @pytest.fixture
    def exact_store(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        s = ExactGlossary(db_path=path)
        s.add("苏念", "Su Nian", category="character", chapter=1)
        yield s
        os.unlink(path)

    @pytest.fixture
    def semantic_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield SemanticGlossary(persist_path=tmpdir)

    def test_conflict_detected_and_returned(self, exact_store, semantic_store):
        state = {
            "chapter_title": "Chapter 5",
            "chapter_content": "苏念走在路上。",
            "chapter_number": 5,
            "target_lang": "en-US",
            "new_terms_found": [
                {"term_cn": "苏念", "term_en": "Sue Nian", "category": "character"},
            ],
        }

        with patch("src.agent.nodes.update_glossary._validate_terms") as mock_validate:
            mock_validate.return_value = {
                "validated_terms": [{"term_cn": "苏念", "term_en": "Sue Nian", "category": "character"}],
                "rejected": [],
            }
            result = update_glossary_node(state, exact_store, semantic_store)

        assert "term_conflicts" in result
        conflicts = result["term_conflicts"]
        assert len(conflicts) == 1
        assert conflicts[0]["term_cn"] == "苏念"
        assert conflicts[0]["existing_en"] == "Su Nian"
        assert conflicts[0]["proposed_en"] == "Sue Nian"
