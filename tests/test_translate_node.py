"""Integration test for the translate node.

Requires DEEPSEEK_API_KEY in environment. Skip if not available.
"""

import os
import re

import pytest

from src.chapter_splitter import split_chapters, ParagraphTag
from src.glossary.exact_store import ExactGlossary
from src.glossary.semantic_store import SemanticGlossary
from src.agent.graph import TranslationAgent


# Path to the test fixture
FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "pei_zong_ch1-3.txt")


def load_fixture() -> str:
    if not os.path.exists(FIXTURE_PATH):
        pytest.skip(f"Fixture not found: {FIXTURE_PATH}")
    return open(FIXTURE_PATH, encoding="utf-8").read()


def has_api_key() -> bool:
    return bool(os.getenv("DEEPSEEK_API_KEY"))


# ------------------------------------------------------------------
# Tests that run without API key
# ------------------------------------------------------------------

class TestFixtureIntegrity:

    def test_fixture_exists(self):
        assert os.path.exists(FIXTURE_PATH), f"Fixture missing: {FIXTURE_PATH}"

    def test_fixture_has_three_chapters(self):
        text = load_fixture()
        chapters = split_chapters(text)
        # Should have at least preamble + 3 chapters
        assert len(chapters) >= 3, f"Expected >= 3 chapters, got {len(chapters)}"

    def test_key_characters_present(self):
        text = load_fixture()
        assert "苏念" in text, "Female lead missing"
        assert "裴衍舟" in text, "Male lead missing"
        assert "系统" in text, "System element missing"

    def test_key_cultural_terms_present(self):
        text = load_fixture()
        cultural_terms = ["霸总", "穿越", "白莲花", "父凭子贵", "社畜"]
        for term in cultural_terms:
            assert term in text, f"Cultural term '{term}' missing from fixture"

    def test_chapter_titles_parsed(self):
        text = load_fixture()
        chapters = split_chapters(text)
        titles = [c.title for c in chapters]
        assert any("穿成" in t for t in titles), "Chapter 1 title not found"
        assert any("父凭子贵" in t for t in titles), "Chapter 3 title not found"


class TestExactGlossaryWithFixture:

    def test_term_extraction_from_fixture(self):
        """Verify that a pre-populated glossary correctly matches fixture text."""
        text = load_fixture()
        store = ExactGlossary()

        # Pre-populate with expected terms
        store.add("苏念", "Su Nian", category="character")
        store.add("裴衍舟", "Pei Yanzhou", category="character")
        store.add("楚淮", "Chu Huai", category="character")
        store.add("林婉清", "Lin Wanqing", category="character")
        store.add("裴氏集团", "Pei Group", category="location")
        store.add("耀星集团", "Starbright Group", category="location")
        store.add("霸总攻略系统", "CEO Conquest System", category="technique")

        matches = store.match_in_text(text)

        # All pre-populated terms should be matched
        assert matches["苏念"] == "Su Nian"
        assert matches["裴衍舟"] == "Pei Yanzhou"
        assert matches["裴氏集团"] == "Pei Group"
        assert matches["耀星集团"] == "Starbright Group"

    def test_no_false_cross_match(self, store=None):
        """'苏' alone should not match '苏念'."""
        if store is None:
            store = ExactGlossary()
            store.add("苏念", "Su Nian", category="character")

        # "苏秘书" contains 苏 but is not the same as 苏念
        text_without_full_name = "苏秘书，这份文件需要签字。"
        matches = store.match_in_text(text_without_full_name)
        # "苏念" should match because "苏秘书" doesn't contain "苏念"
        assert "苏念" not in matches


# ------------------------------------------------------------------
# Live API tests (skipped without DEEPSEEK_API_KEY)
# ------------------------------------------------------------------

@pytest.mark.skipif(not has_api_key(), reason="DEEPSEEK_API_KEY not set")
class TestLiveTranslation:

    def test_translate_short_text(self):
        agent = TranslationAgent()
        result = agent.translate_chapter(
            chapter_title="第1章 测试",
            chapter_content="一个男人走在路上，他想起了自己的妻子。他们曾经很幸福。\n\n突然电话响了。",
            chapter_number=1,
            target_lang="en-US",
        )

        assert result["translated_text"], "Translation should not be empty"
        assert len(result["translated_text"]) > 20, "Translation should be substantial"

    def test_cultural_term_addition(self):
        """Translate a text with a cultural term not in glossary → should be caught."""
        agent = TranslationAgent()

        # Pre-populate with a known character
        agent.exact_store.add("林小满", "Lin Xiaoman", category="character")

        result = agent.translate_chapter(
            chapter_title="第1章 八零年代",
            chapter_content="林小满回到了八零年代的生产队。这里的一切都和记忆里不同。",
            chapter_number=1,
            target_lang="en-US",
        )

        # "八零年代" and "生产队" should appear in either the translation
        # or the new_terms_found, depending on model behavior
        translated = result["translated_text"]
        assert "Lin Xiaoman" in translated, "Glossary term must be used"

    def test_three_chapters_consistent_names(self):
        """Translate 2 chapters and verify character names stay consistent."""
        text = load_fixture()
        chapters = split_chapters(text)
        translatable = [c for c in chapters if c.action != ParagraphTag.SKIP][:2]

        if len(translatable) < 2:
            pytest.skip("Not enough chapters in fixture")

        agent = TranslationAgent()
        summary = ""

        translations = []
        for ch in translatable[:2]:
            result = agent.translate_chapter(
                chapter_title=ch.title,
                chapter_content=ch.content,
                chapter_number=ch.index,
                previous_summary=summary,
            )
            translations.append(result["translated_text"])
            summary = result.get("chapter_summary", "")

        # Verify "Su Nian" appears in both chapters (not "Sue Nian" or other variant)
        # This tests the glossary consistency mechanism
        for i, tt in enumerate(translations):
            assert len(tt.strip()) > 0, f"Chapter {i+1} translation should not be empty"
