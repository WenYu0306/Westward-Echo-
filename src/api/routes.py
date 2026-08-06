"""API routes — Celery-backed translation (mounted at /api)."""

import json
import os
import re
import sqlite3
import asyncio
import threading
import time as _time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse

from ..config import OUTPUT_DIR, CHECKPOINT_DB_PATH, MAX_UPLOAD_SIZE_BYTES, MAX_UPLOAD_SIZE_MB, VERSION
from ..chapter_splitter import split_chapters, ParagraphTag
from ..job_store import job_store
from ..backpressure import backpressure
from ..stats import TranslationStats
from ..api.logging import logger

try:
    from ..celery_app import translate_novel_task, resume_translate_task
    _has_celery = True
    if translate_novel_task is None:
        _has_celery = False
except Exception:
    _has_celery = False

app = FastAPI(title="Westward Echo API", version=VERSION)

# ── Security ───────────────────────────────────────────────────
_VALID_JOB_ID = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')
_KNOWN_LANGS = frozenset({"en-US", "es-ES", "de", "fr"})
_KNOWN_GENRES = frozenset({"romance_ceo", "xianxia", "urban", "scifi", "folk_religion"})

def _safe_job_id(job_id: str) -> str:
    """Reject job IDs containing path traversal or illegal characters."""
    if not _VALID_JOB_ID.match(job_id):
        from fastapi import HTTPException as _HTTPE, status as _S
        raise _HTTPE(status_code=_S.HTTP_400_BAD_REQUEST, detail="Invalid job_id")
    return job_id


# ═══════════════════════════════════════════════════════════════
# File upload validation
# ═══════════════════════════════════════════════════════════════

async def _validate_novel_upload(file: UploadFile):
    """Validate and auto-convert uploaded novel file to plain UTF-8 text.

    Supports: .txt (UTF-8/GBK/GB2312), .rtf (macOS TextEdit), .docx (Word),
              .md (Markdown), .epub (extracted text).
    Returns ``(text, error_message)`` — exactly one will be non-None.
    """
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        return None, f"File too large. Maximum {MAX_UPLOAD_SIZE_MB}MB."

    filename = (file.filename or "").lower()
    text = None

    # ── RTF: macOS TextEdit default format ──
    if filename.endswith(".rtf") or content[:5] == b"{\\rtf":
        text = _convert_rtf(content)

    # ── DOCX: Word documents ──
    elif filename.endswith(".docx") or (content[:2] == b"PK" and b"word/document" in content[:2048]):
        text = _convert_docx(content)

    # ── EPUB: extract plain text from packaged novel ──
    elif filename.endswith(".epub"):
        text = _convert_epub(content)

    # ── Markdown: pass through ──
    elif filename.endswith(".md") or filename.endswith(".markdown"):
        for enc in ["utf-8", "gb18030", "gbk"]:
            try:
                text = content.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue

    # ── Plain text: auto-detect encoding ──
    else:
        for enc in ["utf-8", "gb18030", "gbk", "gb2312"]:
            try:
                text = content.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue

    if not text:
        return None, "Unable to decode file. Supported: .txt, .rtf, .docx, .md, .epub"
    if not re.search(r'[一-鿿]', text[:10000]):
        return None, "File does not appear to contain Chinese text."
    return text, None


@app.get("/health")
def health():
    """Full subsystem health report. Always available (no auth, no rate limit)."""
    from ..health import HealthChecker
    return HealthChecker().check_all()


@app.post("/translate")
async def translate_novel(
    file: UploadFile = File(...),
    target_lang: str = Form("en-US"),
    translate_mode: str = Form("flash"),
    qa_interval: int = Form(20),
    genre: str = Form("romance_ceo"),
    content_type: str = Form("novel"),
    glossary_preset: str = Form(""),
    api_key: str = Form(""),
):
    """Submit a novel for translation. Returns job_id immediately.

    If ``glossary_preset`` is provided, the named preset's glossary is
    pre-loaded into the translation agent before the first chapter, giving
    the translation a warm start with known terminology.
    """
    # ── Backpressure: reject new work if queue is full ──
    if not backpressure.try_accept():
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_overloaded",
                "message": "Too many translations in progress. Try again later.",
                "queue_depth": backpressure.queue_depth,
            },
            headers={"Retry-After": "30"},
        )

    # ── File validation ──
    text, error = await _validate_novel_upload(file)
    if error:
        status_code = 413 if "too large" in error.lower() else 400
        return JSONResponse(status_code=status_code, content={"error": error})

    # ── API key validation (optional: user's key overrides env) ──
    if api_key and not api_key.startswith("sk-"):
        return JSONResponse(
            status_code=400,
            content={"error": "无效的 API Key 格式（应以 sk- 开头）"},
        )
    if not api_key:
        from ..config import DEEPSEEK_API_KEY as _dk
        if not _dk:
            return JSONResponse(
                status_code=400,
                content={"error": "请提供 DeepSeek API Key，或配置服务器环境变量"},
            )
        api_key = _dk

    chapters = split_chapters(text)
    total = len([c for c in chapters if c.action != ParagraphTag.SKIP])

    # Create persistent job record
    filename = file.filename or "unknown.txt"
    job_id = job_store.create_job(filename, target_lang, total, content_type=content_type)

    # ── Pre-load glossary preset if requested ──
    preset_glossary_json = ""
    if glossary_preset:
        preset_glossary = job_store.load_glossary_preset(glossary_preset)
        if preset_glossary:
            preset_glossary_json = json.dumps(preset_glossary, ensure_ascii=False)

    if _has_celery:
        task = translate_novel_task.delay(
            job_id=job_id, text=text, target_lang=target_lang,
            translate_mode=translate_mode, qa_interval=qa_interval,
            genre=genre,
            glossary_preset_glossary=preset_glossary_json,
        )
        return {"job_id": job_id, "task_id": task.id, "total_chapters": total, "status": "queued"}

    # Celery not available — run synchronously in background
    from ..agent.graph import TranslationAgent
    from ..prefetch import ChapterPrefetcher
    from ..circuit_breaker import CircuitBreakerOpenError

    def _run_sync():
        import traceback as _tb
        try:
            chapters_list = [c for c in chapters if c.action != ParagraphTag.SKIP]
            agent = TranslationAgent(api_key=api_key)
            if preset_glossary_json:
                try:
                    preset_terms = json.loads(preset_glossary_json)
                    for cn, en in preset_terms.items():
                        agent.exact_store.add(cn, en, category="culture", target_lang=target_lang)
                except Exception:
                    pass
            prev_summary = ""
            flash_mode = translate_mode == "flash"
            output_path = str(OUTPUT_DIR / f"{job_id}_full_novel_{target_lang}.md")
            ckpt_path = str(OUTPUT_DIR / f"{job_id}_checkpoint.json")

            # ── Resume from checkpoint if available ──
            start_i = 0
            if os.path.exists(ckpt_path):
                try:
                    ckpt = json.loads(Path(ckpt_path).read_text("utf-8"))
                    start_i = ckpt.get("last_idx", -1) + 1
                    prev_summary = ckpt.get("previous_summary", "")
                    if ckpt.get("glossary_snapshot"):
                        agent.load_glossary_snapshot(ckpt["glossary_snapshot"])
                    logger.info("Web sync: resuming %s from chapter %d/%d", job_id, start_i + 1, len(chapters_list))
                except Exception:
                    start_i = 0

            import time as _t
            for i in range(start_i, len(chapters_list)):
                ch = chapters_list[i]
                try:
                    result = agent.translate_chapter(
                        chapter_title=ch.title, chapter_content=ch.content,
                        chapter_number=ch.index, previous_summary=prev_summary,
                        target_lang=target_lang, genre=genre,
                        skip_readback=flash_mode,
                        use_flash_writer=flash_mode,
                    )
                except CircuitBreakerOpenError:
                    job_store.fail_job(job_id, "Circuit breaker opened")
                    return
                except Exception as exc:
                    err_msg = str(exc).lower()
                    if "timed out" in err_msg or "timeout" in err_msg:
                        logger.warning("Sync chapter %d timed out — retrying", ch.index)
                        _t.sleep(3)
                        try:
                            result = agent.translate_chapter(
                                chapter_title=ch.title, chapter_content=ch.content,
                                chapter_number=ch.index, previous_summary=prev_summary,
                                target_lang=target_lang, genre=genre,
                                skip_readback=flash_mode,
                                use_flash_writer=flash_mode,
                            )
                        except Exception as e2:
                            logger.warning("Sync chapter %d failed after retry: %s", ch.index, e2)
                            continue
                    else:
                        logger.warning("Sync chapter %d failed: %s", ch.index, exc)
                        continue
                tt = result.get("translated_text", "")
                title_en = result.get("chapter_title_en", "")
                prev_summary = result.get("chapter_summary", "")
                word_count = len(tt.split())
                src_chars = len(ch.content.replace("\n", "").replace(" ", ""))
                min_words = max(50, src_chars / 10)
                if word_count < min_words:
                    logger.warning("Sync chapter %d: TRUNCATED (%dw < %.0fw min) — skipping write", ch.index, word_count, min_words)
                    continue
                job_store.update_progress(job_id, i + 1, len(chapters_list), ch.title)
                display_title = title_en or ch.title[:60]
                exists = os.path.exists(output_path)
                with open(output_path, "a" if exists else "w", encoding="utf-8") as f:
                    if not exists:
                        f.write(f"# {job_id} — English Translation\n\n")
                    f.write(f"## Chapter {ch.index}: {display_title}\n\n{tt}\n\n---\n\n")
                    f.flush()
                    os.fsync(f.fileno())
                # ── Save checkpoint after every chapter ──
                json.dump({
                    "last_idx": i,
                    "glossary_snapshot": agent.exact_store.snapshot(),
                    "previous_summary": prev_summary,
                }, Path(ckpt_path).open("w"), ensure_ascii=False)
            glossary_snapshot = json.dumps(agent.exact_store.to_dict(), ensure_ascii=False)
            glossary_path = str(OUTPUT_DIR / f"{job_id}_glossary.json")
            Path(glossary_path).write_text(glossary_snapshot, encoding="utf-8")
            job_store.complete_job(job_id, output_path, len(agent.exact_store))
            # Log session cost estimate
            try:
                snap = TranslationStats.token_snapshot()
                logger.info(
                    "Job %s completed. Tokens: %s total, ~$%.4f estimated cost.",
                    job_id, f"{snap['total']:,}", snap.get("estimated_cost_usd", 0),
                )
            except Exception:
                pass
        except Exception as e:
            logger.error("Sync translation failed for job %s: %s\n%s", job_id, e, _tb.format_exc())
            job_store.fail_job(job_id, str(e))
        finally:
            backpressure.release()

    threading.Thread(target=_run_sync, daemon=True).start()
    job_store.update_progress(job_id, 0, total, "Starting...")
    return {"job_id": job_id, "total_chapters": total, "status": "translating"}


@app.post("/translate/multi")
async def translate_multi(
    file: UploadFile = File(...),
    target_langs: str = Form("en-US,es-ES,de,fr"),
    translate_mode: str = Form("flash"),
    genre: str = Form("romance_ceo"),
    content_type: str = Form("novel"),
    qa_interval: int = Form(20),
    glossary_preset: str = Form(""),
    api_key: str = Form(""),
) -> dict:
    """Start translation into multiple languages simultaneously.

    Creates a project, then one job per language. All language variants
    share the same project_id so they can be grouped in the UI.

    Returns ``{project_id, filename, jobs: [{lang, job_id, status}]}``.
    """
    # ── Backpressure: reject new work if queue is full ──
    if not backpressure.try_accept():
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_overloaded",
                "message": "Too many translations in progress. Try again later.",
                "queue_depth": backpressure.queue_depth,
            },
            headers={"Retry-After": "30"},
        )

    # ── Map content_type → default genre ──
    if content_type != "novel" and genre == "romance_ceo":
        genre = {"script": "urban", "game": "scifi"}.get(content_type, genre)

    # ── File validation ──
    text, error = await _validate_novel_upload(file)
    if error:
        status_code = 413 if "too large" in error.lower() else 400
        return JSONResponse(status_code=status_code, content={"error": error})

    chapters = split_chapters(text)
    total = len([c for c in chapters if c.action != ParagraphTag.SKIP])
    filename = file.filename or "unknown.txt"

    # Create a project to group all language variants
    project_id = job_store.create_project(filename)

    # ── Pre-load glossary preset if requested ──
    preset_glossary_json = ""
    if glossary_preset:
        preset_glossary = job_store.load_glossary_preset(glossary_preset)
        if preset_glossary:
            preset_glossary_json = json.dumps(preset_glossary, ensure_ascii=False)

    langs = [lang.strip() for lang in target_langs.split(",") if lang.strip()]
    results = []

    for lang in langs:
        job_id = job_store.add_language_job(project_id, lang, filename, total, content_type=content_type)

        if _has_celery:
            task = translate_novel_task.delay(
                job_id=job_id, text=text, target_lang=lang,
                translate_mode=translate_mode, qa_interval=qa_interval,
                genre=genre,
                glossary_preset_glossary=preset_glossary_json,
            )
            results.append({"lang": lang, "job_id": job_id, "task_id": task.id})
        else:
            # Fallback: run in a background thread (no task_id)
            results.append({"lang": lang, "job_id": job_id})

    # If Celery is not available, spawn background threads
    if not _has_celery:
        def _run_translation(lang: str, jid: str):
            """Run translation synchronously in a background thread."""
            import asyncio as _asyncio
            from ..agent.graph import TranslationAgent
            from ..prefetch import ChapterPrefetcher
            from ..circuit_breaker import CircuitBreakerOpenError

            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)
            try:
                chapters_list = [c for c in chapters if c.action != ParagraphTag.SKIP]
                agent = TranslationAgent(api_key=api_key)
                prev_summary = ""

                if preset_glossary_json:
                    try:
                        preset_terms = json.loads(preset_glossary_json)
                        for term_cn, term_en in preset_terms.items():
                            agent.exact_store.add(term_cn, term_en, category="culture", target_lang=lang)
                    except (json.JSONDecodeError, Exception):
                        pass
                output_path = str(OUTPUT_DIR / f"{jid}_full_novel_{lang}.md")
                ckpt_path = str(OUTPUT_DIR / f"{jid}_checkpoint.json")

                # ── Resume from checkpoint if available ──
                start_i = 0
                if os.path.exists(ckpt_path):
                    try:
                        ckpt = json.loads(Path(ckpt_path).read_text("utf-8"))
                        start_i = ckpt.get("last_idx", -1) + 1
                        prev_summary = ckpt.get("previous_summary", "")
                        if ckpt.get("glossary_snapshot"):
                            agent.load_glossary_snapshot(ckpt["glossary_snapshot"])
                        logger.info("Multi-lang sync: resuming %s from chapter %d/%d", jid, start_i + 1, len(chapters_list))
                    except Exception:
                        start_i = 0

                prefetcher = ChapterPrefetcher(agent.exact_store, agent.semantic_store)
                if len(chapters_list) > 1:
                    try:
                        prefetcher.submit_next(chapters_list[1].content, lang)
                    except Exception:
                        pass

                flash_mode = translate_mode == "flash"
                for i in range(start_i, len(chapters_list)):
                    ch = chapters_list[i]
                    try:
                        result = agent.translate_chapter(
                            chapter_title=ch.title,
                            chapter_content=ch.content,
                            chapter_number=ch.index,
                            previous_summary=prev_summary,
                            target_lang=lang,
                            genre=genre,
                            skip_readback=flash_mode,
                            use_flash_writer=flash_mode,
                        )
                    except CircuitBreakerOpenError:
                        TranslationStats.record_chapter_failed(lang)
                        break
                    except Exception as exc:
                        err_msg = str(exc).lower()
                        if "timed out" in err_msg or "timeout" in err_msg:
                            logger.warning("Multi-lang chapter %d timed out — retrying", ch.index)
                            _time.sleep(3)
                            try:
                                result = agent.translate_chapter(
                                    chapter_title=ch.title,
                                    chapter_content=ch.content,
                                    chapter_number=ch.index,
                                    previous_summary=prev_summary,
                                    target_lang=lang,
                                    genre=genre,
                                    skip_readback=flash_mode,
                                    use_flash_writer=flash_mode,
                                )
                            except Exception as e2:
                                logger.warning("Multi-lang chapter %d failed after retry: %s", ch.index, e2)
                                TranslationStats.record_chapter_failed(lang)
                                continue
                        else:
                            logger.warning("Multi-lang chapter %d failed: %s", ch.index, exc)
                            TranslationStats.record_chapter_failed(lang)
                            continue
                    tt = result.get("translated_text", "")
                    title_en = result.get("chapter_title_en", "")
                    prev_summary = result.get("chapter_summary", "")
                    word_count = len(tt.split())
                    src_chars = len(ch.content.replace("\n", "").replace(" ", ""))
                    min_words = max(50, src_chars / 10)
                    if word_count < min_words:
                        logger.warning("Multi-lang chapter %d: TRUNCATED (%dw < %.0fw min) — skipping write", ch.index, word_count, min_words)
                        TranslationStats.record_chapter_failed(lang)
                        continue
                    job_store.update_progress(jid, i + 1, len(chapters_list), ch.title)
                    TranslationStats.record_chapter_complete(lang)
                    display_title = title_en or ch.title[:60]
                    exists = os.path.exists(output_path)
                    with open(output_path, "a" if exists else "w", encoding="utf-8") as fh:
                        if not exists:
                            fh.write(f"# {jid} — English Translation\n\n")
                        fh.write(f"## Chapter {ch.index}: {display_title}\n\n{tt}\n\n---\n\n")
                        fh.flush()
                        os.fsync(fh.fileno())
                    # ── Save checkpoint after every chapter ──
                    json.dump({
                        "last_idx": i,
                        "glossary_snapshot": agent.exact_store.snapshot(),
                        "previous_summary": prev_summary,
                    }, Path(ckpt_path).open("w"), ensure_ascii=False)
                job_store.complete_job(jid, output_path, 0)
            except Exception as exc:
                logger.error("Multi-lang sync translation failed for job %s: %s", jid, exc)
                job_store.fail_job(jid, str(exc))
            finally:
                backpressure.release()

        with ThreadPoolExecutor(max_workers=len(langs)) as executor:
            for entry in results:
                executor.submit(_run_translation, entry["lang"], entry["job_id"])
            executor.shutdown(wait=False)  # fire-and-forget, backpressure gates cleanup

    return {
        "project_id": project_id,
        "filename": filename,
        "total_chapters": total,
        "jobs": results,
    }


@app.get("/translate/{job_id}")
def get_translation_status(job_id: str):
    """Poll job progress — Celery backend or job_store fallback."""
    if _has_celery:
        from ..celery_app import app as celery_app
        key = f"translation:{job_id}"
        data = celery_app.backend.get(key)
        if data:
            return json.loads(data)
    # Fallback: query job_store when Celery is unavailable
    job = job_store.get_job(job_id)
    if job:
        return job
    return {"status": "unknown", "job_id": job_id}


@app.get("/glossary/{job_id}")
def get_glossary(job_id: str):
    """Download glossary JSON for a completed job."""
    job_id = _safe_job_id(job_id)
    glossary_path = OUTPUT_DIR / f"{job_id}_glossary.json"
    if glossary_path.exists():
        return json.loads(glossary_path.read_text(encoding="utf-8"))
    return JSONResponse(
        status_code=404,
        content={"error": "Glossary not found", "job_id": job_id},
    )


@app.get("/translation/{job_id}")
def get_translation(job_id: str):
    """Download the translated novel markdown."""
    for lang in ["en-US", "es-ES", "de", "fr"]:
        path = OUTPUT_DIR / f"{job_id}_full_novel_{lang}.md"
        if path.exists():
            return {"text": path.read_text(encoding="utf-8"), "target_lang": lang}
    return JSONResponse(
        status_code=404,
        content={"error": "Translation not found", "job_id": job_id},
    )


# ── Chapter parsing helpers for EPUB generation ─────────────────────

_CHAPTER_HEADER_RE = re.compile(r"^#{1,2}\s+Chapter\s+(\d+):?\s*(.*)", re.IGNORECASE)


def _parse_markdown_chapters(md_text: str) -> list[dict]:
    """Parse a translated Markdown file back into chapter dicts.

    Chapters are expected to start with ``# Chapter N: Title`` headers.
    Returns a list of ``{title, content, chapter_num}`` dicts.
    """
    lines = md_text.split("\n")
    chapters = []
    current_title = None
    current_lines = []
    current_num = 0

    for line in lines:
        m = _CHAPTER_HEADER_RE.match(line.strip())
        if m:
            # Save previous chapter
            if current_title is not None:
                chapters.append({
                    "title": current_title,
                    "content": "\n".join(current_lines).strip(),
                    "chapter_num": current_num,
                })
            current_num = int(m.group(1))
            current_title = f"Chapter {current_num}: {m.group(2)}" if m.group(2).strip() else f"Chapter {current_num}"
            current_lines = []
        elif current_title is not None:
            current_lines.append(line)

    # Save last chapter
    if current_title is not None:
        chapters.append({
            "title": current_title,
            "content": "\n".join(current_lines).strip(),
            "chapter_num": current_num,
        })

    return chapters


@app.get("/epub/{job_id}")
def download_epub(job_id: str):
    """Build and return an EPUB file for a completed translation."""
    from ..epub_builder import build_epub

    # ── Locate the translated Markdown file ──
    md_path = None
    target_lang = "en-US"
    for lang in ["en-US", "es-ES", "de", "fr"]:
        candidate = OUTPUT_DIR / f"{job_id}_full_novel_{lang}.md"
        if candidate.exists():
            md_path = candidate
            target_lang = lang
            break

    if md_path is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Translation not found for this job", "job_id": job_id},
        )

    # ── Load glossary if available ──
    glossary = None
    glossary_path = OUTPUT_DIR / f"{job_id}_glossary.json"
    if glossary_path.exists():
        try:
            glossary = json.loads(glossary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            glossary = None

    # ── Parse chapters from the Markdown ──
    md_text = md_path.read_text(encoding="utf-8")
    chapters = _parse_markdown_chapters(md_text)

    if not chapters:
        return JSONResponse(
            status_code=422,
            content={"error": "No chapters found in translated output", "job_id": job_id},
        )

    # ── Extract cover text (first paragraph of first chapter) ──
    first_content = chapters[0].get("content", "") if chapters else ""
    cover_text = first_content.split("\n\n")[0].strip() if first_content else ""

    # ── Build EPUB ──
    epub_path = OUTPUT_DIR / f"{job_id}.epub"
    language_code = target_lang.split("-")[0]  # "en-US" -> "en"

    try:
        build_epub(
            chapters=chapters,
            title=f"Westward Echo — {job_id}",
            author="Westward Echo",
            language=language_code,
            glossary=glossary,
            cover_text=cover_text,
            output_path=str(epub_path),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"EPUB generation failed: {exc}", "job_id": job_id},
        )

    return FileResponse(
        path=str(epub_path),
        media_type="application/epub+zip",
        filename=f"Westward_Echo_{job_id}.epub",
    )


# ═══════════════════════════════════════════════════════════════
# Job management endpoints
# ═══════════════════════════════════════════════════════════════

@app.post("/translate/resume/{job_id}")
async def resume_translation(
    job_id: str,
    file: UploadFile = File(...),
    translate_mode: str = Form("flash"),
    qa_interval: int = Form(20),
    genre: str = Form("romance_ceo"),
):
    """Resume a crashed translation from its last checkpoint.

    1. Load the job from JobStore
    2. Read the SQLite checkpoint table to find the last completed chapter number
    3. Reload the glossary snapshot from that checkpoint
    4. Re-split the uploaded original text to find remaining chapters
    5. Resume translation from chapter N+1 with the restored glossary
    """
    if not _has_celery:
        return JSONResponse(
            status_code=503,
            content={"error": "Celery worker not available", "job_id": job_id},
        )

    # ── File validation ──
    text, error = await _validate_novel_upload(file)
    if error:
        status_code = 413 if "too large" in error.lower() else 400
        return JSONResponse(status_code=status_code, content={"error": error})

    # 1. Load job
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found", "job_id": job_id})

    if job["status"] not in ("translating", "queued", "failed"):
        return JSONResponse(status_code=400, content={
            "error": f"Job has terminal status '{job['status']}' and cannot be resumed",
            "job_id": job_id,
        })

    # 2. Read the checkpoint table to find the last completed chapter
    last_chapter = 0
    glossary_snapshot = "{}"
    try:
        conn = sqlite3.connect(CHECKPOINT_DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT chapter_number, glossary_snapshot "
            "FROM translation_checkpoint "
            "WHERE job_id = ? "
            "ORDER BY chapter_number DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row:
            last_chapter = row["chapter_number"]
            glossary_snapshot = row["glossary_snapshot"] or "{}"
        conn.close()
    except sqlite3.OperationalError:
        # No checkpoint table yet — start from chapter 0
        pass

    # 4. Re-split to verify total chapters
    chapters = split_chapters(text)
    total = len([c for c in chapters if c.action != ParagraphTag.SKIP])

    # 5. Start the resume task from chapter N+1
    start_chapter = last_chapter + 1
    target_lang = job.get("target_lang", "en-US")
    task = resume_translate_task.delay(
        job_id=job_id,
        start_chapter=start_chapter,
        glossary_snapshot=glossary_snapshot,
        text=text,
        target_lang=target_lang,
        translate_mode=translate_mode,
        qa_interval=qa_interval,
        genre=genre,
    )

    # 6. Update job status back to translating
    job_store.update_progress(job_id, last_chapter, total, "")

    return {
        "job_id": job_id,
        "task_id": task.id,
        "resumed_from_chapter": last_chapter,
        "next_chapter": start_chapter,
        "total_chapters": total,
        "status": "resuming",
    }


@app.get("/jobs")
def list_jobs(limit: int = 50):
    """List recent jobs, newest first."""
    return job_store.list_jobs(limit=limit)


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    """Get a single job by id."""
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found", "job_id": job_id})
    return job


@app.get("/projects")
def list_projects(limit: int = 20):
    """List recent multi-language projects with grouped jobs."""
    return job_store.list_projects(limit=limit)


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    """Get a single project with all its language-variant jobs."""
    jobs = job_store.get_project_jobs(project_id)
    if not jobs:
        return JSONResponse(status_code=404, content={"error": "Project not found", "project_id": project_id})
    return {
        "project_id": project_id,
        "filename": jobs[0].get("filename", ""),
        "jobs": jobs,
    }


@app.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    """Delete a job record."""
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found", "job_id": job_id})
    job_store.delete_job(job_id)
    return {"status": "deleted", "job_id": job_id}


# ═══════════════════════════════════════════════════════════════
# Token cost tracking
# ═══════════════════════════════════════════════════════════════

@app.get("/jobs/{job_id}/cost")
def get_job_cost(job_id: str):
    """Return token usage and estimated cost for a job."""
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found", "job_id": job_id})
    cost = job_store.get_job_cost(job_id)
    return {
        "job_id": job_id,
        **cost,
    }


# ═══════════════════════════════════════════════════════════════
# Glossary presets (translation memory across books)
# ═══════════════════════════════════════════════════════════════

@app.post("/presets/{job_id}")
async def save_preset(
    job_id: str,
    name: str = Form(...),
    description: str = Form(""),
):
    """Save a completed job's glossary as a reusable preset."""
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found", "job_id": job_id})

    # Load the job's glossary
    glossary_path = OUTPUT_DIR / f"{job_id}_glossary.json"
    glossary_json = "{}"
    glossary_count = 0
    if glossary_path.exists():
        try:
            glossary_data = json.loads(glossary_path.read_text(encoding="utf-8"))
            glossary_json = json.dumps(glossary_data, ensure_ascii=False)
            glossary_count = len(glossary_data)
        except (json.JSONDecodeError, OSError):
            glossary_json = "{}"

    job_store.save_glossary_as_preset(
        job_id=job_id,
        preset_name=name,
        description=description,
        glossary_json=glossary_json,
    )

    return {
        "status": "saved",
        "preset_name": name,
        "glossary_count": glossary_count,
    }


@app.get("/presets")
def list_presets():
    """List available glossary presets."""
    presets = job_store.list_glossary_presets()
    return {"presets": presets}


@app.get("/presets/{preset_name}")
def get_preset(preset_name: str):
    """Get a single preset's glossary."""
    glossary = job_store.load_glossary_preset(preset_name)
    if not glossary:
        # Check if preset exists at all
        presets = job_store.list_glossary_presets()
        if not any(p["preset_name"] == preset_name for p in presets):
            return JSONResponse(
                status_code=404,
                content={"error": "Preset not found", "preset_name": preset_name},
            )
    return {"preset_name": preset_name, "glossary": glossary, "term_count": len(glossary)}


@app.delete("/presets/{preset_name}")
def delete_preset(preset_name: str):
    """Delete a glossary preset."""
    presets = job_store.list_glossary_presets()
    if not any(p["preset_name"] == preset_name for p in presets):
        return JSONResponse(
            status_code=404,
            content={"error": "Preset not found", "preset_name": preset_name},
        )
    job_store.delete_glossary_preset(preset_name)
    return {"status": "deleted", "preset_name": preset_name}


# ═══════════════════════════════════════════════════════════════
# File format auto-converters
# ═══════════════════════════════════════════════════════════════

def _convert_rtf(raw: bytes) -> Optional[str]:
    """Convert RTF bytes to plain UTF-8 text via macOS textutil."""
    import subprocess, tempfile
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".rtf", delete=False) as tf:
            tmp_path = tf.name
            tf.write(raw)
            tf.flush()
        result = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", tmp_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    return None


def _convert_docx(raw: bytes) -> Optional[str]:
    """Convert DOCX bytes to plain UTF-8 text."""
    import zipfile, io, tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tf:
            tf.write(raw)
            tf.flush()
            from docx import Document as DocxDocument
            doc = DocxDocument(tf.name)
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            os.unlink(tf.name)
            return text.strip() or None
    except Exception:
        return None


def _convert_epub(raw: bytes) -> Optional[str]:
    """Extract plain text from EPUB bytes. Strips HTML tags, returns raw text."""
    import zipfile, io
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            # Find all XHTML/HTML content files in OEBPS/
            text_parts = []
            for name in sorted(zf.namelist()):
                if name.endswith((".xhtml", ".html", ".htm")) and "OEBPS" in name:
                    html = zf.read(name).decode("utf-8", errors="ignore")
                    # Strip HTML tags
                    html = __import__("re").sub(r"<[^>]+>", "", html)
                    html = __import__("re").sub(r"&\w+;", " ", html)
                    html = __import__("re").sub(r"\n{3,}", "\n\n", html)
                    if html.strip():
                        text_parts.append(html.strip())
            return "\n\n".join(text_parts) if text_parts else None
    except Exception:
        return None
