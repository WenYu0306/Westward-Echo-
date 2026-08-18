# 西渡（Westward Echo）交接文档

> 最后更新：2026-08-18（Claude 窗口交接）
> 目的：让下一个窗口/agent 读完就能接手，不用重查一遍

---

## 一、当前最要紧的状态

### 1. 模型已切换：DeepSeek → Qwen（qwen-plus）

西渡已从 DeepSeek 切换到**阿里通义千问 qwen-plus**。原因：DeepSeek 涨价 + 官方 v4-flash/pro 变推理型有 bug。

- **代码已改完并提交**（commit `1edd65a`），已 push 到 GitHub
- **云服务器已同步**，验证通过（容器内实际调 Qwen 成功）
- 切换方式：`.env` 里设了 `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL` 三个覆盖变量，`src/config.py` 读它们，默认回退 DeepSeek
- Qwen base_url: `https://dashscope.aliyuncs.com/compatible-mode/v1`，model: `qwen-plus`

### 2. ⚠️ 未完成：docker-compose.yml 未提交

本地有 `docker-compose.yml` 改动**未 commit**（加了 LLM_ 三个环境变量到 api 和 worker）。这是切换 Qwen 的必要改动，下一个窗口必须提交它。

---

## 二、核心架构（快速理解西渡）

```
接入层：用户浏览器 → Caddy 网关（westwardecho.com）
  ↓
API 服务层：FastAPI（翻译/审校/编辑/CMS）
  ↓
任务调度：Celery Worker + Redis
  ↓
编译引擎（核心）：TranslationAgent = LangGraph 四节点
  READ（文化分析）→ WRITE（感官重建）→ READBACK（冷读）→ FIX（修复）
  ↓
存储层：SQLite（术语/checkpoint/job）+ Chroma（语义向量，独立HTTP）
  ↓
输出：译稿 Markdown / EPUB / 术语表 / 质检报告
```

关键文件：
- `src/agent/graph.py` — TranslationAgent 主控
- `src/agent/nodes/*.py` — 四个节点
- `src/config.py` — 模型路由 + 配置（**已改为可插拔 LLM provider**）
- `docs/architecture.html` — 架构图（可视化）

---

## 三、内容类型（西渡不只是翻译小说）

西渡支持三种 content_type：
- **novel**（网文）：已验证，男频+女频都编译过
- **script**（短剧剧本）：只编译过记忆典当行3集，验证少
- **game**（游戏对白）：零验证

genre 列表：romance_ceo / xianxia / urban / scifi / folk_religion（**缺"现言"女频大类**）

---

## 四、测试

- **530+ 测试，全绿**
- 用正确解释器跑：`/opt/homebrew/bin/python3.11 -m pytest tests/ -q`
- ⚠️ 不要用系统 `python3`（3.9），要用 homebrew 的 3.11

---

## 五、待办清单（按优先级）

### 🔴 立即要做
1. **提交 docker-compose.yml**（未 commit，加 LLM_ 环境变量）

### 🟡 有客户后做（商业化）
2. 认证（API_KEY 还是空，白嫖烧钱风险）
3. 客户隔离（job 无 user 字段）
4. 成本闸门 / 备份 / 端点校验

### 🟢 演示前做
5. 云服务器同步最新代码（服务器落后 commit，含 Qwen 切换）

### 其他已记入记忆的
- 三本书（无限恐怖/地府/间客）术语库待重建（译稿在）
- review API 没 book_id 隔离
- 前端是纯静态 HTML，方向是 agent 化（等铸文先行验证）

---

## 六、关键决策记录（避免重蹈覆辙）

1. **模型只能用指令型**（不输出 reasoning_content、守 JSON）。GLM/Kimi/正式版 DeepSeek 都是推理型，试过了不行。
2. **json_object 硬约束不能去掉**（write.py:50）——去掉会 JSON 崩，前两个窗口踩过。
3. **铸文和西渡保持解耦**（业务上下游，不自动对接）。
4. **西渡不支持 BYOK**（统一服务器 key）。
5. **改代码必须写新测试**、**能跑一遍绝不靠"应该"下结论**。

---

## 七、重要文件索引

| 找什么 | 看哪里 |
|--------|--------|
| 模型路由 | `src/config.py` |
| 商业化审计 | `docs/COMMERCIAL_AUDIT.md` |
| 没接入的能力 | `docs/UNWIRED_CAPABILITIES.md` |
| 商业化缺口计划 | `docs/COMMERCIAL_GAP_PLAN.md` |
| 并发改造方案 | `docs/CONCURRENCY_REDESIGN.md` |
| 架构图 | `docs/architecture.html` |

---

## 八、Git 状态提醒

- 工作树有 **docker-compose.yml 未提交**
- 之前 push 到了 `1edd65a`（Qwen 切换）
- 下一个窗口第一件事：提交 docker-compose.yml，然后 push

---

*这份文档是交接用的状态快照。更详细的背景在记忆目录 `/Users/wenyudemac/.claude/projects/-Users-wenyudemac-Documents/memory/` 里，尤其 `westward_echo_model_routing.md`（模型选型）和 `westward_echo_commercial_gaps.md`（商业化缺口）。*
