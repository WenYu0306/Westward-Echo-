"""Build the cold reader's one-page briefing (no LLM).

【中文·这个文件是干什么的】
冷读者的设定是「毫不知情的外国读者」，但也不能真的一无所知——否则它连
人物是谁都不知道，会把「没看过前文」误判成「这段写得烂」。所以这里给它
一份简短的背景说明，只含三类信息：前情提要 / 人物表 / 已确定的术语。

原来这段逻辑挤在 graph.py 的 TranslationAgent._build_cold_read_context 里，
抽成自由函数。前情提要缓冲区（_chapter_context）仍留在 agent 上（它被测试
直接访问），这里只负责「把缓冲区 + 术语库 + 经验本拼成须知」。
"""
from __future__ import annotations

import re

# 【中文】CJK 统一表意文字。冷读简报里出现任何一个字都意味着"盲读"失效。
_CJK = re.compile(r'[一-鿿]')


def build_cold_read_context(
    chapter_context: list[str],
    exact_store,
    style_memo,
    chapter_number: int = 0,
    target_lang: str = "en-US",
) -> str:
    """Build a one-page briefing for the cold reader — no LLM needed.

    ``target_lang`` scopes the character roster: a store holding several
    languages must not brief the reader with another job's renderings.

    【中文·给冷读者写一页"读者须知"】
    冷读者必须「盲读」英文成品，所以这份须知里**绝对不能出现中文原文**。
    """
    parts = []

    # ── 1. What's happened recently ──
    # 【中文·第一块：前情提要】
    if chapter_context:
        parts.append("## PREVIOUSLY (recent chapter summaries)\n")
        for entry in chapter_context:
            parts.append(f"- {entry}")
        parts.append("")
    elif chapter_number > 1:
        parts.append("## PREVIOUSLY\n"
                     f"You are reading Chapter {chapter_number}. "
                     "This is an early chapter.\n")

    # ── 2. Who's who (from exact glossary) ──
    # 【中文·第二块：人物表】从术语库里挑出"看起来像人名"的条目，只印英文名。
    known = exact_store.to_dict(target_lang=target_lang)
    char_list = []
    for cn, en in known.items():
        if len(cn) >= 2 and len(en) >= 2:
            # Render the English name only. The cold reader is a blind
            # target-language reader, so the trailing Chinese source name
            # `(cn)` was a source-text leak (clause 10) — metadata-level,
            # but stripping it makes the roster strictly blind.
            char_list.append(f"  - **{en}**")
        if len(char_list) >= 15:
            break   # 【中文】最多列 15 个，再多就把提示词撑长了
    if char_list:
        parts.append("## CHARACTERS\n" + '\n'.join(char_list) + "\n")

    # ── 3. Key terms from style memo ──
    # 【中文·第三块：已确定的术语】经验本里的行形如 `[chN] 中文 → 英文 // 备注`。
    # 只把箭头右侧的英文译法写给冷读者——整行照搬会把中文源词也带进去，
    # 冷读者就不再是"盲读"（实测 58/58 章都携带中文源名）。
    terms_text = style_memo.read_all()
    term_lines = []
    for line in terms_text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('[') and '→' in stripped:
            rendering = stripped.split('→', 1)[1].strip()
            rendering = rendering.split('//', 1)[0].strip()
            if not rendering or _CJK.search(rendering):
                continue
            term_lines.append(f"- **{rendering[:120]}**")
            if len(term_lines) >= 8:
                break   # 【中文】同样限量，最多 8 条
    if term_lines:
        parts.append("## ESTABLISHED TERMINOLOGY\n")
        parts.extend(term_lines)
        parts.append("")

    # A briefing with any one section is still worth sending: an off-by-one
    # here (`<= 1`) silently discarded a chapter-2+ briefing that had only
    # the "early chapter" line, or only a character roster.
    if not parts:
        return ""
    return '\n'.join(parts)
