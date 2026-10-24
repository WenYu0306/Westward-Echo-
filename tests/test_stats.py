"""Unit tests for stats.py — translation statistics collector.

snapshot() feeds the dashboard, so every number in it is displayed as fact. The
sliding-window arithmetic (ring-buffer caps, the 5-minute throughput window, the
last-100-calls error sample) was previously unasserted: a test that only checked
"the key exists" passed while the value behind it was computed from the wrong
window. Each test below names the mutation it is meant to catch.
"""

import time as _real_time

import pytest

import src.stats as stats_mod
from src.stats import TranslationStats


def _reset_stats():
    """Clear every class-level counter so tests cannot see each other's data."""
    TranslationStats.reset_tokens()
    TranslationStats._chapters_translated = 0
    TranslationStats._chapters_failed = 0
    TranslationStats._api_calls_total = 0
    TranslationStats._api_calls_failed = 0
    TranslationStats._api_calls_per_lang.clear()
    TranslationStats._api_failures_per_lang.clear()
    TranslationStats._chapters_per_lang.clear()
    TranslationStats._chapters_failed_per_lang.clear()
    TranslationStats._recent_completions.clear()
    TranslationStats._recent_api_calls.clear()


class TestTranslationStats:
    def setup_method(self):
        _reset_stats()

    def test_record_chapter_complete(self):
        TranslationStats.record_chapter_complete("en-US")
        snap = TranslationStats.snapshot()
        assert snap["chapters_translated"] == 1
        assert snap["chapters_per_language"]["en-US"] == 1

    def test_record_chapter_failed(self):
        TranslationStats.record_chapter_failed("en-US")
        snap = TranslationStats.snapshot()
        assert snap["chapters_failed"] == 1
        assert snap["chapters_failed_per_language"]["en-US"] == 1

    def test_record_api_call(self):
        TranslationStats.record_api_call("en-US")
        snap = TranslationStats.snapshot()
        assert snap["api_calls_total"] == 1
        assert snap["api_calls_per_language"]["en-US"] == 1

    def test_record_api_success_then_failure(self):
        TranslationStats.record_api_success("en-US")
        TranslationStats.record_api_failure("en-US")
        snap = TranslationStats.snapshot()
        assert snap["api_calls_failed"] == 1

    def test_record_tokens_accumulates_totals(self):
        TranslationStats.record_tokens(1000, 500)
        ts = TranslationStats.token_snapshot()
        assert ts["input"] == 1000
        assert ts["output"] == 500
        assert ts["total"] == 1500

    def test_multiple_calls_accumulate(self):
        TranslationStats.record_tokens(1000, 500)
        TranslationStats.record_tokens(2000, 1000)
        ts = TranslationStats.token_snapshot()
        assert ts["total"] == 4500
        assert ts["input"] == 3000
        assert ts["output"] == 1500

    def test_snapshot_has_no_cost_figure(self):
        """The regression that mattered: this dict fed dashboard.py, and the
        cost in it was computed from DeepSeek USD rates applied to GLM/Qwen
        traffic bucketed by stale tier labels — a confidently wrong number.
        Cost must now come from token_ledger, which knows the real model."""
        TranslationStats.record_tokens(1_000_000, 1_000_000)
        ts = TranslationStats.token_snapshot()
        assert "estimated_cost_usd" not in ts
        assert not any("cost" in k for k in ts)

    def test_snapshot_has_no_tier_buckets(self):
        ts = TranslationStats.token_snapshot()
        for gone in ("flash_input", "flash_output", "pro_input", "pro_output"):
            assert gone not in ts, f"stale tier bucket still exposed: {gone}"

    def test_throughput(self):
        for _ in range(5):
            TranslationStats.record_chapter_complete("en-US")
        snap = TranslationStats.snapshot()
        assert "throughput_chapters_per_minute" in snap

    def test_multiple_languages(self):
        TranslationStats.record_chapter_complete("en-US")
        TranslationStats.record_chapter_complete("es-ES")
        TranslationStats.record_chapter_complete("en-US")
        snap = TranslationStats.snapshot()
        assert snap["chapters_per_language"]["en-US"] == 2
        assert snap["chapters_per_language"]["es-ES"] == 1

    def test_reset_tokens(self):
        TranslationStats.record_tokens(1000, 500)
        TranslationStats.reset_tokens()
        ts = TranslationStats.token_snapshot()
        assert ts["total"] == 0
        assert ts["input"] == 0
        assert ts["output"] == 0

    def test_record_success_is_alias(self):
        TranslationStats.record_success("en-US")
        snap = TranslationStats.snapshot()
        assert snap["chapters_translated"] == 1

    def test_record_failure_is_alias(self):
        TranslationStats.record_failure("en-US")
        snap = TranslationStats.snapshot()
        assert snap["chapters_failed"] == 1

    def test_snapshot_has_uptime(self):
        snap = TranslationStats.snapshot()
        assert snap["uptime_seconds"] >= 0
        assert "session_start" in snap


# ═══════════════════════════════════════════════════════════════
# Controllable clock
#
# snapshot() derives throughput and uptime from time.monotonic(). Against the
# real clock both are noise, so the only honest assertion available was "the key
# exists" — which is why the window arithmetic survived mutation. Freezing the
# clock makes the numbers exact and therefore falsifiable.
# ═══════════════════════════════════════════════════════════════

class _FrozenTime:
    def __init__(self, now):
        self._now = now

    def monotonic(self):
        return self._now

    def __getattr__(self, name):
        # strftime / gmtime (used for session_start) stay real.
        return getattr(_real_time, name)


@pytest.fixture
def frozen_time(monkeypatch):
    def _freeze(now=10_000.0):
        monkeypatch.setattr(stats_mod, "time", _FrozenTime(now))
        return now

    return _freeze


@pytest.fixture(autouse=True)
def _isolate_class_state():
    """TranslationStats is all class-level state; without this, ring-buffer and
    window tests inherit each other's history and pass for the wrong reason."""
    _reset_stats()
    yield
    _reset_stats()


# ═══════════════════════════════════════════════════════════════
# Ring buffer caps
#
# ``if len(x) > _max_recent: x = x[-_max_recent:]`` — flipping ``>`` to ``<=``
# looks harmless because slicing a short list returns the same content. It is
# not harmless: once the length crosses the cap the mutated guard goes False and
# truncation stops happening *forever*, so the buffer grows without bound in a
# long-running dashboard process. Only overfilling it exposes that.
# ═══════════════════════════════════════════════════════════════

_MAX_RECENT = 200  # hardcoded on purpose: asserting against the class attribute
                   # would make ``_max_recent = 201`` pass by moving the goalpost


class TestRingBufferCaps:
    def test_api_success_history_is_capped(self, frozen_time):
        frozen_time()
        for _ in range(_MAX_RECENT + 2):
            TranslationStats.record_api_success("en-US")
        assert len(TranslationStats._recent_api_calls) == _MAX_RECENT

    def test_api_failure_history_is_capped(self, frozen_time):
        frozen_time()
        for _ in range(_MAX_RECENT + 2):
            TranslationStats.record_api_failure("en-US")
        assert len(TranslationStats._recent_api_calls) == _MAX_RECENT

    def test_completion_history_is_capped(self, frozen_time):
        frozen_time()
        for _ in range(_MAX_RECENT + 2):
            TranslationStats.record_success("en-US")
        assert len(TranslationStats._recent_completions) == _MAX_RECENT

    def test_cap_keeps_the_newest_entries(self, frozen_time):
        """``x[-max:]`` keeps the tail. If it ever became ``x[:max]`` the
        dashboard would report the error rate of the *oldest* 200 calls and never
        notice a provider that started failing an hour ago."""
        frozen_time()
        TranslationStats.record_api_success("oldest-lang")
        for i in range(_MAX_RECENT + 1):
            TranslationStats.record_api_success(f"lang-{i}")
        history = TranslationStats._recent_api_calls
        assert len(history) == _MAX_RECENT
        langs = [entry[1] for entry in history]
        assert "oldest-lang" not in langs
        assert langs[-1] == f"lang-{_MAX_RECENT}"

    def test_mixed_success_and_failure_stay_capped(self, frozen_time):
        frozen_time()
        for i in range(_MAX_RECENT + 2):
            if i % 2:
                TranslationStats.record_api_success("en-US")
            else:
                TranslationStats.record_api_failure("en-US")
        assert len(TranslationStats._recent_api_calls) == _MAX_RECENT


# ═══════════════════════════════════════════════════════════════
# Throughput window
# ═══════════════════════════════════════════════════════════════

class TestThroughputWindow:
    def test_rate_is_completions_over_five_minutes(self, frozen_time):
        """10 completions inside the window → 2.0 chapters/min.

        Kills ``sum(1 for ...)`` → ``sum(2 for ...)`` (would give 4.0) and the
        ``/ 5.0`` → ``/ 6.0`` drift (would give 1.67).
        """
        now = frozen_time(10_000.0)
        TranslationStats._recent_completions.extend([now - 60.0] * 10)
        snap = TranslationStats.snapshot()
        assert snap["throughput_chapters_per_minute"] == 2.0

    def test_completions_outside_window_are_excluded(self, frozen_time):
        now = frozen_time(10_000.0)
        TranslationStats._recent_completions.extend([now - 400.0] * 7)
        assert TranslationStats.snapshot()["throughput_chapters_per_minute"] == 0.0

    def test_window_direction_is_not_inverted(self, frozen_time):
        """``t >= cutoff`` must keep *recent* completions.

        The counts are deliberately asymmetric (3 recent vs 7 old): with equal
        counts a flipped comparison would report the same number and the test
        would be vacuous. Flipped to ``<``, this reads 1.4 instead of 0.6 — the
        dashboard would report throughput from everything older than five
        minutes, i.e. exactly the opposite of a live rate.
        """
        now = frozen_time(10_000.0)
        TranslationStats._recent_completions.extend([now - 10.0] * 3)
        TranslationStats._recent_completions.extend([now - 900.0] * 7)
        snap = TranslationStats.snapshot()
        assert snap["throughput_chapters_per_minute"] == 0.6  # 3 recent / 5 min

    def test_exactly_300_seconds_old_counts_as_inside(self, frozen_time):
        """The window is inclusive at its edge: ``>=`` not ``>``."""
        now = frozen_time(10_000.0)
        TranslationStats._recent_completions.append(now - 300.0)
        assert TranslationStats.snapshot()["throughput_chapters_per_minute"] == 0.2

    def test_just_past_300_seconds_is_outside(self, frozen_time):
        """Pins the 300-second window itself.

        With the cutoff widened to 301, a completion aged 300.5 s falls inside
        and the reported rate is 0.2 instead of 0.0.
        """
        now = frozen_time(10_000.0)
        TranslationStats._recent_completions.append(now - 300.5)
        assert TranslationStats.snapshot()["throughput_chapters_per_minute"] == 0.0

    def test_empty_history_reports_zero(self, frozen_time):
        frozen_time()
        assert TranslationStats.snapshot()["throughput_chapters_per_minute"] == 0.0


# ═══════════════════════════════════════════════════════════════
# Per-language error rate
# ═══════════════════════════════════════════════════════════════

class TestErrorRate:
    def test_totals_failures_and_rate(self, frozen_time):
        """One success plus two failures → 2/3.

        This single assertion set pins six mutation points at once: the success
        tuple's ``True`` flag, the failure tuple's ``False`` flag (either flip
        changes ``failed``), ``total += 1`` and ``failed += 1`` (either doubling
        changes the counts), the ``if total > 0`` guard, and the 4-decimal
        rounding (2/3 rounds to 0.6667, not 0.66667).
        """
        frozen_time()
        TranslationStats.record_api_success("en-US")
        TranslationStats.record_api_failure("en-US")
        TranslationStats.record_api_failure("en-US")
        rate = TranslationStats.snapshot()["error_rates_per_language"]["en-US"]
        assert rate["total"] == 3
        assert rate["failed"] == 2
        assert rate["error_rate"] == 0.6667

    def test_all_successes_report_zero_rate(self, frozen_time):
        frozen_time()
        for _ in range(4):
            TranslationStats.record_api_success("es-ES")
        rate = TranslationStats.snapshot()["error_rates_per_language"]["es-ES"]
        assert rate == {"total": 4, "failed": 0, "error_rate": 0.0}

    def test_languages_are_counted_separately(self, frozen_time):
        frozen_time()
        TranslationStats.record_api_failure("en-US")
        TranslationStats.record_api_success("es-ES")
        rates = TranslationStats.snapshot()["error_rates_per_language"]
        assert rates["en-US"]["error_rate"] == 1.0
        assert rates["es-ES"]["error_rate"] == 0.0

    def test_only_the_last_one_hundred_calls_are_sampled(self, frozen_time):
        """``_recent_api_calls[-100:]`` — widening the slice to 101 lets an
        ancient failure into the live error rate."""
        frozen_time()
        TranslationStats.record_api_failure("en-US")  # oldest, must fall out
        for _ in range(100):
            TranslationStats.record_api_success("en-US")
        rate = TranslationStats.snapshot()["error_rates_per_language"]["en-US"]
        assert rate["total"] == 100
        assert rate["failed"] == 0
        assert rate["error_rate"] == 0.0

    def test_global_failure_counter_still_sees_everything(self, frozen_time):
        """The 100-call sample is a *rate* window; the lifetime counters are not.
        A failure that aged out of the sample must still be counted globally."""
        frozen_time()
        TranslationStats.record_api_failure("en-US")
        for _ in range(100):
            TranslationStats.record_api_success("en-US")
        snap = TranslationStats.snapshot()
        assert snap["api_calls_failed"] == 1
        assert snap["api_failures_per_language"]["en-US"] == 1

    def test_per_language_failure_counter_increments_by_one(self, frozen_time):
        frozen_time()
        TranslationStats.record_api_failure("en-US")
        assert TranslationStats.snapshot()["api_failures_per_language"]["en-US"] == 1

    def test_no_calls_means_no_error_rate_entries(self, frozen_time):
        frozen_time()
        assert TranslationStats.snapshot()["error_rates_per_language"] == {}


# ═══════════════════════════════════════════════════════════════
# Uptime precision
# ═══════════════════════════════════════════════════════════════

class TestUptimePrecision:
    def test_uptime_is_rounded_to_one_decimal(self, monkeypatch, frozen_time):
        """``round(uptime, 1)`` widened to 2 decimals would print 1.23.

        The value is displayed on the dashboard, so the precision is part of the
        contract rather than an implementation detail.
        """
        monkeypatch.setattr(TranslationStats, "_start_time", 9_998.7654)
        frozen_time(10_000.0)
        assert TranslationStats.snapshot()["uptime_seconds"] == 1.2

    def test_uptime_grows_with_elapsed_time(self, monkeypatch, frozen_time):
        monkeypatch.setattr(TranslationStats, "_start_time", 9_000.0)
        frozen_time(10_000.0)
        assert TranslationStats.snapshot()["uptime_seconds"] == 1000.0

    def test_session_start_is_a_utc_timestamp(self, monkeypatch, frozen_time):
        import re

        monkeypatch.setattr(TranslationStats, "_start_time", 1_700_000_000.0)
        frozen_time(1_700_000_500.0)
        snap = TranslationStats.snapshot()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                            snap["session_start"])
