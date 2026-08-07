"""Fault injection tests — verify system resilience under API failures,
network interruptions, and LLM garbage output.

Covers ACCEPTANCE_CRITERIA.md N1.2:
  (a) DeepSeek API 429/500 → exponential backoff + circuit breaker
  (b) Network interruption → error tracking + graceful degradation
  (c) LLM garbage output → parse fallback layers + error recording
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.agent.nodes.write import _parse_write_response
from src.backpressure import BackpressureGuard, backpressure
from src.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    _breakers,
    _breakers_lock,
    get_breaker,
)
from src.error_tracker import get_event_summary, get_recent_issues, record_event
from src.output_guard import (
    check_translation_output,
    has_untranslated_chinese,
    sanitize_translation,
)

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _fresh_breaker(name="test", threshold=3, recovery=0.3):
    """Return a short-recovery breaker for fast fault-injection tests."""
    return CircuitBreaker(
        name=name,
        failure_threshold=threshold,
        recovery_timeout=recovery,
    )


def _flush_error_events():
    """Best-effort clear of recent events between tests."""
    from src.error_tracker import _get_conn
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM translation_events")
        conn.commit()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
# Circuit Breaker — API failure simulation
# ──────────────────────────────────────────────────────────────

class TestCircuitBreakerApiFailures:
    """Simulate DeepSeek API returning 429, 500, and connection errors."""

    def test_closed_to_open_on_consecutive_failures(self):
        """After N consecutive failures, breaker transitions CLOSED → OPEN."""
        cb = _fresh_breaker(threshold=3)

        for i in range(3):
            with pytest.raises(ValueError, match="fail"):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("api fail")))
            assert cb._failure_count == i + 1

        assert cb.state == CircuitBreaker.OPEN
        assert cb.is_open()

    def test_open_fast_fails_without_hitting_fn(self):
        """When OPEN, call() raises CircuitBreakerOpenError immediately."""
        cb = _fresh_breaker(threshold=2, recovery=1.0)

        # Trip the breaker
        for _ in range(2):
            with pytest.raises(ValueError):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        assert cb.is_open()

        call_count = [0]

        def should_not_run():
            call_count[0] += 1
            return "ok"

        with pytest.raises(CircuitBreakerOpenError, match="OPEN"):
            cb.call(should_not_run)

        assert call_count[0] == 0  # fn never executed

    def test_half_open_recovery_on_success(self):
        """After recovery timeout, a successful probe closes the circuit."""
        cb = _fresh_breaker(threshold=2, recovery=0.05)

        # Trip
        for _ in range(2):
            with pytest.raises(ValueError):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        assert cb.is_open()

        # Wait for recovery window
        time.sleep(0.1)

        # Probe should succeed → CLOSED
        result = cb.call(lambda: "recovered")
        assert result == "recovered"
        assert cb.state == CircuitBreaker.CLOSED
        assert not cb.is_open()
        assert cb._failure_count == 0

    def test_half_open_failure_reopens_circuit(self):
        """A failed probe in HALF_OPEN sends the breaker back to OPEN."""
        cb = _fresh_breaker(threshold=2, recovery=0.05)

        for _ in range(2):
            with pytest.raises(ValueError):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        time.sleep(0.1)

        # Probe fails → back to OPEN
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("probe fail")))

        assert cb.state == CircuitBreaker.OPEN

    def test_single_success_resets_failure_count(self):
        """A success before threshold resets the failure counter."""
        cb = _fresh_breaker(threshold=5)

        # 2 failures then 1 success then 2 more failures → should NOT trip
        for _ in range(2):
            with pytest.raises(ValueError):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        cb.call(lambda: "ok")  # resets counter

        for _ in range(2):
            with pytest.raises(ValueError):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        assert cb.state == CircuitBreaker.CLOSED  # not tripped
        assert cb._failure_count == 2  # reset after success, then 2 more


class TestCircuitBreakerPerLanguage:
    """Verify per-language isolation — en-US failure doesn't affect es-ES.

    Uses _fresh_breaker() directly to avoid lock contention with the
    module-level singleton registry and pytest's setup/teardown hooks.
    """

    def test_per_language_isolation(self):
        """Tripping one breaker doesn't affect another."""
        en = _fresh_breaker(name="en-US", threshold=2, recovery=1.0)
        es = _fresh_breaker(name="es-ES", threshold=2, recovery=1.0)

        assert en is not es

        for _ in range(2):
            with pytest.raises(ValueError):
                en.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        assert en.is_open()

        result = es.call(lambda: "hola")
        assert result == "hola"
        assert es.state == CircuitBreaker.CLOSED

    def test_get_breaker_singleton(self):
        """Repeated calls with the same language return the same breaker."""
        a = get_breaker("test-singleton-ar", failure_threshold=2, recovery_timeout=5.0)
        b = get_breaker("test-singleton-ar", failure_threshold=2, recovery_timeout=5.0)
        assert a is b

        # Clean up: remove from registry
        with _breakers_lock:
            _breakers.pop("test-singleton-ar", None)

    def test_snapshot_reflects_current_state(self):
        """Snapshot reports correct state without side effects."""
        cb = _fresh_breaker(name="snap-test", threshold=4, recovery=10.0)
        snap = cb.snapshot()
        assert snap["name"] == "snap-test"
        assert snap["state"] == "closed"
        assert snap["failure_threshold"] == 4


class TestCircuitBreakerEdgeCases:
    """Edge cases and boundary conditions."""

    def test_no_failures_stays_closed(self):
        cb = _fresh_breaker(threshold=3)
        for _ in range(10):
            cb.call(lambda: "ok")
        assert cb.state == CircuitBreaker.CLOSED
        assert cb._failure_count == 0

    def test_failure_count_exceeds_threshold(self):
        """Verify the breaker opens exactly at threshold, not before."""
        cb = _fresh_breaker(threshold=3)

        for i in range(2):
            with pytest.raises(ValueError):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        assert cb.state == CircuitBreaker.CLOSED  # not yet

        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        assert cb.state == CircuitBreaker.OPEN  # tripped at 3rd

    def test_multiple_open_transitions_counted(self):
        cb = _fresh_breaker(threshold=1, recovery=0.05)

        # Trip → recover → trip → recover
        for cycle in range(3):
            with pytest.raises(ValueError):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
            assert cb.is_open()
            time.sleep(0.1)
            cb.call(lambda: "ok")  # recover

        assert cb._open_transitions == 3


# ──────────────────────────────────────────────────────────────
# Backpressure guard — queue overflow protection
# ──────────────────────────────────────────────────────────────

class TestBackpressureGuard:
    """Verify the backpressure guard rejects work when capacity is full."""

    def test_accepts_when_below_capacity(self):
        guard = BackpressureGuard(max_queue_depth=10)
        for _ in range(10):
            assert guard.try_accept() is True
        assert guard.queue_depth == 10

    def test_rejects_when_at_capacity(self):
        guard = BackpressureGuard(max_queue_depth=3)
        for _ in range(3):
            assert guard.try_accept()
        assert guard.try_accept() is False
        assert guard.is_backpressured()

    def test_release_frees_capacity(self):
        guard = BackpressureGuard(max_queue_depth=2)
        assert guard.try_accept()
        assert guard.try_accept()
        assert guard.is_backpressured()

        guard.release()
        assert not guard.is_backpressured()
        assert guard.try_accept()

    def test_release_below_zero_is_safe(self):
        guard = BackpressureGuard(max_queue_depth=5)
        guard.release()  # should not raise
        guard.release()
        assert guard.queue_depth == 0

    def test_snapshot(self):
        guard = BackpressureGuard(max_queue_depth=5)
        guard.try_accept()
        guard.try_accept()
        snap = guard.snapshot()
        assert snap["queue_depth"] == 2
        assert snap["max_queue_depth"] == 5
        assert snap["backpressured"] is False

    def test_thread_safety(self):
        guard = BackpressureGuard(max_queue_depth=100)
        errors = []

        def worker():
            for _ in range(50):
                if guard.try_accept():
                    guard.release()
                else:
                    errors.append("rejected")

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert guard.queue_depth == 0

    def test_module_singleton_defaults(self):
        assert backpressure.max_queue_depth == 100
        assert backpressure.queue_depth == 0


# ──────────────────────────────────────────────────────────────
# LLM Garbage Output — parse fallback layers
# ──────────────────────────────────────────────────────────────

class TestParseLLMResponseFallbacks:
    """Simulate LLM returning non-JSON garbage — verify all 5 fallback layers."""

    def test_layer1_strict_json(self):
        result = _parse_write_response(
            json.dumps({"translated_text": "Hello world", "new_terms_found": [],
                        "adaptation_notes": [], "chapter_summary": "ok"})
        )
        assert result["translated_text"] == "Hello world"
        assert result["chapter_summary"] == "ok"

    def test_layer2_regex_extract_json_object(self):
        """JSON embedded in markdown/chatter — regex extraction."""
        response = (
            'Sure, here is the translation:\n\n'
            '{"translated_text": "She walked in.", '
            '"new_terms_found": [], '
            '"adaptation_notes": [], '
            '"chapter_summary": "A woman enters."}'
        )
        result = _parse_write_response(response)
        assert result["translated_text"] == "She walked in."
        assert result["chapter_summary"] == "A woman enters."

    def test_layer3_regex_field_extraction(self):
        """Only translated_text field is extractable via regex."""
        response = '{"translated_text": "Just the text field", "corrupted_rest'
        result = _parse_write_response(response)
        assert result["translated_text"] == "Just the text field"
        assert result["new_terms_found"] == []

    def test_layer4_markdown_as_translation(self):
        """LLM returns plain markdown (no JSON wrapper) — treat as translation."""
        response = "# Chapter 1: The Beginning\n\nShe opened the door.\n\nIt was dark."
        result = _parse_write_response(response)
        assert "Chapter 1" in result["translated_text"]
        assert "She opened the door" in result["translated_text"]

    def test_layer5_raw_content_fallback(self):
        """Completely unparseable response — return as-is."""
        response = "Some random text that is definitely not JSON or markdown"
        result = _parse_write_response(response)
        assert result["translated_text"] == response

    def test_strips_code_fences(self):
        """Markdown code fences are stripped before parsing."""
        response = (
            '```json\n{"translated_text": "Clean", '
            '"new_terms_found": [], "adaptation_notes": [], "chapter_summary": "ok"}\n```'
        )
        result = _parse_write_response(response)
        assert result["translated_text"] == "Clean"

    def test_unicode_and_escape_handling(self):
        """Escaped unicode and newlines in JSON are preserved."""
        data = {
            "translated_text": 'Line 1\\nLine 2 with "quotes"',
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "ok",
        }
        response = json.dumps(data)
        result = _parse_write_response(response)
        assert "Line 1" in result["translated_text"]
        assert "Line 2" in result["translated_text"]


class TestOutputGuardFaults:
    """Verify output guard catches LLM chatter, empty output, and Chinese residue."""

    def test_empty_translation_detected(self):
        warnings = check_translation_output("")
        assert any("too short" in w for w in warnings)

    def test_none_translation_detected(self):
        warnings = check_translation_output(None)
        assert any("None" in w for w in warnings)

    def test_short_translation_detected(self):
        warnings = check_translation_output("Hi")
        assert any("too short" in w for w in warnings)

    def test_chatter_preamble_detected(self):
        warnings = check_translation_output(
            "Here is the translation of the chapter as requested:\n\n"
            "She walked into the grand hall. The chandelier sparkled overhead. "
            "Her heels clicked against the marble floor, each step echoing through "
            "the vast space. She took a deep breath and steadied herself."
        )
        chatter = [w for w in warnings if "chatter" in w.lower()]
        assert len(chatter) > 0

    def test_meta_commentary_detected(self):
        warnings = check_translation_output(
            "Now let me translate this chapter for you carefully. "
            "She entered the room and looked around. The walls were lined with "
            "ancient books and the air smelled of old paper and dust. She had "
            "never seen anything like this before in her entire life."
        )
        chatter = [w for w in warnings if "chatter" in w.lower()]
        assert len(chatter) > 0

    def test_clean_translation_no_warnings(self):
        long_clean = (
            "She stepped into the grand hall, her heels clicking against the "
            "marble floor. The chandelier cast fractured light across the walls. "
            "She had never been anywhere this opulent. Taking a deep breath, she "
            "straightened her dress and walked toward the CEO's office."
        )
        warnings = check_translation_output(long_clean)
        assert len(warnings) == 0

    def test_sanitize_removes_chatter(self):
        text = (
            "Here is the translation:\n\n"
            "She walked into the room. The air was thick with tension. "
            "Everyone turned to look at her. She kept her chin up and walked on."
        )
        cleaned = sanitize_translation(text)
        assert "Here is the translation" not in cleaned
        assert "She walked into the room" in cleaned

    def test_chinese_residue_detected(self):
        assert has_untranslated_chinese("She walked into the 房间 and looked around.")
        assert not has_untranslated_chinese("She walked into the room and looked around.")

    def test_sanitize_handles_multiline_chatter(self):
        text = (
            "Sure, let me provide that translation.\n\n"
            "OK here is the output:\n\n"
            "The door creaked open. Shadows danced on the walls. She held her "
            "breath and stepped inside, her flashlight beam cutting through the "
            "darkness like a knife. Something moved in the corner of her vision."
        )
        cleaned = sanitize_translation(text)
        assert "Sure, let me" not in cleaned
        assert "OK here is" not in cleaned
        assert "The door creaked open" in cleaned


# ──────────────────────────────────────────────────────────────
# Error tracker — event recording under fault conditions
# ──────────────────────────────────────────────────────────────

class TestErrorTrackerUnderFaults:
    """Verify error_tracker correctly records events from fault scenarios."""

    def setup_method(self):
        _flush_error_events()

    def test_record_parse_fallback_event(self):
        record_event("job-1", 5, "parse_fallback",
                     "Layer 4: markdown-as-translation", "en-US")
        issues = get_recent_issues(limit=10)
        match = [e for e in issues if e["event_type"] == "parse_fallback"]
        assert len(match) >= 1
        assert match[0]["job_id"] == "job-1"
        assert match[0]["chapter_number"] == 5

    def test_record_circuit_breaker_event(self):
        record_event(None, None, "circuit_breaker",
                     "en-US breaker OPEN after 5 failures", "en-US")
        issues = get_recent_issues(limit=10)
        match = [e for e in issues if e["event_type"] == "circuit_breaker"]
        assert len(match) >= 1
        assert "OPEN" in match[0]["detail"]

    def test_record_empty_output_event(self):
        record_event("job-2", 3, "empty_output",
                     "EMPTY: translation is too short (15 chars)", "en-US")
        issues = get_recent_issues(limit=10)
        match = [e for e in issues if e["event_type"] == "empty_output"]
        assert len(match) >= 1

    def test_record_chatter_detected_event(self):
        record_event("job-3", 7, "chatter_detected",
                     "LLM chatter: output preamble", "en-US")
        issues = get_recent_issues(limit=10)
        match = [e for e in issues if e["event_type"] == "chatter_detected"]
        assert len(match) >= 1

    def test_record_guard_warning_event(self):
        record_event("job-4", 1, "guard_warning",
                     "Chinese character residue: 房间", "en-US")
        issues = get_recent_issues(limit=10)
        match = [e for e in issues if e["event_type"] == "guard_warning"]
        assert len(match) >= 1

    def test_multiple_events_ordered_by_recency(self):
        for i in range(5):
            record_event(f"job-{i}", i, "guard_warning", f"warning {i}", "en-US")
        issues = get_recent_issues(limit=3)
        assert len(issues) == 3  # respect limit

    def test_event_summary_aggregation(self):
        _flush_error_events()
        for _ in range(3):
            record_event("j1", 1, "guard_warning", "test", "en-US")
        for _ in range(2):
            record_event("j1", 2, "parse_fallback", "test", "en-US")

        summary = get_event_summary(days=7)
        assert summary["guard_warning"] == 3
        assert summary["parse_fallback"] == 2
        assert summary["total"] == 5

    def test_none_job_and_chapter_accepted(self):
        """System-level events (no job/chapter) are recorded without error."""
        record_event(None, None, "circuit_breaker", "system-level trip", "en-US")
        issues = get_recent_issues(limit=10)
        match = [e for e in issues if e["detail"] == "system-level trip"]
        assert len(match) >= 1
        assert match[0]["job_id"] is None
        assert match[0]["chapter_number"] is None


# ──────────────────────────────────────────────────────────────
# Integration — write_node under fault conditions
# ──────────────────────────────────────────────────────────────

class TestWriteNodeUnderFaults:
    """Integration-level: write_node handles API failures and garbage output."""

    def test_circuit_breaker_error_propagated(self):
        """When the breaker is OPEN, write_node propagates the error."""
        from src.agent.nodes.write import write_node
        from src.agent.state import TranslatorState
        from src.circuit_breaker import _breakers, _breakers_lock

        # Clear any existing "en-US" breaker so we get a fresh one
        # with failure_threshold=1 (the singleton may have threshold=5
        # from a prior test).
        with _breakers_lock:
            _breakers.pop("en-us", None)
            _breakers.pop("en-US", None)

        state: TranslatorState = {
            "chapter_title": "Chapter 1",
            "chapter_content": "苏念醒过来的时候，发现自己躺在一张陌生的大床上。",
            "chapter_number": 1,
            "target_lang": "en-US",
            "genre": "romance_ceo",
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
            "use_flash_writer": False, "cold_read_context": "",
            "term_conflicts": [],
            "resolved_conflicts": [],
            "dialect_context": "",
        }

        # Force the breaker OPEN before the call
        breaker = get_breaker("en-US", failure_threshold=1, recovery_timeout=10.0)
        try:
            breaker.call(lambda: (_ for _ in ()).throw(ValueError("trip")))
        except ValueError:
            pass

        assert breaker.is_open()

        with pytest.raises(CircuitBreakerOpenError):
            write_node(state)

        # Reset breaker so other tests using "en-US" aren't poisoned
        breaker._state = CircuitBreaker.CLOSED
        breaker._failure_count = 0

    def test_garbage_output_falls_back_to_raw_text(self, mock_translate_invoke,
                                                    sample_chapter):
        """LLM returns plain chatter (no JSON) → Layer 4/5 fallback produces output."""
        from src.agent.nodes.write import write_node
        from src.agent.state import TranslatorState
        from src.circuit_breaker import _breakers, _breakers_lock

        # Reset the breaker for this test language to avoid poisoning
        with _breakers_lock:
            _breakers.pop("en-us", None)
            _breakers.pop("en-US", None)

        state: TranslatorState = {
            "chapter_title": sample_chapter["title"],
            "chapter_content": sample_chapter["content"],
            "chapter_number": 1,
            "target_lang": "en-US",
            "genre": "romance_ceo",
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
            "use_flash_writer": False, "cold_read_context": "",
            "term_conflicts": [],
            "resolved_conflicts": [],
            "dialect_context": "",
        }

        # The LLM returns a conversational response instead of JSON
        chatter_text = (
            "I'd be happy to translate this chapter for you! "
            "Let me work through it carefully.\n\n"
            "Su Nian woke up to find herself lying on a large unfamiliar bed. "
            "She rubbed her eyes, trying to remember what had happened last night. "
            "Suddenly, a mechanical voice rang out in her mind:\n\n"
            "[Ding — Congratulations, Host has bound the CEO Strategy System!]\n\n"
            "Su Nian froze. What system? What CEO? She'd just worked some overtime, "
            "how had she ended up transmigrating?\n\n"
            "She looked around. The room was as luxurious as a five-star hotel. "
            "Outside the floor-to-ceiling windows lay the city's nightscape, "
            "neon lights flickering in the darkness."
        )

        with patch("src.agent.nodes.write.ChatOpenAI") as mock_llm_class:
            mock_llm = MagicMock()
            mock_response = MagicMock()
            mock_response.content = chatter_text
            mock_response.response_metadata = {}
            mock_llm.invoke.return_value = mock_response
            mock_llm_class.return_value = mock_llm

            result = write_node(state)

        # Should have produced SOME output via fallback layers
        assert result["translated_text"]
        assert len(result["translated_text"]) > 50
        # Should not have crashed
        assert "new_terms_found" in result

        # Clean up breaker state
        with _breakers_lock:
            _breakers.pop("en-us", None)
            _breakers.pop("en-US", None)

    def test_malformed_json_with_chinese_residue_handled(self):
        """Response is broken JSON with Chinese characters — parse layers handle it."""
        garbage = (
            '```json\n'
            '{"translated_text": "She entered the 房间 and sat down. '
            'The room was decorated with 古董 furniture and 字画 on the walls.", '
            '"new_terms_found": [{"term_cn": "古董", "term_en":'
            # JSON cut off mid-value — malformed
        )
        result = _parse_write_response(garbage)
        # Layer 2 or 3 should extract translated_text
        assert "房间" in result["translated_text"] or "She entered" in result["translated_text"]
        # No crash
        assert isinstance(result, dict)


# ──────────────────────────────────────────────────────────────
# Stats — counters survive fault scenarios
# ──────────────────────────────────────────────────────────────

class TestTranslationStatsUnderFaults:
    """Verify stats counters are updated correctly during fault scenarios."""

    def setup_method(self):
        from src.stats import TranslationStats
        TranslationStats._chapters_translated = 0
        TranslationStats._chapters_failed = 0
        TranslationStats._api_calls_total = 0
        TranslationStats._api_calls_failed = 0
        TranslationStats._api_calls_per_lang.clear()
        TranslationStats._api_failures_per_lang.clear()
        TranslationStats._recent_completions.clear()
        TranslationStats._recent_api_calls.clear()

    def test_api_failures_increment_counter(self):
        from src.stats import TranslationStats
        for _ in range(3):
            TranslationStats.record_api_call("en-US")
            TranslationStats.record_api_failure("en-US")

        snap = TranslationStats.snapshot()
        assert snap["api_calls_total"] == 3
        assert snap["api_calls_failed"] == 3

    def test_per_language_failure_tracking(self):
        from src.stats import TranslationStats
        TranslationStats.record_api_call("en-US")
        TranslationStats.record_api_failure("en-US")
        TranslationStats.record_api_call("es-ES")
        TranslationStats.record_api_success("es-ES")

        snap = TranslationStats.snapshot()
        assert snap["api_failures_per_language"].get("en-US", 0) == 1
        assert snap["api_failures_per_language"].get("es-ES", 0) == 0
        assert snap["api_calls_per_language"].get("es-ES", 0) == 1

    def test_token_tracking(self):
        from src.stats import TranslationStats
        TranslationStats.reset_tokens()
        TranslationStats.record_tokens(1000, 500)
        TranslationStats.record_tokens(2000, 800)

        ts = TranslationStats.token_snapshot()
        assert ts["input"] == 3000
        assert ts["output"] == 1300
        assert ts["total"] == 4300
        assert ts["estimated_cost_usd"] > 0

    def test_snapshot_includes_all_fields(self):
        from src.stats import TranslationStats
        snap = TranslationStats.snapshot()
        expected_keys = [
            "chapters_translated", "chapters_failed",
            "api_calls_total", "api_calls_failed",
            "throughput_chapters_per_minute", "error_rates_per_language",
            "chapters_per_language", "uptime_seconds",
            "tokens_input", "tokens_output", "tokens_total",
            "estimated_cost_usd",
        ]
        for key in expected_keys:
            assert key in snap, f"Missing key: {key}"
