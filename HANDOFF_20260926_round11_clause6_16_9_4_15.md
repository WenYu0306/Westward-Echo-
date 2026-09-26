# 西渡 · 交接（2026-09-26 第11轮 · 条款6/16/9/4 + 条款15 + §三-E A档 · **新窗口从这里开始**）

> **本文件是最新入口。** 上一轮 `HANDOFF_20260925_round10_clause14_guard.md` 的全部改动**已提交**，不要重做。
> 本轮把 round10 §四 的**零成本项全部做完**（位次 4/5/6/7 + 位次 9 条款15）+ §四-1 死字段删除 + §三-E A 档坏章修复，共 **6 个代码提交**（`c97657b`→`06e85c9`）+ 2 个交接提交，工作区干净。
> ✅ round10 §A 五件拍板事项中：**#1 无需做（已决）、#2 已执行、#5 已执行**；**#3/#4 仍未执行**（都需用户先确认，见 §二）。
> 📌 **会话末尾状态**：用户问过"还有多少没干"（答案：**11 项**，总账见 §B），并说了"可以开始"但**未明确批哪一档**，随后改口要求写交接。所以本轮末尾**没有执行任何代码/数据改动**，只补充了交接文档：新增 §B 未完成项总账、§B-2 两个前置问题（A/B 工具不存在 + verdict 判据无方差）、更新 §二-2 与 §六。**金额批准仍然悬着，按铁律6 需重新报金额并取得明确批准。**

## 🛑 先读这里

### A. 三件必须知道的事（都是本轮实测查出的，会改变你的判断）

| # | 事实 | 影响 |
|---|---|---|
| **1** | ⚠️ **交付物 `novels/output/difu_segmented/difu_en.md` 被 `.gitignore:18` 的 `output/` 规则排除，git 无历史** | 本轮对它的 A 档 4 章修复**无法提交、无法用 git 回滚**。唯一副本是 `/tmp/xidu_difu_snapshot_20260926_111103/`（含 SHA-256），而 **/tmp 会被系统清理**。若要长期保护，需另行归档（见 §三-C） |
| **2** | ⚠️ **round10 §四-2 说的"生产数据仅 2 行"判据有误**：那 2 行 `book_id='diag_test'` 来自 `scripts/diag_api_hang.py:140`，是诊断脚本残留，**不是真实书目翻译** | 按"book_id 非空且不以 test_ 开头"判据算生产、按语义算残留。清理时该不该保留这 2 行，需用户明确（见 §二-1） |
| **3** | ⚠️ **我上一条提交信息（`d536eaf` 条款15）写错了测试增量**："净 +30" 实际是 **净 +21**（22 新增 − 1 改写） | 按 git 安全规矩未擅自 amend（用户没要求改写历史）。正确数字见 §四 表。若你在意提交信息准确性，可 `git commit --amend` 自行更正 |

### B. 本轮没碰、下一位接着做的

**未完成项总账：共 11 项，按"需不需要你出手"分四类**

| 类别 | 项数 | 具体 |
|---|---|---|
| **等用户批金额**（1 批可全解） | 3 | 条款12收尾 A/B、条款3 质量回归、条款14 B档6章重译（§二-2，¥10.87 或 ¥78.95） |
| **等用户确认**（破坏性） | 1 | §二-1 `token_ledger.jsonl` 清理 |
| **等用户定方向** | 1 | §五 条款6「写库」侧闸门（要反转 `CHECKPOINT_DB_PATH` 默认值，动部署配置） |
| **需预算但金额还没算** | 3 | 条款2 端到端+西语启动、条款8 Qwen-MT 嵌入评估、条款7 不降质降成本 |
| **零成本、不需拍板** | 3 | 交付物归档（最高优先）、`destructive_attacks.py:821` 死三元、`start.sh` 两处陈旧 |

⚠️ 用户在 round11 会话末尾说了"可以开始"，但**没有明确批哪一档**，随后改口要求"写交接让下一个窗口接手"。**所以金额批准这件事仍然悬着，不要当成已批**——按铁律6，实际调 API 前仍须重新报金额并取得明确批准。

**零成本、不需拍板**：
- **§三-C 交付物归档**（本轮新发现的高优先项，见 A-1）：difu_en.md 只有 /tmp 一重保护，且它是**已交付产物**。建议纳入 git（改 .gitignore 加白名单）或另做归档副本
- **`scripts/destructive_attacks.py:821` 的死三元**：`'OK' if X else 'OK'` 两分支相同、条件不可观测（变异算子不含三元表达式，故永不可见）。与条款9 修掉的 `style_memo.py:115` 同型，本轮未越界修改（不在任何条款范围内）。实测原文：`record_result("OK" if not has_xss else "OK", "8.5 XSS in chapter title", ...)`
- **`start.sh` 两处陈旧**（实测复核）：`:7` 与 `:43` 用 `python3`（本机 3.9.6，硬规矩要 3.11）；`:17-19` 检查 `DEEPSEEK_API_KEY` 是否存在，但该常量已在 `869ce2e` 从 `config.py` 移除、代码已不用它。
  ⚠️ **实测更正一处我先前写错的判断**：该检查**目前不会失败**，`start.sh` 能正常起服务——因为 `.env` 里仍留着 `DEEPSEEK_API_KEY`（实测长度 35、非空），`source .env` 后 `[ -z ... ]` 判定为假。
  真实隐患是另一种性质：这是个**检查已废弃凭据的僵尸门**。它① 不再保护任何真实依赖（代码用的是 `LLM_API_KEY`/`GLM_API_KEY`，这两个反倒没被检查）；② 一旦有人按"清理无用配置"的合理直觉删掉 `.env` 里那个键，`start.sh` 会**立刻起不来**并报一个与真实故障无关的错误。修法：把检查换成 `LLM_API_KEY` + `GLM_API_KEY`（代码实际依赖的两个），并同步更新 `.env.example`。
- **条款6「写库」侧闸门**（见 §五）：成本侧已完成，写库侧的有效方案需改部署默认值，blast radius 超出零成本范围

**需用户确认/批准才能做**（见 §二）：
- §二-1 `token_ledger.jsonl` 清理（破坏性，铁律7）
- §二-2 条款12收尾 + 条款3 A/B + §三-E B档重译（真实 API，铁律6，**金额已算好**）
  ⚠️ 但 §二-2 有两个前置问题必须先解决，见下

### B-2. ⚠️ §二-2 的两个前置问题（本轮会话末尾新查出，会改变方案设计）

**问题①：仓库里没有能做 READBACK 档位 A/B 的工具，得先搭。**
实测全仓零脚本涉及档位对比：`ab_memo_test.py` 比的是"备忘有没有用"（memo ON/OFF），不是"降档降不降质"；`generate_report.py:219-221` 与 `translate_and_verify.py:436` 只是**读**既有 `_quality.json` 做汇总，不做两组对比。所以 §二-2(b) 那 ¥7.15 只是 API 花费，**还得先写一个对比脚本**（复用 `scan_deliverable` 判据 + 两个 `XIDU_NODE_MODEL_READBACK` 环境跑同一批章节）。搭脚本本身零成本，但必须先做。

**问题②：按 round10 措辞"比 PASS/NEEDS_FIX 一致率"很可能拿到一个 100% 的空数字。**
实测 `_quality.json` 59 条记录的字段方差：

| 字段 | 取值种类 | 能否当判据 |
|---|---|---|
| `verdict` | **1**（59 条全是 `PASS`） | ❌ 无方差 |
| `would_keep_reading` | **1**（全是 `True`） | ❌ 无方差 |
| `comprehension_issues` | 6 | ✅ |
| `engagement_gaps` | 4（0:21 / 1:22 / 2:15 / 3:1） | ✅ |
| `impression` 文本 | 59（全不同） | ✅ |

原因在 `graph.py:56-64`：抽样章走 READBACK → FIX 循环，`_should_readback` 在 `verdict == "NEEDS_FIX"` 且未超 `_MAX_FIX_ATTEMPTS` 时路由回 `fix_node`——**一路修到 PASS 为止**。`_report.json` 的 advisories 第 3 条自己就写着："全 PASS 是该抽样设计下的预期结果，不携带质量信息。"

**所以 A/B 判据必须换**：不能用 verdict 一致率，要用 `comprehension_issues` / `engagement_gaps` 计数差 + `impression` 文本差异（两组跑同一批章节、**关掉 FIX 循环**才能看到 READBACK 的原始判定，否则 FIX 会把差异抹平）。这个设计决定要在报金额之前先跟用户对齐，否则花了 ¥7.15 拿到一个无信息的 100%。

### C. 硬规矩（承接 round9 §一/§九、round10 §C，本轮全程遵守）

- **必须用 `python3.11`**（本机 `python3` 是 3.9.6）。
- **任何写库验证必须在 pytest 内跑**（铁律16）。裸跑 `python3.11 -c` 时 `CHECKPOINT_DB_PATH` 指向生产库 `data/checkpoints.db`。
- **变异还原只能用 `/tmp` 快照 + 哈希校验，严禁 `git checkout`**（铁律11）。变异必须串行，期间禁并发 pytest、禁编辑文件；跑完校验 `.mutation_backup` 残留 0。本轮 4 个变异目标全部 0 残留。
- **断言不能假绿**（铁律12/15/17）：绝不用被测常量推导测试输入和期望。本轮所有新测试断言用字面值。

### ⚠️ D. 我本轮差点违反硬规矩（记录在案，供下一位避坑）

条款15 验证生产语义时，我跑了一段 **pytest 之外的裸脚本**去调 `get_breaker(...).call(...)` 触发跳闸。**它没污染生产库纯属巧合**：我用 `spec_from_file_location` 加载模块导致 `__package__` 为空，跳闸路径里的 `from .error_tracker import record_event` 抛 `ImportError`，被 `except Exception: pass` 吞掉。若当时用正常的 `from src.circuit_breaker import ...`，那次跳闸就会真实写入 `data/translation_events.db`。

已核实未污染（`translation_events.db` mtime 00:23:40 早于运行时刻；`demo-lang` 记录 0 条；`git status data/` 无改动）。

**教训**：任何会触发 `record_failure`/`record_event` 的验证，**必须在 pytest 内跑**。"只读验证"的判断标准不是我以为它只读，而是它是否会走到写库路径——熔断器跳闸就会。

---

## 〇、基线（实测，非声称）

```bash
python3.11 -m pytest -q --no-header                    # → 1682 passed, 4 deselected
python3.11 scripts/static_audit.py --self-test         # → 27/27
python3.11 scripts/static_audit.py                     # → 0 条发现（round10 时是 5 条）
python3.11 scripts/static_audit.py --include-src       # → 102 文件，0 条发现
```

- **1682** = round10 基线 1574 + 本轮 **108 个新测试**
- 测试函数累计（按提交精确统计，`^\s*def test_` 计数）：

  | 提交 | 内容 | 累计 | 增量 |
  |---|---|---|---|
  | `8c557fc` | round10 基线 | 1553 | — |
  | `c97657b` | §四-1 死字段删除 | 1553 | +0（纯删除） |
  | `7ac4367` | 条款6 成本闸门 | 1580 | +27 |
  | `b726bce` | 条款16 术语一致性 | 1629 | +49 |
  | `1bf97e4` | 条款9 主笔评估 | 1635 | +6 |
  | `d536eaf` | 条款15 熔断器 | 1656 | **+21**（提交信息误写 +30，见 A-3） |
  | `06e85c9` | 条款4 死脚本删除 + CI 门禁 | 1656 | +0 |

- 本轮触及的变异目标，全部 **0 存活**：

  | 目标 | 结果 | 关联提交 | 备注 |
  |---|---|---|---|
  | `graph_post_process` | 73/73 killed | §四-1、条款9 | 计数未变（本轮只删死字段/死参数） |
  | `cost_gate` | 15/15 killed | 条款6 | 🆕 新目标。首轮 13/15，2 个存活点已消除（未走豁免） |
  | `scan_deliverable` | 76/76 killed | 条款16 | 由 round10 的 51→76（新判据 +25 个变异点） |
  | `style_memo` | 61/61 killed | 条款9 | 由 62→61：死三元同行两个 `60` 常量消除一个（已逐项核实非覆盖流失） |
  | `circuit_breaker` | 45/45 killed | 条款15 | 由 round10 的 18→45（窗口+限流新增变异点） |

- `pytest` 裸跑**恒 ¥0**（conftest 封网 + 重定向）。
- 生产库 `data/` 在 git 视角**无任何改动**（`git status data/` 为空）。

---

## 一、本轮完成的六项（提交序：新→旧）

### 1. 条款4 · 删两个剧本死脚本 + static_audit 接入 CI 硬门禁（`06e85c9`）

**前提与 round10 描述不同（实测）**：round10 说"含真实 UnboundLocalError：`scripts/run_script_pilot.py:174`"。该 bug 确实在（static_audit D 类也报着），但**这个脚本跑不到那一行**——它在 import 阶段就死：

- `from src.script_splitter import split_episodes` → `src/script_splitter.py` 已被 `869ce2e`（2026-09-20 "drop script/game branch + dead code"）删除（315 行）
- `from src.config import DEEPSEEK_API_KEY` → 该常量已不在 config（现只有 `GLM_API_KEY`/`LLM_API_KEY`/`ANTHROPIC_API_KEY`/`CMS_WEBHOOK_API_KEY`）
- 输入语料缺失：`pilots/pei_zong_script.txt`、`pilots/glossary.json` 都不存在
- `content_type="script"` 分支同样已删（`registry.py:56` 对未知类型**静默回退** novel）
- 两脚本全仓零引用；`pilots/output/` 下已交付产物不受影响

所以"修 UnboundLocalError"是修一个**永远触发不到的 bug**；要救活得恢复整条已定案删除的剧本管线（推翻 `869ce2e` 的产品决策），远超条款4 范围。**用户拍板选"删除 + 接入门禁"**。

**动作**：
- 删 `run_script_pilot.py`(180行) + `compile_script_sample.py`(164行)。先做 /tmp 快照 + SHA-256，再用 `trash` 移入废纸篓（非 `rm`）
- **实质产出**：`static_audit.py:42-44` 自己记录的策略是"当前尚未接入 pre-commit/CI，因为两个剧本残留脚本带着 5 条已知断头，接进去会天生红。等断头修完/删完再接，这样闸门天生是绿的，不需要基线豁免机制"。前提现已成立（**5 条发现 → 0 条**），故在 `.github/workflows/test.yml` 的 pytest **之前**新增 `Static audit` 步骤跑三条：`--self-test`(27/27) + 扫 `scripts/`(40文件) + `--include-src`(102文件)，实跑均退出码 0
- **未引入任何基线豁免清单**——豁免机制一旦存在就会被用来把真缺陷登记成"已知"，那是这个项目反复在防的假绿模式
- 覆盖 `src/` 是必要的：CI 的 mypy 只查 `src/agent/`，`src/api/` 或 `src/glossary/` 里的断头 import 否则无人看守

**死脚本可恢复性（三重验证）**：
```bash
git show 06e85c9^:scripts/run_script_pilot.py       # ✅ git 历史
git show 06e85c9^:scripts/compile_script_sample.py  # ✅ git 历史
# /tmp/xidu_dead_scripts_20260926_110235/           # ✅ 快照，SHA-256 与 git 历史逐字节一致
#   run_script_pilot.py      06c6f575860b80552a944c7aa0eba745d5f9fecb948d87fa5b25c81a36213524
#   compile_script_sample.py 5a9a172196f69b35edf5c47804ace928742f9290eabbb747ec4a9c5c68b538bb
```

### 2. 条款15 · circuit_breaker 真正实现滚动窗口 + 试探限流（`d536eaf`）

用户 round10 §A-5 已拍板选 **(b) 真正实现**。按 round10 §七 规格逐条施工，**§七-F 六条完成判据全部自检通过**。⚠️ 本提交**改变生产熔断的跳闸/恢复时机**，按 §七-E 单独一个提交便于回滚。

**堵住的真实风险（§七-B）**：旧实现数"连续失败"，任何一次成功清零计数。于是一个每 5 次调用失败 4 次、中间夹成功的半坏 API **永不跳闸**，管线持续为它付费。改后同样场景**实测第 7 次调用即被快速拒绝**（未打 API）。

**三个死字段的处置**：
- `window_seconds` → 现已真实读取（`_prune_window`）
- `half_open_max` → 现已真实读取并据此拒绝第 N+1 个探测
- `_last_failure_time` → 按铁律4 **删除**（全仓零读取方）

**关键设计取舍（这条最容易做错）**：`record_success` 区分"什么成功了"——
- CLOSED 态普通成功**不清零**窗口（否则退回旧的烧钱隐患）
- HALF_OPEN/OPEN 态的**恢复探测必须清零**（否则下次单点失败立刻重跳，`recovery_timeout` 失去意义）

**锁纪律（§七-E 陷阱，已避开）**：`_lock` 是 `threading.Lock`、**不可重入**。`_failure_count` 变 property 后会在 `record_failure`/`snapshot` 的持锁路径造成死锁，故两处直接读 deque 并写明原因；三个内部辅助（`_prune_window`/`_should_attempt_recovery`/`_time_until_recovery`）都标注"调用方必须持锁"；fn 仍在锁外执行。

**prune 用 `popleft` 而非 `maxlen`**：`maxlen` 会静默丢掉**窗口内**的失败。时间戳单调不减，故只需从左侧剔除、无需扫描。

### 3. 条款9 · 主笔 agent 评估：消费侧已通，修死参数 + 补消费面测试（`1bf97e4`）

round10 初判"倾向缺"。**实测结论相反**：消费侧确实通——
- READ：`read.py:128` 读 → `:135` 填入 `prompts/read.py:227` 占位符
- WRITE：`write.py:96` 读 → `:106` 填入 `prompts/write.py:180` 占位符
- FIX/READBACK **有意**不消费：FIX 提示词规则1"读者的反馈是你唯一的授权范围"；READBACK 是 naive reader 盲读（同条款10 理由）
- 写回闭环也在：`graph.py:495-514` 把 FIX 产出的 `fix_lessons` 存进 bridges.md

**但全仓没有任何测试断言备忘内容真的进了提示词**：所有夹具都填 `"(no memo)"`/`""`，于是"占位符存在但内容没被渲染进去"这类缺陷**全绿**。补 `TestStyleMemoReachesThePrompt`（6 条）堵这个洞，覆盖四个节点的消费面。

**牙齿验证（精准变异，非删参数式崩溃）**：先做的"删 `style_memo=memo`"变异会因 `{style_memo}` 占位符 KeyError 连带杀掉无关测试，**不能证明**我的测试专门检测"内容缺失"。改做精准变异（保留参数、内容置空）→ WRITE 侧只杀掉我新增的 3 条、其余 21 条全绿（节点不崩）。精确证明。

**顺带查出并修掉的真缺陷**：
1. `read_relevant` 的 `chapter_content`/`exact_matches` 由 `bdccc85`(2026-08-04) 引入，此后**任何版本函数体从未读过**（AST 统计 0 次）——round9 §八"收了参数却不用"同型。`read_all` 文档还谎称它"检索与当前章相关的条目"，实际只按时间取每抽屉最近 5 条、无任何匹配步骤 → 按铁律4 删死参数、改 keyword-only，文档如实化为 `"relevance = recency"`。那条钉住死参数的假绿测试改写为断言真实契约（章节术语仍经 `exact_matches_text`/`chapter_content` 各自通道进提示词）
2. `read_all:115` 死三元 `keep = lines[-60:] if header else lines[-60:]` 两分支完全相同。**变异算子不含三元表达式**（只有 Compare/BoolOp/bool常量/非零int-float常量/非常量return），所以这类死代码永远不会被变异测试抓到 → 已消除。AST 全仓扫描另发现 `scripts/destructive_attacks.py:821` 同型，未越界修改（见 B 节）

**文档如实化（两处谎报）**：
- 类文档曾称 "periodic pruning of obsolete entries"，实测模块**无任何裁剪代码**（只有 `_ensure_files` 建空文件 + `record_lesson` 追加）
- 模块文档曾称 "每文件约 50 行、全备忘 2500-4000 tokens"，实测 `difu_xiao_xiansheng` 单 `bridges.md` 就 **31,818 行 / 3.66 MB**，五抽屉合计 **54,006 行**（文档假设的 **636 倍**）
- 两处都改成如实描述：磁盘**无界增长**，prompt 体积靠**读取时截断**（`read_all` 每抽屉最新 60 行、`read_relevant` 每抽屉最新 5 条）保证恒定。裁剪是刻意的非目标——删累积条目会改变真实翻译看到的内容，需 owner 决定，故只记录不实现

### 4. 条款16 · 术语一致性判据（`b726bce`）

**先推翻 round10 的一处结论**：交接写"八爷四种称呼"，实测是错的——`Eighth Lord Fan` 是**另一个角色**（Fan Wujiu），不属八爷；八爷实为 `Chang Baye`(3827) / `Eighth Master`(1685) / `Eighth Master Chang`。七爷姓氏还不一致（`Seventh Lord Xie` / `Seventh Master Chang`）。照"八爷四种称呼"直接写判据会误报——正是 round9 §八"裸判据 95% 误报"的同类陷阱。所以**不做**"自动识别同一实体多译法"（不可靠），只做两类零误报判据。

**判据 7 引号风格**（实测标定 difu_en.md）：直双引号 79,399 vs 弯双引号 4,010/3,995，直引号占 **90.8%** 为主导。形态照 `check_numbering` 的 `dominant_offset`：**主导风格本身不是缺陷，偏离主导才是**。
- 同章混用 20 章 / 通篇少数派 183 章 / 合计 **203 章（8.8%）**，分散在 171 段、最长仅 4 章 → 散点式，非某时段/模型的配置问题
- **两个误报源已实测排除**：① 弯引号只在**中文原标题**里的 Ch157/Ch1345 不算（扫标题会得 22 章，扫正文才是 20）② 弯单引号 `’`(U+2019) 实测 3,089 次、绝大多数是英文撇号（woman's/aren't/wasn't），ASCII 单引号 68,039 次全是撇号 → **只判双引号**

**判据 8 术语表内部冲突**（实测 `_glossary.json` 1467 条）：同一英文渲染对应多个中文源词 29 组，分三类，**只有前两类是缺陷**：
- A 值含脏标记（斜杠/括号）3 组：`'Dog Ni' <- ['犬封恶鬼/犬尼','犬尼']` 等
- B 等长且恰好一字不同 10 组（同音/形近字，必有一错）：`白无畏/白无谓`、`常怀元/常怀远`、`法庆/法清`、`空禅/空蝉`、`幽荧/幽莹`、`女妭/女魃` 等
- C 其余 16 组是**合法变体**（简称/全称/敬称），只报数量供人工核对、不判缺陷：`容慧/容慧禅师`、`鬼母/鬼母邪神` 等
- C 与 B 判据不重叠（实测 16 组无一满足"等长且仅一字不同"）。**A+B=13 组真冲突、零误报**

**刻意不做**"术语在源文出现却没在译文出现"判据：`completeness.py:187-194` 已实测否决（11 个截断章缺 8-24 术语、8 个健康对照缺 5-16，区间重叠；WRITE 本就有权压缩次要提及），重造会得到同一个不可分离的信号。

**接线**：两类判据单独成节（`quotes`/`glossary`），不进逐章 findings——difu 实测 203 章偏离 vs 17 条逐章缺陷，混在一起会把条款14 的高价值分诊项冲掉。但都计入 `--strict` 与结论行，口径与 `numbering_defects` 一致。长列表按 `_QUOTE_LIST_LIMIT=20` 截断。

**连带修掉的真 bug**：`scripts/cost/estimate_offline.py` 传未登记 `--book` 时**静默失败**（`if args.book and key != args.book: continue` 跳过所有书 → 退出码 0、除标定行外零输出）。实测 `--book jianke` 返回 0 且空。其 BOOKS 只标定 8 个实测点、key 与 `run_novel.py` 的 NOVELS **不一致**（同一本书它叫 `wuxian`、那边叫 `limitless_horror`）→ 改为硬失败退出码 2 + 列出可用 key + 点明两套 key 不一致；并让 `__main__` 用 `SystemExit(main())` 传递退出码。

### 5. 条款6 · 成本闸门收敛为单一来源（`7ac4367`）

**实测现状与 round10 描述不同**：round10 说"大半落地，20 个裸露脚本"。实测 `scripts/` 下 42 个 .py，18 个会调 API，其中 4 个有闸门、14 个裸露。真正的落地缺口不是"没闸门"，而是**闸门有 4 份副本、3 种写法**，且四份**全都在读错的变量**。

**真实缺陷（本轮核心修复）**：四份闸门都从 `QWEN_MODEL`/`GLM_MODEL`/`GLM_FULL_MODEL` 判断高价档，但节点实际路由由 `config.NODE_MODELS` 决定，可被 `XIDU_NODE_MODEL_<NODE>` **逐节点覆盖**（`config.py:186-194`）。于是：
```
XIDU_NODE_MODEL_READBACK=qwen3.8-max + QWEN_MODEL=qwen3.8-flash
→ 闸门认为"全是 flash"放行，实际 READBACK 按 ¥36/M 计费
```
`config.py:155-158` 早就为 `MODEL_MAP` 记录过这个缺陷类型（"配置声明与真实行为各走各的"），闸门这边是**同一个病、没人修**。

四份副本的具体差异（实测）：
| 脚本 | 检查的模型 | env 逃生门 | 参数形态 |
|---|---|---|---|
| `scripts/cost/probe_nodes.py:19` | 仅 `QWEN_MODEL` | 有 | 无参，靠 `sys.argv[-1]` 猜 flag（脆弱：flag 不在末位就失效） |
| `scripts/cost/probe_nodes_wide.py:18` | 仅 `QWEN_MODEL` | 有 | 同上 |
| `scripts/adversarial_test.py:57` | 3 个变量 | 有 | `allow_expensive: bool` |
| `scripts/diag_api_hang.py:43` | 2 个，**漏 `GLM_FULL_MODEL`**（FIX 走它，默认 glm-5.3 输出 ¥28/M） | **无** | 整个 argparse 命名空间 |

**改法**：新增 `src/cost_gate.py` 作为唯一判据来源，读 `config.NODE_MODELS`：`is_expensive` / `expensive_nodes`（带输出单价，查不到给 `None` 不猜数）/ `guard_tier`（统一提示 + 退出码 2，同时认 `--allow-expensive` 与 env 逃生门）/ `format_refusal` / `describe_tiers` / `_invoked_script_name`。4 个脚本改为调用它，**净删 13 行**。刻意不自建总价模型：全书估算仍归 `estimate_offline.py`，闸门只报单价并指路，避免第二套成本模型又漂移。高价档口径沿用既有"名字含 max/prime"，不引入新的价格阈值常量。

**`run_novel.py` 加成本预检（只报价、不拒绝）**：全书最贵的入口原先完全裸露。生产批处理本来就允许跑高价档（round10 记录的降档杠杆正说明默认跑 max），所以加的是"先报金额"而非拒绝——开跑前打印逐节点路由与输出单价，并指向零成本估算。`guard_tier` 只装给"不需要高价档"的探针/对抗测试。实测当前路由：
```
read/readback/review_terms/write_fallback = qwen3.8-max(¥36/M)
fix   = glm-5.3(¥28/M)        ← 输出价接近 max 但按"名字含 max/prime"口径不算高价档
write = glm-5.3-flash(¥2.8/M)
```
`fix=glm-5.3(¥28/M)` 的口径取舍是**刻意**的：不引入价格阈值常量（那会多一个会漂移的判据），而 `describe_tiers()` 会把 ¥28.0/M 如实打给人看——报价面向人、闸门只拦最极端档。

**首轮 2 个存活变异点的处理（未走豁免）**：
- `boolop ->or at 129`：`sys.argv` 为空的防御分支，被 pytest 恒非空的 argv 遮蔽 → 抽出 `_invoked_script_name()` 变成可独立断言的函数，补 3 条边界测试（正常 argv / 空 argv / `argv[0]` 空串），变异已杀
- `const bool->False at 130`：`print(..., flush=True)` 紧邻 `sys.exit(2)`，退出流程会刷缓冲，进程内不可观测 → 按"优先消除而非豁免"去掉该参数并在 docstring 记明原因，变异点消失

### 6. §四-1 · 删除死字段 term_conflicts/resolved_conflicts（`c97657b`）

用户 round10 §A-2 已拍板删。纯删除 17 行：`state.py` 两字段定义+注释、`graph.py` `_make_state` 初始化+过时注释 `"Kept for backward compat (unused by new nodes, harmless)"`、4 处测试夹具（`test_fault_injection.py` ×2 / `test_v15_pipeline.py` / `test_write_guards.py`）。`operator`/`Annotated` 导入保留（仍被 `new_terms_found`/`fix_lessons` 使用）。

**额外核实（round10 未查）**：项目**不用 LangGraph checkpointer**（全仓零 `MemorySaver`/`SqliteSaver`），断点是自实现（`job_store` + `glossary_snapshot_json`），**不持久化整份 state**，故老断点恢复不受影响。前端/JSON/SQL/YAML 零引用（仅 `.mypy_cache` 构建缓存命中，已确认 gitignored + 未跟踪）。

---

## 二、需用户确认/批准才能做的（金额已算好）

### 1. §四-2 `token_ledger.jsonl` 清理（破坏性，铁律7）

用户 round10 §A-3 已拍板"先备份再清理，同步更正指纹记录"，但按铁律7 破坏性操作**动手前仍需确认一次**。**本轮实测数据与 round10 记录不同**：

```
总行数(非空) 8816   ← round10 记的是 8813，本轮实测已涨 3 行
  生产数据(book_id 非空且不以 test_ 开头)  2 行
  测试残留(book_id 空或 test_*)          8814 行
  解析失败                                0 行
文件 mtime: Sep 26 00:23:40（早于本轮会话约 10 小时，**不是本轮写的**）
git: data/ 未被跟踪，清理不影响版本库
```

那 2 行"生产数据"是 `book_id='diag_test'`、`node='read'`、`model='qwen3.8-max'`、cost ¥0.421 / ¥0.468——来自 `scripts/diag_api_hang.py:140`。**按语义是诊断脚本残留，不是真实书目翻译**（见 A-2）。

⚠️ 本轮涨的 3 行是 `book_id=''`、`node='write'`、`model='glm-5.3-flash'` 的残留（`conftest.py:36` 的 `XIDU_TOKEN_LEDGER` 重定向是后加的，早于它的测试仍会写真实路径）。

**需用户明确的点**：清理时那 2 行 `diag_test` 是保留（按 round10 判据算"生产"）还是删（按语义算残留）？两种都能自圆其说，猜错会丢真实计费记录或留下噪音。

### 2. §四-3 条款12收尾 + 条款3 A/B + §三-E B档重译（真实 API，铁律6）

用户 round10 §A-4 已批"合并一批"，但按铁律6 **实际调 API 前必须先报具体金额等批准**。金额已用项目**已标定**的成本模型（`scripts/cost/estimate_offline.py`，8 个实测点）算好，非我自造：

**（a）§三-E B 档 6 章重译 ≈ ¥3.72**

⚠️ 第一版估算有缺陷已修正：Ch269/548/811/820 **本来就是截断章**，用截断后的英文长度（439/601/2500/2760 字符）预测成本会**低估金额**。改用 2290 个未截断章标定的 `英文字符/源汉字` 中位数 **3.33**（均值 3.60）重算：

| EN章 | 中文章 | 源汉字 | 截断英文 | 完整英文(估) | ¥下界 | ¥估算 | ¥上界 |
|---|---|---|---|---|---|---|---|
| 269 | 267 | 1,560 | 439 | 5,199 | 0.599 | 0.617 | 0.622 |
| 430 | 428 | 1,559 | 6,463 | 5,196 | 0.617 | 0.617 | 0.622 |
| 548 | 546 | 1,590 | 601 | 5,299 | 0.600 | 0.618 | 0.623 |
| 811 | 809 | 1,558 | 2,500 | 5,192 | 0.606 | 0.617 | 0.622 |
| 820 | 818 | 1,918 | 2,760 | 6,392 | 0.621 | 0.635 | 0.641 |
| 1472 | 1470 | 1,498 | 4,747 | 4,992 | 0.613 | 0.614 | 0.618 |
| **合计** | | **9,683** | | **32,271** | **3.66** | **3.72** | **3.75** |

≈ ¥3.72（区间 ¥3.66–¥3.75），tokens ≈ 0.202 M，含 FIX 触发率 50%（实测标定）。

**（b）条款12收尾 + 条款3 的 A/B —— 两种规模供选**（抽 60 章，与既有冷读抽检规模 59 章一致；步长 38 覆盖全书）

> 🛑 **这两个方案在跑之前必须先解决 §B-2 的两个前置问题**，否则钱花了也拿不到结论：
> ① **工具不存在**——全仓没有能做档位 A/B 的脚本，得先搭（零成本，但必须先做）
> ② **判据要换**——`verdict` 在 `_quality.json` 里 59 条全是 `PASS`（抽样章走 FIX 循环一路修到 PASS），比"PASS/NEEDS_FIX 一致率"必然得到 100% 的空数字。必须改用 `comprehension_issues`/`engagement_gaps` 计数差 + `impression` 文本差异，且**关掉 FIX 循环**才能看到 READBACK 原始判定。这个设计决定要在报金额前先跟用户对齐。

| 方案 | 内容 | A组(max) | B组(flash) | 合计 | 能回答什么 |
|---|---|---|---|---|---|
| **1 · 只跑 READBACK** | 复用已有译文，只对比 READBACK 判定 | ¥6.67 | ¥0.48 | **≈ ¥7.15** | 条款12 的杠杆点：降档后冷读判定是否一致（判据见上，**不是** verdict 一致率）。**实测降幅 13.8 倍** |
| **2 · 完整管线 A/B** | READ+WRITE+READBACK+FIX 全跑两组 | — | — | **≈ ¥75.23** | 上者 + 条款3 的 WRITE 全链路质量回归 |

**我的建议**：方案1 更贴合 round10 §四-3 的原意（round10 措辞是"比 READBACK 的 PASS/NEEDS_FIX 一致率"，但该措辞基于错误假设，见 §B-2 问题②），且 ¥7.15 就能拿到"降档不降质"的结论；条款3 的质量回归若也要做，可先跑方案1 看一致性，够高再决定是否需要方案2。**B 档 6 章重译（¥3.72）建议并入同一批**（都要跑真实章节，省一次预算审批）。

**合计**：方案1 + B档 ≈ **¥10.87**；方案2 + B档 ≈ **¥78.95**。

**推荐的执行顺序**：① 先跟用户对齐 A/B 判据设计（零成本）→ ② 搭对比脚本（零成本）→ ③ 报最终金额并等批准 → ④ 才跑。跳过 ①② 直接跑，很可能花 ¥7.15 拿到一个无信息量的 100%。

**降档杠杆**（round10 已接通，一个环境变量）：
```bash
XIDU_NODE_MODEL_READBACK=qwen3.8-flash   # READBACK 单价 12/36 → 0.8/2.7
```
⚠️ 条款15 之后请注意：`src/cost_gate.py` 现在读 `NODE_MODELS`，所以设了这个环境变量后，闸门会**正确**识别 READBACK 已降档（旧的四份闸门会漏判）。

---

## 三、§三-E 历史坏章清理：A 档已完成，B/C 档未动

### A. A 档 4 章已修复（零成本，纯文本手术）

`difu_en.md` 是已交付产物，改前已做 /tmp 快照 + SHA-256（见 C）。**4 处修改全部经中文原文独立验证**，不依赖 round10 的分诊结论（round10 在条款16、条款4、Ch1077 修法上都被实测推翻过，见下）：

| 章 | 改法 | 中文原文验证 |
|---|---|---|
| **Ch366** | 删行尾 ` ```json ` + 整个裸 `{` 行 | 正文 `...kicked into gear.` 完整 |
| **Ch656** | **行内删**尾部 `{`（非整行删），保留台词 `"We're done for!"` | 第654章「完犊子了」结尾 `完犊子啦！这里……这里特么的是十六楼！！！` **完整收束** |
| **Ch1077** | 删 `End of Chapter.` **+ 其造成的多余 `---`**（共 4 行） | 第1075章「好戏登场」结尾逐句对应（五六个一百度灯泡→five or six hundred-watt bulbs；晚七点大戏开场→exactly seven in the evening）**完整** |
| **Ch1286** | 删 4 行 `—` + `END OF CHAPTER 1286`（共 7 行） | 第1284章「钓鬼」正文完整（`都不用验DNA的......` ← `No paternity test needed`）；四行 `—` 是**源文件章节分隔符 `----------------------------------------` 的渲染残留**，与紧随其后的 `---` 重复 |

⚠️ **round10 给的 Ch1077 修法不完整**：只删 `End of Chapter.` 会留下**双重 `---`**（全书规范是章间单条：2880 条中仅 2 处既有双重异常，在 L48483/L108085）。故实际删了脚手架 + 一对多余分隔线。

⚠️ **round10 要求"人工确认"的 Ch1286 四行 `—`，本轮已用中文原文解决**：既不是诗、不是被打断台词、也不是真截断，是源文件分隔符残留。**正文完整，可安全删除**。

### B. 修复后复扫（实测，非声称）

```
缺陷数 17 → 10（恰好等于删除的 7 条）
  JSON_RESIDUE      4 → 2    （Ch1472 保留，B档）
  META_TEXT_LEAK    2 → 0    （Ch1077/Ch1286 清零 ✅）
  TRUNCATED_BODY   11 → 8    （Ch116/Ch2296 = C档合法保留；Ch269/430/548/811/820/1472 = B档保留）
```
**B 档 6 章与 C 档 2 章全部保留、零误伤。** 四处边界结构均符合全书规范（正文 → 空行 → 单条 `---` → 空行 → 下章标题），双重分隔仍是原有那 2 处未新增。章标题数 **2301 → 2301** 未变。

精确 diff（快照 vs 当前，**只有 4 处改动**）：
```
@@ -31763,8 +31763,7 @@   Ch366:  删 ```json 尾 + 裸 { 行
@@ -59651,7 +59650,7 @@   Ch656:  "We're done for!"{  →  "We're done for!"
@@ -95693,10 +95692,6 @@  Ch1077: 删 End of Chapter. + 一对多余 ---
@@ -113915,13 +113910,6 @@ Ch1286: 删 4×— + END OF CHAPTER 1286
净减 12 行（204309 → 204297）
```

### C. ⚠️ 回滚方式（重要：git 无法回滚）

```bash
# difu_en.md 被 .gitignore:18 的 output/ 排除，git 无历史！
# 唯一副本：
/tmp/xidu_difu_snapshot_20260926_111103/
    difu_en.md    SHA-256: 781e9c12cb9a169453025a4d7dbd41c5f5c38ac87ce9a83b3969cbfb8612d614
    HASH.txt
# 原始规模：204,309 行 / 14,232,263 字节
# 还原：cp /tmp/xidu_difu_snapshot_20260926_111103/difu_en.md novels/output/difu_segmented/difu_en.md
```
**/tmp 会被系统清理。** 建议下一位优先处理 §B 的"交付物归档"项（纳入 git 或另做归档副本）——这是本轮查出的最高优先遗留项。

### D. 配套报告无需同步（已核实）

`_report.json` 的 `issues` 只含 `"JSON 残留: 第[1472]章"`（B 档，本轮未动），与修复后内容一致；`_report.md` 未提及 A 档任何章号。**故 A 档修复不造成报告与内容不一致。**

`_report.json` 现状：`grade: B`、`passed: ['章节完整: 2301/2301', ...]`。⚠️ 注意它的 advisories 第 3 条自己写着"冷读样本不可作为质量结论：覆盖率仅 2.6%…需改用全量零成本扫描（completeness/output_guard）作为门禁"——`scan_deliverable.py` 就是为此而建的。

### E. B 档 6 章（需重译，金额见 §二-2a）

`Ch1472`（整章封在截断信封里，剥残片后 0 行有效正文）+ `Ch269/430/548/811/820`（句中硬截断，如 `Chen Daji had never been`、`'And the`）。
`scan_deliverable.py --all` 另查出 `limitless_horror_full_en.md` 14 章正文首行是未翻译中文章节名（round9 §三-C），同属历史清理，本轮未动。

---

## 四、剩余任务（按 round9 §六 位次，状态已更新）

**已完成 11 条**：1 工程审计 / 4 项目边界（本轮）/ 5 交接验证 / 6 API管控·成本侧（本轮）/ 9 主笔评估（本轮）/ 10 / 11 通用性 / 12 档位（主体）/ 13 全部 / 14（根因）/ 16 术语一致性（本轮）+ 15 circuit_breaker（本轮）

| 位次 | 条款 | 状态 | 备注 |
|---|---|---|---|
| — | §四-1 死字段删除 | ✅ **本轮完成** `c97657b` | 见 §一-6 |
| **4** | 6 · API 调用管控 | 🟡 **成本侧完成** `7ac4367` | 「写库」侧未做，见 §五 |
| **5** | 16 · 术语一致性 | ✅ **本轮完成** `b726bce` | 见 §一-4 |
| **6** | 9 · 主笔 agent 评估 | ✅ **本轮完成** `1bf97e4` | 见 §一-3 |
| **7** | 4 · 项目边界 | ✅ **本轮完成** `06e85c9` | 见 §一-1 |
| **9** | 15 · circuit_breaker | ✅ **本轮完成** `d536eaf` | 见 §一-2 |
| 收尾 | 14 · 历史坏章清理 | 🟡 **A 档 4 章已完成** | B 档 6 章需预算（§二-2a）；C 档 2 章合法不动 |
| **10** | 12 收尾 · 实际降档 A/B | ⬜ **金额已算** | §二-2b，方案1 ¥7.15 / 方案2 ¥75.23 |
| **11** | 3 · 模型变更质量回归 | ⬜ **金额已算** | 可与上一项合并（方案2 覆盖） |
| **12** | 2 · 端到端 + 西语启动 | ⬜ 前置障碍已清 | 需预算 |
| **13** | 8 · Qwen-MT 嵌入评估 | ⬜ 官方文档已查完 | 需预算 |
| **14** | 7 · 不降质降成本 | ⬜ 与12耦合 | 需预算 |

**本轮新增的遗留项**（都不在任何条款范围内，见 B 节）：
- §三-C **交付物归档**（最高优先，A-1）
- `scripts/destructive_attacks.py:821` 死三元
- §五 条款6 写库侧闸门

---

## 五、条款6「写库」侧为什么没做（实测结论，别重复踩）

round10 建议"把闸门扩成成本+写库双闸门"。**成本侧已做完，写库侧我判断不能用同样方式做**，实测依据：

**库级 guard 函数无法防住 round9 那类事故。** round9 的两次生产库污染，事故源是**裸跑 `python3.11 -c`**——那种进程根本不会调用任何 guard 函数，`CHECKPOINT_DB_PATH` 直接指向 `data/checkpoints.db`。在 `src/` 里加一个 `guard_db_write()` 只能防住"守规矩地经过它的代码"，而事故恰恰来自不经过它的代码。**这与条款6 成本侧的情形不同**：成本侧四份闸门至少是真实生效的（只是判据读错变量），而写库侧的闸门在事故路径上是**结构性无效**的。

我本轮自己差点重演这个事故（§D）：一段 pytest 外的裸脚本触发熔断跳闸，差点写 `translation_events.db`，只因加载方式导致 `ImportError` 被吞才幸免。这恰好印证了上面的判断——**风险来自"绕开约定"，不是"约定不够多"**。

**真正有效的选项是反转默认值**：让 `CHECKPOINT_DB_PATH` 默认指向临时库，生产需显式设环境变量。但那会改动服务部署配置（`start.sh`、`docker-compose.yml`、`.env.example`），blast radius 超出"零成本、不需拍板"的范围，**需用户决定**。

**顺带查出 `start.sh` 两处陈旧**（未改，属条款6 范围外）：
- `:7`/`:43` 用 `python3`（本机是 3.9.6），而项目硬规矩是必须 3.11
- `:17-19` 检查 `DEEPSEEK_API_KEY` 是否存在，但该常量已在 `869ce2e` 从 `config.py` 移除、代码已不用它。⚠️ **实测更正**：该检查**目前不会失败**（`.env` 里仍留着这个键，长度 35、非空），start.sh 能正常起服务。它是**检查已废弃凭据的僵尸门**——不再保护任何真实依赖（代码实际用的 `LLM_API_KEY`/`GLM_API_KEY` 反倒没被检查），且一旦有人清理 `.env` 里那个无用键就会立刻起不来。修法见 §B

**当前实际有效的防护**（已存在，够用）：
- `tests/conftest.py:33-53`：在 import `src` **之前**重定向 `XIDU_DATA_DIR`/`XIDU_OUTPUT_DIR`/`XIDU_TOKEN_LEDGER`/`CHECKPOINT_DB_PATH`/`CHROMA_PERSIST_PATH` 到临时目录，并把 6 个 API key 全部投毒为 `sk-FAKE-TESTS-MUST-MOCK`；另有 `_block_outbound_network` + `_forbid_live_llm` 两个 autouse guard
- **硬规矩**（round9 §一/§九、round10 §C）：任何写库验证必须在 pytest 内跑

---

## 六、下一位的第一步

```bash
# 1. 确认基线（都应绿）
python3.11 -m pytest -q --no-header                     # → 1682 passed, 4 deselected
python3.11 scripts/static_audit.py --self-test          # → 27/27
python3.11 scripts/static_audit.py                      # → 0 条发现
python3.11 scripts/static_audit.py --include-src        # → 102 文件，0 条发现
git log --oneline -8                                    # 最上应是本交接提交（06e85c9 之上）

# 2. ⚠️ 优先处理：交付物归档（§三-C / §B）
#    difu_en.md 被 .gitignore:18 的 output/ 排除，本轮 A 档修复只有 /tmp 一重保护
ls -la /tmp/xidu_difu_snapshot_20260926_111103/         # 确认快照还在（/tmp 会被系统清理！）
python3.11 scripts/scan_deliverable.py novels/output/difu_segmented/difu_en.md
#    预期：10 条逐章缺陷（B档6章 + C档2章），A 档 4 章已清零

# 3. 零成本、不需拍板，可直接做（§B）
#    a) scripts/destructive_attacks.py:821 死三元（AST 扫描已定位，与条款9 修的同型）
#    b) start.sh 两处陈旧：:7/:43 用 python3（本机 3.9.6，应 3.11）；:17-19 检查
#       已废弃的 DEEPSEEK_API_KEY（实测目前不会失败，因 .env 仍留着该键；隐患是
#       它不再保护真实依赖、且清理该键后会起不来）。改成检查 LLM_API_KEY +
#       GLM_API_KEY，并同步 .env.example
#    c) 搭 READBACK 档位 A/B 对比脚本（§B-2 问题①：全仓没有这工具，ab_memo_test.py
#       比的是备忘不是档位）。搭脚本零成本、不调 API；跑它才要钱

# 4. 需用户确认/批准（§二）—— 用户上轮说"可以开始"但没明确批哪一档，视为未批
#    §二-1 token_ledger 清理：8816 行 → 需明确那 2 行 diag_test 保留还是删
#    §二-2 A/B + B档重译：方案1 ¥7.15 / 方案2 ¥75.23；B档 ¥3.72
#         🛑 跑之前必须先解决 §B-2 两个前置问题，尤其判据要换：
#            verdict 在 _quality.json 里 59 条全 PASS（FIX 循环一路修到 PASS），
#            比"PASS/NEEDS_FIX 一致率"必然得到 100% 空数字。
#            改用 comprehension_issues / engagement_gaps 计数差 + impression
#            文本差异，且关掉 FIX 循环才能看到 READBACK 原始判定。
#            正确顺序：对齐判据 → 搭脚本 → 报金额 → 等批准 → 才跑

# 5. 需用户定方向（§五）
#    条款6 写库侧：库级 guard 结构性无效（事故源是裸跑 -c，不经过任何 guard），
#    有效方案需反转 CHECKPOINT_DB_PATH 默认值，动 start.sh/docker-compose/.env.example

# 6. 需预算组（位次 12–14，§四 表，金额都还没算）
#    条款2 端到端+西语启动 / 条款8 Qwen-MT 嵌入评估 / 条款7 不降质降成本
```

**变异验证命令**（改完逻辑必跑，串行，期间禁编辑）：
```bash
python3.11 scripts/mutation_test.py cost_gate        # → 15/15
python3.11 scripts/mutation_test.py scan_deliverable # → 76/76
python3.11 scripts/mutation_test.py style_memo       # → 61/61
python3.11 scripts/mutation_test.py circuit_breaker  # → 45/45
python3.11 scripts/mutation_test.py graph_post_process # → 73/73
ls src/*.mutation_backup scripts/*.mutation_backup 2>/dev/null  # 应无输出（残留 0）
```

---

## 七、本轮改动文件清单（全部已提交，交付物除外）

**源码（4 M + 1 A + 2 D）**
- `src/cost_gate.py` — **新增**（条款6 单一来源闸门）：`is_expensive`/`expensive_nodes`/`format_refusal`/`guard_tier`/`describe_tiers`/`_invoked_script_name`
- `src/circuit_breaker.py` — 条款15：`_failure_count:int` → `_failure_times: deque`；`_prune_window`；试探限流；`_failure_count` 只读 property；`_reset()`；删 `_last_failure_time`；`snapshot` 新增 `window_seconds`/`half_open_max`；`get_breaker` 补 `half_open_max` 透传
- `src/style_memo.py` — 条款9：删 `read_relevant` 两个死参数（改 keyword-only）；消除 `read_all:115` 死三元；两处文档谎报如实化（pruning / 规模）
- `src/agent/graph.py` — §四-1 删死字段初始化；条款9 同步 `read_relevant()` 调用点
- `src/agent/state.py` — §四-1 删 `term_conflicts`/`resolved_conflicts` 定义
- `scripts/scan_deliverable.py` — 条款16：新增 `dominant_quote_style`/`check_quote_style`/`check_glossary_conflicts`/`_load_glossary`/`_print_quotes`/`_print_glossary`/`_QUOTE_LIST_LIMIT`；`--strict` 与结论行纳入两类新判据
- `scripts/run_novel.py` — 条款6：开跑前打印逐节点路由与单价 + 指向零成本估算
- `scripts/adversarial_test.py`、`scripts/diag_api_hang.py`、`scripts/cost/probe_nodes.py`、`scripts/cost/probe_nodes_wide.py` — 条款6：4 份 `_guard_tier` 副本改为调用 `cost_gate`；删 `adversarial_test.py` 的 `os`/`textwrap` 未用导入
- `scripts/cost/estimate_offline.py` — 条款16 连带：`--book` 未知 key 由静默失败改为退出码 2；`main(argv=None)`；`SystemExit(main())`
- `scripts/mutation_test.py` — 新增 TARGET `cost_gate`
- `scripts/static_audit.py` — `_MUTATION_COVERED` 补 `src/cost_gate.py`；顶部文档更新为"已接入 CI"
- `.github/workflows/test.yml` — 条款4：pytest 之前新增 `Static audit` 步骤（`--self-test` + 扫 scripts/ + `--include-src`）
- **删除**：`scripts/run_script_pilot.py`(180行)、`scripts/compile_script_sample.py`(164行)

**测试（5 M + 2 A，本轮净 +108）**
- `tests/test_cost_gate.py` — **新增** 28（闸门放行/拒绝决定、legacy 变量盲区双向回归、单价单一来源、argv 边界）
- `tests/test_estimate_offline.py` — **新增** 4（未登记 `--book` 硬失败、两套 key 不一致的警告、正常路径）
- `tests/test_scan_deliverable.py` — +49（引号风格 4 类、术语冲突 3 类、`_load_glossary` 6 条、`--strict` 语义、列表截断、报告标签、行号定位）
- `tests/test_fault_injection.py` — +21（滚动窗口 8、试探限流 5、snapshot 契约、`_reset`、假绿 Defaults 升级为行为断言；§七-C 那条改写而非删）
- `tests/test_prompt_personas.py` — +6（备忘到达 WRITE/READ 提示词、FIX/READBACK 有意不消费、缺键兜底）
- `tests/test_graph_units.py` — `FakeMemo` 改 keyword-only；假绿测试改写为断言真实契约
- `tests/test_fault_injection.py` / `test_v15_pipeline.py` / `test_write_guards.py` — §四-1 夹具清理

**交付物（未提交，被 .gitignore 排除）**
- `novels/output/difu_segmented/difu_en.md` — §三-E A 档 4 章修复，净减 12 行。回滚见 §三-C
