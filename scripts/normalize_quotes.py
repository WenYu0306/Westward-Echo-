#!/usr/bin/env python3.11
"""Unify quotation marks in a delivered book to the dominant (straight) style.

Why this exists
---------------
The scanner found 298 chapters using the minority style, and 47 chapters mixing
both inside one chapter — the one form of the inconsistency a reader actually
notices, because it appears in the same paragraph.

Straight quotes dominate every book produced so far, so the conversion is a
one-way, mechanical mapping. It changes punctuation only: no wording, no
paragraph breaks, no markdown structure.

Safety
------
* Dry run by default; ``--write`` is required to touch the file.
* A ``.bak_quotes_<date>`` copy is written first.
* Only the four curly characters are mapped; everything else is untouched.

Usage::

    python3.11 scripts/normalize_quotes.py --book difu
    python3.11 scripts/normalize_quotes.py --book difu --write
    python3.11 scripts/normalize_quotes.py --file path/to/book_en.md --write
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_novel import NOVELS  # noqa: E402
from src.delivery import split_blocks  # noqa: E402

CURLY = "“”‘’"
TO_STRAIGHT = {ord("“"): '"', ord("”"): '"',
               ord("‘"): "'", ord("’"): "'"}


def count_curly(text: str) -> int:
    return sum(text.count(ch) for ch in CURLY)


def normalize(text: str) -> str:
    return text.translate(TO_STRAIGHT)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--book", choices=sorted(NOVELS))
    group.add_argument("--file", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    if args.file:
        path = args.file
    else:
        cfg = NOVELS[args.book]
        path = Path(cfg["output_dir"]) / cfg["output_file"]
    if not path.exists():
        raise SystemExit(f"not found: {path}")

    text = path.read_text(encoding="utf-8")
    total = count_curly(text)
    if not total:
        print(f"{path.name}: 无弯引号，无需修改")
        return 0

    preamble, blocks = split_blocks(text)
    per_chapter = sorted(((n, count_curly(b)) for n, b in blocks),
                         key=lambda item: -item[1])
    affected = [n for n, c in per_chapter if c]
    print(f"{path.name}: {total} 个弯引号，分布在 {len(affected)} 章")
    print("  最多的 8 章:", [(n, c) for n, c in per_chapter[:8] if c])

    if not args.write:
        print("\n（dry run — 加 --write 才会写入）")
        return 0

    backup = path.with_name(f"{path.name}.bak_quotes_{datetime.now():%Y%m%d}")
    shutil.copy2(path, backup)
    path.write_text(normalize(text), encoding="utf-8")
    print(f"\n已写入 {path}\n备份 {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
