"""Translate first 5 chapters of 地府叫我小先生 — 出马仙/folk religion test."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv; load_dotenv()

from src.encoding import detect_and_read
from src.chapter_splitter import split_chapters
from src.agent.graph import TranslationAgent
from src.cultural_rules import detect_genre, is_known_genre
from src.dialect import build_dialect_context

FIXTURE = "tests/fixtures/《地府叫我小先生》 作者：界玉.txt"

print("=" * 60)
print("WESTWARD ECHO — 出马仙 TEST: 地府叫我小先生")
print("=" * 60)

text, enc = detect_and_read(FIXTURE)
chapters = split_chapters(text)
translatable = [c for c in chapters if c.action.value != "skip"][:5]
total = len(translatable)

# Auto-detect genre
sample = text[:20000]
detected, conf = detect_genre(sample)
genre = detected if detected else "urban"
known = is_known_genre(genre)

print(f"编码: {enc} | 章节: {len(chapters)}章 | 类型: {genre} (known={known}, confidence={conf})")

# Check dialect
dialect_ctx = build_dialect_context(translatable[0].content)
print(f"方言检测: {'YES' if dialect_ctx else '无方言信号'}")
print(f"发现模式: {'YES (no cultural rules for this genre)' if not known else '标准模式'}")
print()

agent = TranslationAgent()
all_results = []
prev_summary = ""

for i, ch in enumerate(translatable):
    chapter_num = ch.index
    print(f"[{i+1}/{total}] 第{chapter_num}章「{ch.title[:30]}」({ch.word_count}字) ... ", end="", flush=True)

    result = agent.translate_chapter(
        chapter_title=ch.title,
        chapter_content=ch.content,
        chapter_number=chapter_num,
        previous_summary=prev_summary,
        target_lang="en-US",
        genre=genre,
    )

    tt = result["translated_text"]
    score = result.get("quality_score", "N/A")
    new_terms = len(result.get("new_terms_found", []))
    json_key = '"translated_text"'
    has_json = tt.strip().startswith("{") or json_key in tt[:200]

    status = "⚠️ JSON" if has_json else "✅"
    print(f"{len(tt)}字 | +{new_terms}词 | QA:{score} {status}")

    preview = tt[:150].replace("\n", " ")
    print(f"   📝 {preview}...\n")

    all_results.append(result)
    prev_summary = result.get("chapter_summary", "")
    time.sleep(0.5)

# Summary
print("=" * 60)
print("RESULTS")
print("=" * 60)
glossary = agent.exact_store.to_dict()
print(f"章节: {len(all_results)}/{total}")
json_key2 = '"translated_text"'
json_ch = sum(1 for r in all_results if r['translated_text'].strip().startswith("{") or json_key2 in r['translated_text'][:200])
print(f"JSON残留: {json_ch}")
print(f"术语积累: {len(glossary)} 条")
for cn, en in sorted(glossary.items()):
    print(f"  {cn} → {en}")
scores = [r.get('quality_score', 0) for r in all_results if r.get('quality_score', 0) > 0]
avg = sum(scores) / len(scores) if scores else 0
print(f"平均评分: {avg:.1f}/5.0")

# Spot-check critical terms
print()
print("⚠️ 关键术语检查:")
check_terms = {
    "出马": "是否使用了 consistent translation？",
    "仙家": "是否区分了 immortal vs spirit/fairy？",
    "上身": "是否避开了 'possession' (负向)？",
    "香主": "是否建立了专有翻译？",
    "弟马": "是否区分于 'disciple'？",
    "地府": "是否用了 Chinese Underworld 而非 Hell？",
    "鬼差": "是否避开了 'demon/ghost' (负向)？",
}
full_en = "\n\n".join(r["translated_text"] for r in all_results)
for cn_term, question in check_terms.items():
    found = cn_term in text[:30000]
    in_gloss = cn_term in glossary
    print(f"  '{cn_term}': 原文出现={found}, 术语表收录={in_gloss} — {question}")

out_path = Path(FIXTURE).parent / "difu_ch1-5_en.md"
out_path.write_text(full_en, encoding="utf-8")
print(f"\n📄 译文: {out_path}")
