"""Re-translate specific chapters that were skipped or truncated."""
import sys, os, json, re, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.agent.graph import TranslationAgent
from src.chapter_splitter import split_chapters, ParagraphTag
from src.encoding import detect_and_read

CHAPTERS_TO_FIX = [1656, 1686, 1741, 1748, 1813, 1818, 1904, 1939, 1969, 1971, 1972, 1988]

cfg = {
    "name": "地府叫我小先生",
    "path": "tests/fixtures/《地府叫我小先生》 作者：界玉.txt",
    "genre": "folk_religion",
    "book_id": "difu_xiao_xiansheng",
    "output_dir": "novels/output/difu_segmented",
    "output_file": "difu_en.md",
}

ckpt_file = os.path.join(cfg["output_dir"], "_checkpoint.json")
out_file = os.path.join(cfg["output_dir"], cfg["output_file"])

text, enc = detect_and_read(cfg["path"])
chapters = split_chapters(text)
chapters = [c for c in chapters if c.action != ParagraphTag.SKIP]
src_by_index = {c.index: c for c in chapters}
total = len(chapters)

ckpt = json.load(open(ckpt_file))
agent = TranslationAgent(book_id=cfg["book_id"])
agent.load_glossary_snapshot(ckpt.get("glossary_snapshot", "{}"))
prev = ckpt.get("previous_summary", "")

import logging
for name in ["httpx", "httpcore", "openai", "chromadb", "urllib3"]:
    logging.getLogger(name).setLevel(logging.WARNING)

for en_idx in CHAPTERS_TO_FIX:
    src_ch = src_by_index.get(en_idx)
    if src_ch is None:
        print(f"Ch{en_idx}: source not found, skipping")
        continue

    # Find position in chapters list
    pos = [i for i, c in enumerate(chapters) if c.index == en_idx]
    if not pos:
        print(f"Ch{en_idx}: not in chapter list, skipping")
        continue
    pos = pos[0]

    ch = chapters[pos]
    print(f"[{pos+1}/{total} Ch{en_idx}] {len(ch.content)} chars ...", end=" ", flush=True)

    try:
        result = agent.translate_chapter(
            chapter_title=ch.title,
            chapter_content=ch.content,
            chapter_number=ch.index,
            previous_summary=prev,
            target_lang="en-US",
            genre=cfg["genre"],
            skip_readback=True,
            use_flash_writer=True,
        )
    except Exception as e:
        print(f"ERROR: {e}")
        continue

    tt = result.get("translated_text", "")
    prev = result.get("chapter_summary", "")
    ok_flag = "OK" if len(tt) >= 100 else "SHORT"

    print(f"{len(tt)}c [{ok_flag}]")

    # Append to output file
    with open(out_file, "a", encoding="utf-8") as f:
        f.write(f"## Chapter {en_idx}: {ch.title[:60]}\n\n{tt}\n\n---\n\n")

print("\nDone.")
