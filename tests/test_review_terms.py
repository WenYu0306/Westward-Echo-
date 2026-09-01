"""Tests for the terminology review node (Qwen cross-check)."""

import json
from unittest.mock import MagicMock

from src.agent.nodes.review_terms import review_terms_node


def _state_with_decisions(*decisions):
    return {"read_analysis": {"terminology_decisions": list(decisions)}}


def _decision(term_cn, proposed_en, category="culture", **extra):
    d = {"term_cn": term_cn, "proposed_en": proposed_en, "category": category}
    d.update(extra)
    return d


def _mock_review(monkeypatch, review_json):
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content=json.dumps(review_json))
    monkeypatch.setattr(
        "src.agent.nodes.review_terms.ChatOpenAI",
        lambda **kwargs: mock_llm,
    )
    monkeypatch.setattr("src.agent.nodes.review_terms.LLM_API_KEY", "fake-key")
    return mock_llm


class TestReviewTerms:
    def test_no_decisions_returns_empty(self):
        state = {"read_analysis": {"terminology_decisions": []}}
        assert review_terms_node(state) == {}

    def test_skippable_categories_not_reviewed(self):
        # location is legitimately transliterated — not reviewed, no LLM call.
        state = _state_with_decisions(
            _decision("九道沟村", "Nine Ravines Village", category="location"),
        )
        assert review_terms_node(state) == {}

    def test_fail_review_corrects_rendering(self, monkeypatch):
        state = _state_with_decisions(
            _decision("南茅北马", "Southern Mao, Northern Ma", category="culture"),
        )
        _mock_review(monkeypatch, {
            "reviews": [
                {
                    "term_cn": "南茅北马",
                    "verdict": "fail",
                    "corrected": "the Maoshan Taoists vs the northern spirit mediums",
                },
            ]
        })
        result = review_terms_node(state)
        assert result["read_analysis"]["terminology_decisions"][0]["proposed_en"] == (
            "the Maoshan Taoists vs the northern spirit mediums"
        )

    def test_pass_review_leaves_rendering(self, monkeypatch):
        state = _state_with_decisions(
            _decision("聋婆婆", "Deaf Granny", category="character"),
        )
        _mock_review(monkeypatch, {
            "reviews": [{"term_cn": "聋婆婆", "verdict": "pass", "corrected": ""}],
        })
        result = review_terms_node(state)
        assert result == {}
        assert state["read_analysis"]["terminology_decisions"][0]["proposed_en"] == "Deaf Granny"

    def test_llm_failure_is_fail_safe(self, monkeypatch):
        state = _state_with_decisions(
            _decision("南茅北马", "Southern Mao, Northern Ma", category="culture"),
        )
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("boom")
        monkeypatch.setattr(
            "src.agent.nodes.review_terms.ChatOpenAI",
            lambda **kwargs: mock_llm,
        )
        result = review_terms_node(state)
        assert result == {}
        assert (
            state["read_analysis"]["terminology_decisions"][0]["proposed_en"]
            == "Southern Mao, Northern Ma"
        )

    def test_clean_rendering_drops_explanation(self):
        from src.agent.nodes.review_terms import _clean_rendering

        assert _clean_rendering("South Mao, North Ma — two worlds split") == "South Mao, North Ma"
        assert _clean_rendering("Deaf Granny") == "Deaf Granny"
        assert _clean_rendering("Chuma Shaman / spirit medium") == "Chuma Shaman / spirit medium"

    def test_reviewer_sees_clean_rendering(self, monkeypatch):
        # The reviewer prompt must contain the clean rendering, not the
        # "rendering — explanation" blob that READ crams into proposed_en.
        state = _state_with_decisions(
            _decision(
                "南茅北马",
                "South Mao, North Ma — two worlds split by the Yellow River",
                category="culture",
            ),
        )
        captured = {}

        def fake_factory(**kwargs):
            mock_llm = MagicMock()
            def invoke(messages):
                captured["messages"] = messages
                return MagicMock(content=json.dumps({"reviews": []}))
            mock_llm.invoke = invoke
            return mock_llm

        monkeypatch.setattr(
            "src.agent.nodes.review_terms.ChatOpenAI",
            fake_factory,
        )
        monkeypatch.setattr("src.agent.nodes.review_terms.LLM_API_KEY", "fake-key")
        review_terms_node(state)
        user_msg = [m for m in captured["messages"] if m.type == "human"][0]
        assert "South Mao, North Ma" in user_msg.content
        assert "two worlds split" not in user_msg.content
