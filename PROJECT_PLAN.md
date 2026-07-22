# 西渡 / Westward Echo —— 全书级文化适配翻译系统

> 基于 LangGraph + Chroma 的 Agent 翻译系统，服务点众科技网文出海业务

---

## 一、项目定位

一个基于大模型与 Agent 技术的全书级文化适配翻译系统，输入一本中文网文（txt），自动输出术语统一、文化适配的多语言译本。核心挑战：在全本几十万字的规模下，保证术语一致、翻译质量、文化适配效果。

### 业务背景

点众科技拥有 35 万册网文版权，正在推进全球化战略。翻译成本高（人工 $30-60/章）、文化壁垒深（中式表达在海外读者中理解率低），是核心痛点。本项目用 Agent 技术将翻译成本降低 99%+，同时通过文化适配提升海外用户阅读体验。

### JD 能力映射

| JD 要求 | 西渡如何证明 |
|----------|------------|
| 1.1 需求→方案→开发→上线完整闭环 | 从点众全球化痛点出发，到可部署的 Docker 化系统 |
| 1.2 智能体框架定制化扩展 | 基于 LangGraph 构建 4 节点 Agent，含自定义 State 和条件路由 |
| 1.3 模型能力工程化、产品化 | 把"翻译+文化适配+术语管理+质检"封装成可用的产品 |
| 1.4 挖掘 AI 赋能场景 | 直击"翻译成本高、文化壁垒深"两个痛点 |
| 1.5 沉淀技术资产 | 输出可复用的 Prompt 模板 + Agent 架构 + 术语库 |
| 2.1.2 后端开发语言 | Python + FastAPI |
| 2.1.3 端到端交付 | 后端 + Gradio 前端 + LLM 调用 + Docker 部署 |
| 2.2.1 智能体框架 | LangGraph（State 管理、多节点编排、条件路由） |
| 2.2.2 大模型 API | DeepSeek V4（Pro/Flash 双模式）+ Claude API 兜底 |
| 2.2.3 RAG 落地 | Chroma 向量检索 + 精确字典双层术语查询 |
| 2.2.4 Prompt 工程 | 术语提取、翻译适配、术语审核、反向回译质检 4 套 Prompt |
| 2.3.1 理解业务 | 项目背景本身就是点众真实业务场景 |

---

## 二、总体架构

```
用户上传 txt
    ↓
FastAPI 后端（文件接收、任务管理、WebSocket 进度推送）
    ↓
章节切分（正则匹配章节标题，保留章节结构）
    ↓
LangGraph Agent（核心翻译流水线）
├── 节点 1：术语提取（首次运行，扫描前 N 章建立初始术语库）
├── 节点 2：翻译+文化适配（单次 LLM 调用，两遍法：先理解后适配）
├── 节点 3：术语更新（从译文提取新术语，精确层+语义层双写）
└── 节点 4：反向回译质检（抽样回译 → 五维评分 → 质量门禁）
    ↓
章节译文输出 + 全本合并
    ↓
Gradio 前端（上传 / 进度 / 下载 / 术语表管理）
```

### 技术栈

| 组件 | 选型 | 原因 |
|------|------|------|
| 后端框架 | FastAPI | 异步支持好，WebSocket 推送翻译进度 |
| Agent 框架 | LangGraph | State 管理精确，节点编排灵活，支持条件路由 |
| 向量数据库 | Chroma | 轻量内嵌，零运维，适合术语语义检索 |
| 精确术语索引 | Python dict | O(1) 精确匹配，保证角色名/地名零误差 |
| LLM | DeepSeek V4（主力，Pro/Flash 双模式）/ Claude API（兜底质检） | V4 Flash 模式做批量翻译（极致低成本），V4 Pro 模式做术语提取和质量评分 |
| 前端 | Gradio | 异步任务支持好，进度回调原生，比 Streamlit 适合长任务 |
| 部署 | Docker + docker-compose | 一键部署，环境隔离 |

---

## 三、分块处理方案

### 3.1 为什么按"章"切分而非按"字数"切分

| 方案 | 优点 | 缺点 |
|------|------|------|
| 按字数切分（每 3000 字一块） | 均匀负载 | 切断叙事流、上下文断裂、术语跨块重复且位置不可控 |
| **按章节切分（采用）** | 自然叙事边界、上下文完整、输出复用章节结构 | 章节长短不一（2000-8000 字） |

网文章节通常 2000-5000 字，英文输出约 1500-3500 words，恰好在 LLM 舒适窗口内。对于超过 8000 字的异常长章，作为 fallback 按"章内小节"（空行分隔）二次拆分。

### 3.2 章节切分实现

```python
import re

CHAPTER_PATTERN = re.compile(r'(第[一二三四五六七八九十百千0-9]+[章节回]\s*.*)')

def split_chapters(text: str) -> list[dict]:
    """按章节标题正则拆分为 [{title, content}] 列表"""
    lines = text.split('\n')
    chapters = []
    current_title = "前言"
    current_lines = []

    for line in lines:
        if CHAPTER_PATTERN.match(line.strip()):
            if current_lines:
                chapters.append({"title": current_title, "content": "\n".join(current_lines)})
            current_title = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        chapters.append({"title": current_title, "content": "\n".join(current_lines)})

    return chapters
```

---

## 四、术语表维护机制（双层检索）

### 4.1 核心设计：双层而非单层

术语一致性的本质需求是：第 500 章出现的"林小满"必须和第 1 章翻译得一模一样。

这是**确定性匹配**问题，不是语义相似度问题。因此不能只依赖 Chroma 向量检索，需要双层：

```
                ┌─────────────────────┐
                │   当前章节原文       │
                └────────┬────────────┘
                         │
              ┌──────────▼──────────┐
              │  精确层：Python dict │
              │  O(1) 精确匹配      │
              │  {cn_term: en_term} │
              │  适用：角色名、地名  │
              └──────────┬──────────┘
                         │ 精确命中 → 直接注入 Prompt
                         │
              ┌──────────▼──────────┐
              │  语义层：Chroma      │
              │  向量语义检索        │
              │  适用：修真术语、成语 │
              │  Top K = 15          │
              └──────────┬──────────┘
                         │ 语义召回 → 补充注入 Prompt
                         │
              ┌──────────▼──────────┐
              │  合并去重 → 注入 LLM │
              └─────────────────────┘
```

### 4.2 精确层（Python dict）

```python
class ExactGlossary:
    """精确术语索引，O(1) 查找，保证角色名/地名零误差"""

    def __init__(self):
        self._dict: dict[str, str] = {}  # {term_cn: term_en}

    def add(self, term_cn: str, term_en: str):
        self._dict[term_cn] = term_en

    def match_in_text(self, text: str) -> dict[str, str]:
        """在文本中扫描所有匹配的术语，返回 {term_cn: term_en}"""
        matched = {}
        for cn, en in self._dict.items():
            if cn in text:
                matched[cn] = en
        return matched

    def to_dict(self) -> dict[str, str]:
        return dict(self._dict)

    def __len__(self) -> int:
        return len(self._dict)
```

这个精确层**不依赖 embedding**，直接字符串包含匹配。对于角色名（林小满、裴总）、地名（青云山）、专有名词（金丹期）——必须精确匹配，不能用向量检索。

### 4.3 语义层（Chroma）

```python
import chromadb

class SemanticGlossary:
    """语义术语检索，用于场景相关的文化术语召回"""

    def __init__(self, persist_path: str = "./chroma_glossary"):
        self.client = chromadb.PersistentClient(path=persist_path)
        self.collection = self.client.get_or_create_collection("terms")

    def add_term(self, term_cn: str, term_en: str, category: str, context: str):
        self.collection.add(
            documents=[f"{term_cn}: {context}"],
            metadatas=[{"term_cn": term_cn, "term_en": term_en, "category": category}],
            ids=[term_cn]  # 用中文术语做 id，天然去重
        )

    def search(self, query_text: str, top_k: int = 15) -> list[dict]:
        results = self.collection.query(query_texts=[query_text], n_results=top_k)
        return [
            {"term_cn": m["term_cn"], "term_en": m["term_en"], "category": m["category"]}
            for m in results["metadatas"][0]
        ]
```

### 4.4 术语表闭环流程

```
翻译每章时：

① 精确层匹配：扫本章原文，O(1) 找出所有已知角色名/地名 → exact_matches
② 语义层检索：用本章原文做 Chroma 向量检索 → semantic_matches
③ 合并去重后注入 Prompt（优先使用精确层结果）
         ↓
④ LLM 翻译，输出译文 + new_terms_found[]
         ↓
⑤ 新术语分流写入：
   · 角色名/地名 → 写入精确层 dict + Chroma
   · 文化术语/成语 → 只写 Chroma
   · 所有术语 → 写入 SQLite 持久化（重启不丢）
```

### 4.5 为什么不用纯 Chroma

纯向量检索的风险示例：

> "林小满" 和 "林晓曼" 拼音相近，embedding 向量余弦相似度可能高达 0.95+。Chroma 可能将"林晓曼"的翻译错误地匹配给"林小满"。

精确层（dict）不存在这个问题：`text.find("林小满")` 和 `text.find("林晓曼")` 是精确匹配，不会混淆。

---

## 五、LangGraph Agent 设计

### 5.1 为什么节点从 5 个精简为 4 个

文化适配不是翻译之后的一道"润色工序"。**翻译的本质就是跨语言+跨文化的信息传递**。如果先直译再单独适配：
- 第一步输出的直译文本已经丢失了原文的语境和修辞
- 第二步（适配节点）看不到中文原文，手里只有信息已损失的英文直译

**正确做法**：翻译和文化适配在同一个 LLM 调用中完成，用 Prompt 内的两遍法。

```
合并前（反模式）：                    合并后（正确）：
[术语提取] → [直译] → [文化适配]      [术语提取] → [翻译+文化适配] → [术语更新] → [质检]
                ↑ 分离 ↑                           ↑ 一个 LLM 调用，Prompt 内两遍法 ↑
```

### 5.2 LangGraph State 定义

```python
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph
import operator

class TranslatorState(TypedDict):
    # === 输入 ===
    chapter_title: str           # 当前章节标题
    chapter_content: str         # 当前章节原文
    chapter_number: int          # 章节序号
    target_lang: str             # 目标语言，如 "en-US"

    # === 术语相关 ===
    exact_glossary: dict         # 精确层：{term_cn: term_en}
    semantic_terms: list[dict]   # 语义层召回结果
    full_glossary_text: str      # 格式化后的术语表文本（注入 Prompt 用）

    # === 翻译输出 ===
    translated_text: str         # 译文
    new_terms_found: list[dict]  # 本章发现的新术语
    adaptation_notes: list[str]  # 文化适配决策记录

    # === 上下文传递（关键：解决跨章一致性问题） ===
    previous_chapter_summary: str  # 上一章 3 句话摘要，传给下一章做上下文

    # === 质检 ===
    quality_score: float         # 质检评分
    quality_issues: list[str]    # 发现的质量问题
    needs_retranslation: bool    # 是否需要重译
```

### 5.3 Graph 结构

```
        ┌─────────┐
        │  START   │
        └────┬─────┘
             │
        ┌────▼──────────┐
        │ fetch_glossary │  ← 精确层匹配 + Chroma 语义检索
        └────┬──────────┘
             │
        ┌────▼──────────┐
        │ translate_node │  ← 翻译+文化适配（核心 LLM 调用）
        └────┬──────────┘
             │
        ┌────▼──────────┐
        │ update_glossary│  ← 新术语入库（精确层 + Chroma + SQLite）
        └────┬──────────┘
             │
        ┌────▼──────────┐
        │ quality_check  │  ← 反向回译质检（每 N 章执行一次）
        └────┬──────────┘
             │
        ┌────▼──────┐      ┌──────────────┐
        │ CONDITION  │─────▶│ retranslate   │  ← 质量不合格 → 重译
        │ needs_retry│      └──────┬───────┘
        └────┬──────┘              │
             │ (通过)              │
             ▼                    ▼
        ┌─────────┐          ┌─────────┐
        │   END   │          │   END   │
        └─────────┘          └─────────┘
```

### 5.4 条件路由

```python
def should_retranslate(state: TranslatorState) -> str:
    """质量门禁：评分低于 3.5 触发重译（最多重试 1 次）"""
    if state.get("quality_score", 5.0) < 3.5:
        return "retranslate_node"
    return "end"

# Graph 构建
builder = StateGraph(TranslatorState)
builder.add_node("fetch_glossary", fetch_glossary_node)
builder.add_node("translate_node", translate_node)
builder.add_node("update_glossary", update_glossary_node)
builder.add_node("quality_check", quality_check_node)
builder.add_node("retranslate_node", translate_node)  # 重译复用翻译节点

builder.add_edge(START, "fetch_glossary")
builder.add_edge("fetch_glossary", "translate_node")
builder.add_edge("translate_node", "update_glossary")
builder.add_edge("update_glossary", "quality_check")
builder.add_conditional_edges("quality_check", should_retranslate, {
    "retranslate_node": "retranslate_node",
    "end": END
})
builder.add_edge("retranslate_node", END)
```

### 5.5 逐章循环（LangGraph 外部编排）

LangGraph 内部不循环处理多章，而是在 FastAPI 层做逐章调用。每次调用 graph.invoke() 翻译一章，上一章的 `previous_chapter_summary` 传给下一章。

```python
# FastAPI 层编排
state = {
    "exact_glossary": {},
    "previous_chapter_summary": "",
    "target_lang": "en-US",
}

for chapter in chapters:
    state["chapter_title"] = chapter["title"]
    state["chapter_content"] = chapter["content"]
    state["chapter_number"] = chapter["number"]

    result = graph.invoke(state)  # LangGraph 执行单章翻译流水线

    # 状态传递到下一章
    state["exact_glossary"] = result["exact_glossary"]     # 术语积累
    state["previous_chapter_summary"] = result.get("chapter_summary", "")  # 上下文

    # 保存译文
    save_chapter(result["translated_text"], chapter["number"])
```

---

## 六、Prompt 模板

### Prompt-A：术语提取节点

```
## SYSTEM

You are a terminology extraction specialist for Chinese-to-English web novel translation. Scan Chinese web novel chapters and identify ALL proper nouns, culturally specific terms, and recurring expressions.

## EXTRACTION RULES

Classify each term as:
- **character**: Names, nicknames, titles (龙傲天, 白莲花, 霸总)
- **location**: Places, realms, sects (青云山, 魔教总坛, 九天大陆)
- **technique**: Martial arts, cultivation, spells (九阴真经, 金丹期, 御剑术)
- **culture**: Era terms, idioms, customs (八零年代, 下海, 铁饭碗, 修真)
- **item**: Artifacts, special objects (储物袋, 筑基丹)
- **era**: Time periods, historical markers

## CULTURAL ADAPTATION (for en-US)

- 八零年代 → "80s rural America" (not literal "the 1980s")
- 霸总 → "Alpha CEO" (not "overbearing president")
- 修真 → "Cultivation" (established xianxia convention)
- 修仙 → "Immortal Cultivation"
- 金丹/元婴 → "Golden Core / Nascent Soul"
- 门派 → "Sect"
- 师兄/师姐 → "senior brother/sister" or use names
- 穿越 → "Transmigration"
- 系统 → "System" (capitalized, LitRPG convention)
- 打脸 → "Face-slapping"
- 丹田 → "Dantian" (untranslated, explain on first occurrence)

## OUTPUT FORMAT

Structured JSON:
{
  "terms": [
    {"term_cn": "string", "term_en": "string", "category": "string", "context": "string", "note": "string"}
  ]
}

---

## USER

Extract terms from:
{{novel_first_10_chapters}}
```

### Prompt-B：翻译+文化适配节点（核心，合并后的单节点）

```
## SYSTEM

You are a professional Chinese-to-English web novel translator specialized in cultural adaptation for the American market. You translate Chinese web novels into natural, engaging English that reads like it was originally written for American audiences.

## CORE PRINCIPLES

### 1. Glossary First (术语优先)
You MUST use the provided glossary translations exactly — no variation. Consistency across all chapters is the #1 priority.

### 2. Two-Pass Translation (两遍法 — 在同一调用中完成理解与适配)
- **Pass 1 — Literal Comprehension (in your mind)**: Understand the exact meaning. Capture every detail, every nuance.
- **Pass 2 — Cultural Rewriting (your output)**: Rewrite for an American reader. Convert Chinese idioms to American equivalents. Adjust cultural references. The output should not read like a translation.

### 3. Cultural Adaptation Mapping

| 中文 | 适配翻译 | 中文 | 适配翻译 |
|------|---------|------|---------|
| 八零年代 | 80s rural America | 牛逼 | badass / epic |
| 霸总 | Alpha CEO | 卧槽 | Holy shit / WTF |
| 修仙 | Cultivation | 装逼 | flex / show off |
| 打脸 | face-slap | 吃瓜群众 | popcorn gallery |
| 龙傲天 | the Chosen One | 白莲花 | goody-two-shoes |
| 飞升 | Ascension | 渡劫 | Heavenly Tribulation |

### 4. Style Guidelines
- Dialogue: **Casual American English**. Characters should sound like they're in a Netflix show.
- Paragraphs: **Short and punchy**. 2-4 sentences. Web novel readers scan, not read.
- Cliffhangers: Preserve the hook. If the original chapter ends on a cliffhanger, sharpen it.
- Emotions: **Show, don't tell**. "His jaw tightened" > "He was angry".
- Action scenes: Short sentences. Active voice. No florid descriptions mid-fight.
- Comedy: American humor cadence — setup, beat, punchline.
- Profanity: Match the intensity. Don't sanitize.

### 5. Untranslatable Terms
- Proper nouns NOT in glossary → Pinyin + brief inline explanation on first occurrence
- Cultural concepts → Closest American equivalent
- RECORD all new terms in `new_terms_found` — never silently translate a recurring proper noun

### 6. Chapter Context
The previous chapter summary is provided below. Use it to maintain narrative continuity, character voice consistency, and proper context for pronouns and references.

## PREVIOUS CHAPTER SUMMARY
{{previous_chapter_summary}}

## GLOSSARY — EXACT MATCHES (MANDATORY)
These terms appear in this chapter. Use these translations EXACTLY:
{{exact_matches_text}}

## GLOSSARY — SEMANTIC MATCHES (REFERENCE)
These culturally relevant terms may help with context:
{{semantic_terms_text}}

## SOURCE TEXT
**Chapter {{chapter_number}}**: {{chapter_title}}

{{chapter_content}}

## OUTPUT (Structured JSON)
{
  "translated_text": "Full chapter in English, preserving paragraph structure",
  "new_terms_found": [
    {"term_cn": "string", "term_en": "string", "category": "string", "context": "string"}
  ],
  "cultural_adaptation_notes": ["2-3 bullets on key adaptation decisions"],
  "chapter_summary": "3-sentence summary of this chapter for next chapter's context"
}
```

### Prompt-C：术语更新审核

```
## SYSTEM

Validate new terms before adding to the master glossary.

## RULES
1. If term already exists in glossary → SKIP (no duplicate)
2. Only proper nouns, culturally specific terms → ACCEPT
3. Generic/common words → SKIP
4. If uncertain about translation quality → mark `status: "pending_review"`

## EXISTING GLOSSARY (for dedup)
{{current_glossary}}

## PROPOSED TERMS
{{new_terms_found}}

## OUTPUT
{
  "validated_terms": [same schema, duplicates removed, status added],
  "rejected_reasons": {"term_cn": "reason"}
}
```

### Prompt-D：反向回译质检

```
## SYSTEM

You are a translation quality auditor. Score the English translation by back-translating to Chinese and comparing.

## EVALUATION (1-5 each)

1. **Semantic Accuracy**: Are all plot points and details preserved?
2. **Character Voice**: Does the character sound the same — personality, class, attitude?
3. **Cultural Adaptation**: Natural American English? Cultural references adapted seamlessly?
4. **Terminology Consistency**: All proper nouns match glossary?
5. **Readability**: Reads like native English web fiction?

## INPUT

Original Chinese: {{original_cn}}
English Translation: {{english_translation}}
Glossary: {{glossary_text}}

## OUTPUT
{
  "back_translated_cn": "natural Chinese back-translation",
  "scores": {"semantic_accuracy": 5, "character_voice": 4, ...},
  "overall": 4.6,
  "issues": [{"severity": "minor|major", "detail": "..."}],
  "recommendation": "PASS|FLAG|REJECT"
}
```

---

## 七、Gradio 前端设计

### 7.1 为什么选 Gradio 而不是 Streamlit

| 维度 | Streamlit | Gradio |
|------|-----------|--------|
| 长任务支持 | 每步交互重新运行脚本，翻译 1000 章需保持连接 | 原生异步队列，任务提交后即时返回 |
| 进度推送 | 无原生支持，需 hack | `gr.Progress()` 原生进度回调 |
| 并发用户 | 不支持（单线程模型） | 支持 `concurrency_limit` 队列 |
| 文件处理 | 默认上限 200MB | 同样支持大文件上传 |
| API 集成 | 弱 | 一键生成 FastAPI 兼容端点 |

Streamlit 适合报表/仪表盘，Gradio 适合 ML 模型推理任务。翻译系统是后者。

### 7.2 页面结构

```
┌────────────────────────────────────────────┐
│         西渡 / Westward Echo                │
│   AI-Powered Web Novel Translation          │
├────────────────────────────────────────────┤
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  📁 Upload Novel (.txt)             │    │
│  │  [Drag & drop or click to upload]    │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  Target Language:  [en-US  ▼]               │
│  LLM: DeepSeek V4  [Flash ▼]  [Pro ▼]  (+ Claude 兜底)  │
│  Quality Check:    [Every 20 chapters ▼]    │
│                                             │
│  [Start Translation]  [Resume Last Job]     │
│                                             │
├────────────────────────────────────────────┤
│  ═══════════════════════════════════        │
│  Progress: Chapter 47/1024                  │
│  ████████████░░░░░░░░░░  4.6%              │
│  Current: Translating "第47章 夜入青云"      │
│  ═══════════════════════════════════        │
├────────────────────────────────────────────┤
│                                             │
│  [📋 Glossary]  [📊 Quality Report]         │
│  [📥 Download EN]  [📥 Download EPUB]       │
│                                             │
└────────────────────────────────────────────┘
```

### 7.3 Gradio 代码骨架

```python
import gradio as gr
import asyncio

def translate_novel(file, target_lang, translate_mode, quality_mode, quality_interval, progress=gr.Progress()):
    """主翻译任务，由 Gradio 队列异步执行"""
    text = file.decode("utf-8")
    chapters = split_chapters(text)
    total = len(chapters)

    progress(0, desc=f"Starting translation of {total} chapters...")

    # DeepSeek V4 Pro / Flash 模式映射
    agent = TranslationAgent(
        translate_model="deepseek-chat-pro" if translate_mode == "V4 Pro" else "deepseek-chat-flash",
        quality_model="claude-opus" if quality_mode == "Claude Opus" else "deepseek-chat-pro",
    )
    all_translations = []
    all_glossary = {}

    for i, ch in enumerate(chapters):
        progress((i+1)/total, desc=f"Translating Chapter {i+1}/{total}: {ch['title'][:50]}")
        result = agent.translate_chapter(
            chapter=ch,
            exact_glossary=all_glossary,
            previous_summary=all_translations[-1].get("summary", "") if all_translations else "",
        )
        all_translations.append(result)
        all_glossary.update(result.get("new_exact_terms", {}))

        # WebSocket 推送进度到前端
        yield progress_update(i+1, total, ch["title"])

    # 合并输出
    full_text = merge_chapters(all_translations)
    glossary_df = format_glossary(all_glossary)

    return full_text, glossary_df, generate_quality_report()

# Gradio 界面
with gr.Blocks(title="Westward Echo") as demo:
    gr.Markdown("# 西渡 / Westward Echo")

    with gr.Row():
        file_input = gr.File(label="Upload .txt", file_types=[".txt"])
        target_lang = gr.Dropdown(["en-US", "es-ES", "ar-SA"], value="en-US", label="Target Language")
        translate_mode = gr.Radio(["V4 Flash", "V4 Pro"], value="V4 Flash", label="Translation Mode")
        quality_mode = gr.Radio(["V4 Pro", "Claude Opus"], value="V4 Pro", label="QA / Arbitration Model")
        quality_interval = gr.Dropdown([10, 20, 50], value=20, label="Quality Check Every N Chapters")

    start_btn = gr.Button("Start Translation", variant="primary")

    progress_bar = gr.Progress()

    with gr.Tabs():
        with gr.Tab("Translation"):
            output_text = gr.Textbox(label="Translated Text", lines=20)
            download_btn = gr.DownloadButton("Download EPUB")
        with gr.Tab("Glossary"):
            glossary_table = gr.Dataframe(label="Term Glossary")
        with gr.Tab("Quality Report"):
            quality_report = gr.Markdown()

    start_btn.click(
        fn=translate_novel,
        inputs=[file_input, target_lang, translate_mode, quality_mode, quality_interval],
        outputs=[output_text, glossary_table, quality_report],
    )

demo.queue(concurrency_count=3).launch(server_port=7860)
```

---

## 八、项目结构

```
westward-echo/
├── src/
│   ├── __init__.py
│   ├── main.py                  # FastAPI 入口
│   ├── config.py                # 环境变量 + LLM 分层模型配置
│   ├── chapter_splitter.py      # 章节切分
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── graph.py             # LangGraph 状态图构建
│   │   ├── state.py             # TranslatorState 定义
│   │   ├── nodes/
│   │   │   ├── __init__.py
│   │   │   ├── fetch_glossary.py    # 双层术语检索
│   │   │   ├── translate.py         # 翻译+文化适配（核心）
│   │   │   ├── update_glossary.py   # 术语更新入库
│   │   │   └── quality_check.py     # 反向回译质检
│   │   └── prompts/
│   │       ├── term_extraction.py
│   │       ├── translation.py
│   │       ├── term_validation.py
│   │       └── quality_check.py
│   ├── glossary/
│   │   ├── __init__.py
│   │   ├── exact_store.py       # 精确层（Python dict + SQLite）
│   │   ├── semantic_store.py    # 语义层（Chroma）
│   │   └── models.py            # Term 数据模型
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py            # FastAPI 路由
│   │   └── websocket.py         # WebSocket 进度推送
│   └── app.py                   # Gradio 前端
├── tests/
│   ├── test_chapter_splitter.py
│   ├── test_glossary.py
│   ├── test_translate_node.py
│   └── fixtures/
│       └── pei_zong_ch1-3.txt   # 测试用例：《裴总每天都想父凭子贵》前三章
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md
```

---

## 九、部署配置

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

EXPOSE 8000 7860

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### docker-compose.yml

```yaml
version: "3.9"
services:
  api:
    build: .
    ports:
      - "8000:8000"
      - "7860:7860"
    environment:
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - DEEPSEEK_FLASH_MODEL=${DEEPSEEK_FLASH_MODEL:-deepseek-chat-flash}
      - DEEPSEEK_PRO_MODEL=${DEEPSEEK_PRO_MODEL:-deepseek-chat-pro}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - CHROMA_PERSIST_PATH=/data/chroma
    volumes:
      - ./data:/data
      - ./output:/app/output
```

---

## 十、测试用例

**测试书目**：《裴总每天都想父凭子贵》（现代言情，霸总题材，适合验证文化适配效果）

### 输入：前三章原文

```
第一章 穿成霸总文女主
第二章 裴总，请自重
第三章 父凭子贵计划启动
```

### 预期输出示例（第 1 章翻译 + 术语提取 + 质检）

**术语提取输出：**
```json
{
  "terms": [
    {"term_cn": "裴总", "term_en": "President Pei", "category": "character", "note": "初期用President Pei建立身份认知"},
    {"term_cn": "霸总", "term_en": "Alpha CEO", "category": "culture", "note": "章节标题使用，美国读者对Alpha male archetype有直觉"},
    {"term_cn": "穿成", "term_en": "transmigrated into", "category": "culture", "note": "穿越/穿书类网文标准译法"},
    {"term_cn": "父凭子贵", "term_en": "Daddy's Golden Ticket", "category": "culture", "note": "不是直译'father relies on son'，而是用'golden ticket'传达'孩子改变命运'的戏剧感"}
  ]
}
```

**翻译输出（片段）：**
```markdown
# Chapter 1: Transmigrated Into a CEO Romance — As the Female Lead

I opened my eyes to a penthouse. Floor-to-ceiling windows. The Shanghai skyline glittering like someone'd scattered diamonds across the night.

Either I was dreaming, or someone had seriously upgraded my apartment.

Then the memories hit. Not my memories. *Her* memories. Su Nian, 24, freshly graduated, somehow already the personal assistant to Pei Yanzhou — thirty-two, absurdly wealthy, face carved by gods with a personal grudge against mere mortals.

The Alpha CEO. The male lead.

And me? I was the female lead. In the novel I'd been hate-reading before bed. The one where the female lead chases the CEO for 800 chapters before he finally—

"Oh, hell no."

---

Translator's Note:
- "Alpha CEO" used for 霸总 — maps to the American romance novel archetype (billionaire alpha male)
- "Daddy's Golden Ticket" adopted for 父凭子贵 — prioritizes the dramatic irony of the premise over literal fidelity
```

**反向回译质检输出：**
```json
{
  "back_translated_cn": "我睁开眼，看到的是顶层公寓。落地窗。上海的天际线在夜色中闪烁，像是有人把钻石撒在了天上。要么我在做梦，要么有人给我的公寓来了一次离谱的升级。",
  "scores": {
    "semantic_accuracy": 5,
    "character_voice": 5,
    "cultural_adaptation": 4,
    "terminology_consistency": 5,
    "readability": 5,
    "overall": 4.8
  },
  "issues": [
    {
      "severity": "minor",
      "detail": "原文首句是'我醒过来的时候，发现自己躺在一张比我整个出租屋还大的床上'，英文版改成了'penthouse'开场，失去了原文'床vs出租屋'的对比幽默。这是合理的文化适配（美国penthouse是财富符号），但可以考虑在第一段加入类似对比。"
    }
  ],
  "recommendation": "PASS"
}
```

---

## 十一、模型选型策略

### 11.1 核心选型：DeepSeek V4（Pro / Flash 双模式）

DeepSeek V4 是当前最新基座模型，通过同一 API 端点提供两种运行模式：

| 模式 | 定位 | 延迟 | 成本 | 适用场景 |
|------|------|------|------|---------|
| **V4 Flash** | 高吞吐、极致低成本 | 低 | 极低 | 逐章翻译（90% 章节）、术语增量提取、反向回译 |
| **V4 Pro** | 高精度、强推理 | 中 | Flash 的 3-5x | 初始术语提取、复杂章节翻译、五维质量评分 |

**为什么这是最理想的架构**：同基座模型 + 双模式，意味着：
- 同一套 API SDK，不需要对接两个厂商
- Prompt 在同一模型体系下行为一致，Flash 和 Pro 的输出格式天然兼容
- 成本控制精确——90% 的 token 消费走 Flash，只有关键节点切 Pro

### 11.2 分层调用策略

翻译系统的不同节点对模型的精度要求不同：

| 节点 | 频率 | 模型选择 | 理由 |
|------|------|---------|------|
| **初始术语提取**（前 10 章） | 1 次/本 | V4 Pro | 术语表质量决定全书一致性，首批术语必须精准 |
| **逐章翻译**（常规章） | N 次 | **V4 Flash** | 给定清晰术语表+System Prompt，Flash 翻译质量足够，成本极低 |
| **逐章翻译**（关键章） | 少量 | V4 Pro | 第一章、结局章、感情高潮章——作为质量锚点 |
| **术语增量提取** | N 次 | V4 Flash | 从译文中提取新名词是结构化抽取任务，Flash 足够 |
| **术语去重审核** | N 次 | V4 Flash | 规则明确（查重+分类），不需要强推理 |
| **反向回译** | N/20 次 | V4 Flash | 英译中对 V4 是母语方向，Flash 完全胜任 |
| **五维质量评分** | N/20 次 | **V4 Pro** | 审美判断需要推理深度，不能用 Flash |
| **质检不合格重译** | 少量 | **V4 Pro** | Flash 没翻好的章，切 Pro 重来 |

### 11.3 为什么翻译主力用 Flash 而不是全用 Pro

- **成本**：Flash 单 token 成本约为 Pro 的 1/3 到 1/5。一本 1000 章网文（每章约 2500 字输入 + 1800 words 输出），全用 Pro 翻译可能 $30-50，全用 Flash 可能 $5-8。
- **延迟**：Flash 首 token 延迟显著更低。翻译是流水线——每章快 1 秒，1000 章就是 16 分钟。
- **质量在网文场景差距不大**：翻译质量的天花板是术语一致性和文化适配规则（由 Prompt + 术语表保证），不是模型推理深度。Flash 在 "给定术语表，遵循文化适配规则翻译这段文字" 这类指令跟随任务上，与 Pro 的差距远小于开放式创作场景。
- **Pro 兜底**：质检节点发现质量差的章节，自动切 Pro 重译。这样 90% 章走 Flash + 10% 章走 Pro，兼顾效率和品质。

### 11.4 Claude API 的定位

Claude 在系统中作为**可选兜底**：
- 当质检节点由 V4 Pro 评分后仍有争议，可以调用 Claude Opus 做"仲裁评估"
- 文化适配特别敏感的章节（如涉及种族、宗教内容），用 Claude 的安全判断能力做二次审核
- 大部分场景不需要 Claude，但架构预留了切换能力

### 11.5 成本估算

以 DeepSeek V4 当前价格为例（具体以官网为准）：

| 项目 | 模型 | 频率 | 千章成本 |
|------|------|------|---------|
| 初始术语提取（前 10 章） | V4 Pro | 1 次 | ~$0.30 |
| 逐章翻译（950 章） | V4 Flash | 950 次 | ~$5.00 |
| 逐章翻译（50 章关键/重译） | V4 Pro | 50 次 | ~$2.50 |
| 术语增量提取 | V4 Flash | 1000 次 | ~$0.50 |
| 反向回译（50 次） | V4 Flash | 50 次 | ~$0.10 |
| 质量评分 + 重译判断 | V4 Pro | 50 次 | ~$0.50 |
| Chroma embedding | BGE-small（本地） | — | 免费 |
| **合计** | | | **~$8.90** |

对比人工翻译（$30-60/章），1000 章人工成本 $30,000-60,000。Agent 成本降低 **99.97%**，且耗时从数月缩到数小时。

> **保守估算**：实际中因重译、超长章、Prompt 长度等因素，建议按 $15-25/本做预算。

### 11.6 配置方式

```bash
# .env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com

# V4 模式选择 — 通过 model 参数指定
DEEPSEEK_FLASH_MODEL=deepseek-chat-flash   # V4 Flash 模式
DEEPSEEK_PRO_MODEL=deepseek-chat-pro       # V4 Pro 模式

# Claude 兜底（可选）
ANTHROPIC_API_KEY=sk-ant-xxx
```

代码中按节点自动选择模型：
```python
# config.py
MODEL_MAP = {
    "translate": os.getenv("DEEPSEEK_FLASH_MODEL", "deepseek-chat-flash"),  # 主力走 Flash
    "translate_critical": os.getenv("DEEPSEEK_PRO_MODEL", "deepseek-chat-pro"),  # 关键章切 Pro
    "term_extraction": os.getenv("DEEPSEEK_PRO_MODEL", "deepseek-chat-pro"),  # 初始提取走 Pro
    "term_extraction_incremental": os.getenv("DEEPSEEK_FLASH_MODEL", "deepseek-chat-flash"),
    "quality_score": os.getenv("DEEPSEEK_PRO_MODEL", "deepseek-chat-pro"),  # 评分走 Pro
    "back_translate": os.getenv("DEEPSEEK_FLASH_MODEL", "deepseek-chat-flash"),
}
```

---

## 十二、多语言扩展

当前设计已预留 `target_lang` 参数。扩展到西班牙语（es-ES）、阿拉伯语（ar-SA）时的改动：

| 组件 | 改动 | 说明 |
|------|------|------|
| LangGraph 节点结构 | 无 | 完全相同 |
| State 定义 | 无 | `target_lang` 字段已存在 |
| 精确层 glossary | 无 | 用 `{term_cn}_{lang}` 做 key 隔离 |
| Chroma collection | 新建 collection | 每种语言独立 collection |
| Prompt-B（翻译节点） | **需重写** | 中文→西班牙语/阿拉伯语的文化适配映射完全不同 |
| Gradio 前端 | 加语言选项 | Dropdown 加项即可 |

阿拉伯语额外注意：RTL 排版、文化禁忌过滤（如酒精、猪肉相关表达）、性别代词系统。

---

## 十三、MVP 实施计划

| 阶段 | 内容 | 时间 |
|------|------|------|
| Week 1 | 章节切分 + LangGraph 骨架 + 精确层 glossary | 2-3 天 |
| Week 2 | 翻译+文化适配节点 + Prompt 调优 + 《裴总》前 3 章跑通 | 3-4 天 |
| Week 3 | Chroma 语义层 + 术语更新闭环 + 反向回译质检 | 3-4 天 |
| Week 4 | Gradio 前端 + FastAPI + WebSocket 进度推送 + Docker 部署 | 3-4 天 |
| Week 5+ | 《裴总》全书翻译 + 人工审校前 20 章 + Prompt 迭代 | 持续 |

---

## 十四、防翻车设计

### 14.1 断点续传（Checkpoint）

翻译 1000 章的网文，如果在第 847 章 API 超时、网络断开或进程崩溃，不能从头再来。需要在 LangGraph 外部编排循环中加 checkpoint 机制。

**实现方案**：每章翻译完成后，写入 SQLite 一条记录：

```python
# checkpoint 表结构
CREATE TABLE translation_checkpoint (
    novel_id TEXT,
    chapter_number INTEGER,
    chapter_title TEXT,
    translated_text TEXT,
    glossary_snapshot TEXT,    -- JSON: 该章完成时的精确层 glossary 快照
    previous_summary TEXT,     -- 该章的 3 句话摘要（传给下一章）
    completed_at TIMESTAMP,
    PRIMARY KEY (novel_id, chapter_number)
);
```

**恢复逻辑**：
```python
def resume_or_start(novel_id: str, chapters: list) -> tuple[int, dict, str]:
    """读取最后的 checkpoint，返回 (起始章号, glossary快照, 上章摘要)"""
    last = db.execute(
        "SELECT chapter_number, glossary_snapshot, previous_summary "
        "FROM translation_checkpoint WHERE novel_id = ? "
        "ORDER BY chapter_number DESC LIMIT 1",
        (novel_id,)
    ).fetchone()

    if last:
        return last[0] + 1, json.loads(last[1]), last[2]
    return 1, {}, ""
```

**面试话术**："这个设计保证了系统在任意时刻崩溃后都能从断点恢复。SQLite 是零运维的嵌入式数据库，checkpoint 写入是原子操作，不会出现半章脏数据。glossary_snapshot 的存在意味着即使 Chroma 文件损坏，精确层术语表也不会丢。"

### 14.2 API 速率限制与重试策略

DeepSeek API 有 RPM（每分钟请求数）和 TPM（每分钟 token 数）限制。连续翻译 1000 章会触发 429 限流。

**三明治方案**：
```python
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
async def translate_with_backoff(agent, chapter, glossary, summary):
    """带指数退避的翻译调用"""
    result = await agent.translate_chapter(chapter, glossary, summary)
    await asyncio.sleep(0.5)  # 章间冷却，避免连续打满 RPM
    return result
```

- **正常节奏**：每章间隔 0.5s，1000 章约 8 分钟完成
- **遇到 429**：指数退避 (2s → 4s → 8s)，3 次重试后放弃该章，记入失败队列
- **失败不阻塞**：失败章跳过继续翻后面，最后统一重试失败队列

### 14.3 非标准段落处理

网文常有不按"第X章"格式的段落：楔子、番外、作者上架感言、请假条、七夕特别篇等。当前正则 `第[一二三四五六七八九十百千0-9]+[章节回]` 匹配不到它们，会被归入全书第一段（"前言"）。

**处理策略**：

| 段落类型 | 识别特征 | 处理方式 |
|---------|---------|---------|
| 楔子/序章 | 单独一行"楔子""序章""引子" | 作为 Chapter 0 翻译，不注入术语表（术语尚未建立） |
| 番外 | "番外""外传""IF线" | 标记为 `is_extra=True`，使用已有术语表，不从中提取新术语 |
| 作者注/请假条 | 短文本（< 500 字）、包含"请假""更新""公告" | **跳过翻译**，保留原文附在输出末尾 |
| 上架感言 | "上架感言""V章""入V" | 跳过翻译 |
| 七夕/春节特别篇 | 短章节 + 节庆关键词 | 正常翻译，但不从中提取术语（防止节日限定梗污染术语库） |

**实现**：
```python
NON_CHAPTER_PATTERNS = {
    "prologue": re.compile(r'^(楔子|序章|引子|第[零0]+章)'),
    "extra": re.compile(r'^(番外|外传|IF线|小剧场)'),
    "author_note": re.compile(r'(请假|更新公告|上架感言|入V|V章)'),
    "special": re.compile(r'(七夕|春节|元旦|中秋|国庆|特别篇|加更)'),
}

def classify_paragraph(title: str, content: str) -> str:
    """分类非标准段落，返回 'translate' | 'translate_no_extract' | 'skip'"""
    if len(content) < 500 and AUTHOR_NOTE_PATTERN.search(title):
        return "skip"
    if SPECIAL_PATTERN.search(title):
        return "translate_no_extract"
    return "translate"
```

### 14.4 成本估算的诚实边界

项目书中的成本数字（~$8.90/千章）是基于以下假设推算的：

| 假设项 | 假设值 | 验证方式 |
|--------|--------|---------|
| V4 Flash 输入价格 | ~$0.14/百万 token | 需去 DeepSeek 官网确认 |
| V4 Flash 输出价格 | ~$0.28/百万 token | 同上 |
| V4 Pro 约为 Flash 的 3-5x | 估算 | 同上 |
| 单章输入 2500 汉字 ≈ 1800 tokens | 经验值 | 实际 tokenize 后校准 |
| 单章英文输出 1800 words ≈ 2700 tokens | 经验值 | 同上 |
| 5% 章节需要 Pro 重译 | 估算 | 生产环境观测后调整 |

**面试话术**："成本数字是按 Flash 约为 Pro 的 1/3 到 1/5 的价格比例推算的，上线前会去 DeepSeek 官网拉真实 pricing 填入 `.env.example`。但不管具体定价如何，Agent 翻译相比人工翻译 $30-60/章，成本降低两个数量级这个结论是稳定的。"

### 14.5 测试用例

`tests/fixtures/pei_zong_ch1-3.txt` — 合成测试数据，已就位。

**书名**：《裴总每天都想父凭子贵》
**体裁**：现代言情 ⊗ 霸总文 ⊗ 穿越 ⊗ 系统文（四要素全涵盖，当下网文主流）
**规模**：约 16KB，354 行，3 章

**为什么用合成数据**：每段都是针对性编写的测试向量。真实小说前 3 章可能一半是环境描写，测试密度低。合成数据的翻译结果可以后验验证——因为编写时就预置了每个文化适配的知识锚点。

**测试覆盖矩阵**：

| 测试维度 | 数据中的覆盖点 |
|---------|-------------|
| 角色名翻译一致性 | 苏念、裴衍舟、楚淮、林婉清 — 4 个角色跨章出镜 |
| 文化适配 | 霸总、白莲花、备胎、父凭子贵、狗血、社畜、带球跑、暖男、金手指、996 |
| 系统面板翻译 | 3 次系统弹窗（含古风游戏 UI 语体 → 英文 LitRPG 语体映射） |
| 对话风格 | 职场对峙（正式）、内心 OS 吐槽（粗口密集）、绿茶式外交（甜中带刺） |
| 章节钩子 | 每章末尾均有 cliffhanger |
| 术语积累 | 第 1 章建立术语 → 第 2-3 章复用 → 验证术语表闭环 |

---

## 十五、前端演进路线

### 15.1 MVP 阶段：Gradio

Gradio 适合 MVP 快速验证（异步队列、进度回调原生的优势覆盖了 Streamlit 的短板），但存在已知局限：

1. **自定义 UI 能力弱**：Gradio 布局系统（Row/Column/Tabs）够用但不灵活。无法做精致的品牌化前端。
2. **多用户并发时，单 Session 内存占用大**。翻译任务（1000 章）运行 30-60 分钟，Gradio 队列能排队但无法跨进程共享状态。
3. **移动端不友好**：桌面优先设计，手机上体验差。

### 15.2 产品化阶段：Gradio API + React 前端

Gradio 支持 `api_mode=True`，启动后自动生成 FastAPI 兼容的 REST 端点。此时可以：
- 保留 Gradio 作为内部调试/Demo 界面
- 另起一个 React 前端（或 Next.js），通过 Gradio API 调用翻译任务
- 进度推送改用 FastAPI WebSocket（独立于 Gradio）

### 15.3 规模化阶段：Celery + Redis 任务队列

当需要支持多用户并发翻译时：
- FastAPI 接收上传 → 写入任务队列（Celery + Redis）
- Worker 进程异步执行 LangGraph 翻译流水线
- WebSocket 推送进度到前端
- 前端完全独立（React / Vue），FastAPI 纯做 API Gateway

这个演进路线在面试中可以作为"技术规划能力"的证据。
