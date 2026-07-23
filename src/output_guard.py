"""Output quality guard — regex/keyword checks the LLM cannot judge itself."""

import re

# Patterns that should NEVER appear in translated output
CHATTER_PATTERNS = [
    (r"(?im)^(Now let me|Let me|I will|I'll)\s+.*?(compile|translate|provide|generate|write)\b.*$", "LLM chatter: translation meta-commentary"),
    (r"(?im)^Here (is|are)\s+(the|my)\s+(translation|output|result).*$", "LLM chatter: output preamble"),
    (r"(?im)^(Sure|OK|Alright|Okay|Great),?\s+(here|let me).*$", "LLM chatter: confirmation preamble"),
    (r"(?im)^(Note|Please note|Important):\s", "LLM chatter: editorial note"),
]

# Translations that are suspiciously short or empty
MIN_TRANSLATION_CHARS = 50


def check_translation_output(text: str) -> list[str]:
    """Run all quality checks. Returns list of warning messages (empty = clean)."""
    warnings = []

    if not text or len(text.strip()) < MIN_TRANSLATION_CHARS:
        warnings.append(f"EMPTY: translation is too short ({len(text) if text else 0} chars)")
        return warnings

    for pattern, description in CHATTER_PATTERNS:
        if re.search(pattern, text):
            warnings.append(description)

    return warnings


def sanitize_translation(text: str) -> str:
    """Remove known bad patterns from translation output."""
    for pattern, _ in CHATTER_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.MULTILINE)
    return text.strip()
