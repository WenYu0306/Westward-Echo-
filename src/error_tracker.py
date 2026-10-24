"""Error tracking & analytics — lightweight SQLite-backed event store.

Records quality-related events (guard warnings, parse fallbacks, circuit
breaker trips, empty outputs) so the team can see where problems cluster
and iterate on prompts / guard rules / model selection.

【中文·这个文件是干什么的】
这是翻译过程的"问题记录本"。每当翻译过程中发生值得注意的事情
（质量守卫发出警告、解析失败走了降级路径、熔断器跳闸、大模型返回空内容等），
就在这里记一条事件到 SQLite 数据库里。之后可以在看板上看到：
哪类问题出现最多、集中在哪些章节、哪些语言——用来指导改进提示词和模型选择。

【中文·它防止什么坏事发生】
如果没有这个记录，出了问题只能翻日志文件逐行找。2301 章跑完后，
想知道"有多少章触发了质量警告""哪种错误最常见"根本没法回答。
有了结构化的事件存储，这些问题一个 SQL 查询就能答出来。

【中文·它在整个流程里的位置】
各翻译节点在遇到异常情况时调 ``record_event()`` 记一笔。
看板和分析页面通过 ``get_event_summary()``、``get_recent_issues()``、
``get_job_health()`` 等函数读取统计数据。
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .config import DATA_DIR
from .migrations import migrate

# 【中文】事件数据库的路径。和 checkpoints.db 放在同一个 data/ 目录下。
DB_PATH = str(DATA_DIR / "translation_events.db")

# 【中文】线程本地存储。每个线程有自己的 SQLite 连接，避免多线程争抢。
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (autocommit via isolation_level=None).

    【中文】获取当前线程专属的 SQLite 连接。
    · 第一次调用时创建连接并设为 WAL 模式（允许并发读写）。
    · 后续调用直接返回缓存的连接，不重复建连。
    · isolation_level=None 表示自动提交，每条 INSERT 立即落盘。
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # 【中文】WAL（Write-Ahead Logging）模式允许多个进程同时读写，
        # 不会互相锁死。对 Celery 多工人场景很重要。
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn  # type: ignore[no-any-return]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS translation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    chapter_number INTEGER,
    event_type TEXT,
    detail TEXT,
    target_lang TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_type ON translation_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_job ON translation_events(job_id);
"""
# 【中文】表结构说明：
# · job_id：翻译任务标识符
# · chapter_number：出问题的章节号
# · event_type：事件类别（guard_warning / parse_fallback / circuit_breaker /
#   empty_output / chatter_detected / json_residue / qa_low_score）
# · detail：人类可读的详细描述
# · target_lang：目标语言
# · created_at：事件发生时间（SQLite 自动生成）
# 两个索引分别按 event_type 和 job_id 加速查询。

# Ensure schema exists at import time via the migration framework.
# 【中文】模块被 import 时就建好表。这样后续代码不需要担心表不存在。
# 通过命名迁移跑一次：新建库建表，旧库跳过（名字已记录）。
_MIGRATIONS = [
    ("create_translation_events", lambda conn: conn.executescript(_SCHEMA)),
]
migrate(DB_PATH, _MIGRATIONS)


# ── Public API ───────────────────────────────────────────────────


def record_event(
    job_id: Optional[str],
    chapter_number: Optional[int],
    event_type: str,
    detail: str,
    target_lang: str = "en-US",
) -> None:
    """Persist a single quality event to the event store.

    Args:
        job_id: Translation job identifier (may be None for system events).
        chapter_number: Chapter where the event occurred (may be None).
        event_type: Category — 'guard_warning', 'parse_fallback',
                    'circuit_breaker', 'empty_output', 'chatter_detected',
                    'json_residue', 'qa_low_score'.
        detail: Human-readable description (the actual warning / error message).
        target_lang: Target language code, default ``"en-US"``.

    【中文】记录一条质量事件到数据库。

    参数：
      · job_id：任务 ID，系统级事件可以传 None
      · chapter_number：章节号，也可以传 None
      · event_type：事件类别，目前支持的有：
        - guard_warning：质量守卫发出的警告
        - parse_fallback：解析失败走了降级路径
        - circuit_breaker：熔断器跳闸
        - empty_output：大模型返回了空内容
        - chatter_detected：检测到模型在闲聊而非翻译
        - json_residue：输出里残留了 JSON 标记
        - qa_low_score：QA 评分过低
      · detail：详细描述（实际的警告/错误消息）
      · target_lang：目标语言代码，默认 en-US
    """
    conn = _get_conn()
    conn.execute(
        """INSERT INTO translation_events
           (job_id, chapter_number, event_type, detail, target_lang)
           VALUES (?, ?, ?, ?, ?)""",
        (job_id, chapter_number, event_type, detail, target_lang),
    )
    conn.commit()


def get_event_summary(days: int = 7) -> dict:
    """Return ``{event_type: count}`` for the last N days, plus total events.

    Example:
        >>> get_event_summary(7)
        {'guard_warning': 12, 'parse_fallback': 3, 'total': 15}

    【中文】统计最近 N 天（默认 7 天）内各类事件的数量。
    返回字典里除了各 event_type 的计数外，还有一个 "total" 键是总和。
    """
    conn = _get_conn()
    rows = conn.execute(
        """SELECT event_type, COUNT(*) AS cnt
           FROM translation_events
           WHERE created_at >= datetime('now', ?)
           GROUP BY event_type
           ORDER BY cnt DESC""",
        (f"-{days} days",),
    ).fetchall()

    summary = {row["event_type"]: row["cnt"] for row in rows}
    summary["total"] = sum(summary.values())
    return summary


def get_recent_issues(limit: int = 20) -> list[dict]:
    """Return the most recent events, newest first.

    【中文】获取最近 N 条事件（默认 20 条），按时间倒序排列。
    供看板的"最近问题"列表使用。
    """
    conn = _get_conn()
    rows = conn.execute(
        """SELECT id, job_id, chapter_number, event_type, detail, target_lang, created_at
           FROM translation_events
           ORDER BY created_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_job_health(job_id: str) -> dict:
    """Per-job health: total chapters processed, chapters with warnings, warning rate.

    Returns:
        {'job_id': str, 'total_chapters': int, 'chapters_with_warnings': int,
         'warning_rate_pct': float, 'total_warnings': int}

    【中文】查看某个翻译任务的"健康度"。

    返回字段：
      · total_chapters：该任务的总章节数
      · chapters_with_warnings：出过警告的章节数
      · warning_rate_pct：警告率（百分比），越高说明问题越多
      · total_warnings：警告总条数（一章可能有多条）

    总章节数优先从 jobs 表查；查不到就用"出过事件的章节数"兜底。
    """
    conn = _get_conn()

    # Count distinct chapters that have events for this job
    # 【中文】先查该任务有多少个不同的章节出过事件。
    row = conn.execute(
        """SELECT COUNT(DISTINCT chapter_number) AS chapters_with_warnings
           FROM translation_events
           WHERE job_id = ? AND chapter_number IS NOT NULL""",
        (job_id,),
    ).fetchone()
    chapters_with_warnings = row["chapters_with_warnings"] if row else 0

    total_warnings = conn.execute(
        "SELECT COUNT(*) AS cnt FROM translation_events WHERE job_id = ?",
        (job_id,),
    ).fetchone()["cnt"]

    # Total chapters: try to get from jobs table, fall back to distinct chapters
    # 【中文】总章节数先从 jobs 表查（那是任务提交时记录的准确数字）。
    # 如果 jobs 表里没有（比如任务已被清理），就用"出过事件的章节数"兜底，
    # 这样至少能给出一个有意义的比率而不是除零错误。
    try:
        from .job_store import DB_PATH as JOBS_DB
        jconn = sqlite3.connect(JOBS_DB)
        jconn.row_factory = sqlite3.Row
        jrow = jconn.execute(
            "SELECT total_chapters FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        total_chapters = jrow["total_chapters"] if jrow else chapters_with_warnings
        jconn.close()
    except Exception:
        total_chapters = chapters_with_warnings

    warning_rate = (
        (chapters_with_warnings / total_chapters * 100)
        if total_chapters > 0
        else 0.0
    )

    return {
        "job_id": job_id,
        "total_chapters": total_chapters,
        "chapters_with_warnings": chapters_with_warnings,
        "warning_rate_pct": round(warning_rate, 1),
        "total_warnings": total_warnings,
    }


def get_editor_stats() -> dict:
    """Return editor-usage stats from editor_edits.db and checkpoints.db.

    Returns:
        {'edited_chapters': int, 'jobs_edited': int,
         'glossary_confirmed': int, 'glossary_rejected': int,
         'batch_replace_count': int}

    【中文】统计人工编辑的使用情况。

    数据来源有三个：
    ① editor_edits.db：人工改稿记录
    ② checkpoints.db 里的 exact_glossary 表：术语审核状态
    ③ editor_edits.db 里的 edit_meta 表：哪些任务被编辑过

    返回字段：
      · edited_chapters：被人工编辑过的章节总数
      · jobs_edited：被编辑过的任务数
      · glossary_confirmed / glossary_rejected：术语确认/拒绝数
      · batch_replace_count：批量替换操作次数（估算值）

    每个数据源都包在 try/except 里，某个库打不开不影响其他数据的收集。
    """
    from .config import CHECKPOINT_DB_PATH

    result = {
        "edited_chapters": 0,
        "jobs_edited": 0,
        "glossary_confirmed": 0,
        "glossary_rejected": 0,
        "batch_replace_count": 0,
    }

    # ── Editor edits ──
    # 【中文】统计所有编辑表里有多少个不同的章节被改过。
    # 编辑表按任务分表（edits_<job_id>），所以要遍历所有匹配的表名。
    from .api.editor import _get_conn as _editor_conn
    try:
        econn = _editor_conn()
        # Count all chapters that have edits across all per-job tables
        tables = econn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'edits_%'"
        ).fetchall()
        total_edits = 0
        for (tname,) in tables:
            cnt = econn.execute(
                f'SELECT COUNT(DISTINCT chapter_num) FROM "{tname}"'
            ).fetchone()[0]
            total_edits += cnt
        result["edited_chapters"] = total_edits
    except Exception:
        pass

    # Count jobs that have been edited
    # 【中文】查 edit_meta 表里有多少条记录，就是有多少个任务被编辑过。
    try:
        from .api.editor import _get_conn as _editor_conn_2
        econn2 = _editor_conn_2()
        row = econn2.execute(
            "SELECT COUNT(*) AS cnt FROM edit_meta"
        ).fetchone()
        if row:
            result["jobs_edited"] = row["cnt"]
    except Exception:
        pass

    # ── Glossary status counts ──
    # 【中文】从 checkpoints.db 的 exact_glossary 表统计术语审核状态。
    try:
        gconn = sqlite3.connect(CHECKPOINT_DB_PATH)
        gconn.row_factory = sqlite3.Row
        confirmed = gconn.execute(
            "SELECT COUNT(*) AS cnt FROM exact_glossary WHERE status = 'confirmed'"
        ).fetchone()
        result["glossary_confirmed"] = confirmed["cnt"] if confirmed else 0

        rejected = gconn.execute(
            "SELECT COUNT(*) AS cnt FROM exact_glossary WHERE status = 'rejected'"
        ).fetchone()
        result["glossary_rejected"] = rejected["cnt"] if rejected else 0
        gconn.close()
    except Exception:
        pass

    # ── Batch replace count (not tracked separately, estimate from tables) ──
    # 【中文】批量替换次数没有单独记录，这里用编辑表里的总行数来估算。
    try:
        from .api.editor import _get_conn as _editor_conn_3
        econn3 = _editor_conn_3()
        tables = econn3.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'edits_%'"
        ).fetchall()
        total_replaces = 0
        for (tname,) in tables:
            cnt = econn3.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
            total_replaces += cnt
        result["batch_replace_count"] = total_replaces
    except Exception:
        pass

    return result


def get_all_jobs_health() -> list[dict]:
    """Return health summary for all jobs that have events recorded.

    Sorted by warning rate descending (worst first).

    【中文】获取所有有事件记录的任务的健康度摘要。
    按警告率从高到低排序——问题最多的任务排在最前面，方便优先排查。
    """
    conn = _get_conn()
    rows = conn.execute(
        """SELECT DISTINCT job_id
           FROM translation_events
           WHERE job_id IS NOT NULL"""
    ).fetchall()

    results = []
    for (job_id,) in rows:
        results.append(get_job_health(job_id))

    results.sort(key=lambda x: x["warning_rate_pct"], reverse=True)
    return results
