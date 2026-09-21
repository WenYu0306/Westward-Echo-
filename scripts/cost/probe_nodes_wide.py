"""Wide-range per-node token probe: extremes of 地府 + real 无限恐怖 chapters.

cost_probe.py only sampled 1065-1436 CJK (地府 is unusually uniform), which is
too narrow to fit fixed-vs-marginal cost. This adds the extremes and the second
book so the linear fit spans 76 -> 6541 CJK.
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
def _node_of(stack):
    for fr in stack:
        if "/src/agent/nodes/" in fr.filename:
            return Path(fr.filename).stem
    return "?"
_orig = TranslationStats.record_tokens.__func__
def patched(cls, input_tokens, output_tokens, tier="flash"):
    CALLS.append({"node": _node_of(traceback.extract_stack()), "tier": tier,
                  "in": int(input_tokens or 0), "out": int(output_tokens or 0)})
    return _orig(cls, input_tokens, output_tokens, tier)
TranslationStats.record_tokens = classmethod(patched)

from src.agent.graph import TranslationAgent
from src.text_io import detect_and_read
from src.chapter_splitter import split_chapters, ParagraphTag

CJK = re.compile(r'[\u4e00-\u9fff]')
BOOKS = {
    "difu": (ROOT / "tests/fixtures/《地府叫我小先生》 作者：界玉.txt", "folk_religion"),
    "wuxian": (ROOT / "tests/fixtures/《无限恐怖》 作者：zhttty.txt", "urban"),
}
loaded = {}
for k, (p, g) in BOOKS.items():
    text, _ = detect_and_read(str(p))
    chs = [c for c in split_chapters(text) if c.action != ParagraphTag.SKIP]
    loaded[k] = (chs, g, sorted((len(CJK.findall(c.content)), i) for i, c in enumerate(chs)))

def pick(book, frac):
    counts = loaded[book][2]
    n = len(counts)
    return counts[min(n - 1, int(n * frac))]

PLAN = [("difu", 0.0, "difu-min"), ("wuxian", 0.50, "wuxian-p50"),
        ("wuxian", 0.90, "wuxian-p90"), ("difu", 1.0, "difu-max")]

agent = TranslationAgent(book_id="cost_probe2")
report = []
for book, frac, label in PLAN:
    chs, genre, _ = loaded[book]
    cjk, idx = pick(book, frac)
    ch = chs[idx]
    CALLS.clear()
    print(f"\n=== {label}: Ch{ch.index} {cjk} CJK (genre={genre}) ===", flush=True)
    try:
        res = agent.translate_chapter(chapter_title=ch.title, chapter_content=ch.content,
            chapter_number=ch.index, previous_summary="", target_lang="en-US",
            genre=genre, skip_readback=False, use_flash_writer=False, content_type="novel")
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
    report.append({"label": label, "book": book, "chapter": ch.index, "cjk": cjk,
                   "src_chars": len(ch.content), "out_chars": out_len, "verdict": verdict,
                   "per_node": per, "total": sum(a["in"] + a["out"] for a in per.values())})
    Path(str(DATA / "cost_probe_wide.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2))
Path(str(DATA / "cost_probe_wide.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2))
print("\nDONE -> scripts/cost/measurements/cost_probe_wide.json", flush=True)
