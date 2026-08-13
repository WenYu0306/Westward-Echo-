"""Celery task queue for async translation jobs.

If Celery/Redis are not installed, this module loads as a no-op.
Install: pip install celery[redis]
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
from .prefetch import ChapterPrefetcher
from .script_splitter import split_episodes
from .stats import TranslationStats

logger = logging.getLogger("westward_echo.celery")

try:
    from celery import Celery
    _celery_ok = True
except ImportError:
    _celery_ok = False

if _celery_ok:
    app = Celery("westward_echo", broker=REDIS_URL, backend=REDIS_URL)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Shanghai",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
    )

    @app.task(bind=True, max_retries=2, default_retry_delay=30)
    def translate_novel_task(self, job_id: str, text: str, target_lang: str = "en-US",
                              translate_mode: str = "flash", qa_interval: int = 20,
                              genre: str = "romance_ceo",
                              glossary_preset_glossary: str = "",
                              content_type: str = "novel",
                              script_mode: str = "full"):
        """Main translation Celery task — durable, retryable, checkpointed.

        content_type selects the splitting + prompt branch:
        "novel" (chapter splitter, web-novel prompts) or "script"
        (episode splitter, short-drama prompts).

        script_mode ("full" | "dialogue") applies to the script branch:
        "dialogue" post-filters output down to spoken lines for dubbing.
        """
        progress = TranslationProgress(job_id)
        try:
            if content_type == "script":
                chunks = split_episodes(text)
            else:
                chunks = split_chapters(text)
            translatable = [c for c in chunks if c.action != ParagraphTag.SKIP]
            total = len(translatable)
            agent = TranslationAgent()
            all_translations = []

            # ── Pre-load glossary from preset (warm start) ──
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
            prefetcher = ChapterPrefetcher(agent.exact_store, agent.semantic_store)
            if len(translatable) > 1:
                prefetcher.submit_next(translatable[1].content, target_lang)

            for i, chapter in enumerate(translatable):
                # Check if prefetch already completed for this chapter
                cached = prefetcher.get_if_ready(chapter.content)
                if cached:
                    agent.set_prefetched_glossary(cached[0], cached[1])

                progress.update(i + 1, total, chapter.title)
                try:
                    result = agent.translate_chapter(
                        chapter_title=chapter.title, chapter_content=chapter.content,
                        chapter_number=chapter.index, previous_summary=prev_summary,
                        target_lang=target_lang, genre=genre,
                        content_type=content_type,
                        script_mode=script_mode,
                    )
                except CircuitBreakerOpenError:
                    # Circuit is open for this language — skip remaining chapters
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
                _save_checkpoint(job_id, chapter.index, result["translated_text"],
                                 result.get("glossary_snapshot_json", "{}"), prev_summary)

                # Start prefetching NEXT chapter's glossary while current chapter sleeps
                if i + 1 < len(translatable):
                    prefetcher.submit_next(translatable[i + 1].content, target_lang)

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
            if self.request.retries < self.max_retries:
                raise self.retry(exc=exc)
            backpressure.release()
            raise

    @app.task(bind=True, max_retries=1, default_retry_delay=30)
    def resume_translate_task(self, job_id: str, start_chapter: int, glossary_snapshot: str,
                               text: str = "", target_lang: str = "en-US",
                               translate_mode: str = "flash", qa_interval: int = 20,
                               genre: str = "romance_ceo", content_type: str = "novel",
                               script_mode: str = "full"):
        """Resume a crashed translation from the given starting chapter number.

        Accepts a glossary snapshot (JSON) and a start_chapter index so the
        translation picks up from the last checkpoint without re-processing
        already-translated chapters.  Previously translated chapters are
        loaded from the checkpoint DB for the final merge.
        """
        progress = TranslationProgress(job_id)
        try:
            if content_type == "script":
                chunks = split_episodes(text)
            else:
                chunks = split_chapters(text)
            translatable = [c for c in chunks if c.action != ParagraphTag.SKIP]
            total = len(translatable)
            agent = TranslationAgent()

            # Restore glossary from the checkpoint snapshot
            agent.load_glossary_snapshot(glossary_snapshot)

            # Load previously-translated chapters from checkpoints
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
                    max_idx = max(completed.keys())
                    prev_summary = _load_checkpoint_summary(job_id, max_idx) or ""

            output_path = str(OUTPUT_DIR / f"{job_id}_full_novel_{target_lang}.md")

            # Build the list of chapters still needing translation (chapters >= start_chapter)
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
                    result = agent.translate_chapter(
                        chapter_title=chapter.title, chapter_content=chapter.content,
                        chapter_number=chapter.index, previous_summary=prev_summary,
                        target_lang=target_lang, genre=genre,
                        content_type=content_type,
                        script_mode=script_mode,
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
            if self.request.retries < self.max_retries:
                raise self.retry(exc=exc)
            backpressure.release()
            raise

else:
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
    """
    display = title_en or chapter_title[:60]
    return f"## Chapter {chapter_num}: {display}\n\n{translated_text}\n\n---"


class TranslationProgress:
    """Track job progress in Redis + SQLite JobStore."""
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.key = f"translation:{job_id}"

    def _set(self, data: dict):
        if app and app.backend:
            app.backend.set(self.key, json.dumps(data))

    def update(self, current: int, total: int, chapter_title: str):
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
        self._set({
            "status": "complete", "output_path": output_path, "glossary_count": glossary_count,
        })
        try:
            from .job_store import job_store
            job_store.complete_job(self.job_id, output_path, glossary_count)
        except Exception:
            pass

    def error(self, message: str):
        self._set({"status": "error", "message": message})
        try:
            from .job_store import job_store
            job_store.fail_job(self.job_id, message)
        except Exception:
            pass


def _save_checkpoint(job_id: str, chapter_number: int, translated_text: str,
                     glossary_snapshot: str, previous_summary: str):
    db_path = CHECKPOINT_DB_PATH
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=30) as conn:
        # WAL allows multiple worker processes to read/write concurrently
        # without SQLite "database is locked" errors. Setting WAL races when
        # multiple workers init the same fresh DB at once — ignore a transient
        # lock (WAL is persistent once set).
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS translation_checkpoint (
                job_id TEXT, chapter_number INTEGER, translated_text TEXT,
                glossary_snapshot TEXT, previous_summary TEXT,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (job_id, chapter_number)
            )
        """)
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
    except Exception:
        return {}


def _load_checkpoint_summary(job_id: str, chapter_number: int) -> str:
    """Load the previous_summary field for a specific checkpoint."""
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
    except Exception:
        return ""
