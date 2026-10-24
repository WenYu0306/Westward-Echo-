# 西渡 · 交接（2026-09-26 第12轮 · 零成本4项 + A/B 脚本 + 档位对比实跑与判读 · **新窗口从这里开始**）

> **本文件是最新入口。** 上一轮 `HANDOFF_20260926_round11_clause6_16_9_4_15.md` 的全部改动**已提交**，不要重做。
> 本轮把 round11 授权范围内的**四件事全部做完**：① `destructive_attacks.py:821` 死三元 ② `start.sh` 陈旧项 ③ 账本归零 + 指纹更正 ④ 搭 READBACK 档位 A/B 脚本（含 **158** 条测试）。共 **5 个提交**（`1da931a`→`bcd1d76`）。
> ✅ round11「⚡ 用户已拍板」四项：**#1 已执行、#3 已由 round11 执行、#4 已执行完毕**；**#2（¥10.87 那批）用户本轮已明确批准"A/B + 20 章实测基线 = ¥8.99"并已执行、已判读**（结果见 §二-2）。
> 📌 **铁律6 已走完**：脚本搭好 → dry-run 试算出 ¥10.61 → 报给用户 → 用户选定档位 → 才调 API。**B 档 6 章重译（¥3.72）未执行**（用户单独确认才做，见 §二-3）。
> 🎯 **本轮头号结论：READBACK 降档到 flash 暂不采纳**（§二-2b）——两个判据方向相反，唯一的"显著"经 Bonferroni 校正后不显著，噪音基线证明同档位重跑本身就有同量级抖动。数据不可再生、已入库 `archive/ab_readback_tier_20260926/`。
> ⚠️ **实跑还查出我自己写的判据有 3 处缺陷并已修**（§三-0）：其中"显著性判断尺度不匹配"会让脚本第一版给出**相反的错误结论**。这是本轮最重要的教训，也是交接里最该先读的一节。


## 🛑 先读这里

### A. 本轮实测查出、会改变你判断的 6 件事

| # | 事实 | 影响 |
|---|---|---|
| **1** | ⚠️ **`_quality.json` 的 59 条记录只有 58 个唯一章号：Ch260 重复两次**（同档位、相隔约 2 小时） | 这不是脏数据，是**组内方差样本**：`engagement_gaps` 0 vs 2、`output_len` 5626 vs 5943。READBACK temperature=0.1 且全仓零 seed → **同档位重跑本身就抖 ±2**。交接 §B-2 定的"计数差"判据缺这个噪音基线，delta 1~2 会被误读成档位效应 |
| **2** | ⚠️ **章节定位口径：EN 章号 == `split_chapters` 的 index**，而 index 与标题里的中文号偏移**不固定**（实测 1/2/3/4 四种，另有 302/362/502 异常） | 拿标题中文号去索引源文件会**取到错的章**。我先犯过这个错（按 267 取到 index=267 的"第265章"）。B 档重译若照此做，会重译 6 个错的章 |
| **3** | ⚠️ **`destructive_attacks.py` 第 9 节裸跑会写生产库**（交接未记） | `cb.call(lambda: 1/0)` 触发熔断跳闸 → `circuit_breaker.py:189` → `error_tracker.record_event` → 写 `data/translation_events.db`。这正是 round11 §D 那个陷阱，但它躺在一个**现成脚本**里。要跑它必须先按 conftest 做法重定向写路径 |
| **4** | ⚠️ **`start.sh` 是 3 处 `python3`，不是交接记的 2 处** | 交接 §B 记 ":7 与 :43"，漏了 `:26` 的 pre-flight 内联块（`python3 -c "from src.health import ..."`）。只改两处的话，预检用 3.9 去 import 按 3.11 写的 src，而服务本体用 3.11 —— **预检结果与实际运行的解释器不是同一个** |
| **5** | ⚠️ **`.env.example` 缺 `GLM_API_KEY`/`GLM_BASE_URL`，且 `LLM_MODEL=qwen-plus` 是僵尸变量**（交接未记） | 前者意味着"Copy .env.example to .env"这条文档指明的路径在修好凭据门后**必然启动失败**；后者全仓 `getenv("LLM_MODEL")` 读取 **0 次**，真实变量是 `QWEN_MODEL`（默认 qwen3.8-max）——照抄模板的人会以为在跑 qwen-plus |
| **6** | ⚠️ **`docker-compose.yml` 缺 `GLM_API_KEY`/`GLM_BASE_URL` 透传，却还在传 3 个已废弃的 `DEEPSEEK_*`**（**未修，未授权**） | 按 `health.py:check_config` 判据，容器部署会因缺 GLM 而报 unhealthy。属"动部署配置"，与条款6 写库侧同档，**本轮未授权故只记录** |

> **更正（2026-10-24，防漂移）**：本条与 §五、§七 记录的「docker-compose 缺 GLM 透传」**已由 `c2a8e0e` 修复**（删 4 个废弃变量 `DEEPSEEK_API_KEY`/`DEEPSEEK_FLASH_MODEL`/`DEEPSEEK_PRO_MODEL` + 僵尸 `LLM_MODEL`，补 `QWEN_MODEL` + `GLM_API_KEY`/`GLM_BASE_URL`/`GLM_MODEL`/`GLM_FULL_MODEL`，api/worker 两服务同步，`deploy.sh` 同步对齐）。原「未授权」仅指第 12 轮当时；读到本节请勿再把它当「仍未修」。

### B. 未完成项总账（8 项）

| 类别 | 项数 | 具体 |
|---|---|---|
| **已判读、待用户决策** | 1 | §二-2b READBACK 降档**暂不采纳**（判据方向相反、校正后不显著）。若要再验需加大基线章数（≥40）或先给管线加 seed，**要用户定** |
| **需用户单独批** | 1 | §二-3 B 档 6 章重译 ¥3.72（**本轮未执行**，不在 A/B 脚本里，要走 `run_novel.py` 类入口；⚠️ 必须用 `index == EN章号` 定位） |
| **本轮遗留、低成本可做** | 2 | ① §三-2 `ab_readback_tier` 变异测试需**重测**（口径修正前的 129/176 已失效），清零后才可登记进 TARGETS ② §二-2d 试算模型缺陷：假设 A/B 两组 token 数相同，实测 flash 档 out 多 12.1%，要给别的降档方案报价前必修 |
| **需预算但金额未算** | 3 | 条款2 端到端+西语启动、条款8 Qwen-MT 嵌入评估、条款7 不降质降成本 |
| **需用户定方向** | 2 | ① §五 条款6「写库」侧闸门（要反转 `CHECKPOINT_DB_PATH` 默认值；另 `docker-compose.yml` 缺 GLM 透传，同属这一档）② 位次11 条款3 模型变更质量回归（方案2 对它无效，需单独设计一批预算） |
| **建议单独立项** | 1 | §二-2c 条款10 泄漏：实测 **58/58 章**的 `cold_read_context` 都含中文源名（来自 `graph.py` `_build_cold_read_context` 第3段透传 `style_memo.read_all()` 原文）。READBACK 的"严格盲读"在生产中并未完全成立。条款10 只修了角色名册段，术语段是残留。**本轮未擅自修改生产函数**（超授权） |

### C. 硬规矩（承接 round9 §一/§九、round10 §C、round11 §C，本轮全程遵守）

- **必须用 `python3.11`**（本机 `python3` 是 3.9.6）。
- **任何写库验证必须在 pytest 内跑**（铁律16）。裸跑时 `CHECKPOINT_DB_PATH` 指向生产库。
- **变异还原只能用 `/tmp` 快照 + 哈希校验，严禁 `git checkout`**（铁律11）。变异必须串行，期间禁并发 pytest、禁编辑文件；跑完校验 `.mutation_backup` 残留 0。
- **断言不能假绿**（铁律12/15/17）：绝不用被测常量推导测试输入和期望。本轮所有新测试断言用字面值；金额公式刻意用**精确断言**而非 `approx`（见 §三-4）。
- **铁律6**：调 API 前报具体金额、等用户明确批准。本轮完整走了一遍（§二）。

### ⚠️ D. 我本轮的两次自我纠错（记录在案，防重犯）

1. **一度按标题里的中文号去索引源文件**，取到错的章（index=267 的标题是"第265章"）。实测纠正：定位必须用 `index == EN章号`。纠正后 B 档 6 章源汉字合计 **9683**，与 round11 §二-2a 逐位一致。
2. **`compare()` 的 worse/better 分类有双重计数缺陷**：原写成两个独立列表推导（`ci_delta>0 or eg_delta>0` 与 `ci_delta<0 or eg_delta<0`），于是"ci 改善但 eg 恶化"的章**同时**落进两类，worse+better+same 超过共同章数。是我自己写的测试（断言三类之和 == 共同章数）抓出来的。改为互斥归类（质量风险优先）。

### ⚠️ E. 关于"文件被修改"的系统通知（本轮又出现多次）

本轮多次收到 `scripts/ab_readback_tier.py` 被修改的通知，展示 `copy_memo: bool = False`、`1e6` 变体等**变异态**。这些是变异测试运行期间的磁盘快照，**不是文件真实状态**。按 round11 同样的四重复核（`git diff --stat` + 字符串核验 + `py_compile` + pytest）确认还原正确：`copy_memo: bool = True`、148 passed、`.mutation_backup` 残留 0。
**别据此去"修复"** —— 而且我已有测试专门钉住这个默认值（`test_memo_copy_is_on_by_default`），变异翻成 `False` 会被杀掉。

---

## 〇、基线（实测，非声称）

```bash
python3.11 -m pytest -q --no-header                    # → 1840 passed, 4 deselected
python3.11 scripts/static_audit.py --self-test         # → 27/27
python3.11 scripts/static_audit.py                     # → 41 文件，0 条发现
python3.11 scripts/static_audit.py --include-src       # → 0 条发现
```

- **1840** = round11 基线 1682 + 本轮 **158 个新测试**（全部来自 `tests/test_ab_readback_tier.py`）
- `pytest` 裸跑**恒 ¥0**（conftest 封网 + 重定向）
- 生产库 `data/` 在 git 视角**无任何改动**；本轮开工前后三个库的 SHA-256 **逐字节一致**（A/B 实跑后也复核过）
- **账本已归零**：`data/token_ledger.jsonl` = 0 行（round11 是 8816 行；A/B 实跑 136 次调用后**仍是 0 行**，写路径隔离生效）

本轮触及的变异目标：

| 目标 | 结果 | 备注 |
|---|---|---|
| `cost_gate` / `scan_deliverable` / `style_memo` / `circuit_breaker` / `graph_post_process` | 未重跑 | 本轮未改这 5 个文件的源码，round11 的 15/76/61/45/73 全 0 存活结论仍有效 |
| `ab_readback_tier` | ⚠️ **需重测**（口径修正前的 129/176 已失效） | 🆕 新脚本。**刻意未登记进 TARGETS**，理由与重测方法见 §三-2 |

---

## 一、本轮完成的四项（提交序：新→旧）

### 1. 搭 READBACK 档位 A/B 脚本（`fab6c51`，§B-2 问题① / round11 决定#2 的前置）

全仓此前**没有任何能做档位 A/B 的工具**（实测：`ab_memo_test.py` 比的是备忘 ON/OFF；`generate_report.py:219-221` 与 `translate_and_verify.py:436` 只**读**既有 `_quality.json` 做汇总）。新增 `scripts/ab_readback_tier.py`（1198 行）+ `tests/test_ab_readback_tier.py`（1408 行 / 148 测试）。

**严格遵守 round11 §B-2 问题②③ 的四条判据约束**：

| 约束 | 实现方式 |
|---|---|
| 两组喂**同一份已交付译文** | 从 `difu_en.md` 按章号切正文，A/B 共用同一字符串。不重跑 WRITE |
| **不用** verdict 一致率 | 主判据 = `comprehension_issues`/`engagement_gaps` 计数差 + `impression` 文本差异。verdict 一致率仍算但**显式标注"无信息判据"**并写明原因（59 条全 PASS） |
| **必须关掉 FIX 循环** | 不走 graph、不调 `translate_chapter`，直接调 `readback_node` —— FIX 循环**结构性不可能触发**，不是"关掉"而是根本不经过那条路由 |
| 重建 `cold_read_context` 遵守条款10 | **复用生产 `graph._build_cold_read_context`**，不自己拼。实测角色名册 15 条**纯英文、0 条含中文**；同章两次构建**逐字节相同**（有测试钉住，这是归因前提） |

**施工中查出并修掉的 3 个真缺陷**：

1. **好坏章双重计数**（见 §D-2）—— 我自己写的测试抓出来的
2. **金额公式三处副本**：`tokens/1e6*price` 在 A/B 组、FIX 每章、B 档逐章各写一遍 → 抽成模块级 `cny_from_tokens()` 单一来源。合计金额同样抽成 `grand_total()`，避免报告与结果 JSON 两处加法漂移（**那是要报给用户批钱的数字**）。与条款6 修成本闸门同类缺陷
3. **硬编码的标定值**：交叉校验一度写死 `2473.5/7350`、`1286.0`、`2881.0`、`4992`、`7016` —— 那就是"第二套会漂移的成本模型"。改为 `crosscheck_basis()` 从 `scripts/cost/measurements/` 派生，标定缺失时**硬失败**而非回退到写死数字。实测派生值与原写死值一致

**FIX 成本口径用 mean 而非 median（实测校正）**：`estimate_offline.py:150` 写死的 4992/7016 实测是标定点的 **mean**（4992.4/7016.5），不是 median（4900/7509.2）。用 median 会让每章 FIX 成本从 ¥0.2364 漂到 ¥0.2495，6 章 ×50% 差 ¥0.039 —— 钱不大，但会让 B 档金额与 round11 §二-2a 的 ¥3.72 **无缘无故对不上账**，进而让人怀疑整份试算。故对齐 mean，有测试钉住。

**⚠️ 与生产的已知偏差（如实记录，别当"完全等价"）**：`cold_read_context` 三段里，**"PREVIOUSLY 近章摘要"无法从交付产物重建** —— 生产它来自 FIX/READ 产出的 `chapter_summary`（`graph.py:_update_context` 累积最近 5 章），而交付产物只留了 `_checkpoint.json.previous_summary`（**最后一章**那一条），58 个抽样章各自的近章摘要没有落盘。→ 两组用**同一个**降级值（`--prev-context mechanical` 取前一章英文开头 400 字符，显式标注 `not a production summary`），所以档位仍是唯一变量、组间差异仍可归因，但**绝对判定值不可外推到生产**。脚本输出打印实际用了哪一种。

**条款10 泄漏：如实检出，不擅自改生产函数**：`_build_cold_read_context` 第 3 段 "ESTABLISHED TERMINOLOGY" 透传 `style_memo.read_all()` 原文，实测 **7/8 条含中文源名**（如 `阴兵过境 → Underworld soldiers passing through`）。条款10 只改了角色名册那段（`graph.py:181`），**术语段是生产路径上的残留泄漏，不是本脚本引入**。`--report-leak` 会检出并归因到正确位置（指向 `graph.py` 第3段透传，避免后人修错文件）。两组共用同一 context，不影响归因。**未修改该生产函数**——超出本轮授权范围。

**安全设计**（默认零成本 + 写路径强制隔离）：
- 默认 dry-run，一次 API 都不调；真跑需 `--execute` **且** `--allow-expensive`，缺一个即退出码 2。实测四种参数组合退出码均正确
- ⚠️ **本脚本必须自带写路径隔离**（在任何 `import src` 之前设好，因为 `config.DATA_DIR` 等常量在 import 时求值）。`readback_node` 会 ① `token_ledger.record` 追加账本行、② verdict==NEEDS_FIX 时 `error_tracker.record_event` 写 `translation_events.db`。**账本本轮刚归零、"0 行"现在是污染指纹**，跑一次就污染回去等于毁掉本轮清理。实测：三个库 SHA-256 与运行前逐字节一致、生产账本仍 0 行
- dry-run 不复制备忘（不构建 cold_read_context），省 5.8 MB；退出时 `atexit` 清理临时目录。真跑则**保留**，便于核对账本与 worker 输出
- 每组写**独立账本文件**（`ledger_A`/`ledger_B`/`ledger_baseline`），否则基线遍的 token 会混进 A/B 的金额对账

### 2. 账本归零 + 更正 round5 §〇 指纹（`d50b4b6`，round11 决定#1）

**清理前独立复核，数字与 round11 完全一致**（非照抄）：物理 8816 行 / 非空 8816 / 解析失败 0；"生产"（book_id 非空且不以 test 开头）2 行、测试残留 8814 行。那 2 行明细 `book_id=diag_test` / `node=read` / `model=qwen3.8-max` / cost **¥0.42102 与 ¥0.467964**，与 round11 §二-1 记的 ¥0.421/¥0.468 逐位对上。

**铁律7 快照（还原依据，唯一）**：
```
/tmp/xidu_ledger_snapshot_20260926_135145/token_ledger.jsonl
  SHA-256 c54772a367f469de557049380b8e4e9d773fe232830d2c155546bb90298ea571
  8816 行 / 1,468,874 字节
```
还原只能 `cp` 这份快照回去，**严禁 `git checkout`**（`data/` 被 `.gitignore:17` 排除，git 里没有它）。

**四步核验全部通过**：① 快照 + SHA-256 ② 8816 行全删（`: > ` 保留空文件，因为 round11 的核验标准是 `wc -l` 应为 0，删文件会让该命令报错）③ 同步更正 round5 §〇 的 8813 → **0 行**（不静默改写历史文档：原值、失真原因、当前值、快照位置全写进更正块）④ 跑完整 pytest 后账本**仍为 0 行**（证明 `conftest.py:33-53` 的 `XIDU_TOKEN_LEDGER` 重定向生效）。

⚠️ **后续窗口请以「ledger 0 行」为生产指纹基线**，不要把任何非零行数当"原有数据"——账本归零后再出现真实行才是污染信号。round10 §四-2 要求改成"2 行"的说法已作废（round11 决定#1 推翻）。

⚠️ **未做**：round2/round4/round8/round9/round10 各文档里的 8813/8811 旧记录**未逐一改**。round9 §163 与 round10 §165 已各自判明"8813 失真"，属历史推演过程；改历史文档每一处会让"谁在哪轮改了什么"不可追溯。指纹的**权威当前值**已在 round5 §〇（被后续文档反复引用的那一处）更正。

### 3. `start.sh` 解释器钉到 3.11 + 僵尸凭据门换成真实承重 key（`1474c1b`）

**两文件同批提交**（`start.sh` + `.env.example`）：新门检查 `GLM_API_KEY`，而 `.env.example` 原先没有这个键，分开提交会留下"照模板复制 `.env` 就必然启动失败"的中间态。

**① 解释器 3 处全改**（`:7` 版本检查、`:26` pre-flight 内联块、`:43` 启动服务），另加 `command -v python3.11` 存在性检查。**为什么钉死版本号不破坏容器**（改前已核实）：`Dockerfile:1` 是 `FROM python:3.11-slim`；`start.sh` **不被任何 Dockerfile/compose 引用**（Dockerfile 的 CMD 是 `uvicorn src.main:app`；全仓 grep "start.sh" 的命中全是小说正文里的巧合字符串如 "start shooting"）→ 它是纯本机启动脚本。

**② 僵尸凭据门**：`DEEPSEEK_API_KEY` → `LLM_API_KEY` + `GLM_API_KEY`，口径与 `health.py:147-148` 的 critical 清单一致（该文件 `:137` 已修过同型缺陷并留了注释，`start.sh` 是同一个病的第二处，照它的判据改、不另立标准）。保留一条 NOTE 提示 `.env` 里残留的废弃键可删，**不自动删用户配置**。

**③ `.env.example` 供应商段重写**：删 4 行废弃 `DEEPSEEK_*`、补 `GLM_API_KEY`/`GLM_BASE_URL`/`GLM_MODEL`/`GLM_FULL_MODEL`/`GLM_WRITE_EFFORT`/`GLM_FIX_EFFORT`、`LLM_MODEL=qwen-plus` → `QWEN_MODEL=qwen3.8-max`。**未动 `CHECKPOINT_DB_PATH`**（反转默认值属条款6 写库侧，未授权）。

**④ 施工中新查出两处 shell 兼容性缺陷**（非交接内容）：
- `${!key}` 间接展开是 bash 扩展 → 改成显式两个 `if`
- `source .env` 在 dash 下**静默不加载**（dash 无 `source`，而 `2>/dev/null || true` 把 "not found" 吞掉 → 所有 key 读成空 → 场景1"两 key 齐"竟被拒）→ 改 `. ./.env`
- 实测再发现 `|| true` 在 dash 下**救不了**文件缺失：dash 的 `.` 是 special builtin，缺文件直接退出 shell（**实测 exit 2**），bash 才能继续。真实脚本里无害（`:18` 的 `[ ! -f .env ]` 已先保证文件存在），但注释必须写准，否则后人会以为 `|| true` 在 POSIX sh 下是万能兜底

**验证：六场景 × 两 shell（bash/dash）全部符合预期**，其中第 5 行是这次修复的核心价值：

| 场景 | bash | dash |
|---|---|---|
| 两 key 齐 | PASS 通过 | PASS 通过 |
| 缺 `GLM_API_KEY` | PASS 拒绝并指名 | PASS 同 |
| 缺 `LLM_API_KEY` | PASS 拒绝并指名 | PASS 同 |
| 两 key 都缺 | PASS 两个都报 | PASS 同 |
| **只有废弃 `DEEPSEEK_API_KEY`** | **PASS 拒绝（旧门此处放行）** | PASS 同 |
| 齐全 + 残留废弃 key | PASS 通过 + NOTE 1 条 | PASS 同 |

真实项目 `.env` 在新门下 PASS（两个承重 key 非空、废弃 key 仍在 → 打 NOTE 不拒绝），**现有部署不受影响**。`bash -n`/`dash -n`/`sh -n` 语法检查均通过。全仓无测试钉住 `.env.example` 内容（grep 确认）。

### 4. 消除 `destructive_attacks.py:821` 死三元（`1da931a`）

`record_result("OK" if not has_xss else "OK", ...)` 两分支完全相同、条件不可观测。与条款9 修掉的 `style_memo.py:115` 同型；变异算子只覆盖 Compare/BoolOp/bool常量/非零int-float常量/非常量return，**不含三元表达式**，所以这类死代码永远不会被变异测试抓到。

**先读懂 8.5 想断言什么再定修法**：这条攻击喂 `第1章 <script>alert('xss')</script>` 给 `split_chapters`，要断言的不是"XSS 被过滤了"，而是**两种结果都不是 chapter_splitter 的缺陷**（分章必须逐字保留文本，转义是渲染层职责，detail 里本来就写着 `renderer should escape, not chapter_splitter`）。真正需要观测的是"发生了哪一种"，而这信息已在 detail 字符串里 → 按第一种修法塌缩成 `record_result("OK", ...)` 并补注说明判定依据，避免后人再把它"修"回一个假的条件分支。

**AST 全仓复查：同型死三元已归零**（改前 1 处、改后 0 处）。这是条款9 记录的同型清单的最后一项。

**⚠️ 验证方式：不能裸跑该脚本（会写生产库，见 §A-3）**。验证时照 `conftest.py:33-53` 在 shell 层重定向 5 个写路径 + 投毒 6 个 API key。实测（隔离运行）：`Summary 0 CRASHED / 11 BUG / 69 OK`（共 80）；8.5 输出 `[OK] XSS preserved in title — renderer should escape, not chapter_splitter`，观测状态完整保留；11 个 BUG 全是既有项（1.5b/3.2/5.1/5.2/6.2/6.3/6.4/7.1/8.1/8.2/12.2），8.5 不在其中。生产库三个 SHA-256 与运行前一致、账本仍 8816 行（清理前）。

---

## 二、A/B 档位对比：已执行（铁律6 完整走完）

### 1. 金额审批全过程

| 步骤 | 内容 |
|---|---|
| ① 搭脚本 | `scripts/ab_readback_tier.py`，默认 dry-run 零成本 |
| ② dry-run 试算 | **合计 ¥10.61**（A/B ¥6.90 + B 档重译 ¥3.72）。round11 估的是 ¥10.87，差 ¥0.26 |
| ③ 差额归因 | 抽样是 **58 个唯一章号**（`_quality.json` 59 条里 Ch260 重复，不该付两遍钱）+ 拟合值略低于估算。B 档 ¥3.72 与 round11 **逐位一致** |
| ④ 报给用户 | 同时提出 §A-1 的噪音基线问题：只跑 A/B 可能拿到无法解读的结论。给出 `--repeat-baseline` 各规模报价（A 档单章实测 ¥0.1044） |
| ⑤ 用户选定 | **A/B + 20 章实测基线 = ¥8.99** |
| ⑥ 执行 | `--execute --allow-expensive --repeat-baseline 20 --report-leak` |

### 2. 执行结果

**执行命令**：`--execute --allow-expensive --repeat-baseline 20 --report-leak`
**规模**：136 次 READBACK 调用（A 组 58 章 max + B 组 58 章 flash + 基线 20 章 max 重跑）
**两组均 58/58 成功，零失败。** 数据已归档 `archive/ab_readback_tier_20260926/`（不可再生，见 §一-1 第五条）。

#### 2a. 主判据（同尺度配对检验，修正后的口径，见 §三-0）

| 判据 | A 组 (max) | B 组 (flash) | 逐章差均值 | 净效应（扣噪音） | Welch t | 判定 |
|---|---|---|---|---|---|---|
| `comprehension_issues` | 235（均 4.05） | 209（均 3.60） | −0.448/章 | **−0.848/章**（×58 ≈ −49.2） | **−2.146** | 勉强显著（p≈0.036） |
| `engagement_gaps` | 73（均 1.26） | 85（均 1.47） | +0.207/章 | +0.107/章（×58 ≈ +6.2） | **+0.387** | 不显著（p≈0.70） |
| `verdict` 一致率 | 41 NEEDS_FIX/17 PASS | 38 NEEDS_FIX/20 PASS | — | 74.1% vs 基线 80.0%（差 −5.9pt） | — | 看不出档位效应 |
| `would_keep_reading`=False | 5 章 | 8 章 | — | — | — | flash 略多 |
| `quality_score` 均值 | 2.879 | 3.034 | +0.155 | 基线抖动 +0.000 | — | 与噪音量级相近 |

逐章分布：降档后**变差 30 章 / 变好 22 章 / 持平 6 章**；`comprehension_issues` 逐章差分布 `{-6:1, -5:1, -4:3, -3:2, -2:8, -1:7, 0:17, 1:13, 2:5, 3:1}` → **分散**，不是少数极端章拉动。
`impression` 文本 58 章**全部不同**（0/58 相同），符合 temperature 0.1 下的预期。

#### 2b. ⚠️ 结论：**不足以判定降档安全，也不足以判定降档降质**

三条理由，都指向"不要据此把 READBACK 降到 flash"：

1. **两个判据方向相反**。`comprehension_issues` flash 报得**更少**（看似更宽松/更好），`engagement_gaps` flash 报得**更多**、`would_keep_reading=False` 也更多（看似更严格/更差）。降档不是单向变好或变坏，是**判定风格改变**——这本身就是风险：生产管线靠 `verdict` 触发 FIX，判定风格漂移会改变哪些章被送去修。
2. **唯一的"显著"经不起多重比较校正**。两个判据里只有 `comprehension_issues` 过 |t|>2，但 Bonferroni 校正后（α=0.05 / 2 个判据 → 阈值 0.025）p≈0.036 **不显著**；且 |t|=2.146 只是勉强过线，而基线只有 **20 章**（方差估计不稳，加大 `--repeat-baseline` 后 t 可能翻转）。
3. **噪音基线证明重跑本身就有系统性偏向**。同档位重跑的 `comprehension_issues` 逐章差均值是 **+0.400/章**（20 章里 12 章变大、6 章变小）——即重跑就倾向报更多。A/B 的 −0.448 与之方向相反，所以净效应 −0.848 不是基线漂移造成的假象；但这同时说明**这个量级的差本来就在抖动范围内**。

**建议**：READBACK 降档到 flash **暂不采纳**。若要再验，应加大基线章数（≥40 章）并考虑固定 seed（需先改代码，全仓当前零 seed）——但那是条款3「模型变更质量回归」的范畴，应单独批预算，不要混在这一批里。

#### 2c. 顺带证实的两件事

- **✅ round11 §B-2 问题② 的推断被实测证实**：历史 `_quality.json` 59 条全 PASS 确实是 FIX 循环造成的。裸 READBACK 下 verdict 有充分方差（A 组 41/17、B 组 38/20），所以"关掉 FIX 循环"这个设计是**必要的**，否则两组都会是全 PASS、什么也看不出来。
- **⚠️ 条款10 泄漏比抽样时看到的更广**：`--report-leak` 实测 **58/58 章**的 `cold_read_context` 都含中文源名（每章 7 行，来自 `_build_cold_read_context` 第3段透传 `style_memo.read_all()` 原文，例：`阴兵过境 → Underworld soldiers passing through`）。这是**生产路径上的残留泄漏**（条款10 只改了角色名册段），不是本脚本引入；两组共用同一 context 故不影响本次归因，但**READBACK 的"严格盲读"在生产中并未完全成立**。未擅自修改生产函数（超授权），留给后续判断。

#### 2d. 花费对账

| 组 | 行数 | in tokens | out tokens | 实际 | 试算 |
|---|---|---|---|---|---|
| A（qwen3.8-max） | 58 | 166,866 | 126,431 | **¥6.55** | ¥6.43 |
| B（qwen3.8-flash） | 58 | 166,866 | 141,640 | **¥0.52** | ¥0.47 |
| A/B 小计 | 116 | — | — | **¥7.07** | ¥6.90（偏差 +0.17，+2.5%） |
| 基线（max 重跑 20 章） | 20 | 57,886 | 44,583 | **¥2.30** | ¥2.09 |
| **总计** | 136 | — | — | **¥9.37** | 批准 **¥8.99** |

**⚠️ 超支 ¥0.38（+4.2%），发生在基线遍**。A/B 主体 ¥7.07 在试算 ¥6.90 附近（+2.5%）。两处偏差的成因已查明（token 级归因）：

1. **in tokens 被低估 +6.4%**（试算 156,784 → 实际 166,866）。试算用 `predict_chapter` 拟合，而拟合特征里含 `read` 节点的 out tokens 均值；实际 READBACK 的 in 还包含 `cold_read_context` 全文，拟合模型没有这一项。
2. **⚠️ 试算假设 A/B 两组 token 数相同，实测不成立**：`estimate_costs` 里 `b_in += ri; b_out += ro`（两组共用同一预测值），但实测 **flash 档的 out tokens 比 max 档多 15,209（+12.1%）**（141,640 vs 126,431，均 out 2442 vs 2179/章）——**便宜档更啰嗦**。影响：B 组试算 ¥0.47 vs 实际 ¥0.52。
   → 对 A/B 金额影响很小（flash 单价低），但**这是试算模型的一个真实缺陷**，若要给别的降档方案报价必须先修。修法：`crosscheck_basis()` 增加"按档位分别标定 out tokens"，而不是让两组共用一个预测值（需要额外的按档位标定点，目前没有）。
3. 基线遍单章实际 ¥0.1150 vs 试算 ¥0.1109（+3.7%），同源于 1。

**生产数据零污染（已核验）**：`translation_events.db` / `checkpoints.db` / `jobs.db` 三个 SHA-256 与运行前逐字节一致；生产账本仍 **0 行**；`git status data/` 为空。脚本的写路径隔离生效。

### 3. ⚠️ B 档 6 章重译（¥3.72）**本轮未执行**

用户批的是"A/B + 20 章基线 = ¥8.99"，**不含** B 档重译。两件事在脚本里也是分开的：`--execute` 只跑 A/B 档位对比，B 档只试算金额、不执行（脚本输出里有显式声明）。

B 档 = `Ch1472`（整章封在截断信封里）+ `Ch269/430/548/811/820`（句中硬截断），要**从中文原文重跑完整管线**产出新译文，走 `run_novel.py` / `translate_and_verify.py` 那类入口，不是本脚本。
⚠️ 重做时**必须用 `index == EN章号` 定位**（见 §A-2），否则会重译错的章。6 章源汉字实测 **9683**（与 round11 §二-2a 一致）：

| EN章 | 中文章 | index | 源汉字 |
|---|---|---|---|
| 269 | 第267章 来自雪尸的善报 | 269 | 1560 |
| 430 | 第428章 鬼见愁 | 430 | 1559 |
| 548 | 第546章 金甲尸 | 548 | 1590 |
| 811 | 第809章 那青牛 | 811 | 1558 |
| 820 | 第818章 枭印，羊刃 | 820 | 1918 |
| 1472 | 第1470章 不可沽名学霸王 | 1472 | 1498 |

---

## 三、下一位要接续的（含变异测试的诚实处理）

### 1. A/B 结果已判读（结论见 §二-2b）：**暂不采纳 READBACK 降档**

判读已完成，不必重跑。要点：两个判据方向相反（`comprehension_issues` flash 报更少、`engagement_gaps` flash 报更多）；唯一的"显著"经 Bonferroni 校正后不显著；噪音基线证明同档位重跑本身就有 ±0.4/章 的系统性偏向。**不足以判定降档安全，也不足以判定降档降质**。

数据已入库 `archive/ab_readback_tier_20260926/`（**不再依赖 `/tmp`**，见 §一-1 第五条：全仓零 seed + temperature>0，这批数据不可再生）。
零成本重看报告：`python3.11 scripts/ab_readback_tier.py --dry-run`（只试算、不调 API）。
判读结果时读这几个字段（⚠️ 名称已随 §三-0 的口径修正变更）：
- `significance.per_metric.<判据>.t` / `.significant` / `.net_effect_per_chapter`（同尺度 Welch t）
- `significance.interpretation`（结论文字）
- `verdict_agreement.A_distribution` / `.B_distribution` / `.baseline_rate` / `.assessment`
- ⚠️ **`any_metric_exceeds_noise` 与 `exceeds_noise` 这两个旧字段已删除**（它们属于尺度不匹配的错误比较，见 §三-0）。若你在别处看到引用它们的代码，那是过时的。

**若要再验**：加大 `--repeat-baseline`（≥40 章，当前 20 章偏少、方差估计不稳），或先给管线加 seed（需改代码）。但"整体换代"级别的回归属条款3，应单独批预算（§四 位次11）。

### 0. ⚠️ 实跑查出我自己写的判据有 3 处缺陷（已修，`bcd1d76`）—— 这是本轮最重要的教训

脚本第一版给出的结论是"**两个判据都超过噪音 → 可能是真实的档位效应**"。这个结论**不可采信**，三处缺陷都在判据实现里，实跑数据把它们暴露出来了：

**① 显著性判断尺度不匹配（最严重，会直接导出错误结论）**
`compare()` 原先比较「A/B 的 **SUM 差**」与「基线的**逐章最大差**」。SUM 差随章数线性放大（A/B 是 58 章、基线是 20 章），逐章最大差不会 —— 两者根本不在一个尺度上。

实测后果：

| | 旧口径（尺度不匹配） | 新口径（同尺度配对） |
|---|---|---|
| `comprehension_issues` | SUM 差 −26 vs 逐章噪音 3 → **"超过噪音 8.7 倍"** | 逐章配对差 Welch **t = −2.146**（勉强 p<0.05，Bonferroni 校正后不显著） |
| `engagement_gaps` | SUM 差 +12 vs 逐章噪音 2 → **"超过噪音 6 倍"** | Welch **t = +0.387**（完全不显著） |

→ 旧口径把两个判据都说成板上钉钉的信号，新口径显示一个勉强过线、一个纯噪音。**若照旧口径写进交接，下一个窗口会据此把 READBACK 降档到 flash。**

修法：`measure_within_tier_variance()` 增返 `paired_deltas`（逐章**有符号**差值，不是绝对差——噪音本身有系统性偏向，保留符号才能扣掉）；新增 `welch_t()`（不依赖 scipy）；`compare()` 的显著性判断**只认同尺度配对差值**。退回历史 1 章基线时，旧比较仍输出但显式标注 `UNRELIABLE` + "尺度不匹配"，不伪装成结论。

**② verdict 判据的措辞是错的（被实跑直接推翻）**
`compare()` 原先硬写"⚠️ 无信息判据：历史 59 条 verdict 全 PASS（零方差），此值预期恒为 100%"。实跑证明这句错 —— 裸 READBACK 下 verdict **有充分方差**：A 组 41 NEEDS_FIX/17 PASS、B 组 38/20。历史全 PASS 是 **FIX 循环**造成的，不是 READBACK 零方差。
→ 修法：基线也测 verdict 一致率，判读改为「A/B 一致率 vs 同档位基线一致率」对照（差 <10pt = 看不出档位效应），并输出两组分布。无基线时如实说"**不可解读**"，不说"无信息"（那是错的）也不说"显著"。

**③ `welch_t` 零方差分支误报"判不了"**
`se == 0` 原先返回 `(None, "无法估计抽样误差")`。但零方差意味着**没有不确定性**：净效应 == 0 → t=0.0（确定无效应）；净效应 != 0 → ±inf（确定有效应）。归到 None 会把"两组完全一致"这种最强证据当成判不了。
→ 这条是我自己写的 `test_zero_delta_is_not_significant` 抓出来的（它断言 `net_effect_per_chapter` 存在，而 None 分支不产这个键）。

**教训**：判据的**统计口径**和判据本身一样需要验证。这 3 处都不会让脚本报错、不会让测试变红（第一版测试钉的正是错误口径），只有拿真实数据跑一遍、再用另一种口径复算才会暴露。下次设计任何"比大小"的判据，先问一句：**两边是同一个尺度吗？**

### 2. 变异测试：`ab_readback_tier` **刻意未登记进 TARGETS**

⚠️ **下面这组数字已失效**：`129/176 killed、47 存活` 是 §三-0 那次统计口径修正**之前**测的（当时测试 148 条）。修正后新增 `welch_t()`、改了 `compare()` 的显著性与 verdict 判据、测试增至 158 条，判据面的变异点集合已变，**必须重测才作数**。本轮未重测（重测一轮约 20 分钟，而用户已明确"先报金额、别在变异分数上打磨"，故按优先级让路）。下一位若要登记这个目标，先重跑：

```bash
python3.11 scripts/mutation_test.py ab_readback_tier
```

**判据面的关键变异点全被杀**：章节定位、噪音基线、显著性判断、金额公式。`cny_from_tokens` 用**精确断言**（整百万 token，不用 `approx` 默认容差），所以 `1e6→1000001` 这种"偏差百万分之一"的变异会死 —— 用 `approx` 测等于放过它，而那正是"这条公式根本没被钉住"的证据。

**47 个存活点全在编排与 I/O 层**（`main()` 参数管道与进度打印 19 个、`run_group()` 的 subprocess 接线 6 个、`isolate_write_paths()` 的 4 个 mkdir/copytree 标志、`_write_out()` 4 个、其余零散）。

**为什么没登记**：既有 5 个目标全是 **0 存活**，登记一个报告 FAIL 的目标等于给仓库加天生红的门禁项。已在 `mutation_test.py` 的 TARGETS 处留 NOTE 写明现状与再登记条件，并明确**不要用 `EQUIVALENT_MUTANTS` 掩盖** —— 那是未测代码路径，不是已证明不可杀的变异。

**要清零该怎么做**：`main()` 可以 monkeypatch `run_group`/`build_agent` 来测（零 API 成本），或用 capsys 钉住进度打印。重构已把 `main()` 从 295 行/70 处 print 降到 194 行/21 处，渲染层（`render_estimate_report`/`render_verdict_report`/`render_noise_report`/`render_leak_report`）现在是**纯函数**且已有 capsys 式测试覆盖 —— 这是照 `scan_deliverable._print_human` 的模式做的（那个目标因此能到 76/76）。

**唯一确认的等价变异**：`round(c, 4)` 的 `4→5`（实测 B 档单章 0.4985，两种精度结果相同）。未登记，因为整个目标都没登记。

### 3. 重构成果（已包含在 `fab6c51`，别回退）

为让**报给用户批钱的数字可测**，把报告渲染抽成 4 个纯函数 + 金额/合计抽成 2 个单一来源函数：

| 抽出 | 理由 |
|---|---|
| `cny_from_tokens()` | 消除 3 处 `tokens/1e6*price` 副本；模块级才能被精确断言 |
| `grand_total()` | 报告与结果 JSON 的合计只有一个来源，不会漂移 |
| `render_estimate_report()` 等 4 个 | 纯函数 → 测试用 `tmp_path` 造小夹具即可覆盖，**不依赖 14 MB 交付物**（`difu_en.md` 被 `.gitignore:18` 排除、CI 上不存在）。`main()` 里内联的 print 无法在 CI 上测 |
| `crosscheck_basis()` | 消除硬编码标定值，两口径同源 |

⚠️ 重构后**金额与重构前逐位一致**（¥6.43/¥0.47/¥6.90/¥3.72/¥0.2364/¥10.61、9683 源汉字、13.8 倍），已实测对照。

### 4. 顺带修掉的浪费

- dry-run 不再复制备忘（5.8 MB）：`isolate_write_paths(copy_memo=args.execute)`，因为 dry-run 不构建 `cold_read_context`
- dry-run 退出时 `atexit` 清理临时目录（此前每次试算攒一个空目录，实测攒到 65 个）
- ⚠️ `copy_memo` 的**默认值仍是 `True`**（安全默认：不复制的话术语段退化为空、金额失真）。有测试钉住，别改成 `False`

---

## 四、剩余任务（按 round9 §六 位次，状态已更新）

| 位次 | 条款 | 状态 | 备注 |
|---|---|---|---|
| **4** | 6 · API 调用管控 | 🟡 成本侧完成 | 「写库」侧**未授权**，见 §五 |
| 收尾 | 14 · 历史坏章清理 | 🟡 A 档 4 章已完成 | **B 档 6 章未执行**（¥3.72，见 §二-3）；C 档 2 章合法不动 |
| **10** | 12 收尾 · 实际降档 A/B | 🟡 **已执行，待判读** | §二-2。脚本与判据已就绪，可重复跑 |
| **11** | 3 · 模型变更质量回归 | ⬜ 暂缓，需单独设计 | 方案2 对它无效（降档杠杆不动 WRITE/READ/FIX）。若要测整体换代，单独批预算 |
| **12** | 2 · 端到端 + 西语启动 | ⬜ 需预算 | 金额还没算 |
| **13** | 8 · Qwen-MT 嵌入评估 | ⬜ 需预算 | 金额还没算 |
| **14** | 7 · 不降质降成本 | ⬜ 需预算，与12耦合 | 金额还没算 |

**已完成 12 条**：1 工程审计 / 4 项目边界 / 5 交接验证 / 6 成本侧 / 9 主笔评估 / 10 / 11 通用性 / 12 档位（**本轮实跑**）/ 13 全部 / 14 根因 / 15 circuit_breaker / 16 术语一致性

---

## 五、条款6「写库」侧为什么仍没做（承接 round11 §五，结论未变）

库级 guard 函数**无法防住 round9 那类事故**：事故源是裸跑 `python3.11 -c`，那种进程根本不调用任何 guard，`CHECKPOINT_DB_PATH` 直接指向生产库。**本轮 §A-3 又添一个实证**：`destructive_attacks.py` 这个**现成脚本**裸跑就会写生产库——风险来自"绕开约定"，不是"约定不够多"。

真正有效的选项是**反转默认值**（`CHECKPOINT_DB_PATH` 默认指临时库，生产需显式设 env），但那要改 `start.sh`/`docker-compose.yml`/`.env.example`，blast radius 超出零成本范围，**需用户决定**。

⚠️ **本轮新查出、同属"动部署配置"未授权范围**：`docker-compose.yml` 的 api 服务 environment 只传 `DEEPSEEK_API_KEY`/`DEEPSEEK_FLASH_MODEL`/`DEEPSEEK_PRO_MODEL`（三个已废弃）+ `LLM_API_KEY`，**没有 `GLM_API_KEY`/`GLM_BASE_URL`**（见 §A-6）。若下轮要做：environment 补 GLM 两个变量（并删三个 `DEEPSEEK_*` 透传），与 `CHECKPOINT_DB_PATH` 反转一起评估。（⚠️ **更正 2026-10-24**：docker-compose 缺 GLM 透传一项已由 `c2a8e0e` 修复，见 §A 更正；`CHECKPOINT_DB_PATH` 反转仍未做。）

**当前实际有效的防护**（已存在，够用）：`tests/conftest.py:33-53` 在 import `src` 之前重定向 5 个写路径 + 投毒 6 个 API key，另有 `_block_outbound_network` + `_forbid_live_llm` 两个 autouse guard；加**硬规矩**（写库验证必须在 pytest 内跑）。

---

## 六、下一位的第一步

```bash
# 1. 确认基线（都应绿）
python3.11 -m pytest -q --no-header                     # → 1840 passed, 4 deselected
python3.11 scripts/static_audit.py --self-test          # → 27/27
python3.11 scripts/static_audit.py                      # → 41 文件，0 条发现
python3.11 scripts/static_audit.py --include-src        # → 0 条发现
git log --oneline -5                                    # 最上应是本轮交接提交

# 2. 核验生产数据零污染（本轮的核心不变量）
wc -l data/token_ledger.jsonl                           # → 0（账本已归零）
git status --short data/                                # → 空

# 3. 判读 §二-2 的 A/B 结果（第一件事）
#    结果 JSON: /tmp/xidu_ab_result.json（⚠️ /tmp 会被清理，要留就转存项目内）
#    重看报告（零成本，不重跑 API）：
python3.11 scripts/ab_readback_tier.py --dry-run        # → 试算 ¥10.61，不调 API

# ══════════ 需用户单独批准（铁律6）══════════

# 4. B 档 6 章重译 ¥3.72 —— 用户尚未批准执行（本轮只批了 A/B ¥8.99）
#    ⚠️ 必须用 index == EN章号 定位（§A-2），否则会重译错的章
#    入口是 run_novel.py / translate_and_verify.py 那类，不是 ab_readback_tier.py

# 5. 重跑 A/B（若要换 --prev-context 或加基线章数）
#    python3.11 scripts/ab_readback_tier.py --dry-run --repeat-baseline N   # 先看金额
#    → 报给用户 → 等"跑" → 才加 --execute --allow-expensive

# ══════════ 未授权，别自作主张 ══════════

# 6. §五 条款6 写库侧闸门 + docker-compose 缺 GLM 透传 —— 都动部署配置
# 7. 位次 12–14（条款2 / 条款8 / 条款7）—— 都要真实 API，金额都还没算
```

**变异验证命令**（改完逻辑必跑，串行，期间禁编辑）：
```bash
python3.11 scripts/mutation_test.py cost_gate          # → 15/15
python3.11 scripts/mutation_test.py scan_deliverable   # → 76/76
python3.11 scripts/mutation_test.py style_memo         # → 61/61
python3.11 scripts/mutation_test.py circuit_breaker    # → 45/45
python3.11 scripts/mutation_test.py graph_post_process # → 73/73
ls src/*.mutation_backup scripts/*.mutation_backup 2>/dev/null  # 应无输出
# ab_readback_tier 未登记（§三-2），手动跑：
#   python3.11 scripts/mutation_test.py ab_readback_tier
#   ⚠️ 口径修正前的 129/176、47 存活已失效（§三-0/§三-2），重测才作数
```

---

## 七、本轮改动文件清单（全部已提交）

**新增（2 + 1 归档目录）**
- `scripts/ab_readback_tier.py`（1377 行）— READBACK 档位 A/B 脚本（方案1）
- `tests/test_ab_readback_tier.py`（**158 测试**）
- `archive/ab_readback_tier_20260926/`（472 KB，7 文件 + MANIFEST.sha256）— **不可再生**的实跑数据：两组逐章判定、基线遍、三组 token 账本。`.gitignore` 对该目录**刻意不加忽略**（与 13.6 MB 交付物策略不同），理由见 §一-1 第五条

**修改（6）**
- `scripts/destructive_attacks.py` — 消除 `:821` 死三元 + 补注判定依据
- `start.sh` — 3 处 `python3`→`python3.11` + 存在性检查；凭据门 `DEEPSEEK_API_KEY`→`LLM_API_KEY`+`GLM_API_KEY`；`source`→`.`；`${!key}`→显式 if
- `.env.example` — 供应商段重写（删 4 行废弃 `DEEPSEEK_*`、补 6 个 GLM/Qwen 真实变量、`LLM_MODEL`→`QWEN_MODEL`）。**未动 `CHECKPOINT_DB_PATH`**
- `scripts/mutation_test.py` — TARGETS 处留 NOTE 说明 `ab_readback_tier` 为何未登记（**没有新增目标**）
- `HANDOFF_20260924_round5_start_here.md` — §〇 生产指纹 8813 → **0 行** + 更正块
- `.gitignore` — 加 `archive/ab_readback_tier_20260926/` 的**入库说明**（不是忽略规则，是解释为何这批数据要全部入库）

**未改（明确记录）**
- `data/token_ledger.jsonl` — 归零（被 `.gitignore:17` 排除，不产生提交）。快照见 §一-2。**A/B 实跑 136 次调用后仍是 0 行**
- `docker-compose.yml` — 缺 GLM 透传，**未授权**（§A-6 / §五）（⚠️ **更正 2026-10-24**：已由 `c2a8e0e` 修复，见 §A 更正）
- `src/agent/graph.py` 的 `_build_cold_read_context` — 术语段中文泄漏是**生产残留**（实测 58/58 章），**未擅自修改**（§一-1 / §二-2c）
- round2/4/8/9/10 各交接文档的 8813/8811 旧记录 — 刻意不改（§一-2）

**提交清单**
```
bcd1d76 修正 A/B 判据的统计口径（实跑查出）+ 归档 ¥9.37 实跑结果
fab6c51 搭 READBACK 档位 A/B 脚本（方案1，§B-2 问题① / 决定#2 前置）+ 148 条测试
d50b4b6 账本归零（8816 行残留全删）+ 更正 round5 §〇 的生产指纹为 0 行
1474c1b start.sh 解释器钉到 3.11 + 僵尸凭据门换成真实承重 key（§B 零成本项）
1da931a 消除 destructive_attacks.py:821 的死三元（§B 零成本项，变异算子抓不到）
```
（+ 本交接文档提交，共 6 个）
