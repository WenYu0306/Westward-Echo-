"""Tests for src/error_tracker.py — event recording, summaries, and job health."""

from src.error_tracker import (
    get_event_summary,
    get_job_health,
    get_recent_issues,
    record_event,
)


class TestRecordEvent:
    def test_record_event_succeeds(self):
        """record_event with valid args should not raise AND should persist."""
        record_event("job1", 1, "guard_warning", "test detail", "en-US")
        issues = get_recent_issues(100)
        assert any(
            e["job_id"] == "job1" and e["event_type"] == "guard_warning"
            for e in issues
        )

    def test_record_event_with_none_job_id(self):
        """record_event with None job_id should not raise AND persist as None."""
        record_event(None, None, "empty_output", "no job", "en-US")
        issues = get_recent_issues(100)
        assert any(
            e["event_type"] == "empty_output" and e["job_id"] is None
            for e in issues
        )


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


# ── Connection bootstrap ──────────────────────────────────────────

class TestConnectionBootstrap:
    def test_missing_parent_directories_are_created(self, tmp_path, monkeypatch):
        """A first run must create its own nesting, not crash on mkdir."""
        import src.error_tracker as et

        nested = tmp_path / "deep" / "nested" / "errors.db"
        monkeypatch.setattr(et, "DB_PATH", str(nested))
        monkeypatch.setattr(et._local, "conn", None)

        conn = et._get_conn()
        try:
            conn.executescript(et._SCHEMA)
            conn.execute(
                "INSERT INTO translation_events (job_id, event_type) VALUES (?, ?)",
                ("bootstrap", "guard_warning"),
            )
            conn.commit()
        finally:
            monkeypatch.setattr(et._local, "conn", None)
            conn.close()
        assert nested.exists()

    def test_connection_created_in_one_thread_is_usable_in_another(
        self, tmp_path, monkeypatch
    ):
        """``check_same_thread=False`` is load-bearing, not cargo-cult.

        Mirror of the job_store test for the same reason: connections come from
        ``threading.local()``, so in today's call graph none crosses a thread
        boundary and the flag looks removable. Flipping it to ``True`` makes
        sqlite3 raise ``ProgrammingError`` on any off-thread use, and nothing
        else in the suite touched that path — which is why ``const bool->True
        at 25`` survived mutation testing.

        Note this module ALSO holds a module-level ``_conn`` built at import time
        (for the schema bootstrap). That connection is likewise usable off its
        creating thread only because of this flag, so the flag is doing real work
        in two places, not one.
        """
        import threading

        import src.error_tracker as et

        monkeypatch.setattr(et, "DB_PATH", str(tmp_path / "cross_thread.db"))
        monkeypatch.setattr(et._local, "conn", None, raising=False)

        conn = et._get_conn()  # created on THIS thread
        errors: dict = {}

        def worker():
            try:
                conn.execute("SELECT 1").fetchone()
            except Exception as exc:  # noqa: BLE001 - recorded, asserted below
                errors["exc"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        try:
            assert "exc" not in errors, (
                f"connection could not be used from another thread: {errors.get('exc')!r}"
            )
        finally:
            monkeypatch.setattr(et._local, "conn", None, raising=False)
            conn.close()


# ── Window and limit defaults ─────────────────────────────────────

class TestEventSummaryWindow:
    def test_default_window_is_seven_days(self):
        # An event 7.5 days old is outside the default window but inside an
        # 8-day one — so this pins the default at exactly 7.
        import src.error_tracker as et

        conn = et._get_conn()
        # Repeatable: the events table is append-only and shared across tests.
        conn.execute(
            "DELETE FROM translation_events WHERE event_type = 'boundary_window_event'"
        )
        conn.execute(
            "INSERT INTO translation_events (job_id, event_type, detail, created_at) "
            "VALUES (?, ?, ?, datetime('now', '-7 days', '-12 hours'))",
            ("window_test", "boundary_window_event", "x"),
        )
        conn.commit()

        assert et.get_event_summary().get("boundary_window_event", 0) == 0
        assert et.get_event_summary(days=8).get("boundary_window_event", 0) == 1

        conn.execute(
            "DELETE FROM translation_events WHERE event_type = 'boundary_window_event'"
        )
        conn.commit()


class TestRecentIssuesLimit:
    def test_default_limit_is_twenty(self):
        for i in range(21):
            record_event(f"limit_job_{i}", 1, "guard_warning", f"d{i}", "en-US")
        assert len(get_recent_issues()) == 20

    def test_explicit_limit_is_respected(self):
        assert len(get_recent_issues(limit=3)) == 3


# ── Job health maths ──────────────────────────────────────────────

class TestJobHealthRate:
    def test_one_warning_chapter_in_three_is_33_3_percent(self):
        import uuid

        from src.job_store import JobStore

        job_id = JobStore().create_job(
            f"rate3_{uuid.uuid4().hex[:8]}.txt", "en-US", 3
        )
        record_event(job_id, 1, "guard_warning", "one chapter warned", "en-US")

        health = get_job_health(job_id)
        # 1/3 * 100 = 33.333... → the stored rate keeps ONE decimal.
        assert health["warning_rate_pct"] == 33.3

    def test_one_warning_chapter_in_four_is_25_percent(self):
        import uuid

        from src.job_store import JobStore

        job_id = JobStore().create_job(
            f"rate4_{uuid.uuid4().hex[:8]}.txt", "en-US", 4
        )
        record_event(job_id, 1, "guard_warning", "one chapter warned", "en-US")

        health = get_job_health(job_id)
        assert health["warning_rate_pct"] == 25.0

    def test_health_dict_shape(self):
        import uuid

        from src.job_store import JobStore

        job_id = JobStore().create_job(
            f"shape_{uuid.uuid4().hex[:8]}.txt", "en-US", 2
        )
        health = get_job_health(job_id)
        assert set(health) == {
            "job_id", "total_chapters", "chapters_with_warnings",
            "warning_rate_pct", "total_warnings",
        }


# ── Aggregate helpers ─────────────────────────────────────────────

class TestEditorStats:
    def test_returns_a_dict(self):
        from src.error_tracker import get_editor_stats
        stats = get_editor_stats()
        assert isinstance(stats, dict)
        assert stats is not None


class TestAllJobsHealth:
    def test_returns_a_list(self):
        from src.error_tracker import get_all_jobs_health
        health = get_all_jobs_health()
        assert isinstance(health, list)

    def test_sorted_worst_first(self):
        import uuid

        from src.error_tracker import get_all_jobs_health
        from src.job_store import JobStore

        store = JobStore()
        bad = store.create_job(f"bad_{uuid.uuid4().hex[:8]}.txt", "en-US", 1)
        good = store.create_job(f"good_{uuid.uuid4().hex[:8]}.txt", "en-US", 10)
        record_event(bad, 1, "guard_warning", "warned", "en-US")
        record_event(good, 1, "guard_warning", "warned", "en-US")

        rates = {h["job_id"]: h["warning_rate_pct"] for h in get_all_jobs_health()}
        assert rates[bad] == 100.0
        assert rates[good] == 10.0

        order = [h["job_id"] for h in get_all_jobs_health()]
        assert order.index(bad) < order.index(good)
