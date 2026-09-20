"""Prompt registry tests — the novel path must remain byte-identical.

The registry selects the prompt set for a content type; "novel" is the
only validated path. These tests guard the hard constraint that the
web-novel pipeline is unaffected: registry("novel") must return exactly
the pre-registry constants, and unknown types must fall back to novel
rather than failing.
"""

import string
from unittest.mock import patch

from src.agent.prompts.fix import FIX_SYSTEM, FIX_USER
from src.agent.prompts.read import READ_SYSTEM, READ_USER
from src.agent.prompts.readback import READBACK_SYSTEM, READBACK_USER
from src.agent.prompts.registry import NOVEL_PROMPTS, get_prompt_set
from src.agent.prompts.write import WRITE_SYSTEM, WRITE_USER


class TestNovelIdentity:
    """registry('novel') must return the exact original constants."""

    def test_read_templates_identical(self):
        ps = get_prompt_set("novel")
        assert ps.read_system == READ_SYSTEM
        assert ps.read_user == READ_USER

    def test_write_templates_identical(self):
        ps = get_prompt_set("novel")
        assert ps.write_system == WRITE_SYSTEM
        assert ps.write_user == WRITE_USER

    def test_readback_templates_identical(self):
        ps = get_prompt_set("novel")
        assert ps.readback_system == READBACK_SYSTEM
        assert ps.readback_user == READBACK_USER

    def test_fix_templates_identical(self):
        ps = get_prompt_set("novel")
        assert ps.fix_system == FIX_SYSTEM
        assert ps.fix_user == FIX_USER

    def test_novel_returns_singleton(self):
        assert get_prompt_set("novel") is NOVEL_PROMPTS


class TestFallback:
    def test_unknown_type_falls_back_to_novel(self):
        assert get_prompt_set("poetry") is NOVEL_PROMPTS

    def test_empty_string_falls_back_to_novel(self):
        assert get_prompt_set("") is NOVEL_PROMPTS

    def test_none_falls_back_to_novel(self):
        assert get_prompt_set(None) is NOVEL_PROMPTS

    def test_case_and_whitespace_normalized(self):
        assert get_prompt_set("  Novel ") is NOVEL_PROMPTS




class TestContentTypePlumbing:
    """content_type must flow translate_chapter → _make_state → state dict,
    including the long-chapter split path."""

    def test_make_state_defaults_to_novel(self):
        from src.agent.graph import TranslationAgent

        agent = TranslationAgent(book_id="test_ct_default")
        state = agent._make_state("T", "c", 1, "", "en-US", "romance_ceo")
        assert state["content_type"] == "novel"

    def test_split_path_defaults_to_novel(self):
        from src.agent.graph import TranslationAgent

        agent = TranslationAgent(book_id="test_ct_split_default")
        captured = []

        def fake_once(title, content, number, prev, lang, genre,
                      skip_readback=False, use_flash_writer=False,
                      content_type="novel"):
            captured.append(content_type)
            return {
                "translated_text": "x",
                "new_terms_found": [],
                "adaptation_notes": [],
                "chapter_summary": "",
                "quality_score": 5.0,
                "quality_issues": [],
                "glossary_snapshot_json": "{}",
            }

        seg = {"content": "seg", "is_last": True, "index": 1, "total": 1}
        with patch.object(agent, "_translate_once", side_effect=fake_once), \
             patch("src.agent.graph.should_split", return_value=True), \
             patch("src.agent.graph.split_chapter", return_value=[seg]):
            agent.translate_chapter("T", "long text", 1, "", "en-US", "urban")

        assert captured == ["novel"]
