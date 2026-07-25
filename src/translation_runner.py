"""Shared translation runner — a single chapter-by-chapter loop used by
both the Celery task and the synchronous fallback path.

Before v0.15.1 the loop was copy-pasted into two files (celery_app.py and
routes.py) with different error handling, progress tracking, and prefetch
support.  This module provides one loop so every path gets the same
behaviour.
"""

import logging
from pathlib import Path
from typing import Callable, Optional

from .agent.graph import TranslationAgent
from .chapter_splitter import merge_chapters
from .circuit_breaker import CircuitBreakerOpenError
from .config import CHAPTER_COOLDOWN_SECONDS, OUTPUT_DIR
from .prefetch import ChapterPrefetcher
from .stats import TranslationStats

logger = logging.getLogger(__name__)


class ProgressCallback:
    """Optional hook so the caller can update UI / job-store progress."""

    def on_progress(self, current: int, total: int, chapter_title: str):  # pragma: no cover
        pass

    def on_complete(self, output_path: str, glossary_count: int):  # pragma: no cover
        pass

    def on_error(self, message: str):  # pragma: no cover
        pass


def run_translation(
    *,
    chapters: list,
    agent: TranslationAgent,
    target_lang: str,
    genre: str,
    job_id: str,
    prev_summary: str = "",
    skip_readback: bool = False,
    use_flash_writer: bool = False,
    progress: Optional[ProgressCallback] = None,
    use_prefetch: bool = True,
    cooldown: float = CHAPTER_COOLDOWN_SECONDS,
    existing_translations: Optional[list[str]] = None,
    start_from_index: int = 1,
) -> dict:
    """Translate ``chapters`` one-by-one and return the merged result.

    Parameters
    ----------
    chapters:
        List of ``Chapter`` dataclass instances (from chapter_splitter).
    agent:
        Pre-configured ``TranslationAgent`` instance.
    target_lang, genre:
        Passed through to ``agent.translate_chapter``.
    job_id:
        Opaque id for progress / output file naming.
    prev_summary:
        Summary from the previous chapter (for continuity).
    skip_readback, use_flash_writer:
        Fast-mode flags forwarded to every chapter.
    progress:
        Optional callback for progress events.
    use_prefetch:
        Whether to run the background glossary prefetcher.
    cooldown:
        Seconds to sleep between chapters (rate-limit buffer).
    existing_translations:
        When resuming, already-translated chapter texts.
    start_from_index:
        1-based chapter index of the *first* un-translated chapter.

    Returns
    -------
    ``{"output_path": str, "all_translations": list[str],
       "glossary_count": int, "prev_summary": str}``
    """
    import time as _time

    total = len(chapters)
    all_translations = list(existing_translations or [])
    _prev = prev_summary

    # ── Chapter prefetch: submit chapter 2's glossary before the loop ──
    prefetcher = None
    if use_prefetch and total > 1:
        prefetcher = ChapterPrefetcher(agent.exact_store, agent.semantic_store)
        try:
            prefetcher.submit_next(chapters[1].content, target_lang)
        except Exception:
            logger.debug("Prefetch init failed — continuing without prefetch")
            prefetcher = None

    for i, ch in enumerate(chapters):
        ch_num = ch.index

        # Check if prefetch already completed for this chapter
        if prefetcher is not None:
            cached = prefetcher.get_if_ready(ch.content)
            if cached:
                agent.set_prefetched_glossary(cached[0], cached[1])

        if progress is not None:
            progress.on_progress(i + 1, total, ch.title)

        try:
            result = agent.translate_chapter(
                chapter_title=ch.title,
                chapter_content=ch.content,
                chapter_number=ch_num,
                previous_summary=_prev,
                target_lang=target_lang,
                genre=genre,
                skip_readback=skip_readback,
                use_flash_writer=use_flash_writer,
            )
        except CircuitBreakerOpenError:
            logger.warning(
                "Circuit breaker OPEN for '%s' at chapter %d — skipping remaining.",
                target_lang, ch_num,
            )
            TranslationStats.record_chapter_failed(target_lang)
            break
        except Exception:
            logger.exception("Chapter %d failed — skipping.", ch_num)
            TranslationStats.record_chapter_failed(target_lang)
            continue

        all_translations.append(result["translated_text"])
        _prev = result.get("chapter_summary", "")
        TranslationStats.record_chapter_complete(target_lang)

        # Start prefetching the NEXT chapter's glossary
        if prefetcher is not None and i + 1 < total:
            try:
                prefetcher.submit_next(chapters[i + 1].content, target_lang)
            except Exception:
                pass

        if cooldown > 0:
            _time.sleep(cooldown)

    if prefetcher is not None:
        try:
            prefetcher.shutdown()
        except Exception:
            pass

    # ── Merge & persist ───────────────────────────────────────────
    output_path = str(OUTPUT_DIR / f"{job_id}_full_novel_{target_lang}.md")
    full_text = merge_chapters(all_translations)
    Path(output_path).write_text(full_text, encoding="utf-8")

    import json as _json
    glossary = agent.exact_store.to_dict()
    glossary_path = str(OUTPUT_DIR / f"{job_id}_glossary.json")
    Path(glossary_path).write_text(
        _json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    if progress is not None:
        progress.on_complete(output_path, len(glossary))

    return {
        "output_path": output_path,
        "all_translations": all_translations,
        "glossary_count": len(glossary),
        "prev_summary": _prev,
    }
