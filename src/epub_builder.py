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

【中文·这个文件是干什么的】
把翻译好的章节打包成 EPUB 电子书文件。
EPUB 是一种通用的电子书格式，Kindle、苹果图书、各类阅读器都能打开，
本质上是个打包好的网页集合（ZIP 压缩包里装着一堆 XHTML + CSS + XML）。

【中文·它在整个流程里的位置】
位于翻译流水线的最末端——所有章节都翻完之后，调用 build_epub()
把它们组装成一个可以下载阅读的 .epub 文件。

【中文·核心概念解释】
· EPUB 3 —— 电子书标准格式，由国际数字出版论坛（IDPF）制定。
  一个 .epub 文件其实就是一个 ZIP 压缩包，里面有：
    mimetype          → 固定字符串，标识这是 EPUB 文件（必须是第一个文件且不压缩）
    META-INF/         → 告诉阅读器去哪里找主配置文件
    OEBPS/            → 实际内容所在目录
      content.opf     → "总目录"，列出所有文件和阅读顺序
      toc.ncx         → 旧版目录格式（兼容老阅读器）
      nav.xhtml       → 新版目录格式（EPUB 3 标准）
      style/default.css → 排版样式表
      chapter_N.xhtml → 每一章的内容（XHTML 格式，类似 HTML）
· XML 命名空间（namespace）—— XML 里用来区分"同名但含义不同的标签"的机制。
  EPUB 涉及多个 XML 标准，每个都有自己的命名空间 URL。
· XHTML —— 比 HTML 更严格的网页格式，EPUB 要求用 XHTML 而不是 HTML。

【中文·本文件的结构】
  · 常量区   —— CSS 样式、XML 命名空间定义
  · HTML 辅助函数 —— 生成封面页、章节页、术语表页等 XHTML 内容
  · XML 构建函数  —— 生成 container.xml、content.opf、toc.ncx 等配置文件
  · build_epub()  —— 主入口，把所有零件打包成一个 .epub 文件

【中文·设计亮点】
完全使用 Python 标准库（zipfile + xml.etree），不依赖任何第三方包。
这意味着不需要 pip install 任何东西就能生成 EPUB，部署更简单。
"""

from __future__ import annotations

import uuid
import zipfile
from pathlib import Path
from typing import Optional, cast
from xml.etree import ElementTree as ET

# ── XML namespaces used by the EPUB package/NCX files ──────────────
# 【中文·XML 命名空间常量】
# EPUB 涉及的几个 XML 标准各自有唯一的命名空间 URL，
# 构建 XML 元素时必须用这些 URL 作为前缀，否则阅读器无法识别。
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"

# ── CSS for clean reading ──────────────────────────────────────────
# 【中文·阅读样式表】
# 这段 CSS 定义了电子书的排版风格：字体、字号、行距、段缩进等。
# 会被打包进 EPUB 的 OEBPS/style/default.css，阅读器加载后应用到所有章节。
# 设计原则：简洁、舒适、适合长时间阅读。

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
# 【中文·命名空间字典】
# 用于 ET.register_namespace()，让生成的 XML 使用可读的前缀（如 dc:title）
# 而不是自动生成的 ns0:title。

NS = {
    "opf":   "http://www.idpf.org/2007/opf",
    "dc":    "http://purl.org/dc/elements/1.1/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "ncx":   "http://www.daisy.org/z3986/2005/ncx/",
}

# Register namespaces so ET doesn't mangle them
# 【中文】注册命名空间前缀，这样 ET.tostring() 输出的 XML 里
# 会用 dc:title 而不是 ns0:title，可读性好很多。
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)
# epubcheck expects no prefix on the OPF namespace
# 【中文】OPF 命名空间注册为空前缀（默认命名空间），
# 因为 EPUB 验证工具（epubcheck）要求 content.opf 里的 OPF 标签不带前缀。
ET.register_namespace("", "http://www.idpf.org/2007/opf")


# ── HTML helpers ────────────────────────────────────────────────────
# 【中文·HTML/XHTML 辅助函数区】
# 下面几个函数负责把纯文本内容包装成合法的 XHTML 页面。


def _escape(text: str) -> str:
    """Minimal XML/HTML escaping.

    【中文·转义特殊字符】
    把 & < > " 这四个在 XML/HTML 里有特殊含义的字符替换成实体引用。
    不这么做的话，如果章节正文里出现 "<script>" 之类的文字，
    会破坏 XHTML 结构甚至引发安全问题（XSS）。
    注意这里没有转义单引号 '，因为在双引号属性值里不需要。
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _markdown_to_html(content: str) -> str:
    """Convert plain-text with blank-line paragraph breaks to <p> tags.

    【中文·把纯文本转成 HTML 段落】
    按空行分段，每段包一层 <p> 标签。换行符替换成空格（避免 XHTML 里出现裸换行）。
    这是一个极简的转换器，不支持 Markdown 语法（加粗、斜体等），
    因为翻译成品已经是纯文本了，不需要复杂的 Markdown 解析。
    """
    paragraphs = content.strip().split("\n\n")
    html_parts = []
    for p in paragraphs:
        text = p.strip().replace("\n", " ")
        if text:
            html_parts.append(f"<p>{_escape(text)}</p>")
    return "\n".join(html_parts)


def _xhtml_page(title: str, body: str) -> str:
    """Wrap body HTML in a minimal XHTML document.

    【中文·把一段 HTML 内容包装成完整的 XHTML 文档】
    加上 XML 声明、DOCTYPE、html/head/body 标签。
    lang="en" 告诉阅读器这本书是英文的（影响断字规则和朗读发音）。
    """
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">\n'
        f"<head><title>{_escape(title)}</title></head>\n"
        f"<body>\n{body}\n</body>\n"
        "</html>"
    )


def _cover_page(title: str, author: str, cover_text: str) -> str:
    """Build a cover page XHTML.

    【中文·生成封面页】
    包含书名、作者名和可选的描述文字。
    cover_text 如果有内容就显示，没有就不渲染描述段落。
    """
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
    """Build a chapter XHTML page.

    【中文·生成章节页】
    标题用 <h1>，正文通过 _markdown_to_html 转成 <p> 段落。
    """
    body = f"<h1>{_escape(title)}</h1>\n{_markdown_to_html(content)}"
    return _xhtml_page(title, body)


def _glossary_page(glossary: dict[str, str]) -> str:
    """Build a glossary appendix XHTML page.

    【中文·生成术语表附录页】
    把 {中文词: 英文译名} 字典排成 <dl> 定义列表，按中文词排序。
    放在书的最后，方便读者查阅不认识的术语。
    """
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
# 【中文·EPUB 配置文件构建区】
# 下面几个函数生成 EPUB 内部的各种 XML 配置文件。
# 这些文件告诉阅读器"这本书有哪些章节""按什么顺序读""目录长什么样"。


def _build_container_xml() -> bytes:
    """META-INF/container.xml

    【中文·生成容器配置文件】
    这是阅读器打开 EPUB 后第一个读取的文件，
    它的作用就一个：告诉阅读器"主配置文件在 OEBPS/content.opf"。
    返回 UTF-8 编码的字节串（写入 ZIP 时需要字节而非字符串）。
    """
    root = ET.Element("container", {
        "version": "1.0",
        "xmlns": "urn:oasis:names:tc:opendocument:xmlns:container",
    })
    rootfiles = ET.SubElement(root, "rootfiles")
    ET.SubElement(rootfiles, "rootfile", {
        "full-path": "OEBPS/content.opf",
        "media-type": "application/oebps-package+xml",
    })
    return cast(bytes, ET.tostring(root, encoding="utf-8", xml_declaration=True))


def _build_opf(
    book_id: str,
    title: str,
    author: str,
    language: str,
    chapter_ids: list[str],     # e.g. ["chapter_1", "chapter_2", ...]
    has_glossary: bool,
) -> bytes:
    """OEBPS/content.opf — the package file.

    【中文·生成包描述文件（content.opf）】
    这是 EPUB 最重要的配置文件，包含三部分：
      ① Metadata（元数据）—— 书名、作者、语言、唯一标识符、修改时间
      ② Manifest（清单）  —— 列出包里所有文件及其类型
      ③ Spine（书脊）     —— 定义阅读顺序（先封面，再各章，最后术语表）

    【参数】
      book_id      书籍唯一标识符（UUID 格式）
      title        书名
      author       作者名
      language     语言代码（如 "en"、"es"）
      chapter_ids  章节 ID 列表，如 ["chapter_1", "chapter_2"]
      has_glossary 是否包含术语表附录

    【返回】UTF-8 编码的 XML 字节串
    """
    package = ET.Element("package", {
        "xmlns": OPF_NS,
        "version": "3.0",
        "unique-identifier": "book-id",
    })

    # ── Metadata ──
    # 【中文】元数据区：书名、作者、语言等基本信息。
    # DC_NS 是 Dublin Core（都柏林核心）命名空间，EPUB 用它来标记通用元数据。
    metadata = ET.SubElement(package, "metadata", {"xmlns:dc": DC_NS})
    ET.SubElement(metadata, f"{{{DC_NS}}}identifier", {"id": "book-id"}).text = book_id
    ET.SubElement(metadata, f"{{{DC_NS}}}title").text = title
    ET.SubElement(metadata, f"{{{DC_NS}}}creator").text = author
    ET.SubElement(metadata, f"{{{DC_NS}}}language").text = language
    meta = ET.SubElement(metadata, "meta", {"property": "dcterms:modified"})
    # 【中文】修改时间写死为固定值。
    # EPUB 规范要求必须有这个字段，但因为我们是即时生成的，
    # 用当前时间会让每次生成的文件不同（不利于校验），所以用固定值。
    meta.text = "2026-01-01T00:00:00Z"

    # ── Manifest ──
    # 【中文】清单区：列出 EPUB 包里包含的所有文件。
    # 每个 item 有 id（内部引用名）、href（文件路径）、media-type（MIME 类型）。
    manifest = ET.SubElement(package, "manifest")
    # 样式表
    ET.SubElement(manifest, "item", {
        "id": "css",
        "href": "style/default.css",
        "media-type": "text/css",
    })
    # 封面页
    ET.SubElement(manifest, "item", {
        "id": "cover",
        "href": "cover.xhtml",
        "media-type": "application/xhtml+xml",
    })
    # EPUB 3 导航文档
    ET.SubElement(manifest, "item", {
        "id": "nav",
        "href": "nav.xhtml",
        "media-type": "application/xhtml+xml",
    })

    # 【中文】逐章添加到清单。
    for cid in chapter_ids:
        ET.SubElement(manifest, "item", {
            "id": cid,
            "href": f"{cid}.xhtml",
            "media-type": "application/xhtml+xml",
        })

    # 【中文】如果有术语表，也加到清单里。
    if has_glossary:
        ET.SubElement(manifest, "item", {
            "id": "glossary",
            "href": "glossary.xhtml",
            "media-type": "application/xhtml+xml",
        })

    # NCX 目录（兼容旧版阅读器）
    ET.SubElement(manifest, "item", {
        "id": "ncx",
        "href": "toc.ncx",
        "media-type": "application/x-dtbncx+xml",
    })

    # ── Spine ──
    # 【中文】书脊区：定义阅读顺序。
    # 阅读器按这里的顺序依次展示各章节。
    # toc="ncx" 指向 NCX 目录文件，供旧版阅读器使用。
    spine = ET.SubElement(package, "spine", {"toc": "ncx"})
    # 封面排第一
    ET.SubElement(spine, "itemref", {"idref": "cover"})
    # 然后是各章
    for cid in chapter_ids:
        ET.SubElement(spine, "itemref", {"idref": cid})
    # 术语表排最后
    if has_glossary:
        ET.SubElement(spine, "itemref", {"idref": "glossary"})

    return cast(bytes, ET.tostring(package, encoding="utf-8", xml_declaration=True))


def _build_ncx(
    book_id: str,
    title: str,
    author: str,
    chapters: list[dict],   # [{title, chapter_num}, ...]
    has_glossary: bool,
) -> bytes:
    """OEBPS/toc.ncx — NCX table of contents (for EPUB 2 compatibility).

    【中文·生成 NCX 目录文件】
    NCX 是 EPUB 2 时代的目录格式，虽然 EPUB 3 有了 nav.xhtml，
    但很多老阅读器只认 NCX，所以两个都要生成。

    结构：
      head     —— 元信息（uid、深度、页数等）
      docTitle —— 书名
      docAuthor—— 作者
      navMap   —— 导航点列表（封面 → 各章 → 术语表）

    playOrder 属性定义导航点的顺序，从 1 开始递增。

    【参数】
      book_id      书籍唯一标识符
      title        书名
      author       作者名
      chapters     章节信息列表，每项含 title 和 chapter_num
      has_glossary 是否有术语表

    【返回】UTF-8 编码的 XML 字节串
    """
    ncx = ET.Element("ncx", {
        "xmlns": NCX_NS,
        "version": "2005-1",
    })

    # 【中文】NCX head 区的元信息。
    # dtb:uid 必须和 content.opf 里的 identifier 一致。
    # depth=1 表示目录只有一层（没有子章节嵌套）。
    # totalPageCount 和 maxPageNumber 设为 0，阅读器会自动计算。
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
    # 【中文】封面的导航点。
    cover_point = ET.SubElement(nav_map, "navPoint", {
        "id": "nav_cover",
        "playOrder": str(play_order),
    })
    ET.SubElement(ET.SubElement(cover_point, "navLabel"), "text").text = "Cover"
    ET.SubElement(cover_point, "content", {"src": "cover.xhtml"})
    play_order += 1

    # Chapters
    # 【中文】逐章添加导航点。
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
    # 【中文】术语表的导航点（如果有的话）。
    if has_glossary:
        gloss_point = ET.SubElement(nav_map, "navPoint", {
            "id": "nav_glossary",
            "playOrder": str(play_order),
        })
        ET.SubElement(ET.SubElement(gloss_point, "navLabel"), "text").text = "Glossary / 术语表"
        ET.SubElement(gloss_point, "content", {"src": "glossary.xhtml"})

    return cast(bytes, ET.tostring(ncx, encoding="utf-8", xml_declaration=True))


def _build_nav_xhtml(chapters: list[dict], has_glossary: bool) -> str:
    """OEBPS/nav.xhtml — EPUB 3 navigation document.

    【中文·生成 EPUB 3 导航文档】
    这是 EPUB 3 标准的目录格式，用 HTML 有序列表（<ol>）呈现。
    新式阅读器优先读这个文件，老式阅读器读 toc.ncx。
    epub:type="toc" 属性告诉阅读器"这是一个目录"。

    【参数】
      chapters     章节信息列表
      has_glossary 是否有术语表

    【返回】完整的 XHTML 文档字符串
    """
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
# 【中文·主构建函数区】


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

    【中文·把翻译好的章节打包成 EPUB 电子书】

    这是本文件的主入口函数。工作流程：
      ① 校验输入（章节列表不能为空）
      ② 确定输出路径和书籍 ID
      ③ 创建 ZIP 压缩包，按 EPUB 规范依次写入各文件：
         mimetype → container.xml → CSS → 封面 → 各章 → 术语表 → OPF → NCX → NAV
      ④ 返回生成的文件绝对路径

    【参数逐个说明】
      chapters    章节列表，每项是字典，需含 title（标题）、content（正文）、
                  chapter_num（章号，可选，缺省按顺序编号）
      title       书名，显示在封面和元数据里
      author      作者名，默认 "Westward Echo"
      language    语言代码，如 "en"（英语）、"es"（西班牙语）
      glossary    可选的术语字典 {中文: 英文}，会作为附录放在书末
      cover_text  可选的封面描述文字
      output_path 输出文件路径，默认为当前目录下的 "{书名}.epub"

    【返回】生成的 .epub 文件的绝对路径字符串

    【坑】
      · mimetype 必须是 ZIP 里的第一个文件且不能压缩（ZIP_STORED），
        否则某些阅读器无法识别这是 EPUB 文件。
      · chapter_nums 在这里统一计算一次，确保 OPF、NCX、文件名三者一致。
        早期版本在各处分别计算，导致重复的 chapter_0.xhtml 和断裂的书脊引用。
    """
    if not chapters:
        raise ValueError("chapters list must not be empty")

    # ── Determine output path ──
    # 【中文】确定输出路径：如果没指定就用书名命名，resolve() 转为绝对路径。
    path = Path(output_path) if output_path else Path(f"{title}.epub")
    path = path.resolve()

    # ── Derived values ──
    # 【中文】生成书籍唯一标识符（UUID），每本书一个，不会重复。
    book_id = str(uuid.uuid4())
    # Number the chapters once, here. The OPF manifest idrefs, the spine, the
    # NCX navPoints and the XHTML file names must all agree; computing the
    # fallback separately in each place wrote duplicate `chapter_0.xhtml`
    # entries for chapters without an explicit number, and left the spine
    # pointing at files that were never created.
    #
    # 【中文】统一计算章节编号。
    # 如果某章没有 chapter_num 字段，就用它在列表中的位置（i+1）作为编号。
    # 这个编号列表会在下面的 OPF、NCX、文件名中复用，保证三处完全一致。
    # 早期版本在三个地方各自算编号，结果不一致导致阅读器找不到章节文件。
    chapter_nums = [
        ch.get("chapter_num") or (i + 1) for i, ch in enumerate(chapters)
    ]
    chapter_ids = [f"chapter_{n}" for n in chapter_nums]

    # ── Open ZIP ──
    # 【中文】创建 ZIP 压缩包并依次写入 EPUB 所需的各个文件。
    try:
        with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as zf:
            # mimetype — MUST be first, uncompressed
            # 【中文】⚠️ mimetype 必须是第一个文件且用 ZIP_STORED（不压缩）。
            # 这是 EPUB 规范的硬性要求：阅读器靠读取 ZIP 头部固定位置的
            # "application/epub+zip" 字符串来判断文件类型，压缩了就找不到。
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)

            # META-INF/container.xml
            # 【中文】容器配置：告诉阅读器主配置文件在哪。
            zf.writestr("META-INF/container.xml", _build_container_xml())

            # OEBPS/style/default.css
            # 【中文】样式表：定义全书的排版风格。
            zf.writestr("OEBPS/style/default.css", READING_CSS.strip().encode("utf-8"))

            # Cover page
            # 【中文】封面页。
            zf.writestr(
                "OEBPS/cover.xhtml",
                _cover_page(title, author, cover_text).encode("utf-8"),
            )

            # Chapters
            # 【中文】逐章写入 XHTML 文件。
            for ch, ch_num in zip(chapters, chapter_nums):
                ch_title = ch.get("title", f"Chapter {ch_num}")
                zf.writestr(
                    f"OEBPS/chapter_{ch_num}.xhtml",
                    _chapter_page(ch_title, ch.get("content", "")).encode("utf-8"),
                )

            # Glossary
            # 【中文】术语表附录（如果提供了术语字典）。
            has_glossary = bool(glossary)
            if has_glossary:
                zf.writestr(
                    "OEBPS/glossary.xhtml",
                    _glossary_page(glossary).encode("utf-8"),
                )

            # Package file (content.opf)
            # 【中文】包描述文件：元数据 + 清单 + 书脊。
            zf.writestr(
                "OEBPS/content.opf",
                _build_opf(book_id, title, author, language, chapter_ids, has_glossary),
            )

            # NCX
            # 【中文】NCX 目录（兼容旧版阅读器）。
            zf.writestr(
                "OEBPS/toc.ncx",
                _build_ncx(book_id, title, author, chapters, has_glossary),
            )

            # EPUB 3 nav
            # 【中文】EPUB 3 导航文档（新版阅读器使用）。
            zf.writestr(
                "OEBPS/nav.xhtml",
                _build_nav_xhtml(chapters, has_glossary).encode("utf-8"),
            )
    except OSError:
        raise
    except Exception as exc:
        # 【中文】非 OS 错误（比如 XML 序列化失败）包装成 OSError 抛出，
        # 让调用方统一用 OSError 捕获所有文件写入相关的异常。
        raise OSError(f"Failed to write EPUB: {exc}") from exc

    return str(path)
