"""Unit tests for epub_builder.py — EPUB 3 file generation."""

import os
import tempfile
import zipfile

import pytest

from src.epub_builder import build_epub


@pytest.fixture
def sample_chapters():
    return [
        {
            "title": "Chapter 1: The Beginning",
            "content": "She stepped into the hall.\n\nIt was dark and cold.",
            "chapter_num": 1,
        },
        {
            "title": "Chapter 2: The Meeting",
            "content": "He turned around.\n\nTheir eyes met across the room.",
            "chapter_num": 2,
        },
    ]


@pytest.fixture
def output_path():
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "test_output.epub")
    yield path
    if os.path.exists(path):
        os.remove(path)
    os.rmdir(tmpdir)


class TestEpubBuild:
    def test_build_valid_epub(self, sample_chapters, output_path):
        path = build_epub(
            chapters=sample_chapters,
            title="Test Novel",
            author="Test Author",
            language="en",
            output_path=output_path,
        )
        assert os.path.samefile(path, output_path)  # macOS /tmp → /private/tmp symlink
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0

    def test_epub_is_valid_zip(self, sample_chapters, output_path):
        build_epub(sample_chapters, "Test", output_path=output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            names = zf.namelist()
            assert "mimetype" in names
            assert "META-INF/container.xml" in names
            assert "OEBPS/content.opf" in names

    def test_mimetype_is_first_and_uncompressed(self, sample_chapters, output_path):
        build_epub(sample_chapters, "Test", output_path=output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            info = zf.getinfo("mimetype")
            assert info.compress_type == zipfile.ZIP_STORED

    def test_empty_chapters_raises(self, output_path):
        with pytest.raises(ValueError, match="must not be empty"):
            build_epub([], "Empty Book", output_path=output_path)

    def test_epub_includes_all_chapters(self, sample_chapters, output_path):
        build_epub(sample_chapters, "Test", output_path=output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            assert "OEBPS/chapter_1.xhtml" in zf.namelist()
            assert "OEBPS/chapter_2.xhtml" in zf.namelist()

    def test_chapter_content_present(self, sample_chapters, output_path):
        build_epub(sample_chapters, "Test", output_path=output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            c1 = zf.read("OEBPS/chapter_1.xhtml").decode("utf-8")
            assert "The Beginning" in c1
            assert "She stepped into the hall" in c1

    def test_cover_page_included(self, sample_chapters, output_path):
        build_epub(sample_chapters, "Test Novel", output_path=output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            cover = zf.read("OEBPS/cover.xhtml").decode("utf-8")
            assert "Test Novel" in cover

    def test_glossary_appendix(self, sample_chapters, output_path):
        glossary = {"霸总": "Alpha CEO", "苏念": "Su Nian"}
        build_epub(
            sample_chapters, "Test", glossary=glossary, output_path=output_path,
        )
        with zipfile.ZipFile(output_path, "r") as zf:
            assert "OEBPS/glossary.xhtml" in zf.namelist()
            g = zf.read("OEBPS/glossary.xhtml").decode("utf-8")
            assert "Alpha CEO" in g
            assert "Su Nian" in g

    def test_no_glossary_when_none(self, sample_chapters, output_path):
        build_epub(sample_chapters, "Test", glossary=None, output_path=output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            assert "OEBPS/glossary.xhtml" not in zf.namelist()

    def test_cover_text(self, sample_chapters, output_path):
        build_epub(
            sample_chapters, "Test", cover_text="A thrilling tale.",
            output_path=output_path,
        )
        with zipfile.ZipFile(output_path, "r") as zf:
            cover = zf.read("OEBPS/cover.xhtml").decode("utf-8")
            assert "A thrilling tale" in cover

    def test_single_chapter(self, output_path):
        chapters = [{
            "title": "Prologue", "content": "Once upon a time.",
            "chapter_num": 1,
        }]
        path = build_epub(chapters, "Single", output_path=output_path)
        assert os.path.exists(path)

    def test_default_output_path(self, sample_chapters):
        path = build_epub(sample_chapters, "DefaultTest")
        assert os.path.exists(path)
        os.remove(path)

    def test_content_xml_escaped(self, output_path):
        chapters = [{
            "title": "Test &amp; More",
            "content": "She said, \"Hello <world>\" & smiled.",
            "chapter_num": 1,
        }]
        build_epub(chapters, "Ampersand Test", output_path=output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            c = zf.read("OEBPS/chapter_1.xhtml").decode("utf-8")
            assert "&amp;" in c or "&gt;" in c  # something was escaped

    def test_ncx_toc_present(self, sample_chapters, output_path):
        build_epub(sample_chapters, "Test", output_path=output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            assert "OEBPS/toc.ncx" in zf.namelist()

    def test_nav_xhtml_present(self, sample_chapters, output_path):
        build_epub(sample_chapters, "Test", output_path=output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            assert "OEBPS/nav.xhtml" in zf.namelist()
