# 西渡 · 交接（2026-09-24 第5轮 · **新窗口从这里开始**）

> **本文件是最新入口。** 上一轮入口 `HANDOFF_20260923_round4_start_here.md` 的"第一步（全量变异重跑）"**已完成并验收通过**，不要再重跑那一轮。
> 前序文档按需查：
> - `HANDOFF_20260923_round4_start_here.md` — 第4轮入口（变异重跑指令、14 条计划顺序、§四待拍板、§五铁律）
> - `HANDOFF_20260923_audit_fix_wip.md` — **§六 后续条款调查结论（第11/10/13/12/14/6/4/9/8 条，最有价值，别重查）**
> - `HANDOFF_20260923_audit_fix_round2.md` — health/stats 变异清零 + 闸门加固
> - `HANDOFF_20260921_audit_untrusted.md` — 上一轮不可信声明（部分已被推翻，见本轮 §四）

---

## 〇、基线（实测，非声称）

```bash
python3.11 -m pytest -q --no-header     # → 1280 passed, 4 deselected
```
- **必须用 `python3.11`**，本机 `python3` 是 3.9
- 全量变异测试 **957/957 killed，0 survived，0 timed out，0 STALE，0 blocked**，1 个登记豁免（`stats.py`，证明已独立复核成立）
- 生产数据四指纹零污染：ledger **0 行** / translation_events.db / checkpoints.db / output **817 文件**（mtime 均早于本轮）
  > ⚠️ **2026-09-26（round12）更正**：此处原记 **8813 行**。该行数**不是生产指纹**——
  > round9 已查明其中真实生产数据只有 2 行，其余是 `conftest.py:36` 加
  > `XIDU_TOKEN_LEDGER` 重定向**之前**的 pytest 污染。round11 进一步查明那 2 行
  > 也不是真实书目翻译，而是 `scripts/diag_api_hang.py:140` 的 `book_id='diag_test'`
  > 诊断残留（cost ¥0.421 / ¥0.468）。用户据此拍板**全部视为残留删除**，故当前值为
  > **0 行**（清理时实测 8816 行 = 8814 测试残留 + 2 行 diag_test）。
  > 后续窗口请以 **ledger 0 行** 为生产指纹基线；**不要**把任何非零行数当成"原有数据"
  > ——账本归零后若再出现真实行，那才是污染信号。快照（还原依据）：
  > `/tmp/xidu_ledger_snapshot_20260926_135145/token_ledger.jsonl`，
  > SHA-256 `c54772a367f469de557049380b8e4e9d773fe232830d2c155546bb90298ea571`。
- 工作区改动**未提交**：**35 M，9 ??**（本轮在上轮 27 M 基础上 +8 个新改文件）
- `pytest` 裸跑**恒 ¥0**（conftest 强制的机器可检不变量，非文档声称）

**两个恢复快照**（如需回退到某状态，`cp -a` 回去即可）：
- `/tmp/westward_snapshot_20260923_215456` — 本轮开工前（= 上轮结束态，349 文件）
- `/tmp/westward_snapshot_post_fix_010235` — 本轮全部修复后、最终变异验收前

---

## 一、条款1（工程审计）本轮做了什么 —— **已完成**

上轮遗留的"全量变异重跑"已跑完并修完。第一轮重跑 610 个变异体挖出 **21 个存活盲区**，全部处置，**0 个靠登记豁免收尾**（豁免会因行号移动变 STALE，删除/补测试不会）：

### A. 补测试杀死（13 个）
| 模块 | 存活变异体 | 补的测试 |
|---|---|---|
| `measurements.py` | 9 个（数字常量 4→5/6→7/7→8/8→9/9→10、split、return→None） | `tests/test_measurements.py` 新增 4 个测试类 |
| `job_store.py:395` | 表不存在分支 `return {}`→`None` | `test_returns_empty_dict_when_glossary_table_absent` |
| `job_store.py:26` / `error_tracker.py:25` | `check_same_thread=False`→`True` | 跨线程使用连接的测试（实证该参数承重） |
| `semantic_store.py:65` | `_ready=False`→`True` | `test_store_is_not_ready_during_init`（**初始化期探针**） |
| `idioms.py:102` | `reverse=True`→`False` | 最长匹配契约测试（注入前缀对使其可观测） |

### B. 删死代码消除（8 个变异点 / 4 处死代码）
| 位置 | 为何是死代码 | 删除前验证 |
|---|---|---|
| `cultural_rules.py` `_MIN_UNIQUE_TERMS=3` | 定义后全项目从未读取（20260921 文档已列却未修=烂尾） | grep 确认 0 引用 |
| `measurements.py` `_format_number` 的 `if n>=1000` 分支 | 与后续分支输出恒等 | 穷举 201,162 数值 + 端到端全量比对，0 差异 |
| `measurements.py` 两处 `len(parts)>1` | 外层 `if "万"/"亿" in s` 已保证 `split(sep,1)` 必产生 2 段 | 16,678 样本 0 反例；30,095 输入差分 0 差异 |
| `style_memo.py` 内层 `block[:budget]` | 被外层 `result[:max_chars]` 完全覆盖 | 5,250 组合穷举，0 差异 |

### C. 连带修复的真实生产 bug（文档未记录）
**`寸` 单位静默丢弃**：`寸` 被 `_NUMERAL_RE` 匹配、`detect_measurements` 返回 `{'寸':[...]}`，但 `UNITS` 字典无 `寸` 键、`build_measurements_hint` 的单位遍历元组也无 → 含"三寸/七寸"的章节得到 `## MEASUREMENT LOCALIZATION` **空标题且无单位行**，且 `_example_for(phrase,"寸")` 抛 `KeyError`。
**根因**：单位清单存在**三份拷贝**（正则字符类 / `UNITS` / hint 遍历元组 + 旧的 if/elif 链），漂移即静默丢数据。
**修法**：正则与遍历都**派生自 `UNITS`**（单一来源），补 `寸` 的换算（1 chi = 10 cun，scale 0.033 m）。
**验证**：38,143 次比对证明原 7 单位输出**逐字不变**。

### D. 一个必须记录的自我纠错（防重犯）
`semantic_store.py:65` 一度被我判"等价不可杀"——**错了**。原因：观测点太弱，只在构造**完成后**读 `_ready`，那时行73/76已覆盖行65初值。改用 `_init_chroma` **内部探针**（行72调用时，行65已执行、行73未执行）后实证：原版 `False` vs 变异 `True`，**可区分→可杀**。差点登记一条错误的豁免证明。
**教训**：判"等价不可杀"前，观测点必须覆盖变量被覆盖**之前**的时刻。已写入项目记忆 `feedback_mutation_equivalence_observation.md`。

### E. 还原与安全校验（铁律2）
`.mutation_backup` 残留 **0**；`src/`/`tests/`/`scripts/` 与含修复快照**逐字节一致**；四指纹零污染。

---

## 二、15 条计划（权威状态见 TodoList，此处给每条下一步）

> 用户指定执行顺序：**1 → 5 → 3 → 11 → 12 → 13 → 14 → 2 → 10 → 8 → 9 → 7 → 6 → 4**（条款1 已完成，下一个是 **5**）。
> 第 15 条是本轮审计中新拆出的，附在末尾。

| # | 条款 | 状态 | 下一步动作 |
|---|---|---|---|
| 1 | 工程审计（假绿/假变异/断头/死代码） | ✅ **已完成**（变异全绿） | 见 §五 移交项 circuit_breaker（已拆为第15条） |
| 5 | 交接文档逐条验证 + 烂尾修复 | 🟡 **进行中（下一个大条目）** | 见下方 §三 |
| 3 | 模型变更质量回归 | 🟡 成本已重算完 | 只剩质量回归，需真实 API → **先报预算等批准** |
| 11 | Agent 定位通用性（剥离恐怖/美国绑定） | ⬜ 清单已查明 | 见 wip §六第11条：15 处硬绑定 + 传递链断点，逐条参数化 |
| 12 | 思考链档位成本优化 | ⬜ 现状已查明 | 最大杠杆：`MODEL_MAP` 三节点（READ/READBACK/REVIEW_TERMS）共用 `QWEN_MODEL`，READBACK 不需 max 档 |
| 13 | TM/TB 知识资产 + 命名空间隔离 | ⬜ 缺口已查明 | TB 扎实、**TM 完全空白**、FIX 回写闭环不存在、`scan_deliverable.py` 不存在 |
| 14 | 全量章节扫描与坏章修复 | 🟡 **坏章范围已扩大查明** | 见 §四-A：**比文档记录的严重**，报告"章节完整 2301/2301"不成立 |
| 2 | 端到端质量验证 + 西语启动 | ⬜ 未开始 | 前置依赖第11条（否则西语仍跑"美国读者"视角） |
| 10 | READBACK 盲读独立性 | 🟢 基本通过 | 只剩 `graph.py:155` 角色中文名泄漏（元数据级，极低） |
| 8 | Qwen-MT 嵌入位置评估 | 🟡 **官方文档已查完** | 见 §四-B，方案须**经用户确认后**再实施 |
| 9 | 主笔 agent 评估 | ⬜ 倾向"缺" | 核实 style_memo 是否真被 WRITE/FIX 消费（初判是"经验注入"非"纵向权威角色"） |
| 7 | 不降质降成本 | ⬜ 与12强耦合 | 基线已有（¥0.5/千英文词），合并到12推进 |
| 6 | API 调用管控 | 🟡 大半落地 | 剩 **20 个"跑就花钱"无闸门脚本**（清单见 §四-C） |
| 4 | 项目边界（剧本/游戏线） | ⬜ 残留已查明 | 2 个剧本脚本（未被引用）+ Dockerfile/pyproject 版本矛盾（见 §四-D） |
| 15 | circuit_breaker 死配置处置（本轮新拆出） | ⬜ **待用户拍板** | 见 §五，三选一，我建议"暂不动先记待办" |

---

## 三、第5条怎么做（下一个大条目）

逐条验证四份 handoff 的"已完成"声称，未通过的判为烂尾**并当场修复**（用户第0条：标记不是交付）。
**方法**：每个声称 → 找代码实现 → 找测试覆盖 → 实跑验证。三者缺一即烂尾。

### 本轮已顺手验证的（可直接引用，别重查）
| 声称 | 验证结果 |
|---|---|
| `round2 §2.1` 的 stats.py 数学等价证明 | ✅ **成立**：代码确为 `recent_5m/5.0`（int÷5），`_max_recent=200`，k 全域（含负值）0 反例 |
| `HANDOFF_20260921.md` 的 657 变异体数字 | ❌ **确认过时**，实际 985（修复死代码后 958） |

### 待验证的高风险声称（都是文档说完成、尚未逐条查的）
- `audit_untrusted §四` 的 6 项"已验证修复"：READ→qwen3.8-max、截断检测 `_was_truncated`、内容过滤兜底(code 1301)、场景级完整性 `completeness.py`、curation gate 数字判据、删死代码(`_has_digit`/`GLM_READ_EFFORT`)。**逐项要查：代码实现在不在、测试覆盖到没到、实跑过没过。**
- `audit_untrusted §五` 自承认 `write_guards` 变异"未重跑、数字作废"——本轮全量已重跑，`write_guards` 60 mutants 全杀，可勾掉这条遗留。
- wip §四 提到的"计时样板 `assert latency_ms>=0` 一个都杀不死"——已被上轮推翻，确认是错的，别再据此清样板。

---

## 四、本轮另外查明的结论（接手可直接用，别重查）

### A. 条款14 坏章范围 —— **比文档记录严重得多**（实测 `novels/output/difu_segmented/difu_en.md`，204309 行 / 2301 章）
文档只记"地府第1472章 JSON 残留"，实测：
1. **3 处 JSON 残留，不是 1 处**：独立成行 `{` 在行 **31767**（Chapter 366《活学活用》内）和行 **134168**（Chapter 1472）。行 81010 `"Zhang Chao": "..."` 是**台词对白不是残留**，勿误判。
2. **Chapter 1472 双重损坏**：整章译文封在 `{"translated_text":"..."}`（行134169，单行4737字符）从未解包，**且在句中被截断**——结尾 `not the pink of embarrassment, but the`，无闭合引号、无 `}`。
3. **章节编号系统性偏移**：英文 `Chapter N` vs 中文 `第M章`，偏移 2→1772章 / 3→310 / 1→155 / 4→55，另 302/362/502 各若干。根因：首行 `## Chapter 0: Prologue（楔子）` 占 0 号，正文从 `Chapter 2` 起（无 Chapter 1）。
4. **5 处中文章号重复映射到不同英文章号**（第1046/1047/1048/1093/1227章 各出现两次，标题不同）——是映射错乱不是内容重复；英文章号本身无重复无缺口。
5. **`_report.json` 的 `passed` 声称「章节完整 2301/2301」「空译检测 0 章」不成立**——1472 章整章不可读。完整性检测只数章节标题、不看正文是否真解包成功，是典型"假绿"。
**做条款14 全量扫描时判据**：不能只数标题，须校验 (a) 正文无 JSON 结构残留 (b) 每章非空且结尾完整 (c) 英中章号映射单调无重复。

### B. 条款8 Qwen-MT —— **官方文档已联网查完**（方案须用户确认后再实施）
- 形态：阿里云百炼**独立翻译专用模型**（基于 Qwen3），型号 `qwen-mt-plus`(旗舰)/`qwen-mt-flash`(首选)/`qwen-mt-lite`(低延迟)/`qwen-mt-turbo`(已停更)。走 **OpenAI 兼容端点**，参数经 `extra_body={"translation_options":{...}}`，与现有 DashScope 调用方式兼容。
- 定价（元/百万token，华北2）：plus 入1.8/出5.4；flash 入0.7/出1.95；lite 入0.6/出1.6。flash 综合约 ¥0.002–0.004/千英文词，**远低于项目 ¥0.5/千词基线**。
- 语言：plus/flash 支持 92 种含中→英、中→西；lite 31 种也含西语。
- **硬约束**：单次最大输入 8192 token / 输出 8192 / 总 16384。项目章节 4000–14000 英文词 ≈ 6000–21000 token，**必须分段**。TPM 限流 25000(plus)/35000(flash)，约每分钟 1.5 章。
- **定制能力（关键利好）**：原生支持 `terms`(术语干预，对接 glossary)、`tm_list`(翻译记忆，对接 style_memo)、`domains`(领域提示)。仅单轮、不支持 System Message、不支持上下文缓存/批量。
- 三个候选嵌入方案（**我的分析推断，非官方结论**）：A 替换 WRITE 为 MT-flash + 术语注入；B MT 出初稿→现有 WRITE 润色；C 分级（高价值章走 WRITE，低价值章走 MT）。
- **待用户批准预算才能实测的**：中→英文学翻译实际质量、8K 分段后跨段连贯性、`terms`/`tm_list` 数量上限、TPM 限流对整书吞吐影响、中→西质量。
- 来源：help.aliyun.com/zh/model-studio/qwen-mt-api、/model-pricing、/machine-translation、/qwen-mt-plus、/rate-limit

### C. 条款6 无闸门脚本清单（共 23 个会花钱脚本，仅 3 个有闸门，**20 个裸露**）
- 已有闸门（合格）：`auto_triage.py`(默认dry-run，`--apply`才调)、`adversarial_test.py`(`--skip-api`+查key)、`seed_from_analyze.py`(`--dry-run`)；`cost/estimate_offline.py` 纯离线无需闸门。
- **20 个裸露**（跑就花钱，无任何确认/预算提示）：`run_novel.py`、`run_one_segment.py`、`run_script_pilot.py`、`run_full_test.py`、`batch_test.py`、`backfill_chapters.py`、`translate_and_verify.py`、`e2e_verify.py`、`audit_e2e.py`、`audit_translate.py`、`ab_test.py`、`ab_memo_test.py`、`test_jianke.py`、`test_difu.py`、`test_multilang.py`、`verify_fidelity.py`、`probe_glm_filter.py`、`probe_deepseek_concurrency.py`、`diag_api_hang.py`、`evaluate_native.py`、`compile_script_sample.py`、`cost/probe_nodes.py`、`cost/probe_nodes_wide.py`。
- conftest 封网机制（毒化凭据 + socket 拦截 + ChatOpenAI 构造拦截）依赖 pytest fixture，**scripts/ 无法直接复用**；建议提取 socket 拦截+凭据毒化为 `scripts/_cost_gate.py` 共享模块，提供 `require_budget_confirmation()`，裸露脚本入口调用。可参考 `scripts/cost/probe_nodes.py:19-38` 的 `_guard_tier()` 模式。

### D. 条款4 边界残留
- 剧本残留 2 脚本：`scripts/compile_script_sample.py`（客户交付样本生成器）、`scripts/run_script_pilot.py`（短剧 pilot 冒烟）。**grep 确认两者均未被任何地方引用**。删除属破坏性操作，需用户确认。
- **版本矛盾**：`Dockerfile:1` = `python:3.11-slim`，而 `pyproject.toml` 的 ruff/mypy 声明 `py39`/`3.9`，并据此 `ignore=["UP045"]`（理由"3.9 下 PEP604 union 语法 import 时崩溃"在 3.11 已不成立）。运行口径与 lint 口径不一致，需统一。
- 另：`src/evaluator_prompts.py`、`novels/output/quanzhi_fanwai_segmented/` 也是 869ce2e 声称"drop script/game branch"后的残留。

---

## 五、待用户拍板（不要自行执行）

### 第15条 circuit_breaker 死配置（本轮审计新拆出，**已两次向用户说明，等决定**）
事实（grep/读码确认）：`src/circuit_breaker.py` 的 `half_open_max`(L38,52) 与 `window_seconds`(L39,53) **只在 `__init__` 赋值、全代码从不读取**；docstring(L46-47) 承诺的"HALF_OPEN 试探限流""滚动窗口失败计数"**两种行为根本未实现**。真实行为：连续失败计数（成功即清零）+ HALF_OPEN 写死成功1次恢复/失败1次再跳闸。`tests/test_fault_injection.py:311-319` 断言这两个从不被使用的字段值 = **假绿**（变异能杀但杀的是空壳）。
潜在风险：API 在窗口内零星失败但夹着成功时，连续计数被清零、永不跳闸，持续往半坏 API 烧钱。
三个方向：(a) 删死配置 + 删假绿断言，代码与注释如实反映现有语义，零行为变化；(b) 真正实现滚动窗口 + 试探限流并补测试，**改变生产熔断跳闸/恢复时机**，需更充分验证；(c) 暂不动先推进条款5。**我建议 (c)**：它要么改语义要么删功能、都该用户拍板，且不在变异盲区清零的关键路径上。

### 上轮遗留、仍未拍板的三件（见 round4 §四，依旧有效）
1. **5 本书的 `name_en`**：代码现回退到 `book_id` 的 ASCII 化并打印 NOTE（`scripts/run_novel.py:171-173`），**未臆造英文书名**。需填 `NOVELS[key]["name_en"]`：无限恐怖 / 地府叫我小先生 / 间客 / 全职高手番外之巅峰荣耀 / 愿你灿烂如阳。
2. **回改已污染历史语料**（破坏性）：`novels/output/difu_segmented/difu_en.md` 首行 `# 地府叫我小先生 — English Translation`、`## Chapter 0: Prologue（楔子）`。代码已修，存量未回改。（与 §四-A 的坏章问题相关，可一并考虑）
3. **清理 `output/` 约 404 个测试残留**（破坏性，全部未被 git 跟踪，删了不可恢复）。

---

## 六、铁律（违反过，写下来防重犯）

1. **任何"完成"字样都要能追溯到实测证据**——本系列已推翻自己 4 次（含本轮 semantic_store:65 的误判，见 §一-D）。
2. **变异测试必须串行**，期间禁止并发跑 pytest、禁止编辑任何文件（脚本原地改写源文件）；跑完 `diff` 校验源文件还原 + `find *.mutation_backup` 为 0。
3. **变异工具改动后必须验证"变异真的生效"**；**判"等价不可杀"前，观测点必须覆盖变量被覆盖之前的时刻**（§一-D 的教训）。
4. **优先消除而非豁免**：能补测试就补测试，是死代码就删（删除前必须穷举/端到端差分证明行为零变化，不能靠"看起来冗余"）。豁免条目会因行号移动变 STALE。
5. **工具的 SKIP/STALE/blocked 必须计入失败**（陈旧条目 = 没验证但看起来绿）。
6. **真实 API 调用一律先报预算等批准**（第6条）。
7. **破坏性操作先请用户确认**。
8. **别凭记忆引用字段/函数名**——历史上多次写了不存在的东西（`esc()`、`config.version`、`StyleMemo` 实为 `StyleMemoStore`、`SemanticStore` 实为 `SemanticGlossary`、`GENRE_KEYWORDS` 实为 `_GENRE_SIGNALS`）。读码确认后再写。

---

## 七、本轮改动文件清单（35 M 中本轮新增/修改的 9 个）

**源码（4）**：`src/measurements.py`（寸修复 + 表驱动重构 + 删死分支 + 删冗余守卫）、`src/cultural_rules.py`（删 `_MIN_UNIQUE_TERMS`）、`src/style_memo.py`（删内层冗余截断）、`src/glossary/exact_store.py`（删冗余长度守卫）。
**测试（5）**：`tests/test_measurements.py`（+148）、`tests/test_idioms.py`（+61）、`tests/test_job_store.py`（+跨线程&表不存在测试）、`tests/test_error_tracker.py`（+45 跨线程）、`tests/test_semantic_store.py`（+43 初始化期探针）。
其余 26 M + 9 ?? 是上几轮遗留、本轮未动。改动**全部未提交**。
