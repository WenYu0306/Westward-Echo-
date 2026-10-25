"""Tests for scripts/qe_metricx.py — segmentation and aggregation.

The model run itself needs an external MetricX checkout and is exercised by the
real command; these tests pin the two places a conclusion could silently invert:
how long chapters are split, and how paired differences are aggregated.
"""

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "qe_metricx.py"

_spec = importlib.util.spec_from_file_location("qe_metricx_under_test", _SCRIPT)
qe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qe)


class TestSplitProportional:
    def test_returns_the_requested_number_of_pieces(self):
        text = "\n\n".join(f"Paragraph {i} " * 20 for i in range(6))
        assert len(qe.split_proportional(text, 6)) == 6

    def test_prefers_newlines_so_paragraphs_are_not_cut(self):
        pieces = qe.split_proportional("aaaa\n\nbbbb\n\ncccc\n\ndddd", 2)
        assert pieces[0] == "aaaa"          # cut landed on the first newline
        assert pieces[1].startswith("bbbb")

    def test_single_piece_or_empty_input(self):
        assert qe.split_proportional("abc", 1) == ["abc"]
        assert qe.split_proportional("", 4) == []

    def test_no_content_is_lost(self):
        text = "a" * 100 + "\n" + "b" * 100
        joined = "".join(qe.split_proportional(text, 3)).replace("\n", "")
        assert joined == text.replace("\n", "")


class TestPrepareRows:
    def test_skips_failed_arms_and_labels_every_row(self):
        outputs = {
            "A": [{"ch": 1, "ok": True, "text": "x " * 300},
                  {"ch": 2, "ok": False, "error": "boom"}],
            "B": [{"ch": 1, "ok": True, "text": "y " * 300}],
        }
        sources = {1: "源" * 100, 2: "源" * 100}
        rows = qe.prepare_rows(outputs, sources, segments=3)

        assert len(rows) == 6                      # 2 arms × 1 chapter × 3
        assert {(r["arm"], r["ch"]) for r in rows} == {("A", 1), ("B", 1)}
        assert all(r["reference"] == "" for r in rows)
        assert [r["seg"] for r in rows if r["arm"] == "A"] == [0, 1, 2]


class TestAggregate:
    def _rows(self, pairs):
        rows = []
        for chapter, index, a_score, b_score in pairs:
            rows.append({"arm": "A", "ch": chapter, "seg": index, "prediction": a_score})
            rows.append({"arm": "B", "ch": chapter, "seg": index, "prediction": b_score})
        return rows

    def test_positive_delta_means_flash_is_worse(self):
        summary = qe.aggregate(self._rows([(1, 0, 10.0, 12.0), (1, 1, 14.0, 15.0)]))
        assert summary["mean"]["A"] == 12.0
        assert summary["mean"]["B"] == 13.5
        assert summary["mean_delta_B_minus_A"] == 1.5
        assert summary["A_better_segments"] == 2
        assert summary["B_better_segments"] == 0

    def test_counts_better_worse_and_tied(self):
        summary = qe.aggregate(self._rows([
            (1, 0, 10.0, 11.0),      # A better
            (1, 1, 10.0, 9.0),       # B better
            (1, 2, 10.0, 10.0),      # tie
        ]))
        assert summary["A_better_segments"] == 1
        assert summary["B_better_segments"] == 1
        assert summary["tied_segments"] == 1
        assert summary["paired_segments"] == 3

    def test_unpaired_rows_are_not_counted_as_differences(self):
        rows = [{"arm": "A", "ch": 1, "seg": 0, "prediction": 10.0}]
        summary = qe.aggregate(rows)
        assert summary["paired_segments"] == 0
        assert summary["mean_delta_B_minus_A"] is None


class TestBootstrapCi:
    def test_identical_differences_give_a_zero_width_interval(self):
        assert qe.bootstrap_ci([2.0, 2.0, 2.0], n_resamples=200) == [2.0, 2.0]

    def test_one_difference_is_not_enough(self):
        assert qe.bootstrap_ci([1.0]) is None

    def test_interval_brackets_the_mean(self):
        low, high = qe.bootstrap_ci([1.0, 2.0, 3.0, 4.0], n_resamples=500)
        assert low <= 2.5 <= high
