"""Cultural-fidelity rule injection into READ (full) and WRITE (brief) prompts."""

import json
from unittest.mock import MagicMock

from langchain_core.messages import HumanMessage

from src.agent.nodes.read import read_node
from src.agent.nodes.write import write_node
from src.glossary.exact_store import ExactGlossary
from src.glossary.semantic_store import SemanticGlossary


def _fake_chat_openai_factory(captured: dict, content: str):
    """Return a FakeChatOpenAI class whose invoke() captures messages."""

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            captured["messages"] = messages
            resp = MagicMock()
            resp.content = content
            resp.response_metadata = {}
            return resp

    return FakeChatOpenAI


def _minimal_state(**overrides):
    state = {
        "chapter_title": "第1章 鬼节",
        "chapter_content": "聋婆婆住在九道沟村，村中大小事情都由聋婆婆和李大爷做主。",
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
    state.update(overrides)
    return state


def _user_prompt_text(captured: dict) -> str:
    assert "messages" in captured, "ChatOpenAI.invoke was never called"
    user_msgs = [m for m in captured["messages"] if isinstance(m, HumanMessage)]
    assert user_msgs, "No HumanMessage in the captured messages"
    return user_msgs[-1].content


class TestReadInjection:
    def test_read_node_injects_full_fidelity_rules(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.read.ChatOpenAI",
            _fake_chat_openai_factory(captured, "{}"),
        )
        read_node(_minimal_state(), ExactGlossary(), SemanticGlossary())
        text = _user_prompt_text(captured)

        assert "CULTURAL FIDELITY RULES" in text
        assert "Character Names" in text
        assert "Terms Of Address" in text
        # READ gets the FULL 8-category set, not the WRITE subset
        assert "Implicit Values" in text
        assert "Wordplay" in text

    def test_read_node_injects_for_script_branch(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.read.ChatOpenAI",
            _fake_chat_openai_factory(captured, "{}"),
        )
        read_node(
            _minimal_state(content_type="script"),
            ExactGlossary(),
            SemanticGlossary(),
        )
        text = _user_prompt_text(captured)
        assert "CULTURAL FIDELITY RULES" in text
        assert "Character Names" in text


class TestWriteInjection:
    def test_write_node_injects_brief_fidelity_rules(self, monkeypatch):
        content = json.dumps({
            "translated_text": (
                "Deaf Granny lived in Jiudaogou village, and every major decision "
                "there passed through her and Uncle Li."
            ),
            "chapter_title_en": "Chapter 1",
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "Deaf Granny and Uncle Li run the village.",
        })
        captured = {}
        monkeypatch.setattr(
            "src.agent.nodes.write.ChatOpenAI",
            _fake_chat_openai_factory(captured, content),
        )
        write_node(_minimal_state())
        text = _user_prompt_text(captured)

        assert "CULTURAL FIDELITY RULES" in text
        assert "Character Names" in text
        assert "Terms Of Address" in text
        # WRITE gets only the brief subset, not the full 8
        assert "Implicit Values" not in text
        assert "Wordplay" not in text
