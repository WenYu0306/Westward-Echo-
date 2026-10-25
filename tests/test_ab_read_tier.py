"""Tests for scripts/ab_read_tier.py — the parts that decide the verdict.

The expensive part (the two arms and the judge) is exercised by the real run and
archived under ``archive/ab_read_tier_*``. These tests pin the two things that
could silently invert a conclusion: mapping the order-swapped judgments back to
arms, and the objective metrics used alongside the judge.
"""

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "ab_read_tier.py"

_spec = importlib.util.spec_from_file_location("ab_read_tier_under_test", _SCRIPT)
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)


class TestResolveVerdict:
    def test_agreement_on_a_counts_for_a(self):
        # Forward: version 1 = A. Reverse: version 2 = A.
        assert ab.resolve_verdict("1", "2") == "A"

    def test_agreement_on_b_counts_for_b(self):
        assert ab.resolve_verdict("2", "1") == "B"

    def test_both_orders_tie_is_a_tie(self):
        assert ab.resolve_verdict("tie", "tie") == "tie"

    def test_order_flip_is_noise_not_a_win(self):
        """The same arm losing in one order and winning in the other is
        position bias; counting it would fabricate a result."""
        assert ab.resolve_verdict("1", "1") == "noise"
        assert ab.resolve_verdict("2", "2") == "noise"

    def test_parse_failure_is_judge_error_not_noise(self):
        """A judge that failed is not evidence of position bias."""
        assert ab.resolve_verdict("parse_error", "tie") == "judge_error"
        assert ab.resolve_verdict("", "") == "judge_error"

    def test_one_tie_one_win_is_noise(self):
        assert ab.resolve_verdict("1", "tie") == "noise"


class TestObjective:
    def test_reports_length_ratio_and_chinese_residue(self):
        metrics = ab.objective("Hello world.", cjk=5, glossary={})
        assert metrics["ratio"] == 2.4
        assert metrics["chinese_chars"] == 0
        assert metrics["ends_on_punctuation"] is True

    def test_detects_a_truncated_ending(self):
        assert ab.objective("cut off mid", cjk=5, glossary={})["ends_on_punctuation"] is False

    def test_counts_glossary_renderings_present(self):
        metrics = ab.objective(
            "Su Nian met Deaf Granny.", cjk=10,
            glossary={"苏念": "Su Nian", "聋婆婆": "Deaf Granny", "王三": "Wang San"},
        )
        assert metrics["glossary_terms_present"] == 2

    def test_zero_cjk_does_not_divide_by_zero(self):
        assert ab.objective("Text.", cjk=0, glossary={})["ratio"] == 0
