"""Three-provider model routing: GLM for 文字思考/输出, Qwen for 判断.

- READ / FIX → GLM 5.3 (full) + reasoning_effort=high
- WRITE → GLM 5.3 flash + reasoning_effort=low
- READBACK / REVIEW_TERMS → Qwen 3.8 max + thinking_budget
"""

import json
from unittest.mock import MagicMock

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


class TestReadUsesGlmFull:
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

        assert captured["kwargs"]["model"] == "glm-5.3"
        assert captured["kwargs"]["base_url"] == GLM_BASE_URL
        assert captured["kwargs"]["api_key"] == GLM_API_KEY
        assert captured["kwargs"]["reasoning_effort"] == "high"
        assert captured["kwargs"]["temperature"] == 0.3
        assert captured["kwargs"]["max_tokens"] == 16384


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
