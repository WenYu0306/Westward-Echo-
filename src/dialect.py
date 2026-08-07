"""Dialect detection and mapping for character voice preservation.

Detects regional Chinese dialect markers in source text and maps them to
appropriate English dialect equivalents. Injects dialect context into the
translation prompt so the LLM can preserve each character's unique voice.
"""

from typing import Optional

# ── Dialect detection patterns ────────────────────────────────
# Each dialect has a set of marker words/phrases that signal the
# character is speaking in that dialect.

DIALECT_MARKERS: dict[str, list[str]] = {
    "dongbei": [
        # 东北话 (Dongbei / Northeastern Mandarin)
        "整", "啥", "咋", "俺", "唠嗑", "得劲儿", "忽悠", "老鼻子",
        "嘎哈", "咋地", "蔫吧", "磕碜", "贼", "嗯呐", "拉倒",
        "扒瞎", "膈应", "磨叽", "扯犊子", "滚犊子",
    ],
    "sichuan": [
        # 四川话 (Sichuanese)
        "啥子", "咋子", "哪个", "巴适", "安逸", "要得", "晓得不",
        "耍", "瓜娃子", "摆龙门阵", "雄起", "莫得",
    ],
    "beijing": [
        # 京片子 (Beijing dialect)
        "您", "爷们", "甭", "嘛呢", "忒", "倍儿", "颠儿了",
        "瓷器", "侃大山", "撮一顿", "门儿清", "遛弯儿",
    ],
    "shanghai": [
        # 上海话标记 (Shanghainese markers in Mandarin text)
        "侬", "阿拉", "伐", "好伐", "拎不清", "捣糨糊",
    ],
    "cantonese": [
        # 粤语标记 (Cantonese markers in Mandarin text)
        "冇", "乜", "佢", "哋", "嘅", "咁", "咩",
        "顶你个肺", "食饭", "饮茶", "靓仔", "靓女",
    ],
}

# ── Dialect → English equivalent mapping ───────────────────────
# These describe the TONE and style the LLM should use, not
# literal word-for-word dialect substitutions.

DIALECT_MAPPING: dict[str, dict[str, str]] = {
    "dongbei": {
        "dialect_en": "Southern American English",
        "markers": "y'all, fixin' to, ain't, reckon, done gone, might could, right quick",
        "tone": "blunt, warm, down-to-earth, sometimes gruff but with a big heart. "
                "Speaks in short, punchy sentences. Loves exaggeration for effect.",
        "note": "Dongbei dialect signals a salt-of-the-earth, no-nonsense character. "
                "Southern American English carries the same combination of warmth + blunt honesty.",
    },
    "sichuan": {
        "dialect_en": "Texas drawl or Irish English",
        "markers": "y'all, fixin' to, gonna, oughta, right kindly",
        "tone": "laid-back, humorous, enjoys life, a bit lazy-sounding but sharp underneath. "
                "Sentences have a lilting, casual rhythm.",
        "note": "Sichuan dialect has a distinctive melodic quality and laid-back attitude. "
                "Texas drawl captures the 'relaxed but sharp' personality.",
    },
    "beijing": {
        "dialect_en": "Brooklyn / working-class New York English",
        "markers": "fuggedaboutit, y'know what I'm sayin, the thing is, listen here",
        "tone": "chatty, street-smart, loves to talk, drops cultural references constantly. "
                "Confident, a bit cocky, knows everyone and everything.",
        "note": "Beijing dialect is the original 'smooth-talking city slicker' voice. "
                "Brooklyn carries the same old-school urban credibility.",
    },
    "shanghai": {
        "dialect_en": "Polished metropolitan English (think Manhattan finance)",
        "markers": "frankly speaking, look, the thing is, honestly, indeed",
        "tone": "refined, slightly aloof, businesslike, values efficiency and sophistication. "
                "Chooses words carefully. Never vulgar.",
        "note": "Shanghainese signals urban sophistication and financial savvy. "
                "Manhattan professional English carries the same signaling.",
    },
    "cantonese": {
        "dialect_en": "British slang / London vernacular",
        "markers": "mate, bloody, proper, innit, sorted, cheers, blimey, knackered",
        "tone": "direct, pragmatic, generous with insults and praise alike. "
                "Colorful expressions. Strong sense of local identity.",
        "note": "Cantonese has a punchy, colorful quality with vivid local expressions. "
                "British slang carries similar energy — blunt, colorful, identity-heavy.",
    },
}


# ── LitRPG / System text detection ──────────────────────────

SYSTEM_TEXT_MARKERS = [
    "叮——", "系统提示", "系统通知", "【系统】", "好感度", "主线任务",
    "支线任务", "经验值", "技能", "成就", "道具",
]


def has_system_text(text: str) -> bool:
    """Detect if chapter contains LitRPG-style system popups.

    Returns True when any of the common Chinese web novel system notification
    markers are found in the text. These indicate game-UI elements (status
    windows, skill acquisition notices, stat changes) that should be rendered
    using LitRPG formatting conventions rather than as prose dialogue.
    """
    return any(m in text for m in SYSTEM_TEXT_MARKERS)


def detect_dialects(text: str) -> dict[str, int]:
    """Scan text for dialect markers. Returns {dialect_name: marker_count}.

    Only returns dialects with >= 2 markers found (filters noise).
    """
    results = {}
    for dialect_name, markers in DIALECT_MARKERS.items():
        count = sum(1 for m in markers if m in text)
        if count >= 2:
            results[dialect_name] = count
    return results


def get_dialect_hint(dialect_name: str) -> Optional[str]:
    """Return a formatted dialect hint for injection into the translation prompt.

    Returns None if the dialect is unknown.
    """
    mapping = DIALECT_MAPPING.get(dialect_name)
    if not mapping:
        return None

    return (
        f"**{mapping['dialect_en']}** dialect. "
        f"Use: {mapping['markers']}. "
        f"Tone: {mapping['tone']} "
        f"({mapping['note']})"
    )


def build_dialect_context(chapter_text: str) -> str:
    """Build the full dialect context block for the translation prompt.

    Returns empty string if no dialects detected.
    """
    detected = detect_dialects(chapter_text)
    if not detected:
        return ""

    # Sort by marker count descending (strongest signal first)
    parts = []
    for dialect_name in sorted(detected, key=detected.get, reverse=True):
        hint = get_dialect_hint(dialect_name)
        if hint:
            dialect_cn = {
                "dongbei": "东北话",
                "sichuan": "四川话",
                "beijing": "京片子",
                "shanghai": "上海话",
                "cantonese": "粤语",
            }.get(dialect_name, dialect_name)
            parts.append(
                f"- Characters speaking **{dialect_cn}** ({detected[dialect_name]} markers found): "
                f"{hint}"
            )

    if not parts:
        return ""

    header = (
        "## DIALECT CONTEXT\n"
        "The source text contains regional Chinese dialects. "
        "To preserve character voice, translate dialect speech using the "
        "mapped English dialect. Maintain consistency: the same character "
        "should use the same dialect throughout.\n\n"
    )
    return header + "\n".join(parts)
