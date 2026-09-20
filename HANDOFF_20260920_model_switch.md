# 西渡模型切换交接（2026-09-20）

> 背景：西渡全面弃 DeepSeek（用户拍板，DeepSeek 对文字工作是灾难），切 GLM/Qwen 三模型混搭。
> 代码和测试都改完、全绿，**但端到端验证还没跑通**——卡在 GLM 的两个问题上（见第五节）。
> 这份交接是给「能用自己终端的 agent」接手端到端验证用的。

---

## 一、最终模型配置（5 节点）

| 节点 | 模型 | 温度 | 思考强度 | max_tokens | timeout |
|---|---|---|---|---|---|
| READ | glm-5.3（GLM full） | 0.3 | reasoning_effort=high | 16384 | **900s** |
| WRITE | glm-5.3-flash | 0.2 | reasoning_effort=low | 16384 | **300s** |
| READBACK | qwen3.8-max | 0.1 | thinking_budget=2048 | 4096 | 120s |
| FIX | glm-5.3（GLM full） | 0.1 | reasoning_effort=high | 16384 | **900s** |
| REVIEW_TERMS | qwen3.8-max | 0.1 | thinking_budget=2048 | 4096 | 120s |

**分配逻辑**：
- GLM 5.3（full）管「文字思考」→ READ（深度找文化缺口）+ FIX（靶向修复）
- GLM 5.3 flash 管「文字输出」→ WRITE（编译输出，便宜 10 倍）
- Qwen 3.8 max 管「读取判断」→ READBACK + REVIEW_TERMS

**思考强度机制差异**（关键）：
- GLM 用 `reasoning_effort` 档位（只有 max/high/low 三档，默认 max 会贵 35 倍，**必须显式设**）
- Qwen 用 `thinking_budget`（显式 token 数，放 `extra_body`；**不能**和 `reasoning_effort` 同设）

**timeout 依据**（照铸文实测）：GLM full 深度思考（adaptation）用 900s、flash（script_writer）用 300s。西渡原来 120s 太短，已改。

---

## 二、已完成的改动清单

**代码**：
- `src/config.py`：删 DeepSeek 全部配置（`DEEPSEEK_*`/`READ_*`/`FLASH_MODEL`/`LLM_MODEL`），加 `GLM_*`（key/base_url/model/full_model/三个 effort）+ `QWEN_MODEL`/`QWEN_THINKING_BUDGET`
- `src/agent/nodes/read.py`：deepseek → GLM full，reasoning_effort=high，timeout 900
- `src/agent/nodes/write.py`：qwen-plus → GLM flash，reasoning_effort=low，温度 0.2，timeout 300
- `src/agent/nodes/readback.py`：qwen-plus → qwen3.8-max，extra_body thinking_budget
- `src/agent/nodes/fix.py`：qwen-plus → GLM full，reasoning_effort=high，timeout 900
- `src/agent/nodes/review_terms.py`：qwen-plus → qwen3.8-max，extra_body thinking_budget
- `src/health.py`：health check probe 从 DeepSeek 改 QWEN_MODEL
- `src/api/routes.py`：上传 key 检查从 DeepSeek 改 LLM_API_KEY

**测试**：
- `tests/test_dual_model.py` 重写成三模型路由测试（5 个），断言**硬编码字符串**（`"glm-5.3"` 等），不是引用 config 常量
- `tests/conftest.py` 加了 circuit breaker 单例重置 fixture
- 完整测试 **556 passed + 4 deselected，全绿**

**变异测试**（`scripts/mutation_model.py`）：**只有「模型切换」这部分做完了** —— 29 个变异，29 个杀死，0 存活（覆盖模型名/温度/max_tokens/思考强度/config 默认值写错）。

**⚠️ 但项目整体的变异测试没做完**，有存活变异没处理、还有一批模块根本没跑变异。详见第六节「变异测试缺口清单」，接手 agent 必须补。

**`.env`**：已配 GLM key（`GLM_API_KEY` + `GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4`），Qwen key 已换成用户给的新 key（`LLM_API_KEY`）。

**工具**：
- `scripts/mutation_test.py`（纯逻辑模块变异测试）
- `scripts/mutation_model.py`（模型/参数变异测试）
- `scripts/e2e_verify.py`（端到端验证，落盘 4 个中间产物）

---

## 三、端到端验证的两个问题（还没跑通）

端到端脚本：`python3.11 scripts/e2e_verify.py`（注意用 3.11，不是 3.9；3.9 缺依赖会卡）。

### 问题 1：GLM 内容安全过滤（code 1301）

第一版测试章节是「穿成霸总文女主」，里面有「草泥马」（脏话谐音）。GLM 返回：

```
openai.BadRequestError: Error code: 400 - code 1301
系统检测到输入或生成内容可能包含不安全或敏感内容
contentFilter: [{'level': 1, 'role': 'assistant'}]
```

- **不是网络/ key /代码问题**，是 GLM 的内容安全层拦了 READ 节点（GLM）的输出
- `role: assistant` 说明是模型生成内容触发的过滤
- **风险**：网文里脏话、「霸总」「穿书」这类元素到处都是，如果 GLM 过滤对网文过度敏感，READ 用 GLM 这个选型要重新评估

**已做的处理**：e2e_verify.py 测试章节换成了「鬼节」题材（folk_religion，无脏话），先验证管线本身通不通。**还没验证换章节后过滤是否还拦**。

### 问题 2：GLM 超时（ReadTimeout）

换干净章节后，README 调 GLM 超时：

```
openai.APITimeoutError: Request timed out (httpcore.ReadTimeout)
```

- 原因：GLM 5.3 深度思考（reasoning_effort=high）慢，西渡原来的 `request_timeout=120` 太短
- 已修复：READ/FIX → 900s、WRITE → 300s（照铸文 adaptation/script_writer）
- **还没验证改 timeout 后能否跑通**

---

## 四、接手 agent 要做的

1. **跑端到端**（用自己终端，能连 GLM/Qwen）：
   ```bash
   cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）" && python3.11 scripts/e2e_verify.py
   ```
   （READ 深度思考可能要几分钟，耐心等）

2. **读结果**：跑完读 `output/` 下 4 个文件交叉核对：
   - `e2e_read_analysis.json`（READ 找的文化缺口）
   - `e2e_translated.json`（WRITE 译文 + 新术语）
   - `e2e_readback.json`（READBACK 冷读反馈）
   - `e2e_result.json`（最终 verdict）
   
   判断标准：`verdict=PASS` + `would_keep_reading=True`，且 4 个文件都有实质内容（不是空）。

3. **重点排查**：
   - 如果还报 400/1301（内容过滤）→ 说明「鬼节」章节也有词触发，或者 GLM 过滤太严，需要进一步换更干净的文本、或评估 GLM 过滤对网文的影响面
   - 如果还超时 → timeout 还要加大，或 READ 的 reasoning_effort 从 high 降到 medium（但 GLM 没 medium，只有 high/low，high 超时就得考虑 low 或换模型）

4. **跑通后的最终验证**：拿一本真实网文（之前 DeepSeek 时代验证过的书，如「地府叫我小先生」）跑几章，冷读 PASS 率对比基线（59/59、16/16），确认换模型不降质量。

---

## 五、关键风险提示（给接手 agent）

1. **GLM 内容过滤是最大风险**：网文脏话多，如果 GLM 频繁拦，READ/FIX 用 GLM 的选型可能不成立，得回到「READ 用 Qwen」或找别的方式。这是选型级的验证，不是修 bug。

2. **timeout 已改但没验证**：900s 是照铸文 adaptation 的经验值，西渡 READ 的 prompt 更长（含文化规则 + 上下文信号），实际可能还要调。

3. **变异测试必须做**：改任何模型/参数/配置后，跑 `python3.11 scripts/mutation_model.py`，确认变异被杀死。断言必须用硬编码字面量，不能用 config 常量（否则共享常量假绿）。

4. **端到端验证的副作用**：`e2e_verify.py` 不初始化 file logging（`setup_logging` 只在 main.py 里），所以 `data/app.log` 不会有节点日志——判断进度看 `output/` 下的文件，别看 app.log。

---

## 六、变异测试缺口清单（接手 agent 必须补，这是没做完的活）

诚实交代：项目整体的变异测试**没做完**。已做完的只有「模型切换参数」那部分（29/29 杀死）。以下缺口接手后必须补。

### A. 有存活变异、没处理的模块

这些模块用 `scripts/mutation_test.py` 跑过变异，但**有存活变异没补测试杀死**：

| 模块 | 存活变异数 |
|---|---|
| style_memo | 49 |
| semantic_store | 35 |
| exact_store | 22 |
| measurements | 16 |
| job_store | 9 |
| epub_builder | 7 |
| circuit_breaker | 9 |
| idioms / dialect / onomatopoeia / cms / chapter_splitter 等 | 各若干 |

**补法**：对每个存活变异，看它是「等效变异」（无害，如 format 精度 +1 不影响输出）还是「真盲点」（测试没覆盖到逻辑）。真盲点就补测试杀死它，再跑 `python3.11 scripts/mutation_test.py <模块key>` 确认 0 存活。

### B. 完全没做变异测试的模块

- **`src/agent/graph.py` 的 `_post_process`** ← 最高优先级。这是西渡「编译靠工程机制兜底」的命根子：
  - `check_cultural_fidelity`（fidelity check）
  - fidelity gate（WRITE 漂移时用 READ 的译法）
  - curation gate（丢空/含中文/带声调拼音/带数字的坏术语）
  这三段 gate 逻辑如果写错（条件翻转、把坏术语放行），测试未必红，必须做变异验证。
- `src/api/routes.py`（几十个 API 端点的参数校验、backpressure 交互、checkpoint 续传）
- `src/api/auth.py`（APIKeyMiddleware）、`cms.py`（WebhookConnector）、`editor.py`、`rate_limit.py`
- `src/health.py`（check_llm_api 等）、`prefetch.py`、`stats.py`

### C. 铁律（写给接手 agent）

- 改任何逻辑/参数/配置后，跑变异测试，报告「N 变异 / N 杀死 / 0 存活」，不能只报「测试全绿」
- 断言必须用硬编码字面量（`"glm-5.3"`、`2048`），不要引用被测常量——否则共享常量假绿漏网
- 这是用户明确拍板的铁律，没得商量

---

## 七、其他遗留（非阻塞，但接手 agent 要知道）

**scripts/ 里的断头引用**：6 个开发调试脚本还在 `from src.encoding import detect_and_read`，但 `src/encoding.py` 已在之前的 script/game 清理中删掉（它是 routes.py 内联编码检测的重复实现）。这些脚本一跑就 ImportError：

- `scripts/run_one_segment.py`
- `scripts/test_jianke.py`
- `scripts/make_ckpt.py`
- `scripts/diag_api_hang.py`
- `scripts/audit_e2e.py`
- `scripts/destructive_attacks.py`

修法：把 `detect_and_read(...)` 换成 `open(path, encoding="utf-8").read()`（它们本来就是读测试 fixture 的 txt）。

---

## 八、代码提交状态

所有改动（config、5 个 node、测试、`.env` 的 key、脚本）**都在本地未提交**。接手 agent 开工前，建议先 `git status` 看清现状，避免和未提交状态搅在一起。
