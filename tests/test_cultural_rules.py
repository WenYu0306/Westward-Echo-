"""Unit tests for cultural_rules.py — genre detection and rule loading."""

import json
import tempfile
from pathlib import Path

import pytest

from src.cultural_rules import (
    detect_genre,
    format_fidelity_for_prompt,
    format_rules_as_bullets,
    format_rules_for_prompt,
    is_known_genre,
    list_known_genres,
    load_fidelity_rules,
    load_rules,
)


@pytest.fixture
def rules_file():
    """Create a minimal cultural_rules.json for testing."""
    data = {
        "genres": {
            "xianxia": {
                "en-US": {
                    "修真": {"target": "cultivation", "note": "Core xianxia concept"},
                    "金丹": {"target": "Golden Core", "note": "Cultivation stage"},
                },
                "es-ES": {
                    "修真": {"target": "cultivación", "note": ""},
                },
            },
            "scifi": {
                "en-US": {
                    "机甲": {"target": "mecha", "note": "Piloted robot"},
                },
            },
        },
        "common": {
            "en-US": {
                "穿越": {"target": "transmigration", "note": "Genre trope"},
            },
        },
    }
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump(data, f)
        path = f.name
    yield path
    Path(path).unlink()


class TestGenreDetection:
    def test_detect_xianxia(self):
        text = "修真世界 金丹 元婴 飞升 渡劫 法器 灵石 宗门 长老 弟子 功法 炼丹"
        genre, score = detect_genre(text)
        assert genre == "xianxia"
        assert score > 0

    def test_detect_scifi(self):
        text = "机甲 联邦 帝国 战舰 星舰 跃迁 基因改造 殖民星 能量罩"
        genre, score = detect_genre(text)
        assert genre == "scifi"
        assert score > 0

    def test_detect_folk_religion(self):
        text = "出马 仙家 弟马 堂口 请神 地府 阎王 判官 鬼差"
        genre, score = detect_genre(text)
        assert genre == "folk_religion"
        assert score > 0

    def test_no_match_returns_empty(self):
        text = "今天天气真好，我去超市买菜。"
        genre, score = detect_genre(text)
        assert genre == ""
        assert score == 0

    def test_ambiguous_returns_empty(self):
        # Mix of scifi and xianxia signals
        text = "机甲 修真 金丹 战舰 飞升 能量罩 渡劫"
        genre, _ = detect_genre(text)
        # Conflicting signals → should not commit to one genre
        assert genre == ""


class TestLoadRules:
    def test_load_genre_rules(self, rules_file):
        rules = load_rules("en-US", "xianxia", path=rules_file)
        assert "修真" in rules
        assert rules["修真"]["target"] == "cultivation"

    def test_common_rules_inherited(self, rules_file):
        rules = load_rules("en-US", "xianxia", path=rules_file)
        assert "穿越" in rules  # from common
        assert rules["穿越"]["target"] == "transmigration"

    def test_genre_overrides_common(self, rules_file):
        # Would need a conflict to test, but our test data doesn't have one
        rules = load_rules("en-US", "scifi", path=rules_file)
        assert "机甲" in rules
        assert rules["机甲"]["target"] == "mecha"

    def test_other_language(self, rules_file):
        rules = load_rules("es-ES", "xianxia", path=rules_file)
        assert "修真" in rules
        assert rules["修真"]["target"] == "cultivación"

    def test_unknown_genre_returns_common_only(self, rules_file):
        rules = load_rules("en-US", "nonexistent_genre", path=rules_file)
        # Only common rules apply
        assert "穿越" in rules
        assert "修真" not in rules  # xianxia-specific


class TestBookLevelRules:
    """``books.<book_id>`` is the innermost rule layer.

    Regression guard for the 无限恐怖 pollution: its 150 machine-extracted
    proper nouns (including Resident Evil IP names) sat in the *shared*
    ``genres.urban`` slot, so the two other urban books were briefed with them.
    They now live under ``books.limitless_horror`` and must reach that book only.
    """

    @pytest.fixture
    def book_rules_file(self, tmp_path):
        data = {
            "common": {"en-US": {"穿越": {"target": "transmigration", "note": ""}}},
            "genres": {"urban": {}},
            "books": {
                "limitless_horror": {
                    "en-US": {"生化危机": {"target": "Biohazard", "note": "IP name"}}
                }
            },
            "fidelity": {},
        }
        p = tmp_path / "book_rules.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(p)

    def test_book_layer_applies_when_book_id_given(self, book_rules_file):
        rules = load_rules("en-US", "urban", path=book_rules_file,
                           book_id="limitless_horror")
        assert "生化危机" in rules
        assert rules["生化危机"]["target"] == "Biohazard"

    def test_book_terms_do_not_leak_to_other_books(self, book_rules_file):
        # The whole point of the refactor: a sibling book sharing the genre
        # must not inherit another book's proper nouns.
        rules = load_rules("en-US", "urban", path=book_rules_file,
                           book_id="quanzhi_fanwai")
        assert "生化危机" not in rules
        assert "穿越" in rules  # common still applies

    def test_omitting_book_id_reproduces_old_behaviour(self, book_rules_file):
        rules = load_rules("en-US", "urban", path=book_rules_file)
        assert "生化危机" not in rules, "book terms must not apply without book_id"
        assert rules == {"穿越": {"target": "transmigration", "note": ""}}

    def test_unknown_book_id_is_harmless(self, book_rules_file):
        rules = load_rules("en-US", "urban", path=book_rules_file,
                           book_id="no_such_book")
        assert "生化危机" not in rules
        assert "穿越" in rules

    def test_book_overrides_genre_for_same_term(self, tmp_path):
        # Precedence: common < genre < book.
        data = {
            "common": {"en-US": {"X": {"target": "common", "note": ""}}},
            "genres": {"urban": {"en-US": {"X": {"target": "genre", "note": ""}}}},
            "books": {"b1": {"en-US": {"X": {"target": "book", "note": ""}}}},
            "fidelity": {},
        }
        p = tmp_path / "precedence.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        assert load_rules("en-US", "urban", path=str(p))["X"]["target"] == "genre"
        assert load_rules("en-US", "urban", path=str(p),
                          book_id="b1")["X"]["target"] == "book"


class TestRealUrbanSlotIsQuarantined:
    """Guards the shipped cultural_rules.json, not a fixture.

    If someone re-adds terms to ``genres.urban`` the three urban books would
    once again share one book's vocabulary. The slot must stay empty.
    """

    def test_urban_genre_slot_is_empty(self):
        data = json.loads(
            Path(__file__).resolve().parent.parent.joinpath("cultural_rules.json")
            .read_text(encoding="utf-8")
        )
        urban = data["genres"]["urban"]
        assert urban == {}, (
            f"genres.urban must stay empty — single-book terms belong under "
            f"'books.<book_id>', not in the slot shared by three urban books. "
            f"Found: {list(urban)}"
        )

    def test_urban_key_still_declared(self):
        # list_known_genres/is_known_genre enumerate genres.*; dropping the key
        # would report urban as unknown and break the genre sync invariant.
        assert is_known_genre("urban")

    def test_limitless_horror_book_slot_holds_the_terms(self):
        data = json.loads(
            Path(__file__).resolve().parent.parent.joinpath("cultural_rules.json")
            .read_text(encoding="utf-8")
        )
        book = data.get("books", {}).get("limitless_horror", {}).get("en-US", {})
        assert len(book) == 150, f"expected the 150 moved terms, got {len(book)}"
        assert "生化危机" in book

    def test_read_node_forwards_book_id_to_load_rules(self, monkeypatch):
        """The refactor is dead unless the READ node actually forwards book_id.

        A source-substring assertion is NOT acceptable here: read.py contains
        ``book_id=state.get("book_id", "")`` in an unrelated
        ``_capture_read_tokens`` call too, so the substring is satisfied even
        when the load_rules wiring is deleted. That exact mutation survived the
        first version of this test. Instead we spy on the call.
        """
        from unittest.mock import MagicMock

        import src.agent.nodes.read as read_mod

        calls = {}

        def spy(**kwargs):
            calls.update(kwargs)
            return {}

        monkeypatch.setattr(read_mod, "load_rules", spy)
        monkeypatch.setattr(read_mod, "ChatOpenAI", lambda **kw: MagicMock())
        resp = MagicMock()
        resp.content = "{}"
        resp.response_metadata = {}
        resp.additional_kwargs = {}
        resp.usage_metadata = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
        monkeypatch.setattr(read_mod, "get_breaker",
                            lambda lang: MagicMock(call=lambda fn, msgs: resp))

        read_mod.read_node(
            {
                "chapter_title": "第1章", "chapter_content": "正文", "chapter_number": 1,
                "book_id": "limitless_horror", "target_lang": "en-US", "genre": "urban",
                "content_type": "novel", "style_memo": "", "previous_chapter_summary": "",
                "exact_matches_text": "", "semantic_matches_text": "", "exact_glossary": {},
                "api_key": "",
            },
            MagicMock(), MagicMock(),
        )

        assert calls.get("book_id") == "limitless_horror", (
            f"read_node did not forward state['book_id'] to load_rules; got {calls}"
        )

    def test_book_terms_reach_the_rendered_prompt(self, monkeypatch):
        """End-to-end: a book's own terms must land in the rendered user prompt.

        Uses the shipped cultural_rules.json, so it also proves the data is
        wired — 生化危机 lives in books.limitless_horror, and the prompt is what
        the model actually sees.
        """
        from unittest.mock import MagicMock

        import src.agent.nodes.read as read_mod

        captured = {}

        class FakeLLM:
            def __init__(self, **kwargs):
                pass

            def invoke(self, messages):
                captured["messages"] = messages
                resp = MagicMock()
                resp.content = "{}"
                resp.response_metadata = {}
                resp.additional_kwargs = {}
                resp.usage_metadata = {"input_tokens": 1, "output_tokens": 1,
                                       "total_tokens": 2}
                return resp

        monkeypatch.setattr(read_mod, "ChatOpenAI", FakeLLM)
        monkeypatch.setattr(read_mod, "get_breaker",
                            lambda lang: MagicMock(call=lambda fn, msgs: fn(msgs)))

        base = {
            "chapter_title": "第1章", "chapter_content": "正文", "chapter_number": 1,
            "target_lang": "en-US", "genre": "urban", "content_type": "novel",
            "style_memo": "", "previous_chapter_summary": "", "exact_matches_text": "",
            "semantic_matches_text": "", "exact_glossary": {}, "api_key": "",
        }
        read_mod.read_node({**base, "book_id": "limitless_horror"},
                           MagicMock(), MagicMock())
        with_book = "\n".join(str(m.content) for m in captured["messages"])

        read_mod.read_node({**base, "book_id": "quanzhi_fanwai"},
                           MagicMock(), MagicMock())
        without = "\n".join(str(m.content) for m in captured["messages"])

        assert "生化危机" in with_book, \
            "limitless_horror's own term did not reach the rendered prompt"
        assert "生化危机" not in without, \
            "a sibling urban book must NOT be briefed with Resident Evil terms"


class TestKnownGenres:
    def test_list_known_genres(self, rules_file):
        genres = list_known_genres(path=rules_file)
        assert "xianxia" in genres
        assert "scifi" in genres

    def test_is_known_genre(self, rules_file):
        assert is_known_genre("xianxia", path=rules_file)
        assert not is_known_genre("nonexistent", path=rules_file)


class TestFormatRules:
    def test_format_rules_for_prompt_empty(self):
        assert format_rules_for_prompt({}) == ""

    def test_format_rules_for_prompt(self):
        rules = {"霸总": {"target": "Alpha CEO", "note": "Romance archetype"}}
        text = format_rules_for_prompt(rules)
        assert "霸总" in text
        assert "Alpha CEO" in text
        assert "Romance archetype" in text

    def test_format_rules_as_bullets_empty(self):
        assert format_rules_as_bullets({}) == ""

    def test_format_rules_as_bullets(self):
        rules = {"穿越": {"target": "transmigration", "note": "Isekai-like"}}
        text = format_rules_as_bullets(rules)
        assert "穿越" in text
        assert "transmigration" in text
        assert "Isekai-like" in text


class TestFormatRulesWithoutTranslation:
    """Entries with an empty ``target``: de/fr source-term lists.

    ``cultural_rules.json`` may mark a term as load-bearing without prescribing
    a rendering, because no vetted one exists for that language. The formatters
    must say so explicitly — an empty table cell under an "Adapted (USE THIS)"
    header reads as "use the empty string", and ``→ ""`` reads as a translation
    that happens to be blank. Both would push the model toward literalism.

    Exact-string assertions on purpose: the tests above only check substring
    membership, so they cannot catch a rendering regression in these branches.
    """

    EMPTY = {"金丹": {"target": "", "note": "cultivation stage"}}
    FULL = {"霸总": {"target": "Alpha CEO", "note": "romance"}}

    def test_table_drops_use_this_when_any_target_empty(self):
        assert format_rules_for_prompt(self.EMPTY) == (
            "| 中文 | Adapted | Note |\n"
            "|------|---------|------|\n"
            "| 金丹 | (no fixed term — adapt, do not translate literally)"
            " | cultivation stage |"
        )

    def test_table_keeps_use_this_when_all_targets_present(self):
        # Byte-identical to the pre-de/fr renderer: the header change must be
        # driven by content, not applied unconditionally.
        assert format_rules_for_prompt(self.FULL) == (
            "| 中文 | Adapted (USE THIS) | Note |\n"
            "|------|--------------------|------|\n"
            "| 霸总 | Alpha CEO | romance |"
        )

    def test_table_mixed_entries(self):
        # One entry without a translation is enough to make "USE THIS" false
        # for the whole table, so the header must switch.
        rules = {**self.FULL, **self.EMPTY}
        text = format_rules_for_prompt(rules)
        assert text.splitlines()[0] == "| 中文 | Adapted | Note |"
        assert "| 霸总 | Alpha CEO | romance |" in text
        assert "(no fixed term — adapt, do not translate literally)" in text

    def test_bullets_render_empty_target_without_quotes(self):
        assert format_rules_as_bullets(self.EMPTY) == (
            "- 金丹 → (no fixed term — adapt, do not translate literally)"
            " (cultivation stage)"
        )

    def test_bullets_render_full_target_with_quotes(self):
        assert format_rules_as_bullets(self.FULL) == '- 霸总 → "Alpha CEO" (romance)'

    def test_missing_target_key_treated_as_empty(self):
        # load_rules merges raw JSON, so an entry could omit "target" outright.
        assert format_rules_as_bullets({"金丹": {"note": "stage"}}).startswith(
            "- 金丹 → (no fixed term"
        )

    def test_bare_string_entry_still_renders_as_translation(self):
        # Legacy shape: value is the translation itself, not a dict.
        assert format_rules_as_bullets({"金丹": "Golden Core"}) == (
            '- 金丹 → "Golden Core"'
        )

    def test_no_target_marker_is_not_a_translation(self):
        # Guards the whole point of these entries: the marker must instruct,
        # never look like a term the model could copy into the glossary.
        from src.cultural_rules import _NO_TARGET

        assert _NO_TARGET.startswith("(no fixed term")
        assert "—" in _NO_TARGET, "marker should read as prose, not as a term"


@pytest.fixture
def fidelity_file():
    """Create a minimal cultural_rules.json with a fidelity partition."""
    data = {
        "fidelity": {
            "en-US": {
                "character_names": {
                    "rule": "Meaningful names must be translated, plain names kept in pinyin.",
                    "examples": [
                        {"cn": "聋婆婆", "do": "Deaf Granny", "why": "pun"},
                        {"cn": "华九难", "do": "Hua Jiunan", "why": "plain"},
                    ],
                },
                "terms_of_address": {
                    "rule": "Convey relationship + status + closeness.",
                    "examples": [{"cn": "白月光", "do": "unattainable first love", "why": ""}],
                },
            },
        },
    }
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump(data, f)
        path = f.name
    yield path
    Path(path).unlink()


class TestFidelityRules:
    def test_load_fidelity_rules(self, fidelity_file):
        rules = load_fidelity_rules("en-US", path=fidelity_file)
        assert "character_names" in rules
        assert "terms_of_address" in rules
        assert rules["character_names"]["rule"]
        assert len(rules["character_names"]["examples"]) == 2

    def test_load_fidelity_unknown_lang(self, fidelity_file):
        rules = load_fidelity_rules("es-ES", path=fidelity_file)
        assert rules == {}

    def test_load_fidelity_from_real_file(self):
        # The real cultural_rules.json has the full 8-category fidelity set.
        rules = load_fidelity_rules("en-US")
        assert "character_names" in rules
        assert "terms_of_address" in rules
        assert "worldview_terms" in rules
        assert "implicit_values" in rules
        assert "wordplay" in rules

    def test_format_fidelity_empty(self):
        assert format_fidelity_for_prompt({}) == ""

    def test_format_fidelity_for_prompt(self, fidelity_file):
        rules = load_fidelity_rules("en-US", path=fidelity_file)
        text = format_fidelity_for_prompt(rules)
        assert "Character Names" in text
        assert "Meaningful names must be translated" in text
        assert "聋婆婆" in text
        assert "Deaf Granny" in text


class TestGenreConfidenceRatio:
    """The runner-up ratio guard — reached only when TWO genres both score.

    (The pre-existing "ambiguous" test uses a sample where neither genre
    reaches the signal floor, so it never exercises this logic.)
    """

    SCIFI_6 = "机甲 联邦 帝国 战舰 星舰 太空港"
    XIANXIA_5 = "修真 修仙 金丹 元婴 飞升"

    def test_two_genres_within_two_to_one_is_ambiguous(self):
        genre, score = detect_genre(f"{self.SCIFI_6} {self.XIANXIA_5}")
        assert (genre, score) == ("", 0)

    def test_ratio_of_exactly_two_to_one_is_accepted(self):
        # 12 scifi vs 5 xianxia = 2.4; the guard is "< 2.0", so this commits.
        text = f"{self.SCIFI_6} {self.SCIFI_6} {self.XIANXIA_5}"
        genre, score = detect_genre(text)
        assert genre == "scifi"
        assert score == 12

    def test_clear_winner_returns_the_highest_scoring_genre(self):
        # A reversed sort would pick xianxia (the lower score) here.
        text = f"{self.SCIFI_6} {self.SCIFI_6} {self.XIANXIA_5}"
        assert detect_genre(text)[0] == "scifi"

    def test_single_scored_genre_always_commits(self):
        genre, score = detect_genre(self.SCIFI_6)
        assert (genre, score) == ("scifi", 6)


class TestGenreSignalFloor:
    def test_exactly_five_signals_is_a_match(self):
        text = "修真 修仙 金丹 元婴 飞升"
        assert detect_genre(text) == ("xianxia", 5)

    def test_four_signals_is_not_a_match(self):
        text = "修真 修仙 金丹 元婴"
        assert detect_genre(text) == ("", 0)

    def test_no_match_returns_the_empty_tuple(self):
        result = detect_genre("今天天气真好，我去超市买菜。")
        assert result == ("", 0)
        assert isinstance(result, tuple)


class TestDefaultRulesPath:
    def test_env_override_is_honoured(self, monkeypatch):
        from src.cultural_rules import _default_rules_path

        monkeypatch.setenv("CULTURAL_RULES_PATH", "/tmp/custom_rules.json")
        assert _default_rules_path() == Path("/tmp/custom_rules.json")

    def test_falls_back_to_the_repo_file(self, monkeypatch):
        from src.cultural_rules import _default_rules_path

        monkeypatch.delenv("CULTURAL_RULES_PATH", raising=False)
        path = _default_rules_path()
        assert isinstance(path, Path)
        assert path.name == "cultural_rules.json"


class TestFidelityExamples:
    def test_only_the_first_example_is_rendered(self):
        rules = {
            "honorifics": {
                "rule": "Keep honorifics meaningful.",
                "examples": [
                    {"cn": "聋婆婆", "do": "Deaf Granny", "why": "meaningful name"},
                    {"cn": "王三", "do": "Wang San", "why": "plain name"},
                ],
            }
        }
        out = format_fidelity_for_prompt(rules)
        assert "聋婆婆" in out
        assert "Deaf Granny" in out
        assert "王三" not in out

    def test_example_why_clause_is_included(self):
        rules = {"honorifics": {"rule": "R", "examples": [
            {"cn": "聋婆婆", "do": "Deaf Granny", "why": "meaningful name"}]}}
        assert "(meaningful name)" in format_fidelity_for_prompt(rules)

    def test_entry_without_examples_still_renders_the_rule(self):
        rules = {"honorifics": {"rule": "Keep honorifics meaningful."}}
        out = format_fidelity_for_prompt(rules)
        assert "Keep honorifics meaningful." in out
