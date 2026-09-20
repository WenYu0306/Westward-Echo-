"""Dual-model split: READ runs on DeepSeek (cultural understanding),
WRITE/READBACK/FIX run on the primary provider (Qwen)."""

import json
from unittest.mock import MagicMock

from src.config import (
    FLASH_MODEL,
    LLM_BASE_URL,
    LLM_MODEL,
    MODEL_MAP,
    READ_API_KEY,
    READ_BASE_URL,
    READ_MODEL,
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


class TestReadUsesDeepSeek:
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

        assert captured["kwargs"]["model"] == READ_MODEL
        assert captured["kwargs"]["base_url"] == READ_BASE_URL
        assert captured["kwargs"]["api_key"] == READ_API_KEY


class TestWriteUsesPrimaryProvider:
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

        assert captured["kwargs"]["model"] == LLM_MODEL
        assert captured["kwargs"]["base_url"] == LLM_BASE_URL


class TestWriteFlashMode:
    """flash mode (use_flash_writer=True) routes to FLASH_MODEL, not the primary."""

    def _capture_write_model(self, monkeypatch, state):
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
        write_node(state)
        return captured["kwargs"]["model"]

    def test_flash_mode_uses_flash_model(self, monkeypatch):
        state = _minimal_state()
        state["use_flash_writer"] = True
        assert self._capture_write_model(monkeypatch, state) == FLASH_MODEL

    def test_pro_mode_uses_primary_model(self, monkeypatch):
        state = _minimal_state()  # use_flash_writer defaults to False
        assert self._capture_write_model(monkeypatch, state) == MODEL_MAP["translate"]
