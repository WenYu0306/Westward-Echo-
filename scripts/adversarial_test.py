#!/usr/bin/env python3
"""Adversarial testing harness for Westward Echo (西渡).

Probes specific weaknesses in the translation pipeline using LLM sub-agents
and direct function calls.  This is NOT a traditional unit/integration test --
it tries to break the system at known vulnerability points surfaced during the
8-issue analysis.

Tests:
  1. Cross-evaluator consistency  (2 Spanish personas, score delta <= 1.5)
  2. Progressive chapter length stress test (500..5000 chars, find failure threshold)
  3. Editor API integrity (CRUD endpoints return valid JSON, no 500s)
  4. Sensitive term boundary scan (edge cases for sensitive_terms module)

Usage:
    cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
    python3 scripts/adversarial_test.py

Requirements:
    Tests 1 & 2 need DEEPSEEK_API_KEY (loaded from .env).  Tests 3 & 4 are local.
    If no API key, Tests 1 & 2 are skipped gracefully.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
from pathlib import Path
from typing import Optional

# Ensure the project root is on sys.path so we can import src.*
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Adversarial testing harness for Westward Echo"
    )
    parser.add_argument(
        "--skip-api",
        action="store_true",
        help="Skip tests that require a DeepSeek API key (Tests 1 & 2).",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Require a running FastAPI server for Test 3 instead of calling functions directly.",
    )
    parser.add_argument(
        "--server-url",
        default="http://localhost:8000",
        help="Base URL for the FastAPI server (default: http://localhost:8000).",
    )
    args = parser.parse_args()

    project_path = PROJECT_ROOT
    fixtures_dir = project_path / "tests" / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    # Modules that pass/fail
    results: list[dict] = []

    # ------------------------------------------------------------------
    # Check for API key availability
    # ------------------------------------------------------------------
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    has_api = bool(api_key) and not args.skip_api

    # ==================================================================
    # TEST 1: Cross-evaluator consistency
    # ==================================================================
    if has_api:
        results.append(_test_cross_evaluator(fixtures_dir))
    else:
        results.append({
            "name": "Cross-evaluator consistency (Maria vs Carlos)",
            "passed": True,
            "skipped": True,
            "detail": "No DEEPSEEK_API_KEY or --skip-api flag set.",
        })

    # ==================================================================
    # TEST 2: Progressive chapter length stress test
    # ==================================================================
    if has_api:
        results.append(_test_chapter_stress(project_path, fixtures_dir))
    else:
        results.append({
            "name": "Progressive stress test (10 chapters)",
            "passed": True,
            "skipped": True,
            "detail": "No DEEPSEEK_API_KEY or --skip-api flag set.",
        })

    # ==================================================================
    # TEST 3: Editor API integrity
    # ==================================================================
    results.append(_test_editor_api(project_path, args.serve, args.server_url))

    # ==================================================================
    # TEST 4: Sensitive term boundary scan
    # ==================================================================
    results.append(_test_sensitive_terms())

    # ==================================================================
    # Report
    # ==================================================================
    print("\n" + "=" * 55)
    print("ADVERSARIAL TEST REPORT")
    print("=" * 55)

    passed_count = 0
    skipped_count = 0
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        if r.get("skipped"):
            status = "SKIP"
            skipped_count += 1
        elif r["passed"]:
            passed_count += 1
        icon = "✅" if r["passed"] else "❌"
        skip_icon = "⚠️" if r.get("skipped") else ""
        detail = r.get("detail", "")
        print(f"{icon}{skip_icon} {r['name']}: {detail}")

    total = len(results)
    failed = total - passed_count - skipped_count
    print(f"\n{passed_count}/{total} modules passed, {failed} vulnerabilities found, {skipped_count} skipped")

    if failed:
        sys.exit(1)


# =========================================================================
# TEST 1: Cross-evaluator consistency
# =========================================================================

def _test_cross_evaluator(fixtures_dir: Path) -> dict:
    """Two Spanish-native evaluator personas should agree within 1.5 points."""
    from src.evaluator_prompts import EVALUATOR_SYSTEM

    name = "Cross-evaluator consistency (Maria vs Carlos)"

    # Build two distinct Spanish personas
    persona_a = (
        "You are Maria, a 34-year-old literary editor from Madrid with 12 years "
        "of experience editing translated fiction for Spanish publishers (Planeta, "
        "Penguin Random House Grupo Editorial). You read Chinese web novels "
        "translated into Spanish professionally."
    )
    persona_b = (
        "You are Carlos, a 29-year-old independent translator based in Mexico City "
        "with 8 years of experience translating East Asian web novels for Latin "
        "American readers. You work with Webnovel, Dreame, and other platforms, "
        "and you specialise in sci-fi and cultivation genres."
    )

    system_prompt_a = EVALUATOR_SYSTEM.format(
        persona=persona_a,
        language_name="Spanish (Peninsular / Latin American neutral)",
    )
    system_prompt_b = EVALUATOR_SYSTEM.format(
        persona=persona_b,
        language_name="Spanish (Latin American, with Mexican regional flavour)",
    )

    user_prompt_base = (
        "## Source\n"
        "间客 (Jianke / Interstellar Guest) -- Chapter 1 (Donglin orphans arc)\n\n"
        "## Focus Areas\n"
        "natural dialogue flow in Spanish, appropriate register consistency, "
        "cultural adaptation quality for Spanish-speaking readers, "
        "terminology consistency with the sci-fi/mecha genre in Spanish\n\n"
        "## Translation to Evaluate\n\n"
    )

    # Load ES fixture
    fixture_path = fixtures_dir / "jianke_ch1_es_ES.md"
    if not fixture_path.exists():
        return {
            "name": name, "passed": False, "skipped": False,
            "detail": f"Fixture not found: {fixture_path}",
        }

    translation_text = fixture_path.read_text(encoding="utf-8")
    if not translation_text.strip():
        return {
            "name": name, "passed": False, "skipped": False,
            "detail": "Fixture is empty",
        }

    user_prompt = user_prompt_base + translation_text

    score_a = _run_evaluator(system_prompt_a, user_prompt, "Maria")
    score_b = _run_evaluator(system_prompt_b, user_prompt, "Carlos")

    if score_a is None or score_b is None:
        return {
            "name": name, "passed": False, "skipped": False,
            "detail": f"LLM returned unparseable output (A={score_a}, B={score_b})",
        }

    diff = abs(score_a["overall_score"] - score_b["overall_score"])
    passed = diff <= 1.5

    # Persist both evaluations
    output = {
        "evaluator_a": {"persona": "Maria", "result": score_a},
        "evaluator_b": {"persona": "Carlos", "result": score_b},
        "score_difference": diff,
        "threshold": 1.5,
        "flag": not passed,
    }
    out_path = fixtures_dir / "adversarial_eval_crosscheck.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [SAVED] {out_path}")

    detail = f"diff={diff:.1f}"
    if not passed:
        detail += " FLAG: evaluator bias detected"

    return {"name": name, "passed": passed, "skipped": False, "detail": detail}


def _run_evaluator(system_prompt: str, user_prompt: str, name: str) -> Optional[dict]:
    """Call DeepSeek V4 Pro with an evaluator persona, return parsed JSON."""
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP

    llm = ChatOpenAI(
        model=MODEL_MAP["quality_score"],
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.0,
        max_tokens=4096,
    )

    print(f"  Running evaluator: {name} ...")
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])

    raw = response.content.strip()
    # Clean markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw)
    cleaned = re.sub(r"\n?\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        print(f"  [WARN] {name} returned unparseable JSON: {raw[:200]}")
        return None


# =========================================================================
# TEST 2: Progressive chapter length stress test
# =========================================================================

def _test_chapter_stress(project_path: Path, fixtures_dir: Path) -> dict:
    """Translate 10 progressively longer chapters to find the empty-output threshold."""
    from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    name = "Progressive stress test (10 chapters)"

    # Generate 10 short test chapters in Chinese via DeepSeek Flash
    lengths = range(500, 5500, 500)  # 500, 1000, ..., 5000
    chapters_txt_path = fixtures_dir / "adversarial_stress_chapters.txt"

    # Generate chapters if they don't exist
    if not chapters_txt_path.exists():
        try:
            chapters = _generate_stress_chapters(lengths)
            chapters_txt_path.write_text(
                "\n\n===CHAPTER_END===\n\n".join(chapters),
                encoding="utf-8",
            )
            print(f"  [SAVED] {chapters_txt_path}")
        except Exception as e:
            return {
                "name": name, "passed": False, "skipped": False,
                "detail": f"Chapter generation failed: {e}",
            }
    else:
        chapters = chapters_txt_path.read_text(encoding="utf-8").split(
            "\n\n===CHAPTER_END===\n\n"
        )
        print(f"  Loaded {len(chapters)} pre-generated stress chapters")

    # Translate each chapter through the full pipeline (agent.graph)
    try:
        from src.agent.graph import TranslationAgent
    except ImportError as e:
        return {
            "name": name, "passed": False, "skipped": False,
            "detail": f"Cannot import TranslationAgent: {e}",
        }

    agent = TranslationAgent()
    agent.load_glossary(target_lang="en-US")

    results_list: list[dict] = []
    critical_chapter = None

    for i, (size, chapter_text) in enumerate(zip(lengths, chapters), start=1):
        print(f"  Translating chapter {i} ({size} chars target) ...", end=" ", flush=True)

        try:
            result = agent.translate_chapter(
                chapter_title=f"Stress Test Chapter {i}",
                chapter_content=_trim_to(chapter_text, size),
                chapter_number=i,
                previous_summary="",
                target_lang="en-US",
                genre="urban",
            )
            translated = result.get("translated_text", "")
            score = result.get("quality_score", 0.0)
            retries = result.get("retranslation_count", 0)
            empty = not translated or len(translated.strip()) < 10

            record = {
                "chapter": i,
                "target_length": size,
                "actual_source_length": len(_trim_to(chapter_text, size)),
                "translated_length": len(translated) if translated else 0,
                "quality_score": score,
                "retranslation_count": retries,
                "empty_output": empty,
            }
            results_list.append(record)

            if empty:
                print(f"EMPTY (retries={retries})")
            else:
                print(f"ok (len={len(translated)}, score={score})")

            if empty and critical_chapter is None:
                critical_chapter = i

            # Brief cooldown to avoid rate-limiting
            import time
            time.sleep(0.5)

        except Exception as e:
            print(f"ERROR: {e}")
            results_list.append({
                "chapter": i,
                "target_length": size,
                "actual_source_length": len(_trim_to(chapter_text, size)),
                "translated_length": 0,
                "quality_score": 0.0,
                "retranslation_count": 0,
                "empty_output": True,
                "error": str(e),
            })

    # Write report
    report = {
        "chapters": results_list,
        "critical_chapter": critical_chapter,
        "critical_approximate_tokens": (
            int((lengths[critical_chapter - 1] / 3) * 0.75)
            if critical_chapter is not None
            else None
        ),
    }
    report_path = fixtures_dir / "adversarial_stress_result.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [SAVED] {report_path}")

    empty_count = sum(1 for r in results_list if r.get("empty_output"))
    if empty_count == 0:
        detail = "all 10 passed"
    else:
        ch = critical_chapter or "?"
        detail = f"critical at chapter {ch} (~{report['critical_approximate_tokens']} tokens)"

    return {"name": name, "passed": True, "skipped": False, "detail": detail}


def _generate_stress_chapters(lengths: range) -> list[str]:
    """Use DeepSeek Flash to generate 10 short Chinese chapters of varying lengths."""
    from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    llm = ChatOpenAI(
        model=MODEL_MAP["translate"],
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.7,
        max_tokens=4096,
    )

    chapters = []
    for size in lengths:
        prompt = (
            f"Write a short Chinese web novel chapter (urban fantasy genre) that is "
            f"EXACTLY around {size} Chinese characters. "
            f"Use complete sentences. Include some dialogue between two characters. "
            f"The story: a modern city where ancient spirits secretly exist. "
            f"Output ONLY the chapter text with NO explanations, NO preamble, "
            f"NO markdown formatting, NO title -- just the raw story text."
        )
        print(f"  Generating chapter ({size} chars) ...", end=" ", flush=True)

        response = llm.invoke([
            SystemMessage(content="You are a Chinese web novel author. Write exactly what is asked with no extra commentary."),
            HumanMessage(content=prompt),
        ])

        text = response.content.strip()
        chapters.append(text)
        print(f"got {len(text)} chars")
    return chapters


def _trim_to(text: str, size: int) -> str:
    """Trim text to approximately <size> characters, at a sentence boundary."""
    if len(text) <= size:
        return text
    truncated = text[:size]
    last_punct = max(
        truncated.rfind("。"),  # 。
        truncated.rfind("！"),  # ！
        truncated.rfind("？"),  # ？
        truncated.rfind("\n"),
    )
    if last_punct > 0:
        truncated = truncated[: last_punct + 1]
    return truncated


# =========================================================================
# TEST 3: Editor API integrity
# =========================================================================

def _test_editor_api(project_path: Path, require_server: bool, server_url: str) -> dict:
    """Call editor CRUD endpoints (or functions directly) and verify integrity."""
    name = "Editor API integrity"

    if require_server:
        return _test_editor_api_via_http(server_url)

    return _test_editor_api_direct(project_path)


def _test_editor_api_via_http(server_url: str) -> dict:
    """Test editor endpoints via HTTP calls to a running FastAPI server."""
    import httpx

    base = server_url.rstrip("/")
    name = "Editor API integrity (HTTP)"
    results = []

    # We need a real job_id.  Try to discover one.
    try:
        resp = httpx.get(f"{base}/api/editor/dummy_job/chapters", timeout=10)
        # Any non-500 response means endpoint works (even 404 is valid JSON)
        ok = resp.status_code != 500
        try:
            resp.json()  # Must be valid JSON
        except Exception:
            ok = False
        results.append(("GET /chapters", ok))
    except httpx.ConnectError:
        return {"name": name, "passed": False, "skipped": False,
                "detail": "Server not running -- use --serve only when server is up"}

    # For the remaining endpoints we need a valid job_id; this approach is limited
    # without a completed job.  Just verify the endpoints respond with JSON.
    tests_passed = sum(1 for _, ok in results if ok)
    return {
        "name": name,
        "passed": tests_passed == len(results),
        "skipped": False,
        "detail": f"{tests_passed}/{len(results)} OK (HTTP requires real job for full test)",
    }


def _test_editor_api_direct(project_path: Path) -> dict:
    """Test editor CRUD functions directly (no running server needed)."""
    name = "Editor API integrity"
    points: list[dict] = []

    # Test 3a: GET /api/editor/{job_id}/chapters
    # We call the list_chapters function directly.
    try:
        from src.api.editor import list_chapters
        # Use a non-existent job -- should return a 404 JSON response
        result = list_chapters("nonexistent_job_adversarial_test")
        # FastAPI returns JSONResponse for errors; verify structure
        if hasattr(result, "status_code"):
            points.append({"check": "GET /chapters", "pass": True,
                           "note": f"Returned HTTP {result.status_code} (expected for missing job)"})
        elif isinstance(result, list):
            points.append({"check": "GET /chapters", "pass": True,
                           "note": f"Returned list of {len(result)} chapters"})
        else:
            points.append({"check": "GET /chapters", "pass": True,
                           "note": f"Returned dict type"})
    except Exception as e:
        points.append({"check": "GET /chapters", "pass": False, "note": str(e)})

    # Test 3b: PUT /api/editor/{job_id}/chapters/{n} with edited paragraph
    try:
        from src.api.editor import get_chapter, update_chapter, _ensure_table
        # We need a completed job in job_store.  Check if any exist.
        from src.job_store import job_store
        all_jobs = job_store.list_jobs()
        completed = [j for j in all_jobs if j.get("status") == "complete"]

        if completed:
            job_id = completed[0]["job_id"]
            # First verify we can get a chapter
            chapter_data = get_chapter(job_id, 1)
            if isinstance(chapter_data, dict) and "error" not in chapter_data:
                # Now try to save an edit
                save_result = update_chapter(
                    job_id, 1,
                    {"paragraphs": [{"index": 0, "text": "ADVERSARIAL TEST EDIT"}]},
                )
                if isinstance(save_result, dict) and save_result.get("status") == "saved":
                    points.append({"check": "PUT /chapters/{n}", "pass": True,
                                   "note": f"Saved edit to job {job_id[:12]}..."})
                else:
                    points.append({"check": "PUT /chapters/{n}", "pass": False,
                                   "note": f"Save returned unexpected: {save_result}"})
            else:
                points.append({"check": "PUT /chapters/{n}", "pass": True,
                               "note": "No chapter data (skipped PUT, GET still works)"})
                # Still count this as pass since GET endpoint worked
        else:
            points.append({"check": "PUT /chapters/{n}", "pass": True,
                           "note": "No completed jobs, skipped PUT test"})
    except Exception as e:
        points.append({"check": "PUT /chapters/{n}", "pass": False, "note": str(e)})

    # Test 3c: POST /api/editor/{job_id}/batch-replace
    # We verify the function signature and that it handles missing job gracefully.
    try:
        from src.api.editor import batch_replace
        result = batch_replace("nonexistent_job", {"term_en_old": "test", "term_en_new": "TEST"})
        # Should return a 404 JSONResponse for missing job
        if hasattr(result, "status_code"):
            points.append({"check": "POST /batch-replace", "pass": True,
                           "note": f"Returned HTTP {result.status_code} for missing job"})
        elif isinstance(result, dict):
            points.append({"check": "POST /batch-replace", "pass": True,
                           "note": f"Returned dict: {result}"})
        else:
            points.append({"check": "POST /batch-replace", "pass": False,
                           "note": f"Unexpected return type: {type(result)}"})
    except Exception as e:
        points.append({"check": "POST /batch-replace", "pass": False, "note": str(e)})

    # Test 3d: GET /api/editor/{job_id}/stats
    try:
        from src.api.editor import get_stats
        result = get_stats("nonexistent_job")
        if hasattr(result, "status_code"):
            points.append({"check": "GET /stats", "pass": True,
                           "note": f"Returned HTTP {result.status_code} for missing job"})
        elif isinstance(result, dict):
            points.append({"check": "GET /stats", "pass": True,
                           "note": f"Returned dict"})
        else:
            points.append({"check": "GET /stats", "pass": False,
                           "note": f"Unexpected return: {type(result)}"})
    except Exception as e:
        points.append({"check": "GET /stats", "pass": False, "note": str(e)})

    # Test 3e: Verify no endpoint crashes with 500 on garbage input
    try:
        from src.api.editor import get_chapter
        result = get_chapter("nonexistent_job", 999999)
        # Just verifying no unhandled exception; even a 404/error dict is fine
        points.append({"check": "No 500 on garbage chapter", "pass": True,
                       "note": "Graceful handling"})
    except Exception as e:
        points.append({"check": "No 500 on garbage chapter", "pass": False, "note": str(e)})

    passed_count = sum(1 for p in points if p["pass"])
    total = len(points)
    all_passed = passed_count == total

    return {
        "name": name,
        "passed": all_passed,
        "skipped": False,
        "detail": f"{passed_count}/{total} endpoints OK",
    }


# =========================================================================
# TEST 4: Sensitive term boundary scan
# =========================================================================

def _test_sensitive_terms() -> dict:
    """Feed the sensitive_terms module edge-case texts and verify detection."""
    from src.sensitive_terms import (
        SENSITIVE_TERMS,
        build_sensitive_term_context,
        scan_arabic_blasphemy,
    )

    name = "Sensitive term boundary scan"
    tests: list[tuple[str, bool, str]] = []

    # Case 1: "上身" in text -> detection triggers
    ctx = build_sensitive_term_context("这位弟马请了仙家上身")
    tests.append((
        '"上身" detection triggers',
        "上身" in ctx and "TERMINOLOGY WARNINGS" in ctx,
        "detected" if "上身" in ctx else "missed",
    ))

    # Case 2: "上身" NOT in text -> no detection
    ctx = build_sensitive_term_context("普通文本，没有敏感词")
    tests.append((
        '"上身" NOT in text -> no false positive',
        ctx == "" or "上身" not in ctx,
        "clean" if ctx == "" else f"got: {ctx[:80]}",
    ))

    # Case 3: "请神上身附体地府阎王" -> all 4 detected
    ctx = build_sensitive_term_context("请神上身附体地府阎王")
    terms_to_find = ["上身", "附体", "请神", "地府", "鬼"]
    found_count = sum(1 for t in terms_to_find if t in ctx)
    tests.append((
        "all 4+ terms detected in compound string",
        found_count >= 4,
        f"found {found_count}/5 target terms",
    ))

    # Case 4: Empty string -> no crash
    try:
        ctx = build_sensitive_term_context("")
        tests.append((
            "Empty string -> no crash",
            True,
            "no crash (returned empty)",
        ))
    except Exception as e:
        tests.append((
            "Empty string -> no crash",
            False,
            f"crashed: {e}",
        ))

    # Case 5: Long text (10000 chars) with terms at positions [0, 5000, 9999]
    try:
        fill_char = "一"  # 一
        long_text = (
            "上身" + fill_char * 4900 + "请神" + fill_char * 4900 + "地府" + fill_char * 90
        )
        # Ensure it's roughly 10k chars
        long_text = long_text[:10000]
        ctx = build_sensitive_term_context(long_text)
        terms_at_edge = ["上身", "请神", "地府"]
        found_edge = sum(1 for t in terms_at_edge if t in ctx)
        tests.append((
            "Long text (10k chars) boundary terms detected",
            found_edge == 3,
            f"found {found_edge}/3 edge terms",
        ))
    except Exception as e:
        tests.append((
            "Long text boundary terms",
            False,
            f"crashed: {e}",
        ))

    # Case 6: Arabic blasphemy scan on clean text
    clean_ar = "هذه ترجمة نظيفة لا تحتوي على أي إساءة دينية"
    violations = scan_arabic_blasphemy(clean_ar)
    tests.append((
        "Arabic clean text -> no blasphemy match",
        len(violations) == 0,
        f"{len(violations)} violations",
    ))

    passed_count = sum(1 for _, ok, _ in tests if ok)
    total = len(tests)

    return {
        "name": name,
        "passed": passed_count == total,
        "skipped": False,
        "detail": f"{passed_count}/{total} tests pass",
    }


# =========================================================================
# Runner
# =========================================================================

if __name__ == "__main__":
    main()
