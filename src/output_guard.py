"""Output quality guard — regex/keyword checks the LLM cannot judge itself."""

import re

# Patterns that should NEVER appear in translated output
CHATTER_PATTERNS = [
    (r"(?im)^(Now let me|Let me|I will|I'll)\s+.*?(compile|translate|provide|generate|write)\b.*$", "LLM chatter: translation meta-commentary"),
    (r"(?im)^Here (is|are)\s+(the|my)\s+(translation|output|result).*$", "LLM chatter: output preamble"),
    (r"(?im)^(Sure|OK|Alright|Okay|Great),?\s+(here|let me).*$", "LLM chatter: confirmation preamble"),
    (r"(?im)^(Note|Please note|Important):\s", "LLM chatter: editorial note"),
]

# Chinese character detection — any Chinese character in the translated output
# means something wasn't translated. This catches 治安 left in English text,
# jianghu left in Spanish, etc.
CHINESE_CHAR_PATTERN = re.compile(r'[一-鿿]')

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


def has_untranslated_chinese(text: str) -> bool:
    """Check if Chinese characters remain in the translated output."""
    return bool(CHINESE_CHAR_PATTERN.search(text))


def find_untranslated_chinese(text: str) -> list[str]:
    """Return list of Chinese words found in the translated output."""
    return list(set(CHINESE_CHAR_PATTERN.findall(text)))


def check_and_record(
    text: str,
    job_id=None,
    chapter_num=None,
    target_lang: str = "en-US",
) -> list:
    """Run quality checks and record warnings to the event store.

    This is the recommended entry-point — it mirrors ``check_translation_output``
    but additionally persists each warning for analytics.
    """
    warnings = check_translation_output(text)

    # ── Chinese character residue check ──
    if has_untranslated_chinese(text):
        chars = find_untranslated_chinese(text)
        cn_warning = f"UNTRANSLATED: {len(chars)} Chinese characters found in output: {', '.join(chars[:5])}"
        warnings.append(cn_warning)

    # ── Arabic blasphemy scan ──
    if target_lang == "ar-SA":
        from .sensitive_terms import scan_arabic_blasphemy
        ar_warnings = scan_arabic_blasphemy(text)
        for aw in ar_warnings:
            warnings.append(aw)

    if warnings:
        from .error_tracker import record_event
        for w in warnings:
            event_type = "guard_warning"
            if w.startswith("EMPTY:"):
                event_type = "empty_output"
            elif "chatter" in w.lower():
                event_type = "chatter_detected"
            elif "UNTRANSLATED" in w:
                event_type = "untranslated_chinese"
            record_event(job_id, chapter_num, event_type, w, target_lang)

    return warnings


def sanitize_translation(text: str) -> str:
    """Remove known bad patterns from translation output."""
    for pattern, _ in CHATTER_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.MULTILINE)
    return text.strip()
