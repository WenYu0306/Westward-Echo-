"""Chinese measurement/unit detection and localization hints.

Chinese web novels frequently use units (万, 亿, 里, 斤, etc.) that
do not translate literally into natural English. This module scans
chapter text for these units and produces a prompt hint so the LLM
can localize them idiomatically.
"""

from __future__ import annotations

import re
from typing import Optional

# ── Unit definitions ────────────────────────────────────────────────
# Each unit maps to its scale factor (relative to the base SI-ish unit),
# an English gloss, and a localization hint.

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
    "亩": {
        "scale": 0.067,
        "en": "mu (0.067 hectare)",
        "hint": "Convert to 'acre' (~0.16 acres) or use 'field'",
    },
}

# ── Detection ───────────────────────────────────────────────────────

# Match a Chinese numeral + unit, e.g. "三万里", "一斤", "十万"
# Supports: 零一二三四五六七八九十百千万亿 + digits 0-9
_NUMERAL_RE = re.compile(
    r"[\d零一二三四五六七八九十百千万亿两]+[里斤丈尺寸亩万亿]"
)


def detect_measurements(text: str) -> dict[str, list[str]]:
    """Find all measurement mentions in *text*.

    Returns a dict mapping unit character → list of matched phrases.
    Only units that appear at least once are present in the result.
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

def _example_for(phrase: str, unit: str) -> str:
    """Generate a concrete localization example for one matched phrase.

    We attempt a heuristic numeric conversion so the LLM has a concrete
    target to work with rather than an abstract rule.
    """
    digits = _parse_chinese_number(phrase[:-1])
    if digits is None:
        # Provide the generic hint instead
        return f"- {phrase} → [{UNITS[unit]['hint']}]"

    scale = UNITS[unit]["scale"]
    converted = digits * scale
    friendly = _format_number(converted)

    if unit == "里":
        miles = converted * 0.621371
        return (
            f"- {phrase} → ~{friendly} km / ~{_format_number(miles)} miles "
            f"(not \"{digits} li\")"
        )
    elif unit == "斤":
        lbs = converted * 2.20462
        return (
            f"- {phrase} → ~{friendly} kg / ~{_format_number(lbs)} lbs "
            f"(not \"{digits} jin\")"
        )
    elif unit == "丈":
        feet = converted / 0.3048
        return (
            f"- {phrase} → ~{friendly} m / ~{_format_number(feet)} ft "
            f"(not \"{digits} zhang\")"
        )
    elif unit == "尺":
        inches = converted * 39.3701
        return (
            f"- {phrase} → ~{friendly} m / ~{_format_number(inches)} in "
            f"(not \"{digits} chi\")"
        )
    elif unit == "亩":
        acres = converted * 0.1647
        return (
            f"- {phrase} → ~{friendly} hectares / ~{_format_number(acres)} acres "
            f"(not \"{digits} mu\")"
        )
    elif unit == "万":
        if digits >= 100:
            millions = digits * 10000 / 1_000_000
            return (
                f"- {phrase} → ~{_format_number(digits * 10000)} / "
                f"{_format_number(millions)}M "
                f"(not \"{digits} ten thousand\")"
            )
        else:
            return (
                f"- {phrase} → ~{_format_number(digits * 10000)} "
                f"(not literally \"{digits} ten thousand\")"
            )
    elif unit == "亿":
        if digits >= 1:
            billions = digits * 100_000_000 / 1_000_000_000
            return (
                f"- {phrase} → ~{_format_number(digits * 100_000_000)} / "
                f"{_format_number(billions)}B "
                f"(not \"{digits} hundred million\")"
            )
        else:
            return (
                f"- {phrase} → ~{_format_number(digits * 100_000_000)} "
                f"(not literally \"{digits} hundred million\")"
            )

    # Fallback: show converted value next to the original
    return f"- {phrase} → ~{friendly} {UNITS[unit]['en']} ({UNITS[unit]['hint']})"


# ── Chinese numeral parsing ─────────────────────────────────────────

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
        right = _parse_chinese_simple(parts[1]) if len(parts) > 1 and parts[1] else 0
        if left is not None and right is not None:
            return left * 10000 + right
        return None

    # Handle "亿" pattern
    if "亿" in s:
        parts = s.split("亿", 1)
        left = _parse_chinese_simple(parts[0]) if parts[0] else 1
        right = 0
        if len(parts) > 1 and parts[1]:
            right = _parse_chinese_number(parts[1]) or 0
        if left is not None:
            return left * 100_000_000 + right
        return None

    return _parse_chinese_simple(s)


def _parse_chinese_simple(s: str) -> int | None:
    """Parse a Chinese numeral without 万/亿, e.g. '三千五百' → 3500."""
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
    """Pretty-print a number for the hint."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n:,.0f}" if n == int(n) else f"{n:,.1f}"
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.1f}"


def build_measurements_hint(text: str) -> str:
    """Generate a prompt hint for measurement localization.

    Returns an empty string when no Chinese measurements are detected.
    Otherwise returns a block that can be prepended to the translation
    user prompt.
    """
    found = detect_measurements(text)
    if not found:
        return ""

    lines = [
        "## MEASUREMENT LOCALIZATION",
        "This chapter contains Chinese units. Convert naturally for English readers:",
    ]

    for unit in ("万", "亿", "里", "斤", "丈", "尺", "亩"):
        phrases = found.get(unit, [])
        if not phrases:
            continue
        for phrase in phrases:
            example = _example_for(phrase, unit)
            lines.append(example)

    return "\n".join(lines)
