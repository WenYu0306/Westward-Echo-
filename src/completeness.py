"""Scene-level completeness check: did every source scene survive?

Why this exists
---------------
The WRITE agent is granted real creative authority — it may restructure,
compress, expand, cut and invent (see ``src/agent/prompts/write.py``). That
freedom is what makes the output read like native English fiction instead of a
translation, and it is the whole point of the pipeline.

But the prompt bounds that freedom in the same breath:

    "The translated_text field must contain the COMPLETE chapter. Do not
     abbreviate, summarize, or skip passages. Every scene in the source must
     have an equivalent in the output — even compressed scenes still convey
     their story function. If you received a reader analysis that says to cut
     something, compress it creatively, don't delete it silently."

Nothing verified that bound. The READBACK agent cannot: by design it never sees
the source ("READBACK is blind to the source", src/agent/graph.py), so it can
judge feel but never fidelity. The truncation guard only counts words. So a
chapter where WRITE silently dropped half the scenes passed every check the
pipeline had.

This module supplies the missing mirror: it compares the source chapter against
the output with signals that need no model and no API call.

Signals
-------
``glossary_missing``
    Glossary terms that occur in the source chapter but whose established
    English rendering never appears in the output. The prompt calls glossary
    terms "LAW", so a missing one is either a dropped scene or a broken
    consistency promise.
``dialogue_ratio``
    Opening-quote count in the output vs the source. Dropped conversations
    show up here before they show up anywhere else.
``length_ratio``
    Output characters per source Chinese character.
``para_ratio``
    Output paragraphs vs source paragraphs.

Calibration policy
------------------
This module **reports**; it does not yet gate. Thresholds for corpus-level
fiction vary by genre and by how much compression a given voice legitimately
does, and guessing them would repeat the project's oldest mistake — shipping a
number nobody validated. Record the metrics for a while, look at the
distribution, then set the gate from evidence.

Usage::

    from src.completeness import check_completeness
    report = check_completeness(source_text, translated_text, glossary)
    if report["flags"]:
        ...

【中文·这个文件是干什么的】
这是译文**完整性检查器**：对比中文原文和英文译文，检测 WRITE 角色
是不是偷偷丢掉了某些场景或段落。纯规则检查，不调用 AI、不花钱。

【中文·为什么需要它】
WRITE 角色被赋予了真正的创作自由——它可以重构、压缩、扩展甚至发明内容。
这种自由是让译文读起来像原生英文小说的关键。但提示词同时也要求
"不能跳过任何场景"。问题是：没有任何机制验证这条要求是否被遵守了。
冷读者（READBACK）看不到原文，只能判断感觉对不对，无法判断有没有漏翻。
字数守卫只数总字数，所以 WRITE 如果悄悄删了一半场景但把剩下的写长了，
字数可能还达标。这个模块补上了这个缺口。

【中文·它在流程里的位置】
在 WRITE 产出译文后被调用，作为质量检查的一环。
目前只做报告（记录指标），不做拦截（不会因为不完整就拒绝交付）。
原因是：阈值还没经过校准——不同类型小说的合理压缩比例不同，
拍脑袋定一个数字会重蹈项目历史上"上线一个没人验证过的阈值"的覆辙。

【中文·四个检测信号】
  ① glossary_missing —— 术语表里的词在原文出现了但译文里没有
  ② dialogue_ratio   —— 对话数量比（译文对话数 / 原文对话数）
  ③ length_ratio     —— 长度比（译文字符数 / 原文中文字符数）
  ④ para_ratio       —— 段落数比（译文段落数 / 原文段落数）

【中文·输出格式】
返回一个字典，包含所有指标和一个 flags 列表。
flags 为空表示没有发现明显问题；非空则列出触发的警告标签，
如 "EMPTY_OUTPUT""DIALOGUE_DROPPED""LENGTH_RATIO_LOW"。
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, Mapping

logger = logging.getLogger(__name__)

# 【中文·字符类型检测正则】
# CJK：匹配中文汉字（Unicode 范围 U+4E00-U+9FFF），用于统计原文中文字数
# LATIN：匹配英文字母，用于辅助判断
CJK = re.compile(r"[\u4e00-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")

# 【中文·引号字符集】
# 只数开引号（不数闭引号），因为数成对引号会把一行对话算两次，
# 而且如果某行对话被删了只剩闭引号，成对计数会掩盖问题。
# 原文用中文引号（"「『），译文可能用英文引号（"）或保留中文引号。
SRC_OPEN_QUOTES = ("\u201c", "\u300c", "\u300e")      # " 「 『
OUT_OPEN_QUOTES = ('"', "\u201c", "\u300c", "\u300e")

# A rendering like "Wuxin (No-Heart)" is stored WITH its gloss, but the prose
# normally uses only the bare name. Matching the gloss verbatim reported a
# false "missing term" for the protagonist of a 2301-chapter book, so strip
# parentheticals and trailing dash-clauses before searching.
# 【中文·术语渲染清理规则】
# 术语表里存的译名可能带括号注释（如 "Wuxin (No-Heart)"）或破折号后缀
# （如 "Huang Chao – the Butcher of Chang'an"），但正文里通常只用裸名。
# 如果拿完整的带注释版本去搜索，会误报"术语缺失"。
# 所以搜索前先去掉括号内容和破折号后缀，只留裸名。
_PAREN = re.compile(r"\([^)]*\)")
_DASH_TAIL = re.compile(r"[\u2013\u2014-].*$")
_SLASH_ALTS = "/"


def base_renderings(rendering: str) -> list[str]:
    """Bare English forms to search for, from a glossary rendering.

    ``"Wuxin (No-Heart)"``        -> ``["Wuxin"]``
    ``"Potter's Field / the common graves"`` -> both alternatives
    ``"Huang Chao – the Butcher of Chang'an"`` -> ``["Huang Chao"]``

    【中文】从术语表的译名中提取用于搜索的裸名形式。
    · 参数 rendering：术语表里存储的完整译名，可能带括号注释、破折号后缀或斜杠分隔的备选。
    · 返回：一个列表，包含所有可用于在译文中搜索的裸名。
      太短的（<3 字符）会被过滤掉，避免在其他单词内部误匹配。
    · 为什么存在：术语表里的译名格式多样，直接拿去搜索会误报。
    · 坑：斜杠 "/" 表示备选译法（如 "Potter's Field / the common graves"），
      每个备选都要单独提取和搜索。
    """
    out: list[str] = []
    for alt in (rendering or _SLASH_ALTS.join([])).split(_SLASH_ALTS):
        b = _PAREN.sub("", alt)
        b = _DASH_TAIL.sub("", b)
        b = b.strip(" ,;:")
        # Too short to search safely (would match inside other words).
        if len(b) >= 3:
            out.append(b)
    return out


def count_dialogue_spans(
    text: str,
    quotes: Iterable[str] = OUT_OPEN_QUOTES,
    *,
    ascii_halved: bool = False,
) -> int:
    """Count spoken spans — a proxy for how many lines of dialogue there are.

    Chinese prose marks speech with a distinct opening quote (``"``), so each
    occurrence is one span. ASCII uses the same character for open and close, so
    raw occurrences are DOUBLE the span count — comparing a Chinese source
    (1 per span) against an ASCII output (2 per span) inflated the ratio and
    made the output look like it carried more dialogue than it did. Pass
    ``ascii_halved=True`` when counting an English output.

    An odd count rounds up, so a single unbalanced quote still registers as one
    span rather than vanishing.

    【中文】统计文本中的对话段数（通过数开引号来近似）。
    · 参数 text：要统计的文本。
    · 参数 quotes：要计数的引号字符集。
    · 参数 ascii_halved：True 时将英文双引号的计数除以 2（向上取整）。
      因为英文双引号开闭相同，每段对话会被数两次。
    · 返回：对话段的估计数量。
    · 为什么需要 ascii_halved：中文引号 "" 开闭不同，数开引号就是对话数；
      英文引号 " 开闭相同，直接数会翻倍，导致比值虚高。
    """
    total = 0
    for q in quotes:
        c = text.count(q)
        if ascii_halved and q == '"':
            c = (c + 1) // 2
        total += c
    return total


def count_paragraphs(text: str) -> int:
    """【中文】统计非空行数作为段落数的近似值。"""
    return sum(1 for line in text.splitlines() if line.strip())


def check_completeness(
    source_text: str,
    translated_text: str,
    glossary: Mapping[str, str] | None = None,
    *,
    min_term_len: int = 2,
    min_length_ratio: float | None = 2.0,
) -> dict:
    """Compare a source chapter against its English output.

    Args:
        source_text: the Chinese chapter as fed to WRITE.
        translated_text: WRITE's English output for that chapter.
        glossary: ``{chinese_term: english_rendering}`` accumulated so far.
            Terms not present in *source_text* are ignored — only terms this
            chapter actually uses can tell us anything about this chapter.
        min_term_len: ignore glossary keys shorter than this; single characters
            match too much Chinese prose to be informative.

    Returns:
        A report dict. ``flags`` is empty when nothing looked wrong.

    【中文】对比原文和译文，生成完整性报告。
    · 参数 source_text：喂给 WRITE 的中文原文。
    · 参数 translated_text：WRITE 产出的英文译文。
    · 参数 glossary：当前积累的术语表 {中文词: 英文译名}。
      只检查本章实际用到的术语，无关术语不参与比较。
    · 参数 min_term_len：忽略短于此长度的术语键。单字术语在中文里太常见，
      匹配太多会导致噪声。默认 2，即至少两个中文字才算有效术语。
    · 参数 min_length_ratio：长度比的最低阈值。低于此值触发 LENGTH_RATIO_LOW。
      设为 None 可禁用此检查。默认 2.0，即译文至少应是原文字符数的 2 倍
      （因为中文一个字的信息量通常大于英文一个字母）。
    · 返回：报告字典，包含所有指标和 flags 列表。
    · 坑：glossary_missing 目前不作为 flag，只作为指标报告。
      原因是在《地府叫我小先生》上校准时发现：已知截断的章节有 8-24 个缺失术语，
      但健康的对照章节也有 5-16 个。范围重叠，无法区分缺陷和正常行为。
      这是因为 WRITE 被授权压缩和删减小提及，所以"术语在原文有、译文没有"
      可能是正常行为。要升级为 flag 需要重新校准。
    """
    src = source_text or ""
    out = translated_text or ""
    glossary = glossary or {}

    # 统计原文中文字数和译文总字符数
    src_cjk = len(CJK.findall(src))
    out_chars = len(out)
    length_ratio = round(out_chars / src_cjk, 2) if src_cjk else None

    # ── glossary coverage ────────────────────────────────────────────────
    # 筛选出本章实际用到的术语（原文中出现的、且长度达标的）
    used_terms = {cn: en for cn, en in glossary.items()
                  if cn and len(cn) >= min_term_len and cn in src}
    glossary_missing: list[str] = []
    for cn, rendering in used_terms.items():
        forms = base_renderings(rendering)
        # 如果术语的所有裸名形式都不在译文里出现，记为缺失
        if forms and not any(f in out for f in forms):
            glossary_missing.append(cn)

    # ── dialogue + structure ─────────────────────────────────────────────
    # 分别统计原文和译文的对话数、段落数，计算比值
    dlg_src = count_dialogue_spans(src, SRC_OPEN_QUOTES)
    dlg_out = count_dialogue_spans(out, OUT_OPEN_QUOTES, ascii_halved=True)
    dialogue_ratio = round(dlg_out / dlg_src, 2) if dlg_src else None
    paras_src = count_paragraphs(src)
    paras_out = count_paragraphs(out)
    para_ratio = round(paras_out / paras_src, 2) if paras_src else None

    # 根据阈值判定是否触发警告标志
    flags: list[str] = []
    if not out.strip():
        flags.append("EMPTY_OUTPUT")
    # 原文至少有 4 段对话但译文一段都没有 → 对话被整体丢弃
    if dlg_src >= 4 and dlg_out == 0:
        flags.append("DIALOGUE_DROPPED")
    # 原文至少 400 字且长度比低于阈值 → 译文可能严重缩水
    if (min_length_ratio is not None and length_ratio is not None
            and src_cjk >= 400 and length_ratio < min_length_ratio):
        flags.append("LENGTH_RATIO_LOW")

    # NOT a flag: `glossary_missing`.
    # Calibrated against 《地府》 — the 11 chapters known to be truncated had
    # 8-24 missing terms, but so did all 8 healthy control chapters (5-16).
    # The ranges overlap, so the signal cannot separate defect from normal.
    # The cause is by design: WRITE is granted authority to compress and cut
    # minor mentions, so "term appears in source, not in output" is expected
    # behaviour, not evidence of a dropped scene. It is reported as a metric
    # only; do not promote it to a flag without a fresh calibration.

    report = {
        "src_cjk": src_cjk,
        "out_chars": out_chars,
        "length_ratio": length_ratio,
        "paras_src": paras_src,
        "paras_out": paras_out,
        "para_ratio": para_ratio,
        "dialogue_src": dlg_src,
        "dialogue_out": dlg_out,
        "dialogue_ratio": dialogue_ratio,
        "glossary_used": len(used_terms),
        "glossary_missing": glossary_missing,
        "flags": flags,
    }
    if flags:
        logger.warning(
            "COMPLETENESS: %s (cjk=%d out=%d ratio=%s dialogue %d->%d "
            "terms %d/%d present)",
            ",".join(flags), src_cjk, out_chars, length_ratio,
            dlg_src, dlg_out, len(used_terms) - len(glossary_missing),
            len(used_terms),
        )
    return report
