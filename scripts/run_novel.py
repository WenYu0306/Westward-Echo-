"""Translate a full novel — auto-advance, checkpoint-safe, quality-tested.

Usage:  python3 scripts/run_novel.py <novel_key>

Novel keys are defined in the NOVELS dict below.  To add a new novel, add
an entry — no need to copy the script.

Examples:
    python3 scripts/run_novel.py limitless_horror
    python3 scripts/run_novel.py difu
"""
import sys, os, time, json, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.agent.graph import TranslationAgent
from src.chapter_splitter import split_chapters, ParagraphTag
from src.text_io import detect_and_read
from src.circuit_breaker import CircuitBreakerOpenError
from src.cost_gate import describe_tiers
from src import token_ledger

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

_CHAPTER_HEAD_RE = re.compile(r"^## Chapter (\d+):.*$", re.MULTILINE)


def scan_delivered(text: str) -> tuple[set[int], dict[int, str]]:
    """Chapter numbers and bodies already present in the deliverable.

    Both are needed: numbers prevent a duplicate write, bodies give a repair run
    a continuity excerpt for the chapter after a gap without re-translating what
    already exists.
    """
    heads = list(_CHAPTER_HEAD_RE.finditer(text))
    written: set[int] = set()
    bodies: dict[int, str] = {}
    for i, match in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        number = int(match.group(1))
        written.add(number)
        bodies[number] = text[match.end():end]
    return written, bodies


def reorder_deliverable(path) -> int:
    """Rewrite the deliverable with chapter blocks in ascending order.

    The runner appends, so a repair run writes a filled gap at the end of the
    file. Without this the book would read 1…775, 243. Returns the number of
    blocks moved.
    """
    text = open(path, encoding="utf-8").read()
    heads = list(_CHAPTER_HEAD_RE.finditer(text))
    if not heads:
        return 0
    preamble = text[:heads[0].start()]
    blocks = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        blocks.append((int(head.group(1)), text[head.start():end]))
    ordered = sorted(blocks, key=lambda b: b[0])
    moved = sum(1 for (n1, _), (n2, _) in zip(blocks, ordered) if n1 != n2)
    if moved:
        with open(path, "w", encoding="utf-8") as f:
            f.write(preamble + "".join(block for _, block in ordered))
    return moved


def main():
    do_seed = "--seed" in sys.argv
    # --repair-missing: scan from chapter 1 and translate only what is absent
    # from the deliverable. Cheap because already-delivered chapters are skipped
    # before any API call.
    repair_missing = "--repair-missing" in sys.argv
    # --limit N: compile only the first N chapters (for quick validation runs)
    limit = None
    for i, a in enumerate(sys.argv):
        if a == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a != str(limit)]

    if len(args) < 1:
        print("Usage: python3 scripts/run_novel.py <novel_key> [--seed] [--limit N] [--repair-missing]")
        print("  --seed  Pre-fill glossary and style memo from Analyze Echo before translating")
        print("  --limit N  Compile only the first N chapters (validation)")
        print("  --repair-missing  Ignore the checkpoint, scan from chapter 1, and")
        print("                    translate only chapters absent from the deliverable")
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

    # --limit N fallback. Every sample_points_fn starts at position 10 (or 3/5),
    # so `--limit 1` / `--limit 2` produced an EMPTY sample set: the "quick
    # validation mode" ran the whole pipeline with no cold read and no quality
    # record at all. Always cold-read the first chapter, and the last one when
    # the run is longer than that.
    if total_chapters and not (sample_points & set(range(1, total_chapters + 1))):
        sample_points = sample_points | {1}
        if total_chapters > 1:
            sample_points = sample_points | {total_chapters}
        print(f"NOTE: no configured sample point falls within {total_chapters} "
              f"chapter(s) — cold-reading position(s) "
              f"{sorted(sample_points & set(range(1, total_chapters + 1)))} instead.")

    print(f"Source: {cfg['name']} — {total_chapters} chapters, {cfg['genre']}")

    # ── 成本预检（铁律6：跑之前先报金额）──────────────────────────────
    # 这是全书最贵的入口，且默认路由就在高价档上（read/readback/review_terms
    # = qwen3.8-max，输出 ¥36/M）。这里**只报价、不拒绝**：生产批处理本来就
    # 允许跑高价档，闸门（src/cost_gate.guard_tier）是给"不需要高价档"的探针
    # 和对抗测试用的。打印逐节点单价，并把整本书的估算指给零成本的
    # estimate_offline.py，避免在这里再造一套成本模型。
    #
    # 不带 --book：estimate_offline.py 有自己的 BOOKS 标定表，key 与本脚本的
    # NOVELS 并不一致（同一本书它叫 wuxian、这里叫 limitless_horror），且只
    # 标定了 8 个实测点。照搬 key 会打印出一条静默失效的命令。
    print(f"Model routing: {describe_tiers()}")
    if total_chapters:
        print("整本书花费估算（零 API 成本、不调模型）: "
              "python3 scripts/cost/estimate_offline.py")
    print()

    ckpt_file = os.path.join(cfg["output_dir"], "_checkpoint.json")
    out_file = os.path.join(cfg["output_dir"], cfg["output_file"])
    glossary_file = os.path.join(cfg["output_dir"], "_glossary.json")
    quality_file = os.path.join(cfg["output_dir"], "_quality.json")
    os.makedirs(cfg["output_dir"], exist_ok=True)

    # Title for the deliverable's H1. cfg["name"] is Chinese ("无限恐怖"), and
    # writing it into an English deliverable breaks the project's #1 quality
    # rule. Prefer an explicit "name_en"; otherwise fall back to the ASCII
    # book_id rather than inventing a translation nobody approved.
    deliverable_title = cfg.get("name_en") or cfg["book_id"].replace("_", " ").title()
    if not cfg.get("name_en"):
        print(f"NOTE: no 'name_en' configured for {cfg['book_id']!r} — the "
              f"deliverable H1 uses the ASCII fallback {deliverable_title!r} "
              f"instead of the Chinese title {cfg['name']!r}.")

    quality_log = []
    if os.path.exists(quality_file):
        quality_log = json.load(open(quality_file))

    # Load what is already delivered: chapter numbers (so a resume never writes
    # a duplicate) and bodies (so a repair run can hand the next chapter a
    # continuity excerpt without paying to re-translate what already exists).
    written_chapters: set[int] = set()
    delivered_bodies: dict[int, str] = {}
    if os.path.exists(out_file):
        with open(out_file, encoding="utf-8") as _f:
            written_chapters, delivered_bodies = scan_delivered(_f.read())

    agent = TranslationAgent(book_id=cfg["book_id"])

    # Chapters skipped in THIS run (error, circuit open, or truncated/empty).
    # Progress must be computed from what was actually delivered, not from how
    # far the loop got — a run that delivered 1 of 2 chapters used to print
    # "100%" and "ALL DONE".
    failed_positions: list[int] = []
    newly_written: list[int] = []

    # Must be bound before the loop: on a fully-checkpointed resume the `while`
    # body never executes, and the summary print below dereferences `glossary`.
    # Without this, that path died with UnboundLocalError.
    glossary = agent.exact_store.to_dict()

    start = 0
    prev = ""
    prev_last_idx = -1
    if os.path.exists(ckpt_file):
        ckpt = json.load(open(ckpt_file))
        prev_last_idx = ckpt.get("last_idx", -1)
        start = prev_last_idx + 1
        agent.load_glossary_snapshot(ckpt.get("glossary_snapshot", "{}"))
        prev = ckpt.get("previous_summary", "")
        if repair_missing:
            print(f"--repair-missing: 从头扫描，跳过已交付章节"
                  f"（checkpoint 原本指向 {start}/{len(chapters)}）")
            start = 0
        else:
            print(f"Resuming from chapter {start + 1}/{len(chapters)}")
    elif repair_missing:
        print("--repair-missing: 无 checkpoint，按常规从头编译")

    segment_num = 1
    overall_t0 = time.monotonic()
    run_calls = run_in = run_out = 0
    run_cny = 0.0
    run_unmetered = 0

    def _chapter_usage(before: int):
        """Tokens/cost of the calls made since row index ``before``."""
        rows = token_ledger.ledger.rows(cfg["book_id"])[before:]
        metered = [r for r in rows if r.get("metered")]
        return (len(rows),
                sum(r["input_tokens"] for r in metered),
                sum(r["output_tokens"] for r in metered),
                sum(r["cost_cny"] or 0.0 for r in rows if r.get("priced")),
                sum(1 for r in rows if not r.get("metered")))

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

            if ch_num in written_chapters:
                # Already delivered — never call the API for it. This is what
                # makes filling a gap cost one chapter instead of a whole book.
                # Feed the next translated chapter a continuity excerpt, since
                # per-chapter summaries were never persisted.
                tail = delivered_bodies.get(ch_num, "").strip()
                if tail:
                    prev = ("(excerpt of the preceding chapter's closing "
                            "paragraphs)\n" + tail[-1200:])
                continue

            rows_before = len(token_ledger.ledger.rows(cfg["book_id"]))

            try:
                result = agent.translate_chapter(
                    chapter_title=ch.title,
                    chapter_content=ch.content,
                    chapter_number=ch_num,
                    previous_summary=prev,
                    target_lang="en-US",
                    genre=cfg["genre"],
                    skip_readback=not is_sample,
                )
            except CircuitBreakerOpenError:
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] CIRCUIT OPEN - sleep 30s")
                failed_positions.append(pos)
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
                        )
                    except Exception as e2:
                        print(f"  [{pos}/{len(chapters)} Ch{ch_num}] ERROR: {e2} (after retry)")
                        failed_positions.append(pos)
                        continue
                else:
                    print(f"  [{pos}/{len(chapters)} Ch{ch_num}] ERROR: {e}")
                    failed_positions.append(pos)
                    continue

            tt = result.get("translated_text", "")
            title_en = result.get("chapter_title_en", "")
            prev = result.get("chapter_summary", "")
            word_count = len(tt.split())
            src_chars = len(ch.content.replace("\n", "").replace(" ", ""))
            min_words = max(50, src_chars / 10)

            ok_flag = "OK"
            # min_words is max(50, src/10), so it is always >= 50: any output
            # shorter than 50 words is already caught by the TRUNCATED branch
            # below. The old `elif word_count < 50: ok_flag = "EMPTY"` was
            # therefore unreachable dead code — collapsed into one judgement.
            if word_count < min_words:
                kind = "EMPTY" if word_count == 0 else "TRUNCATED"
                ok_flag = f"{kind} ({word_count}w < {min_words:.0f}w)"
                # Not written to the deliverable (see the guard below), but it
                # must still count as a failure so the summary is honest.
                failed_positions.append(pos)

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

            _calls, _in, _out, _cny, _unm = _chapter_usage(rows_before)
            run_calls += _calls; run_in += _in; run_out += _out
            run_cny += _cny; run_unmetered += _unm
            if _calls:
                warn = f" | \u26a0 unmetered={_unm}" if _unm else ""
                print(f"     tokens {_in:,} in + {_out:,} out = {_in+_out:,}"
                      f" | \u00a5{_cny:.4f}{warn}")

            exists = os.path.exists(out_file)
            if ch_num not in written_chapters and "TRUNCATED" not in ok_flag and "EMPTY" not in ok_flag:
                with open(out_file, "a" if exists else "w", encoding="utf-8") as f:
                    if not exists:
                        f.write(f"# {deliverable_title} — English Translation\n\n")
                    # ZERO-CHINESE RULE. `ch.title` is the Chinese chapter
                    # heading, so it must never be the fallback: real
                    # deliverables shipped with "## Chapter 0: Prologue（楔子）"
                    # and "# 地府叫我小先生 — English Translation". Fall back to a
                    # plain ASCII number instead of inventing a translation.
                    display_title = title_en or f"Chapter {ch_num}"
                    f.write(f"## Chapter {ch_num}: {display_title}\n\n{tt}\n\n---\n\n")
                written_chapters.add(ch_num)
                newly_written.append(ch_num)
                last_written = i
        json.dump({
            # Never move the checkpoint backwards: a repair run translates early
            # gaps and would otherwise rewind progress that already exists.
            "last_idx": max(prev_last_idx, last_written),
            "glossary_snapshot": agent.exact_store.snapshot(),
            "previous_summary": prev,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, open(ckpt_file, "w"))

        glossary = agent.exact_store.to_dict()
        json.dump(glossary, open(glossary_file, "w"), ensure_ascii=False, indent=2)

        seg_elapsed = (time.monotonic() - t0) / 60
        # Progress reflects chapters actually delivered, not how far the loop
        # scanned. batch_end/len(chapters) used to report "100%" even when half
        # the chapters had been skipped.
        pct = 100 * len(written_chapters) / len(chapters)
        total_elapsed = (time.monotonic() - overall_t0) / 60
        remaining = total_elapsed / batch_end * (len(chapters) - batch_end)
        print(f"  Segment {segment_num}: {start+1}-{batch_end}/{len(chapters)} scanned, "
              f"{len(written_chapters)} delivered "
              f"({pct:.0f}%) in {seg_elapsed:.0f}m | {len(glossary)} terms | "
              f"total: {total_elapsed:.0f}m | ETA: {remaining:.0f}m | quality: {len(quality_log)}")
        print(f"  Tokens so far: {run_in:,} in + {run_out:,} out = {run_in+run_out:,}"
              f" | \u00a5{run_cny:.2f} over {run_calls} calls"
              + (f" | \u26a0 {run_unmetered} unmetered" if run_unmetered else ""))
        print()

        start = batch_end
        segment_num += 1
        if batch_end < len(chapters):
            time.sleep(2)

    total_min = (time.monotonic() - overall_t0) / 60
    delivered = len(written_chapters)
    scanned = len(chapters)
    # "ALL DONE" is only true when every scanned chapter reached the
    # deliverable. The old line printed the scanned count unconditionally, so a
    # run that delivered 1 of 2 chapters reported "ALL DONE: 2 chapters".
    incomplete = (delivered < scanned) or bool(failed_positions)
    if incomplete:
        print(f"INCOMPLETE: {delivered}/{scanned} chapters delivered in "
              f"{total_min:.0f}m — {scanned - delivered} missing")
        if failed_positions:
            preview = sorted(set(failed_positions))[:20]
            suffix = "..." if len(set(failed_positions)) > 20 else ""
            print(f"  Failed/skipped positions: {preview}{suffix}")
        missing = sorted(set(range(1, scanned + 1))
                         - {c for c in written_chapters})[:20]
        if missing:
            print(f"  Chapters absent from the deliverable: {missing}"
                  f"{'...' if delivered < scanned - 20 else ''}")
    else:
        print(f"ALL DONE: {delivered}/{scanned} chapters in {total_min:.0f}m")
    print(f"Glossary: {len(glossary)} terms | Quality checks: {len(quality_log)}")

    # A repair run appends, so a filled gap lands after the last chapter.
    if repair_missing and newly_written and os.path.exists(out_file):
        moved = reorder_deliverable(out_file)
        if moved:
            print(f"Reorder: {moved} 个章节块按章号归位（补译章原在文件末尾）")

    # ── 交付结构扫描：100% 章节、纯文本、零 API ──
    # 此前只有手动跑 scan_deliverable.py 才能发现章号异常/JSON 残留/截断，
    # 而实际交付时没人会记得跑。挂在这里，每次编译结束都留一份分诊清单。
    scan_summary = None
    hard_defects = 0
    if not os.path.exists(out_file):
        # Nothing was written, so there is nothing to scan. A run that failed to
        # deliver anything is already caught by `incomplete`, not here.
        print("Delivery scan: skipped (no deliverable written)")
    else:
        try:
            from pathlib import Path
            from scripts.scan_deliverable import scan_file
            scan = scan_file(Path(out_file))
            scan_summary = {
                "findings_total": scan["findings_total"],
                "findings_by_check": scan["findings_by_check"],
                "chapters_with_findings": scan["chapters_with_findings"],
                "numbering_defects": scan["numbering_defects"],
                "quotes_off_dominant": len(scan["quotes"]["off_dominant_chapters"]),
                "glossary_conflicts": len(scan["glossary"]["conflicts"]),
            }
            # Only defects the scan is confident about drive the exit code.
            # TRUNCATED_BODY has a calibrated ~18% false-positive rate.
            hard_defects = (scan["numbering_defects"]
                            + scan["findings_by_check"].get("JSON_RESIDUE", 0))
            print(f"Delivery scan: {scan['findings_total']} per-chapter finding(s) "
                  f"in {scan['chapters_with_findings']}/{scan['chapters_total']} "
                  f"chapters | numbering defects {scan['numbering_defects']}"
                  f" | glossary conflicts {scan_summary['glossary_conflicts']}"
                  f" | quote drift {scan_summary['quotes_off_dominant']} chapters")
            print("  TRUNCATED_BODY 约 18% 误报（诗词署名/干支纪年），完整清单："
                  "python3.11 scripts/scan_deliverable.py <交付文件>")
        except Exception as exc:
            # "not verified" must never read as "clean".
            print(f"  WARNING: delivery scan failed ({exc}) — deliverable NOT verified")
            hard_defects = 1

    # ── 成本台账：本次运行的真实 token 与花费（此前生产路径完全无记录）──
    # Cumulative for the book, not just this process: a book translated across
    # several sessions must not have its earlier spend erased by the last run.
    cost = token_ledger.summary_from_file(cfg["book_id"])
    cost_path = os.path.join(cfg["output_dir"], "_cost.json")
    json.dump({
        "book_id": cfg["book_id"],
        "chapters": delivered,
        "chapters_scanned": scanned,
        "chapters_failed": sorted(set(failed_positions)),
        "complete": not incomplete,
        "calls": cost["calls"],
        "input_tokens": cost["input_tokens"],
        "output_tokens": cost["output_tokens"],
        "cost_cny": cost["cost_cny"],
        "unmetered_calls": cost["unmetered_calls"],
        "unpriced_calls": cost["unpriced_calls"],
        "per_node": cost["per_node"],
        "per_model": cost["per_model"],
        "timing": cost["timing"],
        "scan": scan_summary,
        "minutes": round(total_min, 1),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, open(cost_path, "w"), ensure_ascii=False, indent=2)
    print(f"Cost: {cost['input_tokens']:,} in + {cost['output_tokens']:,} out"
          f" = {cost['input_tokens']+cost['output_tokens']:,} tokens"
          f" | \u00a5{cost['cost_cny']:.2f}  ({cost['calls']} calls)")
    if cost["unmetered_calls"]:
        print(f"  WARNING: {cost['unmetered_calls']} calls returned no usage payload"
              f" \u2014 real spend is higher than the figure above.")
    if cost["unpriced_calls"]:
        print(f"  WARNING: {cost['unpriced_calls']} calls used a model missing from"
              f" MODEL_PRICES_CNY_PER_M \u2014 cost excludes them.")
    for node, a in sorted(cost["per_node"].items(), key=lambda kv: -kv[1]["cost_cny"]):
        print(f"    {node:<14}{a['calls']:>5} calls{a['input_tokens']:>12,} in"
              f"{a['output_tokens']:>12,} out  \u00a5{a['cost_cny']:>8.2f}")
    print(f"  -> {cost_path}")

    # Non-zero exit lets a caller (CI, a wrapper, cron) detect the gap instead
    # of trusting the word "DONE" in stdout.
    return 1 if (incomplete or hard_defects) else 0


if __name__ == "__main__":
    sys.exit(main())
