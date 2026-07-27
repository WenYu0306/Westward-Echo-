"""Backfill cultural_rules.json from a completed book's style memo.

After finishing a novel, run this to extract verified bridges + terms from
the style memo and merge them into cultural_rules.json under the novel's
genre.  Future translations of the same genre inherit these rules.

Usage:
    python3 scripts/backfill_rules.py <book_id> <genre>
    e.g.  python3 scripts/backfill_rules.py limitless_horror_segmented urban
"""
import json, os, sys, re
from datetime import datetime, timezone

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "cultural_rules.json")

KNOWN_GENRES = {"romance_ceo", "xianxia", "urban", "scifi", "folk_religion"}


def load_rules():
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_rules(rules):
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def extract_bridges(memo_dir: str) -> list[str]:
    """Parse bridges.md: only keep cold-reader confusion reports and bridge patterns."""
    bridges_path = os.path.join(memo_dir, "bridges.md")
    if not os.path.exists(bridges_path):
        return []

    text = open(bridges_path, encoding="utf-8").read()
    rules = []
    seen = set()

    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Only keep specific high-signal entries
        lowered = line.lower()
        is_confusion = "confused by" in lowered and "reader confused" in lowered
        is_exposition = "exposition" in lowered and "drag" in lowered

        if not (is_confusion or is_exposition):
            continue

        cleaned = re.sub(r'^\[ch\d+\]\s*', '', line)
        if not cleaned or len(cleaned) < 30:
            continue

        # Dedup: first 80 chars as fingerprint
        fingerprint = cleaned[:80].lower()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)

        # Extract just the actionable guidance
        parts = cleaned.split(". Test:", 1)
        summary = parts[0][:200].strip()
        if summary:
            rules.append(summary)

    # Cap at 100 bridges to keep rules file manageable
    return rules[:100]


def extract_terms(memo_dir: str) -> list[tuple[str, str]]:
    """Parse terms.md: only keep clear CN → EN term mappings."""
    terms_path = os.path.join(memo_dir, "terms.md")
    if not os.path.exists(terms_path):
        return []

    text = open(terms_path, encoding="utf-8").read()
    rules = []
    seen = set()

    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        fingerprint = line[:40].lower()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)

        cleaned = re.sub(r'^\[ch\d+\]\s*', '', line)
        m = re.match(r'(.+?)\s*[→>]\s*(.+?)(?:\s*//\s*(.+))?$', cleaned)
        if m:
            cn = m.group(1).strip()
            en = m.group(2).strip()
            if len(cn) >= 2 and len(en) >= 2:
                rules.append((cn, en))

    return rules[:100]


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/backfill_rules.py <book_id> <genre>")
        sys.exit(1)

    book_id = sys.argv[1]
    genre = sys.argv[2]

    if genre not in KNOWN_GENRES:
        print(f"Unknown genre '{genre}'. Known: {', '.join(KNOWN_GENRES)}")
        sys.exit(1)

    memo_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data/translation_memory", book_id)

    if not os.path.isdir(memo_dir):
        print(f"Memo directory not found: {memo_dir}")
        sys.exit(1)

    bridges = extract_bridges(memo_dir)
    terms = extract_terms(memo_dir)

    rules = load_rules()

    # Ensure genre structure
    if genre not in rules["genres"]:
        rules["genres"][genre] = {}
    if "en-US" not in rules["genres"][genre]:
        rules["genres"][genre]["en-US"] = {}

    genre_rules = rules["genres"][genre]["en-US"]
    before_count = len(genre_rules)
    added = 0

    tag = f"[{book_id}]"

    # Add bridges as build rules
    for b in bridges:
        key = f"_bridge_{abs(hash(b)) % 100000}"
        if key not in genre_rules:
            genre_rules[key] = {
                "target": b[:120],
                "note": f"Bridge pattern from {book_id} style memo",
            }
            added += 1

    # Add terms
    for cn, en in terms:
        if cn == "_note":
            key = f"_term_note_{abs(hash(en)) % 100000}"
            genre_rules[key] = {
                "target": en[:120],
                "note": f"Term note from {book_id} style memo",
            }
            added += 1
        elif cn not in genre_rules:
            genre_rules[cn] = {
                "target": en,
                "note": f"Auto-extracted from {book_id} translation memory",
            }
            added += 1

    save_rules(rules)

    after_count = len(genre_rules)
    print(f"Genre '{genre}': {before_count} → {after_count} rules (+{added})")
    print(f"  Bridges: {len(bridges)} candidates")
    print(f"  Terms: {len(terms)} candidates")
    print(f"  Total file: {len(json.dumps(rules)):,} bytes")


if __name__ == "__main__":
    main()
