"""西渡 pipeline cost, priced at the vendors' current published rates.

Prices read off the vendors' own pages (2026-09-22):
  qwen3.8-max    https://help.aliyun.com/zh/model-studio/model-pricing
                 中国内地/全球, 0<Token<=1M:  in 12 元/M  out 36 元/M
                 上下文缓存命中按标准输入单价 10% 计费 -> 1.2 元/M
  qwen3.8-flash  same page: in 0.8 元/M  out 2.7 元/M  (cache hit 0.08)
  glm-5.3        https://docs.bigmodel.cn/cn/guide/start/pricing  in 8  out 28 (cache hit 2)
  glm-5.3-flash  same page: in 0.8  out 2.8 (cache hit 0.23)
  Batch API: 输入+输出均 5 折; 与缓存折扣不能同时生效.

Token counts below are REAL API measurements per node (scripts/cost/measurements/cost_probe*.json).

【中文·这个脚本干什么】
用实测 token 数据 + 厂商官方单价，算出"翻一章/翻一本书要花多少钱"。
它还会做线性回归拟合、对比不同降本方案的效果、和人译成本做参照。

【中文·怎么运行】
在项目根目录执行：
```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
python3.11 scripts/cost/price_pipeline.py
```

【中文·运行前要准备什么】
scripts/cost/measurements/ 下要有 cost_probe.json 和/或
cost_probe_wide.json（先跑 probe_nodes*.py 生成）。

【中文·⚠️ 会不会花真钱】
**不会**。本脚本纯离线计算，不调用任何大模型 API。
它只读取已有的实测 JSON 文件做数学运算和费用换算。

【中文·会产生/修改什么文件】
· 产出: scripts/cost/measurements/price_final.json（详细结果）
· ⚠️ 不修改任何源码文件或生产数据
"""
import json
from pathlib import Path

# 【中文】实测数据文件的存放目录。
DATA = Path(__file__).resolve().parent.parent.parent / "scripts" / "cost" / "measurements"
DATA.mkdir(parents=True, exist_ok=True)

# 【中文】各模型的单价表（元/百万 token）。
# 来源：config.py MODEL_PRICES_CNY_PER_M，抓取于 2026-09-22。
# cache_in = 上下文缓存命中的输入单价（比标准输入价便宜很多）。
MODELS = {
    "qwen3.8-max":    {"in": 12.0, "out": 36.0, "cache_in": 1.2},
    "qwen3.8-flash":  {"in": 0.8,  "out": 2.7,  "cache_in": 0.08},
    "glm-5.3":        {"in": 8.0,  "out": 28.0, "cache_in": 2.0},
    "glm-5.3-flash":  {"in": 0.8,  "out": 2.8,  "cache_in": 0.23},
}

# 【中文】默认配置下各节点使用的模型。
# 与 config.py DEFAULT_NODE_MODELS 一致。
NODE_MODEL = {"read": "qwen3.8-max", "write": "glm-5.3-flash",
              "readback": "qwen3.8-max", "review_terms": "qwen3.8-max",
              "fix": "glm-5.3"}

# Cacheable INPUT tokens per node, as ABSOLUTE counts measured at the smallest probe
# chapter (76 汉字, difu-min), where everything that is not the chapter itself is the
# reusable prefix (system prompt + cultural-rule pack + fidelity rules + glossary).
#   read      in 5315 total - ~80 source tokens        -> 5235 reusable
#   write     in 5280 total - ~80 source - 4376 read    ->  824 reusable
#   readback  in 1005 total - ~200 translated-text tok  ->  805 reusable
# Cached hits are billed at 10% (Qwen) / 25% (GLM) of the standard input rate.
#
# 【中文】每个节点的可缓存输入 token 数（绝对值）。
# 在最短章节（76 汉字）的实测中，系统提示词 + 文化规则包 + 术语表等
# 是每章都重复发送的前缀，可以被上下文缓存命中从而大幅降低输入费用。
# 这些数字来自 cost_probe.json 中最短章节的实测数据。
CACHEABLE_TOKENS = {"read": 5235, "write": 824, "readback": 805, "fix": 800}

# 【中文】两本已交付书的基本统计信息。
# n = 总章数, mean_cjk = 平均每章汉字数, total_cjk = 全书汉字总数,
# sample_rate = 实际冷读覆盖率（出货配置下只有部分章节做冷读）,
# genre = 类型标签（用于匹配实测数据）。
BOOKS = {
    "地府叫我小先生": {"n": 2301, "mean_cjk": 1200, "total_cjk": 2763108,
                  "sample_rate": 59 / 2301, "genre": "difu"},
    "无限恐怖":       {"n": 775,  "mean_cjk": 2986, "total_cjk": 2314250,
                  "sample_rate": 26 / 775,  "genre": "wuxian"},
}

# 【中文】Qwen → GLM 的等价模型映射，用于"如果换成 GLM 会怎样"的对比。
GLM_MAP = {"qwen3.8-max": "glm-5.3", "qwen3.8-flash": "glm-5.3-flash",
           "glm-5.3": "glm-5.3", "glm-5.3-flash": "glm-5.3-flash"}


def load():
    """加载实测数据并归一化为每节点单次调用的 token 数。

    【中文】从 cost_probe.json 和 cost_probe_wide.json 读取，
    过滤无效记录，把多调用节点的 token 总量除以调用次数 n。
    返回: [{label, book, cjk, verdict, nodes: {节点: {in, out, n}}}]
    """
    pts = []
    for f in ("cost_probe.json", "cost_probe_wide.json"):
        p = DATA / f
        if p.exists():
            pts += json.loads(p.read_text())
    clean = []
    for r in pts:
        per = r.get("per_node") or {}
        if "read" not in per or "write" not in per:
            continue
        row = {"label": r["label"], "book": r.get("book", "difu"), "cjk": r["cjk"],
               "verdict": r["verdict"], "nodes": {}}
        for n, a in per.items():                       # normalise multi-call nodes to one call
            row["nodes"][n] = {"in": a["in"] / a["n"], "out": a["out"] / a["n"], "n": a["n"]}
        clean.append(row)
    return clean


def slope(pairs):
    """最小二乘法求斜率。

    【中文】标准公式：b = (nΣxy - ΣxΣy) / (nΣx² - (Σx)²)。
    分母为零返回 0。
    """
    m = len(pairs)
    if m < 2:
        return 0.0
    sx = sum(x for x, _ in pairs); sy = sum(y for _, y in pairs)
    sxx = sum(x * x for x, _ in pairs); sxy = sum(x * y for x, y in pairs)
    den = m * sxx - sx * sx
    return (m * sxy - sx * sy) / den if den else 0.0


def main():
    """主函数：加载数据 → 拟合 → 逐书估算 → 降本杠杆对比。"""
    pts = load()
    print("实测点（每节点已折成单次调用）")
    print(f"{'label':<12}{'book':<8}{'CJK':>6}{'read':>8}{'write':>8}"
          f"{'readback':>10}{'fix':>8}  verdict")
    for r in sorted(pts, key=lambda r: r["cjk"]):
        n = r["nodes"]
        g = lambda k: f"{(n[k]['in']+n[k]['out']):.0f}" if k in n else "-"
        print(f"{r['label']:<12}{r['book']:<8}{r['cjk']:>6}"
              f"{g('read'):>8}{g('write'):>8}"
              f"{g('readback'):>10}{g('fix'):>8}  {r['verdict']}")

    out = {}
    for book, b in BOOKS.items():
        gk = b["genre"]
        # 【中文】优先用同类型的实测点拟合；没有就用全部数据。
        rows = [r for r in pts if r["book"] == gk] or pts
        print(f"\n{'='*78}\n{book} — {b['n']} 章, "
              f"均值 {b['mean_cjk']} 汉字/章, "
              f"共 {b['total_cjk']:,} 汉字")
        print(f"用于拟合的实测点: {[r['label'] for r in rows]}")
        per_ch = {}
        for node in ("read", "write", "readback"):
            # 【中文】对每个节点分别做线性回归：token = 截距 + 斜率 × CJK。
            pin = slope([(r["cjk"], r["nodes"][node]["in"])
                         for r in pts if node in r["nodes"]])
            pout = slope([(r["cjk"], r["nodes"][node]["out"])
                          for r in pts if node in r["nodes"]])
            # intercept anchored on this book's own genre points
            # 【中文】截距用同书类型的数据锚定，减少跨类型偏差。
            ins = [r["nodes"][node]["in"] - pin * r["cjk"]
                   for r in rows if node in r["nodes"]]
            outs = [r["nodes"][node]["out"] - pout * r["cjk"]
                    for r in rows if node in r["nodes"]]
            if not ins:
                continue
            ai, ao = sum(ins) / len(ins), sum(outs) / len(outs)
            per_ch[node] = {"in": ai + pin * b["mean_cjk"],
                            "out": ao + pout * b["mean_cjk"]}
        out[book] = {"per_chapter": per_ch, "detail": {}}
        print(f"\n  每章 token（拟合）: " + "  ".join(
            f"{k} {v['in']:.0f}+{v['out']:.0f}={v['in']+v['out']:.0f}"
            for k, v in per_ch.items()))
        total_tok = sum(v['in']+v['out'] for v in per_ch.values())
        print(f"  每章合计 {total_tok:,.0f} tokens"
              f"  | 每千字 "
              f"{total_tok/(b['mean_cjk']/1000)/1000:.2f}k tokens")

        def price(chapters, cold, fix_rate, model_map, cache):
            """计算全书成本。

            【中文】参数说明：
            · chapters: 总章数
            · cold: 冷读比例（1.0=全量冷读，0.026=出货配置的 2.6%）
            · fix_rate: FIX 触发率（0.013=记录值 1.3%，0.5=实测 50%）
            · model_map: 节点→模型的映射字典
            · cache: 是否开启上下文缓存
            返回: (全书总费用, token汇总, 各节点明细)
            """
            total = 0.0; tok = {"in": 0.0, "out": 0.0}
            det = {}
            for node, v in per_ch.items():
                # 【中文】只有 readback 受冷读比例影响（其他节点每章都跑）。
                scale = cold if node == "readback" else 1.0
                ti, to = v["in"] * scale, v["out"] * scale
                m = MODELS[model_map[node]]
                pi, po = m["in"], m["out"]
                if cache:
                    # 【中文】缓存命中部分按 cache_in 单价计费，
                    # 未命中部分按标准输入价。最多缓存 CACHEABLE_TOKENS，
                    # 且不超过输入的 90%（留 10% 给章节正文）。
                    cached = min(CACHEABLE_TOKENS.get(node, 0.0), 0.9 * ti)
                    c_in = (cached / 1e6) * m["cache_in"] + ((ti - cached) / 1e6) * pi
                    c = c_in + (to / 1e6) * po
                else:
                    c = (ti / 1e6) * pi + (to / 1e6) * po
                det[node] = (ti, to, c)
                tok["in"] += ti; tok["out"] += to; total += c
            if fix_rate > 0:
                fix = fix_pt
                m = MODELS[model_map["fix"]]
                c = (fix["in"] / 1e6) * m["in"] + (fix["out"] / 1e6) * m["out"]
                total += c * fix_rate
                det["fix(×%.1f%%)" % (fix_rate * 100)] = (
                    fix["in"], fix["out"], c * fix_rate)
            return total * chapters, tok, det

        # FIX per-event size, measured
        # 【中文】FIX 单次 token 消耗均值，从实测数据中提取。
        fix_rows = [r for r in pts if "fix" in r["nodes"]]
        fix_pt = {"in": sum(r["nodes"]["fix"]["in"] for r in fix_rows) / len(fix_rows),
                  "out": sum(r["nodes"]["fix"]["out"] for r in fix_rows) / len(fix_rows)}
        print(f"  FIX 单次实测: {fix_pt['in']:.0f} in + {fix_pt['out']:.0f} out"
              f"（{len(fix_rows)} 次实测，其中地府中位章一轮 FIX 就要 "
              f"{fix_pt['in']+fix_pt['out']:,.0f} tokens）")

        # 【中文】三种配置方案的成本对比：
        # A = 全量冷读 + 低 FIX 率（记录值 1.3%）
        # B = 全量冷读 + 高 FIX 率（实测 50%）
        # C = 出货配置（仅部分章节冷读）+ 高 FIX 率
        for cfg, cold, fixr in (
            ("A 全量冷读 + FIX(记录值1.3%)", 1.0, 0.013),
            ("B 全量冷读 + FIX(实测50%)",   1.0, 0.50),
            (f"C 出货配置({b['sample_rate']*100:.1f}%冷读) + FIX(50%)",
             b["sample_rate"], 0.50),
        ):
            cost, tok, det = price(b["n"], cold, fixr, NODE_MODEL, cache=False)
            out[book]["detail"][cfg] = {
                "cost": cost,
                "tokens": (tok["in"] + tok["out"]) * b["n"]}
            print(f"\n  -- {cfg}")
            for node, (ti, to, c) in det.items():
                print(f"     {node:<16}{ti*b['n']:>12,.0f} in"
                      f"{to*b['n']:>12,.0f} out{c*b['n']:>10.2f} 元"
                      f"   ({c*b['n']/cost*100:>4.1f}%)")
            total_tokens = (tok['in']+tok['out'])*b['n']
            print(f"     {'全书合计':<16}{tok['in']*b['n']:>12,.0f} in"
                  f"{tok['out']*b['n']:>12,.0f} out{cost:>10.2f} 元"
                  f"   ({total_tokens/1e6:.2f}M tokens)")
            print(f"     每章 {cost/b['n']:.4f} 元 | "
                  f"每千字 {cost/(b['total_cjk']/1000):.4f} 元")

    # levers, per book, on the full-cold-read config with no FIX
    # 【中文】降本杠杆对比：以全量冷读无 FIX 为基准，
    # 展示各种降本手段能省多少钱。
    for book, b in BOOKS.items():
        base, _, _ = price(b["n"], 1.0, 0.0, NODE_MODEL, cache=False)
        print(f"\n{'='*78}\n降本/替代杠杆 — {book}（全量冷读，无 FIX）\n{'='*78}")
        print(f"  基准  READ=qwen3.8-max(12/36)  "
              f"WRITE=glm-5.3-flash(0.8/2.8)      {base:>9.2f} 元   100%")
        c2, _, _ = price(b["n"], 1.0, 0.0, NODE_MODEL, cache=True)
        print(f"  开上下文缓存(READ 命中 1.2 元/M, 10%)         "
              f"            {c2:>9.2f} 元   {c2/base*100:.0f}%")
        mm = dict(NODE_MODEL)
        mm["read"] = "qwen3.8-flash"; mm["readback"] = "qwen3.8-flash"
        c3, _, _ = price(b["n"], 1.0, 0.0, mm, cache=False)
        print(f"  READ+READBACK 换 qwen3.8-flash(0.8/2.7)       "
              f"            {c3:>9.2f} 元   {c3/base*100:.0f}%")
        mm2 = dict(NODE_MODEL)
        mm2["read"] = "glm-5.3"; mm2["readback"] = "glm-5.3"
        c4, _, _ = price(b["n"], 1.0, 0.0, mm2, cache=False)
        print(f"  READ+READBACK 换 glm-5.3(8/28) ← 百炼欠费期唯一可行 "
              f"       {c4:>9.2f} 元   {c4/base*100:.0f}%")
        print(f"  Batch API 全 5 折(与缓存互斥)                "
              f"            {base*0.5:>9.2f} 元   50%")
        low = b['total_cjk']/1000*100
        high = b['total_cjk']/1000*300
        ratio = b['total_cjk']/1000*100/base
        print(f"  人译参考 100-300 元/千字 -> 本书记 "
              f"{low:,.0f}-{high:,.0f} 元"
              f"  (机器成本是其 1/{ratio:,.0f})")
    DATA / "price_final.json".write_text(json.dumps(out, ensure_ascii=False, indent=2))


main()
