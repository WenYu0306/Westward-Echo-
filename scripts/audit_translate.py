"""Translate 15 chapters of 无限恐怖 with full quality pipeline and cold read.

【中文·这个脚本干什么】
翻译质量审计：拿《无限恐怖》前 15 章跑一遍完整翻译管线，产出译文和质量判定，
用来验证"整条流水线从头到尾能跑出合格结果"。比 audit_e2e.py 更重——它跑 15 章
而不是 2 章，且在第 1/5/10/15 章开启完整冷读（READBACK），其余章用快速模式。

【中文·怎么运行】
在项目根目录执行：
```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
python3.11 scripts/audit_translate.py
```

【中文·运行前要准备什么】
· 需要配好 LLM_API_KEY 和 GLM_API_KEY（会真实调用大模型）。
· 测试文本在 tests/fixtures/《无限恐怖》 作者：zhttty.txt。

【中文·⚠️ 会不会花真钱】
会产生真实费用。15 章翻译，其中 4 章走完整管线（含冷读+可能的 FIX），
11 章走快速模式（跳过冷读+flash 写入）。具体金额取决于实际 token 消耗，
无法从代码精确推算。量级比 audit_e2e.py 大约 7-8 倍。

【中文·会产生/修改什么文件】
· novels/output/audit_15/translation.md —— 15 章的英文译文合集。
· novels/output/audit_15/quality.json —— 4 个抽样章的冷读判定结果。
· data/translation_memory/audit_15/ —— 经验本抽屉文件。
· data/token_ledger.jsonl —— 账本追加行。
"""

# 【中文】把项目根目录加到搜索路径，导入所需模块。
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.agent.graph import TranslationAgent
from src.chapter_splitter import split_chapters, ParagraphTag
from src.text_io import detect_and_read
from src.circuit_breaker import CircuitBreakerOpenError

# 【中文】输出目录和章节数常量。N=15 是审计用的固定样本量，不是全书。
OUT_DIR = "novels/output/audit_15"
N = 15

# 【中文】读取中文原文、按章切分、过滤非正文段落，只取前 N 章。
text, enc = detect_and_read("tests/fixtures/《无限恐怖》 作者：zhttty.txt")
chapters = split_chapters(text)
chapters = [c for c in chapters if c.action != ParagraphTag.SKIP][:N]

os.makedirs(OUT_DIR, exist_ok=True)
out_file = os.path.join(OUT_DIR, "translation.md")
quality_file = os.path.join(OUT_DIR, "quality.json")

# 【中文】book_id="audit_15" 让这次审计的经验存到独立抽屉，不污染生产数据。
agent = TranslationAgent(book_id="audit_15")
prev = ""
results = []

# 【中文】压低第三方库的日志级别，避免大量 HTTP/连接日志淹没审计输出。
import logging
for name in ["httpx", "httpcore", "openai", "chromadb", "urllib3"]:
    logging.getLogger(name).setLevel(logging.WARNING)

t0 = time.monotonic()

for i, ch in enumerate(chapters):
    # 【中文】第 1/5/10/15 章是"关键检查点"（key checkpoints），走完整管线；
    # 其余章跳过冷读+用 flash 写入，省钱省时间。这种抽样策略是在"覆盖面"和
    # "成本"之间取平衡——全走完整管线太贵，全跳过又验不到冷读。
    is_sample = (i+1) in {1, 5, 10, 15}  # key checkpoints
    print(f"[{i+1}/{N}] Ch{ch.index} ({ch.word_count}c)...", end=" ", flush=True)

    try:
        result = agent.translate_chapter(
            chapter_title=ch.title,
            chapter_content=ch.content,
            chapter_number=ch.index,
            previous_summary=prev,
            target_lang="en-US",
            genre="urban",
            skip_readback=not is_sample,
            use_flash_writer=not is_sample,
        )
    except Exception as e:
        print(f"ERROR: {e}")
        continue

    tt = result.get("translated_text", "")
    prev = result.get("chapter_summary", "")
    fb = result.get("readback_feedback", {})

    # 【中文】P = 完整管线（Pro），F = 快速模式（Flash）。标签打在输出里方便扫读。
    tag = "P" if is_sample else "F"
    v = fb.get("verdict", "?") if fb else "?"
    print(f"{len(tt)}c {tag} [{v}]")

    # 【中文】把每章译文追加到输出文件。首次写入时先加标题头。
    exists = os.path.exists(out_file)
    with open(out_file, "a" if exists else "w", encoding="utf-8") as f:
        if not exists:
            f.write("# 无限恐怖 — Audit Translation (15 chapters)\n\n")
        f.write(f"## Chapter {ch.index}: {ch.title[:60]}\n\n{tt}\n\n---\n\n")

    # 【中文】只有走了完整管线的章才有冷读反馈（fb），把这些判定结果攒起来
    # 写到 quality.json，供后续分析或人工复核。
    if fb and fb.get("verdict"):
        results.append({
            "ch": ch.index, "verdict": fb["verdict"],
            "keep": fb.get("would_keep_reading"),
            "impression": fb.get("overall_impression", "")[:250],
            "output_len": len(tt),
        })
        json.dump(results, open(quality_file, "w"), ensure_ascii=False, indent=2)

elapsed = (time.monotonic() - t0) / 60
print(f"\nDone: {N} chapters in {elapsed:.0f}m | {len(agent.exact_store)} terms")
print(f"Output: {out_file}")
print(f"Quality: {quality_file}")
for r in results:
    print(f"  Ch{r['ch']}: [{r['verdict']}] keep={r['keep']} — {r['impression'][:120]}")
