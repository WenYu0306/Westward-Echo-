"""API routes — Celery-backed translation (mounted at /api).

【中文·这个文件是干什么的】
这是西渡服务的**主力接口文件**，提供了翻译相关的所有 HTTP 接口。
所谓"HTTP 接口"就是程序之间对话的窗口——网页上点一下按钮、外部脚本发一条请求，
最终都会走到这里定义的某个函数来处理。

【中文·它在整个流程里的位置】
被 src/main.py 挂载到 /api 路径下（注意是最后挂载的，原因见 main.py 注释）。
前端网页、命令行工具、第三方集成都通过这些接口和西渡交互。

【中文·核心概念解释】
  · FastAPI —— Python 的 Web 框架，负责把网址映射到函数、处理请求和响应。
  · Celery —— 后台任务队列。翻译一本书可能要几个小时，不能让请求一直等着，
    所以把翻译任务丢到后台跑，接口立刻返回一个任务号，调用方拿着任务号来查进度。
  · Redis —— Celery 的消息中间件，负责在"提交任务的进程"和"执行任务的进程"之间传话。
  · Backpressure（背压）—— 流量控制机制。当队列满了就拒绝新任务，
    而不是无限堆积导致内存爆掉或大模型账单失控。
  · Checkpoint（断点续翻）—— 每翻完一章就存个档，崩了可以从上次的位置接着翻，
    不用从头来过。
  · Glossary（术语库）—— 记录"这个词该译成什么"的对照表，全书统一译名。
  · Preset（预设）—— 把某本书积累的术语库保存下来，翻下一本书时可以复用。

【中文·接口清单导览】
以下是本文件提供的所有接口，按功能分组：

  📤 提交翻译
    POST /translate          —— 上传小说文件，创建单语种翻译任务
    POST /translate/multi    —— 上传一次，同时翻译成多种语言
    POST /translate/resume/{job_id} —— 从断点恢复崩溃的翻译任务

  📊 查询进度与结果
    GET  /translate/{job_id} —— 查询翻译进度
    GET  /translation/{job_id} —— 下载翻译完成的 Markdown 文件
    GET  /epub/{job_id}      —— 下载翻译完成的 EPUB 电子书
    GET  /glossary/{job_id}  —— 下载该任务积累的术语库

  📋 任务管理
    GET  /jobs               —— 列出最近的翻译任务
    GET  /jobs/{job_id}      —— 查看单个任务详情
    DELETE /jobs/{job_id}    —— 删除一个任务记录
    GET  /projects           —— 列出多语种项目
    GET  /projects/{project_id} —— 查看单个项目及其所有语种任务

  💰 费用追踪
    GET  /jobs/{job_id}/cost —— 查看该任务的实际 token 用量和花费

  📚 术语预设
    POST /presets/{job_id}   —— 把某任务的术语库保存为可复用预设
    GET  /presets            —— 列出所有预设
    GET  /presets/{name}     —— 查看某个预设的内容
    DELETE /presets/{name}   —— 删除一个预设

  🔧 系统
    GET  /health             —— 健康检查（不需要密钥、不限流）
"""

import json
import os
import re
import sqlite3
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .. import token_ledger
from ..api.logging import logger
from ..backpressure import backpressure
from ..chapter_splitter import ParagraphTag, split_chapters
from ..config import (
    CHECKPOINT_DB_PATH,
    DEFAULT_TARGET_LANG,
    MAX_UPLOAD_SIZE_BYTES,
    MAX_UPLOAD_SIZE_MB,
    OUTPUT_DIR,
    SUPPORTED_LANGUAGES,
    VERSION,
)
from ..job_store import job_store
from ..stats import TranslationStats
from ..text_io import decode_bytes

try:
    from ..celery_app import resume_translate_task, translate_novel_task
    _has_celery = True
    # 【中文】Celery 没装时 import 不报错但 task 是 None，需要额外检查。
    if translate_novel_task is None:
        _has_celery = False
except Exception:
    _has_celery = False

# 【中文】创建 FastAPI 子应用实例，所有下面的 @app.xxx 都是在这个应用上注册路由。
# 它会被 main.py 挂载到 /api 路径下。
app = FastAPI(title="Westward Echo API", version=VERSION)

# ── Security ───────────────────────────────────────────────────
# 【中文·安全校验】job_id 的正则白名单：只允许字母、数字、下划线、连字符，
# 最长 64 个字符。目的是防止路径遍历攻击（比如传入 "../../etc/passwd"）
# 或注入恶意字符。所有接收 job_id 的接口都必须先过这道校验。
_VALID_JOB_ID = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')


def _split_by_content_type(text: str, content_type: str):
    """Split upload text into translatable chapters (SKIP-filtered).

    content_type is accepted for forward compatibility but only "novel"
    is a validated path — everything routes through the chapter splitter.

    【中文】把上传的小说原文切分成章节列表。
    参数：
      · text         —— 小说全文字符串。
      · content_type —— 内容类型（预留参数，目前只用 "novel"）。
    返回：过滤掉 SKIP 标记后的章节列表（SKIP 是纯分隔符等非正文内容）。
    """
    chunks = split_chapters(text)
    return [c for c in chunks if c.action != ParagraphTag.SKIP]

def _safe_job_id(job_id: str) -> str:
    """Reject job IDs containing path traversal or illegal characters.

    【中文】校验 job_id 是否合法。不合法就抛出 HTTP 400 异常。
    这是一个安全防线：job_id 会被拼进文件路径里，如果不校验，
    攻击者可以传 "../../etc/passwd" 之类的值来读取任意文件。
    """
    if not _VALID_JOB_ID.match(job_id):
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid job_id")
    return job_id


# ═══════════════════════════════════════════════════════════════
# File upload validation
# ═══════════════════════════════════════════════════════════════

async def _validate_novel_upload(file: UploadFile):
    """Validate and auto-convert uploaded novel file to plain UTF-8 text.

    Supports: .txt (UTF-8/GBK/GB2312), .rtf (macOS TextEdit), .docx (Word),
              .md (Markdown), .epub (extracted text).
    Returns ``(text, error_message)`` — exactly one will be non-None.

    【中文】校验并转换用户上传的小说文件，统一转成 UTF-8 纯文本。
    支持五种格式：.txt、.rtf、.docx、.md、.epub。
    返回一个二元组 (text, error)：成功时 text 有值、error 为 None；
    失败时反过来。这种"二选一"的返回方式避免了抛异常的开销。

    【中文·校验流程】
      ① 检查文件大小是否超限（MAX_UPLOAD_SIZE_MB，默认 50MB）。
      ② 根据文件扩展名或文件头魔数判断格式，调用对应的转换器。
      ③ 检查转换结果是否包含中文字符（前 10000 字符内至少有一个）。
         不含中文说明可能传错了文件，直接拒绝。
    """
    # 【中文】按大小上限读入（最多 MAX+1 字节），超限立即拒绝，
    # 避免把超大文件整体读进内存造成内存耗尽。
    content = await file.read(MAX_UPLOAD_SIZE_BYTES + 1)
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        return None, f"File too large. Maximum {MAX_UPLOAD_SIZE_MB}MB."

    filename = (file.filename or "").lower()
    text = None

    # ── RTF: macOS TextEdit default format ──
    # 【中文】RTF 是 macOS 文本编辑器的默认保存格式。
    # 除了看扩展名，还检查文件头 "{\\rtf"（RTF 文件的魔数），
    # 因为有些 RTF 文件可能没有 .rtf 后缀。
    if filename.endswith(".rtf") or content[:5] == b"{\\rtf":
        text = _convert_rtf(content)

    # ── DOCX: Word documents ──
    # 【中文】DOCX 本质是一个 ZIP 压缩包，里面是 XML 文件。
    # 文件头 "PK" 是 ZIP 格式的魔数（ZIP 发明者 Phil Katz 的名字缩写），
    # 再配合 "word/document" 路径确认是 Word 文档而非其他 ZIP 文件。
    elif (
        filename.endswith(".docx") or (content[:2] == b"PK" and b"word/document" in content[:2048])
    ):
        text = _convert_docx(content)

    # ── EPUB: extract plain text from packaged novel ──
    # 【中文】EPUB 是电子书的标准格式，也是 ZIP 包，里面是 HTML 文件。
    elif filename.endswith(".epub"):
        text = _convert_epub(content)

    # ── Markdown: pass through ──
    # 【中文】Markdown 文件本身就是纯文本，只需要处理编码问题。
    # gb18030 是 GBK 的超集，能解码更多中文编码变体。
    elif filename.endswith(".md") or filename.endswith(".markdown"):
        text = decode_bytes(content, ("utf-8", "gb18030", "gbk"))

    # ── Plain text: auto-detect encoding ──
    # 【中文】其他情况当作纯文本处理，自动检测编码（UTF-8/GBK/GB2312 等）。
    else:
        text = decode_bytes(content)

    # 【中文】转换失败或结果为空 → 返回错误提示。
    if not text:
        return None, "Unable to decode file. Supported: .txt, .rtf, .docx, .md, .epub"
    # 【中文】检查前 10000 个字符里有没有中文字符（Unicode 范围 一-鿿）。
    # 如果没有，说明上传的可能不是中文小说，提前拦截避免浪费资源。
    if not re.search(r'[一-鿿]', text[:10000]):
        return None, "File does not appear to contain Chinese text."
    return text, None


@app.get("/health")
def health():
    """Full subsystem health report. Always available (no auth, no rate limit).

    【中文】健康检查接口。返回所有子系统的状态报告（数据库、缓存、密钥等）。
    这个接口永远开放：不需要 API 密钥、不受限流约束。
    看板页面每 5 秒轮询一次这个地址来判断服务是否正常。
    """
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

    【中文】上传小说文件并提交翻译任务。接口立刻返回 job_id，翻译在后台进行。
    参数：
      · file           —— 上传的小说文件（.txt/.rtf/.docx/.md/.epub）。必填。
      · target_lang    —— 目标语言，默认 "en-US"。可选值见 SUPPORTED_LANGUAGES。
      · translate_mode —— 翻译模式。"flash" = 快速模式（跳过冷读者复检，便宜快）；
                          其他值 = 完整模式（走全部 4 个角色）。
      · qa_interval    —— 每隔多少章做一次质量检查，默认 20。
      · genre          —— 小说类型（romance_ceo/xianxia/urban/scifi/folk_religion）。
                          影响提示词里的风格指导。
      · content_type   —— 内容类型，目前只用 "novel"。
      · glossary_preset—— 术语预设名称。如果填了，会把这个预设的术语库预加载，
                          让翻译从一开始就知道这些词该怎么译（"热启动"）。
      · api_key        —— 用户自己的 LLM API Key。不填就用服务器配置的。
    返回：{"job_id": "...", "task_id": "...", "total_chapters": N, "status": "queued"}
    """
    # ── Validate BEFORE accepting work — rejected requests must not
    #    consume a backpressure slot ──
    # 【中文·⚠️ 先校验再接活】校验失败的请求不能占用背压名额。
    # 如果先占了名额再校验，校验失败后名额虽然会释放，但在高并发下
    # 可能出现"所有名额都被无效请求占着，真正有效的请求进不来"的情况。
    text, error = await _validate_novel_upload(file)
    if error:
        # 【中文】文件太大返回 413（Payload Too Large），其他错误返回 400。
        status_code = 413 if "too large" in error.lower() else 400
        return JSONResponse(status_code=status_code, content={"error": error})

    # ── API key validation (optional: user's key overrides env) ──
    # 【中文】校验用户提供的 LLM API Key 格式。
    # 以 "sk-" 开头是 OpenAI 兼容 API 的标准格式。
    if api_key and not api_key.startswith("sk-"):
        return JSONResponse(
            status_code=400,
            content={"error": "无效的 API Key 格式（应以 sk- 开头）"},
        )
    # 【中文】如果用户没提供 key，就用服务器环境变量里配的。
    # 如果环境变量也没配，那就没法调大模型了，返回 400 告知用户。
    if not api_key:
        from ..config import LLM_API_KEY
        if not LLM_API_KEY:
            return JSONResponse(
                status_code=400,
                content={"error": "请提供 LLM API Key，或配置服务器环境变量"},
            )
        api_key = LLM_API_KEY

    # ── Backpressure: reject new work if queue is full ──
    # 【中文·背压机制】如果当前正在翻译的任务太多（队列满了），
    # 直接拒绝新请求，返回 503（Service Unavailable）。
    # 这比"排队等着"好：排队会让内存持续增长、大模型账单不可控，
    # 而且用户等了半天才发现前面排了 100 个任务，体验更差。
    # 不如一开始就告诉他"忙不过来，请稍后再试"。
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

    try:
        # 【中文】校验通过、名额已占 → 交给内部函数处理实际的翻译启动逻辑。
        return await _translate_accepted(
            text=text, file=file, target_lang=target_lang,
            translate_mode=translate_mode, qa_interval=qa_interval,
            genre=genre, content_type=content_type,
            glossary_preset=glossary_preset, api_key=api_key,
        )
    except Exception:
        # 【中文】如果启动过程中出错，必须释放背压名额，
        # 否则这个名额就永久丢失了，服务会越来越"忙"直到完全不接受新请求。
        backpressure.release()
        raise


async def _translate_accepted(
    text: str, file: UploadFile, target_lang: str, translate_mode: str,
    qa_interval: int, genre: str, content_type: str,
    glossary_preset: str, api_key: str,
):
    """Start a translation job after validation + backpressure accept.

    The caller owns the accepted backpressure slot: on any exception here
    the caller must release it. On success the slot is released by the
    worker (Celery task or sync thread) when the job finishes.

    【中文】校验和背压都已通过，这里负责真正启动翻译任务。
    有两种执行路径：
      ① 有 Celery → 把任务丢进后台队列，worker 进程异步执行。
      ② 没有 Celery → 在当前进程里起一个后台线程同步执行。
    无论哪种路径，任务完成或失败时都必须释放背压名额。
    """
    # 【中文】把原文切分成章节列表，统计有效章节数。
    chapters = _split_by_content_type(text, content_type)
    total = len(chapters)

    # Create persistent job record
    # 【中文】在任务存储（SQLite）里创建一条持久化记录，拿到唯一的 job_id。
    filename = file.filename or "unknown.txt"
    job_id = job_store.create_job(filename, target_lang, total,
                                  content_type=content_type)

    # ── Pre-load glossary preset if requested ──
    # 【中文】如果用户指定了术语预设，把它加载出来序列化成 JSON，
    # 后面传给翻译 agent 做"热启动"——让翻译从一开始就知道这些术语怎么译。
    preset_glossary_json = ""
    if glossary_preset:
        preset_glossary = job_store.load_glossary_preset(glossary_preset)
        if preset_glossary:
            preset_glossary_json = json.dumps(preset_glossary, ensure_ascii=False)

    if _has_celery:
        # 【中文】有 Celery → 异步执行。delay() 把任务序列化后发到 Redis 队列，
        # worker 进程从队列里取出来执行。当前请求立刻返回。
        task = translate_novel_task.delay(
            job_id=job_id, text=text, target_lang=target_lang,
            translate_mode=translate_mode, qa_interval=qa_interval,
            genre=genre,
            glossary_preset_glossary=preset_glossary_json,
            content_type=content_type,
            api_key=api_key,
        )
        return {"job_id": job_id, "task_id": task.id, "total_chapters": total, "status": "queued"}

    # Celery not available — run synchronously in background
    # 【中文】没有 Celery → 在后台线程里同步跑翻译。
    # 这种方式适合单机开发或小规模使用，不需要额外部署 Redis 和 worker。
    from ..agent.graph import TranslationAgent
    from ..circuit_breaker import CircuitBreakerOpenError

    def _run_sync():
        """Background thread function for synchronous translation.

        【中文】后台线程的执行函数。逐章翻译，每章完成后更新进度、写存档。
        整个过程包在 try/finally 里，确保无论成功还是失败都会释放背压名额。
        """
        import traceback as _tb
        try:
            chapters_list = [c for c in chapters if c.action != ParagraphTag.SKIP]
            # 【中文】创建翻译 agent 实例，它内部封装了 4 个 AI 角色的流水线。
            agent = TranslationAgent(book_id=job_id, api_key=api_key,
                                     target_lang=target_lang)
            # 【中文】如果有术语预设，把里面的术语逐条加入 agent 的精确术语库。
            if preset_glossary_json:
                try:
                    preset_terms = json.loads(preset_glossary_json)
                    for cn, en in preset_terms.items():
                        agent.exact_store.add(cn, en, category="culture", target_lang=target_lang)
                except Exception:
                    pass  # 预设加载失败不影响翻译，只是少了热启动的优势
            prev_summary = ""
            flash_mode = translate_mode == "flash"
            output_path = str(OUTPUT_DIR / f"{job_id}_full_novel_{target_lang}.md")
            ckpt_path = str(OUTPUT_DIR / f"{job_id}_checkpoint.json")

            # ── Resume from checkpoint if available ──
            # 【中文·断点续翻】检查是否有上次的存档文件。
            # 如果有，就从上次翻到的位置继续，而不是从头重来。
            # 这对于翻了 500 章突然崩了的场景至关重要。
            start_i = 0
            if os.path.exists(ckpt_path):
                try:
                    ckpt = json.loads(Path(ckpt_path).read_text("utf-8"))
                    start_i = ckpt.get("last_idx", -1) + 1
                    prev_summary = ckpt.get("previous_summary", "")
                    if ckpt.get("glossary_snapshot"):
                        agent.load_glossary_snapshot(
                            ckpt["glossary_snapshot"], target_lang=target_lang
                        )
                    logger.info(
                        "Web sync: resuming %s from chapter %d/%d",
                        job_id, start_i + 1, len(chapters_list),
                    )
                except Exception:
                    start_i = 0  # 存档损坏就从零开始

            import time as _t
            # Chapters actually written vs skipped in THIS run. complete_job
            # used to fire unconditionally after the loop, so a job that
            # delivered 1 of 2 chapters was marked "completed" — the same false
            # completion the CLI runner had.
            #
            # 【中文】记录本次运行实际交付了多少章、哪些章失败了。
            # 以前不管有没有失败的章都标记"完成"，导致用户下载到的文件缺章
            # 却以为翻译成功了。现在只有全部成功才标记完成。
            delivered_count = 0
            failed_indexes: list[int] = []
            for i in range(start_i, len(chapters_list)):
                ch = chapters_list[i]
                try:
                    result = agent.translate_chapter(
                        chapter_title=ch.title, chapter_content=ch.content,
                        chapter_number=ch.index, previous_summary=prev_summary,
                        target_lang=target_lang, genre=genre,
                        skip_readback=flash_mode,
                        content_type=content_type,
                    )
                except CircuitBreakerOpenError:
                    # 【中文】熔断器打开 = 大模型 API 连续出错，主动停止翻译。
                    # 这是保护机制：与其反复调一个坏掉的 API 烧钱，不如停下来等人修。
                    job_store.fail_job(job_id, "Circuit breaker opened")
                    return
                except Exception as exc:
                    err_msg = str(exc).lower()
                    # 【中文】超时错误特殊处理：等 3 秒重试一次。
                    # 网络抖动导致的超时往往重试就能解决，不值得直接标记失败。
                    if "timed out" in err_msg or "timeout" in err_msg:
                        logger.warning("Sync chapter %d timed out — retrying", ch.index)
                        _t.sleep(3)
                        try:
                            result = agent.translate_chapter(
                                chapter_title=ch.title, chapter_content=ch.content,
                                chapter_number=ch.index, previous_summary=prev_summary,
                                target_lang=target_lang, genre=genre,
                                skip_readback=flash_mode,
                                content_type=content_type,
                            )
                        except Exception as e2:
                            logger.warning("Sync chapter %d failed after retry: %s", ch.index, e2)
                            failed_indexes.append(ch.index)
                            continue
                    else:
                        # 【中文】非超时错误 → 记录失败章节，跳过继续翻下一章。
                        logger.warning("Sync chapter %d failed: %s", ch.index, exc)
                        failed_indexes.append(ch.index)
                        continue
                tt = result.get("translated_text", "")
                title_en = result.get("chapter_title_en", "")
                prev_summary = result.get("chapter_summary", "")
                # Truncation guard judges the UNFILTERED text: dialogue-mode
                # deliverables are legitimately much shorter than the source.
                #
                # 【中文·截断检测】检查翻译结果是否太短。
                # 用 pre_filter_text（过滤前的原文）而不是最终译文来判断，
                # 因为对话模式的译文经过精简后确实会比原文短很多，那是正常的。
                # min_words = max(50, 源文去空白字符数/10)，取两者较大值作为下限。
                guard_text = result.get("pre_filter_text", tt)
                word_count = len(guard_text.split())
                src_chars = len(ch.content.replace("\n", "").replace(" ", ""))
                min_words = max(50, src_chars / 10)
                if word_count < min_words:
                    logger.warning(
                        "Sync chapter %d: TRUNCATED (%dw < %.0fw min) — skipping write",
                        ch.index, word_count, min_words,
                    )
                    failed_indexes.append(ch.index)
                    continue
                # 【中文】更新任务进度（已完成章数/总章数/当前章标题）。
                job_store.update_progress(job_id, i + 1, len(chapters_list), ch.title)
                # ZERO-CHINESE RULE: ch.title is the Chinese heading, so it must
                # not be the fallback. Fall back to a plain number instead.
                #
                # 【中文】显示用的章节标题必须是英文。ch.title 是中文原标题，
                # 不能作为兜底值（否则英文成品里会出现中文标题）。
                # 没有英文标题就用 "Chapter N" 代替。
                display_title = title_en or f"Chapter {ch.index}"
                exists = os.path.exists(output_path)
                # 【中文】把译文追加写入输出文件。每章之间用 --- 分隔。
                # flush + fsync 确保数据真正写到磁盘上，而不是还在操作系统缓冲区里。
                # 这样即使进程突然崩溃，已写的章节也不会丢。
                with open(output_path, "a" if exists else "w", encoding="utf-8") as f:
                    if not exists:
                        # Was hard-coded "English Translation" even for es-ES
                        # jobs, which mislabels a Spanish deliverable.
                        #
                        # 【中文】文件头标注语种。以前写死了 "English Translation"，
                        # 翻译成西班牙语时也标"English"，现在改成动态的 target_lang。
                        f.write(f"# {job_id} — {target_lang} Translation\n\n")
                    f.write(f"## Chapter {ch.index}: {display_title}\n\n{tt}\n\n---\n\n")
                    f.flush()
                    os.fsync(f.fileno())
                delivered_count += 1
                # ── Save checkpoint after every chapter ──
                # 【中文·每翻完一章就存档】保存当前位置、术语库快照、上一章摘要。
                # 下次恢复时从 last_idx+1 开始，术语库也恢复到当时的状态。
                with Path(ckpt_path).open("w", encoding="utf-8") as ckpt_f:
                    json.dump({
                        "last_idx": i,
                        "glossary_snapshot": agent.exact_store.snapshot(
                            target_lang=target_lang
                        ),
                        "previous_summary": prev_summary,
                    }, ckpt_f, ensure_ascii=False)
            # 【中文】所有章节翻完后，把完整的术语库导出为 JSON 文件。
            glossary_snapshot = json.dumps(
                agent.exact_store.to_dict(target_lang=target_lang), ensure_ascii=False
            )
            glossary_path = str(OUTPUT_DIR / f"{job_id}_glossary.json")
            Path(glossary_path).write_text(glossary_snapshot, encoding="utf-8")
            # Marking a job "completed" is a promise to the user that the
            # deliverable is whole. When chapters were skipped the file is not
            # whole, so report failure with the reason instead — the UI can then
            # show which chapters are missing rather than offering a broken
            # download as finished work.
            #
            # 【中文】只有所有章节都成功才标记"完成"。有失败的章节就标记"失败"，
            # 并在错误信息里列出失败的章节索引，方便用户在界面上看到缺了哪些。
            if failed_indexes:
                logger.warning(
                    "Job %s delivered %d/%d chapters; %d failed: %s",
                    job_id, delivered_count, len(chapters_list),
                    len(failed_indexes), failed_indexes[:20],
                )
                job_store.fail_job(
                    job_id,
                    f"Incomplete: {delivered_count}/{len(chapters_list)} chapters "
                    f"delivered. Failed chapter indexes: {failed_indexes[:20]}",
                )
            else:
                job_store.complete_job(job_id, output_path, len(agent.exact_store))
            # Log THIS job's real token usage and cost.
            # Previously this logged TranslationStats.token_snapshot() — a
            # process-global in-memory counter — which is why app.log contains
            # "Tokens: 0 total" whenever the translation ran outside this
            # process. The ledger is attributed per book/node/model and priced in
            # CNY at current vendor rates.
            #
            # 【中文】记录这个任务的实际 token 用量和花费。
            # 以前用的是进程级内存计数器，翻译在别的进程跑时这里读到的是 0。
            # 现在改用 token_ledger，按书/节点/模型分别记账，用人民币计价。
            try:
                job_book = getattr(agent, "book_id", "") or job_id
                cost = token_ledger.summary(job_book)
                Path(OUTPUT_DIR / f"{job_id}_cost.json").write_text(
                    json.dumps({**cost, "job_id": job_id}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info(
                    "Job %s completed. Tokens: %s in + %s out = %s, cost CNY %.4f"
                    " (%s calls, %s unmetered, %s unpriced).",
                    job_id, f"{cost['input_tokens']:,}", f"{cost['output_tokens']:,}",
                    f"{cost['input_tokens'] + cost['output_tokens']:,}",
                    cost["cost_cny"], cost["calls"], cost["unmetered_calls"],
                    cost["unpriced_calls"],
                )
            except Exception:
                logger.warning("Job %s: token ledger summary failed", job_id, exc_info=True)
        except Exception as e:
            logger.error("Sync translation failed for job %s: %s\n%s", job_id, e, _tb.format_exc())
            job_store.fail_job(job_id, str(e))
        finally:
            # 【中文】⚠️ 无论成功还是失败，都必须释放背压名额。
            # 放在 finally 里保证一定执行。忘了释放等于永久减少服务容量。
            backpressure.release()

    # 【中文】启动后台线程执行翻译。daemon=True 表示主进程退出时这个线程也跟着退，
    # 不会变成孤儿进程阻塞关闭。
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
):
    """Start translation into multiple languages simultaneously.

    Creates a project, then one job per language. All language variants
    share the same project_id so they can be grouped in the UI.

    Returns ``{project_id, filename, jobs: [{lang, job_id, status}]}``.

    【中文】上传一次小说文件，同时翻译成多种语言。
    创建一个"项目"（project）来关联所有语种的任务，方便在界面上分组查看。
    参数：
      · target_langs —— 逗号分隔的目标语言列表，默认 "en-US,es-ES,de,fr"（四种全翻）。
      · 其他参数同 /translate。
    返回：{"project_id": "...", "filename": "...", "jobs": [...]}
    """
    # ── Validate BEFORE accepting work ──
    # 【中文】和单语种一样，先校验再接活。
    text, error = await _validate_novel_upload(file)
    if error:
        status_code = 413 if "too large" in error.lower() else 400
        return JSONResponse(status_code=status_code, content={"error": error})

    # 【中文】解析逗号分隔的语言列表，去掉空白项。
    langs = [lang.strip() for lang in target_langs.split(",") if lang.strip()]
    if not langs:
        return JSONResponse(
            status_code=400, content={"error": "target_langs 不能为空"},
        )

    # ── Backpressure: each language job takes one queue slot.
    #    All-or-nothing: roll back if we cannot slot every language. ──
    # 【中文·全有或全无的背压策略】每个语种占一个名额。
    # 如果名额不够容纳所有语种，就把已经占的全部退还，返回 503。
    # 不做"部分接受"：翻 4 种语言只接受了 2 种，用户可能不知道少了 2 种，
    # 事后发现会很困惑。要么全接，要么全拒。
    accepted = 0
    for _ in langs:
        if backpressure.try_accept():
            accepted += 1
        else:
            break
    if accepted < len(langs):
        # 【中文】退还已占的名额。
        for _ in range(accepted):
            backpressure.release()
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_overloaded",
                "message": "Too many translations in progress. Try again later.",
                "queue_depth": backpressure.queue_depth,
            },
            headers={"Retry-After": "30"},
        )

    try:
        chapters = _split_by_content_type(text, content_type)
        total = len(chapters)
        filename = file.filename or "unknown.txt"

        # Create a project to group all language variants
        # 【中文】创建一个项目来关联所有语种任务。
        project_id = job_store.create_project()

        # ── Pre-load glossary preset if requested ──
        # 【中文】加载术语预设（如果有的话），所有语种共用同一份预设。
        preset_glossary_json = ""
        if glossary_preset:
            preset_glossary = job_store.load_glossary_preset(glossary_preset)
            if preset_glossary:
                preset_glossary_json = json.dumps(preset_glossary, ensure_ascii=False)

        results = []

        # 【中文】为每个语种创建一个独立的翻译任务。
        for lang in langs:
            job_id = job_store.add_language_job(project_id, lang, filename, total,
                                                content_type=content_type)

            if _has_celery:
                task = translate_novel_task.delay(
                    job_id=job_id, text=text, target_lang=lang,
                    translate_mode=translate_mode, qa_interval=qa_interval,
                    genre=genre,
                    glossary_preset_glossary=preset_glossary_json,
                    content_type=content_type,
                    api_key=api_key,
                )
                results.append({"lang": lang, "job_id": job_id, "task_id": task.id})
            else:
                # Fallback: run in a background thread (no task_id)
                # 【中文】没有 Celery 时先记录任务，后面统一用线程池启动。
                results.append({"lang": lang, "job_id": job_id})

        # If Celery is not available, spawn background threads.
        # Each thread releases exactly one slot (its own) in its finally.
        #
        # 【中文】没有 Celery 时，用线程池并行跑所有语种的翻译。
        # 每个线程在自己的 finally 里释放自己的背压名额，互不干扰。
        if not _has_celery:
            def _run_translation(lang: str, jid: str):
                """Run translation synchronously in a background thread.

                【中文】单个语种的后台翻译函数。逻辑和 _run_sync 基本相同，
                但多了 ChapterPrefetcher（章节预取器）和多语种相关的统计记录。
                """
                import asyncio as _asyncio

                from ..agent.graph import TranslationAgent
                from ..circuit_breaker import CircuitBreakerOpenError
                from ..prefetch import ChapterPrefetcher

                # 【中文】后台线程没有事件循环，需要手动创建一个。
                # TranslationAgent 内部可能用到 async 代码，必须有循环才能跑。
                loop = _asyncio.new_event_loop()
                _asyncio.set_event_loop(loop)
                try:
                    chapters_list = [c for c in chapters if c.action != ParagraphTag.SKIP]
                    agent = TranslationAgent(book_id=job_id, api_key=api_key,
                                             target_lang=lang)
                    prev_summary = ""

                    # 【中文】加载术语预设到 agent 的精确术语库。
                    if preset_glossary_json:
                        try:
                            preset_terms = json.loads(preset_glossary_json)
                            for term_cn, term_en in preset_terms.items():
                                agent.exact_store.add(
                                    term_cn, term_en, category="culture", target_lang=lang
                                )
                        except (json.JSONDecodeError, Exception):
                            pass
                    output_path = str(OUTPUT_DIR / f"{jid}_full_novel_{lang}.md")
                    ckpt_path = str(OUTPUT_DIR / f"{jid}_checkpoint.json")

                    # ── Resume from checkpoint if available ──
                    # 【中文】断点续翻：检查存档，有就从上次位置继续。
                    start_i = 0
                    if os.path.exists(ckpt_path):
                        try:
                            ckpt = json.loads(Path(ckpt_path).read_text("utf-8"))
                            start_i = ckpt.get("last_idx", -1) + 1
                            prev_summary = ckpt.get("previous_summary", "")
                            if ckpt.get("glossary_snapshot"):
                                agent.load_glossary_snapshot(
                                    ckpt["glossary_snapshot"], target_lang=lang
                                )
                            logger.info(
                                "Multi-lang sync: resuming %s from chapter %d/%d",
                                jid, start_i + 1, len(chapters_list),
                            )
                        except Exception:
                            start_i = 0

                    # 【中文】章节预取器：在翻译当前章时，提前把下一章的术语查好，
                    # 减少等待时间。只对多章的书启用（单章没必要预取）。
                    prefetcher = ChapterPrefetcher(agent.exact_store, agent.semantic_store)
                    if len(chapters_list) > 1:
                        try:
                            prefetcher.submit_next(chapters_list[1].content, lang)
                        except Exception:
                            pass

                    flash_mode = translate_mode == "flash"
                    # Per-language counters. These live in _run_translation, a
                    # different function from _run_sync, so they must be bound
                    # here — referencing the _run_sync names would NameError.
                    #
                    # 【中文】每个语种有自己的计数器。这些变量定义在 _run_translation 里，
                    # 和 _run_sync 是完全独立的函数作用域，不能互相引用。
                    delivered_count = 0
                    failed_indexes: list[int] = []
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
                                content_type=content_type,
                            )
                        except CircuitBreakerOpenError:
                            # 【中文】熔断器打开 → 记录该语种有一章失败，停止翻译。
                            TranslationStats.record_chapter_failed(lang)
                            break
                        except Exception as exc:
                            err_msg = str(exc).lower()
                            # 【中文】超时重试逻辑，和 _run_sync 相同。
                            if "timed out" in err_msg or "timeout" in err_msg:
                                logger.warning(
                                    "Multi-lang chapter %d timed out — retrying", ch.index
                                )
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
                                        content_type=content_type,
                                    )
                                except Exception as e2:
                                    logger.warning(
                                        "Multi-lang chapter %d failed after retry: %s",
                                        ch.index, e2,
                                    )
                                    TranslationStats.record_chapter_failed(lang)
                                    failed_indexes.append(ch.index)
                                    continue
                            else:
                                logger.warning("Multi-lang chapter %d failed: %s", ch.index, exc)
                                TranslationStats.record_chapter_failed(lang)
                                failed_indexes.append(ch.index)
                                continue
                        tt = result.get("translated_text", "")
                        title_en = result.get("chapter_title_en", "")
                        prev_summary = result.get("chapter_summary", "")
                        # Guard judges the UNFILTERED text (dialogue-mode
                        # deliverables are legitimately shorter).
                        #
                        # 【中文】截断检测：用过滤前的文本判断，避免误判对话模式的正常精简。
                        guard_text = result.get("pre_filter_text", tt)
                        word_count = len(guard_text.split())
                        src_chars = len(ch.content.replace("\n", "").replace(" ", ""))
                        min_words = max(50, src_chars / 10)
                        if word_count < min_words:
                            logger.warning(
                                "Multi-lang chapter %d: TRUNCATED (%dw < %.0fw min) "
                                "— skipping write",
                                ch.index, word_count, min_words,
                            )
                            TranslationStats.record_chapter_failed(lang)
                            failed_indexes.append(ch.index)
                            continue
                        job_store.update_progress(jid, i + 1, len(chapters_list), ch.title)
                        TranslationStats.record_chapter_complete(lang)
                        # ZERO-CHINESE RULE: ch.title is the Chinese heading and
                        # must not be the fallback. Note the field is named
                        # title_en even though this path serves any target
                        # language — see the generalisation work in 第11条.
                        #
                        # 【中文】显示标题必须是非中文的。注意字段名叫 title_en
                        # 但实际上对所有语种都适用（历史遗留命名）。
                        display_title = title_en or f"Chapter {ch.index}"
                        exists = os.path.exists(output_path)
                        with open(output_path, "a" if exists else "w", encoding="utf-8") as fh:
                            if not exists:
                                # Was hard-coded "English Translation" on the
                                # MULTI-LANGUAGE path, so an es-ES deliverable
                                # was mislabelled as English.
                                #
                                # 【中文】文件头用动态语种标签，不再写死 "English"。
                                fh.write(f"# {jid} — {lang} Translation\n\n")
                            fh.write(f"## Chapter {ch.index}: {display_title}\n\n{tt}\n\n---\n\n")
                            fh.flush()
                            os.fsync(fh.fileno())
                        delivered_count += 1
                        # ── Save checkpoint after every chapter ──
                        # 【中文】每章存档，和 _run_sync 相同逻辑。
                        with Path(ckpt_path).open("w", encoding="utf-8") as ckpt_f:
                            json.dump({
                                "last_idx": i,
                                "glossary_snapshot": agent.exact_store.snapshot(
                                    target_lang=lang
                                ),
                                "previous_summary": prev_summary,
                            }, ckpt_f, ensure_ascii=False)
                    # glossary_count was hard-coded 0 here, discarding the real
                    # term count. And a partially delivered job must not be
                    # marked completed. `len(store)` reads the active language,
                    # which is not this job's language here — count this
                    # language's bucket explicitly.
                    #
                    # 【中文】统计该语种的术语数量。不能用 len(store) 因为它读的是
                    # 当前激活语种的桶，而这里需要的是特定语种的桶。
                    # 以前硬编码为 0，丢弃了真实计数。
                    term_count = len(agent.exact_store.to_dict(target_lang=lang))
                    # 【中文】有失败章节就标记失败，全部成功才标记完成。
                    if failed_indexes:
                        logger.warning(
                            "Multi-lang job %s (%s) delivered %d/%d chapters; %d failed: %s",
                            jid, lang, delivered_count, len(chapters_list),
                            len(failed_indexes), failed_indexes[:20],
                        )
                        job_store.fail_job(
                            jid,
                            f"Incomplete: {delivered_count}/{len(chapters_list)} chapters "
                            f"delivered for {lang}. Failed chapter indexes: "
                            f"{failed_indexes[:20]}",
                        )
                    else:
                        job_store.complete_job(jid, output_path, term_count)
                except Exception as exc:
                    logger.error("Multi-lang sync translation failed for job %s: %s", jid, exc)
                    job_store.fail_job(jid, str(exc))
                finally:
                    # 【中文】⚠️ 每个线程释放自己的背压名额。
                    backpressure.release()

            # 【中文】用线程池并行启动所有语种的翻译。
            # max_workers=len(langs) 表示每种语言一个线程。
            # shutdown(wait=False) = fire-and-forget，不等它们完成就返回响应。
            # 背压机制保证了不会同时跑太多任务。
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
    except Exception:
        # Job setup failed after slots were accepted — give them all back.
        # 【中文】如果任务创建过程中出错，把所有已占的背压名额退还。
        for _ in langs:
            backpressure.release()
        raise


@app.get("/translate/{job_id}")
def get_translation_status(job_id: str):
    """Poll job progress — Celery backend or job_store fallback.

    【中文】查询翻译任务的进度。
    优先从 Celery 后端读取实时进度；没有 Celery 就从本地 SQLite 查。
    前端看板每隔几秒调一次这个接口来刷新进度条。
    返回：包含 status、progress 等字段的字典。
    """
    if _has_celery:
        from ..celery_app import app as celery_app
        # 【中文】Celery 把进度存在 Redis 里，key 格式是 "translation:{job_id}"。
        key = f"translation:{job_id}"
        data = celery_app.backend.get(key)
        if data:
            return json.loads(data)
    # Fallback: query job_store when Celery is unavailable
    # 【中文】没有 Celery 或 Redis 里没数据 → 从本地 SQLite 的任务表查。
    job = job_store.get_job(job_id)
    if job:
        return job
    # 【中文】两边都查不到 → 返回 "unknown"，让调用方知道这个任务号不存在或还没创建。
    return {"status": "unknown", "job_id": job_id}


@app.get("/glossary/{job_id}")
def get_glossary(job_id: str):
    """Download glossary JSON for a completed job.

    【中文】下载某个任务积累的术语库（JSON 格式）。
    术语库记录了翻译过程中学到的"中文→外文"术语对照，
    可以用来分析译名一致性，或作为下一本书的预设。
    返回 404 表示该任务还没有生成术语库（可能还没翻完）。
    """
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
    """Download the translated novel markdown.

    【中文】下载翻译完成的 Markdown 文件内容。
    会自动在所有支持的语种里查找该任务的输出文件，找到第一个就返回。
    返回：{"text": "文件内容", "target_lang": "en-US"}
    返回 404 表示没有找到任何语种的输出文件。
    """
    job_id = _safe_job_id(job_id)
    # 【中文】遍历所有支持的语种，找到第一个存在的输出文件就返回。
    # 这意味着如果一个任务翻了多种语言，只会返回最先找到的那个。
    for lang in SUPPORTED_LANGUAGES:
        path = OUTPUT_DIR / f"{job_id}_full_novel_{lang}.md"
        if path.exists():
            return {"text": path.read_text(encoding="utf-8"), "target_lang": lang}
    return JSONResponse(
        status_code=404,
        content={"error": "Translation not found", "job_id": job_id},
    )


# ── Chapter parsing helpers for EPUB generation ─────────────────────
# 【中文】下面是 EPUB 生成相关的辅助函数。EPUB 是电子书的标准格式，
# 可以把翻译好的 Markdown 打包成 Kindle/Apple Books 等阅读器能打开的文件。

# 【中文】匹配 Markdown 章节标题的正则表达式。
# 格式如 "# Chapter 1: The Beginning" 或 "## Chapter 1: The Beginning"。
# re.IGNORECASE 让 "chapter" 大小写都能匹配。
_CHAPTER_HEADER_RE = re.compile(r"^#{1,2}\s+Chapter\s+(\d+):?\s*(.*)", re.IGNORECASE)


def _parse_markdown_chapters(md_text: str) -> list[dict]:
    """Parse a translated Markdown file back into chapter dicts.

    Chapters are expected to start with ``# Chapter N: Title`` headers.
    Returns a list of ``{title, content, chapter_num}`` dicts.

    【中文】把翻译好的 Markdown 文件解析回章节列表。
    每遇到一个 "# Chapter N: ..." 标题就开始新的一章，
    标题之间的所有内容归入当前章。
    返回：[{"title": "Chapter 1: ...", "content": "正文...", "chapter_num": 1}, ...]
    """
    lines = md_text.split("\n")
    chapters = []
    current_title = None
    current_lines: list[str] = []
    current_num = 0

    for line in lines:
        m = _CHAPTER_HEADER_RE.match(line.strip())
        if m:
            # Save previous chapter
            # 【中文】遇到新章节标题时，先把上一章的内容保存起来。
            if current_title is not None:
                chapters.append({
                    "title": current_title,
                    "content": "\n".join(current_lines).strip(),
                    "chapter_num": current_num,
                })
            current_num = int(m.group(1))
            current_title = (
                f"Chapter {current_num}: {m.group(2)}"
                if m.group(2).strip() else f"Chapter {current_num}"
            )
            current_lines = []
        elif current_title is not None:
            # 【中文】不是章节标题的行 → 归入当前章的内容。
            current_lines.append(line)

    # Save last chapter
    # 【中文】循环结束后别忘了保存最后一章。
    if current_title is not None:
        chapters.append({
            "title": current_title,
            "content": "\n".join(current_lines).strip(),
            "chapter_num": current_num,
        })

    return chapters


@app.get("/epub/{job_id}")
def download_epub(job_id: str):
    """Build and return an EPUB file for a completed translation.

    【中文】把翻译好的 Markdown 打包成 EPUB 电子书文件并返回下载。
    EPUB 是通用的电子书格式，Kindle、Apple Books、微信读书等都能打开。
    流程：找到 Markdown → 解析章节 → 加载术语表 → 生成 EPUB → 返回文件。
    """
    job_id = _safe_job_id(job_id)
    from ..epub_builder import build_epub

    # ── Locate the translated Markdown file ──
    # 【中文】在所有支持的语种里查找该任务的输出文件。
    md_path = None
    target_lang = DEFAULT_TARGET_LANG
    for lang in SUPPORTED_LANGUAGES:
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
    # 【中文】尝试加载术语库，嵌入 EPUB 中供读者查阅。
    # 加载失败不影响 EPUB 生成，只是少了术语表。
    glossary = None
    glossary_path = OUTPUT_DIR / f"{job_id}_glossary.json"
    if glossary_path.exists():
        try:
            glossary = json.loads(glossary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            glossary = None

    # ── Parse chapters from the Markdown ──
    # 【中文】把 Markdown 解析回章节列表，供 EPUB 构建器使用。
    md_text = md_path.read_text(encoding="utf-8")
    chapters = _parse_markdown_chapters(md_text)

    if not chapters:
        # 【中文】解析不出任何章节 → 返回 422（Unprocessable Entity），
        # 意思是"文件存在但内容格式不对，我没法处理"。
        return JSONResponse(
            status_code=422,
            content={"error": "No chapters found in translated output", "job_id": job_id},
        )

    # ── Extract cover text (first paragraph of first chapter) ──
    # 【中文】取第一章的第一段作为封面文字（EPUB 的简介/描述）。
    first_content = chapters[0].get("content", "") if chapters else ""
    cover_text = first_content.split("\n\n")[0].strip() if first_content else ""

    # ── Build EPUB ──
    # 【中文】调用 EPUB 构建器生成文件。language_code 取语种前半部分
    # （"en-US" → "en"），因为 EPUB 标准要求 BCP 47 语言标签。
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

    # 【中文】返回文件下载响应。浏览器收到这个会自动弹出下载对话框。
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

    【中文】从断点恢复一个崩溃的翻译任务。
    需要重新上传原始小说文件（因为服务端不一定保留了原文）。
    流程：
      ① 从任务存储加载任务记录。
      ② 从 SQLite 存档表查出上次翻到第几章。
      ③ 恢复当时的术语库快照。
      ④ 重新切分上传的原文，找到剩余的章节。
      ⑤ 从第 N+1 章开始继续翻译，术语库保持连贯。
    前提：必须有 Celery（后台任务队列），否则返回 503。
    """
    if not _has_celery:
        # 【中文】断点续翻依赖 Celery 的 resume_translate_task，
        # 没有 Celery 就无法执行。返回 503 告知调用方。
        return JSONResponse(
            status_code=503,
            content={"error": "Celery worker not available", "job_id": job_id},
        )

    # ── File validation ──
    # 【中文】校验上传的文件，和新建任务时的校验逻辑相同。
    text, error = await _validate_novel_upload(file)
    if error:
        status_code = 413 if "too large" in error.lower() else 400
        return JSONResponse(status_code=status_code, content={"error": error})

    # 1. Load job
    # 【中文】从任务存储加载任务记录，确认任务存在且状态允许恢复。
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found", "job_id": job_id})

    # 【中文】只有"翻译中""排队中""失败"三种状态的任务才能恢复。
    # "完成"的任务不需要恢复，"未知"状态的任务不应该被操作。
    if job["status"] not in ("translating", "queued", "failed"):
        return JSONResponse(status_code=400, content={
            "error": f"Job has terminal status '{job['status']}' and cannot be resumed",
            "job_id": job_id,
        })

    # Resume starts new work — take a queue slot. The resume task releases
    # it on completion/failure, so accept and release stay paired.
    # 【中文】恢复翻译也算新工作，需要占一个背压名额。
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

    try:
        # 2. Read the checkpoint table to find the last completed chapter
        # 【中文】从 SQLite 存档表查出上次翻到的最大章节号和术语库快照。
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
            # 【中文】存档表还不存在（可能是第一次翻就崩了）→ 从第 0 章开始。
            pass

        # 4. Re-split to verify total chapters (content type comes from the job record)
        # 【中文】重新切分上传的原文，确认总章节数。
        content_type = job.get("content_type", "novel")
        chapters = _split_by_content_type(text, content_type)
        total = len(chapters)

        # 5. Start the resume task from chapter N+1
        # 【中文】从上次完成的下一章开始，把恢复任务丢进 Celery 队列。
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
            content_type=content_type,
        )

        # 6. Update job status back to translating
        # 【中文】把任务状态更新回"翻译中"，进度设为上次完成的位置。
        job_store.update_progress(job_id, last_chapter, total, "")

        return {
            "job_id": job_id,
            "task_id": task.id,
            "resumed_from_chapter": last_chapter,
            "next_chapter": start_chapter,
            "total_chapters": total,
            "status": "resuming",
        }
    except Exception:
        # 【中文】出错时释放背压名额，和 translate_novel 相同的保护逻辑。
        backpressure.release()
        raise


@app.get("/jobs")
def list_jobs(limit: int = 50):
    """List recent jobs, newest first.

    【中文】列出最近的翻译任务，最新的排最前。
    参数 limit 控制最多返回多少条，默认 50。
    """
    return job_store.list_jobs(limit=limit)


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    """Get a single job by id.

    【中文】根据任务号查询单个任务的详细信息（状态、进度、文件名等）。
    返回 404 表示任务号不存在。
    """
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found", "job_id": job_id})
    return job


@app.get("/projects")
def list_projects(limit: int = 20):
    """List recent multi-language projects with grouped jobs.

    【中文】列出最近的多语种翻译项目，每个项目包含其下所有语种的任务。
    参数 limit 控制最多返回多少条，默认 20。
    """
    return job_store.list_projects(limit=limit)


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    """Get a single project with all its language-variant jobs.

    【中文】查询单个多语种项目的详情，包括该项目下所有语种的任务列表。
    返回 404 表示项目不存在。
    """
    jobs = job_store.get_project_jobs(project_id)
    if not jobs:
        return JSONResponse(
            status_code=404, content={"error": "Project not found", "project_id": project_id}
        )
    return {
        "project_id": project_id,
        "filename": jobs[0].get("filename", ""),
        "jobs": jobs,
    }


@app.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    """Delete a job record.

    【中文】删除一个任务的记录。
    注意：这只删除数据库里的任务元数据，不会删除已生成的翻译文件和术语库文件。
    返回 404 表示任务不存在。
    """
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
    """Return this job's real token usage and cost.

    Read from the token ledger, which is attributed per book/node/model and
    priced with the current vendor rates in ``config.MODEL_PRICES_CNY_PER_M``.

    Previously this returned ``job_store.get_job_cost`` — a query against the
    jobs table's token columns, which nothing ever wrote (``update_token_usage``
    has no callers), so the endpoint reported 0 tokens for every job. Its
    fallback prices were also DeepSeek's ($0.14/$0.28 per M), which do not apply
    to the GLM/Qwen stack. Both vendors bill in CNY, so ``cost_cny`` is the
    authoritative figure and ``estimated_cost_usd`` is reported as ``None``
    rather than converted at an invented exchange rate.

    【中文】查询某个任务的实际 token 用量和花费（人民币）。
    数据来自 token_ledger（token 账本），按书/节点/模型分别记账，
    价格用 config.MODEL_PRICES_CNY_PER_M 里的最新单价计算。

    【中文·历史踩坑】以前这个接口查的是 jobs 表的 token 列，
    但从来没有代码往那些列里写过数据，所以永远返回 0。
    而且旧的价格是 DeepSeek 的美元价，和现在的 GLM/Qwen 人民币计价完全不搭。
    现在改为从 token_ledger 读真实数据，estimated_cost_usd 设为 None
    （不编造汇率转换）。
    """
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found", "job_id": job_id})
    s = token_ledger.summary(job_id)
    return {
        "job_id": job_id,
        "tokens_input": s["input_tokens"],
        "tokens_output": s["output_tokens"],
        "total": s["input_tokens"] + s["output_tokens"],
        "cost_cny": s["cost_cny"],
        "estimated_cost_usd": None,
        "pricing_note": (
            "cost_cny is authoritative (both vendors bill in CNY). "
            "tokens are attributed per node/model from the token ledger."
        ),
        "calls": s["calls"],
        "unmetered_calls": s["unmetered_calls"],
        "unpriced_calls": s["unpriced_calls"],
        "per_node": s["per_node"],
        "per_model": s["per_model"],
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
    """Save a completed job's glossary as a reusable preset.

    【中文】把某个已完成任务的术语库保存为可复用的"预设"。
    下次翻译新书时可以在 /translate 接口里指定这个预设名，
    让翻译从一开始就知道这些术语怎么译（"热启动"）。
    参数：
      · job_id      —— 源任务号，从它的输出目录里读取术语库。
      · name        —— 预设名称（必填）。后续用这个名字来引用。
      · description —— 预设描述（可选）。
    """
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found", "job_id": job_id})

    # Load the job's glossary
    # 【中文】从输出目录读取该任务的术语库 JSON 文件。
    # 文件不存在或解析失败就用空对象 {}，不阻断保存操作。
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

    # 【中文】把术语库存入数据库的预设表里。
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
    """List available glossary presets.

    【中文】列出所有已保存的术语预设。
    返回：{"presets": [{"preset_name": "...", "description": "..."}, ...]}
    """
    presets = job_store.list_glossary_presets()
    return {"presets": presets}


@app.get("/presets/{preset_name}")
def get_preset(preset_name: str):
    """Get a single preset's glossary.

    【中文】查看某个预设的术语内容。
    如果预设存在但术语库为空，返回空 glossary 和 term_count=0。
    如果预设根本不存在，返回 404。
    """
    glossary = job_store.load_glossary_preset(preset_name)
    if not glossary:
        # Check if preset exists at all
        # 【中文】glossary 为空有两种可能：预设不存在，或预设存在但内容为空。
        # 需要额外查一下预设列表来区分这两种情况。
        presets = job_store.list_glossary_presets()
        if not any(p["preset_name"] == preset_name for p in presets):
            return JSONResponse(
                status_code=404,
                content={"error": "Preset not found", "preset_name": preset_name},
            )
    return {"preset_name": preset_name, "glossary": glossary, "term_count": len(glossary)}


@app.delete("/presets/{preset_name}")
def delete_preset(preset_name: str):
    """Delete a glossary preset.

    【中文】删除一个术语预设。
    返回 404 表示预设不存在。
    """
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
    """Convert RTF bytes to plain UTF-8 text via macOS textutil.

    【中文】把 RTF 格式的文件内容转成纯文本。
    使用 macOS 自带的 textutil 命令行工具做转换。
    只在 macOS 上有效，Linux/Windows 上会返回 None。
    参数 raw 是文件的原始字节内容。
    返回：转换后的纯文本字符串，失败返回 None。
    """
    import subprocess
    import tempfile
    tmp_path = None
    try:
        # 【中文】textutil 只接受文件路径，不接受 stdin，
        # 所以需要先把字节写到临时文件里。
        with tempfile.NamedTemporaryFile(suffix=".rtf", delete=False) as tf:
            tmp_path = tf.name
            tf.write(raw)
            tf.flush()
        # 【中文】调用 textutil 把 RTF 转成纯文本，输出到 stdout。
        # timeout=10 防止转换卡死。
        result = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", tmp_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    finally:
        # 【中文】无论成功失败都要清理临时文件，避免磁盘空间泄漏。
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    return None


def _convert_docx(raw: bytes) -> Optional[str]:
    """Convert DOCX bytes to plain UTF-8 text.

    【中文】把 DOCX（Word 文档）的字节内容转成纯文本。
    使用 python-docx 库解析 DOCX 内部的 XML 结构，提取段落文本。
    返回：拼接后的纯文本字符串，失败返回 None。
    """
    import tempfile
    tmp_path = None
    try:
        # 【中文】python-docx 也只接受文件路径，需要先写临时文件。
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tf:
            tmp_path = tf.name
            tf.write(raw)
            tf.flush()
        from docx import Document as DocxDocument
        doc = DocxDocument(tmp_path)
        # 【中文】遍历所有段落，取非空的文本拼接起来。
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return text.strip() or None
    except Exception:
        return None
    finally:
        # 【中文】无论成功失败都清理临时文件，避免磁盘空间泄漏。
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def _convert_epub(raw: bytes) -> Optional[str]:
    """Extract plain text from EPUB bytes. Strips HTML tags, returns raw text.

    【中文】从 EPUB 字节中提取纯文本。
    EPUB 本质是 ZIP 包，里面是 HTML/XHTML 文件。
    这里解压 ZIP，找到 OEBPS 目录下的 HTML 文件，
    用正则去掉 HTML 标签和实体编码，得到纯文本。
    返回：拼接后的纯文本字符串，失败返回 None。
    """
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            # Find all XHTML/HTML content files in OEBPS/
            # 【中文】EPUB 标准规定内容文件放在 OEBPS/ 目录下。
            # 按文件名排序确保章节顺序正确。
            text_parts = []
            for name in sorted(zf.namelist()):
                if name.endswith((".xhtml", ".html", ".htm")) and "OEBPS" in name:
                    html = zf.read(name).decode("utf-8", errors="ignore")
                    # Strip HTML tags
                    # 【中文】用正则去掉 HTML 标签（<p>、<div> 等）。
                    html = __import__("re").sub(r"<[^>]+>", "", html)
                    # 【中文】把 HTML 实体（&amp;、&lt; 等）替换成空格。
                    html = __import__("re").sub(r"&\w+;", " ", html)
                    # 【中文】把连续多个空行压缩成两个换行（段落分隔）。
                    html = __import__("re").sub(r"\n{3,}", "\n\n", html)
                    if html.strip():
                        text_parts.append(html.strip())
            return "\n\n".join(text_parts) if text_parts else None
    except Exception:
        return None
