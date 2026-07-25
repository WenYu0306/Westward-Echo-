"""Translate 15 chapters of 无限恐怖 with full quality pipeline and cold read."""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.agent.graph import TranslationAgent
from src.chapter_splitter import split_chapters, ParagraphTag
from src.encoding import detect_and_read
from src.circuit_breaker import CircuitBreakerOpenError

OUT_DIR = "novels/output/audit_15"
N = 15

text, enc = detect_and_read("tests/fixtures/《无限恐怖》 作者：zhttty.txt")
chapters = split_chapters(text)
chapters = [c for c in chapters if c.action != ParagraphTag.SKIP][:N]

os.makedirs(OUT_DIR, exist_ok=True)
out_file = os.path.join(OUT_DIR, "translation.md")
quality_file = os.path.join(OUT_DIR, "quality.json")

agent = TranslationAgent(book_id="audit_15")
prev = ""
results = []

import logging
for name in ["httpx", "httpcore", "openai", "chromadb", "urllib3"]:
    logging.getLogger(name).setLevel(logging.WARNING)

t0 = time.monotonic()

for i, ch in enumerate(chapters):
    is_sample = (i+1) in {1, 5, 10, 15}  # key checkpoints
    print(f"[{i+1}/{N}] Ch{ch.index} ({ch.word_count}c)...", end=" ", flush=True)

    try:
        result = agent.translate_chapter(
            chapter_title=ch.title,
            chapter_content=ch.content,
            chapter_number=ch.index,
            previous_summary=prev,
            target_lang="en-US",
            genre="urban",
            skip_readback=not is_sample,
            use_flash_writer=not is_sample,
        )
    except Exception as e:
        print(f"ERROR: {e}")
        continue

    tt = result.get("translated_text", "")
    prev = result.get("chapter_summary", "")
    fb = result.get("readback_feedback", {})

    tag = "P" if is_sample else "F"
    v = fb.get("verdict", "?") if fb else "?"
    print(f"{len(tt)}c {tag} [{v}]")

    exists = os.path.exists(out_file)
    with open(out_file, "a" if exists else "w", encoding="utf-8") as f:
        if not exists:
            f.write("# 无限恐怖 — Audit Translation (15 chapters)\n\n")
        f.write(f"## Chapter {ch.index}: {ch.title[:60]}\n\n{tt}\n\n---\n\n")

    if fb and fb.get("verdict"):
        results.append({
            "ch": ch.index, "verdict": fb["verdict"],
            "keep": fb.get("would_keep_reading"),
            "impression": fb.get("overall_impression", "")[:250],
            "output_len": len(tt),
        })
        json.dump(results, open(quality_file, "w"), ensure_ascii=False, indent=2)

elapsed = (time.monotonic() - t0) / 60
print(f"\nDone: {N} chapters in {elapsed:.0f}m | {len(agent.exact_store)} terms")
print(f"Output: {out_file}")
print(f"Quality: {quality_file}")
for r in results:
    print(f"  Ch{r['ch']}: [{r['verdict']}] keep={r['keep']} — {r['impression'][:120]}")
