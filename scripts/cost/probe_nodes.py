"""Per-node token measurement on real 地府 chapters, to price the pipeline.

Records TranslationStats.record_tokens calls and attributes each to the node
that made it, so READ(qwen3.8-max) / WRITE(glm-5.3-flash) / FIX(glm-5.3) /
READBACK(qwen3.8-max) can each be priced at their own rate.
"""
import json, re, sys, traceback
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent.parent / "scripts" / "cost" / "measurements"
DATA.mkdir(parents=True, exist_ok=True)

ROOT = Path("/Users/wenyudemac/Documents/dev/Westward Echo（西渡）")
sys.path.insert(0, str(ROOT))

# ── 试验阶段成本闸门 ──────────────────────────────────────────────
# 探针只为了测 token 结构，不需要高价档。默认拒绝在 max 档上跑：
# 同一次探测，qwen3.8-max 输出价 36 元/M，qwen3.8-flash 2.7 元/M（13 倍差）。
def _guard_tier():
    import os, sys
    m = os.getenv("QWEN_MODEL", "qwen3.8-max")
    if sys.argv[-1] == "--allow-expensive" or os.getenv("XIDU_ALLOW_EXPENSIVE") == "1":
        return
    if "max" in m or "prime" in m:
        print(f"""
⛔ 拒绝运行：QWEN_MODEL={m} 是高价档，而探针只需要 token 结构。

   试验阶段请用 flash 档（同样的信息，1/13 的钱）：
       QWEN_MODEL=qwen3.8-flash  python3 {Path(sys.argv[0]).name}
       QWEN_MODEL=qwen3.7-flash  python3 {Path(sys.argv[0]).name}   # 最便宜，0.2/0.8

   确实要用 max 档，明说：
       python3 {Path(sys.argv[0]).name} --allow-expensive
       （上一轮我在 max 档上跑了 8 章，烧掉 ¥5.05；flash 档同样 8 章约 ¥0.4）
""", flush=True)
        sys.exit(2)

_guard_tier()

from src.stats import TranslationStats

CALLS = []
NODE_FILES = ("/src/agent/nodes/",)

def _node_of(stack):
    for fr in stack:
        for marker in NODE_FILES:
            if marker in fr.filename:
                return Path(fr.filename).stem
    return "?"

_orig = TranslationStats.record_tokens.__func__

def patched(cls, input_tokens, output_tokens, tier="flash"):
    CALLS.append({"node": _node_of(traceback.extract_stack()),
                  "tier": tier, "in": int(input_tokens or 0), "out": int(output_tokens or 0)})
    return _orig(cls, input_tokens, output_tokens, tier)

TranslationStats.record_tokens = classmethod(patched)

# force exactly one FIX so the FIX node's cost is measured, not guessed
import src.agent.graph as G
from src.agent.nodes.readback import readback_node as _real_readback
FORCE = {"left": 1}
def forcing_readback(state):
    out = _real_readback(state)
    if FORCE["left"] > 0 and isinstance(out, dict):
        fb = dict(out.get("readback_feedback") or {})
        fb["verdict"] = "NEEDS_FIX"
        fb.setdefault("comprehension_issues", ["(probe) fabricated issue to trigger FIX"])
        fb.setdefault("engagement_gaps", ["(probe) fabricated gap to trigger FIX"])
        out = dict(out); out["readback_feedback"] = fb
        FORCE["left"] -= 1
        print("  [probe] forced NEEDS_FIX to measure FIX node", flush=True)
    return out
G.readback_node = forcing_readback

from src.agent.graph import TranslationAgent
from src.text_io import detect_and_read
from src.chapter_splitter import split_chapters, ParagraphTag

CJK = re.compile(r'[\u4e00-\u9fff]')

text, _ = detect_and_read(str(ROOT / "tests/fixtures/《地府叫我小先生》 作者：界玉.txt"))
chapters = [c for c in split_chapters(text) if c.action != ParagraphTag.SKIP]
counts = sorted((len(CJK.findall(c.content)), i) for i, c in enumerate(chapters))
n = len(counts)
picks = {"short": counts[int(n*0.25)], "median": counts[int(n*0.50)], "long": counts[int(n*0.90)]}
print(f"地府: {n} chapters; picked {[(k, v[0]) for k, v in picks.items()]}", flush=True)

agent = TranslationAgent(book_id="cost_probe")
report = []
for label, (cjk, idx) in picks.items():
    ch = chapters[idx]
    CALLS.clear()
    print(f"\n=== {label}: Ch{ch.index} {cjk} CJK ===", flush=True)
    try:
        res = agent.translate_chapter(chapter_title=ch.title, chapter_content=ch.content,
            chapter_number=ch.index, previous_summary="", target_lang="en-US",
            genre="folk_religion", skip_readback=False, use_flash_writer=False,
            content_type="novel")
        verdict = (res.get("readback_feedback") or {}).get("verdict")
        out_len = len(res.get("translated_text", "") or "")
    except Exception as e:
        print("  FAILED:", type(e).__name__, str(e)[:200], flush=True)
        verdict, out_len = f"ERROR:{type(e).__name__}", 0
    per = {}
    for c in CALLS:
        a = per.setdefault(c["node"], {"n": 0, "in": 0, "out": 0})
        a["n"] += 1; a["in"] += c["in"]; a["out"] += c["out"]
    for node, a in sorted(per.items()):
        print(f"  {node:<14} n={a['n']:<3} in={a['in']:<7} out={a['out']:<7} tot={a['in']+a['out']}", flush=True)
    report.append({"label": label, "chapter": ch.index, "cjk": cjk,
                   "src_chars": len(ch.content), "out_chars": out_len,
                   "verdict": verdict, "per_node": per,
                   "total": sum(a["in"]+a["out"] for a in per.values())})
    Path(str(DATA / "cost_probe.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2))

Path(str(DATA / "cost_probe.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2))
print("\nDONE -> scripts/cost/measurements/cost_probe.json", flush=True)
