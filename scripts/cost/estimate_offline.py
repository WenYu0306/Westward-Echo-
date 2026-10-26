"""零 API 成本估算：不调任何模型，直接算出一本书翻译要花多少钱。

原理：每节点的 token 数与"源文字符数 / 英文输出字符数"近似线性，
用 scripts/cost/measurements/ 里的实测点标定，再套到真实语料上。

用法：
    python3 scripts/cost/estimate_offline.py                # 估算已交付的两本
    python3 scripts/cost/estimate_offline.py --book 间客     # 估算一本新书（先跑一遍拿英文长度）

【中文·这个脚本干什么】
**纯离线**算出一本完整的书翻译下来要花多少钱。它不调用任何大模型 API，
只用之前 probe_nodes*.py 跑出来的实测数据做数学拟合，然后套到全书章节上。

【中文·⚠️ 会不会花真钱】
**⚠️ 本脚本不花钱，纯离线计算。**
这是它和 probe_nodes.py / probe_nodes_wide.py 的关键区别。
probe 脚本要真调 API 测 token，本脚本只读 probe 留下的 JSON 文件做算术。

【中文·怎么运行】
在项目根目录执行：
```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
python3.11 scripts/cost/estimate_offline.py              # 估算两本已交付的书
python3.11 scripts/cost/estimate_offline.py --book difu   # 只估地府
```

【中文·运行前要准备什么】
① scripts/cost/measurements/ 下要有 cost_probe*.json 实测数据
   （没有就先跑 probe_nodes.py 和 probe_nodes_wide.py）
② tests/fixtures/ 下要有原文文件
③ novels/output/ 下要有英文译文（用来获取每章英文长度；
   没有的话会按源文 ×3.3 估算）

【中文·会产生/修改什么文件】
不产生也不修改任何文件。只在终端打印估算结果。
"""
import argparse, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
MEAS = ROOT / "scripts" / "cost" / "measurements"
CJK = re.compile(r"[\u4e00-\u9fff]")

# 单价从 config 派生，不再手抄。手抄表会随档位调整悄悄失真——READ 从
# qwen3.8-max 降到 flash 那次，手抄的 (12,36) 会让 READ 高估 15 倍。
# 唯一权威来源是 config.MODEL_PRICES_CNY_PER_M（抓取于 2026-09-22）。
from src.config import MODEL_PRICES_CNY_PER_M, NODE_MODELS  # noqa: E402

_PRICE_NODES = ("read", "write", "readback", "fix")
try:
    PRICE = {node: MODEL_PRICES_CNY_PER_M[NODE_MODELS[node]]
             for node in _PRICE_NODES}
except KeyError as exc:
    raise SystemExit(
        f"node model {exc.args[0]} has no entry in MODEL_PRICES_CNY_PER_M; "
        f"add it to src/config.py before estimating cost") from exc

# 【中文】已交付的两本书的配置信息。
# src = 原文路径，out = 英文译文路径，genre = 类型标签。
BOOKS = {
    "difu":   {"name": "地府叫我小先生", "src": "tests/fixtures/《地府叫我小先生》 作者：界玉.txt",
               "out": "novels/output/difu_segmented/difu_en.md", "genre": "difu"},
    "wuxian": {"name": "无限恐怖", "src": "tests/fixtures/《无限恐怖》 作者：zhttty.txt",
               "out": "novels/output/limitless_horror_segmented/limitless_horror_en.md", "genre": "wuxian"},
}


def load_points():
    """加载所有实测数据点，归一化为每节点单次调用的 token 数。

    【中文】从 measurements/ 目录读取所有 cost_probe*.json 文件，
    过滤掉缺少 read/write 的记录，把每个节点的 token 除以调用次数 n。
    返回: [{genre, label, src(源文字符数), en(英文字符数),
            n: {节点名: (单次输入token, 单次输出token)}}]
    """
    pts = []
    for f in sorted(MEAS.glob("cost_probe*.json")):
        for r in json.loads(f.read_text()):
            per = r.get("per_node") or {}
            if "read" not in per or "write" not in per:
                continue
            pts.append({"genre": r.get("book", "difu"), "label": r["label"],
                        "src": r["src_chars"], "en": r["out_chars"],
                        "n": {k: (v["in"] / v["n"], v["out"] / v["n"]) for k, v in per.items()}})
    return pts


def lstsq(X, y):
    """最小二乘法求解线性方程组 Xβ = y。

    【中文】纯手写的线性回归求解器（不依赖 numpy）。
    用高斯消元法解正规方程 (XᵀX)β = Xᵀy。
    X 是 m×k 矩阵（m 个样本、k 个特征），y 是 m 维向量。
    返回 k 维系数向量 β。
    选主元时用最大值策略提高数值稳定性。
    """
    m, k = len(X), len(X[0])
    A = [[sum(X[i][a] * X[i][b] for i in range(m)) for b in range(k)] for a in range(k)]
    B = [sum(X[i][a] * y[i] for i in range(m)) for a in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(A[r][c]))
        A[c], A[p] = A[p], A[c]
        B[c], B[p] = B[p], B[c]
        if abs(A[c][c]) < 1e-12:
            continue
        for r in range(k):
            if r != c and A[r][c]:
                f = A[r][c] / A[c][c]
                for cc in range(c, k):
                    A[r][cc] -= f * A[c][cc]
                B[r] -= f * B[c]
    return [B[i] / A[i][i] if abs(A[i][i]) > 1e-12 else 0.0 for i in range(k)]


def calibrate(pts):
    """用实测数据标定每个节点的 token 预测模型。

    【中文】对 read/write/readback 三个节点分别做线性回归：
    · READ 的特征只有 [1, 源文字符数]（因为 READ 的输出是分析结果，
      不能用自身当特征，否则退化成恒等式）
    · WRITE/READBACK 的特征是 [1, 源文字符数, 英文字符数, READ输出token]
    另外记录每种类型的 READ 输出均值，用于预测时填补未知值。
    返回: {节点名: {in: 系数列表, out: 系数列表}, read_out_mean: {genre: 均值}}
    """
    cal = {}
    for node in ("read", "write", "readback"):
        rows = [p for p in pts if node in p["n"]]
        def feats(p):
            # READ 的输出不得用自身当特征，否则退化成恒等式
            if node == "read":
                return [1.0, p["src"]]
            return [1.0, p["src"], p["en"], p["n"]["read"][1]]
        cal[node] = {"in": lstsq([feats(p) for p in rows], [p["n"][node][0] for p in rows]),
                     "out": lstsq([feats(p) for p in rows], [p["n"][node][1] for p in rows])}
    cal["read_out_mean"] = {}
    for g in {p["genre"] for p in pts}:
        rows = [p for p in pts if p["genre"] == g and "read" in p["n"]]
        if rows:
            cal["read_out_mean"][g] = sum(r["n"]["read"][1] for r in rows) / len(rows)
    return cal


def md_chapter_lengths(path):
    """从英文 Markdown 文件中提取每章的字符长度。

    【中文】按 '## Chapter N:' 标题切分，返回 {章节号: 该章字符数}。
    用于获取已交付译文的实际英文长度，让估算更准确。
    """
    t = path.read_text(encoding="utf-8", errors="replace")
    marks = [(int(m.group(1)), m.start()) for m in re.finditer(r"^## Chapter (\d+):", t, re.M)]
    return {num: (marks[i + 1][1] if i + 1 < len(marks) else len(t)) - pos
            for i, (num, pos) in enumerate(marks)}


def predict_chapter(cal, genre, src_chars, en_chars):
    """预测单章各节点的 token 消耗。

    【中文】用标定好的线性模型，根据源文字符数和英文字符数，
    预测 read/write/readback 三个节点各需要多少输入/输出 token。
    READ 的输出用同类型均值替代（因为预测时还不知道 READ 会输出什么）。
    返回: {节点名: (预测输入token, 预测输出token)}
    """
    ro = cal["read_out_mean"].get(genre, sum(cal["read_out_mean"].values()) / len(cal["read_out_mean"]))
    tot = {}
    for node, c in cal.items():
        if node == "read_out_mean":
            continue
        f = [1.0, src_chars] if node == "read" else [1.0, src_chars, en_chars, ro]
        tot[node] = (sum(a * b for a, b in zip(c["in"], f)), sum(a * b for a, b in zip(c["out"], f)))
    return tot


def main(argv=None):
    """主函数：加载数据 → 标定模型 → 逐书逐章估算成本。

    【中文】参数 argv 支持外部传入命令行参数（方便测试）。
    默认估算两本书；--book 指定只估某一本；--fix-rate 指定 FIX 触发率。
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default=None, help="只估这一本（key 见脚本 BOOKS）")
    # 【中文】FIX 触发率默认 0.5，即 50% 的章节需要 FIX。
    # 这个值来自实测观察（docs/COST_MODEL.md §0.5）。
    ap.add_argument("--fix-rate", type=float, default=0.5, help="FIX 触发率，默认 0.5（实测）")
    args = ap.parse_args(argv)
    from src.text_io import detect_and_read
    from src.chapter_splitter import split_chapters, ParagraphTag

    pts = load_points()
    cal = calibrate(pts)

    # --book 传了未登记的 key 时，原先只是静默跳过所有书：退出码 0、除标定行
    # 外没有任何输出。调用方（包括 scripts/run_novel.py 打印的提示命令）无从
    # 得知估算根本没跑。宁可硬失败，也不要一个看着成功的空结果。
    #
    # 【中文】如果 --book 传了一个不在 BOOKS 字典里的 key，
    # 直接报错退出（返回码 2），而不是默默什么都不做。
    if args.book and args.book not in BOOKS:
        print(f"error: 未知书目 {args.book!r}；可用: {', '.join(sorted(BOOKS))}",
              file=sys.stderr)
        print("注：这里的 key 与 scripts/run_novel.py 的 NOVELS key 不一致"
              "（同一本书可能叫 wuxian / limitless_horror），且只包含已有实测"
              "标定点的书。", file=sys.stderr)
        return 2

    print(f"标定：{len(pts)} 个实测点（无 API 调用）")
    # 【中文】FIX 单次成本（元）。4992 in + 7016 out 是实测均值，
    # 乘以 glm-5.3 的单价 (8.0, 28.0) 元/百万 token。
    # 来历：probe_nodes.py 实测的 FIX 节点 token 消耗均值。
    fix_cost = (4992 / 1e6) * PRICE["fix"][0] + (7016 / 1e6) * PRICE["fix"][1]

    grand_t = grand_c = 0.0
    for key, b in BOOKS.items():
        if args.book and key != args.book:
            continue
        text, _ = detect_and_read(str(ROOT / b["src"]))
        chs = [c for c in split_chapters(text) if c.action != ParagraphTag.SKIP]
        outp = ROOT / b["out"]
        # 【中文】如果有英文译文就从中读取每章实际长度；
        # 没有的话后面会用 源文长度 × 3.3 估算。
        en = md_chapter_lengths(outp) if outp.exists() else {}
        tot = {n: [0.0, 0.0] for n in ("read", "write", "readback")}
        en_chars = 0.0
        for ch in chs:
            # 【中文】3.3 是中译英的经验膨胀系数（1 汉字 ≈ 3.3 英文字符）。
            e = en.get(ch.index) or len(ch.content) * 3.3      # 未交付章节按源文比例估
            en_chars += e
            for n, (i, o) in predict_chapter(cal, b["genre"], len(ch.content), e).items():
                tot[n][0] += i
                tot[n][1] += o
        # 【中文】总成本 = Σ(各节点输入token × 输入单价 + 输出token × 输出单价)。
        cost = sum(i / 1e6 * PRICE[n][0] + o / 1e6 * PRICE[n][1] for n, (i, o) in tot.items())
        tk = sum(sum(v) for v in tot.values())
        # 【中文】5.9 是英文字符数 → 英文词数的换算系数（平均词长 ≈ 5.9 字符）。
        words = en_chars / 5.9
        print(f"\n{b['name']} — {len(chs)} 章 | 英文 ≈ {words:,.0f} 词 | {tk/1e6:.1f}M tokens")
        for n, (i, o) in tot.items():
            print(f"   {n:<9}{i:>13,.0f} in{o:>13,.0f} out"
                  f"{i/1e6*PRICE[n][0]+o/1e6*PRICE[n][1]:>9.2f} 元")
        print(f"   无 FIX ¥{cost:,.2f} | FIX {args.fix_rate:.0%} 触发 ¥{cost+fix_cost*args.fix_rate*len(chs):,.2f}"
              f" | 每千词 ¥{(cost+fix_cost*args.fix_rate*len(chs))/(words/1000):.3f}")
        grand_t += tk
        grand_c += cost + fix_cost * args.fix_rate * len(chs)
    if not args.book:
        print(f"\n合计 {grand_t/1e6:.1f}M tokens | ¥{grand_c:,.2f}（FIX {args.fix_rate:.0%}）"
              f"  — 本估算未消耗任何 API 额度")


if __name__ == "__main__":
    raise SystemExit(main())
