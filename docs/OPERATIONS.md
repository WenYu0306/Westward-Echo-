# 西渡 · 部署与运维手册

> 面向：负责部署、上线、排障的人（你自己或运维）。覆盖部署拓扑、配置项、
> 监控、日志、备份回滚、故障排查。所有字段/路径均来自 `src/config.py`、
> `docker-compose.yml`、`deploy.sh`、`src/health.py`，非凭记忆。

## 一、部署拓扑

`docker-compose.yml` 定义 4 个服务：

| 服务 | 镜像/命令 | 作用 | 暴露 |
|---|---|---|---|
| `redis` | `redis:7-alpine` | 任务队列 broker + backend（Celery 用） | 不暴露宿主端口 |
| `chroma` | `chromadb/chroma:latest` | 语义术语向量库（独立 HTTP 服务） | 内网 8000 |
| `api` | 本仓库 build | FastAPI 服务 + Web UI + JSON API | 宿主 `8000:8000` |
| `worker` | `celery -A src.celery_app worker --concurrency=2` | 异步翻译 worker | 无 |

- `api` 和 `worker` 共享宿主卷：`./data:/app/data`、`./output:/app/output`（数据持久化在宿主机）。
- Redis 与 Chroma 都**不映射宿主端口**（避免无密码暴露）。
- 本地开发（不用 Docker）只需 `python3.11 -m src.main`（单进程，不带 Celery/Redis）。

## 二、快速部署（Ubuntu 22.04）

```bash
bash deploy.sh          # 装 Docker → clone 仓库 → 生成 .env → docker compose up -d --build
```

部署后验证：

```bash
cd ~/Westward-Echo
docker compose ps                      # 服务状态
docker compose logs -f api             # 跟踪 API 日志
curl http://localhost:8000/api/health  # 健康检查
```

`deploy.sh` 会在 `.env` 缺失时交互式询问 Qwen/GLM 密钥，并自动生成随机 `API_KEY`。

## 三、配置项全表

> 全部从环境变量读取（写到 `.env`）。`src/config.py` 是唯一配置中心。

### 密钥（缺任一个都起不来）

| 变量 | 默认 | 作用 |
|---|---|---|
| `LLM_API_KEY` | 空 | Qwen 密钥（判断层 READ/READBACK/REVIEW_TERMS） |
| `GLM_API_KEY` | 空 | GLM 密钥（文字层 WRITE/FIX） |
| `API_KEY` | 空 | 网页 API 鉴权；**留空 = 开发模式免鉴权**，生产必须设 |

### 模型与思考

| 变量 | 默认 | 作用 |
|---|---|---|
| `QWEN_MODEL` | `qwen3.8-max` | Qwen 判断层模型 |
| `QWEN_THINKING_BUDGET` | `2048` | Qwen thinking token 上限 |
| `GLM_MODEL` | `glm-5.3-flash` | WRITE 文字输出（便宜 ~10 倍） |
| `GLM_FULL_MODEL` | `glm-5.3` | FIX 文字思考 |
| `GLM_WRITE_EFFORT` / `GLM_FIX_EFFORT` | `low` / `high` | GLM reasoning 档位（max 档贵约 35 倍，必须显式设） |
| `LLM_BASE_URL` | dashscope 兼容端点 | Qwen 接口 |
| `GLM_BASE_URL` | `open.bigmodel.cn/api/paas/v4` | GLM 接口 |
| `ANTHROPIC_API_KEY` | 空 | 可选，仅对抗仲裁用 |

### 存储路径

| 变量 | 默认 | 作用 |
|---|---|---|
| `XIDU_DATA_DIR` | `./data` | **所有数据**的根（SQLite/ledger/chroma/memo/log） |
| `XIDU_OUTPUT_DIR` | `./output` | **成品**根（.md 译稿、_quality.json） |
| `CHECKPOINT_DB_PATH` | `./data/checkpoints.db` | 断点续翻存档 |
| `CHROMA_PERSIST_PATH` | `./data/chroma` | 向量库持久化 |

> ⚠️ `XIDU_DATA_DIR` 改错会让生产数据"消失"（不是删除，是写到了别处）。生产别动它。

### 服务与开关

| 变量 | 默认 | 作用 |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker/backend |
| `HOST` / `API_PORT` | `0.0.0.0` / `8000` | 监听地址/端口 |
| `CORS_ORIGINS` | 空（同源） | 跨域白名单（独立前端才需要） |
| `STYLE_MEMO_ENHANCED` | `true` | 经验本 6 抽屉（false 回退 v0.16 行为） |
| `RATE_LIMIT_RPM` / `RATE_LIMIT_ENABLED` | `30` / `true` | 每分钟限流次数 |
| `HEALTH_LIVE_LLM_PROBE` | `false` | 真实 1-token LLM 探测（**花钱**，默认关） |
| `LOG_LEVEL` / `LOG_FILE` | `INFO` / `./data/app.log` | 日志级别/路径 |
| `MAX_UPLOAD_SIZE_MB` | `50` | 上传大小上限 |
| `CMS_SOURCE_TYPE` | `file` | 原文来源（`file`/`webhook`） |
| `CMS_FILE_BASE_DIR` | `./novels` | file 模式的原文目录 |

### 逐节点降档（无需改代码）

```bash
XIDU_NODE_MODEL_READBACK=qwen3.8-flash   # READBACK 单价 12/36 → 0.8/2.7
XIDU_NODE_EFFORT_FIX=low                  # 若 FIX 切到 GLM 的思考档位
```

节点名：`read` / `read_fallback` / `write` / `write_fallback` / `readback` / `readback_fallback` / `fix` / `review_terms` / `review_terms_fallback`。provider 由模型名派生（`qwen*`→Qwen、`glm*`→GLM），未知前缀直接报错。

**硬编码（非 env）**：`SUPPORTED_LANGUAGES = (en-US, es-ES, de, fr)`、`MODEL_PRICES_CNY_PER_M`（单价表，改模型必须同步加行，否则 `token_ledger` 报 `unpriced_calls`）。

## 四、健康检查与监控

- **健康检查** `GET /api/health`（无鉴权、无限流）。8 项：磁盘空间、配置、输出目录、SQLite、LLM API、内存、磁盘 I/O、Chroma。结果 `healthy` / `degraded` / `unhealthy`。
- **启动自检**：`main.py` 的 `create_app()` 启动时调用 `check_all()`，`unhealthy` 直接拒绝启动。
- **运行看板** `/dashboard`（无鉴权）+ `GET /api/dashboard/metrics`：进度、花费、错误。
- **用量分析** `/usage` + `GET /api/usage/events`：报错统计、人工编辑行为。
- **关键提示**：`HEALTH_LIVE_LLM_PROBE` 默认 `false`（看板每 5 秒轮询 `/api/health`，若开启真实探测会每分钟 12 次付费调用）。

## 五、日志

- 默认写到 `data/app.log`（`LOG_FILE`），级别 `LOG_LEVEL`。
- Docker 下：`docker compose logs -f api` / `docker compose logs -f worker`。
- 排查"翻译为何报错"：先看 worker 日志（节点实际执行在 worker 里），再看 `/usage` 页的 `translation_events` 事件。

## 六、备份与回滚

**要备份的（都在 `data/`，见 `docs/DATA_MODEL.md`）**：

| 文件 | 内容 |
|---|---|
| `jobs.db` / `checkpoints.db` / `translation_events.db` / `editor_edits.db` | 任务/存档/事件/编辑（SQLite，WAL 模式） |
| `token_ledger.jsonl` | 计费账本（追加写） |
| `chroma/` | 语义向量库 |

**安全备份（SQLite 是 WAL 模式，运行中直接 cp 可能不一致）**：

```bash
# 方式 A：停服务后整包打包（最简单、最可靠）
docker compose down
tar -czf backup-$(date +%F).tar.gz data/ output/

# 方式 B：在线备份（不停服，逐库用 sqlite3 .backup）
sqlite3 data/checkpoints.db ".backup data/checkpoints.bak"
```

**回滚**：`docker compose down` → 用备份覆盖 `data/` → `docker compose up -d --build`。

> 历史教训（`docs/STATUS.md` §五）：三本书曾清空术语库重播种；备份要在"清空/迁移"这类不可逆操作**之前**做，不要事后补。

## 七、常见故障排查

| 症状 | 先查 |
|---|---|
| 启动拒绝 | `curl /api/health`，看哪项 `error`（多半是 `LLM_API_KEY`/`GLM_API_KEY` 缺失） |
| 翻译全报错、看板却绿 | `/api/health` 的 `llm_api` 是否 `live_probe=false`（配置检查≠真实通）；临时设 `HEALTH_LIVE_LLM_PROBE=true` 复测 |
| 请求 401 | 请求头漏了 `X-API-Key`，或 `.env` 里 `API_KEY` 与调用方不一致 |
| 请求 429 | 超过 `RATE_LIMIT_RPM`（默认 30/分），看 `X-RateLimit-Reset` 等会重试 |
| 请求 503 | 背压触发：队列超 100 章，稍后再试 |
| 术语审核页看着正常但数据不生效 | 检查 `/api` 挂载顺序（`main.py` 里通配 `/api` 必须最后挂，踩过坑） |
