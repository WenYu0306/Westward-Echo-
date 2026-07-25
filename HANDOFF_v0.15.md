# Westward Echo v0.15 — Project Handoff

**日期**: 2026-07-25
**提交**: 8 个 commits（从 v0.12.0 到 v0.15）
**目标**: 中国网文→英语的多 Agent 翻译引擎

---

## 一、项目在干什么

把中文网络小说翻译成英语。不是简单翻译——是用多 Agent（READ→WRITE→READBACK→FIX）的读者视角来翻。目标读者是没接触过中国文化的美国普通人。

## 二、架构

### 四节点流水线

```
START → READ → WRITE → READBACK → (NEEDS_FIX?) → FIX → READBACK (loop)
                                              ↓ (PASS)
                                             END
```

| 节点 | 身份 | 干什么 | 模型 |
|------|------|--------|------|
| READ | 中文网文读者 | 读中文章节，分析文化差距+画面差距+术语策略 | DeepSeek V4 Pro |
| WRITE | 英语类型小说作家 | 用英语重新讲这个故事（不是翻译） | DeepSeek V4 Pro（可切 Flash） |
| READBACK | 冷读者（美国普通人） | 不知道这是翻译，只读英语输出，报告体验 | DeepSeek V4 Pro |
| FIX | 编辑 | 根据冷读者反馈修复具体问题 | DeepSeek V4 Pro |

### 数据层（保持不变，从 v0.12 继承）

- `exact_store` (dict + SQLite) — 精确术语表，O(1) 查找
- `semantic_store` (Chroma + ONXX) — 语义术语检索
- `cultural_rules.json` — 726 行手写文化映射（不再作为权威，降级为参考）
- 9 种信号检测器（方言、成语、拟声词、度量、敏感词等）— 保留，作为 READ 的输入信号

### v0.15 新加的

**感官图片规则 (Sensory Image Gaps)**

核心洞察：中文读者读到"鬼节"、"出马弟子"、"四梁八柱"时，脑子里面自动浮现一幅完整的画面——不是因为他们阅读能力强，而是因为他们跟作者共享同一个文化画面库（香火味、神龛、黑绳子、月光下的雪地）。英文读者没有这个库，读到的是一个抽象标签。

READ agent 的任务：标记出每个中文读者能自然"看到"的图片，附带"感官锚词"——普遍人类感官素材（冻肉、霜、冰、未融雪的寂静）——WRITE agent 用这些素材重建画面。

验证过的效果：地府 v0.14→v0.15 的画面改善被两个独立冷读者确认。

**翻译风格备忘录 (Style Memo)**

六个抽屉的知识库（characters.md, pacing.md, bridges.md, prose.md, terms.md），每章结束后追加经验。第 200 章的翻译不应该从零开始——它应该能看到第 3、15、87 章的教训。验证了吗？没有——还没跑过完整长篇小说。

**快速/完整双模式**

非采样章：READ(Pro) + WRITE(Flash) — 跳过 READBACK
采样章：READ(Pro) + WRITE(Pro) + READBACK(Pro) — 全流程

减少不必要的冷读调用，降低速度和成本 50%。

## 三、已证实的东西

### 确凿的

1. **Ch1 空输出 bug 已修复** — 删掉 MCP tool call 机制后，第 1 章不再产出 0 字符
2. **感官规则制造了画面** — 地府 v0.15 的第三方审计明确说"看见了"（尸体倒下、呼吸蒸汽对比、candle flames），之前的版本这些画面不存在
3. **README 的修复速度** — 说明段的 image gaps + 文本展开部分（Four Pillars 的纯文化说明段毁掉了 pace）审计把这些问题明确指了出来
4. **端到端管道工作** — 4 个节点全部调用成功，graph 路由正确、state 传递无误、skip_readback/use_flash_writer 全链路贯通
5. **风格备忘录有写入** — `update_from_feedback` 在冷读反馈真实运行时能正确提取规则并填入抽屉文件

### 部分证实

6. **跨类型泛化** — 无限恐怖的第一章审计员评分还行（PASS），但只有 2 章数据、没有长距离验证

## 四、未证实的东西（诚实说）

### 最大的未知

**长距离质量稳定性** — 翻译了 13 章无限恐怖就停了（沙盒超时+API hang），从来没跑过 500+ 章。章节 500 的角色声音会不会漂移、术语是否始终一致、信息堆砌是会改善还是每一章都犯相同的错——全部未验证。

**风格备忘录的有效性** — 抽屉设计做好了、写入逻辑验证了，但从没把它注入回 READ/WRITE 的 prompt 里跑过完整实验。"第 200 章比第 10 章更好"——从未测试。

**跨小说知识传递** — 一整本都没跑完，两本的对比验证完全是空白。

**API 稳定性（新发现的 bug）** — 诊断测试证明 DeepSeek V4 在 `max_tokens=16384` 下试图生成大量输出时会永久 hang（92 秒超时）。`max_tokens` 已临时降到 8192，但这只是绕过问题——不是根因分析。

## 五、已知的未解决

| 问题 | 优先级 | 状态 |
|------|--------|------|
| DeepSeek V4 在大输出下 hang | 严重 | `max_tokens` 降到 8192 临时绕过——未解决根因 |
| 说明段仍被审计员指为"wiki 段落" | 高 | READ prompt 已加固——未重新验证 |
| Uncle Li 是透明人 | 高 | WRITE prompt 加了角色独特性规则——未重新验证 |
| 第二章双开口（语调分裂） | 中 | 出版编辑在上一轮指出的——部分修复但是**未重新验证** |
| `run_one_segment.py` 断点机制 | 好 | 设计正确但只配合 15 章段 |


## 六、代码组织

**核心改动文件（全部在 v0.15 commit 中）**

```
src/agent/graph.py          # 4-node 图 + TranslationAgent（140 行新增/修改）
src/agent/state.py            # 加了 image_gaps, style_memo, skip_readback, use_flash_writer
src/agent/nodes/read.py       # NEW: READ agent — 图画差距检测
src/agent/nodes/write.py      # NEW: WRITE agent — 感官翻译规则
src/agent/nodes/readback.py   # NEW: READBACK cold reader
src/agent/nodes/fix.py        # NEW: FIX editor
src/agent/prompts/read.py     # READ system+user prompt
src/agent/prompts/write.py    # WRITE system+user prompt
src/agent/prompts/readback.py # READBACK prompt
src/agent/prompts/fix.py      # FIX prompt
src/agent/prompts/translation.py # 保留：LANGUAGE_STYLE_NOTES
src/style_memo.py             # NEW: 6-drawer translation style memo
src/config.py                 # MODEL_MAP 全部改为 Pro
```

**已删除（v0.12 的旧 6-node 架构）**

```
src/agent/nodes/fetch_glossary.py
src/agent/nodes/translate.py
src/agent/nodes/update_glossary.py
src/agent/nodes/arbitrate_terms.py
src/agent/nodes/quality_check.py
src/agent/nodes/polish.py
src/agent/prompts/term_validation.py
src/agent/prompts/term_arbitration.py
src/agent/prompts/term_extraction.py
src/agent/prompts/quality_check.py
src/agent/prompts/polish.py
src/tools.py                  # MCP tool call mechanism
```

**运行脚本**

| 脚本 | 用途 |
|------|------|
| `run_one_segment.py` | 全量翻译:15章一段、自动续、断点恢复 |
| `audit_translate.py` | 审计翻译:15章+4个采样点+冷读 |
| `make_ckpt.py` | 从已有译文重建断点（用于程序挂了） |
| `audit_e2e.py` | 端到端测试（不翻译所有章节） |
| `ab_test.py` | Flash vs Pro A/B 测试 |
| `diag_api_hang.py` | API 挂起的 6 步诊断 |

**测试小说（在 `tests/fixtures/`）**

```
地府叫我小先生 — 2301章 (folk_religion)
无限恐怖 — 775章 (urban/infinite flow)
吞噬星空 — 1487章 (scifi)
我有一座恐怖屋 — 1214章 (modern horror)
全职高手 — ~1700章 (esports)
唐朝工科生 — ~1000章 (historical)
覆汉 — ~900章 (historical)
间客 — ~800章 (scifi)
```

## 七、如何运行

**前置条件**：DeepSeek API key 在 `.env` 里。

**完整翻译一本小说**（终端里运行，不用沙盒）：

```bash
cd "/Users/wenyudemac/Documents/dev/Westward Echo（西渡）"
rm -rf novels/output/limitless_horror_segmented   # 清旧数据
python3 scripts/run_one_segment.py
```

15 章一段，段段自动续。Ctrl+C 安全退出，重跑同一个命令从断点恢复。质量采样点（每 50 章左右）自动运行冷读，结果存到 `_quality.json`。

**审计翻译（15章 + 第三方冷读）**：

```bash
python3 scripts/audit_translate.py
```

翻译 15 章，在章节 1/5/10/15 跑完整冷读，保存到 `novels/output/audit_15/quality.json`。

## 八、成本

**全 Pro + Flash bulk 模式**（采样章 Pro WRITE，普通章 Flash WRITE）：

- 地府 2301 章：~$18（Pro bulk: $32）
- 无限恐怖 775 章：~$6

## 九、下一步（建议）

1. **先把 DeepSeek API 挂起的根因弄清楚** — 诊断已经证明问题在 `max_tokens=16384` 上模型无法生成完整响应，但 `max_tokens=8192` 只是绕过
2. **跑 15 章审计翻译** — 用最新修复（说明段加固 + 角色约束）过一遍第三方冷读，看分数是否从 6/10 涨了
3. **跑 775 章完整翻译** — 从自己终端跑 `run_one_segment.py`，验证长距离稳定性
4. **A/B 比对 200 章前后的质量** — 看看备忘录积累了多少真实收益

---

## 附：v0.12 → v0.15 完整的 commit 链

```
7d484df fix: reduce max_tokens from 16384 to 8192
bfa434a fix: add 120s request_timeout to all ChatOpenAI calls
c3b2444 fix: harden expository image gaps + enforce character distinctiveness
c2eefdc fix: audit — checkpoint recovery, cleanup, production runner
b055e05 refactor: v0.15 reader-centric pipeline with sensory image gaps and style memo
```
