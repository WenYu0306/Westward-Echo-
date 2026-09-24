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


# ═══════════════════════════════════════════════════════════════════
# Fallback-path guards. These were found by mutation testing: the
# primary path was covered, the safety nets behind it were not — and a
# safety net nobody tests is a safety net that can be silently deleted.
# ═══════════════════════════════════════════════════════════════════

class TestContentFilterDetection:
    """`_is_content_filter_error` decides whether a GLM rejection falls back to
    Qwen instead of losing the chapter. If it ever returns falsy, a 1301 kills
    the chapter — the exact failure that lost 间客 chapter 1 on the GLM run."""

    def test_zhipu_error_code_1301_is_detected(self):
        from src.agent.nodes.write import _is_content_filter_error
        exc = Exception("Error code: 400 - {'error': {'code': '1301', 'message': 'filtered'}}")
        assert _is_content_filter_error(exc) is True

    def test_content_filter_keyword_is_detected(self):
        from src.agent.nodes.write import _is_content_filter_error
        exc = Exception("Error code: 400 - {'contentFilter': [{'level': 1, 'role': 'assistant'}]}")
        assert _is_content_filter_error(exc) is True

    def test_chinese_unsafe_marker_is_detected(self):
        from src.agent.nodes.write import _is_content_filter_error
        assert _is_content_filter_error(Exception("内容不安全，已拦截")) is True

    def test_timeout_is_not_a_content_filter(self):
        from src.agent.nodes.write import _is_content_filter_error
        assert _is_content_filter_error(Exception("Request timed out after 300s")) is False

    def test_rate_limit_is_not_a_content_filter(self):
        from src.agent.nodes.write import _is_content_filter_error
        assert _is_content_filter_error(Exception("429 Too Many Requests")) is False

    def test_auth_error_is_not_a_content_filter(self):
        from src.agent.nodes.write import _is_content_filter_error
        assert _is_content_filter_error(Exception("401 invalid api key")) is False


class TestFallbackWriterConfig:
    """The fallback writer must carry the same guards as the primary one:
    its whole purpose is to be used when the primary provider refuses."""

    def _kwargs(self, api_key="sk-test"):
        from src.agent.nodes.write import _build_fallback_writer
        from src.config import node_llm_kwargs
        with patch("src.agent.nodes.write.ChatOpenAI") as cls:
            _build_fallback_writer(
                **node_llm_kwargs("write_fallback", api_key_override=api_key)
            )
        return cls.call_args.kwargs

    def test_model_is_the_routed_fallback_model(self):
        from src.config import node_model
        assert self._kwargs()["model"] == node_model("write_fallback")

    def test_temperature_is_zero_point_two(self):
        assert self._kwargs()["temperature"] == 0.2

    def test_max_tokens_is_16384(self):
        assert self._kwargs()["max_tokens"] == 16384

    def test_request_timeout_is_300(self):
        assert self._kwargs()["request_timeout"] == 300

    def test_retries_are_disabled_at_the_client(self):
        assert self._kwargs()["max_retries"] == 0

    def test_json_object_response_format_is_requested(self):
        assert self._kwargs()["model_kwargs"] == {"response_format": {"type": "json_object"}}

    def test_uses_thinking_budget_not_reasoning_effort(self):
        """Qwen gets an explicit thinking_budget; reasoning_effort is GLM-only."""
        from src.config import QWEN_THINKING_BUDGET
        kw = self._kwargs()
        assert kw["extra_body"] == {"thinking_budget": QWEN_THINKING_BUDGET}
        assert "reasoning_effort" not in kw

    def test_the_caller_supplied_key_is_the_one_used(self):
        assert self._kwargs(api_key="sk-user-owned")["api_key"] == "sk-user-owned"


class TestByokKeyPrecedence:
    """A user-supplied key must win over the operator's key.

    Mutation testing showed `state.get("api_key") or GLM_API_KEY` could be
    inverted without a single test failing — which would silently bill the
    operator for a BYOK user's chapters."""

    def _constructed_kwargs(self, state):
        with patch("src.agent.nodes.write.ChatOpenAI") as cls, \
             patch("src.agent.nodes.write.job_store") as js:
            js.get_confirmed_terms.return_value = {}
            js.get_rejected_terms.return_value = []
            mock_llm = MagicMock()
            mock_llm.invoke.return_value = _Resp(
                content='{"translated_text": "' + ("word " * 40) + '"}',
                metadata={"finish_reason": "stop"})
            cls.return_value = mock_llm
            try:
                write_node(state)
            except Exception:
                pass
        return cls.call_args.kwargs if cls.call_args else {}

    def test_user_key_overrides_the_operator_key(self):
        st = _state(_LONG_SOURCE)
        st["api_key"] = "sk-user-owned"
        assert self._constructed_kwargs(st)["api_key"] == "sk-user-owned"

    def test_operator_key_used_when_the_state_has_none(self):
        from src.config import GLM_API_KEY
        assert self._constructed_kwargs(_state(_LONG_SOURCE))["api_key"] == GLM_API_KEY


class TestRetryPathChineseGuard:
    """The Chinese-residue strip on the RETRY path had no test at all: only the
    first response was covered. For a zh-CN target the strip must not run."""

    def _run_retry(self, target_lang):
        short = _Resp(content='{"translated_text": ""}', metadata={"finish_reason": "stop"})
        retry = _Resp(
            content='{"translated_text": "' + ("word " * 40) + '这里有中文"}',
            metadata={"finish_reason": "stop"})

        # The residue helpers are imported *inside* the function from
        # output_guard, so they must be patched at their source module. Only the
        # retry text carries Chinese — the empty first reply does not — so a
        # pass here cannot come from the primary path.
        def _has_chinese(text):
            return "这里有中文" in text

        with patch("src.agent.nodes.write.ChatOpenAI") as cls, \
             patch("src.agent.nodes.write.job_store") as js, \
             patch("src.output_guard.has_untranslated_chinese", side_effect=_has_chinese), \
             patch("src.output_guard.strip_chinese_residue") as scr:
            js.get_confirmed_terms.return_value = {}
            js.get_rejected_terms.return_value = []
            scr.return_value = ("cleaned", 3)
            mock_llm = MagicMock()
            mock_llm.invoke.side_effect = [short, retry]
            cls.return_value = mock_llm
            st = _state(_LONG_SOURCE)
            st["target_lang"] = target_lang
            write_node(st)
        return scr

    def test_retry_strips_residue_for_english_target(self):
        scr = self._run_retry("en-US")
        assert scr.called, "retry text with Chinese residue was not stripped for en-US"

    def test_retry_keeps_residue_for_chinese_target(self):
        scr = self._run_retry("zh-CN")
        assert scr.called is False, "residue must NOT be stripped when the target is Chinese"


class TestFormatImageGaps:
    def test_no_gaps_returns_a_placeholder(self):
        from src.agent.nodes.write import _format_image_gaps
        assert _format_image_gaps([]) == "(No image gaps detected — write freely.)"

    def test_gap_fields_reach_the_prompt(self):
        from src.agent.nodes.write import _format_image_gaps
        out = _format_image_gaps([{
            "priority": "critical", "passage": "PASSAGE-X",
            "cn_reader_sees": "CN-SEES", "en_reader_gets": "EN-GETS",
            "sensory_anchors": "ANCHOR-Z",
        }])
        for marker in ("PASSAGE-X", "CN-SEES", "EN-GETS", "ANCHOR-Z"):
            assert marker in out, marker
        assert "CRITICAL" in out

    def test_priorities_are_separated(self):
        from src.agent.nodes.write import _format_image_gaps
        out = _format_image_gaps([
            {"priority": "critical", "passage": "C1"},
            {"priority": "medium", "passage": "M1"},
        ])
        assert out.index("C1") < out.index("M1")


class TestCaptureHelpersSemantics:
    """A complete payload feeds both the ledger and the legacy counters; an
    incomplete one feeds only the ledger (where the gap stays countable)."""

    def test_complete_payload_updates_both(self):
        from src.agent.nodes import write as wmod
        resp = _Resp(content="x", metadata={"token_usage": {
            "prompt_tokens": 100, "completion_tokens": 50}})
        with patch.object(wmod.token_ledger, "record") as led, \
             patch.object(wmod.TranslationStats, "record_tokens") as legacy:
            wmod._capture_response_tokens(resp, "glm-5.3-flash")
        assert led.call_args.args == ("write", "glm-5.3-flash", 100, 50)
        assert legacy.call_args.kwargs["input_tokens"] == 100
        assert legacy.call_args.kwargs["output_tokens"] == 50

    def test_missing_payload_records_unmetered_and_skips_legacy(self):
        from src.agent.nodes import write as wmod
        resp = _Resp(content="x", metadata={})
        with patch.object(wmod.token_ledger, "record") as led, \
             patch.object(wmod.TranslationStats, "record_tokens") as legacy:
            wmod._capture_response_tokens(resp, "glm-5.3-flash")
        assert led.call_args.args == ("write", "glm-5.3-flash", None, None)
        assert legacy.called is False

    def test_partial_payload_skips_the_legacy_counter(self):
        from src.agent.nodes import write as wmod
        resp = _Resp(content="x", metadata={"token_usage": {"prompt_tokens": 100}})
        with patch.object(wmod.token_ledger, "record") as led, \
             patch.object(wmod.TranslationStats, "record_tokens") as legacy:
            wmod._capture_response_tokens(resp, "glm-5.3-flash")
        assert led.call_args.args == ("write", "glm-5.3-flash", 100, None)
        assert legacy.called is False, "half a payload must not be summed"

    def test_read_helper_attributes_the_book_and_chapter(self):
        from src.agent.nodes import read as rmod
        resp = _Resp(content="x", metadata={"token_usage": {
            "prompt_tokens": 7, "completion_tokens": 8}})
        with patch.object(rmod.token_ledger, "record") as led, \
             patch.object(rmod.TranslationStats, "record_tokens"):
            rmod._capture_read_tokens(resp, "qwen3.8-flash", book_id="difu", chapter=173)
        assert led.call_args.args[0] == "read"
        # The model must be the one passed in, not a module constant: READ is
        # re-tierable, and booking a flash chapter against a hard-coded max name
        # would put its cost on the wrong model.
        assert led.call_args.args[1] == "qwen3.8-flash"
        assert led.call_args.kwargs == {"book_id": "difu", "chapter": 173}

    def test_readback_helper_books_the_model_it_is_given(self):
        from src.agent.nodes import readback as rbmod
        resp = _Resp(content="x", metadata={"token_usage": {
            "prompt_tokens": 7, "completion_tokens": 8}})
        with patch.object(rbmod.token_ledger, "record") as led, \
             patch.object(rbmod.TranslationStats, "record_tokens"):
            rbmod._capture_readback_tokens(resp, "qwen3.8-flash", book_id="difu", chapter=9)
        assert led.call_args.args == ("readback", "qwen3.8-flash", 7, 8)
        assert led.call_args.kwargs == {"book_id": "difu", "chapter": 9}

    def test_fix_helper_books_the_model_it_is_given(self):
        from src.agent.nodes import fix as fmod
        resp = _Resp(content="x", metadata={"token_usage": {
            "prompt_tokens": 7, "completion_tokens": 8}})
        with patch.object(fmod.token_ledger, "record") as led, \
             patch.object(fmod.TranslationStats, "record_tokens"):
            fmod._capture_fix_tokens(resp, "glm-5.3-flash", book_id="difu", chapter=9)
        assert led.call_args.args == ("fix", "glm-5.3-flash", 7, 8)
        assert led.call_args.kwargs == {"book_id": "difu", "chapter": 9}


class TestFidelityRulesFallback:
    """When the language has no fidelity rules, the prompt must say so rather
    than silently adjoining an empty string (mutation found this `or` could be
    inverted with no test failing)."""

    def _user_prompt(self, formatter_return):
        with patch("src.agent.nodes.write.format_fidelity_for_prompt",
                   return_value=formatter_return), \
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
        messages = mock_llm.invoke.call_args.args[0]
        return messages[-1].content

    def test_empty_rules_produce_an_explicit_placeholder(self):
        prompt = self._user_prompt("")
        assert "(No cultural fidelity rules for this language.)" in prompt

    def test_real_rules_are_carried_through(self):
        prompt = self._user_prompt("NAME-RULE-XYZ")
        assert "NAME-RULE-XYZ" in prompt
        assert "(No cultural fidelity rules for this language.)" not in prompt


class TestFlashWriterFlagIsAccountingOnly:
    """`use_flash_writer` used to look like a model switch. It is not: WRITE runs
    on GLM_MODEL either way, and the flag only labelled the legacy counters
    ("flash"/"pro"). These tests pin that the flag cannot change the model, so a
    future reader cannot mistake it for a routing switch."""

    def _run(self, state):
        from src.agent.nodes import write as wmod
        with patch("src.agent.nodes.write.ChatOpenAI") as cls, \
             patch("src.agent.nodes.write.job_store") as js, \
             patch.object(wmod.TranslationStats, "record_tokens") as legacy:
            js.get_confirmed_terms.return_value = {}
            js.get_rejected_terms.return_value = []
            mock_llm = MagicMock()
            mock_llm.invoke.return_value = _Resp(
                content='{"translated_text": "' + ("word " * 40) + '"}',
                metadata={"finish_reason": "stop",
                          "token_usage": {"prompt_tokens": 5, "completion_tokens": 6}})
            cls.return_value = mock_llm
            write_node(state)
        return cls.call_args.kwargs, legacy

    def test_absent_flag_defaults_to_off_and_pro_tier(self):
        st = _state(_LONG_SOURCE)
        st.pop("use_flash_writer")
        kwargs, legacy = self._run(st)
        assert legacy.call_args.kwargs["tier"] == "pro"

    def test_flag_switches_only_the_accounting_label(self):
        from src.config import GLM_MODEL
        st = _state(_LONG_SOURCE)
        st["use_flash_writer"] = True
        kwargs, legacy = self._run(st)
        assert kwargs["model"] == GLM_MODEL      # unchanged by the flag
        assert legacy.call_args.kwargs["tier"] == "flash"

    def test_model_is_identical_with_and_without_the_flag(self):
        off = _state(_LONG_SOURCE); off["use_flash_writer"] = False
        on = _state(_LONG_SOURCE); on["use_flash_writer"] = True
        assert self._run(off)[0]["model"] == self._run(on)[0]["model"]


class TestParseResponseFenceHandling:
    """A fenced reply with a closing ``` takes the [1:-1] branch; one without a
    closing fence must not lose its body. Mutation found the slice index was
    free to change with nothing failing."""

    def test_fenced_json_with_closing_fence_parses(self):
        body = '{"translated_text": "hello world"}'
        out = _parse_write_response("```json\n" + body + "\n```")
        assert out["translated_text"] == "hello world"

    def test_fenced_json_without_closing_fence_keeps_its_body(self):
        body = '{"translated_text": "no closing fence"}'
        out = _parse_write_response("```json\n" + body)
        assert out["translated_text"] == "no closing fence"

    def test_plain_json_is_unaffected(self):
        out = _parse_write_response('{"translated_text": "plain"}')
        assert out["translated_text"] == "plain"


class TestFormatReadAnalysis:
    def test_sections_are_joined_and_labelled(self):
        from src.agent.nodes.write import _format_read_analysis
        out = _format_read_analysis({
            "emotional_arc": "ARC-XYZ",
            "crafted_moments": ["MOMENT-1", "MOMENT-2"],
            "pacing_notes": "PACE-ABC",
        })
        for marker in ("ARC-XYZ", "MOMENT-1", "MOMENT-2", "PACE-ABC"):
            assert marker in out, marker
        assert out.count("\n\n") >= 2        # sections are separated, not concatenated

    def test_empty_analysis_is_empty_string(self):
        from src.agent.nodes.write import _format_read_analysis
        assert _format_read_analysis({}) == ""


class TestImageGapPriorityBuckets:
    """Each priority bucket must be selected by its own label."""

    def test_high_priority_gap_reaches_the_prompt(self):
        from src.agent.nodes.write import _format_image_gaps
        out = _format_image_gaps([{"priority": "high", "passage": "HIGH-ONLY"}])
        assert "HIGH-ONLY" in out

    def test_high_bucket_does_not_swallow_critical(self):
        from src.agent.nodes.write import _format_image_gaps
        out = _format_image_gaps([{"priority": "critical", "passage": "CRIT-ONLY"}])
        assert "HIGH" not in out.split("CRIT-ONLY")[0].replace("HIGH-ONLY", "")

    def test_all_three_buckets_reach_the_prompt(self):
        from src.agent.nodes.write import _format_image_gaps
        out = _format_image_gaps([
            {"priority": "critical", "passage": "P-CRIT"},
            {"priority": "high", "passage": "P-HIGH"},
            {"priority": "medium", "passage": "P-MED"},
        ])
        for marker in ("P-CRIT", "P-HIGH", "P-MED"):
            assert marker in out, marker


class TestFallbackWriterKeyPrecedence:
    """The Qwen fallback (used only after GLM refuses with 1301) must also honour
    a BYOK key. Mutation showed `state.get("api_key") or LLM_API_KEY` on that
    path could be inverted with no test failing."""

    def _fallback_call(self, state):
        from src.agent.nodes import write as wmod
        with patch("src.agent.nodes.write.ChatOpenAI") as cls, \
             patch("src.agent.nodes.write.job_store") as js, \
             patch("src.agent.nodes.write._build_fallback_writer") as builder:
            js.get_confirmed_terms.return_value = {}
            js.get_rejected_terms.return_value = []
            primary = MagicMock()
            primary.invoke.side_effect = Exception(
                "Error code: 400 - {'error': {'code': '1301'}}")
            fallback = MagicMock()
            fallback.invoke.return_value = _Resp(
                content='{"translated_text": "' + ("word " * 40) + '"}',
                metadata={"finish_reason": "stop"})
            cls.return_value = primary
            builder.return_value = fallback
            write_node(state)
        return builder.call_args.kwargs

    def test_byok_key_is_used_for_the_fallback(self):
        st = _state(_LONG_SOURCE)
        st["api_key"] = "sk-user-owned"
        assert self._fallback_call(st)["api_key"] == "sk-user-owned"

    def test_operator_key_is_used_when_state_has_none(self):
        # The key is resolved by node_llm_kwargs for whichever provider the
        # fallback is routed to, so assert against that rather than a constant:
        # re-tiering write_fallback onto GLM must not make this test lie.
        from src.config import node_llm_kwargs
        expected = node_llm_kwargs("write_fallback")["api_key"]
        assert self._fallback_call(_state(_LONG_SOURCE))["api_key"] == expected


class TestFallbackMustLeaveTheRefusingProvider:
    """The 1301 filter is deterministic on the same text, so retrying the same
    provider cannot succeed — the fallback only earns its name by crossing to
    the other one. Mutation survived routing `write_fallback` onto `write`'s
    tier until this was asserted, because the builder itself is mocked here and
    the config-level test cannot see which node the 1301 path asks for.

    Tier choice stays free (qwen3.8-flash is a legitimate cheaper fallback);
    what must not change is the provider.
    """

    def _fallback_kwargs_seen_by_the_node(self, state):
        """Run the 1301 path and capture the kwargs the node hands the builder.

        The spy must not call the real builder: ChatOpenAI is patched to return
        the *primary* (refusing) client, so delegating would raise 1301 again
        from inside the except block. It records the routing kwargs and returns
        a separate client that answers normally.
        """
        seen = {}

        fallback_client = MagicMock()
        fallback_client.invoke.return_value = _Resp(
            content='{"translated_text": "' + ("word " * 40) + '"}',
            metadata={"finish_reason": "stop"})

        def spy(**kwargs):
            seen.update(kwargs)
            return fallback_client

        with patch("src.agent.nodes.write.ChatOpenAI") as cls, \
             patch("src.agent.nodes.write.job_store") as js, \
             patch("src.agent.nodes.write._build_fallback_writer", side_effect=spy):
            js.get_confirmed_terms.return_value = {}
            js.get_rejected_terms.return_value = []
            primary = MagicMock()
            primary.invoke.side_effect = Exception(
                "Error code: 400 - {'error': {'code': '1301'}}")
            cls.return_value = primary
            write_node(state)
        return seen

    def test_fallback_endpoint_differs_from_the_primary_endpoint(self):
        from src.config import node_llm_kwargs
        seen = self._fallback_kwargs_seen_by_the_node(_state(_LONG_SOURCE))
        primary = node_llm_kwargs("write")
        assert seen["base_url"] != primary["base_url"], (
            "the 1301 fallback must not retry the provider that just refused"
        )

    def test_fallback_model_differs_from_the_primary_model(self):
        from src.config import node_model
        seen = self._fallback_kwargs_seen_by_the_node(_state(_LONG_SOURCE))
        assert seen["model"] != node_model("write")

    def test_fallback_model_is_the_routed_fallback_tier(self):
        from src.config import node_model
        seen = self._fallback_kwargs_seen_by_the_node(_state(_LONG_SOURCE))
        assert seen["model"] == node_model("write_fallback")


class TestFenceHandlingDoesNotPolluteTelemetry:
    """A well-formed fenced reply must be parsed by Layer 1 (strict JSON).

    Mutation testing surfaced a surviving mutant that changed the closing-fence
    check from ``lines[-1]`` to ``lines[-2]``. The parsed chapter is identical —
    which is why no wording test caught it — but the reply then falls through to
    Layer 2 (regex rescue) and writes a ``parse_fallback`` event for text that
    was never malformed. The error tracker is the record this project uses to
    judge the toolchain, so false fallbacks there are a real defect, not a
    harmless refactor.
    """

    def _parse(self, content):
        from src.agent.nodes import write as wmod
        with patch("src.error_tracker.record_event") as ev:
            out = wmod._parse_write_response(content)
        return out, ev

    def test_well_formed_fenced_reply_writes_no_fallback_event(self):
        body = '{"translated_text": "hello world"}'
        out, ev = self._parse("```json\n" + body + "\n```")
        assert out["translated_text"] == "hello world"
        assert ev.called is False, (
            "a perfectly closed fence was reported as a parser fallback: "
            f"{ev.call_args.args[3] if ev.called else ''}"
        )

    def test_unfenced_reply_writes_no_fallback_event(self):
        out, ev = self._parse('{"translated_text": "plain"}')
        assert out["translated_text"] == "plain"
        assert ev.called is False

    def test_genuinely_broken_reply_still_reports_a_fallback(self):
        """The event must still fire when the reply really is malformed."""
        out, ev = self._parse('Here you go:\n```json\n{"translated_text": "rescued"}\n```')
        assert out["translated_text"] == "rescued"
        assert ev.called is True
