"""Tests for src/sensitive_terms.py."""

from src.sensitive_terms import SENSITIVE_TERMS, build_sensitive_term_context


class TestSensitiveTerms:

    def test_has_at_least_4_terms(self):
        assert len(SENSITIVE_TERMS) >= 4

    def test_shang_shen_has_warning_about_possession(self):
        assert "上身" in SENSITIVE_TERMS
        assert "possession" in SENSITIVE_TERMS["上身"].lower()

    def test_difu_has_warning_about_hell(self):
        assert "地府" in SENSITIVE_TERMS
        assert "hell" in SENSITIVE_TERMS["地府"].lower()

    def test_context_for_sensitive_text(self):
        ctx = build_sensitive_term_context("这位弟马请了仙家上身")
        assert len(ctx) > 0
        assert "TERMINOLOGY WARNINGS" in ctx

    def test_context_contains_relevant_terms(self):
        ctx = build_sensitive_term_context("仙家上身附体请神")
        assert "上身" in ctx
        assert "请神" in ctx

    def test_context_empty_for_no_sensitive_terms(self):
        ctx = build_sensitive_term_context("普通文本，没有敏感词")
        assert ctx == ""

    def test_context_formatted_as_markdown(self):
        ctx = build_sensitive_term_context("地府阎王上身")
        assert ctx.startswith("## TERMINOLOGY WARNINGS")
