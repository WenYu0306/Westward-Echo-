# 西渡 · 数据所有权表（Data Model & Ownership）

> 本文回答一个问题：**每个数据文件归谁管、里面有哪些表、schema 定义在哪、
> 迁移怎么记**。它是「数据所有权」的唯一权威来源，改任何一张表前先看这里。

## 总览

项目有 **4 个 SQLite 文件 + 1 个 JSONL 账本 + 1 个 Chroma 向量库**。全部落在
`DATA_DIR`（默认 `data/`，可用 `XIDU_DATA_DIR` 覆盖）下。

| 数据文件 | 类型 | 所有者模块 | 表 / 集合 |
|---|---|---|---|
| `jobs.db` | SQLite | `src/job_store.py` | `jobs`、`rejected_terms`、`glossary_presets` |
| `checkpoints.db` | SQLite（**共享**） | `src/celery_app.py` + `src/glossary/exact_store.py` | `translation_checkpoint`、`exact_glossary` |
| `translation_events.db` | SQLite | `src/error_tracker.py` | `translation_events` |
| `editor_edits.db` | SQLite | `src/api/editor.py` | `edit_meta` + 动态 `edits_{job_id}` |
| `token_ledger.jsonl` | JSONL（追加） | `src/token_ledger.py` | 每行一个 JSON 对象（无表） |
| `chroma/` | 向量库 | `src/glossary/semantic_store.py` | 语义术语嵌入（无 SQL 表） |

## 迁移机制

所有 SQLite 文件共用 `src/migrations.py` 提供的极简命名迁移框架：

- 每个迁移有唯一名字，跑过一次就记入该库的 `schema_migrations` 表，不再重复。
- 迁移按清单顺序执行，每个迁移独立事务（失败回滚该迁移、之前已提交的不受影响）。
- 用**命名迁移**而不是 `PRAGMA user_version`，因为 `checkpoints.db` 有两个 owner
  （celery_app、exact_store），`user_version` 是「每库一个整数」会互相打架。
- `migrate()` 内部统一处理 `PRAGMA journal_mode=WAL`、目录创建、并发竞争
  （`INSERT OR IGNORE` 登记 + `add_column_if_missing` 容忍重复加列）。

**给每个库加新列/新表的正确姿势**：在所有者模块的 `_MIGRATIONS` 清单末尾追加一个
命名迁移（用 `add_column_if_missing` 或 `executescript`），**不要**再手写散落的
`ALTER TABLE`。

## 各数据文件明细

### 1. `jobs.db` — 翻译任务

- **所有者**：`src/job_store.py`（`JobStore` 单例）
- **schema 位置**：`src/job_store.py` 的 `_SCHEMA` + `_MIGRATIONS`
- **迁移清单**：
  | 迁移名 | 作用 |
  |---|---|
  | `create_jobs_tables` | 建 `jobs` + `rejected_terms` + `glossary_presets` + 索引 |
  | `jobs_add_tokens_input` | 旧库补 `jobs.tokens_input` |
  | `jobs_add_tokens_output` | 旧库补 `jobs.tokens_output` |
  | `jobs_add_content_type` | 旧库补 `jobs.content_type` |

- **表**：
  - `jobs`：任务生命周期（job_id 主键、project_id、filename、target_lang、
    content_type、total/completed_chapters、status、output_path、glossary_count、
    tokens_input/output、error_message、created_at、completed_at）。
  - `rejected_terms`：人工否决的译法（term_cn, rejected_en, target_lang）。
  - `glossary_presets`：保存的术语预设（preset_name 主键、glossary_json）。

### 2. `checkpoints.db` — 翻译存档 + 术语库（**共享，两个 owner**）

- **路径**：`src/config.py` 的 `CHECKPOINT_DB_PATH`
- **⚠️ 共享库**：两个模块各自管各自的表，迁移命名互不冲突；另有读者跨模块读。

| 表 | 所有者 | 迁移名 | 说明 |
|---|---|---|---|
| `translation_checkpoint` | `src/celery_app.py` | `create_translation_checkpoint` | 每章存档（job_id, chapter_number, translated_text, glossary_snapshot, previous_summary, completed_at） |
| `exact_glossary` | `src/glossary/exact_store.py` | `exact_glossary_rename_legacy` → `create_exact_glossary` | 精确术语库，主键 (book_id, term_cn, target_lang)。`rename_legacy` 把 book_id 隔离前的旧表改名腾位 |

- **读者**（只读，不拥有 schema）：
  - `src/job_store.py::get_confirmed_terms` → 读 `exact_glossary`（取 status='confirmed' 的权威译法）
  - `src/error_tracker.py` → 读 `exact_glossary`（统计术语审核状态）

### 3. `translation_events.db` — 事件 / 问题记录

- **所有者**：`src/error_tracker.py`
- **schema 位置**：`src/error_tracker.py` 的 `_SCHEMA` + `_MIGRATIONS`
- **迁移**：`create_translation_events`
- **表**：`translation_events`（id, job_id, chapter_number, event_type, detail,
  target_lang, created_at）+ 2 个索引（event_type、job_id）。

### 4. `editor_edits.db` — 段落编辑

- **所有者**：`src/api/editor.py`
- **schema 位置**：`src/api/editor.py` 的 `_EDIT_META_SCHEMA` + `_MIGRATIONS`
- **迁移**：`create_edit_meta`
- **表**：
  - `edit_meta`（静态）：job_id 主键、last_edited_at。
  - `edits_{job_id}`（**动态，非迁移**）：每个任务一张，表名含任务号，
    列 chapter_num/paragraph_index/edited_text/edited_at，主键 (chapter_num, paragraph_index)。
    动态表在 `_ensure_table()` 里按需建，**不**进迁移清单。

### 5. `token_ledger.jsonl` — 计费账本（非 SQLite）

- **所有者**：`src/token_ledger.py`
- **路径**：`data/token_ledger.jsonl`（可用 `XIDU_TOKEN_LEDGER` 覆盖）
- **格式**：追加写，每行一个 JSON 对象（book/chapter/node/model/input/output tokens）。
  无表结构、无迁移——成本按 `config.MODEL_PRICES_CNY_PER_M` 的当前单价在读取时算。

### 6. `chroma/` — 语义向量库

- **所有者**：`src/glossary/semantic_store.py`
- **路径**：`src/config.py` 的 `CHROMA_PERSIST_PATH`
- **内容**：语义术语嵌入，供「近似术语检索」用。由 Chroma 自身管理持久化，
  不经 SQLite，也无 `schema_migrations`。
