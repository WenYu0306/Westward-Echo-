"""Sensitive term classification for cultural-context warnings.

Some terms need explicit handling because literal translation would carry
harmful Western religious/cultural connotations. This module provides
contextual warnings injected into every chapter's translation prompt when
these terms appear.
"""

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


def build_sensitive_term_context(text: str) -> str:
    """If sensitive terms appear in the chapter, return a warning block for the prompt."""
    found = {term: note for term, note in SENSITIVE_TERMS.items() if term in text}
    if not found:
        return ""
    lines = [
        "## TERMINOLOGY WARNINGS",
        "The following terms appear in this chapter. Handle with care:",
    ]
    for term, note in found.items():
        lines.append(f"- **{term}**: {note}")
    return "\n".join(lines)
