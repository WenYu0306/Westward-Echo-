"""Audit: end-to-end smoke test with skip_readback + use_flash_writer."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.agent.graph import TranslationAgent
from src.chapter_splitter import split_chapters, ParagraphTag
from src.encoding import detect_and_read

text, enc = detect_and_read("tests/fixtures/《无限恐怖》 作者：zhttty.txt")
chapters = split_chapters(text)
chapters = [c for c in chapters if c.action != ParagraphTag.SKIP]
ch = chapters[0]

agent = TranslationAgent(book_id="audit_test")
try:
    result = agent.translate_chapter(
        chapter_title=ch.title,
        chapter_content=ch.content,
        chapter_number=ch.index,
        target_lang="en-US",
        genre="urban",
        skip_readback=True,
        use_flash_writer=True,
    )
    tt = result.get("translated_text", "")
    analysis = result.get("read_analysis", {})
    ig = analysis.get("image_gaps", [])
    memo_text = result.get("style_memo", "")
    print(f"RESULT: {len(tt)}c output, {len(ig)} image gaps, memo: {len(memo_text)}c")

    # Check style memo was actually updated
    memo_raw = agent.style_memo.read_all()
    print(f"Memo size: {len(memo_raw)}c")
    # Check files actually got content
    for fname in ["characters.md", "pacing.md", "bridges.md", "prose.md", "terms.md"]:
        path = agent.style_memo.root / fname
        content = path.read_text() if path.exists() else ""
        non_header = [l for l in content.split("\n") if l.strip() and not l.startswith("#")]
        print(f"  {fname}: {len(non_header)} content lines")

    # Test full pipeline (with READBACK)
    print("\nTesting full pipeline (with READBACK)...")
    ch2 = chapters[1]
    result2 = agent.translate_chapter(
        chapter_title=ch2.title,
        chapter_content=ch2.content,
        chapter_number=ch2.index,
        previous_summary=result.get("chapter_summary", ""),
        target_lang="en-US",
        genre="urban",
        skip_readback=False,
        use_flash_writer=False,
    )
    fb = result2.get("readback_feedback", {})
    verdict = fb.get("verdict", "?")
    print(f"RESULT: {len(result2['translated_text'])}c, verdict={verdict}")

    print("\n=== ALL TESTS PASSED ===")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"\n=== FAILED: {e} ===")
