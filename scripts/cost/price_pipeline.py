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
"""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent.parent / "scripts" / "cost" / "measurements"
DATA.mkdir(parents=True, exist_ok=True)

MODELS = {
    "qwen3.8-max":    {"in": 12.0, "out": 36.0, "cache_in": 1.2},
    "qwen3.8-flash":  {"in": 0.8,  "out": 2.7,  "cache_in": 0.08},
    "glm-5.3":        {"in": 8.0,  "out": 28.0, "cache_in": 2.0},
    "glm-5.3-flash":  {"in": 0.8,  "out": 2.8,  "cache_in": 0.23},
}
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
CACHEABLE_TOKENS = {"read": 5235, "write": 824, "readback": 805, "fix": 800}
BOOKS = {
    "地府叫我小先生": {"n": 2301, "mean_cjk": 1200, "total_cjk": 2763108,
                  "sample_rate": 59 / 2301, "genre": "difu"},
    "无限恐怖":       {"n": 775,  "mean_cjk": 2986, "total_cjk": 2314250,
                  "sample_rate": 26 / 775,  "genre": "wuxian"},
}
GLM_MAP = {"qwen3.8-max": "glm-5.3", "qwen3.8-flash": "glm-5.3-flash",
           "glm-5.3": "glm-5.3", "glm-5.3-flash": "glm-5.3-flash"}

def load():
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
    m = len(pairs)
    if m < 2:
        return 0.0
    sx = sum(x for x, _ in pairs); sy = sum(y for _, y in pairs)
    sxx = sum(x * x for x, _ in pairs); sxy = sum(x * y for x, y in pairs)
    den = m * sxx - sx * sx
    return (m * sxy - sx * sy) / den if den else 0.0

def main():
    pts = load()
    print("实测点（每节点已折成单次调用）")
    print(f"{'label':<12}{'book':<8}{'CJK':>6}{'read':>8}{'write':>8}{'readback':>10}{'fix':>8}  verdict")
    for r in sorted(pts, key=lambda r: r["cjk"]):
        n = r["nodes"]
        g = lambda k: f"{(n[k]['in']+n[k]['out']):.0f}" if k in n else "-"
        print(f"{r['label']:<12}{r['book']:<8}{r['cjk']:>6}{g('read'):>8}{g('write'):>8}{g('readback'):>10}{g('fix'):>8}  {r['verdict']}")

    out = {}
    for book, b in BOOKS.items():
        gk = b["genre"]
        rows = [r for r in pts if r["book"] == gk] or pts
        print(f"\n{'='*78}\n{book} — {b['n']} 章, 均值 {b['mean_cjk']} 汉字/章, 共 {b['total_cjk']:,} 汉字")
        print(f"用于拟合的实测点: {[r['label'] for r in rows]}")
        per_ch = {}
        for node in ("read", "write", "readback"):
            pin = slope([(r["cjk"], r["nodes"][node]["in"]) for r in pts if node in r["nodes"]])
            pout = slope([(r["cjk"], r["nodes"][node]["out"]) for r in pts if node in r["nodes"]])
            # intercept anchored on this book's own genre points
            ins = [r["nodes"][node]["in"] - pin * r["cjk"] for r in rows if node in r["nodes"]]
            outs = [r["nodes"][node]["out"] - pout * r["cjk"] for r in rows if node in r["nodes"]]
            if not ins:
                continue
            ai, ao = sum(ins) / len(ins), sum(outs) / len(outs)
            per_ch[node] = {"in": ai + pin * b["mean_cjk"], "out": ao + pout * b["mean_cjk"]}
        out[book] = {"per_chapter": per_ch, "detail": {}}
        print(f"\n  每章 token（拟合）: " + "  ".join(
            f"{k} {v['in']:.0f}+{v['out']:.0f}={v['in']+v['out']:.0f}" for k, v in per_ch.items()))
        print(f"  每章合计 {sum(v['in']+v['out'] for v in per_ch.values()):,.0f} tokens"
              f"  | 每千字 {sum(v['in']+v['out'] for v in per_ch.values())/(b['mean_cjk']/1000)/1000:.2f}k tokens")

        def price(chapters, cold, fix_rate, model_map, cache):
            total = 0.0; tok = {"in": 0.0, "out": 0.0}
            det = {}
            for node, v in per_ch.items():
                scale = cold if node == "readback" else 1.0
                ti, to = v["in"] * scale, v["out"] * scale
                m = MODELS[model_map[node]]
                pi, po = m["in"], m["out"]
                if cache:
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
                total += c * fix_rate; det["fix(×%.1f%%)" % (fix_rate * 100)] = (fix["in"], fix["out"], c * fix_rate)
            return total * chapters, tok, det

        # FIX per-event size, measured
        fix_rows = [r for r in pts if "fix" in r["nodes"]]
        fix_pt = {"in": sum(r["nodes"]["fix"]["in"] for r in fix_rows) / len(fix_rows),
                  "out": sum(r["nodes"]["fix"]["out"] for r in fix_rows) / len(fix_rows)}
        print(f"  FIX 单次实测: {fix_pt['in']:.0f} in + {fix_pt['out']:.0f} out"
              f"（{len(fix_rows)} 次实测，其中地府中位章一轮 FIX 就要 {fix_pt['in']+fix_pt['out']:,.0f} tokens）")

        for cfg, cold, fixr in (
            ("A 全量冷读 + FIX(记录值1.3%)", 1.0, 0.013),
            ("B 全量冷读 + FIX(实测50%)",   1.0, 0.50),
            (f"C 出货配置({b['sample_rate']*100:.1f}%冷读) + FIX(50%)", b["sample_rate"], 0.50),
        ):
            cost, tok, det = price(b["n"], cold, fixr, NODE_MODEL, cache=False)
            out[book]["detail"][cfg] = {"cost": cost, "tokens": (tok["in"] + tok["out"]) * b["n"]}
            print(f"\n  -- {cfg}")
            for node, (ti, to, c) in det.items():
                print(f"     {node:<16}{ti*b['n']:>12,.0f} in{to*b['n']:>12,.0f} out{c*b['n']:>10.2f} 元"
                      f"   ({c*b['n']/cost*100:>4.1f}%)")
            print(f"     {'全书合计':<16}{tok['in']*b['n']:>12,.0f} in{tok['out']*b['n']:>12,.0f} out{cost:>10.2f} 元"
                  f"   ({(tok['in']+tok['out'])*b['n']/1e6:.2f}M tokens)")
            print(f"     每章 {cost/b['n']:.4f} 元 | 每千字 {cost/(b['total_cjk']/1000):.4f} 元")

    # levers, per book, on the full-cold-read config with no FIX
    for book, b in BOOKS.items():
        base, _, _ = price(b["n"], 1.0, 0.0, NODE_MODEL, cache=False)
        print(f"\n{'='*78}\n降本/替代杠杆 — {book}（全量冷读，无 FIX）\n{'='*78}")
        print(f"  基准  READ=qwen3.8-max(12/36)  WRITE=glm-5.3-flash(0.8/2.8)      {base:>9.2f} 元   100%")
        c2, _, _ = price(b["n"], 1.0, 0.0, NODE_MODEL, cache=True)
        print(f"  开上下文缓存(READ 命中 1.2 元/M, 10%)                     {c2:>9.2f} 元   {c2/base*100:.0f}%")
        mm = dict(NODE_MODEL); mm["read"] = "qwen3.8-flash"; mm["readback"] = "qwen3.8-flash"
        c3, _, _ = price(b["n"], 1.0, 0.0, mm, cache=False)
        print(f"  READ+READBACK 换 qwen3.8-flash(0.8/2.7)                   {c3:>9.2f} 元   {c3/base*100:.0f}%")
        mm2 = dict(NODE_MODEL); mm2["read"] = "glm-5.3"; mm2["readback"] = "glm-5.3"
        c4, _, _ = price(b["n"], 1.0, 0.0, mm2, cache=False)
        print(f"  READ+READBACK 换 glm-5.3(8/28) ← 百炼欠费期唯一可行       {c4:>9.2f} 元   {c4/base*100:.0f}%")
        print(f"  Batch API 全 5 折(与缓存互斥)                            {base*0.5:>9.2f} 元   50%")
        print(f"  人译参考 100-300 元/千字 -> 本书记 {b['total_cjk']/1000*100:,.0f}-{b['total_cjk']/1000*300:,.0f} 元"
              f"  (机器成本是其 1/{b['total_cjk']/1000*100/base:,.0f})")
    DATA / "price_final.json".write_text(json.dumps(out, ensure_ascii=False, indent=2))

main()
