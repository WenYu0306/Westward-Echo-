"""Editor API — paragraph-level CRUD for post-translation human-in-the-loop editing.

Stores edits in per-job SQLite tables so they survive restarts. The CN originals
are read from the same OUTPUT_DIR and chapter-splitter pipeline used for translation.

【中文·这个文件是干什么的】
这是「段落编辑器」的后端接口。翻译完成后，人可以在浏览器里逐段修改英文译文，
改动的内容存在独立的 SQLite 数据库里，服务重启也不会丢。

【中文·对应哪个网址】
所有接口都挂在 /api/editor/ 下面，由 main.py 挂载。
前端页面在 src/editor_ui.py，地址是 /editor/{任务号}。

【中文·用户在上面能做什么】
  ① 查看章节列表和每章的中英对照段落
  ② 直接在页面上编辑英文段落（所见即所得）
  ③ 批量替换某个术语的全书译法
  ④ 导出 EPUB 电子书

【中文·它和后端怎么配合】
前端（你看到的网页）每次切换章节、保存改动、做批量替换时，
都会向后端的这些接口发请求。后端读写 SQLite 数据库，
返回 JSON 格式的结果给前端显示。

【中文·⚠️ 语言清单副本问题】
config.py 的注释提到过：editor.py 里的 SUPPORTED_LANGUAGES 曾与
routes.py 的不一致，导致两者对"该找哪个输出文件"产生分歧。
现在统一从 config.py 导入，不再各自维护副本。

【中文·本文件的接口清单】
  GET  /editor/{job_id}/chapters              → 列出所有章节
  GET  /editor/{job_id}/chapters/{chapter_num} → 获取某章的中英段落+已有编辑
  PUT  /editor/{job_id}/chapters/{chapter_num} → 保存某章的编辑
  POST /editor/{job_id}/batch-replace          → 全书批量替换术语
  GET  /editor/{job_id}/stats                  → 获取编辑统计
"""

# 【中文】re 是正则表达式模块，用来匹配章节标题格式。
# sqlite3 是 Python 内置的数据库驱动，编辑记录就存在它里面。
# threading 用来实现"每个线程一个数据库连接"，避免多线程冲突。
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

# split_chapters 把整本小说原文按章节切开，这里用来找某一章的中文原文。
from ..chapter_splitter import split_chapters

# 【中文】从统一配置导入路径和语言清单，避免和本文件里的副本不一致。
from ..config import DATA_DIR, OUTPUT_DIR, SUPPORTED_LANGUAGES, VERSION
from ..job_store import job_store
from ..migrations import migrate

# 【中文】创建独立的 FastAPI 应用，专门处理 /api/editor/* 的请求。
app = FastAPI(title="Westward Echo Editor API", version=VERSION)

# ── SQLite for edits (per-worker thread-local connection) ────────────────
# 【中文·编辑记录的数据库】每个任务的编辑改动存在 editor_edits.db 里，
# 用 SQLite 而不是内存，这样服务重启后改动还在。

EDITOR_DB_PATH = str(DATA_DIR / "editor_edits.db")
# 【中文】threading.local() 创建一个"线程本地存储"对象，
# 每个线程往里面存的东西互相看不到，这样多线程各自用自己的数据库连接，
# 不会出现两个线程同时写同一个连接导致数据错乱的问题。
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection.

    【中文】获取当前线程专属的数据库连接。
    · 返回：一个 sqlite3.Connection 对象
    · 为什么用线程本地：Celery 后台任务可能多线程并发执行，
      SQLite 不支持多线程共享同一个连接，每个线程必须有自己的。
    · WAL 模式：Write-Ahead Logging，允许读写同时进行而不互相阻塞，
      比默认的 rollback journal 模式更适合并发场景。
    · row_factory = sqlite3.Row：让查询结果可以用列名访问（row["字段名"]），
      而不是只能用数字下标（row[0]），代码更易读。
    """
    # 【中文】先检查当前线程有没有已创建的连接，有就直接复用。
    conn = getattr(_local, "conn", None)
    if conn is None:
        # 【中文】第一次使用，创建新连接并配置。
        Path(EDITOR_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(EDITOR_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn  # type: ignore[no-any-return]


# 【中文】静态 schema：所有任务共用的 edit_meta 表，通过迁移框架建。
# 动态的 edits_{job_id} 表仍在 _ensure_table 里按需建，不属于迁移
# （表名含任务号，数量不定，无法放进静态迁移清单）。
_EDIT_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS edit_meta (
    job_id TEXT PRIMARY KEY,
    last_edited_at TEXT
);
"""

_MIGRATIONS = [
    ("create_edit_meta", lambda conn: conn.executescript(_EDIT_META_SCHEMA)),
]
migrate(EDITOR_DB_PATH, _MIGRATIONS)


def _ensure_table(job_id: str):
    """Create the per-job edits table if it doesn't exist.

    【中文】确保某个任务的编辑表存在，不存在就创建。
    · 参数 job_id：任务标识，会被清洗后用作表名后缀
    · 返回：清洗后的表名字符串
    · 每个任务一张独立的表，互不干扰。表结构：
      - edits_{job_id}：存每段编辑后的文字，主键是 (章节号, 段落序号)
      - edit_meta：存每个任务最后编辑时间，用于统计和排序
    """
    # Sanitize job_id for use as table name suffix
    # 【中文】把任务号里非字母数字下划线的字符替换成下划线，防止 SQL 注入。
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", job_id)
    table = f"edits_{safe}"
    conn = _get_conn()
    # 【中文】CREATE TABLE IF NOT EXISTS：表已存在则跳过，不会报错也不会覆盖。
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{table}" (
            chapter_num INTEGER NOT NULL,
            paragraph_index INTEGER NOT NULL,
            edited_text TEXT NOT NULL,
            edited_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (chapter_num, paragraph_index)
        )
    """)
    conn.commit()
    return table


# ── Helpers ───────────────────────────────────────────────────────────────


def _load_cn_paragraphs(text: str) -> list[str]:
    """Split a chapter's Chinese text into non-empty paragraphs.

    【中文】把一章的中文原文按换行符切成段落列表，去掉空段。
    · 参数 text：一整章的中文文本
    · 返回：非空段落字符串列表
    """
    paras = text.strip().split("\n")
    return [p.strip() for p in paras if p.strip()]


def _load_en_paragraphs(text: str) -> list[str]:
    """Split a chapter's English text into non-empty paragraphs.

    【中文】把一章的英文译文按换行符切成段落列表，去掉空段。
    · 参数 text：一整章的英文文本（可能含 Markdown 格式）
    · 返回：非空段落字符串列表
    · 注意：英文 Markdown 通常用双换行分段，但为了和中文段落对齐，
      这里统一按单换行切分。
    """
    # English Markdown may use double-newline for paragraph breaks
    # but for alignment we want single-line paragraphs
    paras = text.strip().split("\n")
    return [p.strip() for p in paras if p.strip()]


def _get_en_chapter_text(job_id: str, chapter_num: int) -> Optional[str]:
    """Read a single chapter's English translation from the Markdown output.

    【中文】从翻译输出的 Markdown 文件里提取某一章的英文译文。
    · 参数 job_id：任务号，用来拼文件名
    · 参数 chapter_num：章节号（从 1 开始）
    · 返回：该章的英文文本字符串；找不到返回 None
    · 工作原理：遍历所有支持的语言，找到对应的输出文件后，
      用正则表达式匹配 "# Chapter N:" 标题来定位章节起止位置。
    · ⚠️ 注意：这里按 SUPPORTED_LANGUAGES 的顺序逐个尝试，
      找到第一个存在的文件就返回。如果某个语言的文件存在但里面没有
      对应章节标题，会跳过继续找下一个语言的文件。
    """
    for lang in SUPPORTED_LANGUAGES:
        path = OUTPUT_DIR / f"{job_id}_full_novel_{lang}.md"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            # Parse out the specific chapter
            # 【中文】用正则匹配 "# Chapter N:" 格式的章节标题行。
            pattern = re.compile(
                rf"^#\s+Chapter\s+{chapter_num}:?\s*.*$",
                re.IGNORECASE | re.MULTILINE,
            )
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            start = matches[0].end()
            # Find the next chapter header after this one
            # 【中文】找下一章的标题来确定当前章节的结束位置。
            next_pattern = re.compile(
                rf"^#\s+Chapter\s+{chapter_num + 1}:?\s*",
                re.IGNORECASE | re.MULTILINE,
            )
            next_match = next_pattern.search(text, start)
            # 【中文】如果有下一章就用它的位置做截止，否则取到文件末尾。
            end = next_match.start() if next_match else len(text)
            return text[start:end].strip()
        return None
    return None


def _get_cn_chapter(
    job_id: str, chapter_num: int, total_chapters: int
) -> Optional[str]:
    """Read the Chinese original for a specific chapter.

    Strategy: try to load from the same source used during translation.
    The job's original text may be stored or we fall back to re-splitting
    from a cached file.

    【中文】读取某一章的中文原文。
    · 参数 job_id：任务号
    · 参数 chapter_num：章节号
    · 参数 total_chapters：总章节数（当前未使用，保留以备将来优化）
    · 返回：该章的中文文本；找不到返回 None
    · 工作原理：在输出目录找 {job_id}_source.txt（翻译时保存的原文），
      用 split_chapters 重新切分后按章节号匹配。
    """
    # First, try stored original in output dir
    # 【中文】优先从输出目录里找翻译时保存的原文文件。
    src_path = OUTPUT_DIR / f"{job_id}_source.txt"
    if src_path.exists():
        text = src_path.read_text(encoding="utf-8")
        # 【中文】把整本书原文按章节切开，找到目标章节。
        chapters = split_chapters(text)
        for ch in chapters:
            if ch.index == chapter_num:
                return ch.content
    return None


# ── Endpoints ─────────────────────────────────────────────────────────────


@app.get("/editor/{job_id}/chapters")
def list_chapters(job_id: str) -> list[dict]:
    """Return all chapter numbers and titles for a completed job.

    【中文】列出某个翻译任务的所有章节。
    · 网址：GET /api/editor/{任务号}/chapters
    · 参数 job_id：任务号（在 URL 路径里）
    · 返回：章节列表，每项包含 chapter_num（章节号）和 title（标题）
    · 只有状态为 "complete" 的任务才能编辑，未完成的返回 HTTP 400。
    · 标题优先从英文译文的第一行提取（如果是 Markdown 标题格式），
      提取不到就用默认的 "Chapter N"。
    """
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})  # type: ignore[return-value]

    # 【中文】只有翻译完成的任务才允许进入编辑器。
    if job["status"] != "complete":
        return JSONResponse(  # type: ignore[return-value]
            status_code=400,
            content={"error": "Editing is only available for completed translations"},
        )

    total = job["total_chapters"]
    chapters = []
    for i in range(1, total + 1):
        chapter_title = f"Chapter {i}"
        # Try to extract title from translation if available
        # 【中文】尝试从英文译文里提取更友好的章节标题。
        en_text = _get_en_chapter_text(job_id, i)
        if en_text:
            first_line = en_text.split("\n")[0].strip()
            if first_line.startswith("#"):
                chapter_title = first_line.lstrip("#").strip()
        chapters.append({"chapter_num": i, "title": chapter_title})

    return chapters


@app.get("/editor/{job_id}/chapters/{chapter_num}")
def get_chapter(job_id: str, chapter_num: int) -> dict:
    """Return {chapter_num, title, cn_paragraphs, en_paragraphs, edits}.

    Loads the Chinese original and English translation, splits both into
    paragraphs. Also returns any saved edits for this chapter.

    【中文】获取某一章的完整编辑数据。
    · 网址：GET /api/editor/{任务号}/chapters/{章节号}
    · 返回：JSON，包含：
      - cn_paragraphs：中文原文段落列表
      - en_paragraphs：英文译文段落列表（已合并之前保存的编辑）
      - edits：该章所有已保存的编辑记录（{段落序号: 编辑后文字}）
      - title：章节标题
    · 前端拿到这些数据后，左边显示中文、右边显示英文，
      如果某段之前被编辑过，就显示编辑后的版本而不是原始译文。
    """
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})  # type: ignore[return-value]

    total = job["total_chapters"]
    if chapter_num < 1 or chapter_num > total:
        return JSONResponse(status_code=404, content={"error": "Chapter out of range"})  # type: ignore[return-value]

    # Get Chinese original
    # 【中文】读取中文原文并切成段落。
    cn_text = _get_cn_chapter(job_id, chapter_num, total)
    cn_paragraphs: list[str] = []
    if cn_text:
        cn_paragraphs = _load_cn_paragraphs(cn_text)

    # Get English translation
    # 【中文】读取英文译文并切成段落。
    en_text = _get_en_chapter_text(job_id, chapter_num)
    en_paragraphs: list[str] = []
    chapter_title = f"Chapter {chapter_num}"
    if en_text:
        en_paragraphs = _load_en_paragraphs(en_text)
        # Extract title from first line if it's a header
        # 【中文】如果第一段是 Markdown 标题，提取为章节标题并从段落列表里移除。
        first = en_paragraphs[0] if en_paragraphs else ""
        if first.startswith("#"):
            chapter_title = first.lstrip("#").strip()
            en_paragraphs = en_paragraphs[1:]  # Remove title from paragraph list

    # Get any saved edits
    # 【中文】从数据库查出该章所有已保存的编辑记录。
    table = _ensure_table(job_id)
    conn = _get_conn()
    rows = conn.execute(
        f'SELECT paragraph_index, edited_text FROM "{table}" WHERE chapter_num = ? '
        "ORDER BY paragraph_index",
        (chapter_num,),
    ).fetchall()
    edits = {row["paragraph_index"]: row["edited_text"] for row in rows}

    # Merge edits into the en_paragraphs
    # 【中文】把编辑记录合并到英文段落列表里：有编辑的用编辑版，没有的用原始译文。
    display_paragraphs = []
    for idx, para in enumerate(en_paragraphs):
        display_paragraphs.append(edits.get(idx, para))

    return {
        "chapter_num": chapter_num,
        "title": chapter_title,
        "cn_paragraphs": cn_paragraphs,
        "en_paragraphs": display_paragraphs,
        "edits": {str(k): v for k, v in edits.items()},
    }


@app.put("/editor/{job_id}/chapters/{chapter_num}")
def update_chapter(job_id: str, chapter_num: int, data: dict) -> dict:
    """Save edited English paragraphs.

    Data: {paragraphs: [{index, text}, ...]}

    Persists edits to a per-job SQLite table so they survive restarts.

    【中文】保存用户对某章英文段落的编辑。
    · 网址：PUT /api/editor/{任务号}/chapters/{章节号}
    · 参数 data：JSON 格式，包含 paragraphs 数组，每项有 index（段落序号）
      和 text（编辑后的文字）
    · 返回：{"status": "saved", "saved_count": N, "chapter_num": M}
    · 使用 INSERT OR REPLACE：如果该段落之前被编辑过就覆盖旧记录，
      没有就插入新记录。
    · 前端触发时机：用户点"保存"按钮、按 Ctrl+S、或停止输入 3 秒后自动保存。
    """
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})  # type: ignore[return-value]

    paragraphs = data.get("paragraphs", [])
    if not paragraphs:
        # 【中文】没有要保存的内容，直接返回"无变更"。
        return {"status": "no_changes", "saved_count": 0}

    table = _ensure_table(job_id)
    conn = _get_conn()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    saved = 0

    for item in paragraphs:
        idx = item.get("index", -1)
        text = item.get("text", "")
        if idx < 0:
            continue
        # 【中文】INSERT OR REPLACE：同一段落多次编辑只保留最新版本。
        conn.execute(
            f'INSERT OR REPLACE INTO "{table}" '
            "(chapter_num, paragraph_index, edited_text, edited_at) "
            "VALUES (?, ?, ?, ?)",
            (chapter_num, idx, text, now),
        )
        saved += 1

    # Update last-edited timestamp
    # 【中文】更新该任务的"最后编辑时间"元数据。
    conn.execute(
        "INSERT OR REPLACE INTO edit_meta (job_id, last_edited_at) VALUES (?, ?)",
        (job_id, now),
    )
    conn.commit()

    return {"status": "saved", "saved_count": saved, "chapter_num": chapter_num}


@app.post("/editor/{job_id}/batch-replace")
def batch_replace(job_id: str, data: dict) -> dict:
    """Apply a term replacement across all chapters.

    Data: {term_en_old, term_en_new}
    Returns the count of replacements made.

    【中文】全书批量替换某个英文术语。
    · 网址：POST /api/editor/{任务号}/batch-replace
    · 参数 data：JSON，包含 term_en_old（要查找的旧词）和 term_en_new（替换成的新词）
    · 返回：{"status": "ok", "replacements": N, ...}，N 是替换了多少处
    · 工作原理：遍历所有章节的每一段，如果该段之前被编辑过就用编辑后的版本
      做替换，否则用原始译文。替换结果作为新的编辑记录存入数据库。
    · ⚠️ 注意：这是纯文本替换，不区分大小写边界，"the" 会匹配 "other" 里的 "the"。
      用户需要自己确保查找词足够精确。
    """
    term_old = data.get("term_en_old", "")
    term_new = data.get("term_en_new", "")

    if not term_old:
        return JSONResponse(status_code=400, content={"error": "term_en_old is required"})  # type: ignore[return-value]

    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})  # type: ignore[return-value]

    table = _ensure_table(job_id)
    conn = _get_conn()
    total_replaced = 0

    # We need to apply replacements to the source EN text + saved edits
    # 【中文】遍历所有章节，对每一段执行替换。
    total = job["total_chapters"]
    for chapter_num in range(1, total + 1):
        en_text = _get_en_chapter_text(job_id, chapter_num)
        if not en_text:
            continue

        en_paragraphs = _load_en_paragraphs(en_text)
        # Remove title if present
        # 【中文】去掉 Markdown 标题行，只对正文段落做替换。
        if en_paragraphs and en_paragraphs[0].startswith("#"):
            en_paragraphs = en_paragraphs[1:]

        for idx, para in enumerate(en_paragraphs):
            # Check if there's a saved edit first
            # 【中文】优先使用已保存的编辑版本，没有才用原始译文。
            row = conn.execute(
                f'SELECT edited_text FROM "{table}" '
                "WHERE chapter_num = ? AND paragraph_index = ?",
                (chapter_num, idx),
            ).fetchone()

            current_text = row["edited_text"] if row else para
            if term_old in current_text:
                new_text = current_text.replace(term_old, term_new)
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    f'INSERT OR REPLACE INTO "{table}" '
                    "(chapter_num, paragraph_index, edited_text, edited_at) "
                    "VALUES (?, ?, ?, ?)",
                    (chapter_num, idx, new_text, now),
                )
                total_replaced += 1

    if total_replaced > 0:
        # 【中文】有替换发生时更新最后编辑时间。
        conn.execute(
            "INSERT OR REPLACE INTO edit_meta (job_id, last_edited_at) VALUES (?, ?)",
            (job_id, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
        )
    conn.commit()

    return {
        "status": "ok",
        "replacements": total_replaced,
        "term_old": term_old,
        "term_new": term_new,
    }


@app.get("/editor/{job_id}/stats")
def get_stats(job_id: str) -> dict:
    """Return {total_chapters, edited_chapters, last_edited_at}.

    【中文】获取某个任务的编辑统计信息。
    · 网址：GET /api/editor/{任务号}/stats
    · 返回：JSON，包含：
      - total_chapters：总章节数
      - edited_chapters：已被编辑过的章节数（去重计数）
      - last_edited_at：最后一次编辑的时间戳
    · 前端用途：编辑器顶栏显示"已编辑 X / Y 章"的进度。
    """
    job = job_store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})  # type: ignore[return-value]

    total = job["total_chapters"]
    table = _ensure_table(job_id)
    conn = _get_conn()

    # Count distinct chapters with edits
    # 【中文】用 COUNT(DISTINCT ...) 统计有多少个不同的章节被编辑过。
    row = conn.execute(
        f'SELECT COUNT(DISTINCT chapter_num) as cnt FROM "{table}"'
    ).fetchone()
    edited_count = row["cnt"] if row else 0

    # 【中文】从元数据表读取最后编辑时间。
    meta_row = conn.execute(
        "SELECT last_edited_at FROM edit_meta WHERE job_id = ?", (job_id,)
    ).fetchone()
    last_edited = meta_row["last_edited_at"] if meta_row else None

    return {
        "total_chapters": total,
        "edited_chapters": edited_count,
        "last_edited_at": last_edited,
    }
