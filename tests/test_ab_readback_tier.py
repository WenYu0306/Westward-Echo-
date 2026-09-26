"""Tests for scripts/ab_readback_tier.py — the READBACK tier A/B harness.

Three things here are easy to get wrong and expensive to get wrong, so each is
pinned with a literal-value assertion:

* **Chapter addressing.** The delivered `.md` numbers chapters by EN index, and
  the Chinese number printed in each title drifts from that index by a
  *non-constant* offset (measured on the real corpus: 1/2/3/4, plus outliers at
  302/362/502). Addressing by the title's Chinese number — or assuming a fixed
  offset — silently selects the *wrong chapter*. That would have made the B-tier
  re-translation re-translate six chapters nobody asked for.
* **The noise floor.** `_quality.json` holds 59 records for 58 chapters: Ch260
  appears twice, same tier, ~2 h apart, and its ``engagement_gaps`` differ by 2
  while ``output_len`` differs by 317. That is within-tier variance at
  temperature 0.1 with no seed. Without this baseline, a between-group delta of
  1–2 gets read as a tier effect when it is noise.
* **The cost guard.** A-tier is ``qwen3.8-max`` (expensive tier), so ``--execute``
  must be refused unless ``--allow-expensive`` is also given.

The A/B judgement deliberately does *not* use verdict agreement: all 59 historical
records are PASS (zero variance), so that metric can only ever return 100%.
``compare`` still computes it, but flags it as uninformative.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "ab_readback_tier.py"


@pytest.fixture(scope="module")
def ab():
    """Import the harness by path — scripts/ is not a package.

    Module level holds only constants and defs; every ``import src`` lives
    inside a function, so importing this cannot trigger config's import-time
    path evaluation.
    """
    spec = importlib.util.spec_from_file_location("ab_readback_tier", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ab_readback_tier"] = mod
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════════
# Chapter addressing
# ══════════════════════════════════════════════════════════════════
def _write_md(tmp_path, chapters):
    """Build a reduced deliverable with the real heading shape."""
    parts = []
    for num, title, body in chapters:
        parts.append(f"## Chapter {num}: {title}\n\n{body}\n\n---\n")
    path = tmp_path / "book_en.md"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


class TestChapterAddressing:
    def test_en_chapter_number_is_the_key(self, ab, tmp_path):
        """Chapters are keyed by the EN number in the heading."""
        md = _write_md(tmp_path, [
            (269, "第267章 来自雪尸的善报", "Body of 269."),
            (430, "第428章 鬼见愁", "Body of 430."),
        ])
        got = ab.extract_delivered_chapters(md)
        assert sorted(got) == [269, 430]
        assert got[269]["body"] == "Body of 269."
        assert got[430]["body"] == "Body of 430."

    def test_title_chinese_number_is_recorded_but_not_the_key(self, ab, tmp_path):
        """The drifting Chinese number is parsed into cn_num, never used to key.

        Here the offset is 2 — the real corpus has 1/2/3/4, so it must not be
        assumed constant, and it must not be used to look chapters up.
        """
        md = _write_md(tmp_path, [(269, "第267章 来自雪尸的善报", "B.")])
        got = ab.extract_delivered_chapters(md)
        assert got[269]["cn_num"] == 267
        assert 267 not in got

    def test_non_constant_offset_does_not_break_addressing(self, ab, tmp_path):
        """Offsets of 1, 2 and 4 in one file: each still addresses correctly."""
        md = _write_md(tmp_path, [
            (10, "第9章 松老", "Ten."),
            (269, "第267章 来自雪尸的善报", "TwoSixNine."),
            (820, "第816章 目光尽落山海关", "EightTwenty."),
        ])
        got = ab.extract_delivered_chapters(md)
        assert got[10]["cn_num"] == 9
        assert got[269]["cn_num"] == 267
        assert got[820]["cn_num"] == 816
        assert got[820]["body"] == "EightTwenty."

    def test_trailing_rule_is_stripped_from_body(self, ab, tmp_path):
        """The inter-chapter `---` is a separator, not body text."""
        md = _write_md(tmp_path, [(5, "第3章 测试", "Real body.")])
        got = ab.extract_delivered_chapters(md)
        assert got[5]["body"] == "Real body."
        assert "---" not in got[5]["body"]

    def test_body_char_count_matches_body(self, ab, tmp_path):
        md = _write_md(tmp_path, [(5, "第3章 测试", "Real body.")])
        got = ab.extract_delivered_chapters(md)
        assert got[5]["chars"] == len("Real body.")

    def test_last_chapter_is_not_truncated(self, ab, tmp_path):
        """The final chapter has no following heading — it must keep its body."""
        md = _write_md(tmp_path, [
            (1, "第1章 甲", "First."),
            (2, "第2章 乙", "Second and last."),
        ])
        got = ab.extract_delivered_chapters(md)
        assert got[2]["body"] == "Second and last."


# ══════════════════════════════════════════════════════════════════
# Sampling
# ══════════════════════════════════════════════════════════════════
class TestSampling:
    def test_duplicate_chapter_yields_one_sample(self, ab, tmp_path):
        """59 records / 58 chapters: the duplicate must not double-run (or bill)."""
        q = tmp_path / "_quality.json"
        q.write_text(json.dumps([
            {"ch_number": 10, "verdict": "PASS"},
            {"ch_number": 260, "verdict": "PASS"},
            {"ch_number": 260, "verdict": "PASS"},
            {"ch_number": 300, "verdict": "PASS"},
        ]), encoding="utf-8")
        assert ab.sampled_chapters(q) == [10, 260, 300]

    def test_samples_are_sorted(self, ab, tmp_path):
        q = tmp_path / "_quality.json"
        q.write_text(json.dumps([
            {"ch_number": 300}, {"ch_number": 10}, {"ch_number": 160},
        ]), encoding="utf-8")
        assert ab.sampled_chapters(q) == [10, 160, 300]


# ══════════════════════════════════════════════════════════════════
# The noise floor
# ══════════════════════════════════════════════════════════════════
class TestWithinTierVariance:
    def test_ch260_shape_gives_eg_floor_2(self, ab, tmp_path):
        """The real Ch260 duplicate: eg [0,2] → floor 2, ci [2,2] → floor 0."""
        q = tmp_path / "_quality.json"
        q.write_text(json.dumps([
            {"ch_number": 260, "verdict": "PASS", "comprehension_issues": 2,
             "engagement_gaps": 0, "output_len": 5626,
             "timestamp": "2026-07-29T01:26:54Z"},
            {"ch_number": 260, "verdict": "PASS", "comprehension_issues": 2,
             "engagement_gaps": 2, "output_len": 5943,
             "timestamp": "2026-07-29T03:20:49Z"},
        ]), encoding="utf-8")
        got = ab.within_tier_variance(q)
        assert got["available"] is True
        assert got["n_repeat_chapters"] == 1
        floor = got["noise_floor"]
        assert floor["engagement_gaps_max_gap"] == 2
        assert floor["comprehension_issues_max_gap"] == 0
        assert floor["output_len_max_gap"] == 317

    def test_no_duplicates_reports_unavailable(self, ab, tmp_path):
        """Without a repeat there is no baseline — say so, don't invent one."""
        q = tmp_path / "_quality.json"
        q.write_text(json.dumps([
            {"ch_number": 10, "engagement_gaps": 1},
            {"ch_number": 20, "engagement_gaps": 3},
        ]), encoding="utf-8")
        got = ab.within_tier_variance(q)
        assert got["available"] is False
        assert "note" in got

    def test_three_runs_takes_max_gap(self, ab, tmp_path):
        q = tmp_path / "_quality.json"
        q.write_text(json.dumps([
            {"ch_number": 7, "engagement_gaps": 0, "comprehension_issues": 1,
             "output_len": 100, "timestamp": "t1"},
            {"ch_number": 7, "engagement_gaps": 3, "comprehension_issues": 1,
             "output_len": 400, "timestamp": "t2"},
            {"ch_number": 7, "engagement_gaps": 1, "comprehension_issues": 5,
             "output_len": 250, "timestamp": "t3"},
        ]), encoding="utf-8")
        got = ab.within_tier_variance(q)
        floor = got["noise_floor"]
        assert floor["engagement_gaps_max_gap"] == 3
        assert floor["comprehension_issues_max_gap"] == 4
        assert floor["output_len_max_gap"] == 300


class TestMeasuredVariance:
    def test_same_shape_as_history(self, ab):
        """--repeat-baseline output must satisfy the same contract compare() uses."""
        first = [{"ch": 1, "ok": True, "engagement_gaps": 1, "comprehension_issues": 0},
                 {"ch": 2, "ok": True, "engagement_gaps": 0, "comprehension_issues": 2}]
        second = [{"ch": 1, "ok": True, "engagement_gaps": 4, "comprehension_issues": 0},
                  {"ch": 2, "ok": True, "engagement_gaps": 0, "comprehension_issues": 2}]
        got = ab.measure_within_tier_variance(first, second)
        assert got["available"] is True
        assert got["n_repeat_chapters"] == 2
        assert got["noise_floor"]["engagement_gaps_max_gap"] == 3
        assert got["noise_floor"]["comprehension_issues_max_gap"] == 0

    def test_no_common_chapter_is_unavailable(self, ab):
        got = ab.measure_within_tier_variance(
            [{"ch": 1, "ok": True, "engagement_gaps": 1, "comprehension_issues": 0}],
            [{"ch": 9, "ok": True, "engagement_gaps": 1, "comprehension_issues": 0}],
        )
        assert got["available"] is False


# ══════════════════════════════════════════════════════════════════
# compare() — the judgement
# ══════════════════════════════════════════════════════════════════
def _row(ch, ci, eg, impression="imp", verdict="PASS"):
    return {"ch": ch, "ok": True, "verdict": verdict,
            "comprehension_issues": ci, "engagement_gaps": eg,
            "impression": impression}


NOISE_EG2 = {"available": True, "n_repeat_chapters": 1, "chapters": [],
             "noise_floor": {"engagement_gaps_max_gap": 2,
                             "comprehension_issues_max_gap": 0}}


class TestCompare:
    def test_counts_are_summed_not_verdicts(self, ab):
        a = [_row(1, 2, 1), _row(2, 0, 3)]
        b = [_row(1, 3, 2), _row(2, 0, 1)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        assert got["comprehension_issues"]["A"]["sum"] == 2
        assert got["comprehension_issues"]["B"]["sum"] == 3
        assert got["comprehension_issues"]["delta_sum"] == 1
        assert got["engagement_gaps"]["A"]["sum"] == 4
        assert got["engagement_gaps"]["B"]["sum"] == 3
        assert got["engagement_gaps"]["delta_sum"] == -1

    def test_verdict_agreement_is_flagged_uninformative(self, ab):
        """All-PASS inputs give 100% — the note must say that carries no signal."""
        a = [_row(1, 0, 0), _row(2, 1, 1)]
        b = [_row(1, 3, 3), _row(2, 0, 0)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        assert got["verdict_agreement"]["rate"] == 1.0
        assert "无信息" in got["verdict_agreement"]["note"]

    def test_only_common_chapters_are_compared(self, ab):
        a = [_row(1, 0, 0), _row(2, 0, 0)]
        b = [_row(1, 0, 0), _row(3, 0, 0)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        assert got["n_common"] == 1

    def test_failed_rows_are_excluded_from_pairs(self, ab):
        a = [_row(1, 0, 0), {"ch": 2, "ok": False, "error": "boom"}]
        b = [_row(1, 0, 0), _row(2, 5, 5)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        assert got["n_failed_A"] == 1
        assert got["n_common"] == 1
        assert got["engagement_gaps"]["B"]["sum"] == 0

    def test_identical_impressions_are_counted(self, ab):
        a = [_row(1, 0, 0, "same"), _row(2, 0, 0, "diff-A")]
        b = [_row(1, 0, 0, "same"), _row(2, 0, 0, "diff-B")]
        got = ab.compare(a, b, {}, NOISE_EG2)
        assert got["impression_identical"] == 1

    def test_worse_better_same_split(self, ab):
        a = [_row(1, 0, 0), _row(2, 2, 2), _row(3, 1, 1)]
        b = [_row(1, 1, 1), _row(2, 0, 0), _row(3, 1, 1)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        assert got["chapters_worse_under_B"] == 1
        assert got["chapters_better_under_B"] == 1
        assert got["chapters_same"] == 1

    def test_historical_counts_are_attached_per_chapter(self, ab):
        hist = {1: {"comprehension_issues": 7, "engagement_gaps": 8}}
        got = ab.compare([_row(1, 0, 0)], [_row(1, 0, 0)], hist, NOISE_EG2)
        row = got["per_chapter"][0]
        assert row["hist_ci"] == 7
        assert row["hist_eg"] == 8

    def test_missing_history_gives_none_not_zero(self, ab):
        """A chapter with no historical record must show None, not a fake 0.

        Zero would read as "the historical run found no issues", which is a
        claim about a run that never happened.
        """
        got = ab.compare([_row(1, 0, 0)], [_row(1, 0, 0)], {}, NOISE_EG2)
        row = got["per_chapter"][0]
        assert row["hist_ci"] is None
        assert row["hist_eg"] is None

    def test_worse_counts_a_single_metric_regression(self, ab):
        """ci up / eg flat is still "worse under B".

        Pins the `or` in the worse predicate: with `and`, a chapter that
        regressed on exactly one metric would be counted as unchanged.
        """
        a = [_row(1, 0, 3)]
        b = [_row(1, 1, 3)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        assert got["per_chapter"][0]["ci_delta"] == 1
        assert got["per_chapter"][0]["eg_delta"] == 0
        assert got["chapters_worse_under_B"] == 1
        assert got["chapters_same"] == 0

    def test_better_counts_a_single_metric_gain(self, ab):
        """Mirror of the above for the `better` predicate."""
        a = [_row(1, 3, 3)]
        b = [_row(1, 3, 1)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        assert got["per_chapter"][0]["eg_delta"] == -2
        assert got["chapters_better_under_B"] == 1
        assert got["chapters_worse_under_B"] == 0

    def test_mixed_direction_is_worse_not_better(self, ab):
        """ci improves but eg regresses → counted as worse (quality risk)."""
        a = [_row(1, 3, 0)]
        b = [_row(1, 0, 2)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        assert got["chapters_worse_under_B"] == 1
        assert got["chapters_better_under_B"] == 0
        assert got["chapters_same"] == 0

    def test_chapters_same_is_the_remainder(self, ab):
        """same = common - worse - better, and the three must partition."""
        a = [_row(1, 0, 0), _row(2, 1, 1), _row(3, 2, 2)]
        b = [_row(1, 0, 0), _row(2, 2, 2), _row(3, 0, 0)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        assert got["n_common"] == 3
        total = (got["chapters_worse_under_B"]
                 + got["chapters_better_under_B"]
                 + got["chapters_same"])
        assert total == 3

    def test_impressions_are_truncated_at_300(self, ab):
        """The per-chapter dump keeps 300 chars, not the whole impression."""
        long_a, long_b = "a" * 900, "b" * 900
        got = ab.compare([_row(1, 0, 0, long_a)], [_row(1, 0, 0, long_b)],
                         {}, NOISE_EG2)
        row = got["per_chapter"][0]
        assert len(row["impression_A"]) == 300
        assert len(row["impression_B"]) == 300
        assert row["impression_A"] == "a" * 300

    def test_whitespace_only_impression_difference_still_counts(self, ab):
        """strip() before comparing: trailing whitespace is not a real diff."""
        got = ab.compare([_row(1, 0, 0, "same text")],
                         [_row(1, 0, 0, "same text   ")], {}, NOISE_EG2)
        assert got["impression_identical"] == 1

    def test_mean_is_sum_over_common_count(self, ab):
        """Pins the denominator: mutating it shifts every mean."""
        a = [_row(1, 1, 0), _row(2, 3, 0)]
        b = [_row(1, 0, 0), _row(2, 0, 0)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        assert got["comprehension_issues"]["A"]["mean"] == 2.0
        assert got["comprehension_issues"]["B"]["mean"] == 0.0


class TestSignificance:
    def test_delta_within_noise_is_called_uninformative(self, ab):
        """eg delta 1 against a floor of 2 → must NOT be read as a tier effect."""
        a = [_row(1, 0, 1)]
        b = [_row(1, 0, 2)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        sig = got["significance"]
        assert sig["available"] is True
        assert sig["per_metric"]["engagement_gaps"]["delta_sum_abs"] == 1
        assert sig["per_metric"]["engagement_gaps"]["exceeds_noise"] is False
        assert sig["any_metric_exceeds_noise"] is False
        assert "不能判定" in sig["interpretation"]

    def test_delta_above_noise_is_flagged_as_possible_signal(self, ab):
        """eg delta 5 against a floor of 2 → exceeds noise."""
        a = [_row(1, 0, 0), _row(2, 0, 0)]
        b = [_row(1, 0, 3), _row(2, 0, 2)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        sig = got["significance"]
        assert sig["per_metric"]["engagement_gaps"]["delta_sum_abs"] == 5
        assert sig["per_metric"]["engagement_gaps"]["exceeds_noise"] is True
        assert sig["any_metric_exceeds_noise"] is True

    def test_zero_delta_does_not_exceed_noise(self, ab):
        a = [_row(1, 2, 1)]
        b = [_row(1, 2, 1)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        sig = got["significance"]
        assert sig["any_metric_exceeds_noise"] is False
        assert sig["per_metric"]["comprehension_issues"]["delta_sum_abs"] == 0

    def test_no_noise_baseline_reports_unavailable(self, ab):
        """Without a baseline, say so rather than implying significance."""
        got = ab.compare([_row(1, 0, 0)], [_row(1, 0, 5)], {}, None)
        assert got["significance"]["available"] is False

    def test_negative_delta_uses_absolute_value(self, ab):
        """Direction must not hide a large gap: B better by 5 still exceeds 2."""
        a = [_row(1, 0, 5), _row(2, 0, 0)]
        b = [_row(1, 0, 0), _row(2, 0, 0)]
        got = ab.compare(a, b, {}, NOISE_EG2)
        sig = got["significance"]["per_metric"]["engagement_gaps"]
        assert sig["delta_sum_abs"] == 5
        assert sig["exceeds_noise"] is True


# ══════════════════════════════════════════════════════════════════
# cold_read_context — the attribution precondition
# ══════════════════════════════════════════════════════════════════
class TestColdReadContext:
    def test_prev_context_is_byte_identical_between_builds(self, ab, tmp_path):
        """If the two groups got different context, tier would not be the only
        variable and the whole comparison would be unattributable."""
        md = _write_md(tmp_path, [(9, "第8章 前", "Previous body."),
                                  (10, "第9章 今", "Current body.")])
        delivered = ab.extract_delivered_chapters(md)
        first = ab.make_prev_context("mechanical", 10, delivered)
        second = ab.make_prev_context("mechanical", 10, delivered)
        assert first == second
        assert first != ""

    def test_mechanical_extracts_the_previous_chapter(self, ab, tmp_path):
        md = _write_md(tmp_path, [(9, "第8章 前", "Previous body text here."),
                                  (10, "第9章 今", "Current.")])
        got = ab.make_prev_context("mechanical", 10, ab.extract_delivered_chapters(md))
        assert "Previous body text here." in got
        assert "mechanical excerpt" in got

    def test_mechanical_is_labelled_not_passed_off_as_production(self, ab, tmp_path):
        """The excerpt is NOT a production LLM summary; it must say so."""
        md = _write_md(tmp_path, [(9, "第8章 前", "Prev."), (10, "第9章 今", "Cur.")])
        got = ab.make_prev_context("mechanical", 10, ab.extract_delivered_chapters(md))
        assert "not a production summary" in got

    def test_first_chapter_has_no_previous(self, ab, tmp_path):
        md = _write_md(tmp_path, [(1, "第1章 始", "First.")])
        assert ab.make_prev_context("mechanical", 1,
                                    ab.extract_delivered_chapters(md)) == ""

    def test_none_mode_returns_empty(self, ab, tmp_path):
        md = _write_md(tmp_path, [(9, "第8章 前", "Prev."), (10, "第9章 今", "Cur.")])
        assert ab.make_prev_context("none", 10, ab.extract_delivered_chapters(md)) == ""

    def test_unknown_mode_raises(self, ab, tmp_path):
        """A typo'd mode must fail loudly, not silently fall back to empty."""
        with pytest.raises(ValueError):
            ab.make_prev_context("bogus", 10, {})

    def test_mechanical_truncates_long_previews(self, ab, tmp_path):
        """400 chars of excerpt, not the whole previous chapter.

        'q' rather than 'x': the label itself contains "excerpt", so counting
        'x' would include that one and read 401.
        """
        md = _write_md(tmp_path, [(9, "第8章 前", "q" * 2000),
                                  (10, "第9章 今", "Cur.")])
        got = ab.make_prev_context("mechanical", 10, ab.extract_delivered_chapters(md))
        assert got.count("q") == 400

    def test_mechanical_keeps_short_previews_whole(self, ab, tmp_path):
        """Under 400 chars: nothing is cut."""
        md = _write_md(tmp_path, [(9, "第8章 前", "q" * 120),
                                  (10, "第9章 今", "Cur.")])
        got = ab.make_prev_context("mechanical", 10, ab.extract_delivered_chapters(md))
        assert got.count("q") == 120

    def test_mechanical_collapses_whitespace(self, ab, tmp_path):
        """Newlines in the source excerpt would break the one-line list format."""
        md = _write_md(tmp_path, [(9, "第8章 前", "line one\n\n  line two\n"),
                                  (10, "第9章 今", "Cur.")])
        got = ab.make_prev_context("mechanical", 10, ab.extract_delivered_chapters(md))
        assert "line one line two" in got
        assert "\n" not in got


class TestSourceLeakCheck:
    def test_chinese_lines_are_reported(self, ab):
        """Clause 10: READBACK is a strict blind read; source names are a leak."""
        ctx = ("## CHARACTERS\n  - **Duan Tou Gui**\n"
               "## ESTABLISHED TERMINOLOGY\n"
               "- [ch1904] 阴兵过境 → Underworld soldiers passing through\n")
        got = ab.check_source_leak(ctx)
        assert len(got) == 1
        assert "阴兵过境" in got[0]

    def test_english_only_context_has_no_leak(self, ab):
        ctx = "## CHARACTERS\n  - **Duan Tou Gui**\n  - **Elder Pine**\n"
        assert ab.check_source_leak(ctx) == []

    def test_empty_context_has_no_leak(self, ab):
        assert ab.check_source_leak("") == []


# ══════════════════════════════════════════════════════════════════
# Cost guard
# ══════════════════════════════════════════════════════════════════
class TestCostGuard:
    def test_execute_without_allow_expensive_is_refused(self, ab, capsys):
        """A-tier is qwen3.8-max — the expensive tier. Refuse, exit 2."""
        rc = ab.main(["--execute"])
        assert rc == 2
        assert "--allow-expensive" in capsys.readouterr().err

    def test_mutually_exclusive_modes_are_rejected_by_argparse(self, ab):
        with pytest.raises(SystemExit) as exc:
            ab.main(["--dry-run", "--execute"])
        assert exc.value.code == 2

    def test_unknown_prev_context_mode_is_rejected_by_argparse(self, ab):
        with pytest.raises(SystemExit) as exc:
            ab.main(["--dry-run", "--prev-context", "bogus"])
        assert exc.value.code == 2


class TestCnyFromTokens:
    """The single source of the money formula.

    Exact assertions only — no ``approx`` with a default tolerance. A mutant
    that changes the 1e6 denominator to 1000001 shifts the figure by one part
    in a million: harmless-looking, but it proves the formula nobody can afford
    to get wrong (this is the number the user approves spending on) was never
    actually pinned.
    """

    def test_one_million_input_tokens_at_max_input_price(self, ab):
        assert ab.cny_from_tokens(1_000_000, 0, 12.0, 36.0) == 12.0

    def test_one_million_output_tokens_at_max_output_price(self, ab):
        assert ab.cny_from_tokens(0, 1_000_000, 12.0, 36.0) == 36.0

    def test_denominator_is_exactly_one_million(self, ab):
        """Both halves scaled by 1e6, summed: 12.0 + 2.7 = 14.7 exactly."""
        assert ab.cny_from_tokens(1_000_000, 1_000_000, 12.0, 2.7) == 14.7

    def test_flash_prices(self, ab):
        assert ab.cny_from_tokens(1_000_000, 1_000_000, 0.8, 2.7) == 3.5

    def test_zero_tokens_cost_nothing(self, ab):
        assert ab.cny_from_tokens(0, 0, 12.0, 36.0) == 0.0

    def test_scales_linearly(self, ab):
        one = ab.cny_from_tokens(1000, 1000, 12.0, 36.0)
        ten = ab.cny_from_tokens(10000, 10000, 12.0, 36.0)
        assert ten == one * 10

    def test_input_and_output_are_independent_terms(self, ab):
        """Swapping the two prices must change the result — proves the formula
        is not symmetric by accident (a mutant that crossed price_in with
        tokens_out would otherwise survive)."""
        normal = ab.cny_from_tokens(1_000_000, 2_000_000, 12.0, 36.0)
        swapped = ab.cny_from_tokens(2_000_000, 1_000_000, 12.0, 36.0)
        assert normal == 12.0 + 72.0
        assert swapped == 24.0 + 36.0
        assert normal != swapped

    def test_matches_the_real_a_group_figure(self, ab):
        """The actual dry-run A-group number, computed exactly:
        156784 in + 126356 out at (12.0, 36.0) = 1.881408 + 4.548816."""
        got = ab.cny_from_tokens(156784, 126356, 12.0, 36.0)
        assert got == 156784 / 1e6 * 12.0 + 126356 / 1e6 * 36.0
        assert round(got, 2) == 6.43

    def test_matches_the_real_b_group_figure(self, ab):
        got = ab.cny_from_tokens(156784, 126356, 0.8, 2.7)
        assert round(got, 2) == 0.47


class TestIsolateWritePaths:
    """The guarantee that a real run cannot pollute production data/.

    The production ledger was just zeroed (round12) and its "0 rows" is now the
    pollution fingerprint, so a silent failure here would undo that and make
    every later check read stale data as clean.
    """

    ENV_KEYS = ("XIDU_DATA_DIR", "XIDU_OUTPUT_DIR", "XIDU_TOKEN_LEDGER",
                "CHECKPOINT_DB_PATH", "CHROMA_PERSIST_PATH")

    @pytest.fixture
    def saved_env(self, monkeypatch):
        """Snapshot the redirect vars so the harness cannot leak into pytest."""
        for k in self.ENV_KEYS:
            monkeypatch.delenv(k, raising=False)
        yield
        # conftest set these before src was imported; restore is monkeypatch's job

    def test_returns_the_temp_root(self, ab, saved_env):
        """main() writes worker/payload/result files under this path; returning
        None would break every one of them."""
        tmp = ab.isolate_write_paths(copy_memo=False)
        assert tmp is not None
        assert tmp.is_dir()

    def test_all_write_paths_are_redirected(self, ab, saved_env):
        tmp = ab.isolate_write_paths(copy_memo=False)
        for k in self.ENV_KEYS:
            assert os.environ[k].startswith(str(tmp)), k

    def test_data_and_output_subdirs_are_created(self, ab, saved_env):
        tmp = ab.isolate_write_paths(copy_memo=False)
        assert (tmp / "data").is_dir()
        assert (tmp / "output").is_dir()

    def test_ledger_points_inside_the_temp_dir(self, ab, saved_env):
        tmp = ab.isolate_write_paths(copy_memo=False)
        assert os.environ["XIDU_TOKEN_LEDGER"] == str(tmp / "data" / "token_ledger.jsonl")

    def test_checkpoint_db_points_inside_the_temp_dir(self, ab, saved_env):
        tmp = ab.isolate_write_paths(copy_memo=False)
        assert os.environ["CHECKPOINT_DB_PATH"] == str(tmp / "data" / "checkpoints.db")

    def test_production_data_dir_is_not_the_target(self, ab, saved_env):
        """The redirected path must not be the repo's real data/."""
        ab.isolate_write_paths(copy_memo=False)
        assert os.environ["XIDU_DATA_DIR"] != str(ab.ROOT / "data")

    def test_memo_copy_is_on_by_default(self, ab, saved_env):
        """Default must be True: without the copied drawers StyleMemoStore finds
        nothing, the terminology section of cold_read_context degrades to empty,
        and both the estimate and the real run are then measuring the wrong
        prompt. The default is the safety property, so pin it."""
        import inspect
        sig = inspect.signature(ab.isolate_write_paths)
        assert sig.parameters["copy_memo"].default is True

    def test_copy_memo_true_copies_the_drawers(self, ab, tmp_path, monkeypatch,
                                               saved_env):
        """With copy_memo=True the production memo content must be readable from
        the isolated dir (read production, write temp)."""
        src_memo = tmp_path / "prod" / "data" / "translation_memory" / ab.BOOK_ID
        src_memo.mkdir(parents=True)
        (src_memo / "terms.md").write_text("# terms\n- [ch1] 甲 → A\n",
                                           encoding="utf-8")
        monkeypatch.setattr(ab, "ROOT", tmp_path / "prod")

        tmp = ab.isolate_write_paths(copy_memo=True)
        copied = tmp / "data" / "translation_memory" / ab.BOOK_ID / "terms.md"
        assert copied.exists()
        assert "甲 → A" in copied.read_text(encoding="utf-8")

    def test_copy_memo_false_leaves_no_memo(self, ab, tmp_path, monkeypatch,
                                            saved_env):
        src_memo = tmp_path / "prod" / "data" / "translation_memory" / ab.BOOK_ID
        src_memo.mkdir(parents=True)
        (src_memo / "terms.md").write_text("# terms\n", encoding="utf-8")
        monkeypatch.setattr(ab, "ROOT", tmp_path / "prod")

        tmp = ab.isolate_write_paths(copy_memo=False)
        assert not (tmp / "data" / "translation_memory" / ab.BOOK_ID).exists()

    def test_copy_memo_true_survives_a_missing_memo_dir(self, ab, tmp_path,
                                                        monkeypatch, saved_env):
        """A fresh checkout has no memo yet; that must not crash the harness."""
        monkeypatch.setattr(ab, "ROOT", tmp_path / "prod")
        (tmp_path / "prod").mkdir()
        tmp = ab.isolate_write_paths(copy_memo=True)
        assert tmp.is_dir()

    def test_each_call_gets_a_fresh_directory(self, ab, saved_env):
        first = ab.isolate_write_paths(copy_memo=False)
        second = ab.isolate_write_paths(copy_memo=False)
        assert first != second


# ══════════════════════════════════════════════════════════════════
# Report rendering — pure functions, so the numbers reported to the user
# are testable without the 14 MB deliverable (gitignored, absent in CI).
#
# Expectations are literal strings, never formatted with the same width the
# code uses: a mutant that changes a width spec would otherwise produce a
# self-consistent expectation and survive.
# ══════════════════════════════════════════════════════════════════
def _est(total=6.90, a_cny=6.43, b_cny=0.47, ratio=13.8, n_ch=58):
    return {
        "n_chapters": n_ch,
        "A": {"model": "qwen3.8-max", "price": (12.0, 36.0),
              "in": 156784.0, "out": 126356.0, "cny": a_cny},
        "B": {"model": "qwen3.8-flash", "price": (0.8, 2.7),
              "in": 156784.0, "out": 126356.0, "cny": b_cny},
        "total_cny": total,
        "saving_ratio": ratio,
        "crosscheck": {
            "in_over_en_median": 0.391, "in_est": 142124.0,
            "out_est_low": 74588.0, "out_est_high": 167098.0,
            "en_chars_total": 363649, "n_points": 6, "dropped_points": 1,
            "A_cny_range": (4.39, 7.72), "B_cny_range": (0.32, 0.56),
        },
    }


def _bt(no_fix=3.01, with_fix=3.72, total_chars=9683):
    return {
        "chapters": [
            {"en": 269, "cn": 267, "src_chars": 1560, "cny_no_fix": 0.498},
            {"en": 1472, "cn": 1470, "src_chars": 1498, "cny_no_fix": 0.495},
        ],
        "src_chars_total": total_chars,
        "cny_no_fix": no_fix, "fix_rate": 0.5, "cny_with_fix": with_fix,
        "fix_statistic": "mean", "fix_in": 4992.375, "fix_out": 7016.5,
        "n_fix_points": 4, "fix_cny_each": 0.2364,
    }


class TestGrandTotal:
    def test_sums_ab_and_b_tier(self, ab):
        assert ab.grand_total(_est(), _bt()) == pytest.approx(6.90 + 3.72)

    def test_includes_baseline_extra(self, ab):
        assert ab.grand_total(_est(), _bt(), 0.55) == pytest.approx(6.90 + 3.72 + 0.55)

    def test_default_extra_is_zero(self, ab):
        assert ab.grand_total(_est(), _bt()) == ab.grand_total(_est(), _bt(), 0.0)

    def test_rendered_total_matches_grand_total(self, ab):
        """The report must not compute its own sum — that is how two copies of a
        figure drift apart, and this figure is what gets approved."""
        report = ab.render_estimate_report(_est(total=6.90), _bt(with_fix=3.72))
        assert "合计 ≈ ¥10.62" in report
        assert f"¥{ab.grand_total(_est(total=6.90), _bt(with_fix=3.72)):.2f}" in report

    def test_rendered_total_includes_baseline_extra(self, ab):
        report = ab.render_estimate_report(_est(total=6.90), _bt(with_fix=3.72),
                                          0.55, 5)
        assert "合计 ≈ ¥11.17" in report


class TestRenderEstimateReport:
    def test_both_tiers_and_their_prices_appear(self, ab):
        out = ab.render_estimate_report(_est(), _bt())
        assert "qwen3.8-max" in out
        assert "qwen3.8-flash" in out
        assert "in 12.0/out 36.0" in out
        assert "in 0.8/out 2.7" in out

    def test_group_costs_are_shown_to_the_cent(self, ab):
        """The figure the user approves. Literal, not re-formatted."""
        out = ab.render_estimate_report(_est(), _bt())
        assert "→ ¥6.43" in out
        assert "→ ¥0.47" in out
        assert "小计 ¥6.90" in out

    def test_token_counts_are_grouped_with_commas(self, ab):
        out = ab.render_estimate_report(_est(), _bt())
        assert "156,784" in out
        assert "126,356" in out

    def test_saving_ratio_is_shown(self, ab):
        out = ab.render_estimate_report(_est(ratio=13.8), _bt())
        assert "13.8 倍" in out

    def test_b_tier_chapters_list_their_source_lengths(self, ab):
        out = ab.render_estimate_report(_est(), _bt())
        assert "EN Ch269" in out
        assert "中文第267章" in out
        assert "1560" in out
        assert "EN Ch1472" in out

    def test_b_tier_fix_derivation_is_disclosed(self, ab):
        """The FIX tokens must be shown as derived from calibration, so the
        reader can tell where ¥3.72 came from."""
        out = ab.render_estimate_report(_est(), _bt())
        assert "FIX token 取自标定 mean" in out
        assert "4,992" in out
        assert "7,016" in out
        assert "¥0.2364" in out

    def test_crosscheck_discloses_its_point_count(self, ab):
        out = ab.render_estimate_report(_est(), _bt())
        assert "标定 6 点" in out
        assert "剔除 en=0 的退化点 1 个" in out

    def test_handoff_estimate_is_shown_for_reconciliation(self, ab):
        """¥10.87 is the figure already approved; showing it next to the
        recalculated one is what makes a discrepancy visible."""
        out = ab.render_estimate_report(_est(), _bt())
        assert "交接估算 ¥10.87" in out

    def test_no_api_call_notice(self, ab):
        out = ab.render_estimate_report(_est(), _bt())
        assert "零 API 调用" in out

    def test_3_33_ratio_is_disclosed(self, ab):
        """Truncated chapters must not be costed on their truncated length."""
        out = ab.render_estimate_report(_est(), _bt())
        assert "3.33" in out

    def test_baseline_section_absent_by_default(self, ab):
        out = ab.render_estimate_report(_est(), _bt())
        assert "--repeat-baseline=" not in out

    def test_baseline_section_shows_chapter_count_and_cost(self, ab):
        out = ab.render_estimate_report(_est(), _bt(), 0.55, 5)
        assert "--repeat-baseline=5 章" in out
        assert "额外 ¥0.55" in out

    def test_zero_baseline_extra_omits_the_section(self, ab):
        """Falsy extra must not print an empty optional block."""
        out = ab.render_estimate_report(_est(), _bt(), 0.0, 0)
        assert "--repeat-baseline=" not in out

    def test_rules_are_68_wide(self, ab):
        """Literal widths: pinning them keeps a width mutant visible."""
        out = ab.render_estimate_report(_est(), _bt())
        assert "═" * 68 in out
        assert "─" * 68 in out
        assert "═" * 69 not in out


class TestRenderNoiseReport:
    NOISE = {"available": True, "n_repeat_chapters": 1,
             "chapters": [{"ch": 260, "n_runs": 2,
                           "engagement_gaps": [0, 2],
                           "comprehension_issues": [2, 2],
                           "output_len": [5626, 5943],
                           "timestamps": ["t1", "t2"]}],
             "noise_floor": {"engagement_gaps_max_gap": 2,
                             "comprehension_issues_max_gap": 0}}

    def test_unavailable_baseline_renders_nothing(self, ab):
        assert ab.render_noise_report({"available": False, "note": "x"}) == ""

    def test_shows_the_repeat_chapter(self, ab):
        out = ab.render_noise_report(self.NOISE)
        assert "Ch260" in out
        assert "同档位跑 2 次" in out

    def test_shows_the_floor_values(self, ab):
        out = ab.render_noise_report(self.NOISE)
        assert "engagement_gaps 组内最大差 2" in out

    def test_states_the_interpretation_rule(self, ab):
        """The whole point: a delta inside the floor is not a tier effect."""
        out = ab.render_noise_report(self.NOISE)
        assert "**不能**判定为档位差异" in out

    def test_admits_the_baseline_is_thin(self, ab):
        out = ab.render_noise_report(self.NOISE)
        assert "基线仅 1 章样本" in out


class TestRenderVerdictReport:
    def _verdict(self, **kw):
        base = {
            "n_common": 58, "n_failed_A": 0, "n_failed_B": 0,
            "comprehension_issues": {"A": {"sum": 40, "mean": 0.69},
                                     "B": {"sum": 44, "mean": 0.76},
                                     "delta_sum": 4},
            "engagement_gaps": {"A": {"sum": 30, "mean": 0.52},
                                "B": {"sum": 31, "mean": 0.53},
                                "delta_sum": 1},
            "verdict_agreement": {"rate": 1.0, "note": "⚠️ 无信息判据"},
            "impression_identical": 0,
            "chapters_worse_under_B": 5,
            "chapters_better_under_B": 2,
            "chapters_same": 51,
            "significance": {"available": False},
        }
        base.update(kw)
        return base

    def _costs(self):
        return ({"rows": 58, "cny": 6.41, "by_model": {"qwen3.8-max": {
                    "rows": 58, "in": 156784, "out": 126356, "cny": 6.41}}},
                {"rows": 58, "cny": 0.47, "by_model": {"qwen3.8-flash": {
                    "rows": 58, "in": 156784, "out": 126356, "cny": 0.47}}})

    def test_primary_criteria_are_labelled(self, ab):
        ca, cb = self._costs()
        out = ab.render_verdict_report(self._verdict(), ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(), _bt(), {"rows": 0, "cny": 0.0},
                                       "history", 0)
        assert "【主判据】comprehension_issues" in out
        assert "【主判据】engagement_gaps" in out

    def test_deltas_are_signed(self, ab):
        """A bare number hides direction; the sign is the finding."""
        ca, cb = self._costs()
        out = ab.render_verdict_report(self._verdict(), ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(), _bt(), {"rows": 0, "cny": 0.0},
                                       "history", 0)
        assert "差 +4" in out
        assert "差 +1" in out

    def test_verdict_agreement_is_flagged_uninformative_in_output(self, ab):
        ca, cb = self._costs()
        out = ab.render_verdict_report(self._verdict(), ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(), _bt(), {"rows": 0, "cny": 0.0},
                                       "history", 0)
        assert "100.0%" in out
        assert "无信息判据" in out

    def test_actual_cost_split_by_group(self, ab):
        """A and B must be separately attributable, or the 13.8x claim is
        unverifiable."""
        ca, cb = self._costs()
        out = ab.render_verdict_report(self._verdict(), ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(), _bt(), {"rows": 0, "cny": 0.0},
                                       "history", 0)
        assert "A 组 ¥6.41（58 行）" in out
        assert "B 组 ¥0.47（58 行）" in out

    def test_estimate_vs_actual_deviation_is_shown(self, ab):
        ca, cb = self._costs()
        out = ab.render_verdict_report(self._verdict(), ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(total=6.90), _bt(),
                                       {"rows": 0, "cny": 0.0}, "history", 0)
        assert "试算 ¥6.90 vs 实际 ¥6.88" in out
        assert "-0.02" in out

    def test_production_ledger_check_is_reported(self, ab):
        """The isolation guarantee has to be visible, not just true."""
        ca, cb = self._costs()
        out = ab.render_verdict_report(self._verdict(), ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(), _bt(), {"rows": 0, "cny": 0.0},
                                       "history", 0)
        assert "生产账本核验：0 行" in out

    def test_nonzero_production_ledger_is_still_printed(self, ab):
        ca, cb = self._costs()
        out = ab.render_verdict_report(self._verdict(), ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(), _bt(), {"rows": 0, "cny": 0.0},
                                       "history", 7)
        assert "生产账本核验：7 行" in out

    def test_scope_disclaimer_names_both_amounts(self, ab):
        """--execute covers only the A/B comparison. Both figures must be named
        so nobody believes the B-tier re-translation ran too."""
        ca, cb = self._costs()
        out = ab.render_verdict_report(self._verdict(), ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(total=6.90), _bt(with_fix=3.72),
                                       {"rows": 0, "cny": 0.0}, "history", 0)
        assert "只跑 A/B 档位对比" in out
        assert "¥6.90" in out
        assert "不含** B 档 6 章重译" in out
        assert "¥3.72" in out

    def test_significance_block_rendered_when_available(self, ab):
        v = self._verdict(significance={
            "available": True,
            "per_metric": {"engagement_gaps": {
                "delta_sum_abs": 1, "noise_floor": 2,
                "exceeds_noise": False}},
            "any_metric_exceeds_noise": False,
            "interpretation": "⚠️ 所有判据的组间差都在同档位组内方差范围内",
            "note": "样本仅 1 章",
        })
        ca, cb = self._costs()
        out = ab.render_verdict_report(v, ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(), _bt(), {"rows": 0, "cny": 0.0},
                                       "measured (--repeat-baseline)", 0)
        assert "【显著性】" in out
        assert "在噪音内" in out
        assert "measured (--repeat-baseline)" in out

    def test_significance_flags_a_real_signal(self, ab):
        v = self._verdict(significance={
            "available": True,
            "per_metric": {"engagement_gaps": {
                "delta_sum_abs": 9, "noise_floor": 2,
                "exceeds_noise": True}},
            "any_metric_exceeds_noise": True,
            "interpretation": "至少一个判据的组间差超过同档位组内方差",
            "note": "n",
        })
        ca, cb = self._costs()
        out = ab.render_verdict_report(v, ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(), _bt(), {"rows": 0, "cny": 0.0},
                                       "history", 0)
        assert "超过噪音" in out

    def test_missing_significance_says_so(self, ab):
        ca, cb = self._costs()
        out = ab.render_verdict_report(self._verdict(), ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(), _bt(), {"rows": 0, "cny": 0.0},
                                       "history", 0)
        assert "取不到噪音基线" in out

    def test_baseline_cost_shown_separately(self, ab):
        """Baseline spend must not be folded into the A/B reconciliation."""
        ca, cb = self._costs()
        out = ab.render_verdict_report(self._verdict(), ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(), _bt(),
                                       {"rows": 5, "cny": 0.55,
                                        "by_model": {}}, "history", 0)
        assert "另有基线遍 ¥0.55" in out

    def test_no_baseline_line_when_baseline_unused(self, ab):
        ca, cb = self._costs()
        out = ab.render_verdict_report(self._verdict(), ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {}},
                                       _est(), _bt(), {"rows": 0, "cny": 0.0},
                                       "history", 0)
        assert "另有基线遍" not in out

    def test_per_model_rows_are_listed(self, ab):
        ca, cb = self._costs()
        out = ab.render_verdict_report(self._verdict(), ca, cb,
                                       {"rows": 116, "cny": 6.88,
                                        "by_model": {
                                            "qwen3.8-max": {"rows": 58, "in": 156784,
                                                            "out": 126356, "cny": 6.41},
                                            "qwen3.8-flash": {"rows": 58, "in": 156784,
                                                              "out": 126356, "cny": 0.47}}},
                                       _est(), _bt(), {"rows": 0, "cny": 0.0},
                                       "history", 0)
        assert "qwen3.8-max" in out
        assert "qwen3.8-flash" in out


class TestRenderLeakReport:
    def test_no_leaks_reports_zero(self, ab):
        out = ab.render_leak_report({}, 58)
        assert "0/58" in out
        assert "非本脚本引入" not in out

    def test_leaks_are_counted_and_exemplified(self, ab):
        out = ab.render_leak_report({10: ["阴兵过境 → Underworld soldiers"]}, 58)
        assert "1/58" in out
        assert "Ch10" in out
        assert "阴兵过境" in out

    def test_only_three_examples_are_printed(self, ab):
        leaks = {i: [f"泄漏{i}"] for i in range(10)}
        out = ab.render_leak_report(leaks, 58)
        assert "10/58" in out
        assert out.count("行，例") == 3

    def test_attributes_the_leak_to_production_not_the_script(self, ab):
        """The leak comes from _build_cold_read_context passing style_memo text
        through — the same on the production path. Misattributing it here would
        send someone to fix the wrong file."""
        out = ab.render_leak_report({10: ["因果 → karma"]}, 58)
        assert "非本脚本引入" in out
        assert "不影响归因" in out

    def test_long_examples_are_truncated(self, ab):
        out = ab.render_leak_report({1: ["x" * 300]}, 5)
        assert "x" * 80 in out
        assert "x" * 81 not in out


# ══════════════════════════════════════════════════════════════════
# Constants — pinned so a silent drift shows up as a test failure
# ══════════════════════════════════════════════════════════════════
class TestConstants:
    def test_tiers_are_the_documented_lever(self, ab):
        assert ab.TIER_A == "qwen3.8-max"
        assert ab.TIER_B == "qwen3.8-flash"

    def test_b_tier_chapter_list_matches_the_handoff(self, ab):
        """§二-2a: Ch1472 + Ch269/430/548/811/820."""
        # read the literal out of main()'s source so a drift is caught
        src = _SCRIPT.read_text(encoding="utf-8")
        assert "b_tier_en = [269, 430, 548, 811, 820, 1472]" in src

    def test_en_per_cjk_median_is_the_calibrated_value(self, ab):
        """3.33 = median over 2290 untruncated chapters (§二-2a)."""
        assert ab.EN_PER_CJK_MEDIAN == 3.33

    def test_genre_keys_are_the_two_different_registries(self, ab):
        """personas uses folk_religion; estimate_offline's calibration uses difu.
        Conflating them silently misses the calibration bucket."""
        assert ab.GENRE == "folk_religion"
        assert ab.COST_GENRE == "difu"

    def test_no_hardcoded_calibration_numbers_remain(self, ab):
        """The cross-check basis must be derived, not copied.

        These literals used to sit in estimate_costs as a second cost model:
        2473.5/7350, 1286.0, 2881.0, 4992, 7016. When measurements/ is
        re-calibrated, hardcoded copies silently disagree with the fitted
        figures and nobody can tell which is right.

        Comments are excluded on purpose: they record the history of the defect
        and legitimately name the old values. What must not exist is a *live*
        literal feeding the arithmetic.
        """
        code_lines = [
            (i, ln) for i, ln in
            enumerate(_SCRIPT.read_text(encoding="utf-8").splitlines(), 1)
            if not ln.lstrip().startswith("#")
        ]
        for stale in ("2473.5", "7350", "1286.0", "2881.0", "4992", "7016"):
            hits = [i for i, ln in code_lines if stale in ln]
            assert not hits, (
                f"stale hardcoded calibration value {stale} still live at "
                f"line(s) {hits} — derive it from scripts/cost/measurements/")


# ══════════════════════════════════════════════════════════════════
# crosscheck_basis — derived from measurements/, never hardcoded
# ══════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def crosscheck(ab):
    """Derived calibration basis — module-scoped so the measurements are read once.

    Defined at module level, not as a class attribute: pytest deprecates
    class-scoped fixtures written as instance methods.
    """
    return ab.crosscheck_basis()


class TestCrosscheckBasis:
    def test_fix_statistic_is_mean_to_match_estimate_offline(self, ab, crosscheck):
        """mean, not median: estimate_offline.py:150 hardcodes 4992/7016, which
        are the *mean* of the calibration points (4992.4/7016.5). The median is
        4900/7509.2. Using the median would shift per-chapter FIX cost from
        ¥0.2364 to ¥0.2495 and break reconciliation with the handoff's ¥3.72."""
        assert crosscheck["fix_statistic"] == "mean"

    def test_fix_values_match_the_calibration_mean(self, ab, crosscheck):
        assert round(crosscheck["fix_in"], 1) == 4992.4
        assert round(crosscheck["fix_out"], 1) == 7016.5

    def test_fix_cost_per_chapter_reconciles(self, ab, crosscheck):
        """¥0.2364 per FIX chapter — the value the handoff's ¥3.72 rests on."""
        per = crosscheck["fix_in"] / 1e6 * 8.0 + crosscheck["fix_out"] / 1e6 * 28.0
        assert round(per, 4) == 0.2364

    def test_readback_out_range_is_the_measured_span(self, ab, crosscheck):
        assert crosscheck["out_min"] == 1286.0
        assert crosscheck["out_max"] == 2881.0

    def test_degenerate_point_is_dropped_and_counted(self, ab, crosscheck):
        """The difu-max point has en=0; keeping it would drive the ratio to
        infinity. It must be excluded *and* the exclusion made visible."""
        assert crosscheck["dropped"] == 1
        assert crosscheck["n_points"] == 6

    def test_ratio_is_a_median_not_a_mean(self, ab, crosscheck):
        """Median resists the difu-min outlier (en=784 → ratio 1.28), which a
        mean would let dominate."""
        assert round(crosscheck["in_over_en_median"], 3) == 0.391

    def test_fix_point_count(self, ab, crosscheck):
        assert crosscheck["n_fix_points"] == 4


# ══════════════════════════════════════════════════════════════════
# estimate_costs — the numbers that get reported to the user
# ══════════════════════════════════════════════════════════════════
class TestEstimateCosts:
    @pytest.fixture
    def prepared(self, ab, monkeypatch):
        """Seed the source cache so estimate_costs runs without reading the
        multi-megabyte corpus."""
        monkeypatch.setattr(ab, "_SRC_CACHE", {
            269: {"src_chars": 1560, "en_est": 1560 * 3.33, "title": "t"},
        })
        chapters = [{"en_num": 269, "en_chars": 5199, "src_chars": 1560}]
        return chapters, [(269, 267)]

    def test_a_and_b_see_identical_tokens(self, ab, prepared):
        """The whole point of plan 1: both groups get the same text, so the
        token counts must be equal and only the unit price may differ."""
        chapters, b_src = prepared
        est, _ = ab.estimate_costs(chapters, b_src)
        assert est["A"]["in"] == est["B"]["in"]
        assert est["A"]["out"] == est["B"]["out"]
        assert est["A"]["in"] > 0

    def test_tier_prices_come_from_the_price_tables(self, ab, prepared):
        chapters, b_src = prepared
        est, _ = ab.estimate_costs(chapters, b_src)
        assert tuple(est["A"]["price"]) == (12.0, 36.0)
        assert tuple(est["B"]["price"]) == (0.8, 2.7)

    def test_a_costs_far_more_than_b(self, ab, prepared):
        """13.8x is the documented lever ratio."""
        chapters, b_src = prepared
        est, _ = ab.estimate_costs(chapters, b_src)
        assert est["A"]["cny"] > est["B"]["cny"]
        assert 13.0 < est["saving_ratio"] < 15.0

    def test_total_is_the_sum_of_both_groups(self, ab, prepared):
        chapters, b_src = prepared
        est, _ = ab.estimate_costs(chapters, b_src)
        assert est["total_cny"] == pytest.approx(est["A"]["cny"] + est["B"]["cny"])

    def test_b_tier_uses_the_3_33_ratio_not_truncated_length(self, ab, monkeypatch):
        """A truncated chapter's delivered English length would understate cost.
        §二-2a: estimate from source hanzi × 3.33 instead."""
        monkeypatch.setattr(ab, "_SRC_CACHE", {
            269: {"src_chars": 1560, "en_est": 1560 * 3.33, "title": "t"},
        })
        _, bt = ab.estimate_costs(
            [{"en_num": 269, "en_chars": 5199, "src_chars": 1560}], [(269, 267)])
        ch = bt["chapters"][0]
        assert ch["src_chars"] == 1560
        assert ch["en"] == 269
        assert ch["cn"] == 267

    def test_b_tier_fix_rate_is_fifty_percent(self, ab, prepared):
        chapters, b_src = prepared
        _, bt = ab.estimate_costs(chapters, b_src)
        assert bt["fix_rate"] == 0.5

    def test_b_tier_with_fix_exceeds_without(self, ab, prepared):
        chapters, b_src = prepared
        _, bt = ab.estimate_costs(chapters, b_src)
        assert bt["cny_with_fix"] > bt["cny_no_fix"]
        expected = bt["cny_no_fix"] + bt["fix_cny_each"] * 0.5 * len(b_src)
        assert bt["cny_with_fix"] == pytest.approx(expected)

    def test_b_tier_source_chars_are_summed(self, ab, monkeypatch):
        monkeypatch.setattr(ab, "_SRC_CACHE", {
            269: {"src_chars": 1560, "en_est": 5195, "title": "a"},
            430: {"src_chars": 1559, "en_est": 5192, "title": "b"},
        })
        _, bt = ab.estimate_costs(
            [{"en_num": 269, "en_chars": 5199, "src_chars": 1560},
             {"en_num": 430, "en_chars": 6463, "src_chars": 1559}],
            [(269, 267), (430, 428)])
        assert bt["src_chars_total"] == 3119
        assert len(bt["chapters"]) == 2

    def test_crosscheck_scales_with_chapter_count(self, ab, prepared):
        """out_est_low/high are per-chapter × n, so doubling chapters doubles them."""
        chapters, b_src = prepared
        est1, _ = ab.estimate_costs(chapters, b_src)
        est2, _ = ab.estimate_costs(chapters * 2, b_src)
        assert est2["crosscheck"]["out_est_high"] == pytest.approx(
            est1["crosscheck"]["out_est_high"] * 2)

    def test_crosscheck_carries_its_derivation(self, ab, prepared):
        """The report must show how many calibration points fed it."""
        chapters, b_src = prepared
        est, _ = ab.estimate_costs(chapters, b_src)
        cc = est["crosscheck"]
        assert cc["n_points"] == 6
        assert cc["dropped_points"] == 1
        assert cc["en_chars_total"] == 5199

    def test_chapter_count_is_recorded(self, ab, prepared):
        chapters, b_src = prepared
        est, _ = ab.estimate_costs(chapters, b_src)
        assert est["n_chapters"] == 1

    def test_empty_chapter_list_gives_zero_not_crash(self, ab, prepared):
        """Zero chapters: everything must be 0.0, and saving_ratio must not
        divide by zero."""
        _, b_src = prepared
        est, _ = ab.estimate_costs([], b_src)
        assert est["A"]["cny"] == 0.0
        assert est["B"]["cny"] == 0.0
        assert est["saving_ratio"] == 0.0


# ══════════════════════════════════════════════════════════════════
# ledger_cost — reconciling the estimate against what was really spent
# ══════════════════════════════════════════════════════════════════
def _write_ledger(tmp_path, name, rows):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                 encoding="utf-8")
    return p


class TestLedgerCost:
    def test_missing_file_is_zero_not_crash(self, ab, tmp_path):
        got = ab.ledger_cost(tmp_path, "nope.jsonl")
        assert got["rows"] == 0
        assert got["cny"] == 0.0
        assert got["by_model"] == {}

    def test_empty_file_is_zero(self, ab, tmp_path):
        _write_ledger(tmp_path, "ledger_A.jsonl", [])
        got = ab.ledger_cost(tmp_path, "ledger_A.jsonl")
        assert got["rows"] == 0
        assert got["cny"] == 0.0

    def test_cost_uses_the_models_own_prices(self, ab, tmp_path):
        """qwen3.8-max is ¥12/M in, ¥36/M out (literal from the price table)."""
        _write_ledger(tmp_path, "ledger_A.jsonl", [
            {"model": "qwen3.8-max", "input_tokens": 1_000_000, "output_tokens": 0},
        ])
        got = ab.ledger_cost(tmp_path, "ledger_A.jsonl")
        assert got["cny"] == pytest.approx(12.0)

    def test_output_price_is_applied_separately(self, ab, tmp_path):
        _write_ledger(tmp_path, "ledger_A.jsonl", [
            {"model": "qwen3.8-max", "input_tokens": 0, "output_tokens": 1_000_000},
        ])
        got = ab.ledger_cost(tmp_path, "ledger_A.jsonl")
        assert got["cny"] == pytest.approx(36.0)

    def test_flash_tier_is_cheaper_per_token(self, ab, tmp_path):
        """qwen3.8-flash: ¥0.8/M in, ¥2.7/M out."""
        _write_ledger(tmp_path, "ledger_B.jsonl", [
            {"model": "qwen3.8-flash", "input_tokens": 1_000_000,
             "output_tokens": 1_000_000},
        ])
        got = ab.ledger_cost(tmp_path, "ledger_B.jsonl")
        assert got["cny"] == pytest.approx(0.8 + 2.7)

    def test_rows_are_counted_and_summed(self, ab, tmp_path):
        _write_ledger(tmp_path, "ledger_A.jsonl", [
            {"model": "qwen3.8-max", "input_tokens": 1000, "output_tokens": 500},
            {"model": "qwen3.8-max", "input_tokens": 2000, "output_tokens": 500},
        ])
        got = ab.ledger_cost(tmp_path, "ledger_A.jsonl")
        assert got["rows"] == 2
        slot = got["by_model"]["qwen3.8-max"]
        assert slot["rows"] == 2
        assert slot["in"] == 3000
        assert slot["out"] == 1000

    def test_models_are_bucketed_separately(self, ab, tmp_path):
        """A/B must be attributable per tier, so buckets cannot be merged."""
        _write_ledger(tmp_path, "ledger_A.jsonl", [
            {"model": "qwen3.8-max", "input_tokens": 10, "output_tokens": 10},
            {"model": "qwen3.8-flash", "input_tokens": 20, "output_tokens": 20},
        ])
        got = ab.ledger_cost(tmp_path, "ledger_A.jsonl")
        assert set(got["by_model"]) == {"qwen3.8-max", "qwen3.8-flash"}
        assert got["by_model"]["qwen3.8-max"]["in"] == 10
        assert got["by_model"]["qwen3.8-flash"]["in"] == 20

    def test_missing_token_fields_read_as_zero(self, ab, tmp_path):
        """An unmetered row must not crash the reconciliation."""
        _write_ledger(tmp_path, "ledger_A.jsonl", [
            {"model": "qwen3.8-max"},
        ])
        got = ab.ledger_cost(tmp_path, "ledger_A.jsonl")
        assert got["rows"] == 1
        assert got["by_model"]["qwen3.8-max"]["in"] == 0

    def test_missing_model_is_bucketed_as_unknown(self, ab, tmp_path):
        _write_ledger(tmp_path, "ledger_A.jsonl", [{"input_tokens": 5}])
        got = ab.ledger_cost(tmp_path, "ledger_A.jsonl")
        assert "?" in got["by_model"]

    def test_blank_lines_are_skipped(self, ab, tmp_path):
        d = tmp_path / "data"
        d.mkdir(parents=True, exist_ok=True)
        (d / "ledger_A.jsonl").write_text(
            json.dumps({"model": "qwen3.8-max", "input_tokens": 1000,
                        "output_tokens": 0}) + "\n\n\n",
            encoding="utf-8")
        got = ab.ledger_cost(tmp_path, "ledger_A.jsonl")
        assert got["rows"] == 1


# ══════════════════════════════════════════════════════════════════
# measure_within_tier_variance — missing-key robustness
# ══════════════════════════════════════════════════════════════════
class TestMeasuredVarianceEdgeCases:
    def test_missing_keys_read_as_zero(self, ab):
        """A row without the counter keys must not crash the baseline."""
        first = [{"ch": 1, "ok": True}]
        second = [{"ch": 1, "ok": True, "engagement_gaps": 3}]
        got = ab.measure_within_tier_variance(first, second)
        assert got["noise_floor"]["engagement_gaps_max_gap"] == 3
        assert got["noise_floor"]["comprehension_issues_max_gap"] == 0

    def test_failed_rows_are_excluded(self, ab):
        """A crashed run has no counters; including it would fabricate a gap."""
        first = [{"ch": 1, "ok": True, "engagement_gaps": 5,
                  "comprehension_issues": 0},
                 {"ch": 2, "ok": False, "error": "boom"}]
        second = [{"ch": 1, "ok": True, "engagement_gaps": 1,
                   "comprehension_issues": 0},
                  {"ch": 2, "ok": True, "engagement_gaps": 0,
                   "comprehension_issues": 0}]
        got = ab.measure_within_tier_variance(first, second)
        assert got["n_repeat_chapters"] == 1
        assert got["noise_floor"]["engagement_gaps_max_gap"] == 4

    def test_basis_names_the_tier_and_count(self, ab):
        first = [{"ch": 1, "ok": True, "engagement_gaps": 0,
                  "comprehension_issues": 0},
                 {"ch": 2, "ok": True, "engagement_gaps": 0,
                  "comprehension_issues": 0}]
        got = ab.measure_within_tier_variance(first, first)
        assert "2" in got["noise_floor"]["basis"]
        assert got["source"] == "measured (--repeat-baseline)"
