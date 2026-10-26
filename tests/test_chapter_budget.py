"""Tests for src/chapter_budget.py — cross-run attempt and spend memory.

Chapter 660 of 《无限恐怖》 was fully translated six times across three runs
(37 calls, ¥3.96, 12x the book median) and never delivered, because nothing
remembered the earlier failures. These tests pin the memory that stops it.
"""

import json

from src.chapter_budget import ChapterBudget


def _budget(tmp_path, **kw):
    return ChapterBudget(tmp_path / "_attempts.json", **kw)


class TestShouldAttempt:
    def test_fresh_chapter_is_allowed(self, tmp_path):
        assert _budget(tmp_path).should_attempt(1) == (True, "")

    def test_attempt_count_caps_retries(self, tmp_path):
        b = _budget(tmp_path, max_attempts=2)
        b.record(1, cost=0.1)
        b.record(1, cost=0.1)
        ok, why = b.should_attempt(1)
        assert ok is False and "2 次" in why

    def test_cost_cap_stops_before_attempt_cap(self, tmp_path):
        b = _budget(tmp_path, max_attempts=99, max_cost_cny=1.0)
        b.record(1, cost=1.5)
        ok, why = b.should_attempt(1)
        assert ok is False and "¥1.50" in why

    def test_needs_fast_mode_after_first_attempt(self, tmp_path):
        b = _budget(tmp_path)
        assert b.needs_fast_mode(1) is False
        b.record(1, cost=0.1)
        assert b.needs_fast_mode(1) is True


class TestPersistence:
    def test_survives_a_reload(self, tmp_path):
        b = _budget(tmp_path)
        b.record(7, cost=0.5, outcome="ok")
        again = _budget(tmp_path)
        entry = again.get(7)
        assert entry["attempts"] == 1
        assert entry["cost_cny"] == 0.5
        assert entry["outcome"] == "ok"

    def test_costs_accumulate(self, tmp_path):
        b = _budget(tmp_path)
        b.record(7, cost=0.5)
        b.record(7, cost=0.25)
        assert b.get(7)["cost_cny"] == 0.75

    def test_corrupt_file_starts_empty(self, tmp_path):
        path = tmp_path / "_attempts.json"
        path.write_text("{not json", encoding="utf-8")
        assert ChapterBudget(path).summary()["chapters"] == 0

    def test_summary_lists_over_budget_chapters(self, tmp_path):
        b = _budget(tmp_path, max_attempts=1)
        b.record(3, cost=0.1)
        s = b.summary()
        assert s["over_budget"] == [3]
        assert s["total_cost_cny"] == 0.1

    def test_file_shape_is_stable(self, tmp_path):
        b = _budget(tmp_path)
        b.record(2, cost=0.2)
        raw = json.loads((tmp_path / "_attempts.json").read_text(encoding="utf-8"))
        assert raw == {"2": {"attempts": 1, "cost_cny": 0.2, "outcome": "ok"}}
