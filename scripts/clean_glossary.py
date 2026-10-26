#!/usr/bin/env python3.11
"""Clean malformed keys out of a book's glossary.

Why this exists
---------------
The glossary is the exact-match table injected into every READ prompt. It
accumulates keys the model invented alongside real ones. Measured on 《地府》
(1467 entries): three keys contained slashes or parentheticals, and two of
those never occur in the source at all — ``犬封恶鬼/犬尼`` and
``少将军 (addressing Chen Daji)``. They cannot match anything, they bloat the
prompt, and they make the term report look worse than the book is.

What this does
--------------
Conservative by construction — a glossary key is only removed when there is
evidence it is garbage:

1. The key is split on ``/`` and stripped of parentheticals; only forms that
   occur in the source are kept. If none survive, the key could never match
   anything → drop it (invented).
2. If every surviving form is already in the glossary, the composite key adds
   nothing (it can never match, since the whole key is absent from the source)
   → drop it (redundant).
3. Anything else is **reported, not changed**. Routing ``崔珏 / 崔府君`` (one
   English rendering covering two names) requires deciding which English belongs
   to which name; guessing would silently rewrite the glossary.

A key whose stripped form already exists with a different English rendering is
never overwritten — that would create one Chinese term with two English
renderings, the one error a reader can actually see.

Deliberately NOT touched: keys that occur in the source. Two spellings of the
same name (白无畏 / 白无谓) are the author's own variation; both map to one
English rendering, so the delivered text is already consistent.

Usage::

    python3.11 scripts/clean_glossary.py --book difu            # dry run
    python3.11 scripts/clean_glossary.py --book difu --write    # apply
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_novel import NOVELS  # noqa: E402
from src.config import CHECKPOINT_DB_PATH  # noqa: E402
from src.db_backup import backup_sqlite  # noqa: E402
from src.text_io import detect_and_read  # noqa: E402

_PARENTHETICAL = re.compile(r"[（(][^）)]*[）)]")
_SUSPECT = re.compile(r"[/()（）]")


def candidate_keys(key: str, source: str) -> list[str]:
    """Split/strip a malformed key into forms that occur in the source.

    Returns an empty list when nothing usable survives — the key matched
    nothing in the source, so it was invented.
    """
    out: list[str] = []
    for part in key.split("/"):
        stripped = _PARENTHETICAL.sub("", part).strip()
        if stripped and stripped in source and stripped not in out:
            out.append(stripped)
    return out


def clean(glossary: dict, source: str) -> tuple[dict, list[dict], list[dict]]:
    """Return ``(cleaned, changed, review)``; does not mutate the input."""
    cleaned = dict(glossary)
    changed: list[dict] = []
    review: list[dict] = []
    for key in [k for k in glossary if _SUSPECT.search(k)]:
        candidates = candidate_keys(key, source)
        if not candidates:
            cleaned.pop(key, None)
            changed.append({"key": key, "en": glossary[key],
                            "action": "dropped", "why": "源文从未出现"})
            continue
        if all(c in glossary for c in candidates):
            cleaned.pop(key, None)
            changed.append({"key": key, "en": glossary[key],
                            "action": "dropped", "why": "拆开后各项已存在"})
            continue
        review.append({
            "key": key, "en": glossary[key], "candidates": candidates,
            "existing": {c: glossary[c] for c in candidates if c in glossary},
        })
    return cleaned, changed, review


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--book", required=True, choices=sorted(NOVELS))
    parser.add_argument("--write", action="store_true",
                        help="apply the changes (a .bak is written first)")
    args = parser.parse_args(argv)

    cfg = NOVELS[args.book]
    glossary_path = Path(cfg["output_dir"]) / "_glossary.json"
    if not glossary_path.exists():
        raise SystemExit(f"no glossary at {glossary_path}")
    glossary = json.loads(glossary_path.read_text(encoding="utf-8"))
    source, _ = detect_and_read(cfg["path"])

    cleaned, changed, review = clean(glossary, source)

    print(f"{args.book}: {len(glossary)} 条 → {len(cleaned)} 条")
    for item in changed:
        print(f"  ✗ 删除 {item['key']!r} → {item['en']!r}（{item['why']}）")
    for item in review:
        detail = ", ".join(f"{c}={e!r}" for c, e in item["existing"].items())
        print(f"  ⚠ 需人工判断 {item['key']!r} → {item['en']!r}"
              f"；拆开为 {item['candidates']}"
              + (f"（其中已存在：{detail}）" if detail else ""))

    if not args.write:
        print("\n（dry run — 加 --write 才会写入）")
        return 0
    if not changed:
        print("\n无需修改")
    else:
        backup = glossary_path.with_name(
            glossary_path.name + f".bak_clean_{datetime.now():%Y%m%d}")
        shutil.copy2(glossary_path, backup)
        glossary_path.write_text(
            json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写入 {glossary_path}\n备份 {backup}")

    # The checkpoint holds the snapshot a resumed run actually loads. Cleaning
    # only _glossary.json would leave the dirty keys in every future repair run.
    ckpt_path = Path(cfg["output_dir"]) / "_checkpoint.json"
    if ckpt_path.exists():
        ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
        snapshot = json.loads(ckpt.get("glossary_snapshot") or "{}")
        snap_clean, snap_changed, _ = clean(snapshot, source)
        if snap_changed:
            ckpt_backup = ckpt_path.with_name(
                ckpt_path.name + f".bak_clean_{datetime.now():%Y%m%d}")
            shutil.copy2(ckpt_path, ckpt_backup)
            ckpt["glossary_snapshot"] = json.dumps(
                snap_clean, ensure_ascii=False)
            ckpt_path.write_text(
                json.dumps(ckpt, ensure_ascii=False), encoding="utf-8")
            print(f"已同步 checkpoint 快照：{len(snapshot)} 条 → {len(snap_clean)} 条"
                  f"（备份 {ckpt_backup}）")
        else:
            print("checkpoint 快照无需修改")

    # Mirror the removals into SQLite **without deleting rows**: the term is
    # flagged rejected, so it stops being injected but stays auditable and
    # reversible. A hard delete would be the one action nobody can undo.
    dropped = [item["key"] for item in changed]
    if dropped:
        backup = backup_sqlite(CHECKPOINT_DB_PATH)
        with sqlite3.connect(CHECKPOINT_DB_PATH) as conn:
            conn.executemany(
                "UPDATE exact_glossary SET status='rejected' "
                "WHERE book_id=? AND term_cn=?",
                [(cfg["book_id"], key) for key in dropped])
            conn.commit()
        print(f"库内已标记 rejected：{len(dropped)} 条"
              + (f"（备份 {backup.name}）" if backup else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
