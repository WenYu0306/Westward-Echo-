# 西渡「文化保真度」落地计划

> 2026-09-01 起草。性质：产品定义 → 工程落地的一步。
> 结论来源：地府 `聋婆婆 → Lóng Pópo` 生产事故，暴露"文化保真度"这一层从未被西渡显式设计，全靠模型自觉，换模型（DeepSeek→Qwen）即漂移。

---

## 一、问题一句话

西渡的"文化编译质量"分两层：**可读性**（冷读已验）和**一致性**（术语库已管），但最核心的**文化保真度**（文化含义有没有编译过去）——没定义、没规则、没校验，全靠模型自觉。

## 二、边界（已定，勿越界）

- **西渡只做文本层的文化编译**，不做叙事层改编（爽点/节奏归铸文或当地）。
- 本计划只补"文本层文化保真度"，不碰叙事层。

## 三、现有架构盘点（改之前先看清）

| 层 | 现状 | 缺口 |
|----|------|------|
| 规则层 | `cultural_rules.json` 只有**术语级**映射（`common`/`genres` → `{term: {target, note}}`），如"打脸→face-slap" | 缺**策略级**规则（"人名带谐音必须意译"这类原则） |
| 注入层 | 规则只进 READ（`read.py` 的 `cultural_rules_table`），WRITE 只有 `LANGUAGE_STYLE_NOTES` | 文化保真度规则没进任何节点 |
| 校验层 | 四节点：READ→WRITE→READBACK→FIX。READBACK 是**盲评**，验不了"文化含义丢了没" | 从 READ 到 WRITE，"文化缺口补没补上"无人回检 |
| 固化层 | `_post_process` 把 `new_terms` 写进 exact/semantic store | 第 1 章的术语决策靠模型自觉，写进去的可能是错的（音译） |

## 四、方案：四件事，全落在现有架构上

### 1. 规则层 —— `cultural_rules.json` 加 `fidelity` 分区

新增顶层 key `fidelity`，**策略级**规则（区别于现有的术语级 `common`/`genres`）。八类：

```
fidelity:
  en-US:                      # 按 target_lang 分，跟 common/genres 一致
    character_names:          # ① 人名
    terms_of_address:         # ② 称谓
    worldview_terms:          # ③ 世界观术语
    cultural_practices:       # ④ 习俗场景
    idioms_allusions:         # ⑤ 典故成语俗语
    wordplay:                 # ⑥ 语言游戏
    implicit_values:          # ⑦ 隐含价值观
    symbolism:                # ⑧ 象征
```

每一类结构：`{ "rule": "...", "examples": [{cn, why, do}, ...] }`（正反例，让指令型模型能照做）。

配套改 `src/cultural_rules.py`：
- 新增 `load_fidelity_rules(target_lang)` —— 读 `fidelity` 分区
- 新增 `format_fidelity_for_prompt(rules)` —— 格式化成可注入 prompt 的文本

### 2. 注入层 —— 规则进 READ（决策点），WRITE 只加精简兜底

- **READ**（`read.py`）：`read_user` 新增 `{fidelity_rules}` placeholder，注入完整八类规则。因为 READ 是术语决策点（决定人名怎么译）。
- **WRITE**（`write.py`）：只注入**精简版**（3-5 条最高频原则：人名/称谓/文化梗），不重复全部八类——省 token，且 WRITE 主要执行 READ 的决策。

> 为什么 WRITE 不全注入：成本。规则只在决策点（READ）完整展开，执行点（WRITE）只兜底。

### 3. 校验层 —— 加"文化保真度回检"（规则校验，不用 LLM）

位置：WRITE 之后。逻辑：对照 READ 的 `terminology_decisions` + `cultural_gaps`，检查 WRITE 译文**有没有真的执行**：

- READ 决策"聋婆婆 → Deaf Granny"，但 WRITE 输出里出现 `Lóng Pópo` → 标记**保真度失败**；
- READ 标了 critical 的文化缺口，WRITE 输出里找不到对应交代 → 标记。

**实现用规则校验，不用 LLM**（成本敏感）。校验结果进 `quality_issues` 或日志，先做到"能发现、能诊断"，后续再考虑"触发 FIX"。

落点：`src/agent/graph.py` 的 `_post_process`，或 `write.py` 返回前。倾向 `_post_process`（不改 graph 结构）。

### 4. 固化层 —— 第 1 章术语决策经约束+校验后才进术语库

`_post_process` 里 `new_terms` 写进 exact_store 前，加门槛：

- 术语的 `proposed_en` 来自 READ 决策（已有，`terminology_decisions`）；
- 只有当回检（第 3 步）确认 WRITE 真的用了这个译法，才写入术语库；
- 若 WRITE 没执行 READ 决策（漂移了），**不固化**，并记日志。

这样术语库里存的是"被验证过的译法"，不是"模型碰巧的译法"。

---

## 五、测试计划（铁律：改新代码必须写新测试）

对应 `tests/`，新增/扩展：

1. `test_cultural_rules.py` —— `load_fidelity_rules` 能读 `fidelity` 分区、格式正确、未知 lang 回退。
2. `test_fidelity_injection.py` —— READ/WRITE 的 prompt 里真的带了 fidelity 规则文本。
3. `test_fidelity_check.py` —— 回检逻辑：READ 决策意译但 WRITE 音译 → 标记失败；决策正确执行 → 通过。
4. `test_fidelity_gate.py` —— 术语固化门槛：漂移的术语不写入，验证过的才写入。

用 `/opt/homebrew/bin/python3.11 -m pytest tests/ -q` 跑（铁律：不用系统 python3.9）。

---

## 六、分阶段实施

| 阶段 | 内容 | 依赖 | 测试 | 状态 |
|------|------|------|------|------|
| P0 | 规则层：`fidelity` 分区 + `cultural_rules.py` 两个新函数 | 无 | test_cultural_rules | ✅ 完成 |
| P1 | 注入层：READ 全量 + WRITE 精简 | P0 | test_fidelity_injection | ✅ 完成 |
| P2 | 校验层：回检逻辑 | P1 | test_fidelity_check | ✅ 完成 |
| P3 | 固化层：术语库写入门槛 | P2 | test_fidelity_gate | ✅ 完成 |

**P0-P3 全部完成（2026-09-01）。** 新增 57 个测试全绿。

### 实现落点

- `cultural_rules.json` + `fidelity` 分区（8 类策略级规则，en-US）
- `src/cultural_rules.py` + `load_fidelity_rules()` / `format_fidelity_for_prompt()`
- `src/agent/fidelity.py`（新）+ `check_cultural_fidelity()`（规则回检，不用 LLM）
- `src/agent/prompts/{read,script_read}.py` + `{fidelity_rules}` placeholder（READ 全量）
- `src/agent/prompts/{write,script_write}.py` + `{fidelity_rules}` placeholder（WRITE 精简）
- `src/agent/nodes/read.py` / `write.py` — 加载并注入 fidelity 规则
- `src/agent/graph.py` — `_post_process` 加回检 + fidelity gate

---

## 七、风险与注意

- **成本**：fidelity 规则注入会增加 READ 的 prompt token。八类规则要控制篇幅（每条 rule 一两句 + 2-3 个例子），别写成论文。
- **不过度固化**：规则给"原则 + 例子"，不给"死映射"（死映射是术语库 `common` 的活）。文化判断需要弹性。
- **先只做 en-US**：其他语言（es-ES/de/fr）后续补，避免一次铺太开。
- **目标市场维度**（印尼 vs 美国）不在本计划内，那是另一个正交的坑，单独立项。
