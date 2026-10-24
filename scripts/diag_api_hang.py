"""Diagnose provider latency / hang: is it the model, the long output, or the pipeline?

Originally written to chase a DeepSeek hang, and left behind when the project
dropped DeepSeek in 2026-09. It was dead in three separate ways:

1. It executed at import time, so merely importing it loaded a 7 MB novel and
   fired live API calls.
2. It imported ``DEEPSEEK_API_KEY`` / ``DEEPSEEK_BASE_URL`` from ``src.config``;
   those names no longer exist, so it raised ImportError immediately.
3. Test 4 formatted ``WRITE_USER`` without ``fidelity_rules``, a placeholder
   added by the cultural-fidelity layer. Even with a working provider it would
   have died with KeyError.

The question it answers is provider-independent and still useful — where does a
long translation call actually spend its time — so it is repaired rather than
deleted. It now targets whichever providers ``src.config`` points at.

⚠️ This script makes REAL, billable API calls. It is gated the same way as the
cost probes: it refuses to run on an expensive model tier unless you say so.

Usage:
    python3 scripts/diag_api_hang.py                 # gated; explains itself
    python3 scripts/diag_api_hang.py --allow-expensive
    python3 scripts/diag_api_hang.py --tests 1,2,6   # subset (6 is free)
"""

import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NOVEL_PATH = "tests/fixtures/《无限恐怖》 作者：zhttty.txt"
TARGET_CHAPTER_INDEX = 3      # 0-based; Ch4 is ~4086 CN chars, the one that hung
LARGE_OUTPUT_PROMPT = (
    "Generate exactly 2000 words of lorem ipsum style filler text. Just keep writing."
)
TIMEOUT = 30


def _guard_tier(args) -> None:
    """Refuse to burn an expensive tier on a latency probe.

    Single source of truth is ``src.cost_gate``. This local copy used to check
    only QWEN_MODEL and GLM_MODEL — it missed GLM_FULL_MODEL (which the FIX
    node routes through, default glm-5.3 at ¥28/M) and had no
    XIDU_ALLOW_EXPENSIVE escape hatch, unlike the other three copies.
    """
    from src.cost_gate import guard_tier
    guard_tier(
        allow_expensive=args.allow_expensive,
        purpose="本探针只测延迟，flash 档同样能看出挂在哪一层（价格约 1/13）。",
        hint="   只想看提示词体积（免费、不调 API）：\n"
             f"       python3 {Path(sys.argv[0]).name} --tests 6",
    )


def _load_chapter():
    from src.chapter_splitter import ParagraphTag, split_chapters
    from src.text_io import detect_and_read

    if not Path(NOVEL_PATH).exists():
        raise SystemExit(f"diagnostic corpus not found: {NOVEL_PATH}")
    text, _enc = detect_and_read(NOVEL_PATH)
    chapters = [c for c in split_chapters(text) if c.action != ParagraphTag.SKIP]
    if len(chapters) <= TARGET_CHAPTER_INDEX:
        raise SystemExit(
            f"corpus has only {len(chapters)} chapter(s); "
            f"cannot probe index {TARGET_CHAPTER_INDEX}"
        )
    return chapters[TARGET_CHAPTER_INDEX]


def _timed(label: str, fn) -> bool:
    """Run one probe, print elapsed time, and report success."""
    print(f"=== {label} ===")
    t0 = time.monotonic()
    try:
        detail = fn()
        print(f"  OK: {detail}, {time.monotonic() - t0:.0f}s")
        return True
    except Exception as exc:
        print(f"  FAIL after {time.monotonic() - t0:.0f}s: {type(exc).__name__}: {exc}")
        return False


def _write_llm(**overrides):
    """Build a WRITE-tier client (GLM flash), i.e. the text-output path."""
    from langchain_openai import ChatOpenAI

    from src.config import GLM_API_KEY, GLM_BASE_URL, GLM_MODEL

    kwargs = {
        "model": GLM_MODEL,
        "api_key": GLM_API_KEY,
        "base_url": GLM_BASE_URL,
        "max_tokens": 16384,
        "request_timeout": TIMEOUT,
    }
    kwargs.update(overrides)
    return ChatOpenAI(**kwargs)


def _read_llm(**overrides):
    """Build a READ/judge-tier client (Qwen), i.e. the reasoning path."""
    from langchain_openai import ChatOpenAI

    from src.config import LLM_API_KEY, LLM_BASE_URL, QWEN_MODEL, QWEN_THINKING_BUDGET

    kwargs = {
        "model": QWEN_MODEL,
        "api_key": LLM_API_KEY,
        "base_url": LLM_BASE_URL,
        "max_tokens": 16384,
        "request_timeout": TIMEOUT,
        # thinking_budget, not reasoning_effort: on this endpoint the two
        # cannot be set together.
        "extra_body": {"thinking_budget": QWEN_THINKING_BUDGET},
    }
    kwargs.update(overrides)
    return ChatOpenAI(**kwargs)


def _build_pipeline_prompt(chapter) -> tuple[str, str, dict]:
    """Reproduce the exact WRITE call the pipeline makes.

    Returns (system_prompt, user_prompt, state) so test 6 can report sizes
    without spending anything.
    """
    from src.agent.graph import TranslationAgent
    from src.agent.nodes.read import read_node
    from src.agent.nodes.write import _format_image_gaps, _format_read_analysis
    from src.agent.prompts.translation import LANGUAGE_STYLE_NOTES
    from src.agent.prompts.write import WRITE_SYSTEM, WRITE_USER
    from src.cultural_rules import format_fidelity_for_prompt, load_fidelity_rules

    target_lang = "en-US"
    agent = TranslationAgent(book_id="diag_test")
    state = agent._make_state(
        chapter.title, chapter.content, chapter.index, "",
        target_lang, "urban", skip_readback=True,
    )
    read_result = read_node(state, agent.exact_store, agent.semantic_store)
    analysis = read_result.get("read_analysis", {})
    analysis_text = _format_read_analysis(analysis)
    image_text = _format_image_gaps(read_result.get("image_gaps", []))

    # fidelity_rules is a required placeholder since the cultural-fidelity
    # layer landed; the old version of this script omitted it and would have
    # raised KeyError here.
    fidelity_text = format_fidelity_for_prompt(load_fidelity_rules(target_lang))
    regional_style = LANGUAGE_STYLE_NOTES.get(target_lang, "")

    user_prompt = WRITE_USER.format(
        style_memo=state.get("style_memo", ""),
        reader_analysis=analysis_text,
        image_gaps=image_text,
        chapter_number=chapter.index,
        chapter_title=chapter.title,
        genre="urban",
        exact_matches=state.get("exact_matches_text", ""),
        semantic_matches=state.get("semantic_matches_text", ""),
        previous_summary="(first chapter)",
        confirmed_terms="(none)",
        rejected_terms="(none)",
        regional_style=regional_style or "(no regional override)",
        fidelity_rules=fidelity_text,
        chapter_content=chapter.content,
    )
    return WRITE_SYSTEM, user_prompt, state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Locate where a provider call hangs: model, long output, or pipeline prompt."
    )
    parser.add_argument("--allow-expensive", action="store_true",
                        help="run even when an expensive model tier is configured")
    parser.add_argument("--tests", default="1,2,3,4,5,6",
                        help="comma-separated subset of probes to run (default: all)")
    args = parser.parse_args(argv)

    selected = {t.strip() for t in args.tests.split(",") if t.strip()}
    if not selected:
        raise SystemExit("--tests selected nothing")

    # Test 6 is free (it only measures prompt sizes), so it may run without the
    # tier gate; anything that calls a provider goes through the gate.
    if selected - {"6"}:
        _guard_tier(args)
        from src.config import GLM_API_KEY, LLM_API_KEY
        if not LLM_API_KEY or not GLM_API_KEY:
            raise SystemExit(
                "LLM_API_KEY and GLM_API_KEY must both be set — this probe calls "
                "both the judge tier (Qwen) and the write tier (GLM)."
            )

    from langchain_core.messages import HumanMessage, SystemMessage

    chapter = _load_chapter()
    print(f"Target: {chapter.title}, {chapter.word_count} CN chars\n")

    results: dict[str, bool] = {}

    if "1" in selected:
        def _t1():
            from src.config import GLM_API_KEY, GLM_BASE_URL, GLM_MODEL
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model=GLM_MODEL, api_key=GLM_API_KEY,
                             base_url=GLM_BASE_URL, max_tokens=100,
                             request_timeout=TIMEOUT)
            r = llm.invoke([HumanMessage(content="Say hello")])
            return r.content[:50]
        results["1 write-tier, 100 tokens"] = _timed("1: bare write tier (100 tokens)", _t1)

    if "2" in selected:
        def _t2():
            llm = _write_llm()
            r = llm.invoke([HumanMessage(content=LARGE_OUTPUT_PROMPT)])
            return f"{len(r.content)} chars"
        results["2 write-tier, 16K output"] = _timed("2: write tier, 16K output", _t2)

    if "3" in selected:
        def _t3():
            llm = _write_llm()
            r = llm.invoke([HumanMessage(
                content=f"Translate this to English:\n\n{chapter.content[:3000]}")])
            return f"{len(r.content)} chars"
        results["3 real chapter, no system"] = _timed(
            "3: write tier + real chapter content (no system prompt)", _t3)

    system_prompt = user_prompt = None
    if "4" in selected or "5" in selected or "6" in selected:
        system_prompt, user_prompt, _state = _build_pipeline_prompt(chapter)

    if "4" in selected:
        def _t4():
            llm = _write_llm()
            r = llm.invoke([SystemMessage(content=system_prompt),
                            HumanMessage(content=user_prompt)])
            return f"{len(r.content)} chars"
        results["4 full WRITE prompt"] = _timed(
            "4: write tier + full WRITE system prompt", _t4)

    if "5" in selected:
        def _t5():
            llm = _read_llm()
            r = llm.invoke([SystemMessage(content=system_prompt),
                            HumanMessage(content=user_prompt)])
            return f"{len(r.content)} chars"
        results["5 judge tier, full prompt"] = _timed(
            "5: judge tier (Qwen) + full WRITE prompt", _t5)

    if "6" in selected:
        print("\n=== 6: Prompt sizes (no API call) ===")
        print(f"  System prompt: {len(system_prompt)} chars "
              f"(~{len(system_prompt) // 3} tokens)")
        print(f"  User prompt:   {len(user_prompt)} chars (~{len(user_prompt) // 3} tokens)")
        total = len(system_prompt) + len(user_prompt)
        print(f"  Total:         ~{total // 3} tokens")
        print(f"  Chapter:       {len(chapter.content)} chars")
        results["6 prompt sizes"] = True

    print("\n=== Summary ===")
    for label, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
