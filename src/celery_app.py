"""Celery task queue for async translation jobs.

If Celery/Redis are not installed, this module loads as a no-op.
Install: pip install celery[redis]
"""

import json
import time
import sqlite3
from pathlib import Path

try:
    from celery import Celery
    _celery_ok = True
except ImportError:
    _celery_ok = False

from .config import (
    REDIS_URL,
    CHECKPOINT_DB_PATH,
    CHAPTER_COOLDOWN_SECONDS,
    OUTPUT_DIR,
)
from .chapter_splitter import split_chapters, merge_chapters, ParagraphTag
from .agent.graph import TranslationAgent


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
                              genre: str = "romance_ceo"):
        """Main translation Celery task — durable, retryable, checkpointed."""
        progress = TranslationProgress(job_id)
        try:
            chapters = split_chapters(text)
            translatable = [c for c in chapters if c.action != ParagraphTag.SKIP]
            total = len(translatable)
            agent = TranslationAgent()
            all_translations = []
            prev_summary = ""
            output_path = str(OUTPUT_DIR / f"{job_id}_full_novel_{target_lang}.md")

            for i, chapter in enumerate(translatable):
                progress.update(i + 1, total, chapter.title)
                result = agent.translate_chapter(
                    chapter_title=chapter.title, chapter_content=chapter.content,
                    chapter_number=chapter.index, previous_summary=prev_summary,
                    target_lang=target_lang, genre=genre,
                )
                all_translations.append(result["translated_text"])
                prev_summary = result.get("chapter_summary", "")
                _save_checkpoint(job_id, chapter.index, result["translated_text"],
                                 result.get("glossary_snapshot_json", "{}"), prev_summary)
                time.sleep(CHAPTER_COOLDOWN_SECONDS)

            full_text = merge_chapters(all_translations)
            Path(output_path).write_text(full_text, encoding="utf-8")
            glossary = agent.exact_store.to_dict()
            glossary_path = str(OUTPUT_DIR / f"{job_id}_glossary.json")
            Path(glossary_path).write_text(json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")
            progress.complete(output_path, len(glossary))
            return {"status": "complete", "output_path": output_path, "total_chapters": total, "glossary_count": len(glossary)}
        except Exception as exc:
            progress.error(str(exc))
            raise self.retry(exc=exc)

else:
    app = None  # type: ignore
    translate_novel_task = None  # type: ignore


class TranslationProgress:
    """Track job progress in Redis + SQLite JobStore."""
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.key = f"translation:{job_id}"

    def _set(self, data: dict):
        if app and app.backend:
            app.backend.set(self.key, json.dumps(data))

    def update(self, current: int, total: int, chapter_title: str):
        self._set({"status": "translating", "current": current, "total": total, "chapter_title": chapter_title})
        try:
            from .job_store import job_store
            job_store.update_progress(self.job_id, current, total, chapter_title)
        except Exception:
            pass  # non-critical

    def complete(self, output_path: str, glossary_count: int):
        self._set({"status": "complete", "output_path": output_path, "glossary_count": glossary_count})
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
    with sqlite3.connect(db_path) as conn:
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
