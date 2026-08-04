"""Unit tests for v0.15 reader-centric pipeline.

All LLM calls are mocked — no API keys or network required.

Coverage:
  - Graph routing (conditional edges, retry limits)
  - _make_state (glossary injection, prefetch, style memo)
  - Node outputs (READ, WRITE, READBACK, FIX with patched ChatOpenAI)
  - parse_llm_json shared utility (all layers)
  - _post_process (term persistence, cultural note merging, style memo)
  - Chapter auto-split (threshold, segment count, bridging)
"""

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.state import TranslatorState
from src.agent.graph import (
    build_graph,
    _should_readback,
    _needs_fix,
    TranslationAgent,
)
from src.agent.parse_utils import parse_llm_json
from src.glossary.exact_store import ExactGlossary
from src.glossary.semantic_store import SemanticGlossary
from src.chapter_slicer import should_split, split_chapter


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _make_mock_llm(content: str) -> MagicMock:
    """Return a MagicMock that mimics ChatOpenAI, returning *content* from .invoke()."""
    mock_response = MagicMock()
    mock_response.content = content
    mock_response.response_metadata = {"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}}
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = mock_response
    return mock_llm


def _make_state(**overrides) -> TranslatorState:
    """Build a minimal valid TranslatorState."""
    state: TranslatorState = {
        "chapter_title": "Test Chapter",
        "chapter_content": "测试内容。",
        "chapter_number": 1,
        "target_lang": "en-US",
        "genre": "romance_ceo",
        "exact_glossary": {},
        "semantic_terms": [],
        "exact_matches_text": "",
        "semantic_matches_text": "",
        "translated_text": "",
        "new_terms_found": [],
        "adaptation_notes": [],
        "chapter_summary": "",
        "previous_chapter_summary": "",
        "quality_score": 5.0,
        "quality_issues": [],
        "retranslation_count": 0,
        "glossary_snapshot_json": "",
        "read_analysis": {},
        "readback_feedback": {},
        "context_signals": "",
        "image_gaps": [],
        "style_memo": "",
        "skip_readback": False,
        "use_flash_writer": False,
        "cold_read_context": "",
        "term_conflicts": [],
        "resolved_conflicts": [],
        "dialect_context": "",
    }
    state.update(overrides)
    return state


def _new_stores():
    """Return (ExactGlossary, SemanticGlossary) backed by temp files."""
    exact = ExactGlossary(db_path=os.path.join(tempfile.gettempdir(), "test_v15_exact.db"))
    semantic = SemanticGlossary(
        persist_path=os.path.join(tempfile.gettempdir(), "test_v15_chroma")
    )
    return exact, semantic


# ═══════════════════════════════════════════════════════════════
# 1a. Graph routing
# ═══════════════════════════════════════════════════════════════

class TestShouldReadback:
    def test_skip_readback_true_returns_end(self):
        state = _make_state(skip_readback=True)
        assert _should_readback(state) == "__end__"

    def test_skip_readback_false_returns_readback(self):
        state = _make_state(skip_readback=False)
        assert _should_readback(state) == "readback_node"

    def test_skip_readback_missing_defaults_to_readback(self):
        state = _make_state()
        del state["skip_readback"]
        assert _should_readback(state) == "readback_node"


class TestNeedsFix:
    def test_needs_fix_with_retries_available(self):
        state = _make_state(
            readback_feedback={"verdict": "NEEDS_FIX"},
            retranslation_count=0,
        )
        assert _needs_fix(state) == "fix_node"

    def test_pass_goes_to_end(self):
        state = _make_state(
            readback_feedback={"verdict": "PASS"},
            retranslation_count=0,
        )
        assert _needs_fix(state) == "__end__"

    def test_needs_fix_max_retries_exceeded(self):
        state = _make_state(
            readback_feedback={"verdict": "NEEDS_FIX"},
            retranslation_count=2,
        )
        assert _needs_fix(state) == "__end__"

    def test_needs_fix_retries_above_max(self):
        state = _make_state(
            readback_feedback={"verdict": "NEEDS_FIX"},
            retranslation_count=3,
        )
        assert _needs_fix(state) == "__end__"

    def test_missing_feedback_defaults_to_pass(self):
        state = _make_state()
        assert "readback_feedback" in state and state["readback_feedback"] == {}
        assert _needs_fix(state) == "__end__"

    def test_none_feedback_defaults_to_pass(self):
        state = _make_state(readback_feedback=None)
        assert _needs_fix(state) == "__end__"


class TestBuildGraph:
    """Graph assembles without errors and produces a compiled graph."""

    def test_build_graph_returns_compiled(self):
        exact, semantic = _new_stores()
        graph = build_graph(exact, semantic)
        assert graph is not None
        assert hasattr(graph, "invoke")

    def test_full_graph_read_to_end(self):
        """Full graph invocation: patch ChatOpenAI in all 4 node modules.

        The real node functions run (validating state propagation and routing),
        but with mocked LLM responses so no API calls are made.
        """
        exact, semantic = _new_stores()

        read_out = {
            "emotional_arc": "Tension builds.", "cultural_gaps": [],
            "image_gaps": [], "terminology_decisions": [], "pacing_notes": "",
            "crafted_moments": [],
        }
        write_out = {
            "translated_text": "She walked into the grand hall, her footsteps echoing against marble. The room was vast and empty, lit only by a single chandelier.",
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "A woman enters.",
        }
        readback_out = {
            "verdict": "PASS", "overall_impression": "Good.",
            "would_keep_reading": True, "comprehension_issues": [],
            "engagement_gaps": [], "standout_moments": [],
            "character_tracking": "", "world_comprehension": "",
        }

        with patch("src.agent.nodes.read.ChatOpenAI") as mock_read_llm, \
             patch("src.agent.nodes.write.ChatOpenAI") as mock_write_llm, \
             patch("src.agent.nodes.readback.ChatOpenAI") as mock_readback_llm:

            mock_read_llm.return_value = _make_mock_llm(json.dumps(read_out))
            mock_write_llm.return_value = _make_mock_llm(json.dumps(write_out))
            mock_readback_llm.return_value = _make_mock_llm(json.dumps(readback_out))

            graph = build_graph(exact, semantic)
            state = _make_state()
            result = graph.invoke(state)

            assert "grand hall" in result["translated_text"]
            assert result["quality_score"] == 5.0
            assert result["readback_feedback"]["verdict"] == "PASS"

    def test_graph_skip_readback_fast_path(self):
        """skip_readback=True: READ→WRITE→END, no READBACK LLM call."""
        exact, semantic = _new_stores()

        read_out = {"emotional_arc": "", "cultural_gaps": [], "image_gaps": [],
                     "terminology_decisions": [], "pacing_notes": "", "crafted_moments": []}
        write_out = {"translated_text": "Fast output.", "new_terms_found": [],
                      "adaptation_notes": [], "chapter_summary": "Fast."}

        with patch("src.agent.nodes.read.ChatOpenAI") as mock_read_llm, \
             patch("src.agent.nodes.write.ChatOpenAI") as mock_write_llm, \
             patch("src.agent.nodes.readback.ChatOpenAI") as mock_readback_llm:

            mock_read_llm.return_value = _make_mock_llm(json.dumps(read_out))
            mock_write_llm.return_value = _make_mock_llm(json.dumps(write_out))

            graph = build_graph(exact, semantic)
            state = _make_state(skip_readback=True)
            result = graph.invoke(state)

            assert result["translated_text"] == "Fast output."
            mock_readback_llm.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# 1b. _make_state — glossary context builder
# ═══════════════════════════════════════════════════════════════

class TestMakeState:
    def test_exact_matches_from_store(self):
        agent = TranslationAgent(book_id="test_make_state")
        agent.exact_store.add("测试", "test", category="culture")
        state = agent._make_state("Title", "这是测试内容。", 1, "", "en-US", "romance_ceo")
        assert "测试" in state["exact_matches_text"]
        assert "test" in state["exact_matches_text"]

    def test_no_match_produces_placeholder(self):
        agent = TranslationAgent(book_id="test_make_state_empty")
        state = agent._make_state("T", "No matching terms here.", 1, "", "en-US", "romance_ceo")
        assert isinstance(state["exact_matches_text"], str)

    def test_exact_glossary_dict_populated(self):
        agent = TranslationAgent(book_id="test_make_state_dict")
        agent.exact_store.add("主角", "Protagonist", category="character")
        state = agent._make_state("C1", "主角登场。", 1, "", "en-US", "romance_ceo")
        assert "主角" in state["exact_glossary"]
        assert state["exact_glossary"]["主角"] == "Protagonist"

    def test_style_memo_injected(self):
        agent = TranslationAgent(book_id="test_make_state_memo")
        agent.style_memo.record_lesson("terms", "TestTerm → TestTrans", 1)
        state = agent._make_state("C1", "text", 1, "", "en-US", "romance_ceo")
        assert "TestTerm" in state["style_memo"]

    def test_skip_readback_flag_passed(self):
        agent = TranslationAgent(book_id="test_make_state_flags")
        state = agent._make_state("T", "c", 1, "", "en-US", "romance_ceo",
                                  skip_readback=True)
        assert state["skip_readback"] is True

    def test_use_flash_writer_flag_passed(self):
        agent = TranslationAgent(book_id="test_make_state_flash")
        state = agent._make_state("T", "c", 1, "", "en-US", "romance_ceo",
                                  use_flash_writer=True)
        assert state["use_flash_writer"] is True

    def test_prefetched_glossary_used(self):
        agent = TranslationAgent(book_id="test_make_state_prefetch")
        agent._prefetched_exact = {"预取词": "PrefetchedTerm"}
        agent._prefetched_semantic = [{"term_cn": "语义词", "term_en": "SemanticTerm", "category": "culture"}]
        # Also add a term to the real store — it should NOT be looked up
        agent.exact_store.add("不该出现", "ShouldNotAppear")
        state = agent._make_state("T", "预取词 语义词", 1, "", "en-US", "romance_ceo")
        assert "预取词" in state["exact_matches_text"]
        assert "SemanticTerm" in state["semantic_matches_text"]
        assert "不该出现" not in state["exact_matches_text"]

    def test_prefetched_cleared_after_use(self):
        agent = TranslationAgent(book_id="test_make_state_clear")
        agent._prefetched_exact = {"词": "Word"}
        agent._prefetched_semantic = []
        agent._make_state("T", "词", 1, "", "en-US", "romance_ceo")
        assert agent._prefetched_exact is None
        assert agent._prefetched_semantic is None


# ═══════════════════════════════════════════════════════════════
# 1c. Node outputs (patched ChatOpenAI)
# ═══════════════════════════════════════════════════════════════

class TestReadNode:
    def test_produces_analysis_fields(self):
        from src.agent.nodes.read import read_node

        agent = TranslationAgent(book_id="test_read_out")
        analysis = {
            "emotional_arc": "Rising tension.",
            "cultural_gaps": [{"element": "鬼节", "cn_reader_gets": "fear", "en_reader_misses": "context", "bridge_strategy": "bridge", "bridge_guidance": "Use sensory detail."}],
            "image_gaps": [{"passage": "text", "cn_reader_sees": "full scene", "en_reader_gets": "thin", "priority": "critical", "sensory_anchors": "cold, silence"}],
            "terminology_decisions": [{"term_cn": "仙", "proposed_en": "Immortal", "reasoning": "fits", "cultural_note": "divine"}],
            "pacing_notes": "Good.",
            "crafted_moments": ["A reveal."],
        }

        with patch("src.agent.nodes.read.ChatOpenAI") as mock_cls:
            mock_cls.return_value = _make_mock_llm(json.dumps(analysis, ensure_ascii=False))
            state = agent._make_state("Ch1", "测试内容 鬼节 仙。", 1, "", "en-US", "folk_religion")
            result = read_node(state, agent.exact_store, agent.semantic_store)

        assert result["read_analysis"]["emotional_arc"] == "Rising tension."
        assert len(result["read_analysis"]["cultural_gaps"]) == 1
        assert len(result["read_analysis"]["image_gaps"]) == 1
        assert len(result["read_analysis"]["terminology_decisions"]) == 1
        assert len(result["image_gaps"]) == 1
        assert result["image_gaps"][0]["priority"] == "critical"

    def test_parse_fallback_on_garbage(self):
        from src.agent.nodes.read import read_node

        agent = TranslationAgent(book_id="test_read_fallback")

        with patch("src.agent.nodes.read.ChatOpenAI") as mock_cls:
            mock_cls.return_value = _make_mock_llm("NOT JSON AT ALL")
            state = agent._make_state("Ch1", "text", 1, "", "en-US", "romance_ceo")
            result = read_node(state, agent.exact_store, agent.semantic_store)

        assert "Parse failed" in result["read_analysis"]["emotional_arc"]


class TestWriteNode:
    def test_produces_translation(self):
        from src.agent.nodes.write import write_node

        response = {
            "translated_text": "She entered the grand hall.",
            "new_terms_found": [{"term_cn": "大殿", "term_en": "grand hall", "category": "location", "context": "...", "note": ""}],
            "adaptation_notes": ["Adapted architecture term."],
            "chapter_summary": "Heroine enters hall.",
        }

        with patch("src.agent.nodes.write.ChatOpenAI") as mock_cls:
            mock_cls.return_value = _make_mock_llm(json.dumps(response, ensure_ascii=False))
            state = _make_state(chapter_content="她走进大殿。")
            result = write_node(state)

        assert "She entered the grand hall" in result["translated_text"]
        assert len(result["new_terms_found"]) == 1
        assert result["new_terms_found"][0]["term_en"] == "grand hall"

    def test_empty_output_triggers_retry(self):
        from src.agent.nodes.write import write_node

        empty_resp = {"translated_text": "  ", "new_terms_found": [], "adaptation_notes": [], "chapter_summary": ""}
        valid_resp = {"translated_text": "Retry succeeded with valid content here.", "new_terms_found": [], "adaptation_notes": [], "chapter_summary": "ok"}

        with patch("src.agent.nodes.write.ChatOpenAI") as mock_cls:
            mock_llm = MagicMock()
            mock_llm.invoke.side_effect = [
                MagicMock(content=json.dumps(empty_resp), response_metadata={}),
                MagicMock(content=json.dumps(valid_resp), response_metadata={}),
            ]
            mock_cls.return_value = mock_llm
            state = _make_state()
            result = write_node(state)

        assert "Retry succeeded" in result["translated_text"]
        assert mock_llm.invoke.call_count == 2

    def test_use_flash_writer_selects_flash_model(self):
        from src.agent.nodes.write import write_node

        response = {"translated_text": "Flash output.", "new_terms_found": [], "adaptation_notes": [], "chapter_summary": "Flash."}

        with patch("src.agent.nodes.write.ChatOpenAI") as mock_cls:
            mock_cls.return_value = _make_mock_llm(json.dumps(response))
            state = _make_state(use_flash_writer=True)
            write_node(state)

        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["model"] == "deepseek-chat"

    def test_parse_fallback_layer5_raw_content(self):
        from src.agent.nodes.write import write_node

        raw_text = "She walked in. The room was dark. She felt afraid."

        with patch("src.agent.nodes.write.ChatOpenAI") as mock_cls:
            mock_cls.return_value = _make_mock_llm(raw_text)
            state = _make_state()
            result = write_node(state)

        assert "She walked in" in result["translated_text"]


class TestReadbackNode:
    def test_pass_verdict_gives_high_score(self):
        from src.agent.nodes.readback import readback_node

        fb = {"verdict": "PASS", "overall_impression": "Good.", "would_keep_reading": True, "comprehension_issues": [], "engagement_gaps": [], "standout_moments": [], "character_tracking": "", "world_comprehension": ""}

        with patch("src.agent.nodes.readback.ChatOpenAI") as mock_cls:
            mock_cls.return_value = _make_mock_llm(json.dumps(fb))
            state = _make_state(translated_text="Long enough content for evaluation. " * 10)
            result = readback_node(state)

        assert result["quality_score"] == 5.0
        assert result["readback_feedback"]["verdict"] == "PASS"

    def test_needs_fix_verdict_gives_low_score(self):
        from src.agent.nodes.readback import readback_node

        fb = {"verdict": "NEEDS_FIX", "overall_impression": "Confusing.", "would_keep_reading": False, "comprehension_issues": [{"passage": "para 3", "issue": "unclear"}], "engagement_gaps": [], "standout_moments": [], "character_tracking": "", "world_comprehension": ""}

        with patch("src.agent.nodes.readback.ChatOpenAI") as mock_cls:
            mock_cls.return_value = _make_mock_llm(json.dumps(fb))
            state = _make_state(translated_text="Long enough content for evaluation. " * 10)
            result = readback_node(state)

        assert result["quality_score"] == 2.0
        assert len(result["quality_issues"]) > 0

    def test_short_chapter_bypasses_llm(self):
        from src.agent.nodes.readback import readback_node

        with patch("src.agent.nodes.readback.ChatOpenAI") as mock_cls:
            state = _make_state(translated_text="Hi")
            result = readback_node(state)

        mock_cls.assert_not_called()
        assert result["quality_score"] == 0.0
        assert result["readback_feedback"]["verdict"] == "NEEDS_FIX"

    def test_parse_fallback_is_needs_fix(self):
        from src.agent.nodes.readback import readback_node

        with patch("src.agent.nodes.readback.ChatOpenAI") as mock_cls:
            mock_cls.return_value = _make_mock_llm("GARBAGE NOT JSON")
            state = _make_state(translated_text="Long enough content for evaluation. " * 10)
            result = readback_node(state)

        assert result["readback_feedback"]["verdict"] == "NEEDS_FIX"
        assert result["readback_feedback"]["would_keep_reading"] is False


class TestFixNode:
    def test_produces_polished_text(self):
        from src.agent.nodes.fix import fix_node

        fix_resp = {"polished_text": "Fixed version of the chapter.", "changes_made": ["Fixed pronoun in paragraph 2."]}

        with patch("src.agent.nodes.fix.ChatOpenAI") as mock_cls:
            mock_cls.return_value = _make_mock_llm(json.dumps(fix_resp))
            state = _make_state(
                translated_text="Original version of the chapter.",
                readback_feedback={"verdict": "NEEDS_FIX", "comprehension_issues": [{"passage": "p2", "issue": "pronoun unclear"}]},
            )
            result = fix_node(state)

        assert "Fixed version" in result["translated_text"]
        assert len(result["adaptation_notes"]) == 1
        assert result["quality_issues"] == []

    def test_parse_fallback_uses_raw_content(self):
        from src.agent.nodes.fix import fix_node

        raw = "Edited content directly."

        with patch("src.agent.nodes.fix.ChatOpenAI") as mock_cls:
            mock_cls.return_value = _make_mock_llm(raw)
            state = _make_state(translated_text="Original.")
            result = fix_node(state)

        assert result["translated_text"] == raw


# ═══════════════════════════════════════════════════════════════
# 1d. parse_llm_json — shared utility
# ═══════════════════════════════════════════════════════════════

class TestParseLlmJson:
    def test_strict_json(self):
        data = {"key": "value", "num": 42}
        result, is_fb = parse_llm_json(json.dumps(data), {"fallback": True})
        assert result == data
        assert not is_fb

    def test_code_fence_stripped(self):
        data = {"a": 1}
        content = '```json\n{"a": 1}\n```'
        result, is_fb = parse_llm_json(content, {})
        assert result == data
        assert not is_fb

    def test_code_fence_no_closing(self):
        content = '```json\n{"a": 1}'
        result, is_fb = parse_llm_json(content, {})
        assert result == {"a": 1}

    def test_regex_extraction_from_chatter(self):
        content = 'Sure, here is the output:\n\n{"x": "y"}\n\nHope that helps!'
        result, is_fb = parse_llm_json(content, {})
        assert result == {"x": "y"}
        assert is_fb

    def test_fallback_returned_on_garbage(self):
        fallback = {"error": "parse failed"}
        result, is_fb = parse_llm_json("NOT JSON AT ALL", fallback)
        assert result is fallback
        assert is_fb

    def test_empty_string_returns_fallback(self):
        fallback = {"default": True}
        result, is_fb = parse_llm_json("", fallback)
        assert result is fallback
        assert is_fb

    def test_none_content_returns_fallback(self):
        fallback = {"default": True}
        result, is_fb = parse_llm_json(None, fallback)
        assert result is fallback
        assert is_fb

    def test_escaped_characters_preserved(self):
        data = {"text": 'Line 1\nLine 2 with "quotes"'}
        content = json.dumps(data)
        result, _ = parse_llm_json(content, {})
        assert "Line 1" in result["text"]
        assert "Line 2" in result["text"]


# ═══════════════════════════════════════════════════════════════
# 1e. _post_process — term persistence
# ═══════════════════════════════════════════════════════════════

class TestPostProcess:
    def test_persists_character_terms_to_exact_store(self):
        agent = TranslationAgent(book_id="test_pp_exact")
        agent._post_process({
            "new_terms_found": [
                {"term_cn": "张三", "term_en": "Zhang San", "category": "character", "context": "...", "note": ""},
                {"term_cn": "李四", "term_en": "Li Si", "category": "character", "context": "...", "note": ""},
            ],
            "read_analysis": {},
            "readback_feedback": {},
            "chapter_number": 1,
        }, "en-US")

        assert "张三" in agent.exact_store.to_dict()
        assert "李四" in agent.exact_store.to_dict()

    def test_persists_location_terms_to_exact_store(self):
        agent = TranslationAgent(book_id="test_pp_location")
        agent._post_process({
            "new_terms_found": [
                {"term_cn": "桃花村", "term_en": "Peach Blossom Village", "category": "location"},
            ],
            "read_analysis": {},
            "readback_feedback": {},
            "chapter_number": 2,
        }, "en-US")
        assert "桃花村" in agent.exact_store.to_dict()

    def test_skips_culture_terms_in_exact_store(self):
        agent = TranslationAgent(book_id="test_pp_skip_culture")
        agent._post_process({
            "new_terms_found": [
                {"term_cn": "灵力", "term_en": "Spiritual Energy", "category": "technique"},
            ],
            "read_analysis": {},
            "readback_feedback": {},
            "chapter_number": 3,
        }, "en-US")
        assert "灵力" not in agent.exact_store.to_dict()

    def test_merges_cultural_notes_from_read_analysis(self):
        agent = TranslationAgent(book_id="test_pp_merge_notes")
        result = agent._post_process({
            "new_terms_found": [
                {"term_cn": "仙", "term_en": "Immortal", "category": "character"},
            ],
            "read_analysis": {
                "terminology_decisions": [
                    {"term_cn": "仙", "proposed_en": "Immortal", "cultural_note": "A being who transcended mortality through cultivation."},
                ],
            },
            "readback_feedback": {},
            "chapter_number": 4,
        }, "en-US")
        terms = result.get("new_terms_found", [])
        term = next((t for t in terms if t["term_cn"] == "仙"), None)
        assert term is not None
        assert "transcended mortality" in term.get("note", "")

    def test_updates_style_memo_from_feedback(self):
        agent = TranslationAgent(book_id="test_pp_memo")
        agent._post_process({
            "new_terms_found": [],
            "read_analysis": {},
            "readback_feedback": {
                "verdict": "PASS",
                "engagement_gaps": [
                    {"passage": "long exposition paragraph", "issue": "Too much exposition — got bored."},
                ],
            },
            "chapter_number": 5,
        }, "en-US")
        memo = agent.style_memo.read_all()
        assert "exposition" in memo.lower()

    def test_glossary_snapshot_generated(self):
        agent = TranslationAgent(book_id="test_pp_snapshot")
        agent.exact_store.add("词", "Word", category="culture")
        result = agent._post_process({
            "new_terms_found": [],
            "read_analysis": {},
            "readback_feedback": {},
            "chapter_number": 6,
        }, "en-US")
        snapshot = result.get("glossary_snapshot_json", "")
        assert snapshot
        parsed = json.loads(snapshot)
        assert "词" in parsed


# ═══════════════════════════════════════════════════════════════
# 1f. Chapter auto-split
# ═══════════════════════════════════════════════════════════════

class TestChapterSlicer:
    def test_should_split_above_threshold(self):
        long_chapter = "测试" * 2300
        assert should_split(long_chapter)

    def test_should_split_below_threshold(self):
        short_chapter = "测试" * 100
        assert not should_split(short_chapter)

    def test_should_split_edge_at_threshold(self):
        edge_chapter = "测试" * 2249
        assert not should_split(edge_chapter)

    def test_split_chapter_produces_multiple_segments(self):
        content = "测试第一段。\n\n测试第二段。\n\n" * 500
        segments = split_chapter(content)
        assert len(segments) > 1

    def test_split_chapter_last_segment_flagged(self):
        content = "测试第一段。\n\n" * 600
        segments = split_chapter(content)
        assert segments[-1]["is_last"]

    def test_split_chapter_short_content_single_segment(self):
        segments = split_chapter("短内容。")
        assert len(segments) == 1
        assert segments[0]["is_last"]
