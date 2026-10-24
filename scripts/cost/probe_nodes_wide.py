"""Wide-range per-node token probe: extremes of 地府 + real 无限恐怖 chapters.

cost_probe.py only sampled 1065-1436 CJK (地府 is unusually uniform), which is
too narrow to fit fixed-vs-marginal cost. This adds the extremes and the second
book so the linear fit spans 76 -> 6541 CJK.

【中文·这个脚本干什么】
probe_nodes.py 只在《地府》里选了三个中等长度的章节，
汉字数范围太窄（1065~1436），没法准确拟合"固定开销 vs 随字数增长的开销"。
本脚本扩大采样范围：加入极短（76 字）和极长（6541 字）的章节，
并跨到第二本书《无限恐怖》，让线性回归的数据跨度从 76 到 6541 汉字。

【中文·怎么运行】
在项目根目录执行：
```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
python3.11 scripts/cost/probe_nodes_wide.py
# 高价模型闸门同 probe_nodes.py：
python3.11 scripts/cost/probe_nodes_wide.py --allow-expensive
```

【中文·运行前要准备什么】
① 配好 LLM_API_KEY 和 GLM_API_KEY 环境变量
② tests/fixtures/ 下要有两本书的原文文件
③ 确保没有其他人在同时编辑项目源码

【中文·⚠️ 会不会花真钱】
⚠️ **会真实调用大模型 API 产生费用**。
本脚本跑 4 个章节（两本书各两个极端），预计总花费约 ¥0.5~0.8。
具体金额取决于实际 token 消耗，以上仅为粗略估算。
单价依据：config.py MODEL_PRICES_CNY_PER_M（抓取于 2026-09-22）。

【中文·会产生/修改什么文件】
· 产出: scripts/cost/measurements/cost_probe_wide.json
· ⚠️ 不修改任何源码文件
"""
import json, re, sys, traceback
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent.parent / "scripts" / "cost" / "measurements"
DATA.mkdir(parents=True, exist_ok=True)

ROOT = Path("/Users/wenyudemac/Documents/dev/Westward Echo（西渡）")
sys.path.insert(0, str(ROOT))
# ── 试验阶段成本闸门（单一来源：src/cost_gate.py）──────────────────────
# 探针只为了测 token 结构，不需要高价档。原先这里有一份 _guard_tier 副本，
# 只读 QWEN_MODEL：逐节点覆盖 XIDU_NODE_MODEL_<NODE> 升档时它会放行，
# 而实际按高价档计费。改读 config.NODE_MODELS 后两个方向都正确。
# 本脚本无 argparse，--allow-expensive 从 argv 直接识别后交给统一闸门。
#
# 【中文】成本闸门，防止不小心用高价模型烧钱。详见 src/cost_gate.py。
from src.cost_gate import guard_tier

guard_tier(
    allow_expensive="--allow-expensive" in sys.argv,
    purpose="探针只需要 token 结构，flash 档给的信息完全一样（约 1/13 的钱）。",
)

from src.stats import TranslationStats

# 【中文】CALLS 收集所有 API 调用的 token 记录（同 probe_nodes.py）。
CALLS = []


def _node_of(stack):
    """从调用栈识别触发 API 调用的节点名。"""
    for fr in stack:
        if "/src/agent/nodes/" in fr.filename:
            return Path(fr.filename).stem
    return "?"


_orig = TranslationStats.record_tokens.__func__


def patched(cls, input_tokens, output_tokens, tier="flash"):
    """拦截 record_tokens，记录 token 到 CALLS 列表。"""
    CALLS.append({"node": _node_of(traceback.extract_stack()), "tier": tier,
                  "in": int(input_tokens or 0), "out": int(output_tokens or 0)})
    return _orig(cls, input_tokens, output_tokens, tier)


TranslationStats.record_tokens = classmethod(patched)

from src.agent.graph import TranslationAgent
from src.text_io import detect_and_read
from src.chapter_splitter import split_chapters, ParagraphTag

CJK = re.compile(r'[\u4e00-\u9fff]')

# 【中文】两本书的路径和类型标签。
BOOKS = {
    "difu": (ROOT / "tests/fixtures/《地府叫我小先生》 作者：界玉.txt", "folk_religion"),
    "wuxian": (ROOT / "tests/fixtures/《无限恐怖》 作者：zhttty.txt", "urban"),
}
loaded = {}
for k, (p, g) in BOOKS.items():
    text, _ = detect_and_read(str(p))
    chs = [c for c in split_chapters(text) if c.action != ParagraphTag.SKIP]
    # 【中文】按汉字数排序，方便后面按百分位选取。
    loaded[k] = (chs, g, sorted((len(CJK.findall(c.content)), i) for i, c in enumerate(chs)))


def pick(book, frac):
    """按百分位选取章节。frac=0.0 取最短，1.0 取最长。"""
    counts = loaded[book][2]
    n = len(counts)
    return counts[min(n - 1, int(n * frac))]


# 【中文】采样计划：4 个章节，覆盖两本书的极端长度。
# difu-min = 地府最短章（~76 汉字），wuxian-p50 = 无限恐怖中位章，
# wuxian-p90 = 无限恐怖 90% 分位章，difu-max = 地府最长章。
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
            genre=genre, skip_readback=False, content_type="novel")
        verdict = (res.get("readback_feedback") or {}).get("verdict")
        out_len = len(res.get("translated_text", "") or "")
    except Exception as e:
        print("  FAILED:", type(e).__name__, str(e)[:200], flush=True)
        verdict, out_len = f"ERROR:{type(e).__name__}", 0
    # 【中文】汇总本章各节点的 token 消耗。
    per = {}
    for c in CALLS:
        a = per.setdefault(c["node"], {"n": 0, "in": 0, "out": 0})
        a["n"] += 1; a["in"] += c["in"]; a["out"] += c["out"]
    for node, a in sorted(per.items()):
        print(f"  {node:<14} n={a['n']:<3} in={a['in']:<7} out={a['out']:<7} tot={a['in']+a['out']}", flush=True)
    report.append({"label": label, "book": book, "chapter": ch.index, "cjk": cjk,
                   "src_chars": len(ch.content), "out_chars": out_len, "verdict": verdict,
                   "per_node": per, "total": sum(a["in"] + a["out"] for a in per.values())})
    # 【中文】每跑完一章就写一次，防中途崩溃丢数据。
    Path(str(DATA / "cost_probe_wide.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2))
Path(str(DATA / "cost_probe_wide.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2))
print("\nDONE -> scripts/cost/measurements/cost_probe_wide.json", flush=True)
