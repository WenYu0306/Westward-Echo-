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

    def test_default_output_path(self, sample_chapters, tmp_path, monkeypatch):
        # Default path is CWD/<title>.epub — isolate it into tmp_path so a
        # failure can't leave an artifact in the project root.
        monkeypatch.chdir(tmp_path)
        path = build_epub(sample_chapters, "DefaultTest")
        assert path == str(tmp_path / "DefaultTest.epub")
        assert os.path.exists(path)

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

    def test_xml_files_declare_their_encoding(self, sample_chapters, output_path):
        # Every XML part must carry the declaration: a reader that falls back to
        # a locale default would mis-decode the Chinese in a glossary appendix.
        build_epub(sample_chapters, "Test", glossary={"王三": "Wang San"},
                   output_path=output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            for name in ("META-INF/container.xml", "OEBPS/content.opf",
                         "OEBPS/toc.ncx"):
                xml = zf.read(name).decode("utf-8")
                assert xml.startswith("<?xml "), name
                assert "encoding='utf-8'" in xml or 'encoding="utf-8"' in xml, name


class TestEpubNavOrdering:
    """playOrder must number the TOC entries 1..N in reading order."""

    def _ncx(self, chapters, output_path, glossary=None):
        build_epub(chapters, "Test", glossary=glossary, output_path=output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            return zf.read("OEBPS/toc.ncx").decode("utf-8")

    def test_cover_is_play_order_one(self, sample_chapters, output_path):
        ncx = self._ncx(sample_chapters, output_path)
        cover = ncx.split('id="nav_cover"')[1].split("</navPoint>")[0]
        assert 'playOrder="1"' in cover

    def test_first_chapter_is_play_order_two(self, sample_chapters, output_path):
        ncx = self._ncx(sample_chapters, output_path)
        first = ncx.split('id="nav_ch_1"')[1].split("</navPoint>")[0]
        assert 'playOrder="2"' in first

    def test_second_chapter_is_play_order_three(self, sample_chapters, output_path):
        ncx = self._ncx(sample_chapters, output_path)
        second = ncx.split('id="nav_ch_2"')[1].split("</navPoint>")[0]
        assert 'playOrder="3"' in second

    def test_glossary_follows_the_chapters(self, sample_chapters, output_path):
        ncx = self._ncx(sample_chapters, output_path, glossary={"王三": "Wang San"})
        gloss = ncx.split('id="nav_glossary"')[1].split("</navPoint>")[0]
        assert 'playOrder="4"' in gloss


class TestEpubDerivedChapterIds:
    """A chapter without an explicit number still gets a 1-based id."""

    def test_missing_chapter_num_falls_back_to_one_based_index(self, output_path):
        chapters = [
            {"title": "First", "content": "Alpha text here."},
            {"title": "Second", "content": "Beta text here."},
        ]
        build_epub(chapters, "Test", output_path=output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            names = zf.namelist()
            assert "OEBPS/chapter_1.xhtml" in names
            assert "OEBPS/chapter_2.xhtml" in names
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
            assert 'idref="chapter_1"' in opf
            assert 'idref="chapter_2"' in opf
