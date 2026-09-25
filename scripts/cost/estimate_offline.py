"""零 API 成本估算：不调任何模型，直接算出一本书翻译要花多少钱。

原理：每节点的 token 数与"源文字符数 / 英文输出字符数"近似线性，
用 scripts/cost/measurements/ 里的实测点标定，再套到真实语料上。

用法：
    python3 scripts/cost/estimate_offline.py                # 估算已交付的两本
    python3 scripts/cost/estimate_offline.py --book 间客     # 估算一本新书（先跑一遍拿英文长度）
"""
import argparse, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
MEAS = ROOT / "scripts" / "cost" / "measurements"
CJK = re.compile(r"[\u4e00-\u9fff]")

# 现抓单价（元/百万 token）：脚本头改这 6 个数即可跟随定价页变动
PRICE = {"read": (12.0, 36.0),       # qwen3.8-max
         "write": (0.8, 2.8),        # glm-5.3-flash
         "readback": (12.0, 36.0),   # qwen3.8-max
         "fix": (8.0, 28.0)}         # glm-5.3

BOOKS = {
    "difu":   {"name": "地府叫我小先生", "src": "tests/fixtures/《地府叫我小先生》 作者：界玉.txt",
               "out": "novels/output/difu_segmented/difu_en.md", "genre": "difu"},
    "wuxian": {"name": "无限恐怖", "src": "tests/fixtures/《无限恐怖》 作者：zhttty.txt",
               "out": "novels/output/limitless_horror_segmented/limitless_horror_en.md", "genre": "wuxian"},
}


def load_points():
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
    t = path.read_text(encoding="utf-8", errors="replace")
    marks = [(int(m.group(1)), m.start()) for m in re.finditer(r"^## Chapter (\d+):", t, re.M)]
    return {num: (marks[i + 1][1] if i + 1 < len(marks) else len(t)) - pos
            for i, (num, pos) in enumerate(marks)}


def predict_chapter(cal, genre, src_chars, en_chars):
    ro = cal["read_out_mean"].get(genre, sum(cal["read_out_mean"].values()) / len(cal["read_out_mean"]))
    tot = {}
    for node, c in cal.items():
        if node == "read_out_mean":
            continue
        f = [1.0, src_chars] if node == "read" else [1.0, src_chars, en_chars, ro]
        tot[node] = (sum(a * b for a, b in zip(c["in"], f)), sum(a * b for a, b in zip(c["out"], f)))
    return tot


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default=None, help="只估这一本（key 见脚本 BOOKS）")
    ap.add_argument("--fix-rate", type=float, default=0.5, help="FIX 触发率，默认 0.5（实测）")
    args = ap.parse_args(argv)
    from src.text_io import detect_and_read
    from src.chapter_splitter import split_chapters, ParagraphTag

    pts = load_points()
    cal = calibrate(pts)

    # --book 传了未登记的 key 时，原先只是静默跳过所有书：退出码 0、除标定行
    # 外没有任何输出。调用方（包括 scripts/run_novel.py 打印的提示命令）无从
    # 得知估算根本没跑。宁可硬失败，也不要一个看着成功的空结果。
    if args.book and args.book not in BOOKS:
        print(f"error: 未知书目 {args.book!r}；可用: {', '.join(sorted(BOOKS))}",
              file=sys.stderr)
        print("注：这里的 key 与 scripts/run_novel.py 的 NOVELS key 不一致"
              "（同一本书可能叫 wuxian / limitless_horror），且只包含已有实测"
              "标定点的书。", file=sys.stderr)
        return 2

    print(f"标定：{len(pts)} 个实测点（无 API 调用）")
    fix_cost = (4992 / 1e6) * PRICE["fix"][0] + (7016 / 1e6) * PRICE["fix"][1]

    grand_t = grand_c = 0.0
    for key, b in BOOKS.items():
        if args.book and key != args.book:
            continue
        text, _ = detect_and_read(str(ROOT / b["src"]))
        chs = [c for c in split_chapters(text) if c.action != ParagraphTag.SKIP]
        outp = ROOT / b["out"]
        en = md_chapter_lengths(outp) if outp.exists() else {}
        tot = {n: [0.0, 0.0] for n in ("read", "write", "readback")}
        en_chars = 0.0
        for ch in chs:
            e = en.get(ch.index) or len(ch.content) * 3.3      # 未交付章节按源文比例估
            en_chars += e
            for n, (i, o) in predict_chapter(cal, b["genre"], len(ch.content), e).items():
                tot[n][0] += i
                tot[n][1] += o
        cost = sum(i / 1e6 * PRICE[n][0] + o / 1e6 * PRICE[n][1] for n, (i, o) in tot.items())
        tk = sum(sum(v) for v in tot.values())
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
