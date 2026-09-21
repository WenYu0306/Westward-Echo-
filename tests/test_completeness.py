"""Tests for src/completeness.py — the scene-level completeness check.

The check exists because nothing verified the WRITE prompt's own bound:
"Every scene in the source must have an equivalent in the output." READBACK
cannot verify it (by design it never sees the source), so the mirror has to
come from a source-aware comparison.

Thresholds here were calibrated against 《地府叫我小先生》: the 11 chapters
known to be truncated, plus 8 healthy controls. That calibration produced one
negative result worth locking down — see
``test_missing_glossary_term_alone_is_not_a_flag``.
"""

from src.completeness import (
    base_renderings,
    check_completeness,
    count_dialogue_spans,
    count_paragraphs,
)


class TestBaseRenderings:
    """The glossary stores renderings WITH their gloss; prose uses the bare name.

    Matching the stored string verbatim once produced a false "52 terms missing"
    verdict for a book whose protagonist's name appears 1007 times.
    """

    def test_strips_parenthetical_gloss(self):
        assert base_renderings("Wuxin (No-Heart)") == ["Wuxin"]
        assert base_renderings("Yongye (Eternal Night)") == ["Yongye"]

    def test_splits_slash_alternatives(self):
        assert base_renderings("Potter's Field / the common graves") == [
            "Potter's Field", "the common graves",
        ]

    def test_drops_trailing_dash_clause(self):
        assert base_renderings("Huang Chao \u2013 the Butcher of Chang'an") == [
            "Huang Chao"
        ]

    def test_rejects_too_short_to_search_safely(self):
        # "A-Da" as a substring would match inside unrelated words.
        assert base_renderings("A-Da") == []
        assert base_renderings("Wu") == []

    def test_empty_rendering_yields_nothing(self):
        assert base_renderings("") == []


class TestCountingHelpers:
    def test_counts_chinese_open_quotes_only(self):
        assert count_dialogue_spans("\u201c\u4f60\u597d\u201d", ("\u201c",)) == 1

    def test_ascii_quotes_are_halved_because_they_pair(self):
        # ASCII uses the same glyph for open and close, so raw occurrences are
        # double the span count. Comparing that against a Chinese source
        # (1 glyph per span) inflated the ratio.
        assert count_dialogue_spans('She said, "hi."', ('"',)) == 2
        assert count_dialogue_spans('She said, "hi."', ('"',), ascii_halved=True) == 1
        assert count_dialogue_spans('"a." "b." "c."', ('"',), ascii_halved=True) == 3

    def test_unbalanced_ascii_quote_still_counts_as_one(self):
        assert count_dialogue_spans('She said, "hi', ('"',), ascii_halved=True) == 1

    def test_counts_paragraphs_ignoring_blanks(self):
        assert count_paragraphs("a\n\n  \nb") == 2


class TestFlags:
    def test_empty_output_is_flagged(self):
        r = check_completeness("\u5979\u8bf4\uff1a\u201c\u4f60\u6765\u4e86\u3002\u201d" * 50, "")
        assert "EMPTY_OUTPUT" in r["flags"]

    def test_dropped_dialogue_is_flagged(self):
        src = "\u201c\u4f60\u597d\u3002\u201d" * 6          # 6 spoken lines
        r = check_completeness(src, "He walked in and sat down without a word. " * 40)
        assert "DIALOGUE_DROPPED" in r["flags"]
        assert r["dialogue_src"] == 6
        assert r["dialogue_out"] == 0

    def test_a_single_quote_is_not_structure(self):
        # Below the >= 4 threshold this is noise, not a dropped conversation.
        r = check_completeness("\u201c\u597d\u3002\u201d" * 1 + "\u4ed6\u8d70\u4e86\u3002" * 50,
                               "He left. " * 60)
        assert "DIALOGUE_DROPPED" not in r["flags"]

    def test_low_length_ratio_is_flagged(self):
        src = "\u5979\u62c9\u5f00\u4e86\u95e8\u3002" * 200      # ~1800 CJK
        r = check_completeness(src, "She opened the door. " * 8)
        assert "LENGTH_RATIO_LOW" in r["flags"]

    def test_healthy_chapter_is_clean(self):
        src = "\u5979\u62c9\u5f00\u4e86\u95e8\u3002" * 200
        r = check_completeness(src, "She opened the door and stepped through. " * 120)
        assert r["flags"] == []

    def test_length_ratio_threshold_can_be_disabled(self):
        src = "\u5979\u62c9\u5f00\u4e86\u95e8\u3002" * 200
        r = check_completeness(src, "short.", min_length_ratio=None)
        assert "LENGTH_RATIO_LOW" not in r["flags"]

    def test_short_source_is_not_ratio_flagged(self):
        # A prologue stub cannot meaningfully be "too short".
        r = check_completeness("\u5979\u6765\u4e86\u3002" * 5, "She came.", min_length_ratio=2.0)
        assert "LENGTH_RATIO_LOW" not in r["flags"]


class TestCalibrationNegativeResults:
    """Locked-in negative results: signals that were measured and REJECTED.

    Without these tests the next reader is likely to "improve" the module by
    promoting a signal that was already proven not to discriminate.
    """

    def test_missing_glossary_term_alone_is_not_a_flag(self):
        """A term in the source but absent from the output is NOT a defect.

        Measured on 《地府》: the 11 truncated chapters had 8-24 missing terms,
        but so did all 8 healthy controls (5-16). The ranges overlap, so the
        signal cannot separate defect from normal. The reason is by design —
        WRITE is granted authority to compress and cut minor mentions, so a
        dropped passing reference is expected behaviour, not a dropped scene.
        """
        src = "\u9648\u5927\u8ba1\u8d70\u8fdb\u6765\u4e86\u3002" * 200
        out = "Someone walked in. " * 200          # term never rendered
        r = check_completeness(src, out, {"\u9648\u5927\u8ba1": "Chen Daji"})
        assert r["glossary_missing"] == ["\u9648\u5927\u8ba1"]   # still reported
        assert "GLOSSARY_TERMS_MISSING" not in r["flags"]         # but not a flag
        assert r["flags"] == []

    def test_term_absent_from_source_is_ignored(self):
        """Only terms this chapter actually uses can say anything about it."""
        src = "\u4ed6\u8d70\u4e86\u3002" * 300
        out = "He left. " * 200
        r = check_completeness(src, out, {"\u9648\u5927\u8ba1": "Chen Daji"})
        assert r["glossary_used"] == 0
        assert r["glossary_missing"] == []

    def test_gloss_present_in_prose_counts_as_covered(self):
        """The bare name in prose satisfies a glossed glossary entry."""
        src = "\u65e0\u5fc3\u7ad9\u5728\u95e8\u53e3\u3002" * 200
        out = "Wuxin stood in the doorway. " * 200
        r = check_completeness(src, out, {"\u65e0\u5fc3": "Wuxin (No-Heart)"})
        assert r["glossary_missing"] == []


class TestBoundaryConditions:
    """Boundary tests added to kill the mutants that initially survived.

    Mutation run on this module: 35 mutants / 27 killed / 8 survived. None of
    the 8 was an equivalent mutant — every one was a real boundary this file
    had failed to pin down:
    len threshold 3, min_term_len default 2, three round(..., 2) precisions,
    ascii_halved=True, the dlg_src >= 4 floor, and the src_cjk >= 400 floor.
    """

    def test_three_char_rendering_is_searchable(self):
        """len(b) >= 3 — a 3-character name is long enough to search safely."""
        assert base_renderings("Abe") == ["Abe"]
        assert base_renderings("Nan") == ["Nan"]

    def test_two_char_rendering_is_not_searchable(self):
        """The other side of the same boundary."""
        assert base_renderings("Wu") == []

    def test_min_term_len_default_is_two(self):
        """A 2-character term must be eligible by default.

        Raising the default would silently stop checking every two-character
        term in the glossary, which is most of them.
        """
        src = "\u5927\u8ba1\u8d70\u4e86\u8fdb\u6765\u3002" * 200      # 大计 present
        out = "Da Ji walked in. " * 200
        r = check_completeness(src, out, {"\u5927\u8ba1": "Da Ji"})
        assert r["glossary_used"] == 1
        assert r["glossary_missing"] == []

    def test_length_ratio_is_rounded_to_two_places(self):
        # 1000 / 7 = 142.857142... -> 142.86 at 2dp, 142.857 at 3dp
        src = "\u5979\u6765\u4e86\u3002\u4ed6\u8d70\u4e86\u3002\u597d"          # 7 CJK
        r = check_completeness(src, "x" * 1000)
        assert r["src_cjk"] == 7
        assert r["length_ratio"] == 142.86

    def test_dialogue_ratio_is_rounded_to_two_places(self):
        # 1 output span / 3 source spans = 0.3333... -> 0.33 at 2dp
        src = "\u201c\u4f60\u597d\u3002\u201d" * 3
        out = 'He said, "hi."'
        r = check_completeness(src, out)
        assert r["dialogue_src"] == 3
        assert r["dialogue_out"] == 1
        assert r["dialogue_ratio"] == 0.33

    def test_para_ratio_is_rounded_to_two_places(self):
        # 1 output paragraph / 3 source paragraphs = 0.33
        src = "a\n\nb\n\nc"
        src = "\u4e00\u3002\n\n\u4e8c\u3002\n\n\u4e09\u3002"
        r = check_completeness(src, "One paragraph only.")
        assert r["paras_src"] == 3
        assert r["paras_out"] == 1
        assert r["para_ratio"] == 0.33

    def test_output_dialogue_count_halves_ascii_quotes(self):
        """ascii_halved=True — three quoted spans must count as three, not six."""
        src = "\u201c\u597d\u3002\u201d" * 5
        out = '"One." "Two." "Three."'
        r = check_completeness(src, out)
        assert r["dialogue_out"] == 3
        assert r["dialogue_src"] == 5

    def test_dialogue_dropped_floor_is_four_source_spans(self):
        """dlg_src >= 4 — exactly four spoken lines with none rendered is a drop."""
        src = "\u201c\u597d\u3002\u201d" * 4 + "\u4ed6\u8d70\u4e86\u3002" * 60
        out = "He left without saying anything at all. " * 60
        r = check_completeness(src, out)
        assert r["dialogue_src"] == 4
        assert r["dialogue_out"] == 0
        assert "DIALOGUE_DROPPED" in r["flags"]

    def test_length_ratio_floor_is_400_source_cjk(self):
        """src_cjk >= 400 — exactly 400 Chinese characters can still be flagged.

        \u5979\u62c9\u5f00\u4e86\u95e8\u3002 is 5 CJK characters, so 80 repetitions is
        exactly at the floor.
        """
        src = "\u5979\u62c9\u5f00\u4e86\u95e8\u3002" * 80
        r = check_completeness(src, "She opened the door. " * 4)
        assert r["src_cjk"] == 400
        assert "LENGTH_RATIO_LOW" in r["flags"]

    def test_just_below_the_length_floor_is_not_flagged(self):
        src = "\u5979\u62c9\u5f00\u4e86\u95e8\u3002" * 79      # 395 CJK
        r = check_completeness(src, "She opened the door. " * 4)
        assert r["src_cjk"] == 395
        assert "LENGTH_RATIO_LOW" not in r["flags"]
