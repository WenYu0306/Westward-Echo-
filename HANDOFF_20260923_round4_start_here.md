# 西渡 · 交接（2026-09-23 第4轮 · **新窗口从这里开始**）

> **本文件是最新入口。** 前序文档按需查：
> - `HANDOFF_20260923_round3.md` — 第3轮增量（job_store 死代码、check_source 断头、generate_report 判据重写）
> - `HANDOFF_20260923_audit_fix_round2.md` — health/stats 变异清零 + 闸门加固
> - `HANDOFF_20260923_audit_fix_wip.md` — **19 处缺陷清单 + 后续条款调查结论（§六，最有价值，别重查）**
> - `HANDOFF_20260921_audit_untrusted.md` — 上一轮的不可信声明（部分已被本轮推翻）

## 〇、基线（实测，非声称）

```bash
python3.11 -m pytest -q --no-header     # → 1255 passed, 4 deselected
```
- **必须用 `python3.11`**，本机 `python3` 是 3.9
- 生产数据四指纹零污染（ledger 8813 行 / translation_events.db / checkpoints.db / output 817 文件）
- 工作区改动**未提交**（25+ M，6 ??）
- `pytest` 裸跑**恒 ¥0**，这是 conftest 强制的机器可检不变量，不是文档声称

## 一、立刻可执行的第一步（第1条最后一项）

**任务：全量重跑变异测试。已量化，28 分钟，不是 2-4 小时。**

本轮离线实测（`mutants_for` 是纯计算不落盘，所以不用跑测试就能算）得出三个结论：

| 问题 | 实测答案 |
|---|---|
| 是不是每个变异体一个进程？ | **是**。`run_tests` 用 `subprocess.run([sys.executable,"-m","pytest",...])`，`scripts/mutation_test.py:199-203` |
| 657 这个数还对吗？ | **不对，实际 985**。代码增长了，且 health(134)+stats(28) 是新增 target |
| 真的全部要重跑吗？ | **是，99%**。比对新旧偏移公式：985 个里只有 **12 个**新旧算出同一位置，**973 个位置不同** → 旧数字全部作废 |
| 要多久？ | **28 分钟**。已按 target 分组实测每套件单次耗时：多数 2.0s（langchain import 下限），最慢 `graph_post_process` 71×5.0s=5.9m、`measurements` 113×2.0s=3.8m |

已清零、**不必重跑**的 6 个 target：`fidelity`(74)、`completeness`(35)、`write_guards`(60)、`token_ledger`(44)、`health`(134)、`stats`(28)。**剩 610 个变异体 / 21 个 target。**

**决定：放弃 round2 提议的 conftest 惰性化改造。** 那个 `_forbid_live_llm` 是"裸跑 pytest 恒 ¥0"的承重墙（第6条），改它要证明"测试函数中途才 import 节点也能事前拦住"——用改造风险换 28→10 分钟不划算。

```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
# ⚠️ 变异期间禁止并发跑 pytest、禁止编辑任何文件（脚本原地改写源文件）
# 先备份，输出重定向到文件，跑完必须校验还原
for f in $(python3.11 -c "
import importlib.util;spec=importlib.util.spec_from_file_location('m','scripts/mutation_test.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
print(' '.join(v[0] for v in m.TARGETS.values()))"); do cp "$f" "/tmp/$(echo $f|tr / _).bak"; done
python3.11 scripts/mutation_test.py > /tmp/mut_full.log 2>&1; echo "退出码=$?"
tail -20 /tmp/mut_full.log
find . -name "*.mutation_backup" -not -path "./.git/*" | wc -l   # 必须为 0
```

**预期不是"全清零"**，更可能挖出一批真实盲区（round2 在 health 上就挖出 103 个存活）。挖到存活要**手工施加变异体复现**再补测试——这是项目铁律，round2 靠这条发现了工具自身的字节偏移 bug。

## 二、14 条计划（权威状态见 TodoList，此处给下一步）

用户指定执行顺序：**1 → 5 → 3 → 11 → 12 → 13 → 14 → 2 → 10 → 8 → 9 → 7 → 6 → 4**

| # | 条款 | 状态 | 下一步动作 |
|---|---|---|---|
| 1 | 工程审计（假绿/假变异/断头/死代码） | 🟡 只剩变异重跑 | 跑上面那 28 分钟 → 修存活盲区 |
| 5 | 交接文档逐条验证 + 烂尾修复 | ⬜ 未开始 | 见下方 §三 |
| 3 | 模型变更质量回归 | 🟡 成本已重算完 | 只剩质量回归，需真实 API → **先报预算** |
| 11 | Agent 定位通用性（剥离恐怖/美国绑定） | ⬜ 清单已查明 | 见 wip §六第11条，15 处硬绑定逐条改参数化 |
| 12 | 思考链档位成本优化 | ⬜ 现状已查明 | 最大杠杆：`MODEL_MAP` 三节点共用 `QWEN_MODEL` |
| 13 | TM/TB 知识资产 + 命名空间隔离 | ⬜ 缺口已查明 | TB 扎实、**TM 完全空白**、FIX 回写闭环不存在 |
| 14 | 全量章节扫描与坏章修复 | ⬜ 已有线索 | **地府第 1472 章 JSON 残留**是已知硬伤 |
| 2 | 端到端质量验证 + 西语启动 | ⬜ 未开始 | 前置依赖第11条（否则西语仍跑"美国读者"视角） |
| 10 | READBACK 盲读独立性 | 🟢 基本通过 | 只剩 `graph.py:155` 角色中文名泄漏 |
| 8 | Qwen-MT 嵌入位置评估 | ⬜ 未开始 | 先联网查官方文档，**方案须确认后再实施** |
| 9 | 主笔 agent 评估 | ⬜ 倾向"缺" | 核实 style_memo 是否真被 WRITE/FIX 消费 |
| 7 | 不降质降成本 | ⬜ 与12强耦合 | 基线已有（¥0.5/千英文词） |
| 6 | API 调用管控 | 🟡 大半落地 | 剩 `scripts/` 下 ~20 个"跑就花钱"无闸门脚本 |
| 4 | 项目边界（剧本/游戏线） | ⬜ 未开始 | 剧本残留 2 个脚本 + Dockerfile/pyproject 版本矛盾 |

## 三、第5条怎么做（下一个大条目）

逐条验证四份 handoff 的"已完成"声称，未通过的判为烂尾**并当场修复**（用户第0条：标记不是交付）。

**方法**：每个声称 → 找代码实现 → 找测试覆盖 → 实跑验证。三者缺一即烂尾。

**本轮已推翻的 3 条**（可直接作为示范，也说明"不要相信任何已完成字样，包括我们自己写的"）：
1. `audit_untrusted §9.6`「测试套件零 API 调用，跑一次 ¥0」→ **错**。封网插件实测拦到 11 次 `('dashscope.aliyuncs.com', 443)`，来自 `health.py:213`
2. `wip §4.1-3`「计时样板可用 `assert latency_ms >= 0` 批量清掉」→ **错，一个都杀不死**（`1000→1001` 结果仍 ≥0）
3. `wip` 引用的「83.1% 术语一致率」→ **错**，那次采样自身带着 `[maybe:]`/`[variant:]` 缺陷

**待验证的高风险声称**（都是文档说完成、我尚未逐条查的）：
- `audit_untrusted §四` 列的 6 项"已验证修复"（截断检测、内容过滤兜底、场景级完整性、curation gate）
- `round2 §2.1` 的 `EQUIVALENT_MUTANTS` 登记表里那条"数学等价"证明
- `HANDOFF_20260921.md` 的 657 变异体数字（**已确认过时，实际 985**）

## 四、待用户拍板（不要自行执行）

1. **5 本书的 `name_en`**：代码现在回退到 `book_id` 的 ASCII 化并打印 NOTE，**未臆造英文书名**。需填 `scripts/run_novel.py` 的 `NOVELS[key]["name_en"]`：无限恐怖 / 地府叫我小先生 / 间客 / 全职高手番外之巅峰荣耀 / 愿你灿烂如阳
2. **回改已污染历史语料**（破坏性）：`novels/output/difu_segmented/difu_en.md` 首行 `# 地府叫我小先生 — English Translation`、`## Chapter 0: Prologue（楔子）`。代码已修，存量未回改
3. **清理 `output/` 约 404 个测试残留**（破坏性，全部未被 git 跟踪，删了不可恢复）

## 五、铁律（违反过，写下来防重犯）

1. **任何"完成"字样都要能追溯到实测证据**——本系列已推翻自己 3 次
2. **变异测试必须串行**，跑完 `diff` 校验源文件还原 + `find *.mutation_backup` 为 0
3. **变异工具改动后必须验证"变异真的生效"**（round2 靠这条发现字节偏移 bug）
4. **工具的 SKIP/STALE 必须计入失败**（陈旧条目 = 没验证但看起来绿）
5. **真实 API 调用一律先报预算等批准**（第6条）
6. **破坏性操作先请用户确认**
7. 别凭记忆引用字段/函数名——本轮我 3 次写了不存在的东西（`esc()`、`config.version`、跨函数计数器）
