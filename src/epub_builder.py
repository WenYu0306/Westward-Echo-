"""EPUB builder for Westward Echo.

Takes translated chapters and generates an EPUB file ready for e-readers.
Uses ebooklib (pure Python, no system dependencies).
"""

import uuid
from pathlib import Path

from ebooklib import epub


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

p.no-indent {
    text-indent: 0;
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
    max-width: 24em;
    margin-left: auto;
    margin-right: auto;
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


def _make_chapter_html(title: str, body_html: str) -> str:
    """Wrap a chapter body in a minimal HTML document."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
<head><title>{_escape(title)}</title></head>
<body>
<h1>{_escape(title)}</h1>
{body_html}
</body>
</html>"""


def _make_cover_html(title: str, author: str, cover_text: str) -> str:
    """Build the cover page HTML."""
    desc_html = f'<p class="book-desc">{_escape(cover_text)}</p>' if cover_text else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
<head><title>Cover</title></head>
<body>
<div class="cover">
<p class="book-title">{_escape(title)}</p>
<p class="book-author">by {_escape(author)}</p>
{desc_html}
</div>
</body>
</html>"""


def _make_glossary_html(glossary: dict[str, str]) -> str:
    """Build an appendix chapter containing the glossary."""
    items = ""
    for cn, en in sorted(glossary.items()):
        items += f"<dt>{_escape(cn)}</dt><dd>{_escape(en)}</dd>\n"
    body = f'<dl class="glossary">\n{items}</dl>'
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
<head><title>Glossary</title></head>
<body>
<h1>Glossary / 术语表</h1>
{body}
</body>
</html>"""


def _escape(text: str) -> str:
    """Minimal XML/HTML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _markdown_paragraphs_to_html(content: str) -> str:
    """Convert plain-text body with blank-line paragraph breaks to <p> tags."""
    paragraphs = content.strip().split("\n\n")
    html_parts = []
    for p in paragraphs:
        text = p.strip().replace("\n", " ")
        if text:
            html_parts.append(f"<p>{_escape(text)}</p>")
    return "\n".join(html_parts)


def build_epub(
    chapters: list[dict],
    title: str,
    author: str = "Westward Echo",
    language: str = "en",
    glossary: dict | None = None,
    cover_text: str = "",
    output_path: str = "",
) -> str:
    """Build an EPUB file from translated chapters.

    Args:
        chapters: List of dicts with keys ``title``, ``content``, ``chapter_num``.
        title: Book title (shown on cover and in metadata).
        author: Author name for metadata.
        language: Language code (e.g. "en", "es").
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

    # ── Create book ──
    book = epub.EpubBook()
    book.set_identifier(str(uuid.uuid4()))
    book.set_title(title)
    book.set_language(language)
    book.add_author(author)

    # ── Add CSS ──
    css = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=READING_CSS.encode("utf-8"),
    )
    book.add_item(css)

    # ── Cover page ──
    cover = epub.EpubHtml(
        title="Cover",
        file_name="cover.xhtml",
        lang=language,
    )
    cover.content = _make_cover_html(title, author, cover_text).encode("utf-8")
    cover.add_item(css)
    book.add_item(cover)

    # ── Spine / TOC lists ──
    spine = ["nav", cover]
    toc = []

    # ── Chapters ──
    chapter_items = []
    for ch in chapters:
        ch_title = ch.get("title", f"Chapter {ch.get('chapter_num', '?')}")
        ch_num = ch.get("chapter_num", 0)
        body_html = _markdown_paragraphs_to_html(ch.get("content", ""))

        item = epub.EpubHtml(
            title=ch_title,
            file_name=f"chapter_{ch_num}.xhtml",
            lang=language,
        )
        item.content = _make_chapter_html(ch_title, body_html).encode("utf-8")
        item.add_item(css)
        book.add_item(item)
        chapter_items.append(item)
        spine.append(item)
        toc.append(epub.Link(f"chapter_{ch_num}.xhtml", ch_title, f"ch_{ch_num}"))

    # ── Glossary appendix ──
    if glossary:
        glossary_item = epub.EpubHtml(
            title="Glossary",
            file_name="glossary.xhtml",
            lang=language,
        )
        glossary_item.content = _make_glossary_html(glossary).encode("utf-8")
        glossary_item.add_item(css)
        book.add_item(glossary_item)
        spine.append(glossary_item)
        toc.append(epub.Link("glossary.xhtml", "Glossary / 术语表", "glossary"))

    # ── Assemble ──
    book.toc = toc
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # ── Write ──
    try:
        epub.write_epub(str(path), book)
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"Failed to write EPUB: {exc}") from exc

    return str(path)
