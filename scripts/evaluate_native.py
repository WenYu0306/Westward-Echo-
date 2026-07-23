#!/usr/bin/env python3
"""Run native-speaker persona evaluations on translated chapters.

Reads translated .md fixtures for es-ES and ar-SA, calls DeepSeek V4 Pro
with a persona-driven system prompt, parses the structured JSON response,
and writes each report to tests/fixtures/eval_{lang}.json.

Usage:
    python3 scripts/evaluate_native.py              # evaluate es-ES + ar-SA
    python3 scripts/evaluate_native.py --lang es-ES # evaluate a single language
    python3 scripts/evaluate_native.py --lang en-US # baseline English evaluation
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Ensure the project root is on sys.path so we can import src.*
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP
from src.evaluator_prompts import EVALUATOR_PROFILES, EVALUATOR_SYSTEM

# ---------------------------------------------------------------------------
# Map language codes to fixture paths and source title
# ---------------------------------------------------------------------------

FIXTURE_MAP = {
    "es-ES": {
        "path": PROJECT_ROOT / "tests" / "fixtures" / "jianke_ch1_es_ES.md",
        "title": "间客 (Jianke / Interstellar Guest) — Chapter 1 (Donglin orphans arc)",
    },
    "ar-SA": {
        "path": PROJECT_ROOT / "tests" / "fixtures" / "jianke_ch1_ar_SA.md",
        "title": "间客 (Jianke / Interstellar Guest) — Chapter 1 (Donglin orphans arc)",
    },
    "en-US": {
        "path": PROJECT_ROOT / "tests" / "fixtures" / "jianke_ch1-5_en.md",
        "title": "间客 (Jianke / Interstellar Guest) — Chapter 1 (Donglin orphans arc)",
    },
}

OUTPUT_DIR = PROJECT_ROOT / "tests" / "fixtures"


def _clean_json(text: str) -> str:
    """Strip markdown fences and any leading/trailing non-JSON noise."""
    text = text.strip()
    # Remove markdown code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?\s*```$", "", text)
    return text.strip()


def evaluate_translation(
    target_lang: str,
    translation_text: str,
    source_title: str,
):
    """Run native-speaker evaluation.

    Args:
        target_lang: Language code from EVALUATOR_PROFILES (e.g. "es-ES").
        translation_text: The full translated chapter as a string.
        source_title: Human-readable description of the source (for the prompt).

    Returns:
        Parsed evaluation dict with keys: overall_score, scores, summary,
        strengths, issues, passed — or None if the LLM output couldn't be parsed.
    """
    if target_lang not in EVALUATOR_PROFILES:
        print(f"[ERROR] Unknown language code '{target_lang}'. Valid: {list(EVALUATOR_PROFILES)}")
        return None

    profile = EVALUATOR_PROFILES[target_lang]

    system_prompt = EVALUATOR_SYSTEM.format(
        persona=profile["persona"],
        language_name=profile["language_name"],
    )

    user_prompt = (
        f"## Source\n{source_title}\n\n"
        f"## Focus Areas (native-speaker priorities for this language)\n"
        f"{profile['focus_areas']}\n\n"
        f"## Translation to Evaluate\n\n{translation_text}"
    )

    # Use the quality_score model (V4 Pro) — evaluation needs reasoning depth
    llm = ChatOpenAI(
        model=MODEL_MAP["quality_score"],
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.0,
        max_tokens=4096,  # Same as quality_check for consistency
    )

    print(f"\n{'=' * 60}")
    print(f"Evaluating {target_lang} ({profile['language_name']})")
    print(f"Model: {MODEL_MAP['quality_score']}")
    print(f"Editor: {profile['persona'].split(',')[0].replace('You are ', '')}")
    print(f"{'=' * 60}")

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])

    raw = response.content.strip()
    print(f"\n--- Raw response (first 500 chars) ---")
    print(raw[:500])

    try:
        cleaned = _clean_json(raw)
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"\n[ERROR] JSON parse failed: {e}")
        print(f"Cleaned response:\n{cleaned}")
        return None

    # Validate required fields
    required_top = {"overall_score", "scores", "summary", "strengths", "issues", "passed"}
    missing = required_top - set(result.keys())
    if missing:
        print(f"[WARN] Missing top-level keys: {missing}")

    required_scores = {"readability", "dialogue", "cultural_adaptation", "terminology", "register"}
    if "scores" in result:
        missing_scores = required_scores - set(result["scores"].keys())
        if missing_scores:
            print(f"[WARN] Missing score keys: {missing_scores}")

    # Print summary inline for quick feedback
    print(f"\n--- Evaluation Summary ({target_lang}) ---")
    print(f"Overall: {result.get('overall_score', '?')}  |  Passed: {result.get('passed', '?')}")
    print(f"Scores: {result.get('scores', {})}")
    print(f"Summary: {result.get('summary', 'N/A')}")
    if result.get("strengths"):
        print(f"Strengths: {result['strengths'][:2]}")
    if result.get("issues"):
        print(f"Issues found: {len(result['issues'])}")
        for issue in result["issues"][:5]:
            print(f"  [{issue.get('severity', '?')}] {issue.get('problem', '?')[:100]}")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Native-speaker persona evaluation")
    parser.add_argument(
        "--lang",
        choices=["es-ES", "ar-SA", "en-US", "all"],
        default="all",
        help="Language(s) to evaluate (default: all)",
    )
    args = parser.parse_args()

    if not DEEPSEEK_API_KEY:
        print("[ERROR] DEEPSEEK_API_KEY is not set. Check your .env file.")
        sys.exit(1)

    langs = list(FIXTURE_MAP) if args.lang == "all" else [args.lang]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for lang in langs:
        fixture = FIXTURE_MAP[lang]
        fixture_path = fixture["path"]

        if not fixture_path.exists():
            print(f"[SKIP] Fixture not found: {fixture_path}")
            continue

        translation_text = fixture_path.read_text(encoding="utf-8")
        if not translation_text.strip():
            print(f"[SKIP] Fixture is empty: {fixture_path}")
            continue

        # For en-US, extract only the first chapter (before "**4:" or end of chapter 1)
        if lang == "en-US":
            # The English fixture is jianke_ch1-5_en.md — take only chapter 1
            # Chapter 1 starts after "**3: Behind the Hundred Black Silhouettes**"
            # Chapter 2 starts at "**4:" or the next chapter marker
            ch1_match = re.search(
                r"\*\*3: Behind the Hundred Black Silhouettes\*\*.*",
                translation_text,
                re.DOTALL,
            )
            if ch1_match:
                ch1_text = ch1_match.group(0)
                # Truncate at the next chapter heading
                next_ch = re.search(r"\n\*\*\d+:", ch1_text)
                if next_ch:
                    ch1_text = ch1_text[: next_ch.start()]
                translation_text = ch1_text.strip()

        result = evaluate_translation(
            target_lang=lang,
            translation_text=translation_text,
            source_title=fixture["title"],
        )

        if result is None:
            print(f"\n[FAIL] Could not get a valid evaluation for {lang}")
            continue

        out_path = OUTPUT_DIR / f"eval_{lang.replace('-', '_')}.json"
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[SAVED] {out_path}")


if __name__ == "__main__":
    main()
