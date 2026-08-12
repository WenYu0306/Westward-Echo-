"""Unit tests for style_memo.py — translation style memory store."""

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def memo_store():
    """Return a StyleMemoStore backed by a temp directory."""
    import src.style_memo as sm
    with tempfile.TemporaryDirectory() as tmpdir:
        # Patch both config.DATA_DIR AND style_memo.DATA_DIR since the latter
        # was imported at module-load time and won't see config changes.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sm, "DATA_DIR", Path(tmpdir))
            store = sm.StyleMemoStore("test_book")
            yield store


class TestStyleMemoInit:
    def test_creates_all_drawers(self, memo_store):
        for fname in ["MEMO.md", "characters.md", "pacing.md", "bridges.md",
                       "prose.md", "terms.md"]:
            assert (memo_store.root / fname).exists()

    def test_read_all_returns_content(self, memo_store):
        text = memo_store.read_all()
        assert "Character Voices" in text
        assert "Cultural Bridge Patterns" in text

    def test_read_relevant_returns_content(self, memo_store):
        text = memo_store.read_relevant()
        assert len(text) > 0

    def test_read_relevant_with_data_is_shorter(self, memo_store):
        """read_relevant should return less text than read_all on same store."""
        memo_store.record_lesson("characters", "Su Nian: sharp retorts when cornered", 1)
        all_text = memo_store.read_all()
        relevant_text = memo_store.read_relevant(max_chars=2000)
        assert len(relevant_text) <= len(all_text)


class TestRecordLesson:
    def test_record_to_characters(self, memo_store):
        memo_store.record_lesson("characters", "Su Nian: cold exterior, warm heart", 1)
        text = memo_store.read_all()
        assert "Su Nian" in text
        assert "[ch1]" in text

    def test_record_to_pacing(self, memo_store):
        memo_store.record_lesson("pacing", "Keep exposition under 3 paragraphs", 2)
        text = memo_store.read_all()
        assert "exposition" in text.lower()

    def test_record_lesson_dedup(self, memo_store):
        lesson = "Su Nian uses sharp retorts when cornered"
        memo_store.record_lesson("characters", lesson, 1)
        memo_store.record_lesson("characters", lesson, 2)
        text = memo_store.read_all()
        # Should only appear once
        assert text.count("sharp retorts") == 1

    def test_invalid_drawer_raises(self, memo_store):
        with pytest.raises(ValueError):
            memo_store.record_lesson("invalid_drawer", "lesson", 1)


class TestUpdateFromReadAnalysis:
    def test_records_terminology_decisions(self, memo_store):
        read_analysis = {
            "terminology_decisions": [
                {
                    "term_cn": "霸总", "proposed_en": "Alpha CEO",
                    "cultural_note": "Dominant male lead archetype",
                },
            ],
        }
        memo_store.update_from_read_analysis(read_analysis, 1)
        text = memo_store.read_all()
        assert "霸总" in text
        assert "Alpha CEO" in text

    def test_records_cultural_gaps(self, memo_store):
        read_analysis = {
            "cultural_gaps": [
                {
                    "element": "敬酒 ritual",
                    "bridge_strategy": "analogy",
                    "bridge_guidance": "Like a toast at a wedding",
                },
            ],
        }
        memo_store.update_from_read_analysis(read_analysis, 1)
        text = memo_store.read_all()
        assert "敬酒" in text

    def test_records_pacing_notes(self, memo_store):
        read_analysis = {
            "pacing_notes": "Chapter opens slow, accelerates in second half.",
        }
        memo_store.update_from_read_analysis(read_analysis, 1)
        text = memo_store.read_all()
        assert "accelerates" in text

    def test_records_image_gaps(self, memo_store):
        read_analysis = {
            "image_gaps": [
                {"priority": "critical"},
                {"priority": "high"},
                {"priority": "medium"},
            ],
        }
        memo_store.update_from_read_analysis(read_analysis, 1)
        text = memo_store.read_all()
        assert "image gaps" in text.lower()

    def test_no_image_gaps_records_warning(self, memo_store):
        read_analysis = {"image_gaps": []}
        memo_store.update_from_read_analysis(read_analysis, 1)
        text = memo_store.read_all()
        assert "No image gaps" in text


class TestCharactersDrawer:
    """v0.17: characters.md must accumulate data from READ analysis."""

    def test_character_name_routed_to_characters(self, memo_store):
        read_analysis = {
            "terminology_decisions": [
                {"term_cn": "苏念", "proposed_en": "Su Nian",
                 "category": "character",
                 "reasoning": "Protagonist name — keep Pinyin for consistency",
                 "cultural_note": "名字中的'念'暗示 lingering feeling，不宜直译"},
                {"term_cn": "霸总", "proposed_en": "Alpha CEO",
                 "category": "culture",
                 "reasoning": "Romance archetype shorthand"},
            ],
        }
        memo_store.update_from_read_analysis(read_analysis, 1)
        text = memo_store.read_all()
        # "苏念" is 2-char CN with reasoning + cultural_note → routed to characters
        assert "苏念" in text
        # "霸总" is 2-char but no reasoning/cultural_note gate... actually it has reasoning
        # The filter is: 2 <= len(cn) <= 4 AND (reasoning or cultural_note)
        # Both qualify but 霸总 goes to terms too (existing behavior), characters gets both

    def test_crafted_moments_routed_to_characters(self, memo_store):
        read_analysis = {
            "crafted_moments": [
                "Su Nian's first confrontation with Pei Yanzhou — her cold refusal "
                "establishes the power-reversal dynamic that defines the first arc.",
            ],
        }
        memo_store.update_from_read_analysis(read_analysis, 1)
        text = memo_store.read_all()
        assert "power-reversal" in text

    def test_feature_gate_disables_characters_routing(self, memo_store):
        """When STYLE_MEMO_ENHANCED=False, characters.md stays empty (v0.16 behavior)."""
        import src.style_memo as sm
        read_analysis = {
            "terminology_decisions": [
                {"term_cn": "苏念", "proposed_en": "Su Nian",
                 "reasoning": "Protagonist name", "cultural_note": ""},
            ],
            "crafted_moments": ["A long enough crafted moment about a character."],
        }
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sm, "STYLE_MEMO_ENHANCED", False)
            memo_store.update_from_read_analysis(read_analysis, 1)
        # characters.md should have only the header, no content entries
        characters_path = memo_store.root / "characters.md"
        content = characters_path.read_text("utf-8")
        lines = [l for l in content.strip().split("\n") if l.strip() and not l.startswith("#")]
        assert len(lines) == 0, f"characters.md should be empty when gate is off, got: {lines}"


class TestProseDrawer:
    """v0.17: prose.md gets rhythm notes from READ + standout moments from READBACK."""

    def test_rhythm_pacing_routed_to_prose(self, memo_store):
        read_analysis = {
            "pacing_notes": "Paragraph density is high in this chapter — "
                            "three consecutive dense paragraphs slow the rhythm. "
                            "建议 WRITER 用短句打破叙事密度。",
        }
        memo_store.update_from_read_analysis(read_analysis, 1)
        text = memo_store.read_all()
        assert "density" in text.lower()

    def test_non_rhythm_pacing_not_routed_to_prose(self, memo_store):
        """Pacing notes without rhythm keywords go to pacing only, not prose."""
        memo_store.record_lesson("characters", "placeholder", 0)  # so read_relevant works
        read_analysis = {
            "pacing_notes": "This chapter is fast-paced with lots of action.",
        }
        memo_store.update_from_read_analysis(read_analysis, 1)
        # Read prose.md directly
        prose_path = memo_store.root / "prose.md"
        prose_content = prose_path.read_text("utf-8")
        # "fast-paced" is not a rhythm keyword → should NOT be in prose
        assert "fast-paced" not in prose_content

    def test_standout_moments_routed_to_prose(self, memo_store):
        readback_feedback = {
            "standout_moments": [
                "The description of the villa at dawn — cinematic and vivid, "
                "felt like watching a film opening.",
            ],
        }
        memo_store.update_from_feedback(readback_feedback, {}, 1)
        text = memo_store.read_all()
        assert "cinematic" in text.lower()

    def test_prose_feature_gate_blocks_standout(self, memo_store):
        """When STYLE_MEMO_ENHANCED=False, standout moments don't write to prose."""
        import src.style_memo as sm
        readback_feedback = {
            "standout_moments": [
                "A beautiful scene with excellent pacing and rhythm.",
            ],
        }
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sm, "STYLE_MEMO_ENHANCED", False)
            memo_store.update_from_feedback(readback_feedback, {}, 1)
        prose_path = memo_store.root / "prose.md"
        prose_content = prose_path.read_text("utf-8")
        assert "beautiful scene" not in prose_content


class TestUpdateFromFeedback:
    def test_records_engagement_gaps(self, memo_store):
        readback_feedback = {
            "engagement_gaps": [
                {"passage": "Long explanation of cultivation levels",
                 "issue": "Exposition dump — wanted to skip"},
            ],
        }
        memo_store.update_from_feedback(readback_feedback, {}, 1)
        text = memo_store.read_all()
        assert "Exposition drag" in text

    def test_records_comprehension_issues(self, memo_store):
        readback_feedback = {
            "comprehension_issues": [
                {"passage": "qiankun pouch",
                 "issue": "What is this object?"},
            ],
        }
        memo_store.update_from_feedback(readback_feedback, {}, 1)
        text = memo_store.read_all()
        assert "qiankun pouch" in text

    def test_records_translation_feel(self, memo_store):
        readback_feedback = {
            "overall_impression": "This reads like a translation — feels stiff.",
        }
        memo_store.update_from_feedback(readback_feedback, {}, 1)
        text = memo_store.read_all()
        assert "translation feel" in text
