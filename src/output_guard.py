"""Output quality guard — regex/keyword checks the LLM cannot judge itself.

【中文·这个文件是干什么的】
这是翻译成品的**质量安检门**。AI 翻完一章后，这个模块用纯规则（不调用 AI、不花钱）
检查译文里有没有不该出现的东西，发现问题就报警或自动清除。

【中文·它检测什么问题】
  ① **AI 废话残留**：AI 有时候会在译文开头写"好的，以下是我的翻译："
     "Now let me translate..."这类元评论。读者看到这些会出戏，必须删掉。
  ② **中文字符残留**：AI 偶尔会在英文译文里漏几个没翻译的中文字，
     比如把"治安"直接留在英文段落里。
  ③ **JSON 信封残留**：AI 有时会把整章译文包在 JSON 格式里返回
    （比如 `{"translated_text": "..."}`），如果解析失败，
     这些 JSON 标记就会混进成品。
  ④ **AI 脚手架文字**：AI 自己发明的标记，比如"END OF CHAPTER"
    （章节结束）、"As an AI"（作为 AI）等，不属于正文内容。

【中文·它在流程里的位置】
在 WRITE 节点产出译文后被调用，是交付前的最后一道防线。
`check_and_record()` 还会把发现的问题记入事件追踪系统，用于后续分析。

【中文·本文件的主要函数】
  check_translation_output()  —— 检查 AI 废话和过短译文
  has_untranslated_chinese()  —— 检查有没有残留中文字符
  strip_chinese_residue()     —— 自动删除残留的中文字符
  sanitize_translation()      —— 自动删除 AI 废话
  check_and_record()          —— 检查 + 记录到事件追踪系统
  json_residue_on_line()      —— 检查单行是否是 JSON 信封残留
  meta_text_on_line()         —— 检查单行是否是 AI 脚手架文字
  has_json_envelope_residue() —— 检查整篇是否有 JSON 信封残留
  has_meta_text_leak()        —— 检查整篇是否有脚手架文字泄漏
"""

import re

# 【中文·AI 废话匹配规则】
# 一组正则表达式（一种"文字花样搜索规则"，可以一次匹配一类符合某种模式的文字），
# 用来识别 AI 在译文里留下的元评论。每条规则包含一个正则和一个描述标签。
# ⚠️ 这是业务数据，改动会影响检测准确率，不要修改内容。
CHATTER_PATTERNS = [
    (
        r"(?im)^(Now let me|Let me|I will|I'll)\s+.*?"
        r"(compile|translate|provide|generate|write)\b.*$",
        "LLM chatter: translation meta-commentary",
    ),
    (
        r"(?im)^Here (is|are)\s+(the|my)\s+(translation|output|result).*$",
        "LLM chatter: output preamble",
    ),
    (
        r"(?im)^(Sure|OK|Alright|Okay|Great),?\s+(here|let me).*$",
        "LLM chatter: confirmation preamble",
    ),
    (r"(?im)^(Note|Please note|Important):\s", "LLM chatter: editorial note"),
]

# 【中文·中文字符检测规则】
# 匹配任何 CJK 统一汉字（Unicode 范围 U+4E00 到 U+9FFF）。
# 如果英文译文里出现了这些字符，说明有内容没被翻译。
CHINESE_CHAR_PATTERN = re.compile(r'[一-鿿]')

# 【中文·最短译文长度阈值】
# 少于 50 个字符的译文几乎肯定是异常的（AI 可能拒绝翻译或只输出了摘要）。
MIN_TRANSLATION_CHARS = 50


def check_translation_output(text: str) -> list[str]:
    """Run all quality checks. Returns list of warning messages (empty = clean).

    【中文】对译文跑一遍所有质量检查，返回发现的问题列表。
    · 参数 text：AI 产出的译文文本。
    · 返回：警告消息列表。空列表表示一切正常。
    · 检查项：① 译文是否为空或过短 ② 是否包含 AI 废话模式。
    · 为什么存在：AI 不是每次都乖乖输出纯译文，有时候会夹带私货，
      这道检查确保交付给读者的内容是干净的。
    """
    if text is None:
        return ["EMPTY: translation is None"]

    warnings = []

    # 译文为空或太短，可能是 AI 拒绝翻译或出了错
    if not text or len(text.strip()) < MIN_TRANSLATION_CHARS:
        warnings.append(f"EMPTY: translation is too short ({len(text) if text else 0} chars)")
        return warnings

    # 逐条检查 AI 废话模式
    for pattern, description in CHATTER_PATTERNS:
        if re.search(pattern, text):
            warnings.append(description)

    return warnings


def has_untranslated_chinese(text: str) -> bool:
    """Check if Chinese characters remain in the translated output.

    【中文】检查译文里是否还残留中文字符。
    · 参数 text：译文文本。
    · 返回：True 表示发现了中文字符，False 表示没有。
    · 为什么存在：哪怕只剩一个中文字，也说明翻译不完整。
    """
    return bool(CHINESE_CHAR_PATTERN.search(text))


def find_untranslated_chinese(text: str) -> list[str]:
    """Return list of Chinese words found in the translated output.

    【中文】找出译文里所有残留的中文字符，去重后返回。
    · 参数 text：译文文本。
    · 返回：一个列表，包含所有发现的中文字符（每个字符只出现一次）。
    · 坑：返回的是单个字符而非词语，因为正则按字符匹配。
      比如"治安"会变成 ['治', '安'] 两个独立字符。
    """
    return list(set(CHINESE_CHAR_PATTERN.findall(text)))


def strip_chinese_residue(text: str) -> tuple[str, int]:
    """Remove isolated Chinese characters from translated text.

    Aggressive strategy: delete every CJK character.  LLMs sometimes
    leak 2-6 source characters in long translations — this ensures
    the reader never sees them.  Returns (cleaned_text, count_removed).

    【中文】暴力清除译文里所有残留的中文字符。
    · 参数 text：译文文本。
    · 返回：(清理后的文本, 删除的字符数量)。
    · 为什么用暴力策略：AI 漏翻的中文字通常只有 2~6 个，散落在长文里，
      逐个判断上下文太复杂且容易误判。直接全删最安全——
      这些残留字符对英文读者来说本来就是乱码，删了不影响阅读。
    · 坑：删除后可能留下多余空格或空行，所以后面紧跟两步清理：
      把连续多个空格合并成一个，把三个以上换行合并成两个。
    """
    if not text:
        return text, 0
    before = len(text)
    cleaned = CHINESE_CHAR_PATTERN.sub("", text)
    # Collapse whitespace that the deletion may have left behind
    cleaned = re.sub(r" {2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(), before - len(cleaned)


def check_and_record(
    text: str,
    job_id=None,
    chapter_num=None,
    target_lang: str = "en-US",
) -> list:
    """Run quality checks and record warnings to the event store.

    This is the recommended entry-point — it mirrors ``check_translation_output``
    but additionally persists each warning for analytics.

    【中文】跑质量检查并把结果记入事件追踪系统（推荐的入口函数）。
    · 参数 text：译文文本。
    · 参数 job_id：翻译任务 ID，用于关联到具体任务。
    · 参数 chapter_num：章节号。
    · 参数 target_lang：目标语言代码。
    · 返回：警告消息列表（同 check_translation_output，但多了中文残留检查）。
    · 为什么存在：光检查不够，还要记录下来才能做统计分析，
      比如"哪种问题最常见""哪个模型出错最多"。
    · 坑：这里用了延迟导入（from .error_tracker import record_event），
      因为 error_tracker 模块可能在某些环境下不可用。
    """
    warnings = check_translation_output(text)

    # ── Chinese character residue check ──
    # 额外检查中文字符残留（check_translation_output 不包含这项）
    if has_untranslated_chinese(text):
        chars = find_untranslated_chinese(text)
        cn_warning = (
            f"UNTRANSLATED: {len(chars)} Chinese characters found in output: "
            f"{', '.join(chars[:5])}"
        )
        warnings.append(cn_warning)

    if warnings:
        from .error_tracker import record_event
        for w in warnings:
            # 根据警告类型分类，方便后续按类别统计
            event_type = "guard_warning"
            if w.startswith("EMPTY:"):
                event_type = "empty_output"
            elif "chatter" in w.lower():
                event_type = "chatter_detected"
            elif "UNTRANSLATED" in w:
                event_type = "untranslated_chinese"
            record_event(job_id, chapter_num, event_type, w, target_lang)

    return warnings


def sanitize_translation(text: str) -> str:
    """Remove known bad patterns from translation output.

    【中文】自动清除译文里的 AI 废话模式。
    · 参数 text：译文文本。
    · 返回：清理后的文本（AI 废话行被整行删除）。
    · 为什么存在：有些 AI 废话是可以安全删除的（比如"好的，以下是翻译："），
      删了不影响正文内容。这个函数做自动修复，不需要人工介入。
    · 坑：只处理 CHATTER_PATTERNS 里的模式，不处理中文残留或 JSON 信封。
      那些需要其他函数处理。
    """
    for pattern, _ in CHATTER_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.MULTILINE)
    return text.strip()


# ── JSON-envelope residue & scaffolding leak ────────────────────────────
# Single source of truth for these two defect classes.
#
# scripts/scan_deliverable.py imports these so the *production* guard (runs on
# each finished chapter inside WRITE) and the *post-hoc* full-corpus scan can
# never drift apart — the §八 failure mode where two copies of one rule
# silently diverge. Before this, scan_deliverable held the only copy and the
# production path had no equivalent check at all, which is how Ch366 (```json
# fence + bare `{`), Ch1472 (whole chapter sealed in an unpacked
# {"translated_text": ...} envelope) and Ch1077/Ch1286 ("End of Chapter."
# scaffolding) reached a delivered file.
#
# 【中文·JSON 信封残留和 AI 脚手架泄漏检测】
# AI 有时候会把译文包在 JSON 格式里返回，比如：
#   {"translated_text": "Chapter content here..."}
# 正常情况下代码会解析这个 JSON 取出正文，但如果解析失败，
# 整个 JSON 结构就会混进成品文件里。历史上真的发生过：
#   · 第 366 章：出现了 ```json 代码围栏和裸 { 符号
#   · 第 1472 章：整章被包在未解析的 JSON 信封里
#   · 第 1077/1286 章：出现了 AI 自编的 "End of Chapter." 标记
# 这些规则是唯一的权威来源，生产守卫和事后扫描都从这里取规则，
# 避免两份规则各自演化导致不一致。

# 【中文】裸 JSON 行：如果某一行恰好只是这些符号之一，
# 那它是 JSON 信封的一部分，不是正文内容。
_BARE_JSON_LINES = frozenset(("{", "}", "},", "{,"))
# 【中文】JSON 代码围栏标记
_JSON_FENCE = "```json"
# 【中文】JSON 信封里的字段名，这些是内部字段，不会出现在正常译文中
_JSON_KEYS = ("translated_text", "chapter_title_en", "new_terms_found",
              "polished_text", "changes_made")
# An envelope key followed by a colon, ANYWHERE on the line — not just at line
# start. The earlier ``startswith(key)`` form only caught *pretty-printed*
# envelopes (where the key begins its own line, as in Ch1472's two-line shape).
# A *compact* truncated envelope — ``{"translated_text": "Grand Warden...`` all
# on one line, cut off before the closing brace — starts with ``{``, so the key
# was not at line start and slipped through. Measured: the old rule returned
# None for that line. These are internal field names; they never occur in a
# legitimately translated chapter, so a hit always means an envelope leaked.
# 【中文·JSON 字段名匹配规则】
# 用正则匹配任意位置出现的 JSON 字段名（不只是行首）。
# 早期版本只检查行首，结果紧凑格式的 JSON（整行以 { 开头）漏检了。
_JSON_KEY_RE = re.compile(r'"(' + "|".join(_JSON_KEYS) + r')"\s*:')


# Scaffolding the model invents around the translation. These are NOT in the
# prompt or anywhere in src/ — they are LLM hallucination, which is exactly why
# a guard (not the prompt) has to catch them. Line-anchored: "END OF CHAPTER"
# at line start is scaffolding, the same words mid-sentence are legitimate.
# 【中文·AI 脚手架文字匹配规则】
# AI 有时候会自己发明一些标记文字，比如"END OF CHAPTER""As an AI"。
# 这些不在提示词里，完全是 AI 的幻觉，所以只能靠守卫来拦截。
# 注意：这些规则是行锚定的——"END OF CHAPTER"在行首才是脚手架，
# 如果在句子中间出现（比如正文提到"end of chapter"）则是合法的。
_META_PATTERNS = [
    (re.compile(r"^END OF CHAPTER\b", re.I), "chapter-end scaffolding"),
    (re.compile(r"\bAs an AI\b", re.I), "model self-reference"),
    (re.compile(r"\bI cannot (?:fulfil|comply|provide)\b", re.I), "model refusal"),
    (re.compile(r"^(?:Note|Disclaimer):\s", re.I), "model preamble"),
]


def json_residue_on_line(line: str) -> str | None:
    """Label for this line if it is JSON-envelope residue, else ``None``.

    Line-oriented so the production guard and the corpus scan apply the *same*
    rule to the *same* granularity. A whole finished chapter is checked by
    scanning its lines (see ``has_json_envelope_residue``).

    【中文】检查单行文本是否是 JSON 信封残留。
    · 参数 line：一行文本。
    · 返回：如果是残留，返回描述标签（如 "bare '{' line"）；否则返回 None。
    · 为什么按行检查：生产和事后扫描用同一套规则、同一粒度，
      避免两份代码各自判断导致结果不一致。
    · 检查三种情况：① 裸 JSON 符号行 ② ```json 围栏 ③ JSON 字段名。
    """
    stripped = line.strip()
    if stripped in _BARE_JSON_LINES:
        return f"bare {stripped!r} line"
    if _JSON_FENCE in stripped:
        return "```json fence"
    m = _JSON_KEY_RE.search(stripped)
    if m:
        return f'unpacked envelope key "{m.group(1)}"'
    return None


def meta_text_on_line(line: str) -> str | None:
    """Label for this line if it is LLM scaffolding/refusal, else ``None``.

    【中文】检查单行文本是否是 AI 脚手架文字或拒绝回复。
    · 参数 line：一行文本。
    · 返回：如果是脚手架文字，返回描述标签；否则返回 None。
    """
    for pattern, label in _META_PATTERNS:
        if pattern.search(line):
            return label
    return None


def has_json_envelope_residue(text: str) -> bool:
    """True if a finished chapter still carries the model's JSON envelope.

    【中文】检查整篇译文是否还带着 JSON 信封残留。
    · 参数 text：完整的译文文本。
    · 返回：True 表示发现了 JSON 残留，False 表示干净。
    · 原理：逐行扫描，只要有一行命中 json_residue_on_line 就算有残留。
    """
    if not text:
        return False
    return any(json_residue_on_line(line) for line in text.split("\n"))


def has_meta_text_leak(text: str) -> bool:
    """True if a finished chapter carries scaffolding like ``END OF CHAPTER``.

    【中文】检查整篇译文是否有 AI 脚手架文字泄漏。
    · 参数 text：完整的译文文本。
    · 返回：True 表示发现了脚手架文字，False 表示干净。
    · 原理：逐行扫描，只要有一行命中 meta_text_on_line 就算有泄漏。
    """
    if not text:
        return False
    return any(meta_text_on_line(line) for line in text.split("\n"))
