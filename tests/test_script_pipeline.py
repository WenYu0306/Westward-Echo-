"""Core-path test: content_type="script" full 4-node pipeline end-to-end.

The script pipeline is the second content_type branch (v0.16). It uses a
different PromptSet (script_read/write/readback/fix) and a different splitter
(script_splitter.split_episodes) but the same LangGraph graph, same JSON
schemas, and same parse fallbacks as the novel path.

This test verifies:
1. get_prompt_set("script") returns the SCRIPT_PROMPTS, not NOVEL_PROMPTS
2. Each script-* prompt file contains the content_type-specific persona
3. The 4-node graph runs end-to-end with script content and mocked LLM
4. Output format is screenplay (Episode N / Scene N headers, UPPERCASE speakers)
5. script_mode="dialogue" post-filters output to spoken lines only
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.agent.graph import TranslationAgent, build_graph
from src.agent.prompts.registry import NOVEL_PROMPTS, SCRIPT_PROMPTS, get_prompt_set
from src.agent.state import TranslatorState


# ═══════════════════════════════════════════════════════════════════
# Prompt selection — content_type branching
# ═══════════════════════════════════════════════════════════════════

class TestScriptPromptSelection:
    def test_script_returns_different_prompts_than_novel(self):
        novel = get_prompt_set("novel")
        script = get_prompt_set("script")
        assert script is not novel
        assert script is SCRIPT_PROMPTS
        assert novel is NOVEL_PROMPTS

    def test_script_read_persona_is_short_drama_viewer(self):
        ps = get_prompt_set("script")
        assert "short-drama" in ps.read_system.lower()
        assert "vertical" in ps.read_system.lower() or "Douyin" in ps.read_system

    def test_script_write_persona_is_screenwriter(self):
        ps = get_prompt_set("script")
        assert "screenplay" in ps.write_system.lower() or "screenwriter" in ps.write_system.lower() or "script" in ps.write_system.lower()

    def test_script_readback_persona_is_phone_scroller(self):
        ps = get_prompt_set("script")
        combined = (ps.readback_system + ps.readback_user).lower()
        assert "phone" in combined or "scroll" in combined or "mobile" in combined or "short" in combined

    def test_script_fix_is_script_doctor(self):
        ps = get_prompt_set("script")
        combined = (ps.fix_system + ps.fix_user).lower()
        assert "doctor" in combined or "fix" in combined or "editor" in combined or "script" in combined

    def test_game_falls_back_to_novel(self):
        assert get_prompt_set("game") is NOVEL_PROMPTS

    def test_unknown_falls_back_to_novel(self):
        assert get_prompt_set("bogus") is NOVEL_PROMPTS
        assert get_prompt_set("") is NOVEL_PROMPTS

    def test_all_script_prompts_share_placeholder_signature(self):
        """Every script prompt .format() must accept the same placeholders as novel.

        Failure here means the script branch would crash at runtime because
        node code calls .format() with the same kwargs regardless of content_type.
        """
        novel = get_prompt_set("novel")
        script = get_prompt_set("script")

        # Extract placeholder names from each template
        import re
        def placeholders(template: str) -> set:
            return set(re.findall(r'\{(\w+)\}', template))

        novel_read = placeholders(novel.read_user)
        script_read = placeholders(script.read_user)
        assert novel_read == script_read, f"read_user mismatch: {novel_read ^ script_read}"

        novel_write = placeholders(novel.write_user)
        script_write = placeholders(script.write_user)
        assert novel_write == script_write, f"write_user mismatch: {novel_write ^ script_write}"

        novel_rb = placeholders(novel.readback_user)
        script_rb = placeholders(script.readback_user)
        assert novel_rb == script_rb, f"readback_user mismatch: {novel_rb ^ script_rb}"

        novel_fix = placeholders(novel.fix_user)
        script_fix = placeholders(script.fix_user)
        assert novel_fix == script_fix, f"fix_user mismatch: {novel_fix ^ script_fix}"


# ═══════════════════════════════════════════════════════════════════
# Full 4-node graph with script content — mocked LLM
# ═══════════════════════════════════════════════════════════════════

# Mock LLM outputs that look like screenplay format
SCRIPT_READ_OUTPUT = json.dumps({
    "emotional_arc": "Opening hook: mysterious arrival. Rising tension through confrontation.",
    "cultural_gaps": [
        {
            "element": "穿书 trope",
            "bridge_strategy": "analogy",
            "bridge_guidance": "Frame as 'waking up inside a TV show' — ReelShort viewers know this."
        }
    ],
    "terminology_decisions": [
        {
            "term_cn": "霸总", "proposed_en": "Alpha CEO",
            "category": "character", "reasoning": "Wealthy dominant male lead archetype",
            "cultural_note": "Short-drama shorthand for ultra-rich controlling love interest"
        },
        {
            "term_cn": "苏念", "proposed_en": "Su Nian",
            "category": "character", "reasoning": "Keep Pinyin — protagonist name, no cultural equivalent"
        }
    ],
    "pacing_notes": "Episode opens with a hook in scene 1. Dialogue-driven, fast cuts.",
    "crafted_moments": [],
    "image_gaps": [
        {"priority": "high", "description": "别墅 interior — need a visual anchor for luxury"}
    ]
})

SCRIPT_WRITE_OUTPUT_EP1 = json.dumps({
    "translated_text": (
        "Episode 1: Transmigrated into the CEO's Wife\n\n"
        "Scene 1: PEI FAMILY VILLA — MASTER BEDROOM / NIGHT\n\n"
        "Su Nian's eyes snap open. She's lying on a massive bed in a room "
        "that looks like a five-star hotel suite. Floor-to-ceiling windows "
        "frame the city skyline.\n\n"
        "SU NIAN: Where... where am I?\n\n"
        "A mechanical chime echoes in her head.\n\n"
        "SYSTEM (V.O.): Ding — Host bound successfully. Current affection: -50.\n\n"
        "Su Nian sits up, running her fingers through her hair. Her reflection "
        "in the window shows a face she recognizes — but the designer clothes "
        "are definitely not hers.\n\n"
        "SU NIAN (OS): I transmigrated. Into a CEO romance novel. Great.\n\n"
        "The door opens. PEI YANZHOU enters — tall, perfectly tailored suit, "
        "eyes cold as winter. He stops when he sees her awake.\n\n"
        "PEI YANZHOU: You're awake. Good. We have a contract to discuss.\n\n"
        "SU NIAN: A contract?\n\n"
        "PEI YANZHOU: Our marriage. Three months. Then you leave with the money.\n\n"
        "Su Nian stares at him. Then she laughs — not the reaction he expected.\n\n"
        "SU NIAN: No.\n\n"
        "【End of Episode 1】\n"
    ),
    "chapter_title_en": "Transmigrated into the CEO's Wife",
    "new_terms_found": [
        {"term_cn": "裴衍舟", "term_en": "Pei Yanzhou", "category": "character"}
    ],
    "adaptation_notes": ["SYSTEM (V.O.) pattern kept — LitRPG convention familiar to Western audiences"],
    "chapter_summary": "Su Nian transmigrates and refuses the CEO's contract."
})

SCRIPT_READBACK_OUTPUT = json.dumps({
    "verdict": "PASS",
    "comprehension_issues": [],
    "engagement_gaps": [],
    "overall_impression": "Hooked immediately. The transmigration setup is clear and the "
                         "refusal at the end makes me want to swipe to the next episode. "
                         "Dialogue is sharp. Feels native, not translated.",
    "quality_score": 8.5
})


@pytest.fixture
def script_chapter():
    return {
        "title": "第1集 穿成霸总文女主",
        "content": (
            "场景1：裴家别墅-主卧/夜\n\n"
            "苏念睁开眼睛，发现自己躺在一张陌生的大床上。落地窗外是城市夜景。\n\n"
            "【叮——宿主绑定成功，当前好感度：-50】\n\n"
            "苏念坐起身来。她穿越了，穿进了一本霸总文。\n\n"
            "门开了，裴衍舟走了进来。西装笔挺，眼神冰冷。\n\n"
            "裴衍舟：醒了？正好，我们有份契约要谈。\n\n"
            "苏念：什么契约？\n\n"
            "裴衍舟：我们的婚姻。三个月，结束，你拿钱走人。\n\n"
            "苏念盯着他看了三秒，然后笑了。\n\n"
            "苏念：不。\n"
        ),
        "number": 1,
    }


def _mock_llm(response_str: str):
    """Build a mock ChatOpenAI that returns the given JSON string."""
    resp = MagicMock()
    resp.content = response_str
    resp.response_metadata = {"token_usage": {"prompt_tokens": 500, "completion_tokens": 300}}
    llm = MagicMock()
    llm.invoke.return_value = resp
    return llm


class TestScriptPipelineE2E:
    """Run the full 4-node graph with content_type='script' and mocked LLM."""

    def test_graph_runs_with_script_content_type(self, script_chapter):
        """The 4-node graph must complete without error when content_type='script'.

        Verifies that the script prompts are selected and that their .format()
        calls don't crash (placeholder signatures match node code).
        """
        read_llm = _mock_llm(SCRIPT_READ_OUTPUT)
        write_llm = _mock_llm(SCRIPT_WRITE_OUTPUT_EP1)
        readback_llm = _mock_llm(SCRIPT_READBACK_OUTPUT)

        agent = TranslationAgent()

        with patch("src.agent.nodes.read.ChatOpenAI", return_value=read_llm), \
             patch("src.agent.nodes.write.ChatOpenAI", return_value=write_llm), \
             patch("src.agent.nodes.readback.ChatOpenAI", return_value=readback_llm):
            result = agent.translate_chapter(
                chapter_title=script_chapter["title"],
                chapter_content=script_chapter["content"],
                chapter_number=1,
                previous_summary="",
                target_lang="en-US",
                genre="romance_ceo",
                skip_readback=False,  # Full pipeline: READ→WRITE→READBACK→END
                content_type="script",
            )

        assert result["translated_text"], "Output must not be empty"
        assert "Episode 1" in result["translated_text"]
        assert "Scene 1" in result["translated_text"]
        assert "SU NIAN" in result["translated_text"]
        assert "PEI YANZHOU" in result["translated_text"]
        assert result["chapter_summary"], "Summary must not be empty"
        # READBACK maps PASS → 5.0, NEEDS_FIX → 2.0 (binary gate, not the LLM's raw score)
        assert result["quality_score"] == 5.0

    def test_script_readback_needs_fix_triggers_fix_node(self, script_chapter):
        """When READBACK says NEEDS_FIX and retries < 2, FIX node must run."""
        read_llm = _mock_llm(SCRIPT_READ_OUTPUT)
        write_llm = _mock_llm(SCRIPT_WRITE_OUTPUT_EP1)

        needs_fix = json.dumps({
            "verdict": "NEEDS_FIX",
            "comprehension_issues": [
                {"passage": "SYSTEM (V.O.)", "issue": "What is this voice?"}
            ],
            "engagement_gaps": [],
            "overall_impression": "Confused by the system voice.",
            "quality_score": 3.0
        })

        fix_output = json.dumps({
            "polished_text": json.loads(SCRIPT_WRITE_OUTPUT_EP1)["translated_text"].replace(
                "SYSTEM (V.O.)", "SYSTEM VOICE"
            ),
            "changes_made": ["Clarified SYSTEM as 'SYSTEM VOICE'"],
        })

        readback_llm = _mock_llm(needs_fix)
        fix_llm = _mock_llm(fix_output)

        agent = TranslationAgent()

        with patch("src.agent.nodes.read.ChatOpenAI", return_value=read_llm), \
             patch("src.agent.nodes.write.ChatOpenAI", return_value=write_llm), \
             patch("src.agent.nodes.readback.ChatOpenAI", return_value=readback_llm), \
             patch("src.agent.nodes.fix.ChatOpenAI", return_value=fix_llm):
            result = agent.translate_chapter(
                chapter_title=script_chapter["title"],
                chapter_content=script_chapter["content"],
                chapter_number=1,
                previous_summary="",
                target_lang="en-US",
                skip_readback=False,
                content_type="script",
            )
        # The FIX node should have replaced "SYSTEM (V.O.)" → "SYSTEM VOICE"
        assert "SYSTEM VOICE" in result["translated_text"]
        # FIX ran twice (READBACK→FIX→READBACK→FIX), both times READBACK
        # returned NEEDS_FIX, so retranslation_count=2 at forced accept.
        assert result["retranslation_count"] == 2

    def test_script_fast_mode_skips_readback(self, script_chapter):
        """skip_readback=True → READ→WRITE→END, no READBACK or FIX."""
        read_llm = _mock_llm(SCRIPT_READ_OUTPUT)
        write_llm = _mock_llm(SCRIPT_WRITE_OUTPUT_EP1)

        agent = TranslationAgent()
        with patch("src.agent.nodes.read.ChatOpenAI", return_value=read_llm), \
             patch("src.agent.nodes.write.ChatOpenAI", return_value=write_llm):
            result = agent.translate_chapter(
                chapter_title=script_chapter["title"],
                chapter_content=script_chapter["content"],
                chapter_number=1,
                skip_readback=True,
                content_type="script",
            )
        assert result["translated_text"]
        assert result["readback_feedback"] == {}  # Never called


# ═══════════════════════════════════════════════════════════════════
# script_mode="dialogue" — output post-filter
# ═══════════════════════════════════════════════════════════════════

class TestScriptDialogueMode:
    def test_dialogue_mode_filters_action_lines(self, script_chapter):
        """script_mode='dialogue' must return only spoken lines + scene headers."""
        read_llm = _mock_llm(SCRIPT_READ_OUTPUT)
        write_llm = _mock_llm(SCRIPT_WRITE_OUTPUT_EP1)

        agent = TranslationAgent()
        with patch("src.agent.nodes.read.ChatOpenAI", return_value=read_llm), \
             patch("src.agent.nodes.write.ChatOpenAI", return_value=write_llm):
            result = agent.translate_chapter(
                chapter_title=script_chapter["title"],
                chapter_content=script_chapter["content"],
                chapter_number=1,
                skip_readback=True,
                content_type="script",
                script_mode="dialogue",
            )

        text = result["translated_text"]
        # Must keep episode/scene headers and dialogue
        assert "Episode 1" in text
        assert "Scene 1" in text
        assert "SU NIAN" in text
        assert "PEI YANZHOU" in text
        # Must drop action/direction prose
        assert "snap open" not in text.lower()
        assert "five-star" not in text
        # Must drop 【】 panels
        assert "【" not in text

    def test_dialogue_mode_preserves_pre_filter_text(self, script_chapter):
        """pre_filter_text must contain the full script before dialogue filtering."""
        read_llm = _mock_llm(SCRIPT_READ_OUTPUT)
        write_llm = _mock_llm(SCRIPT_WRITE_OUTPUT_EP1)

        agent = TranslationAgent()
        with patch("src.agent.nodes.read.ChatOpenAI", return_value=read_llm), \
             patch("src.agent.nodes.write.ChatOpenAI", return_value=write_llm):
            result = agent.translate_chapter(
                chapter_title=script_chapter["title"],
                chapter_content=script_chapter["content"],
                chapter_number=1,
                skip_readback=True,
                content_type="script",
                script_mode="dialogue",
            )

        full = result["pre_filter_text"]
        assert "snap open" in full.lower()  # Action line preserved in pre_filter
        filtered = result["translated_text"]
        assert "snap open" not in filtered.lower()  # Action line removed in deliverable

    def test_dialogue_mode_novel_is_noop(self):
        """script_mode='dialogue' with content_type='novel' does nothing."""
        from src.agent.graph import TranslationAgent
        from unittest.mock import MagicMock, patch

        read_llm = _mock_llm(json.dumps({
            "emotional_arc": "Setup.", "cultural_gaps": [],
            "terminology_decisions": [], "pacing_notes": "",
            "crafted_moments": [], "image_gaps": []
        }))
        write_llm = _mock_llm(json.dumps({
            "translated_text": "She walked into the hall.\n\nIt was cold.",
            "chapter_title_en": "Chapter One",
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "She enters."
        }))

        agent = TranslationAgent()
        with patch("src.agent.nodes.read.ChatOpenAI", return_value=read_llm), \
             patch("src.agent.nodes.write.ChatOpenAI", return_value=write_llm):
            result = agent.translate_chapter(
                chapter_title="第一章",
                chapter_content="她走进了大厅。很冷。",
                chapter_number=1,
                skip_readback=True,
                content_type="novel",
                script_mode="dialogue",  # Should be ignored for novel
            )
        assert "pre_filter_text" not in result  # Only set for script
        assert "She walked" in result["translated_text"]
