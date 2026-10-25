"""Tests for scripts/translate_titles.py — the pure parsing/rewrite half.

The API half is exercised by the real run and recorded in token_ledger; these
tests pin the parts that decide *what* gets sent and *how* the file is rebuilt,
because a wrong key or a mis-deleted line silently corrupts a delivered book.
"""

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "translate_titles.py"

_spec = importlib.util.spec_from_file_location("translate_titles_under_test", _SCRIPT)
tt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tt)


class TestNormalizeTitle:
    def test_strips_a_chapter_prefix(self):
        assert tt.normalize_title("第1章 鬼节，鬼敲门") == "鬼节，鬼敲门"
        assert tt.normalize_title("第两百回 夜行") == "夜行"

    def test_bare_chapter_number_becomes_empty(self):
        """The heading already carries the number; repeating it is noise."""
        assert tt.normalize_title("第一章") == ""
        assert tt.normalize_title("第 12 回") == ""

    def test_drops_a_chinese_parenthetical_after_an_english_title(self):
        assert tt.normalize_title("Prologue（楔子）") == "Prologue"

    def test_keeps_a_chinese_title_with_no_scaffolding(self):
        assert tt.normalize_title("楔子") == "楔子"

    def test_a_title_word_that_looks_like_a_chapter_label_survives(self):
        """`第一回轮班的是郑吒` means "the first shift was Zheng Zha" — the
        `第一回` is the title, not a chapter number, because no separator
        follows it."""
        assert tt.normalize_title("第一回轮班的是郑吒三人") == "第一回轮班的是郑吒三人"


class TestCollectHeadingMode:
    def test_only_chinese_headings_are_entries(self):
        lines = [
            "## Chapter 1: Prologue",
            "## Chapter 2: 第1章 鬼节",
            "not a heading",
        ]
        entries = tt.collect_entries(lines, "heading")
        assert [e["line"] for e in entries] == [1]
        assert entries[0]["key"] == "鬼节"
        assert entries[0]["prefix"] == "## Chapter 2"

    def test_english_parenthetical_is_an_entry(self):
        lines = ["## Chapter 0: Prologue（楔子）"]
        entries = tt.collect_entries(lines, "heading")
        assert entries[0]["key"] == "Prologue"


class TestCollectBracketMode:
    def test_bracket_line_supplies_the_title_and_is_marked_for_deletion(self):
        lines = [
            "## Chapter 2: 第一章",
            "",
            "[醒来 — Part 1]",
            "",
            "Body text.",
        ]
        entries = tt.collect_entries(lines, "bracket")
        assert entries == [{
            "line": 0, "prefix": "## Chapter 2",
            "key": "醒来 — Part 1", "delete": 2,
        }]

    def test_heading_without_bracket_falls_back_to_its_own_title(self):
        lines = ["## Chapter 58: 第一回轮班的是郑吒", "", "Body."]
        entries = tt.collect_entries(lines, "bracket")
        assert entries[0]["key"] == "第一回轮班的是郑吒"
        assert entries[0]["delete"] is None
    def test_already_english_heading_without_bracket_is_skipped(self):
        lines = ["## Chapter 1: Prologue", "", "Body."]
        assert tt.collect_entries(lines, "bracket") == []


class TestApplyMap:
    def test_replaces_heading_and_removes_the_bracket_line(self):
        lines = [
            "## Chapter 2: 第一章",
            "",
            "[醒来 — Part 1]",
            "",
            "Body text.",
        ]
        entries = tt.collect_entries(lines, "bracket")
        out = tt.apply_map(lines, entries, {"醒来 — Part 1": "Awakening — Part 1"})
        assert out == [
            "## Chapter 2: Awakening — Part 1",
            "",
            "",
            "Body text.",
        ]

    def test_missing_key_raises_instead_of_writing_a_blank_title(self):
        lines = ["## Chapter 2: 第1章 鬼节"]
        entries = tt.collect_entries(lines, "heading")
        try:
            tt.apply_map(lines, entries, {})
        except KeyError as exc:
            assert "鬼节" in str(exc)
        else:                                   # pragma: no cover
            raise AssertionError("missing translation must not be written")

    def test_empty_key_drops_the_title_but_keeps_the_heading(self):
        lines = ["## Chapter 119: 第四章"]
        entries = tt.collect_entries(lines, "heading")
        out = tt.apply_map(lines, entries, {})
        assert out == ["## Chapter 119:"]


class TestValidation:
    def test_flags_missing_and_chinese_leaks(self):
        missing, leaks = tt.invalid_titles(
            {"甲": "Alpha", "乙": "", "丙": "Gamma 丙"}, ["甲", "乙", "丙"])
        assert missing == ["乙"]
        assert leaks == ["丙"]

    def test_clean_mapping_has_no_findings(self):
        assert tt.invalid_titles({"甲": "Alpha"}, ["甲"]) == ([], [])


class TestEstimate:
    def test_scales_with_the_number_of_titles(self):
        small = tt.estimate_cny(40, "glm-5.3-flash")
        large = tt.estimate_cny(4000, "glm-5.3-flash")
        assert 0 < small < large

    def test_unknown_model_is_none_not_zero(self):
        """An unpriced model is "cannot estimate", not "free"."""
        assert tt.estimate_cny(40, "mystery-model") is None
