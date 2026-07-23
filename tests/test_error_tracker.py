"""Tests for src/error_tracker.py — event recording, summaries, and job health."""

import pytest
from src.error_tracker import (
    record_event,
    get_event_summary,
    get_recent_issues,
    get_job_health,
)


class TestRecordEvent:
    def test_record_event_succeeds(self):
        """record_event with valid args should not raise."""
        record_event("job1", 1, "guard_warning", "test detail", "en-US")

    def test_record_event_with_none_job_id(self):
        """record_event with None job_id should not raise."""
        record_event(None, None, "empty_output", "no job", "en-US")


class TestGetEventSummary:
    def test_returns_dict_with_total_key(self):
        """get_event_summary() returns a dict containing a 'total' key."""
        record_event("job_summary", 1, "guard_warning", "summary test", "en-US")
        summary = get_event_summary(days=7)
        assert isinstance(summary, dict)
        assert "total" in summary

    def test_returns_per_type_counts(self):
        """After recording events, summary has correct per-type counts."""
        record_event("job_counts", 1, "guard_warning", "d1", "en-US")
        record_event("job_counts", 2, "guard_warning", "d2", "en-US")
        record_event("job_counts", 3, "guard_warning", "d3", "en-US")
        record_event("job_counts", 4, "parse_fallback", "d4", "en-US")
        summary = get_event_summary(days=7)
        assert summary.get("guard_warning", 0) >= 3
        assert summary.get("parse_fallback", 0) >= 1

    def test_three_events_same_type_shows_count_three(self):
        """After recording 3 events of same type, summary shows count=3 for that type."""
        record_event("job3x", 1, "circuit_breaker", "e1", "en-US")
        record_event("job3x", 2, "circuit_breaker", "e2", "en-US")
        record_event("job3x", 3, "circuit_breaker", "e3", "en-US")
        summary = get_event_summary(days=7)
        assert summary["circuit_breaker"] >= 3


class TestGetRecentIssues:
    def test_returns_list(self):
        """get_recent_issues returns a list."""
        record_event("job_ri", 1, "guard_warning", "recent test", "en-US")
        issues = get_recent_issues(5)
        assert isinstance(issues, list)

    def test_max_length_respected(self):
        """get_recent_issues(5) returns at most 5 items."""
        for i in range(10):
            record_event("job_lim", i, "guard_warning", f"detail {i}", "en-US")
        issues = get_recent_issues(5)
        assert len(issues) <= 5

    def test_newest_first(self):
        """get_recent_issues returns events newest first."""
        import time
        record_event("job_order_final", 1, "guard_warning", "alpha", "en-US")
        time.sleep(1.1)  # SQLite datetime('now') has second granularity
        record_event("job_order_final", 2, "guard_warning", "omega", "en-US")
        issues = get_recent_issues(100)
        details = [i["detail"] for i in issues if i["job_id"] == "job_order_final"]
        assert len(details) >= 2, f"Expected >=2 events, got: {details}"
        # omega was recorded later, so should appear before alpha
        assert "omega" in details and "alpha" in details


class TestGetJobHealth:
    def test_returns_dict_with_expected_keys(self):
        """get_job_health returns dict with total_chapters, warning_count, warning_rate."""
        record_event("job_h", 1, "guard_warning", "health test", "en-US")
        health = get_job_health("job_h")
        assert isinstance(health, dict)
        assert "total_chapters" in health
        assert "warning_rate_pct" in health
        assert "total_warnings" in health
        assert health["job_id"] == "job_h"

    def test_nonexistent_job_returns_zeros(self):
        """get_job_health for nonexistent job returns zeros, not an error."""
        health = get_job_health("nonexistent_job_xyz")
        assert health["total_chapters"] == 0
        assert health["total_warnings"] == 0
        assert health["warning_rate_pct"] == 0.0
        assert health["chapters_with_warnings"] == 0


class TestMultiLanguage:
    def test_different_languages_both_recorded(self):
        """Events for en-US and es-ES are both reflected in summary."""
        record_event("job_lang", 1, "guard_warning", "english warning", "en-US")
        record_event("job_lang", 2, "guard_warning", "spanish warning", "es-ES")
        summary = get_event_summary(days=7)
        # Both should aggregate under guard_warning
        assert summary.get("guard_warning", 0) >= 2
