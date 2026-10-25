#!/usr/bin/env python3.11
"""READ tier A/B: does qwen3.8-flash degrade the delivered translation?

The cost model says READ (not WRITE) dominates the bill, and that moving READ
from qwen3.8-max (12/36 CNY per M) to qwen3.8-flash (0.8/2.7) would cut a book's
cost by ~90%. That experiment was never run because the quality question was
left open. This script runs it on real chapters.

Design
------
- One variable: ``XIDU_NODE_MODEL_READ``. WRITE and its model are fixed, and
  both arms see the same source, glossary snapshot, and empty prev-summary.
- Each arm runs READ -> WRITE directly (no READBACK/FIX loop), so the comparison
  is about what READ's analysis does to the translation, not about repair.
- Judgment is blind and external: qwen3.8-max (a different family from the GLM
  writer) sees the Chinese source and two unlabelled English versions, in both
  orders. A verdict that flips with order is recorded as noise, not a win.
- Every call is metered per arm into an isolated ledger; the script prints the
  real CNY and wall-clock delta at the end.

Limitations stated up front
---------------------------
Isolated chapters (no prev-summary) are harsher than production for *both* arms
equally, so the A/B delta is meaningful even though absolute quality is not.
READBACK/FIX are deliberately out of scope.

Usage::

    python3.11 scripts/ab_read_tier.py                 # dry run: chapters + estimate
    python3.11 scripts/ab_read_tier.py --run           # run both arms + judge
    python3.11 scripts/ab_read_tier.py --run --chapters 11,173

【中文·这个脚本干什么】
验证"把 READ 从 qwen3.8-max 降到 flash，译文会不会变差"。这是唯一能省约
90% 成本的杠杆，此前从未验证过。默认只做 dry-run；`--run` 才真正调用模型。
两臂只有 READ 档位不同，其余输入完全相同；由另一个模型家族盲评两份译文，
并且正反两个顺序各评一次，顺序一换结论就变的算噪音、不算胜负。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from scripts.run_novel import NOVELS  # noqa: E402
from src import token_ledger  # noqa: E402
from src.agent.parse_utils import parse_llm_json  # noqa: E402
from src.chapter_splitter import ParagraphTag, split_chapters  # noqa: E402
from src.config import MODEL_PRICES_CNY_PER_M, node_llm_kwargs  # noqa: E402
from src.text_io import detect_and_read  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TIER_A = "qwen3.8-max"
TIER_B = "qwen3.8-flash"
DEFAULT_CHAPTERS = [11, 173, 604, 1238, 1800, 2301]
CJK = re.compile(r"[一-鿿]")

_JUDGE_SYSTEM = """You are an experienced English editor comparing two translations
of the same Chinese web-novel chapter.

Judge only on:
1. Fidelity to the source (meaning, names, numbers, relationships).
2. Completeness (nothing omitted, nothing cut off mid-sentence).
3. Natural English prose (idiom, rhythm, register).

Ignore formatting, markdown, and chapter titles.

Output ONLY JSON:
{"winner": "1" | "2" | "tie", "fidelity": "1"|"2"|"tie",
 "completeness": "1"|"2"|"tie", "prose": "1"|"2"|"tie",
 "reason": "<one sentence>"}
"""

_WORKER_SRC = '''"""Run READ -> WRITE for one arm in a fresh process.

A subprocess is required: config.NODE_MODELS is evaluated at import time, so
changing XIDU_NODE_MODEL_READ after import has no effect in-process.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["XIDU_AB_ROOT"])
sys.path.insert(0, str(ROOT))

payload = json.loads(Path(os.environ["XIDU_AB_PAYLOAD"]).read_text(encoding="utf-8"))
glossary = json.loads(Path(os.environ["XIDU_AB_GLOSSARY"]).read_text(encoding="utf-8"))

from src.agent.graph import TranslationAgent  # noqa: E402
from src.agent.nodes.read import read_node  # noqa: E402
from src.agent.nodes.review_terms import review_terms_node  # noqa: E402
from src.agent.nodes.write import write_node  # noqa: E402

agent = TranslationAgent(book_id=payload["book_id"])
if glossary:
    agent.load_glossary_snapshot(
        json.dumps(glossary, ensure_ascii=False), target_lang="en-US"
    )

rows = []
for item in payload["chapters"]:
    try:
        state = agent._make_state(
            item["title"], item["content"], item["ch"], "",
            "en-US", payload["genre"], skip_readback=True,
        )
        state.update(read_node(state, agent.exact_store, agent.semantic_store) or {})
        state.update(review_terms_node(state) or {})
        out = write_node(state) or {}
        rows.append({
            "ch": item["ch"], "ok": True,
            "title": item["title"],
            "text": out.get("translated_text") or "",
            "analysis_len": len(json.dumps(state.get("read_analysis") or {}, ensure_ascii=False)),
        })
    except Exception as exc:
        rows.append({"ch": item["ch"], "ok": False, "error": repr(exc)})

Path(os.environ["XIDU_AB_RESULT"]).write_text(
    json.dumps(rows, ensure_ascii=False), encoding="utf-8")
'''


def load_chapters(cfg, indexes: list[int]) -> list[dict]:
    text, _ = detect_and_read(cfg["path"])
    by_index = {c.index: c for c in split_chapters(text)
                if c.action != ParagraphTag.SKIP}
    missing = [i for i in indexes if i not in by_index]
    if missing:
        raise SystemExit(f"chapters not found in source: {missing}")
    return [{"ch": i,
             "title": by_index[i].title,
             "content": by_index[i].content,
             "cjk": by_index[i].word_count} for i in indexes]


def estimate(cfg, chapters, judge_per_chapter=0.22) -> None:
    read_a = MODEL_PRICES_CNY_PER_M[TIER_A]
    read_b = MODEL_PRICES_CNY_PER_M[TIER_B]
    write = MODEL_PRICES_CNY_PER_M[node_llm_kwargs("write")["model"]]
    total = 0.0
    for ch in chapters:
        a = 7000 / 1e6 * read_a[0] + 9000 / 1e6 * read_a[1]
        b = 7000 / 1e6 * read_b[0] + 10000 / 1e6 * read_b[1]
        w = 10000 / 1e6 * write[0] + 3000 / 1e6 * write[1]
        total += a + b + 2 * w + judge_per_chapter
    print(f"estimated cost for {len(chapters)} chapters: ¥{total:.2f}")


def run_arm(tier: str, payload: dict, glossary: dict, out_dir: Path) -> list[dict]:
    tag = tier.replace(".", "_")
    worker = out_dir / "_worker.py"
    worker.write_text(_WORKER_SRC, encoding="utf-8")
    payload_path = out_dir / f"_payload_{tag}.json"
    glossary_path = out_dir / "_glossary_snapshot.json"
    result_path = out_dir / f"_result_{tag}.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    glossary_path.write_text(json.dumps(glossary, ensure_ascii=False), encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "XIDU_AB_ROOT": str(ROOT),
        "XIDU_AB_PAYLOAD": str(payload_path),
        "XIDU_AB_GLOSSARY": str(glossary_path),
        "XIDU_AB_RESULT": str(result_path),
        "XIDU_NODE_MODEL_READ": tier,
        "XIDU_TOKEN_LEDGER": str(out_dir / f"ledger_{tag}.jsonl"),
    })
    proc = subprocess.run([sys.executable, str(worker)], env=env,
                          cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"arm {tier} failed:\n{proc.stderr[-3000:]}")
    return json.loads(result_path.read_text(encoding="utf-8"))


def _judge(llm, source, text1, text2, book_id, model, label) -> dict:
    user = (
        f"SOURCE (Chinese):\n{source}\n\n"
        f"VERSION 1:\n{text1}\n\n"
        f"VERSION 2:\n{text2}\n"
    )
    from langchain_core.messages import HumanMessage, SystemMessage
    messages = [SystemMessage(content=_JUDGE_SYSTEM), HumanMessage(content=user)]
    response, elapsed = token_ledger.timed_call(llm.invoke, messages)
    token_ledger.record_response(response, "ab_judge", model,
                                 book_id=book_id, elapsed_s=elapsed)
    content = response.content if isinstance(response.content, str) else str(response.content)
    parsed, _ = parse_llm_json(content, {})
    if not parsed:
        parsed = {"winner": "parse_error", "reason": content[:200]}
    parsed["label"] = label
    return parsed


def _build_judge():
    from langchain_openai import ChatOpenAI
    kwargs = node_llm_kwargs("review_terms")     # qwen3.8-max, judging tier
    return ChatOpenAI(  # type: ignore[call-arg]
        temperature=0.0, max_tokens=4096, request_timeout=180,
        max_retries=2, **kwargs,
    )


def resolve_verdict(forward_winner: str, reverse_winner: str) -> str:
    """Map two order-swapped judgments back to an arm.

    In the forward pass "1" is A and "2" is B; in the reverse pass they swap.
    A verdict only counts when both orders point at the same arm — otherwise
    the difference is position bias, not quality.
    """
    arm_forward = {"1": "A", "2": "B", "tie": "tie"}.get(forward_winner, "?")
    arm_reverse = {"1": "B", "2": "A", "tie": "tie"}.get(reverse_winner, "?")
    # A judge that failed to produce a verdict is not evidence of noise.
    # 【中文】裁判调用失败（解析不出 JSON）不等于"两种顺序结论相反"，
    # 混在一起会把"测不出来"伪装成"没有差异"。
    if arm_forward == "?" or arm_reverse == "?":
        return "judge_error"
    if arm_forward == arm_reverse and arm_forward in ("A", "B"):
        return arm_forward
    if arm_forward == "tie" and arm_reverse == "tie":
        return "tie"
    return "noise"


def _judge_all(cfg, chapters, rows_a, rows_b) -> list[dict]:
    """Blind pairwise judgment, both orders, so position bias is visible."""
    llm = _build_judge()
    judge_model = node_llm_kwargs("review_terms")["model"]
    judge_book = f"{cfg['book_id']}_read_ab_judge"
    a = {r["ch"]: r for r in rows_a}
    b = {r["ch"]: r for r in rows_b}
    verdicts = []
    for ch in chapters:
        ra, rb = a.get(ch["ch"]), b.get(ch["ch"])
        if not ra or not rb or not ra.get("ok") or not rb.get("ok"):
            continue
        forward = _judge(llm, ch["content"], ra["text"], rb["text"],
                         judge_book, judge_model, "A_first")
        reverse = _judge(llm, ch["content"], rb["text"], ra["text"],
                         judge_book, judge_model, "B_first")
        w1 = forward.get("winner")
        w2 = reverse.get("winner")
        verdict = resolve_verdict(w1, w2)
        verdicts.append({
            "ch": ch["ch"], "verdict": verdict,
            "forward": forward, "reverse": reverse,
            "fidelity": forward.get("fidelity"), "completeness": forward.get("completeness"),
            "prose": forward.get("prose"),
        })
    return verdicts


def objective(text: str, cjk: int, glossary: dict) -> dict:
    stripped = text.strip()
    terms = [en for cn, en in glossary.items()
             if cn in stripped or en.lower() in stripped.lower()]
    return {
        "chars": len(text),
        "ratio": round(len(text) / cjk, 2) if cjk else 0,
        "chinese_chars": len(CJK.findall(text)),
        "ends_on_punctuation": bool(stripped) and stripped[-1] in '.!?"”’',
        "glossary_terms_present": len(terms),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("book", nargs="?", default="difu", choices=sorted(NOVELS))
    parser.add_argument("--chapters", default="",
                        help="comma-separated source chapter indexes")
    parser.add_argument("--run", action="store_true",
                        help="actually call the models (default: dry run)")
    parser.add_argument("--judge-only", action="store_true",
                        help="reuse outputs.json and only run the blind judge")
    parser.add_argument("--out", default="",
                        help="output directory (default: archive/ab_read_tier_<date>)")
    args = parser.parse_args(argv)

    cfg = NOVELS[args.book]
    indexes = ([int(x) for x in args.chapters.split(",") if x.strip()]
               if args.chapters else DEFAULT_CHAPTERS)
    chapters = load_chapters(cfg, indexes)

    print(f"READ A/B on {cfg['name']}: {len(chapters)} chapters {indexes}")
    print(f"  A = {TIER_A}   B = {TIER_B}   WRITE fixed = "
          f"{node_llm_kwargs('write')['model']}")
    estimate(cfg, chapters)
    if not args.run and not args.judge_only:
        print("dry run — pass --run to spend it")
        return 0

    out_dir = Path(args.out) if args.out else (
        ROOT / "archive" / f"ab_read_tier_{datetime.now(timezone.utc):%Y%m%d}")
    out_dir.mkdir(parents=True, exist_ok=True)
    glossary = json.loads((Path(cfg["output_dir"]) / "_glossary.json").read_text(
        encoding="utf-8")) if (Path(cfg["output_dir"]) / "_glossary.json").exists() else {}
    payload = {"book_id": cfg["book_id"], "genre": cfg["genre"], "chapters": chapters}

    if args.judge_only:
        outputs = json.loads((out_dir / "outputs.json").read_text(encoding="utf-8"))
        rows_a, rows_b = outputs["A"], outputs["B"]
    else:
        print("running arm A (max)…")
        rows_a = run_arm(TIER_A, payload, glossary, out_dir)
        print("running arm B (flash)…")
        rows_b = run_arm(TIER_B, payload, glossary, out_dir)
        # Persist the raw outputs before judging: a judge failure must not lose
        # the thing money was spent on.
        (out_dir / "outputs.json").write_text(
            json.dumps({"A": rows_a, "B": rows_b}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    print("judging (blind, both orders)…")
    verdicts = _judge_all(cfg, chapters, rows_a, rows_b)

    _report(cfg, chapters, rows_a, rows_b, glossary, out_dir, verdicts)
    return 0


def _report(cfg, chapters, rows_a, rows_b, glossary, out_dir, verdicts) -> None:
    a = {r["ch"]: r for r in rows_a}
    b = {r["ch"]: r for r in rows_b}
    cost_a = _ledger_total(out_dir / f"ledger_{TIER_A.replace('.', '_')}.jsonl")
    cost_b = _ledger_total(out_dir / f"ledger_{TIER_B.replace('.', '_')}.jsonl")
    judge_cost = token_ledger.summary_from_file(
        book_id=f"{cfg['book_id']}_read_ab_judge")
    print(f"\ncost: A ¥{cost_a['cost_cny']:.4f} ({cost_a['timing']['total_s']:.0f}s) | "
          f"B ¥{cost_b['cost_cny']:.4f} ({cost_b['timing']['total_s']:.0f}s) | "
          f"judge ¥{judge_cost['cost_cny']:.4f}")

    results = []
    for ch in chapters:
        ra, rb = a.get(ch["ch"]), b.get(ch["ch"])
        if not ra or not rb or not ra.get("ok") or not rb.get("ok"):
            results.append({"ch": ch["ch"], "error": "arm failed"})
            continue
        results.append({
            "ch": ch["ch"],
            "objective": {
                "A": objective(ra["text"], ch["cjk"], glossary),
                "B": objective(rb["text"], ch["cjk"], glossary),
            },
        })

    judged = {v["ch"] for v in verdicts}
    unjudged = [ch["ch"] for ch in chapters if ch["ch"] not in judged]

    path = out_dir / "results.json"
    path.write_text(json.dumps({
        "book": cfg["name"], "tier_a": TIER_A, "tier_b": TIER_B,
        "cost": {
            "A": {**cost_a, "available": bool(cost_a.get("calls"))},
            "B": {**cost_b, "available": bool(cost_b.get("calls"))},
            "judge": {**judge_cost, "available": bool(judge_cost.get("calls"))},
        },
        "judged": len(judged),
        "unjudged": unjudged,
        "results": results,
        "verdicts": verdicts,
        "outputs": {"A": rows_a, "B": rows_b},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    for item in results:
        if "error" in item:
            print(f"  Ch{item['ch']}: {item['error']}")
            continue
        oa, ob = item["objective"]["A"], item["objective"]["B"]
        print(f"  Ch{item['ch']}: ratio {oa['ratio']} vs {ob['ratio']}, "
              f"truncated A={not oa['ends_on_punctuation']} "
              f"B={not ob['ends_on_punctuation']}, "
              f"glossary A={oa['glossary_terms_present']} B={ob['glossary_terms_present']}")

    counts = {}
    for verdict in verdicts:
        counts[verdict["verdict"]] = counts.get(verdict["verdict"], 0) + 1
    print(f"\nblind verdicts (both orders must agree): {counts}")
    if len(judged) < len(chapters):
        print(f"  WARNING: {len(chapters) - len(judged)} chapter(s) unjudged "
              f"(arm or judge failure): {sorted(set(ch['ch'] for ch in chapters) - judged)}")
    for verdict in verdicts:
        print(f"  Ch{verdict['ch']}: {verdict['verdict']} — "
              f"A: {verdict['forward'].get('reason', '')[:90]}")


def _ledger_total(path: Path) -> dict:
    """Per-arm cost. A missing ledger must be visible, not reported as ¥0."""
    if not path.exists():
        print(f"WARNING: arm ledger missing: {path}")
    return token_ledger.summary_from_file(path=path)


if __name__ == "__main__":
    raise SystemExit(main())
