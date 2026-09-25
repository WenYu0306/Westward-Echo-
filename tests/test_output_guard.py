"""Tests for src/output_guard.py — LLM output quality checker."""

from unittest.mock import patch

from src.output_guard import (
    check_and_record,
    check_translation_output,
    find_untranslated_chinese,
    has_json_envelope_residue,
    has_meta_text_leak,
    has_untranslated_chinese,
    json_residue_on_line,
    meta_text_on_line,
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


class TestNullAndRecordedOutputs:
    def test_none_translation_is_reported_not_swallowed(self):
        warnings = check_translation_output(None)
        assert warnings is not None
        assert any(w.startswith("EMPTY:") for w in warnings)

    def test_chinese_residue_warning_lists_at_most_five_chars(self):
        from src.output_guard import check_and_record

        # Seven distinct Chinese characters — the warning shows the first five.
        text = "甲 乙 丙 丁 戊 己 庚 and some English text here."
        warnings = check_and_record(text or "", job_id="guard-test", chapter_num=1)
        cn = [w for w in warnings if w.startswith("UNTRANSLATED")]
        assert cn, "Chinese residue must be reported"
        listed = cn[0].split("output: ")[1]
        assert len([c for c in listed.split(", ") if c.strip()]) == 5

    def test_recorded_output_returns_the_warning_list(self):
        from src.output_guard import check_and_record

        result = check_and_record(
            "She stepped into the hall." * 5, job_id="guard-clean", chapter_num=1
        )
        assert isinstance(result, list)
        assert result == []


class TestJsonResidueOnLine:
    """Line-level envelope detection. Every branch is tested separately so a
    mutation that drops one key or one bare-brace variant fails (iron rule 2:
    assertions must have teeth)."""

    def test_bare_open_brace(self):
        assert json_residue_on_line("{") == "bare '{' line"

    def test_bare_close_brace(self):
        assert json_residue_on_line("}") == "bare '}' line"

    def test_bare_close_brace_comma(self):
        assert json_residue_on_line("},") == "bare '},' line"

    def test_bare_open_brace_comma(self):
        assert json_residue_on_line("{,") == "bare '{,' line"

    def test_json_fence_detected(self):
        assert json_residue_on_line("He had them in his pocket...```json") == "```json fence"

    def test_each_envelope_key_detected(self):
        for key in ('"translated_text"', '"chapter_title_en"',
                    '"new_terms_found"', '"polished_text"', '"changes_made"'):
            label = json_residue_on_line(key + ': "some value"')
            assert label == f"unpacked envelope key {key}", key

    def test_clean_prose_line_is_not_residue(self):
        assert json_residue_on_line("She stepped into the hall and closed the door.") is None

    def test_a_brace_inside_prose_is_not_a_bare_line(self):
        # Only a line that IS the brace counts; prose containing one does not.
        assert json_residue_on_line("He wrote {x} on the board.") is None

    def test_compact_single_line_envelope_is_detected(self):
        """Regression: the key need not be at line start.

        The old ``startswith(key)`` rule missed a compact truncated envelope
        (``{"translated_text": "..."`` all on one line) because the line begins
        with ``{``, not the key. Measured: it returned None. This is the Ch1472
        failure shape in its compact form.
        """
        line = '{"translated_text": "Grand Warden Xin shouted, and then'
        assert json_residue_on_line(line) == 'unpacked envelope key "translated_text"'

    def test_envelope_key_after_leading_text_is_detected(self):
        # Key mid-line (e.g. after a `{` and other fields) still counts.
        line = '{"x": 1, "new_terms_found": ["a"], and it ran on'
        assert json_residue_on_line(line) == 'unpacked envelope key "new_terms_found"'

    def test_prose_mentioning_a_key_without_a_colon_is_not_residue(self):
        # "translated_text" as a plain word (no `"..."` + colon) is legitimate.
        assert json_residue_on_line(
            "The translated_text field matters here."
        ) is None


class TestMetaTextOnLine:
    """Line-level scaffolding detection. Each pattern tested on its own."""

    def test_end_of_chapter_upper(self):
        assert meta_text_on_line("END OF CHAPTER 1286") == "chapter-end scaffolding"

    def test_end_of_chapter_mixed_case(self):
        assert meta_text_on_line("End of Chapter.") == "chapter-end scaffolding"

    def test_as_an_ai(self):
        assert meta_text_on_line("As an AI, I cannot help with that.") == "model self-reference"

    def test_model_refusal(self):
        assert meta_text_on_line("I cannot provide a translation of this.") == "model refusal"

    def test_note_preamble(self):
        assert meta_text_on_line("Note: the following is a translation.") == "model preamble"

    def test_disclaimer_preamble(self):
        assert meta_text_on_line("Disclaimer: this is fiction.") == "model preamble"

    def test_clean_prose_line_is_not_meta(self):
        assert meta_text_on_line("The chapter ended quietly, and the snow fell.") is None

    def test_mid_sentence_end_of_chapter_is_not_scaffolding(self):
        # Line-anchored: the words inside a sentence are legitimate prose.
        assert meta_text_on_line("She reached the end of chapter three at last.") is None


class TestChapterLevelGuards:
    """The whole-chapter wrappers the WRITE node calls."""

    def test_has_json_envelope_residue_true(self):
        body = "Some prose.\n```\n{\n  \"translated_text\": \"hi\"\n}"
        assert has_json_envelope_residue(body) is True

    def test_has_json_envelope_residue_false_on_clean(self):
        assert has_json_envelope_residue("Just a clean chapter of prose. It ends well.") is False

    def test_has_json_envelope_residue_false_on_empty(self):
        assert has_json_envelope_residue("") is False

    def test_has_meta_text_leak_true(self):
        assert has_meta_text_leak("Prose line.\nEND OF CHAPTER 7") is True

    def test_has_meta_text_leak_false_on_clean(self):
        assert has_meta_text_leak("A clean chapter with no scaffolding at all.") is False

    def test_has_meta_text_leak_false_on_empty(self):
        assert has_meta_text_leak("") is False
