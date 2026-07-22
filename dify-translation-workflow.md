# Dify 网文出海文化适配翻译 Agent 工作流设计方案

> 目标：中文网文 → 英文译本，术语全本一致，美国市场文化适配
> 技术栈：Dify Workflow（纯配置，零代码）
> 扩展：架构预留西班牙语、阿拉伯语接口

---

## 一、总体架构

```
┌──────────────┐    ┌──────────────────┐    ┌──────────────┐
│  预处理阶段   │───▶│  Workflow A      │───▶│  Google Sheets │
│ (按章切分txt) │    │  术语表初始化     │    │  术语库        │
└──────────────┘    └──────────────────┘    └──────┬───────┘
                                                   │
┌──────────────┐    ┌──────────────────┐           │
│  逐章翻译     │◀───│  Workflow B      │◀──────────┘
│  (循环执行)   │───▶│  逐章翻译         │───▶ 术语库(更新)
└──────────────┘    └──────────────────┘
                           │
                    ┌──────▼───────┐
                    │  Workflow C  │
                    │  质量抽检     │
                    └──────────────┘
```

**三个独立 Workflow，通过 Google Sheets 共享术语表：**
- **Workflow A**：术语表初始化（首次运行，处理前10章提取术语）
- **Workflow B**：逐章翻译（每章运行一次，核心工作流）
- **Workflow C**：反向回译质量抽检（每20章抽检一次）

---

## 二、分块处理方案

### 2.1 为什么按"章"切分而非按"字数"切分

| 方案 | 优点 | 缺点 |
|------|------|------|
| 按字数切分（每3000字一块） | 均匀负载 | 切断叙事流、上下文断裂、术语跨块重复 |
| **按章节切分（推荐）** | 自然边界、上下文完整、输出复用章节结构 | 章节长短不一 |

网文章节通常 2000-5000 字，恰好在 GPT-4/Claude 舒适窗口内（输出英文约 1500-3500 words）。选择**按章切分**。

### 2.2 预处理步骤（Dify 外部，一次性操作）

```
输入：一本完整中文网文 .txt
      ↓
步骤1：用任意文本编辑器按正则 "第[一二三四五六七八九十百千0-9]+[章节回]" 自动切分
      ↓
步骤2：命名规则 chapter_{序号}_{章节标题}.txt
      例：chapter_001_我在八零年代当后妈.txt
      ↓
步骤3：上传到 Google Drive / 本地文件夹，作为 Workflow B 的输入源
```

> **Dify 内不需要做切分。** 如果非要在 Dify 内切分，可以在 Workflow B 的 Start 节点前加一个 **LLM 节点**，用 Structured Output 识别章节边界并拆分为数组，再接入 Iteration 节点逐章处理。但不推荐（一本 1000 章的书单次 Workflow 运行会超时）。

### 2.3 执行策略

```
第 1 步：运行 Workflow A（1次）
   输入：前 10 章合并文本
   输出：初始术语表 → 写入 Google Sheets

第 2 步：循环运行 Workflow B（每章 1 次，共 N 次）
   每次输入：单个章节文本 + 章节编号
   每次输出：英文译文章节 + 新术语 → 追加到 Google Sheets

第 3 步：每 20 章运行一次 Workflow C（N/20 次）
   输入：随机抽取 3 段译文
   输出：质量报告
```

---

## 三、术语表维护机制

### 3.1 为什么不用 Dify Knowledge Base 作为主存储

- Dify KB 在 Workflow 中**只能检索（读）**，不能写入
- 术语需要在翻译过程中**持续积累和更新**（写）
- 结论：KB 只能做辅助检索，**主存储用 Google Sheets**

### 3.2 Google Sheets 术语库设计

**Sheet 名称：** `Novel_Glossary`

| 列名 | 说明 | 示例 |
|------|------|------|
| `term_cn` | 中文原词 | 八零年代 |
| `term_en` | 英文翻译 | 80s rural America |
| `category` | 分类 | era / character / location / technique / culture / item |
| `context` | 上下文（原文句子） | "我一睁眼就回到了八零年代" |
| `chapter_first_seen` | 首次出现章节 | 1 |
| `status` | 状态 | confirmed / pending_review |
| `target_lang` | 目标语言 | en-US |

### 3.3 Google Apps Script 部署（一次性配置，10行代码）

在 Google Sheet 中打开 **扩展程序 > Apps Script**，粘贴以下脚本，部署为 Web 应用：

```javascript
// 无需修改，直接部署即可
function doGet(e) {
  const sheet = SpreadsheetApp.getActiveSheet();
  const data = sheet.getDataRange().getValues();
  // 按目标语言过滤
  const lang = e.parameter.lang || 'en-US';
  const rows = data.filter((r, i) => i === 0 || r[6] === lang);
  return ContentService.createTextOutput(JSON.stringify(rows))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  const sheet = SpreadsheetApp.getActiveSheet();
  const body = JSON.parse(e.postData.contents);
  // body = { terms: [{term_cn, term_en, category, context, chapter_first_seen, target_lang}] }
  body.terms.forEach(t => {
    // 去重：如果 term_cn 已存在且 status=confirmed，跳过
    const data = sheet.getDataRange().getValues();
    const exists = data.find(r => r[0] === t.term_cn && r[5] === 'confirmed');
    if (!exists) {
      sheet.appendRow([t.term_cn, t.term_en, t.category, t.context, t.chapter_first_seen, 'pending_review', t.target_lang]);
    }
  });
  return ContentService.createTextOutput(JSON.stringify({ success: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
```

部署后获得一个 URL：`https://script.google.com/macros/s/xxxxx/exec`

### 3.4 术语表闭环流程

```
┌──────────────────────────────────────────────────────┐
│                                                      │
│  ① 翻译前：HTTP Request GET → 拉取全量术语表          │
│       ↓                                              │
│  ② LLM Prompt 中注入术语表作为强制约束                 │
│       ↓                                              │
│  ③ LLM 翻译，输出译文 + 新发现的术语列表               │
│       ↓                                              │
│  ④ HTTP Request POST → 新术语写入 Google Sheets       │
│       ↓                                              │
│  ⑤ （可选）每50章人工审核一次 pending_review 术语       │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### 3.5 Dify Knowledge Base 的辅助角色（可选）

如果术语表超过 500 条（长篇小说），单次 Prompt 注入所有术语会撑爆上下文。此时可：

1. 定期将 Google Sheets 中 `status=confirmed` 的术语导出为 CSV
2. 上传到 Dify Knowledge Base
3. 在 Workflow B 的翻译节点前加 **Knowledge Retrieval 节点**，用当前章节原文做语义检索，只召回相关术语

> 小说明：术语少于 300 条时，直接全量注入 Prompt 更简单可靠，不需要 KB。

---

## 四、工作流节点配置

### 4.1 Workflow A：术语表初始化

**触发时机：** 首次翻译一本新书时运行一次

**节点连接顺序：**

```
┌──────────┐
│  START   │  input: novel_first_10_chapters (string)
└────┬─────┘
     │
┌────▼─────┐
│  LLM ①   │  术语提取节点
│  模型: GPT-4o / Claude Opus
│  温度: 0.1 (低温度保证稳定)
│  Prompt: 见第五节 Prompt-A
│  Structured Output: JSON
│  { terms: [{term_cn, term_en, category, context}] }
└────┬─────┘
     │
┌────▼─────┐
│  LLM ②   │  术语审核/去重节点
│  模型: GPT-4o-mini (轻量任务)
│  Prompt: 检查①的输出，合并重复项，标记不确定的为 pending_review
│  Structured Output: JSON
└────┬─────┘
     │
┌────▼──────┐
│ HTTP POST │  写入 Google Sheets
│  URL: https://script.google.com/macros/s/xxxxx/exec
│  Method: POST
│  Body: { terms: [{{LLM②.output.terms}}] }
└────┬──────┘
     │
┌────▼─────┐
│   END    │  output: glossary_summary (string), term_count (number)
└──────────┘
```

**Structured Output JSON Schema（LLM ①）：**

```json
{
  "type": "object",
  "properties": {
    "terms": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "term_cn": { "type": "string", "description": "中文原词" },
          "term_en": { "type": "string", "description": "建议英文翻译" },
          "category": { "type": "string", "enum": ["character", "location", "technique", "culture", "item", "era"] },
          "context": { "type": "string", "description": "该词首次出现的原文句子" },
          "note": { "type": "string", "description": "翻译理由/文化适配说明" }
        },
        "required": ["term_cn", "term_en", "category"]
      }
    }
  },
  "required": ["terms"]
}
```

---

### 4.2 Workflow B：逐章翻译（核心工作流）

**触发时机：** 每章运行一次

**节点连接顺序：**

```
┌──────────┐
│  START   │
│  inputs: │
│   · chapter_text (string)          ← 当前章节原文
│   · chapter_number (number)        ← 章节编号
│   · target_lang (string, "en-US")  ← 目标语言
└────┬─────┘
     │
┌────▼──────┐
│ HTTP GET  │  拉取术语表
│  URL: https://script.google.com/macros/s/xxxxx/exec?lang={{target_lang}}
│  Method: GET
│  Output: glossary_json (原始术语表数组)
└────┬──────┘
     │
┌────▼──────┐
│  TEMPLATE │  术语表格式化
│  将 glossary_json 转为可注入 Prompt 的文本
│  模板：
│  | {{term_cn}} | {{term_en}} | {{category}} |
│  输出: glossary_text (string)
└────┬─────┘
     │     (可选，术语>300条时启用)
     │    ┌──────────────────┐
     ├───▶│ Knowledge Retrieval│
     │    │ 用 chapter_text   │
     │    │ 语义检索 KB 中术语  │
     │    │ Top K = 20        │
     │    └────────┬─────────┘
     │            │ relevant_terms
     │            │
┌────▼────────────▼─────┐
│        LLM ①          │  翻译节点（核心）
│  模型: GPT-4o / Claude Opus
│  温度: 0.2
│  System Prompt: 见第五节 Prompt-B
│  User Prompt: 
│    当前章节原文: {{chapter_text}}
│    术语表: {{glossary_text}}
│    相关术语(来自KB): {{relevant_terms}}
│  Structured Output: JSON
│  { translated_text, new_terms_found[], 
│    cultural_adaptation_notes[] }
└────┬──────────────────┘
     │
┌────▼──────┐
│ IF/ELSE   │  判断是否有新术语
│  条件: {{LLM①.output.new_terms_found.length}} > 0
└──┬───┬───┘
   │   │ (为空则跳过)
   │   └──▶ 跳过写入
   │
┌──▼────────┐
│ HTTP POST │  写入新术语到 Google Sheets
│  URL: https://script.google.com/macros/s/xxxxx/exec
│  Body: { terms: [{{LLM①.output.new_terms_found}}] }
└──┬────────┘
   │
┌──▼────────┐
│  TEMPLATE │  格式化最终输出
│  模板：
│  # Chapter {{chapter_number}}
│  {{LLM①.output.translated_text}}
│  ---
│  <!-- glossary_updated: {{new_term_count}} terms -->
└──┬────────┘
   │
┌──▼────┐
│  END  │
│  outputs:
│   · translated_chapter (string)         ← 英文译文章节
│   · new_terms_count (number)            ← 本章新增术语数
│   · adaptation_notes (string)           ← 文化适配说明
└───────┘
```

---

### 4.3 Workflow C：反向回译质量抽检

**触发时机：** 每翻译完 20 章运行一次

**节点连接顺序：**

```
┌──────────┐
│  START   │
│  inputs: │
│   · sample_passages (array[string])  ← 随机抽3段译文
│   · original_cn (array[string])      ← 对应的中文原文
│   · chapter_numbers (array[number])
└────┬─────┘
     │
┌────▼──────┐
│ ITERATION │  遍历每段译文
│  输入数组: sample_passages
└────┬─────┘
     │ (对每个 sample)
     │
┌────▼──────┐
│   LLM ①   │  反向回译节点
│  模型: GPT-4o-mini (轻量，成本低)
│  温度: 0.0
│  将英文译文反向翻译为中文
│  Structured Output: { back_translated_cn: string }
└────┬─────┘
     │
┌────▼──────┐
│   LLM ②   │  对比评估节点
│  模型: GPT-4o / Claude Opus
│  System Prompt: 见第五节 Prompt-D
│  User Prompt:
│    中文原文: {{original_cn[INDEX]}}
│    英文译文: {{sample_passages[INDEX]}}
│    反向回译: {{LLM①.output.back_translated_cn}}
│  Structured Output:
│  { score: 1-5, issues: [], term_consistency_check: bool }
└────┬─────┘
     │
┌────▼──────┐
│ AGGREGATE │  汇总评估结果
│  计算平均分、汇总问题列表
└────┬─────┘
     │
┌────▼──────┐
│ IF/ELSE   │  质量门禁
│  条件: average_score < 3.5
│  → 低分: 标记章节需重译
│  → 高分: 通过
└──┬───┬───┘
   │   │
   ▼   ▼
┌──────┐
│ END  │
│ outputs: quality_report
└──────┘
```

---

## 五、Prompt 模板

### Prompt-A：术语提取节点（Workflow A - LLM ①）

```
## SYSTEM

You are a terminology extraction specialist for Chinese-to-English web novel translation. Your task is to scan Chinese web novel chapters and identify ALL proper nouns, culturally specific terms, and recurring expressions that need consistent translation.

## EXTRACTION RULES

For each term, identify it as one of:
- **character**: Person names, nicknames, titles (e.g. 龙傲天, 白莲花, 霸总)
- **location**: Place names, realms, sects (e.g. 青云山, 魔教总坛, 九天大陆)  
- **technique**: Martial arts, cultivation methods, spells (e.g. 九阴真经, 金丹期, 御剑术)
- **culture**: Era-specific terms, idioms, customs (e.g. 八零年代, 下海, 铁饭碗, 修真)
- **item**: Magical artifacts, special objects (e.g. 储物袋, 筑基丹)
- **era**: Time periods, dynasties, historical markers

## CULTURAL ADAPTATION GUIDELINES (for en-US market)

When suggesting English translations, prioritize American reader comprehension:
- 八零年代 → "80s rural America" (NOT literal "the 1980s" — convey the socio-economic vibe)
- 霸总 → "Alpha CEO" (NOT "overbearing president" — map to familiar US archetype)
- 修真 → "Cultivation" (established convention in the xianxia genre)
- 修仙 → "Immortal Cultivation" 
- 金丹/元婴 → "Golden Core / Nascent Soul" (keep Chinese fantasy flavor)
- 门派 → "Sect" (not "school" or "faction" — matches fantasy genre convention)
- 师兄/师姐 → Use names or "senior brother/sister" (keep hierarchy flavor)
- 穿越 → "Transmigration" (established genre term)
- 系统 → "System" (capitalized, established LitRPG convention)
- 打脸 → "Face-slapping" (established webnovel convention)
- 丹田 → "Dantian" (keep untranslated, explain once in first occurrence)

## OUTPUT FORMAT

Extract every term that appears in the text. If a term has multiple possible translations, pick ONE and note alternatives in the "note" field. Group by category.

---

## USER

Extract all proper nouns, culturally specific terms, and recurring expressions from the following Chinese web novel chapters. Output as structured JSON.

{{novel_first_10_chapters}}
```

### Prompt-B：翻译节点（Workflow B - LLM ① 核心）

```
## SYSTEM

You are a professional Chinese-to-English web novel translator specializing in cultural adaptation for the American market. You translate Chinese web novels (网文) into natural, engaging English that American readers will love.

## CORE PRINCIPLES

### 1. Glossary First (术语优先)
You MUST use the provided glossary for ALL term translations. If a term is in the glossary, use the glossary translation exactly — no variation. Consistency across all chapters is the #1 priority.

### 2. Two-Pass Translation (两遍翻译法)
- **Pass 1 — Literal**: Understand the exact meaning of the Chinese text. Capture every detail.
- **Pass 2 — Adaptation**: Rewrite for an American reader. Convert Chinese idioms to American equivalents. Adjust cultural references. Make it read like it was originally written in English.

### 3. Cultural Adaptation Rules (文化适配规则)

| 中文表达 | 直译 (don't use) | 适配翻译 (use this) |
|----------|-----------------|-------------------|
| 八零年代 | the 1980s | 80s rural America / small-town 80s |
| 霸总 | overbearing president | Alpha CEO / dominant CEO |
| 修仙 | cultivate immortality | Cultivation / Immortal Cultivation |
| 打脸 | hit face | face-slap / epic takedown |
| 装逼 | pretend | flex / show off |
| 龙傲天 | Long Aotian | the overpowered hero / the Chosen One |
| 白莲花 | white lotus | innocent act / goody-two-shoes (derogatory) |
| 玛丽苏 | Mary Sue | Mary Sue (keep — term is already English) |
| 吃瓜群众 | melon-eating masses | popcorn gallery / bystanders eating popcorn |
| 牛逼 | cow's vagina (literally) | badass / epic / legendary |
| 卧槽 | lie槽 | Holy shit / WTF / Damn |
| 社会摇 | social shake | street dance / hood shuffle |
| 修真世界 | cultivation world | the Cultivation World / the World of Cultivators |
| 飞升 | fly up / ascend | Ascension (capitalized, major milestone) |
| 渡劫 | cross tribulation | Tribulation / Heavenly Tribulation |

### 4. Style Guidelines
- Use **casual American English** for dialogue. Characters should sound like they're in a Netflix show, not a textbook.
- Keep **short paragraphs**. Web novels thrive on punchy, scannable prose. 2-4 sentences per paragraph.
- Preserve **cliffhangers**. If a chapter ends on a hook, make the English hook just as sharp.
- **Show, don't tell** emotions: "His jaw tightened" > "He was angry"
- Keep **action scenes fast**: Short sentences. Active voice. No elaborate descriptions mid-fight.
- For **comedic moments**: Use American humor cadence — setup, beat, punchline.
- **Profanity**: Match the Chinese intensity. If the original is vulgar, the English should be too.

### 5. Handling Untranslatable Terms
If you encounter a term NOT in the glossary:
1. Check if it's a proper noun → transliterate in Pinyin + brief inline explanation on first occurrence only
2. If it's a cultural concept → find the closest American equivalent
3. RECORD it in `new_terms_found` — never silently translate a new proper noun

### 6. Chapter Output Format
Output the translated chapter in this EXACT format:

## Chapter [number]
[English title if applicable]

[Translated body text — no markdown headers within the chapter body]

---
Translator's Notes (only if needed):
- [brief note about any major cultural adaptation decisions]

## GLOSSARY (术语表)
You are provided with the following mandatory term translations. Use them EXACTLY as shown:

{{glossary_text}}

## RELEVANT TERMS FROM KNOWLEDGE BASE
{{relevant_terms}}

## SOURCE TEXT
Translate the following chapter:

{{chapter_text}}

## OUTPUT
Provide the translation in the specified structured JSON format, including:
- `translated_text`: The complete translated chapter in English
- `new_terms_found`: Any new terms discovered in this chapter that are NOT in the provided glossary
- `cultural_adaptation_notes`: 2-3 bullet points explaining key adaptation decisions in this chapter
```

### Prompt-C：术语表更新节点（Workflow B - LLM ① 的 new_terms_found 部分）

> 这个 Prompt 内含在 Prompt-B 的 Structured Output 中，不需要单独节点。LLM 在翻译的同时输出 new_terms_found。但如果你希望用专门的审核节点，可以在 HTTP POST 前加一个 LLM：

```
## SYSTEM

You are a terminology quality checker. Review the new terms extracted from a translated chapter and validate them before adding to the master glossary.

## VALIDATION RULES

1. **Not in existing glossary**: Check against the provided glossary. If a term already exists, DO NOT add it.
2. **Is it a proper noun?** Only add terms that are names, places, techniques, or culturally specific. Generic words don't belong.
3. **Translation quality**: Is the English translation accurate and culturally appropriate? If unsure, mark `status: "pending_review"`.
4. **Consistency check**: If the same Chinese term appears with different translations across chapters, flag it.
5. **Category accuracy**: Verify the category assignment is correct.

## INPUT

Existing glossary (for dedup check):
{{glossary_text}}

New terms proposed from current chapter:
{{new_terms_from_translation}}

## OUTPUT

Return validated terms with corrections. Remove duplicates. Mark uncertain ones for human review.
```

### Prompt-D：反向回译测试节点（Workflow C - LLM ②）

```
## SYSTEM

You are a translation quality auditor for a Chinese→English web novel localization project. Your job is to back-translate English passages to Chinese and evaluate whether the original meaning, tone, and cultural nuance were preserved.

## EVALUATION FRAMEWORK

Score each passage on 5 dimensions (1-5 each):

### 1. Semantic Accuracy (语义准确度)
- 5: Perfect. All details preserved.
- 3: Minor omissions or additions that don't change the story.
- 1: Major plot points lost or fabricated.

### 2. Character Voice (角色声音一致性)  
- 5: Character "sounds" the same in English — personality, class, attitude intact.
- 3: Voice is flattened but directionally correct.
- 1: Character is unrecognizable (e.g. a gruff general sounds like a polite student).

### 3. Cultural Adaptation Quality (文化适配质量)
- 5: Natural American English with seamlessly adapted cultural references.
- 3: Readable but feels translated. Cultural references are explained rather than adapted.
- 1: Awkward Chinglish or completely wrong cultural mapping.

### 4. Terminology Consistency (术语一致性)
- 5: All proper nouns match the glossary exactly.
- 3: Minor variations in capitalization or hyphenation.
- 1: Glossary terms translated differently or missed entirely.

### 5. Readability (可读性)
- 5: Reads like native English web fiction. Smooth flow, natural dialogue.
- 3: Comprehensible but stilted. Reader would notice it's a translation.
- 1: Requires effort to understand.

## BACK-TRANSLATION RULES

When back-translating to Chinese:
- Produce NATURAL Chinese, not literal word-for-word
- This reveals what an English reader actually understood
- If the back-translation changes the original meaning, that's a red flag

## TERM GLOSSARY (for consistency check)

{{glossary_text}}

## EVALUATION INPUT

**Original Chinese (原文):**
{{original_cn}}

**English Translation (英译文):**
{{english_translation}}

## OUTPUT

Provide:
1. Back-translation to Chinese
2. Score for each dimension (1-5)
3. Overall score (average)
4. Specific issues found (if any)
5. Recommendation: PASS / FLAG_FOR_REVIEW / REJECT
```

---

## 六、输出格式规范

### 6.1 单章输出格式

每章翻译结果使用统一 Markdown 模板：

```markdown
# Chapter {chapter_number}: {chapter_title_en}
<!-- meta: original_chapter={chapter_number} | terms_count={n} | translated_at={timestamp} -->

[正文内容 — 纯英文，无 Markdown 标题]

---

<!-- END OF CHAPTER {chapter_number} -->
```

### 6.2 全本合并脚本

翻译全部章节后，用以下命令合并为单一文件（在终端执行，非 Dify）：

```bash
# 按章节编号排序合并
ls chapter_*.md | sort -V | xargs cat > full_novel_en.md

# 转换为 EPUB（需要安装 Pandoc）
pandoc full_novel_en.md -o full_novel_en.epub --metadata title="Full Novel EN"
```

### 6.3 术语表导出

定期从 Google Sheets 导出术语表为 CSV，作为翻译项目的交付物之一：

```
novel_name_glossary.csv
```

包含所有 confirmed 和 pending_review 的术语，供人工审核。

---

## 七、测试用例

**测试用书：** 《我在八零年代当后妈》
**测试范围：** 前 3 章

### 7.1 测试输入

**章节拆分后：**
- `chapter_001_穿越八零年代.txt`（约 2500 字）
- `chapter_002_初见孩子们.txt`（约 2800 字）
- `chapter_003_极品亲戚.txt`（约 2200 字）

### 7.2 Workflow A 预期输出（术语初始化，跑前3章合并文本）

```json
{
  "terms": [
    {
      "term_cn": "八零年代",
      "term_en": "80s rural America",
      "category": "era",
      "context": "我一睁眼，就回到了八零年代。",
      "note": "原文是1980s中国农村，美国市场适配为80s rural America以营造类似的时代感"
    },
    {
      "term_cn": "后妈",
      "term_en": "stepmother",
      "category": "culture",
      "context": "我竟然成了一个孩子们的后妈。",
      "note": "直译即可，英文中有完全对应的概念。注意上下文语气决定是stepmom还是stepmother"
    },
    {
      "term_cn": "生产队",
      "term_en": "the commune",
      "category": "culture",
      "context": "生产队的活今天必须干完。",
      "note": "不直译production brigade。美国读者对commune有直观理解（公社/集体农庄），语义接近"
    },
    {
      "term_cn": "知青",
      "term_en": "sent-down youth",
      "category": "culture",
      "context": "隔壁老李是个回城知青。",
      "note": "首次出现时保留' sent-down youth (the urban teenagers exiled to the countryside during the Cultural Revolution)'，后续简化为sent-down youth"
    },
    {
      "term_cn": "铁饭碗",
      "term_en": "ironclad job / job for life",
      "category": "culture",
      "context": "在供销社上班可是铁饭碗。",
      "note": "美国读者没有铁饭碗概念，用ironclad government job传达'稳定'含义"
    },
    {
      "term_cn": "供销社",
      "term_en": "the state-run supply store",
      "category": "culture",
      "context": "去供销社买点盐。",
      "note": "美国读者对state-run store有概念（类似post office的感觉），不需解释为co-op"
    },
    {
      "term_cn": "林小满",
      "term_en": "Lin Xiaoman",
      "category": "character",
      "context": "林小满，从今天起，你就是他们的后妈了。",
      "note": "女主名。音译，全本保持一致。首次出现可加注'full of promise'的名字含义"
    }
  ]
}
```

### 7.3 Workflow B 预期输出（第1章翻译）

**输入：** Chapter 1 原文 + 上述术语表

**输出（LLM① translated_text）：**

```markdown
# Chapter 1: I Woke Up in 80s Rural America

The first thing I noticed was the ceiling. Cracked plaster, yellowed with age, a single naked bulb hanging from a frayed wire. This was not my apartment. This was not even my decade.

I sat up too fast and my head swam. A flood of memories that weren't mine crashed through my skull — Lin Xiaoman, age 22, freshly married to a widower with two kids in some Podunk town nobody'd ever heard of. The previous owner of this body had been... well, let's just say she wasn't winning any popularity contests.

"Mom?"

A tiny voice. I turned. Two kids stood in the doorway — a boy, maybe five, with dirt on his cheek and suspicion in his eyes, and a girl, three at most, clutching his sleeve like he was the only solid thing in her world.

Oh, hell no.

I was a stepmother. In the 80s. In the middle of nowhere.

Somewhere, someone was having a great laugh at my expense.

---

<!-- END OF CHAPTER 1 -->
```

**LLM① new_terms_found：**

```json
[
  {
    "term_cn": "老王家的",
    "term_en": "Old Wang's place / the Wangs'",
    "category": "culture",
    "context": "去老王家的借点酱油。",
    "note": "中国农村用'老X家的'指代邻居，英文化为Old Wang's place传达同样随意的邻里感"
  },
  {
    "term_cn": "公分",
    "term_en": "work points",
    "category": "culture",
    "context": "今天挣了八个公分。",
    "note": "生产队时代的工分制，英文work points是标准翻译"
  }
]
```

### 7.4 Workflow C 预期输出（反向回译测试）

**输入：** 第1章随机3段 + 对应原文

**LLM② 评估输出（对其中1段）：**

```json
{
  "score": {
    "semantic_accuracy": 5,
    "character_voice": 4,
    "cultural_adaptation": 5,
    "terminology_consistency": 5,
    "readability": 5,
    "overall": 4.8
  },
  "back_translated_cn": "我第一眼注意到的是天花板。开裂的石灰墙，被岁月熏得发黄，一根光秃秃的灯泡吊在磨损的电线上。这不是我的公寓。这甚至不是我的年代。",
  "issues": [
    {
      "severity": "minor",
      "detail": "原文'林小满今年22岁'被改编为'Lin Xiaoman, age 22'，缺少了原文中略带幽默的自嘲语气。建议改为'Lin Xiaoman, twenty-two years young and already a mother of two'保留原文的讽刺感。"
    }
  ],
  "recommendation": "PASS"
}
```

### 7.5 第1-3章翻译后的术语表快照

| term_cn | term_en | category | chapter_first_seen |
|---------|---------|----------|-------------------|
| 八零年代 | 80s rural America | era | 1 |
| 后妈 | stepmother | culture | 1 |
| 生产队 | the commune | culture | 1 |
| 知青 | sent-down youth | culture | 1 |
| 铁饭碗 | ironclad government job | culture | 1 |
| 供销社 | state-run supply store | culture | 1 |
| 林小满 | Lin Xiaoman | character | 1 |
| 老王家的 | Old Wang's place | culture | 1 |
| 公分 | work points | culture | 1 |
| 极品亲戚 | the relatives from hell | culture | 3 |
| 二流子 | deadbeat / lowlife | culture | 3 |

---

## 八、多语言扩展方案

### 8.1 架构复用

当前设计已预留 `target_lang` 参数。扩展到西班牙语(es-ES)、阿拉伯语(ar-SA)时：

- **Google Sheets**：同一张表，`target_lang` 列区分不同语言的术语
- **Workflow A/B/C**：**零修改**，只改变 `target_lang` 输入参数
- **Prompt 模板**：需要翻译为对应语言版本（文化适配规则不同）

### 8.2 每种语言需要定制的内容

| 组件 | 改动范围 | 说明 |
|------|---------|------|
| Google Sheets | 无 | 按 target_lang 过滤即可 |
| Workflow A Prompt | 小改 | 术语提取规则通用，示例可加语言特定 |
| Workflow B System Prompt | **大改** | 文化适配表需重写（中文→西班牙语/阿拉伯语的文化映射完全不同） |
| Workflow C Prompt | 小改 | 评估标准通用，回译语言改为目标语言 |
| Workflow 节点结构 | **无** | 完全相同 |

### 8.3 建议的多语言配置方式

在 Dify 中创建 3 套 Workflow B（仅 Prompt 不同）：

```
Workflow B-enUS  → 翻译节点使用英文 Prompt
Workflow B-esES  → 翻译节点使用西班牙语 Prompt（文化适配规则改为西语世界）
Workflow B-arSA  → 翻译节点使用阿拉伯语 Prompt（注意RTL排版、文化禁忌）
```

Workflow A 和 C 可以共用，通过 `target_lang` 变量切换。

---

## 九、实施建议

### 9.1 分阶段落地

```
Week 1: 搭 Google Sheets + Workflow A，跑通术语提取
Week 2: 搭 Workflow B，翻译前10章，人工精修术语表
Week 3: 批量翻译10-50章，建立质量基线
Week 4: 搭 Workflow C，跑通质量门禁，迭代 Prompt
Week 5+: 规模化翻译，人工抽检改为每50章一次
```

### 9.2 成本估算（以 GPT-4o 为例）

| 项目 | 单章成本 | 1000章成本 |
|------|---------|-----------|
| Workflow A（术语提取，1次） | $0.50 | $0.50 |
| Workflow B（逐章翻译） | $0.08-0.15/章 | $80-150 |
| Workflow C（质量抽检，50次） | $0.03/次 | $1.50 |
| **合计** | | **$82-152** |

对比人工翻译（$30-60/章），成本降低 **99%+**。

### 9.3 关键注意事项

1. **第1-10章必须人工精修**。前几章的术语表质量决定了全本的翻译一致性。
2. **每50章做一次术语表审计**。Google Sheets 中 `pending_review` 的术语在人工确认后改为 `confirmed`。
3. **对话/内心独白需要特别注意文化适配**。叙述部分相对好处理，对话是文化差异最大的地方。
4. **R-18 内容**：如果原文包含成人内容，需要在 Prompt 中明确处理策略（保留/淡化/删除）。
