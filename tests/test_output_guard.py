"""Tests for src/output_guard.py — LLM output quality checker."""

from unittest.mock import patch

from src.output_guard import (
    check_and_record,
    check_translation_output,
    find_untranslated_chinese,
    has_untranslated_chinese,
    sanitize_translation,
    strip_chinese_residue,
)


class TestCheckTranslationOutput:

    def test_empty_string_returns_warning(self):
        warnings = check_translation_output("")
        assert len(warnings) > 0

    def test_very_short_returns_warning(self):
        warnings = check_translation_output("Hi")
        assert len(warnings) > 0

    def test_llm_chatter_compile_detected(self):
        text = "Now let me compile the translation with cultural adaptation."
        warnings = check_translation_output(text)
        assert len(warnings) > 0

    def test_llm_chatter_preamble_detected(self):
        text = "Here is the translation for this chapter."
        warnings = check_translation_output(text)
        assert len(warnings) > 0

    def test_llm_chatter_confirmation_detected(self):
        text = "Sure, here you go. The translation is below."
        warnings = check_translation_output(text)
        assert len(warnings) > 0

    def test_valid_translation_passes(self):
        text = (
            "This is a valid chapter translation. It has multiple sentences and is long "
            "enough to pass the minimum length check. The story begins here."
        )
        warnings = check_translation_output(text)
        assert len(warnings) == 0

    def test_just_barely_long_enough_passes(self):
        text = "A" * 60
        warnings = check_translation_output(text)
        assert len(warnings) == 0


class TestSanitizeTranslation:

    def test_removes_compile_chatter(self):
        # Regex patterns use `$` with re.MULTILINE, so chatter on its own line
        # gets removed while content on subsequent lines is preserved.
        text = "Now let me compile the translation.\nThe actual text starts here."
        result = sanitize_translation(text)
        assert "Now let me" not in result
        assert "The actual text starts here." in result

    def test_handles_clean_text_unchanged(self):
        text = "This is a perfect translation with no chatter."
        result = sanitize_translation(text)
        assert result == text

    def test_removes_preamble(self):
        text = "Here is the translation: Chapter one begins."
        result = sanitize_translation(text)
        assert "Here is the translation" not in result


class TestChineseResidue:
    """Chinese-character detection/stripping — the mutation test showed uncovered."""

    def test_has_untranslated_chinese_true(self):
        assert has_untranslated_chinese("纯English残留") is True

    def test_has_untranslated_chinese_false(self):
        assert has_untranslated_chinese("Pure English text.") is False

    def test_find_returns_cjk_chars(self):
        chars = find_untranslated_chinese("治安残留")
        assert set(chars) == {"治", "安", "残", "留"}

    def test_strip_removes_cjk(self):
        cleaned, count = strip_chinese_residue("abc治安def")
        assert cleaned == "abcdef"
        assert count == 2

    def test_strip_empty_returns_zero(self):
        cleaned, count = strip_chinese_residue("")
        assert cleaned == ""
        assert count == 0


class TestMinLengthBoundary:
    """MIN_TRANSLATION_CHARS = 50 boundary (hardcoded, not the constant)."""

    def test_49_chars_is_too_short(self):
        warnings = check_translation_output("A" * 49)
        assert any(w.startswith("EMPTY:") for w in warnings)

    def test_50_chars_passes_length_check(self):
        warnings = check_translation_output("A" * 50)
        assert not any(w.startswith("EMPTY:") for w in warnings)


class TestCheckAndRecordEventTypes:
    """check_and_record classifies warnings into the right event_type."""

    def test_empty_event_type(self):
        with patch("src.error_tracker.record_event") as mock_rec:
            check_and_record("", "job1", 1, "en-US")
        types = [c.args[2] for c in mock_rec.call_args_list]
        assert "empty_output" in types

    def test_chatter_event_type(self):
        with patch("src.error_tracker.record_event") as mock_rec:
            check_and_record(
                "Now let me compile the translation. " + "x" * 50, "job1", 1, "en-US"
            )
        types = [c.args[2] for c in mock_rec.call_args_list]
        assert "chatter_detected" in types

    def test_untranslated_event_type(self):
        with patch("src.error_tracker.record_event") as mock_rec:
            text = "Valid English translation sentence. " * 5 + "治安残留"
            check_and_record(text, "job1", 1, "en-US")
        types = [c.args[2] for c in mock_rec.call_args_list]
        assert "untranslated_chinese" in types
