"""Three-provider model routing.

- READ / READBACK / REVIEW_TERMS → Qwen 3.8 max + thinking_budget
- WRITE → GLM 5.3 flash + reasoning_effort=low
- FIX → GLM 5.3 (full) + reasoning_effort=high

READ moved off GLM 5.3 because Zhipu's content filter rejects real web-novel
chapters with code 1301. Measured: 《间客》 by Mao Ni — a mainstream published
novel — lost 1 of 2 chapters to the filter on a normal run. Qwen takes its
reasoning budget as ``thinking_budget`` and must NOT also receive
``reasoning_effort``, so that parameter is expected to be absent for READ.
"""

import json
from unittest.mock import MagicMock

import pytest

from src.config import (
    GLM_API_KEY,
    GLM_BASE_URL,
    LLM_API_KEY,
    LLM_BASE_URL,
)


def _capture_factory(captured: dict, content: str):
    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def invoke(self, messages):
            resp = MagicMock()
            resp.content = content
            resp.response_metadata = {}
            return resp

    return FakeChatOpenAI


def _minimal_state():
    return {
        "chapter_title": "第1章 鬼节",
        "chapter_content": "聋婆婆住在九道沟村。",
        "chapter_number": 1,
        "target_lang": "en-US",
        "genre": "folk_religion",
        "content_type": "novel",
        "style_memo": "(no memo)",
        "previous_chapter_summary": "(first chapter)",
        "exact_matches_text": "(no glossary)",
        "semantic_matches_text": "(no semantic)",
        "exact_glossary": {},
        "api_key": "",
        "image_gaps": [],
    }


class TestReadUsesQwen:
    def test_read_node_model_and_base_url(self, monkeypatch):
        from src.agent.nodes.read import read_node
        from src.glossary.exact_store import ExactGlossary
        from src.glossary.semantic_store import SemanticGlossary

        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.read.ChatOpenAI",
            _capture_factory(captured, "{}"),
        )
        read_node(_minimal_state(), ExactGlossary(), SemanticGlossary())

        assert captured["kwargs"]["model"] == "qwen3.8-max"
        assert captured["kwargs"]["base_url"] == LLM_BASE_URL
        assert captured["kwargs"]["api_key"] == LLM_API_KEY
        assert captured["kwargs"]["extra_body"] == {"thinking_budget": 2048}
        assert captured["kwargs"]["temperature"] == 0.3
        assert captured["kwargs"]["max_tokens"] == 16384
        assert captured["kwargs"]["request_timeout"] == 900
        # Must NOT be set: Qwen uses thinking_budget instead, and the two
        # cannot be combined.
        assert "reasoning_effort" not in captured["kwargs"]


class TestWriteUsesGlmFlash:
    def test_write_node_model_and_base_url(self, monkeypatch):
        from src.agent.nodes.write import write_node

        content = json.dumps({
            "translated_text": "Deaf Granny lived in the village.",
            "chapter_title_en": "Ch1",
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "summary",
        })
        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.write.ChatOpenAI",
            _capture_factory(captured, content),
        )
        write_node(_minimal_state())

        assert captured["kwargs"]["model"] == "glm-5.3-flash"
        assert captured["kwargs"]["base_url"] == GLM_BASE_URL
        assert captured["kwargs"]["reasoning_effort"] == "low"
        assert captured["kwargs"]["temperature"] == 0.2
        assert captured["kwargs"]["max_tokens"] == 16384


class TestReadbackUsesQwen:
    def test_readback_node_model_and_base_url(self, monkeypatch):
        from src.agent.nodes.readback import readback_node

        content = json.dumps({
            "verdict": "PASS",
            "comprehension_issues": [],
            "engagement_gaps": [],
            "overall_impression": "ok",
        })
        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.readback.ChatOpenAI",
            _capture_factory(captured, content),
        )
        state = _minimal_state()
        state["translated_text"] = "Long enough text " * 20
        readback_node(state)

        assert captured["kwargs"]["model"] == "qwen3.8-max"
        assert captured["kwargs"]["base_url"] == LLM_BASE_URL
        assert captured["kwargs"]["extra_body"] == {
            "thinking_budget": 2048
        }
        assert captured["kwargs"]["temperature"] == 0.1
        assert captured["kwargs"]["max_tokens"] == 4096


class TestFixUsesGlmFull:
    def test_fix_node_model_and_base_url(self, monkeypatch):
        from src.agent.nodes.fix import fix_node

        content = json.dumps({
            "polished_text": "fixed text",
            "changes_made": [],
        })
        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.fix.ChatOpenAI",
            _capture_factory(captured, content),
        )
        state = _minimal_state()
        state["translated_text"] = "current text"
        state["readback_feedback"] = {"verdict": "NEEDS_FIX", "comprehension_issues": []}
        fix_node(state)

        assert captured["kwargs"]["model"] == "glm-5.3"
        assert captured["kwargs"]["base_url"] == GLM_BASE_URL
        assert captured["kwargs"]["reasoning_effort"] == "high"
        assert captured["kwargs"]["temperature"] == 0.1
        assert captured["kwargs"]["max_tokens"] == 16384


class TestReviewTermsUsesQwen:
    def test_review_terms_node_model_and_base_url(self, monkeypatch):
        from src.agent.nodes.review_terms import review_terms_node

        content = json.dumps({"reviews": []})
        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.review_terms.ChatOpenAI",
            _capture_factory(captured, content),
        )
        state = _minimal_state()
        state["read_analysis"] = {
            "terminology_decisions": [
                {"term_cn": "霸总", "proposed_en": "Alpha CEO", "category": "character"}
            ]
        }
        review_terms_node(state)

        assert captured["kwargs"]["model"] == "qwen3.8-max"
        assert captured["kwargs"]["base_url"] == LLM_BASE_URL
        assert captured["kwargs"]["extra_body"] == {
            "thinking_budget": 2048
        }
        assert captured["kwargs"]["temperature"] == 0.1
        assert captured["kwargs"]["max_tokens"] == 4096


# ── routing table is the single source of truth ──────────────────────
# Before this, MODEL_MAP was dead config: every node imported a provider
# constant directly, so re-tiering a node meant editing code, and editing
# MODEL_MAP changed nothing. These tests pin that the table now drives the
# nodes — the cost lever in COST_MODEL_20260922.md §0.5 is connected.


class TestRoutingTableDrivesNodes:
    def test_readback_follows_the_routed_model(self, monkeypatch):
        """Re-tiering READBACK via the routing table must change what it builds.

        This is the whole point of the change: READBACK only judges
        comprehension, so dropping it from qwen3.8-max (12/36 per M) to
        qwen3.8-flash (0.8/2.7) cuts that node ~15x with no code edit.
        """
        from src.agent.nodes import readback as rbmod
        from src import config

        monkeypatch.setitem(config.NODE_MODELS, "readback", "qwen3.8-flash")

        content = json.dumps({
            "verdict": "PASS", "comprehension_issues": [],
            "engagement_gaps": [], "overall_impression": "ok",
        })
        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.readback.ChatOpenAI",
            _capture_factory(captured, content),
        )
        state = _minimal_state()
        state["translated_text"] = "Long enough text " * 20
        rbmod.readback_node(state)

        assert captured["kwargs"]["model"] == "qwen3.8-flash"

    def test_glm_node_follows_the_routed_model_and_effort(self, monkeypatch):
        """A GLM node re-tiered stays on GLM's reasoning control, not Qwen's."""
        from src.agent.nodes import fix as fmod
        from src import config

        monkeypatch.setitem(config.NODE_MODELS, "fix", "glm-5.3-flash")
        monkeypatch.setitem(config.NODE_EFFORTS, "fix", "low")

        content = json.dumps({"polished_text": "fixed", "changes_made": []})
        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.fix.ChatOpenAI", _capture_factory(captured, content))
        state = _minimal_state()
        state["translated_text"] = "current text"
        state["readback_feedback"] = {"verdict": "NEEDS_FIX", "comprehension_issues": []}
        fmod.fix_node(state)

        assert captured["kwargs"]["model"] == "glm-5.3-flash"
        assert captured["kwargs"]["base_url"] == GLM_BASE_URL
        assert captured["kwargs"]["reasoning_effort"] == "low"
        # Qwen's control must not leak onto a GLM client.
        assert "extra_body" not in captured["kwargs"]

    def test_cross_provider_retier_routes_to_the_right_endpoint(self, monkeypatch):
        """Moving a Qwen node onto GLM must switch base_url and reasoning form."""
        from src.agent.nodes import review_terms as rtmod
        from src import config

        monkeypatch.setitem(config.NODE_MODELS, "review_terms", "glm-5.3-flash")
        monkeypatch.setitem(config.NODE_EFFORTS, "review_terms", "low")

        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.review_terms.ChatOpenAI",
            _capture_factory(captured, json.dumps({"reviews": []})),
        )
        state = _minimal_state()
        state["read_analysis"] = {"terminology_decisions": [
            {"term_cn": "霸总", "proposed_en": "Alpha CEO", "category": "character"}
        ]}
        rtmod.review_terms_node(state)

        assert captured["kwargs"]["model"] == "glm-5.3-flash"
        assert captured["kwargs"]["base_url"] == GLM_BASE_URL
        assert captured["kwargs"]["reasoning_effort"] == "low"
        assert "extra_body" not in captured["kwargs"]

    def test_write_fallback_follows_the_routed_model(self, monkeypatch):
        """The 1301 fallback is separately routable, so it can be re-tiered too."""
        from src.agent.nodes import write as wmod
        from src import config

        monkeypatch.setitem(config.NODE_MODELS, "write_fallback", "qwen3.8-flash")
        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.write.ChatOpenAI", _capture_factory(captured, "{}"))
        wmod._build_fallback_writer(
            **config.node_llm_kwargs("write_fallback", api_key_override="sk-test"))
        assert captured["kwargs"]["model"] == "qwen3.8-flash"

    def test_unknown_model_name_is_rejected_not_silently_routed(self):
        """A model the router cannot place must raise, not pick an endpoint."""
        from src.config import node_provider
        with pytest.raises(ValueError):
            node_provider("gpt-4o")

    def test_unknown_node_is_rejected(self):
        from src.config import node_model
        with pytest.raises(ValueError):
            node_model("quality_score")

    def test_env_override_is_the_documented_retier_switch(self, monkeypatch):
        from src.config import _env_overridden
        monkeypatch.setenv("XIDU_NODE_MODEL_READBACK", "qwen3.8-flash")
        out = _env_overridden(
            {"readback": "qwen3.8-max", "read": "qwen3.8-max"}, "MODEL")
        assert out == {"readback": "qwen3.8-flash", "read": "qwen3.8-max"}

    def test_byok_key_wins_over_env_on_a_qwen_node(self, monkeypatch):
        """A job's own key must not be replaced by the operator's env key."""
        from src.agent.nodes.read import read_node
        from src.glossary.exact_store import ExactGlossary
        from src.glossary.semantic_store import SemanticGlossary

        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.read.ChatOpenAI", _capture_factory(captured, "{}"))
        state = _minimal_state()
        state["api_key"] = "sk-user-owned"
        read_node(state, ExactGlossary(), SemanticGlossary())
        assert captured["kwargs"]["api_key"] == "sk-user-owned"

    def test_byok_key_wins_over_env_on_a_glm_node(self, monkeypatch):
        from src.agent.nodes.fix import fix_node

        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.fix.ChatOpenAI",
            _capture_factory(captured, json.dumps(
                {"polished_text": "fixed", "changes_made": []})))
        state = _minimal_state()
        state["api_key"] = "sk-user-owned"
        state["translated_text"] = "current text"
        state["readback_feedback"] = {"verdict": "NEEDS_FIX", "comprehension_issues": []}
        fix_node(state)
        assert captured["kwargs"]["api_key"] == "sk-user-owned"

    def test_every_routed_model_is_priced(self):
        from src.config import NODE_MODELS, MODEL_PRICES_CNY_PER_M
        for node, model in NODE_MODELS.items():
            assert model in MODEL_PRICES_CNY_PER_M, f"{node} -> {model} unpriced"
