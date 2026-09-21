# 西渡交接（2026-09-21 · 质量审计与不可信声明）

> **本文档性质**：把当天一次独立审计的结论、已验证的修复、以及**一批必须视为不可信的既有数字**交代清楚。
> **本文档优先级高于此前所有交接文档。** 若本文档与 `HANDOFF_20260921.md` 冲突，以本文档为准。

---

## 〇、西渡是干什么的

**把中文网络小说编译成英文给美国读者看——不是翻译，是重写。**

四个 agent：

| 节点 | 角色 | 干什么 |
|---|---|---|
| **READ** | 双语文化智能 | 读中文原文，找**文化缺口**——"中文读者脑中免费补上、美国人补不上的画面"，并提出术语决策 |
| **WRITE** | **英文类型小说作者** | **不是译者**。被明确授权**重构 / 压缩 / 扩写 / 砍段 / 创造**，目的是让美国读者**感受到中文读者感受到的东西** |
| **READBACK** | **不知道这是翻译的美国冷读者** | 只读英文产出，报"读得懂吗？无聊吗？还想追吗？" |
| **FIX** | 编辑 | 按冷读者的具体抱怨做**定点**修复 |

**全类型，不限恐怖**——恐怖只是被挑出来当最难的那一类。

**四条验收标准必须同时成立**（不是四选一）：

1. **零中文**——prompt 里写明"Chinese characters in the output are the #1 quality failure"
2. **完整性**——"Every scene in the source must have an equivalent in the output"
3. **术语一致**——"Glossary terms are **LAW**"
4. **冷读想追更**

---

## 一、【不可信声明】此前的测试数字与质量记录，全部不可信

**这不是"可能有偏差"，是"已查明不可作为依据"。** 逐条列明：

### 1.1 项目级变异测试的数字不可信

`HANDOFF_20260921.md` 报告：

> Project-wide: **657 mutants / 643 killed / 14 proven equivalents / 0 unexplained**

**这个结果是在一个偏位的仪器上测出来的。** 原因见第二节：`scripts/mutation_test.py` 的变异体被写到了**错误的位置**。

**必须重跑全部 target 才能给出可信数字。**

### 1.2 冷读质量记录不可信（59/59、16/16、3/3）

三份记录（`novels/output/difu_segmented/_quality.json` 等）全部**不可作为质量依据**，三个独立原因：

**① 写译文的与判译文的是同一个模型家族。**
记录产生于 2026-07-28 ~ 08-03，当时的配置：

```python
MODEL_MAP = {
    "translate": DEEPSEEK_FLASH_MODEL,   # WRITE
    "read":      DEEPSEEK_PRO_MODEL,     # READ
    "readback":  DEEPSEEK_FLASH_MODEL,   # ← 冷读者，同族
    "fix":       DEEPSEEK_FLASH_MODEL,
}
```

**自评不可作为外部质量证据。**

**② 抽检率 2.6%，且抽检样本是"特殊生产"的。**
`run_novel.py` 的抽样点是每 50 章一个。**而抽样点决定了生产路径**：

```python
is_sample = pos in sample_points
skip_readback=not is_sample,      # 只有采样章跑冷读
```

**97.4% 的章从未被冷读、也从未进入修复循环。** 记录只描述了那 2.6%。

**③ 统计上无法检出实际缺陷率。**
实测交付缺陷率 0.46%。在 0.46% 缺陷率下，抽 59 章**漏掉全部缺陷的概率是 79%**。
**"59/59 全 PASS"是这个缺陷率下的预期结果，不携带质量信息。**

**④ 换采样点结论会反转。** 《无限恐怖》的同目录下存在 `_reaudit_v2.json`：
换 6 个章节重审，**0/6 全部 NEEDS_FIX**（那 6 章都不在原采样点内）。
**且该重审结论没有任何文档记录。**

### 1.3 进度报告不可信

实测：`run_novel.py` 跑 2 章，第 2 章被内容过滤拦死、**从未写进交付文件**，而它打印：

```
Segment 1: 1-2/2 (100%) in 7m
ALL DONE: 2 chapters in 7m
```

**实际交付 1/2（50%），报告 100% 完成。**

### 1.4 质量分（`quality_score`）不可信

```python
# graph.py:53
verdict = feedback.get("verdict", "PASS") if feedback else "PASS"
# graph.py:296  初始状态
"quality_score": 5.0,
# graph.py:387  取不到也是 5.0
"quality_score": final_result.get("quality_score", 5.0),
```

**flash 模式下冷读被跳过，`quality_score` 恒为初始值 5.0——它是初值，不是测量值。**
返回结果里**没有** `readback_skipped` 之类的标记，下游无法区分"冷读说 PASS"与"根本没人读"。

（注：`quality_score` 不出现在 `src/api/`、`celery_app.py`、`job_store.py`，所以**用户在界面上看不到这个假 5.0**；但内部脚本会读到它。）

---

## 二、本次查明的工具链缺陷（三处，均已修）

### 2.1 【严重】变异体被写到错误位置 —— `node_span` 用字符数配字节偏移

`scripts/mutation_test.py` 原代码：

```python
def node_span(source, node):
    lines = source.split("\n")
    start = sum(len(l) + 1 for l in lines[: node.lineno - 1]) + node.col_offset
    #           ↑ 字符数                                          ↑ 字节偏移
```

**`ast` 的 `col_offset` 是 UTF-8 字节偏移**，而这里用**字符数**累加行前缀。
**任何含中文的行两者都不等**——而这个项目的注释、字符串、弯引号、破折号里到处是中文。

**实测**（`src/agent/fidelity.py` 第 30 行）：

```
第 30 行：字符数 53，字节数 63
AST: value=8  col_offset=51（字节）
脚本算出的偏移: 1245  → 实际切到 '9'    ← 切错位置
正确的字节偏移: 1275  → 切片得到 '8'    ← 对的
```

**后果**：变异体 `const 8->9 at 30` **完全没有改动第 30 行**，而是改了文件里另一个无关位置。
没有测试能杀掉它 → **被记成"存活盲点"**。

**三种可能的坏结果，全部会污染结论**：

| 变异落到哪 | 后果 |
|---|---|
| 无关字符 | mutant 无效 → 误记"存活盲点" |
| **另一处真实代码** | **测试可能被无关原因杀死 → 误记"杀死"，实际目标位置未验证** |
| 字符串/注释里 | 静默无效，且看不出来 |

**修法**：全程按字节。`node_span` 用 `len(l.encode("utf-8"))`，并且**切片也要切字节**（用字节偏移去切 str 同样错）：

```python
data = source.encode("utf-8")
mutated = (data[:start] + replacement.encode("utf-8") + data[end:]).decode("utf-8")
```

**验证**：修复后 27 个 const 变异体**全部真正改到目标行**（0 个未生效）；
`fidelity` 重跑 **74/74 杀死，0 存活**（修复前报 73/74，那 1 个是假的）。

**`scripts/mutation_model.py` 不受此 bug 影响**——它用字符串 `replace()`，不走 span 计算。
**所以它的 29/29 是可信的。**

### 2.2 变异脚本被强杀会留下变异体（两个脚本都有）

**SIGTERM 不做栈展开**，所以 `finally` 的还原不执行，**源文件留着变异体**。
更糟的是 `mutation_test.py` 的备份会被**下一次运行覆盖**——它把已损坏的文件当"original"，
结束时还原成它并删除备份。**损坏从此永久且静默。**

**实测发生过 3 次**：

| 文件 | 被留下的变异 | 为什么没被发现 |
|---|---|---|
| `src/agent/nodes/write.py` | `state.get('exact_glossary') and {}`（应为 `or {}`） | 语义等价于传空术语表，功能静默失效 |
| `src/agent/nodes/review_terms.py` | `extra_body={"thinking_budget": ...}` **整行被删** | Python 容忍括号内多余缩进，**语法仍然合法** |
| （同上，第 2 次复发） | 同 write.py | — |

**修法（两个脚本都已加）**：
1. **发现残留 `*.mutation_backup` 就拒绝启动**（检查必须在读文件**之前**）
2. **SIGTERM/SIGINT 处理器**：先写回源文件再退出
3. `mutation_model.py` **此前完全没有备份机制**，现已补上

**验证**：SIGTERM 中途打断 → 源码哈希不变、无残留备份。

### 2.3 SKIP 不计数 → 陈旧变异清单会假装全绿

`mutation_model.py` 的 `MUTATIONS` 是**手写的字符串对**。改了代码之后，旧条目会 `pattern not found` 而**静默跳过**，而 summary 不统计 SKIP。

**实测**：READ 换成 Qwen 之后，3 条 READ 的变异条目已失效（`GLM_FULL_MODEL`、`GLM_READ_EFFORT` 已不存在），**summary 仍报"0 survived"**。

**修法**：SKIP 改为 `[STALE]` 并计入失败：

```
stale mutations (pattern gone): N
FAIL: N mutation(s) no longer match the source. Update MUTATIONS before trusting this result.
```

---

## 三、与"内容过滤"相关的结论（含对上一份交接的更正）

`HANDOFF_20260921.md` 的「一·B」结论是：

> GLM 的内容过滤咬的是脏话/脏话谐音，不是网文题材……**所以「READ/FIX 用 GLM」的选型成立**。

**该结论已被实测推翻。**

那份 A/B 只测了**一章、一种改法**（把「草泥马」换成「羊驼」）。用项目自己的生产脚本跑《间客》（**猫腻的正式出版作品，无脏话**）前 2 章：

```
[2/2 Ch2] ERROR: Error code: 400 - {'contentFilter': [{'level': 1, 'role': 'assistant'}],
          'error': {'code': '1301', 'message': '系统检测到输入或生成内容可能包含不安全或敏感内容...'}}
Segment 1: 1-2/2 (100%) in 7m
```

**2 章里 1 章被拦死（50%）。** 这不是脏话问题，是正常出版小说。

**处置**：READ 已换到 **qwen3.8-max**（见第四节）。换后同一本书同一 2 章：**2/2 交付，第 2 章冷读 PASS，would_keep_reading=True**。

**另注意**：官方文档未提供降低过滤的开关；[错误码文档](https://docs.bigmodel.cn/cn/faq/api-code) 仅说明这是内容安全拦截。
现实中同类问题的通行做法是**换家兜底**（例：[从 HTTP 400 转到 ChatGPT 兜底](https://www.shuijingwanwq.com/2026/08/15/25898/)）。

**不要**为了绕过过滤而改写原文——那是**审查原文**，产品必须译出原文的粗鲁。

---

## 四、本次已验证的修复

全部通过：**998 个测试通过** + **变异验证**。

### 4.1 READ → qwen3.8-max

| 文件 | 改动 |
|---|---|
| `src/agent/nodes/read.py` | `GLM_FULL_MODEL`→`QWEN_MODEL`；`GLM_API_KEY/GLM_BASE_URL`→`LLM_API_KEY/LLM_BASE_URL`；`reasoning_effort=high`→**`extra_body={"thinking_budget": 2048}`**（**两者不能同设**，所以 effort 是删除而非保留） |
| `src/config.py` | `MODEL_MAP["read"]`→`QWEN_MODEL`；删除已成死配置的 `GLM_READ_EFFORT` |
| `tests/test_dual_model.py` | 断言改为 `"qwen3.8-max"` / `LLM_BASE_URL` / `LLM_API_KEY` / `extra_body=={"thinking_budget":2048}`；**并断言 `reasoning_effort` 不存在** |

**WRITE / FIX 仍在 GLM**（未改）。**验证**：`mutation_model.py` **29/29 杀死，0 存活，0 陈旧**。

### 4.2 截断检测（`src/agent/nodes/write.py`）

**此前没有任何代码读 `finish_reason`**，所以撞 `max_tokens` 的截断与完整输出**无法区分**，残缺内容当作完整译文落盘。
这正是 `src/chapter_slicer.py` 记录的那个事故：*"can exhaust DeepSeek V4's context window, producing empty translations with Chinese character residue"*。
切片只是降低了概率，**从来不是检测器**，而且**它的预算按 DeepSeek 上下文校准，没算新栈的思考 token**。

- 新增 `_was_truncated()`：读 `response_metadata` / `additional_kwargs` 的 `finish_reason`；
  `length` / `max_tokens` / `max_output_tokens` / `content_filter` → 清空输出，走重试路径
- **Layer 5 不再把任何不可解析的响应当译文**。它仍救回"模型没给 JSON 信封但确实写了散文"这个合法情形；**丢弃符号乱码与残桩**

### 4.3 内容过滤兜底（`write.py`）

遇 code 1301 时，**只对该章**换另一家重试**一次**。同家重试无效（过滤对同一文本是确定性的），改写原文等于交付审查版。

### 4.4 场景级完整性检查（`src/completeness.py`，新模块）

WRITE 被授权压缩与砍段，而 prompt 同时规定"每场戏必须有对应"——**此前没有任何东西验证这条**：
READBACK 原理上看不到原文（`graph.py` 注释明写 "READBACK is blind to the source"），截断守卫只数词数。

**三个判据，全部零成本、无需 API**：长度比、对话 span 比、术语覆盖。

**标定**（《地府》：11 个已知截断章 vs 8 个正常章）：

| 判据 | 结果 |
|---|---|
| 长度比 | 缺陷 0.23~1.84，正常 3.70~8.13 |
| 对话 span | 缺陷 **16~26 → 0**，正常保留 70~90% |
| **`glossary_missing`** | ❌ **不能作判据**：缺陷章缺 8~24 个，**正常章也缺 5~16 个，区间重叠** |

**`glossary_missing` 只作指标、不作 flag**，并写了测试锁死这条否定结论
（原因：WRITE 被授权压缩次要提及，所以"源文提到、译文没提"是**设计允许**的）。

**合成判定：11/11 命中缺陷，0/8 误报。** 目前**只记录、不拦截**——阈值要用更大语料校准。

### 4.5 【本次审计的重要发现】curation gate 静默删掉正确术语

**症状**（实测运行日志）：

```
CURATION GATE ch2: dropping '联邦二十三频道' → 'Federal Channel 23'
                  (digit appeared that the source doesn't have)
```

**而源文里这个词出现 3 次**（`二十三频道` 16 次），**并且源文两种写法都有**：

```
暂时停止了联邦23频道在整个河西州的信号接收后
```

**根因**：

```python
# 旧代码
if any(ch.isdigit() for ch in en) and not any(ch.isdigit() for ch in term_cn):
    return False, "digit appeared that the source doesn't have"
```

**`ch.isdigit()` 只认阿拉伯数字字符。** 「二十三」是汉字，被判成"没有数字"，
于是英文里的 `23` 就成了"凭空捏造"。

**后果**：删掉了这一章的**主线术语**（孤儿游行的口号就是"我们要看联邦二十三频道！"），
并把它**永久拉黑**，后续章节的译法从此不一致。

**这不是边缘案例**：中文里层数、品阶、年份、人数、名号**全都用汉字写数字**。

**修法**：判据从"源文有没有阿拉伯数字字符"改成"**这个数字源文里到底有没有**"。

```python
def _numbers_in(s):  # 阿拉伯数字 + 中文数字，两种读法都支持
    # 三零一 → 301（逐位读）   二百零一 → 201（位权读）
```

**同时保住了 `王三 → M3` 这个保护，但换了正确的理由**：
`王三` 里有「三」，所以 `M3` 的 3 **不算捏造**——`M3` 的问题是**"它是编号，不是名字"**。
新增独立规则 `_is_code_like`（裸字母+数字，如 `M3`、`B12`）。

**两条规则的标定依据**：全量 **1936 条真实术语，各 0 误报**；
所有合法含数字译名都有词结构（`Refuge Base 7`、`Nemesis-2`、`Riffraff No.1`、`Chaonei Street 130`、`301 Degrees`）。

**验证**：`fidelity` 变异 **74/74 杀死，0 存活**（修复字节偏移 bug 之后）。

### 4.6 顺带删除死代码

- `_has_digit`（替换后无调用方）
- `GLM_READ_EFFORT`（READ 换 Qwen 后无用途）

---

## 五、验证状态总表

| 项目 | 结果 |
|---|---|
| 全套测试 | **998 passed, 4 deselected** |
| `completeness` 变异 | **35/35 杀死，0 存活** |
| 模型参数变异（`mutation_model.py`） | **29/29 杀死，0 存活，0 陈旧** |
| `fidelity` 变异 | **74/74 杀死，0 存活** |
| `write_guards` 变异 | ⚠️ **未重跑**——上一次 21/48 是在**被污染的基线**上算的，**数字作废** |
| 端到端实测 | 《间客》2 章：READ 在 GLM 时 **1/2**；READ 换 Qwen 后 **2/2 + 冷读 PASS** |

提交：`1959cf1`、`291ded9`（工作区干净）。

---

## 六、已知未修（如实交代）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 1 | **`run_novel.py` 失败章静默消失，进度仍报 100%** | 实测 1/2 报 100% + ALL DONE | "完成"这个词不可信 |
| 2 | **`routes.py:322` `complete_job` 无条件调用** | 章节失败 `continue` 后仍标记完成 | 同上 |
| 3 | **中文标题落进英文交付物** | 交付文件里 `## Chapter 2: 第一章 钟楼街的游行` | 违反 ZERO-CHINESE RULE |
| 4 | **`--limit N` 模式永不冷读** | 抽样点从第 10 章起，N<10 时抽样集为空 | "快速验证模式"没有任何质量检查 |
| 5 | **遥测库被测试写入** | 23 次测试调用写入 69 条假"冷读失败" | 生产遥测不可信 |
| 6 | **两张价格表仍是 DeepSeek 价** | `job_store.py:207`、`stats.py:53-57` | 成本数字全错；后者还区分**已不存在的 flash/pro 两档** |
| 7 | **健康检查只验废弃变量** | `health.py:121-129` 的 `critical_vars` 只有 `DEEPSEEK_API_KEY`；**GLM/Qwen 的 key 一个都不验** | **部署假安全感** |
| 8 | **`write_node` 有 27 个存活变异体** | 在我本次未触碰的代码里 | 见第五节的作废说明 |
| 9 | **`_quality.json` 的 `output_len` 在分割章节上不可靠** | `run_novel.py:254` 记 `len(tt)`，长章被切段时只记到最后一段 | 记录字段本身会误导 |
| 10 | **交付文件章标题前缀不统一** | `## Chapter N:` vs `## Ch N:` | 下游解析器会静默漏整本书 |
| 11 | **`check_source.py`（仓库根）仍断头** | `from src.encoding import detect_and_read` | 一跑就崩 |
| 12 | **`scripts/run_one_segment.py` 一跑就崩** | `UnboundLocalError: local variable 'glossary'` | 收尾打印引用了未赋值变量 |

---

## 七、给下一位接手者的补充铁律

原有三条铁律（跑变异、硬编码字面量、等价变异要证明）**继续有效**。增加四条：

1. **【新增】变异工具改动后必须验证"变异真的生效"**
   至少抽查：变异后的目标行**确实变了**。
   **本次之所以能发现字节偏移 bug，就是因为"存活变异体"看起来不合逻辑而去手工复现。**

2. **【新增】变异测试必须串行**
   变异脚本**原地改写源文件**。**不要和 pytest、编辑、其他变异并发**——
   本次审计中因此产生了 1 次假失败、1 次假 SyntaxError、1 次编辑被冲掉。
   **推荐：变异只跑一次，输出重定向到文件，之后 grep 文件。**

3. **【新增】工具的 SKIP / STALE 必须计入失败**
   陈旧条目 = 没有验证，但看起来是绿的。

4. **【新增】"报告完成"必须有对应的实测证据**
   本次实测到"50% 交付报 100%"。**任何"完成"字样都要能追溯到实际写出的产物。**

---

## 八、下一次接手应当先做的三件事

1. **重跑全部变异 target**——因为偏位 bug 影响所有既有数字。约 657 个变异体，按本次实测的速率估约 2–4 小时。
2. **修第六节的 1、2、3 三条**（进度真实化、有条件 `complete_job`、标题零中文）——它们决定"产出报告能不能信"。
3. **把全量扫描接成交付门禁**——`scan_deliverable.py` 的思路（全量、零成本、秒级）替代 2.6% 抽检。
   **实测它能抓到 2.6% 抽样漏掉的缺陷章。**

---

**本文件由一次独立审计产生，不是本次开发会话的自述。**
**其中"不可信声明"与"工具链缺陷"两节，是对此前交接文档的更正，请优先采信。**
