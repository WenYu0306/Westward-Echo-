"""Translate first 5 chapters of 间客 — real novel, real world test."""
import sys, time, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv; load_dotenv()

from src.encoding import detect_and_read
from src.chapter_splitter import split_chapters
from src.agent.graph import TranslationAgent

FIXTURE = "tests/fixtures/《间客》（精校版全本）作者：猫腻.txt"

print("=" * 60)
print("WESTWARD ECHO — REAL NOVEL TEST: 间客 by 猫腻")
print("=" * 60)

# Phase 0: Encoding detection
print("\n[0] 编码检测...")
text, encoding = detect_and_read(FIXTURE)
print(f"   检测到: {encoding}")

chapters = split_chapters(text)
translatable = [c for c in chapters if c.action.value != "skip"][:5]  # First 5 chapters
total = len(translatable)

print(f"   {len(chapters)} 章总计, 测试前 {total} 章\n")

# Phase 1: Translate
agent = TranslationAgent()
all_results = []
prev_summary = ""

for i, ch in enumerate(translatable):
    chapter_num = ch.index
    print(f"[{i+1}/{total}] 第{chapter_num}章「{ch.title[:40]}」({ch.word_count}字) ... ", end="", flush=True)

    result = agent.translate_chapter(
        chapter_title=ch.title,
        chapter_content=ch.content,
        chapter_number=chapter_num,
        previous_summary=prev_summary,
        target_lang="en-US",
        genre="scifi",  # New genre!
    )

    tt = result["translated_text"]
    score = result.get("quality_score", "N/A")
    new_terms = len(result.get("new_terms_found", []))
    has_json = tt.strip().startswith("{") or '"translated_text"' in tt[:200]

    status = "⚠️ JSON" if has_json else "✅"
    print(f"{len(tt)}字 | +{new_terms}词 | QA:{score} {status}")

    # Show preview
    preview = tt[:150].replace("\n", " ")
    print(f"   📝 {preview}...\n")

    all_results.append(result)
    prev_summary = result.get("chapter_summary", "")
    time.sleep(0.5)

# Phase 2: Summary
full_en = "\n\n".join(r["translated_text"] for r in all_results)
glossary = agent.exact_store.to_dict()

print("=" * 60)
print("RESULTS")
print("=" * 60)
print(f"章节完成: {len(all_results)}/{total}")
json_key = '"translated_text"'
json_residue = sum(1 for r in all_results if r['translated_text'].strip().startswith('{') or json_key in r['translated_text'][:200])
print(f"JSON 残留: {json_residue}")
print(f"术语积累: {len(glossary)} 条")
for cn, en in sorted(glossary.items()):
    print(f"  {cn} → {en}")
print(f"平均评分: {sum(r.get('quality_score', 0) for r in all_results)/len(all_results):.1f}/5.0")

# Save output
out_path = Path(FIXTURE).parent / "jianke_ch1-5_en.md"
out_path.write_text(full_en, encoding="utf-8")
print(f"\n📄 译文保存: {out_path}")
