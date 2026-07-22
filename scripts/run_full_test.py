"""Full pipeline test — translate all 3 chapters of the test novel, verify results."""

import json, sys, time, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv; load_dotenv()

from src.chapter_splitter import split_chapters, ParagraphTag
from src.agent.graph import TranslationAgent

FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "pei_zong_ch1-3.txt"

def main():
    text = FIXTURE.read_text(encoding="utf-8")
    chapters = split_chapters(text)
    translatable = [c for c in chapters if c.action != ParagraphTag.SKIP]

    print(f"📖 共 {len(translatable)} 章待翻译\n")

    agent = TranslationAgent()
    all_text = []
    prev_summary = ""

    for ch in translatable:
        print(f"⏳ 第 {ch.index} 章「{ch.title[:30]}」({ch.word_count} 字) ...", flush=True)

        result = agent.translate_chapter(
            chapter_title=ch.title,
            chapter_content=ch.content,
            chapter_number=ch.index,
            previous_summary=prev_summary,
            target_lang="en-US",
        )

        # Show first ~300 chars of translation
        tt = result["translated_text"]
        preview = tt[:300].replace("\n", " ")

        print(f"   ✅ {len(tt)} 字符 | 新术语: {len(result.get('new_terms_found',[]))} | 评分: {result.get('quality_score','N/A')}")
        print(f"   📝 {preview}...")
        print()

        all_text.append(tt)
        prev_summary = result.get("chapter_summary", "")
        time.sleep(0.5)

    # ── Summary ──
    full = "\n\n".join(all_text)
    glossary = agent.exact_store.to_dict()

    print("═" * 50)
    print(f"✅ 翻译完成")
    print(f"   总字数: {len(full)} 字符")
    print(f"   术语表: {len(glossary)} 条")
    for cn, en in sorted(glossary.items()):
        print(f"     {cn} → {en}")

    # Save full output
    out = FIXTURE.parent / "pei_zong_ch1-3_en.md"
    out.write_text(full, encoding="utf-8")
    print(f"\n📄 译文已保存: {out}")

    # Save glossary
    gloss_out = FIXTURE.parent / "pei_zong_glossary.json"
    gloss_out.write_text(json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📄 术语表已保存: {gloss_out}")


if __name__ == "__main__":
    main()
