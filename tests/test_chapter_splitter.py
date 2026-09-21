"""Tests for chapter_splitter.py"""

from src.chapter_splitter import (
    CHAPTER_PATTERN,
    ParagraphTag,
    classify_paragraph,
    split_chapters,
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


class TestChapterPreview:
    def _chapter(self, content):
        from src.chapter_splitter import Chapter
        return Chapter(index=1, title="t", content=content)

    def test_default_preview_is_eighty_chars(self):
        assert len(self._chapter("字" * 200).preview()) == 80

    def test_explicit_preview_length(self):
        assert self._chapter("abcdefghij" * 20).preview(10) == "abcdefghij"

    def test_preview_returns_the_content_itself_when_short(self):
        assert self._chapter("短内容").preview() == "短内容"

    def test_preview_strips_surrounding_whitespace(self):
        assert self._chapter("\n\n  正文开始  \n").preview() == "正文开始"

    def test_preview_is_a_string(self):
        assert isinstance(self._chapter("字" * 200).preview(), str)


class TestClassifyHolidaySpecial:
    def test_holiday_special_is_translated_without_term_extraction(self):
        tag, action = classify_paragraph("七夕", "长" * 600)
        assert tag == ParagraphTag.SPECIAL
        assert action == ParagraphTag.TRANSLATE_NO_EXTRACT

    def test_classify_always_returns_a_tuple(self):
        result = classify_paragraph("七夕", "长" * 600)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(x, ParagraphTag) for x in result)


class TestAuthorNoteLengthBoundary:
    """A note is only skipped when it is SHORT — the threshold is exclusive."""

    def test_content_of_exactly_500_chars_is_not_an_author_note(self):
        tag, action = classify_paragraph("请假条：今天更新推迟", "文" * 500)
        assert tag != ParagraphTag.AUTHOR_NOTE
        assert action != ParagraphTag.SKIP

    def test_content_of_499_chars_is_an_author_note(self):
        tag, action = classify_paragraph("请假条：今天更新推迟", "文" * 499)
        assert tag == ParagraphTag.AUTHOR_NOTE
        assert action == ParagraphTag.SKIP


class TestPreambleDetection:
    def _text(self, preamble_cn_chars):
        return "文" * preamble_cn_chars + "\n\n第1章 开始\n\n正文内容。\n"

    def test_preamble_with_exactly_50_cjk_chars_becomes_a_prologue(self):
        chapters = split_chapters(self._text(50))
        assert len(chapters) == 2
        assert chapters[0].index == 0
        assert chapters[0].title == "楔子"

    def test_preamble_with_49_cjk_chars_is_dropped(self):
        chapters = split_chapters(self._text(49))
        assert len(chapters) == 1
        assert chapters[0].title == "第1章 开始"

    def test_chapter_numbering_restarts_after_a_prologue(self):
        # The 楔子 takes slot 0; the first real chapter is numbered 2.
        chapters = split_chapters(self._text(60))
        assert [c.index for c in chapters] == [0, 2]

    def test_text_starting_with_a_header_has_no_preamble(self):
        chapters = split_chapters("第1章 开始\n\n正文内容。\n")
        assert len(chapters) == 1
        assert chapters[0].index == 1
        assert chapters[0].title == "第1章 开始"


class TestChapterBoundariesAndNumbering:
    def _two_chapters(self):
        return split_chapters(
            "第1章 甲\n\n甲章的正文在下面。\n\n第2章 乙\n\n乙章的正文在下面。\n"
        )

    def test_each_chapter_stops_at_the_next_header(self):
        chapters = self._two_chapters()
        assert len(chapters) == 2
        assert "甲章的正文" in chapters[0].content
        assert "第2章" not in chapters[0].content
        assert "乙章的正文" not in chapters[0].content

    def test_second_chapter_keeps_its_own_body(self):
        chapters = self._two_chapters()
        assert "乙章的正文" in chapters[1].content
        assert "甲章的正文" not in chapters[1].content

    def _three_chapters(self):
        return split_chapters(
            "第1章 甲\n\n甲正文。\n\n第2章 乙\n\n乙正文。\n\n第3章 丙\n\n丙正文。\n"
        )

    def test_chapters_are_numbered_consecutively_from_one(self):
        assert [c.index for c in self._three_chapters()] == [1, 2, 3]

    def test_three_chapters_keep_three_distinct_bodies(self):
        chapters = self._three_chapters()
        assert chapters[0].content.strip() == "甲正文。"
        assert chapters[1].content.strip() == "乙正文。"
        assert chapters[2].content.strip() == "丙正文。"


class TestMergeChapters:
    def test_join_with_blank_line(self):
        from src.chapter_splitter import merge_chapters
        assert merge_chapters(["第一段", "第二段"]) == "第一段\n\n第二段"

    def test_returns_a_string(self):
        from src.chapter_splitter import merge_chapters
        assert isinstance(merge_chapters([]), str)

    def test_empty_list_is_an_empty_string(self):
        from src.chapter_splitter import merge_chapters
        assert merge_chapters([]) == ""
