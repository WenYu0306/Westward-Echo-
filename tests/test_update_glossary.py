"""Unit tests for update_glossary_node and its helpers.

Tests EXACT_CATEGORIES, SKIP_VALIDATION_CATEGORIES, and the routing logic
that decides which terms go to the exact layer vs semantic-only.

NO LLM API calls are made — _validate_terms is tested via mocking or
by providing terms that bypass validation entirely.
"""

import sys
import os
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.nodes.update_glossary import (
    EXACT_CATEGORIES,
    SKIP_VALIDATION_CATEGORIES,
    _validate_terms,
    update_glossary_node,
)
from src.agent.state import TranslatorState
from src.glossary.exact_store import ExactGlossary
from src.glossary.semantic_store import SemanticGlossary


# ------------------------------------------------------------------
# Category classification tests (pure logic, no mocks needed)
# ------------------------------------------------------------------

class TestCategoryClassification:

    def test_character_in_exact_categories(self):
        """Character terms are routed to the exact layer."""
        assert "character" in EXACT_CATEGORIES

    def test_location_in_exact_categories(self):
        """Location terms are routed to the exact layer."""
        assert "location" in EXACT_CATEGORIES

    def test_culture_not_in_exact_categories(self):
        """Culture terms do NOT go to the exact layer (semantic only)."""
        assert "culture" not in EXACT_CATEGORIES

    def test_technique_not_in_exact_categories(self):
        """Technique/ability names go semantic-only."""
        assert "technique" not in EXACT_CATEGORIES

    def test_item_not_in_exact_categories(self):
        """Item names go semantic-only."""
        assert "item" not in EXACT_CATEGORIES

    def test_era_not_in_exact_categories(self):
        """Era/period names go semantic-only."""
        assert "era" not in EXACT_CATEGORIES


class TestSkipValidationCategories:

    def test_culture_skips_validation(self):
        """Culture terms use rules-based classification, skip LLM validation."""
        assert "culture" in SKIP_VALIDATION_CATEGORIES

    def test_item_skips_validation(self):
        assert "item" in SKIP_VALIDATION_CATEGORIES

    def test_era_skips_validation(self):
        assert "era" in SKIP_VALIDATION_CATEGORIES

    def test_character_not_skipped(self):
        """Character terms MUST go through LLM validation (to avoid dupes)."""
        assert "character" not in SKIP_VALIDATION_CATEGORIES

    def test_location_not_skipped(self):
        """Location terms MUST go through LLM validation."""
        assert "location" not in SKIP_VALIDATION_CATEGORIES


# ------------------------------------------------------------------
# _validate_terms tests (LLM call mocked out)
# ------------------------------------------------------------------

class TestValidateTerms:

    def test_all_skip_terms_bypass_llm(self):
        """When all terms are in SKIP_VALIDATION_CATEGORIES, no LLM call is made."""
        terms = [
            {"term_cn": "风水", "term_en": "feng shui", "category": "culture"},
            {"term_cn": "灵石", "term_en": "spirit stone", "category": "item"},
            {"term_cn": "洪荒", "term_en": "Primordial Era", "category": "era"},
        ]
        result = _validate_terms(terms, "(Empty — first chapter)")
        assert result["validated_terms"] == terms
        assert result["rejected"] == []

    def test_mixed_terms_split_validation(self):
        """Terms that need validation trigger LLM call; skip terms pass through."""
        terms = [
            {"term_cn": "苏念", "term_en": "Su Nian", "category": "character"},
            {"term_cn": "青云宗", "term_en": "Azure Cloud Sect", "category": "location"},
            {"term_cn": "风水", "term_en": "feng shui", "category": "culture"},
        ]
        # Mock the LLM to return the character/location terms as validated
        with patch("src.agent.nodes.update_glossary.ChatOpenAI") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value.content = json.dumps({
                "validated_terms": [
                    {"term_cn": "苏念", "term_en": "Su Nian", "category": "character"},
                    {"term_cn": "青云宗", "term_en": "Azure Cloud Sect", "category": "location"},
                ],
                "rejected": [],
            })
            mock_llm_class.return_value = mock_llm

            result = _validate_terms(terms, "苏念: Su Nian\n")

            # All 3 terms should be in validated (2 from LLM + 1 skipped)
            assert len(result["validated_terms"]) == 3
            validated_cns = {t["term_cn"] for t in result["validated_terms"]}
            assert validated_cns == {"苏念", "青云宗", "风水"}

    def test_empty_new_terms_returns_cleanly(self):
        """An empty list returns without errors (used by early-return path)."""
        result = _validate_terms([], "(Empty)")
        assert result["validated_terms"] == []
        assert result["rejected"] == []


# ------------------------------------------------------------------
# update_glossary_node tests (integration of routing logic)
# ------------------------------------------------------------------

class TestUpdateGlossaryNode:

    @pytest.fixture
    def exact_store(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        store = ExactGlossary(db_path=path)
        yield store
        os.unlink(path)

    @pytest.fixture
    def semantic_store(self):
        """Create a SemanticGlossary with a temp persist path.

        The ONNX embedding model may not be available in CI/test environments,
        so we swallow initialization failures — tests that actually call
        add_batch on it should mock that method.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SemanticGlossary(persist_path=tmpdir)
            yield store

    @pytest.fixture
    def base_state(self):
        return TranslatorState(
            chapter_title="Chapter 1",
            chapter_content="苏念走在庭院中。",
            chapter_number=1,
            target_lang="en-US",
            exact_glossary={},
            semantic_terms=[],
            exact_matches_text="",
            semantic_matches_text="",
            translated_text="Su Nian walked in the courtyard.",
            new_terms_found=[],
            adaptation_notes=[],
            chapter_summary="Su Nian arrives.",
            previous_chapter_summary="",
            quality_score=0.0,
            quality_issues=[],
            retranslation_count=0,
            glossary_snapshot_json="{}",
        )

    def test_empty_new_terms_snapshot_returned(self, exact_store, semantic_store, base_state):
        """When new_terms_found is empty, only a glossary snapshot is returned."""
        result = update_glossary_node(base_state, exact_store, semantic_store)
        assert "glossary_snapshot_json" in result
        assert "term_conflicts" in result
        assert result["term_conflicts"] == []
        # No terms should have been added
        assert len(exact_store) == 0

    def test_validated_terms_split_to_exact_vs_semantic(self, exact_store, semantic_store, base_state):
        """Character/location terms go to exact; all go to semantic."""
        base_state["new_terms_found"] = [
            {"term_cn": "苏念", "term_en": "Su Nian", "category": "character"},
            {"term_cn": "风灵谷", "term_en": "Wind Spirit Valley", "category": "location"},
            {"term_cn": "金丹", "term_en": "Golden Core", "category": "technique"},
        ]

        with patch("src.agent.nodes.update_glossary._validate_terms") as mock_validate:
            mock_validate.return_value = {
                "validated_terms": base_state["new_terms_found"],
                "rejected": [],
            }
            result = update_glossary_node(base_state, exact_store, semantic_store)

        # Character + location should be in exact layer
        assert exact_store.get("苏念") == "Su Nian"
        assert exact_store.get("风灵谷") == "Wind Spirit Valley"
        # Technique: if Chroma is healthy, stays semantic-only.
        # If Chroma is degraded, the safety-net fallback also persists to exact_store.
        if semantic_store.is_healthy():
            assert exact_store.get("金丹") is None
        assert "glossary_snapshot_json" in result

    def test_terms_in_skip_validation_bypass_llm(self, exact_store, semantic_store, base_state):
        """Culture/item/era terms skip validation but still go to semantic store."""
        base_state["new_terms_found"] = [
            {"term_cn": "风水", "term_en": "feng shui", "category": "culture"},
            {"term_cn": "洪荒", "term_en": "Primordial Era", "category": "era"},
        ]

        # No mocking of _validate_terms — it should use the real function
        # which detects all terms are in SKIP_VALIDATION_CATEGORIES
        result = update_glossary_node(base_state, exact_store, semantic_store)

        # Culture/era are NOT in EXACT_CATEGORIES, so normally skip exact_store.
        # But when Chroma is degraded, the safety-net fallback also persists them.
        if semantic_store.is_healthy():
            assert len(exact_store) == 0
        assert "glossary_snapshot_json" in result

    def test_semantic_store_receives_all_categories(self, exact_store, semantic_store, base_state):
        """The semantic store (Chroma) receives ALL terms regardless of category."""
        base_state["new_terms_found"] = [
            {"term_cn": "苏念", "term_en": "Su Nian", "category": "character"},
            {"term_cn": "霸总", "term_en": "Alpha CEO", "category": "culture"},
        ]

        with patch("src.agent.nodes.update_glossary._validate_terms") as mock_validate:
            mock_validate.return_value = {
                "validated_terms": base_state["new_terms_found"],
                "rejected": [],
            }
            with patch.object(semantic_store, "add_batch") as mock_semantic_add:
                update_glossary_node(base_state, exact_store, semantic_store)
                # add_batch should have been called with ALL terms
                mock_semantic_add.assert_called_once()
                call_args = mock_semantic_add.call_args[0][0]
                assert len(call_args) == 2  # Both character and culture terms
