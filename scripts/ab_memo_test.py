"""A/B test: does the style memo improve translation quality?

Phase 1 (once): Translate chapters 1-194 with memo ON, building up
    translation experience in data/translation_memory/<book_id>/.

Phase 2 (A/B): Translate chapters 195-205 twice:
    Run A — memo disabled (TranslationAgent with book_id=None, fresh memo)
    Run B — memo enabled  (TranslationAgent reusing the Phase 1 book_id)

Both runs use full pipeline (Pro WRITE + READBACK) so cold-read scores
are comparable.  The glossary accumulated in Phase 1 is shared between
both runs to isolate the memo's effect from terminology accumulation.

Output: novels/output/ab_memo_test_*/quality.json with per-chapter verdicts.

【中文·这个脚本干什么】
验证"翻译经验本（style memo）到底有没有用"的 A/B 对比测试。
A/B 测试就是：同一批稿子用两套配置各跑一遍，对比结果好坏，
从而知道改动到底有没有用、值不值这个钱。
· 对照组（Run A）：关掉经验本，翻译时不带任何前面积累的经验。
· 实验组（Run B）：开着经验本，翻译时带上第一阶段积累的 194 章经验。
· 比什么指标：冷读判定（PASS 数量）、读者是否愿意继续读（keep_reading）。
· 怎么算通过：B 组 PASS 数 > A 组 → 经验本有用；相等 → 无效果；更少 → 反而有害。

⚠️ **当前状态**：README 提到这个 A/B "基础设施建好了但从未真正跑过对比"。
从代码看，脚本结构完整、可以运行，但没有 archive/ 下的历史产物或交接文档
记录实际执行结果。注释如实描述代码逻辑，不编造未发生的测试结论。

【中文·怎么运行】
```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
python3.11 scripts/ab_memo_test.py
```

【中文·运行前要准备什么】
· LLM_API_KEY 和 GLM_API_KEY（会真实调用大模型）。
· tests/fixtures/《无限恐怖》 作者：zhttty.txt 必须存在。

【中文·⚠️ 会不会花真钱】
会产生真实费用，且金额较大：
· 第一阶段：194 章完整管线翻译（积累经验的"预热"阶段）。
· 第二阶段：11 章 × 2 组 = 22 次完整管线翻译。
合计约 216 章次的翻译调用。具体金额无法从代码精确推算（token 数由大模型
实际生成决定），但这是所有 scripts/ 里最贵的脚本之一。

【中文·会产生/修改什么文件】
· novels/output/ab_memo_test_phase1/ —— 第一阶段的译文和经验本。
· novels/output/ab_memo_test_a/ —— A 组（无经验本）的译文和质量判定。
· novels/output/ab_memo_test_b/ —— B 组（有经验本）的译文和质量判定。
· data/translation_memory/ab_memo_test*/ —— 各组经验本抽屉。
· data/token_ledger.jsonl —— 账本追加行。
"""

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.graph import TranslationAgent
from src.chapter_splitter import ParagraphTag, split_chapters
from src.circuit_breaker import CircuitBreakerOpenError
from src.text_io import detect_and_read

NOVEL_PATH = "tests/fixtures/《无限恐怖》 作者：zhttty.txt"
BOOK_ID = "ab_memo_test"
FIRST_CHAPTER = 1
MEMO_BUILD_END = 194   # chapters to build memo from (exclusive)
TEST_START = 195
TEST_END = 205          # chapters to A/B test (inclusive)
OUT_DIR_A = "novels/output/ab_memo_test_a"   # memo OFF
OUT_DIR_B = "novels/output/ab_memo_test_b"   # memo ON
PHASE1_DIR = "novels/output/ab_memo_test_phase1"


def _suppress_noise():
    # 【中文】压低第三方库日志，避免 HTTP/连接日志淹没测试输出。
    for name in ["httpx", "httpcore", "openai", "chromadb", "urllib3"]:
        logging.getLogger(name).setLevel(logging.WARNING)


def _translate_range(agent, chapters, start, end, book_id, out_dir, label):
    """Translate chapters [start, end) (0-indexed) and save quality data.

    【中文·作用】翻译指定范围的章节，把译文和质量判定分别存到文件里。
    这是 A/B 测试的核心执行函数——第一阶段和 A/B 两组都调它。

    【中文·参数】
    · agent: TranslationAgent 实例，携带（或不携带）经验本。
    · chapters: 全部章节列表（从 split_chapters 得到）。
    · start/end: 要翻译的章节索引范围（0-based，左闭右开）。
    · book_id: 传给 agent 的书目 ID，决定经验本存到哪。
    · out_dir: 输出目录路径。
    · label: 打印标签（如 "PHASE1"/"A-NOMEMO"/"B-MEMO"），方便区分输出。

    【中文·返回】质量判定列表（每项含 verdict、keep_reading 等字段）。
    """
    os.makedirs(out_dir, exist_ok=True)
    quality_file = os.path.join(out_dir, "quality.json")
    out_file = os.path.join(out_dir, "translation.md")
    results = []
    prev = ""

    for i in range(start, min(end, len(chapters))):
        ch = chapters[i]
        # 【中文】A/B 测试中每一章都走完整管线（is_sample=True），因为要比的是
        # 冷读判定质量，跳过冷读就拿不到可比数据。这与 audit_translate.py 的
        # 抽样策略不同——那里是为了省钱，这里是为了可比性。
        is_sample = True  # Every chapter gets full pipeline for A/B comparison
        pos = i + 1

        try:
            result = agent.translate_chapter(
                chapter_title=ch.title,
                chapter_content=ch.content,
                chapter_number=ch.index,
                previous_summary=prev,
                target_lang="en-US",
                genre="urban",
                skip_readback=not is_sample,
            )
        except CircuitBreakerOpenError:
            # 【中文】熔断器打开 = 连续失败太多，自动保护停机。
            # 直接 break 而不是 continue：熔断后继续调 API 只会浪费钱。
            print(f"  [{label}] Ch{ch.index}: CIRCUIT BREAKER OPEN — stopping.")
            break
        except Exception as e:
            # 【中文】单章失败不中断整批：记错误后跳到下一章。
            print(f"  [{label}] Ch{ch.index}: ERROR — {e}")
            continue

        tt = result.get("translated_text", "")
        prev = result.get("chapter_summary", "")
        fb = result.get("readback_feedback", {})

        verdict = fb.get("verdict", "?")
        keep = fb.get("would_keep_reading")
        score = result.get("quality_score", "?")
        print(f"  [{label}] Ch{pos}/{end - start}: {len(tt)}c [{verdict}] keep={keep}")

        # 【中文】把译文追加到输出文件，首次写入时加标题头。
        # Append to output file
        exists = os.path.exists(out_file)
        with open(out_file, "a" if exists else "w", encoding="utf-8") as f:
            if not exists:
                f.write(f"# AB Memo Test — {label}\n\n")
            f.write(f"## Chapter {ch.index}: {ch.title[:60]}\n\n{tt}\n\n---\n\n")

        # 【中文】只收集有冷读反馈的章（即走了完整管线的章）的质量数据。
        if fb and fb.get("verdict"):
            results.append({
                "ch": ch.index,
                "pos": pos,
                "verdict": verdict,
                "keep_reading": keep,
                "quality_score": score,
                "impression": fb.get("overall_impression", "")[:300],
                "output_len": len(tt),
            })

    with open(quality_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    return results


def main():
    """主入口：依次跑三个阶段，最后对比 A/B 结果。

    【中文·流程】
    ① 第一阶段（预热）：用经验本翻译前 194 章，积累经验。
    ② Run A（对照组）：关掉经验本，翻译第 195-205 章。
    ③ Run B（实验组）：开着经验本，翻译同样的 195-205 章。
    ④ 对比两组 PASS 数量和 keep_reading 数量。
    """
    _suppress_noise()
    text, _ = detect_and_read(NOVEL_PATH)
    chapters = split_chapters(text)
    chapters = [c for c in chapters if c.action != ParagraphTag.SKIP]
    total = len(chapters)
    print(f"Loaded {total} chapters from {NOVEL_PATH}")
    print(f"Memo build: chapters 1–{MEMO_BUILD_END - 1}")
    print(f"A/B test:  chapters {TEST_START}–{TEST_END}")

    # ── Phase 1: Build memo (chapters 1–194) ────────────────────────
    # 【中文】第一阶段：用 194 章"喂"经验本。这些章节的翻译产物本身不是测试目标，
    # 目的是让经验本积累足够的翻译经验（文风、节奏、角色处理等），供 B 组使用。
    memo_agent = TranslationAgent(book_id=BOOK_ID)
    print(f"\n=== PHASE 1: Building style memo (ch 1–{MEMO_BUILD_END - 1}) ===")
    t0 = time.monotonic()
    _translate_range(
        memo_agent, chapters, FIRST_CHAPTER - 1, MEMO_BUILD_END,
        BOOK_ID, PHASE1_DIR, "PHASE1",
    )
    elapsed = (time.monotonic() - t0) / 60
    print(f"Phase 1 done in {elapsed:.1f}m | {len(memo_agent.exact_store)} glossary terms")

    # 【中文】保存术语表快照：A/B 两组要共享同一份术语表，这样比出来的差异
    # 才能归因到"经验本"而不是"术语积累量不同"。这是控制变量的关键。
    # Save phase 1 glossary snapshot for both A/B runs
    glossary_snapshot = memo_agent.exact_store.snapshot()

    # ── Run A: Memo OFF ─────────────────────────────────────────────
    # 【中文】对照组：用一个全新的 book_id（经验本为空），但灌入第一阶段的术语表。
    # 这样 A 组和 B 组的唯一区别就是"有没有经验本"，术语积累量相同。
    # Use a fresh book_id so the style memo is empty — but restore the
    # phase 1 glossary so terminology accumulation doesn't confound
    # the comparison.
    agent_a = TranslationAgent(book_id="ab_memo_test_a_nomemo")
    if glossary_snapshot and glossary_snapshot != "{}":
        agent_a.load_glossary_snapshot(glossary_snapshot)

    print(f"\n=== RUN A: Memo OFF (ch {TEST_START}–{TEST_END}) ===")
    t0 = time.monotonic()
    results_a = _translate_range(
        agent_a, chapters, TEST_START - 1, TEST_END,
        "ab_memo_test_a", OUT_DIR_A, "A-NOMEMO",
    )
    elapsed_a = (time.monotonic() - t0) / 60

    # ── Run B: Memo ON ──────────────────────────────────────────────
    # 【中文】实验组：直接用第一阶段的 agent（经验本+术语表都在），翻译同样的章节。
    # Reuse the Phase 1 agent which has the accumulated style memo + glossary.
    print(f"\n=== RUN B: Memo ON (ch {TEST_START}–{TEST_END}) ===")
    t0 = time.monotonic()
    results_b = _translate_range(
        memo_agent, chapters, TEST_START - 1, TEST_END,
        BOOK_ID, OUT_DIR_B, "B-MEMO",
    )
    elapsed_b = (time.monotonic() - t0) / 60

    # ── Summary ──────────────────────────────────────────────────────
    # 【中文】汇总对比：数两组的 PASS 数和 keep_reading 数，算差值。
    # delta > 0 → 经验本提升了质量；= 0 → 无效果；< 0 → 反而有害。
    a_pass = sum(1 for r in results_a if r["verdict"] == "PASS")
    b_pass = sum(1 for r in results_b if r["verdict"] == "PASS")
    a_keep = sum(1 for r in results_a if r.get("keep_reading"))
    b_keep = sum(1 for r in results_b if r.get("keep_reading"))

    print(f"\n=== RESULTS ===")
    print(f"Run A (no memo):  {a_pass}/{len(results_a)} PASS, {a_keep} keep-reading, {elapsed_a:.1f}m")
    print(f"Run B (memo):    {b_pass}/{len(results_b)} PASS, {b_keep} keep-reading, {elapsed_b:.1f}m")

    if len(results_a) == len(results_b) and len(results_a) > 0:
        delta = b_pass - a_pass
        print(f"Memo delta:      {'+' if delta >= 0 else ''}{delta} PASS verdicts")
        if delta > 0:
            print("Style memo IMPROVES quality in this test window.")
        elif delta == 0:
            print("Style memo had NO MEASURABLE EFFECT in this test window.")
        else:
            print("Style memo DEGRADED quality — investigate memo contamination.")

    print(f"\nQuality files:")
    print(f"  Run A: {os.path.join(OUT_DIR_A, 'quality.json')}")
    print(f"  Run B: {os.path.join(OUT_DIR_B, 'quality.json')}")


if __name__ == "__main__":
    main()
