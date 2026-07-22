"""API routes — Celery-backed translation (mounted at /api)."""

import json
import re
import uuid
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse

from ..config import OUTPUT_DIR
from ..chapter_splitter import split_chapters, ParagraphTag

try:
    from ..celery_app import translate_novel_task
    _has_celery = True
except Exception:
    _has_celery = False

app = FastAPI(title="Westward Echo API", version="0.2.0")


@app.get("/health")
def health():
    return {"status": "ok", "celery": _has_celery, "version": "0.2.0"}


@app.post("/translate")
async def translate_novel(
    file: UploadFile = File(...),
    target_lang: str = Form("en-US"),
    translate_mode: str = Form("flash"),
    qa_interval: int = Form(20),
):
    """Submit a novel for translation. Returns job_id immediately."""
    job_id = str(uuid.uuid4())[:8]
    text = (await file.read()).decode("utf-8")
    chapters = split_chapters(text)
    total = len([c for c in chapters if c.action != ParagraphTag.SKIP])

    if _has_celery:
        task = translate_novel_task.delay(
            job_id=job_id, text=text, target_lang=target_lang,
            translate_mode=translate_mode, qa_interval=qa_interval,
        )
        return {"job_id": job_id, "task_id": task.id, "total_chapters": total, "status": "queued"}
    return JSONResponse(
        status_code=503,
        content={"error": "Celery worker not available", "job_id": job_id, "total_chapters": total},
    )


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
