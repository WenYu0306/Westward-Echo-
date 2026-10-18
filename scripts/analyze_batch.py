"""Deep analysis of batch test data.

【中文·这个脚本干什么】
读取 batch_test.py 跑出来的批量测试结果（batch_results.json），
从七个维度做汇总分析：类型检测、编码分布、翻译成功率、术语表积累、
中英字数比、QA 评分、错误事件。

【中文·怎么运行】
在项目根目录下执行：
```bash
python3.11 scripts/analyze_batch.py
```

【中文·运行前要准备什么】
需要先跑过 batch_test.py，生成 tests/fixtures/batch_results.json。

【中文·⚠️ 会不会花真钱、会不会写生产数据库】
不会花真钱（不调用大模型 API）。
不会写生产数据库（只读 JSON 文件、打印结果到终端）。
注意：第 7 步会尝试调用 error_tracker 读取错误事件数据库，
但被 try/except 包裹，读不到也不会报错。

【中文·会产生/修改什么文件】
不产生任何文件，所有结果直接打印到终端。
"""
import json

# 【中文】读取 batch_test.py 产出的 JSON 结果文件。
# 这个文件记录了每本书前 3 章的翻译测试数据。
data = json.load(open("tests/fixtures/batch_results.json"))

print("=" * 60)
print("BATCH DATA ANALYSIS — 10 Novels")
print("=" * 60)

# ── 1. 类型检测 ──
# 【中文】统计有多少本书被成功识别了类型（置信度 > 0），
# 有多少本走了"探索模式"（置信度 = 0，即系统不认识这个类型）。
print("\n--- 类型检测 ---")
unknown_count = 0
detected_count = 0
for r in data:
    conf = r.get("confidence", 0)
    detected = r.get("detected_genre") or "urban"
    if conf == 0:
        unknown_count += 1
    else:
        detected_count += 1
print(f"有明确类型识别 (conf>0): {detected_count}/10")
print(f"走 discovery mode (conf=0): {unknown_count}/10")

# 【中文】列出未被识别类型的书，帮助开发者判断是否需要新增类型规则。
if unknown_count > 0:
    print("Discovery mode novels (no genre detected):")
    for r in data:
        if r.get("confidence", 0) == 0:
            print(f"  {r['name'][:40]}: {r['total_chapters']}章, {r['total_words']}字")

# ── 2. 编码分布 ──
# 【中文】统计各文件的字符编码（UTF-8 / GBK 等），确认 text_io 模块
# 的自动编码检测是否覆盖了所有常见编码。
print("\n--- 编码分布 ---")
enc_counts = {}
for r in data:
    enc = r.get("encoding", "unknown")
    enc_counts[enc] = enc_counts.get(enc, 0) + 1
for enc, count in sorted(enc_counts.items()):
    print(f"  {enc}: {count}本")

# ── 3. 翻译成功率 ──
# 【中文】统计所有书的测试章节中，成功和失败的比例。
# 每本书只测前 3 章，所以这里的样本量 = 书数 × 3。
print("\n--- 翻译统计 (每本3章) ---")
total_ok = 0
total_fail = 0
for r in data:
    ch = r["chapters_tested"]
    total_ok += sum(1 for c in ch if "error" not in c)
    total_fail += sum(1 for c in ch if "error" in c)
rate = total_fail / (total_ok + total_fail) * 100 if (total_ok + total_fail) > 0 else 0
print(f"成功: {total_ok}, 失败: {total_fail} ({rate:.1f}%)")

# ── 4. 术语表积累 ──
# 【中文】翻译 3 章后，每本书积累了多少条术语。
# 术语太少可能意味着 READ 节点没有充分提取术语，
# 或者这本书本身专有名词就少。
print("\n--- 术语表积累 (3章后) ---")
sizes = [r["glossary_size"] for r in data]
print(f"平均: {sum(sizes)/len(sizes):.0f}条, 最小: {min(sizes)}, 最大: {max(sizes)}")
few_terms = [r for r in data if r["glossary_size"] <= 5]
if few_terms:
    print(f"术语少(≤5条): {len(few_terms)}本")
    for r in few_terms:
        ch = r["chapters_tested"]
        ok = sum(1 for c in ch if "error" not in c)
        genre = r.get("detected_genre") or "urban"
        print(f"  {r['name'][:35]}: {r['glossary_size']}条, genre={genre}, {ok}/{len(ch)} ok")

# ── 5. 中英字数比 ──
# 【中文】英文译文字符数 / 中文原文字符数。
# 健康区间大约 2.0–4.0x；太低可能是截断，太高可能是注水。
print("\n--- 中英字数比 ---")
ratios = []
for r in data:
    for c in r["chapters_tested"]:
        if "words_cn" in c and "words_en" in c and c["words_cn"] > 0 and c["words_en"] > 0:
            ratios.append(c["words_en"] / c["words_cn"])
if ratios:
    print(f"平均: {sum(ratios)/len(ratios):.1f}x ({len(ratios)}章节)")
    print(f"范围: {min(ratios):.1f}x - {max(ratios):.1f}x")

# ── 6. QA 评分 ──
# 【中文】质量检查节点的打分分布（满分 5.0）。
# 注意：这是模型自评，不是人类评分，仅供参考。
print("\n--- QA评分 ---")
scores = []
for r in data:
    for c in r["chapters_tested"]:
        s = c.get("score", 0)
        if isinstance(s, (int, float)) and s > 0:
            scores.append(s)
if scores:
    print(f"均分: {sum(scores)/len(scores):.1f}/5.0 ({len(scores)}章)")
    excellent = sum(1 for s in scores if s >= 5.0)
    good = sum(1 for s in scores if 4.0 <= s < 5.0)
    ok_s = sum(1 for s in scores if 3.0 <= s < 4.0)
    poor = sum(1 for s in scores if s < 3.0)
    print(f"  5.0: {excellent}, 4.x: {good}, 3.x: {ok_s}, <3: {poor}")

# ── 7. 错误事件 ──
# 【中文】尝试从错误追踪器读取最近 1 天的错误事件汇总。
# 被 try/except 包裹：如果 error_tracker 模块不可用或数据库不存在，
# 只打印提示而不中断整个分析。
print("\n--- 错误事件 (本次) ---")
try:
    from src.error_tracker import get_event_summary
    events = get_event_summary(days=1)
    if events.get("total", 0) > 0:
        for etype, count in sorted(events.items()):
            if etype != "total" and count > 0:
                print(f"  {etype}: {count}")
    else:
        print("  (无错误事件记录)")
except Exception as e:
    print(f"  (error_tracker不可用: {e})")
