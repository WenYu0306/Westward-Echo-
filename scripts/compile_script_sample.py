"""Compile a short-drama script sample (first N episodes) for customer delivery.

Compiles a 铸文-produced finished script (第N集 + 场景 + 对白 format) through
Westward Echo's script pipeline. This is the deliverable sample a copyright
holder would see — not a novel translation.

Usage:
    python3 scripts/compile_script_sample.py [--episodes N] [--all]

Default compiles the first 3 episodes in full mode (with cold-read).

Outputs to pilots/output/jiyi_dianhang/:
    jiyi_dianhang_en.md   compiled English script
    _glossary.json        CN→EN term table
    _quality.json         cold-read verdicts
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()

from src.agent.graph import TranslationAgent
from src.script_splitter import split_episodes
from src.chapter_splitter import ParagraphTag
from src.encoding import detect_and_read
from src.circuit_breaker import CircuitBreakerOpenError
from src.config import DEEPSEEK_API_KEY

SCRIPT_PATH = "pilots/jiyi_dianhang/jiyi_dianhang_westward.txt"
OUT_DIR = "pilots/output/jiyi_dianhang"
BOOK_ID = "jiyi_dianhang"          # isolated style memo for this script
GENRE = "scifi"                     # memory-pawnshop sci-fi suspense


def main():
    if not DEEPSEEK_API_KEY:
        print("DEEPSEEK_API_KEY not set — add it to .env first.")
        sys.exit(1)

    # ── Parse args ──
    n_episodes = 3
    if "--all" in sys.argv:
        n_episodes = 10**9  # all episodes
    for i, a in enumerate(sys.argv):
        if a == "--episodes" and i + 1 < len(sys.argv):
            n_episodes = int(sys.argv[i + 1])

    text, enc = detect_and_read(SCRIPT_PATH)
    episodes = [e for e in split_episodes(text) if e.action != ParagraphTag.SKIP]
    episodes = episodes[:n_episodes]
    print(f"Compiling {len(episodes)} episodes from {SCRIPT_PATH} (encoding: {enc})")

    os.makedirs(OUT_DIR, exist_ok=True)
    out_file = os.path.join(OUT_DIR, "jiyi_dianhang_en.md")
    glossary_file = os.path.join(OUT_DIR, "_glossary.json")
    quality_file = os.path.join(OUT_DIR, "_quality.json")
    ckpt_file = os.path.join(OUT_DIR, "_checkpoint.json")

    quality_log = []
    if os.path.exists(quality_file):
        quality_log = json.load(open(quality_file))

    agent = TranslationAgent(book_id=BOOK_ID)

    start = 0
    prev = ""
    if os.path.exists(ckpt_file):
        ckpt = json.load(open(ckpt_file))
        start = ckpt.get("last_idx", -1) + 1
        prev = ckpt.get("previous_summary", "")
        snap = ckpt.get("glossary_snapshot")
        if snap and snap != "{}":
            agent.load_glossary_snapshot(snap)
        print(f"Resuming from episode {start + 1}/{len(episodes)}")

    import logging
    for name in ["httpx", "httpcore", "openai", "chromadb", "urllib3"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    t0 = time.monotonic()
    for i in range(start, len(episodes)):
        ep = episodes[i]
        pos = i + 1
        # First 3 episodes all run full mode (cold-read) — this is a sample.
        is_sample = True

        try:
            result = agent.translate_chapter(
                chapter_title=ep.title,
                chapter_content=ep.content,
                chapter_number=ep.index,
                previous_summary=prev,
                target_lang="en-US",
                genre=GENRE,
                skip_readback=not is_sample,
                use_flash_writer=not is_sample,
                content_type="script",
            )
        except CircuitBreakerOpenError:
            print(f"  [{pos}/{len(episodes)} Ep{ep.index}] CIRCUIT OPEN — aborting")
            break
        except Exception as e:
            print(f"  [{pos}/{len(episodes)} Ep{ep.index}] ERROR: {e}")
            continue

        tt = result.get("translated_text", "")
        prev = result.get("chapter_summary", "")

        fb = result.get("readback_feedback", {})
        if fb:
            verdict = fb.get("verdict", "?")
            keep = fb.get("would_keep_reading", "?")
            imp = fb.get("overall_impression", "")[:300]
            q = {
                "ep_position": pos,
                "ep_number": ep.index,
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
            print(f"  [{pos}/{len(episodes)} Ep{ep.index}] {len(tt)}c [{verdict}] keep={keep}")
            if imp:
                print(f"     {imp}")
        else:
            print(f"  [{pos}/{len(episodes)} Ep{ep.index}] {len(tt)}c")

        exists = os.path.exists(out_file)
        with open(out_file, "a" if exists else "w", encoding="utf-8") as f:
            if not exists:
                f.write("# 记忆典当行 (Memory Pawnshop) — English Compilation\n\n")
            f.write(f"## Episode {ep.index}: {ep.title[:60]}\n\n{tt}\n\n---\n\n")

        json.dump({
            "last_idx": i,
            "glossary_snapshot": agent.exact_store.snapshot(),
            "previous_summary": prev,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, open(ckpt_file, "w"))

    glossary = agent.exact_store.to_dict()
    json.dump(glossary, open(glossary_file, "w"), ensure_ascii=False, indent=2)

    elapsed = (time.monotonic() - t0) / 60
    print(f"\nDONE: {len(episodes)} episodes in {elapsed:.1f}m | {len(glossary)} terms | "
          f"{len(quality_log)} cold-reads")
    print(f"译稿: {out_file}")
    print(f"术语: {glossary_file}")
    print(f"冷读: {quality_file}")


if __name__ == "__main__":
    main()
