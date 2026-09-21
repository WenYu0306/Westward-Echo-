"""Guards on WRITE's output path: token-ceiling truncation and unparseable replies.

Both guards exist because of one failure the pipeline used to hide:

    a response cut off at ``max_tokens`` was indistinguishable from a complete
    one. Nothing read ``finish_reason``; the multi-layer parser then handed the
    partial text back as ``translated_text`` (Layer 5 returned the raw content),
    and the chapter shipped short. That is the failure the DeepSeek-era
    adversarial test recorded as "empty translations with Chinese character
    residue" (src/chapter_slicer.py). Slicing lowered the odds but was never a
    detector, and its budget was calibrated for DeepSeek's context window —
    not for GLM/Qwen plus the reasoning tokens the new stack spends.

Assertions use hardcoded literals (project iron rule) so a shared constant
cannot make the test green by drifting along with the code.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.agent.nodes.write import (
    _parse_write_response,
    _was_truncated,
    write_node,
)


class _Resp:
    """Minimal stand-in for a LangChain AIMessage."""

    def __init__(self, content="", metadata=None, kwargs=None):
        self.content = content
        self.response_metadata = metadata if metadata is not None else {}
        self.additional_kwargs = kwargs if kwargs is not None else {}


class TestWasTruncated:
    def test_normal_completion_is_not_truncated(self):
        assert _was_truncated(_Resp(metadata={"finish_reason": "stop"})) == ""

    def test_length_is_truncation(self):
        assert _was_truncated(_Resp(metadata={"finish_reason": "length"})) == "length"

    def test_max_tokens_variant(self):
        assert _was_truncated(_Resp(metadata={"finish_reason": "max_tokens"})) == "max_tokens"

    def test_max_output_tokens_variant(self):
        assert (
            _was_truncated(_Resp(metadata={"finish_reason": "max_output_tokens"}))
            == "max_output_tokens"
        )

    def test_content_filter_stops_early_too(self):
        assert (
            _was_truncated(_Resp(metadata={"finish_reason": "content_filter"}))
            == "content_filter"
        )

    def test_reads_additional_kwargs_as_fallback(self):
        assert _was_truncated(_Resp(kwargs={"finish_reason": "length"})) == "length"

    def test_case_insensitive(self):
        assert _was_truncated(_Resp(metadata={"finish_reason": "LENGTH"})) == "length"

    def test_missing_field_is_not_truncated(self):
        assert _was_truncated(_Resp(metadata={})) == ""

    def test_tool_calls_are_not_truncation(self):
        assert _was_truncated(_Resp(metadata={"finish_reason": "tool_calls"})) == ""

    def test_survives_a_broken_response_object(self):
        class Broken:
            @property
            def response_metadata(self):
                raise RuntimeError("boom")

        assert _was_truncated(Broken()) == ""


class TestLayerFive:
    """Layer 5 rescues prose the model wrote without the JSON envelope — but
    only when it is actually prose. It used to rescue everything."""

    def test_chatty_preamble_plus_translation_is_rescued(self):
        chatter = (
            "I'd be happy to translate this chapter for you! "
            "Let me work through it carefully.\n\n"
            "Su Nian woke up to find herself lying on a large unfamiliar bed."
        )
        r = _parse_write_response(chatter, chapter_number=1)
        assert r["translated_text"]
        assert len(r["translated_text"]) > 50

    def test_symbol_soup_is_discarded(self):
        r = _parse_write_response("\u3002\uff0c\uff0c\u2014\u2014\u3002\uff1f\uff01 ZZZ",
                                  chapter_number=1)
        assert r["translated_text"] == ""

    def test_empty_response_is_discarded(self):
        r = _parse_write_response("", chapter_number=1)
        assert r["translated_text"] == ""

    def test_forty_letters_in_ten_words_is_rescued(self):
        body = "word " * 10                       # 10 words, 40 letters
        r = _parse_write_response(body, chapter_number=1)
        assert r["translated_text"] == body.strip()

    def test_thirty_nine_letters_is_discarded(self):
        body = "word " * 9 + "wor"                # 10 words, 39 letters
        r = _parse_write_response(body, chapter_number=1)
        assert r["translated_text"] == ""

    def test_json_envelope_still_wins_over_layer_five(self):
        r = _parse_write_response(
            '{"translated_text": "Clean output.", "new_terms_found": []}',
            chapter_number=1,
        )
        assert r["translated_text"] == "Clean output."


def _state(content: str) -> dict:
    return {
        "chapter_title": "Test Chapter",
        "chapter_content": content,
        "chapter_number": 1,
        "target_lang": "en-US",
        "genre": "folk_religion",
        "exact_glossary": {},
        "semantic_terms": [],
        "exact_matches_text": "",
        "semantic_matches_text": "",
        "translated_text": "",
        "new_terms_found": [],
        "adaptation_notes": [],
        "chapter_summary": "",
        "previous_chapter_summary": "",
        "quality_score": 5.0,
        "quality_issues": [],
        "retranslation_count": 0,
        "glossary_snapshot_json": "",
        "read_analysis": {},
        "readback_feedback": {},
        "context_signals": "",
        "image_gaps": [],
        "style_memo": "",
        "skip_readback": False,
        "use_flash_writer": False,
        "cold_read_context": "",
        "term_conflicts": [],
        "resolved_conflicts": [],
        "dialect_context": "",
    }


_LONG_SOURCE = "\u5979\u62c9\u5f00\u4e86\u95e8\u3002" * 200


class TestWriteNodeTruncation:
    """End-to-end: a truncated model response must not become the chapter."""

    def _invoke_with(self, metadata):
        resp = _Resp(content='{"translated_text": "' + ("partial " * 60) + '"}',
                     metadata=metadata)
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = resp
        return mock_llm

    def test_truncated_response_is_not_shipped(self):
        with patch("src.agent.nodes.write.ChatOpenAI") as cls, \
             patch("src.agent.nodes.write.job_store") as js:
            js.get_confirmed_terms.return_value = {}
            js.get_rejected_terms.return_value = []
            cls.return_value = self._invoke_with({"finish_reason": "length"})
            result = write_node(_state(_LONG_SOURCE))
        # Every attempt was truncated, so nothing survives to be written.
        assert result["translated_text"] == ""

    def test_complete_response_is_shipped(self):
        with patch("src.agent.nodes.write.ChatOpenAI") as cls, \
             patch("src.agent.nodes.write.job_store") as js:
            js.get_confirmed_terms.return_value = {}
            js.get_rejected_terms.return_value = []
            cls.return_value = self._invoke_with({"finish_reason": "stop"})
            result = write_node(_state(_LONG_SOURCE))
        assert "partial" in result["translated_text"]


class TestWriteNodeConfiguration:
    """Pin the LLM construction and the retry contract.

    These constants (temperature, max_tokens, request_timeout, RETRY_THRESHOLD)
    are exactly the "shared constant" case the project's iron rule warns about:
    nothing asserted them, so a mutation to any of them left the suite green.
    Hardcoded literals on purpose — referencing the imported name would let a
    mutation of the constant carry the assertion along with it.
    """

    def _kwargs(self, metadata=None):
        with patch("src.agent.nodes.write.ChatOpenAI") as cls, \
             patch("src.agent.nodes.write.job_store") as js:
            js.get_confirmed_terms.return_value = {}
            js.get_rejected_terms.return_value = []
            mock_llm = MagicMock()
            resp = _Resp(content='{"translated_text": "' + ("word " * 40) + '"}',
                         metadata=metadata or {"finish_reason": "stop"})
            mock_llm.invoke.return_value = resp
            cls.return_value = mock_llm
            write_node(_state(_LONG_SOURCE))
        return cls.call_args.kwargs

    def test_temperature_is_zero_point_two(self):
        assert self._kwargs()["temperature"] == 0.2

    def test_max_tokens_is_16384(self):
        assert self._kwargs()["max_tokens"] == 16384

    def test_request_timeout_is_300(self):
        assert self._kwargs()["request_timeout"] == 300

    def test_retries_are_disabled_at_the_client(self):
        """The node runs its own bounded retry; a silent client retry would
        multiply cost and hide the failure from the retry accounting."""
        assert self._kwargs()["max_retries"] == 0

    def test_json_object_response_format_is_requested(self):
        assert self._kwargs()["model_kwargs"] == {
            "response_format": {"type": "json_object"}
        }

    def test_retry_threshold_is_ten(self):
        from src.agent.nodes.write import RETRY_THRESHOLD
        assert RETRY_THRESHOLD == 10


class TestWriteNodeRetryContract:
    def _run(self, text):
        calls = {"n": 0}
        with patch("src.agent.nodes.write.ChatOpenAI") as cls, \
             patch("src.agent.nodes.write.job_store") as js:
            js.get_confirmed_terms.return_value = {}
            js.get_rejected_terms.return_value = []

            def _invoke(_messages):
                calls["n"] += 1
                return _Resp(
                    content='{"translated_text": "%s"}' % text,
                    metadata={"finish_reason": "stop"},
                )

            mock_llm = MagicMock()
            mock_llm.invoke.side_effect = _invoke
            cls.return_value = mock_llm
            result = write_node(_state(_LONG_SOURCE))
        return calls["n"], result

    def test_exactly_ten_chars_is_accepted_without_retry(self):
        n, result = self._run("1234567890")
        assert n == 1
        assert result["translated_text"] == "1234567890"

    def test_nine_chars_triggers_the_full_retry_budget(self):
        n, _ = self._run("123456789")
        assert n == 3                      # 1 attempt + 2 retries

    def test_empty_output_uses_the_same_budget(self):
        n, _ = self._run("")
        assert n == 3

    def test_short_retry_output_is_not_accepted(self):
        n, result = self._run("ab")
        assert n == 3
        assert len(result["translated_text"].strip()) < 10


class TestChineseResidueHandling:
    """The strip only applies to non-Chinese targets."""

    def _run(self, target):
        with patch("src.agent.nodes.write.ChatOpenAI") as cls, \
             patch("src.agent.nodes.write.job_store") as js:
            js.get_confirmed_terms.return_value = {}
            js.get_rejected_terms.return_value = []
            mock_llm = MagicMock()
            mock_llm.invoke.return_value = _Resp(
                content='{"translated_text": "' + ("Hello \u4f60\u597d " * 40) + '"}',
                metadata={"finish_reason": "stop"},
            )
            cls.return_value = mock_llm
            st = _state(_LONG_SOURCE)
            st["target_lang"] = target
            return write_node(st)["translated_text"]

    def test_chinese_residue_is_stripped_for_english_target(self):
        assert "\u4f60\u597d" not in self._run("en-US")

    def test_chinese_residue_is_kept_for_chinese_target(self):
        assert "\u4f60\u597d" in self._run("zh-CN")


class TestFormatImageGaps:
    def test_empty_gaps_yield_the_placeholder(self):
        from src.agent.nodes.write import _format_image_gaps
        assert _format_image_gaps([]) == "(No image gaps detected — write freely.)"

    def test_priorities_are_grouped(self):
        from src.agent.nodes.write import _format_image_gaps
        out = _format_image_gaps([
            {"priority": "critical", "passage": "C1"},
            {"priority": "high", "passage": "H1"},
            {"priority": "medium", "passage": "M1"},
        ])
        assert "CRITICAL" in out and "C1" in out
        assert "HIGH" in out and "H1" in out
        assert "MEDIUM" in out and "M1" in out

    def test_unknown_priority_is_omitted(self):
        from src.agent.nodes.write import _format_image_gaps
        out = _format_image_gaps([{"priority": "trivial", "passage": "T1"}])
        assert "T1" not in out
        assert "CRITICAL" not in out


class TestFormatReadAnalysisFidelityBrief:
    def test_only_name_and_address_rules_reach_the_prompt(self):
        captured = {}
        with patch("src.agent.nodes.write.load_fidelity_rules",
                          return_value={"character_names": "N", "terms_of_address": "A",
                                        "unrelated_section": "SHOULD_NOT_APPEAR"}), \
             patch("src.agent.nodes.write.format_fidelity_for_prompt",
                          side_effect=lambda d: captured.update(d) or "rules"), \
             patch("src.agent.nodes.write.ChatOpenAI") as cls, \
             patch("src.agent.nodes.write.job_store") as js:
            js.get_confirmed_terms.return_value = {}
            js.get_rejected_terms.return_value = []
            mock_llm = MagicMock()
            mock_llm.invoke.return_value = _Resp(
                content='{"translated_text": "' + ("word " * 40) + '"}',
                metadata={"finish_reason": "stop"})
            cls.return_value = mock_llm
            write_node(_state(_LONG_SOURCE))
        assert set(captured) == {"character_names", "terms_of_address"}


class TestFenceStripping:
    def test_json_inside_a_closed_fence_is_parsed(self):
        r = _parse_write_response('```json\n{"translated_text": "X"}\n```')
        assert r["translated_text"] == "X"

    def test_json_inside_an_unterminated_fence_is_parsed(self):
        r = _parse_write_response('```json\n{"translated_text": "Y"}')
        assert r["translated_text"] == "Y"


class TestGlossaryReachesCompletenessCheck:
    def test_glossary_is_passed_through(self):
        from src.agent.nodes import write as wmod
        with patch("src.agent.nodes.write.check_completeness") as cc, \
             patch("src.agent.nodes.write.ChatOpenAI") as cls, \
             patch("src.agent.nodes.write.job_store") as js:
            cc.return_value = {"flags": [], "length_ratio": 5.0,
                               "dialogue_src": 0, "dialogue_out": 0}
            js.get_confirmed_terms.return_value = {}
            js.get_rejected_terms.return_value = []
            mock_llm = MagicMock()
            mock_llm.invoke.return_value = _Resp(
                content='{"translated_text": "' + ("word " * 40) + '"}',
                metadata={"finish_reason": "stop"})
            cls.return_value = mock_llm
            st = _state(_LONG_SOURCE)
            st["exact_glossary"] = {"\u5927\u8ba1": "Da Ji"}
            write_node(st)
        assert cc.call_args.args[2] == {"\u5927\u8ba1": "Da Ji"}
