"""Translate all 775 chapters — auto-advance, checkpoint-safe, quality-tested.

Run from your terminal:  python3 scripts/run_one_segment.py

Translation runs continuously. At sample points (every ~50 chapters + key
milestones), the full pipeline runs (Pro WRITE + READBACK cold reader), and
the cold reader's verdict is saved to _quality.json. You can check it anytime:
  cat novels/output/limitless_horror_segmented/_quality.json
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.agent.graph import TranslationAgent
from src.chapter_splitter import split_chapters, ParagraphTag
from src.encoding import detect_and_read
from src.circuit_breaker import CircuitBreakerOpenError

CKPT_DIR = "novels/output/limitless_horror_segmented"
BOOK_ID = "limitless_horror_segmented"
NOVEL_PATH = "tests/fixtures/《无限恐怖》 作者：zhttty.txt"
SEGMENT = 15

# Sample points for quality checks (1-based position in the translatable list)
SAMPLE_POINTS = frozenset(range(10, 780, 50)) | frozenset({25, 75, 100, 200, 300, 400, 500, 600, 700, 775})


def main():
    text, enc = detect_and_read(NOVEL_PATH)
    chapters = split_chapters(text)
    chapters = [c for c in chapters if c.action != ParagraphTag.SKIP][:775]

    ckpt_file = os.path.join(CKPT_DIR, "_checkpoint.json")
    out_file = os.path.join(CKPT_DIR, "limitless_horror_en.md")
    glossary_file = os.path.join(CKPT_DIR, "_glossary.json")
    quality_file = os.path.join(CKPT_DIR, "_quality.json")
    os.makedirs(CKPT_DIR, exist_ok=True)

    # Load existing quality log
    quality_log = []
    if os.path.exists(quality_file):
        quality_log = json.load(open(quality_file))

    # --- Checkpoint resume ---
    agent = TranslationAgent(book_id=BOOK_ID)

    start = 0
    prev = ""
    if os.path.exists(ckpt_file):
        ckpt = json.load(open(ckpt_file))
        start = ckpt.get("last_idx", -1) + 1
        snapshot = ckpt.get("glossary_snapshot", "{}")
        prev = ckpt.get("previous_summary", "")
        agent.load_glossary_snapshot(snapshot)
        print(f"Resuming from chapter {start + 1}/{len(chapters)}")

    segment_num = 1
    overall_t0 = time.monotonic()

    # --- Turn off HTTP noise ---
    import logging
    for name in ["httpx", "httpcore", "openai", "chromadb", "urllib3"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    while start < len(chapters):
        batch_end = min(start + SEGMENT, len(chapters))
        t0 = time.monotonic()

        for i in range(start, batch_end):
            ch = chapters[i]
            ch_num = ch.index
            pos = i + 1  # 1-based chapter position
            is_sample = pos in SAMPLE_POINTS

            # At sample points: full pipeline (Pro WRITE + READBACK cold reader)
            # Ordinary chapters: fast mode (Flash WRITE, no READBACK)
            try:
                result = agent.translate_chapter(
                    chapter_title=ch.title,
                    chapter_content=ch.content,
                    chapter_number=ch_num,
                    previous_summary=prev,
                    target_lang="en-US",
                    genre="urban",
                    skip_readback=not is_sample,
                    use_flash_writer=not is_sample,
                )
            except CircuitBreakerOpenError:
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] CIRCUIT OPEN - sleep 30s")
                time.sleep(30)
                continue
            except Exception as e:
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] ERROR: {e}")
                continue

            tt = result.get("translated_text", "")
            prev = result.get("chapter_summary", "")
            ok_flag = "OK" if len(tt) >= 50 else "EMPTY"

            # --- Quality logging at sample points ---
            fb = result.get("readback_feedback", {})
            if is_sample and fb:
                verdict = fb.get("verdict", "?")
                keep = fb.get("would_keep_reading", "?")
                imp = fb.get("overall_impression", "")[:250]
                q = {
                    "ch_position": pos,
                    "ch_number": ch_num,
                    "verdict": verdict,
                    "would_keep_reading": keep,
                    "output_len": len(tt),
                    "impression": imp,
                    "comprehension_issues": len(fb.get("comprehension_issues", [])),
                    "engagement_gaps": len(fb.get("engagement_gaps", [])),
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                quality_log.append(q)
                json.dump(quality_log, open(quality_file, "w"), ensure_ascii=False, indent=2)
                model_tag = "[P]"  # Pro
                issues = q["comprehension_issues"] + q["engagement_gaps"]
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] {len(tt)}c {model_tag} 🔍 [{verdict}] keep={keep} issues={issues}")
                if imp:
                    print(f"     {imp}")
            else:
                model_tag = "[F]"  # Flash
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] {len(tt)}c {model_tag} {ok_flag}")

            exists = os.path.exists(out_file)
            with open(out_file, "a" if exists else "w", encoding="utf-8") as f:
                if not exists:
                    f.write("# 无限恐怖 — English Translation\n\n")
                f.write(f"## Chapter {ch_num}: {ch.title[:60]}\n\n{tt}\n\n---\n\n")

        # --- Checkpoint ---
        json.dump({
            "last_idx": batch_end - 1,
            "glossary_snapshot": agent.exact_store.snapshot(),
            "previous_summary": prev,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, open(ckpt_file, "w"))

        glossary = agent.exact_store.to_dict()
        json.dump(glossary, open(glossary_file, "w"), ensure_ascii=False, indent=2)

        seg_elapsed = (time.monotonic() - t0) / 60
        pct = 100 * batch_end / len(chapters)
        total_elapsed = (time.monotonic() - overall_t0) / 60
        remaining = total_elapsed / batch_end * (len(chapters) - batch_end)
        sample_count = len(quality_log)
        print(f"  Segment {segment_num}: {start+1}-{batch_end}/{len(chapters)} "
              f"({pct:.0f}%) in {seg_elapsed:.0f}m | {len(glossary)} terms | "
              f"total: {total_elapsed:.0f}m | ETA: {remaining:.0f}m | quality: {sample_count}")
        print()

        start = batch_end
        segment_num += 1
        if batch_end < len(chapters):
            time.sleep(2)

    total_min = (time.monotonic() - overall_t0) / 60
    print(f"ALL DONE: {len(chapters)} chapters in {total_min:.0f}m")
    print(f"Glossary: {len(glossary)} terms | Quality checks: {len(quality_log)}")
    print(f"Output: {out_file}")
    print(f"Quality: {quality_file}")


if __name__ == "__main__":
    main()
