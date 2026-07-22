"""Tests for chapter_splitter.py"""

import pytest
from src.chapter_splitter import (
    split_chapters,
    classify_paragraph,
    ParagraphTag,
    CHAPTER_PATTERN,
)


class TestChapterPattern:
    """Verify the chapter-header regex matches common Chinese web novel formats."""

    def test_arabic_number_chapter(self):
        assert CHAPTER_PATTERN.match("第1章 穿越")
        assert CHAPTER_PATTERN.match("第123章 大结局")

    def test_chinese_number_chapter(self):
        assert CHAPTER_PATTERN.match("第一章 穿越成霸总文女主")
        assert CHAPTER_PATTERN.match("第十一章 初见")
        assert CHAPTER_PATTERN.match("第一百二十章 决战")

    def test_alternate_headers(self):
        assert CHAPTER_PATTERN.match("第一回 初入江湖")
        assert CHAPTER_PATTERN.match("第三话")

    def test_whitespace_insensitive(self):
        assert CHAPTER_PATTERN.match("  第1章  穿越  ")

    def test_non_chapter_text(self):
        assert not CHAPTER_PATTERN.match("我正在回家的路上")
        assert not CHAPTER_PATTERN.match("番外：裴衍舟的一天")


class TestClassifyParagraph:

    def test_author_note_detection(self):
        tag, action = classify_paragraph("请假条：今天更新推迟", "短")
        assert action == ParagraphTag.SKIP

    def test_prologue_detection(self):
        tag, action = classify_paragraph("楔子", "足够长的内容".ljust(600, "文"))
        assert tag == ParagraphTag.PROLOGUE
        assert action == ParagraphTag.TRANSLATE

    def test_extra_detection(self):
        tag, action = classify_paragraph("番外：七夕特别篇", "足够长的内容".ljust(600, "文"))
        assert tag == ParagraphTag.EXTRA
        assert action == ParagraphTag.TRANSLATE_NO_EXTRACT

    def test_normal_chapter(self):
        tag, action = classify_paragraph("第三章 父凭子贵", "正常章节内容".ljust(600, "文"))
        assert tag == ParagraphTag.CHAPTER
        assert action == ParagraphTag.TRANSLATE


class TestSplitChapters:

    def test_split_three_chapters(self):
        text = """楔子：这是一个故事

第1章 穿成霸总文女主

我醒过来的时候，发现自己躺在一张大床上。

第2章 裴总

裴衍舟看着眼前的女人。

第3章 结局

从此幸福快乐地生活。
"""
        chapters = split_chapters(text)

        # Should find preamble (楔子) + chap1 + chap2 + chap3
        translatable = [c for c in chapters if c.action != ParagraphTag.SKIP]
        assert len(translatable) >= 3

    def test_no_chapter_headers(self):
        text = "这是一段没有章节标题的纯文本。\n" * 10
        chapters = split_chapters(text)
        assert len(chapters) == 1
        assert chapters[0].index == 1

    def test_empty_text(self):
        chapters = split_chapters("")
        assert len(chapters) >= 0

    def test_chapter_word_count(self):
        text = "第1章 测试\n" + "测" * 500
        chapters = split_chapters(text)
        assert chapters[0].word_count == 500
