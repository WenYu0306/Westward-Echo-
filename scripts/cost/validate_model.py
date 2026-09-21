"""Leave-one-out validation of the per-chapter cost model."""
import json, itertools
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent.parent / "scripts" / "cost" / "measurements"
DATA.mkdir(parents=True, exist_ok=True)
B = {"difu": {"n": 2301, "mean": 1200}, "wuxian": {"n": 775, "mean": 2986}}
def load():
    pts = []
    for f in ("cost_probe.json", "cost_probe_wide.json"):
        p = DATA / f
        if p.exists(): pts += json.loads(p.read_text())
    out = []
    for r in pts:
        per = r.get("per_node") or {}
        if "read" not in per or "write" not in per: continue
        out.append((r["label"], r.get("book", "difu"), r["cjk"],
                    {n: (a["in"]/a["n"], a["out"]/a["n"]) for n, a in per.items()}))
    return out
def slope(pairs):
    m = len(pairs); sx = sum(x for x,_ in pairs); sy = sum(y for _,y in pairs)
    sxx = sum(x*x for x,_ in pairs); sxy = sum(x*y for x,y in pairs)
    d = m*sxx - sx*sx
    return (m*sxy - sx*sy)/d if d else 0.0
def predict(train, book, cjk):
    tot = 0.0
    for node in ("read", "write", "readback"):
        rows = [r for r in train if node in r[3]]
        if not rows: continue
        b_i = slope([(r[2], r[3][node][0]) for r in rows])
        b_o = slope([(r[2], r[3][node][1]) for r in rows])
        own = [r for r in rows if r[1] == book] or rows
        a_i = sum(r[3][node][0] - b_i*r[2] for r in own)/len(own)
        a_o = sum(r[3][node][1] - b_o*r[2] for r in own)/len(own)
        tot += (a_i + b_i*cjk) + (a_o + b_o*cjk)
    return tot

pts = load()
print("逐点：模型预测 vs 实测（留一法，用其余全部点拟合）")
print(f"{'label':<12}{'book':<8}{'CJK':>6}{'实测':>9}{'预测':>9}{'误差':>9}")
errs = []
for i, r in enumerate(pts):
    train = pts[:i] + pts[i+1:]
    pred = predict(train, r[1], r[2])
    actual = sum(v[0]+v[1] for v in r[3].values())
    e = (pred-actual)/actual*100
    errs.append(e)
    print(f"{r[0]:<12}{r[1]:<8}{r[2]:>6}{actual:>9,.0f}{pred:>9,.0f}{e:>8.0f}%")
print(f"\n留一法误差: 中位 {sorted(errs)[len(errs)//2]:+.0f}%  范围 {min(errs):+.0f}% ~ {max(errs):+.0f}%")
print(f"平均绝对误差 {sum(abs(e) for e in errs)/len(errs):.0f}%\n")
print("整本书一级的稳健性：每本书用 3 组不同子集拟合，看每章 token 预测的摆动")
for book, meta in B.items():
    rows = [r for r in pts if r[1] == book]
    combos = list(itertools.combinations(rows, max(2, len(rows)-1)))[:6]
    vals = []
    for c in combos:
        vals.append(predict(c, book, meta["mean"]) / 1000)
    print(f"  {book}: 每章预测 {min(vals):.1f}k ~ {max(vals):.1f}k tokens"
          f"  摆动 {(max(vals)-min(vals))/ (sum(vals)/len(vals)) *100:.0f}%")
