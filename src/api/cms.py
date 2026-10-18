"""CMS API endpoints — import novels from CMS, publish translations, list sources.

Mounted at /api/cms by src/main.py.

【中文·这个文件是干什么的】
这是西渡和"内容管理系统"（CMS，Content Management System）对接的接口。
CMS 就是存放小说原文的地方——可以是本地文件夹，也可以是远程服务器。
本文件提供三个能力：
  ① 从 CMS 拉取小说原文，创建翻译任务（POST /import）
  ② 把翻完的成品推回 CMS（POST /publish/{job_id}）
  ③ 列出 CMS 里有哪些小说可以翻（GET /sources）

【中文·它在整个流程里的位置】
被 src/main.py 挂载到 /api/cms 路径下。
调用方（比如网页前端或外部脚本）通过这些接口和西渡交互，
不需要自己去找文件、解析格式，只要告诉西渡"翻哪本书"就行。

【中文·核心概念解释】
  · Connector（连接器）—— 一个抽象层，屏蔽了"小说存在哪里"的细节。
    不管原文在本地文件夹还是远程服务器，调用方都用同一套接口操作。
    具体用哪种连接器由 .env 里的 CMS_SOURCE_TYPE 决定（file 或 webhook）。
  · HTTP POST —— "提交数据"的请求方式，这里用来提交"请帮我翻这本书"的请求。
  · HTTP GET  —— "获取数据"的请求方式，这里用来查询"有哪些书可翻"。
  · HTTP 400  —— "请求有误"，比如传了非法的文件名。
  · HTTP 404  —— "找不到"，比如指定的小说不存在。
  · HTTP 409  —— "冲突"，比如任务还没翻完就想发布。
  · HTTP 502  —— "上游出错"，比如 CMS 那边连不上。
"""

from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse

from ..chapter_splitter import ParagraphTag, split_chapters
from ..cms import get_connector
from ..config import VERSION
from ..job_store import job_store

try:
    from ..celery_app import translate_novel_task
    # celery_app sets the task to None when celery is not installed;
    # the import itself still succeeds, so check the value too
    # (same guard as routes.py).
    # 【中文】Celery 是后台任务队列，负责在后台跑翻译这种耗时操作。
    # 如果没装 Celery，import 不会报错但 translate_novel_task 会是 None，
    # 所以除了检查 import 是否成功，还要检查值是否为 None。
    _has_celery = translate_novel_task is not None
except Exception:
    _has_celery = False

# 【中文】创建一个独立的 FastAPI 子应用，专门处理 CMS 相关的接口。
# 它会被 main.py 挂载到 /api/cms 路径下，所有这里的接口地址都自动加上 /api/cms 前缀。
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

    【中文】从 CMS 拉取一本小说的原文，并创建一个翻译任务。
    参数：
      · source_type —— 来源类型，必须和 .env 里 CMS_SOURCE_TYPE 一致。
        "file" = 本地文件；"webhook" = 远程服务器。默认 "file"。
      · source_id   —— 小说标识。本地文件模式下是不带 .txt 后缀的文件名；
        webhook 模式下是 CMS 那边的唯一 ID。必填。
      · job_title   —— 任务标题（可选）。不填就用 source_id 作为文件名。
    返回：包含 job_id（任务号）、task_id（后台任务号）、total_chapters（总章数）的字典。
    """
    # 【中文】获取对应类型的连接器，用它来拉取小说原文。
    connector = get_connector()

    try:
        text = connector.pull_novel(source_id)
    except ValueError as exc:
        # source_id failed validation (traversal attempt, bad chars, etc.)
        # 【中文】source_id 校验失败（比如包含 ../ 试图访问上级目录，或有非法字符）。
        # 返回 400 表示"你传的参数有问题"。⚠️ 这是安全防线，防止路径遍历攻击。
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except FileNotFoundError as exc:
        # 【中文】指定的小说文件不存在 → 返回 404。
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except Exception as exc:
        # 【中文】其他未知错误（比如远程服务器连不上）→ 返回 502（上游出错）。
        return JSONResponse(status_code=502, content={"error": f"CMS pull failed: {exc}"})

    # 【中文】把原文按章节切分，统计有效章节数（跳过纯分隔符等非正文内容）。
    chapters = split_chapters(text)
    total = len([c for c in chapters if c.action != ParagraphTag.SKIP])

    # Use job_title as the filename, falling back to the source_id
    # 【中文】确定输出文件名：优先用用户给的标题，没有就用 source_id。
    filename = (job_title.strip() + ".txt") if job_title.strip() else f"{source_id}.txt"
    # 【中文】在任务存储里创建一条记录，返回唯一的 job_id。
    job_id = job_store.create_job(filename, "en-US", total)

    if _has_celery:
        # 【中文】有 Celery → 把翻译任务丢进后台队列异步执行。
        # delay() 是 Celery 的调用方式，任务会在 worker 进程里跑，
        # 当前请求立刻返回，不用等翻译完成。
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

    # 【中文】没有 Celery → 只创建任务记录，标记为 "created"。
    # 实际翻译需要另外触发（比如通过 routes.py 的 /translate 接口）。
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

    【中文】把翻完的小说推送到指定平台。
    参数：
      · job_id   —— 任务号，URL 路径里传入。
      · platform —— 发布目标平台，默认 "web"。
    返回：包含发布结果的字典（url、status 等）。

    【中文·前置检查】
    只有状态为 "complete" 的任务才能发布。如果任务还在翻译中或失败了，
    返回 409（冲突），意思是"现在不能做这个操作"。
    """
    # Verify the job exists
    # 【中文】先确认任务存在，不存在就返回 404。
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found", "job_id": job_id})

    # 【中文】检查任务状态，只有 "complete" 才允许发布。
    # 409 Conflict = "你的请求本身没问题，但当前状态不允许这个操作"。
    if job.get("status") != "complete":
        return JSONResponse(
            status_code=409,
            content={
                "error": f"Job is not complete (status: {job.get('status')})",
                "job_id": job_id,
            },
        )

    # 【中文】通过连接器把译文推到目标平台。
    connector = get_connector()
    try:
        result = connector.push_translation(job_id, platform)
    except Exception as exc:
        # 【中文】推送失败 → 返回 500（服务器内部错误）。
        return JSONResponse(
            status_code=500,
            content={"error": f"Publish failed: {exc}", "job_id": job_id},
        )

    return {
        "job_id": job_id,
        "platform": platform,
        **result,  # 【中文】把连接器返回的结果展开合并到响应里。
    }


@app.get("/sources")
def list_available_sources():
    """List novel source identifiers available in the configured CMS source.

    Currently only the ``"file"`` connector supports listing sources.

    【中文】列出 CMS 里有哪些小说可以翻译。
    返回：{"sources": [...], "count": N}，sources 是可用的小说标识列表。
    注意：目前只有 "file" 类型的连接器支持列出来源，
    webhook 类型可能返回空列表。
    """
    connector = get_connector()
    sources = connector.list_sources()
    return {"sources": sources, "count": len(sources)}
