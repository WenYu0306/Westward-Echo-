"""Seed Westward Echo's glossary and style memo from Analyze Echo's extraction.

Pre-process a novel through Analyze Echo first:
    cd "../Analyze Echo（析）"
    python3 main.py run "/path/to/novel.txt" --book-title "Novel Name"

Then seed Westward Echo before translation:
    python3 scripts/seed_from_analyze.py <novel_key>

This populates the ExactGlossary with pre-discovered terms and the StyleMemo
with character, worldbuilding, and pacing hints — so Chapter 1 already knows
about the entire book's terminology and cast.
"""

import sys, os, json, time, re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.glossary.exact_store import ExactGlossary
from src.style_memo import StyleMemoStore

# ── Mapping: Westward Echo novel key → Analyze Echo extraction filename ──
NOVEL_TO_EXTRACTION = {
    "difu":              "地府叫我小先生_extraction.json",
    "limitless_horror":  "无限恐怖_extraction.json",
    "fuhan":             "覆汉_extraction.json",
    "jianke":            "间客_extraction.json",
    "tangchao":          "唐朝工科生_extraction.json",
    "quanzhi":           "全职高手_extraction.json",
    "tunshi_xingkong":   "吞噬星空_extraction.json",
    "tunshi_xingkong2":  "吞噬星空2_extraction.json",
    "kongbuwu":          "_我有一座恐怖屋_作者_我会修空调_extraction.json",
    "quanzhi_fw":        "全职高手番外之巅峰荣耀_extraction.json",
}

ANALYZE_OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "Analyze Echo（析）", "output",
)

ANALYZE_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "Analyze Echo（析）", "data",
)

BATCH_SIZE = 200  # terms per LLM translation batch


def load_extraction(novel_key: str) -> dict:
    """Load Analyze Echo's extraction JSON for a novel."""
    filename = NOVEL_TO_EXTRACTION.get(novel_key)
    if filename is None:
        print(f"Unknown novel key: {novel_key}")
        print(f"Known keys: {list(NOVEL_TO_EXTRACTION.keys())}")
        sys.exit(1)

    path = os.path.join(ANALYZE_OUTPUT, filename)
    if not os.path.exists(path):
        print(f"Extraction file not found: {path}")
        print("Run Analyze Echo on this novel first:")
        print(f'  cd "../Analyze Echo（析）"')
        print(f'  python3 main.py run "<novel.txt>"')
        sys.exit(1)

    with open(path) as f:
        return json.load(f)


def batch_translate_terms(terms: list[dict], book_id: str) -> dict[str, str]:
    """Translate all Chinese terms to English in batches.

    Returns {term_cn: term_en} dictionary.
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

    total = len(terms)
    translated: dict[str, str] = {}

    system = (
        "You are translating Chinese web novel terminology to English. "
        "Each term is a proper noun, cultivation concept, location name, "
        "character name, faction name, or specialized domain term.\n\n"
        "Rules:\n"
        "1. Character names: translate meaningfully, not pinyin. "
        "\"华九难\" → \"Hua Jiunan\" (keep surname + given name). "
        "\"聋婆婆\" → \"Deaf Granny\".\n"
        "2. Cultivation/power terms: use established English xianxia conventions "
        "(\"筑基\" → \"Foundation Establishment\", \"金丹\" → \"Golden Core\").\n"
        "3. Location names: translate meaningfully (\"九道沟\" → \"Nine Paths Gully\").\n"
        "4. Faction names: translate the meaning (\"青云宗\" → \"Azure Cloud Sect\").\n"
        "5. Techniques/skills: translate descriptively.\n"
        "6. Items/artifacts: translate their nature.\n\n"
        "Output ONLY a JSON object: {\"term_cn\": \"term_en\", ...}\n"
        "No preamble. No markdown. No explanations."
    )

    llm = ChatOpenAI(
        model="deepseek-chat",
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.1,
        max_tokens=8192,
        request_timeout=180,
        max_retries=0,
    )

    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch = terms[batch_start:batch_end]

        term_list = []
        for t in batch:
            cn = t.get("term", "")
            if cn and cn not in translated:
                term_list.append(cn)

        if not term_list:
            continue

        user = json.dumps({"terms": term_list}, ensure_ascii=False)
        messages = [SystemMessage(content=system), HumanMessage(content=user)]

        print(f"  Translating terms {batch_start+1}-{batch_end}/{total} "
              f"({len(term_list)} new)...", end=" ", flush=True)

        try:
            resp = llm.invoke(messages)
            content = resp.content.strip()

            # Strip markdown fences
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

            result = json.loads(content)
            if isinstance(result, list):
                # Some models return [{cn: en}, ...] instead of {cn: en}
                for item in result:
                    if isinstance(item, dict):
                        translated.update(item)
            elif isinstance(result, dict):
                translated.update(result)

            new_count = sum(1 for t in term_list if t in result
                          if isinstance(result, dict) or any(
                              isinstance(i, dict) and t in i for i in result
                              if isinstance(result, list)))
            print(f"ok ({len(term_list)} terms)")
        except Exception as e:
            print(f"failed: {e}")
            # Continue with next batch — terms in this batch stay untranslated
            continue

    print(f"  Translated: {len(translated)}/{total} terms")
    return translated


def _seed_sentiment(extraction: dict, memo: StyleMemoStore):
    """Seed pacing.md with per-chapter sentiment data from Analyze Echo.

    Sentiment scores are normalized floats [-1, +1] keyed by paragraph ID
    ({book_id}_{chapter}_{paragraph}). We aggregate by chapter to produce a
    per-chapter emotional tone hint that the READ agent sees automatically
    through read_relevant() — no prompt changes needed.
    """
    sentiment_scores = extraction.get("sentiment_scores", {})
    if not sentiment_scores:
        print("  No sentiment_scores in extraction — skipping.")
        return

    # Aggregate by chapter: {chapter_num: [score, ...]}
    chapter_scores: dict[int, list[float]] = {}
    for para_id, score in sentiment_scores.items():
        parts = para_id.rsplit("_", 2)  # {book}_{chapter}_{paragraph}
        if len(parts) >= 2:
            try:
                ch = int(parts[-2])
                chapter_scores.setdefault(ch, []).append(float(score))
            except (ValueError, IndexError):
                continue

    if not chapter_scores:
        return

    top_chapters = sorted(chapter_scores.items())[:50]  # first 50 chapters
    mood_lines = []
    for ch, scores in top_chapters:
        avg = sum(scores) / len(scores)
        if avg > 0.5:
            tone = "高昂/乐观基调"
        elif avg > 0.15:
            tone = "偏暖"
        elif avg < -0.5:
            tone = "压抑/悲观基调"
        elif avg < -0.15:
            tone = "偏低沉"
        else:
            tone = "中性"
        mood_lines.append(f"Ch{ch}: {tone} (avg={avg:+.2f}, n={len(scores)})")

    if mood_lines:
        memo.record_lesson(
            "pacing",
            "[析情绪数据] Per-chapter sentiment from Analyze Echo: "
            + "; ".join(mood_lines[:15])
            + (f"... +{len(mood_lines)-15} more" if len(mood_lines) > 15 else ""),
            0,
        )

    # Global stats
    stats = extraction.get("stats", {})
    avg = stats.get("sentiment_avg")
    if avg is not None:
        if avg > 0.1:
            gist = "整体偏暖，情感波动在正区间。WRITE 可适当保留中文原文的温情底色。"
        elif avg < -0.1:
            gist = "整体偏冷，压抑和紧张是文本主调。WRITE 不应强行加暖色调。"
        else:
            gist = "情绪分布均衡，冷暖交替。WRITE 跟随原文的情绪节奏即可。"
        memo.record_lesson(
            "pacing",
            f"[析情绪数据] 全书情绪均值={avg:+.4f}。{gist}",
            0,
        )
        print(f"  pacing.md: sentiment data for {len(mood_lines)} chapters")


def _seed_relations(book_id: str, memo: StyleMemoStore):
    """Seed characters.md with relationship data from Analyze Echo's knowledge graph.

    Consumes relations.json produced by Analyze Echo (data/{book_id}/relations.json).
    Extracts two relation types:
      APPEARS_WITH (≥10 co-appearances) → characters.md
      BELONGS_TO (character → faction)   → characters.md
    """
    relations_path = os.path.join(ANALYZE_DATA, book_id, "relations.json")
    if not os.path.exists(relations_path):
        print("  No relations.json found — skipping.")
        return

    try:
        with open(relations_path) as f:
            kg = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  Failed to load relations.json: {exc}")
        return

    relations = kg.get("relations", [])
    if not relations:
        print("  relations.json is empty — skipping.")
        return

    # ── APPEARS_WITH: frequent character-pair co-appearances ──
    appears_with = [r for r in relations
                    if r.get("type") == "APPEARS_WITH" and r.get("frequency", 0) >= 10]
    appears_with.sort(key=lambda r: r.get("frequency", 0), reverse=True)
    seeded_pairs = 0
    for r in appears_with[:20]:
        src = r.get("source", "?")
        tgt = r.get("target", "?")
        freq = r.get("frequency", 0)
        memo.record_lesson(
            "characters",
            f"[析关系] {src} ↔ {tgt} 同场 {freq} 次。对话语气应反映熟悉度。",
            0,
        )
        seeded_pairs += 1

    # ── BELONGS_TO: character → faction affiliation ──
    belongs_to = [r for r in relations if r.get("type") == "BELONGS_TO"]
    seeded_factions = 0
    for r in belongs_to[:15]:
        char = r.get("source", "?")
        faction = r.get("target", "?")
        memo.record_lesson(
            "characters",
            f"[析关系] {char} 属于 [{faction}]。翻译时注意该角色的自称、敬称和"
            f"语气应符合其势力层级。",
            0,
        )
        seeded_factions += 1

    print(f"  characters.md: {seeded_pairs} high-frequency pairs + "
          f"{seeded_factions} faction affiliations")


def seed_glossary(extraction: dict, book_id: str):
    """Pre-fill ExactGlossary with terms discovered by Analyze Echo."""
    terms = extraction.get("terms", [])
    if not terms:
        print("No terms found in extraction.")
        return {}

    print(f"\n=== Step 1: Seed Glossary ({len(terms)} terms) ===")

    # Check how many are already in the glossary (scoped to this book)
    store = ExactGlossary(book_id=book_id)
    store.load_from_db("en-US")
    existing = store.to_dict()
    new_terms = [t for t in terms if t.get("term", "") not in existing]
    print(f"  Already in glossary: {len(terms) - len(new_terms)}")
    print(f"  New terms to translate: {len(new_terms)}")

    if not new_terms:
        print("  All terms already seeded — skipping.")
        return existing

    # Batch translate
    translated = batch_translate_terms(new_terms, book_id)

    # Write to glossary
    added = 0
    for t in new_terms:
        cn = t.get("term", "")
        en = translated.get(cn)
        if en:
            store.add(
                term_cn=cn,
                term_en=en,
                category="character" if len(cn) <= 4 and not any(
                    k in cn for k in ["术","法","功","丹","器","宗","派","境","界","门"]
                ) else "culture",
                context="",
                chapter=0,
                note=f"Pre-seeded from Analyze Echo. Book: {book_id}",
                target_lang="en-US",
            )
            added += 1

    print(f"  Written to glossary: {added} terms")
    return store.to_dict()


def seed_style_memo(extraction: dict, book_id: str):
    """Pre-fill StyleMemo drawers with world knowledge."""
    wb = extraction.get("worldbuilding", {})
    scenes = extraction.get("scenes", [])

    if not wb and not scenes:
        print("No worldbuilding or scenes in extraction — skipping StyleMemo.")
        return

    print(f"\n=== Step 2: Seed StyleMemo ===")
    memo = StyleMemoStore(book_id)

    # ── Terms: key power system and faction names ──
    power_terms = wb.get("power_system", [])
    if power_terms:
        top_power = sorted(power_terms, key=lambda x: x.get("rank", 0))[:10]
        power_str = ", ".join(p["term"] for p in top_power)
        memo.record_lesson(
            "terms",
            f"Power/realm system (ascending): {power_str}",
            0,
        )
        print(f"  terms.md: power system ({len(top_power)} levels)")

    # ── Bridges: key factions ──
    factions = wb.get("factions", [])[:15]
    if factions:
        faction_names = [f["name"] for f in factions if f.get("name")]
        memo.record_lesson(
            "bridges",
            f"Major factions: {', '.join(faction_names)}. "
            f"Each faction name must be translated consistently.",
            0,
        )
        print(f"  bridges.md: {len(faction_names)} factions")

    # ── Bridges: key locations ──
    locations = wb.get("locations", [])
    if locations:
        # Top-level locations (level 0-1)
        top_locs = [loc for loc in locations if loc.get("level", 0) <= 1][:10]
        if top_locs:
            loc_names = [l["name"] for l in top_locs]
            memo.record_lesson(
                "bridges",
                f"Key locations: {', '.join(loc_names)}. "
                f"Translate location names meaningfully — English readers need "
                f"to understand them as places, not opaque labels.",
                0,
            )
            print(f"  bridges.md: {len(loc_names)} locations")

    # ── Pacing: scene rhythm from scene count ──
    if scenes:
        scene_count = len(scenes)
        avg_scenes_per_chapter = scene_count / max(1, extraction.get("stats", {}).get("chapters", 1))
        memo.record_lesson(
            "pacing",
            f"Average {avg_scenes_per_chapter:.1f} scenes per chapter across "
            f"{scene_count} total scenes. Scene boundaries detected by "
            f"Analyze Echo — use these as hints for pacing and paragraph breaks.",
            0,
        )
        # Conflict ratio
        conflict_count = sum(1 for s in scenes if s.get("conflict"))
        if scene_count > 0:
            conflict_ratio = conflict_count / scene_count * 100
            memo.record_lesson(
                "pacing",
                f"~{conflict_ratio:.0f}% of scenes contain conflict. "
                f"WRITE should preserve this ratio — action and calm scenes "
                f"should alternate at roughly this frequency.",
                0,
            )
        print(f"  pacing.md: scene rhythm hints")

    # ── Characters: list from extraction ──
    terms = extraction.get("terms", [])
    chars = [t["term"] for t in terms if len(t.get("term", "")) <= 4
             and not any(k in t.get("term", "") for k in
                         ["术","法","功","丹","器","宗","派","境","界","门",
                          "堂","阵","符","经","典","录","诀","式","道","仙",
                          "鬼","魔","妖","佛","神","圣","王","帝","主"])]
    chars = chars[:20]
    if chars:
        memo.record_lesson(
            "characters",
            f"Key character names found (may include non-characters): "
            f"{', '.join(chars)}. Establish English renderings early and "
            f"use them consistently across all chapters.",
            0,
        )
        print(f"  characters.md: {len(chars)} names")

    print("  StyleMemo seeded.")

    # ── v0.17: Analyze Echo sentiment + knowledge graph ──
    verbose = "--verbose" in sys.argv
    print(f"\n--- v0.17: Analyze Echo data integration ---")

    print(f"  Sentiment...", end=" " if not verbose else "\n")
    _seed_sentiment(extraction, memo)

    print(f"  Relations...", end=" " if not verbose else "\n")
    _seed_relations(book_id, memo)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/seed_from_analyze.py <novel_key>")
        print("Available novels:")
        for key in sorted(NOVEL_TO_EXTRACTION.keys()):
            path = os.path.join(ANALYZE_OUTPUT, NOVEL_TO_EXTRACTION[key])
            status = "✓" if os.path.exists(path) else "✗"
            print(f"  {status} {key}")
        sys.exit(1)

    novel_key = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    print(f"=== Seed Westward Echo from Analyze Echo ===")
    print(f"Novel: {novel_key}")

    # Load extraction
    extraction = load_extraction(novel_key)
    book_id = extraction.get("book_id", novel_key)
    stats = extraction.get("stats", {})
    print(f"Book: {book_id}")
    print(f"Stats: {stats.get('terms', '?')} terms, "
          f"{stats.get('scenes', '?')} scenes, "
          f"{stats.get('chapters', '?')} chapters, "
          f"{stats.get('chars', 0):,} chars")

    if dry_run:
        print("\n--dry-run: would seed glossary + style memo (no writes)")
        return

    # Seed
    glossary = seed_glossary(extraction, book_id)
    seed_style_memo(extraction, book_id)

    print(f"\n=== Seeding complete ===")
    print(f"Glossary: {len(glossary)} terms")
    print(f"StyleMemo: data/translation_memory/{book_id}/")


if __name__ == "__main__":
    main()
