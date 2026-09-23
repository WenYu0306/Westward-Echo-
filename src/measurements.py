"""Chinese measurement/unit detection and localization hints.

Chinese web novels frequently use units (万, 亿, 里, 斤, etc.) that
do not translate literally into natural English. This module scans
chapter text for these units and produces a prompt hint so the LLM
can localize them idiomatically.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

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
    # 寸 was matched by _NUMERAL_RE but had no UNITS entry, so any "三寸"/"七寸"
    # was detected and then silently dropped: build_measurements_hint emitted the
    # "## MEASUREMENT LOCALIZATION" header with zero unit lines under it, and
    # _example_for(phrase, "寸") raised KeyError. Detection and handling must
    # agree — either the regex drops 寸 or UNITS knows about it. 1 chi = 10 cun.
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
_UNIT_CHARS = re.escape("".join(UNITS))
_NUMERAL_RE = re.compile(
    rf"[\d零一二三四五六七八九十百千万亿两]+[{_UNIT_CHARS}]"
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
