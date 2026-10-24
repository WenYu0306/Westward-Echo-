"""SQLite-backed job store for translation job persistence.

Each translation run is a "job" tracked from creation through progress
updates to completion or failure.  Jobs persist across server restarts.

【中文·任务存储模块】
一、这个文件是干什么的？
    管理翻译任务的生命周期：创建 → 进度更新 → 完成/失败。
    所有数据存在 SQLite 数据库（jobs.db）里，服务器重启也不丢。

二、它在整个流程里的位置：
    · API 层（routes.py）调用这里创建任务、查询进度
    · Celery 后台任务调用这里更新进度和状态
    · Dashboard 界面调用这里展示任务列表
    · 还管理术语预设（glossary presets）和被拒绝术语的记录

三、核心概念：
    · 任务（job）：一次完整的翻译运行，包含书名、目标语言、总章数等
    · 项目（project）：多语言翻译时的分组单位，同一本书翻成多种语言
      共享一个 project_id
    · 术语预设（preset）：把某次翻译积累的术语库保存下来，
      下次翻另一本书时可以直接加载，不用从零积累
    · 被拒绝术语（rejected terms）：人工审核时否决的译法，
      AI 后续不会再提议这些译法

四、本文件的结构：
    · _get_conn()     —— 获取线程安全的数据库连接
    · _SCHEMA         —— 三张表的建表语句
    · JobStore 类     —— 对外提供全部 CRUD 操作
    · job_store       —— 模块级单例，外部直接用
"""

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import DATA_DIR
from .migrations import add_column_if_missing, drop_column_if_exists, migrate

# 【中文】数据库文件路径：data/jobs.db
DB_PATH = str(DATA_DIR / "jobs.db")

# 【中文】线程本地存储：每个线程有自己的数据库连接，避免多线程竞争。
# threading.local() 就像一个每人一个的储物柜，
# 每个线程往里放自己的 conn，互不干扰。
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (autocommit via isolation_level=None).

    【中文】获取当前线程的 SQLite 数据库连接。
    · 第一次调用时创建连接并存到线程本地存储
    · 后续调用直接复用已有连接
    · isolation_level=None 表示自动提交模式（每条 SQL 立即生效）
    · WAL 模式允许多个进程同时读写同一个数据库文件
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row  # 让查询结果可以用列名访问
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn  # type: ignore[no-any-return]


# 【中文】数据库表结构定义（三张表）。
# ⚠️ 注意：这里的 SQL 使用 CHECK 约束限制了 content_type 和 status 的取值范围。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id              TEXT PRIMARY KEY,
    project_id          TEXT,
    filename            TEXT NOT NULL,
    target_lang         TEXT NOT NULL,
    content_type        TEXT NOT NULL DEFAULT 'novel'
                        CHECK(content_type IN ('novel')),
    total_chapters      INTEGER NOT NULL,
    completed_chapters  INTEGER NOT NULL DEFAULT 0,
    current_chapter_title TEXT,
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued','translating','complete','failed')),
    output_path         TEXT,
    glossary_count      INTEGER,
    error_message       TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_project_id ON jobs(project_id);

CREATE TABLE IF NOT EXISTS rejected_terms (
    term_cn     TEXT NOT NULL,
    rejected_en TEXT NOT NULL,
    target_lang TEXT NOT NULL DEFAULT 'en-US',
    rejected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (term_cn, rejected_en, target_lang)
);

CREATE TABLE IF NOT EXISTS glossary_presets (
    preset_name         TEXT PRIMARY KEY,
    description         TEXT,
    created_from_job_id TEXT,
    glossary_json       TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ── Migrations ────────────────────────────────────────────────
# 【中文】按顺序的命名迁移。迁移 1 建当前 schema（对已存在的库是 no-op）；
# 迁移 2 给「比 content_type 更早创建的旧库」补列；迁移 3 清掉历史死列
# tokens_input/tokens_output（update_token_usage 早已删除、无人写入，永远为 0）。
def _drop_tokens_columns(conn):
    drop_column_if_exists(conn, "jobs", "tokens_input")
    drop_column_if_exists(conn, "jobs", "tokens_output")


_MIGRATIONS = [
    ("create_jobs_tables", lambda conn: conn.executescript(_SCHEMA)),
    ("jobs_add_content_type", lambda conn: add_column_if_missing(
        conn, "jobs", "content_type", "TEXT NOT NULL DEFAULT 'novel'")),
    ("drop_jobs_tokens_columns", _drop_tokens_columns),
]


class JobStore:
    """Persistent store for translation jobs.

    【中文】翻译任务的持久化存储类。
    对外提供：
    · 任务 CRUD：create_job / get_job / list_jobs / delete_job
    · 进度更新：update_progress / complete_job / fail_job
    · 项目管理：create_project / add_language_job / get_project_jobs
    · 术语预设：save_glossary_as_preset / load_glossary_preset / ...
    · 被拒绝术语：reject_term_with_feedback / get_rejected_terms
    · 已确认术语：get_confirmed_terms（从 exact_glossary 表读取）
    """

    def __init__(self):
        """Ensure the schema exists on first use.

        【中文】初始化时确保表结构存在。通过迁移框架按序跑命名迁移：
        新建库从零建表，旧库自动补缺失的列，已跑过的迁移跳过。
        """
        migrate(DB_PATH, _MIGRATIONS)

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _now() -> str:
        """【中文】返回当前 UTC 时间的字符串表示，用于记录时间戳。"""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """【中文】把 SQLite 行对象转成普通字典。"""
        return dict(row)

    # ── public API ───────────────────────────────────────────

    def create_job(
        self,
        filename: str,
        target_lang: str,
        total_chapters: int,
        content_type: str = "novel",
        project_id: Optional[str] = None,
    ) -> str:
        """Create a new job record and return its job_id.

        【中文】创建一个新的翻译任务。
        · 参数 filename：源文件名
        · 参数 target_lang：目标语言代码（如 "en-US"）
        · 参数 total_chapters：总章节数
        · 参数 content_type：内容类型，目前只支持 "novel"
        · 参数 project_id：所属项目 ID（多语言翻译时使用）
        · 返回：8 位 UUID 作为任务 ID
        · 初始状态为 "queued"（排队中）
        """
        job_id = str(uuid.uuid4())[:8]
        conn = _get_conn()
        conn.execute(
            """INSERT INTO jobs (job_id, project_id, filename, target_lang, content_type, \
total_chapters, status)
               VALUES (?, ?, ?, ?, ?, ?, 'queued')""",
            (job_id, project_id, filename, target_lang, content_type, total_chapters),
        )
        conn.commit()
        return job_id

    def update_progress(
        self,
        job_id: str,
        current: int,
        total: int,
        chapter_title: str,
    ):
        """Update progress counters and status to 'translating'.

        【中文】更新任务进度。
        · 参数 job_id：任务 ID
        · 参数 current：已完成章节数
        · 参数 total：总章节数（可能因切分而变化）
        · 参数 chapter_title：当前正在翻译的章节标题
        · 同时将状态改为 "translating"（翻译中）
        """
        conn = _get_conn()
        conn.execute(
            """UPDATE jobs
               SET completed_chapters = ?,
                   total_chapters = ?,
                   current_chapter_title = ?,
                   status = 'translating'
               WHERE job_id = ?""",
            (current, total, chapter_title, job_id),
        )
        conn.commit()

    def complete_job(
        self,
        job_id: str,
        output_path: str,
        glossary_count: int,
    ):
        """Mark a job as complete.

        【中文】标记任务为完成。
        · 参数 output_path：输出文件路径
        · 参数 glossary_count：最终术语库大小
        · 同时记录完成时间
        """
        conn = _get_conn()
        conn.execute(
            """UPDATE jobs
               SET status = 'complete',
                   output_path = ?,
                   glossary_count = ?,
                   completed_at = ?
               WHERE job_id = ?""",
            (output_path, glossary_count, self._now(), job_id),
        )
        conn.commit()

    def fail_job(self, job_id: str, error_message: str):
        """Mark a job as failed with an error message.

        【中文】标记任务为失败，并记录错误信息。"""
        conn = _get_conn()
        conn.execute(
            """UPDATE jobs
               SET status = 'failed',
                   error_message = ?,
                   completed_at = ?
               WHERE job_id = ?""",
            (error_message, self._now(), job_id),
        )
        conn.commit()

    # ── token / cost accounting ──────────────────────────────────────
    #
    # update_token_usage() and get_job_cost() were deleted here. update_token_usage
    # had ZERO callers in the entire codebase, so the jobs table's tokens_input /
    # tokens_output columns were permanently 0; get_job_cost then read those
    # always-zero columns and priced them at DeepSeek's $0.14/$0.28 per M — a
    # provider abandoned in 2026-09. The result was an endpoint that always
    # reported "$0.0000" for every job.
    #
    # The columns are left in the schema (dropping them needs a migration, and
    # _row_to_dict returns dict(row) so their presence is harmless). They are
    # inert. Authoritative per-job usage and cost come from
    # src/token_ledger.py: token_ledger.summary(book_id), which records the real
    # model per call and prices it in CNY from config.MODEL_PRICES_CNY_PER_M.
    # GET /api/jobs/{job_id}/cost already reads that, not these columns.
    #
    # 【中文】token 用量和费用计算说明：
    # 这里的 update_token_usage() 和 get_job_cost() 已被删除。
    # 原因：update_token_usage 在整个代码库里没有任何调用者，
    # 导致 tokens_input/tokens_output 永远是 0；
    # get_job_cost 读这两个永远为 0 的列，还用已废弃的 DeepSeek 美元单价算钱，
    # 结果每个任务都报 "$0.0000"。
    # 列保留在表结构里（删列需要迁移，且不影响功能），但已无用。
    # 真正的用量和费用来自 src/token_ledger.py。

    # ── glossary presets (translation memory across books) ──

    def save_glossary_as_preset(
        self, job_id: str, preset_name: str, description: str = "", glossary_json: str = ""
    ):
        """Save a completed job's glossary as a reusable preset.

        If glossary_json is omitted, the preset is created empty — callers
        should populate it after creating the preset.

        【中文】把完成任务的术语库保存为可复用的预设。
        · 参数 job_id：来源任务 ID
        · 参数 preset_name：预设名称（唯一标识）
        · 参数 description：描述文字
        · 参数 glossary_json：术语库 JSON 字符串
        · 同名预设会被覆盖（INSERT OR REPLACE）
        """
        conn = _get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO glossary_presets
               (preset_name, description, created_from_job_id, glossary_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (preset_name, description, job_id, glossary_json, self._now()),
        )
        conn.commit()

    def load_glossary_preset(self, preset_name: str) -> dict:
        """Load a preset glossary. Returns {term_cn: term_en} or empty dict.

        【中文】加载术语预设，返回 {中文: 译文} 字典。
        找不到或解析失败时返回空字典。
        """
        conn = _get_conn()
        row = conn.execute(
            "SELECT glossary_json FROM glossary_presets WHERE preset_name = ?",
            (preset_name,),
        ).fetchone()
        if not row:
            return {}
        try:
            import json as _json
            return _json.loads(row["glossary_json"]) or {}
        except Exception:
            return {}

    def load_glossary_preset_raw(self, preset_name: str) -> Optional[str]:
        """Return the raw JSON string of a preset, or None if not found.

        【中文】返回预设的原始 JSON 字符串，找不到返回 None。
        与 load_glossary_preset 的区别：这个不解码，直接返回原始字符串。
        """
        conn = _get_conn()
        row = conn.execute(
            "SELECT glossary_json FROM glossary_presets WHERE preset_name = ?",
            (preset_name,),
        ).fetchone()
        return row["glossary_json"] if row else None  # type: ignore[no-any-return]

    def list_glossary_presets(self) -> list[dict]:
        """List all available presets.

        【中文】列出所有可用的术语预设，按创建时间倒序排列。"""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT preset_name, description, created_from_job_id, created_at "
            "FROM glossary_presets ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete_glossary_preset(self, preset_name: str):
        """Remove a glossary preset.

        【中文】删除指定的术语预设。"""
        conn = _get_conn()
        conn.execute("DELETE FROM glossary_presets WHERE preset_name = ?", (preset_name,))
        conn.commit()

    def get_job(self, job_id: str) -> Optional[dict]:
        """Return a single job by id, or None.

        【中文】按 ID 查询单个任务，找不到返回 None。"""
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[dict]:
        """Return recent jobs, newest first.

        【中文】列出最近的任务，按创建时间倒序，默认最多 50 条。"""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete_job(self, job_id: str):
        """Remove a job record.

        【中文】删除指定的任务记录。"""
        conn = _get_conn()
        conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        conn.commit()

    def get_incomplete_jobs(self) -> list[dict]:
        """Return jobs with status 'translating' that may need recovery.

        【中文】查找状态为 "translating" 的任务。
        这些任务可能在服务器重启前中断了，需要恢复处理。
        """
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'translating' ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── project / multi-language ────────────────────────────

    def create_project(self, source_filename: str) -> str:
        """Create a project grouping for multi-language translations.

        Returns a project_id that all language variants share.

        【中文】创建一个多语言翻译项目。
        · 参数 source_filename：源文件名
        · 返回：8 位 UUID 作为项目 ID
        · 同一本书翻成多种语言时，各语言任务共享这个 project_id
        """
        return str(uuid.uuid4())[:8]

    def add_language_job(self, project_id: str, target_lang: str,
                        filename: str, total_chapters: int,
                        content_type: str = "novel") -> str:
        """Create a job within a project for a specific target language.

        【中文】在项目下创建一个特定语言的翻译任务。
        内部就是调 create_job 并关联 project_id。
        """
        return self.create_job(filename, target_lang, total_chapters,
                              content_type=content_type, project_id=project_id)

    def get_project_jobs(self, project_id: str) -> list[dict]:
        """Return all jobs (language variants) belonging to a project.

        【中文】获取某个项目下的所有任务（各语言版本），按语言排序。"""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM jobs WHERE project_id = ? ORDER BY target_lang",
            (project_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_projects(self, limit: int = 20) -> list[dict]:
        """Return recent projects with their language-variant jobs grouped.

        Each entry: {project_id, filename, created_at, jobs: [{lang, job_id, status, ...}]}

        【中文】列出最近的项目及其下属的各语言任务。
        · 参数 limit：最多返回多少个项目，默认 20
        · 返回：项目列表，每个含 project_id、filename、created_at
          和 jobs（该项目下所有语言任务的列表）
        · 按项目内最新任务的创建时间倒序排列
        """
        conn = _get_conn()
        # Get distinct projects, ordered by most recent job creation
        # 【中文】先查出所有不同的项目，按最新任务时间排序
        rows = conn.execute(
            """SELECT project_id, filename, MAX(created_at) AS created_at
               FROM jobs
               WHERE project_id IS NOT NULL
               GROUP BY project_id
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

        projects = []
        for row in rows:
            pid = row["project_id"]
            # 对每个项目，查出它下面的所有任务
            job_rows = conn.execute(
                "SELECT * FROM jobs WHERE project_id = ? ORDER BY target_lang",
                (pid,),
            ).fetchall()
            projects.append({
                "project_id": pid,
                "filename": row["filename"],
                "created_at": row["created_at"],
                "jobs": [self._row_to_dict(j) for j in job_rows],
            })
        return projects

    # ── rejected terms ──────────────────────────────────────

    def reject_term_with_feedback(self, term_cn: str, rejected_en: str,
                                  target_lang: str = "en-US"):
        """Record a rejected translation so the Agent can avoid it.

        Called by the /review reject endpoint when a human reviewer
        rejects a glossary term.  The Agent reads this table before
        each translation so it never re-proposes the same bad term.

        【中文】记录一条被人工否决的术语译法。
        · 参数 term_cn：中文原词
        · 参数 rejected_en：被否决的译文
        · 参数 target_lang：目标语言
        · AI 在每次翻译前会读这张表，避免再次提议被否决的译法
        · 同一条记录重复插入会被覆盖（INSERT OR REPLACE）
        """
        conn = _get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO rejected_terms
               (term_cn, rejected_en, target_lang, rejected_at)
               VALUES (?, ?, ?, ?)""",
            (term_cn, rejected_en, target_lang, self._now()),
        )
        conn.commit()

    def get_rejected_terms(self, target_lang: str = "en-US") -> list[dict]:
        """Return all rejected translations for injection into prompts.

        【中文】获取指定语言的所有被否决术语，供注入 AI 提示词。
        按否决时间倒序排列。
        """
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT term_cn, rejected_en, rejected_at, target_lang
               FROM rejected_terms
               WHERE target_lang = ?
               ORDER BY rejected_at DESC""",
            (target_lang,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_confirmed_terms(self, target_lang: str = "en-US", book_id: str = "default") -> dict:
        """Return only confirmed terms (higher authority than auto-extracted).

        These are terms a human reviewer has explicitly approved.
        They should NEVER be overwritten by the Agent and MUST be
        used as-is in translations.

        Returns {} when the glossary table does not exist yet. That table is
        created by glossary/exact_store.py, not by this module, so it is absent
        on a fresh database until the first ExactGlossary is constructed — and
        write_node calls this before that is guaranteed. "No human-confirmed
        terms yet" is a legitimate state, not an error: the old code raised
        sqlite3.OperationalError there, which propagated out of the LangGraph
        task and failed the chapter.

        【中文】获取人工确认过的术语（权威度高于自动提取的）。
        · 参数 target_lang：目标语言代码
        · 返回：{中文: 译文} 字典
        · ⚠️ 这个方法读的是 exact_glossary 表（由 exact_store.py 创建），
          不是本模块的 jobs.db。如果那张表还不存在（全新数据库），
          返回空字典而不是报错——旧代码在这里抛异常导致整章翻译失败。
        · 这些术语绝对不能被 AI 覆盖，必须原样使用。
        """
        from .config import CHECKPOINT_DB_PATH
        conn = sqlite3.connect(CHECKPOINT_DB_PATH)
        try:
            # 先检查表是否存在，不存在就直接返回空字典
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='exact_glossary'"
            ).fetchone()
            if not exists:
                return {}
            rows = conn.execute(
                """SELECT term_cn, term_en
                   FROM exact_glossary
                   WHERE book_id = ? AND status = 'confirmed' AND target_lang = ?""",
                (book_id, target_lang),
            ).fetchall()
            return {row[0]: row[1] for row in rows}
        finally:
            conn.close()


# Module-level singleton
# 【中文】模块级单例：整个进程共享一个 JobStore 实例。
job_store = JobStore()

