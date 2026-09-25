"""Output quality guard — regex/keyword checks the LLM cannot judge itself."""

import re

# Patterns that should NEVER appear in translated output
CHATTER_PATTERNS = [
    (
        r"(?im)^(Now let me|Let me|I will|I'll)\s+.*?"
        r"(compile|translate|provide|generate|write)\b.*$",
        "LLM chatter: translation meta-commentary",
    ),
    (
        r"(?im)^Here (is|are)\s+(the|my)\s+(translation|output|result).*$",
        "LLM chatter: output preamble",
    ),
    (
        r"(?im)^(Sure|OK|Alright|Okay|Great),?\s+(here|let me).*$",
        "LLM chatter: confirmation preamble",
    ),
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
    if text is None:
        return ["EMPTY: translation is None"]

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


def strip_chinese_residue(text: str) -> tuple[str, int]:
    """Remove isolated Chinese characters from translated text.

    Aggressive strategy: delete every CJK character.  LLMs sometimes
    leak 2-6 source characters in long translations — this ensures
    the reader never sees them.  Returns (cleaned_text, count_removed).
    """
    if not text:
        return text, 0
    before = len(text)
    cleaned = CHINESE_CHAR_PATTERN.sub("", text)
    # Collapse whitespace that the deletion may have left behind
    cleaned = re.sub(r" {2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(), before - len(cleaned)


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
        cn_warning = (
            f"UNTRANSLATED: {len(chars)} Chinese characters found in output: "
            f"{', '.join(chars[:5])}"
        )
        warnings.append(cn_warning)

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


# ── JSON-envelope residue & scaffolding leak ────────────────────────────
# Single source of truth for these two defect classes.
#
# scripts/scan_deliverable.py imports these so the *production* guard (runs on
# each finished chapter inside WRITE) and the *post-hoc* full-corpus scan can
# never drift apart — the §八 failure mode where two copies of one rule
# silently diverge. Before this, scan_deliverable held the only copy and the
# production path had no equivalent check at all, which is how Ch366 (```json
# fence + bare `{`), Ch1472 (whole chapter sealed in an unpacked
# {"translated_text": ...} envelope) and Ch1077/Ch1286 ("End of Chapter."
# scaffolding) reached a delivered file.

# A line that is exactly one of these is the model's JSON envelope, not prose.
_BARE_JSON_LINES = frozenset(("{", "}", "},", "{,"))
_JSON_FENCE = "```json"
_JSON_KEYS = ('"translated_text"', '"chapter_title_en"', '"new_terms_found"',
              '"polished_text"', '"changes_made"')

# Scaffolding the model invents around the translation. These are NOT in the
# prompt or anywhere in src/ — they are LLM hallucination, which is exactly why
# a guard (not the prompt) has to catch them. Line-anchored: "END OF CHAPTER"
# at line start is scaffolding, the same words mid-sentence are legitimate.
_META_PATTERNS = [
    (re.compile(r"^END OF CHAPTER\b", re.I), "chapter-end scaffolding"),
    (re.compile(r"\bAs an AI\b", re.I), "model self-reference"),
    (re.compile(r"\bI cannot (?:fulfil|comply|provide)\b", re.I), "model refusal"),
    (re.compile(r"^(?:Note|Disclaimer):\s", re.I), "model preamble"),
]


def json_residue_on_line(line: str) -> str | None:
    """Label for this line if it is JSON-envelope residue, else ``None``.

    Line-oriented so the production guard and the corpus scan apply the *same*
    rule to the *same* granularity. A whole finished chapter is checked by
    scanning its lines (see ``has_json_envelope_residue``).
    """
    stripped = line.strip()
    if stripped in _BARE_JSON_LINES:
        return f"bare {stripped!r} line"
    if _JSON_FENCE in stripped:
        return "```json fence"
    for key in _JSON_KEYS:
        if stripped.startswith(key):
            return f"unpacked envelope key {key}"
    return None


def meta_text_on_line(line: str) -> str | None:
    """Label for this line if it is LLM scaffolding/refusal, else ``None``."""
    for pattern, label in _META_PATTERNS:
        if pattern.search(line):
            return label
    return None


def has_json_envelope_residue(text: str) -> bool:
    """True if a finished chapter still carries the model's JSON envelope."""
    if not text:
        return False
    return any(json_residue_on_line(line) for line in text.split("\n"))


def has_meta_text_leak(text: str) -> bool:
    """True if a finished chapter carries scaffolding like ``END OF CHAPTER``."""
    if not text:
        return False
    return any(meta_text_on_line(line) for line in text.split("\n"))
