"""Tests for src/output_guard.py — LLM output quality checker."""

from src.output_guard import check_translation_output, sanitize_translation


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
