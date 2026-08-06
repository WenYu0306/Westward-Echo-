"""CMS API endpoints — import novels from CMS, publish translations, list sources.

Mounted at /api/cms by src/main.py.
"""

from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse

from ..config import VERSION
from ..cms import get_connector
from ..chapter_splitter import split_chapters, ParagraphTag
from ..job_store import job_store

try:
    from ..celery_app import translate_novel_task
    _has_celery = True
except Exception:
    _has_celery = False

app = FastAPI(title="Westward Echo CMS API", version=VERSION)


@app.post("/import")
def import_from_cms(
    source_type: str = Form("file"),
    source_id: str = Form(...),
    job_title: str = Form(""),
):
    """Pull a novel from the configured CMS source and create a translation job.

    ``source_type`` must match the configured ``CMS_SOURCE_TYPE`` (``"file"`` or
    ``"webhook"``).  ``source_id`` is the filename (without ``.txt``) for the file
    connector, or the CMS identifier for the webhook connector.
    """
    connector = get_connector()

    try:
        text = connector.pull_novel(source_id)
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"CMS pull failed: {exc}"})

    chapters = split_chapters(text)
    total = len([c for c in chapters if c.action != ParagraphTag.SKIP])

    # Use job_title as the filename, falling back to the source_id
    filename = (job_title.strip() + ".txt") if job_title.strip() else f"{source_id}.txt"
    job_id = job_store.create_job(filename, "en-US", total)

    if _has_celery:
        task = translate_novel_task.delay(
            job_id=job_id,
            text=text,
            target_lang="en-US",
            translate_mode="flash",
            qa_interval=20,
            genre="romance_ceo",
        )
        return {
            "job_id": job_id,
            "task_id": task.id,
            "total_chapters": total,
            "title": job_title or source_id,
        }

    return {
        "job_id": job_id,
        "task_id": None,
        "total_chapters": total,
        "title": job_title or source_id,
        "status": "created",
    }


@app.post("/publish/{job_id}")
def publish_translation(
    job_id: str,
    platform: str = Form("web"),
):
    """Push a completed translation to the configured publishing target.

    Returns a dict with ``url``, ``status``, and optionally ``platform_message``.
    """
    # Verify the job exists
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found", "job_id": job_id})

    if job.get("status") != "complete":
        return JSONResponse(
            status_code=409,
            content={
                "error": f"Job is not complete (status: {job.get('status')})",
                "job_id": job_id,
            },
        )

    connector = get_connector()
    try:
        result = connector.push_translation(job_id, platform)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Publish failed: {exc}", "job_id": job_id},
        )

    return {
        "job_id": job_id,
        "platform": platform,
        **result,
    }


@app.get("/sources")
def list_available_sources():
    """List novel source identifiers available in the configured CMS source.

    Currently only the ``"file"`` connector supports listing sources.
    """
    connector = get_connector()
    sources = connector.list_sources()
    return {"sources": sources, "count": len(sources)}
