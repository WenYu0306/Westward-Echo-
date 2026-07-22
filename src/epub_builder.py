"""EPUB builder for Westward Echo.

Takes translated chapters and generates a valid EPUB 3 file ready for e-readers.
Uses only the Python standard library (no third-party dependencies).

EPUB 3 structure (subset):
  mimetype
  META-INF/container.xml
  OEBPS/content.opf    — package manifest, spine, metadata
  OEBPS/toc.ncx        — NCX table of contents (for older readers)
  OEBPS/nav.xhtml      — EPUB 3 navigation document
  OEBPS/style/default.css
  OEBPS/<chapter>.xhtml
"""

from __future__ import annotations

import uuid
import zipfile
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET


# ── CSS for clean reading ──────────────────────────────────────────

READING_CSS = """
body {
    font-family: "Georgia", "Times New Roman", serif;
    font-size: 0.95em;
    line-height: 1.7;
    color: #1c1c1e;
    margin: 0;
    padding: 0;
}
h1 {
    font-family: "Helvetica Neue", "Arial", sans-serif;
    font-size: 1.5em;
    font-weight: 700;
    margin: 0 0 1.2em 0;
    page-break-before: always;
    text-align: left;
    color: #1c1c1e;
}
h1:first-of-type {
    page-break-before: avoid;
}
p {
    margin: 0 0 0.8em 0;
    text-indent: 1.5em;
}
.cover {
    text-align: center;
    padding: 3em 1em;
}
.cover .book-title {
    font-family: "Helvetica Neue", "Arial", sans-serif;
    font-size: 2.2em;
    font-weight: 700;
    margin-bottom: 0.3em;
    color: #1c1c1e;
}
.cover .book-author {
    font-family: "Helvetica Neue", "Arial", sans-serif;
    font-size: 1.1em;
    font-weight: 400;
    color: #6e6e73;
    margin-bottom: 2em;
}
.cover .book-desc {
    font-family: "Georgia", "Times New Roman", serif;
    font-size: 0.95em;
    font-style: italic;
    color: #3a3a3c;
    margin-top: 2em;
    text-indent: 0;
}
.glossary dt {
    font-weight: 600;
    margin-top: 0.6em;
}
.glossary dd {
    margin-left: 1em;
    margin-bottom: 0.4em;
    color: #3a3a3c;
}
"""

# ── XML namespaces ─────────────────────────────────────────────────

NS = {
    "opf":   "http://www.idpf.org/2007/opf",
    "dc":    "http://purl.org/dc/elements/1.1/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "ncx":   "http://www.daisy.org/z3986/2005/ncx/",
}

# Register namespaces so ET doesn't mangle them
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)
# epubcheck expects no prefix on the OPF namespace
ET.register_namespace("", "http://www.idpf.org/2007/opf")


# ── HTML helpers ────────────────────────────────────────────────────

def _escape(text: str) -> str:
    """Minimal XML/HTML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _markdown_to_html(content: str) -> str:
    """Convert plain-text with blank-line paragraph breaks to <p> tags."""
    paragraphs = content.strip().split("\n\n")
    html_parts = []
    for p in paragraphs:
        text = p.strip().replace("\n", " ")
        if text:
            html_parts.append(f"<p>{_escape(text)}</p>")
    return "\n".join(html_parts)


def _xhtml_page(title: str, body: str) -> str:
    """Wrap body HTML in a minimal XHTML document."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">\n'
        f"<head><title>{_escape(title)}</title></head>\n"
        f"<body>\n{body}\n</body>\n"
        "</html>"
    )


def _cover_page(title: str, author: str, cover_text: str) -> str:
    """Build a cover page XHTML."""
    desc = f'<p class="book-desc">{_escape(cover_text)}</p>' if cover_text else ""
    body = (
        '<div class="cover">\n'
        f'<p class="book-title">{_escape(title)}</p>\n'
        f'<p class="book-author">by {_escape(author)}</p>\n'
        f"{desc}\n"
        "</div>"
    )
    return _xhtml_page("Cover", body)


def _chapter_page(title: str, content: str) -> str:
    """Build a chapter XHTML page."""
    body = f"<h1>{_escape(title)}</h1>\n{_markdown_to_html(content)}"
    return _xhtml_page(title, body)


def _glossary_page(glossary: dict[str, str]) -> str:
    """Build a glossary appendix XHTML page."""
    items = []
    for cn, en in sorted(glossary.items()):
        items.append(f"<dt>{_escape(cn)}</dt><dd>{_escape(en)}</dd>")
    body = (
        "<h1>Glossary / 术语表</h1>\n"
        '<dl class="glossary">\n'
        + "\n".join(items)
        + "\n</dl>"
    )
    return _xhtml_page("Glossary", body)


# ── EPUB XML manifests ──────────────────────────────────────────────

def _build_container_xml() -> bytes:
    """META-INF/container.xml"""
    root = ET.Element("container", {
        "version": "1.0",
        "xmlns": "urn:oasis:names:tc:opendocument:xmlns:container",
    })
    rootfiles = ET.SubElement(root, "rootfiles")
    ET.SubElement(rootfiles, "rootfile", {
        "full-path": "OEBPS/content.opf",
        "media-type": "application/oebps-package+xml",
    })
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _build_opf(
    book_id: str,
    title: str,
    author: str,
    language: str,
    chapter_ids: list[str],     # e.g. ["chapter_1", "chapter_2", ...]
    has_glossary: bool,
) -> bytes:
    """OEBPS/content.opf — the package file."""
    OPF = "http://www.idpf.org/2007/opf"
    DC  = "http://purl.org/dc/elements/1.1/"

    package = ET.Element("package", {
        "xmlns": OPF,
        "version": "3.0",
        "unique-identifier": "book-id",
    })

    # ── Metadata ──
    metadata = ET.SubElement(package, "metadata", {"xmlns:dc": DC})
    ET.SubElement(metadata, f"{{{DC}}}identifier", {"id": "book-id"}).text = book_id
    ET.SubElement(metadata, f"{{{DC}}}title").text = title
    ET.SubElement(metadata, f"{{{DC}}}creator").text = author
    ET.SubElement(metadata, f"{{{DC}}}language").text = language
    meta = ET.SubElement(metadata, "meta", {"property": "dcterms:modified"})
    meta.text = "2026-01-01T00:00:00Z"

    # ── Manifest ──
    manifest = ET.SubElement(package, "manifest")
    ET.SubElement(manifest, "item", {
        "id": "css",
        "href": "style/default.css",
        "media-type": "text/css",
    })
    ET.SubElement(manifest, "item", {
        "id": "cover",
        "href": "cover.xhtml",
        "media-type": "application/xhtml+xml",
    })
    ET.SubElement(manifest, "item", {
        "id": "nav",
        "href": "nav.xhtml",
        "media-type": "application/xhtml+xml",
    })

    for cid in chapter_ids:
        ET.SubElement(manifest, "item", {
            "id": cid,
            "href": f"{cid}.xhtml",
            "media-type": "application/xhtml+xml",
        })

    if has_glossary:
        ET.SubElement(manifest, "item", {
            "id": "glossary",
            "href": "glossary.xhtml",
            "media-type": "application/xhtml+xml",
        })

    ET.SubElement(manifest, "item", {
        "id": "ncx",
        "href": "toc.ncx",
        "media-type": "application/x-dtbncx+xml",
    })

    # ── Spine ──
    spine = ET.SubElement(package, "spine", {"toc": "ncx"})
    ET.SubElement(spine, "itemref", {"idref": "cover"})
    for cid in chapter_ids:
        ET.SubElement(spine, "itemref", {"idref": cid})
    if has_glossary:
        ET.SubElement(spine, "itemref", {"idref": "glossary"})

    return ET.tostring(package, encoding="utf-8", xml_declaration=True)


def _build_ncx(
    book_id: str,
    title: str,
    author: str,
    chapters: list[dict],   # [{title, chapter_num}, ...]
    has_glossary: bool,
) -> bytes:
    """OEBPS/toc.ncx — NCX table of contents (for EPUB 2 compatibility)."""
    NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"

    ncx = ET.Element("ncx", {
        "xmlns": NCX_NS,
        "version": "2005-1",
    })

    head = ET.SubElement(ncx, "head")
    ET.SubElement(head, "meta", {"name": "dtb:uid", "content": book_id})
    ET.SubElement(head, "meta", {"name": "dtb:depth", "content": "1"})
    ET.SubElement(head, "meta", {"name": "dtb:totalPageCount", "content": "0"})
    ET.SubElement(head, "meta", {"name": "dtb:maxPageNumber", "content": "0"})

    doc_title = ET.SubElement(ncx, "docTitle")
    ET.SubElement(doc_title, "text").text = title

    doc_author = ET.SubElement(ncx, "docAuthor")
    ET.SubElement(doc_author, "text").text = author

    nav_map = ET.SubElement(ncx, "navMap")

    play_order = 1
    # Cover
    cover_point = ET.SubElement(nav_map, "navPoint", {
        "id": "nav_cover",
        "playOrder": str(play_order),
    })
    ET.SubElement(ET.SubElement(cover_point, "navLabel"), "text").text = "Cover"
    ET.SubElement(cover_point, "content", {"src": "cover.xhtml"})
    play_order += 1

    # Chapters
    for ch in chapters:
        ch_num = ch.get("chapter_num", play_order)
        ch_title = ch.get("title", f"Chapter {ch_num}")
        point = ET.SubElement(nav_map, "navPoint", {
            "id": f"nav_ch_{ch_num}",
            "playOrder": str(play_order),
        })
        ET.SubElement(ET.SubElement(point, "navLabel"), "text").text = ch_title
        ET.SubElement(point, "content", {"src": f"chapter_{ch_num}.xhtml"})
        play_order += 1

    # Glossary
    if has_glossary:
        gloss_point = ET.SubElement(nav_map, "navPoint", {
            "id": "nav_glossary",
            "playOrder": str(play_order),
        })
        ET.SubElement(ET.SubElement(gloss_point, "navLabel"), "text").text = "Glossary / 术语表"
        ET.SubElement(gloss_point, "content", {"src": "glossary.xhtml"})

    return ET.tostring(ncx, encoding="utf-8", xml_declaration=True)


def _build_nav_xhtml(chapters: list[dict], has_glossary: bool) -> str:
    """OEBPS/nav.xhtml — EPUB 3 navigation document."""
    items = ["<li><a href=\"cover.xhtml\">Cover</a></li>"]
    for ch in chapters:
        ch_num = ch.get("chapter_num", 0)
        ch_title = ch.get("title", f"Chapter {ch_num}")
        items.append(f'<li><a href="chapter_{ch_num}.xhtml">{_escape(ch_title)}</a></li>')
    if has_glossary:
        items.append('<li><a href="glossary.xhtml">Glossary / 术语表</a></li>')

    nav_body = (
        '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc" id="toc">\n'
        "<h1>Table of Contents</h1>\n"
        "<ol>\n"
        + "\n".join(items)
        + "\n</ol>\n"
        "</nav>"
    )
    return _xhtml_page("Navigation", nav_body)


# ── Main builder ────────────────────────────────────────────────────

def build_epub(
    chapters: list[dict],
    title: str,
    author: str = "Westward Echo",
    language: str = "en",
    glossary: Optional[dict] = None,
    cover_text: str = "",
    output_path: str = "",
) -> str:
    """Build a valid EPUB 3 file from translated chapters.

    Args:
        chapters: List of dicts with keys ``title``, ``content``, ``chapter_num``.
        title: Book title (shown on cover and in metadata).
        author: Author name for metadata.
        language: Language code (e.g. ``"en"``, ``"es"``).
        glossary: Optional ``{cn_term: en_term}`` dict appended as an appendix.
        cover_text: Optional description paragraph for the cover page.
        output_path: Where to write the ``.epub`` file.  Defaults to
                     ``<title>.epub`` in the current directory.

    Returns:
        The absolute path to the generated EPUB file.

    Raises:
        ValueError: If ``chapters`` is empty.
        OSError: If the output file cannot be written.
    """
    if not chapters:
        raise ValueError("chapters list must not be empty")

    # ── Determine output path ──
    path = Path(output_path) if output_path else Path(f"{title}.epub")
    path = path.resolve()

    # ── Derived values ──
    book_id = str(uuid.uuid4())
    chapter_ids = [f"chapter_{ch.get('chapter_num', i + 1)}" for i, ch in enumerate(chapters)]

    # ── Open ZIP ──
    try:
        with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as zf:
            # mimetype — MUST be first, uncompressed
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)

            # META-INF/container.xml
            zf.writestr("META-INF/container.xml", _build_container_xml())

            # OEBPS/style/default.css
            zf.writestr("OEBPS/style/default.css", READING_CSS.strip().encode("utf-8"))

            # Cover page
            zf.writestr(
                "OEBPS/cover.xhtml",
                _cover_page(title, author, cover_text).encode("utf-8"),
            )

            # Chapters
            for ch in chapters:
                ch_title = ch.get("title", f"Chapter {ch.get('chapter_num', '?')}")
                ch_num = ch.get("chapter_num", 0)
                zf.writestr(
                    f"OEBPS/chapter_{ch_num}.xhtml",
                    _chapter_page(ch_title, ch.get("content", "")).encode("utf-8"),
                )

            # Glossary
            has_glossary = bool(glossary)
            if has_glossary:
                zf.writestr(
                    "OEBPS/glossary.xhtml",
                    _glossary_page(glossary).encode("utf-8"),
                )

            # Package file (content.opf)
            zf.writestr(
                "OEBPS/content.opf",
                _build_opf(book_id, title, author, language, chapter_ids, has_glossary),
            )

            # NCX
            zf.writestr(
                "OEBPS/toc.ncx",
                _build_ncx(book_id, title, author, chapters, has_glossary),
            )

            # EPUB 3 nav
            zf.writestr(
                "OEBPS/nav.xhtml",
                _build_nav_xhtml(chapters, has_glossary).encode("utf-8"),
            )
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"Failed to write EPUB: {exc}") from exc

    return str(path)
