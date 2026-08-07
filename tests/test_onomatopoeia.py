"""Unit tests for src/onomatopoeia.py — Chinese onomatopoeia detection and context hints."""

from src.onomatopoeia import build_onomatopoeia_context, detect_onomatopoeia


class TestDetectOnomatopoeia:
    """Tests for detect_onomatopoeia()."""

    def test_detects_onomatopoeia_deduplicated_in_list(self):
        """'哗啦啦' appearing twice should produce two entries (one per occurrence)."""
        result = detect_onomatopoeia("哗啦啦哗啦啦的水声")
        assert result == ["哗啦啦", "哗啦啦"]

    def test_returns_empty_list_for_plain_text(self):
        """Text without any known onomatopoeia returns an empty list."""
        result = detect_onomatopoeia("普通文本没有拟声词")
        assert result == []

    def test_detects_multiple_distinct_sounds(self):
        """'啪！砰！咔嚓！' should detect at least three distinct sounds."""
        result = detect_onomatopoeia("啪！砰！咔嚓！")
        # At least 3 distinct sounds
        unique = set(result)
        assert len(unique) >= 3
        assert "啪" in result
        assert "砰" in result
        assert "咔嚓" in result


class TestBuildOnomatopoeiaContext:
    """Tests for build_onomatopoeia_context()."""

    def test_returns_empty_when_no_sounds_found(self):
        """No onomatopoeia in text produces an empty string."""
        result = build_onomatopoeia_context("普通文本没有拟声词")
        assert result == ""

    def test_returns_non_empty_with_sound_in_output(self):
        """When 哗啦啦 is detected, the context must mention it."""
        result = build_onomatopoeia_context("哗啦啦的水声")
        assert result != ""
        assert "哗啦啦" in result

    def test_sound_appearing_twice_listed_once_in_context(self):
        """A sound detected multiple times should appear only once in the context block."""
        result = build_onomatopoeia_context("哗啦啦哗啦啦的水声")
        # 哗啦啦 should appear exactly once in the output (not counting the header)
        # The dedup is done by set() in build_onomatopoeia_context
        assert result.count("哗啦啦") == 1
