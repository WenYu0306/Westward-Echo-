"""API routes — Celery-backed translation (mounted at /api)."""

import json
import uuid
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse

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
