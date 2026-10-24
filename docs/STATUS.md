# 西渡 · 当前状态与未完成项

> 本文是"西渡现在做到哪、还差什么、哪些要你拍板"的**唯一权威出处**。
> 取代并合并了此前散落的三份过期文档（`COMMERCIAL_AUDIT` / `COMMERCIAL_GAP_PLAN` /
> `UNWIRED_CAPABILITIES`，均已归档至 `archive/outdated/`）与第 12 轮交接的
> `§B 未完成项总账`（归档至 `archive/handoffs/`）。
> 与 `README.md` 的 "What's Not Here Yet" 一节去重后合并于此。

## 一、当前版本与已落地

- **VERSION `0.17.0`**（`src/config.py`）。双 provider 分工：判断层（READ/READBACK/REVIEW_TERMS）走 Qwen `qwen3.8-max`，文字层 WRITE 走 GLM `glm-5.3-flash`、FIX 走 `glm-5.3`。
- 已交付两本全书编译验证：《无限恐怖》775 章、《地府叫我小先生》2301 章，冷读全 PASS、0 崩溃。
- 工程质量基线：1840 测试全绿；ruff / mypy / vulture 死代码门禁已接通。

## 二、未完成项总账（承接第 12 轮 §B）

> 分类沿用原交接口径；每条标注"要谁行动"。

| 类别 | 内容 | 行动方 |
|---|---|---|
| 已判读、待决策 | READBACK 降档 **暂不采纳**（判据方向相反、校正后不显著）。若要再验需加大基线章数（≥40）或先给管线加 seed | **你拍板** |
| 需单独批预算 | B 档 6 章重译 ¥3.72（未执行；要用 `index == EN章号` 定位章） | **你批准** |
| 低成本可做 | ① `ab_readback_tier` 变异测试**重测**（口径修正前 129/176 已失效）② 试算模型缺陷：A/B 两组 token 数相同假设错误，flash 档 out 多 12.1% | 可自行做 |
| 需预算、金额未算 | 条款2 端到端+西语启动；条款8 Qwen-MT 嵌入评估；条款7 不降质降成本 | 先报价等你批 |
| 需定方向 | ① 条款6「写库」侧闸门（反转 `CHECKPOINT_DB_PATH` 默认值 + `docker-compose.yml` 缺 GLM 透传）② 条款3 模型变更质量回归（需单独设计预算） | **你定方向** |
| 建议单独立项 | 条款10 严格盲读泄漏：实测 58/58 章 `cold_read_context` 含中文源名（`graph.py` `_build_cold_read_context` 第 3 段透传 `style_memo.read_all()` 原文） | **你定是否立项** |

## 三、已声明能力但未接入主流程（需复核）

> 来源：2026-08 商业化审计。以下能力"有代码有测试，但翻译主流程/前端未接入"。
> 自 2026-08 起部分可能已变，动手前先 grep 复核。

- **质量证明未接主流程**：质检报告（`scripts/generate_report.py`）、母语者 5 维评分（`scripts/evaluate_native.py` + `src/evaluator_prompts.py`）、冷读统计汇总（`src/error_tracker.py` 记录但不汇总）——用户翻译完只拿到译稿，看不到"编译得多好"。
- **`channel`（男频/女频）正交维度缺失**：只有 `genre`，无显式频道字段。当前用 `urban` 顶替女频可 PASS，记为待办。
- **`game`（游戏对白）内容类型零验证**：`content_type` 有 novel/script/game 三维，game 支线从未实跑验证。

## 四、声明但未验证 / 未完成的边界（与 README 一致）

- **非英语语种质量未验证**：`es-ES`/`de`/`fr` 术语级数据存在，但 `de`/`fr` 多为首创译法（标 `⚠️ FIRST-ESTABLISHED`），且 `cultural_rules.json` 的文化保真度规则只有 `en-US`。非英语语种译文质量从未验证。
- **客户端多租户缺失**：认证是单一共享 API key；数据按 `book_id` 隔离，但无 per-customer key/计费边界。
- **严格盲读未完全成立**：冷读简报的术语段仍携带中文源译法（= §二"条款10 泄漏"，同一问题）。
- **长距离 style memo A/B 未跑**：基础设施在，但 memo-on vs memo-off 的质量对比从未端到端跑过。
- **伏笔保护缺失**：管线可能删掉标注为"纯文化冗余"的段落，无机制防止误删埋下的伏笔线。

## 五、零碎尾巴（低优先，等用到再做）

- 三本书术语库待重建（译稿在，术语库曾清空重播种）。
- `review` API 是否已做 `book_id` 隔离需复核（旧审计记为未做）。

## 六、本文的维护约定

- 任何"已完成/已修复"的项，改这里并同步 `README.md` 的对应小节，不要只在交接文档里更新。
- 新增"未完成项"时，标注**来源（实测/审计/交接轮次）**和**行动方（你 / 可自行做 / 先报价）**。
