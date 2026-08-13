"""Core-path test: glossary accumulation and injection across multiple chapters.

When translating a book, each chapter's new terms (characters, locations,
culture terms) must be injected into the NEXT chapter's translation prompt.
This is the mechanism that prevents "name drift" — where the same character
gets different English names in different chapters.

The pipeline:
  Chapter 1 → READ → WRITE → READBACK → _post_process
    → new terms written to exact_store + semantic_store
  Chapter 2 → _make_state
    → exact_store.match_in_text(ch2_content) → injected as exact_matches_text
    → semantic_store.search(ch2_content) → injected as semantic_matches_text
    → style_memo.read_relevant() → injected as style_memo

This test verifies the entire accumulation chain with mocked LLMs.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.agent.graph import TranslationAgent


# ── Mock LLM outputs for two consecutive chapters ────────────────────

CH1_READ = json.dumps({
    "emotional_arc": "Introduction: heroine discovers transmigration.",
    "cultural_gaps": [
        {"element": "穿书", "bridge_strategy": "analogy",
         "bridge_guidance": "Frame as 'waking up inside a novel she read'"}
    ],
    "terminology_decisions": [
        {"term_cn": "苏念", "proposed_en": "Su Nian", "category": "character",
         "reasoning": "Protagonist name — keep Pinyin", "cultural_note": ""},
        {"term_cn": "裴衍舟", "proposed_en": "Pei Yanzhou", "category": "character",
         "reasoning": "Male lead — keep Pinyin", "cultural_note": ""},
        {"term_cn": "霸总", "proposed_en": "Alpha CEO", "category": "culture",
         "reasoning": "Dominant male archetype", "cultural_note": "Romance shorthand"},
        {"term_cn": "裴氏别墅", "proposed_en": "Pei Family Villa", "category": "location",
         "reasoning": "Primary setting", "cultural_note": ""},
    ],
    "pacing_notes": "Cold open with system notification hook.",
    "crafted_moments": [],
    "image_gaps": [{"priority": "high", "description": "Villa interior"}]
})

CH1_WRITE = json.dumps({
    "translated_text": (
        "Su Nian opened her eyes. She was lying on a massive bed in a room "
        "that looked like a five-star hotel suite. The Pei Family Villa was "
        "everything the novel had promised — and more.\n\n"
        "A mechanical chime echoed in her skull.\n\n"
        "[System notification: Host bound successfully.]\n\n"
        "She had transmigrated into the CEO romance novel she'd been reading "
        "before she fell asleep. And standing in the doorway was Pei Yanzhou — "
        "the Alpha CEO himself."
    ),
    "chapter_title_en": "The Transmigration",
    "new_terms_found": [
        {"term_cn": "苏念", "term_en": "Su Nian", "category": "character"},
        {"term_cn": "裴衍舟", "term_en": "Pei Yanzhou", "category": "character"},
        {"term_cn": "霸总", "term_en": "Alpha CEO", "category": "culture"},
        {"term_cn": "裴氏别墅", "term_en": "Pei Family Villa", "category": "location"},
    ],
    "adaptation_notes": ["'穿书' → 'transmigrated into the novel' for Western isekai readers"],
    "chapter_summary": "Su Nian transmigrates into a CEO romance novel and meets Pei Yanzhou."
})

CH1_READBACK = json.dumps({
    "verdict": "PASS",
    "comprehension_issues": [],
    "engagement_gaps": [],
    "overall_impression": "Smooth opening. Su Nian is immediately sympathetic. "
                         "Pei Yanzhou's entrance is cinematic. Feels native.",
    "quality_score": 8.0
})

CH2_READ = json.dumps({
    "emotional_arc": "Rising tension: heroine confronts the CEO.",
    "cultural_gaps": [],
    "terminology_decisions": [
        {"term_cn": "契约", "proposed_en": "marriage contract", "category": "culture",
         "reasoning": "Contract marriage trope — common in CEO romance"}
    ],
    "pacing_notes": "Dialogue-driven, fast-paced negotiation scene.",
    "crafted_moments": [],
    "image_gaps": []
})

CH2_WRITE = json.dumps({
    "translated_text": (
        "Pei Yanzhou slid a document across the mahogany desk.\n\n"
        "\"A marriage contract,\" he said. \"Three months. Then you walk away "
        "with the money.\"\n\n"
        "Su Nian picked up the paper. She'd read this scene before — in every "
        "CEO romance novel she'd ever devoured. The difference was, this time "
        "she was living it.\n\n"
        "\"No,\" she said.\n\n"
        "Pei Yanzhou's eyes narrowed. That was not the answer he'd expected "
        "from a woman whose family owed him everything."
    ),
    "chapter_title_en": "The Contract",
    "new_terms_found": [
        {"term_cn": "契约", "term_en": "marriage contract", "category": "culture"},
    ],
    "adaptation_notes": [],
    "chapter_summary": "Su Nian refuses Pei Yanzhou's marriage contract."
})

CH2_READBACK = json.dumps({
    "verdict": "PASS",
    "comprehension_issues": [],
    "engagement_gaps": [],
    "overall_impression": "The refusal is a perfect hook. Names are consistent. "
                         "Contract trope is instantly understandable.",
    "quality_score": 8.5
})


def _mock_llm(response_str: str):
    resp = MagicMock()
    resp.content = response_str
    resp.response_metadata = {"token_usage": {"prompt_tokens": 500, "completion_tokens": 300}}
    llm = MagicMock()
    llm.invoke.return_value = resp
    return llm


class TestGlossaryAccumulation:
    """Verify that terms from chapter 1 are injected into chapter 2."""

    def test_character_names_propagate_to_next_chapter(self):
        """Su Nian + Pei Yanzhou from ch1 must appear in ch2's _make_state."""
        agent = TranslationAgent(book_id="test_character_names_propagate")

        ch1 = {
            "title": "第一章 穿书",
            "content": (
                "苏念睁开眼睛，发现自己躺在一张陌生的大床上。"
                "门外传来脚步声，裴衍舟走了进来。西装笔挺。"
                "霸总果然如小说里写的一样。这就是裴氏别墅。"
            ),
            "number": 1,
        }

        ch2 = {
            "title": "第二章 契约",
            "content": (
                "苏念走下楼。裴衍舟已经坐在客厅里了，面前放着一份文件。"
                "\"签了这个契约。\"他说。"
                "\"不。\"苏念把文件推了回去。"
                "裴衍舟的眼神暗了暗。这个霸总不习惯被拒绝。"
            ),
            "number": 2,
        }

        read_llm_1 = _mock_llm(CH1_READ)
        write_llm_1 = _mock_llm(CH1_WRITE)
        readback_llm_1 = _mock_llm(CH1_READBACK)

        with patch("src.agent.nodes.read.ChatOpenAI", return_value=read_llm_1), \
             patch("src.agent.nodes.write.ChatOpenAI", return_value=write_llm_1), \
             patch("src.agent.nodes.readback.ChatOpenAI", return_value=readback_llm_1):
            r1 = agent.translate_chapter(
                chapter_title=ch1["title"], chapter_content=ch1["content"],
                chapter_number=1, skip_readback=False, content_type="novel",
            )

        assert r1["translated_text"], "Ch1 must produce output"
        assert "Su Nian" in r1["translated_text"]
        assert "Pei Yanzhou" in r1["translated_text"]
        assert "Alpha CEO" in r1["translated_text"]
        # Verify terms were written to stores
        assert agent.exact_store.get("苏念") == "Su Nian"
        assert agent.exact_store.get("裴衍舟") == "Pei Yanzhou"
        assert agent.exact_store.get("裴氏别墅") == "Pei Family Villa"
        # Culture term goes to semantic store only (not exact)
        assert agent.exact_store.get("霸总") is None

        # ── Translate chapter 2 ──────────────────────────────────
        read_llm_2 = _mock_llm(CH2_READ)
        write_llm_2 = _mock_llm(CH2_WRITE)
        readback_llm_2 = _mock_llm(CH2_READBACK)

        with patch("src.agent.nodes.read.ChatOpenAI", return_value=read_llm_2), \
             patch("src.agent.nodes.write.ChatOpenAI", return_value=write_llm_2), \
             patch("src.agent.nodes.readback.ChatOpenAI", return_value=readback_llm_2):
            r2 = agent.translate_chapter(
                chapter_title=ch2["title"], chapter_content=ch2["content"],
                chapter_number=2, skip_readback=False, content_type="novel",
            )

        assert r2["translated_text"], "Ch2 must produce output"
        # Ch2 inherited names from ch1 — they must be consistent
        assert "Su Nian" in r2["translated_text"]
        assert "Pei Yanzhou" in r2["translated_text"]
        # New term from ch2
        assert "contract" in r2["translated_text"].lower()

    def test_exact_matches_text_injected_into_ch2_state(self):
        """_make_state for ch2 must include ch1's exact matches in the prompt text."""
        agent = TranslationAgent(book_id="test_exact_matches_injected")

        # Seed the exact store as if chapter 1 already ran
        agent.exact_store.add("苏念", "Su Nian", category="character")
        agent.exact_store.add("裴衍舟", "Pei Yanzhou", category="character")
        agent.exact_store.add("裴氏别墅", "Pei Family Villa", category="location")

        ch2_content = "苏念走下楼。裴衍舟在客厅等她。契约摆在茶几上。"

        state = agent._make_state(
            title="第二章 契约", content=ch2_content, number=2,
            prev_summary="Su Nian transmigrates and meets Pei Yanzhou.",
            lang="en-US", genre="romance_ceo",
        )

        exact_matches_text = state["exact_matches_text"]
        # Must contain all three terms that appear in ch2_content
        assert "苏念" in exact_matches_text
        assert "Su Nian" in exact_matches_text
        assert "裴衍舟" in exact_matches_text
        assert "Pei Yanzhou" in exact_matches_text

        # 裴氏别墅 does NOT appear in ch2_content → should NOT be matched
        # (match_in_text only returns terms whose CN appears in the chapter)
        assert "裴氏别墅" not in exact_matches_text

    def test_style_memo_accumulates_across_chapters(self):
        """After ch1, style_memo must contain ch1's terminology decisions.

        The style memo is injected into _make_state via style_memo.read_relevant(),
        so ch2's READ agent sees ch1's accumulated translation wisdom.
        """
        # Use an isolated book_id so style_memo doesn't collide with other
        # tests sharing the default book directory (fingerprint dedup would
        # otherwise skip entries written by an earlier test).
        agent = TranslationAgent(book_id="test_style_memo_accumulation")

        ch1 = {
            "title": "第一章", "number": 1,
            "content": "苏念穿越了。她变成了霸总文女主。裴衍舟是男主角。",
        }

        read_llm = _mock_llm(CH1_READ)
        write_llm = _mock_llm(CH1_WRITE)
        readback_llm = _mock_llm(CH1_READBACK)

        with patch("src.agent.nodes.read.ChatOpenAI", return_value=read_llm), \
             patch("src.agent.nodes.write.ChatOpenAI", return_value=write_llm), \
             patch("src.agent.nodes.readback.ChatOpenAI", return_value=readback_llm):
            agent.translate_chapter(
                chapter_title=ch1["title"], chapter_content=ch1["content"],
                chapter_number=1, skip_readback=False, content_type="novel",
            )

        memo = agent.style_memo.read_all()
        # After ch1, memo must contain the terminology decisions
        assert "Su Nian" in memo
        assert "Pei Yanzhou" in memo
        assert "Alpha CEO" in memo
        # Pacing notes from READ analysis
        assert "hook" in memo.lower() or "notification" in memo.lower()
        # Cultural bridge from READ analysis
        assert "穿书" in memo or "transmigration" in memo.lower()

    def test_cold_read_context_accumulates_summaries(self):
        """After ch1, ch2's cold_read_context must reference ch1's summary."""
        agent = TranslationAgent(book_id="test_cold_read_context")

        ch1 = {
            "title": "第一章", "number": 1,
            "content": "苏念穿越了。她变成了霸总文女主。",
        }

        read_llm = _mock_llm(CH1_READ)
        write_llm = _mock_llm(CH1_WRITE)
        readback_llm = _mock_llm(CH1_READBACK)

        with patch("src.agent.nodes.read.ChatOpenAI", return_value=read_llm), \
             patch("src.agent.nodes.write.ChatOpenAI", return_value=write_llm), \
             patch("src.agent.nodes.readback.ChatOpenAI", return_value=readback_llm):
            agent.translate_chapter(
                chapter_title=ch1["title"], chapter_content=ch1["content"],
                chapter_number=1, skip_readback=False, content_type="novel",
            )

        # Build state for ch2 — cold_read_context should include ch1 summary
        state = agent._make_state(
            title="第二章", content="新的章节内容。",
            number=2, prev_summary="", lang="en-US", genre="romance_ceo",
        )
        context = state["cold_read_context"]
        assert "PREVIOUSLY" in context
        assert "Pei Yanzhou" in context or "Su Nian" in context

    def test_prefetched_glossary_used_for_ch2(self):
        """When prefetched results exist, _make_state uses them (skips blocking lookup)."""
        agent = TranslationAgent(book_id="test_prefetched_glossary")
        # Set prefetched data as if ChapterPrefetcher already ran
        agent.set_prefetched_glossary(
            {"苏念": "Su Nian", "裴衍舟": "Pei Yanzhou"},
            [{"term_cn": "霸总", "term_en": "Alpha CEO", "category": "culture"}],
        )

        state = agent._make_state(
            title="第二章", content="苏念和裴衍舟对峙。霸总的威严不容挑战。",
            number=2, prev_summary="", lang="en-US", genre="romance_ceo",
        )

        # Prefetch was consumed (flag cleared)
        assert agent._prefetched_exact is None
        assert agent._prefetched_semantic is None
        # Exact matches from prefetch
        assert "Su Nian" in state["exact_matches_text"]
        assert "Pei Yanzhou" in state["exact_matches_text"]
        # Semantic matches from prefetch (霸总 not in exact, so it appears)
        assert "Alpha CEO" in state["semantic_matches_text"]

    def test_forced_accept_logged_when_readback_fails_after_max_retries(self, caplog):
        """FORCED_ACCEPT must log a warning when NEEDS_FIX persists after 2 retries."""
        agent = TranslationAgent(book_id="test_forced_accept")

        ch1 = {
            "title": "第一章", "number": 1,
            "content": "苏念穿越了。" * 10,
        }

        needs_fix = json.dumps({
            "verdict": "NEEDS_FIX",
            "comprehension_issues": [{"passage": "x", "issue": "Confusing"}],
            "engagement_gaps": [{"passage": "y", "issue": "Boring"}],
            "overall_impression": "Bad.",
            "quality_score": 2.0
        })
        fix_out = json.dumps({
            "translated_text": "Fixed version.",
            "adaptation_notes": ["Fixed"],
            "chapter_summary": "ok"
        })

        read_llm = _mock_llm(CH1_READ)
        write_llm = _mock_llm(CH1_WRITE)
        readback_llm = _mock_llm(needs_fix)
        fix_llm = _mock_llm(fix_out)

        import logging
        with caplog.at_level(logging.WARNING):
            with patch("src.agent.nodes.read.ChatOpenAI", return_value=read_llm), \
                 patch("src.agent.nodes.write.ChatOpenAI", return_value=write_llm), \
                 patch("src.agent.nodes.readback.ChatOpenAI", return_value=readback_llm), \
                 patch("src.agent.nodes.fix.ChatOpenAI", return_value=fix_llm):
                agent.translate_chapter(
                    chapter_title=ch1["title"], chapter_content=ch1["content"],
                    chapter_number=1, skip_readback=False, content_type="novel",
                )

        forced_accept_logs = [r for r in caplog.records if "FORCED_ACCEPT" in r.message]
        assert len(forced_accept_logs) >= 1, "FORCED_ACCEPT must be logged when FIX can't satisfy READBACK"
