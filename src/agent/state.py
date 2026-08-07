"""LangGraph state definition for the translation agent."""

import operator
from typing import Annotated, TypedDict


class TranslatorState(TypedDict):
    # === Input ===
    chapter_title: str
    chapter_content: str
    chapter_number: int
    target_lang: str                # e.g. "en-US"
    genre: str                      # e.g. "romance_ceo", "xianxia", "urban"
    content_type: str               # "novel" (default) | "script" | "game" — prompt branch selector

    # === Glossary ===
    exact_glossary: dict            # {term_cn: term_en} — exact matches
    semantic_terms: list[dict]      # [{term_cn, term_en, category}] — semantic hits
    exact_matches_text: str         # Formatted table for prompt injection
    semantic_matches_text: str      # Formatted table for prompt injection

    # === Translation output ===
    translated_text: str
    new_terms_found: Annotated[list[dict], operator.add]  # Accumulated across retries
    adaptation_notes: list[str]
    chapter_summary: str

    # === Context continuity ===
    previous_chapter_summary: str   # From previous chapter → injected into next

    # === Quality ===
    quality_score: float
    quality_issues: list[str]
    retranslation_count: int

    # === Glossary snapshot (for checkpoint) ===
    glossary_snapshot_json: str     # JSON string of exact glossary for SQLite checkpoint

    # === Term conflict arbitration ===
    term_conflicts: Annotated[list[dict], operator.add]
    # [{term_cn, existing_en, proposed_en, chapter_existing, chapter_proposed}]
    resolved_conflicts: Annotated[list[dict], operator.add]
    # [{term_cn, correct_en, wrong_en, reason}]

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
