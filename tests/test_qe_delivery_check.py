"""Tests for scripts/qe_delivery_check.py — the guard on the READ downshift.

The MetricX run itself needs an external checkout and is exercised by the real
command. These tests pin the three places a guard could silently lie: how
chapters are sampled, how duplicated chapter numbers are handled, and when a
baseline comparison is allowed to call a delivery "worse".
"""

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "qe_delivery_check.py"

_spec = importlib.util.spec_from_file_location("qe_delivery_under_test", _SCRIPT)
qdc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qdc)


class TestParseEnChapters:
    def test_splits_body_by_heading(self):
        chapters, counts = qdc.parse_en_chapters(
            "## Chapter 1: First\nline one\nline two\n"
            "## Chapter 2: Second\nbody\n")
        assert chapters == {1: "line one\nline two", 2: "body"}
        assert counts[1] == 1 and counts[2] == 1

    def test_duplicate_numbers_are_counted_so_the_caller_can_exclude_them(self):
        """《无限恐怖》 has two chapters numbered 301–308. A dict would
        concatenate them and score two chapters against one source chapter."""
        chapters, counts = qdc.parse_en_chapters(
            "## Chapter 301: A\nfirst\n## Chapter 301: B\nsecond\n## Chapter 302: C\nx\n")
        assert counts[301] == 2
        assert counts[302] == 1
        assert chapters[301] == "first\nsecond"      # documented merge behaviour
        assert {ch for ch, n in counts.items() if n == 1} == {302}


class TestSampleChapters:
    def test_is_deterministic_and_spans_the_book(self):
        picks = qdc.sample_chapters(set(range(1, 2302)), 6)
        assert picks == qdc.sample_chapters(set(range(1, 2302)), 6)
        assert picks[0] == 1 and picks[-1] == 2301
        assert len(picks) == len(set(picks)) == 6

    def test_more_than_available_returns_everything(self):
        assert qdc.sample_chapters({1, 2, 3}, 6) == [1, 2, 3]

    def test_empty_or_zero(self):
        assert qdc.sample_chapters(set(), 6) == []
        assert qdc.sample_chapters({1, 2, 3}, 0) == []

    def test_single_pick_is_not_an_endpoint(self):
        picks = qdc.sample_chapters(set(range(1, 11)), 1)
        assert len(picks) == 1 and 1 < picks[0] < 10


class TestCompareToBaseline:
    def _rows(self, scores):
        return [{"ch": ch, "seg": seg, "prediction": value}
                for (ch, seg), value in scores.items()]

    def _baseline(self, scores):
        out = {}
        for (ch, seg), value in scores.items():
            out.setdefault(str(ch), {})[str(seg)] = value
        return {"scores": out, "created_utc": "2026-10-26",
                "note": "compiled with qwen3.8-max"}

    def test_within_threshold_is_ok(self):
        base = {(1, 0): 10.0, (1, 1): 10.0}
        result = qdc.compare_to_baseline(
            self._rows({(1, 0): 10.2, (1, 1): 10.3}), self._baseline(base), 0.5)
        assert result["verdict"] == "ok"
        assert result["mean_delta_new_minus_baseline"] == 0.25

    def test_consistent_degradation_beyond_threshold_is_worse(self):
        """Identical positive deltas give a zero-width CI above 0."""
        base = {(1, 0): 10.0, (1, 1): 10.0, (1, 2): 10.0}
        result = qdc.compare_to_baseline(
            self._rows({(1, 0): 12.0, (1, 1): 12.0, (1, 2): 12.0}),
            self._baseline(base), 0.5)
        assert result["verdict"] == "worse"
        assert result["bootstrap_95ci"] == [2.0, 2.0]

    def test_noisy_degradation_is_inconclusive_not_worse(self):
        """Point estimate above the threshold, but the CI still touches 0 —
        the sample cannot support a rollback order."""
        base = {(1, 0): 10.0, (1, 1): 10.0}
        result = qdc.compare_to_baseline(
            self._rows({(1, 0): 13.0, (1, 1): 9.0}), self._baseline(base), 0.5)
        assert result["mean_delta_new_minus_baseline"] == 1.0
        assert result["verdict"] == "inconclusive"

    def test_no_shared_pairs(self):
        base = {(2, 0): 10.0}
        result = qdc.compare_to_baseline(
            self._rows({(1, 0): 12.0}), self._baseline(base), 0.5)
        assert result["verdict"] == "no_overlap"
        assert result["mean_delta_new_minus_baseline"] is None

    def test_unmatched_segments_are_counted_not_ignored(self):
        """A scored segment with no baseline counterpart must be visible to the
        caller, which refuses to issue a verdict on a partial comparison.
        (The opposite side — a scored run shorter than the input — is caught
        upstream by the length check in main().)"""
        base = {(1, 0): 10.0}
        result = qdc.compare_to_baseline(
            self._rows({(1, 0): 10.0, (3, 0): 11.0}), self._baseline(base), 0.5)
        assert result["unmatched_segments"] == 1
        assert result["paired_segments"] == 1

    def test_one_bad_segment_is_insufficient_not_ok(self):
        """A single pair cannot support a CI; it must not pass as evidence."""
        base = {(1, 0): 10.0}
        result = qdc.compare_to_baseline(
            self._rows({(1, 0): 13.0}), self._baseline(base), 0.5)
        assert result["verdict"] == "insufficient"

    def test_input_rows_are_rejected_with_an_actionable_error(self):
        """The real call site once passed build_rows output (no 'prediction');
        that KeyError'd only on real runs. Fail with a message instead."""
        base = {(1, 0): 10.0}
        with pytest.raises(ValueError, match="prediction"):
            qdc.compare_to_baseline(
                [{"ch": 1, "seg": 0, "source": "x", "hypothesis": "y"}],
                self._baseline(base), 0.5)

    def test_threshold_uses_the_unrounded_mean(self):
        """A true mean of 0.5004 is above a 0.5 threshold even though it
        rounds to 0.500 — rounding first would wave it through."""
        base = {(1, 0): 10.0, (1, 1): 10.0}
        result = qdc.compare_to_baseline(
            self._rows({(1, 0): 10.5008, (1, 1): 10.5}), self._baseline(base), 0.5)
        assert result["mean_delta_new_minus_baseline"] == 0.5
        assert result["verdict"] != "ok"

    def test_per_chapter_deltas_are_reported(self):
        base = {(1, 0): 10.0, (2, 0): 10.0}
        result = qdc.compare_to_baseline(
            self._rows({(1, 0): 10.0, (2, 0): 12.0}), self._baseline(base), 0.5)
        assert result["per_chapter_delta"] == {"1": 0.0, "2": 2.0}
