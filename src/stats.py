"""Thread-safe translation statistics collectors for the dashboard.

Records per-language API calls, successes/failures, chapter completions,
and throughput.  Used by the observability dashboard and circuit breaker.
"""

import threading
import time
from collections import defaultdict
from typing import Dict, List


class TranslationStats:
    """Thread-safe counters for dashboard metrics.

    Usage::

        TranslationStats.record_api_call("en-US")
        TranslationStats.record_api_success("en-US")
        TranslationStats.record_api_failure("en-US")
        TranslationStats.record_chapter_complete("en-US")
        TranslationStats.record_chapter_failed("en-US")
        snapshot = TranslationStats.snapshot()
    """

    _lock = threading.Lock()

    # Global counters
    _chapters_translated: int = 0
    _chapters_failed: int = 0
    _api_calls_total: int = 0
    _api_calls_failed: int = 0

    # Per-language counters: lang -> count
    _api_calls_per_lang: Dict[str, int] = defaultdict(int)
    _api_failures_per_lang: Dict[str, int] = defaultdict(int)
    _chapters_per_lang: Dict[str, int] = defaultdict(int)
    _chapters_failed_per_lang: Dict[str, int] = defaultdict(int)

    # Recent history for sliding-window throughput (last N calls)
    _recent_completions: List[float] = []  # timestamps of completed chapters
    _recent_api_calls: List[tuple] = []    # [(timestamp, lang, success), ...]
    _max_recent = 200

    # Token / cost tracking
    _tokens_input: int = 0
    _tokens_output: int = 0

    # Session start time
    _start_time: float = time.monotonic()

    # Pricing constants (DeepSeek V4, USD per million tokens, cache-miss)
    # Flash: $0.14/M input,  $0.28/M output  — default for WRITE/READBACK/FIX
    # Pro:   $0.435/M input, $0.87/M output  — READ node only
    # Estimates use Flash pricing (conservative; actual cost slightly higher
    # from READ-Pro calls on non-sample chapters).
    _PRICE_INPUT_PER_M = 0.14   # $0.14/M input
    _PRICE_OUTPUT_PER_M = 0.28  # $0.28/M output

    @classmethod
    def record_api_call(cls, lang: str = "en-US"):
        """Record that an API call was initiated."""
        with cls._lock:
            cls._api_calls_total += 1
            cls._api_calls_per_lang[lang] = cls._api_calls_per_lang.get(lang, 0) + 1

    @classmethod
    def record_api_success(cls, lang: str = "en-US"):
        """Record a successful API response."""
        with cls._lock:
            cls._recent_api_calls.append((time.monotonic(), lang, True))
            if len(cls._recent_api_calls) > cls._max_recent:
                cls._recent_api_calls = cls._recent_api_calls[-cls._max_recent:]

    @classmethod
    def record_api_failure(cls, lang: str = "en-US"):
        """Record a failed API call."""
        with cls._lock:
            cls._api_calls_failed += 1
            cls._api_failures_per_lang[lang] = cls._api_failures_per_lang.get(lang, 0) + 1
            cls._recent_api_calls.append((time.monotonic(), lang, False))
            if len(cls._recent_api_calls) > cls._max_recent:
                cls._recent_api_calls = cls._recent_api_calls[-cls._max_recent:]

    @classmethod
    def record_success(cls, lang: str = "en-US"):
        """Record a completed chapter translation (convenience name)."""
        with cls._lock:
            cls._chapters_translated += 1
            cls._chapters_per_lang[lang] = cls._chapters_per_lang.get(lang, 0) + 1
            cls._recent_completions.append(time.monotonic())
            if len(cls._recent_completions) > cls._max_recent:
                cls._recent_completions = cls._recent_completions[-cls._max_recent:]

    @classmethod
    def record_failure(cls, lang: str = "en-US"):
        """Record a failed chapter translation."""
        with cls._lock:
            cls._chapters_failed += 1
            cls._chapters_failed_per_lang[lang] = cls._chapters_failed_per_lang.get(lang, 0) + 1

    @classmethod
    def record_chapter_complete(cls, lang: str = "en-US"):
        """Alias for ``record_success`` — chapter completed."""
        cls.record_success(lang)

    @classmethod
    def record_chapter_failed(cls, lang: str = "en-US"):
        """Alias for ``record_failure`` — chapter failed."""
        cls.record_failure(lang)

    @classmethod
    def record_tokens(cls, input_tokens: int, output_tokens: int):
        """Accumulate token usage from an LLM API response."""
        with cls._lock:
            cls._tokens_input += input_tokens
            cls._tokens_output += output_tokens

    @classmethod
    def token_snapshot(cls) -> dict:
        """Return {total, input, output, estimated_cost_usd} for the session."""
        with cls._lock:
            total = cls._tokens_input + cls._tokens_output
            cost = (
                (cls._tokens_input / 1_000_000) * cls._PRICE_INPUT_PER_M
                + (cls._tokens_output / 1_000_000) * cls._PRICE_OUTPUT_PER_M
            )
            return {
                "total": total,
                "input": cls._tokens_input,
                "output": cls._tokens_output,
                "estimated_cost_usd": round(cost, 4),
            }

    @classmethod
    def reset_tokens(cls):
        """Reset token counters (e.g. at start of a new job)."""
        with cls._lock:
            cls._tokens_input = 0
            cls._tokens_output = 0

    @classmethod
    def snapshot(cls) -> dict:
        """Return a point-in-time snapshot of all metrics."""
        with cls._lock:
            now = time.monotonic()

            # Throughput: chapters/min over the last 5 minutes
            cutoff = now - 300  # 5 minutes
            recent_5m = sum(1 for t in cls._recent_completions if t >= cutoff)
            throughput = recent_5m / 5.0  # chapters per minute over last 5 min

            # Error rate per language (last 100 API calls)
            recent_calls = cls._recent_api_calls[-100:]
            error_rates = {}
            for _ts, _lang, _ok in recent_calls:
                if _lang not in error_rates:
                    error_rates[_lang] = {"total": 0, "failed": 0}
                error_rates[_lang]["total"] += 1
                if not _ok:
                    error_rates[_lang]["failed"] += 1

            per_lang_error = {}
            for lang, counts in error_rates.items():
                total = counts["total"]
                failed = counts["failed"]
                per_lang_error[lang] = {
                    "total": total,
                    "failed": failed,
                    "error_rate": round(failed / total, 4) if total > 0 else 0.0,
                }

            uptime = now - cls._start_time

            return {
                "chapters_translated": cls._chapters_translated,
                "chapters_failed": cls._chapters_failed,
                "api_calls_total": cls._api_calls_total,
                "api_calls_failed": cls._api_calls_failed,
                "throughput_chapters_per_minute": round(throughput, 2),
                "error_rates_per_language": per_lang_error,
                "chapters_per_language": dict(cls._chapters_per_lang),
                "chapters_failed_per_language": dict(cls._chapters_failed_per_lang),
                "api_calls_per_language": dict(cls._api_calls_per_lang),
                "api_failures_per_language": dict(cls._api_failures_per_lang),
                "uptime_seconds": round(uptime, 1),
                "session_start": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(cls._start_time)
                ),
                "tokens_input": cls._tokens_input,
                "tokens_output": cls._tokens_output,
                "tokens_total": cls._tokens_input + cls._tokens_output,
                "estimated_cost_usd": round(
                    (cls._tokens_input / 1_000_000) * cls._PRICE_INPUT_PER_M
                    + (cls._tokens_output / 1_000_000) * cls._PRICE_OUTPUT_PER_M,
                    4,
                ),
            }
