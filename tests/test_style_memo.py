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
        lines = [
            line for line in content.strip().split("\n")
            if line.strip() and not line.startswith("#")
        ]
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


class TestReadAllTruncation:
    """Pin read_all()'s line-count threshold and line-slice boundaries."""

    def test_truncates_over_70_lines(self, memo_store):
        lines = ["# H"] + ["EARLYMARKER"] + [f"L{i}" for i in range(2, 71)]
        (memo_store.root / "characters.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        text = memo_store.read_all()
        assert "EARLYMARKER" not in text

    def test_keeps_last_60_lines_with_header(self, memo_store):
        lines = ["# H"] + [f"L{i}" for i in range(1, 75)]
        lines[14] = "MARK61"
        (memo_store.root / "characters.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        text = memo_store.read_all()
        assert "MARK61" not in text

    def test_keeps_last_60_lines_without_header(self, memo_store):
        lines = ["NOHEADER"] + [f"L{i}" for i in range(1, 75)]
        lines[14] = "MARK61B"
        (memo_store.root / "pacing.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        text = memo_store.read_all()
        assert "MARK61B" not in text


class TestReadRelevantBoundaries:
    """Pin read_relevant()'s budget, truncation and slice boundaries."""

    def test_default_max_chars_is_5000(self, memo_store):
        entry = "a" * 250
        for fname in ["characters.md", "pacing.md", "bridges.md",
                      "prose.md", "terms.md"]:
            (memo_store.root / fname).write_text(
                "# H\n" + "\n".join([entry] * 5) + "\n", encoding="utf-8")
        result = memo_store.read_relevant()
        assert len(result) == 5000

    def test_default_recent_is_5(self, memo_store):
        body = "\n".join(["OLDEST_MARKER"] + [f"entry{i}" for i in range(1, 6)])
        (memo_store.root / "characters.md").write_text(
            "# H\n" + body + "\n", encoding="utf-8")
        result = memo_store.read_relevant()
        assert "OLDEST_MARKER" not in result
        assert "entry5" in result

    def test_single_entry_drawer_is_processed(self, memo_store):
        (memo_store.root / "characters.md").write_text(
            "# H\nZENTRY\n", encoding="utf-8")
        result = memo_store.read_relevant(max_chars=300)
        assert "Index" not in result
        assert "ZENTRY" in result

    def test_entry_truncated_at_250(self, memo_store):
        (memo_store.root / "characters.md").write_text(
            "# H\n" + "7" * 300 + "\n", encoding="utf-8")
        result = memo_store.read_relevant(max_chars=300)
        assert "7" * 251 not in result
        assert "7" * 250 in result

    def test_includes_first_entry(self, memo_store):
        (memo_store.root / "characters.md").write_text(
            "# H\nFIRSTENTRY\nSECONDENTRY\n", encoding="utf-8")
        result = memo_store.read_relevant(max_chars=300)
        assert "FIRSTENTRY" in result
        assert "SECONDENTRY" in result

    def test_budget_201_continues_to_next_drawer(self, memo_store):
        (memo_store.root / "characters.md").write_text(
            "H\n" + "a" * 250 + "\n" + "b" * 46 + "\n", encoding="utf-8")
        (memo_store.root / "pacing.md").write_text(
            "H2\nMARKER_DRAWER2\n", encoding="utf-8")
        result = memo_store.read_relevant(max_chars=500)
        assert "MARKER_DRAWER2" in result

    def test_does_not_fall_back_to_read_all(self, memo_store):
        body = "\n".join(["OLD_MARKER"] + [f"entry{i}" for i in range(1, 6)])
        (memo_store.root / "characters.md").write_text(
            "# H\n" + body + "\n", encoding="utf-8")
        result = memo_store.read_relevant(max_chars=300)
        assert "OLD_MARKER" not in result

    def test_caps_result_at_max_chars(self, memo_store):
        (memo_store.root / "characters.md").write_text(
            "# C\n" + "a" * 150 + "\n", encoding="utf-8")
        (memo_store.root / "pacing.md").write_text(
            "# P\n" + "b" * 250 + "\n", encoding="utf-8")
        result = memo_store.read_relevant(max_chars=400)
        assert len(result) == 400


class TestStoreInitAndDedup:
    def test_init_is_idempotent(self, memo_store):
        import src.style_memo as sm
        sm.StyleMemoStore("test_book")

    def test_dedup_fingerprint_is_50_chars(self, memo_store):
        memo_store.record_lesson("characters", "x" * 50 + "AAAA" + " one", 1)
        memo_store.record_lesson("characters", "x" * 50 + "BBBB" + " two", 2)
        text = memo_store.read_all()
        assert "AAAA" in text
        assert "BBBB" not in text


class TestReadAnalysisBoundaries:
    def test_cultural_gap_element_truncated_at_60(self, memo_store):
        memo_store.update_from_read_analysis(
            {"cultural_gaps": [{"element": "7" * 100, "bridge_guidance": "g"}]}, 1)
        bridges = (memo_store.root / "bridges.md").read_text(encoding="utf-8")
        assert "7" * 61 not in bridges

    def test_cultural_gap_guidance_truncated_at_150(self, memo_store):
        memo_store.update_from_read_analysis(
            {"cultural_gaps": [{"element": "E", "bridge_guidance": "8" * 200}]}, 1)
        bridges = (memo_store.root / "bridges.md").read_text(encoding="utf-8")
        assert "8" * 151 not in bridges

    def test_pacing_note_truncated_at_300(self, memo_store):
        memo_store.update_from_read_analysis({"pacing_notes": "5" * 400}, 1)
        pacing = (memo_store.root / "pacing.md").read_text(encoding="utf-8")
        assert "5" * 301 not in pacing

    def test_prose_rhythm_truncated_at_300(self, memo_store):
        memo_store.update_from_read_analysis(
            {"pacing_notes": "4" * 400 + " rhythm"}, 1)
        prose = (memo_store.root / "prose.md").read_text(encoding="utf-8")
        assert "4" * 301 not in prose

    def test_pacing_note_len_11_recorded(self, memo_store):
        memo_store.update_from_read_analysis({"pacing_notes": "abcdefghijk"}, 1)
        pacing = (memo_store.root / "pacing.md").read_text(encoding="utf-8")
        assert "abcdefghijk" in pacing

    def test_prose_rhythm_len_11_recorded(self, memo_store):
        memo_store.update_from_read_analysis({"pacing_notes": "density 123"}, 1)
        prose = (memo_store.root / "prose.md").read_text(encoding="utf-8")
        assert "density 123" in prose

    def test_prose_rhythm_len_gt_10_recorded(self, memo_store):
        memo_store.update_from_read_analysis(
            {"pacing_notes": "density is high in chapter"}, 1)
        prose = (memo_store.root / "prose.md").read_text(encoding="utf-8")
        assert "density is high" in prose

    def test_pacing_none_is_skipped(self, memo_store):
        memo_store.update_from_read_analysis(
            {"pacing_notes": None, "image_gaps": [{"priority": "critical"}]}, 1)
        pacing = (memo_store.root / "pacing.md").read_text(encoding="utf-8")
        assert "image gaps detected" in pacing.lower()

    def test_image_gaps_counts(self, memo_store):
        memo_store.update_from_read_analysis(
            {"image_gaps": [
                {"priority": "critical"},
                {"priority": "high"},
                {"priority": "medium"},
            ]}, 1)
        pacing = (memo_store.root / "pacing.md").read_text(encoding="utf-8")
        assert "critical=1" in pacing
        assert "high=1" in pacing


class TestTerminologyAndCharactersBoundaries:
    def test_terminology_requires_cn_and_en(self, memo_store):
        memo_store.update_from_read_analysis(
            {"terminology_decisions": [
                {"term_cn": "UNIQUECN", "proposed_en": ""}]}, 1)
        text = memo_store.read_all()
        assert "UNIQUECN" not in text

    def test_character_len_5_not_routed(self, memo_store):
        memo_store.update_from_read_analysis(
            {"terminology_decisions": [
                {"term_cn": "ABCDE", "proposed_en": "Five", "reasoning": "r"}]}, 1)
        characters = (memo_store.root / "characters.md").read_text(encoding="utf-8")
        assert "ABCDE" not in characters

    def test_character_len_2_routed(self, memo_store):
        memo_store.update_from_read_analysis(
            {"terminology_decisions": [
                {"term_cn": "AB", "proposed_en": "Two",
                 "reasoning": "keep", "cultural_note": ""}]}, 1)
        characters = (memo_store.root / "characters.md").read_text(encoding="utf-8")
        assert "AB" in characters

    def test_character_reasoning_only_routed(self, memo_store):
        memo_store.update_from_read_analysis(
            {"terminology_decisions": [
                {"term_cn": "XYZ", "proposed_en": "Three",
                 "reasoning": "why", "cultural_note": ""}]}, 1)
        characters = (memo_store.root / "characters.md").read_text(encoding="utf-8")
        assert "XYZ" in characters

    def test_terms_cultural_note_truncated_at_120(self, memo_store):
        memo_store.update_from_read_analysis(
            {"terminology_decisions": [
                {"term_cn": "ABCDE", "proposed_en": "EN",
                 "cultural_note": "3" * 200}]}, 1)
        terms = (memo_store.root / "terms.md").read_text(encoding="utf-8")
        assert "3" * 121 not in terms

    def test_terms_reasoning_truncated_at_120(self, memo_store):
        memo_store.update_from_read_analysis(
            {"terminology_decisions": [
                {"term_cn": "ABCDE", "proposed_en": "EN",
                 "reasoning": "2" * 200}]}, 1)
        terms = (memo_store.root / "terms.md").read_text(encoding="utf-8")
        assert "2" * 121 not in terms

    def test_characters_cultural_note_truncated_at_120(self, memo_store):
        memo_store.update_from_read_analysis(
            {"terminology_decisions": [
                {"term_cn": "CN", "proposed_en": "EN",
                 "cultural_note": "1" * 200}]}, 1)
        characters = (memo_store.root / "characters.md").read_text(encoding="utf-8")
        assert "1" * 121 not in characters

    def test_crafted_moment_len_21_routed(self, memo_store):
        memo_store.update_from_read_analysis(
            {"crafted_moments": ["y" * 21]}, 1)
        characters = (memo_store.root / "characters.md").read_text(encoding="utf-8")
        assert "y" * 21 in characters

    def test_crafted_moment_len_20_not_routed(self, memo_store):
        memo_store.update_from_read_analysis(
            {"crafted_moments": ["w" * 20]}, 1)
        characters = (memo_store.root / "characters.md").read_text(encoding="utf-8")
        assert "w" * 20 not in characters

    def test_crafted_moment_truncated_at_200(self, memo_store):
        memo_store.update_from_read_analysis(
            {"crafted_moments": ["0" * 300]}, 1)
        characters = (memo_store.root / "characters.md").read_text(encoding="utf-8")
        assert "0" * 201 not in characters

    def test_crafted_moment_non_string_skipped(self, memo_store):
        memo_store.update_from_read_analysis(
            {"crafted_moments": [["ZZZZCRF"] * 21]}, 1)
        characters = (memo_store.root / "characters.md").read_text(encoding="utf-8")
        assert "ZZZZCRF" not in characters


class TestFeedbackBoundaries:
    def test_engagement_passage_truncated_at_80(self, memo_store):
        memo_store.update_from_feedback(
            {"engagement_gaps": [
                {"passage": "6" * 100, "issue": "exposition"}]}, {}, 1)
        pacing = (memo_store.root / "pacing.md").read_text(encoding="utf-8")
        assert "6" * 81 not in pacing

    def test_comprehension_passage_truncated_at_80(self, memo_store):
        memo_store.update_from_feedback(
            {"comprehension_issues": [
                {"passage": "9" * 100, "issue": "unclear"}]}, {}, 1)
        bridges = (memo_store.root / "bridges.md").read_text(encoding="utf-8")
        assert "9" * 81 not in bridges

    def test_engagement_info_only_records(self, memo_store):
        memo_store.update_from_feedback(
            {"engagement_gaps": [
                {"passage": "p", "issue": "too much info dump"}]}, {}, 1)
        text = memo_store.read_all()
        assert "Exposition drag" in text

    def test_engagement_explain_only_records(self, memo_store):
        memo_store.update_from_feedback(
            {"engagement_gaps": [
                {"passage": "p", "issue": "explains too much"}]}, {}, 1)
        text = memo_store.read_all()
        assert "Exposition drag" in text

    def test_standout_len_21_recorded(self, memo_store):
        memo_store.update_from_feedback(
            {"standout_moments": ["t" * 21]}, {}, 1)
        prose = (memo_store.root / "prose.md").read_text(encoding="utf-8")
        assert "t" * 21 in prose

    def test_standout_truncated_at_200(self, memo_store):
        memo_store.update_from_feedback(
            {"standout_moments": ["9" * 300]}, {}, 1)
        prose = (memo_store.root / "prose.md").read_text(encoding="utf-8")
        assert "9" * 201 not in prose

    def test_standout_non_string_skipped(self, memo_store):
        memo_store.update_from_feedback(
            {"standout_moments": [["ZZZZSTD"] * 21]}, {}, 1)
        prose = (memo_store.root / "prose.md").read_text(encoding="utf-8")
        assert "ZZZZSTD" not in prose

    def test_overall_impression_truncated_at_200(self, memo_store):
        memo_store.update_from_feedback(
            {"overall_impression": "8" * 300 + " translation"}, {}, 1)
        prose = (memo_store.root / "prose.md").read_text(encoding="utf-8")
        assert "8" * 201 not in prose

    def test_translation_only_impression_records(self, memo_store):
        memo_store.update_from_feedback(
            {"overall_impression": "feels like machine translation"}, {}, 1)
        text = memo_store.read_all()
        assert "translation feel" in text

    def test_reads_like_only_impression_records(self, memo_store):
        memo_store.update_from_feedback(
            {"overall_impression": "this reads like an essay"}, {}, 1)
        text = memo_store.read_all()
        assert "translation feel" in text
