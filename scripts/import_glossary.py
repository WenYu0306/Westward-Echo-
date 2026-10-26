#!/usr/bin/env python3.11
"""Load a delivered book's glossary into the exact-match store (SQLite).

Why this exists
---------------
``exact_glossary`` was re-keyed by (book_id, term_cn, target_lang) on
2026-10-25; the pre-isolation table was renamed to ``exact_glossary_legacy``
and **not backfilled** — it has 9,056 rows with no book attribution, so it
cannot be split reliably. As a result the live table held almost nothing
(地府: 1 row) even though the delivered books carry 1,459 / 293 terms in their
own ``_glossary.json``.

The JSON files are per-book and are what the translation runs actually used, so
importing them is exact — no guessing which legacy row belonged to which book.

Status: imported terms default to ``pending_review`` because no human confirmed
them. Pass ``--status confirmed`` only if that is true.

Usage::

    python3.11 scripts/import_glossary.py --all --dry-run
    python3.11 scripts/import_glossary.py --all
    python3.11 scripts/import_glossary.py --book difu
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_novel import NOVELS  # noqa: E402
from src.glossary.exact_store import ExactGlossary  # noqa: E402


def import_book(key: str, status: str, dry_run: bool) -> dict:
    cfg = NOVELS[key]
    path = Path(cfg["output_dir"]) / "_glossary.json"
    if not path.exists():
        return {"book": key, "skipped": f"no {path}"}
    mapping = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict) or not mapping:
        return {"book": key, "skipped": "empty or malformed"}
    store = ExactGlossary(book_id=cfg["book_id"])
    store.load_from_db()
    before = len(store)
    written = 0 if dry_run else store.bulk_upsert(
        mapping, target_lang="en-US", status=status)
    if not dry_run:
        store.load_from_db()
    return {
        "book": key, "book_id": cfg["book_id"],
        "source_terms": len(mapping), "before": before,
        "written": written if not dry_run else len(mapping),
        "after": len(store) if not dry_run else before + len(mapping),
        "dry_run": dry_run,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--book", choices=sorted(NOVELS))
    group.add_argument("--all", action="store_true")
    parser.add_argument("--status", default="pending_review",
                        choices=["pending_review", "confirmed"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    keys = sorted(NOVELS) if args.all else [args.book]
    for key in keys:
        result = import_book(key, args.status, args.dry_run)
        if "skipped" in result:
            print(f"{key:24s} skipped: {result['skipped']}")
            continue
        print(f"{result['book_id']:24s} 源 {result['source_terms']:>5} 条 | "
              f"库内 {result['before']:>4} → {result['after']:>4}"
              f"{'  (dry run)' if result['dry_run'] else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
