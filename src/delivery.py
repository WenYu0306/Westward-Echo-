"""One place that turns a translated chapter into delivered Markdown.

Why this module exists
----------------------
The same delivery loop was written five times: the CLI runner, the synchronous
API (single- and multi-language), and the Celery task (fresh and resumed). Each
copy had its own chapter header format, its own truncation floor, and its own
append logic, so one defect had to be fixed five times — and usually was not.
Measured on 2026-10-26: 8 places appended to a deliverable, 4 copies of the
truncation formula, and the oldest path (Celery) had no guard at all for two
years while the newest copy got one.

Nothing here talks to an LLM. It is pure text handling plus one atomic append,
so every path can share it without dragging in the agent.

The contract
------------
* A chapter block is ``## Chapter N: <ascii title>`` + blank line + body +
  blank line + ``---`` + blank line.
* The title falls back to ``Chapter N`` — never to the Chinese heading. The
  project's zero-Chinese rule has been violated by that fallback before.
* ``is_deliverable`` is the only definition of "good enough to ship":
  non-empty and at least ``max(50, source_chars / 10)`` words.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

CHAPTER_HEAD_RE = re.compile(r"^## Chapter (\d+):.*$", re.MULTILINE)

MIN_WORDS = 50
CHARS_PER_WORD = 10


def chapter_block(number: int, title_en: str, text: str) -> str:
    """Format one chapter. ``title_en`` empty → ``Chapter N``, never Chinese."""
    display = (title_en or "").strip() or f"Chapter {number}"
    return f"## Chapter {number}: {display}\n\n{text}\n\n---\n\n"


def is_deliverable(translated: str, source: str) -> tuple[bool, str]:
    """Return ``(ok, reason)``; ``reason`` is empty when ok.

    The floor is a length sanity check, not a quality judgement: an empty or
    half-finished output must not reach the reader. Dialogue-heavy content can
    legitimately be short relative to the source, which is why the floor is a
    tenth of the source rather than a ratio close to one.
    """
    text = (translated or "").strip()
    if not text:
        return False, "empty output"
    words = len(text.split())
    src_chars = len(source.replace("\n", "").replace(" ", ""))
    floor = max(MIN_WORDS, src_chars / CHARS_PER_WORD)
    if words < floor:
        return False, f"truncated ({words}w < {floor:.0f}w)"
    return True, ""


def split_blocks(text: str) -> tuple[str, list[tuple[int, str]]]:
    """Split a deliverable into ``(preamble, [(chapter_number, block), ...])``.

    The preamble is everything before the first chapter heading (usually the
    ``# Title`` line), preserved verbatim so rewrites never drop it.
    """
    heads = list(CHAPTER_HEAD_RE.finditer(text))
    if not heads:
        return text, []
    preamble = text[:heads[0].start()]
    blocks = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        blocks.append((int(head.group(1)), text[head.start():end]))
    return preamble, blocks


def scan_written(text: str) -> tuple[set[int], dict[int, str]]:
    """Chapter numbers already delivered, and their bodies.

    Numbers prevent a duplicate write; bodies let a repair run hand the next
    chapter a continuity excerpt without re-translating what exists.
    """
    heads = list(CHAPTER_HEAD_RE.finditer(text))
    written: set[int] = set()
    bodies: dict[int, str] = {}
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        number = int(head.group(1))
        written.add(number)
        bodies[number] = text[head.end():end]
    return written, bodies


def append_chapter(path, number: int, title_en: str, text: str,
                   header: str = "") -> None:
    """Append one chapter; create the file (and its H1) if needed.

    ``flush`` + ``fsync`` so a crash cannot lose chapters already reported as
    written. The synchronous API path did this; the CLI copy did not.
    """
    path = Path(path)
    exists = path.exists()
    with path.open("a" if exists else "w", encoding="utf-8") as handle:
        if not exists and header:
            handle.write(f"# {header}\n\n")
        handle.write(chapter_block(number, title_en, text))
        handle.flush()
        os.fsync(handle.fileno())


def replace_chapters(path, replacements: dict[int, str]) -> int:
    """Rewrite the named chapters' bodies in place; return how many changed.

    Used to repair a chapter that exists but is corrupt. Unlike a normal run
    this must not append — the block already has a position in the book.
    """
    path = Path(path)
    preamble, blocks = split_blocks(path.read_text(encoding="utf-8"))
    if not blocks:
        return 0
    out = [preamble]
    replaced = 0
    for number, block in blocks:
        if number in replacements:
            heading = block.split("\n", 1)[0].rstrip()
            out.append(f"{heading}\n\n{replacements[number].strip()}\n\n---\n\n")
            replaced += 1
        else:
            out.append(block)
    if replaced:
        path.write_text("".join(out), encoding="utf-8")
    return replaced


def reorder(path) -> int:
    """Sort chapter blocks ascending; return how many positions changed.

    A repair run appends, so a filled gap lands at the end of the file.
    """
    path = Path(path)
    preamble, blocks = split_blocks(path.read_text(encoding="utf-8"))
    if not blocks:
        return 0
    ordered = sorted(blocks, key=lambda item: item[0])
    moved = sum(1 for (a, _), (b, _) in zip(blocks, ordered) if a != b)
    if moved:
        path.write_text(preamble + "".join(block for _, block in ordered),
                        encoding="utf-8")
    return moved
