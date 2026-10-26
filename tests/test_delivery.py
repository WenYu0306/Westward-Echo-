"""Tests for src/delivery.py — the one place chapters become Markdown.

Every delivery path (CLI, sync API ×2, Celery ×2) now calls this module. Before
the extraction each had its own copy of the block format and the truncation
floor, and the copies drifted: the oldest path had no guard for two years while
the newest had one. These tests are the contract they all share.
"""

from pathlib import Path

import pytest

from src import delivery


class TestChapterBlock:
    def test_format_is_epub_parseable(self):
        block = delivery.chapter_block(42, "Answer", "Translated content.")
        assert block.startswith("## Chapter 42: Answer")
        assert block.endswith("---\n\n")
        assert "Translated content." in block

    def test_missing_title_falls_back_to_ascii(self):
        """Zero-Chinese rule: the Chinese heading must never be the fallback."""
        block = delivery.chapter_block(7, "", "Body.")
        assert block.startswith("## Chapter 7: Chapter 7")
        assert not any("一" <= ch <= "鿿" for ch in block.split("\n")[0])

    def test_blank_title_falls_back_too(self):
        assert delivery.chapter_block(3, "   ", "x").startswith("## Chapter 3: Chapter 3")


class TestIsDeliverable:
    def test_empty_is_rejected(self):
        ok, why = delivery.is_deliverable("", "源文" * 100)
        assert ok is False and "empty" in why

    def test_whitespace_only_is_rejected(self):
        assert delivery.is_deliverable("  \n\n ", "源文" * 100)[0] is False

    def test_truncated_is_rejected(self):
        ok, why = delivery.is_deliverable("Far too short.", "源文" * 500)
        assert ok is False and "truncated" in why

    def test_adequate_passes(self):
        assert delivery.is_deliverable("word " * 60, "源文" * 100)[0] is True

    def test_floor_is_relative_to_source_length(self):
        # 2000 source chars → floor 200 words; 100 words must fail.
        assert delivery.is_deliverable("word " * 100, "源" * 2000)[0] is False
        assert delivery.is_deliverable("word " * 201, "源" * 2000)[0] is True


class TestSplitAndScan:
    TEXT = ("# Book\n\n"
            "## Chapter 1: One\n\nbody one\n\n---\n\n"
            "## Chapter 2: Two\n\nbody two\n\n---\n\n")

    def test_preamble_is_preserved(self):
        preamble, blocks = delivery.split_blocks(self.TEXT)
        assert preamble == "# Book\n\n"
        assert [n for n, _ in blocks] == [1, 2]

    def test_scan_returns_numbers_and_bodies(self):
        written, bodies = delivery.scan_written(self.TEXT)
        assert written == {1, 2}
        assert "body one" in bodies[1]
        assert "body two" not in bodies[1]

    def test_empty_text(self):
        assert delivery.scan_written("") == (set(), {})
        assert delivery.split_blocks("") == ("", [])


class TestAppend:
    def test_creates_file_with_header(self, tmp_path):
        path = tmp_path / "book.md"
        delivery.append_chapter(path, 1, "One", "body", header="My Book")
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# My Book\n\n")
        assert "## Chapter 1: One" in text

    def test_appends_without_repeating_header(self, tmp_path):
        path = tmp_path / "book.md"
        delivery.append_chapter(path, 1, "One", "a", header="My Book")
        delivery.append_chapter(path, 2, "Two", "b", header="My Book")
        text = path.read_text(encoding="utf-8")
        assert text.count("# My Book") == 1
        assert [n for n in (1, 2) if f"## Chapter {n}:" in text] == [1, 2]


class TestReplaceAndReorder:
    def test_replace_touches_only_named_chapters(self, tmp_path):
        path = tmp_path / "book.md"
        path.write_text("# T\n\n## Chapter 1: One\n\nOLD\n\n---\n\n"
                        "## Chapter 2: Two\n\nKEEP\n\n---\n\n", encoding="utf-8")
        replaced = delivery.replace_chapters(path, {1: "NEW"})
        text = path.read_text(encoding="utf-8")
        assert replaced == 1
        assert "NEW" in text and "OLD" not in text
        assert "KEEP" in text and text.startswith("# T")

    def test_unknown_chapter_changes_nothing(self, tmp_path):
        path = tmp_path / "book.md"
        path.write_text("## Chapter 1: One\n\nA\n\n---\n\n", encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        assert delivery.replace_chapters(path, {9: "X"}) == 0
        assert path.read_text(encoding="utf-8") == before

    def test_reorder_sorts_and_keeps_preamble(self, tmp_path):
        path = tmp_path / "book.md"
        path.write_text("# T\n\n## Chapter 1: One\n\nA\n\n---\n\n"
                        "## Chapter 3: Three\n\nC\n\n---\n\n"
                        "## Chapter 2: Two\n\nB\n\n---\n\n", encoding="utf-8")
        moved = delivery.reorder(path)
        text = path.read_text(encoding="utf-8")
        assert moved > 0
        assert text.startswith("# T")
        order = [line.split(":")[0] for line in text.split("\n")
                 if line.startswith("## Chapter")]
        assert order == ["## Chapter 1", "## Chapter 2", "## Chapter 3"]

    def test_reorder_is_a_no_op_when_already_sorted(self, tmp_path):
        path = tmp_path / "book.md"
        path.write_text("## Chapter 1: One\n\nA\n\n---\n\n"
                        "## Chapter 2: Two\n\nB\n\n---\n\n", encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        assert delivery.reorder(path) == 0
        assert path.read_text(encoding="utf-8") == before
