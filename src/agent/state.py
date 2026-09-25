"""LangGraph state definition for the translation agent."""

import operator
from typing import Annotated, TypedDict


class TranslatorState(TypedDict):
    # === Input ===
    chapter_title: str
    chapter_content: str
    chapter_number: int
    book_id: str                    # which book this chapter belongs to (token ledger key)
    target_lang: str                # e.g. "en-US"
    genre: str                      # e.g. "romance_ceo", "xianxia", "urban"
    content_type: str               # "novel" — the only validated prompt branch

    # === Glossary ===
    exact_glossary: dict            # {term_cn: term_en} — exact matches
    semantic_terms: list[dict]      # [{term_cn, term_en, category}] — semantic hits
    exact_matches_text: str         # Formatted table for prompt injection
    semantic_matches_text: str      # Formatted table for prompt injection

    # === Translation output ===
    translated_text: str
    new_terms_found: Annotated[list[dict], operator.add]  # Accumulated across retries
    adaptation_notes: list[str]
    # FIX's own account of what it changed and why. Separate from
    # adaptation_notes and deliberately accumulated: FIX → READBACK → FIX is a
    # loop, and adaptation_notes is a plain field, so a second FIX round used to
    # overwrite the first round's notes — the paid-for repair knowledge was
    # discarded before anything could read it.
    fix_lessons: Annotated[list[str], operator.add]
    chapter_summary: str

    # === Context continuity ===
    previous_chapter_summary: str   # From previous chapter → injected into next

    # === Quality ===
    # quality_score is a MEASUREMENT, not a default. It starts at 0.0 meaning
    # "nobody has read this chapter yet" and is only set by readback_node
    # (PASS=5.0, NEEDS_FIX=2.0, unparseable response=0.0). It used to start at
    # 5.0, which made fast-mode chapters look perfect while never being read —
    # downstream averaging then reported a quality score nobody ever measured.
    quality_score: float
    quality_issues: list[str]
    retranslation_count: int
    # Authoritative flag: True means READBACK never ran for this chapter, so
    # quality_score carries no information. Downstream must check this before
    # treating a score as evidence of quality.
    readback_skipped: bool

    # === Glossary snapshot (for checkpoint) ===
    glossary_snapshot_json: str     # JSON string of exact glossary for SQLite checkpoint

    # === Dialect adaptation ===
    dialect_context: str             # Detected dialect hints for this chapter

    # === v0.15: Reader-centric agent outputs ===
    read_analysis: dict              # READ agent's structured reading analysis
    readback_feedback: dict          # READBACK cold reader's honest reaction
    context_signals: str             # Aggregated output from 9 signal detectors
    image_gaps: list[dict]           # Sensory image gaps: what CN reader sees vs EN reader misses
    style_memo: str                  # Accumulated translation lessons from prior chapters
    # When True, skip READBACK+FIX (fast mode for non-sample chapters)
    skip_readback: bool
    use_flash_writer: bool           # When True, WRITE uses Flash instead of Pro
    api_key: str                     # BYOK: user-provided API key ("" = use env default)
    cold_read_context: str           # Character roster + recent summaries for READBACK briefing
