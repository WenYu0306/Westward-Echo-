# 西渡 · 交接（2026-09-25 第10轮 · 条款13-A/10/14 收尾 · **新窗口从这里开始**）

> **本文件是最新入口。** 上一轮 `HANDOFF_20260925_round9_clause13_rerank.md` 的全部改动**已提交**，不要重做。
> 本轮按 round9 §六 位次表完成 **位次 1（13-A）→ 位次 2（条款10）→ 位次 3（条款14 的"防复发"半）**，共 3 个提交，工作区干净。
> ⚠️ **四件待拍板事项用户本轮已全部拍板**（见下）。**条款14 只完成了一半**：防复发（生产守卫）已做并提交；清历史（坏章修复）已精确分诊但**未执行**，留给下一位，是新窗口的第一步。

## 🛑 先读这里

### A. round9 四件待拍板事项 —— 用户已全部决定

| # | 事项 | **用户决定** | 执行状态 |
|---|---|---|---|
| **1** | TM 句对存储（条款13-B）做不做 | **不做，转投条款16 术语一致性** | ✅ 已决，无需再做。条款13 至此全部关闭（13-A 本轮收尾，13-C/13-D 上轮完成，13-B 决定不做） |
| **2** | `term_conflicts`/`resolved_conflicts` 死字段删不删 | **删掉并同步清理引用** | ⬜ **已批未执行**。见 §四-1 |
| **3** | `token_ledger.jsonl` 8811 行测试残留清不清 | **先备份再清理，同步更正指纹记录** | ⬜ **已批未执行**（破坏性，见铁律7）。见 §四-2 |
| **4** | 是否给条款12 收尾 + 条款3 批 A/B 预算 | **批，两项合并一批** | ⬜ **已批未执行**。按铁律6，**实际调 API 前必须先报具体金额等批准**。见 §四-3 |

> #2/#3/#4 是用户**直接拍板**的授权，不是我推断的。#3/#4 涉及破坏性操作与真实花钱，执行前仍需按铁律走确认/报预算流程。

### B. 本轮没碰、下一位接着做的（全部零成本、不需再拍板）

round9 §六 **位次 3 的"坏章修复"半 + 位次 4–7**：条款14 坏章修复（§三）→ 条款6 API管控 → 条款16 术语一致性 → 条款9 主笔评估 → 条款4 项目边界。

### C. 硬规矩（承接 round9 §一/§九，本轮全程遵守，未出事故）

- **必须用 `python3.11`**（本机 `python3` 是 3.9）。
- **任何写库验证必须在 pytest 内跑**（铁律16）。裸跑 `python3.11 -c` 时 `CHECKPOINT_DB_PATH` 指向生产库 `data/checkpoints.db`，round9 因此污染两次。本轮所有验证都在 pytest 内或只读，**未写生产库**。
- **变异还原只能用 `/tmp` 快照 + 哈希校验，严禁 `git checkout`**（铁律11）。变异必须串行，期间禁并发 pytest、禁编辑文件；跑完校验 `.mutation_backup` 残留 0。本轮 6 个变异目标全部 0 残留。
- **断言不能假绿**（铁律12/15/17）：绝不用被测常量推导测试输入和期望。本轮所有新测试断言用字面值。

---

## 〇、基线（实测，非声称）

```bash
python3.11 -m pytest -q --no-header                    # → 1565 passed, 4 deselected
python3.11 scripts/static_audit.py --self-test         # → 27/27
```
- 1565 = round9 基线 1524 + 本轮 **41 个新测试**（13-A +14、条款10 +1、条款14 +26）
- 本轮触及的变异目标，全部 **0 存活**：
  | 目标 | 结果 | 关联提交 |
  |---|---|---|
  | `exact_store` | 60/60 killed | 13-A |
  | `graph_post_process` | 73/73 killed | 13-A、条款10 |
  | `review_api` | 18/18 killed | 13-A |
  | `output_guard` | 25/25 killed | 条款14 |
  | `scan_deliverable` | 51/51 killed | 条款14（由 round9 的 53→51，判据字面量移入 output_guard 单一来源，重复变异点消失，总覆盖不减反增） |
  | `write_guards` | 58/58 killed | 条款14 |
- 提交历史（新→旧）：
  | 提交 | 内容 |
  |---|---|
  | `26c9bf6` | 条款14：JSON信封残留/脚手架文本升级为生产期守卫（单一来源） |
  | `9623a2d` | 条款10：READBACK 冷读简报剥离角色中文名（关闭一整条） |
  | `c7854f0` | 条款13-A：术语库内存 `_dict` 改为按语种分桶（缺口③ 收尾） |
  | `782f90c` | round9 交接修正（上一轮入口） |
- `pytest` 裸跑**恒 ¥0**（conftest 封网）。

---

## 一、条款13-A 完成（`c7854f0`）—— 位次1，缺口③ 收尾

**问题**：`ExactGlossary` 内存层是单个扁平 `_dict`，一次只装一个语种，而 SQLite 主键已含 `target_lang`。`review.py` 只能靠"每命名空间一个 store 实例"规避跨语种切换，非根治。

**改法**：`_dict` → `_by_lang: {lang: {term_cn: term_en}}` + `_active_lang` 指针。一个实例可同时持多语种且互不串。所有读写方法统一接受 `target_lang`（缺省=活动语种，**与改造前逐项等价**）。

**向后兼容（关键）**：
- `snapshot()` 仍输出**扁平单语种** `{term_cn: term_en}`，不嵌套——现有生产 checkpoint（`difu_segmented/_checkpoint.json` 实测 1467 条扁平）和每个 job 单一语种的契约都不变。
- `restore_snapshot()` 同时接受扁平（按 `target_lang` 入桶）与嵌套 `{lang:{...}}`（多语种一次恢复）两种格式。非 dict 输入抛 `TypeError`。

**连带修复 5 处真实缺陷**（都属 round9 §八"收了参数却不用"同型）：
1. `prefetch.py:103` `match_in_text` 漏传 `target_lang` → 多语种 store 下取错语种精确匹配。
2. `graph.py` `TranslationAgent` 加 `target_lang` 构造参数并设活动语种；`match_in_text`/`to_dict`/`snapshot`/`restore_snapshot`/冷读简报全部按语种 → **西语任务不再把术语快照到空的 en-US 桶**（这是改造引入又当场堵上的真实风险）。
3. `routes.py`/`celery_app.py` 多语种与 Web 路径的快照读写、术语导出、计数、断点恢复全部显式带语种。
4. DB 层方法（`get_term_info`/`get_status`/`confirm_term`/`reject_term` 等）默认语种由写死 `en-US` 改为活动语种，消除"内存默认活动语种、DB 默认 en-US"的不一致（round9 缺陷#3 同根）。
5. `review.py` 顶部过时注释更正（per-namespace 缓存由"缺陷规避"变为"偏好"）。

**注**：`review.py` 仍保留 per-namespace store 缓存。13-A 后单实例本可服务多语种，但缓存是"简单且显然正确"的选择（每次请求 `load_from_db` 刷新），非缺陷规避，故未删。

---

## 二、条款10 完成（`9623a2d`）—— 位次2，关闭一整条

`_build_cold_read_context` 的 `## CHARACTERS` 段渲染 `- **{en}** ({cn})`，把角色中文源名印进 READBACK 简报。READBACK 是 naive target-language 冷读者、prompt 无原文占位符（实测确认：`readback.py` 只读 `translated_text`+`cold_read_context`+reader 身份），是严格盲读，中文名是元数据级源文泄漏。

**改法**：只渲染英文译名 `- **{en}**`。`len(cn) >= 2` 守卫仍读源名长度区分真名/单字词，只是不打印源文。条款10 至此全部通过、**可关闭**。

---

## 三、条款14 完成"防复发"半（`26c9bf6`）—— 位次3，**坏章修复未做**

### 已做：两类缺陷升级为生产期守卫

**根因**：difu_en.md 的坏章能进交付物，是因为这两类缺陷此前**只有 scan_deliverable.py 事后扫描能发现，生产路径 WRITE 节点无任何对应守卫**。截断那类已被 round9 之前的 `_was_truncated`（提交 `291ded9`，2026-09-21）堵上，但信封/脚手架完全裸奔。difu_en.md 产出于 **2026-08-04**，早于截断守卫一个半月——这是大部分坏章的时间背景。

**改法（`output_guard.py` 作单一来源，杜绝 round9 §八"两套判据各自漂移"）**：
- `output_guard` 新增行级判据 `json_residue_on_line` / `meta_text_on_line` + 章级包装 `has_json_envelope_residue` / `has_meta_text_leak`。
- `write.py` 在 `sanitize` 后、重试前接入守卫：命中即丢弃正文触发既有重试，并记录 `*_residue` 事件；**重试路径对称补同一守卫**（`continue` 进下一次重试，全部耗尽走既有 for-else 跳过）。
- `scan_deliverable.py` 的 `check_json_residue`/`check_meta_text` 改为**委托** output_guard 共享判据，删除自身 `_JSON_KEYS`/`_JSON_FENCE`/`_META_PATTERNS` 副本。
- **委托后真实语料扫描结果逐条不变**（实测：12 章 / JSON_RESIDUE 4 / META_TEXT_LEAK 2 / TRUNCATED_BODY 11，Ch366/1077/1286/1472 全部同样命中）→ 证明重构功能等价。
- 正对照证明守卫不误伤：正确解包的 fenced JSON（Layer 1）仍照常交付。

### ⚠️ 未做：坏章修复（清历史）—— **新窗口第一步，已精确分诊**

⚠️ **本轮没动 `difu_en.md`**（已交付产物，按铁律7 破坏性操作需确认）。下表是**实测分诊**（用 scan_deliverable 判据剥残片后逐章核对正文是否完整收束），不是估计：

| 档 | 章号 | 行号 | 性质 | 修法 |
|---|---|---|---|---|
| **A 纯文本可修（零成本，4 章）** | Ch366 | 31766-31767 | ```json 围栏 + 裸 `{`，删后正文 `...kicked into gear.` **完整** | 删 L31766 尾部 ```json 起 + 整个 L31767 |
| | Ch656 | 59654 | 台词后接裸 `{`（`"We're done for!"{`），删 `{` 后台词**完整** | **行内删尾部 `{`**（非整行删） |
| | Ch1077 | 95699 | `End of Chapter.` 脚手架，删后正文 `...gleaming in the dark.` **完整** | 删 L95699 |
| | Ch1286 | 113923 | `END OF CHAPTER 1286` 脚手架，删后末行为 `—` | 删 L113923；⚠️ 删后 `—\n—` 重复模式，**需人工确认**是诗/被打断台词还是真截断 |
| **B 必须重译（花钱，6 章）** | Ch1472 | 134168-134169 | 整章封在截断的 `{"translated_text":...}` 里，剥残片后 **0 行有效正文** | 重跑管线（并入 §四-3 A/B 批次） |
| | Ch269/430/548/811/820 | 23953/38288/50223/72193/72909 | **句中硬截断**（如 `Chen Daji had never been`），无残片可删，正文本身被切 | 重跑管线（并入 A/B 批次） |
| **C 合法不动（2 章）** | Ch116 | 10475 | 诗词署名 `— Su Shi, *Butterflies in Love with Flowers*` | 不动（round9 已标定为合法） |
| | Ch2296 | 203607 | 干支纪年 `Jie Yu, Guimao year, Guihai month, Jichou day` | 不动 |

**执行建议**：A 档 4 章纯文本手术**零成本**，可立即做，但 `difu_en.md` 是已交付产物——**改前先 `/tmp` 快照备份**，改后用 `scan_deliverable.py` 复扫确认 A 档清零、B/C 档不误伤。B 档 6 章并入 §四-3 的 A/B 预算批次（重译要真实 API，铁律6 先报预算）。

**另**：`scan_deliverable.py --all` 还查出 `limitless_horror_full_en.md` **14 章正文首行是未翻译的中文章节名**（round9 §三-C 已记），同样属"清历史"，可一并纳入 A 档思路（删/译首行中文）。

---

## 四、剩余任务（按 round9 §六 位次，状态已更新）

**已完成 6 条**：1 工程审计 / 5 交接验证 / 11 通用性 / 12 档位（主体）/ **13 全部**（13-A 本轮收尾）/ **10**（本轮关闭）

### 零成本、不需拍板（接着做）

| 位次 | 条款 | 状态 | 备注 |
|---|---|---|---|
| **3后半** | **14 坏章修复** | 🟡 **防复发✅，清历史待执行** | §三 已精确分诊，A 档 4 章零成本可立即做（先备份），B 档 6 章并入 A/B 批次 |
| **4** | 6 · API 调用管控 | ⬜ 大半落地，20 个裸露脚本 | round9 两次污染生产库正是这条要防的。建议闸门扩成"成本+写库"双闸门 |
| **5** | 16 · 术语一致性 | ⬜ 未开始 | **用户决定 TM 预算转投这里**（§A-1）。实测证据最强：八爷四种称呼、咒语 7 种译法、弯/直引号混用。复用 scan_deliverable 加判据 |
| **6** | 9 · 主笔 agent 评估 | ⬜ 倾向"缺" | 纯代码调查。核实 `style_memo` 是否真被 WRITE/FIX 消费（消费侧未验证） |
| **7** | 4 · 项目边界（剧本/游戏线） | ⬜ 未开始 | 含真实 `UnboundLocalError`：`scripts/run_script_pilot.py:174`（`glossary` 全在 While 块内赋值，却在无条件路径读取） |

### 已批待执行（用户已拍板，见 §A）

| # | 事项 | 用户决定 | 执行要点 |
|---|---|---|---|
| **四-1** | 死字段 `term_conflicts`/`resolved_conflicts` | **删** | 实测全仓仅 3 类引用：`state.py:56/58` 定义、`graph.py:338-339` 初始化、4 处测试夹具字面量（`test_fault_injection.py:685/748`、`test_v15_pipeline.py:79`、`test_write_guards.py:150`）。无节点读写、无 checkpointer 持久化 → 无外部消费者。删除时**同步清理全部引用**（铁律9），删后跑全量+变异 |
| **四-2** | `token_ledger.jsonl` 8811 行残留 | **备份+清理+更正指纹** | 实测 8813 行中生产数据仅 2 行（`book_id` 非空且不以 `test_` 开头）。**破坏性**：① 先 `/tmp` 快照 ② 删 8811 行 `book_id` 空或 `test_*` 的残留，保留 2 行生产 ③ **同步更正 round5 §〇 记的"8813 行生产指纹"**为"2 行"，否则后续窗口误判生产数据被污染。成因：`conftest.py:36` 的 `XIDU_TOKEN_LEDGER` 重定向是后加的 |
| **四-3** | 条款12 收尾 + 条款3 A/B 预算 | **批，合并一批** | 杠杆已接通，降档=一个环境变量 `XIDU_NODE_MODEL_READBACK=qwen3.8-flash`（READBACK 单节点约 15 倍降幅）。**铁律6：实际调 API 前先报具体金额等批准**。建议把 §三 B 档 6 章重译也并入这一批（都要跑真实章节，省一次预算审批） |

### 待拍板（不自行投入）

| 位次 | 条款 | 状态 |
|---|---|---|
| **9** | 15 · circuit_breaker 死配置 | ⬜ round5 §五 三选一，前轮建议"暂不动"。**本轮用户未决定，仍需拍板** |

### 需预算组（位次 12–14，按此序，可与四-3 攒一批）

条款2 端到端+西语启动（前置障碍已清）/ 条款8 Qwen-MT 嵌入评估 / 条款7 不降质降成本（与12耦合）。

---

## 五、本轮改动文件清单（全部已提交）

**源码（6 M）**
- `src/glossary/exact_store.py` — `_dict`→`_by_lang` 分桶；`active_lang`/`set_active_lang`/`loaded_languages`/`_bucket`/`_writable_bucket`；所有读写方法接 `target_lang`；快照扁平+嵌套双向兼容
- `src/agent/graph.py` — `TranslationAgent(target_lang=)` 构造设活动语种；冷读简报剥中文名（条款10）；`match_in_text`/`to_dict`/`snapshot`/`restore_snapshot`/`load_glossary_snapshot` 按语种
- `src/prefetch.py` — `match_in_text` 补传 `target_lang`
- `src/api/routes.py` — Web/多语种路径快照读写、术语导出、计数、断点恢复带语种；agent 构造带 `target_lang`
- `src/celery_app.py` — 两任务 agent 构造带 `target_lang`；resume 快照恢复带语种
- `src/output_guard.py` — **新增**信封/脚手架判据（单一来源）：`json_residue_on_line`/`meta_text_on_line`/`has_json_envelope_residue`/`has_meta_text_leak` + `_BARE_JSON_LINES`/`_JSON_FENCE`/`_JSON_KEYS`/`_META_PATTERNS`
- `src/agent/nodes/write.py` — WRITE 节点接入信封/脚手架守卫（首次产出 + 重试路径对称）
- `src/api/review.py` — 过时注释更正（13-A 后 per-namespace 缓存非缺陷规避）
- `scripts/scan_deliverable.py` — `check_json_residue`/`check_meta_text` 委托 output_guard；删判据副本；新增 `_RULE_WIDTH`/`_CHECK_COL` 布局常量（消除内联宽度的不可杀变异点）

**测试（4 M）**
- `tests/test_glossary.py` +13（多语种隔离/持久化/快照兼容 3 个测试类）
- `tests/test_prefetch.py` +1（语种传递守护）
- `tests/test_graph_units.py`（FakeExact 同步新签名；角色名单剥中文名断言）
- `tests/test_post_process_gates.py`（snapshot 假对象接 `target_lang` + 断言语种传递）
- `tests/test_output_guard.py` +18（判据逐分支 + 章级包装 3 个测试类）
- `tests/test_write_guards.py` +4（端到端信封/脚手架守卫 + 正对照）

---

## 六、下一位的第一步

```bash
# 1. 确认基线（都应绿）
python3.11 -m pytest -q --no-header                     # → 1565 passed
python3.11 scripts/static_audit.py --self-test          # → 27/27

# 2. 条款14 坏章修复（位次3后半，清历史）——先做 A 档零成本 4 章
#    ⚠️ difu_en.md 是已交付产物：改前 /tmp 快照备份，改后 scan_deliverable 复扫
python3.11 scripts/scan_deliverable.py novels/output/difu_segmented/difu_en.md
#    A 档（纯文本可修）：Ch366 / Ch656 / Ch1077 / Ch1286（§三 表，含精确行号与修法）
#    B 档（必须重译）：Ch1472 / Ch269/430/548/811/820 → 并入 §四-3 A/B 批次
#    C 档（合法不动）：Ch116 / Ch2296

# 3. 已批清理（§四-1 死字段、§四-2 ledger）—— 用户已拍板，但 #3 破坏性需走确认
# 4. 然后位次 4–7：条款6 → 16（TM 预算转投这里）→ 9 → 4
```

**⚠️ 需用户确认/批准才能做的**：
- §四-2 ledger 清理（破坏性，铁律7）
- §四-3 + §三 B 档重译（真实 API，铁律6 先报金额）
- 位次9 条款15 circuit_breaker（待拍板）

**可直接做、不需再问的**：§三 A 档坏章修复（先备份）、§四-1 死字段删除、位次 4–7。
