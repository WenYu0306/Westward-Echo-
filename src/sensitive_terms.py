"""Sensitive term classification for cultural-context warnings.

Some terms need explicit handling because literal translation would carry
harmful Western religious/cultural connotations. This module provides
contextual warnings injected into every chapter's translation prompt when
these terms appear.
"""

import re

# ── Chinese source-text sensitive terms ──────────────────────
# Terms that need explicit cultural-context warnings in every chapter
SENSITIVE_TERMS = {
    "上身": (
        "⚠️ POSITIVE spirit possession (仙家 takes form to help). "
        "NEVER 'possessed'. Use 'the Master descends' or 'spirit takes form'."
    ),
    "附体": (
        "⚠️ Similar to 上身. Spirit embodiment, not demonic possession."
    ),
    "请神": (
        "⚠️ Sacred invocation ritual. NEVER 'summoning' (evil connotation). "
        "Use 'invoke the spirits' or 'call upon the Masters'."
    ),
    "地府": (
        "⚠️ Bureaucratic afterlife, not Hell. "
        "Use 'Underworld' or 'Netherworld Court'. NEVER 'Hell' or 'Hades'."
    ),
    "鬼": (
        "⚠️ Context-dependent. Can be 'ghost' (neutral), 'spirit' (neutral), "
        "or 'demon' (evil). Check context."
    ),
    "仙": (
        "⚠️ Context-dependent. xianxia=Immortal, folk_religion=Master/Spirit Guardian. "
        "NOT 'fairy' or 'god'."
    ),
}

# ── Arabic religious blasphemy — PRODUCT SURVIVAL ──────────────
# These patterns in translated Arabic output can get the entire
# platform blocked in UAE, Saudi Arabia, and other Gulf states.
# This is NOT a translation quality issue — it's a legal red line.

AR_BLASPHEMY_PATTERNS = [
    # Direct blasphemy — "curse your religion/god/prophet"
    (re.compile(r'يلعن\s*(دين|الله|رب|النبي|الرسول|الإسلام)', re.IGNORECASE),
     "CRITICAL: Arabic religious blasphemy — direct curse on Allah/religion/prophet"),
    (re.compile(r'كفر|كافر|ملحد|مرتد|زنديق', re.IGNORECASE),
     "CRITICAL: Accusation of apostasy/unbelief — legally dangerous in Gulf states"),
    (re.compile(r'سب\s*(الله|الدين|الرسول|النبي|القرآن)', re.IGNORECASE),
     "CRITICAL: Insulting Allah/religion/prophet/Quran — platform-level ban risk"),
    # Normalization of haram
    (re.compile(r'(الخمر|الخمور)\s*(جميل|لذيذ|رائع|حلو)', re.IGNORECASE),
     "MAJOR: Normalizing alcohol consumption as positive — haram violation"),
    (re.compile(r'شرب\s*(الخمر|الكحول|البيرة)', re.IGNORECASE),
     "MAJOR: Casual alcohol consumption by viewpoint character"),
]

AR_BLASPHEMY_WARNING = """\
## ⚠️ RELIGIOUS RED LINE (ar-SA only)
This translation targets the Arabic-speaking market including Gulf states.
The following are ABSOLUTELY FORBIDDEN in the output:

1. NEVER curse Allah, the Prophet, Islam, or anyone's religion.
   Even if the Chinese source character says "操你妈的" or "天杀的",
   the Arabic translation MUST use non-religious insults.
   Use: 'يا ابن الكلب' (son of a dog), 'يا خسارة' (what a loss),
   'تباً' (damn), 'اللعنة' (curse/damn — general, not religious).
   NEVER use: 'يلعن دينك', 'كفر', 'كافر', 'سب الدين'.

2. NEVER normalize haram behavior positively.
   Alcohol, pork, casual sex — may appear in the source because it's Chinese.
   Translate factually. Do NOT add positive adjectives to haram acts.

3. NEVER use takfir language (declaring someone an unbeliever).
   This is a legal offense in several Gulf countries.

This is NOT optional. A single violation can get the entire book blocked
across the Middle East.
"""


def build_sensitive_term_context(text: str, target_lang: str = "en-US") -> str:
    """If sensitive terms appear in the chapter, return a warning block for the prompt.

    The *target_lang* parameter enables language-specific critical warnings
    (e.g. Arabic religious red lines) that are injected regardless of source
    text content.
    """
    parts = []

    # Source-text Chinese sensitive terms
    found = {term: note for term, note in SENSITIVE_TERMS.items() if term in text}
    if found:
        lines = [
            "## TERMINOLOGY WARNINGS",
            "The following terms appear in this chapter. Handle with care:",
        ]
        for term, note in found.items():
            lines.append(f"- **{term}**: {note}")
        parts.append("\n".join(lines))

    # Per-language critical warnings (injected regardless of source content)
    if target_lang == "ar-SA":
        parts.append(AR_BLASPHEMY_WARNING)

    return "\n\n".join(parts)


def scan_arabic_blasphemy(text: str) -> list[str]:
    """Post-translation scan: detect Arabic religious red-line violations.

    Returns a list of violation descriptions (empty list = clean).
    Called by output_guard after each ar-SA chapter.
    """
    violations = []
    for pattern, description in AR_BLASPHEMY_PATTERNS:
        if pattern.search(text):
            # Extract the matched phrase for logging
            match = pattern.search(text)
            snippet = match.group(0)[:50] if match else "(matched)"
            violations.append(f"{description}: '{snippet}'")
    return violations
