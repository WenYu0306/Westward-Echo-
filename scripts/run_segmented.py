"""Segmented 775-chapter runner: short processes, no sandbox timeout risk.

Each segment = 15 chapters = ~17 minutes. Safe for any environment.
Uses Flash WRITE for bulk chapters, Pro WRITE for sample chapters.
Watchdog stops the run if quality collapses.

Run from your terminal:  python3 scripts/run_segmented.py
Resume from crash:      python3 scripts/run_segmented.py --resume
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.graph import TranslationAgent
from src.chapter_splitter import split_chapters, ParagraphTag
from src.encoding import detect_and_read
from src.circuit_breaker import CircuitBreakerOpenError

SEGMENT_SIZE = 15  # chapters per segment (~17 min, safe for sandbox)
MAX_CHAPTERS = 775
BOOK_ID = "limitless_horror_segmented"
NOVEL_PATH = "tests/fixtures/《无限恐怖》 作者：zhttty.txt"
GENRE = "urban"
CKPT_DIR = "novels/output/limitless_horror_segmented"

# Watchdog: READBACK samples at these chapter indices (1-based position in loop)
SAMPLE_POINTS = frozenset(
    list(range(10, MAX_CHAPTERS, 12)) +  # ~every 12 chapters
    [50, 100, 150, 200, 300, 400, 500, 600, 700, 775]
)


def run_one_segment(start_idx: int, max_chapters: int):
    """Translate up to max_chapters starting from start_idx. Returns True if done."""
    text, enc = detect_and_read(NOVEL_PATH)
    all_chapters = split_chapters(text)
    chapters = [c for c in all_chapters if c.action != ParagraphTag.SKIP][:MAX_CHAPTERS]
    end_idx = min(start_idx + max_chapters, len(chapters))
    total = len(chapters)

    os.makedirs(CKPT_DIR, exist_ok=True)

    # ── Load checkpoint ──
    agent = TranslationAgent(book_id=BOOK_ID)
    prev_summary = ""
    ckpt_file = os.path.join(CKPT_DIR, "_checkpoint.json")
    out_file = os.path.join(CKPT_DIR, "limitless_horror_en.md")

    if os.path.exists(ckpt_file):
        try:
            ckpt = json.load(open(ckpt_file))
            snapshot = ckpt.get("glossary_snapshot", "{}")
            prev_summary = ckpt.get("previous_summary", "")
            agent.load_glossary_snapshot(snapshot)
        except Exception:
            pass

    mode = "a" if os.path.exists(out_file) else "w"
    if mode == "w":
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(f"# 无限恐怖 — Segmented Translation\n\n")

    # ── Translate this segment ──
    t0 = time.monotonic()
    consecutive_empty = 0
    last_two_verdicts = []

    for i in range(start_idx, end_idx):
        ch = chapters[i]
        ch_num = ch.index
        is_sample = (i + 1) in SAMPLE_POINTS
        use_flash_writer = not is_sample  # Flash for bulk, Pro for samples

        try:
            result = agent.translate_chapter(
                chapter_title=ch.title,
                chapter_content=ch.content,
                chapter_number=ch_num,
                previous_summary=prev_summary,
                target_lang="en-US",
                genre=GENRE,
                skip_readback=not is_sample,
                use_flash_writer=use_flash_writer,
            )
        except CircuitBreakerOpenError:
            print(f"  [{i+1}/{total}] CIRCUIT OPEN — skip, sleep 30s")
            time.sleep(30)
            continue
        except Exception as e:
            print(f"  [{i+1}/{total} Ch{ch_num}] ERROR: {e}")
            continue

        tt = result.get("translated_text", "")
        prev_summary = result.get("chapter_summary", "")

        # Check empty
        if not tt or len(tt.strip()) < 50:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                print(f"\n  ABORT: 3 consecutive empty outputs")
                return False
        else:
            consecutive_empty = 0

        # Write chapter
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(f"## Chapter {ch_num}: {ch.title[:60]}\n\n{tt}\n\n---\n\n")

        # Sample verdict check
        fb = result.get("readback_feedback", {})
        tag = "F" if use_flash_writer else "P"
        sample_icon = ""

        if is_sample and fb:
            verdict = fb.get("verdict", "?")
            imp = fb.get("overall_impression", "")[:120]
            sample_icon = f" [{verdict}]"
            last_two_verdicts.append(verdict)
            if len(last_two_verdicts) > 2:
                last_two_verdicts.pop(0)
            print(f"  [{i+1}/{total} Ch{ch_num}]{tag}{sample_icon} {len(tt)}c — {imp}")
            if len(last_two_verdicts) == 2 and all(v == "NEEDS_FIX" for v in last_two_verdicts):
                print(f"\n  ABORT: Watchdog — 2 consecutive NEEDS_FIX")
                return False
        else:
            eta = (time.monotonic() - t0) / max(i - start_idx + 1, 1) * (end_idx - i - 1) / 60
            print(f"  [{i+1}/{total} Ch{ch_num}]{tag}{sample_icon} {len(tt)}c | ETA {eta:.0f}m")

    # ── Save checkpoint ──
    json.dump({
        "last_idx": end_idx - 1,
        "ch_num": chapters[end_idx - 1].index if end_idx > start_idx else 0,
        "glossary_snapshot": agent.exact_store.snapshot(),
        "previous_summary": prev_summary,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, open(ckpt_file, "w"))

    elapsed = (time.monotonic() - t0) / 60
    pct = 100 * end_idx / total
    print(f"  Segment done: {start_idx+1}-{end_idx}/{total} in {elapsed:.0f}m ({pct:.0f}%)")
    print()

    return end_idx >= total  # True = all done


if __name__ == "__main__":
    # Load all chapters to count
    text, enc = detect_and_read(NOVEL_PATH)
    all_chaps = split_chapters(text)
    chaps = [c for c in all_chaps if c.action != ParagraphTag.SKIP][:MAX_CHAPTERS]
    total = len(chaps)

    # Find where we are
    ckpt_file = os.path.join(CKPT_DIR, "_checkpoint.json")
    start = 0
    if os.path.exists(ckpt_file) and "--resume" not in sys.argv:
        try:
            ckpt = json.load(open(ckpt_file))
            start = ckpt.get("last_idx", -1) + 1
            if start >= total:
                print(f"All {total} chapters done! Run again with --force to restart.")
                sys.exit(0)
        except Exception:
            pass

    if "--resume" in sys.argv and os.path.exists(ckpt_file):
        try:
            ckpt = json.load(open(ckpt_file))
            start = ckpt.get("last_idx", -1) + 1
        except Exception:
            pass

    segments = (total - start + SEGMENT_SIZE - 1) // SEGMENT_SIZE
    print(f"无限恐怖: {total} chapters total, starting at ch {start+1}")
    print(f"  {segments} segments of {SEGMENT_SIZE} chapters each")
    print(f"  ~{segments * 17 / 60:.1f} hours remaining\n")

    for seg in range(segments):
        seg_start = start + seg * SEGMENT_SIZE
        remaining = min(SEGMENT_SIZE, total - seg_start)
        print(f"=== Segment {seg+1}/{segments}: chapters {seg_start+1}-{seg_start+remaining} ===")
        done = run_one_segment(seg_start, remaining)
        if done:
            print(f"\nAll {total} chapters complete.")
            break
        if seg < segments - 1:
            print(f"Sleeping 3s before next segment...")
            time.sleep(3)
