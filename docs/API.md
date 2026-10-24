# 西渡 · 对外 API 参考

> 面向：以 API 形态集成西渡的**客户/调用方**。
> 完整机器可读契约见 **`/docs`**（Swagger UI）和 **`/openapi.json`**（OpenAPI，
> FastAPI 自动生成）。本文只讲鉴权、限流、错误码和端点概览。

## 一、鉴权

- 请求头 `X-API-Key: <你的密钥>`。
- 密钥由部署方在 `.env` 的 `API_KEY` 配置；**留空 = 开发模式免鉴权**（生产不会这样）。
- 密钥错误或缺失 → **HTTP 401**。
- 对比用常数时间比较（防时序侧信道）。

## 二、限流与背压

- **限流**：令牌桶，默认 `RATE_LIMIT_RPM=30` 次/分钟。超额 → **HTTP 429**，响应头 `X-RateLimit-Reset` 给出可重试时间。读方法（GET）不限流。
- **背压**：队列超过约 100 章时拒绝新任务 → **HTTP 503**（"稍后再试"）。

## 三、错误码速查

| 码 | 含义 | 处理 |
|---|---|---|
| 401 | 缺/错 `X-API-Key` | 补上正确密钥 |
| 422 | 请求体校验失败 | 看响应里的字段错误 |
| 429 | 超限流 | 等 `X-RateLimit-Reset` 再试 |
| 503 | 背压/过载 | 稍后重试 |
| 500 | 服务端异常 | 联系部署方，附 job_id |

## 四、端点概览

### 编译

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/api/translate` | 提交单本书编译 |
| POST | `/api/translate/multi` | 批量提交 |
| POST | `/api/translate/resume/{job_id}` | 从断点续传 |

### 结果查询

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/translate/{job_id}` | 编译状态 |
| GET | `/api/translation/{job_id}` | 译稿输出 |
| GET | `/api/glossary/{job_id}` | 术语表 |
| GET | `/api/epub/{job_id}` | 导出 EPUB |

### 任务与项目管理

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/jobs` | 任务列表 |
| GET | `/api/jobs/{job_id}` | 任务详情 |
| GET | `/api/jobs/{job_id}/cost` | 任务花费 |
| DELETE | `/api/jobs/{job_id}` | 删除任务 |
| GET | `/api/projects` / `/api/projects/{project_id}` | 项目列表/详情 |
| GET/POST/DELETE | `/api/presets*` | 术语预设 |

### 人工参与

| 方法 | 路径 | 作用 |
|---|---|---|
| * | `/api/review/*` | 术语确认/拒绝/反馈 |
| * | `/api/editor/*` | 章节读/写/批量替换 |

### 内容管理与观测

| 方法 | 路径 | 作用 |
|---|---|---|
| * | `/api/cms/*` | 导入原文、发布成品 |
| GET | `/api/health` | 健康检查（无鉴权） |
| GET | `/api/dashboard/metrics` | 看板指标（无鉴权） |
| GET | `/api/usage/events` | 用量/报错事件（无鉴权） |

## 五、一个最小调用示例

```bash
curl -X POST http://<host>:8000/api/translate \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"filename": "novel.txt", "target_lang": "en-US", "content_type": "novel"}'
```

> 请求体字段以 `/docs` 里的 OpenAPI schema 为准（`target_lang` ∈ `en-US`/`es-ES`/`de`/`fr`；
> `content_type` ∈ `novel`/`script`/`game`）。
