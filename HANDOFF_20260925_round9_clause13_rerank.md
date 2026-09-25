# 西渡 · 交接（2026-09-25 第9轮 · 条款12/13 收尾 + 计划重排 · **新窗口从这里开始**）

> **本文件是最新入口。** 上一轮 `HANDOFF_20260924_round8_node_tier_routing.md` 的全部改动**已提交**，不要重做。
> 本轮完成 **条款12** 与 **条款13 的 3/4**，共 8 个提交，工作区干净。
> ⚠️ **本轮最重要的一件事：对计划做了重排**（见 §六）。旧顺序已不适用，因为调查挖出的缺陷改变了优先级依据。

---

## 〇、基线（实测，非声称）

```bash
python3.11 -m pytest -q --no-header                    # → 1524 passed, 4 deselected
python3.11 scripts/static_audit.py --self-test         # → 27/27
python3.11 scripts/mutation_test.py graph_post_process # → 73/73 killed
python3.11 scripts/mutation_test.py exact_store        # → 42/42 killed
python3.11 scripts/mutation_test.py review_api         # → 18/18 killed
python3.11 scripts/mutation_test.py scan_deliverable   # → 53/53 killed
python3.11 scripts/mutation_model.py                   # → 38/38 killed
```
- **必须用 `python3.11`**，本机 `python3` 是 3.9
- 1524 = round7 基线 1370 + 本轮 **154 个新测试**
- 本轮 8 个提交，累计 **34 files changed, +5499 −452**，其中 tests/ **+2468 行**
- `cultural_rules.json` **本轮未触及**（123156 字节，round7 提交态）
- 提交历史（新→旧）：
  | 提交 | 内容 |
  |---|---|
  | `52e7940` | 条款13-D：人工审校闭环（挂载顺序 + 命名空间隔离） |
  | `d189ad3` | 条款13-C：`scan_deliverable.py` 全量交付扫描 |
  | `37be137` | 条款13-B：FIX 回写闭环 |
  | `9c627cb` | 条款13-A：术语表两处缺陷（语种误标 + 零匹配倒全库） |
  | `cfd5c72` | round8 交接文档 |
  | `a148597` | 条款12：档位路由接成单一来源 |
  | `a8f4da9` | round7 文档数字更正 |
  | `06d8fd0` | round7 代码数据（P0 补交） |
- `pytest` 裸跑**恒 ¥0**（conftest 封网）

---

## 一、⚠️ 本轮我自己的两次事故（先读，防重犯）

**事故**：我两次在 **pytest 之外**跑写库脚本，把测试数据写进了**生产库** `data/checkpoints.db`。

| 次数 | 场景 | 污染 | 恢复 |
|---|---|---|---|
| 1 | 诊断 `confirm_term` 双语种行为 | 写入 `book_id='job_42'` 2 行 | 已备份 + 精确 DELETE，核对 131 行 / `confirmed` 0 行 |
| 2 | 验证前后端契约的请求序列 | 写入 `book_id='jobA'/'jobB'` | 脚本内自清理，核对 131 行 / `rejected_terms` 0 行 |

**根因**：`CHECKPOINT_DB_PATH` 的临时目录重定向靠 `tests/conftest.py:37` 的环境变量，**只在 pytest 下生效**。裸跑 `python3.11 -c "..."` 时它指向真实生产库。

**讽刺之处**：两次事故都发生在我正在实现"命名空间隔离"的时候——而这个隔离缺陷的本质正是"调用方忘了传对上下文就落到默认位置"。

**教训（已写为铁律 16）**：
1. **任何写库的验证都必须在 pytest 内跑**（新建 `tests/test_zz_debug*.py`，跑完删）。
2. 若必须裸跑，**先打印 `CHECKPOINT_DB_PATH` 确认它在临时目录**，且**脚本自带清理**。
3. 事故后必须逐项核对基线（总行数 / `confirmed` 计数 / `rejected_terms` 行数），不能只看"删了"。

备份仍在：`/tmp/checkpoints_backup_20260925_193538.db`（注：这是**污染后**拍的，不能用于回滚，只作对照）。

---

## 二、条款12 完成（`a148597`）

**决定性发现：`MODEL_MAP` 此前是死配置。** 文档（round5 §四、`COST_MODEL_20260922.md` §0.5）都说"最大杠杆是 `MODEL_MAP` 三节点共用 `QWEN_MODEL`"，实测：五个生产节点全部直接 `import QWEN_MODEL/GLM_MODEL/GLM_FULL_MODEL`，`MODEL_MAP` 只被 `dashboard.py`（展示）和 `adversarial_test.py`（离线脚本）读取。**改它一分钱都省不下来**，§0.5 的 10–17 倍杠杆（¥1,974→¥198）一直是断开的。

已接通：`config.py` 新增 `NODE_MODELS`/`NODE_EFFORTS`/`node_provider`/`node_model`/`node_llm_kwargs`，**降档变成一个环境变量**（`XIDU_NODE_MODEL_READBACK=qwen3.8-flash`），provider 由模型名派生故跨 provider 降档也能正确路由。默认值与改造前逐项等价（实测核对）。

连带修三个真实缺陷：账本硬编码模型名（降档后会把成本记到 max 档、正好掩盖省下的钱）、`review_terms` 密钥守卫硬编码 `LLM_API_KEY`（降到 GLM 后整个术语评审静默跳过）、`write_fallback` 路由无人守护（1301 过滤确定性 → 兜底必须换 provider）。

**剩余**：实际降档需 A/B 验证质量，按铁律6 先报预算。

---

## 三、条款13 完成 3/4（`9c627cb` `37be137` `d189ad3` `52e7940`）

### A. 术语表两处缺陷（`9c627cb`）
1. **三处表头写死 `| Chinese | English |`**（`exact_store` ×2、`graph._format_semantic`）→ 西语运行时告诉模型"这列是英语"，**直接阻塞条款2**。`to_formatted_text_with_notes` 甚至收了 `target_lang` 却只用来查 note。已建 `config.LANGUAGE_SHORT_LABELS` + `target_language_label()`（键被测试断言等于 `SUPPORTED_LANGUAGES`、值等于 `LANGUAGE_NAMES` 首词，不会成为第 13 份副本）。
2. **零匹配章节倒出整本词库**：`matched_terms or self._dict` 把 `{}` 与 `None` 混为一谈。实测 2000 条术语的书，无匹配章节被注入 **41,824 字符**。既有测试全绿是因为它跑在**空词库**上（"无匹配"与"无术语"在空库下不可区分）——典型假绿。

### B. FIX 回写闭环（`37be137`）
`_post_process` **完全不读 `changes_made`**（语法树实测 NO）→ FIX 花钱修完经验全丢。且 `FIX→READBACK→FIX` 是循环而 `adaptation_notes` 非累加字段，第二轮覆盖第一轮。新增 `fix_lessons: Annotated[list[str], operator.add]`，回写入 `bridges.md`（该抽屉定义就是 "patterns that worked (and didn't)"），每章上限 5。

### C. 全量交付扫描（`d189ad3`）
`_report.json` 声称 `chapters_translated: 2301` / `grade B`，但背后是 `cold_read_coverage_pct: 2.56`（59 章），完整性检查只数标题。新建 `scripts/scan_deliverable.py`，六项判据**全部先在真实语料标定**，零成本（实测无网络调用、不导入 LLM 客户端）。

**实测结果**（`difu_en.md`，2301 章，0.4 秒）：17 条缺陷 / 12 章受影响。
- 抓住文档已知的 Ch366（```json 围栏 + 裸 `{`）、Ch1472（未解包信封 + 句中截断）
- **新发现 2 处文档从未记录**：Ch1077 `End of Chapter.`、Ch1286 `END OF CHAPTER 1286`（脚手架文本进了交付物）
- 行号 **17/17 与文件真实内容逐条交叉核对一致**
- `--all` 另查出 `limitless_horror_full_en.md` **14 章正文首行是未翻译的中文章节名**（`醒来 上`/`死亡擦肩而过 下`…），标题行只有 `## Chapter 2: 第一章`。判据正确区分"标题带中文=设计如此"与"正文带中文=缺陷"

**标定记录**（判据必须能区分缺陷与正常，否则只报指标不当门禁）：
- 裸用"结尾标点"判据 → **81/2301 命中，~95% 是误报**（markdown 强调符/闭合引号）。同 `completeness.py` 的 `glossary_missing` 病（11 个截断章缺 8–24 术语，8 个健康对照缺 5–16，区间重叠）
- 剥离强调符与引号、**保留破折号**（被打断的台词是合法章节结尾）→ **11/2301 = 0.48%**，其中 9 真 2 合法（诗词署名、干支纪年）
- 故输出定位为**带证据的分诊清单**，不是通过/失败门禁：11 行人能读完，81 行不能

### D. 人工审校闭环（`52e7940`）—— 本轮最大的计划外发现
生产库 `SELECT COUNT(*) WHERE status='confirmed'` = **0 行**。不是"没人用"，是**用了也没写进去**。四个叠加缺陷：

| # | 缺陷 | 实测证据 |
|---|---|---|
| 1 | `app.mount("/api", api_router)` 注册在 `/api/review` **之前** | Starlette 按注册顺序匹配 mount，catch-all 吞掉其后所有更具体子应用 → `/api/review/*` 与 `/api/editor/*` **全部 404**。前端三个 fetch（`web_ui.py:1837/1886/1897`）从未到达服务器。`/api/cms` 幸存只因它注册在 `/api` 之前 |
| 2 | `_get_store()` 缓存单个 `ExactGlossary()`，`book_id` 硬编码 `default` | 生产任务 `book_id=job_id`（`routes.py:471` 每语种一个 `add_language_job`）。审校 API 只能看到 **28/131 = 21.4%** 的术语，**没有一本是生产书** |
| 3 | `load_from_db`/`confirm_term`/`reject_term` 不带 `target_lang` | 默认 en-US。确认西语术语时 UPDATE 匹配 0 行却返回 `{"ok": true}` |
| 4 | 两方法返回 `None` | 命名空间不匹配与成功不可区分 |

修法：catch-all 移到最后；store 缓存按 `(book_id, target_lang)` 键控；`ExactGlossary.list_namespaces()` 类方法 + `GET /namespaces`；三端点全接 `target_lang` 并对非注册语种 400（不回退）；两方法改返回受影响行数，API 据此区分成功 / 仅记录反馈 / 404。前端加书目+语种下拉框、`nsQuery()` 统一构造参数、切书目时重建语种列表。

**前后端契约已实测**：按前端完整请求序列（`loadNamespaces → fetchTerms → confirm/reject`）对活应用逐步重放，字段一致；`nsQuery` 的 URL 编码用 node 实测（含中文与 `/`）。前端守护测试另做变异验证：删掉 confirm 的 `nsQuery` 后 **2 个测试同时失败**。

### ⚠️ E. 缺口① TM 句对存储 —— **实测数据表明不该做，需用户拍板**

| 测量 | 结果 |
|---|---|
| 源文是否被持久化 | **从未**。`chapter_content` 无任何 save/persist 路径；`_checkpoint.json` 只有 `last_idx`/`glossary_snapshot`/`previous_summary`/`timestamp` 四键 |
| 中文句子重复率 | **0.59%**（三本样本合并 679 句，仅 4 句重复） |
| 英文句子冗余 | 5.33%（229,533 句 / 12,230 冗余），但按句长分档：≤12 字符 **55.9%**、13–40 字符 **43.7%**、>40 字符仅 **0.4%（46 句）** |
| 高频重复句内容 | `"He paused."`(110x)、`"A pause."`(86x)、`"Silence."`(76x)、`"Nothing."`(69x)、`"..."`(130x) —— 全是不携带文化内容的通用短句 |

**结论**：TM 唯一可能有价值的部分只有 **46 句（0.4%）**，而其中 top 几名是 `"Blessings of the Boundless Yellow Heaven."` 的 **7 种不同译法**——**TM 解决不了这个**（TM 要求 源→译 映射唯一），这是术语一致性问题。建 TM 还要先新增一整层源文持久化。

→ **建议不做，把预算投到下面 §四 的术语一致性上。但这是用户的决定。**

### 缺口③ 的另一半（未做）
内存 `_dict` 仍是扁平的、一次只装一个语种（SQLite 主键含 `target_lang`，schema 已预留）。当前不阻塞：生产路径每语种一个 `job_id` → 一个 `TranslationAgent` → 一个 store 实例。**但 `review.py` 现在会跨命名空间切换**，靠"每个命名空间一个 store 实例"规避，不是根治。

---

## 四、🆕 实测发现的交付质量缺陷（未处理，建议拆为条款16）

调查 TM 时顺带测出**真实的交付质量问题**，比 TM 值得做：

| 发现 | 实测数据 | 性质 |
|---|---|---|
| 同内容多表述 | **43 组**（>25 字符句，同词袋签名 ≥2 种表述且总出现 ≥5 次） | ⚠️ **上界，需分诊**：`shook his head slowly` vs `slowly shook his head` 是正常散文变化；但 `"Don't worry, Eighth Master."` vs `"Eighth Master, don't worry."`（20 次/7 种）是同一台词被反复重写 |
| glossary value 互为包含 | **238 对** | ⚠️ **上界，需分诊**：`麻衣姥姥` vs `麻衣姥姥的地宫` 是长短词关系（合法）；`常怀远='Chang Huaiyuan'` vs `常怀元='Chang Huaiyuan'` 是**不同汉字同一罗马音**（可能真冲突） |
| 同一角色多种称呼 | `八爷` → `Eighth Master` / `Eighth Lord Fan` / `Chang Baye`；`七爷` → `Seventh Master` / `Seventh Lord` / `Seventh Elder Xie` | **真缺陷**：同一角色在交付物里有 Master/Lord/Elder/Baye 四种称呼 |
| 仪式短语无术语锁定 | glossary 1467 条中**无 `黄天`/`无量` 任何条目** | **真缺陷**：高频咒语从未进术语表，所以被自由重译成 7 种 |
| 引号未规范化 | 弯引号 `“` 4010 / `”` 3995，直引号 `"` 79,399 | **真缺陷**：同一交付物混用两套引号 |

**零成本可做**：`scan_deliverable.py` 已经是分诊工具，这几项都能加判据（引号规范化、glossary 内部冲突、仪式短语缺术语）。

---

## 五、其他本轮查明的失真记录

| 项 | 文档原记 | 实测 |
|---|---|---|
| `data/token_ledger.jsonl` | round5 §〇 当作"生产数据指纹 8813 行" | **8813 行里真实生产数据只有 2 行**。89.8% 未计量（token 全 0）；恒 `(100,50)` 84 行、`(5,6)` 721 行；`book_id` 空或 `test_*` 开头 8811 行。成因：`conftest.py:36` 的 `XIDU_TOKEN_LEDGER` 重定向是**后来**才加的，此前 pytest 往生产账本追加真实行 |
| `cultural_rules.json` 字节数 | round7 记 `58231 → 118151` | **`59817 → 123156`**（已在 `a8f4da9` 更正，另更正 round-trip `60633 vs 59817`、首个差异字节 54470、"上千行 diff"实为多 68 行） |
| 变异闸门 `git diff --numstat` 应显示"0 删除行" | round7 §五 | **只适用纯新增注入，不适用数据搬移**（urban→books 迁移实测产生 241 删除行）。判数据完整性必须用结构化逐键比对 |

---

## 六、🔀 15 条计划重排（本轮最重要的交接内容）

> **旧顺序（1→5→3→11→12→13→14→2→10→8→9→7→6→4）已不适用。**
> 重排依据：本轮调查发现 ①部分条款的"前置工具"已就位（条款14 现在只差执行）②部分条款的实质缺口是假的（TM）③查出了比原条款更严重的计划外缺陷（审校 API、术语一致性）④我自己两次污染生产库说明条款6 的优先级被严重低估。

**已完成 4 条**：1 工程审计 / 5 交接验证 / 11 Agent 通用性 / **12 档位成本**

### 新顺序（按"不需预算 + 已具备执行条件 + 影响已交付产物"排序）

| 新位次 | 条款 | 状态 | 为什么排这里 |
|---|---|---|---|
| **1** | **14 全量扫描 + 坏章修复** | 🟡 工具已就位 | **本轮 `scan_deliverable.py` 已把判据标定完、坏章位置精确到行号**（Ch366/Ch1472/Ch1077/Ch1286 + 章号 5 处歧义映射）。从"需先调查"变成"只差执行"，且不需预算。这是唯一影响**已交付产物**的项 |
| **2** | **16 术语一致性（🆕 本轮拆出）** | ⬜ 未开始 | §四 的实测缺陷。直接影响交付质量，零成本，且可复用 `scan_deliverable.py` 加判据。**优先级高于 TM** |
| **3** | **6 API 调用管控** | 🟡 20 个裸露脚本 | **本轮我两次污染生产库正是这条要防的事故类型**（"跑就花钱/跑就写库、无闸门"）。原排倒数第 4，严重低估。建议把 `scripts/_cost_gate.py` 扩成"成本 + 写库"双闸门 |
| **4** | **9 主笔 agent 评估** | ⬜ 倾向"缺" | 纯代码调查不需预算。核实 `style_memo` 是否真被 WRITE/FIX 消费——本轮已确认 `_post_process` 确实写 memo，但**消费侧未验证** |
| **5** | **4 项目边界（剧本/游戏线）** | ⬜ 未开始 | 含一个**真实的 UnboundLocalError**：`scripts/run_script_pilot.py:174`（`glossary` 全部赋值都在 While 保护块内，却在无条件路径被读取）。本轮 `static_audit --include-src` 实测复现，5 条既存发现之一 |
| **6** | **10 READBACK 盲读独立性** | 🟢 基本通过 | 只剩 `graph.py:155` 角色中文名泄漏（元数据级，极低）。小修，可顺手做掉 |
| **7** | **13 收尾（TM 拍板）** | 🟡 3/4 完成 | §三-E 的实测数据已给出"建议不做"的结论。**需用户拍板**，不宜自行投入 |
| **8** | **15 circuit_breaker 死配置** | ⬜ 待拍板 | round5 §五 三选一，前轮建议"暂不动"。仍需用户决定 |
| — | **需预算的（按此序）** | | |
| **9** | **12 收尾：实际降档 A/B** | 🟡 杠杆已接通 | 现在降档 = 一个环境变量。READBACK → `qwen3.8-flash` 是单节点约 15 倍降幅。建议先跑 A/B 比 PASS/NEEDS_FIX 一致率 |
| **10** | **3 模型变更质量回归** | 🟡 成本已算 | 可与上一项**合并一次预算**：都要跑真实章节对比 |
| **11** | **2 端到端质量 + 西语启动** | ⬜ 前置已满足 | 条款11（视角）与本轮表头修复（西语不再被标成英语）都已完成，**障碍已清除** |
| **12** | **8 Qwen-MT 嵌入评估** | 🟡 文档已查完 | 三个候选方案待确认，实测需预算 |
| **13** | **7 不降质降成本** | ⬜ 与12耦合 | 条款12 已铺路：§0.5 的五档配置现在都可直接用环境变量组合表达，无需改代码 |

**合并预算的建议**：条款 12 收尾 / 3 / 2 / 8 都需要真实 API，可攒成一批跑（铁律6 要求先报预算）。条款14 与条款16 **完全不需要预算**，应优先做完。

---

## 七、写代码的方法约束（本轮反复踩到，务必遵守）

### 变异验证（铁律2/5/11）
- **必须串行**，期间禁并发跑 pytest、禁编辑任何文件（脚本原地改写源文件）
- **还原只能用 `/tmp` 快照 + 哈希校验，严禁 `git checkout`**（它还原的是最后一次提交，不是变异前的工作区；round7 因此抹掉 60KB 数据）
- 跑完必须校验：`.mutation_backup` 残留 0 + 源文件哈希与快照一致
- **SKIP/STALE/blocked 计入失败**

### 断言不能假绿（铁律12/15 + 本轮新增 17）
- **绝不用被测常量推导测试的输入和期望**。本轮我自己又犯一次：上限测试用 `_MAX_FIX_LESSONS_PER_CHAPTER + 4` 造输入、又用同一常量做断言 → 改常量两边同步变，恒真，变异 `const 5->6` 存活。改为字面值 `9 → 5` + 单独加 `test_cap_constant_is_five`（同 round7 `EXPECTED_TAGGED` 固定为 148 的做法）
- 同类：列宽断言写成 `f"{x:<20}"`（与被测常量同源）→ 改字面空格锚定；截断断言写 `len <= 250` 太松 → 改精确等值
- **变异体存活有三种原因**：①变异太弱 ②断言没牙 ③**被测单元被 mock 掉了，断言根本没覆盖那条路径**（本轮 `write_fallback` 路由变异存活即属③：builder 被整体 mock，config 层测试看不见 1301 路径向哪个节点要参数）
- **夹具缺陷也会让变异等价**：本轮两个行号 `+1` 变异存活，是因为夹具正文前有空行与 `---` 被过滤，起点后移仍落在同一过滤区。要验行号就得让正文紧接标题
- **测试不能依赖其他测试的遗留数据**：本轮一个测试类单独跑失败、全文件跑通过——它读的是 API 测试留下的行。每个夹具自带数据

### 判数据完整性（铁律13）
- 用**结构化逐键比对**（flatten 成 leaf 路径字典，比 `removed/added/changed` 三集合），**不用文本 diff 行数**
- 本轮据此排除"西语条目被删"的误报：`git diff` 显示 241 删除行含西语 xianxia 条目，但 HEAD 与工作区的 `genres.xianxia.es-ES` 块**逐字节相同（3215 字节）**，"只删未补"的仅 1 行且是 `"urban": {` 块头

### 写 JSON（round7 §五，本轮复测仍成立）
- `json.dumps(indent=2, ensure_ascii=False)` **与原文件不匹配**（工作区 123156 vs round-trip 123972，多 68 行）——`fidelity.*.examples` 是内联单行对象
- 必须用 **brace-match 文本手术**；`common` 语种键缩进 4，`genres` 语种键缩进 6，**两者不同**
- 术语名可能自带 `/`（`菜鸟 / 资深者`）——校验脚本按 `/` 拼路径会切坏，用元组键

### 文化适配准入标准（用户两次纠正，已入项目记忆）
- 判某语种术语能否补齐，标准是**"能否让目标语读者看懂"**，不是"目标语有无既定译法"
- 西渡存在的理由就是"外国读者看不懂中文词汇，需要文化适配"。按后者当门槛，既有的 `打脸 → humillación épica` 也永远 UNVERIFIED
- 真正的源数据是 **en-US 的人工适配指引**（含 `⚠️ CRITICAL`、`NEVER 'Hell'`、`NOT 'zombie'`），检索结果只作参考
- 首创译法标 `⚠️ FIRST-ESTABLISHED — pending native review`；语义警示必须逐语种翻译保留

---

## 八、本轮新增的缺陷模式（建议加入审计清单）

**"配置表声明了分工，但生产代码不读它"** —— 已出现 **3 次**：
| 案例 | 声明 | 实际 | 发现于 |
|---|---|---|---|
| 语种清单 | `config.SUPPORTED_LANGUAGES` | 复制 12 份且已漂移（`routes` 有裸 `en`，`editor` 没有） | round7 |
| 档位路由 | `MODEL_MAP` 五节点分档 | 五节点全部直接 import provider 常量，`MODEL_MAP` 只被展示层读 | 本轮条款12 |
| 语种参数 | `to_formatted_text_with_notes(target_lang=)` | 收了参数却只用于查 note，表头照样写死 `English` | 本轮条款13-A |

**排查手法**：`grep -rn "<配置名>" src/` 看是否只有展示层/注释在读，再 `grep -n "model=\|<硬编码常量>"` 看真实取值从哪来。

**候选同类嫌疑**（未查）：`_KNOWN_GENRES`（`routes.py:52`，已确认死配置）、`stats.py` 的 `record_tokens(tier=)`（已确认"参数保留但忽略"）、`use_flash_writer`（`test_write_guards.py` 已钉住它"只是会计标记、不改模型"）。

**另一个模式：挂载/注册顺序决定路由是否可达**。`/api` catch-all 先注册就吞掉后注册的所有 `/api/*` 子应用，而**全量测试全绿**——因为 `review.py` 零覆盖。加子应用时必须确认顺序，且要有可达性测试（本轮 `TestReviewApiIsMounted` 已钉住）。

---

## 九、铁律（承接 round5/6/7/8，本轮新增 16–17）

1. 任何"完成"都要能追溯到实测证据
2. 变异测试必须串行，期间禁并发/禁编辑；跑完校验还原 + `*.mutation_backup` 为 0
3. 判"等价不可杀"前，观测点必须覆盖变量被覆盖之前的时刻
4. **优先消除而非豁免**；删死代码前必须穷举/差分证明行为零变化
5. 工具的 SKIP/STALE/blocked 计入失败
6. 真实 API 调用一律先报预算等批准
7. 破坏性操作先请用户确认
8. 别凭记忆引用字段/函数名——读码确认后再写
9. 删配置/删符号必须同步清理所有引用方
10. 改 prompt/模板后必须验证"渲染真的生效"
11. 变异验证的还原只能用 `/tmp` 快照，严禁 `git checkout`
12. 子串断言在多处相似代码的文件里几乎必然假绿
13. 判数据完整性用结构化逐键比对，不用文本 diff 行数
14. 文档里的数字要实测复核，不要当事实引用
15. 变异体存活有第三种原因：被测单元被 mock 掉了
16. **【本轮新增】任何写库的验证都必须在 pytest 内跑** ——`CHECKPOINT_DB_PATH` 的临时目录重定向靠 `conftest.py`，裸跑 `python3.11 -c` 时指向生产库。本轮两次污染 `data/checkpoints.db` 都是这么来的，且第二次发生在我正在实现"命名空间隔离"的时候
17. **【本轮新增】判据必须先标定误报率再提升为缺陷** ——裸"结尾标点"判据 81/2301 命中、95% 误报；标定后 11/2301、9 真 2 假。误报率高到无法分诊的判据只能当指标，不能当门禁（同 `completeness.py` 的 `glossary_missing`）。**标定要用真实语料，不能用合成夹具**：扫描器首跑的两个 bug（`_TERMINAL` 写成 str、行号偏移 69 行）都只有跑真实 2301 章才暴露，标定脚本发现不了

---

## 十、本轮改动文件清单（34 文件，全部已提交）

**新增（3）**
- `scripts/scan_deliverable.py` — 487 行，六判据全量交付扫描
- `tests/test_scan_deliverable.py` — 577 行，64 测试
- `tests/test_review_api.py` — 646 行，57 测试

**源码（10 M）**
- `src/config.py` — `SUPPORTED_LANGUAGES` 派生 `LANGUAGE_SHORT_LABELS` + `target_language_label()`；档位路由层（`NODE_MODELS`/`NODE_EFFORTS`/`node_provider`/`node_model`/`node_llm_kwargs`/`_env_overridden`）
- `src/agent/nodes/{read,write,readback,fix,review_terms}.py` — 全部改经 `node_llm_kwargs`；三个 `_capture_*_tokens` 改接收实际模型名；`_build_qwen_writer` → `_build_fallback_writer`
- `src/agent/state.py` — 新增 `fix_lessons: Annotated[list[str], operator.add]`
- `src/agent/graph.py` — 回写 `fix_lessons` 到 `bridges.md`；`_format_semantic` 加语种；两处术语表调用补传 `target_lang`；`_MAX_FIX_LESSONS_PER_CHAPTER=5`
- `src/glossary/exact_store.py` — 表头语种化；`None` vs `{}` 区分；`list_namespaces()` 类方法；`confirm_term`/`reject_term` 返回受影响行数
- `src/api/review.py` — 全文重写：命名空间键控 store 缓存、`/namespaces` 端点、三端点接 `target_lang`、非法语种 400、404 区分
- `src/main.py` — catch-all `/api` 挂载移到最后
- `src/web_ui.py` — 审校页加书目/语种下拉框、`nsQuery()`、`loadNamespaces()`、`repopulateLangs()`、`langsFor()`、init 自执行块

**测试（8 M）**
- `test_dual_model.py` +10、`test_post_process_gates.py` +8、`test_glossary.py` +6、`test_language_registry.py` +5、`test_write_guards.py` +5、`test_review_terms.py` 2 处补丁目标改指 config、`test_graph_units.py` FakeExact 同步签名、`test_cultural_rules*.py`（round7 补交）

**脚本（2 M）**
- `scripts/mutation_model.py` — 变异表重写（13 STALE → 0）；`run_tests()` 扩跑 `test_write_guards.py`
- `scripts/mutation_test.py` — 新增 TARGET `review_api`、`scan_deliverable`；`exact_store` 补入 `test_review_api.py`

**交接文档（2 新增）**
- `HANDOFF_20260924_round8_node_tier_routing.md`（`cfd5c72`）
- `HANDOFF_20260925_round9_clause13_rerank.md`（本文件）

---

## 十一、下一位的第一步

```bash
# 1. 确认基线（都应绿）
python3.11 -m pytest -q --no-header                     # → 1524 passed
python3.11 scripts/static_audit.py --self-test          # → 27/27

# 2. 看一眼真实交付物的缺陷清单（零成本，0.4 秒）
python3.11 scripts/scan_deliverable.py novels/output/difu_segmented/difu_en.md
python3.11 scripts/scan_deliverable.py --all            # 含 limitless_horror 的 14 章中文残留

# 3. 按 §六 新顺序，第一个该做的是条款14（坏章修复）
#    坏章位置已精确到行号，判据已标定，不需预算
```

**三件需用户拍板的事**（承接 round5 §五 + 本轮新增）
1. **TM 句对存储做不做**（§三-E，实测数据建议不做）
2. **`term_conflicts` / `resolved_conflicts` 死字段删不删** —— 全仓穷举确认无任何节点读写、checkpoint 不序列化，只有测试夹具把它作为 state 字面量列出；但 `graph.py:337` 注释写着 "Kept for backward compat (unused by new nodes, harmless)"，是**已记录的决定**，按铁律7 不擅自推翻
3. `data/token_ledger.jsonl` 的 8811 行测试残留清不清（§五）——破坏性，且 round5 把它列为生产指纹，清理会改变指纹值
4. 承接项：5 本书的 `name_en`、回改已污染历史语料、清理 `output/` 约 404 个测试残留、`_KNOWN_GENRES` 死配置
