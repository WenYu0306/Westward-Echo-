"""A/B test: does the style memo improve translation quality?

Phase 1 (once): Translate chapters 1-194 with memo ON, building up
    translation experience in data/translation_memory/<book_id>/.

Phase 2 (A/B): Translate chapters 195-205 twice:
    Run A — memo disabled (TranslationAgent with book_id=None, fresh memo)
    Run B — memo enabled  (TranslationAgent reusing the Phase 1 book_id)

Both runs use full pipeline (Pro WRITE + READBACK) so cold-read scores
are comparable.  The glossary accumulated in Phase 1 is shared between
both runs to isolate the memo's effect from terminology accumulation.

Output: novels/output/ab_memo_test_*/quality.json with per-chapter verdicts.
"""

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.graph import TranslationAgent
from src.chapter_splitter import ParagraphTag, split_chapters
from src.circuit_breaker import CircuitBreakerOpenError
from src.encoding import detect_and_read

NOVEL_PATH = "tests/fixtures/《无限恐怖》 作者：zhttty.txt"
BOOK_ID = "ab_memo_test"
FIRST_CHAPTER = 1
MEMO_BUILD_END = 194   # chapters to build memo from (exclusive)
TEST_START = 195
TEST_END = 205          # chapters to A/B test (inclusive)
OUT_DIR_A = "novels/output/ab_memo_test_a"   # memo OFF
OUT_DIR_B = "novels/output/ab_memo_test_b"   # memo ON
PHASE1_DIR = "novels/output/ab_memo_test_phase1"


def _suppress_noise():
    for name in ["httpx", "httpcore", "openai", "chromadb", "urllib3"]:
        logging.getLogger(name).setLevel(logging.WARNING)


def _translate_range(agent, chapters, start, end, book_id, out_dir, label):
    """Translate chapters [start, end) (0-indexed) and save quality data."""
    os.makedirs(out_dir, exist_ok=True)
    quality_file = os.path.join(out_dir, "quality.json")
    out_file = os.path.join(out_dir, "translation.md")
    results = []
    prev = ""

    for i in range(start, min(end, len(chapters))):
        ch = chapters[i]
        is_sample = True  # Every chapter gets full pipeline for A/B comparison
        pos = i + 1

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
        except CircuitBreakerOpenError:
            print(f"  [{label}] Ch{ch.index}: CIRCUIT BREAKER OPEN — stopping.")
            break
        except Exception as e:
            print(f"  [{label}] Ch{ch.index}: ERROR — {e}")
            continue

        tt = result.get("translated_text", "")
        prev = result.get("chapter_summary", "")
        fb = result.get("readback_feedback", {})

        verdict = fb.get("verdict", "?")
        keep = fb.get("would_keep_reading")
        score = result.get("quality_score", "?")
        print(f"  [{label}] Ch{pos}/{end - start}: {len(tt)}c [{verdict}] keep={keep}")

        # Append to output file
        exists = os.path.exists(out_file)
        with open(out_file, "a" if exists else "w", encoding="utf-8") as f:
            if not exists:
                f.write(f"# AB Memo Test — {label}\n\n")
            f.write(f"## Chapter {ch.index}: {ch.title[:60]}\n\n{tt}\n\n---\n\n")

        if fb and fb.get("verdict"):
            results.append({
                "ch": ch.index,
                "pos": pos,
                "verdict": verdict,
                "keep_reading": keep,
                "quality_score": score,
                "impression": fb.get("overall_impression", "")[:300],
                "output_len": len(tt),
            })

    with open(quality_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    return results


def main():
    _suppress_noise()
    text, _ = detect_and_read(NOVEL_PATH)
    chapters = split_chapters(text)
    chapters = [c for c in chapters if c.action != ParagraphTag.SKIP]
    total = len(chapters)
    print(f"Loaded {total} chapters from {NOVEL_PATH}")
    print(f"Memo build: chapters 1–{MEMO_BUILD_END - 1}")
    print(f"A/B test:  chapters {TEST_START}–{TEST_END}")

    # ── Phase 1: Build memo (chapters 1–194) ────────────────────────
    memo_agent = TranslationAgent(book_id=BOOK_ID)
    print(f"\n=== PHASE 1: Building style memo (ch 1–{MEMO_BUILD_END - 1}) ===")
    t0 = time.monotonic()
    _translate_range(
        memo_agent, chapters, FIRST_CHAPTER - 1, MEMO_BUILD_END,
        BOOK_ID, PHASE1_DIR, "PHASE1",
    )
    elapsed = (time.monotonic() - t0) / 60
    print(f"Phase 1 done in {elapsed:.1f}m | {len(memo_agent.exact_store)} glossary terms")

    # Save phase 1 glossary snapshot for both A/B runs
    glossary_snapshot = memo_agent.exact_store.snapshot()

    # ── Run A: Memo OFF ─────────────────────────────────────────────
    # Use a fresh book_id so the style memo is empty — but restore the
    # phase 1 glossary so terminology accumulation doesn't confound
    # the comparison.
    agent_a = TranslationAgent(book_id="ab_memo_test_a_nomemo")
    if glossary_snapshot and glossary_snapshot != "{}":
        agent_a.load_glossary_snapshot(glossary_snapshot)

    print(f"\n=== RUN A: Memo OFF (ch {TEST_START}–{TEST_END}) ===")
    t0 = time.monotonic()
    results_a = _translate_range(
        agent_a, chapters, TEST_START - 1, TEST_END,
        "ab_memo_test_a", OUT_DIR_A, "A-NOMEMO",
    )
    elapsed_a = (time.monotonic() - t0) / 60

    # ── Run B: Memo ON ──────────────────────────────────────────────
    # Reuse the Phase 1 agent which has the accumulated style memo + glossary.
    print(f"\n=== RUN B: Memo ON (ch {TEST_START}–{TEST_END}) ===")
    t0 = time.monotonic()
    results_b = _translate_range(
        memo_agent, chapters, TEST_START - 1, TEST_END,
        BOOK_ID, OUT_DIR_B, "B-MEMO",
    )
    elapsed_b = (time.monotonic() - t0) / 60

    # ── Summary ──────────────────────────────────────────────────────
    a_pass = sum(1 for r in results_a if r["verdict"] == "PASS")
    b_pass = sum(1 for r in results_b if r["verdict"] == "PASS")
    a_keep = sum(1 for r in results_a if r.get("keep_reading"))
    b_keep = sum(1 for r in results_b if r.get("keep_reading"))

    print(f"\n=== RESULTS ===")
    print(f"Run A (no memo):  {a_pass}/{len(results_a)} PASS, {a_keep} keep-reading, {elapsed_a:.1f}m")
    print(f"Run B (memo):    {b_pass}/{len(results_b)} PASS, {b_keep} keep-reading, {elapsed_b:.1f}m")

    if len(results_a) == len(results_b) and len(results_a) > 0:
        delta = b_pass - a_pass
        print(f"Memo delta:      {'+' if delta >= 0 else ''}{delta} PASS verdicts")
        if delta > 0:
            print("Style memo IMPROVES quality in this test window.")
        elif delta == 0:
            print("Style memo had NO MEASURABLE EFFECT in this test window.")
        else:
            print("Style memo DEGRADED quality — investigate memo contamination.")

    print(f"\nQuality files:")
    print(f"  Run A: {os.path.join(OUT_DIR_A, 'quality.json')}")
    print(f"  Run B: {os.path.join(OUT_DIR_B, 'quality.json')}")


if __name__ == "__main__":
    main()
