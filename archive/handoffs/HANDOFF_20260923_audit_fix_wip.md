# 西渡交接（2026-09-23 · 审计修复第 1 轮，第1条未完成）

> **本文档性质**：一次会话的**工作区状态快照**，不是完工报告。
> **当前状态：14 条指令中，第1条尚未完成**（剩余工作见第四节，已精确到行号）。
> 所有改动**已落盘但未提交**（`git status` 可见）。测试基线 **1154 passed**。
> 本文档优先级高于 `HANDOFF_20260921_audit_untrusted.md` 中与本次实测冲突的部分。

---

## 一、本轮最重要的结论：缺陷不是散的，是三条因果链

审计中共查实 **19 处缺陷**（含 5 处上一轮文档未记录的新发现）。按根因归类：

### 链 A：换模型只换主干，没扫外围（6 处）

`c121f0b`（DeepSeek → GLM/Qwen）只改了 `config.py` 与五个节点文件，外围全部遗留：

| 位置 | 缺陷 |
|---|---|
| `src/health.py` `check_config` | 仍要求 `DEEPSEEK_API_KEY`；真正的 `LLM_API_KEY`/`GLM_API_KEY` 一个不验 → 全新部署会被 `main.py` 拒绝启动 |
| `src/stats.py:44-58` | 仍按 DeepSeek USD 价算钱；档位标签错位（READ 跑 qwen-max 记成 `"pro"`，FIX 跑 glm-5.3 记成 `"flash"`） |
| `src/web_ui.py` ×2 | 前端 JS 硬编码 `$0.14/$0.28`；且读的 `tokens_input/output` 列恒为 0 → 双重死代码 |
| `scripts/evaluate_native.py:27` | import 已删除的 `DEEPSEEK_API_KEY`/`DEEPSEEK_BASE_URL` → ImportError；**且引用 `MODEL_MAP["quality_score"]`，这个键从来不存在** |
| `scripts/diag_api_hang.py:6` | 同上 ImportError；且**顶层执行**（import 即加载 7MB 小说 + 发真实请求） |
| `scripts/seed_from_analyze.py:76,100` | 函数内 import 已删除常量 + 硬编码 `model="deepseek-chat"`；**它是活代码**（`run_novel.py:125` 的 `--seed` 路径会 import 它） |

### 链 B：报告来自意图，不来自观测（6 处）

| 位置 | 缺陷 |
|---|---|
| `scripts/run_novel.py` | `ALL DONE: {len(chapters)}` 用扫描数当交付数；实测 1/2 交付报 "100%" |
| `scripts/run_one_segment.py:183` | 同上；且 `glossary` 只在 while 内赋值 → 全部已 checkpoint 时 `UnboundLocalError` |
| `src/api/routes.py:323` + `:609` | `complete_job` 无条件调用，失败章 `continue` 后仍标记完成 |
| `graph.py:300,391` | `quality_score` 初值 5.0，跳过冷读的章节永远"满分"，且无 `readback_skipped` 标记 → 下游无法区分"冷读通过"与"根本没人读" |
| `run_novel.py` `--limit N` | 所有 `sample_points_fn` 起点是 10（或 3/5）→ `--limit 1/2` 抽样集为空，"快速验证模式"零质量检查 |
| 交付物标题 | `display_title = title_en or ch.title[:60]` → 中文标题落进英文成品；**实测已污染真实语料**：`novels/output/difu_segmented/difu_en.md` 首行 `# 地府叫我小先生 — English Translation`，且有 `## Chapter 0: Prologue（楔子）` |

### 链 C：测试断言快乐路径，从不断言机制（5 处）——**这是承重墙**

链 C 不产生直接损失，但它让 A 和 B **永远不被发现**：

```
测试只断言状态码 → 假报告没人抓 → 换模型烂尾没人抓 → 成品混着中文标题和空章
```

| 位置 | 缺陷 |
|---|---|
| `src/health.py:213` | `check_llm_api` 发**真实 dashscope 请求**，异常在 `:231-233` 降级成 `warn`；而 `/api/health` 无鉴权无限流、`dashboard.py` 前端 `setInterval(poll, 5000)` → **开着仪表盘每 5 秒烧一次钱**，且 `test_health` 断言 `status in ("healthy","degraded")` 照样 PASS |
| `tests/test_api_routes.py:4` | 文件头写 "mocked LLM"，fixture（16-30 行）**一行 mock 都没有** → 6 个测试在后台线程真跑完整流水线（`routes.py:500` → `graph.py:233` → `read.py:157 llm.invoke`），只断言 HTTP 状态码所以全绿 |
| `tests/test_glossary_accumulation.py` | 4 个测试 patch 了 read/write/readback，**唯独漏 `review_terms`** → 真实调用，异常被 LangGraph 吞掉，测试"通过" |
| `scripts/auto_triage.py` | **整个脚本顶层执行**：import 就 `DELETE FROM exact_glossary`（23-26 行）+ 真实 API + 批量 `UPDATE status='confirmed'`。任何测试收集器/编辑器索引碰它就删生产数据 |
| `tests/conftest.py` | 无任何网络封禁、无数据隔离 → 实测一次 `pytest` 写入生产台账 **241 行**假记录、改动 `translation_events.db`、在 `output/` 留 **404 个**测试残留文件 |

**关键判断**：`HANDOFF_20260921_audit_untrusted.md` §9.6 声称"测试套件零 API 调用，跑一次 ¥0"——**该结论作废**。封网插件实测拦到 11 次 `('dashscope.aliyuncs.com', 443)`。

### 独立缺陷（2 处）

| 位置 | 缺陷 |
|---|---|
| `src/text_io.py:28` | 用 PEP604 `str \| None` 但**缺 `from __future__ import annotations`**，而 `pyproject.toml` 声明 `target-version = "py39"` 并注释"会在 import 时崩溃"。它是全项目**唯一**缺该保护的文件 → py3.9 下 `src.api.routes` 整条链 import 即崩。生产 Docker 跑 3.11 所以没暴露 |
| `src/job_store.py:393` `get_confirmed_terms` | 跨模块读 `exact_glossary` 表，但该表由 `glossary/exact_store.py` 创建，`job_store` 自己不建表也不防御 → 表不存在时 `sqlite3.OperationalError` 从 `write_node` 抛出、章节失败。**此前测试因共用那个"恰好有表"的污染库而一直假绿**；是本轮加了数据隔离后才暴露的真实生产缺陷 |

---

## 二、本轮已修复并验证的内容

**测试：1090 → 1154 passed**（净增 64 个）。生产数据四个文件实测**零改动**（ledger / translation_events.db / checkpoints.db / output/ 文件数）。

### 2.1 链 C 修复（防护已固化，不再依赖人工声称）

| 改动 | 文件 |
|---|---|
| `check_llm_api` 默认零成本配置检查；真实探测需 `live_llm=True` | `src/health.py:218-304` |
| 探测失败如实报 `error`（不再降级 `warn`）；缺 key / base_url 畸形 → `critical: True` | `src/health.py:233-249,290-304` |
| `check_config` 改验 `LLM_API_KEY` + `GLM_API_KEY`；废弃 DeepSeek 变量降级为 `warn` 提示 | `src/health.py:130-187` |
| 新增 `HEALTH_LIVE_LLM_PROBE` 开关，默认 `false`；`main.py` 按开关传参 | `src/config.py:57-61`、`src/main.py:13,33` |
| **conftest 双层强制拦截**：① `_block_outbound_network` 拦非回环连接 ② `_forbid_live_llm` 逐节点替换 `ChatOpenAI` 为 `_LiveLLMGuard`，违规**记入 `_llm_violations` 并在 teardown `pytest.fail`**（因为异常会死在后台线程里） | `tests/conftest.py` |
| `drain_background_threads()`：在 mock 还原**之前** join 掉 `routes.py` 起的 daemon 线程，否则漏网线程撞上真实客户端且违规被算到下一个测试头上 | `tests/conftest.py:151-166` |
| 数据隔离：`DATA_DIR`/`OUTPUT_DIR` 支持 `XIDU_DATA_DIR`/`XIDU_OUTPUT_DIR` 覆盖；conftest 在**任何 src 导入之前**重定向（含 `CHROMA_PERSIST_PATH`、`CHECKPOINT_DB_PATH`、`XIDU_TOKEN_LEDGER`） | `src/config.py:10-21`、`tests/conftest.py:29-46` |
| 凭证投毒：`LLM_API_KEY` 等 6 个变量设为假值；**`CHROMA_HOST` 不能投毒**（它是主机名不是密钥，投毒会让 chromadb 连向乱码主机产生误报） | `tests/conftest.py:48-56` |
| 新增 `live_api` marker；`addopts` 排除 `requires_api_key` 与 `live_api` → **`pytest` 裸跑恒为 ¥0** | `pyproject.toml:47-58` |
| `test_api_routes.py` 补 `_stub_translate_chapter`（autouse），打桩 `TranslationAgent.translate_chapter`；返回结构含 `translated_text`/`pre_filter_text`/`chapter_title_en`/`chapter_summary`，长度足以越过 `routes.py` 的截断守卫 | `tests/test_api_routes.py:24-58` |
| `test_glossary_accumulation.py` 补 `_mock_review_terms`（autouse）；`REVIEW_TERMS_PASS = {"reviews": []}` 表示全部放行无修正 = 这些测试写作时的原行为 | `tests/test_glossary_accumulation.py:136-159` |
| `auto_triage.py` **整体重写**：`main()` + `__main__` 守卫、默认 dry-run（不调 API 不写库）、`--apply` 才写、`--no-delete` 抑制 REJECT 删除、改用 `LLM_API_KEY`/`QWEN_MODEL`、JSON 围栏剥离 | `scripts/auto_triage.py` |

**新增测试文件**：`tests/test_health.py`（19）、`tests/test_auto_triage.py`（22）、`tests/test_run_novel.py`（14）、`tests/test_run_one_segment.py`（7）。

### 2.2 链 B 修复

| 改动 | 文件 |
|---|---|
| 进度按**真实写出数**算（`len(written_chapters)`），非批次边界；不完整打印 `INCOMPLETE: N/M` + 缺失章列表 + `sys.exit(1)` | `scripts/run_novel.py:354-364,374-402` |
| `_cost.json` 新增 `chapters`(交付) / `chapters_scanned` / `chapters_failed` / `complete` 四字段 | `scripts/run_novel.py:386-401` |
| `glossary` 在 while **之前**初始化（修 UnboundLocalError）；新增 `failed_positions` | `scripts/run_novel.py:186-196` |
| 零中文：H1 用 `deliverable_title`（`name_en` → 否则 `book_id` 的 ASCII 化，**不臆造英文书名**）；章节标题 `title_en or f"Chapter {ch_num}"` | `scripts/run_novel.py:167-178,335-340` |
| `--limit N` 兜底：抽样集与 `[1..total]` 无交集时强制冷读第 1 章（及末章） | `scripts/run_novel.py:146-157` |
| 删除不可达死分支 `elif word_count < 50: EMPTY`（因 `min_words = max(50, …)` 恒 ≥50）；截断/空译文计入 `failed_positions` | `scripts/run_novel.py:278-286` |
| `run_one_segment.py` 同批修复：`glossary` 前置初始化、`failed_positions`、零中文标题、空译文不写盘、`INCOMPLETE` + `sys.exit(1)` | `scripts/run_one_segment.py:58-64,96-102,136-154,183-201` |
| `routes.py` 同步路径：`delivered_count`/`failed_indexes`；有失败则 `fail_job(带缺失清单)` 而非 `complete_job`；标题零中文；H1 用 `{target_lang}` 而非硬编码 "English" | `src/api/routes.py:259-260,291-353` |
| `routes.py` 多语言路径：同上 + **修 H1 硬编码 "English Translation"（西语交付物被标成英语）** + `complete_job(jid, path, 0)` 的 `0` 改为真实 `len(agent.exact_store)` + **计数器必须在 `_run_translation` 内初始化**（它在 `_run_sync` 里初始化过，跨函数作用域会 NameError） | `src/api/routes.py:528-533,564-637` |
| `quality_score` 初值 5.0 → **0.0（未测量）**；新增权威标记 `readback_skipped`；返回处 `5.0` 默认值 → `0.0`，`readback_skipped` 默认 `True` | `src/agent/state.py:32-45`、`src/agent/graph.py:300-306,390-401` |
| 改正两个**把 bug 锁成预期**的测试：`test_quality_score_starts_at_five` → `..._starts_unmeasured`；`test_split_quality_score_defaults_to_five` → `..._stays_unmeasured_when_absent`（并断言 `readback_skipped is True`） | `tests/test_graph_units.py:361-378,728-748` |

**下游影响已核实（重要）**：`quality_score` 改 0.0 后，跳过冷读的章节会被 `< 3.0` 误判为低质。实测三个消费方**均安全**：`translate_and_verify.py` 与 `test_jianke.py` 都不传 `skip_readback`（总是冷读）；`ab_memo_test.py:96` 有 `if fb and fb.get("verdict")` 守卫，只在冷读真跑过时才聚合。

### 2.3 链 A + 独立缺陷修复

| 改动 | 文件 |
|---|---|
| 补 `from __future__ import annotations` → py3.9 下 `src.api.routes` 恢复可导入（已实测 `python3 -c "from src.api import routes"` 通过） | `src/text_io.py:15-21` |
| `get_confirmed_terms` 加 `sqlite_master` 表存在性检查，表不存在返回 `{}` | `src/job_store.py:393-421` |
| `stats.py` 移除 flash/pro 分桶与 USD 价目常量，改单一 `_tokens_input`/`_tokens_output`；`token_snapshot()` **不再含任何成本字段**；`snapshot()` 移除 `estimated_cost_usd` 与 4 个 tier 字段 | `src/stats.py:44-58,114-152,204-216` |
| `record_tokens(tier=)` 参数**保留但忽略**（5 个调用点仍传，改签名要动 5 处且该值已无信息量）；`tier` 默认值 `"flash"` → `""` | `src/stats.py:114-130` |
| 前端两处死成本渲染：`renderJobDetail` 的成本卡删除（同步函数无法取数）；`loadJobResults` 改 `await fetch('/api/jobs/{id}/cost')` 显示 `¥` + unmetered/unpriced 提示 | `src/web_ui.py:969-972,1076-1094` |
| 仪表盘 Config 卡 `'DeepSeek V4 / Celery'` 硬编码 → 真实 `version` + `model_stack`（`" + ".join(sorted(set(MODEL_MAP.values())))`）；`get_dashboard_data()` 补 `version`/`model_stack`/`model_map` 字段 | `src/dashboard.py:13-19,217,283-295` |
| `evaluate_native.py` 改用 `LLM_API_KEY`/`LLM_BASE_URL`/`QWEN_MODEL` + `extra_body={"thinking_budget":2048}`；密钥校验改 `LLM_API_KEY`；加"会产生真实计费调用"提示 | `scripts/evaluate_native.py:27,95-113,170-175` |
| `seed_from_analyze.py` 改用 `GLM_API_KEY`/`GLM_BASE_URL`/`GLM_MODEL`（术语渲染属文字输出类，与 WRITE 同档） | `scripts/seed_from_analyze.py:74-82,105-116` |
| `diag_api_hang.py` **整体重写**为通用 provider 延迟诊断：`main()` 守卫、`_guard_tier()` 成本闸门（高价档退出码 2，与 `probe_nodes.py` 一致）、`--tests` 子集选择、test 6 免费、write 档用 GLM / judge 档用 Qwen、**补上 `fidelity_rules` 占位符**（旧版 format 漏传，文化保真层加入后必 KeyError） | `scripts/diag_api_hang.py` |

**诊断脚本重写已实测验证**（零 API）：导入无副作用（network=0 sqlite=0）、闸门退出码 2、`--allow-expensive --tests 6` 退出码 0、`--tests 6` 跑通并输出 `System prompt 7757 chars / User prompt 28502 chars / Total ~12086 tokens`——**这同时证明 `fidelity_rules` 修复有效**。

### 2.4 变异 target 扩充

`scripts/mutation_test.py` 的 `TARGETS` 新增两项（`health.py` 与 `stats.py` 此前**完全不在变异覆盖范围内**）：

```python
"health": ("src/health.py", ["tests/test_health.py"]),
"stats":  ("src/stats.py",  ["tests/test_stats.py"]),
```

---

## 三、变异验证结果（第1条未完成的直接原因）

已跑：`python3.11 scripts/mutation_test.py health stats`（源文件已确认完整还原，无残留 `*.mutation_backup`）。

**结果：162 mutants / killed 41 / survived 121。** 我新写的 41 个测试**远不够**——这本身又是一层假绿。

### 3.1 `health.py`：134 mutants，killed 31，survived 103

按函数归类（关键发现：**每个检查函数恰好只 killed 1 个，且全是 `return {...} -> None` 变异体**，即分支逻辑一个都没测到）：

| 函数 | killed | survived | 覆盖率 | 状态 |
|---|---|---|---|---|
| `__init__` | 1 | 0 | 100% | ✅ |
| `check_all` | 7 | 6 | 54% | ⚠️ 启动闸门逻辑未测全 |
| `check_config` | 4 | 2 | 67% | ⚠️ |
| `check_llm_api` | 13 | 18 | 42% | ⚠️ |
| `check_chroma` | 1 | 14 | 7% | ❌ 未覆盖 |
| `check_disk_io` | 1 | 13 | 7% | ❌ 未覆盖 |
| `check_disk_space` | 1 | 16 | 6% | ❌ 未覆盖 |
| `check_memory` | 1 | 20 | 5% | ❌ 未覆盖 |
| `check_output_dir_writable` | 1 | 8 | 11% | ❌ 未覆盖 |
| `check_sqlite` | 1 | 6 | 14% | ❌ 未覆盖 |

**`check_all` 的 6 个存活（最高优先，决定 `unhealthy` 启动闸门）**：
```
L80  const 1->2        | error_count = sum(1 for c in checks.values() if c["status"] == "error")
L82  boolop ->or        | if any(c["status"] == "error" and c.get("critical", False)
L82  const bool->True   | 同上（critical 默认值）
L85  boolop ->and       | elif error_count > 0 or warn_count > 0:
L85  compare Gt->LtE ×2 | 同上
```

**`check_llm_api` 的 18 个存活**：12 个是 `elapsed = round((time.monotonic() - start) * 1000, 1)` 计时样板（L234/243/252/279/293/302，低价值，可用一条 `latency_ms >= 0` 断言批量清掉）；**6 个是真行为**：
```
L268  const 1->2        | "max_tokens": 1,          ← 探针必须最小化，否则多花钱
L276  const 5.0->6.0    | with httpx.Client(timeout=5.0)
L285  const 200->201    | body = resp.text[:200]    ← 错误信息截断长度
L290/L296/L304 const bool->False | "live_probe": True
```

### 3.2 `stats.py`：28 mutants，killed 10，survived 18

```
L82   const 1->2        | cls._api_failures_per_lang[lang] = ….get(lang, 0) + 1
L83   const bool->True  | cls._recent_api_calls.append((…, lang, False))
L84   compare Gt->LtE   | if len(cls._recent_api_calls) > cls._max_recent:
L94   compare Gt->LtE   | if len(cls._recent_completions) > cls._max_recent:
L163  const 300->301    | cutoff = now - 300        ← 吞吐量 5 分钟窗口
L164  const 1->2 / compare GtE->Lt | throughput = recent_5m / 5.0
L165  const 5.0->6.0    | 同上
L168  const 100->101    | recent_calls = cls._recent_api_calls[-100:]  ← 错误率取样窗口
L173  const 1->2        | error_rates[_lang]["total"] += 1
L175  const 1->2        | error_rates[_lang]["failed"] += 1
L184  compare Gt->LtE / const 4->5 | round(failed / total, 4)
L194  const 2->3        | uptime = now - cls._start_time 附近
L200  const 1->2        | round(uptime, 1)
```
未覆盖的行为：环形缓冲上限（`_max_recent` 截断）、吞吐量窗口、错误率取样与计算、`_recent_api_calls` 里 success/failure 布尔位。

---

## 四、第1条剩余工作（下一轮直接从这里接着干）

### 4.1 必须做（变异存活清零）

1. **补 `health.py` 六个未覆盖检查函数的分支测试**（`check_disk_space` / `check_output_dir_writable` / `check_sqlite` / `check_chroma` / `check_memory` / `check_disk_io`，共 77 个存活变异体）。分支清单：
   - `check_disk_space(min_free_mb=500)`：`shutil.disk_usage(path).free // (1024*1024)`；`< min_free_mb` → `error`+`critical:True`；否则 `ok`；异常 → `error`+`critical`。**要测阈值边界（等于 500 不算低）**、`critical` 标志、异常路径。注入点：`monkeypatch` `src.health.shutil.disk_usage`。
   - `check_output_dir_writable()`：`mkdir` → 写 `.health_probe` → `unlink`；异常 → `error`+`critical`。注入点：`src.health.OUTPUT_DIR` 指向不可写路径（如已存在的**文件**路径）。
   - `check_sqlite()`：`sqlite3.connect(CHECKPOINT_DB_PATH)` + `SELECT 1` + `close`；异常 → `error`+`critical`。注入点：`src.health.CHECKPOINT_DB_PATH` 指向不可打开的路径。
   - `check_chroma(exact_store, semantic_store)`：`Path(CHROMA_PERSIST_PATH).mkdir`；`semantic_store is None` → `ok`（"persist path writable"）；`is_healthy()` True → `ok`（"ready with embeddings"）；False → **`warn`**（"embeddings unavailable"）；异常 → **`warn`**（注意不是 error）。注入点：传 `MagicMock`，`is_healthy` 返回值可控。
   - `check_memory()`（**模块级函数，非方法**）：`import psutil`；`pct > 95` → `error`；`elif pct > 80` → `warn`；否则 `ok`；三者都带 `used_gb`/`total_gb`/`percent`；`ImportError` → `warn`（"psutil not installed"）；其他异常 → `warn`。**边界要测 95/96、80/81**。注入点：`monkeypatch` 一个假 `psutil` 模块进 `sys.modules`，`virtual_memory()` 返回带 `used/total/percent` 的对象。注意本机**未装 psutil**（日志有 "psutil not installed"），所以 ImportError 分支是真实路径。
   - `check_disk_io()`（**模块级函数**）：写 `OUTPUT_DIR/.disk_io_probe` → 读回 → `unlink`；`read_back != data` → `error`+`critical`（"Read-back mismatch"）；`elapsed > 5000` → `warn`；否则 `ok`；异常 → `error`+`critical`。注入点：`monkeypatch` `src.health.OUTPUT_DIR`，或 patch `Path.read_bytes` 返回不同数据以触发 mismatch。
2. **补 `check_all` 的 6 个存活**：需要构造「非 critical 的 error」（→ `degraded` 而非 `unhealthy`）、「有 error 但 `critical` 缺失」（`c.get("critical", False)` 默认值）、「只有 warn」（→ `degraded`）、「全 ok」（→ `healthy`），并断言 `summary` 三个计数与 `checks` 逐项一致。已有 `test_critical_error_makes_report_unhealthy` 与 `test_summary_counts_match_checks` 可作模板。
3. **补 `check_llm_api` 的 6 个真行为**：断言 `max_tokens == 1`、`timeout == 5.0`、错误信息被截断到 200 字符、三个分支都带 `live_probe: True`。已有 `_FakeClient`/`_BoomClient` 桩可扩展成记录 kwargs 的形式。计时样板可用一条 `assert result["latency_ms"] >= 0` 批量清掉。
4. **补 `stats.py` 的 18 个存活**：环形缓冲上限（塞 >200 条验证被截断到 `_max_recent`）、`throughput_chapters_per_minute`（5 分钟窗口 + `/5.0`）、错误率（`total`/`failed` 计数 + `round(...,4)` + `failed/total`）、`uptime_seconds`（`round(...,1)`）、`_recent_api_calls` 里 success=True / failure=False 的布尔位。
5. 重跑 `python3.11 scripts/mutation_test.py health stats`，目标 **survived = 0**。⚠️ 铁律：变异期间**不要**并发跑 pytest / 改文件（脚本原地改写源文件）；跑完 `diff` 确认源文件已还原。

### 4.2 待办（第1条范围内，尚未动）

6. **`job_store.update_token_usage` / `get_job_cost` 死代码**：已确认生产端**零调用者**（`routes.py:993` 的 `/jobs/{id}/cost` 已改读 `token_ledger.summary()`），仅 `tests/test_job_store.py:132-168` 在测它们，且断言 DeepSeek 美元价（`0.42`/`0.14`/`0.28`）。上一轮文档 §9.8-2 说"留着是为了不破坏未知第三方读取"——**这个理由不成立**：它是内部 SQLite 方法，不是公开 API。建议删除方法 + 删除对应 4 个测试，或改为委托 `token_ledger`。
7. **`scripts/check_source.py` 断头**：`from src.encoding import detect_and_read`，而 `src/encoding.py` 不存在（已重命名为 `src/text_io.py`）。该文件被 `.gitignore:63` 忽略（**未被 git 跟踪**），是一次性调试草稿，引用的 fixture `tests/fixtures/《地府叫我小先生》 作者：界玉.txt` 确实存在（10MB）。**处置建议：改 import 为 `src.text_io` 即可跑通，或直接删除（需用户确认，因为删未跟踪文件不可恢复）**。
8. **`scripts/` 下其余 DeepSeek 残留**：`probe_deepseek_concurrency.py` 有 `main()` 守卫、导入安全，但已无用途（探针对象是废弃 provider）。`scripts/test_jianke.py:77` 与 `translate_and_verify.py:436` 打印 `/5.0` 满分制——若 `quality_score` 语义已改为"未测量=0.0"，这些输出的解读需在文档中说明（当前无实际错误，因为这些脚本不跳过冷读）。
9. **`run_novel.py` 的 `name_en` 未配置**：5 本书的 `name` 全是中文，我**没有臆造英文书名**，回退到 `book_id` 的 ASCII 化（如 `unit_test_book` → `Unit Test Book`）并打印 NOTE。需要人工给出 5 本书的正式英文书名后填入 `NOVELS[key]["name_en"]`：`limitless_horror`(无限恐怖)、`difu`(地府叫我小先生)、`jianke`(间客)、`quanzhi_fanwai`(全职高手番外之巅峰荣耀)、`yuannicanlanruyang`(愿你灿烂如阳)。
10. **已污染的历史语料未清理**：`novels/output/difu_segmented/difu_en.md` 等真实交付物里**已经存在**中文标题（`# 地府叫我小先生 — English Translation`、`## Chapter 0: Prologue（楔子）`）。代码已修，但**存量文件未回改**。这属于第14条（全量扫描修复）范围，需一并处理。
11. **`output/` 里 404 个测试残留文件**：隔离生效后不再新增，但**存量未清理**（`output/*_glossary.json`、`*_checkpoint.json`、`*_cost.json`、`*_full_novel_es-ES.md` 等，`git ls-files output/` 为 0 → 全部未跟踪）。清理属破坏性操作，**需用户确认**。

### 4.3 本轮我自己犯的错（已修，记录以防重犯）

按铁律"必须验证变异真的生效 / 报告完成必须有实测证据"，如实记录：

1. 往 `dashboard.py` 写了个**根本不存在的** `esc()` 函数（全文件仅我那一处引用）→ 会触发 JS ReferenceError 把整个卡片渲染搞崩。已移除（值来自 `MODEL_MAP`，内部可信非用户输入，不需转义）。
2. 引用了 `config.version` / `config.model_stack`，但 `get_dashboard_data()` 当时**并不提供**这两个字段 → 已在 `dashboard.py:283-295` 补上真实字段并实测取值正确。
3. `test_run_one_segment.py` 断言 `_glossary.json` 必然存在，但那条路径（无可翻译章节）恰恰不产生该文件 → 改为断言打印内容（`Glossary: 1 terms`）。
4. 在 `_run_translation` 里引用了 `_run_sync` 作用域的计数器 → 跨函数 NameError。已在 `routes.py:528-533` 补初始化。
5. 首个 conftest 拦截插件封死了 `socket.socket` 构造 → FastAPI TestClient 走进程内传输但仍构造 socket 对象，产生 59 个**误报失败**。改为只拦 `connect`/`connect_ex`/`create_connection` 且放行回环。
6. 把 `CHROMA_HOST` 当密钥投毒 → chromadb 拿它当主机名去连 `('sk-fake-blocked-by-blocknet', 8000)`，产生 30 次误报。已改为 `os.environ.pop("CHROMA_HOST", None)`。
7. `pyproject.toml` 的 `markers` 数组里用了跨行字符串拼接 → TOML 不支持，`Unclosed array`。已改为单行。
8. Edit 时打错字符 `round((.time.monotonic()` → 已修正。
9. 一次 Edit 的 `old_string` 凭记忆写成 "Runs all health checks"（实际是 "Runs all subsystem health checks"）→ 未匹配，重读后修正。

**教训**：这 9 处里 1/2/4 与链 A/C 同形——凭印象写、没验证。所以"DeepSeek 留的坑"不是全部解释；**没有强制验证闸门时，任何执行者都会犯同类错**。这也是为什么本轮把每条规则都做成机器可检的不变量（conftest 拦截、非零退出码、`readback_skipped` 标记），而不是写进文档。

---

## 五、复现与验证方法（下一轮先跑这三条）

```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"

# 1. 完整测试基线（应为 1154 passed；裸跑恒 ¥0，live 测试已被 addopts 排除）
python3.11 -m pytest -q --no-header

# 2. 验证生产数据零污染（四个文件指纹必须全部不变）
L0=$(wc -l < data/token_ledger.jsonl); D0=$(md5 -q data/translation_events.db)
C0=$(md5 -q data/checkpoints.db); O0=$(find output -type f | wc -l)
python3.11 -m pytest -q --no-header
# 再取 L1/D1/C1/O1 比对

# 3. 变异验证（串行；勿并发 pytest；跑完 diff 确认还原）
cp src/health.py /tmp/h.bak && cp src/stats.py /tmp/s.bak
python3.11 scripts/mutation_test.py health stats > /tmp/mut.log 2>&1
diff -q /tmp/h.bak src/health.py && diff -q /tmp/s.bak src/stats.py
```

⚠️ **必须用 `python3.11`**。本机默认 `python3` 是 **3.9**，`src/text_io.py` 修复前在 3.9 下整条 API 链 import 即崩（现已修）；但 `langchain`/`pytest` 等依赖装在 3.11 与 3.9 两处，混用会出现难解的导入错误。生产 Docker 是 `python:3.11-slim`（`Dockerfile:1`），而 `pyproject.toml` 声明 `target-version = "py39"` —— **这两者本身矛盾，属第4条边界问题，建议统一为 3.11**。

---

## 六、后续条款的调查结论（已查明，可直接开工，不必重新调查）

### 第11条 Agent 定位通用性（问题严重，清单已完整）

**硬绑定（system prompt 写死，无参数可覆盖）**：

| 位置 | 原文 |
|---|---|
| `prompts/write.py:9` | `You write supernatural horror fiction in English` ← **类型+语言双重写死** |
| `prompts/write.py:10-11` | `American horror — Stephen King, Shirley Jackson, Junji Ito, Lovecraft` |
| `prompts/write.py:14` | `adapting a Chinese folk-horror web novel for American readers` ← 三重写死 |
| `prompts/write.py:43` | `American horror readers are smart` |
| `prompts/read.py:2` | `through both Chinese and American eyes simultaneously` |
| `prompts/read.py:14-15` | `you live in America reading English genre fiction` |
| `prompts/read.py:20-21,52-53,89` | `As an American reader` / `Does an American reader need to know` / `What would an American reader see?` |
| `prompts/read.py:67` | `English readers don't need it` |
| `prompts/read.py:98` | `carries the chapter's emotion/horror/tension` ← 恐怖类型预设 |
| `prompts/read.py:107-108` | `"出马弟子", "四梁八柱", "鬼节"` ← 民俗恐怖示例写死 |
| `prompts/readback.py:3` | `reads the English output as a naive American reader` |
| `prompts/readback.py:9` | `You are an American reader who enjoys supernatural horror and dark fantasy` |
| `prompts/readback.py:14` | `"The Underworld Calls Me Little Master"` ← 具体恐怖书名写死 |
| `prompts/readback.py:99` | `Read this chapter as an American reader` |
| `prompts/fix.py:45` | `a one-sentence analogy to something American readers know` |

**参数默认值有偏**：`graph.py:206` `genre: str = "romance_ceo"`、`nodes/write.py:114` 同样 fallback `romance_ceo`（**与 prompt 里的恐怖预设自相矛盾**）、`nodes/write.py:120` `"(Standard American English.)"`、`evaluator_prompts.py:21,64,90` 所有语种 `focus_areas` 均含 `sci-fi/mecha terminology`。

**传递链断点（断头）**：`target_lang`/`genre` 在 `state.py:13-14` 定义、`graph.py:284-285` 注入 state，但——
- READ（`nodes/read.py:137-138`）：user prompt 收了两个参数，**system prompt 全文硬编码 American/English，参数被忽略**
- WRITE（`nodes/write.py:108-123`）：`genre` 传入（L114），**`target_lang` 未传入 format**（`write_user` 模板无 `{target_language}` 占位符）
- READBACK（`nodes/readback.py:63-66`）：format 只有 `previous_context`+`chapter_content`，**两个参数都没传**
- FIX（`nodes/fix.py:47-52`）：**两个参数都没传**

**结论**：`target_lang` 仅在 READ 的 user prompt 里作为一个字段出现，system prompt 的身份设定完全不受其影响；WRITE/READBACK/FIX 三节点 system prompt 纯硬编码，`target_lang` 传到 state 后**彻底断裂**。`genre` 在 READBACK/FIX 完全不感知。

**`cultural_rules.json`（68KB）结构已查明**：顶层 `common`/`genres`/`fidelity`；`common` 段覆盖 `en-US`(L3)、`es-ES`(L73)、`ar-SA`(L143) **三语种均有实质内容**；`genres` 段 5 个类型：`romance_ceo`(223)、`xianxia`(270)、`urban`(522)、`folk_religion`(1126)、`scifi`(1262)，每类型下按语种分 key；**无 horror/thriller/supernatural 条目**（与 prompt 硬编码错位）。

### 第10条 READBACK 盲读独立性（**已基本验证通过，仅一处轻微泄漏**）

**判定：READBACK 是真盲读。** 证据：
- `nodes/readback.py` 仅从 state 读 **两个**字段：`translated_text`(L32)、`cold_read_context`(L58)，在 L63-66 构造 prompt。**未触碰** `chapter_content`(原文)/`read_analysis`/`exact_glossary`/`semantic_terms`/`exact_matches_text`/`semantic_matches_text`/`style_memo`
- `prompts/readback.py` 的 `READBACK_USER`(L92-102) 只有 `{previous_context}` 与 `{chapter_content}` 两个槽位，**无原文占位符**；system prompt L11-12 明写 `You don't speak Chinese. You have no idea this story was originally written in another language.`；全文无 compare/source/original/translation
- state 虽携带原文（`state.py:10` `chapter_content`、L20-21 术语文本、L50 `read_analysis`），但节点代码未访问 → **state 有原文 ≠ prompt 有原文**

**唯一泄漏（待修，🟡极低）**：`graph.py:155` `_build_cold_read_context()` 生成的角色列表含中文名 `f"  - **{en}** ({cn})"`。属元数据级、不构成情节泄漏，但严格盲读应剥离。

**FIX 正确看到原文**（`fix.py:47-52` 注入 `original_cn`+`current_en`+`reader_feedback`+`glossary_text`；system prompt L18-19 `Use the Chinese original for accuracy`）——符合编辑角色。

**其他节点**：REVIEW_TERMS 只看 `read_analysis.terminology_decisions` 的术语对（职责所需）；`fidelity.py`/`completeness.py` 是纯程序化检查非 LLM 评审，`completeness.py` 确实看原文但只出统计指标，且**当前只记录不拦截**（L46-48）。

**遗留（属第1条范畴，本轮已部分修）**：`skip_readback=True` 时走 END，`quality_score` 此前恒为初值 5.0 且无标记 → 本轮已改为 0.0 + `readback_skipped`。

### 第13条 TM/TB 知识库（现状已审计，缺口明确）

**已有基础（比预想好）**：
- `src/glossary/exact_store.py`：SQLite（WAL）+ 内存 dict O(1) 查找；复合主键 `(book_id, term_cn, target_lang)`；字段含 `category`/`context`/`chapter_first_seen`/`note`/`status`/`target_lang`；状态机 `pending_review` → `confirmed`（或 `reject_term` 删除）；API 有 `add`/`add_batch`/`match_in_text`/`confirm_term`/`reject_term`/`get_all_terms(status_filter=)`/`to_formatted_text_with_notes`
- `src/glossary/semantic_store.py`：**真 embedding**（`all-MiniLM-L6-v2` ONNX，非关键词匹配）；Chroma `PersistentClient`（本地）/`HttpClient`（生产，`CHROMA_HOST` 切换）；**每书每语种独立 collection** `terms_{book_id}_{lang}`；API `add_term`/`add_batch`/`search(query_text, top_k=15)`
- `book_id` 隔离**已修好**：`routes.py:225,458` 用 `job_id` 作 `book_id`（不再共用 `"default"`）；`exact_store` 所有 SQL 带 `WHERE book_id = ?`（L83,94,152,164,182,235,315,324,335,346）；`style_memo.py:32` 按 `data/translation_memory/<book_id>/` 隔离；`token_ledger.py:86,92` 记 `book_id`

**四项缺口**：
1. **TM（翻译记忆）完全空白**——无源文→译文句对存储、无模糊匹配复用。`style_memo.py` 虽在 `data/translation_memory/` 目录下，但存的是**风格经验教训**（markdown），不是可复用句对
2. **FIX 自动回写闭环不存在（断头）**——`confirm_term`/`reject_term` 仅被 `api/review.py:37-66`（人工编辑器 UI）调用；`nodes/fix.py` 无任何 glossary 写回；`graph.py` FIX 后只做 fidelity gate（L463-464），不更新术语库
3. **多语种只有 schema 预留**——`target_lang` 在主键里、API 都收该参数，但实际产出（`jianke_segmented/_glossary.json`）仍是 `cn→en` **扁平 dict**，非矩阵
4. **`scan_deliverable.py` 不存在**——`scripts/` 下无该文件（上一轮文档 §八-3 提到的思路未落地）

**质量门禁现状**：`completeness.py` ✅ 被 `write.py:294` 调用；`output_guard.py` ✅ 被 `write.py:204` 调用；`agent/fidelity.py` ✅ 被 `graph.py:409-421` 调用；**`scan_deliverable.py` ❌ 不存在**。

### 第12条 推理档位（现状已查明）

| 节点 | 模型 | 档位参数 | 值 | 位置 | 有注释说明依据？ |
|---|---|---|---|---|---|
| READ | `qwen3.8-max` | `thinking_budget` | 2048 | `nodes/read.py:108` | ✅ L105-107（说明与 `reasoning_effort` 互斥） |
| WRITE | `glm-5.3-flash` | `reasoning_effort` | `"low"` | `nodes/write.py:63` | ✅ `config.py:35` |
| WRITE(fallback) | `qwen3.8-max` | `thinking_budget` | 2048 | `nodes/write.py:490` | ✅ L481-482 |
| READBACK | `qwen3.8-max` | `thinking_budget` | 2048 | `nodes/readback.py:52` | ❌ **无注释** |
| REVIEW_TERMS | `qwen3.8-max` | `thinking_budget` | 2048 | `nodes/review_terms.py:114` | ❌ **无注释** |
| FIX | `glm-5.3` | `reasoning_effort` | `"high"` | `nodes/fix.py:35` | ✅ `config.py:36` |

**无 `reasoning_effort` 与 `thinking_budget` 同设冲突**（GLM 用前者、Qwen 用后者，分工清晰）。

**最大杠杆（上一轮已查明，本轮未动）**：`MODEL_MAP`（`config.py:92-98`）把 READ/READBACK/REVIEW_TERMS **三节点挂在同一常量 `QWEN_MODEL` 上**，一动全变 max。READBACK 只是"读得懂吗"的判断，**根本不需要 max 档**。同 token 换档位账单差 **10-17 倍**（`COST_MODEL_20260922.md` §9.2：现状 ¥1,974 → 全 flash ¥198 → 最便宜 ¥117）。

**单价权威来源**：`src/config.py:106-114` `MODEL_PRICES_CNY_PER_M`（7 个模型，抓取于 2026-09-22，已交叉验证）。**注意 §12 提到的"档位数量口径不一致"（3档/6档/5档）本轮未验证**——需按指令以实际 API 行为为准，且真实探测须按第6条攒批告知。

### 第14条 全量扫描（语料位置与规模已查明）

`novels/output/` 共 **29MB / 24 个条目**。分书目录：`difu_segmented`（地府叫我小先生，2301 章）、`limitless_horror_segmented`（无限恐怖，775 章）、`jianke_segmented`、`quanzhi_fanwai_segmented`、`yuannicanlanruyang_segmented`；每个含 `_checkpoint.json`/`_glossary.json`/`_quality.json` + 交付 md。另有 `audit_15/`、`jianke_test/`、`limitless_horror{,_200,_fast}/`。

**可复用基础**：`src/completeness.py` 三判据（长度比 / 对话 span 比 / 术语覆盖）**零成本、无需 API**，已标定（《地府》11 个已知截断章 vs 8 个正常章）：长度比缺陷 0.23~1.84 / 正常 3.70~8.13；对话 span 缺陷 **16~26 → 0** / 正常保留 70~90%；**`glossary_missing` 不能作判据**（缺陷章缺 8~24 个，正常章也缺 5~16 个，**区间重叠**，因 WRITE 被授权压缩次要提及）——合成判定 **11/11 命中缺陷，0/8 误报**。当前**只记录不拦截**。`src/output_guard.py` 正则检测残留中文/过短/LLM 废话。

**已知存量污染**：交付物里有中文标题（见 §4.2-10）。

### 第6条 API 管控（本轮已落地大半）

- ✅ conftest 双层拦截 + `live_api` marker + `addopts` 排除 → **`pytest` 裸跑恒 ¥0，且这是机器可检的不变量而非文档声称**
- ✅ `diag_api_hang.py` 加成本闸门（高价档退出码 2，与 `scripts/cost/probe_nodes.py:19-38` 的 `_guard_tier()` 一致）
- ✅ `auto_triage.py` 默认 dry-run
- ⏳ 待办：把 `probe_nodes*.py` 的闸门模式推广到 `scripts/` 下其余会真实调用的脚本（`run_novel.py`、`batch_test.py`、`adversarial_test.py`、`ab_test.py`、`test_difu.py`、`test_jianke.py`、`test_multilang.py`、`translate_and_verify.py`、`e2e_verify.py`、`verify_fidelity.py`、`audit_e2e.py`、`audit_translate.py`、`run_full_test.py`、`backfill_chapters.py`、`probe_glm_filter.py`、`seed_from_analyze.py`、`evaluate_native.py`、`compile_script_sample.py`、`run_script_pilot.py`）——**这些脚本目前都是"跑就花钱"，无闸门、无预告**
- ⏳ 历史花费记账（上一轮 §9.5）：Qwen 侧 **¥5.05**（8 章逐节点 hook，115,841 in + 101,652 out），**把百炼余额用光**，之后 Qwen 调用返回 `HTTP 400 Arrearage`；GLM 侧同日另用 191,931 tokens（另一账号）

### 第4条 边界（残留已查明）

`869ce2e` 声称 "drop script/game branch + dead code"，但仓库仍残留：`scripts/compile_script_sample.py`、`scripts/run_script_pilot.py`（两者都调 `translate_chapter`）；`src/evaluator_prompts.py`；`novels/output/quanzhi_fanwai_segmented/`。`.gitignore:76` 有 "Pilot data (finished scripts from 铸文 + compilation output — not code)" 注释。另 **`Dockerfile:1` 是 `python:3.11-slim` 而 `pyproject.toml` 声明 `py39`**，运行时口径矛盾（见 §五）。

### 第9条 主笔 agent（相关机制已查明）

`src/style_memo.py` 存**风格经验教训**（markdown，6 抽屉，按 `data/translation_memory/<book_id>/` 隔离），由 `config.py:65` `STYLE_MEMO_ENHANCED`（默认 true）控制；`graph.py:309-317` 在初始 state 里注入 `self.style_memo.read_relevant(content, exact_matches)`，无记忆时给一段"stay close to the source"的引导。`READBACK` 只看单章不看全书；FIX→READBACK 循环上限 `_MAX_FIX_ATTEMPTS`（= `MAX_RETRANSLATION_ATTEMPTS` = 2，`config.py:74`）。**需核实：style_memo 是否真被 WRITE/FIX 消费、是否有否决权语义**——初步看它是"经验注入"而非"纵向权威角色"，**倾向于判定"缺主笔 agent"**。

### 第8条 Qwen-MT（本轮未开始）

需先联网查 Qwen-MT / Plus / Flash / Lite / Turbo 的官方文档（差异、术语干预 `terms` 字段、领域提示、翻译记忆、上下文限制、定价 ¥1.8/¥5.4 每百万 tokens、限流、档位定位），再评估嵌入位置。**外部建议的 REVIEW_TERMS 位置存疑**：Qwen-MT 是翻译执行模型，REVIEW_TERMS 是评审决策环节，任务性质不同。候选：WRITE（初稿引擎，传 REVIEW_TERMS 确认的术语表做术语干预）、FIX（术语回滚+全局一致性，用翻译记忆确保新译法全书同步）。**变更方案须经确认后再实施，不要直接改。**

---

## 七、给下一位的提醒

1. **`python3.11`，不是 `python3`**（本机默认 3.9）。
2. 变异测试**必须串行**，跑完 `diff` 确认源文件还原；`scripts/mutation_test.py` 有 SIGTERM 处理器与"发现残留 `*.mutation_backup` 就拒绝启动"的保护。
3. **不要相信任何"已完成"字样**，包括本文档。本轮已推翻上一轮文档的 §9.6（"测试零 API 调用"）。每处修复都要能追溯到实测证据。
4. 改 `scripts/` 下的生产脚本前先跑 `tests/test_run_novel.py` / `test_run_one_segment.py`——它们是本轮新加的，锁死了"进度真实化 + 零中文 + 不崩溃"三条性质。
5. **真实 API 调用一律先报预算等批准**（第6条）。当前 `scripts/` 下 20 个脚本仍是"跑就花钱"无闸门。

---

**本文件由一次审计+修复会话产生，第1条未完成。工作区改动未提交。**
