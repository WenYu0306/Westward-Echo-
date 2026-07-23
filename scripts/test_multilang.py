"""Test es-ES and ar-SA translation on 间客 chapter 1."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv; load_dotenv()

from src.encoding import detect_and_read
from src.chapter_splitter import split_chapters
from src.agent.graph import TranslationAgent

FIXTURE = "tests/fixtures/《间客》（精校版全本）作者：猫腻.txt"
text, enc = detect_and_read(FIXTURE)
chapters = split_chapters(text)
translatable = [c for c in chapters if c.action.value != "skip"]
ch = translatable[2]  # Chapter 1 (after 楔子)

agent = TranslationAgent()

for lang, genre, display in [("es-ES", "scifi", "Spanish 🇪🇸"), ("ar-SA", "scifi", "Arabic 🇸🇦")]:
    print(f"\n{'='*50}")
    print(f"{display} — 第{ch.index}章「{ch.title[:30]}」({ch.word_count}字)")
    print(f"{'='*50}")

    result = agent.translate_chapter(
        chapter_title=ch.title,
        chapter_content=ch.content,
        chapter_number=ch.index,
        previous_summary="",
        target_lang=lang,
        genre=genre,
    )

    tt = result["translated_text"]
    score = result.get("quality_score", "N/A")
    terms = len(result.get("new_terms_found", []))

    print(f"  字数: {len(tt)} | 新术语: {terms} | QA: {score}")
    print(f"  前200字: {tt[:200]}")

    out = Path(FIXTURE).parent / f"jianke_ch1_{lang.replace('-','_')}.md"
    out.write_text(tt, encoding="utf-8")
    print(f"  📄 {out}")

    time.sleep(0.5)

print(f"\n✅ 多语种翻译完成")
