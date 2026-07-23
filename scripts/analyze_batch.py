"""Deep analysis of batch test data."""
import json

data = json.load(open("tests/fixtures/batch_results.json"))

print("=" * 60)
print("BATCH DATA ANALYSIS — 10 Novels")
print("=" * 60)

# 1. Type misclassification
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

if unknown_count > 0:
    print("Discovery mode novels (no genre detected):")
    for r in data:
        if r.get("confidence", 0) == 0:
            print(f"  {r['name'][:40]}: {r['total_chapters']}章, {r['total_words']}字")

# 2. Encoding
print("\n--- 编码分布 ---")
enc_counts = {}
for r in data:
    enc = r.get("encoding", "unknown")
    enc_counts[enc] = enc_counts.get(enc, 0) + 1
for enc, count in sorted(enc_counts.items()):
    print(f"  {enc}: {count}本")

# 3. Translation success
print("\n--- 翻译统计 (每本3章) ---")
total_ok = 0
total_fail = 0
for r in data:
    ch = r["chapters_tested"]
    total_ok += sum(1 for c in ch if "error" not in c)
    total_fail += sum(1 for c in ch if "error" in c)
rate = total_fail / (total_ok + total_fail) * 100 if (total_ok + total_fail) > 0 else 0
print(f"成功: {total_ok}, 失败: {total_fail} ({rate:.1f}%)")

# 4. Glossary
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

# 5. Word ratio
print("\n--- 中英字数比 ---")
ratios = []
for r in data:
    for c in r["chapters_tested"]:
        if "words_cn" in c and "words_en" in c and c["words_cn"] > 0 and c["words_en"] > 0:
            ratios.append(c["words_en"] / c["words_cn"])
if ratios:
    print(f"平均: {sum(ratios)/len(ratios):.1f}x ({len(ratios)}章节)")
    print(f"范围: {min(ratios):.1f}x - {max(ratios):.1f}x")

# 6. Quality scores
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

# 7. Error events
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
