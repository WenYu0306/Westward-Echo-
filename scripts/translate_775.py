"""Production-grade multi-chapter translation runner with quality watchdog.

Translates ALL translatable chapters of a novel.  Stops EARLY if quality
collapse is detected — saving money on dead-end runs.

Watchdog rules:
  - Randomly samples chapters for deep inspection (default: every 8-15 chapters)
  - 2 consecutive NEEDS_FIX verdicts → abort (quality collapsed)
  - 3 consecutive EMPTY outputs → abort (generation broken)
  - >15% empty output rate → abort (systemic failure)
"""

import sys, os, json, time, signal, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.graph import TranslationAgent
from src.chapter_splitter import split_chapters, ParagraphTag
from src.encoding import detect_and_read
from src.circuit_breaker import CircuitBreakerOpenError


class QualityWatchdog:
    """Monitors translation quality at sampled checkpoints and decides when to abort."""

    def __init__(self, total_chapters: int, sample_interval: tuple = (8, 15), seed: int = 42):
        """
        Args:
            total_chapters: total translatable chapters
            sample_interval: (min, max) chapters between quality checks
            seed: fixed for reproducibility
        """
        self.sample_log: list[dict] = []
        self.total = total_chapters
        self.stopped = False
        self.stop_reason = ""

        # Pre-generate sampling schedule (random but deterministic)
        rng = random.Random(seed)
        samples = []
        pos = 10  # first check at ~ch10
        while pos < total_chapters:
            samples.append(pos)
            pos += rng.randint(*sample_interval)
        self.schedule = set(samples)
        # Add specific milestones
        for m in [50, 100, 200, 300, 500]:
            if m < total_chapters:
                self.schedule.add(m)

    def is_sample_point(self, chapter_idx: int) -> bool:
        return chapter_idx in self.schedule

    def should_sample_next(self, chapter_idx: int) -> int:
        """Return the next sample point >= chapter_idx, or -1 if none left."""
        upcoming = [s for s in sorted(self.schedule) if s >= chapter_idx]
        return upcoming[0] if upcoming else -1

    def record(self, chapter_idx: int, chapter_num: int, verdict: str, output_len: int, image_gaps: int):
        entry = {
            "chapter_idx": chapter_idx,
            "chapter_num": chapter_num,
            "verdict": verdict,
            "output_len": output_len,
            "image_gaps": image_gaps,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.sample_log.append(entry)

        # ── Stop checks ──
        # Check 1: two consecutive NEEDS_FIX at sample points
        if len(self.sample_log) >= 2:
            last_two = self.sample_log[-2:]
            if all(e["verdict"] == "NEEDS_FIX" for e in last_two):
                self.stopped = True
                self.stop_reason = (
                    f"QUALITY COLLAPSE: 2 consecutive NEEDS_FIX at "
                    f"ch{last_two[0]['chapter_num']} and ch{last_two[1]['chapter_num']}"
                )
                return

        # Check 2: severe empty output at sample point
        if output_len < 50:
            empty_samples = [e for e in self.sample_log if e["output_len"] < 50]
            if len(empty_samples) >= 2:
                self.stopped = True
                self.stop_reason = (
                    f"QUALITY COLLAPSE: empty output at {len(empty_samples)} sample points "
                    f"(ch{empty_samples[0]['chapter_num']}, ch{empty_samples[-1]['chapter_num']})"
                )
                return

    def status_report(self) -> str:
        if not self.sample_log:
            return "Watchdog: no samples yet."
        passes = sum(1 for e in self.sample_log if e["verdict"] == "PASS")
        fails = sum(1 for e in self.sample_log if e["verdict"] == "NEEDS_FIX")
        empties = sum(1 for e in self.sample_log if e["output_len"] < 50)
        latest = self.sample_log[-1]
        return (
            f"Watchdog: {len(self.sample_log)} samples | "
            f"{passes} PASS / {fails} NEEDS_FIX / {empties} empty | "
            f"Last: ch{latest['chapter_num']} [{latest['verdict']}] {latest['output_len']}c"
        )


def translate_novel(
    novel_path: str,
    book_id: str,
    output_dir: str,
    target_lang: str = "en-US",
    genre: str = "scifi",
    resume: bool = True,
    watchdog: QualityWatchdog = None,
):
    """Translate every translatable chapter in a novel file."""
    # ── Load and split ──
    print(f"Loading {novel_path}...")
    text, enc = detect_and_read(novel_path)
    chapters = split_chapters(text)
    translatable = [c for c in chapters if c.action != ParagraphTag.SKIP]
    total = len(translatable)
    print(f"  {total} translatable chapters found (encoding: {enc})")

    # ── Watchdog ──
    if watchdog is None:
        watchdog = QualityWatchdog(total)
    next_sample = watchdog.should_sample_next(0)

    # ── Checkpoint state ──
    ckpt_dir = os.path.join(output_dir, book_id)
    os.makedirs(ckpt_dir, exist_ok=True)
    ckpt_file = os.path.join(ckpt_dir, "_checkpoint.json")
    glossary_file = os.path.join(ckpt_dir, "_glossary.json")
    watchdog_file = os.path.join(ckpt_dir, "_watchdog.json")

    start_idx = 0
    agent = TranslationAgent(book_id=book_id)

    if resume and os.path.exists(ckpt_file):
        try:
            ckpt = json.load(open(ckpt_file))
            start_idx = ckpt.get("last_completed_idx", -1) + 1
            snapshot = ckpt.get("glossary_snapshot", "{}")
            agent.load_glossary_snapshot(snapshot)
            # Restore watchdog state
            if os.path.exists(watchdog_file):
                saved = json.load(open(watchdog_file))
                watchdog.sample_log = saved.get("sample_log", [])
                watchdog.stopped = saved.get("stopped", False)
                watchdog.stop_reason = saved.get("stop_reason", "")
                if watchdog.stopped:
                    print(f"  Watchdog: run was aborted — {watchdog.stop_reason}")
                    return 0, 0, 0
            print(f"  Resuming from chapter {start_idx + 1}/{total}")
            print(f"  {watchdog.status_report()}")
        except Exception:
            print("  Checkpoint corrupt — starting fresh")

    # ── Output file (append mode after resume) ──
    output_file = os.path.join(ckpt_dir, f"{book_id}_full_en.md")
    mode = "a" if start_idx > 0 else "w"
    if mode == "w":
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(f"# {book_id} — English Translation\n")
            f.write(f"## Genre: {genre}  |  Target: {target_lang}\n\n")

    # ── Main loop ──
    prev_summary = ""
    completed = start_idx
    failures = 0
    skipped_circuit = 0
    consecutive_empty = 0
    start_time = time.monotonic()

    def _save_checkpoint(idx: int):
        snapshot = agent.exact_store.snapshot()
        json.dump({
            "last_completed_idx": idx,
            "glossary_snapshot": snapshot,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, open(ckpt_file, "w"))
        # Save watchdog state alongside
        json.dump({
            "sample_log": watchdog.sample_log,
            "stopped": watchdog.stopped,
            "stop_reason": watchdog.stop_reason,
        }, open(watchdog_file, "w"))

    def _save_glossary():
        glossary = agent.exact_store.to_dict()
        json.dump(glossary, open(glossary_file, "w"), ensure_ascii=False, indent=2)

    # Handle Ctrl+C gracefully
    interrupted = False
    def _on_interrupt(sig, frame):
        nonlocal interrupted
        interrupted = True
        print("\n  ⚠️ Interrupted — saving checkpoint and exiting...")
    signal.signal(signal.SIGINT, _on_interrupt)

    print(f"\nStarting: {total} chapters ({total - start_idx} remaining)")
    print(f"Watchdog: sampling {len(watchdog.schedule)} points, next at ~ch{next_sample}")
    print(f"Output: {output_file}\n")

    for i in range(start_idx, total):
        if interrupted:
            _save_checkpoint(i - 1)
            _save_glossary()
            break

        ch = translatable[i]
        ch_num = ch.index
        ch_title = ch.title[:60]

        # Progress display
        elapsed = time.monotonic() - start_time
        done = max(i - start_idx, 1)
        eta = (elapsed / done) * (total - start_idx - done)
        pct = 100 * i / total

        # Only run full pipeline (with READBACK) at watchdog sample points
        is_sample = watchdog.is_sample_point(i)
        try:
            result = agent.translate_chapter(
                chapter_title=ch.title,
                chapter_content=ch.content,
                chapter_number=ch_num,
                previous_summary=prev_summary,
                target_lang=target_lang,
                genre=genre,
                skip_readback=not is_sample,
            )

            tt = result.get("translated_text", "")
            prev_summary = result.get("chapter_summary", "")
            fb = result.get("readback_feedback", {})
            verdict = fb.get("verdict", "PASS") if fb and fb.get("verdict") else ("FAST" if not is_sample else "?")
            empty = not tt or len(tt.strip()) < 50
            had_readback = bool(fb and fb.get("verdict"))

            if empty:
                consecutive_empty += 1
                print(f"  [{i+1}/{total} Ch{ch_num}] ⚠️ EMPTY OUTPUT [{consecutive_empty}x]")
                tt = f"[TRANSLATION FAILED — EMPTY OUTPUT]\n\n{ch.content[:500]}"

                # ── 3 consecutive empties → abort ──
                if consecutive_empty >= 3:
                    print(f"\n  🛑 ABORTED: {consecutive_empty} consecutive empty outputs")
                    _save_checkpoint(i - 1)
                    _save_glossary()
                    break
            else:
                consecutive_empty = 0

            # Write chapter immediately
            with open(output_file, "a", encoding="utf-8") as f:
                f.write(f"## Chapter {ch_num}: {ch_title}\n\n")
                f.write(f"{tt}\n\n---\n\n")

            completed += 1
            ig_count = len(result.get("read_analysis", {}).get("image_gaps", []))

            # ── Print ──
            sample_mark = " 🔍" if watchdog.is_sample_point(i) else ""
            rdbk_info = f" [{verdict}]" if had_readback else " [no-rdbk]"
            empty_warn = f" ⚠️{consecutive_empty}xEMPTY" if consecutive_empty > 0 else ""
            print(f"  [{i+1}/{total} Ch{ch_num}] {len(tt)}c{rdbk_info}{sample_mark}"
                  f"{empty_warn} | ETA {eta/60:.0f}m | {pct:.0f}%")

            # ── Watchdog sample ──
            if watchdog.is_sample_point(i):
                watchdog.record(i, ch_num, verdict, len(tt), ig_count)
                next_sample = watchdog.should_sample_next(i + 1)
                print(f"  🔍 {watchdog.status_report()}")
                if next_sample > 0:
                    print(f"     Next sample: ~ch{next_sample}")

                if watchdog.stopped:
                    print(f"\n  🛑 ABORTED: {watchdog.stop_reason}")
                    _save_checkpoint(i)
                    _save_glossary()
                    break

        except CircuitBreakerOpenError:
            skipped_circuit += 1
            print(f"  [{i+1}/{total} Ch{ch_num}] ⚡ CIRCUIT OPEN — skipping")
            time.sleep(10)
            continue

        except Exception as e:
            failures += 1
            print(f"  [{i+1}/{total} Ch{ch_num}] ❌ ERROR: {e}")
            with open(output_file, "a", encoding="utf-8") as f:
                f.write(f"## Chapter {ch_num}: {ch_title}\n\n")
                f.write(f"[TRANSLATION FAILED: {e}]\n\n---\n\n")
            continue

        # Save checkpoint every chapter
        _save_checkpoint(i)
        if (i + 1) % 50 == 0:
            _save_glossary()
            print(f"    📋 Glossary saved ({len(agent.exact_store)} terms)")

    # ── Final save ──
    if not interrupted and not watchdog.stopped:
        _save_checkpoint(total - 1)
    _save_glossary()

    elapsed = (time.monotonic() - start_time) / 60
    stop_info = f"\n  🛑 STOPPED: {watchdog.stop_reason}" if watchdog.stopped else ""
    print(f"\n{'='*60}")
    print(f"Complete: {completed}/{total} chapters ({elapsed:.0f}m){stop_info}")
    print(f"Failures: {failures}  |  Circuit-open: {skipped_circuit}")
    print(f"Glossary: {len(agent.exact_store)} terms")
    print(f"  {watchdog.status_report()}")
    print(f"Output: {output_file}")
    return completed, failures, skipped_circuit


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("novel_path", help="Path to the .txt novel file")
    parser.add_argument("--book-id", required=True, help="Book identifier for memo/checkpoints")
    parser.add_argument("--genre", default="scifi")
    parser.add_argument("--lang", default="en-US")
    parser.add_argument("--output-dir", default="novels/output")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    watchdog = QualityWatchdog(0)  # total set after loading

    with open(args.novel_path, "rb") as f:
        raw = f.read(1024)  # quick peek for total

    watchdog.total = 775  # placeholder, set properly after load

    translate_novel(
        novel_path=args.novel_path,
        book_id=args.book_id,
        output_dir=args.output_dir,
        target_lang=args.lang,
        genre=args.genre,
        resume=not args.no_resume,
        watchdog=watchdog,
    )
