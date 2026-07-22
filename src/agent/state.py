"""LangGraph state definition for the translation agent."""

from typing import TypedDict, Annotated
import operator


class TranslatorState(TypedDict):
    # === Input ===
    chapter_title: str
    chapter_content: str
    chapter_number: int
    target_lang: str                # e.g. "en-US"

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
