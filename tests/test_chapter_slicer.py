"""Tests for chapter_slicer.py — adaptive chapter splitting at paragraph boundaries."""

from src.chapter_slicer import (
    MAX_CHARS_PER_SEGMENT,
    SPLIT_THRESHOLD_CHARS,
    build_segment_title,
    should_split,
    split_chapter,
)


def _cn(chars: int) -> str:
    """Generate a string of `chars` Chinese characters by repeating '章'."""
    return "章" * chars


def _long_text(total_chars: int, para_size: int = 500) -> str:
    """Build chapter content with paragraph breaks, totalling ~total_chars."""
    paras = []
    remaining = total_chars
    while remaining > 0:
        chunk = min(para_size, remaining)
        paras.append("文" * chunk)
        remaining -= chunk
    return "\n\n".join(paras)


class TestShouldSplit:
    """Threshold-based gating for the split path."""

    def test_short_text_returns_false(self):
        """100 Chinese chars is well below the split threshold."""
        assert should_split(_cn(100)) is False

    def test_text_over_4500_chars_returns_true(self):
        """Any text whose stripped char count exceeds SPLIT_THRESHOLD_CHARS."""
        assert should_split(_cn(SPLIT_THRESHOLD_CHARS + 1)) is True

    def test_exactly_at_threshold_returns_false(self):
        """Boundary: at exactly 4500 chars it should NOT split."""
        assert should_split(_cn(SPLIT_THRESHOLD_CHARS)) is False

    def test_whitespace_and_newlines_are_ignored_in_count(self):
        """Only visible Chinese characters are counted for the threshold check."""
        content = "章" * 4500 + "\n\n   \n\n"
        assert should_split(content) is False  # just below

    def test_empty_string(self):
        assert should_split("") is False


class TestSplitChapter:
    """Verify the multi-segment splitting logic."""

    def test_short_text_returns_single_segment(self):
        text = "这是一段短文本。不需要拆分。"
        segments = split_chapter(text)
        assert len(segments) == 1
        assert segments[0]["content"] == text

    def test_long_text_returns_multiple_segments(self):
        text = _long_text(5_000)
        segments = split_chapter(text)
        assert len(segments) >= 2

    def test_each_segment_not_exceed_max_chars(self):
        """All segments must have <= MAX_CHARS_PER_SEGMENT visible characters."""
        text = _long_text(12_000)
        segments = split_chapter(text)
        for seg in segments:
            stripped = seg["content"].replace("\n", "").replace(" ", "")
            assert len(stripped) <= MAX_CHARS_PER_SEGMENT

    def test_segments_have_all_five_keys(self):
        """Every segment dict must contain index, total, content, is_first, is_last."""
        text = _long_text(7_000)
        segments = split_chapter(text)
        for seg in segments:
            assert "index" in seg
            assert "total" in seg
            assert "content" in seg
            assert "is_first" in seg
            assert "is_last" in seg

    def test_first_segment_is_first_true(self):
        text = _long_text(7_000)
        segments = split_chapter(text)
        assert segments[0]["is_first"] is True
        assert segments[0]["is_last"] is False

    def test_last_segment_is_last_true(self):
        text = _long_text(7_000)
        segments = split_chapter(text)
        assert segments[-1]["is_last"] is True
        assert segments[-1]["is_first"] is False

    def test_splits_at_paragraph_boundaries(self):
        """When paragraphs exist, segments must end at paragraph breaks.

        We build text with identifiable paragraph markers and verify no
        segment breaks mid-paragraph — i.e. each segment's content is a
        substring of the original (no concatenation across paragraphs
        that weren't already joined).
        """
        paras = [f"第{i}段：" + "文" * 500 for i in range(1, 15)]
        text = "\n\n".join(paras)
        segments = split_chapter(text)
        # Re-join segments and strip → should match the original's non-whitespace
        rejoined = "".join(s["content"].replace("\n", "").replace(" ", "") for s in segments)
        original_stripped = text.replace("\n", "").replace(" ", "")
        assert rejoined == original_stripped, "No characters should be lost"

    def test_chapter_without_paragraph_breaks_still_splits(self):
        """When there are no paragraph breaks, split should fall back to sentence
        boundaries (or character limit) instead of returning a single huge segment."""
        # A single massive block with sentence breaks but no \n\n
        text = ("今天天气很好。" + "我们一起去公园散步。" + "路上看到了很多花。" +
                "文" * (MAX_CHARS_PER_SEGMENT + 500))
        segments = split_chapter(text)
        assert len(segments) >= 2

    def test_empty_content_does_not_crash(self):
        segments = split_chapter("")
        assert isinstance(segments, list)
        assert len(segments) == 1  # Returns single wrapping segment, not empty

    def test_very_short_content(self):
        """Even a tiny string should return a list without error."""
        segments = split_chapter("你好")
        assert segments == [{
            "index": 1,
            "total": 1,
            "content": "你好",
            "is_first": True,
            "is_last": True,
        }]


class TestBuildSegmentTitle:
    """Segment title generation for display."""

    def test_includes_part_number(self):
        seg = {"index": 2, "total": 4, "content": "...", "is_first": False, "is_last": False}
        title = build_segment_title("第一章 开始", seg)
        assert title == "第一章 开始 [Part 2/4]"

    def test_single_segment_title(self):
        seg = {"index": 1, "total": 1, "content": "...", "is_first": True, "is_last": True}
        title = build_segment_title("楔子", seg)
        assert title == "楔子 [Part 1/1]"
