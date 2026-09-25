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

# ── 试验阶段成本闸门（单一来源：src/cost_gate.py）──────────────────────
# 探针只为了测 token 结构，不需要高价档。这份 _guard_tier 曾是四个副本的
# 原型，只读 QWEN_MODEL：逐节点覆盖 XIDU_NODE_MODEL_<NODE> 升档时它会放行，
# 而实际按高价档计费。改读 config.NODE_MODELS 后两个方向都正确。
# 本脚本无 argparse，--allow-expensive 从 argv 直接识别后交给统一闸门。
from src.cost_gate import guard_tier

guard_tier(
    allow_expensive="--allow-expensive" in sys.argv,
    purpose="探针只需要 token 结构，flash 档给的信息完全一样（约 1/13 的钱）。",
)

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
