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
