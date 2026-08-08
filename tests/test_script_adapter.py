"""Tests for script_adapter.py — the Forge Echo → Westward Echo seam."""

import os

from src.chapter_splitter import ParagraphTag
from src.script_adapter import convert_forge_script, extract_forge_metadata
from src.script_splitter import split_episodes

# Representative Forge Echo sample mirroring jiyi_dianhang's real format
FORGE_SAMPLE = """# jiyi_dianhang

**题材**：科幻
**前提**：近未来记忆典当行里，一名记忆鉴定师在匿名典当的记忆中，目睹了一场谋杀。
**集数**：2集
**每集时长**：≥55秒

## 角色

### 陆辞安（protagonist）
- 核心矛盾：每天鉴定他人记忆真伪，却无法相信自己脑中缺失的那一块。

---

## 第1集：记忆里的死者

- **时长目标**：55秒
- **情绪基调**：冷峻压抑
- **钩子类型**：悬念

### 场景：记忆典当行·鉴定室（深夜）
氛围：逼仄昏暗，仅鉴定台上方一盏冷白聚光灯照亮工作区
在场：陆辞安

**[S01] 特写** （3秒）
画面：一只戴着黑色防静电手套的手，指尖捏着一枚泛着微蓝荧光的记忆芯片
动作：手指停顿在半空，微微颤抖后恢复稳定

> **陆辞安**（平静下藏着烦躁）[手指轻抚芯片边缘的划痕]：MC-2049-11-17…第三次了。

**[S02] 中景** （2秒）
画面：记忆芯片滑入鉴定舱接口，舱内蓝色指示灯依次亮起

> **系统**（机械、无起伏）[指示灯频率加快]：鉴定序列启动。

**【集末钩子】** 鉴定室的屏幕倒计时仍在跳动。陆辞安站在黑暗中。

---

## 第2集：妹妹的残影

- **时长目标**：55秒
- **情绪基调**：阴郁而不安
- **钩子类型**：情感

### 场景：陆辞安公寓玄关（深夜）
氛围：逼仄的走廊里只有安全出口指示灯的青绿光
在场：陆辞安

**[S01] 中景** （8秒）
画面：玄关墙面整面被发黄的便签纸覆盖

> **陆辞安**（陈述事实，空洞）[盯住便签]：我不记得。

> **陆辞安**（低沉自嘲）[触碰便签]：写在这里的每一个字，都只是我一个人的偏执。
"""


class TestEpisodeConversion:
    def test_episode_header(self):
        out = convert_forge_script(FORGE_SAMPLE)
        assert "第1集 记忆里的死者" in out
        assert "第2集 妹妹的残影" in out

    def test_setup_block_dropped(self):
        out = convert_forge_script(FORGE_SAMPLE)
        assert "题材" not in out.split("第1集")[0]
        assert "核心矛盾" not in out

    def test_metadata_bullets_dropped(self):
        out = convert_forge_script(FORGE_SAMPLE)
        assert "时长目标" not in out
        assert "情绪基调" not in out
        assert "钩子类型" not in out


class TestSceneConversion:
    def test_scene_header_with_time(self):
        out = convert_forge_script(FORGE_SAMPLE)
        assert "场景1：记忆典当行·鉴定室/深夜" in out
        assert "场景1：陆辞安公寓玄关/深夜" in out  # resets per episode

    def test_atmosphere_kept_as_prose(self):
        out = convert_forge_script(FORGE_SAMPLE)
        assert "逼仄昏暗" in out


class TestDialogueConversion:
    def test_speaker_emotion_kept_action_dropped(self):
        out = convert_forge_script(FORGE_SAMPLE)
        assert "陆辞安（平静下藏着烦躁）：MC-2049-11-17…第三次了。" in out
        # action bracket content must not leak into the line
        assert "手指轻抚芯片" not in out

    def test_shot_headers_dropped(self):
        out = convert_forge_script(FORGE_SAMPLE)
        assert "[S01]" not in out
        assert "特写" not in out or "特写" in out.split("画面")[0] is False

    def test_visual_lines_kept_as_prose(self):
        out = convert_forge_script(FORGE_SAMPLE)
        assert "一只戴着黑色防静电手套的手" in out

    def test_hook_preserved(self):
        out = convert_forge_script(FORGE_SAMPLE)
        assert "【集末钩子】鉴定室的屏幕倒计时仍在跳动" in out


class TestFailSafe:
    def test_non_forge_text_returned_unchanged(self):
        plain = "第1集 普通剧本\n\n场景1：地点/时间\n\n陆辞安：台词。\n"
        assert convert_forge_script(plain) == plain

    def test_empty_string(self):
        assert convert_forge_script("") == ""


class TestMetadataExtraction:
    def test_extract_all_fields(self):
        meta = extract_forge_metadata(FORGE_SAMPLE)
        assert meta["title"] == "jiyi_dianhang"
        assert meta["genre"] == "科幻"
        assert "记忆典当行" in meta["premise"]
        assert meta["episode_count"] == "2集"

    def test_missing_fields_empty(self):
        meta = extract_forge_metadata("# only_title\n")
        assert meta["title"] == "only_title"
        assert meta["genre"] == ""


class TestEndToEndSplit:
    """Converted output must be ingestible by Westward Echo's splitter."""

    def test_sample_splits_two_episodes(self):
        converted = convert_forge_script(FORGE_SAMPLE)
        episodes = split_episodes(converted)
        episodes = [e for e in episodes if e.action != ParagraphTag.SKIP]
        assert len(episodes) == 2
        assert episodes[0].title == "第1集 记忆里的死者"
        assert episodes[1].scene_count == 1

    def test_real_file_splits_65_episodes(self):
        """If the real Forge Echo export exists, verify full conversion."""
        real = (
            "/Users/wenyudemac/Documents/dev/Forge Echo（铸文）"
            "/projects/jiyi_dianhang/export/jiyi_dianhang_剧本.md"
        )
        if not os.path.exists(real):
            import pytest
            pytest.skip("real Forge Echo export not present")
        with open(real, encoding="utf-8") as f:
            converted = convert_forge_script(f.read())
        episodes = split_episodes(converted)
        episodes = [e for e in episodes if e.action != ParagraphTag.SKIP]
        assert len(episodes) == 65
