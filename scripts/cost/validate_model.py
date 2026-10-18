"""Leave-one-out validation of the per-chapter cost model.

【中文·这个脚本干什么】
用"留一法"验证成本模型的预测准不准。所谓留一法就是：
每次从实测数据里拿出一个点当"考试题"，用剩下的点拟合模型，
看模型对这个被拿出来的点预测得准不准。轮流拿一遍，
就能知道模型整体靠不靠谱。

【中文·怎么运行】
在项目根目录执行：
```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
python3.11 scripts/cost/validate_model.py
```

【中文·运行前要准备什么】
需要先跑过 probe_nodes.py 和 probe_nodes_wide.py，
生成 scripts/cost/measurements/cost_probe.json 和
cost_probe_wide.json 两个实测数据文件。没有它们就没有数据可验证。

【中文·⚠️ 会不会花真钱】
**不会**。本脚本纯离线计算，不调用任何大模型 API，
只读取已有的实测 JSON 文件做数学运算。

【中文·会产生/修改什么文件】
不产生也不修改任何文件。只在终端打印验证结果。
"""
import json, itertools
from pathlib import Path

# 【中文】实测数据文件的存放目录。
DATA = Path(__file__).resolve().parent.parent.parent / "scripts" / "cost" / "measurements"
DATA.mkdir(parents=True, exist_ok=True)

# 【中文】两本已交付书的基本信息，用于"整本书一级稳健性"检验。
# n = 总章数，mean = 平均每章汉字数。
B = {"difu": {"n": 2301, "mean": 1200}, "wuxian": {"n": 775, "mean": 2986}}


def load():
    """加载所有实测数据点，归一化为每节点单次调用的 token 数。

    【中文】从 cost_probe.json 和 cost_probe_wide.json 读取实测记录，
    过滤掉缺少 read/write 节点的无效记录，把每个节点的 token 总量
    除以调用次数 n，折成"单次调用"的标准格式。
    返回: [(标签, 书名, CJK字符数, {节点名: (输入token, 输出token)})]
    """
    pts = []
    for f in ("cost_probe.json", "cost_probe_wide.json"):
        p = DATA / f
        if p.exists(): pts += json.loads(p.read_text())
    out = []
    for r in pts:
        per = r.get("per_node") or {}
        # 【中文】read 和 write 是核心节点，缺了就没法算成本，跳过。
        if "read" not in per or "write" not in per: continue
        out.append((r["label"], r.get("book", "difu"), r["cjk"],
                    {n: (a["in"]/a["n"], a["out"]/a["n"]) for n, a in per.items()}))
    return out


def slope(pairs):
    """最小二乘法求斜率（线性回归的斜率部分）。

    【中文】给定一组 (x, y) 数据点，用最小二乘法拟合 y = a + b*x
    中的斜率 b。公式是标准的：b = (nΣxy - ΣxΣy) / (nΣx² - (Σx)²)。
    分母为零时返回 0（数据点 x 值完全相同，无法拟合斜率）。
    """
    m = len(pairs); sx = sum(x for x,_ in pairs); sy = sum(y for _,y in pairs)
    sxx = sum(x*x for x,_ in pairs); sxy = sum(x*y for x,y in pairs)
    d = m*sxx - sx*sx
    return (m*sxy - sx*sy)/d if d else 0.0


def predict(train, book, cjk):
    """用训练集拟合模型，预测指定书的每章总 token 数。

    【中文】对 read/write/readback 三个节点分别做线性回归：
    token = 截距 + 斜率 × CJK字符数。截距用同书类型的数据点锚定
    （如果没有同类型的就用全部数据），斜率用全部训练数据。
    最后把三个节点的预测值加起来，得到每章总 token。

    参数:
      train: 训练数据列表（已排除当前测试点）
      book: 书名 key（"difu" 或 "wuxian"）
      cjk: 该书平均每章汉字数
    返回: 预测的每章总 token 数
    """
    tot = 0.0
    for node in ("read", "write", "readback"):
        rows = [r for r in train if node in r[3]]
        if not rows: continue
        # 【中文】斜率用全部训练数据拟合。
        b_i = slope([(r[2], r[3][node][0]) for r in rows])
        b_o = slope([(r[2], r[3][node][1]) for r in rows])
        # 【中文】截距优先用同书类型的数据锚定，减少跨类型偏差。
        own = [r for r in rows if r[1] == book] or rows
        a_i = sum(r[3][node][0] - b_i*r[2] for r in own)/len(own)
        a_o = sum(r[3][node][1] - b_o*r[2] for r in own)/len(own)
        tot += (a_i + b_i*cjk) + (a_o + b_o*cjk)
    return tot


# ═══════════════════════════════════════════════════════════
# 【中文】主流程：先做逐点留一法验证，再做整本书稳健性检验。
# ═══════════════════════════════════════════════════════════
pts = load()
print("逐点：模型预测 vs 实测（留一法，用其余全部点拟合）")
print(f"{'label':<12}{'book':<8}{'CJK':>6}{'实测':>9}{'预测':>9}{'误差':>9}")
errs = []
for i, r in enumerate(pts):
    # 【中文】留一法：把第 i 个点拿出来当测试集，其余当训练集。
    train = pts[:i] + pts[i+1:]
    pred = predict(train, r[1], r[2])
    actual = sum(v[0]+v[1] for v in r[3].values())
    e = (pred-actual)/actual*100
    errs.append(e)
    print(f"{r[0]:<12}{r[1]:<8}{r[2]:>6}{actual:>9,.0f}{pred:>9,.0f}{e:>8.0f}%")

# 【中文】汇总留一法的误差分布。中位数比平均数更能抵抗极端值。
print(f"\n留一法误差: 中位 {sorted(errs)[len(errs)//2]:+.0f}%"
      f"  范围 {min(errs):+.0f}% ~ {max(errs):+.0f}%")
print(f"平均绝对误差 {sum(abs(e) for e in errs)/len(errs):.0f}%\n")

# 【中文】整本书稳健性检验：用不同的子集组合拟合，
# 看预测结果是否稳定。如果换个训练子集预测就大幅摆动，
# 说明模型不够稳健、过度依赖某几个数据点。
print("整本书一级的稳健性：每本书用 3 组不同子集拟合，"
      "看每章 token 预测的摆动")
for book, meta in B.items():
    rows = [r for r in pts if r[1] == book]
    # 【中文】取最多 6 种组合（combinations 可能很多，限制上限）。
    combos = list(itertools.combinations(rows, max(2, len(rows)-1)))[:6]
    vals = []
    for c in combos:
        vals.append(predict(c, book, meta["mean"]) / 1000)
    print(f"  {book}: 每章预测 {min(vals):.1f}k ~ {max(vals):.1f}k tokens"
          f"  摆动 {(max(vals)-min(vals))/ (sum(vals)/len(vals)) *100:.0f}%")
