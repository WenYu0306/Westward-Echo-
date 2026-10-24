"""Lightweight, named SQLite migrations shared across the project's DB files.

【中文·这个文件是干什么的】
一个极简的 SQLite 迁移框架。项目里有 4 个 SQLite 文件（jobs.db /
checkpoints.db / translation_events.db / editor_edits.db），每个文件的表结构
由各自的模块负责。过去 schema 靠散落在各模块里的 CREATE TABLE IF NOT EXISTS
和手写的 ALTER TABLE 来建，没有版本记录，改起来容易踩坑。

本框架用一张 ``schema_migrations`` 表记录「哪些迁移已经跑过」，每个迁移有
唯一名字，只跑一次。用名字而不是 ``PRAGMA user_version`` 的原因：checkpoints.db
是共享库（celery_app 建 translation_checkpoint、exact_store 建 exact_glossary），
两个模块各管各的表；user_version 是「每个库一个整数」，两个 owner 会互相打架，
用名字就能让两个模块各自注册互不冲突的迁移。

【中文·为什么不用 Alembic】
4 个 SQLite 文件、十来张表，Alembic 太重。这里只需要「按序跑一遍、幂等、
有记录」，一个文件就够了。

【中文·并发安全】
部署时 api 进程 + 2 个 celery worker 可能同时启动、同时建同一个新库。SQLite
的 WAL 模式 + 连接 timeout 已经处理了大部分锁竞争；迁移本身是幂等的
（CREATE TABLE IF NOT EXISTS / add_column_if_missing），所以两个进程同时跑同
一个迁移只是重复执行无害操作。记录用 INSERT OR IGNORE，避免两个进程同时
登记同一个迁移名触发 UNIQUE 冲突。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Sequence, Union

# A migration is a callable (conn) -> None.
Migration = Callable[[sqlite3.Connection], None]
# Named migrations: (unique name, migration callable).
NamedMigration = tuple[str, Migration]


def add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, col_type: str
) -> None:
    """Add a column if it doesn't already exist (idempotent, race-tolerant).

    【中文】安全地给表加一列：先查 PRAGMA table_info，列不存在才 ALTER。
    如果另一个进程抢先加了同一列，会抛 "duplicate column name"，
    这里捕获并当作「已迁移」忽略。
    """
    existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column in existing:
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except sqlite3.OperationalError as exc:
        if "duplicate column" in str(exc).lower():
            return  # 另一个进程已经抢先加过了
        raise


def drop_column_if_exists(conn: sqlite3.Connection, table: str, column: str) -> None:
    """Drop a column if it exists (idempotent; needs SQLite ≥ 3.35).

    【中文】安全地删一列：先查 PRAGMA table_info，列不存在则什么都不做。
    用于把「历史上加了但已无用的死列」从旧库里清掉；新库建表时直接不建该列。
    """
    existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column in existing:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def migrate(db_path: Union[str, Path], migrations: Sequence[NamedMigration]) -> None:
    """Apply pending named migrations in order, each in its own transaction.

    【中文】按顺序执行尚未跑过的迁移。每个迁移跑完立刻记入
    schema_migrations 表并提交；某个迁移失败则回滚该迁移并抛出，
    之前已提交的迁移不受影响。WAL 模式先设置（多进程并发读写需要），
    失败可忽略（WAL 一旦设置即持久，后续连接自动继承）。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass  # already WAL (persistent) or transient lock — retry later

        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')))"
        )
        applied = {
            row[0] for row in conn.execute("SELECT name FROM schema_migrations").fetchall()
        }

        for name, fn in migrations:
            if name in applied:
                continue
            try:
                fn(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)", (name,)
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    finally:
        conn.close()
