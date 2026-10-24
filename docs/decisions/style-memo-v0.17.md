# Westward Echo v0.17 — Style Memo 补全 & 析数据接入（设计记录 · 已完成）

> **状态（2026-10-24 更新）：本方案已实现并随主版本落地。** 第一/二优先（characters/prose 抽屉）由 `src/style_memo.py` + `STYLE_MEMO_ENHANCED`（`src/config.py`，默认 `true`）实现；第三/四优先（析情绪曲线 + 知识图谱播种）由 `scripts/seed_from_analyze.py` 的 `_seed_sentiment`/`_seed_relations` 实现。`VERSION` 已升至 `0.17.0`。本文保留原方案内容作为设计记录，`STYLE_MEMO_ENHANCED=false` 仍可回退到 v0.16 行为。

## 原则

1. **不改架构。** 不改 4-node 管道，不改 API 接口，不改 prompt 模板签名
2. **不改现有可用的东西。** 现在能编译 2301 章的能力不动它
3. **每一项都是可逆的。** 有个开关能关掉，回退到 v0.16 行为
4. **优先修复已经声明但没跑起来的能力**

---

## 第一优先：characters.md 抽屉修复（半天）

### 问题

Style Memo 声明了六个抽屉。characters.md 从第一天起就是空的——不是没数据，是**没有任何代码往里面写**。

`update_from_read_analysis()` 每章都在跑。它写 terms、bridges、pacing。characters 和 prose 不在它的写入范围里。`update_from_feedback()` 也不写 characters。

但 READ 节点每章都在产出角色相关数据：`terminology_decisions` 里有角色名的翻译决策和选词理由，`crafted_moments` 里有角色关键出场。这些数据已经到了 `_post_process` 这一步，只是没有被路由到 characters 抽屉。

### 怎么改

改一个文件：`src/style_memo.py`

在 `update_from_read_analysis()` 方法里，追加一段：

```python
# ── Characters: capture voice/dialogue decisions ──────
# Terminology decisions tagged as character names → characters drawer
for td in ra.get("terminology_decisions", []):
    cn = td.get("term_cn", "")
    reasoning = td.get("reasoning", "")
    cultural_note = td.get("cultural_note", "")
    # Only route character-name decisions here (2-4 char Chinese names
    # with cultural reasoning attached, vs generic term decisions)
    if cn and 2 <= len(cn) <= 4 and (reasoning or cultural_note):
        entry = f"{cn} → {td.get('proposed_en', '?')}"
        if cultural_note:
            entry += f" // {cultural_note[:120]}"
        self.record_lesson("characters", entry, chapter_number)

# Crafted moments that are character-specific → characters drawer
for m in ra.get("crafted_moments", []):
    if isinstance(m, str) and len(m) > 20:
        self.record_lesson("characters", m[:200], chapter_number)
```

`record_lesson` 自带 50 字符指纹去重——同一个角色不会重复记录。

### 预期效果

翻译到第 5 章后，characters.md 开始有内容：

```
# Character Voices

[ch1] 林初夏 → Lin Chuxia // 名字里的"初夏"是时间感的暗示，英译保留了季节联想
[ch1] 顾墨言 → Gu Moyan // "墨"字暗示沉默寡言和内敛，WRITER 在对话密度上反向处理
[ch3] 苏念 → Su Nian // "念"不宜译成 Thought——它更像是 lingering feeling
```

这些记录会通过 `read_relevant()` 喂回给 READ 和 WRITE 节点——角色翻译决策有了跨章记忆。

---

## 第二优先：prose.md 抽屉激活（半天）

### 问题

prose.md 现在的唯一写入路径是：冷读者说 "reads like translation" → 触发 `update_from_feedback` 写入一条。"翻译味"是唯一能解锁这个抽屉的信号。

但如果翻译质量好（冷读者没抱怨），prose.md 永远是空的。等于说**你只有犯了错才能学习**——而做对了什么、行文节奏好在哪，什么也不记。

### 怎么改

两个改动都在 `src/style_memo.py`：

**改动 a：`update_from_read_analysis` 加 prose 写入**

READ 节点的 `pacing_notes` 里经常包含句子长度、段落密度、场景节奏的分析。把其中和行文相关的部分挖出来写进 prose：

```python
# ── Prose: sentence/paragraph rhythm from READ analysis ──
pacing = ra.get("pacing_notes", "")
if pacing and len(pacing.strip()) > 10:
    # Extract rhythm notes from pacing analysis
    # Key patterns to detect:
    # - sentence length variation
    # - paragraph density
    # - show-vs-tell observations
    rhythm_keywords = ["句", "段", "节奏", "密度", "描写", "叙述",
                       "sentence", "paragraph", "rhythm", "density"]
    if any(kw in pacing.lower() for kw in rhythm_keywords):
        self.record_lesson("prose", pacing.strip()[:300], chapter_number)
```

**改动 b：prose 写入从"冷读者抱怨"扩展到"冷读者发现"**

`update_from_feedback` 里 prose 的写入条件，从只触发 "reads like translation"，扩展为也触发正面信号：

```python
# ── Prose: cold reader rhythm observations (positive + negative) ──
# Old: only logged when reader detected "translation feel"
# New: also log standout rhythm moments the reader noticed
for sm in fb.get("standout_moments", []):
    if isinstance(sm, str) and len(sm) > 20:
        self.record_lesson("prose", f"Standout: {sm[:200]}", chapter_number)

# Still log translation-feel warnings (existing behavior)
if fb.get("overall_impression", ""):
    imp = fb["overall_impression"]
    if "translation" in imp.lower() or "reads like" in imp.lower():
        self.record_lesson(
            "prose",
            f"Cold reader detected translation feel: {imp[:200]}. "
            f"Reduce 'show-then-explain' pattern.",
            chapter_number,
        )
```

### 开关

`config.py` 加 `STYLE_MEMO_ENHANCED = True`（环境变量控制）。`False` 回退到现有行为。

### 预期效果

六个抽屉全部开始积累内容。terms/bridges/pacing 继续工作不变。characters 有了角色翻译历史。prose 有了行文节奏的正反面记录。

翻译到第 50 章时，新章节的 System Prompt 里注入的内容从"三个抽屉的最新 5 条"变成"五个抽屉的最新 5 条"。WRITE 节点会看到一个更完整的"本作的翻译记忆"。

---

## 第三优先：接入析的情绪曲线（半天）

### 为什么编译需要情绪曲线

西渡的 READ 节点在分析原文时，要做 `emotional_arc` 判断——这段话的情绪走向是什么。但这个判断完全依赖 READ 节点自己的阅读理解。

如果析已经算好了原文的情绪曲线（`[-1, +1]`，逐段标注），READ 节点就不需要"重新理解情绪"——情绪数据是给它的已知信息，它可以专注做它更擅长的事：发现文化缺口和图像缺口。

### 怎么改

两个改动：

**改动 a：`scripts/seed_from_analyze.py` 新增情绪数据播种**

在现有的术语播种 + worldbuilding 播种基础上，加一个步骤：

```python
def _seed_sentiment(book_id: str, extraction: dict, memo: StyleMemoStore):
    """从析的情绪曲线数据播种 Style Memo 的情感节奏提示。"""
    sentiment_scores = extraction.get("sentiment_scores", {})
    if not sentiment_scores:
        return

    # 按章聚合情绪值
    chapter_scores: dict[int, list[float]] = {}
    for para_id, score in sentiment_scores.items():
        # para_id 格式：{book_id}_{chapter}_{paragraph}
        parts = para_id.split("_")
        if len(parts) >= 3:
            try:
                ch = int(parts[-2])
                chapter_scores.setdefault(ch, []).append(float(score))
            except (ValueError, IndexError):
                continue

    # 写入 pacing 抽屉：每章的宏观情绪基调
    for ch in sorted(chapter_scores):
        scores = chapter_scores[ch]
        avg = sum(scores) / len(scores)
        if avg > 0.5:
            tone = "高昂/乐观基调"
        elif avg < -0.5:
            tone = "压抑/悲观基调"
        elif avg < 0:
            tone = "偏低沉"
        else:
            tone = "中性偏暖"
        memo.record_lesson(
            "pacing",
            f"析情绪数据: Ch{ch} 平均情感值={avg:.2f} ({tone})，"
            f"样本段数={len(scores)}。WRITER 保持此情绪基调。",
            ch,
        )
```

把确定性数据（析算出来的）织入 Style Memo，而不是留在 JSON 文件里没人读。

**改动 b：`src/agent/nodes/read.py` 接收情绪信号**

在 `_build_context_signals()` 函数里加一个信号：

```python
def _build_context_signals(state: TranslatorState) -> str:
    # ... 现有的 dialect/measurements/onomatopoeia/idioms/sensitive 信号 ...

    # ── 情绪曲线信号（从析的 Style Memo 注入）──
    style_memo = state.get("style_memo", "")
    if "析情绪数据" in style_memo:
        # 从 style memo 里提取当前章节的情绪提示
        # （seed_from_analyze 已经按章写进了 pacing 抽屉）
        pass  # style_memo 已经通过 read_relevant() 包含了情绪数据
```

实际上不需要单独提取——`read_relevant()` 已经把 pacing 抽屉里的最新条目注入到 `style_memo` 字段了。析的情绪数据播种到 pacing 抽屉后，READ 节点**自动**就能看到。

### 预期效果

READ 节点在分析第 50 章时，context_signals 里包含：

```
[析情绪数据] 本章原文情绪基调：偏低沉 (-0.3)。
前章 Ch49 情绪：+0.6 (高昂/乐观基调)。
情绪正在下行。注意本章的文化缺口可能带有压抑和内在紧张。
```

READ 不需要重新猜情绪了——它知道原文的情绪曲线，可以把脑力省下来做图像缺口的深度分析。

---

## 第四优先：接入析的知识图谱（半天）

### 为什么编译需要关系图谱

西渡现在从析拿术语表（`seed_from_analyze.py` 播种到 ExactGlossary）。但从析能拿到的不只是"角色叫什么"——还有"角色之间什么关系"。

具体场景：
- READ 节点分析"角色 A 对角色 B 说了一句话"时，如果知道 A 和 B 在原文里 APPEARS_WITH 286 次，它就能判断"这两个角色之间的对话风格应该是熟悉的/有默契的"，而不是重新从零理解
- 翻译角色对话时，BELONGS_TO 关系告诉西渡"这个角色所处的势力/层级"，影响译文里的正式程度和称呼方式

### 怎么改

**改动：`scripts/seed_from_analyze.py` 新增关系数据播种**

```python
def _seed_relations(book_id: str, book_dir: str, memo: StyleMemoStore):
    """从析的知识图谱播种角色关系信息。"""
    relations_path = os.path.join(book_dir, "relations.json")
    if not os.path.exists(relations_path):
        return

    relations = json.loads(Path(relations_path).read_text(encoding="utf-8"))

    # 提取高频 APPEARS_WITH 关系（≥10 次同场的角色对）
    appears_with = [r for r in relations.get("relations", [])
                    if r.get("type") == "APPEARS_WITH" and r.get("frequency", 0) >= 10]

    # 按频次排序，取 top 20
    appears_with.sort(key=lambda r: r.get("frequency", 0), reverse=True)
    for r in appears_with[:20]:
        src = r.get("source", "?")
        tgt = r.get("target", "?")
        freq = r.get("frequency", 0)
        memo.record_lesson(
            "characters",
            f"[析关系数据] {src} 与 {tgt} 原文同场 {freq} 次。"
            f"WRITER 保持这两个角色之间的熟悉度和对话语气。",
            0,
        )

    # 提取 BELONGS_TO 关系（角色→势力归属，影响称呼方式）
    belongs_to = [r for r in relations.get("relations", [])
                  if r.get("type") == "BELONGS_TO"]
    for r in belongs_to:
        char = r.get("source", "?")
        faction = r.get("target", "?")
        memo.record_lesson(
            "characters",
            f"[析关系数据] {char} 属于 [{faction}]。"
            f"翻译时注意该角色使用的自称和敬称应符合其势力层级。",
            0,
        )
```

关系数据写入 characters 抽屉——因为这直接影响角色对话的翻译方式。写入时机和现有术语播种一样：翻译开始前跑一次 `seed_from_analyze.py`。

### 预期效果

翻译一本新书时，seed 脚本在播种完术语库之后，追加：

```
[析关系数据] 罗峰 与 巴巴塔 原文同场 286 次。
  WRITER 保持这两个角色之间的熟悉度和对话语气。

[析关系数据] 罗峰 属于 [极限武馆]。
  翻译时注意该角色使用的自称和敬称应符合其势力层级。
```

从第一章起，READ 和 WRITE 就知道"谁跟谁是绑定出场的"，不需要靠自己从第一章慢慢猜。

---

## 第五优先：`seed_from_analyze.py` 整合为 `extraction.json` 全量消费（1 天）

### 现状

`seed_from_analyze.py` 现在是 338 行，消费了析产出的：
- `terms[]`（→ ExactGlossary 播种）
- `worldbuilding{}`（→ terms.md + bridges.md 播种）
- `scenes[]`（→ pacing.md 播种）

没消费的：
- `sentiment_scores{}`（26 万段情绪标注）→ 本方案第三优先
- `relations.json`（9.7 万关系）→ 本方案第四优先
- `characters.json`（角色聚合）→ 已有路径但不能保证播种完整度

### 怎么改

重构 `seed_from_analyze.py`：

1. 把 emotions/sentiment_scores → pacing 抽屉的播种逻辑加进去（第三优先）
2. 把 relations.json → characters 抽屉的播种逻辑加进去（第四优先）
3. 加一个 `--dry-run` 参数：打印会播种什么数据，不实际写入
4. 加一个 `--verbose` 参数：打印每个播种步骤的统计（"播种了 142 条术语，23 条情绪记录，15 条高频关系"）

---

## 实施顺序

```
第一天：
  上午：characters.md 抽屉修复（style_memo.py 一个方法加 15 行）
  下午：prose.md 抽屉激活（style_memo.py 两个方法各加 10 行）

第二天：
  上午：接入析的情绪曲线（seed_from_analyze.py 新加函数 + 验证）
  下午：接入析的知识图谱（seed_from_analyze.py 新加函数 + 验证）

第三天：
  上午：seed_from_analyze.py 整合（dry-run + verbose + 全量覆盖确认）
  下午：跑《地府》第 1-20 章验证（旧行为不受影响 + 新数据正常注入）
```

每个优先级完成后，跑 `pytest tests/ -v` 确认 263 个测试全绿。

---

## 不需要做的事

- **不需要改 4-node 管道。** READ/WRITE/READBACK/FIX 的签名和行为不变
- **不需要改 API。** 西渡对外服务的行为完全不受影响
- **不需要跑全量重翻译。** Style Memo 增强只在**下一本新书**的翻译中生效。已翻译完的不动
- **不需要改 prompt。** Style Memo 通过 `read_relevant()` 注入——prompt 模板不需要改

---

## 做完后的效果

西渡 v0.16 和 v0.17 的区别：

| 维度 | v0.16 | v0.17 |
|------|-------|-------|
| terms.md | ✓ 有内容 | ✓ 不变 |
| bridges.md | ✓ 有内容 | ✓ 不变 |
| pacing.md | ✓ 有内容 | ✓ 新增析的情绪数据 |
| characters.md | ✗ 永远空 | ✓ 角色翻译决策 + 析的关系数据 |
| prose.md | ✗ 只在翻译味出现时记一笔 | ✓ 正面+反面都记，每章更新 |
| 析情绪曲线 | ✗ 数据躺在文件里 | ✓ 播种到 pacing 抽屉，READ 自动可见 |
| 析知识图谱 | ✗ 数据躺在文件里 | ✓ 播种到 characters 抽屉，翻译前就知道角色关系 |
