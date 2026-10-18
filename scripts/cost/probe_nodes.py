"""Per-node token measurement on real 地府 chapters, to price the pipeline.

Records TranslationStats.record_tokens calls and attributes each to the node
that made it, so READ(qwen3.8-max) / WRITE(glm-5.3-flash) / FIX(glm-5.3) /
READBACK(qwen3.8-max) can each be priced at their own rate.

【中文·这个脚本干什么】
真实跑几个章节的翻译流程，记录每个节点（READ/WRITE/FIX/READBACK）
实际消耗了多少 token。这些实测数据是后续算"翻一本书花多少钱"的基础。

【中文·怎么运行】
在项目根目录执行：
```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
python3.11 scripts/cost/probe_nodes.py
# 如果当前配置用了高价模型（qwen3.8-max），闸门会拦住。
# 确认要花这笔钱后加 --allow-expensive：
python3.11 scripts/cost/probe_nodes.py --allow-expensive
```

【中文·运行前要准备什么】
① 配好 LLM_API_KEY 和 GLM_API_KEY 环境变量（或 .env 文件）
② tests/fixtures/ 下要有《地府叫我小先生》原文文件
③ 确保没有其他人在同时编辑项目源码

【中文·⚠️ 会不会花真钱】
⚠️ **会真实调用大模型 API 产生费用**。
每跑一个章节，5 个节点各调一次 API。按 config.py 默认配置
（READ/READBACK 用 qwen3.8-max，WRITE 用 glm-5.3-flash，
FIX 用 glm-5.3），每章大约消耗 2~3 万 token。
本脚本跑 3 个章节（短/中/长各一），预计总花费约 ¥0.3~0.5。
具体金额取决于章节长度和实际 token 消耗，以上仅为粗略估算。
单价依据：config.py MODEL_PRICES_CNY_PER_M（抓取于 2026-09-22）。

【中文·会产生/修改什么文件】
· 产出: scripts/cost/measurements/cost_probe.json（实测数据）
· ⚠️ 不修改任何源码文件
"""
import json, re, sys, traceback
from pathlib import Path

# 【中文】实测数据的输出目录。
DATA = Path(__file__).resolve().parent.parent.parent / "scripts" / "cost" / "measurements"
DATA.mkdir(parents=True, exist_ok=True)

ROOT = Path("/Users/wenyudemac/Documents/dev/Westward Echo（西渡）")
sys.path.insert(0, str(ROOT))

# ── 试验阶段成本闸门（单一来源：src/cost_gate.py）──────────────────────
# 探针只为了测 token 结构，不需要高价档。这份 _guard_tier 曾是四个副本的
# 原型，只读 QWEN_MODEL：逐节点覆盖 XIDU_NODE_MODEL_<NODE> 升档时它会放行，
# 而实际按高价档计费。改读 config.NODE_MODELS 后两个方向都正确。
# 本脚本无 argparse，--allow-expensive 从 argv 直接识别后交给统一闸门。
#
# 【中文】成本闸门：检查当前配置有没有用到高价模型（名字含 max/prime）。
# 如果有就拦住并要求 --allow-expensive 确认。防止不小心烧钱。
# 详见 src/cost_gate.py 的注释。
from src.cost_gate import guard_tier

guard_tier(
    allow_expensive="--allow-expensive" in sys.argv,
    purpose="探针只需要 token 结构，flash 档给的信息完全一样（约 1/13 的钱）。",
)

from src.stats import TranslationStats

# 【中文】CALLS 列表用来收集所有 API 调用的 token 用量。
# 下面的 patched 函数会在每次 record_tokens 被调用时往这里追加一条记录。
CALLS = []
# 【中文】节点代码所在的目录标记，用来从调用栈里识别"是哪个节点在调 API"。
NODE_FILES = ("/src/agent/nodes/",)


def _node_of(stack):
    """从调用栈里找出是哪个翻译节点触发了这次 API 调用。

    【中文】遍历调用栈的每一帧，如果文件名包含 /src/agent/nodes/，
    就取文件名（不含扩展名）作为节点名（如 read、write、fix）。
    找不到就返回 "?"。
    """
    for fr in stack:
        for marker in NODE_FILES:
            if marker in fr.filename:
                return Path(fr.filename).stem
    return "?"


# 【中文】保存原始的 record_tokens 方法，patched 里还要调它。
_orig = TranslationStats.record_tokens.__func__


def patched(cls, input_tokens, output_tokens, tier="flash"):
    """拦截 record_tokens 调用，记录 token 用量到 CALLS 列表。

    【中文】这是 monkey-patch：把 TranslationStats.record_tokens 替换成
    这个函数。每次有节点调 API 后记账时，我们先记一条到 CALLS
    （带上是从哪个节点来的），然后再调原始方法正常记账。
    """
    CALLS.append({"node": _node_of(traceback.extract_stack()),
                  "tier": tier, "in": int(input_tokens or 0), "out": int(output_tokens or 0)})
    return _orig(cls, input_tokens, output_tokens, tier)


TranslationStats.record_tokens = classmethod(patched)

# force exactly one FIX so the FIX node's cost is measured, not guessed
#
# 【中文】强制触发一次 FIX 节点。正常情况下冷读者可能说"没问题"，
# FIX 就不会被调用，我们就测不到 FIX 的 token 消耗。
# 所以这里 monkey-patch readback_node，让第一次冷读结果必定返回
# NEEDS_FIX，从而触发 FIX 节点。只强制一次（FORCE["left"] = 1），
# 后续的冷读恢复正常行为。
import src.agent.graph as G
from src.agent.nodes.readback import readback_node as _real_readback
FORCE = {"left": 1}


def forcing_readback(state):
    """包装后的冷读节点：第一次强制返回 NEEDS_FIX 以触发 FIX。"""
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

# 【中文】加载《地府叫我小先生》全文并分章。
text, _ = detect_and_read(str(ROOT / "tests/fixtures/《地府叫我小先生》 作者：界玉.txt"))
chapters = [c for c in split_chapters(text) if c.action != ParagraphTag.SKIP]

# 【中文】按汉字数排序，选三个代表性章节：
# short = 25% 分位（偏短的章节）
# median = 50% 分位（中等长度）
# long = 90% 分位（偏长的章节）
# 这样能覆盖不同长度的 token 消耗差异。
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
        # 【中文】调用完整的翻译流程（含所有节点），
        # patched 的 record_tokens 会自动记录每次 API 调用的 token。
        res = agent.translate_chapter(chapter_title=ch.title, chapter_content=ch.content,
            chapter_number=ch.index, previous_summary="", target_lang="en-US",
            genre="folk_religion", skip_readback=False, use_flash_writer=False,
            content_type="novel")
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
    report.append({"label": label, "chapter": ch.index, "cjk": cjk,
                   "src_chars": len(ch.content), "out_chars": out_len,
                   "verdict": verdict, "per_node": per,
                   "total": sum(a["in"]+a["out"] for a in per.values())})
    # 【中文】每跑完一章就写一次文件，防止中途崩溃丢失已完成的数据。
    Path(str(DATA / "cost_probe.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2))

# 【中文】最终再写一次确保完整。
Path(str(DATA / "cost_probe.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2))
print("\nDONE -> scripts/cost/measurements/cost_probe.json", flush=True)
