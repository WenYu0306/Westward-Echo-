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
    # The node reads its key via config.node_llm_kwargs, which resolves the
    # routed provider's key at call time — so patch config, not a name this
    # module no longer imports. conftest already poisons it to a non-empty
    # fake; this keeps the intent explicit (a key present ⇒ the node runs).
    monkeypatch.setattr("src.config.LLM_API_KEY", "fake-key")
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
        # Key is resolved through config.node_llm_kwargs, not a module-local
        # name here; patch config so the "key present ⇒ node runs" intent holds.
        monkeypatch.setattr("src.config.LLM_API_KEY", "fake-key")
        review_terms_node(state)
        user_msg = [m for m in captured["messages"] if m.type == "human"][0]
        assert "South Mao, North Ma" in user_msg.content
        assert "two worlds split" not in user_msg.content


class TestReviewTermsAccounting:
    """The review node runs on qwen3.8-max; its spend must reach the ledger.

    Before this, review_terms recorded nothing at all, so every chapter's
    review cost was invisible in every cost figure the project produced.
    """

    def test_successful_call_is_recorded_with_model_and_elapsed(self, monkeypatch):
        from src.config import NODE_MODELS

        state = _state_with_decisions(
            _decision("聋婆婆", "Deaf Granny", category="character"))
        _mock_review(monkeypatch, {"reviews": []})
        recorded = MagicMock()
        monkeypatch.setattr(
            "src.agent.nodes.review_terms.token_ledger.record_response", recorded)

        review_terms_node(state)

        assert recorded.call_count == 1
        args, kwargs = recorded.call_args
        assert args[1] == "review_terms"
        assert args[2] == NODE_MODELS["review_terms"]
        assert kwargs["elapsed_s"] is not None

    def test_content_filter_fallback_is_booked_against_the_fallback_model(self, monkeypatch):
        from src.config import NODE_MODELS

        state = _state_with_decisions(
            _decision("南茅北马", "Southern Mao, Northern Ma", category="culture"))
        primary = MagicMock()
        primary.invoke.side_effect = Exception("1301 contentFilter rejected")
        fallback = MagicMock()
        fallback.invoke.return_value = MagicMock(
            content=json.dumps({"reviews": []}))
        clients = iter([primary, fallback])
        monkeypatch.setattr(
            "src.agent.nodes.review_terms.ChatOpenAI",
            lambda **kwargs: next(clients),
        )
        monkeypatch.setattr("src.config.LLM_API_KEY", "fake-key")
        monkeypatch.setattr("src.config.GLM_API_KEY", "fake-key")
        recorded = MagicMock()
        monkeypatch.setattr(
            "src.agent.nodes.review_terms.token_ledger.record_response", recorded)

        review_terms_node(state)

        assert recorded.call_count == 1
        assert recorded.call_args.args[2] == NODE_MODELS["review_terms_fallback"]

    def test_failed_call_records_nothing(self, monkeypatch):
        """No successful response → no phantom zero in the ledger."""
        state = _state_with_decisions(
            _decision("南茅北马", "Southern Mao, Northern Ma", category="culture"))
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("plain network failure")
        monkeypatch.setattr(
            "src.agent.nodes.review_terms.ChatOpenAI", lambda **kwargs: mock_llm)
        monkeypatch.setattr("src.config.LLM_API_KEY", "fake-key")
        recorded = MagicMock()
        monkeypatch.setattr(
            "src.agent.nodes.review_terms.token_ledger.record_response", recorded)

        assert review_terms_node(state) == {}
        recorded.assert_not_called()
