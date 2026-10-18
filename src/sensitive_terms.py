"""Sensitive term classification for cultural-context warnings.

Some terms need explicit handling because literal translation would carry
harmful Western religious/cultural connotations. This module provides
contextual warnings injected into every chapter's translation prompt when
these terms appear.

【中文·这个文件是干什么的】
定义一组"敏感词"及其翻译注意事项。这里的"敏感"不是政治敏感，
而是**文化误译风险**——这些中文词如果直译成英文，会带上完全错误的
西方宗教/文化含义，让外国读者产生严重误解。

例如："上身"在中文网文里通常是"仙家附体帮忙"（正面），
但如果直译成 "possessed"，英文读者会联想到恶魔附身（负面）。
所以必须在提示词里明确警告 AI：这个词不能那样翻。

【中文·它在整个流程里的位置】
被翻译流水线的 READ 节点调用：每章翻译前，扫描原文是否包含这些词，
如果有，就把对应的警告拼进提示词里，让 WRITE 角色知道该怎么处理。

【中文·核心概念】
· SENSITIVE_TERMS —— 一个字典，键是中文词，值是英文警告说明。
  这段英文说明会原样塞进给 AI 的提示词里，所以必须是英文。
· build_sensitive_term_context() —— 扫描章节文本，把命中的警告
  拼成一段格式化的提示词片段返回。
"""


# ── Chinese source-text sensitive terms ──────────────────────
# Terms that need explicit cultural-context warnings in every chapter
#
# 【中文·源文本敏感词表】
# 每个词条的格式：中文词 → 英文警告说明。
# 警告说明是给 AI 看的，告诉它这个词的正确文化含义和禁止使用的译法。
# ⚠️ 这些英文字符串是喂给大模型的提示词内容，属于业务数据，不要修改。
SENSITIVE_TERMS = {
    # "上身"：正面含义，仙家显灵帮助主角，绝非恶魔附身
    "上身": (
        "⚠️ POSITIVE spirit possession (仙家 takes form to help). "
        "NEVER 'possessed'. Use 'the Master descends' or 'spirit takes form'."
    ),
    # "附体"：与"上身"类似，神灵附体而非恶魔侵占
    "附体": (
        "⚠️ Similar to 上身. Spirit embodiment, not demonic possession."
    ),
    # "请神"：神圣的祈请仪式，不是召唤邪灵
    "请神": (
        "⚠️ Sacred invocation ritual. NEVER 'summoning' (evil connotation). "
        "Use 'invoke the spirits' or 'call upon the Masters'."
    ),
    # "地府"：中国神话中的官僚化阴间机构，不是基督教的地狱
    "地府": (
        "⚠️ Bureaucratic afterlife, not Hell. "
        "Use 'Underworld' or 'Netherworld Court'. NEVER 'Hell' or 'Hades'."
    ),
    # "鬼"：含义取决于上下文，可能是中性的亡灵也可能是恶灵
    "鬼": (
        "⚠️ Context-dependent. Can be 'ghost' (neutral), 'spirit' (neutral), "
        "or 'demon' (evil). Check context."
    ),
    # "仙"：仙侠语境下是修仙者，民俗语境下是守护灵，绝不是西方童话仙子
    "仙": (
        "⚠️ Context-dependent. xianxia=Immortal, folk_religion=Master/Spirit Guardian. "
        "NOT 'fairy' or 'god'."
    ),
}

# ── Per-language critical warnings ──────────────────────────
# Language-specific content safety rules injected regardless of source text.
#
# 【中文·按目标语言的额外警告】
# 目前代码里这个区域预留了位置但还没有实际内容。
# 未来可以针对不同目标语言（西班牙语、德语等）添加特定的文化安全规则。


def build_sensitive_term_context(text: str, target_lang: str = "en-US") -> str:
    """If sensitive terms appear in the chapter, return a warning block for the prompt.

    【中文·扫描章节文本，生成敏感词警告片段】

    遍历 SENSITIVE_TERMS 里的每个词，检查它是否出现在本章正文中。
    如果有命中的词，就把对应的警告拼成一段 Markdown 格式的提示词片段。
    这段片段会被拼进 WRITE 角色的提示词里，让它翻译时避开错误译法。

    【参数】
      text        章节正文（中文原文）
      target_lang 目标语言代码，默认 "en-US"。
                  目前所有警告都是英文的，这个参数预留给未来多语言支持。

    【返回】
      有命中 → 格式化的警告文本块（Markdown 格式）
      无命中 → 空字符串（不会往提示词里塞多余内容）
    """
    parts = []

    # 【中文】用字典推导式找出本章正文中包含的所有敏感词及其警告。
    found = {term: note for term, note in SENSITIVE_TERMS.items() if term in text}
    if found:
        lines = [
            "## TERMINOLOGY WARNINGS",
            "The following terms appear in this chapter. Handle with care:",
        ]
        for term, note in found.items():
            lines.append(f"- **{term}**: {note}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)
