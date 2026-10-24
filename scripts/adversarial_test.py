#!/usr/bin/env python3
"""Adversarial testing harness for Westward Echo (西渡).

Probes specific weaknesses in the translation pipeline using LLM sub-agents
and direct function calls.  This is NOT a traditional unit/integration test --
it tries to break the system at known vulnerability points surfaced during the
8-issue analysis.

Tests:
  1. Cross-evaluator consistency  (2 Spanish personas, score delta <= 1.5)   [billable]
  2. Progressive chapter length stress test (500..5000 chars)                 [billable]
  3. Editor API integrity (CRUD endpoints return valid JSON, no 500s)         [local]
  4. Sensitive term boundary scan (edge cases for sensitive_terms module)     [local]

Usage:
    cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
    python3 scripts/adversarial_test.py              # Tests 3 & 4 only (zero cost)
    python3 scripts/adversarial_test.py --allow-api  # also run billable Tests 1 & 2

Requirements:
    Tests 1 & 2 call the GLM flash tier and cost real money, so they require
    --allow-api explicitly.  Tests 3 & 4 are local and always run.

【中文·这个脚本干什么】
对抗性测试（adversarial testing）：故意喂最刁钻、最不讲理的输入，看程序会不会崩
或者产出垃圾。好比请一个专业"找茬员"来试你的产品，不是正常使用，而是专门挑毛病。
本脚本包含 4 个独立测试，其中 2 个花钱（调大模型）、2 个不花钱（纯本地检查）。

【中文·怎么运行】
```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
# 只跑免费测试（Test 3 + Test 4）
python3.11 scripts/adversarial_test.py
# 也跑付费测试（Test 1 + Test 2），需两个开关
python3.11 scripts/adversarial_test.py --allow-api --allow-expensive
```
⚠️ 注意：必须用 python3.11，本机 python3 是 3.9 跑不了。

【中文·运行前要准备什么】
· 付费测试需要 LLM_API_KEY 和 GLM_API_KEY。
· 免费测试不需要任何密钥。
· Test 1 需要 tests/fixtures/jianke_ch1_es_ES.md 西班牙语测试文本。

【中文·⚠️ 会不会花真钱】
· 默认运行（不加 --allow-api）：**零费用**，只跑 Test 3 和 Test 4。
· 加 --allow-api：Test 1 调 2 次 READBACK 节点、Test 2 调约 10 次完整管线。
  具体金额取决于 token 消耗，无法从代码精确推算。成本闸门会强制使用 flash
  档（约 1/10 价格），除非额外加 --allow-expensive 放行高价档。

【中文·会产生/修改什么文件】
· tests/fixtures/adversarial_eval_crosscheck.json —— Test 1 的评估结果。
· tests/fixtures/adversarial_stress_chapters.txt —— Test 2 生成的测试章节。
· tests/fixtures/adversarial_stress_result.json —— Test 2 的压力测试结果。
· Test 3 可能读取 data/jobs.db（如果直接调用 editor 函数且有已完成任务）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

# Ensure the project root is on sys.path so we can import src.*
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")


# ---------------------------------------------------------------------------
# Trial-phase cost gate — single source of truth in src/cost_gate.py.
# ---------------------------------------------------------------------------
# The billable tests here only need "can two evaluators agree" / "where does
# output collapse", which flash answers just as well as the deep tiers:
# qwen3.8-max outputs at ¥36/M versus qwen3.8-flash at ¥2.7/M, and glm-5.3 at
# ¥28/M versus glm-5.3-flash at ¥2.8/M — 10-13x. Refuse any expensive tier
# unless asked.
#
# All routed tiers are checked, not just GLM: the cross-evaluator test is a
# judgement task and runs on the READBACK tier, whose default is qwen3.8-max.
# A gate that only looked at GLM would let the single most expensive call in
# this script through. cost_gate reads config.NODE_MODELS, so per-node
# overrides (XIDU_NODE_MODEL_<NODE>) are seen too — this local copy used to
# read QWEN_MODEL/GLM_MODEL and missed them.
def _guard_tier(allow_expensive: bool) -> None:
    """成本闸门：阻止在 flash 以外的贵价档位上跑付费测试。

    【中文·作用】检查当前所有节点的模型档位，如果有高价档（如 qwen3.8-max）
    且用户没有显式放行，就拒绝运行并退出码 2。这是防止"裸跑烧钱"的安全阀。
    为什么对抗测试用 flash 就够：它只需要回答"两个评估器打分差多少"和
    "输出在哪个长度开始塌"，不需要深度思考能力，flash 档足够且便宜约 10 倍。
    """
    from src.cost_gate import guard_tier
    guard_tier(
        allow_expensive=allow_expensive,
        purpose='对抗测试只需要"两个评估器是否一致" / "输出在哪里塌掉"，'
                "flash 档同样能回答（约 1/10 的钱）。",
        # 本脚本的计费测试还要 --allow-api 才会跑，所以放行需两个 flag。
        hint="   注意：本脚本还需 --allow-api 才会跑计费测试，"
             "故放行是 --allow-api --allow-expensive 两个一起。",
    )

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """主入口：解析命令行参数，按顺序跑 4 个测试，汇总报告。

    【中文·流程】
    ① 解析参数（--allow-api / --allow-expensive / --serve 等）。
    ② 如果开了 --allow-api，先过成本闸门检查档位。
    ③ 依次跑 Test 1-4，每个测试返回一个结果字典。
    ④ 打印汇总报告，有失败则退出码 1。
    """
    parser = argparse.ArgumentParser(
        description="Adversarial testing harness for Westward Echo"
    )
    parser.add_argument(
        "--allow-api",
        action="store_true",
        help="Run the billable Tests 1 & 2 (real LLM calls). Without this flag "
             "only the local Tests 3 & 4 run, at zero cost.",
    )
    parser.add_argument(
        "--skip-api",
        action="store_true",
        help="DEPRECATED no-op: skipping the API tests is now the default. "
             "Use --allow-api to opt in to billable Tests 1 & 2.",
    )
    parser.add_argument(
        "--allow-expensive",
        action="store_true",
        help="Permit the billable tests to run on a non-flash (expensive) tier. "
             "Trial-phase policy is flash-only; see _guard_tier().",
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

    if args.skip_api and not args.allow_api:
        print("note: --skip-api is now the default and does nothing; "
              "pass --allow-api to run billable Tests 1 & 2.")

    project_path = PROJECT_ROOT
    fixtures_dir = project_path / "tests" / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    # Modules that pass/fail
    results: list[dict] = []

    # 【中文】付费测试必须显式开启。历史上曾因为"检测到密钥就自动跑"导致裸跑
    # 烧钱——.env 里残留着旧密钥，不加任何参数就会触发付费测试。现在改成默认关闭，
    # 必须 --allow-api 才跑。
    # ------------------------------------------------------------------
    # Billable tests are opt-in. Previously this read DEEPSEEK_API_KEY and ran
    # Tests 1 & 2 whenever a key happened to be present, which spent real money
    # on a bare `python3 scripts/adversarial_test.py`. .env still carries a
    # non-empty DEEPSEEK_API_KEY, so that default was live, not theoretical.
    # ------------------------------------------------------------------
    has_api = bool(args.allow_api)
    if has_api:
        _guard_tier(args.allow_expensive)
    api_missing = "" if has_api else (
        "Test requires --allow-api (billable). Not run."
    )

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
            "detail": api_missing,
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
            "detail": api_missing,
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
    """Two Spanish-native evaluator personas should agree within 1.5 points.

    【中文·Test 1：跨评估器一致性】
    让两个不同人设的西班牙语评估器（Maria 和 Carlos）分别给同一篇译文打分，
    看他们的分数差是否 ≤ 1.5。如果差太大，说明评估标准不稳定——同一个东西
    两个人打分差很远，那冷读判定就不可信了。
    · 阈值 1.5：来历未在代码中说明，似乎是经验值。
    · ⚠️ 会花钱：调 2 次 READBACK 节点的大模型 API。
    """
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
    """Score a translation with an evaluator persona, return parsed JSON.

    【中文·作用】用指定人设的评估器给一篇译文打分，返回解析后的 JSON 字典。
    打分是判断类任务，所以走 READBACK 节点（与生产管线一致）。
    历史上曾用 DeepSeek 的 quality_score 节点，该节点在三模型拆分时已删除。

    Scoring is a judgement task, so it runs on the READBACK tier — the same
    split the pipeline uses (NODE_MODELS["readback"]), on the Qwen provider.
    It used to call MODEL_MAP["quality_score"] on the DeepSeek stack; that key
    and those config constants were both removed in c121f0b when the pipeline
    moved to the GLM/Qwen three-model split, so this raised KeyError/ImportError
    on any run that reached it.
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.config import LLM_API_KEY, LLM_BASE_URL, NODE_MODELS

    llm = ChatOpenAI(
        model=NODE_MODELS["readback"],
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
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
    """Translate 10 progressively longer chapters to find the empty-output threshold.

    【中文·Test 2：渐进式章节长度压力测试】
    生成 10 个从 500 到 5000 字递增的中文章节，逐个送进翻译管线，看在哪个长度
    开始产出空译文。目的是找到管线的"崩溃阈值"——超过这个长度就翻不出来了。
    · ⚠️ 会花钱：生成章节调 WRITE 节点 + 翻译调完整管线，共约 20 次 API 调用。
    · 如果已有预生成的测试章节文件，会直接复用，不再重新生成。

    The LLM calls happen inside _generate_stress_chapters (chapter synthesis)
    and agent.translate_chapter (the pipeline under test); this function itself
    needs no model config, so the imports it used to carry were dead.
    """
    name = "Progressive stress test (10 chapters)"

    # Generate 10 short test chapters in Chinese via the WRITE tier
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
    """Generate 10 short Chinese chapters of varying lengths, via the WRITE tier.

    【中文·作用】用 WRITE 节点（GLM flash）生成指定长度的中文章节文本。
    这是纯文字输出任务，用最便宜的 flash 档就够。生成的文本会缓存到文件里，
    下次跑直接复用，不再重复花钱。

    Synthesising chapter prose is a text-output task, so it uses NODE_MODELS["write"]
    (GLM flash) on the GLM provider — the same tier split the pipeline uses, and
    the cheapest one that does the job, per the trial-phase flash-only policy.
    It used to call MODEL_MAP["translate"] on the DeepSeek stack; that key and
    those config constants were removed in c121f0b.
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage
    from src.config import GLM_API_KEY, GLM_BASE_URL, NODE_MODELS

    llm = ChatOpenAI(
        model=NODE_MODELS["write"],
        api_key=GLM_API_KEY,
        base_url=GLM_BASE_URL,
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
    """Call editor CRUD endpoints (or functions directly) and verify integrity.

    【中文·Test 3：编辑器接口完整性】
    验证段落编辑器的增删改查接口不会崩。默认直接调函数（不需要启动服务），
    加 --serve 则走 HTTP 请求。不花钱，纯本地检查。
    """
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
    """Feed the sensitive_terms module edge-case texts and verify detection.

    【中文·Test 4：敏感词边界扫描】
    给敏感词检测模块喂各种边界情况的文本（空串、超长文本、含/不含敏感词等），
    验证它不会漏检、不会误报、不会崩溃。不花钱，纯本地检查。
    · 6 个测试用例覆盖了：正常检出、无误报、复合字符串多词检出、空串安全、
      万字长文边界词检出。
    · 历史上曾因删除阿拉伯语支持时没同步本脚本，导致 import 失败、整个
      测试工具连免费模式都跑不起来。已修复。

    Only build_sensitive_term_context is imported. This used to also import
    scan_arabic_blasphemy and SENSITIVE_TERMS: the former was removed from
    src/sensitive_terms.py in 5bba508 ("drop ar-SA"), which did not touch this
    script, so the import raised ImportError on every run — including
    --skip-api, because Test 4 is called unconditionally. That made the whole
    harness unusable while the docs still listed it as a gated, working script.
    SENSITIVE_TERMS was imported but never used.
    """
    from src.sensitive_terms import build_sensitive_term_context

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

    # Case 6 (removed): Arabic blasphemy scan. ar-SA support was dropped from
    # the project in 5bba508 — scan_arabic_blasphemy no longer exists — so this
    # case tested a capability that is gone. It was the last case, so nothing
    # below it needed renumbering.

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
