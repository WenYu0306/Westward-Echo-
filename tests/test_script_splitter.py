"""Tests for script_splitter.py"""

from src.chapter_splitter import ParagraphTag
from src.script_splitter import (
    EPISODE_PATTERN,
    SCENE_PATTERN,
    EpisodeTag,
    classify_episode,
    extract_dialogue,
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


# Sample shaped like real pipeline output (see pilots/output/pei_zong_script)
_SAMPLE_EPISODE = """Episode 1: Transmigrated

Scene 1: PEI PENTHOUSE - MASTER BEDROOM / MORNING

Su Nian's eyes snap open. She sits up in a bed so vast she looks tiny in it.

SU NIAN (OS): This bed is bigger than my entire rental apartment.

A smart speaker chimes in with a gentle female voice.

SMART SPEAKER (V.O.): Pei Group's stock rose 2.3% today.

【Ding — You are now bound to the CEO Capture System.】
【Current Affection: -50】

SU NIAN: Impossible. Absolutely impossible.

She throws back the sheets. Her eyes go cold.

Scene 2: PEI GROUP - LOBBY / DAY

SU NIAN: I'm not afraid of you anymore.
"""


class TestExtractDialogue:
    """Dialogue-only deliverable for the script branch (dubbing/ADR)."""

    def test_keeps_dialogue_and_os(self):
        out = extract_dialogue(_SAMPLE_EPISODE)
        assert "SU NIAN: Impossible. Absolutely impossible." in out
        assert "SU NIAN (OS): This bed is bigger" in out
        assert "SMART SPEAKER (V.O.):" in out
        assert "I'm not afraid of you anymore." in out

    def test_keeps_structural_headers(self):
        out = extract_dialogue(_SAMPLE_EPISODE)
        assert "Episode 1: Transmigrated" in out
        assert "Scene 1: PEI PENTHOUSE - MASTER BEDROOM / MORNING" in out
        assert "Scene 2: PEI GROUP - LOBBY / DAY" in out

    def test_drops_action_lines(self):
        out = extract_dialogue(_SAMPLE_EPISODE)
        assert "eyes snap open" not in out
        assert "smart speaker chimes" not in out
        assert "throws back the sheets" not in out

    def test_drops_panels(self):
        out = extract_dialogue(_SAMPLE_EPISODE)
        assert "【" not in out
        assert "Affection" not in out

    def test_scene_header_not_dialogue(self):
        # "Scene 1:" matches the label-colon shape but is not a speaker.
        out = extract_dialogue("Episode 1: T\n\nScene 1: LOBBY / DAY\n\nSU NIAN: Hi.\n")
        lines = [line for line in out.split("\n") if line.strip()]
        assert sum(1 for line in lines if line.startswith("Scene")) == 1
        assert "SU NIAN: Hi." in out

    def test_fallback_when_no_dialogue_found(self):
        broken = "Some prose with no speaker lines at all.\n\nMore prose."
        assert extract_dialogue(broken) == broken

    def test_empty_input(self):
        assert extract_dialogue("") == ""

    def test_continuation_of_wrapped_dialogue(self):
        text = "SU NIAN: First part of the line\nsecond part of the line\n\nShe turns away.\n"
        out = extract_dialogue(text)
        assert "second part of the line" in out
        assert "She turns away." not in out

    # --- Shapes observed in live LLM output ---

    def test_stacked_cue_shape(self):
        # Hollywood convention: speaker cue alone, parenthetical note,
        # then the line below — no colon anywhere.
        text = (
            "Scene 1: LIN CORP - BOARDROOM / DAY\n\n"
            "Lin Zhao slides the document across the table.\n\n"
            "LIN ZHAO\n(Cold, flat)\nWe're done. The engagement's off.\n\n"
            "He reaches into his jacket and pulls out a jade box.\n\n"
            "SU WAN\nKeep it.\n\n"
            "FADE OUT.\n"
        )
        out = extract_dialogue(text)
        assert "LIN ZHAO" in out
        assert "(Cold, flat)" in out            # acting note kept
        assert "We're done. The engagement's off." in out
        assert "SU WAN" in out
        assert "Keep it." in out
        assert "slides the document" not in out  # action dropped
        assert "jade box" not in out
        assert "FADE OUT." not in out            # transition dropped

    def test_colon_dialogue_below_shape(self):
        # Colon after the cue, dialogue on the following line.
        text = (
            "SU WAN (OS):\nThree years of being the joke. Tonight, that ends.\n\n"
            "The golden light intensifies around her.\n"
        )
        out = extract_dialogue(text)
        assert "SU WAN (OS):" in out
        assert "Three years of being the joke." in out
        assert "golden light" not in out

    def test_stacked_uppercase_action_not_cue(self):
        # Mixed-case action sentences must never be mistaken for cues.
        text = "She freezes. Something is wrong.\n\nSU WAN: Run.\n"
        out = extract_dialogue(text)
        assert "She freezes." not in out
        assert "SU WAN: Run." in out
