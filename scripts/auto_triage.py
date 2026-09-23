"""Auto-triage glossary using LLM self-review. Only uncertain terms flagged for human.

SAFETY NOTES — read before changing this file:

* The previous version executed at import time. Merely importing it (a test
  collector, an editor indexer, a stray ``python -c "import ..."``) would
  DELETE rows from the production glossary, spend real API credits, and rewrite
  ``status='confirmed'`` in bulk. Everything now lives behind ``main()`` and the
  ``if __name__ == "__main__"`` guard.
* Nothing is destructive by default. Pass ``--apply`` to write; without it the
  script only reports what it *would* do.
* This project abandoned DeepSeek in 2026-09. The old hard-coded
  ``deepseek-v4-flash`` / ``DEEPSEEK_API_KEY`` were dead configuration that
  could only fail. It now uses the configured provider from src.config.
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Categories that are metadata rather than story terms (author names, book
# titles, structural labels, idioms already handled elsewhere...).
METADATA_CATEGORIES = {
    "author_name", "book_title", "title", "work_title", "novel_title",
    "platform_name", "publisher_name", "structural", "volume_label",
    "volume_title", "narrative_structure", "arc_title", "theme",
    "idiom", "slang", "cultural_reference", "literary_work",
    "film_title", "contract_term",
}

BATCH_SIZE = 50

TRIAGE_PROMPT = """Review these Chinese→English web novel glossary entries. For each entry, decide:

KEEP — Translation is correct and appropriate. Mark confirmed.
FIX — Translation has issues. Provide the corrected English.
REJECT — This is not a proper term (generic word, metadata, etc). Should be removed.

Return JSON: {{"decisions": [{{"term_cn": "...", "decision": "KEEP|FIX|REJECT", "fixed_en": "only if FIX"}}]}}

Terms to review:
{terms_json}
"""


def _connect(db_path: str) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise SystemExit(f"glossary database not found: {db_path}")
    return sqlite3.connect(db_path)


def _report_pending(conn) -> int:
    """Print how many metadata-category rows and pending terms exist."""
    placeholders = ",".join("?" * len(METADATA_CATEGORIES))
    meta = conn.execute(
        f"SELECT COUNT(*) FROM exact_glossary WHERE category IN ({placeholders})",
        sorted(METADATA_CATEGORIES),
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM exact_glossary WHERE status = 'pending_review'"
    ).fetchone()[0]
    return meta, pending


def _fetch_batch(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT term_cn, term_en, category, context FROM exact_glossary "
        "WHERE status = 'pending_review' LIMIT ?",
        (BATCH_SIZE,),
    ).fetchall()
    return [
        {"term_cn": r[0], "term_en": r[1], "category": r[2], "context": r[3] or ""}
        for r in rows
    ]


def _call_llm(batch: list[dict]) -> dict:
    """Ask the configured provider to triage one batch.

    Returns the parsed decision envelope. Raises on transport/HTTP failure so
    the caller can abort without having written anything.
    """
    import httpx

    from src.config import LLM_API_KEY, LLM_BASE_URL, QWEN_MODEL

    if not LLM_API_KEY:
        raise SystemExit(
            "LLM_API_KEY is not set — refusing to call a provider. "
            "This script makes a real, billable API call; see the repo rule "
            "that live calls must be announced first."
        )

    prompt = TRIAGE_PROMPT.format(
        terms_json=json.dumps(batch, ensure_ascii=False, indent=2)
    )
    resp = httpx.post(
        f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {LLM_API_KEY}",
                 "Content-Type": "application/json"},
        json={
            "model": QWEN_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 4096,
        },
        timeout=60,
    )
    if not resp.is_success:
        raise RuntimeError(f"LLM triage failed: {resp.status_code} {resp.text[:200]}")

    content = resp.json()["choices"][0]["message"]["content"]
    # Models frequently wrap JSON in a ```json fence; strip it defensively.
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
        content = content.strip()
    return json.loads(content)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="LLM-assisted glossary triage (read-only unless --apply)."
    )
    parser.add_argument("--db", default=str(Path("data") / "checkpoints.db"),
                        help="glossary SQLite path (default: data/checkpoints.db)")
    parser.add_argument("--apply", action="store_true",
                        help="actually write KEEP/FIX/REJECT decisions. Without "
                             "this the script only reports what it would do.")
    parser.add_argument("--no-delete", action="store_true",
                        help="even with --apply, never DELETE rows (REJECT "
                             "becomes a report-only suggestion).")
    args = parser.parse_args(argv)

    conn = _connect(args.db)
    meta_count, pending_count = _report_pending(conn)

    print(f"Glossary: {args.db}")
    print(f"  metadata-category rows (candidates for cleanup): {meta_count}")
    print(f"  pending_review terms: {pending_count}")

    if pending_count == 0:
        total = conn.execute("SELECT COUNT(*) FROM exact_glossary").fetchone()[0]
        confirmed = conn.execute(
            "SELECT COUNT(*) FROM exact_glossary WHERE status='confirmed'"
        ).fetchone()[0]
        print(f"✅ nothing to triage — {total} terms, {confirmed} confirmed")
        conn.close()
        return 0

    if not args.apply:
        # Dry run: do NOT call the API either. Reporting counts is free; the
        # whole point of the default is to spend nothing and change nothing.
        print("\nDRY RUN — no API call, no writes. Pass --apply to triage for real.")
        print("  (an --apply run makes one billable LLM call per "
              f"{BATCH_SIZE}-term batch)")
        conn.close()
        return 0

    # ── live path ────────────────────────────────────────────────────────
    batch = _fetch_batch(conn)
    print(f"\nTriaging {len(batch)} term(s) — this makes a real API call.")
    result = _call_llm(batch)

    kept = fixed = rejected = skipped = 0
    for d in result.get("decisions", []):
        cn = d.get("term_cn")
        decision = d.get("decision")
        if not cn or decision not in ("KEEP", "FIX", "REJECT"):
            skipped += 1
            continue

        if decision == "KEEP":
            conn.execute(
                "UPDATE exact_glossary SET status='confirmed' WHERE term_cn=?", (cn,)
            )
            kept += 1
        elif decision == "FIX":
            fixed_en = d.get("fixed_en")
            if not fixed_en:
                skipped += 1
                continue
            conn.execute(
                "UPDATE exact_glossary SET term_en=?, status='confirmed' WHERE term_cn=?",
                (fixed_en, cn),
            )
            fixed += 1
        elif decision == "REJECT":
            if args.no_delete:
                print(f"  would DELETE (suppressed by --no-delete): {cn}")
                skipped += 1
            else:
                conn.execute("DELETE FROM exact_glossary WHERE term_cn=?", (cn,))
                rejected += 1

    conn.commit()

    remaining = conn.execute(
        "SELECT COUNT(*) FROM exact_glossary WHERE status='pending_review'"
    ).fetchone()[0]
    print(f"\n✅ confirmed: {kept}\n🔧 fixed: {fixed}\n🗑️  deleted: {rejected}")
    if skipped:
        print(f"⏭️  skipped (unparseable/suppressed): {skipped}")
    if remaining:
        print(f"\n⚠️ {remaining} still pending (batch size is {BATCH_SIZE})")

    total = conn.execute("SELECT COUNT(*) FROM exact_glossary").fetchone()[0]
    confirmed = conn.execute(
        "SELECT COUNT(*) FROM exact_glossary WHERE status='confirmed'"
    ).fetchone()[0]
    print(f"\n📊 {total} total | ✅ {confirmed} confirmed | ⚠️ {remaining} pending")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
