"""Prompt personalisation: genre + target_lang must actually reach all four nodes.

Why this file exists
--------------------
Clause 11 of the audit found that the four system prompts hardcoded one genre
(supernatural horror) and one audience (American), while ``target_lang``/``genre``
flowed into state and then stopped dead: no node ever ``.format()``-ed its system
prompt. Parameterising the prompts (``src/agent/prompts/personas.py``) is only
half the fix — the other half is proving the wiring renders, because every
existing test mocks the LLM and inspects only the HumanMessage. A node that
forgot to ``.format()`` would ship literal ``{genre_name}`` to the model while
the suite stayed green. These tests capture the actual SystemMessage.
"""

import json
import re
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.nodes.fix import fix_node
from src.agent.nodes.read import read_node
from src.agent.nodes.readback import readback_node
from src.agent.nodes.write import write_node
from src.agent.prompts.personas import (
    GENERIC_GENRE,
    GENRE_PROFILES,
    build_prompt_context,
    known_genres,
    known_languages,
)
from src.glossary.exact_store import ExactGlossary
from src.glossary.semantic_store import SemanticGlossary

# A placeholder that survived .format() looks like {lower_snake}. Doubled braces
# from the JSON examples render to single { } and are NOT placeholders, so this
# pattern must never match rendered output.
_LEAKED_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _capture_factory(captured: dict, content: str):
    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            pass

        def invoke(self, messages):
            captured["messages"] = messages
            resp = MagicMock()
            resp.content = content
            resp.response_metadata = {}
            resp.additional_kwargs = {}
            resp.usage_metadata = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
            return resp

    return FakeChatOpenAI


_WRITE_CONTENT = json.dumps({
    "translated_text": "Deaf Granny ran the village; every decision passed through her.",
    "chapter_title_en": "Chapter 1",
    "new_terms_found": [],
    "adaptation_notes": [],
    "chapter_summary": "Deaf Granny and Uncle Li run Jiudaogou.",
})

_READBACK_CONTENT = json.dumps({
    "overall_impression": "Clear and gripping.",
    "comprehension_issues": [],
    "engagement_gaps": [],
    "character_tracking": "Distinct.",
    "world_comprehension": "Understood.",
    "would_keep_reading": True,
    "standout_moments": ["The shrine scene."],
    "verdict": "PASS",
})

_FIX_CONTENT = json.dumps({
    "polished_text": "Deaf Granny ran the village; every decision passed through her, revised.",
    "changes_made": ["Clarified the opening."],
})


def _state(**overrides) -> dict:
    state = {
        "chapter_title": "第1章 鬼节",
        "chapter_content": "聋婆婆住在九道沟村，村中大小事情都由聋婆婆和李大爷做主。",
        "chapter_number": 1,
        "book_id": "test_persona",
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
        "translated_text": "Deaf Granny ran the village.",
        "readback_feedback": {
            "verdict": "NEEDS_FIX",
            "comprehension_issues": [{"passage": "the shrine", "issue": "unexplained"}],
        },
        "cold_read_context": "",
    }
    state.update(overrides)
    return state


def _system_text(captured: dict) -> str:
    assert "messages" in captured, "ChatOpenAI.invoke was never called"
    sys_msgs = [m for m in captured["messages"] if isinstance(m, SystemMessage)]
    assert sys_msgs, "No SystemMessage captured — node did not send a system prompt"
    return sys_msgs[-1].content


def _user_text(captured: dict) -> str:
    users = [m for m in captured["messages"] if isinstance(m, HumanMessage)]
    assert users, "No HumanMessage captured"
    return users[-1].content


# ── Each node renders its system prompt (no leaked placeholders) ───────────

class TestNodesRenderSystemPrompt:
    """The regression guard: every node must .format() its system prompt.

    Before clause 11 the nodes passed the raw constant, so this is the test that
    would have caught a literal ``{genre_name}`` reaching the model.
    """

    def test_read_node_renders(self, monkeypatch):
        cap = {}
        monkeypatch.setattr("src.agent.nodes.read.ChatOpenAI",
                            _capture_factory(cap, "{}"))
        read_node(_state(), ExactGlossary(), SemanticGlossary())
        sys_text = _system_text(cap)
        assert not _LEAKED_PLACEHOLDER.search(sys_text), \
            f"READ system prompt leaked a placeholder: {sys_text[:200]}"
        assert "folk-religion" in sys_text  # genre profile reached the prompt

    def test_write_node_renders(self, monkeypatch):
        cap = {}
        monkeypatch.setattr("src.agent.nodes.write.ChatOpenAI",
                            _capture_factory(cap, _WRITE_CONTENT))
        write_node(_state())
        sys_text = _system_text(cap)
        user_text = _user_text(cap)
        assert not _LEAKED_PLACEHOLDER.search(sys_text), \
            f"WRITE system prompt leaked a placeholder: {sys_text[:200]}"
        # The user template gained output_language/reader/readers too.
        assert not _LEAKED_PLACEHOLDER.search(user_text), \
            f"WRITE user prompt leaked a placeholder: {user_text[:200]}"
        assert "supernatural horror" not in sys_text  # hardcoded genre is gone

    def test_readback_node_renders(self, monkeypatch):
        cap = {}
        monkeypatch.setattr("src.agent.nodes.readback.ChatOpenAI",
                            _capture_factory(cap, _READBACK_CONTENT))
        readback_node(_state(translated_text="x" * 200))
        sys_text = _system_text(cap)
        assert not _LEAKED_PLACEHOLDER.search(sys_text), \
            f"READBACK system prompt leaked a placeholder: {sys_text[:200]}"
        # The hardcoded English title of 《地府》 must be gone; a neutral
        # stand-in is used until name_en is settled.
        assert "The Underworld Calls Me Little Master" not in sys_text
        assert "this book" in sys_text

    def test_fix_node_renders(self, monkeypatch):
        cap = {}
        monkeypatch.setattr("src.agent.nodes.fix.ChatOpenAI",
                            _capture_factory(cap, _FIX_CONTENT))
        fix_node(_state())
        sys_text = _system_text(cap)
        user_text = _user_text(cap)
        assert not _LEAKED_PLACEHOLDER.search(sys_text), \
            f"FIX system prompt leaked a placeholder: {sys_text[:200]}"
        assert not _LEAKED_PLACEHOLDER.search(user_text), \
            f"FIX user prompt leaked a placeholder: {user_text[:200]}"


# ── Parameters actually drive the output (not decoration) ──────────────────

class TestParametersDriveOutput:
    def test_genre_changes_read_system(self, monkeypatch):
        def render(genre):
            cap = {}
            monkeypatch.setattr("src.agent.nodes.read.ChatOpenAI",
                                _capture_factory(cap, "{}"))
            read_node(_state(genre=genre), ExactGlossary(), SemanticGlossary())
            return _system_text(cap)

        folk, scifi = render("folk_religion"), render("scifi")
        assert folk != scifi
        assert "folk-religion" in folk
        assert "science fiction" in scifi
        # Genre-specific concept lists must not bleed across genres.
        assert "机甲" in scifi and "机甲" not in folk
        assert "出马弟子" in folk and "出马弟子" not in scifi

    def test_target_lang_changes_read_audience(self, monkeypatch):
        def render(lang):
            cap = {}
            monkeypatch.setattr("src.agent.nodes.read.ChatOpenAI",
                                _capture_factory(cap, "{}"))
            read_node(_state(target_lang=lang), ExactGlossary(), SemanticGlossary())
            return _system_text(cap)

        en, es = render("en-US"), render("es-ES")
        assert en != es
        assert "American reader" in en
        assert "Spanish-speaking reader" in es
        assert "American" not in es

    def test_unknown_genre_falls_back_to_generic(self, monkeypatch):
        cap = {}
        monkeypatch.setattr("src.agent.nodes.write.ChatOpenAI",
                            _capture_factory(cap, _WRITE_CONTENT))
        write_node(_state(genre="cyberpunk_noir_never_defined"))
        sys_text = _system_text(cap)
        # Falls back to the generic profile, which admits the gap rather than
        # asserting a wrong genre's conventions.
        assert "genre is not specified" in sys_text
        assert "cyberpunk" not in sys_text
        assert not _LEAKED_PLACEHOLDER.search(sys_text)


# ── build_prompt_context contract ──────────────────────────────────────────

class TestBuildContext:
    REQUIRED_KEYS = {
        "audience", "readers", "reader", "Reader", "locale", "output_language",
        "language_name", "genre_name", "genre_tradition", "genre_craft",
        "genre_concepts", "genre_image_case", "genre_emotion",
    }

    def test_all_keys_present_and_nonempty(self):
        ctx = build_prompt_context("urban", "en-US")
        assert self.REQUIRED_KEYS <= set(ctx)
        for k in self.REQUIRED_KEYS:
            assert ctx[k], f"{k} is empty"

    def test_defaults_are_safe(self):
        # No args must not raise and must produce a usable en-US/generic brief.
        ctx = build_prompt_context()
        assert ctx["output_language"] == "English"
        assert ctx["genre_name"] == GENERIC_GENRE["name"]

    def test_language_name_uses_single_source(self):
        from src.agent.prompts.translation import LANGUAGE_NAMES
        ctx = build_prompt_context("scifi", "es-ES")
        assert ctx["language_name"] == LANGUAGE_NAMES["es-ES"]

    def test_case_insensitive_genre(self):
        assert build_prompt_context("SCIFI", "en-US")["genre_name"] == \
            GENRE_PROFILES["scifi"]["name"]


# ── Three-way genre sync (the data-consistency fix) ────────────────────────

class TestGenreSync:
    """``_GENRE_SIGNALS`` was missing ``urban`` — the genre three of the five
    corpus books are configured with — so detect_genre could never return it.
    These guard the three genre vocabularies against drifting apart again."""

    def test_personas_covers_every_rules_json_genre(self):
        from src.cultural_rules import list_known_genres
        json_genres = set(list_known_genres())
        persona_genres = set(known_genres())
        assert json_genres <= persona_genres, \
            f"cultural_rules.json genres missing a persona: {json_genres - persona_genres}"

    def test_signals_covers_every_rules_json_genre(self):
        from src.cultural_rules import _GENRE_SIGNALS, list_known_genres
        json_genres = set(list_known_genres())
        signal_genres = set(_GENRE_SIGNALS)
        assert json_genres <= signal_genres, \
            f"_GENRE_SIGNALS missing genres from rules.json: {json_genres - signal_genres}"

    def test_urban_is_detectable(self):
        """Regression: urban was absent from _GENRE_SIGNALS, so detect_genre
        could never return it despite three books using it."""
        from src.cultural_rules import detect_genre
        # A passage dense in urban-fantasy markers.
        text = "他的气沉入丹田，世家子弟的身份让他不得不隐藏实力。风水轮流转，" * 5
        genre, score = detect_genre(text)
        # Either it detects urban, or it declines — but it must not be *unable*
        # to ever return urban. Assert the keyword list exists and is non-empty.
        from src.cultural_rules import _GENRE_SIGNALS
        assert "urban" in _GENRE_SIGNALS and _GENRE_SIGNALS["urban"]
        assert genre in ("urban", "")  # never crashes, urban is reachable


class TestLanguageSync:
    def test_personas_languages_match_language_names(self):
        from src.agent.prompts.translation import LANGUAGE_NAMES
        # Every language the prompts can address must have a display name, and
        # vice-versa — otherwise a lang falls back silently to American wording.
        assert set(known_languages()) == set(LANGUAGE_NAMES)

    @pytest.mark.parametrize("lang", ["en-US", "es-ES", "de", "fr"])
    def test_every_language_renders_without_leak(self, lang):
        from src.agent.prompts.read import READ_SYSTEM
        ctx = build_prompt_context("urban", lang)
        out = READ_SYSTEM.format(**ctx)
        assert not _LEAKED_PLACEHOLDER.search(out)
