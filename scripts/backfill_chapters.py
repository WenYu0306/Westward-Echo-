"""Re-translate specific chapters that were skipped or truncated.

⚠️ **本脚本会写入生产数据库和修改交付物文件，运行前请确认已备份！**
· 会调用大模型 API → **产生真实费用**（每章一次完整翻译流水线）
· 会往 novels/output/difu_segmented/difu_en.md **追加**重译内容
· 会通过 TranslationAgent 写入 data/checkpoints.db（术语库、存档）
· 硬编码了 12 个章节号和《地府叫我小先生》的配置，不是通用脚本

【中文·这个脚本干什么】
把之前翻译失败或被截断的特定章节重新翻译一遍，追加到成品文件末尾。
相当于"补漏"——全书翻完后发现有些章节没翻好，用这个脚本单独重翻。

【中文·怎么运行】
```bash
python3.11 scripts/backfill_chapters.py
```
不需要传参数，要重翻的章节号硬编码在 CHAPTERS_TO_FIX 里。

【中文·运行前要准备什么】
① tests/fixtures/ 下要有《地府叫我小先生》原文
② novels/output/difu_segmented/_checkpoint.json 必须存在
   （里面存着术语表快照和前文摘要，重翻时需要这些上下文）
③ .env 中配好 GLM_API_KEY 和 LLM_API_KEY

【中文·⚠️ 会不会花真钱、会不会写生产数据库】
⚠️ **会花真钱**：每章调用完整翻译流水线（READ → WRITE），
使用 GLM flash + Qwen，费用取决于章节长度和模型档位。
⚠️ **会写生产数据库**：TranslationAgent 内部会更新
data/checkpoints.db 中的术语表和 checkpoint 记录。
⚠️ **会修改交付物**：以追加模式写入 difu_en.md。

【中文·会产生/修改什么文件】
· 修改：novels/output/difu_segmented/difu_en.md（追加）
· 修改：data/checkpoints.db（通过 TranslationAgent）
"""
import sys, os, json, re, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.agent.graph import TranslationAgent
from src.chapter_splitter import split_chapters, ParagraphTag
from src.text_io import detect_and_read

# 【中文】需要重翻的英文章号列表。这些是之前翻译时被跳过或截断的章节。
# 注意：这是硬编码的，每次要补不同的章节需要手动改这个列表。
CHAPTERS_TO_FIX = [1656, 1686, 1741, 1748, 1813, 1818, 1904, 1939, 1969, 1971, 1972, 1988]

# 【中文】《地府叫我小先生》的配置信息，硬编码而非从 run_novel.py 导入。
cfg = {
    "name": "地府叫我小先生",
    "path": "tests/fixtures/《地府叫我小先生》 作者：界玉.txt",
    "genre": "folk_religion",
    "book_id": "difu_xiao_xiansheng",
    "output_dir": "novels/output/difu_segmented",
    "output_file": "difu_en.md",
}

ckpt_file = os.path.join(cfg["output_dir"], "_checkpoint.json")
out_file = os.path.join(cfg["output_dir"], cfg["output_file"])

# 【中文】读取原文并切分章节，建立"章号 → 章节对象"的索引。
text, enc = detect_and_read(cfg["path"])
chapters = split_chapters(text)
chapters = [c for c in chapters if c.action != ParagraphTag.SKIP]
src_by_index = {c.index: c for c in chapters}
total = len(chapters)

# 【中文】加载 checkpoint 里的术语表快照和前文摘要。
# 这样重翻时能继承之前的术语决策，不会从头开始积累。
ckpt = json.load(open(ckpt_file))
agent = TranslationAgent(book_id=cfg["book_id"])
agent.load_glossary_snapshot(ckpt.get("glossary_snapshot", "{}"))
prev = ckpt.get("previous_summary", "")

# 【中文】压低第三方库的日志级别，避免翻译过程中被大量 HTTP 日志淹没。
import logging
for name in ["httpx", "httpcore", "openai", "chromadb", "urllib3"]:
    logging.getLogger(name).setLevel(logging.WARNING)

# 【中文】逐章重翻。skip_readback=True 表示跳过冷读环节（省时间省钱），
# use_flash_writer=True 表示用便宜的 flash 模型做 WRITE。
for en_idx in CHAPTERS_TO_FIX:
    src_ch = src_by_index.get(en_idx)
    if src_ch is None:
        print(f"Ch{en_idx}: source not found, skipping")
        continue

    # Find position in chapters list
    pos = [i for i, c in enumerate(chapters) if c.index == en_idx]
    if not pos:
        print(f"Ch{en_idx}: not in chapter list, skipping")
        continue
    pos = pos[0]

    ch = chapters[pos]
    print(f"[{pos+1}/{total} Ch{en_idx}] {len(ch.content)} chars ...", end=" ", flush=True)

    try:
        result = agent.translate_chapter(
            chapter_title=ch.title,
            chapter_content=ch.content,
            chapter_number=ch.index,
            previous_summary=prev,
            target_lang="en-US",
            genre=cfg["genre"],
            skip_readback=True,
            use_flash_writer=True,
        )
    except Exception as e:
        print(f"ERROR: {e}")
        continue

    tt = result.get("translated_text", "")
    prev = result.get("chapter_summary", "")
    # 【中文】译文少于 100 字符标记为 SHORT，可能是截断或翻译失败。
    ok_flag = "OK" if len(tt) >= 100 else "SHORT"

    print(f"{len(tt)}c [{ok_flag}]")

    # 【中文】⚠️ 以追加模式写入交付物文件。注意是 append 不是 overwrite，
    # 所以多次运行会在文件末尾重复追加同一章节。
    with open(out_file, "a", encoding="utf-8") as f:
        f.write(f"## Chapter {en_idx}: {ch.title[:60]}\n\n{tt}\n\n---\n\n")

print("\nDone.")
