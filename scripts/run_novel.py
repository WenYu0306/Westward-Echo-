"""Translate a full novel — auto-advance, checkpoint-safe, quality-tested.

Usage:  python3 scripts/run_novel.py <novel_key>

Novel keys are defined in the NOVELS dict below.  To add a new novel, add
an entry — no need to copy the script.

Examples:
    python3 scripts/run_novel.py limitless_horror
    python3 scripts/run_novel.py difu
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.agent.graph import TranslationAgent
from src.chapter_splitter import split_chapters, ParagraphTag
from src.encoding import detect_and_read
from src.circuit_breaker import CircuitBreakerOpenError

# ═══════════════════════════════════════════════════════════════
# Novel registry — add new books here
# ═══════════════════════════════════════════════════════════════
NOVELS = {
    "limitless_horror": {
        "name": "无限恐怖",
        "path": "tests/fixtures/《无限恐怖》 作者：zhttty.txt",
        "genre": "urban",
        "book_id": "limitless_horror",
        "output_dir": "novels/output/limitless_horror_segmented",
        "output_file": "limitless_horror_en.md",
        "expected_chapters": 775,
        # Every ~50 chapters + key milestones
        "sample_points_fn": lambda total: (
            frozenset(range(10, min(total, 2300) + 1, 50))
            | {n for n in [25, 75, 100, 200, 300, 400, 500, 600, 700, total]
               if n <= total}
        ),
    },
    "difu": {
        "name": "地府叫我小先生",
        "path": "tests/fixtures/《地府叫我小先生》 作者：界玉.txt",
        "genre": "folk_religion",
        "book_id": "difu_xiao_xiansheng",
        "output_dir": "novels/output/difu_segmented",
        "output_file": "difu_en.md",
        "expected_chapters": 2301,
        "sample_points_fn": lambda total: (
            frozenset(range(10, min(total, 2300) + 1, 50))
            | {n for n in [25, 75, 100, 200, 300, 500, 750, 1000, 1250, 1500, 1750, 2000, total]
               if n <= total}
        ),
    },
    "jianke": {
        "name": "间客",
        "path": "tests/fixtures/《间客》（精校版全本）作者：猫腻.txt",
        "genre": "scifi",
        "book_id": "jianke",
        "output_dir": "novels/output/jianke_segmented",
        "output_file": "jianke_en.md",
        "expected_chapters": 743,
        "sample_points_fn": lambda total: (
            frozenset(range(10, min(total, 2300) + 1, 50))
            | {n for n in [25, 50, 100, 200, 300, 400, 500, 600, 700, total]
               if n <= total}
        ),
    },
    "quanzhi_fanwai": {
        "name": "全职高手番外之巅峰荣耀",
        "path": "tests/fixtures/《全职高手番外之巅峰荣耀》作者：蝴蝶蓝.txt",
        "genre": "urban",
        "book_id": "quanzhi_fanwai",
        "output_dir": "novels/output/quanzhi_fanwai_segmented",
        "output_file": "quanzhi_fanwai_en.md",
        "expected_chapters": 25,
        "sample_points_fn": lambda total: (
            frozenset(range(5, min(total, 50) + 1, 5))
            | {total if total > 0 else 1}
        ),
    },
    "yuannicanlanruyang": {
        "name": "愿你灿烂如阳",
        "path": "/Users/wenyudemac/Downloads/《愿你灿烂如阳》作者：花毛白.txt",
        "genre": "urban",
        "book_id": "yuannicanlanruyang",
        "output_dir": "novels/output/yuannicanlanruyang_segmented",
        "output_file": "yuannicanlanruyang_en.md",
        "expected_chapters": 680,
        "sample_points_fn": lambda total: (
            frozenset(range(3, min(total, 30) + 1, 3))
            | {total if total > 0 else 1}
        ),
    },
}

SEGMENT = 15  # chapters per checkpoint segment


def main():
    do_seed = "--seed" in sys.argv
    # --limit N: compile only the first N chapters (for quick validation runs)
    limit = None
    for i, a in enumerate(sys.argv):
        if a == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a != str(limit)]

    if len(args) < 1:
        print("Usage: python3 scripts/run_novel.py <novel_key> [--seed] [--limit N]")
        print("  --seed  Pre-fill glossary and style memo from Analyze Echo before translating")
        print("  --limit N  Compile only the first N chapters (validation)")
        print("Available novels:")
        for key, cfg in NOVELS.items():
            print(f"  {key:20s} — {cfg['name']} ({cfg['genre']}, ~{cfg.get('expected_chapters', '?')} chapters)")
        sys.exit(1)

    novel_key = args[0]
    if novel_key not in NOVELS:
        print(f"Unknown novel '{novel_key}'. Available: {', '.join(NOVELS.keys())}")
        sys.exit(1)

    cfg = NOVELS[novel_key]

    # ── Seed from Analyze Echo before translation ──
    if do_seed:
        from scripts.seed_from_analyze import (
            load_extraction, seed_glossary, seed_style_memo,
        )
        extraction = load_extraction(novel_key)
        seed_glossary(extraction, cfg["book_id"])
        seed_style_memo(extraction, cfg["book_id"])

    text, enc = detect_and_read(cfg["path"])
    chapters = split_chapters(text)
    chapters = [c for c in chapters if c.action != ParagraphTag.SKIP]
    if limit:
        chapters = chapters[:limit]
    total_chapters = len(chapters)
    expected = cfg.get("expected_chapters")

    if expected and total_chapters != expected:
        print(f"WARNING: source has {total_chapters} chapters, expected {expected}.")
        print("Using actual count. Sample points are clipped to actual chapter range.")

    sample_points = cfg["sample_points_fn"](total_chapters)

    print(f"Source: {cfg['name']} — {total_chapters} chapters, {cfg['genre']}")

    ckpt_file = os.path.join(cfg["output_dir"], "_checkpoint.json")
    out_file = os.path.join(cfg["output_dir"], cfg["output_file"])
    glossary_file = os.path.join(cfg["output_dir"], "_glossary.json")
    quality_file = os.path.join(cfg["output_dir"], "_quality.json")
    os.makedirs(cfg["output_dir"], exist_ok=True)

    quality_log = []
    if os.path.exists(quality_file):
        quality_log = json.load(open(quality_file))

    # Load set of already-written chapters to prevent duplicates on resume
    written_chapters: set[int] = set()
    if os.path.exists(out_file):
        import re as _re
        with open(out_file, encoding="utf-8") as _f:
            for _m in _re.finditer(r"^## Chapter (\d+):", _f.read(), _re.MULTILINE):
                written_chapters.add(int(_m.group(1)))

    agent = TranslationAgent(book_id=cfg["book_id"])

    start = 0
    prev = ""
    if os.path.exists(ckpt_file):
        ckpt = json.load(open(ckpt_file))
        start = ckpt.get("last_idx", -1) + 1
        agent.load_glossary_snapshot(ckpt.get("glossary_snapshot", "{}"))
        prev = ckpt.get("previous_summary", "")
        print(f"Resuming from chapter {start + 1}/{len(chapters)}")

    segment_num = 1
    overall_t0 = time.monotonic()

    import logging
    for name in ["httpx", "httpcore", "openai", "chromadb", "urllib3"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    while start < len(chapters):
        batch_end = min(start + SEGMENT, len(chapters))
        t0 = time.monotonic()
        last_written = start  # track array position of last successfully written chapter

        for i in range(start, batch_end):
            ch = chapters[i]
            ch_num = ch.index
            pos = i + 1
            is_sample = pos in sample_points

            try:
                result = agent.translate_chapter(
                    chapter_title=ch.title,
                    chapter_content=ch.content,
                    chapter_number=ch_num,
                    previous_summary=prev,
                    target_lang="en-US",
                    genre=cfg["genre"],
                    skip_readback=not is_sample,
                    use_flash_writer=not is_sample,
                )
            except CircuitBreakerOpenError:
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] CIRCUIT OPEN - sleep 30s")
                time.sleep(30)
                continue
            except Exception as e:
                err_msg = str(e).lower()
                is_timeout = "timed out" in err_msg or "timeout" in err_msg
                if is_timeout:
                    print(f"  [{pos}/{len(chapters)} Ch{ch_num}] TIMEOUT (attempt 1) - retrying...")
                    time.sleep(3)
                    try:
                        result = agent.translate_chapter(
                            chapter_title=ch.title,
                            chapter_content=ch.content,
                            chapter_number=ch_num,
                            previous_summary=prev,
                            target_lang="en-US",
                            genre=cfg["genre"],
                            skip_readback=not is_sample,
                            use_flash_writer=not is_sample,
                        )
                    except Exception as e2:
                        print(f"  [{pos}/{len(chapters)} Ch{ch_num}] ERROR: {e2} (after retry)")
                        continue
                else:
                    print(f"  [{pos}/{len(chapters)} Ch{ch_num}] ERROR: {e}")
                    continue

            tt = result.get("translated_text", "")
            title_en = result.get("chapter_title_en", "")
            prev = result.get("chapter_summary", "")
            word_count = len(tt.split())
            src_chars = len(ch.content.replace("\n", "").replace(" ", ""))
            min_words = max(50, src_chars / 10)

            ok_flag = "OK"
            if word_count < min_words:
                ok_flag = f"TRUNCATED ({word_count}w < {min_words:.0f}w)"
            elif word_count < 50:
                ok_flag = "EMPTY"

            fb = result.get("readback_feedback", {})
            if is_sample and fb:
                verdict = fb.get("verdict", "?")
                keep = fb.get("would_keep_reading", "?")
                imp = fb.get("overall_impression", "")[:250]
                q = {
                    "ch_position": pos, "ch_number": ch_num,
                    "verdict": verdict, "would_keep_reading": keep,
                    "output_len": len(tt), "impression": imp,
                    "comprehension_issues": len(fb.get("comprehension_issues", [])),
                    "engagement_gaps": len(fb.get("engagement_gaps", [])),
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                quality_log.append(q)
                json.dump(quality_log, open(quality_file, "w"), ensure_ascii=False, indent=2)
                issues = q["comprehension_issues"] + q["engagement_gaps"]
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] {len(tt)}c [P] [{verdict}] keep={keep} issues={issues}")
                if imp:
                    print(f"     {imp}")
            else:
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] {len(tt)}c [F] {ok_flag}")

            exists = os.path.exists(out_file)
            if ch_num not in written_chapters and "TRUNCATED" not in ok_flag and "EMPTY" not in ok_flag:
                with open(out_file, "a" if exists else "w", encoding="utf-8") as f:
                    if not exists:
                        f.write(f"# {cfg['name']} — English Translation\n\n")
                    display_title = title_en or ch.title[:60]
                    f.write(f"## Chapter {ch_num}: {display_title}\n\n{tt}\n\n---\n\n")
                written_chapters.add(ch_num)
                last_written = i
        json.dump({
            "last_idx": last_written,
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
        print(f"  Segment {segment_num}: {start+1}-{batch_end}/{len(chapters)} "
              f"({pct:.0f}%) in {seg_elapsed:.0f}m | {len(glossary)} terms | "
              f"total: {total_elapsed:.0f}m | ETA: {remaining:.0f}m | quality: {len(quality_log)}")
        print()

        start = batch_end
        segment_num += 1
        if batch_end < len(chapters):
            time.sleep(2)

    total_min = (time.monotonic() - overall_t0) / 60
    print(f"ALL DONE: {len(chapters)} chapters in {total_min:.0f}m")
    print(f"Glossary: {len(glossary)} terms | Quality checks: {len(quality_log)}")


if __name__ == "__main__":
    main()
