"""Tests for script_splitter.py"""

from src.chapter_splitter import ParagraphTag
from src.script_splitter import (
    Episode,
    EpisodeTag,
    EPISODE_PATTERN,
    Scene,
    SCENE_PATTERN,
    classify_episode,
    merge_episodes,
    parse_scenes,
    split_episodes,
)


class TestEpisodePattern:
    """Verify the episode-header regex matches common short-drama formats."""

    def test_arabic_number_episode(self):
        assert EPISODE_PATTERN.match("第1集")
        assert EPISODE_PATTERN.match("第12集 净身出户")

    def test_chinese_number_episode(self):
        assert EPISODE_PATTERN.match("第三集")
        assert EPISODE_PATTERN.match("第十一集 反转")

    def test_whitespace_insensitive(self):
        assert EPISODE_PATTERN.match("  第3集  钩子  ")

    def test_non_episode_text(self):
        assert not EPISODE_PATTERN.match("苏念走进了会议室")
        assert not EPISODE_PATTERN.match("场景1：裴家别墅")


class TestScenePattern:

    def test_scene_header_with_colon(self):
        assert SCENE_PATTERN.match("场景1：裴家别墅-主卧/夜")
        assert SCENE_PATTERN.match("场景三：走廊 / 傍晚")

    def test_markdown_prefixed_scene(self):
        assert SCENE_PATTERN.match("##场景1：酒店包厢外走廊/傍晚")

    def test_non_scene_text(self):
        assert not SCENE_PATTERN.match("苏念（内心OS）：我穿书了？")
        assert not SCENE_PATTERN.match("【系统提示：好感度+10】")


class TestClassifyEpisode:

    def test_normal_episode(self):
        tag, action = classify_episode("第1集 穿书")
        assert tag == EpisodeTag.EPISODE
        assert action == ParagraphTag.TRANSLATE

    def test_extra_episode(self):
        tag, action = classify_episode("番外：裴衍舟的一天")
        assert tag == EpisodeTag.EXTRA
        assert action == ParagraphTag.TRANSLATE_NO_EXTRACT


class TestSplitEpisodes:

    def test_split_three_episodes(self):
        text = """第1集 穿成霸总文女主

场景1：裴家别墅-主卧/夜

苏念醒来，发现自己躺在陌生的大床上。

第2集 裴总的契约

场景1：裴家客厅/清晨

裴衍舟递出一份契约。

第3集 拒绝

场景1：裴家书房/傍晚

苏念把契约推了回去。
"""
        episodes = split_episodes(text)
        assert len(episodes) == 3
        assert episodes[0].index == 1
        assert episodes[2].title == "第3集 拒绝"

    def test_no_episode_headers(self):
        text = "苏念走进了会议室。\n" * 10
        episodes = split_episodes(text)
        assert len(episodes) == 1
        assert episodes[0].index == 1

    def test_empty_episode_skipped(self):
        text = "第1集\n\n第2集\n\n有内容的集。\n"
        episodes = split_episodes(text)
        assert len(episodes) == 1
        assert episodes[0].title == "第2集"

    def test_episode_word_count(self):
        text = "第1集 测试\n" + "剧" * 500
        episodes = split_episodes(text)
        assert episodes[0].word_count == 500

    def test_scene_count_property(self):
        text = "第1集\n\n场景1：地点/时间\n\n内容\n\n场景2：地点/时间\n\n内容\n"
        episodes = split_episodes(text)
        assert episodes[0].scene_count == 2


class TestParseScenes:

    def test_parse_two_scenes(self):
        content = """场景1：裴家别墅-主卧/夜

苏念醒来。

场景2：裴家客厅/清晨

裴衍舟递出契约。"""
        scenes = parse_scenes(content)
        assert len(scenes) == 2
        assert scenes[0].header == "场景1：裴家别墅-主卧/夜"
        assert "裴衍舟递出契约" in scenes[1].content

    def test_no_scene_headers(self):
        content = "苏念走进了会议室。\n苏念坐下来。"
        scenes = parse_scenes(content)
        assert len(scenes) == 1
        assert scenes[0].header == ""

    def test_preamble_before_first_scene(self):
        content = "冷开场：一段没有场景头的文字。\n\n场景1：地点/时间\n\n正文。"
        scenes = parse_scenes(content)
        assert len(scenes) == 2
        assert scenes[0].index == 0
        assert scenes[0].header == ""

    def test_scene_header_not_split_by_dialogue(self):
        # Dialogue mentioning 场景 must not be treated as a header
        content = "场景1：会议室/白天\n\n苏念：这个场景我见过。\n"
        scenes = parse_scenes(content)
        assert len(scenes) == 1


class TestMergeEpisodes:

    def test_merge(self):
        assert merge_episodes(["Ep one.", "Ep two."]) == "Ep one.\n\nEp two."
