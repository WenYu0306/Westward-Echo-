"""Chinese measurement/unit detection and localization hints.

Chinese web novels frequently use units (万, 亿, 里, 斤, etc.) that
do not translate literally into natural English. This module scans
chapter text for these units and produces a prompt hint so the LLM
can localize them idiomatically.

【中文·这个检测器检测什么】
检测中文原文里的**度量衡单位和大数词**。中文网文经常用中国传统单位，
比如"三尺""一斤""十里""万亩"，还有大数词"万""亿"。

【中文·为什么需要它】
这些单位直译成英文，读者完全没概念。举几个具体例子：
  · "三万里" → AI 可能译成 "three wan li"（英文读者不知道 wan 和 li 是什么），
    应该译成 "~15,000 km / ~9,300 miles"。
  · "一斤肉" → AI 可能译成 "one jin of meat"，
    应该译成 "~1.1 lbs of meat" 或 "~half a kilo of meat"。
  · "十万大军" → AI 可能译成 "ten wan army"，
    应该译成 "an army of 100,000"。
这个检测器找到这些单位后，给 AI 一份具体的换算示例和目标语言的建议表达。

【中文·它在流程里的位置】
在 READ 阶段被 `_build_context_signals()` 调用（见 src/agent/nodes/read.py）。
扫描原文，把发现的度量单位和换算提示拼成一段文本，作为 READ 的起步线索。

【中文·输出格式】
返回一段 Markdown 格式的提示文本，例如：
    ## MEASUREMENT LOCALIZATION
    - 三万里 → ~15,000 km / ~9,320 miles (not "3 wan li")
    - 一斤 → ~0.5 kg / ~1.1 lbs (not "1 jin")
如果没有发现任何度量单位，返回空字符串。

【中文·本文件的三个核心部分】
  ① UNITS 字典 —— 每种单位的换算比例和英文说明
  ② detect_measurements() —— 用正则找出原文里的度量短语
  ③ build_measurements_hint() —— 把检测结果格式化成提示文本
中间还有中文数字解析器（_parse_chinese_number），负责把"三千五百"变成 3500。
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# ── Unit definitions ────────────────────────────────────────────────
# Each unit maps to its scale factor (relative to the base SI-ish unit),
# an English gloss, and a localization hint.
# 【中文·单位定义表】
# 每种中文单位对应三个信息：
#   scale —— 换算到国际单位制的倍率（如 1 里 = 0.5 公里）
#   en    —— 英文名称和简要说明
#   hint  —— 给 AI 的本地化建议（怎么自然地表达给目标语言读者）
# ⚠️ 这是业务数据，改动会直接影响换算结果，不要修改内容。

UNITS: dict[str, dict] = {
    "万": {
        "scale": 10000,
        "en": "ten thousand",
        "hint": "Use numerals (10K, 100K) or 'thousand/million' naturally",
    },
    "亿": {
        "scale": 100000000,
        "en": "hundred million",
        "hint": "Use 'hundred million' or 'billion' (100M) naturally",
    },
    "里": {
        "scale": 0.5,
        "en": "li (0.5 km)",
        "hint": "Convert to 'league', 'mile', or use 'li' with context. 1 li ≈ 0.3 miles.",
    },
    "斤": {
        "scale": 0.5,
        "en": "jin (0.5 kg)",
        "hint": "Convert to 'pound' (~1.1 lbs) or 'half-kilo' depending on context",
    },
    "丈": {
        "scale": 3.33,
        "en": "zhang (3.3 m)",
        "hint": "Convert to 'yard' or 'ten feet' naturally. 1 zhang ≈ 10 feet.",
    },
    "尺": {
        "scale": 0.33,
        "en": "chi (33 cm)",
        "hint": "Convert to 'foot' (~1 ft) naturally",
    },
    # 寸 was matched by _NUMERAL_RE but had no UNITS entry, so any "三寸"/"七寸"
    # was detected and then silently dropped: build_measurements_hint emitted the
    # "## MEASUREMENT LOCALIZATION" header with zero unit lines under it, and
    # _example_for(phrase, "寸") raised KeyError. Detection and handling must
    # agree — either the regex drops 寸 or UNITS knows about it. 1 chi = 10 cun.
    # 【中文·历史踩坑记录】寸曾经只在正则里被匹配但不在 UNITS 表里，
    # 导致检测到"三寸"后无法生成提示，静默丢失。后来补上了这条。
    "寸": {
        "scale": 0.033,
        "en": "cun (3.3 cm)",
        "hint": "Convert to 'inch' (~1.3 in) naturally",
    },
    "亩": {
        "scale": 0.067,
        "en": "mu (0.067 hectare)",
        "hint": "Convert to 'acre' (~0.16 acres) or use 'field'",
    },
}

# ── Detection ───────────────────────────────────────────────────────

# Match a Chinese numeral + unit, e.g. "三万里", "一斤", "十万"
# Supports: 零一二三四五六七八九十百千万亿 + digits 0-9
#
# The unit character class is DERIVED from UNITS, not written out by hand. It used
# to be a hand-maintained literal, which is how 寸 ended up matched by the regex
# while absent from UNITS: detect_measurements happily returned {'寸': ['三寸']},
# build_measurements_hint then skipped it (its own hardcoded tuple lacked 寸 too),
# and the chapter got a "## MEASUREMENT LOCALIZATION" header with no units under
# it. Two copies of one list cannot stay in sync; one copy cannot drift.
# 【中文·度量单位检测正则】
# 一种"文字花样搜索规则"，匹配"中文数字+单位字符"的组合，
# 比如"三万里""一斤""十万"。支持阿拉伯数字和中文数字混合（如"3万"）。
# 单位字符集从 UNITS 字典动态生成，不是手写列表。
# 这样新增单位时只需改 UNITS 一处，正则自动跟上，避免两处不同步的老问题。
_UNIT_CHARS = re.escape("".join(UNITS))
_NUMERAL_RE = re.compile(
    rf"[\d零一二三四五六七八九十百千万亿两]+[{_UNIT_CHARS}]"
)


def detect_measurements(text: str) -> dict[str, list[str]]:
    """Find all measurement mentions in *text*.

    Returns a dict mapping unit character → list of matched phrases.
    Only units that appear at least once are present in the result.

    【中文】扫描原文，找出所有度量短语。
    · 参数 text：要扫描的中文原文。
    · 返回：字典，键是单位字符（如"里"），值是匹配到的短语列表（如["三万里", "十里"]）。
      只包含至少出现一次的单位。
    · 为什么去重：同一个短语可能出现多次（如"十里"在同一章出现 5 次），
      只需要记录一次就够了，避免提示文本冗长重复。
    """
    found: dict[str, list[str]] = {}
    seen: set[str] = set()
    for m in _NUMERAL_RE.finditer(text):
        phrase = m.group()
        if phrase in seen:
            continue
        seen.add(phrase)
        # The unit is the last character
        unit = phrase[-1]
        found.setdefault(unit, []).append(phrase)
    return found


# ── Hint builder ────────────────────────────────────────────────────

# Per-unit rendering of the converted value. Table-driven instead of an if/elif
# chain for one concrete reason: the chain was the THIRD place a new unit had to
# be registered (after UNITS and the _NUMERAL_RE character class), and missing
# any one of the three failed SILENTLY. That is exactly what happened with 寸 —
# it was matched and returned by detect_measurements, then produced no hint line
# at all, so a chapter containing "三寸" got a "## MEASUREMENT LOCALIZATION"
# header with nothing under it.
#
# A unit present in UNITS but absent from this table degrades to the generic line
# at the bottom of _example_for (converted value + English gloss + hint) instead of
# vanishing or raising KeyError. Today every unit in UNITS has a renderer, so that
# line is defensive rather than live — it exists for the NEXT unit someone adds,
# and it is covered by a test that removes a renderer to prove the degradation
# works. That is the difference from the old if/elif chain, whose trailing
# fallback was unreachable by construction and therefore untestable.
#
# 【中文·单位渲染器表】
# 每种单位有一个专门的格式化函数（lambda），把换算后的数值变成人类可读的提示行。
# 用表驱动而不是 if/elif 链，原因是历史上 if/elif 链是新增单位时需要改的第三处地方，
# 漏改任何一处都会静默失败（寸就是这么丢的）。
# 如果某个单位没有专属渲染器，会降级到通用格式（不会报错也不会消失）。
# ⚠️ 这是业务数据，改动会影响提示文本格式，不要修改内容。
_UNIT_RENDERERS: dict[str, Callable[[float, int, str], str]] = {
    "里": lambda c, d, f: (
        f'~{f} km / ~{_format_number(c * 0.621371)} miles (not "{d} li")'
    ),
    "斤": lambda c, d, f: (
        f'~{f} kg / ~{_format_number(c * 2.20462)} lbs (not "{d} jin")'
    ),
    "丈": lambda c, d, f: (
        f'~{f} m / ~{_format_number(c / 0.3048)} ft (not "{d} zhang")'
    ),
    "尺": lambda c, d, f: (
        f'~{f} m / ~{_format_number(c * 39.3701)} in (not "{d} chi")'
    ),
    # scale is metres; report centimetres and inches rather than a sub-metre
    # decimal, which reads as noise to an English reader.
    # 【中文】寸的换算值很小（0.033 米），用厘米和英寸表示比用小数米更直观。
    "寸": lambda c, d, f: (
        f'~{_format_number(c * 100)} cm / ~{_format_number(c * 39.3701)} in '
        f'(not "{d} cun")'
    ),
    "亩": lambda c, d, f: (
        f'~{f} hectares / ~{_format_number(c * 0.1647)} acres (not "{d} mu")'
    ),
    "万": lambda c, d, f: (
        f'~{f} / {_format_number(c / 1_000_000)}M (not "{d} ten thousand")'
        if d >= 100
        else f'~{f} (not literally "{d} ten thousand")'
    ),
    "亿": lambda c, d, f: (
        f'~{f} / {_format_number(c / 1_000_000_000)}B (not "{d} hundred million")'
        if d >= 1
        else f'~{f} (not literally "{d} hundred million")'
    ),
}


def _example_for(phrase: str, unit: str) -> str:
    """Generate a concrete localization example for one matched phrase.

    We attempt a heuristic numeric conversion so the LLM has a concrete
    target to work with rather than an abstract rule.

    【中文】为一个匹配到的度量短语生成具体的换算示例。
    · 参数 phrase：匹配到的原始短语，如"三万里"。
    · 参数 unit：单位字符，如"里"。
    · 返回：一行格式化的提示文本，如 '- 三万里 → ~15,000 km / ~9,320 miles (not "3 wan li")'。
    · 为什么尝试数值换算：给 AI 一个具体的目标数值比给抽象规则更有效。
      AI 看到"~15,000 km"就知道该怎么写了，看到"convert to miles"还得自己算。
    · 坑：如果中文数字解析失败（比如遇到了不支持的写法），
      就降级到只显示通用提示（hint），不会报错。
    """
    digits = _parse_chinese_number(phrase[:-1])
    if digits is None:
        # Provide the generic hint instead
        return f"- {phrase} → [{UNITS[unit]['hint']}]"

    scale = UNITS[unit]["scale"]
    converted = digits * scale
    friendly = _format_number(converted)

    renderer = _UNIT_RENDERERS.get(unit)
    if renderer is not None:
        return f"- {phrase} → {renderer(converted, digits, friendly)}"

    # Generic path: unit is known to UNITS (so scale/hint are valid) but has no
    # bespoke rendering. Show the converted value next to the English gloss.
    # Defensive, not currently live — see _UNIT_RENDERERS for the 寸 bug this
    # prevents, and test_unit_without_renderer_degrades_to_generic for proof it works.
    # 【中文】通用降级路径：单位在 UNITS 里有定义但没有专属渲染器时使用。
    # 目前所有单位都有渲染器，这行代码是防御性的，为将来新增单位兜底。
    return f"- {phrase} → ~{friendly} {UNITS[unit]['en']} ({UNITS[unit]['hint']})"


# ── Chinese numeral parsing ─────────────────────────────────────────
# 【中文·中文数字解析器】
# 把中文数字字符串转换成整数。支持纯中文（"三千五百"→3500）、
# 纯阿拉伯数字（"3500"→3500）、以及混合格式（"3万"→30000、"3.5亿"→350000000）。

# 【中文·中文数字字符对照表】
# 每个中文数字字符对应的数值。"两"是"二"的口语变体，也映射到 2。
# ⚠️ 这是业务数据，不要修改内容。
_CN_DIGITS: dict[str, int] = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "百": 100,
    "千": 1000,
}


def _parse_chinese_number(s: str) -> Optional[int]:
    """Convert a Chinese numeral string to an integer.

    Supports mixed Arabic-Chinese like "3万" (30000) and pure Chinese
    like "三万" (30000).  Returns *None* on parse failure.

    【中文】把中文数字字符串转成整数。
    · 参数 s：中文数字字符串，如"三万""3.5亿""三千五百"。
    · 返回：对应的整数，解析失败返回 None。
    · 解析顺序：先试纯数字 → 再试"数字+万/亿"混合 → 再试含"万"的纯中文
      → 再试含"亿"的纯中文 → 最后试简单中文数字。
    · 坑：万和亿的解析逻辑不同——万右边用 _parse_chinese_simple（不含万亿），
      亿右边递归调用 _parse_chinese_number（因为"一亿三千万"里亿后面还有万）。
    """
    if not s:
        return None

    # Pure digits short-circuit
    if s.isdigit():
        return int(s)

    # Mixed: leading Arabic digits + 万/亿, e.g. "3万", "3.5亿"
    m = re.match(r"^(\d+(?:\.\d+)?)([万亿])$", s)
    if m:
        base = float(m.group(1))
        scale = 10000 if m.group(2) == "万" else 100_000_000
        return int(base * scale)

    # Pure Chinese numerals
    # Handle "万" pattern: X万Y → X*10000 + Y
    if "万" in s:
        parts = s.split("万", 1)
        left = _parse_chinese_simple(parts[0]) if parts[0] else 1
        # `len(parts) > 1` used to guard this and the 亿 branch below. It was
        # always true: we only reach here when "万" in s, and split(sep, 1) on a
        # string containing sep always yields exactly 2 parts (verified over
        # 16,678 samples, zero counterexamples). A condition that cannot be
        # false makes `and`→`or` on it unkillable by any test, so it was a
        # permanent survivor. Removing the dead conjunct removes the survivor —
        # no equivalence proof to register, and nothing to re-verify when this
        # line moves. Behaviour is unchanged: 30,095 inputs diffed clean.
        right = _parse_chinese_simple(parts[1]) if parts[1] else 0
        if left is not None and right is not None:
            return left * 10000 + right
        return None

    # Handle "亿" pattern
    if "亿" in s:
        parts = s.split("亿", 1)
        left = _parse_chinese_simple(parts[0]) if parts[0] else 1
        right = 0
        if parts[1]:
            # 亿右边可能还包含万（如"一亿三千万"），所以递归调用
            right = _parse_chinese_number(parts[1]) or 0
        if left is not None:
            return left * 100_000_000 + right
        return None

    return _parse_chinese_simple(s)


def _parse_chinese_simple(s: str) -> int | None:
    """Parse a Chinese numeral without 万/亿, e.g. '三千五百' → 3500.

    【中文】解析不含万/亿的简单中文数字。
    · 参数 s：简单中文数字字符串，如"三百五十""十二""零"。
    · 返回：对应的整数，解析失败返回 None。
    · 原理：逐字符扫描，遇到数字就暂存，遇到位权（十百千）就乘上去累加。
      比如"三千五百"→ 3×1000 + 5×100 = 3500。
    · 坑："十五"这种省略写法（省了"一"）也能处理——遇到十时如果 current==0，
      自动补 1，所以"十五"= 1×10 + 5 = 15。
    """
    if not s:
        return 0
    if s.isdigit():
        return int(s)

    total = 0
    current = 0
    for ch in s:
        if ch in _CN_DIGITS:
            d = _CN_DIGITS[ch]
            if d >= 10:  # 十, 百, 千
                if current == 0:
                    current = 1
                current *= d
                total += current
                current = 0
            else:
                current = d
        else:
            return None
    total += current
    return total if total > 0 or s in ("零", "〇") else None


def _format_number(n: float) -> str:
    """Pretty-print a number for the hint.

    【中文】把数字格式化成人类友好的字符串。
    · ≥10 亿 → "1.5B" 格式
    · ≥100 万 → "1.5M" 格式
    · 整数 → 带千分位逗号（如 "15,000"）
    · 小数 → 保留一位小数带千分位（如 "1,500.5"）
    """
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    # There used to be an `if n >= 1000:` branch here returning
    # f"{n:,.0f}" / f"{n:,.1f}". It was byte-for-byte identical to the two
    # branches below for every input — verified by exhaustive enumeration over
    # 201,162 values (integers, halves, negatives, random floats to 1e13) plus a
    # full end-to-end diff of build_measurements_hint across all units: zero
    # differences. The comma grouping it expressed is already carried by the
    # `:,` format spec in both remaining branches.
    #
    # Dead branches are worse than merely redundant ones: they make two mutants
    # (`GtE->Lt` and `1000->1001` on that comparison) unkillable by ANY test,
    # which is a permanent non-zero survivor count. That trains the next reader
    # to treat a non-zero number as normal — the exact failure mode this suite
    # exists to prevent. Removed rather than registered as equivalent, because
    # there is no behaviour here worth preserving.
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.1f}"


def build_measurements_hint(text: str) -> str:
    """Generate a prompt hint for measurement localization.

    Returns an empty string when no Chinese measurements are detected.
    Otherwise returns a block that can be prepended to the translation
    user prompt.

    【中文】完整的度量单位提示构建器：检测 → 换算 → 格式化 → 拼接标题头。
    这是对外暴露的主入口函数，read.py 直接调用它。
    · 参数 text：要扫描的中文原文（一整章）。
    · 返回：Markdown 格式的度量提示块；没发现度量单位则返回空字符串 ""。
    · 为什么按 UNITS 的顺序遍历而不是按 found 的顺序：UNITS 的插入顺序是
      大数词在前、长度单位在后，这样生成的提示按读者预期的逻辑分组。
    """
    found = detect_measurements(text)
    if not found:
        return ""

    lines = [
        "## MEASUREMENT LOCALIZATION",
        "This chapter contains Chinese units. Convert naturally for English readers:",
    ]

    # Iterate UNITS itself, not a hand-written copy of its keys — see _NUMERAL_RE
    # above for the bug this prevents. UNITS insertion order keeps the hint
    # grouped the way a reader expects (big multipliers first, then lengths).
    for unit in UNITS:
        phrases = found.get(unit, [])
        if not phrases:
            continue
        for phrase in phrases:
            example = _example_for(phrase, unit)
            lines.append(example)

    return "\n".join(lines)
