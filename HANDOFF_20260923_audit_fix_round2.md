# 西渡交接（2026-09-23 · 审计修复第 2 轮 · 第1条"变异清零"子任务完成）

> **本文档性质**：工作区状态快照，不是完工报告。
> **本轮范围**：只做完了第1条里的一个子任务——`health.py` / `stats.py` 两个变异 target 的存活清零。
> **第1条整体仍未完成**：①其余变异 target 本轮未重跑，状态未知；②上一轮 §4.2 的 6 条待办（死代码/断头/污染语料等）一条未动。
> 所有改动**已落盘但未提交**（`git status` 25 M + 5 ??）。测试基线 **1241 passed**（上轮 1154，本轮净增 87）。
> 生产数据四个指纹实测**零改动**（ledger 8813 行 / events.db / checkpoints.db / output 817 文件）。
> 本文档承接 `HANDOFF_20260923_audit_fix_wip.md`，该文件第六节的调查结论仍然有效，**不必重新调查**，下文只在有更新处标注。

---

## 一、本轮做了什么（一句话：把"假变异"这层假绿补上）

上一轮把测试从 1090 加到 1154，但变异测试暴露了 **162 个变异体里 121 个存活**——新写的测试仍是假绿（只断言状态、不断言机制）。本轮针对存活最密集的 `health.py`(103 存活) 与 `stats.py`(18 存活) 补分支测试，把这两个 target 打到清零。

### 1.1 实测结果（本轮亲眼所见，非声称）

```
src/health.py : 134 mutants → 134/134 killed (0 survived, 0 timed out)   退出码 0
src/stats.py  :  28 mutants →  27/27  killed (0 survived, 1 proven-equivalent skipped)  退出码 0
全量套件      : 1241 passed, 4 deselected
源文件还原    : diff 确认 health.py / stats.py 与备份一致，无 *.mutation_backup 残留
```

### 1.2 `health.py` 六个函数：从"恰好 killed 1 个 return" 到分支全覆盖

上轮每个检查函数只 killed 1 个（全是 `return {...} -> None`），分支逻辑一个没测。本轮逐个补齐（数字为本轮实测 killed/总数）：

| 函数 | 上轮 | 本轮 | 本轮补的关键分支 |
|---|---|---|---|
| `check_disk_space` | 1/17 | 17/17 | 500MB 阈值边界（等于不算低）、`critical` 标志、`1024²` 换算、`parents/exist_ok`、`disk_usage` 异常 |
| `check_output_dir_writable` | 1/9 | 9/9 | 路径被普通文件占用→critical、写失败、探针清理 |
| `check_sqlite` | 1/7 | 7/7 | 目录当库路径→OperationalError、缺父目录、连接必关闭 |
| `check_chroma` | 1/15 | 15/15 | 三态（无 store / healthy / unhealthy→**warn 非 error**）、探测异常→warn、不可写→warn |
| `check_memory` | 1/21 | 21/21 | **假 psutil 注入**激活数值分支、80/81 与 95/96 边界、`1024³`+2位小数换算、ImportError 真实路径 |
| `check_disk_io` | 1/14 | 14/14 | 读回不符→critical、`urandom(64)` 长度锁定、5000.5ms 慢盘 warn、unlink 失败 |
| `check_all` | 7/13 | 13/13 | 「非 critical error→degraded 非 unhealthy」「warn 带 critical 仍不 unhealthy」「`critical` 默认 False」——逐个锁 `and`/`or`/默认值 |
| `check_llm_api` | 13/31 | 31/31 | `max_tokens==1`、`timeout==5.0`、错误体截断到 200、三分支都带 `live_probe:True` |

### 1.3 `stats.py`：补齐窗口算术（仪表盘当作事实展示的数字）

环形缓冲上限（`_max_recent=200` 过填验证截断 + 保尾不保头）、吞吐量 5 分钟窗口（方向 `>=cutoff` 不能反、300s 边界、`/5.0`）、错误率取样（`[-100:]` 窗口 + 全局计数器不受窗口影响 + `total/failed` 布尔位 + `round(_,4)`）、uptime `round(_,1)`。全部用**冻结时钟**断言精确值，不用 `>=0` 这类废断言。

### 1.4 ⚠️ 推翻了上一轮文档 §4.1-3 的一条错误建议

上轮文档说："计时样板可用一条 `assert latency_ms >= 0` 批量清掉"。**这条是错的，一个都杀不死**：`elapsed = round((monotonic()-start)*1000, 1)` 里，`1000→1001` 结果仍 ≥0，`round(_,1)→round(_,2)` 也仍 ≥0。正确做法是注入可控时钟喂固定 delta（0.123456s），断言精确 `123.5ms`——`_FakeTime`/`fake_time` fixture 已实现，`_EXPECTED_MS=123.5`。这是"凭印象写建议、没验证"的又一例，与上轮 §4.3 记录的错误同形。

---

## 二、变异闸门本身的两处加固（让"清零"可信、可持续）

### 2.1 等价变异体登记表 `EQUIVALENT_MUTANTS`（`scripts/mutation_test.py:114`）

`stats.py:194` 的 `round(throughput,2)→round(...,3)` 是**数学等价变异体**：`throughput = int/5.0`，整数除以 5 最多 1 位小数（.0/.2/.4/.6/.8），舍入到 2 位或 3 位必然相同。已全枚举 `k=0..2000` 验证 **0 处差异**，证明附在登记项里。

为什么登记而不是硬造断言去杀：不可杀变异体若永久挂在"survived 盲区"里，会让每次运行都显示非零数字，操作者久而久之开始忽略它——**这正是假绿的温床**。登记表把它如实分类为"已证等价"，盲区计数只留真正可杀的。

**防腐蚀机制**：登记项每轮校验是否仍匹配生成的变异体；匹配不到就报 `[STALE-EQUIVALENCE]` 并计入失败（通常是行号漂移或表达式被改），登记表不能悄悄烂掉去藏真存活。

### 2.2 闸门改为可失败（`scripts/mutation_test.py:371-380`）

`main()` 现在 `return 1` + `sys.exit(main())`：只要有 `survived / timed_out / stale / blocked` 任一非零就以**非零退出码**失败。`REFUSING TO RUN`（发现残留 backup）和模块/测试文件缺失也计入 `blocked` 而非返回全 0——**源文件可能还带着变异体时，绝不能报绿**。一个不能失败的闸门只是文档，不是闸门。

> 本轮已用注入变异体实测验证：往 `check_memory` 塞 `pct>95 → <=` 后跑 `mutation_test.py health`，闸门如期 `FAIL` + 退出码 1 + `survived:1`，源文件随后还原。

---

## 三、我诊断了但**没做完**的（已回退，无半成品残留）

### 3.1 变异闸门慢——根因已定位，优化未实施

用户问"闸门怎么这么慢"。实测数据：

- 单个 pytest 子进程冷启动 **~2.0s**，其中 `import langchain_openai` 独占 **~1.5s**
- `tests/conftest.py` 的 `_forbid_live_llm`（autouse fixture）**每个测试无条件 import 全部 5 个 LLM 节点模块**（连带 langchain），哪怕测试根本不碰 LLM
- 变异测试 162 个变异体 = **162 个独立 pytest 进程**，`162 × 1.5s ≈ 243s` 全烧在与 health/stats 无关的 langchain 导入上

**优化方案（未实施）**：把 `_forbid_live_llm` 改成惰性安装——守卫只在节点模块真正被 import 时才装，不碰 LLM 的测试零 langchain 开销。预计单模块变异从 ~4 分钟压到 ~30 秒。**关键约束：不能削弱任何拦截**（这是第6条"测试恒 ¥0"的承重墙），必须保证"测试函数中途才 import 节点"也能事前拦截，不漏拦、不烧钱。

**当前状态**：我动手时改了 conftest 顶部 import（加 `importlib.util`/`sys`），被用户喊停，**已完整回退**——`grep importlib.util tests/conftest.py` 为空，`_forbid_live_llm` 仍是原样无条件 import 版本（`conftest.py:180` 起）。工作区干净，无半成品。

---

## 四、第1条剩余待办（继承自上轮 §4.2，本轮一条未动）

> ⚠️ 这些仍在第1条范围内。"变异清零"只是第1条的一个子任务，不代表第1条完成。

1. **其余变异 target 未重跑**：`mutation_test.py` 的 `TARGETS` 有 20+ 项（`graph_post_process`、`write_guards`、`exact_store`、`token_ledger`、`completeness` 等）。本轮只跑了 `health`/`stats`，**其余模块的当前变异存活状态未知**——可能是上轮就有的盲区，也可能因上轮改动而漂移。下一位应择机全量跑一遍 `python3.11 scripts/mutation_test.py`（注意：很慢，见 §三；建议先做 §3.1 的提速）。
2. **`job_store.update_token_usage` / `get_job_cost` 死代码**：生产端零调用者（`/jobs/{id}/cost` 已改读 `token_ledger.summary()`），仅 `tests/test_job_store.py:132-168` 在测且断言 DeepSeek 美元价（`0.42/0.14/0.28`）。上轮"留着不破坏第三方读取"的理由不成立（内部 SQLite 方法非公开 API）。**建议删方法 + 删对应 4 个测试**，或改为委托 `token_ledger`。
3. **`scripts/check_source.py` 断头**：`from src.encoding import detect_and_read`，而 `src/encoding.py` 已重命名为 `src/text_io.py`。该文件被 `.gitignore` 忽略（未跟踪），是一次性调试草稿。**改 import 为 `src.text_io` 即可跑通，或删除（删未跟踪文件不可恢复，需用户确认）**。
4. **`scripts/` 其余 DeepSeek 残留**：`probe_deepseek_concurrency.py`（有 `main()` 守卫、导入安全，但探针对象是废弃 provider，已无用途）；`scripts/test_jianke.py:77` 与 `translate_and_verify.py:436` 打印 `/5.0` 满分制——`quality_score` 语义上轮已改为"未测量=0.0"，这些输出解读需在文档说明（当前无实际错误，因这两脚本不跳过冷读）。
5. **`run_novel.py` 的 `name_en` 未配置**：5 本书的 `name` 全中文，上轮**未臆造英文书名**，回退到 `book_id` 的 ASCII 化并打印 NOTE。需人工给出正式英文书名填入 `NOVELS[key]["name_en"]`：`limitless_horror`(无限恐怖)、`difu`(地府叫我小先生)、`jianke`(间客)、`quanzhi_fanwai`(全职高手番外之巅峰荣耀)、`yuannicanlanruyang`(愿你灿烂如阳)。
6. **已污染的历史语料未清理**：`novels/output/difu_segmented/difu_en.md` 等真实交付物里**已存在**中文标题（`# 地府叫我小先生 — English Translation`、`## Chapter 0: Prologue（楔子）`）。代码已修，存量文件未回改。属第14条范围。
7. **`output/` 404 个测试残留文件**（上轮记录；本轮实测 output 共 817 文件，隔离生效后不再新增）。存量未清理，`git ls-files output/` 为 0（全部未跟踪）。**清理属破坏性操作，需用户确认。**

---

## 五、后续条款调查结论（继承自上轮 §六，仍有效，不必重查）

上轮已查明、本轮无更新的条款，**直接读 `HANDOFF_20260923_audit_fix_wip.md` 第六节**，要点复述如下以免遗漏：

- **第11条 Agent 通用性**（问题严重，清单完整）：`prompts/write.py`/`read.py`/`readback.py`/`fix.py` 大量硬编码 `American horror`/`English`/具体恐怖书名；`target_lang`/`genre` 在 state 定义、graph 注入，但 READBACK/FIX **两参数都没传**，WRITE 未传 `target_lang`，READ 的 system prompt 全文硬编码忽略参数 → 传递链断裂。`genre` 默认值 `romance_ceo` 与 prompt 恐怖预设自相矛盾。`cultural_rules.json`(68KB) 结构已查明：`common` 覆盖 en-US/es-ES/ar-SA 三语种，`genres` 有 5 类型但**无 horror/thriller**（与硬编码错位）。
- **第10条 READBACK 盲读独立性**（已基本验证通过）：READBACK 是真盲读，只从 state 读 `translated_text`+`cold_read_context`，prompt 无原文占位符。**唯一泄漏（🟡极低）**：`graph.py:155` `_build_cold_read_context()` 角色列表含中文名 `f"  - **{en}** ({cn})"`，元数据级，严格盲读应剥离。FIX 正确看到原文（符合编辑角色）。
- **第13条 TM/TB**（现状已审计，缺口明确）：`exact_store`(SQLite+内存 O(1)) 与 `semantic_store`(真 embedding `all-MiniLM-L6-v2`，每书每语种独立 collection) 已有，`book_id` 隔离已修好。**四缺口**：①TM 句对存储完全空白 ②FIX 自动回写闭环不存在（`confirm_term`/`reject_term` 仅被人工 UI 调用）③多语种只有 schema 预留、实际产出仍扁平 dict ④`scan_deliverable.py` 不存在。
- **第12条 推理档位**（已查明）：READ/READBACK/REVIEW_TERMS 用 `qwen3.8-max` + `thinking_budget=2048`；WRITE 用 `glm-5.3-flash` + `reasoning_effort=low`；FIX 用 `glm-5.3` + `high`。无同设冲突。**最大杠杆**：`MODEL_MAP` 把 READ/READBACK/REVIEW_TERMS 挂在同一常量 `QWEN_MODEL`，一动全变 max；READBACK 只是"读得懂吗"判断，不需要 max。换档账单差 10-17 倍（`COST_MODEL_20260922.md`：现状 ¥1974 → 全 flash ¥198）。单价权威源 `config.py:106-114`。**档位数量口径（3/5/6 档）未实测验证**，需按第12条以实际 API 行为为准。
- **第14条 全量扫描**：`novels/output/` 共 29MB。可复用 `src/completeness.py` 三判据（长度比/对话 span 比/术语覆盖，零成本无需 API，已标定 11/11 命中缺陷 0/8 误报；注意 `glossary_missing` 不能作判据，区间重叠）。当前只记录不拦截。`src/output_guard.py` 正则检测残留中文/过短/LLM 废话。
- **第8条 Qwen-MT**（未开始）：需先联网查官方文档（档位/术语干预 `terms`/领域提示/翻译记忆/上下文限制/定价/限流）再评估嵌入位置。外部建议的 REVIEW_TERMS 位置存疑（Qwen-MT 是翻译执行模型，REVIEW_TERMS 是评审决策）。候选 WRITE/FIX。**变更方案须确认后再实施，不直接改。**
- **第6条 API 管控**（上轮已落地大半）：conftest 双层拦截 + `live_api` marker + `addopts` 排除 → `pytest` 裸跑恒 ¥0（机器可检不变量）；`diag_api_hang.py` 加成本闸门；`auto_triage.py` 默认 dry-run。**待办**：把闸门模式推广到 `scripts/` 下其余 ~20 个"跑就花钱"无闸门脚本（清单见上轮 §六第6条）。
- **第4条 边界**：`Dockerfile:1` 是 `python:3.11-slim` 而 `pyproject.toml` 声明 `py39`，运行时口径矛盾，建议统一 3.11。`scripts/compile_script_sample.py`/`run_script_pilot.py` 是剧本线残留（已剥离为"译境"项目）。
- **第9条 主笔 agent**：`style_memo.py` 存风格经验（markdown 6 抽屉，按 book_id 隔离），是"经验注入"非"纵向权威角色"，倾向判定"缺主笔 agent"。需核实 style_memo 是否真被 WRITE/FIX 消费、有无否决权语义。

---

## 六、复现与验证（下一位先跑这三条）

```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"

# 1. 全量基线（应 1241 passed；裸跑恒 ¥0，live 测试被 addopts 排除）
python3.11 -m pytest -q --no-header

# 2. 变异闸门（health/stats，应双双退出码 0、survived 0）
cp src/health.py /tmp/h.bak && cp src/stats.py /tmp/s.bak
python3.11 scripts/mutation_test.py health stats; echo "退出码=$?"
diff -q /tmp/h.bak src/health.py && diff -q /tmp/s.bak src/stats.py   # 必须都一致
find . -name "*.mutation_backup" -not -path "./.git/*" | wc -l         # 必须为 0

# 3. 零污染比对（ledger/events.db/checkpoints.db/output 文件数四指纹跑测前后必须全不变）
L0=$(wc -l < data/token_ledger.jsonl); D0=$(md5 -q data/translation_events.db)
C0=$(md5 -q data/checkpoints.db); O0=$(find output -type f | wc -l)
python3.11 -m pytest -q --no-header
# 再取 L1/D1/C1/O1 比对
```

**本轮实测基线值**（供下一位对照，若变了说明有污染或回归）：
`ledger=8813 行`，`events.db / checkpoints.db md5 不变`，`output=817 文件`，`1241 passed`。

---

## 七、给下一位的提醒

1. **必须用 `python3.11`**（本机默认 `python3` 是 3.9）。`src/text_io.py` 上轮已补 `from __future__ import annotations`，3.9 下 API 链 import 不再崩，但依赖装在 3.11/3.9 两处，混用会出难解导入错误。
2. **变异测试必须串行**，跑完 `diff` 确认源文件还原 + `find *.mutation_backup` 为 0；脚本有 SIGTERM 处理器与"发现残留 backup 就 REFUSING TO RUN"保护。
3. **不要相信任何"已完成"字样，包括本文档**。本轮就推翻了上轮 §4.1-3 的错误建议（`latency_ms>=0` 杀不死变异体）。每处修复都要能追溯到实测证据。
4. **等价变异体登记要附证明**，且 `EQUIVALENT_MUTANTS` 只登记"数学上不可区分"的，不是"难测就塞这里"的垃圾桶——难测的属于测试文件，不属于登记表。
5. 改 `scripts/` 生产脚本前先跑 `tests/test_run_novel.py`/`test_run_one_segment.py`（锁死进度真实化+零中文+不崩溃）。
6. **真实 API 调用一律先报预算等批准**（第6条）。`scripts/` 下 ~20 个脚本仍"跑就花钱"无闸门。
7. **破坏性操作（删 404 残留文件、回改污染语料、删未跟踪的 check_source.py）一律先请用户确认**——删未跟踪文件不可恢复。

---

**本文件由第 2 次审计+修复会话产生。第1条"变异清零"子任务完成（health/stats 实测 0 存活），第1条整体未完成（其余 target 未跑 + §四 7 条待办未动）。工作区改动未提交。**
