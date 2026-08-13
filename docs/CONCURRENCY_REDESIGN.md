# 西渡商用并发改造方案

> 状态：方案定稿，待实施
> 日期：2026-08-14
> 目标：让 worker 能 `replicas: N` 真并行，且多本书术语互不污染

---

## 一、为什么要做（问题定性）

西渡上云后、接第一个真实客户之前，存在一个**商用分水岭**级别的阻塞：

**当前无法多本书真并行编译。** 表面看是 `docker-compose.yml` 里 worker `--concurrency=2`、`replicas: 1`，根因有**两个**：

| # | 根因 | 现象 | 层 |
|---|------|------|-----|
| A | Chroma 嵌入式 `PersistentClient`（底层 SQLite，单进程独占） | 多 worker 进程并发访问同一 persist 目录会 SQLite 锁冲突/损坏，崩溃 | 存储层 |
| B | 术语库无 `book_id` 隔离 | 多本书同时编译时，术语互相覆盖、语义搜索串味 | 数据层 |

**A 和 B 必须一起解决。** 只改 A（Chroma 独立）会让多 worker"技术上能并行"，但 B 没改，多本书会往同一个术语池乱写，编译质量崩掉。A 解决"能并行"，B 保证"并行不出错"。

---

## 二、现状盘点（已核实）

### 术语库三个存储点

| 存储 | 文件 | 隔离键 | 多进程风险 |
|------|------|--------|-----------|
| 精确术语库 `exact_glossary` | `data/checkpoints.db` | 主键 `term_cn`，无 book_id | ❌ 无隔离，跨书覆盖 |
| 语义术语库 collection | `data/chroma/` | `terms_{lang}`，无 book_id | ❌ 无隔离 + 嵌入式单进程 |
| 翻译 checkpoint | `data/checkpoints.db` `translation_checkpoint` | job_id 有，但 SQLite 无 WAL | ⚠️ 偶发锁 |

### 关键代码位置

- `src/glossary/semantic_store.py:117` — `chromadb.PersistentClient`（嵌入式）
- `src/glossary/semantic_store.py:156` — collection 名 `terms_{target_lang.replace('-', '_')}`
- `src/glossary/exact_store.py:42` — `exact_glossary` 表，`term_cn TEXT PRIMARY KEY`
- `src/agent/graph.py:108-113` — `TranslationAgent.__init__`，`ExactGlossary()` / `SemanticGlossary()` **不传 book_id**
- `docker-compose.yml:37` — worker `--concurrency=2`，`:53` `replicas: 1`

### 现有数据

- `exact_glossary` 表 **9056 条**，全是霸总文等测试残留，非生产资产
- 三本书（无限恐怖/地府/间客）都未交付，术语可清空重来
- 析的 extraction 都在 `Analyze Echo/output/`（`地府叫我小先生_extraction.json`、`无限恐怖_extraction.json`、`间客_extraction.json`），可重新播种

---

## 三、改造方案

### 改动 1：exact_store 加 book_id 隔离

**`src/glossary/exact_store.py`**

- 表结构加 `book_id` 列，主键 `(book_id, term_cn, target_lang)`
- `__init__` 加 `book_id` 参数（默认 `"default"`，向后兼容测试）
- 所有读写方法带 `book_id`：
  - `add` / `add_batch` / `_persist_term` → 写入时带 book_id
  - `load_from_db` / `match_in_text` / `get` / `to_dict` → 只操作当前 book_id 的数据
  - `snapshot` / `restore_snapshot` → 快照只含当前 book 的术语
- `_init_db` 加 `PRAGMA journal_mode=WAL`（对齐 job_store）

**迁移**：旧表数据**备份后清空**（因为都是测试残留，且无 book_id 无法追溯）。`CREATE TABLE` 用新 schema，旧表 rename 成 `exact_glossary_legacy` 留底。

### 改动 2：semantic_store 改 book_id + HttpClient

**`src/glossary/semantic_store.py`**

- `__init__` 加 `book_id` 参数，collection 名 `terms_{book_id}_{lang}`
- client 模式改为**双态**：
  - 环境变量 `CHROMA_SERVER_URL`（或 `CHROMA_HOST`）存在 → `chromadb.HttpClient`（生产，连独立 chroma 服务）
  - 不存在 → 保留 `PersistentClient`（本地开发 / 测试 fallback）
- `get_or_create_collection`、`add_term`、`add_batch`、`search`、`count` 全部带 book_id

### 改动 3：TranslationAgent 传 book_id

**`src/agent/graph.py`**

- `__init__` 里 `ExactGlossary(book_id=book_id)`、`SemanticGlossary(book_id=book_id)`

### 改动 4：docker-compose 加 chroma 服务 + worker 可扩展

**`docker-compose.yml`**

```yaml
services:
  chroma:
    image: chromadb/chroma:latest
    volumes:
      - ./data/chroma:/chroma/chroma
      - ./data/onnx_cache:/chroma/onnx_cache   # 复用已有 ONNX 模型
    environment:
      - CHROMA_SERVER_AUTHN_CREDENTIALS=        # 内网，暂不设认证
    restart: unless-stopped

  api:
    environment:
      - CHROMA_HOST=chroma
      - CHROMA_PORT=8000

  worker:
    command: celery -A src.celery_app worker --loglevel=info --concurrency=4
    environment:
      - CHROMA_HOST=chroma
      - CHROMA_PORT=8000
    deploy:
      replicas: 2   # 改造完成后可调
```

### 改动 5：SQLite WAL 对齐

- `src/celery_app.py` 的 `_save_checkpoint` / `_load_checkpoint_translations` 连接加 `PRAGMA journal_mode=WAL`
- `src/glossary/exact_store.py` 所有连接加 WAL

---

## 四、数据迁移策略

**决策：旧数据清空，从析重新播种。** 理由：

1. 现有 9056 条是测试残留，非生产资产
2. 三本书都未交付，术语变更无外部影响
3. 旧表无 book_id，无法可靠追溯归属，回填是浪费

**步骤**：

```bash
# 1. 备份旧表
sqlite3 data/checkpoints.db ".backup data/checkpoints_backup_20260814.db"

# 2. 改造代码后，重启，新 schema 生效（旧表 rename 留底）

# 3. 从析重新播种三本书（带 book_id）
python3 scripts/seed_from_analyze.py difu
python3 scripts/seed_from_analyze.py limitless_horror
python3 scripts/seed_from_analyze.py jianke
```

**注意**：`seed_from_analyze.py` 也需要改，把 `book_id` 传给 `ExactGlossary`。

---

## 五、测试与验证

1. **单元测试**：`test_glossary.py` 加 book_id 隔离用例（两本书同名术语不互相覆盖）
2. **完整回归**：跑全部测试套件，确认 525+ 用例无回归
3. **本地并发验证**：起 2 个进程各跑一本书，验证术语不串
4. **部署验证**：服务器 `replicas: 2`，同时提交两本书，观察是否并行 + 术语正确

---

## 六、实施顺序（按依赖）

```
1. exact_store 加 book_id 隔离（改动 1）
2. semantic_store 加 book_id + HttpClient 双态（改动 2）
3. TranslationAgent 传 book_id（改动 3）
4. seed_from_analyze.py 传 book_id（改动 5 配套）
5. docker-compose 加 chroma 服务 + worker 可扩展（改动 4）
6. SQLite WAL 对齐（改动 5）
7. 数据迁移：备份 → 清空 → 重新播种
8. 测试 + 部署验证
```

---

## 七、风险与回滚

| 风险 | 缓解 |
|------|------|
| 迁移破坏旧数据 | 先 `sqlite3 .backup` 备份；旧表 rename 留底 |
| Chroma server 起不来 | 双态设计：本地开发仍可用 PersistentClient |
| embedding 模型在 chroma 容器拿不到 | 挂载 `./data/onnx_cache` 进容器，复用已有模型 |
| worker 并行后术语串味 | book_id 隔离是前提，先做改动 1/2/3 再做 replicas |

**回滚**：代码回退到改造前 commit，恢复 `checkpoints_backup_20260814.db`，docker-compose 去掉 chroma 服务、worker replicas 回到 1。

---

## 八、遗留问题（后续补）

1. **review API 未做 book_id 隔离** — `src/api/review.py:19` 仍用全局 `ExactGlossary()`（默认 book_id="default"）。审校接口 `/api/review/terms` 等不带 book_id 参数，前端 review.html 也未传。这导致审校的是 "default" 池，而非用户实际翻译的书。
   - 补齐方式：review API 加 book_id 参数（或从 job 上下文推断），前端加 book 选择器。
   - 本次改造**暂不处理**，避免扩大范围、破坏前端契约。

2. **本地 PersistentClient 与生产 HttpClient 的行为差异** — 双态设计下，本地测试走 PersistentClient（单进程无冲突），生产走 HttpClient。需在部署时用真实 chroma 服务验证一次语义搜索。

3. **chroma 容器镜像版本** — `chromadb/chroma:latest` 需在服务器上确认能拉取（国内镜像源），必要时换 `mirror.ccs.tencentyun.com` 前缀。
