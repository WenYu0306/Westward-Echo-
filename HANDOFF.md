# 西渡（Westward Echo）交接文档

> 最后更新：2026-08-18（Claude 窗口交接）
> 目的：让下一个窗口/agent 读完就能接手，不用重查一遍

---

## 一、当前最要紧的状态

### 1. 模型已切换：DeepSeek → Qwen（qwen-plus）

西渡已从 DeepSeek 切换到**阿里通义千问 qwen-plus**。原因：DeepSeek 涨价 + 官方 v4-flash/pro 变推理型有 bug。

- 代码已改完并提交，已 push 到 GitHub（`1edd65a`）
- 云服务器已同步，验证通过（容器内实际调 Qwen 成功）
- 切换方式：`.env` 设 `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL` 三个覆盖变量，`src/config.py` 读它们，默认回退 DeepSeek
- Qwen base_url: `https://dashscope.aliyuncs.com/compatible-mode/v1`，model: `qwen-plus`

### 2. ⚠️ 待 push 的 commit

本地有 1 笔 commit 未 push（`1160275`，含 docker-compose LLM_ 环境变量 + HANDOFF.md）。网络好了 `git push origin main` 即可。

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
- **novel**（网文）：男频+女频都验证过
- **script**（短剧剧本）：男频（记忆典当行3集）+ 女频（禁止重生第1集）都验证过
- **game**（游戏对白）：零验证

**维度缺口**：西渡缺 `channel`（男频/女频）正交维度，只有 genre（romance_ceo/xianxia/urban/scifi/folk_religion）。当前用 urban 顶替女频也 PASS，不影响编译质量，记为待办。

---

## 四、编译验证成果（已实测）

| 内容 | 冷读结果 |
|------|---------|
| 无限恐怖（男频小说，全书） | ✅ PASS |
| 地府叫我小先生（男频小说，全书） | ✅ PASS |
| 愿你灿烂如阳（女频小说，前12章） | ✅ 4/4 PASS，issues=0 |
| 禁止重生（女频剧本，第1集） | ✅ PASS |

---

## 五、三本书译稿位置

| 书 | 译稿路径 | 状态 |
|----|---------|------|
| 无限恐怖 | `novels/output/limitless_horror_segmented/limitless_horror_en.md`（6.3M） | 完整 |
| 地府 | `novels/output/difu_segmented/difu_en.md`（14M） | 完整 |
| 间客 | `novels/output/jianke_test/jianke_test_en.md`（142K） | 只有前24章测试段 |

---

## 六、测试

- **530+ 测试，全绿**
- 用正确解释器跑：`/opt/homebrew/bin/python3.11 -m pytest tests/ -q`
- ⚠️ 不要用系统 `python3`（3.9），要用 homebrew 的 3.11

---

## 七、待办清单（按优先级）

### 🔴 立即要做
1. **push `1160275`**（docker-compose LLM_ 环境变量 + HANDOFF.md）

### 🟡 有客户后做（商业化）
2. 认证（API_KEY 空，白嫖烧钱风险）
3. 客户隔离（job 无 user 字段）
4. 成本闸门 / 备份 / 端点校验
5. game 支线零验证
6. 补 channel 维度（男频/女频）

### 🟢 演示前做
7. 云服务器同步最新代码（含 Qwen 切换）

### 其他已记入记忆的
- 三本书术语库待重建（译稿在，术语库丢了）
- review API 没 book_id 隔离
- 前端是纯静态 HTML，方向是 agent 化（等铸文先行验证）

---

## 八、关键决策记录（避免重蹈覆辙）

1. **模型只能用指令型**（不输出 reasoning_content、守 JSON）。GLM/Kimi/正式版 DeepSeek 都是推理型，试过不行。
2. **json_object 硬约束不能去掉**（write.py:50）——去掉会 JSON 崩，前两个窗口踩过。
3. **铸文和西渡保持解耦**（业务上下游，不自动对接）。
4. **西渡不支持 BYOK**（统一服务器 key）。
5. **改代码必须写新测试**、**能跑一遍绝不靠"应该"下结论**。
6. **西渡差异化 = 四节点管线设计，不是套壳模型**（深度来自管线分工，不是模型单点）。

---

## 九、重要文件索引

| 找什么 | 看哪里 |
|--------|--------|
| 模型路由 | `src/config.py` |
| 商业化审计 | `docs/COMMERCIAL_AUDIT.md` |
| 没接入的能力 | `docs/UNWIRED_CAPABILITIES.md` |
| 商业化缺口计划 | `docs/COMMERCIAL_GAP_PLAN.md` |
| 并发改造方案 | `docs/CONCURRENCY_REDESIGN.md` |
| 架构图 | `docs/architecture.html` |

---

## 十、Git 状态提醒

- 工作树干净
- 1 笔 commit 未 push（`1160275`）
- 下一个窗口第一件事：`git push origin main`

---

*更详细的背景在记忆目录 `/Users/wenyudemac/.claude/projects/-Users-wenyudemac-Documents/memory/`，尤其 `westward_echo_model_routing.md`（模型选型）、`westward_echo_differentiation.md`（差异化）、`echo_series_design_philosophy.md`（设计哲学）、`westward_echo_commercial_gaps.md`（商业化缺口）。*
