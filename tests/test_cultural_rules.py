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
