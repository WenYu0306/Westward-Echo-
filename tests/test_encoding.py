"""Unit tests for encoding.py — Chinese text encoding detection."""

import os
import tempfile

import pytest

from src.encoding import detect_and_read, _looks_like_chinese


class TestLooksLikeChinese:
    def test_chinese_text(self):
        assert _looks_like_chinese("第一章 天空是灰色的。这是一段中文测试文本。" * 50)

    def test_english_text(self):
        assert not _looks_like_chinese("Hello world, this is English text." * 50)

    def test_mixed_text_with_enough_chinese(self):
        # ~50% Chinese, well above 5% threshold
        mixed = "中文测试 hello world " * 100
        assert _looks_like_chinese(mixed)

    def test_empty_text(self):
        assert not _looks_like_chinese("")


class TestDetectAndRead:
    def test_utf8_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb") as f:
            f.write("第一章 测试内容\n这是正文。\n".encode("utf-8"))
            path = f.name
        try:
            text, enc = detect_and_read(path)
            assert "第一章" in text
            assert enc in ("utf-8",)
        finally:
            os.unlink(path)

    def test_utf8_bom(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb") as f:
            f.write(b"\xef\xbb\xbf" + "第一章 测试\n".encode("utf-8"))
            path = f.name
        try:
            text, enc = detect_and_read(path)
            assert enc == "utf-8-bom"
            assert "第一章" in text
        finally:
            os.unlink(path)

    def test_utf16_bom_le(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb") as f:
            # UTF-16 LE BOM + "测试"
            f.write(b"\xff\xfe" + "测试内容\n".encode("utf-16-le"))
            path = f.name
        try:
            text, enc = detect_and_read(path)
            assert enc == "utf-16-bom"
        finally:
            os.unlink(path)

    def test_gbk_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb") as f:
            f.write("第一章 测试内容\n这是正文。\n".encode("gbk"))
            path = f.name
        try:
            text, enc = detect_and_read(path)
            assert "第一章" in text
        finally:
            os.unlink(path)

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            detect_and_read("/nonexistent/path/file.txt")
