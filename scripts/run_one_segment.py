"""Translate all 775 chapters — auto-advance, checkpoint-safe, quality-tested.

Run from your terminal:  python3 scripts/run_one_segment.py

Translation runs continuously. At sample points (every ~50 chapters + key
milestones), the full pipeline runs (Pro WRITE + READBACK cold reader), and
the cold reader's verdict is saved to _quality.json. You can check it anytime:
  cat novels/output/limitless_horror_segmented/_quality.json

════════════════════════════════════════════════════════════════════════
【中文·README 官方推荐的全本翻译入口】
════════════════════════════════════════════════════════════════════════

① 这个脚本干什么？
   把《无限恐怖》全书 775 章从中文翻译成英文。
   每 15 章为一段，跑完一段存一次档（checkpoint），
   中途崩了或按 Ctrl+C 停了，重启后从最后存档点继续。
   每隔约 50 章 + 关键里程碑章节做一次完整质量抽检，
   结果存到 _quality.json。

② 怎么运行？
   在项目根目录下执行：
     cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
     python3.11 scripts/run_one_segment.py
   ⚠️ 必须用 python3.11，系统自带的 python3 是 3.9 跑不了。

③ 运行前要准备什么？
   · .env 文件里配好 LLM_API_KEY 和 GLM_API_KEY（大模型密钥）
   · tests/fixtures/ 下要有《无限恐怖》原文 txt 文件
   · 不需要启动 Redis（命令行脚本不走 Celery）

④ ⚠️ 会不会花真钱？
   会！每章都要调用大模型 API，会产生真实费用。
   普通章节走快速模式（Flash WRITE，跳过冷读者），比较便宜；
   抽检章节走完整流水线（Pro WRITE + READBACK），贵约一倍。
   全书 775 章的总花费取决于 token 消耗量，无法在此精确预估，
   可用 scripts/cost/estimate_offline.py 做零成本估算。

⑤ 会产生/修改什么文件？
   · novels/output/limitless_horror_segmented/limitless_horror_en.md
     → 英文译文成品（追加写入）
   · novels/output/limitless_horror_segmented/_checkpoint.json
     → 断点存档（覆盖写入）
   · novels/output/limitless_horror_segmented/_glossary.json
     → 术语表快照（覆盖写入）
   · novels/output/limitless_horror_segmented/_quality.json
     → 质量抽检记录（追加写入）
"""
import sys, os, time, json
# 【中文】把项目根目录加到 Python 搜索路径里，
# 这样下面的 import src.xxx 才能找到。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import delivery
from src.agent.graph import TranslationAgent
from src.chapter_splitter import split_chapters, ParagraphTag
from src.text_io import detect_and_read
from src.circuit_breaker import CircuitBreakerOpenError

# 【中文】输出目录、书籍标识、原文路径、分段大小。
# SEGMENT=15 表示每 15 章为一段，跑完一段存一次档。
# 这样中途出问题最多损失 15 章的进度。
CKPT_DIR = "novels/output/limitless_horror_segmented"
BOOK_ID = "limitless_horror_segmented"
NOVEL_PATH = "tests/fixtures/《无限恐怖》 作者：zhttty.txt"
SEGMENT = 15

# Sample points for quality checks (1-based position in the translatable list)
# 【中文】质量抽检点：哪些章节要走完整流水线（含冷读者）。
# 规则：每隔 50 章 + 几个关键里程碑（25/75/100/200/.../775）。
# 不在抽检点的章节走快速模式（跳过 READBACK），省钱省时间。
SAMPLE_POINTS = frozenset(range(10, 780, 50)) | frozenset({25, 75, 100, 200, 300, 400, 500, 600, 700, 775})


def main():
    """翻译全书的主函数。

    【中文·整体流程】
    ① 读原文、切章节
    ② 检查有没有存档，有就从存档点继续
    ③ 按 15 章一段循环翻译，每段结束存一次档
    ④ 抽检章节走完整流水线，其余走快速模式
    ⑤ 全部跑完后打印总结
    """
    # 【中文】读取原文文件，自动检测编码（UTF-8/GBK 等）。
    text, enc = detect_and_read(NOVEL_PATH)
    # 【中文】把全文切成章节列表。
    chapters = split_chapters(text)
    # 【中文】过滤掉非正文内容（如目录、作者声明），只取前 775 章。
    chapters = [c for c in chapters if c.action != ParagraphTag.SKIP][:775]

    # 【中文】定义各种输出文件的路径。
    ckpt_file = os.path.join(CKPT_DIR, "_checkpoint.json")
    out_file = os.path.join(CKPT_DIR, "limitless_horror_en.md")
    glossary_file = os.path.join(CKPT_DIR, "_glossary.json")
    quality_file = os.path.join(CKPT_DIR, "_quality.json")
    os.makedirs(CKPT_DIR, exist_ok=True)

    # Load existing quality log
    # 【中文】如果之前跑过，加载已有的质量抽检记录，后面追加不覆盖。
    quality_log = []
    if os.path.exists(quality_file):
        quality_log = json.load(open(quality_file))

    # --- Checkpoint resume ---
    # 【中文】创建翻译代理。book_id 用来隔离不同书的术语库和经验本。
    agent = TranslationAgent(book_id=BOOK_ID)

    # 【中文·断点恢复逻辑】
    # 如果 _checkpoint.json 存在，说明之前跑过但中断了。
    # 读取存档里的 last_idx（上次跑到第几章）、术语库快照、上一章摘要，
    # 然后从下一章开始继续。这就是"游戏存档"机制。
    start = 0
    prev = ""
    if os.path.exists(ckpt_file):
        ckpt = json.load(open(ckpt_file))
        start = ckpt.get("last_idx", -1) + 1
        snapshot = ckpt.get("glossary_snapshot", "{}")
        prev = ckpt.get("previous_summary", "")
        agent.load_glossary_snapshot(snapshot)
        print(f"Resuming from chapter {start + 1}/{len(chapters)}")

    segment_num = 1
    overall_t0 = time.monotonic()

    # Must be initialised outside the loop: when every chapter is already
    # checkpointed the `while` body never runs, and the summary print at the end
    # referenced `glossary` before assignment → UnboundLocalError.
    # 【中文】glossary 必须在循环外初始化，否则如果所有章节都已翻译完，
    # while 循环体一次都不执行，最后打印时 glossary 变量就不存在。
    glossary = agent.exact_store.to_dict()
    # Chapters that failed and were skipped. "ALL DONE" must not be printed as if
    # every chapter was delivered — that is what made the progress report untrustworthy.
    # 【中文】记录失败的章节位置，最后打印时如实报告"不完整"而不是谎称"全部完成"。
    failed_positions: list = []

    # --- Turn off HTTP noise ---
    # 【中文】把 HTTP 客户端的日志级别调到 WARNING，
    # 否则每个 API 请求都会打一堆 DEBUG 信息刷屏。
    import logging
    for name in ["httpx", "httpcore", "openai", "chromadb", "urllib3"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    # 【中文·主循环：按段推进翻译】
    # 每次处理 SEGMENT（15）章，处理完一段就存档。
    # ⚠️ Ctrl+C 安全：按 Ctrl+C 后 Python 会在当前语句结束后抛出
    # KeyboardInterrupt，此时本段可能还没存完档。但因为上一段的存档
    # 是完整的，重启后会从上一段末尾继续，最多损失当前段已跑但未存档的章节。
    while start < len(chapters):
        batch_end = min(start + SEGMENT, len(chapters))
        t0 = time.monotonic()

        for i in range(start, batch_end):
            ch = chapters[i]
            ch_num = ch.index
            pos = i + 1  # 1-based chapter position
            # 【中文】判断当前章节是否是抽检点。
            is_sample = pos in SAMPLE_POINTS

            # At sample points: full pipeline (Pro WRITE + READBACK cold reader)
            # Ordinary chapters: fast mode (Flash WRITE, no READBACK)
            # 【中文】抽检章节走完整流水线（skip_readback=False），
            # 普通章节走快速模式（skip_readback=True，跳过冷读者省钱）。
            try:
                result = agent.translate_chapter(
                    chapter_title=ch.title,
                    chapter_content=ch.content,
                    chapter_number=ch_num,
                    previous_summary=prev,
                    target_lang="en-US",
                    genre="urban",
                    skip_readback=not is_sample,
                )
            except CircuitBreakerOpenError:
                # 【中文】熔断器打开：API 连续失败太多次，自动暂停防雪崩。
                # 等 30 秒让 API 恢复，然后跳过本章继续下一章。
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] CIRCUIT OPEN - sleep 30s")
                failed_positions.append(pos)
                time.sleep(30)
                continue
            except Exception as e:
                # 【中文】其他异常（网络超时、模型返回格式错误等），
                # 记录失败位置，跳过本章继续。不会中断整本书的进度。
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] ERROR: {e}")
                failed_positions.append(pos)
                continue

            tt = result.get("translated_text", "")
            # 【中文】保存本章摘要，传给下一章作为上下文衔接。
            prev = result.get("chapter_summary", "")
            ok_flag = "OK" if len(tt) >= 50 else "EMPTY"

            # --- Quality logging at sample points ---
            # 【中文】如果是抽检章节且有冷读者反馈，记录质量数据。
            fb = result.get("readback_feedback", {})
            if is_sample and fb:
                # 【中文】提取冷读者的判决和评价。
                verdict = fb.get("verdict", "?")
                keep = fb.get("would_keep_reading", "?")
                imp = fb.get("overall_impression", "")[:250]
                q = {
                    "ch_position": pos,
                    "ch_number": ch_num,
                    "verdict": verdict,
                    "would_keep_reading": keep,
                    "output_len": len(tt),
                    "impression": imp,
                    "comprehension_issues": len(fb.get("comprehension_issues", [])),
                    "engagement_gaps": len(fb.get("engagement_gaps", [])),
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                quality_log.append(q)
                # 【中文】每次抽检都立即写入文件，防止中途崩溃丢失所有抽检记录。
                json.dump(quality_log, open(quality_file, "w"), ensure_ascii=False, indent=2)
                model_tag = "[P]"  # Pro
                issues = q["comprehension_issues"] + q["engagement_gaps"]
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] {len(tt)}c {model_tag} 🔍 [{verdict}] keep={keep} issues={issues}")
                if imp:
                    print(f"     {imp}")
            else:
                model_tag = "[F]"  # Flash
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] {len(tt)}c {model_tag} {ok_flag}")

            # Do not write an empty/stub translation into the deliverable: that
            # is how 空章/符号章 ended up mixed into finished books. The old code
            # computed ok_flag, printed "EMPTY", and wrote the chapter anyway.
            # 【中文】译文少于 50 词视为空章/残章，不写入成品文件。
            # 历史教训：以前不管多短都写进去，导致成品里混入了空白章节。
            if len(tt.split()) < 50:
                print(f"  [{pos}/{len(chapters)} Ch{ch_num}] EMPTY/STUB "
                      f"({len(tt.split())}w) — not written")
                failed_positions.append(pos)
                continue

            # ZERO-CHINESE RULE: the deliverable is for English readers, so a
            # Chinese chapter title must never land in it. Use the translated
            # title the pipeline produced and fall back to a plain number, not
            # to ch.title (which is Chinese).
            # 【中文】零中文规则：成品是给英文读者看的，章节标题必须用英文。
            # 优先用流水线翻译出的英文标题，没有就用 "Chapter N" 兜底，
            # 绝不用 ch.title（那是中文原标题）。
            # Block format + zero-Chinese fallback: src/delivery.py, shared with
            # run_novel, the API and Celery so this copy cannot drift again.
            delivery.append_chapter(
                out_file, ch_num, result.get("chapter_title_en", ""), tt,
                header="无限恐怖 — English Translation")

        # --- Checkpoint ---
        # 【中文·段末存档】每跑完一段（15 章），把进度、术语库快照、
        # 上一章摘要写入 _checkpoint.json。下次启动时从这里恢复。
        # ⚠️ Ctrl+C 如果在存档过程中按下，可能导致存档文件不完整。
        # 但因为存档是先写临时内容再覆盖，最坏情况是回到上一段的存档点。
        json.dump({
            "last_idx": batch_end - 1,
            "glossary_snapshot": agent.exact_store.snapshot(),
            "previous_summary": prev,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, open(ckpt_file, "w"))

        # 【中文】同时导出术语表快照，方便人工查看当前积累了哪些术语。
        glossary = agent.exact_store.to_dict()
        json.dump(glossary, open(glossary_file, "w"), ensure_ascii=False, indent=2)

        # 【中文】打印本段进度摘要：耗时、完成百分比、预计剩余时间。
        seg_elapsed = (time.monotonic() - t0) / 60
        pct = 100 * batch_end / len(chapters)
        total_elapsed = (time.monotonic() - overall_t0) / 60
        remaining = total_elapsed / batch_end * (len(chapters) - batch_end)
        sample_count = len(quality_log)
        print(f"  Segment {segment_num}: {start+1}-{batch_end}/{len(chapters)} "
              f"({pct:.0f}%) in {seg_elapsed:.0f}m | {len(glossary)} terms | "
              f"total: {total_elapsed:.0f}m | ETA: {remaining:.0f}m | quality: {sample_count}")
        print()

        start = batch_end
        segment_num += 1
        # 【中文】段间冷却 2 秒，防止请求太密集触发 API 限速。
        if batch_end < len(chapters):
            time.sleep(2)

    # 【中文·全书翻译结束，打印最终总结】
    total_min = (time.monotonic() - overall_t0) / 60
    delivered = len(chapters) - len(failed_positions)
    # Never claim "ALL DONE" when chapters were skipped: that is the exact
    # behaviour that made this project's progress reports untrustworthy
    # (a run that delivered 1 of 2 chapters printed "100%" and "ALL DONE").
    # 【中文】如果有失败章节，如实报告 INCOMPLETE 而不是谎称 ALL DONE。
    # 历史教训：以前不管有没有跳过都打印"ALL DONE"，导致进度报告不可信。
    if failed_positions:
        print(f"INCOMPLETE: {delivered}/{len(chapters)} chapters delivered "
              f"in {total_min:.0f}m — {len(failed_positions)} failed/skipped")
        preview = failed_positions[:20]
        suffix = "..." if len(failed_positions) > 20 else ""
        print(f"  Failed chapter positions: {preview}{suffix}")
    else:
        print(f"ALL DONE: {delivered}/{len(chapters)} chapters in {total_min:.0f}m")
    print(f"Glossary: {len(glossary)} terms | Quality checks: {len(quality_log)}")
    print(f"Output: {out_file}")
    print(f"Quality: {quality_file}")
    # Non-zero exit lets a caller (CI, a wrapper script) detect the gap instead
    # of trusting the printed word "DONE".
    # 【中文】有失败章节时返回退出码 1，让外部调用者（CI/包装脚本）
    # 能通过退出码检测到不完整，而不是靠解析 stdout 文字。
    if failed_positions:
        sys.exit(1)


if __name__ == "__main__":
    main()
