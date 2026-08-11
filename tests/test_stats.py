"""Unit tests for stats.py — translation statistics collector."""

import pytest

from src.stats import TranslationStats


class TestTranslationStats:
    def setup_method(self):
        TranslationStats.reset_tokens()
        # Reset counters through snapshot (read-only ops don't reset)
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

    def test_record_tokens_flash(self):
        TranslationStats.record_tokens(1000, 500, tier="flash")
        ts = TranslationStats.token_snapshot()
        assert ts["flash_input"] == 1000
        assert ts["flash_output"] == 500
        assert ts["total"] == 1500

    def test_record_tokens_pro(self):
        TranslationStats.record_tokens(2000, 1000, tier="pro")
        ts = TranslationStats.token_snapshot()
        assert ts["pro_input"] == 2000
        assert ts["pro_output"] == 1000

    def test_mixed_tiers(self):
        TranslationStats.record_tokens(1000, 500, tier="flash")
        TranslationStats.record_tokens(2000, 1000, tier="pro")
        ts = TranslationStats.token_snapshot()
        assert ts["total"] == 4500
        assert ts["input"] == 3000
        assert ts["output"] == 1500

    def test_cost_calculation(self):
        TranslationStats.record_tokens(1_000_000, 0, tier="flash")
        TranslationStats.record_tokens(0, 1_000_000, tier="flash")
        ts = TranslationStats.token_snapshot()
        # $0.14 + $0.28 = $0.42
        assert ts["estimated_cost_usd"] == pytest.approx(0.42, rel=0.01)

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
        TranslationStats.record_tokens(1000, 500, tier="flash")
        TranslationStats.reset_tokens()
        ts = TranslationStats.token_snapshot()
        assert ts["total"] == 0
        assert ts["flash_input"] == 0

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
