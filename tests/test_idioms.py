"""Unit tests for src/idioms.py — Chinese idiom detection and translation hints."""

from src.idioms import COMMON_IDIOMS, build_idiom_context, detect_idioms


class TestDetectIdioms:
    """Tests for detect_idioms()."""

    def test_detects_single_idiom_with_meaning(self):
        """'画蛇添足' should be detected and return its meaning."""
        result = detect_idioms("这是在画蛇添足")
        assert len(result) == 1
        idiom, meaning = result[0]
        assert idiom == "画蛇添足"
        assert isinstance(meaning, str)
        assert len(meaning) > 0

    def test_returns_empty_for_text_with_no_idioms(self):
        """Plain text without any known idioms returns an empty list."""
        result = detect_idioms("普通文本没有成语")
        assert result == []

    def test_detects_three_idioms_in_mixed_text(self):
        """Three distinct idioms should all be found."""
        result = detect_idioms("画蛇添足，此地无银三百两，掩耳盗铃")
        assert len(result) == 3
        idioms_found = [entry[0] for entry in result]
        assert "画蛇添足" in idioms_found
        assert "此地无银三百两" in idioms_found
        assert "掩耳盗铃" in idioms_found

    def test_dedup_idiom_appearing_twice(self):
        """An idiom appearing twice should only be reported once."""
        result = detect_idioms("画蛇添足不是画蛇添足")
        assert len(result) == 1
        assert result[0][0] == "画蛇添足"

    def test_all_idioms_are_valid_keys_with_string_values(self):
        """Every entry in COMMON_IDIOMS dict should have a non-empty string value."""
        assert len(COMMON_IDIOMS) >= 50, f"Expected 50+ idioms, found {len(COMMON_IDIOMS)}"
        for idiom, meaning in COMMON_IDIOMS.items():
            assert isinstance(idiom, str), f"Key {idiom!r} is not a string"
            assert isinstance(meaning, str), f"Value for {idiom!r} is not a string"
            assert len(meaning) > 0, f"Meaning for {idiom!r} is empty"


class TestBuildIdiomContext:
    """Tests for build_idiom_context()."""

    def test_returns_empty_for_no_idioms(self):
        """No idioms found should produce an empty string."""
        result = build_idiom_context("普通文本没有成语")
        assert result == ""

    def test_returns_non_empty_with_idiom_in_hint(self):
        """When an idiom is found, the context block should mention it."""
        result = build_idiom_context("这是在画蛇添足")
        assert result != ""
        assert "画蛇添足" in result

    def test_output_contains_do_not_translate_literally(self):
        """The hint block must warn the LLM not to translate literally."""
        result = build_idiom_context("这是在画蛇添足")
        assert "DO NOT translate literally" in result
