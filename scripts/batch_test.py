"""Batch test — analyze + translate all novels in fixtures directory."""
import sys, time, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv; load_dotenv()

from src.encoding import detect_and_read
from src.chapter_splitter import split_chapters
from src.agent.graph import TranslationAgent
from src.cultural_rules import detect_genre, is_known_genre, list_known_genres
from src.error_tracker import record_event, get_event_summary

FIXTURES = Path("tests/fixtures")

# Find all novel txt files (skip test_novel, pei_zong, difu_en, jianke_en)
novels = sorted([
    f for f in FIXTURES.glob("*.txt")
    if not f.name.startswith("test_novel") and "pei_zong" not in f.name
    and "difu" not in f.name and "jianke" not in f.name
])

print("=" * 60)
print(f"BATCH TEST — {len(novels)} novels")
print("=" * 60)

results = []

for novel_path in novels:
    name = novel_path.stem[:40]
    print(f"\n{'─' * 50}")
    print(f"📖 {name}")
    print(f"{'─' * 50}")

    # Phase 0: Encoding + structure
    try:
        text, enc = detect_and_read(str(novel_path))
    except Exception as e:
        print(f"  ❌ Encoding failed: {e}")
        results.append({"name": name, "status": "encoding_failed", "error": str(e)})
        continue

    chapters = split_chapters(text)
    translatable = [c for c in chapters if c.action.value != "skip"]
    total = len(translatable)
    total_words = sum(c.word_count for c in translatable)

    # Genre detection
    sample = text[:20000]
    detected, conf = detect_genre(sample)
    genre = detected if detected else "urban"
    known = is_known_genre(genre)

    print(f"  编码: {enc} | 章节: {total} | 字数: {total_words}")
    print(f"  类型: {genre} (known={known}, conf={conf})")

    # Translate first 3 chapters
    test_chapters = translatable[:3]
    agent = TranslationAgent()
    prev_summary = ""
    chapter_results = []

    for i, ch in enumerate(test_chapters):
        print(f"  [{i+1}/3] 第{ch.index}章「{ch.title[:30]}」({ch.word_count}字) ... ", end="", flush=True)

        try:
            result = agent.translate_chapter(
                chapter_title=ch.title,
                chapter_content=ch.content,
                chapter_number=ch.index,
                previous_summary=prev_summary,
                target_lang="en-US",
                genre=genre,
            )
            tt = result["translated_text"]
            score = result.get("quality_score", "N/A")
            chapter_results.append({
                "chapter": ch.index, "words_cn": ch.word_count,
                "words_en": len(tt), "score": score,
            })
            print(f"{len(tt)}字 | QA:{score} ✅")
        except Exception as e:
            print(f"❌ {e}")
            chapter_results.append({"chapter": ch.index, "error": str(e)})

        prev_summary = result.get("chapter_summary", "") if 'result' in dir() else ""
        time.sleep(0.3)

    results.append({
        "name": name, "encoding": enc, "genre": genre,
        "detected_genre": detected, "confidence": conf,
        "total_chapters": total, "total_words": total_words,
        "chapters_tested": chapter_results,
        "glossary_size": len(agent.exact_store),
    })

# Summary
print(f"\n{'=' * 60}")
print("BATCH SUMMARY")
print(f"{'=' * 60}")

for r in results:
    status = "✅" if r.get("chapters_tested") else "❌"
    ch = r.get("chapters_tested", [])
    ok = sum(1 for c in ch if "error" not in c)
    fail = sum(1 for c in ch if "error" in c)
    print(f"{status} {r['name'][:40]}: {r['encoding']}, {r['genre']}, {r['total_chapters']}章, {ok}/{len(ch)} ok")

# Error summary
events = get_event_summary(days=1)
print(f"\n📊 错误追踪 (本次):")
for etype, count in sorted(events.items()):
    if etype != "total":
        print(f"  {etype}: {count}")

print(f"\n💾 结果: tests/fixtures/batch_results.json")
json_path = FIXTURES / "batch_results.json"
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
print(f"   {json_path}")
