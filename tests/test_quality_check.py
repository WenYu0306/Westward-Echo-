"""Unit tests for _extract_sample_passages in src/agent/nodes/quality_check.py.

Tests the paragraph-based sampling heuristic without any LLM API calls.
"""

import sys
import os

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.nodes.quality_check import _extract_sample_passages


# ------------------------------------------------------------------
# Helper to build a multi-paragraph chapter
# ------------------------------------------------------------------

def _make_chapter(n_paragraphs: int) -> str:
    """Build a chapter with n paragraphs, each > 100 chars."""
    paragraphs = []
    for i in range(1, n_paragraphs + 1):
        # Paragraphs long enough to pass the > 100 char filter
        p = (
            f"Paragraph {i}. This is a long paragraph that contains enough text to "
            f"exceed the minimum length requirement of one hundred characters. "
            f"The story continues here with more detail and description. "
            f"Characters move through the scene, dialogue unfolds, and the plot advances."
        )
        paragraphs.append(p)
    return "\n\n".join(paragraphs)


def _make_cn_original(n_lines: int) -> str:
    """Build a Chinese original with n lines, each > 50 chars."""
    lines = []
    for i in range(1, n_lines + 1):
        line = (
            f"第{i}段。这是一段足够长的中文文本，用于测试质量检查的段落提取功能。"
            f"人物在场景中移动，对话展开，情节推进。继续添加更多内容以满足最小长度要求。"
        )
        lines.append(line)
    return "\n".join(lines)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestExtractSamplePassages:

    def test_returns_3_samples_for_large_chapter(self):
        """A chapter with 10+ paragraphs should return exactly 3 samples."""
        en_text = _make_chapter(10)
        cn_text = _make_cn_original(10)
        samples = _extract_sample_passages(en_text, cn_text, n=3)
        assert len(samples) == 3

    def test_returns_empty_for_short_chapter(self):
        """A chapter with only 2 long paragraphs (below n=3) returns empty list."""
        en_text = _make_chapter(2)
        cn_text = _make_cn_original(2)
        samples = _extract_sample_passages(en_text, cn_text, n=3)
        assert samples == []

    def test_samples_have_en_and_cn_keys(self):
        """Every sample dict contains both 'en' and 'cn' keys."""
        en_text = _make_chapter(15)
        cn_text = _make_cn_original(15)
        samples = _extract_sample_passages(en_text, cn_text, n=3)
        for sample in samples:
            assert "en" in sample, f"Sample missing 'en' key: {sample.keys()}"
            assert "cn" in sample, f"Sample missing 'cn' key: {sample.keys()}"
            assert len(sample["en"]) > 0, "English passage should not be empty"

    def test_samples_from_beginning_middle_end(self):
        """Samples are drawn from indices [0, len//2, len-1] — beginning, middle, end."""
        en_text = _make_chapter(11)  # indices: 0, 5, 10
        cn_text = _make_cn_original(15)
        samples = _extract_sample_passages(en_text, cn_text, n=3)

        # First sample should contain "Paragraph 1"
        assert "Paragraph 1" in samples[0]["en"]

        # Middle sample should be from the middle (paragraph index 5 → paragraph 6)
        assert "Paragraph 6" in samples[1]["en"]

        # Last sample should be from the end (paragraph index 10 → paragraph 11)
        assert "Paragraph 11" in samples[2]["en"]

    def test_handles_empty_translated_text_gracefully(self):
        """Empty or whitespace-only translated text returns empty list (no crash)."""
        samples = _extract_sample_passages("", "一些中文内容\n" * 20, n=3)
        assert samples == []

        # Whitespace-only should also not crash
        samples = _extract_sample_passages("   \n\n   \n\n  ", "一些中文内容\n" * 20, n=3)
        assert samples == []

    def test_paragraphs_shorter_than_100_chars_are_filtered_out(self):
        """Only paragraphs longer than 100 chars are considered."""
        # Two short paragraphs (under 100 chars) and three long ones
        en_text = (
            "Short para.\n\n"
            + _make_chapter(1) + "\n\n"
            + "Tiny.\n\n"
            + _make_chapter(1) + "\n\n"
            + _make_chapter(1)
        )
        cn_text = _make_cn_original(5)
        # Total filtered: 3 long paragraphs >= n=3
        samples = _extract_sample_passages(en_text, cn_text, n=3)
        assert len(samples) == 3

    def test_cn_passage_uses_approximate_alignment(self):
        """Chinese passage index is computed proportionally to paragraph position."""
        en_text = _make_chapter(10)
        cn_text = _make_cn_original(20)
        samples = _extract_sample_passages(en_text, cn_text, n=3)

        # First sample: en idx 0, cn_idx = int(0 / 10 * 20) = 0
        assert samples[0]["cn"] != ""
        assert "第1段" in samples[0]["cn"]

        # Middle sample: en idx 5, cn_idx = int(5 / 10 * 20) = 10
        assert "第11段" in samples[1]["cn"]

        # Last sample: en idx 9, cn_idx = int(9 / 10 * 20) = 18
        assert "第19段" in samples[2]["cn"]

    def test_no_crash_when_cn_empty(self):
        """When original Chinese text is empty, samples still have 'cn' key."""
        en_text = _make_chapter(10)
        samples = _extract_sample_passages(en_text, "", n=3)
        assert len(samples) == 3
        for sample in samples:
            assert "cn" in sample
            assert "en" in sample
