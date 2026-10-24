# 西渡 · 文档索引

> 本文档是项目**全部文字文档的单一入口**。找任何文档从这里开始。
> 状态标记：**权威**（仍在用的唯一出处）· **设计记录**（已完成方案的"为什么"）·
> **交付物**（对外产出的成品）· **活 UI**（被后端按路径加载的页面）· **归档**（历史/过期，仅留查）。

## 权威文档（还在用）

| 路径 | 说明 |
|---|---|
| [`../README.md`](../README.md) | 项目总览：是什么、架构、模型分工、验证结果、快速上手（英文，对外） |
| [`STATUS.md`](STATUS.md) | **当前状态与未完成项**：现在做到哪、还差什么、哪些要你拍板 |
| [`ENGINEERING_RULES.md`](ENGINEERING_RULES.md) | 工程铁律、方法约束、计划位次表 |
| [`DATA_MODEL.md`](DATA_MODEL.md) | 数据所有权：每个数据文件归谁管、schema 在哪、迁移怎么记 |
| [`COST_MODEL.md`](COST_MODEL.md) | 成本模型：实测 token + 官方单价 + 全书账单 |
| [`OPERATIONS.md`](OPERATIONS.md) | 部署与运维：拓扑、配置项、监控、日志、备份回滚、排障 |
| [`USER_GUIDE.md`](USER_GUIDE.md) | 客户使用手册：上传→编译→审校→导出全流程 |
| [`API.md`](API.md) | 对外 API：鉴权/限流/错误码/端点概览（完整见 /docs） |

## 设计记录（已完成方案，记录"为什么"）

| 路径 | 说明 |
|---|---|
| [`decisions/cultural-fidelity.md`](decisions/cultural-fidelity.md) | 文化保真度落地：规则层/注入层/校验层/固化层（P0–P3 已完成） |
| [`decisions/concurrency-redesign.md`](decisions/concurrency-redesign.md) | 商用并发改造：Chroma HTTP + book_id 隔离 + WAL（已落地） |
| [`decisions/style-memo-v0.17.md`](decisions/style-memo-v0.17.md) | Style Memo 增强 + 析数据接入（已随 v0.17 落地） |

## 活 UI（被 `src/main.py` 按路径加载，勿移动）

| 路径 | 说明 |
|---|---|
| [`anchor-v5.html`](anchor-v5.html) | 首页落地页（`/` 主返回） |
| [`anchor-v4.html`](anchor-v4.html) | 首页降级回退（anchor-v5 缺失时） |

## 交付物（对外成品，多版本并存未合并）

| 路径 | 说明 |
|---|---|
| [`deliverables/`](deliverables/) | PRD 4 份（.pages/.docx 多版本）+ 产品介绍 HTML + 面试 demo 讲解稿 |

## 归档（历史/过期，仅留查，勿当现状）

| 路径 | 说明 |
|---|---|
| [`../archive/handoffs/`](../archive/handoffs/) | 历史交接文档（round1–round12） |
| [`../archive/outdated/`](../archive/outdated/) | 已过期的商业化/审计文档（内容已合并进 STATUS.md） |
| [`../archive/designs-html/`](../archive/designs-html/) | 死稿 UI 设计稿（anchor-v3 及早期 dashboard/editor 等） |
| [`../archive/deliverables/`](../archive/deliverables/) | 已交付产物快照 |
| [`../archive/ab_readback_tier_20260926/`](../archive/ab_readback_tier_20260926/) | READBACK 档位 A/B 实验数据 |

## 维护约定

- 新增文档：先想清楚它属于上面哪一类，再决定放哪；同时在这里补一行。
- 文档过期：先判断有没有"仍有效"内容要抽到 `STATUS.md` 或对应权威文档，再移入 `archive/`。
- 移动文档：同步更新这里和其它文档里的引用路径。
