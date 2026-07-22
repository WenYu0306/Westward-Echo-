"""Chapter splitter for Chinese web novels.

Splits a raw .txt file into chapters using regex patterns, and classifies
non-standard paragraphs (prologues, extras, author notes, etc.).
"""

import re
from dataclasses import dataclass, field
from enum import Enum


class ParagraphTag(str, Enum):
    CHAPTER = "chapter"             # Normal chapter
    PROLOGUE = "prologue"           # 楔子 / 序章 / 引子
    EXTRA = "extra"                 # 番外 / 外传 / IF线
    AUTHOR_NOTE = "author_note"     # 请假条 / 上架感言 / 更新公告
    SPECIAL = "special"             # 节日特别篇 / 加更

    # Actions
    TRANSLATE = "translate"
    TRANSLATE_NO_EXTRACT = "translate_no_extract"  # Translate but skip term extraction
    SKIP = "skip"                                  # Don't translate


# --- Regex patterns ---

# Standard chapter headers: "第123章", "第一百二十章", "第三章 标题"
CHAPTER_PATTERN = re.compile(
    r'^\s*(第[一二三四五六七八九十百千0-9]+[章节回话]\s*.*)', re.MULTILINE
)

# Non-standard section classification
NON_CHAPTER_PATTERNS = {
    ParagraphTag.PROLOGUE: re.compile(r'^(楔子|序章|引子|第[零0〇]+章)'),
    ParagraphTag.EXTRA:     re.compile(r'^(番外|外传|IF线|小剧场|后记)'),
    ParagraphTag.AUTHOR_NOTE: re.compile(r'(请假|更新公告|上架感言|入V|V章|停更|恢复更新)'),
    ParagraphTag.SPECIAL:   re.compile(r'(七夕|春节|元旦|中秋|国庆|圣诞|特别篇|加更|福利)'),
}

# Short-text threshold for author notes (skip translation)
AUTHOR_NOTE_MIN_LENGTH = 500


@dataclass
class Chapter:
    """A single chapter (or special section) from the novel."""

    index: int                      # 1-based chapter number
    title: str                      # Original chapter title line
    content: str                    # Full body text
    tag: ParagraphTag = ParagraphTag.CHAPTER
    action: ParagraphTag = ParagraphTag.TRANSLATE

    @property
    def word_count(self) -> int:
        """Approximate Chinese character count."""
        return len(self.content.replace('\n', '').replace(' ', ''))

    def preview(self, n: int = 80) -> str:
        """First n characters of content for display."""
        return self.content.strip()[:n]


def classify_paragraph(title: str, content: str) -> tuple[ParagraphTag, ParagraphTag]:
    """Classify a section and decide its translation action.

    Returns (tag, action).
    """
    title_lower = title.lower()
    content_len = len(content.replace('\n', '').replace(' ', ''))

    # Author notes: short + contains announcement keywords
    if content_len < AUTHOR_NOTE_MIN_LENGTH and NON_CHAPTER_PATTERNS[ParagraphTag.AUTHOR_NOTE].search(title):
        return ParagraphTag.AUTHOR_NOTE, ParagraphTag.SKIP

    # Prologue: translate normally (no glossary yet, so it acts as a cold-start chapter)
    if NON_CHAPTER_PATTERNS[ParagraphTag.PROLOGUE].search(title_lower):
        return ParagraphTag.PROLOGUE, ParagraphTag.TRANSLATE

    # Extras: translate but don't extract new terms
    if NON_CHAPTER_PATTERNS[ParagraphTag.EXTRA].search(title_lower):
        return ParagraphTag.EXTRA, ParagraphTag.TRANSLATE_NO_EXTRACT

    # Holiday specials: translate but no term extraction
    if NON_CHAPTER_PATTERNS[ParagraphTag.SPECIAL].search(title):
        return ParagraphTag.SPECIAL, ParagraphTag.TRANSLATE_NO_EXTRACT

    # Default: normal chapter
    return ParagraphTag.CHAPTER, ParagraphTag.TRANSLATE


def split_chapters(text: str) -> list[Chapter]:
    """Split a full novel text into chapters.

    Uses the chapter-title regex to find boundaries, then between boundaries
    collects the body. Anything before the first chapter header becomes a
    preamble (tagged PROLOGUE).
    """
    # Find all chapter header positions
    matches = list(CHAPTER_PATTERN.finditer(text))
    chapters = []
    index = 0

    if not matches:
        # No chapter headers found — treat entire text as one section
        tag, action = classify_paragraph("正文", text)
        return [Chapter(index=1, title="正文", content=text.strip(), tag=tag, action=action)]

    # Preamble (before first chapter header)
    if matches[0].start() > 0:
        preamble = text[:matches[0].start()].strip()
        if preamble:
            tag, action = classify_paragraph("楔子", preamble)
            chapters.append(Chapter(index=0, title="楔子", content=preamble, tag=tag, action=action))
            index = 1

    # Extract chapters between headers
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()

        if not content:
            continue  # Skip empty chapters (e.g. placeholder headers)

        tag, action = classify_paragraph(title, content)
        chapters.append(Chapter(
            index=index + 1,
            title=title,
            content=content,
            tag=tag,
            action=action,
        ))
        index += 1

    return chapters


def merge_chapters(translations: list[str]) -> str:
    """Merge translated chapter strings into a single book file."""
    return "\n\n".join(translations)
