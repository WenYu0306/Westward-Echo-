"""API routes — Celery-backed translation (mounted at /api)."""

import json
import re
import sqlite3
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse

from ..config import OUTPUT_DIR, CHECKPOINT_DB_PATH
from ..chapter_splitter import split_chapters, ParagraphTag
from ..job_store import job_store
from ..backpressure import backpressure
from ..stats import TranslationStats

try:
    from ..celery_app import translate_novel_task, resume_translate_task
    _has_celery = True
except Exception:
    _has_celery = False

app = FastAPI(title="Westward Echo API", version="0.2.0")


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
):
    """Submit a novel for translation. Returns job_id immediately."""
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

    text = (await file.read()).decode("utf-8")
    chapters = split_chapters(text)
    total = len([c for c in chapters if c.action != ParagraphTag.SKIP])

    # Create persistent job record
    filename = file.filename or "unknown.txt"
    job_id = job_store.create_job(filename, target_lang, total)

    if _has_celery:
        task = translate_novel_task.delay(
            job_id=job_id, text=text, target_lang=target_lang,
            translate_mode=translate_mode, qa_interval=qa_interval,
            genre=genre,
        )
        return {"job_id": job_id, "task_id": task.id, "total_chapters": total, "status": "queued"}
    return JSONResponse(
        status_code=503,
        content={"error": "Celery worker not available", "job_id": job_id, "total_chapters": total},
    )


@app.post("/translate/multi")
async def translate_multi(
    file: UploadFile = File(...),
    target_langs: str = Form("en-US,es-ES,ar-SA"),
    translate_mode: str = Form("flash"),
    genre: str = Form("romance_ceo"),
    qa_interval: int = Form(20),
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

    text = (await file.read()).decode("utf-8")
    chapters = split_chapters(text)
    total = len([c for c in chapters if c.action != ParagraphTag.SKIP])
    filename = file.filename or "unknown.txt"

    # Create a project to group all language variants
    project_id = job_store.create_project(filename)

    langs = [lang.strip() for lang in target_langs.split(",") if lang.strip()]
    results = []

    for lang in langs:
        job_id = job_store.add_language_job(project_id, lang, filename, total)

        if _has_celery:
            task = translate_novel_task.delay(
                job_id=job_id, text=text, target_lang=lang,
                translate_mode=translate_mode, qa_interval=qa_interval,
                genre=genre,
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
                agent = TranslationAgent()
                all_translations = []
                prev_summary = ""
                output_path = str(OUTPUT_DIR / f"{jid}_full_novel_{lang}.md")

                prefetcher = ChapterPrefetcher(agent.exact_store, agent.semantic_store)
                if len(chapters_list) > 1:
                    try:
                        prefetcher.prefetch(chapters_list[1].heading or chapters_list[1].content[:200])
                    except Exception:
                        pass

                for i, ch in enumerate(chapters_list):
                    try:
                        result = agent.process(
                            chapter=ch, chapter_num=i + 1, total_chapters=len(chapters_list),
                            target_lang=lang, prev_summary=prev_summary,
                            translate_mode=translate_mode,
                            genre=genre,
                        )
                        all_translations.append(result.translation)
                        prev_summary = result.summary or prev_summary
                        title = ch.heading or f"Chapter {i + 1}"
                        job_store.update_progress(jid, i + 1, len(chapters_list), title)
                        TranslationStats.record_chapter_complete(lang)
                    except CircuitBreakerOpenError:
                        TranslationStats.record_chapter_failed(lang)
                        break
                    except Exception:
                        TranslationStats.record_chapter_failed(lang)
                        continue

                merged = "\n\n".join(all_translations)
                import os as _os
                _os.makedirs(str(OUTPUT_DIR), exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as fh:
                    fh.write(merged)
                job_store.complete_job(jid, output_path, 0)
            except Exception as exc:
                job_store.fail_job(jid, str(exc))
            finally:
                backpressure.release()

        executor = ThreadPoolExecutor(max_workers=len(langs))
        for entry in results:
            executor.submit(_run_translation, entry["lang"], entry["job_id"])

    return {
        "project_id": project_id,
        "filename": filename,
        "total_chapters": total,
        "jobs": results,
    }


@app.get("/translate/{job_id}")
def get_translation_status(job_id: str):
    """Poll job progress from Redis."""
    if _has_celery:
        from ..celery_app import app as celery_app
        key = f"translation:{job_id}"
        data = celery_app.backend.get(key)
        if data:
            return json.loads(data)
    return {"status": "unknown", "job_id": job_id}


@app.get("/glossary/{job_id}")
def get_glossary(job_id: str):
    """Download glossary JSON for a completed job."""
    glossary_path = OUTPUT_DIR / f"{job_id}_glossary.json"
    if glossary_path.exists():
        return json.loads(glossary_path.read_text(encoding="utf-8"))
    return {"error": "Glossary not found", "job_id": job_id}


@app.get("/translation/{job_id}")
def get_translation(job_id: str):
    """Download the translated novel markdown."""
    for lang in ["en-US", "es-ES", "ar-SA"]:
        path = OUTPUT_DIR / f"{job_id}_full_novel_{lang}.md"
        if path.exists():
            return {"text": path.read_text(encoding="utf-8"), "target_lang": lang}
    return {"error": "Translation not found", "job_id": job_id}


# ── Chapter parsing helpers for EPUB generation ─────────────────────

_CHAPTER_HEADER_RE = re.compile(r"^#\s+Chapter\s+(\d+):?\s*(.*)", re.IGNORECASE)


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
    for lang in ["en-US", "es-ES", "ar-SA"]:
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

    text = (await file.read()).decode("utf-8")

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
