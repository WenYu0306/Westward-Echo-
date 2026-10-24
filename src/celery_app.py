"""Celery task queue for async translation jobs.

If Celery/Redis are not installed, this module loads as a no-op.
Install: pip install celery[redis]

【中文·这个文件是干什么的】
这是翻译任务的"后台调度中心"。翻译一章小说需要调用多次大模型 API，
可能要几分钟甚至十几分钟。如果在网页请求里同步等它完成，浏览器早就超时了。
所以实际做法是：网页收到翻译请求后，把任务丢到后台队列里，
立刻回复"收到了，稍后来取"；后台工人（Celery worker）从队列里取出任务慢慢跑，
跑完了把结果存到文件和数据库里。

【中文·什么是 Celery / 队列】
Celery 是一个 Python 后台任务框架。你可以把它想象成一个"工人调度系统"：
· 你（网页接口）把活儿写成一张工单丢进"待办箱"（Redis 队列）
· 工人（Celery worker）从待办箱里取工单，一件一件做
· 做完了把结果放回"已完成箱"（Redis backend + SQLite）
这样网页不用干等，工人可以并行处理多个任务。

【中文·它防止什么坏事发生】
① 网页请求不会因为翻译太慢而超时断开
② 任务有断点恢复机制——跑到第 500 章崩了，能从第 499 章接着跑
③ 有背压保护——队列满了就拒绝新任务，不会无限堆积
④ 有熔断器——某语言 API 挂了就不继续给它发请求，省钱省时间

【中文·它在整个流程里的位置】
src/api/routes.py 收到翻译请求 → 调 translate_novel_task.delay() 丢进队列
→ Celery worker 取出执行 → 逐章调 TranslationAgent 翻译 → 每章存档
→ 全部完成后合并输出文件。如果中途崩溃，resume_translate_task 从断点续翻。
"""

import json
import logging
import sqlite3
import time
from pathlib import Path

from .agent.graph import TranslationAgent
from .backpressure import backpressure
from .chapter_splitter import ParagraphTag, merge_chapters, split_chapters
from .circuit_breaker import CircuitBreakerOpenError
from .config import (
    CHAPTER_COOLDOWN_SECONDS,
    CHECKPOINT_DB_PATH,
    OUTPUT_DIR,
    REDIS_URL,
)
from .migrations import migrate
from .prefetch import ChapterPrefetcher
from .stats import TranslationStats

logger = logging.getLogger("westward_echo.celery")

# 【中文】尝试导入 Celery。如果没装（比如纯开发环境），_celery_ok=False，
# 下面的任务定义会被跳过，模块加载为空操作。这样项目在没有 Redis 的环境下
# 也能 import 而不报错。
try:
    from celery import Celery
    _celery_ok = True
except ImportError:
    _celery_ok = False

if _celery_ok:
    # 【中文】创建 Celery 应用实例，broker 和 backend 都用 Redis。
    # broker = 消息队列（待办箱），backend = 结果存储（已完成箱）。
    app = Celery("westward_echo", broker=REDIS_URL, backend=REDIS_URL)
    app.conf.update(
        task_serializer="json",          # 任务参数用 JSON 序列化
        accept_content=["json"],         # 只接受 JSON 格式的消息
        result_serializer="json",        # 结果也用 JSON
        timezone="Asia/Shanghai",        # 时区设为上海
        enable_utc=True,                 # 内部用 UTC 存储时间
        task_track_started=True,         # 追踪任务的 started 状态
        task_acks_late=True,             # 任务完成后才确认消费（防丢失）
        task_reject_on_worker_lost=True, # 工人崩溃时把任务退回队列
        worker_prefetch_multiplier=1,    # 每个工人一次只预取 1 个任务（防积压）
    )

    @app.task(bind=True, max_retries=2, default_retry_delay=30)
    def translate_novel_task(self, job_id: str, text: str, target_lang: str = "en-US",
                              translate_mode: str = "flash", qa_interval: int = 20,
                              genre: str = "romance_ceo",
                              glossary_preset_glossary: str = "",
                              content_type: str = "novel",
                              api_key: str = ""):
        """Main translation Celery task — durable, retryable, checkpointed.

        content_type is accepted for forward compatibility; only "novel" is
        a validated path and everything routes through the chapter splitter.

        【中文·主翻译任务】这是整个翻译流水线的入口函数。

        参数：
          · job_id：任务唯一标识符
          · text：小说全文（原始文本）
          · target_lang：目标语言，默认 en-US
          · translate_mode：翻译模式（保留参数，目前未使用）
          · qa_interval：每隔多少章做一次 QA 检查，默认 20
          · genre：小说类型，影响提示词风格，默认 romance_ceo
          · glossary_preset_glossary：预设术语表（JSON 字符串），可选
          · content_type：内容类型，目前只支持 "novel"

        流程：
          ① 把全文拆成章节
          ② 加载预设术语（如果有）
          ③ 逐章翻译，每章完成后存档（checkpoint）
          ④ 全部完成后合并成完整译文 + 导出术语表
          ⑤ 释放背压名额

        ⚠️ 每章翻译都会调用大模型 API，产生真实费用。
        """
        progress = TranslationProgress(job_id)
        try:
            # 【中文】把全文按章节拆分。ParagraphTag.SKIP 标记的是不需要翻译的内容
            # （如作者的话、版权声明等），过滤掉。
            chunks = split_chapters(text)
            translatable = [c for c in chunks if c.action != ParagraphTag.SKIP]
            total = len(translatable)
            agent = TranslationAgent(book_id=job_id, target_lang=target_lang, api_key=api_key)
            all_translations = []

            # ── Pre-load glossary from preset (warm start) ──
            # 【中文】如果提交了预设术语表，先把它加载到术语库里。
            # 这样第一章就能用上已有的术语翻译，不用等大模型自己猜。
            if glossary_preset_glossary and glossary_preset_glossary != "{}":
                try:
                    preset_terms = json.loads(glossary_preset_glossary)
                    for term_cn, term_en in preset_terms.items():
                        agent.exact_store.add(
                            term_cn, term_en, category="culture", target_lang=target_lang
                        )
                    logger.info(
                        "Pre-loaded %d glossary terms from preset for job %s",
                        len(preset_terms), job_id,
                    )
                except (json.JSONDecodeError, Exception) as exc:
                    logger.warning("Failed to pre-load glossary preset for job %s: %s", job_id, exc)
            prev_summary = ""
            output_path = str(OUTPUT_DIR / f"{job_id}_full_novel_{target_lang}.md")

            # ── Chapter prefetch: submit chapter 1's glossary fetch before the loop ──
            # 【中文】术语预取优化：在翻译当前章节的同时，后台提前查下一章的术语。
            # 这样轮到下一章时术语已经准备好了，不用干等。
            prefetcher = ChapterPrefetcher(agent.exact_store, agent.semantic_store)
            if len(translatable) > 1:
                prefetcher.submit_next(translatable[1].content, target_lang)

            for i, chapter in enumerate(translatable):
                # Check if prefetch already completed for this chapter
                # 【中文】检查这一章的术语预取是否已经完成。
                cached = prefetcher.get_if_ready(chapter.content)
                if cached:
                    agent.set_prefetched_glossary(cached[0], cached[1])

                progress.update(i + 1, total, chapter.title)
                try:
                    # 【中文】⚠️ 这里是真正调用大模型翻译的地方，会产生真实费用。
                    result = agent.translate_chapter(
                        chapter_title=chapter.title, chapter_content=chapter.content,
                        chapter_number=chapter.index, previous_summary=prev_summary,
                        target_lang=target_lang, genre=genre,
                        content_type=content_type,
                    )
                except CircuitBreakerOpenError:
                    # Circuit is open for this language — skip remaining chapters
                    # 【中文】该语言的熔断器跳闸了，API 暂时不可用。
                    # 不再继续翻译剩余章节，直接退出循环。
                    # 已翻译的章节不受影响（它们已经存档了）。
                    logger.warning(
                        "Circuit breaker OPEN for language '%s' at chapter %d/%d — "
                        "skipping remaining chapters for this language.",
                        target_lang, chapter.index, total,
                    )
                    TranslationStats.record_chapter_failed(target_lang)
                    break

                all_translations.append(_chapter_md(
                    chapter.index, chapter.title, result["translated_text"],
                    result.get("chapter_title_en", ""),
                ))
                prev_summary = result.get("chapter_summary", "")
                TranslationStats.record_chapter_complete(target_lang)
                # 【中文】每章翻译完立刻存档（checkpoint）。这就是"游戏存档"机制：
                # 崩了能从上次存档点接着玩，不用从头再来。
                _save_checkpoint(job_id, chapter.index, result["translated_text"],
                                 result.get("glossary_snapshot_json", "{}"), prev_summary)

                # Start prefetching NEXT chapter's glossary while current chapter sleeps
                # 【中文】趁当前章节的冷却时间里，提前查下一章的术语。
                if i + 1 < len(translatable):
                    prefetcher.submit_next(translatable[i + 1].content, target_lang)

                # 【中文】章节间冷却，避免太快打满 API 限流。
                # CHAPTER_COOLDOWN_SECONDS 默认 0.5 秒。
                time.sleep(CHAPTER_COOLDOWN_SECONDS)

            prefetcher.shutdown()
            # 【中文】所有章节翻译完成，合并成完整的 Markdown 文件。
            full_text = merge_chapters(all_translations)
            Path(output_path).write_text(full_text, encoding="utf-8")
            # 【中文】导出最终术语表为 JSON 文件。
            glossary = agent.exact_store.to_dict()
            glossary_path = str(OUTPUT_DIR / f"{job_id}_glossary.json")
            Path(glossary_path).write_text(
                json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            progress.complete(output_path, len(glossary))
            # 【中文】释放背压名额，让新任务可以进来。
            backpressure.release()
            return {
                "status": "complete", "output_path": output_path, "total_chapters": total,
                "glossary_count": len(glossary),
            }
        except Exception as exc:
            progress.error(str(exc))
            TranslationStats.record_chapter_failed(target_lang)
            # Keep the backpressure slot held while a retry is pending;
            # release exactly once when no retry remains. (Releasing before
            # self.retry() would double-release when the retry finishes.)
            #
            # 【中文】异常处理 + 自动重试逻辑。
            # ⚠️ 背压名额的释放时机很关键：
            # · 还有重试机会 → 不释放（重试时仍然占着名额）
            # · 重试次数用完 → 才释放
            # 如果在 self.retry() 之前就释放，重试完成后又会释放一次，
            # 导致名额多还了一个，背压计数变成负数或不准。
            if self.request.retries < self.max_retries:
                raise self.retry(exc=exc)
            backpressure.release()
            raise

    @app.task(bind=True, max_retries=1, default_retry_delay=30)
    def resume_translate_task(self, job_id: str, start_chapter: int, glossary_snapshot: str,
                               text: str = "", target_lang: str = "en-US",
                               translate_mode: str = "flash", qa_interval: int = 20,
                               genre: str = "romance_ceo", content_type: str = "novel",
                               api_key: str = ""):
        """Resume a crashed translation from the given starting chapter number.

        Accepts a glossary snapshot (JSON) and a start_chapter index so the
        translation picks up from the last checkpoint without re-processing
        already-translated chapters.  Previously translated chapters are
        loaded from the checkpoint DB for the final merge.

        【中文·断点续翻任务】当翻译中途崩溃后，用这个任务从断点继续。

        参数：
          · start_chapter：从第几章开始续翻
          · glossary_snapshot：上次存档时的术语表快照（JSON 字符串）
          · 其他参数同 translate_novel_task

        流程：
          ① 恢复术语表快照（保证续翻后的术语和前文一致）
          ② 从 checkpoint 数据库加载已翻译的章节
          ③ 从 start_chapter 开始继续翻译
          ④ 合并新旧译文

        ⚠️ 同样会调用大模型 API，产生真实费用。
        """
        progress = TranslationProgress(job_id)
        try:
            chunks = split_chapters(text)
            translatable = [c for c in chunks if c.action != ParagraphTag.SKIP]
            total = len(translatable)
            agent = TranslationAgent(book_id=job_id, target_lang=target_lang, api_key=api_key)

            # Restore glossary from the checkpoint snapshot
            # 【中文】从快照恢复术语表。这一步至关重要：如果不恢复，
            # 续翻后的章节会用全新的术语翻译，和前文不一致，全书译名就乱了。
            agent.load_glossary_snapshot(glossary_snapshot, target_lang=target_lang)

            # Load previously-translated chapters from checkpoints
            # 【中文】从 checkpoint 数据库加载之前已经翻译好的章节。
            all_translations = []
            prev_summary = ""
            if start_chapter > 0:
                completed = _load_checkpoint_translations(job_id)
                # completed is a dict {chapter_index: translated_text}
                for ch in translatable:
                    if ch.index in completed:
                        all_translations.append(_chapter_md(
                            ch.index, ch.title, completed[ch.index],
                        ))
                    elif ch.index >= start_chapter:
                        break
                if completed:
                    # Use the summary from the last completed checkpoint
                    # 【中文】取最后一个已完成章节的摘要，作为续翻的上下文。
                    max_idx = max(completed.keys())
                    prev_summary = _load_checkpoint_summary(job_id, max_idx) or ""

            output_path = str(OUTPUT_DIR / f"{job_id}_full_novel_{target_lang}.md")

            # Build the list of chapters still needing translation (chapters >= start_chapter)
            # 【中文】筛选出还需要翻译的章节（>= start_chapter 的）。
            remaining = [c for c in translatable if c.index >= start_chapter]
            # ── Chapter prefetch ──
            prefetcher = ChapterPrefetcher(agent.exact_store, agent.semantic_store)
            if len(remaining) > 1:
                prefetcher.submit_next(remaining[1].content, target_lang)

            for i, chapter in enumerate(translatable):
                if chapter.index < start_chapter:
                    continue  # Skip already-translated chapters
                progress.update(i + 1, total, chapter.title)

                # Check if prefetch already completed for this chapter
                cached = prefetcher.get_if_ready(chapter.content)
                if cached:
                    agent.set_prefetched_glossary(cached[0], cached[1])

                try:
                    # 【中文】⚠️ 调用大模型翻译，会产生真实费用。
                    result = agent.translate_chapter(
                        chapter_title=chapter.title, chapter_content=chapter.content,
                        chapter_number=chapter.index, previous_summary=prev_summary,
                        target_lang=target_lang, genre=genre,
                        content_type=content_type,
                    )
                except CircuitBreakerOpenError:
                    logger.warning(
                        "Circuit breaker OPEN for language '%s' at chapter %d — "
                        "skipping remaining chapters for this language.",
                        target_lang, chapter.index,
                    )
                    TranslationStats.record_chapter_failed(target_lang)
                    break

                all_translations.append(_chapter_md(
                    chapter.index, chapter.title, result["translated_text"],
                    result.get("chapter_title_en", ""),
                ))
                prev_summary = result.get("chapter_summary", "")
                TranslationStats.record_chapter_complete(target_lang)
                _save_checkpoint(job_id, chapter.index, result["translated_text"],
                                 result.get("glossary_snapshot_json", "{}"), prev_summary)

                # Start prefetching NEXT chapter's glossary
                # Find the next chapter in remaining list
                # 【中文】在 remaining 列表里找到当前章节的下一个，提交预取。
                for j, rch in enumerate(remaining):
                    if rch.index == chapter.index and j + 1 < len(remaining):
                        prefetcher.submit_next(remaining[j + 1].content, target_lang)
                        break

                time.sleep(CHAPTER_COOLDOWN_SECONDS)

            prefetcher.shutdown()
            full_text = merge_chapters(all_translations)
            Path(output_path).write_text(full_text, encoding="utf-8")
            glossary = agent.exact_store.to_dict()
            glossary_path = str(OUTPUT_DIR / f"{job_id}_glossary.json")
            Path(glossary_path).write_text(
                json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            progress.complete(output_path, len(glossary))
            backpressure.release()
            return {
                "status": "complete", "output_path": output_path, "total_chapters": total,
                "glossary_count": len(glossary),
            }
        except Exception as exc:
            progress.error(str(exc))
            TranslationStats.record_chapter_failed(target_lang)
            # Keep the backpressure slot held while a retry is pending;
            # release exactly once when no retry remains. (Releasing before
            # self.retry() would double-release when the retry finishes.)
            #
            # 【中文】同 translate_novel_task 的重试逻辑：
            # 还有重试机会就不释放背压名额，用完了才释放。
            if self.request.retries < self.max_retries:
                raise self.retry(exc=exc)
            backpressure.release()
            raise

else:
    # 【中文】Celery 没装时的占位值。外部代码 import 这些名字不会报错，
    # 但值是 None，调用前需要检查。
    app = None  # type: ignore
    translate_novel_task = None  # type: ignore
    resume_translate_task = None  # type: ignore


def _chapter_md(chapter_num: int, chapter_title: str, translated_text: str,
                title_en: str = "") -> str:
    """Format one chapter as a Markdown block with a parseable header.

    The EPUB endpoint (_parse_markdown_chapters in routes.py) requires
    '## Chapter N:' headers — merging raw translations without headers
    makes EPUB generation return 422 for every Celery job. Format matches
    the sync path in routes.py.

    【中文】把一章译文格式化为 Markdown 块。
    标题格式必须是 ``## Chapter N: xxx``，因为 EPUB 生成接口靠这个格式
    来识别章节边界。没有这个标题头，EPUB 生成会返回 422 错误。
    display 优先用英文标题，没有就用中文标题的前 60 个字符。
    """
    display = title_en or chapter_title[:60]
    return f"## Chapter {chapter_num}: {display}\n\n{translated_text}\n\n---"


class TranslationProgress:
    """Track job progress in Redis + SQLite JobStore.

    【中文·这个类做什么】
    追踪翻译任务的进度，同时写入两个地方：
    ① Redis（通过 Celery backend）—— 供实时查询
    ② SQLite JobStore —— 持久化存储，Redis 丢了也能查
    每次更新都写两份，任一失败不影响另一份。
    """
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.key = f"translation:{job_id}"

    def _set(self, data: dict):
        """【中文】把进度数据写入 Redis。app 不存在时静默跳过。"""
        if app and app.backend:
            app.backend.set(self.key, json.dumps(data))

    def update(self, current: int, total: int, chapter_title: str):
        """【中文】更新进度：当前第几章 / 共几章 / 章节标题。"""
        self._set({
            "status": "translating", "current": current, "total": total,
            "chapter_title": chapter_title,
        })
        try:
            from .job_store import job_store
            job_store.update_progress(self.job_id, current, total, chapter_title)
        except Exception:
            pass  # non-critical

    def complete(self, output_path: str, glossary_count: int):
        """【中文】标记任务完成，记录输出路径和术语数量。"""
        self._set({
            "status": "complete", "output_path": output_path, "glossary_count": glossary_count,
        })
        try:
            from .job_store import job_store
            job_store.complete_job(self.job_id, output_path, glossary_count)
        except Exception:
            pass

    def error(self, message: str):
        """【中文】标记任务出错，记录错误信息。"""
        self._set({"status": "error", "message": message})
        try:
            from .job_store import job_store
            job_store.fail_job(self.job_id, message)
        except Exception:
            pass


# 【中文】checkpoints.db 里的 translation_checkpoint 表由本模块（celery_app）
# 所有。它和 exact_store 的 exact_glossary 表共用一个 checkpoints.db 文件，
# 迁移框架用命名迁移让两个 owner 互不冲突。
_CREATE_CHECKPOINT = """
CREATE TABLE IF NOT EXISTS translation_checkpoint (
    job_id TEXT, chapter_number INTEGER, translated_text TEXT,
    glossary_snapshot TEXT, previous_summary TEXT,
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (job_id, chapter_number)
)
"""

_CHECKPOINT_MIGRATIONS = [
    ("create_translation_checkpoint", lambda conn: conn.executescript(_CREATE_CHECKPOINT)),
]


def _save_checkpoint(job_id: str, chapter_number: int, translated_text: str,
                     glossary_snapshot: str, previous_summary: str):
    """【中文】保存一章的翻译存档（checkpoint）。

    这就是"游戏存档"的实现：每翻完一章就把译文、术语表快照、章节摘要
    存到 SQLite 里。崩了之后 resume_translate_task 从这里读取，接着翻。

    参数：
      · job_id：任务 ID
      · chapter_number：章节号
      · translated_text：译文
      · glossary_snapshot：术语表快照（JSON 字符串）
      · previous_summary：章节摘要（给下一章做上下文用）

    WAL 模式允许多个工人并发写入。设置 WAL 时如果遇到锁竞争就忽略
    （WAL 一旦设过就是持久的，重复设置只是保险措施）。
    """
    db_path = CHECKPOINT_DB_PATH
    # 【中文】建表交给迁移框架（内部处理 WAL、目录创建、并发安全、迁移记录），
    # 这里只做写入。migrate 是幂等的：已跑过的迁移直接跳过。
    migrate(db_path, _CHECKPOINT_MIGRATIONS)
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO translation_checkpoint "
            "(job_id, chapter_number, translated_text, glossary_snapshot, previous_summary) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, chapter_number, translated_text, glossary_snapshot, previous_summary),
        )
        conn.commit()


def _load_checkpoint_translations(job_id: str) -> dict[int, str]:
    """Load all previously translated chapter texts from the checkpoint DB.

    Returns a dict mapping chapter_number -> translated_text.

    【中文】从 checkpoint 数据库加载某个任务所有已翻译的章节。
    返回 {章节号: 译文} 字典。数据库不存在或读取出错时返回空字典。
    """
    db_path = CHECKPOINT_DB_PATH
    if not Path(db_path).exists():
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT chapter_number, translated_text FROM translation_checkpoint "
                "WHERE job_id = ? ORDER BY chapter_number ASC",
                (job_id,),
            ).fetchall()
        return {row["chapter_number"]: row["translated_text"] for row in rows}
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return {}  # 全新库，尚无存档表 → 合法的「无存档」
        logger.error("Checkpoint read failed for job %s: %s", job_id, exc)
        raise
    except Exception:
        logger.exception("Checkpoint read failed for job %s", job_id)
        raise


def _load_checkpoint_summary(job_id: str, chapter_number: int) -> str:
    """Load the previous_summary field for a specific checkpoint.

    【中文】加载某一章存档中的"章节摘要"字段。
    用于断点续翻时恢复上下文——续翻的第一章需要知道上一章讲了什么。
    """
    db_path = CHECKPOINT_DB_PATH
    if not Path(db_path).exists():
        return ""
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT previous_summary FROM translation_checkpoint "
                "WHERE job_id = ? AND chapter_number = ?",
                (job_id, chapter_number),
            ).fetchone()
        return row[0] if row else ""
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return ""  # 全新库，尚无存档表 → 合法的「无存档」
        logger.error("Checkpoint summary read failed for job %s: %s", job_id, exc)
        raise
    except Exception:
        logger.exception("Checkpoint summary read failed for job %s", job_id)
        raise
