# 西渡 · 交接（2026-09-24 第8轮 · 条款12 档位路由接通 · **新窗口从这里开始**）

> **本文件是最新入口。** 上一轮 `HANDOFF_20260924_round7_lang_registry.md` 的全部改动**已提交**（`06d8fd0` 代码数据 + `a8f4da9` 文档更正），不要重做。
> 本轮做完 **P0 提交** + **条款12（思考链档位成本）**，全部已提交（`a148597`），工作区干净。

---

## 〇、基线（实测，非声称）

```bash
python3.11 -m pytest -q --no-header              # → 1385 passed, 4 deselected
python3.11 scripts/static_audit.py --self-test   # → 27/27
python3.11 scripts/mutation_model.py             # → 38/38 killed, 0 survived, 0 stale
```
- **必须用 `python3.11`**，本机 `python3` 是 3.9
- 1385 = 上轮基线 1370 + 本轮新增 **15 个测试**：
  | 文件 | 新增 | 内容 |
  |---|---|---|
  | `tests/test_dual_model.py` | 10 | 路由表真驱动节点 + 跨 provider 降档 + BYOK 优先级 + 非法输入拒绝 |
  | `tests/test_write_guards.py` | 5 | 兜底必须换 provider（3）+ readback/fix 账本归属（2） |
- `.mutation_backup` 残留 **0**；全仓无对节点模块级 provider 常量的残留引用（grep 实测）
- `cultural_rules.json` **未触及**（本轮纯代码层）
- 提交历史：`a148597`（条款12）← `a8f4da9`（round7 文档更正）← `06d8fd0`（round7 代码数据）← `b4cfe44` ← `445ec8b`
- `pytest` 裸跑**恒 ¥0**（conftest 封网）

---

## 一、P0 已清除：round7 改动已提交

接手第一件事是提交 round7 全部未提交改动——那是上轮 `git checkout` 抹掉 60KB 数据的直接成因。

**提交前逐键复核（不采信文档声称）**：

| 复核项 | 方法 | 结果 |
|---|---|---|
| 测试基线 | `pytest -q` | ✅ 1370 passed |
| 静态审计 | `static_audit.py --self-test` | ✅ 27/27 |
| 既有数据零改动 | HEAD vs 工作区**逐 leaf 路径比对** | ✅ `changed = 0`（572 → 1020 leaf） |
| removed 归属 | 同上 | ✅ 300 条全部是 `genres.urban` 旧位置 |
| added 归属 | 同上 | ✅ 748 leaf = urban 迁入 300 + es/de/fr 新增，与条数吻合 |
| 四语种矩阵 | 脚本统计 | ✅ common 17 / romance_ceo 3 / xianxia 20 / folk_religion 33 / scifi 32 |
| 待审校标记 | grep | ✅ 148 条，与 `EXPECTED_TAGGED` 一致 |
| urban 隔离行为 | 实跑 `load_rules` | ✅ 无限恐怖 167、另两本 17、`生化危机` 已隔离 |
| 格式特征 | 缩进/尾换行/fidelity 内联 | ✅ 与 HEAD 一致，未被 `json.dumps` 展开 |

**⚠️ 排除一个误报（方法论，值得记住）**：`git diff -U0` 显示 241 删除行，其中含西语 xianxia 条目（`丹田`/`师兄`/`师叔`），看似数据被删。实测：① HEAD 与工作区的 `genres.xianxia.es-ES` 块**逐字节相同（3215 字节）**；② "只删未补"的仅 **1 行**，就是 `"urban": {` 块头（块被清空为 `{}`）。
→ **判数据完整性必须用结构化逐键比对，不能用文本 diff 行数。** 已写入 round7 文档铁律 13。

**同时更正 round7 文档 4 处错误数字**（`a8f4da9`）：
| 文档原记 | 实测 |
|---|---|
| `58231 → 118151` 字节 | **`59817 → 123156`** |
| round-trip `59047 vs 58231` | **`60633 vs 59817`**（HEAD） |
| 首个差异字节 `53032` | **`54470`** |
| "上千行不可审 diff" | **多 68 行**（2130 vs 2198） |

数字错但结论对：`json.dumps` round-trip 确实不匹配（`fidelity.*.examples` 是内联单行对象）。已写入 round7 文档铁律 14（**文档数字要实测复核**）。

**另更正一条会误导人的方法约束**：round7 写"校验闸门要 `git diff --numstat` 显示 0 删除行"——**这只适用于纯新增注入，不适用于数据搬移**（urban→books 迁移必然产生删除行，实测 `1164 241`）。已在 round7 文档中区分场景。

---

## 二、条款12 的决定性发现：`MODEL_MAP` 是死配置

文档（round5 §四、round6 §三、`COST_MODEL_20260922.md` §0.5）都说"最大杠杆是 `MODEL_MAP` 三节点共用 `QWEN_MODEL`，READBACK 不需 max 档"。

**实测更正：改 `MODEL_MAP` 一分钱都省不下来。**

| 证据 | 结果 |
|---|---|
| `grep -rn "MODEL_MAP" src/` | 只有 `dashboard.py`（展示）+ `health.py`（docstring）读取 |
| `grep -n "model=" src/agent/nodes/*.py` | **五个节点全部直接 import `QWEN_MODEL`/`GLM_MODEL`/`GLM_FULL_MODEL`** |
| `scripts/adversarial_test.py:314,483` | 唯一真用它选模的地方（离线脚本，非生产链路） |

也就是说：**档位配置声明与真实行为各走各的**——与 round7 修的"语种清单复制 12 份、参数化了但没有开放"是**同一类缺陷**。`COST_MODEL_20260922.md` §0.5 算出的 10–17 倍杠杆（¥1,974 → ¥198）因此一直是**断开的杠杆**。

### 顺带查明的第二个失真：token 账本被测试数据污染

`data/token_ledger.jsonl` **8813 行里真实生产数据只有 2 行**。

| 特征 | 数量 | 说明 |
|---|---|---|
| `metered=False` | 7911（89.8%） | token 全 0 |
| token 恒 `(100, 50)` | 84 | 夹具假数据 |
| token 恒 `(5, 6)` | 721 | 夹具假数据 |
| `book_id` 为空或以 `test_` 开头 | 8811 | 测试残留 |
| **真实生产行** | **2** | — |

成因：`tests/conftest.py:36` 后来才加 `XIDU_TOKEN_LEDGER` 重定向到临时目录，此前 pytest 会往生产账本追加真实行（conftest 注释自己就记了这件事）。
→ **round5 §〇 把"ledger 8813 行"当作生产数据四指纹之一，这个数字是失真的**，不能用来证明"生产数据零污染"（作为 mtime 指纹仍有效）。

**未处理**（属破坏性操作，需用户拍板）：清理 `data/token_ledger.jsonl` 的 8811 行测试残留。注意 round5 把它列为生产指纹，清理会改变该指纹值，需同步更新相关文档/测试。

---

## 三、本轮做了什么（`a148597`）

### A. config.py 新增路由层（默认行为逐项等价，实测核对）

```python
NODE_MODELS = {           # 每节点一档，可被 XIDU_NODE_MODEL_<NODE> 覆盖
    "read": QWEN_MODEL, "write": GLM_MODEL, "write_fallback": QWEN_MODEL,
    "readback": QWEN_MODEL, "fix": GLM_FULL_MODEL, "review_terms": QWEN_MODEL,
}
NODE_EFFORTS = {...}      # GLM 思考档位，可被 XIDU_NODE_EFFORT_<NODE> 覆盖
node_provider(model)      # provider 由模型名派生；未知 → ValueError，不静默挑 endpoint
node_model(node)          # 未知节点 → ValueError，不给静默默认
node_llm_kwargs(node, *, api_key_override="")
                          # → model/base_url/api_key + 该 provider 的思考控制形态
MODEL_MAP = dict(NODE_MODELS)   # 别名保留，dashboard/health/脚本零改动
```

- **`write_fallback` 单列一档**：1301 内容过滤兜底此前和主路径共用 `QWEN_MODEL` 常量，无法独立降档。
- **provider 由模型名派生，不由节点写死**：所以跨 provider 降档（`READBACK → glm-5.3-flash`）也能正确切 `base_url` 与思考控制形态。
- **Qwen 用 `extra_body.thinking_budget`、GLM 用 `reasoning_effort`，两者不能同设**——这个选择收敛到 `node_llm_kwargs` 一处，不再散在五个节点里（原先 write.py 的兜底函数就重复了一遍）。

**实测等价性**（改造前后逐项一致）：
```
read           -> qwen3.8-max   | dashscope.aliyuncs.com | thinking_budget 2048
write          -> glm-5.3-flash | open.bigmodel.cn       | effort low
write_fallback -> qwen3.8-max   | dashscope.aliyuncs.com | thinking_budget 2048
readback       -> qwen3.8-max   | dashscope.aliyuncs.com | thinking_budget 2048
fix            -> glm-5.3       | open.bigmodel.cn       | effort high
review_terms   -> qwen3.8-max   | dashscope.aliyuncs.com | thinking_budget 2048
```

### B. 五节点接线

- `read/write/readback/fix/review_terms` 全部改经 `node_llm_kwargs`，**节点内直接建模常量清零**（grep 实测，只剩注释提及）。
- `_build_qwen_writer` → `_build_fallback_writer(**provider_kwargs)`，不再自行选 provider；两个引用它的测试已同步改名。
- **`review_terms` 的密钥守卫改为检查路由解析出的实际密钥**：原先硬编码 `if not LLM_API_KEY`，该节点一旦降到 GLM 就会**静默跳过整个术语评审**（fail-safe 变成 fail-silent）。
- 节点自有的生成参数（temperature / max_tokens / request_timeout / model_kwargs）留在节点内——它们是 per-task 不是 per-provider。

### C. 连带修复的真实缺陷：账本记错模型

`read`/`readback`/`fix` 的 `_capture_*_tokens` **硬编码** `QWEN_MODEL`/`GLM_FULL_MODEL` 记账。

**后果**：一旦降档，会把 flash 档的 token 记到 max 档单价上——**正好掩盖降档省下的钱**，让 §0.5 的收益无法被度量。改为接收实际服务该章的模型名（`write.py` 早前已用 `used_model` 修过同类缺陷，这次是补齐另三个节点）。

`write` 空输出重试路径记 `provider_kwargs["model"]` 而非 `used_model`：重试用的是**主客户端**，而 `used_model` 可能已被 1301 兜底改写。

**未动**：`tier="flash"/"pro"` 与 `use_flash_writer` 标记——round 审计已记录 `stats.py` 的 `record_tokens(tier=)` "参数保留但忽略"，属另一条线。

### D. 变异表重写（原表 13 条 STALE）

旧 `MUTATIONS` 锁定 `model=QWEN_MODEL`、`extra_body={...}`、`reasoning_effort=GLM_WRITE_EFFORT` 等**已不存在的写法** → 首跑 13 条 STALE（铁律5：计入失败）。

- 改为变异 `node_llm_kwargs("read"→"write")` 这类**节点名错路由**，以及路由层各分支（provider 派生、effort 查表、BYOK 优先级、两个 endpoint、非法输入兜底）。
- **`run_tests()` 扩展为同时跑 `test_write_guards.py`**：账本归属与兜底路径的断言在那个文件，只跑 `test_dual_model.py` 会让这些变异体存活。

### E. 一个真实业务不变量此前无人守护

变异 `write_fallback → write 档` **首次运行 SURVIVED**。这不是变异太弱，是**测试确实抓不住**：`_build_fallback_writer` 在测试里被整体 mock，config 层测试又看不见 1301 路径向哪个节点要参数。

而这个变异的业务后果是实的：**1301 内容过滤对同一文本是确定性的**（`write.py` docstring 自己写明），兜底若路由回同一 provider，必然再次被拒——章节直接丢失。

补 `TestFallbackMustLeaveTheRefusingProvider`（3 个测试）锁定"**兜底必须换 provider**"，**不限制具体档位**（`qwen3.8-flash` 作为更便宜的兜底是合法的）。重跑：**38/38 killed**。

> 方法论印证 round7 铁律：**变异体存活先怀疑变异太弱，再怀疑断言没牙**——但这次是第三种情况：**被测单元被 mock 掉了，断言根本没覆盖到那条路径**。

---

## 四、验证汇总

| 项 | 结果 |
|---|---|
| 全量测试 | ✅ **1385 passed**, 4 deselected（+15） |
| `mutation_model.py` | ✅ **38/38 killed, 0 survived, 0 stale** |
| `.mutation_backup` 残留 | ✅ 0 |
| `static_audit.py --self-test` | ✅ 27/27 |
| 节点内直接建模常量 | ✅ 清零（grep 实测） |
| 死变量 | ✅ 清 1 处（`write.py` 的 `api_key`，改造后不再被读取） |
| `dashboard` 的 `model_stack` | ✅ 不变（`glm-5.3 + glm-5.3-flash + qwen3.8-max`） |
| `cultural_rules.json` | ✅ 未触及 |

**`static_audit --include-src` 的既存发现（非本轮引入）**：`scripts/run_script_pilot.py:174` 的 `glossary` 可能未绑定——该文件我未修改，属**条款4**（剧本/游戏线残留，2 个未被引用的剧本脚本之一）。

---

## 五、下一位接着做

**工作区干净，可直接接着做下面任一项。**

### 条款12 的剩余部分（需 API 预算）
杠杆已接通，但**还没真的降档**——因为"READBACK 降到 flash 会不会降低质量"必须实测，按铁律6 先报预算。现在降档只需：
```bash
XIDU_NODE_MODEL_READBACK=qwen3.8-flash   # 12/36 → 0.8/2.7，该节点约 15 倍
```
`COST_MODEL_20260922.md` §0.5 的五档配置现在**都可直接用环境变量组合表达**，无需改代码。建议先跑 A/B 对比 READBACK 判定的 PASS/NEEDS_FIX 一致率。

### 15 条计划状态（权威见 §六，本轮完成条款12）
下一个**不需预算**的是 **条款13（TM/TB 知识资产）**：TM 句对存储完全空白、FIX 自动回写闭环不存在、`scan_deliverable.py` 不存在。
条款 3 / 2 / 8 与条款11 的收尾都卡在真实 API 预算上。

### 质量保留项（承接 round7 §五，仍有效）
1. **de/fr 的 148 条 `FIRST-ESTABLISHED` 待母语审校**——审校后移除标记并同步 `test_cultural_rules_multilang.py::EXPECTED_TAGGED`（已固定为 148）。
2. `fidelity` 仍只有 en-US（8 条类别级规则）；`books.limitless_horror` 只有 en-US（150 条，含 9+ 西方 IP 名在西语区分西班牙/拉美两套）。
3. `_KNOWN_GENRES` 死配置（`routes.py:52`，全仓零读取）——删除属破坏性操作需确认。
4. 52 处 `= "en-US"` 签名默认值未收敛。
5. round6 遗留：`evaluator_prompts.py` 未参数化、`personas.py` 未进 `mutation_test.py` 的 TARGETS、新 genre 文案未经真实 API 验证。
6. **新增**：`data/token_ledger.jsonl` 的 8811 行测试残留（见 §二）——破坏性操作，需拍板。

### 本轮暴露的新缺陷模式（建议写进审计清单）
**"配置表声明了分工，但生产代码不读它"** ——已出现两次：语种清单（12 处副本，round7 修）、`MODEL_MAP`（死配置，本轮修）。
排查手法：`grep -rn "<配置名>" src/` 看是否只有展示层/注释在读，再 `grep -n "model=\|<硬编码常量>" src/agent/nodes/*.py` 看真实取值从哪来。
候选同类嫌疑：`_KNOWN_GENRES`（已确认死配置）、`stats.py` 的 `tier` 参数（已确认"保留但忽略"）。

---

## 六、15 条计划状态（本轮更新条款12）

> 用户指定顺序：**1 → 5 → 3 → 11 → 12 → 13 → 14 → 2 → 10 → 8 → 9 → 7 → 6 → 4**（第 15 条附末尾）。
> **完成 4 条**（1/5/11/12），基本通过 1 条（10），部分完成 4 条（3/14/8/6），未开始 6 条（13/2/9/7/4/15）。

| # | 条款 | 状态 | 实测依据 / 下一步 |
|---|---|---|---|
| 1 | 工程审计 | ✅ 完成 | `67975f3`，957/957 变异全杀 |
| 5 | 交接验证+烂尾修复 | ✅ 完成 | `fc5c452` |
| 11 | Agent 定位通用性 | ✅ 完成 | `445ec8b`。剩 API 质量验证、`evaluator_prompts` 参数化、`personas` 进变异 TARGETS |
| **12** | **思考链档位成本** | ✅ **完成（`a148597`）** | **杠杆已接通**：`MODEL_MAP` 从死配置变单一来源，降档=改环境变量。**剩**：实际降档需 A/B 验证质量（API 预算） |
| 10 | READBACK 盲读独立性 | 🟢 基本通过 | 仅剩 `graph.py:155` 角色中文名泄漏 |
| 3 | 模型变更质量回归 | 🟡 部分 | 成本已重算；质量回归需 API → 先报预算 |
| 14 | 全量扫描+坏章修复 | 🟡 部分 | 坏章范围已查明（round5 §四-A）；**修复未做** |
| 8 | Qwen-MT 嵌入评估 | 🟡 部分 | 官方文档已查完；方案待拍板，实测需预算 |
| 6 | API 调用管控 | 🟡 部分 | 剩 20 个裸露脚本无闸门（round5 §四-C） |
| 13 | TM/TB 知识资产 | ⬜ 未开始 | **下一个不需预算的大条目**：TM 空白、FIX 回写闭环不存在、`scan_deliverable.py` 不存在 |
| 2 | 端到端质量+西语启动 | ⬜ 未开始 | 前置依赖 11（已满足）；需 API 预算 |
| 9 | 主笔 agent 评估 | ⬜ 未开始 | 核实 style_memo 是否真被 WRITE/FIX 消费 |
| 7 | 不降质降成本 | ⬜ 未开始 | **本轮条款12 已为其铺路**（降档开关就位）；基线 ¥0.5/千英文词 |
| 4 | 项目边界（剧本/游戏线） | ⬜ 未开始 | 剧本残留 2 脚本 + Dockerfile/pyproject 版本矛盾 + `run_script_pilot.py:174` 未绑定变量（本轮 static_audit 实测） |
| 15 | circuit_breaker 死配置 | ⬜ 待拍板 | round5 §五，三选一 |

---

## 七、本轮改动文件清单（10 文件，已提交 `a148597`）

**源码（6 M）**：
- `src/config.py` — 新增 `NODE_MODELS`/`NODE_EFFORTS`/`_env_overridden`/`node_provider`/`node_model`/`node_llm_kwargs`；`MODEL_MAP` 改为别名；新增 `write_fallback` 档
- `src/agent/nodes/read.py` — 接线；`_capture_read_tokens` 加 `model` 参数
- `src/agent/nodes/write.py` — 接线（主路径 + 1301 兜底）；`_build_qwen_writer` → `_build_fallback_writer`；重试路径账本记主客户端模型；删死变量 `api_key`
- `src/agent/nodes/readback.py` — 接线；`_capture_readback_tokens` 加 `model` 参数
- `src/agent/nodes/fix.py` — 接线；`_capture_fix_tokens` 加 `model` 参数
- `src/agent/nodes/review_terms.py` — 接线；密钥守卫改查路由解析结果

**测试（3 M）**：
- `tests/test_dual_model.py` — +10（`TestRoutingTableDrivesNodes`）；补 `import pytest`
- `tests/test_write_guards.py` — +5（`TestFallbackMustLeaveTheRefusingProvider` 3 个 + readback/fix 账本归属 2 个）；两处 helper 同步改名与 `model` 参数；`_mock_review` 类补丁改指向 config
- `tests/test_review_terms.py` — 2 处 `monkeypatch.setattr` 从 `nodes.review_terms.LLM_API_KEY`（已不存在）改为 `src.config.LLM_API_KEY`

**脚本（1 M）**：
- `scripts/mutation_model.py` — 变异表重写（13 条 STALE → 0）；`run_tests()` 扩展跑 `test_write_guards.py`

---

## 八、铁律（承接 round5/6/7，全部有效）

1. 任何"完成"都要能追溯到实测证据
2. 变异测试必须串行，期间禁并发/禁编辑；跑完校验还原 + `*.mutation_backup` 为 0
3. 判"等价不可杀"前，观测点必须覆盖变量被覆盖之前的时刻
4. 优先消除而非豁免；删死代码前必须穷举/差分证明行为零变化
5. 工具的 SKIP/STALE/blocked 计入失败
6. 真实 API 调用一律先报预算等批准
7. 破坏性操作先请用户确认
8. 别凭记忆引用字段/函数名——读码确认后再写
9. 删配置/删符号必须同步清理所有引用方（**本轮再次踩到**：改 `_capture_*_tokens` 签名与 `_build_qwen_writer` 改名后，3 个测试文件共 5 处引用需同步）
10. 改 prompt/模板后必须验证"渲染真的生效"
11. 变异验证的还原只能用 `/tmp` 快照，严禁 `git checkout`
12. 子串断言在多处相似代码的文件里几乎必然假绿
13. 判数据完整性用结构化逐键比对，不用文本 diff 行数
14. 文档里的数字要实测复核，不要当事实引用（**本轮更正 round7 文档 4 处字节数 + 发现账本 8811 行污染**）
15. **【本轮新增】变异体存活有第三种原因：被测单元被 mock 掉了** ——除了"变异太弱"和"断言没牙"，还可能是断言根本没覆盖到那条执行路径。本轮 `write_fallback` 路由变异存活即属此类：builder 被整体 mock，config 层测试又看不见 1301 路径。要验"某调用点向某依赖传了什么"，必须 spy 在**调用点**上捕获实参，不能只 mock 掉被调方。
