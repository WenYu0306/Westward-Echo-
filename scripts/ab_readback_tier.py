#!/usr/bin/env python3.11
"""READBACK 档位 A/B 对比 —— 方案1（受控对比，复用已交付译文）。

回答的问题（条款12 收尾 / 条款3 相关）：把 READBACK 从 qwen3.8-max 降到
qwen3.8-flash，**冷读判定会不会变差**？降幅实测 13.8 倍（in 12→0.8、
out 36→2.7，单价 元/百万 token）。

════════════════════════════════════════════════════════════════════
为什么是"方案1"而不是"完整管线 A/B"（方案2）
════════════════════════════════════════════════════════════════════
方案2（READ+WRITE+READBACK+FIX 全跑两组）有归因缺陷，已排除：
  1. 降档杠杆只动 READBACK 一个节点（XIDU_NODE_MODEL_READBACK），其余 5 个
     节点路由不变；
  2. 但方案2 会把 WRITE 也重跑，于是两组的**译文本身就不一样**；
  3. 随机性关不掉：全仓零 seed，各节点 temperature > 0
     （write.py:51=0.2、readback.py:53=0.1）。
→ 判定差异 = 译文差异（噪音）+ 档位差异（信号），无法分离。

本脚本实现方案1：两组喂**同一份已交付译文**，唯一变量是 READBACK 档位。

════════════════════════════════════════════════════════════════════
判据（刻意不用 verdict 一致率）
════════════════════════════════════════════════════════════════════
`novels/output/difu_segmented/_quality.json` 59 条记录里 `verdict` 全是
PASS、`would_keep_reading` 全是 True（取值种类 = 1，零方差）。原因在
graph.py:56-64：抽样章走 READBACK → FIX 循环，`_should_readback` 在
verdict == "NEEDS_FIX" 且未超 _MAX_FIX_ATTEMPTS 时路由回 fix_node，
**一路修到 PASS 为止**。比 verdict 一致率必然得到 100% 的空数字。

所以判据换成有方差的字段（实测 _quality.json 取值种类）：
  comprehension_issues  6 种  ✅
  engagement_gaps       4 种  ✅（0:21 / 1:22 / 2:15 / 3:1）
  impression 文本      59 种  ✅（全不同）
且**不走 graph、不调 translate_chapter**，直接调 readback_node —— FIX 循环
在结构上不可能触发，看到的是 READBACK 的原始判定。

════════════════════════════════════════════════════════════════════
安全：默认零成本、零写库
════════════════════════════════════════════════════════════════════
· **默认 dry-run**：一次 API 都不调，只试算金额。
· 真跑需同时给 `--execute --allow-expensive`（A 组是高价档 max）。
· **写路径强制隔离到临时目录**（在任何 `import src` 之前设好，因为
  config.DATA_DIR 等常量在 import 时求值）。readback_node 会
  ① token_ledger.record 追加账本行、② verdict == NEEDS_FIX 时
  error_tracker.record_event 写 translation_events.db。两者都落在隔离目录，
  **生产 data/ 零污染**——账本刚归零（round12），不能跑一次就污染回去。
  生产备忘/术语是**只读复制**进隔离目录的，读到的内容与生产一致。
· 绝不 `git checkout` 还原任何东西。

════════════════════════════════════════════════════════════════════
⚠️ 与生产的已知偏差（如实记录，别当"完全等价"）
════════════════════════════════════════════════════════════════════
cold_read_context 三段里，**"PREVIOUSLY 近章摘要"无法从交付产物重建**：
生产它来自 FIX/READ 产出的 chapter_summary（graph.py:_update_context 累积
最近 5 章），而交付产物只留了 `_checkpoint.json.previous_summary`（**最后
一章**那一条），59 个抽样章各自的近章摘要没有落盘。
→ 两组用**同一个**降级值，所以档位仍是唯一变量、组间差异仍可归因；
  但绝对判定值不可外推到生产。用 `--prev-context` 选降级方式，
  脚本会在输出里打印实际用了哪一种。
角色名册与术语段是**真实重建**的（复用生产 _build_cold_read_context）。

条款10 提醒：READBACK 是严格盲读。生产 graph.py:181 的角色名册已改为
`f"  - **{en}**"`（只渲染英文译名、不印中文源名）。本脚本**复用**该函数、
不自己拼，所以不会把中文名倒退回去。但 `_build_cold_read_context` 第 3 段
"ESTABLISHED TERMINOLOGY" 透传 style_memo.read_all() 原文，实测其中
7/8 条含中文源名（如 `阴兵过境 → Underworld soldiers passing through`）——
这是生产路径上的残留泄漏，不是本脚本引入的。脚本会用 --report-leak 检出并
如实报告，但**不擅自修改生产函数**（超出本轮授权范围）。

用法：
    # 零成本试算（默认，不调 API）
    python3.11 scripts/ab_readback_tier.py --dry-run

    # 真跑（要钱，需两个开关）
    python3.11 scripts/ab_readback_tier.py --execute --allow-expensive

    # 只跑前 3 章的小样（省钱验通路）
    python3.11 scripts/ab_readback_tier.py --execute --allow-expensive --limit 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 书目常量（与 scripts/run_novel.py 的 NOVELS["difu"] 对齐）──
BOOK_ID = "difu_xiao_xiansheng"
SOURCE_TXT = "tests/fixtures/《地府叫我小先生》 作者：界玉.txt"
DELIVERED_MD = "novels/output/difu_segmented/difu_en.md"
OUT_DIR = "novels/output/difu_segmented"
GENRE = "folk_religion"          # personas.GENRE_PROFILES 认的 key
COST_GENRE = "difu"              # estimate_offline 标定点的 book key（不同套！）
TARGET_LANG = "en-US"

TIER_A = "qwen3.8-max"           # 现网默认档
TIER_B = "qwen3.8-flash"         # 降档目标


# ══════════════════════════════════════════════════════════════════
# 写路径隔离：必须在 import src 之前执行
# ══════════════════════════════════════════════════════════════════
def isolate_write_paths(*, copy_memo: bool = True) -> Path:
    """把所有写路径指向一次性临时目录，并把生产备忘**只读复制**进去。

    照 tests/conftest.py:33-53 的做法：config.DATA_DIR / OUTPUT_DIR 与派生的
    DB_PATH 常量在 import 时求值，所以这些环境变量必须早于任何 `import src`。

    复制备忘是为了"读到生产内容、写到临时目录"两全：不复制的话
    StyleMemoStore 会在空的临时目录里找不到抽屉，cold_read_context 的术语段
    就退化成空，试算与真跑都会失真。
    """
    tmp = Path(tempfile.mkdtemp(prefix="xidu-ab-readback-"))
    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    (tmp / "output").mkdir(parents=True, exist_ok=True)

    os.environ["XIDU_DATA_DIR"] = str(data)
    os.environ["XIDU_OUTPUT_DIR"] = str(tmp / "output")
    os.environ["XIDU_TOKEN_LEDGER"] = str(data / "token_ledger.jsonl")
    os.environ["CHECKPOINT_DB_PATH"] = str(data / "checkpoints.db")
    os.environ["CHROMA_PERSIST_PATH"] = str(data / "chroma")

    if copy_memo:
        src_memo = ROOT / "data" / "translation_memory" / BOOK_ID
        if src_memo.is_dir():
            dst = data / "translation_memory" / BOOK_ID
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_memo, dst)
    return tmp


# ══════════════════════════════════════════════════════════════════
# 从交付产物提取"同一份译文"（两组共用，方案1 可归因的关键）
# ══════════════════════════════════════════════════════════════════
_CHAPTER_RE = re.compile(r"^## Chapter (\d+): (.*)$", re.M)


def extract_delivered_chapters(md_path: Path) -> dict[int, dict]:
    """按 EN 章号切出已交付译文正文。

    ⚠️ 定位口径（实测标定，别改）：**EN 章号 == split_chapters 的 index**。
    标题里的中文号与 index 的偏移**不固定**（实测 index − 标题号 有 1/2/3/4
    四种，另有 302/362/502 的异常），所以既不能假设固定偏移，也不能拿标题里
    的中文号去索引源文件（那样 B 档重译会取到错的章）。
    """
    text = md_path.read_text(encoding="utf-8", errors="replace")
    marks = [(int(m.group(1)), m.group(2).strip(), m.start())
             for m in _CHAPTER_RE.finditer(text)]
    out: dict[int, dict] = {}
    for i, (num, title, pos) in enumerate(marks):
        end = marks[i + 1][2] if i + 1 < len(marks) else len(text)
        block = text[pos:end]
        body = block.split("\n", 1)[1] if "\n" in block else ""
        # 去掉章尾分隔线与空行，只留正文（与 scan_deliverable 的口径一致）
        body = re.sub(r"\n-{3,}\s*$", "", body.strip()).strip()
        cn = re.search(r"第(\d+)章", title)
        out[num] = {
            "title": title,
            "cn_num": int(cn.group(1)) if cn else None,
            "body": body,
            "chars": len(body),
        }
    return out


def sampled_chapters(quality_path: Path) -> list[int]:
    """抽样章清单：取 _quality.json 里的**唯一**章号，保证与历史冷读抽检同批。

    ⚠️ 实测 _quality.json 有 59 条记录但只有 58 个唯一章号：Ch260 出现两次
    （2026-07-29T01:26:54Z 与 03:20:49Z）。这个重复不是数据缺陷，而是宝贵的
    **组内方差样本**——见 within_tier_variance()。
    """
    recs = json.loads(quality_path.read_text(encoding="utf-8"))
    return sorted({int(r["ch_number"]) for r in recs})


def within_tier_variance(quality_path: Path) -> dict:
    """从历史重复记录里提取**同档位组内方差**，作为 A/B 判据的噪音基线。

    为什么必须有这个基线（实测发现，交接未记）：
    `_quality.json` 的 Ch260 有两条记录，同一档位、同一章、相隔约 2 小时跑的：
        output_len 5626 / 5943（差 317 字符）
        comprehension_issues 2 / 2（相同）
        engagement_gaps 0 / 2（**差 2**）
    READBACK 的 temperature 是 0.1（readback.py:53）且全仓零 seed，所以
    **同档位重跑本身就会产生 ±2 量级的 engagement_gaps 抖动**。

    → 若 A/B 两组的 delta_sum 落在这个量级内，就**不能**判定为档位差异，
      那是噪音。没有这个基线，计数差判据会把随机性读成结论——正是本项目
      反复在防的假信号。
    """
    recs = json.loads(quality_path.read_text(encoding="utf-8"))
    by_ch: dict[int, list[dict]] = {}
    for r in recs:
        by_ch.setdefault(int(r["ch_number"]), []).append(r)

    repeats = {c: rs for c, rs in by_ch.items() if len(rs) > 1}
    if not repeats:
        return {"available": False,
                "note": "_quality.json 无重复章号，取不到组内方差样本"}

    out: dict = {"available": True, "n_repeat_chapters": len(repeats),
                 "chapters": []}
    eg_gaps: list[int] = []
    ci_gaps: list[int] = []
    len_gaps: list[int] = []
    for ch, rs in sorted(repeats.items()):
        egs = [r.get("engagement_gaps") for r in rs]
        cis = [r.get("comprehension_issues") for r in rs]
        lens = [r.get("output_len") for r in rs]
        eg_gap = (max(x for x in egs if x is not None)
                  - min(x for x in egs if x is not None)) if any(
                      x is not None for x in egs) else None
        ci_gap = (max(x for x in cis if x is not None)
                  - min(x for x in cis if x is not None)) if any(
                      x is not None for x in cis) else None
        len_gap = (max(x for x in lens if x is not None)
                   - min(x for x in lens if x is not None)) if any(
                       x is not None for x in lens) else None
        if eg_gap is not None:
            eg_gaps.append(eg_gap)
        if ci_gap is not None:
            ci_gaps.append(ci_gap)
        if len_gap is not None:
            len_gaps.append(len_gap)
        out["chapters"].append({
            "ch": ch, "n_runs": len(rs),
            "engagement_gaps": egs, "comprehension_issues": cis,
            "output_len": lens,
            "eg_gap": eg_gap, "ci_gap": ci_gap, "len_gap": len_gap,
            "timestamps": [r.get("timestamp") for r in rs],
        })

    out["noise_floor"] = {
        "engagement_gaps_max_gap": max(eg_gaps) if eg_gaps else None,
        "comprehension_issues_max_gap": max(ci_gaps) if ci_gaps else None,
        "output_len_max_gap": max(len_gaps) if len_gaps else None,
        "basis": "同一档位、同一章重跑的最大差值。A/B 的 delta 不超过此值时"
                 "不可判定为档位差异。",
    }
    return out


def historical_verdicts(quality_path: Path) -> dict[int, dict]:
    recs = json.loads(quality_path.read_text(encoding="utf-8"))
    return {int(r["ch_number"]): r for r in recs}


# ══════════════════════════════════════════════════════════════════
# cold_read_context 重建（复用生产函数，不自己拼）
# ══════════════════════════════════════════════════════════════════
def build_agent(glossary_json: Path):
    """构造一个只用于重建 cold_read_context 的 agent（不跑管线）。

    术语灌进内存 store 而不是走 SQLite：交付的 _glossary.json 是权威快照，
    从库里读会读到别的东西（库路径已被隔离成空库）。
    """
    from src.agent.graph import TranslationAgent

    agent = TranslationAgent(book_id=BOOK_ID, target_lang=TARGET_LANG)
    snapshot = glossary_json.read_text(encoding="utf-8")
    agent.exact_store.restore_snapshot(snapshot, target_lang=TARGET_LANG)
    return agent


def make_prev_context(mode: str, chapter_num: int,
                      delivered: dict[int, dict]) -> str:
    """近章摘要的降级实现（生产值无法从交付产物重建，见模块 docstring）。

    两组必须拿到**同一个**值，否则档位不再是唯一变量。
    """
    if mode == "none":
        return ""
    if mode == "mechanical":
        # 机械截取前一章英文译文的开头。明确不是生产的 LLM 摘要，只是给冷读者
        # 一点连续性感；标注来源，避免被误当成生产等价物。
        prev = delivered.get(chapter_num - 1)
        if not prev or not prev["body"]:
            return ""
        snippet = " ".join(prev["body"].split())[:400]
        return (f"[mechanical excerpt from the preceding chapter, not a "
                f"production summary] {snippet}")
    raise ValueError(f"unknown --prev-context mode {mode!r}")


def rebuild_cold_read_context(agent, chapter_num: int, delivered: dict[int, dict],
                              prev_mode: str) -> str:
    """复用生产 graph._build_cold_read_context，只替换无法重建的近章摘要段。"""
    agent._chapter_context.clear()
    prev = make_prev_context(prev_mode, chapter_num, delivered)
    if prev:
        agent._chapter_context.append(prev)
    return agent._build_cold_read_context(
        chapter_number=chapter_num, target_lang=TARGET_LANG)


def check_source_leak(context: str) -> list[str]:
    """检出 cold_read_context 里的中文源名（条款10：READBACK 是严格盲读）。"""
    cjk = re.compile(r"[\u4e00-\u9fff]")
    return [ln.strip()[:110] for ln in context.split("\n") if cjk.search(ln)]


# ══════════════════════════════════════════════════════════════════
# 零成本试算（复用项目已标定的成本模型，不自建第二套）
# ══════════════════════════════════════════════════════════════════
def cny_from_tokens(tokens_in: float, tokens_out: float,
                    price_in: float, price_out: float) -> float:
    """元 = (in tokens ÷ 1e6 × 输入单价) + (out tokens ÷ 1e6 × 输出单价)。

    单价是**元 / 百万 token**，所以分母恒为 1e6。抽成模块级函数有两个理由：
    ① 这条公式原先在本文件出现 **3 次**（A/B 组、FIX 每章、B 档逐章）。任何
      一处改了分母或漏了一项，另两处不会跟着变——正是条款6 修成本闸门时针对
      的同一类缺陷（同一判据存在多份副本，各改各的）。
    ② 作为模块级纯函数才能被**精确**断言。金额是要报给用户批钱的数字，用
      approx 默认容差测等于放过"分母被改成 1000001"这种错——偏差只有百万分
      之一、看着无害，但它证明这条公式根本没被钉住。
    """
    return tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out


def crosscheck_basis() -> dict:
    """从 scripts/cost/measurements/ 的标定点派生交叉校验基准。

    为什么要派生而不是写死：本脚本的口径一（拟合）已经读标定点，若口径二
    （交叉校验）用另一组写死的数字，标定文件更新后两口径会各走各的——正是
    config.py:155-158 记录过的"配置声明与真实行为各走各的"那类缺陷。

    返回：
      in_over_en_median  READBACK 的 in tokens / 英文字符数 的中位比值
      out_min / out_max  READBACK 的 out tokens 实测区间
      fix_in / fix_out   FIX 的 in/out tokens 中位数（B 档重译算 FIX 成本用）
      n_points / dropped 参与统计的点数与被剔除的点数
    """
    sys.path.insert(0, str(ROOT / "scripts" / "cost"))
    from estimate_offline import load_points

    pts = load_points()
    in_ratios, rb_outs, fix_ins, fix_outs = [], [], [], []
    dropped = 0
    for p in pts:
        n = p["n"]
        rb = n.get("readback")
        if rb:
            en = p.get("en") or 0
            if en > 0 and rb[0] > 0:
                in_ratios.append(rb[0] / en)
            else:
                # 标定点里有 en=0 的退化点（difu-max）。留着会把比值推到无穷，
                # 剔掉并计数，让"剔了几个"显式可见而不是静默发生。
                dropped += 1
            if rb[1] > 0:
                rb_outs.append(rb[1])
        fx = n.get("fix")
        if fx:
            if fx[0] > 0:
                fix_ins.append(fx[0])
            if fx[1] > 0:
                fix_outs.append(fx[1])

    def median(vals):
        s = sorted(vals)
        return s[len(s) // 2] if s else 0.0

    def mean(vals):
        return (sum(vals) / len(vals)) if vals else 0.0

    if not in_ratios or not rb_outs:
        raise RuntimeError(
            "标定点不足以派生交叉校验基准（scripts/cost/measurements/ 缺 readback "
            "数据）；宁可硬失败，也不回退到写死的数字。")

    return {
        # 比值取中位数：单个退化点（en 极小的 difu-min = 1.28）会把均值推到
        # 无意义的量级，中位数抗离群。这是本脚本自建的口径，estimate_offline
        # 里没有对应物。
        "in_over_en_median": median(in_ratios),
        # out 区间用实测 min/max，给口径一的拟合值一个可对照的上下界。
        "out_min": min(rb_outs),
        "out_max": max(rb_outs),
        # ⚠️ FIX 用**均值**，不是中位数：这是为了与 estimate_offline.py:150 的
        # 口径一致（那里写死的 4992/7016 实测就是标定点的 mean：4992.4/7016.5，
        # 不是 median 4900/7509.2）。B 档重译的金额要能和交接 §二-2a 的 ¥3.72
        # 对账，就必须同口径；换成中位数会让每章 FIX 成本从 ¥0.2364 漂到
        # ¥0.2495，6 章 ×50% 差 ¥0.039 —— 数字不大，但那是**无缘无故对不上账**，
        # 会让人怀疑整份试算。派生自标定文件后，estimate_offline 的写死值若与
        # 标定漂移，这里会跟着标定走（正确方向），并在输出里显式打印实际取值。
        "fix_in": mean(fix_ins),
        "fix_out": mean(fix_outs),
        "fix_statistic": "mean",
        "n_points": len(in_ratios),
        "n_fix_points": len(fix_ins),
        "dropped": dropped,
    }


def estimate_costs(chapters: list[dict], b_tier_src: list[tuple[int, int]]):
    """返回 (A/B 组金额明细, B 档重译金额明细)。

    口径一：**拟合模型** —— 复用 scripts/cost/estimate_offline.py 的
    calibrate/predict_chapter（7 个实测标定点的最小二乘）。交接 §二-2 的
    ¥7.15 / ¥3.72 就是这个口径，便于对账。
    口径二：**实测比值** —— READBACK 的 in tokens 与英文字符数的标定比值，
    以及 out tokens 的标定区间。用来给口径一做交叉校验（拟合只有 7 个点，
    外推到 59 章可能偏）。
    """
    sys.path.insert(0, str(ROOT / "scripts" / "cost"))
    from estimate_offline import calibrate, load_points, predict_chapter, PRICE

    cal = calibrate(load_points())

    # ── A/B 组：只跑 READBACK，输入是已交付英文译文 ──
    a_in = a_out = b_in = b_out = 0.0
    for ch in chapters:
        pred = predict_chapter(cal, COST_GENRE, ch["src_chars"], ch["en_chars"])
        ri, ro = pred["readback"]
        a_in += ri
        a_out += ro
        b_in += ri
        b_out += ro

    price_a = PRICE["readback"]                    # (12.0, 36.0) qwen3.8-max
    from src.config import MODEL_PRICES_CNY_PER_M
    price_b = MODEL_PRICES_CNY_PER_M[TIER_B]       # (0.8, 2.7) qwen3.8-flash

    def money(tokens_in, tokens_out, price):
        # Thin adapter only — the arithmetic lives in cny_from_tokens so there
        # is exactly one copy of the "tokens ÷ 1e6 × unit price" formula.
        return cny_from_tokens(tokens_in, tokens_out, price[0], price[1])

    ab = {
        "n_chapters": len(chapters),
        "A": {"model": TIER_A, "price": price_a, "in": a_in, "out": a_out,
              "cny": money(a_in, a_out, price_a)},
        "B": {"model": TIER_B, "price": price_b, "in": b_in, "out": b_out,
              "cny": money(b_in, b_out, price_b)},
    }
    ab["total_cny"] = ab["A"]["cny"] + ab["B"]["cny"]
    ab["saving_ratio"] = (ab["A"]["cny"] / ab["B"]["cny"]) if ab["B"]["cny"] else 0.0

    # 口径二交叉校验：从标定点**派生** in/en_chars 比值与 out 区间。
    # ⚠️ 这里一度是硬编码的字面量（2473.5/7350、1286.0、2881.0 等）——那就是
    # 项目反复在防的"第二套会漂移的成本模型"：scripts/cost/measurements/ 更新后，
    # 拟合口径（口径一）会跟着变，而硬编码的交叉校验还停在旧数，两者对不上时
    # 无人知道哪个对。现在两口径同源，标定变了两个一起变。
    cb = crosscheck_basis()
    en_total = sum(c["en_chars"] for c in chapters)
    ab["crosscheck"] = {
        "in_over_en_median": round(cb["in_over_en_median"], 3),
        "in_est": en_total * cb["in_over_en_median"],
        "out_est_low": cb["out_min"] * len(chapters),
        "out_est_high": cb["out_max"] * len(chapters),
        "en_chars_total": en_total,
        "n_points": cb["n_points"],
        "dropped_points": cb["dropped"],
    }
    cc = ab["crosscheck"]
    cc["A_cny_range"] = (money(cc["in_est"], cc["out_est_low"], price_a),
                         money(cc["in_est"], cc["out_est_high"], price_a))
    cc["B_cny_range"] = (money(cc["in_est"], cc["out_est_low"], price_b),
                         money(cc["in_est"], cc["out_est_high"], price_b))

    # ── B 档 6 章重译：完整管线（read/write/readback/fix），从中文原文跑 ──
    # FIX 的 token 数同样从标定点派生（此前写死 4992/7016，抄自
    # estimate_offline.py:150 的内联常量 —— 那也是一处会漂移的副本）。
    fix_cost_each = cny_from_tokens(cb["fix_in"], cb["fix_out"],
                                    PRICE["fix"][0], PRICE["fix"][1])
    bt = {"chapters": [], "src_chars_total": 0}
    tot_no_fix = 0.0
    for en_num, cn_num in b_tier_src:
        pred = predict_chapter(cal, COST_GENRE,
                               chapters_by_en(en_num)["src_chars"],
                               chapters_by_en(en_num)["en_est"])
        c = sum(cny_from_tokens(i, o, PRICE[n][0], PRICE[n][1])
                for n, (i, o) in pred.items())
        tot_no_fix += c
        bt["chapters"].append({"en": en_num, "cn": cn_num,
                               "src_chars": chapters_by_en(en_num)["src_chars"],
                               "cny_no_fix": round(c, 4)})
        bt["src_chars_total"] += chapters_by_en(en_num)["src_chars"]
    bt["cny_no_fix"] = tot_no_fix
    bt["fix_rate"] = 0.5                            # 交接标定值（实测）
    bt["cny_with_fix"] = tot_no_fix + fix_cost_each * bt["fix_rate"] * len(b_tier_src)
    # 把 FIX 成本的派生依据一起记下来：报出去的金额要能追溯到标定点，
    # 否则下一位无从判断 ¥3.72 是怎么来的、标定更新后该不该变。
    bt["fix_statistic"] = cb["fix_statistic"]
    bt["fix_in"] = cb["fix_in"]
    bt["fix_out"] = cb["fix_out"]
    bt["n_fix_points"] = cb["n_fix_points"]
    bt["fix_cny_each"] = fix_cost_each
    return ab, bt


_SRC_CACHE: dict[int, dict] = {}


def chapters_by_en(en_num: int) -> dict:
    """B 档重译用的源长度 + 完整英文长度估计。

    截断章不能用截断后的英文长度预测成本（会低估）。按交接 §二-2a 的做法，
    用 2290 个未截断章标定的"英文字符/源汉字"中位数 **3.33** 重算。
    """
    if en_num not in _SRC_CACHE:
        raise KeyError(f"chapter {en_num} not loaded; call load_sources() first")
    return _SRC_CACHE[en_num]


EN_PER_CJK_MEDIAN = 3.33      # 交接 §二-2a：2290 个未截断章标定的中位数


def load_sources(b_tier_en: list[int]) -> dict[int, int]:
    """从中文源文件按 index(==EN章号) 取 B 档各章源长度，填 _SRC_CACHE。"""
    from src.chapter_splitter import ParagraphTag, split_chapters
    from src.text_io import detect_and_read

    text, _ = detect_and_read(str(ROOT / SOURCE_TXT))
    chs = [c for c in split_chapters(text) if c.action != ParagraphTag.SKIP]
    by_index = {c.index: c for c in chs}
    out = {}
    for en in b_tier_en:
        c = by_index.get(en)
        if c is None:
            raise KeyError(f"源文件里没有 index={en} 的章（EN Ch{en}）")
        src_chars = len(c.content)
        _SRC_CACHE[en] = {"src_chars": src_chars,
                          "en_est": src_chars * EN_PER_CJK_MEDIAN,
                          "title": c.title}
        out[en] = src_chars
    return out


# ══════════════════════════════════════════════════════════════════
# 真跑：两组各调一次 readback_node
# ══════════════════════════════════════════════════════════════════
def run_group(tier: str, work: list[dict], tmp: Path,
              ledger_name: str = "token_ledger.jsonl") -> list[dict]:
    """在一组里逐章调 readback_node。tier 通过环境变量切换（生产同一个杠杆）。

    ``ledger_name`` 让每一遍写到**独立**的账本文件：基线那一遍（--repeat-
    baseline）若与 A/B 组共用一个账本，它的 token 会被算进"实际花费"，于是
    A/B 的金额对账失真。分开文件后各自计量。
    """
    os.environ["XIDU_NODE_MODEL_READBACK"] = tier

    # config.NODE_MODELS 在 import 时求值，换档必须让子进程重读 —— 用子进程
    # 跑，既避免"改了 env 但模块常量还是旧的"，也让账本/事件库天然落在隔离目录。
    script = tmp / f"_worker_{tier.replace('.', '_')}.py"
    script.write_text(_WORKER_SRC, encoding="utf-8")

    import subprocess
    tag = f"{tier.replace('.', '_')}_{Path(ledger_name).stem}"
    payload = tmp / f"_payload_{tag}.json"
    payload.write_text(json.dumps(work, ensure_ascii=False), encoding="utf-8")
    result_file = tmp / f"_result_{tag}.json"

    env = dict(os.environ)
    env["XIDU_AB_PAYLOAD"] = str(payload)
    env["XIDU_AB_RESULT"] = str(result_file)
    # 覆盖子进程的账本路径：基线遍写独立文件，不与 A/B 组混账
    env["XIDU_TOKEN_LEDGER"] = str(tmp / "data" / ledger_name)
    env["XIDU_NODE_MODEL_READBACK"] = tier

    proc = subprocess.run([sys.executable, str(script)], env=env,
                          cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"worker for {tier} failed (rc={proc.returncode}):\n"
            f"{proc.stderr[-3000:]}")
    if not result_file.exists():
        raise RuntimeError(f"worker for {tier} produced no result file")
    return json.loads(result_file.read_text(encoding="utf-8"))


_WORKER_SRC = '''"""A/B worker：在子进程里按指定档位跑 READBACK。

单独进程是为了让 config.NODE_MODELS 重新读环境变量（模块常量在 import 时
求值），同时把账本/事件库的写入天然关在隔离目录里。
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["XIDU_AB_ROOT"])
sys.path.insert(0, str(ROOT))

payload = json.loads(Path(os.environ["XIDU_AB_PAYLOAD"]).read_text(encoding="utf-8"))

from src.agent.nodes.readback import readback_node  # noqa: E402
from src.circuit_breaker import CircuitBreakerOpenError  # noqa: E402

rows = []
for item in payload:
    state = {
        "translated_text": item["body"],
        "cold_read_context": item["cold_context"],
        "chapter_number": item["ch"],
        "book_id": item["book_id"],
        "target_lang": item["target_lang"],
        "genre": item["genre"],
        "content_type": "novel",
        "api_key": "",
    }
    row = {"ch": item["ch"], "tier": item["tier"]}
    try:
        out = readback_node(state)
        fb = out.get("readback_feedback") or {}
        row.update({
            "ok": True,
            "verdict": fb.get("verdict"),
            "would_keep_reading": fb.get("would_keep_reading"),
            "comprehension_issues": len(fb.get("comprehension_issues") or []),
            "engagement_gaps": len(fb.get("engagement_gaps") or []),
            "impression": (fb.get("overall_impression") or ""),
            "quality_score": out.get("quality_score"),
            "quality_issues": out.get("quality_issues") or [],
        })
    except CircuitBreakerOpenError as e:
        row.update({"ok": False, "error": f"CircuitBreakerOpenError: {e}"})
    except Exception as e:
        row.update({"ok": False, "error": f"{type(e).__name__}: {e}"})
    rows.append(row)

Path(os.environ["XIDU_AB_RESULT"]).write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"worker done: {len(rows)} chapters @ {payload[0]['tier'] if payload else '?'}")
'''


def measure_within_tier_variance(a_rows: list[dict], a_rows2: list[dict]) -> dict:
    """实测的同档位组内方差（--repeat-baseline 时用）。

    与 within_tier_variance() 同形（返回 noise_floor 结构），让 compare() 能
    用同一套显著性判断。区别是这里的基线来自本脚本的**裸 READBACK**（无 FIX
    循环），口径与 A/B 组一致；而 _quality.json 里的历史基线是走完整管线的
    产物，口径不同、且只有 1 章样本。
    """
    first = {r["ch"]: r for r in a_rows if r.get("ok")}
    second = {r["ch"]: r for r in a_rows2 if r.get("ok")}
    common = sorted(set(first) & set(second))
    if not common:
        return {"available": False, "note": "基线两遍无共同成功章，取不到方差"}

    eg_gaps, ci_gaps = [], []
    chapters = []
    for c in common:
        eg = [first[c].get("engagement_gaps") or 0, second[c].get("engagement_gaps") or 0]
        ci = [first[c].get("comprehension_issues") or 0, second[c].get("comprehension_issues") or 0]
        eg_gaps.append(max(eg) - min(eg))
        ci_gaps.append(max(ci) - min(ci))
        chapters.append({"ch": c, "n_runs": 2, "engagement_gaps": eg,
                         "comprehension_issues": ci})
    return {
        "available": True,
        "source": "measured (--repeat-baseline)",
        "n_repeat_chapters": len(common),
        "chapters": chapters,
        "noise_floor": {
            "engagement_gaps_max_gap": max(eg_gaps),
            "comprehension_issues_max_gap": max(ci_gaps),
            "basis": f"{len(common)} 章同档位（{TIER_A}）裸 READBACK 跑两遍的"
                     f"最大差值，口径与 A/B 组一致。",
        },
    }


def ledger_cost(tmp: Path, name: str = "token_ledger.jsonl") -> dict:
    """从隔离账本读**真实** token 与金额（跑完后对账用）。

    ``name`` 指定账本文件：A/B 组用默认文件，基线遍用独立文件，分开计量。
    """
    p = tmp / "data" / name
    if not p.exists():
        return {"rows": 0, "cny": 0.0, "by_model": {}}
    from src.token_ledger import cost_cny
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    total = 0.0
    by_model: dict[str, dict] = {}
    for r in rows:
        m = r.get("model") or "?"
        c = cost_cny(m, r.get("input_tokens"), r.get("output_tokens")) or 0.0
        total += c
        slot = by_model.setdefault(m, {"rows": 0, "in": 0, "out": 0, "cny": 0.0})
        slot["rows"] += 1
        slot["in"] += r.get("input_tokens") or 0
        slot["out"] += r.get("output_tokens") or 0
        slot["cny"] += c
    return {"rows": len(rows), "cny": total, "by_model": by_model}


# ══════════════════════════════════════════════════════════════════
# 判据：组间差异（不是 verdict 一致率）
# ══════════════════════════════════════════════════════════════════
def compare(a_rows: list[dict], b_rows: list[dict],
            hist: dict[int, dict], noise: dict | None = None) -> dict:
    a = {r["ch"]: r for r in a_rows if r.get("ok")}
    b = {r["ch"]: r for r in b_rows if r.get("ok")}
    common = sorted(set(a) & set(b))

    def agg(rows, key):
        vals = [r.get(key) or 0 for r in rows]
        return {"sum": sum(vals), "mean": (sum(vals) / len(vals)) if vals else 0.0}

    ci_a = agg([a[c] for c in common], "comprehension_issues")
    ci_b = agg([b[c] for c in common], "comprehension_issues")
    eg_a = agg([a[c] for c in common], "engagement_gaps")
    eg_b = agg([b[c] for c in common], "engagement_gaps")

    # verdict 一致率照样算出来，但明确标注它无信息（预期恒 100%）
    same_verdict = sum(1 for c in common if a[c]["verdict"] == b[c]["verdict"])
    same_impression = sum(1 for c in common
                          if a[c]["impression"].strip() == b[c]["impression"].strip())

    per_chapter = []
    for c in common:
        per_chapter.append({
            "ch": c,
            "ci_A": a[c]["comprehension_issues"],
            "ci_B": b[c]["comprehension_issues"],
            "ci_delta": b[c]["comprehension_issues"] - a[c]["comprehension_issues"],
            "eg_A": a[c]["engagement_gaps"],
            "eg_B": b[c]["engagement_gaps"],
            "eg_delta": b[c]["engagement_gaps"] - a[c]["engagement_gaps"],
            "verdict_A": a[c]["verdict"],
            "verdict_B": b[c]["verdict"],
            "hist_ci": (hist.get(c) or {}).get("comprehension_issues"),
            "hist_eg": (hist.get(c) or {}).get("engagement_gaps"),
            "impression_A": a[c]["impression"][:300],
            "impression_B": b[c]["impression"][:300],
        })

    # 三类必须**互斥**，否则 worse + better + same 会超过共同章数。
    # ⚠️ 这里一度写成两个独立的列表推导（`ci_delta > 0 or eg_delta > 0` 与
    # `ci_delta < 0 or eg_delta < 0`），于是一个"ci 改善但 eg 恶化"的章会
    # **同时**落进 worse 和 better —— 两个计数都虚高，分区不成立。
    # 归类顺序是有意的：只要有一个判据恶化就算 worse（降档的质量风险优先
    # 呈现），两项都改善才算 better，其余是 same。
    worse, better = [], []
    for p in per_chapter:
        ci_d, eg_d = p["ci_delta"], p["eg_delta"]
        if ci_d > 0 or eg_d > 0:
            worse.append(p)
        elif ci_d < 0 or eg_d < 0:
            better.append(p)

    # ── 显著性：把 delta 与同档位组内方差比，避免把噪音读成档位信号 ──
    significance = {"available": False}
    if noise and noise.get("available"):
        floor = noise.get("noise_floor") or {}
        eg_floor = floor.get("engagement_gaps_max_gap")
        ci_floor = floor.get("comprehension_issues_max_gap")
        eg_delta = abs(eg_b["sum"] - eg_a["sum"])
        ci_delta = abs(ci_b["sum"] - ci_a["sum"])
        verdicts = {}
        if eg_floor is not None:
            verdicts["engagement_gaps"] = {
                "delta_sum_abs": eg_delta, "noise_floor": eg_floor,
                "exceeds_noise": eg_delta > eg_floor,
            }
        if ci_floor is not None:
            verdicts["comprehension_issues"] = {
                "delta_sum_abs": ci_delta, "noise_floor": ci_floor,
                "exceeds_noise": ci_delta > ci_floor,
            }
        any_exceeds = any(v["exceeds_noise"] for v in verdicts.values())
        significance = {
            "available": bool(verdicts),
            "per_metric": verdicts,
            "any_metric_exceeds_noise": any_exceeds,
            "interpretation": (
                "至少一个判据的组间差超过同档位组内方差 → 可能是真实的档位效应，"
                "但仍需看逐章分布是否集中。"
                if any_exceeds else
                "⚠️ 所有判据的组间差都在同档位组内方差范围内 → **不能判定降档降质，"
                "也不能判定降档安全**。这是无信息结果，不要据此下结论。"
            ),
            "note": "噪音基线来自 _quality.json 的 Ch260 重复记录（同档位重跑，"
                    "engagement_gaps 差 2）。样本仅 1 章，基线本身很粗——"
                    "要更可靠的基线需同档位多跑几章（额外花钱）。",
        }

    return {
        "n_common": len(common),
        "n_failed_A": len([r for r in a_rows if not r.get("ok")]),
        "n_failed_B": len([r for r in b_rows if not r.get("ok")]),
        "comprehension_issues": {"A": ci_a, "B": ci_b,
                                 "delta_sum": ci_b["sum"] - ci_a["sum"]},
        "engagement_gaps": {"A": eg_a, "B": eg_b,
                            "delta_sum": eg_b["sum"] - eg_a["sum"]},
        "verdict_agreement": {
            "rate": (same_verdict / len(common)) if common else 0.0,
            "note": "⚠️ 无信息判据：历史 59 条 verdict 全 PASS（零方差），"
                    "此值预期恒为 100%，不能用来判断降档是否降质。",
        },
        "impression_identical": same_impression,
        "chapters_worse_under_B": len(worse),
        "chapters_better_under_B": len(better),
        "chapters_same": len(common) - len(worse) - len(better),
        "significance": significance,
        "per_chapter": per_chapter,
    }


# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
# 报告渲染：纯函数（吃数据结构 → 吐字符串）
#
# 为什么抽出来：① 金额与判据的**渲染**本身必须可测——格式化写错等于让人批
# 错数字，而 main() 里内联的 print 无法在 CI 上测（交付物 difu_en.md 被
# .gitignore:18 排除，CI 上不存在）。② 内联 print 让变异算子在 295 行
# main() 里产生大量不可观测的变异点。抽成纯函数后，测试用 tmp_path 造的小
# 夹具就能覆盖（与 scan_deliverable._print_human 同一模式）。
# ══════════════════════════════════════════════════════════════════
_RULE = "═" * 68
_THIN_RULE = "─" * 68


def grand_total(ab_est: dict, bt_est: dict, baseline_extra: float = 0.0) -> float:
    """合计金额的**单一来源**。

    渲染报告与写 JSON 结果都必须调它。两处各写一遍加法早晚会漂移，而漂移的
    后果是"报给用户批钱的数字"与"结果文件里记的数字"不一致——那正是条款6
    修闸门时针对的同一类缺陷（同一判据存在多份副本，各改各的）。
    """
    return ab_est["total_cny"] + bt_est["cny_with_fix"] + baseline_extra


def render_noise_report(noise: dict) -> str:
    """组内方差（噪音基线）报告。无基线时返回空串。"""
    if not noise.get("available"):
        return ""
    nf = noise["noise_floor"]
    lines = ["", "⚠️ 组内方差（噪音基线，实测自历史重复记录）："]
    for c in noise["chapters"]:
        lines.append(f"   Ch{c['ch']} 同档位跑 {c['n_runs']} 次："
                     f"engagement_gaps {c['engagement_gaps']}、"
                     f"comprehension_issues {c['comprehension_issues']}、"
                     f"output_len {c['output_len']}")
    lines.append(f"   → engagement_gaps 组内最大差 "
                 f"{nf['engagement_gaps_max_gap']}、comprehension_issues "
                 f"{nf['comprehension_issues_max_gap']}")
    lines.append("   含义：A/B 的组间 delta 不超过这个量级时，**不能**判定为档位差异。")
    lines.append("   基线仅 1 章样本，很粗。若要可靠基线需同档位多跑几章"
                 "（额外花钱，见 --repeat-baseline）。")
    return "\n".join(lines)


def render_estimate_report(ab_est: dict, bt_est: dict,
                           baseline_extra: float = 0.0,
                           n_baseline: int = 0) -> str:
    """试算金额报告。**这是要报给用户批钱的数字**，渲染必须可测。"""
    A, B = ab_est["A"], ab_est["B"]
    cc = ab_est["crosscheck"]
    out = ["", _RULE, "试算金额（零 API 调用）", _RULE,
           "",
           f"【A/B 档位对比】{ab_est['n_chapters']} 章 × 2 组（只跑 READBACK）",
           f"  A 组 {A['model']:16} 单价 in {A['price'][0]}/out {A['price'][1]} 元每M",
           f"      tokens in {A['in']:>12,.0f}  out {A['out']:>10,.0f}"
           f"   → ¥{A['cny']:.2f}",
           f"  B 组 {B['model']:16} 单价 in {B['price'][0]}/out {B['price'][1]} 元每M",
           f"      tokens in {B['in']:>12,.0f}  out {B['out']:>10,.0f}"
           f"   → ¥{B['cny']:.2f}",
           f"  小计 ¥{ab_est['total_cny']:.2f}   （A/B 单价比 "
           f"{ab_est['saving_ratio']:.1f} 倍）",
           f"  交叉校验（从 scripts/cost/measurements/ 派生，非拟合、非写死）："
           f"英文共 {cc['en_chars_total']:,} 字符 × in/en 中位 "
           f"{cc['in_over_en_median']} → in ≈ {cc['in_est']:,.0f}",
           f"      标定 {cc['n_points']} 点（剔除 en=0 的退化点 "
           f"{cc['dropped_points']} 个）；out 区间取实测 min/max",
           f"      A 组区间 ¥{cc['A_cny_range'][0]:.2f}–{cc['A_cny_range'][1]:.2f}"
           f" | B 组区间 ¥{cc['B_cny_range'][0]:.2f}–{cc['B_cny_range'][1]:.2f}",
           "",
           "【B 档 6 章重译】完整管线（从中文原文重跑，与 A/B 独立）"]
    for c in bt_est["chapters"]:
        out.append(f"  EN Ch{c['en']:<5} 中文第{c['cn']}章  源 {c['src_chars']:>5} 字"
                   f"   ¥{c['cny_no_fix']:.3f}")
    out += [
        f"  合计源汉字 {bt_est['src_chars_total']}（交接 §二-2a 记 9683）",
        f"  无 FIX ¥{bt_est['cny_no_fix']:.2f} | FIX 触发率 "
        f"{bt_est['fix_rate']:.0%} → ¥{bt_est['cny_with_fix']:.2f}",
        f"  FIX token 取自标定 {bt_est['fix_statistic']}（in "
        f"{bt_est['fix_in']:,.0f} / out {bt_est['fix_out']:,.0f}，"
        f"{bt_est['n_fix_points']} 个标定点）→ 每章 ¥{bt_est['fix_cny_each']:.4f}",
        f"  英文长度按 {EN_PER_CJK_MEDIAN} 字符/源汉字（截断章不能用截断后的"
        f"英文长度，会低估）",
    ]
    if baseline_extra:
        out += ["",
                f"【可选】--repeat-baseline={n_baseline} 章：A 档再跑一遍做实测噪音"
                f"基线 → 额外 ¥{baseline_extra:.2f}",
                "   为什么值得考虑：历史基线只有 Ch260 一章样本，且它是走完整"
                "管线（含 FIX）的产物，与本脚本的裸 READBACK 口径不同。"
                "实测基线才能判断 A/B 的 delta 是不是噪音。"]
    grand = grand_total(ab_est, bt_est, baseline_extra)
    out += ["", _THIN_RULE,
            f"合计 ≈ ¥{grand:.2f}    （交接估算 ¥10.87 = 7.15 + 3.72）",
            _THIN_RULE]
    return "\n".join(out)


def render_verdict_report(verdict: dict, cost_a: dict, cost_b: dict,
                          actual: dict, ab_est: dict, bt_est: dict,
                          baseline_cost: dict, noise_source: str,
                          prod_rows: int) -> str:
    """真跑后的结果报告：判据 + 显著性 + 实际花费对账。"""
    ci = verdict["comprehension_issues"]
    eg = verdict["engagement_gaps"]
    va = verdict["verdict_agreement"]
    out = ["", _RULE, "结果", _RULE,
           f"共同可比章数 {verdict['n_common']} | A 组失败 "
           f"{verdict['n_failed_A']} | B 组失败 {verdict['n_failed_B']}",
           "",
           f"【主判据】comprehension_issues  A 合计 {ci['A']['sum']}"
           f"（均 {ci['A']['mean']:.2f}） | B 合计 {ci['B']['sum']}"
           f"（均 {ci['B']['mean']:.2f}） | 差 {ci['delta_sum']:+d}",
           f"【主判据】engagement_gaps       A 合计 {eg['A']['sum']}"
           f"（均 {eg['A']['mean']:.2f}） | B 合计 {eg['B']['sum']}"
           f"（均 {eg['B']['mean']:.2f}） | 差 {eg['delta_sum']:+d}",
           "",
           f"impression 文本完全相同的章数：{verdict['impression_identical']}"
           f"/{verdict['n_common']}",
           f"verdict 一致率 {va['rate']:.1%} —— {va['note']}",
           "",
           f"降档后判定变差的章 {verdict['chapters_worse_under_B']} | "
           f"变好 {verdict['chapters_better_under_B']} | "
           f"持平 {verdict['chapters_same']}"]

    sig = verdict.get("significance") or {}
    if sig.get("available"):
        out += ["", "【显著性】组间差 vs 同档位组内方差："]
        for metric, v in (sig.get("per_metric") or {}).items():
            flag = "超过噪音" if v["exceeds_noise"] else "在噪音内"
            out.append(f"   {metric}: |delta|={v['delta_sum_abs']} vs 噪音基线 "
                       f"{v['noise_floor']} → {flag}")
        out += [f"   {sig['interpretation']}",
                f"   基线来源：{noise_source}",
                f"   {sig['note']}"]
    else:
        out += ["", "【显著性】⚠️ 取不到噪音基线，无法判断组间差是信号还是噪音。"]

    out += ["",
            f"【实际花费】A 组 ¥{cost_a['cny']:.2f}（{cost_a['rows']} 行） | "
            f"B 组 ¥{cost_b['cny']:.2f}（{cost_b['rows']} 行） | "
            f"合计 ¥{actual['cny']:.2f}"]
    for m, s in (actual.get("by_model") or {}).items():
        out.append(f"   {m:18} {s['rows']:>3} 行  in {s['in']:>10,}  "
                   f"out {s['out']:>9,}  ¥{s['cny']:.2f}")
    if baseline_cost.get("rows"):
        out.append(f"   （另有基线遍 ¥{baseline_cost['cny']:.2f}，独立计量）")
    out += [f"   试算 ¥{ab_est['total_cny']:.2f} vs 实际 "
            f"¥{actual['cny']:.2f} → 偏差 "
            f"{(actual['cny'] - ab_est['total_cny']):+.2f}",
            "",
            f"生产账本核验：{prod_rows} 行（本次运行前应为 0；非 0 说明隔离失效）",
            "",
            f"⚠️ 本脚本的 --execute **只跑 A/B 档位对比**"
            f"（≈¥{ab_est['total_cny']:.2f}），**不含** B 档 6 章重译"
            f"（≈¥{bt_est['cny_with_fix']:.2f}）。",
            "   B 档重译是从中文原文跑完整管线产出新译文，属另一件事"
            "（走 run_novel.py / translate_and_verify.py 那类入口），"
            "本脚本只试算它的金额、不执行。"]
    return "\n".join(out)


def render_leak_report(leaks: dict, n_samples: int) -> str:
    """条款10 泄漏检出报告。"""
    out = ["", f"条款10 泄漏检出：{len(leaks)}/{n_samples} 章的 "
               f"cold_read_context 含中文源名"]
    for n, hit in list(leaks.items())[:3]:
        out.append(f"   Ch{n}: {len(hit)} 行，例 {hit[0][:80]!r}")
    if leaks:
        out.append("   ⚠️ 来源是 _build_cold_read_context 第3段透传 "
                   "style_memo.read_all() 原文（生产路径同样如此），"
                   "非本脚本引入。两组共用同一 context，不影响归因。")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="READBACK 档位 A/B（方案1：复用已交付译文的受控对比）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="只试算金额，不调 API（默认）")
    mode.add_argument("--execute", action="store_true",
                      help="真跑（要钱）。必须同时给 --allow-expensive")
    ap.add_argument("--allow-expensive", action="store_true",
                    help="确认允许高价档（A 组 qwen3.8-max）。真跑必需")
    ap.add_argument("--limit", type=int, default=0,
                    help="只跑前 N 章（省钱验通路）；0 = 全部 59 章")
    ap.add_argument("--prev-context", choices=["none", "mechanical"],
                    default="mechanical",
                    help="近章摘要的降级方式（生产值无法从交付产物重建）")
    ap.add_argument("--repeat-baseline", type=int, default=0, metavar="N",
                    help="真跑时额外把 A 档在同一批 N 章上再跑一遍，用实测的"
                         "同档位方差当噪音基线（默认 0 = 只用 _quality.json 里"
                         "那 1 章历史样本）。会额外花钱，金额见试算输出的"
                         " baseline_extra_cny")
    ap.add_argument("--report-leak", action="store_true",
                    help="检出 cold_read_context 里的中文源名泄漏（条款10）")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = ap.parse_args(argv)

    if args.execute and not args.allow_expensive:
        print("拒绝：--execute 必须同时给 --allow-expensive。\n"
              "A 组是 qwen3.8-max（高价档），这是成本闸门的要求，不是形式。",
              file=sys.stderr)
        return 2

    # copy_memo 只在真跑时需要：cold_read_context 由 build_agent →
    # StyleMemoStore 构建，而 dry-run 只做试算、根本不碰备忘。少复制一份
    # 5.8 MB 的抽屉（实测 difu_xiao_xiansheng 五抽屉 54,006 行）。
    tmp = isolate_write_paths(copy_memo=args.execute)
    if not args.execute:
        # dry-run 的临时目录没有排查价值（没调 API、没有账本行），退出时清掉，
        # 免得反复试算攒出一堆空目录。真跑则**保留**，好让人核对账本与 worker 输出。
        import atexit
        atexit.register(shutil.rmtree, tmp, True)
    os.environ["XIDU_AB_ROOT"] = str(ROOT)
    print(f"写路径已隔离到临时目录：{tmp}")
    print(f"（生产 data/ 不会被写入；账本当前应为 0 行）\n")

    from src.config import MODEL_PRICES_CNY_PER_M, NODE_MODELS

    delivered = extract_delivered_chapters(ROOT / DELIVERED_MD)
    quality_path = ROOT / OUT_DIR / "_quality.json"
    samples = sampled_chapters(quality_path)
    hist = historical_verdicts(quality_path)
    noise = within_tier_variance(quality_path)
    n_records = len(hist) and sum(
        1 for _ in json.loads(quality_path.read_text(encoding="utf-8")))
    print(f"交付物章节数 {len(delivered)} | 抽样章 {len(samples)} 个唯一章号"
          f"（_quality.json 共 {n_records} 条记录，Ch260 重复 2 次）")

    noise_report = render_noise_report(noise)
    if noise_report:
        print(noise_report)

    b_tier_en = [269, 430, 548, 811, 820, 1472]
    src_chars = load_sources(b_tier_en)
    print(f"B 档 6 章源汉字实测：{src_chars} 合计 {sum(src_chars.values())}")

    # ── 试算（零成本，两种口径）──
    est_chapters = []
    for n in samples:
        ch = delivered.get(n)
        if not ch:
            continue
        est_chapters.append({"en_num": n, "en_chars": ch["chars"],
                             "src_chars": src_chars.get(n, 0)})
    # A/B 只跑 READBACK，需要 src_chars 供拟合模型用；抽样章的源长度按需取
    if any(c["src_chars"] == 0 for c in est_chapters):
        need = [c["en_num"] for c in est_chapters if c["src_chars"] == 0]
        extra = load_sources(need)
        for c in est_chapters:
            if c["src_chars"] == 0:
                c["src_chars"] = extra[c["en_num"]]
                _SRC_CACHE[c["en_num"]]["en_est"] = c["en_chars"]

    b_tier_pairs = [(en, delivered[en]["cn_num"]) for en in b_tier_en]
    ab_est, bt_est = estimate_costs(est_chapters, b_tier_pairs)

    # A 组单价后面算基线追加费用时还要用，故单独取出来。
    A = ab_est["A"]

    # 基线追加费用先算出来，再交给渲染函数：合计金额只有一个来源
    # （grand_total），报告与结果 JSON 不会各算一遍而漂移。
    baseline_extra = 0.0
    n_bl = 0
    if args.repeat_baseline:
        # A 档在 N 章上多跑一遍 = 额外一次 A 组 READBACK（只算 A，不算 B：
        # 基线要测的是"同档位重跑的抖动"，用高价档做基线才对得上 A 组的量级）
        n_bl = min(args.repeat_baseline, len(est_chapters))
        per_ch = A["cny"] / max(len(est_chapters), 1)
        baseline_extra = per_ch * n_bl

    print(render_estimate_report(ab_est, bt_est, baseline_extra, n_bl))
    grand = grand_total(ab_est, bt_est, baseline_extra)
    print(f"当前 READBACK 路由：{NODE_MODELS['readback']}"
          f"  单价 {MODEL_PRICES_CNY_PER_M.get(NODE_MODELS['readback'])}")

    if not args.execute:
        print("\n✅ dry-run 结束，未调用任何 API、未产生费用。")
        print("🛑 铁律6：以上是试算金额。要真跑请把最终金额报给用户、拿到"
              "「跑」字之后再加 --execute --allow-expensive。")
        _write_out(args.out, {"mode": "dry-run", "ab_estimate": ab_est,
                              "b_tier_estimate": bt_est,
                              "baseline_extra_cny": baseline_extra,
                              "noise_baseline": noise,
                              "grand_total_cny": grand})
        return 0

    # ── 真跑 ──
    from src import cost_gate
    expensive = cost_gate.expensive_nodes()
    if expensive:
        print("\n成本闸门：当前路由含高价档节点")
        for node, model, price in expensive:
            print(f"   {node}: {model}  out ¥{price}/M")
    print(cost_gate.describe_tiers())

    run_samples = samples[:args.limit] if args.limit else samples
    print(f"\n真跑 {len(run_samples)} 章 × 2 组 = {len(run_samples) * 2} 次 "
          f"READBACK 调用…")

    agent = build_agent(ROOT / OUT_DIR / "_glossary.json")
    work = []
    leaks: dict[int, list[str]] = {}
    for n in run_samples:
        ch = delivered.get(n)
        if not ch:
            print(f"  ⚠️ EN Ch{n} 在交付物里找不到，跳过")
            continue
        ctx = rebuild_cold_read_context(agent, n, delivered, args.prev_context)
        if args.report_leak:
            hit = check_source_leak(ctx)
            if hit:
                leaks[n] = hit
        for tier in (TIER_A, TIER_B):
            work.append({"ch": n, "tier": tier, "body": ch["body"],
                         "cold_context": ctx, "book_id": BOOK_ID,
                         "target_lang": TARGET_LANG, "genre": GENRE})

    if args.report_leak:
        print(render_leak_report(leaks, len(run_samples)))

    print(f"\n近章摘要降级方式：--prev-context={args.prev_context}")
    a_rows = run_group(TIER_A, [w for w in work if w["tier"] == TIER_A], tmp,
                       ledger_name="ledger_A.jsonl")
    b_rows = run_group(TIER_B, [w for w in work if w["tier"] == TIER_B], tmp,
                       ledger_name="ledger_B.jsonl")

    # ── 可选：A 档再跑一遍，用实测方差当噪音基线（独立账本，不混入 A/B 对账）──
    measured_noise = None
    baseline_cost = {"rows": 0, "cny": 0.0, "by_model": {}}
    if args.repeat_baseline:
        a_work = [w for w in work if w["tier"] == TIER_A]
        n_bl = min(args.repeat_baseline, len(a_work))
        print(f"\n实测噪音基线：A 档（{TIER_A}）在同 {n_bl} 章上再跑一遍…")
        a_rows2 = run_group(TIER_A, a_work[:n_bl], tmp,
                            ledger_name="ledger_baseline.jsonl")
        bl_chs = {r["ch"] for r in a_rows2}
        measured_noise = measure_within_tier_variance(
            [r for r in a_rows if r["ch"] in bl_chs], a_rows2)
        baseline_cost = ledger_cost(tmp, "ledger_baseline.jsonl")
        nf = (measured_noise.get("noise_floor") or {})
        print(f"   实测噪音基线（{measured_noise.get('n_repeat_chapters', 0)} 章）："
              f"engagement_gaps 最大差 "
              f"{nf.get('engagement_gaps_max_gap')}、comprehension_issues "
              f"{nf.get('comprehension_issues_max_gap')}")
        print(f"   基线花费 ¥{baseline_cost['cny']:.2f}（独立计量，不计入 A/B 对账）")

    verdict = compare(a_rows, b_rows, hist, measured_noise or noise)
    cost_a = ledger_cost(tmp, "ledger_A.jsonl")
    cost_b = ledger_cost(tmp, "ledger_B.jsonl")
    actual = {"rows": cost_a["rows"] + cost_b["rows"],
              "cny": cost_a["cny"] + cost_b["cny"],
              "by_model": {**cost_a["by_model"], **cost_b["by_model"]}}

    used_noise = measured_noise or noise
    noise_source = used_noise.get("source") or "history (_quality.json Ch260)"
    prod_ledger = ROOT / "data" / "token_ledger.jsonl"
    prod_rows = (len([l for l in prod_ledger.read_text(encoding="utf-8").splitlines()
                      if l.strip()]) if prod_ledger.exists() else 0)

    print(render_verdict_report(verdict, cost_a, cost_b, actual, ab_est, bt_est,
                                baseline_cost, noise_source, prod_rows))

    _write_out(args.out, {"mode": "execute", "ab_estimate": ab_est,
                          "b_tier_estimate": bt_est, "verdict": verdict,
                          "actual_cost": actual, "cost_A": cost_a,
                          "cost_B": cost_b, "baseline_cost": baseline_cost,
                          "noise_baseline": used_noise,
                          "noise_source": noise_source,
                          "prev_context": args.prev_context,
                          "b_tier_executed": False,
                          "leaks": {str(k): v for k, v in leaks.items()}})
    return 0


def _write_out(path: str, data: dict) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {p}")


if __name__ == "__main__":
    raise SystemExit(main())
