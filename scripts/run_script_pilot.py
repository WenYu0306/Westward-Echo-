"""Short-drama pilot — translate the self-made 12-episode script fixture.

Run from your terminal:  python3 scripts/run_script_pilot.py

This is the smoke-test entry point for the ``script`` content-type branch.
It mirrors scripts/run_one_segment.py (checkpoint-safe, quality-sampled)
but feeds the pipeline a vertical short-drama script instead of a novel,
with an isolated book_id so the novel line's style memo is untouched.

Sample episodes run the full pipeline (WRITE + READBACK cold viewer);
ordinary episodes run fast mode. Verdicts land in _quality.json.
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.agent.graph import TranslationAgent
from src.script_splitter import split_episodes
from src.chapter_splitter import ParagraphTag
from src.encoding import detect_and_read
from src.circuit_breaker import CircuitBreakerOpenError
from src.config import DEEPSEEK_API_KEY

CKPT_DIR = "pilots/output/pei_zong_script"
BOOK_ID = "pilot_pei_zong_script"          # isolated style memo
SCRIPT_PATH = "pilots/pei_zong_script.txt"
GLOSSARY_PATH = "pilots/glossary.json"
SEGMENT = 6

# Sample points: the hook, the midpoint, the cliffhanger.
SAMPLE_POINTS = frozenset({1, 6, 12})


def main():
    if not DEEPSEEK_API_KEY:
        print("DEEPSEEK_API_KEY not set — add it to .env before running the pilot.")
        sys.exit(1)

    text, enc = detect_and_read(SCRIPT_PATH)
    episodes = split_episodes(text)
    episodes = [e for e in episodes if e.action != ParagraphTag.SKIP]
    print(f"Loaded {len(episodes)} episodes from {SCRIPT_PATH} (encoding: {enc})")

    ckpt_file = os.path.join(CKPT_DIR, "_checkpoint.json")
    out_file = os.path.join(CKPT_DIR, "pei_zong_script_en.md")
    glossary_file = os.path.join(CKPT_DIR, "_glossary.json")
    quality_file = os.path.join(CKPT_DIR, "_quality.json")
    os.makedirs(CKPT_DIR, exist_ok=True)

    quality_log = []
    if os.path.exists(quality_file):
        quality_log = json.load(open(quality_file))

    # --- Agent with isolated book_id (style memo separate from novels) ---
    agent = TranslationAgent(book_id=BOOK_ID)

    # --- Preload the pilot glossary (character names + panel terms) ---
    with open(GLOSSARY_PATH, encoding="utf-8") as f:
        preset = json.load(f)
    for cn, en in preset.items():
        agent.exact_store.add(cn, en, category="character")
    print(f"Preloaded {len(preset)} glossary terms")

    # --- Checkpoint resume ---
    start = 0
    prev = ""
    if os.path.exists(ckpt_file):
        ckpt = json.load(open(ckpt_file))
        start = ckpt.get("last_idx", -1) + 1
        prev = ckpt.get("previous_summary", "")
        snapshot = ckpt.get("glossary_snapshot")
        if snapshot and snapshot != "{}":
            agent.load_glossary_snapshot(snapshot)
        print(f"Resuming from episode {start + 1}/{len(episodes)}")

    segment_num = 1
    overall_t0 = time.monotonic()

    import logging
    for name in ["httpx", "httpcore", "openai", "chromadb", "urllib3"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    while start < len(episodes):
        batch_end = min(start + SEGMENT, len(episodes))
        t0 = time.monotonic()

        for i in range(start, batch_end):
            ep = episodes[i]
            ep_num = ep.index
            pos = i + 1  # 1-based episode position
            is_sample = pos in SAMPLE_POINTS

            try:
                result = agent.translate_chapter(
                    chapter_title=ep.title,
                    chapter_content=ep.content,
                    chapter_number=ep_num,
                    previous_summary=prev,
                    target_lang="en-US",
                    genre="romance_ceo",
                    skip_readback=not is_sample,
                    use_flash_writer=not is_sample,
                    content_type="script",
                )
            except CircuitBreakerOpenError:
                print(f"  [{pos}/{len(episodes)} Ep{ep_num}] CIRCUIT OPEN - sleep 30s")
                time.sleep(30)
                continue
            except Exception as e:
                print(f"  [{pos}/{len(episodes)} Ep{ep_num}] ERROR: {e}")
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
                    "ep_position": pos,
                    "ep_number": ep_num,
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
                issues = q["comprehension_issues"] + q["engagement_gaps"]
                print(f"  [{pos}/{len(episodes)} Ep{ep_num}] {len(tt)}c [S] 🔍 [{verdict}] keep={keep} issues={issues}")
                if imp:
                    print(f"     {imp}")
            else:
                print(f"  [{pos}/{len(episodes)} Ep{ep_num}] {len(tt)}c [F] {ok_flag}")

            exists = os.path.exists(out_file)
            with open(out_file, "a" if exists else "w", encoding="utf-8") as f:
                if not exists:
                    f.write("# Fu Ping Zi Gui (Rising by the Son) — English Script Pilot\n\n")
                f.write(f"## Episode {ep_num}: {ep.title[:60]}\n\n{tt}\n\n---\n\n")

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
        pct = 100 * batch_end / len(episodes)
        total_elapsed = (time.monotonic() - overall_t0) / 60
        remaining = total_elapsed / batch_end * (len(episodes) - batch_end)
        print(f"  Segment {segment_num}: {start+1}-{batch_end}/{len(episodes)} "
              f"({pct:.0f}%) in {seg_elapsed:.1f}m | {len(glossary)} terms | "
              f"total: {total_elapsed:.1f}m | ETA: {remaining:.1f}m | quality: {len(quality_log)}")
        print()

        start = batch_end
        segment_num += 1
        if batch_end < len(episodes):
            time.sleep(2)

    total_min = (time.monotonic() - overall_t0) / 60
    print(f"ALL DONE: {len(episodes)} episodes in {total_min:.1f}m")
    print(f"Glossary: {len(glossary)} terms | Quality checks: {len(quality_log)}")
    print(f"Output: {out_file}")
    print(f"Quality: {quality_file}")


if __name__ == "__main__":
    main()
