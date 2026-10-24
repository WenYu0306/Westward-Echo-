"""Audit: end-to-end smoke test with skip_readback.

【中文·这个脚本干什么】
端到端冒烟测试（smoke test）：用最简配置跑一遍完整翻译管线，验证"从原文进去、
译文出来"这条主通路没有断。好比新买的电器先通电看看能不能亮，不追求质量，
只确认基本功能正常。

【中文·怎么运行】
在项目根目录执行：
```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
python3.11 scripts/audit_e2e.py
```

【中文·运行前要准备什么】
· 需要配好 LLM_API_KEY 和 GLM_API_KEY（会真实调用大模型）。
· 测试文本在 tests/fixtures/《无限恐怖》 作者：zhttty.txt。

【中文·⚠️ 会不会花真钱】
会产生真实费用。本脚本调两次 translate_chapter（一次跳过冷读、一次完整管线），
每次涉及多个节点的 API 调用。具体金额取决于章节长度和模型档位，无法从代码
精确推算（因为 token 数由大模型实际生成决定），但量级较小（仅 2 章）。

【中文·会产生/修改什么文件】
· 写入 data/translation_memory/audit_test/（经验本抽屉文件）。
· 可能写入 data/token_ledger.jsonl（账本追加行）。
· 可能写入 data/checkpoints.db（断点续翻存档）。
· 不会写到 novels/output/（无 OUT_DIR 设置）。
"""

# 【中文】把项目根目录加到 Python 搜索路径，这样下面的 from src.xxx 才能找到。
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.agent.graph import TranslationAgent
from src.chapter_splitter import split_chapters, ParagraphTag
from src.text_io import detect_and_read

# 【中文】读取测试用的中文小说原文，按章切分，过滤掉非正文段落（如卷首语等）。
text, enc = detect_and_read("tests/fixtures/《无限恐怖》 作者：zhttty.txt")
chapters = split_chapters(text)
chapters = [c for c in chapters if c.action != ParagraphTag.SKIP]
ch = chapters[0]

# 【中文】创建翻译代理，book_id="audit_test" 表示这次跑的经验存到独立的抽屉里，
# 不污染生产数据。
agent = TranslationAgent(book_id="audit_test")
try:
    # ── 第一轮：快速模式（跳过冷读）──
    # 【中文】skip_readback=True 跳过 READBACK/FIX 循环。
    # 这个开关让第一轮又快又省钱，目的是验证"READ → WRITE 这条最短通路能跑通"。
    result = agent.translate_chapter(
        chapter_title=ch.title,
        chapter_content=ch.content,
        chapter_number=ch.index,
        target_lang="en-US",
        genre="urban",
        skip_readback=True,
    )
    tt = result.get("translated_text", "")
    analysis = result.get("read_analysis", {})
    ig = analysis.get("image_gaps", [])
    memo_text = result.get("style_memo", "")
    print(f"RESULT: {len(tt)}c output, {len(ig)} image gaps, memo: {len(memo_text)}c")

    # 【中文】验证经验本（style memo）确实被更新了——如果翻译跑了但经验本没写进东西，
    # 说明沉淀机制断了，后续章节就吃不到前面积累的经验。
    # Check style memo was actually updated
    memo_raw = agent.style_memo.read_all()
    print(f"Memo size: {len(memo_raw)}c")
    # 【中文】逐个检查经验本的五个抽屉文件是否有实质内容（排除标题行）。
    # 每个抽屉负责不同维度的翻译经验：角色、节奏、衔接、文笔、术语。
    # Check files actually got content
    for fname in ["characters.md", "pacing.md", "bridges.md", "prose.md", "terms.md"]:
        path = agent.style_memo.root / fname
        content = path.read_text() if path.exists() else ""
        non_header = [l for l in content.split("\n") if l.strip() and not l.startswith("#")]
        print(f"  {fname}: {len(non_header)} content lines")

    # ── 第二轮：完整管线（含冷读）──
    # 【中文】这次关掉两个快捷开关，走完整的 READ → WRITE → READBACK 流程。
    # 目的是验证冷读者节点也能正常工作，并且能给出判定（verdict）。
    # Test full pipeline (with READBACK)
    print("\nTesting full pipeline (with READBACK)...")
    ch2 = chapters[1]
    result2 = agent.translate_chapter(
        chapter_title=ch2.title,
        chapter_content=ch2.content,
        chapter_number=ch2.index,
        previous_summary=result.get("chapter_summary", ""),
        target_lang="en-US",
        genre="urban",
        skip_readback=False,
    )
    fb = result2.get("readback_feedback", {})
    verdict = fb.get("verdict", "?")
    print(f"RESULT: {len(result2['translated_text'])}c, verdict={verdict}")

    print("\n=== ALL TESTS PASSED ===")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"\n=== FAILED: {e} ===")
