# 西渡 · 交接（2026-09-24 第6轮 · 条款11 进行中 · **新窗口从这里开始**）

> **本文件是最新入口。** 上一轮 `HANDOFF_20260924_round5_start_here.md` 的条款1、条款5 **已完成并提交**（`67975f3`、`fc5c452`），不要重做。
> 本轮在做**条款11（Agent 定位通用性：剥离恐怖/美国硬绑定）**，因额度中断，**改到一半、工作区未提交**。
> 前序文档按需查：
> - `HANDOFF_20260924_round5_start_here.md` — 第5轮入口（15 条计划顺序、§四已查明结论、§五待拍板、§六铁律）
> - `HANDOFF_20260923_audit_fix_wip.md` **§六 第11条** — 条款11 的原始 15 处硬绑定清单 + 传递链断点（本文档§二已逐条落地，别重查）

---

## 〇、基线（实测，非声称）

```bash
python3.11 -m pytest -q --no-header     # → 1299 passed, 4 deselected
```
- **必须用 `python3.11`**，本机 `python3` 是 3.9
- 1299 = 上轮基线 1280 + 本轮新增 `tests/test_prompt_personas.py` 的 19 个测试
- **工作区改动已全部提交**：`445ec8b`（条款11 本体）+ `65209b0`（本交接文档）；工作区干净
- 提交历史：`445ec8b`（条款11）← `65209b0`（交接）← `fc5c452`（条款5 残余）← `67975f3`（条款1）
- `write_guards` 变异 **60/60 killed，0 survived / 0 stale / 0 blocked**（改了 write.py 接线后重跑验证，源文件已还原、四指纹零污染）
- `pytest` 裸跑**恒 ¥0**（conftest 封网，机器可检不变量）

---

## 一、P0 悬空状态 —— ✅ 已清除（曾一度悬空，现已修并提交）

**背景**：本轮删了 `cultural_rules.json` 的 ar-SA 配置（181 行/40 条）后，一度出现"配置已删、脚本引用未清"的悬空状态——正是 `5bba508` 制造 adversarial_test 断头的同一模式（删 A 不同步 B）。**当时故意不提交代码**，等清理完再一起提交。

**现已全部清理并提交（`445ec8b`）**：
| 文件 | 已改 |
|---|---|
| `scripts/test_multilang.py` | docstring 去 ar-SA；循环元组删 `("ar-SA",...)`，只留 es-ES |
| `scripts/evaluate_native.py` | docstring 去 ar-SA（并更正过时的 "DeepSeek V4 Pro"→"evaluator model"）；`FIXTURE_MAP` 删 ar-SA 键；CLI `choices` 去 ar-SA |

**验证**：两脚本 `py_compile` 通过（裸露脚本不实跑）；全项目 ar-SA 活引用清零（剩余全是解释性注释，有意保留说明为何废弃）；`evaluate_native.py` 剩余 fixture（`jianke_ch1_es_ES.md`、`jianke_ch1-5_en.md`）实测存在，未删出断头；`static_audit --include-src` 无新断头。

**遗留 fixture**（未被 git 跟踪，删不删看下一位，属破坏性操作）：`tests/fixtures/jianke_ch1_ar_SA.md`、`tests/fixtures/eval_ar_SA.json`。

---

## 二、条款11 本轮做了什么 —— **接线已通、文案已写、质量未验**

原始清单（wip §六第11条）：15 处硬绑定 + 4 个传递链断点 + 4 处参数默认值有偏。**已落地如下**：

### A. 核心障碍（实测发现，文档未记录）
1. **四个 SYSTEM prompt 全部含未转义 JSON 示例花括号，`.format()` 直接抛 `KeyError`**（实测 `WRITE_SYSTEM`/`READ_SYSTEM`/`READBACK_SYSTEM`/`FIX_SYSTEM` 四个全炸）。→ 已全部把 JSON 块的 `{`/`}` 转义为 `{{`/`}}`。
2. **四节点的 system prompt 此前是直接传常量、零 `.format()` 调用**（`SystemMessage(content=prompts.xxx_system)`）。所以参数化不是"改文本"，是**新建渲染链路**。→ 四节点已全部接线为 `prompts.xxx_system.format(**ctx)`。
   - ⚠️ **`write.py` 有两处 SystemMessage**（主路径 L127、空输出重试 fallback L250），已都接到同一个预渲染变量 `system_prompt`，不会漏。
   - `review_terms.py` 用自己的 `_REVIEW_SYSTEM`（术语评审，不涉题材/读者），**不在本次范围，未动**。

### B. 新增参数模块 `src/agent/prompts/personas.py`（?? 未跟踪）
- `build_prompt_context(genre, target_lang) -> dict[str,str]`：返回 13 个占位符（`audience/readers/reader/Reader/locale/output_language/language_name` + `genre_name/genre_tradition/genre_craft/genre_concepts/genre_image_case/genre_emotion`）。
- **5 个 genre profile**：`folk_religion`（M.R.James/The Wicker Man 系）、`urban`（Gaiman/Miéville 系）、`scifi`（Ted Chiang/刘慈欣系）、`xianxia`（Will Wight 系）、`romance_ceo`（Helen Hoang 系）；各含 name/tradition/craft（创作准则）/concepts（image-gap 示例中文词）/image_case/emotion。
- **`GENERIC_GENRE` 兜底**：未知 genre 不谎称某类型，明写"genre is not specified，从本章自行推断"。
- **4 语种** `_LANG_PROFILES`：en-US/es-ES/de/fr，每语种给全语法形态（audience/readers/reader/Reader/locale/output_language），避免 prose 拼接出 "a American" 类错误。
- **单一来源**：`LANGUAGE_NAMES` 从 `translation.py` 导入，不重声明（吸取 measurements.py「寸」三份拷贝漂移的教训）。
- 未知 genre/lang 都 `logger.warning` + 安全 fallback（lang→en-US，genre→generic），**不静默**。

### C. 四 prompt 文件改造（`src/agent/prompts/{read,write,readback,fix}.py`）
- 全部硬编码的 "American"/"horror"/"Stephen King, Junji Ito, Lovecraft"/"English" 换成占位符。
- **READBACK 写死的书名 `"The Underworld Calls Me Little Master"`（《地府》）已移除**，换成 `{book_title}`，节点侧传中性 `"this book"`（name_en 待拍板，见 §五-2；不点名也符合盲读精神）。
- READ_USER/WRITE_USER/READBACK_USER/FIX_USER 也补了必要占位符（`audience`/`output_language`/`reader`/`readers`），节点侧已同步传值。
- 保留了测试锁定的文本：`READ_SYSTEM` 的 `"COVERAGE IS MANDATORY"`（`test_fidelity_injection.py:107` 断言）实测仍在。

### D. 三处数据不一致（用户批准本轮一并修）
| # | 缺陷 | 状态 |
|---|---|---|
| 1 | `_GENRE_SIGNALS` **缺 `urban`**（三本书配 urban，但 `detect_genre` 永远无法返回它） | ✅ **已修**：补 16 个都市异能关键词（兵王/赘婿/世家/风水…），实测**零碰撞**现有 8 个 detect_genre 测试文本，新测试文本 urban 得分 10 唯一胜出 |
| 2 | `cultural_rules.json` 残留 **ar-SA 死配置**（`5bba508` drop ar-SA 时漏删；src/ 零引用、`_KNOWN_LANGS` 不含） | 🟡 **配置已删**（3 块共 181 行，实测非 ar-SA 内容逐键一致）；**脚本引用未清 → 见 §一 P0** |
| 3 | **de/fr 在 cultural_rules.json 完全无规则** | ⬜ **未处理**（见下方"未做"） |

**JSON 是手工格式化的**（indent=2、ensure_ascii=False、无尾换行、fidelity 示例内联），`json.dumps` round-trip **不匹配** → 删 ar-SA 用的是**保字节文本手术**（brace-match 定位 + 连带删前兄弟尾逗号），备份在 `/tmp/cultural_rules_backup_*.json`。下一位改这个 JSON **别用 json.dumps 重写**，会产生上千行不可审 diff。

### E. 新增测试 `tests/test_prompt_personas.py`（?? 未跟踪，19 个）
**关键**：现有测试（`test_fidelity_injection.py` 等）**只捕获 HumanMessage、从不验 SystemMessage** → 节点若漏 `.format()` 会静默泄漏 `{genre_name}` 给模型而测试全绿（本项目反复出现的假绿模式）。新测试用 `FakeChatOpenAI` 捕获 **SystemMessage**，验证：
- 四节点 system prompt 渲染后**无 `{占位符}` 泄漏**（正则 `\{[a-z_]+\}`）
- **参数真驱动**：genre 改 folk_religion↔scifi 输出不同、机甲/出马弟子概念不串；lang 改 en-US↔es-ES 受众措辞变、American 不出现在 es
- 未知 genre 回退 generic 且承认"未指定"
- **三方 genre 同步**：`cultural_rules.json` ⊆ `_GENRE_SIGNALS` ⊆ `personas.GENRE_PROFILES`（防再次漂移）
- 4 语种各自渲染无泄漏

---

## 三、条款11 还没做的（下一位接着做）

1. **P0：脚本层 ar-SA 同步清理**（见 §一，必须先做，否则工作区是半成品）。
2. **de/fr 无 cultural_rules.json 规则** —— ⚠️ **不能简单回退到 en-US**：实测 en-US 与 es-ES 是**同键不同值**（17 条键相同、值分别是英文/西语术语），回退 en-US 会把**英文术语注入德/法语译文**。正确做法二选一（需用户定）：(a) 补 de/fr 的规则数据（大工程）；(b) 在 `load_rules` 加**显式语言回退层**并记 warning，且 fidelity 段目前**只有 en-US**（连 es-ES 都没有）。当前 de/fr 走 `load_rules` 返回 `{}`，READ 节点显示"(No genre-specific rules.)"，**不崩但无规则**。
3. **`src/evaluator_prompts.py` 的 "sci-fi/mecha terminology" 硬编码未动** —— 4 个语种 profile 的 `focus_areas`（L21/64 等）都写死 sci-fi/mecha，`EVALUATOR_SYSTEM`（L90）第4条评分项也写死 "sci-fi/mecha terms"。这是 evaluator（打分器）的题材绑定，本轮只改了四节点 prompt，**evaluator 未参数化**。
4. **`personas.py` 未进 mutation TARGETS** —— 新模块无变异覆盖（`scripts/mutation_test.py` 的 TARGETS 表）。read/readback/fix 节点、prompts/*.py 也都不在 TARGETS。
5. ✅ **已完成**：改了 `write.py` 接线后重跑 `write_guards` 变异 → **60/60 killed，0 survived / 0 stale / 0 blocked**，接线未引入存活变异体。
6. **⚠️ 新 genre 文案的实际翻译质量未经 API 验证** —— 文案是我按各类型文学传统写的（folk_religion→village-ritual dread、scifi→idea-driven…），**接线通了、渲染对了、变异绿了，但"这些 genre 指引能否真的提升对应书的译文质量"需要真实 API 跑几章才知道**，按铁律6 须**先报预算等批准**。这是条款11 的最终验收缺口，也是唯一剩下的实质项。
7. ✅ **已提交**：P0 悬空清除后，本轮全部改动已提交 `445ec8b`，工作区干净。

---

## 四、15 条计划最新状态（权威见 TodoList）

> 用户指定顺序：1 → 5 → 3 → 11 → 12 → 13 → 14 → 2 → 10 → 8 → 9 → 7 → 6 → 4（第15条附末尾）。
> **条款1、5、11 主体已完成并提交。** 注意：用户本轮临时插队让先做 11（原顺序 11 在 3 之后，但 3 需 API 预算，故先推 11）。

| # | 条款 | 状态 | 下一步 |
|---|---|---|---|
| 1 | 工程审计 | ✅ 完成（`67975f3`，957/957 变异全杀） | — |
| 5 | 交接验证+烂尾修复 | ✅ 完成（`fc5c452`） | 移交项见其 §七：剧本残留 2 脚本仍有 5 条断头，清完后 `scripts/static_audit.py` 可接 CI |
| 11 | Agent 定位通用性 | 🟢 **主体完成并提交（`445ec8b`）** | 四节点 prompt 已参数化 + 接线 + 变异绿。剩：新 genre 文案的翻译质量需 API 验证（§三-6）、evaluator_prompts 未参数化、de/fr 无规则、personas 未进变异 TARGETS |
| 3 | 模型变更质量回归 | 🟡 成本已重算 | 只剩质量回归，需真实 API → 先报预算 |
| 12 | 思考链档位成本 | ⬜ | `MODEL_MAP` 三节点共用 `QWEN_MODEL`，READBACK 不需 max 档 |
| 13 | TM/TB 知识资产 | ⬜ | TM 空白、FIX 回写闭环不存在、`scan_deliverable.py` 不存在 |
| 14 | 全量扫描+坏章修复 | 🟡 坏章已查明 | 见项目记忆 `project_difu_bad_chapters.md`；报告"2301/2301"不成立 |
| 2 | 端到端质量+西语启动 | ⬜ | 前置依赖 11（否则西语跑"美国读者"视角）；需 API 预算 |
| 10 | READBACK 盲读独立性 | 🟢 基本通过 | 只剩 `graph.py:155` 角色中文名泄漏（元数据级，极低） |
| 8 | Qwen-MT 嵌入评估 | 🟡 官方文档已查完 | 见 round5 §四-B；方案须用户确认后实施 |
| 9 | 主笔 agent 评估 | ⬜ 倾向"缺" | 核实 style_memo 是否真被 WRITE/FIX 消费 |
| 7 | 不降质降成本 | ⬜ 与12耦合 | 基线 ¥0.5/千英文词 |
| 6 | API 调用管控 | 🟡 大半落地 | 剩 20 个裸露脚本无闸门；本轮 ar-SA 清理涉及其中 2 个 |
| 4 | 项目边界（剧本/游戏线） | ⬜ | 剧本残留 2 脚本（5 条断头）+ Dockerfile/pyproject 版本矛盾 + `evaluator_prompts.py`/`quanzhi_fanwai_segmented/` 残留 |
| 15 | circuit_breaker 死配置 | ⬜ **待拍板** | 见 round5 §五，三选一，前轮建议"暂不动" |

---

## 五、待用户拍板（承接 round5 §五，仍有效）

1. **5 本书的 `name_en`**（无限恐怖/地府叫我小先生/间客/全职高手番外/愿你灿烂如阳）—— 代码现回退 `book_id` ASCII 化。**本轮 READBACK 的 `{book_title}` 暂用中性 "this book"**，name_en 定了之后可在 `readback.py` 节点把 `ctx["book_title"]` 换成真实书名（state 目前无书名字段，需先加）。
2. **回改已污染历史语料**（破坏性）：`difu_en.md` 首行中文标题等。
3. **清理 `output/` 约 404 个测试残留**（破坏性，未跟踪，删不可恢复）。
4. **条款15 circuit_breaker**（round5 §五，三选一）。
5. **新增：de/fr 的 cultural_rules.json 规则怎么办**（见 §三-2，补数据 vs 加语言回退层）。

---

## 六、本轮改动文件清单（14 文件，已提交 `445ec8b`）

**新增（2）**：
- `src/agent/prompts/personas.py` — genre×语种参数模块（build_prompt_context + 5 profile + GENERIC + 4 lang）
- `tests/test_prompt_personas.py` — 19 测试，捕获 SystemMessage 验渲染无泄漏 + 参数真驱动 + 三方 genre 同步

**源码改动（10 M）**：
- `src/agent/prompts/read.py` / `write.py` / `readback.py` / `fix.py` — 硬编码→占位符，JSON 块花括号转义
- `src/agent/nodes/read.py` / `write.py`（含 fallback 两处）/ `readback.py` / `fix.py` — 导入 build_prompt_context、渲染 system prompt、user prompt 补占位符
- `src/cultural_rules.py` — `_GENRE_SIGNALS` 补 urban（16 关键词）
- `cultural_rules.json` — 删 ar-SA 死配置（181 行/40 条，保字节手术）
- `scripts/test_multilang.py` / `evaluate_native.py` — 同步清理 ar-SA 引用（P0）

**已验证**：`write_guards` 变异 60/60 killed（0 survived/stale/blocked）；全量 1299 passed；static_audit 无新断头；四指纹零污染。均已提交。

---

## 七、铁律（承接 round5 §六，全部有效，本轮再次验证其价值）

1. **任何"完成"都要能追溯到实测证据** —— 本轮测试全绿但我**没敢直接采信**：现有测试只验 HumanMessage，system prompt 接线错了也会全绿。是新写的捕获 SystemMessage 的测试才证明接线真通了。**假绿模式在本项目已出现 5+ 次。**
2. **变异测试必须串行**，期间禁并发/禁编辑；跑完 diff 校验还原 + `*.mutation_backup` 为 0。
3. **判"等价不可杀"前，观测点必须覆盖变量被覆盖之前的时刻。**
4. **优先消除而非豁免**；删死代码前必须穷举/差分证明行为零变化。
5. **工具的 SKIP/STALE/blocked 计入失败。**
6. **真实 API 调用一律先报预算等批准。** 裸露脚本不能实跑验证，改完只能 py_compile。
7. **破坏性操作先请用户确认**（删文件用 `git rm` 保可恢复；JSON 改前先备份）。
8. **别凭记忆引用字段/函数名** —— 读码确认后再写。本轮踩过：`_GENRE_SIGNALS`（不是 `GENRE_KEYWORDS`）、`SemanticGlossary`（不是 `SemanticStore`）、`build_prompt_context` 的 key 名。
9. **【本轮新增】删配置/删符号必须同步清理所有引用方** —— `5bba508` 删 ar-SA 漏改 adversarial_test.py 造断头；本轮删 ar-SA 配置也差点漏改 test_multilang.py/evaluate_native.py（见 §一 P0）。删任何东西前先 `grep -rn` 全项目引用。
10. **【本轮新增】改 prompt/模板后必须验证"渲染真的生效"** —— 参数化模板最危险的失败是静默泄漏 `{占位符}` 字面量给模型，而 mock 测试看不见。必须有捕获实际 prompt 文本的测试。

---

## 八、条款11 剩余工作（P0 已清、代码已提交、变异已绿）

已完成并提交的：P0 ar-SA 脚本清理、四节点参数化接线、write_guards 变异 60/60、
全量 1299 passed、static_audit 无新断头。**工作区干净，可直接接着做下面任一项。**

```bash
# 验证当前状态（都应绿）
python3.11 -m pytest -q --no-header                    # → 1299 passed
python3.11 scripts/static_audit.py --self-test         # → 27/27
```

**剩余项（按优先级）**：
1. **新 genre 文案的翻译质量验证**（条款11 最终验收缺口，§三-6）——需真实 API 跑几章
   对比"参数化前 vs 后"或不同 genre 指引的译文差异。**按铁律6 先报预算等批准**。
2. **evaluator_prompts.py 参数化**（§三-3）——4 语种 focus_areas + EVALUATOR_SYSTEM
   的 sci-fi/mecha 硬编码，与四节点同类问题，可复用 personas.build_prompt_context。
3. **de/fr 的 cultural_rules.json 规则**（§三-2，待用户拍板）——补数据 vs 加显式语言
   回退层。⚠️ 不能简单回退 en-US（同键不同值，会注入英文术语）。
4. **personas.py 进 mutation TARGETS**（§三-4）——新模块目前无变异覆盖。

**改 prompt/模板的铁律（本轮验证过，务必遵守）**：任何模板改动后，必须有捕获
实际渲染文本的测试（不是只验 HumanMessage），否则 `{占位符}` 泄漏给模型时测试
仍全绿。删任何配置/符号前先 `grep -rn` 全项目同步清理引用方。
