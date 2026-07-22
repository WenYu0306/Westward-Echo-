"""Node 3.5: Term conflict arbitration.

Runs when the update_glossary node detects that a new term conflicts with
an existing glossary entry — same term_cn but different term_en. The arbiter
LLM picks the best translation, updates the exact_store, and records the
decision for backward correction of earlier chapters.

Uses V4 Flash because this is a simple comparison task that doesn't need
deep reasoning — the criteria are straightforward and the cost adds up
across hundreds of chapters.
"""

import json
from typing import TYPE_CHECKING

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..state import TranslatorState
from ..prompts.term_arbitration import ARBITER_SYSTEM, ARBITER_USER
from ...config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP
from ...glossary.exact_store import ExactGlossary

if TYPE_CHECKING:
    from ...glossary.semantic_store import SemanticGlossary


def _arbitrate_single_conflict(
    conflict: dict,
    state: TranslatorState,
) -> dict:
    """Ask the LLM to pick the best translation for a single conflicting term.

    Args:
        conflict: {term_cn, existing_en, proposed_en, chapter_existing, chapter_proposed}
        state: The full TranslatorState for context (genre, target_lang, etc.)

    Returns:
        {term_cn, winner_en, loser_en, reason}
    """
    llm = ChatOpenAI(
        model=MODEL_MAP.get("term_arbitration", "deepseek-v4-flash"),
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.0,
        max_tokens=512,
    )

    user_prompt = ARBITER_USER.format(
        term_cn=conflict["term_cn"],
        translation_a=conflict["existing_en"],
        chapters_a=f"chapters {_format_chapter_list(conflict.get('chapter_existing', 'unknown'))}",
        translation_b=conflict["proposed_en"],
        chapters_b=f"chapter {conflict.get('chapter_proposed', 'unknown')}",
        genre=state.get("genre", "general"),
        target_lang=state.get("target_lang", "en-US"),
        context=state.get("chapter_content", "")[:500],  # First 500 chars for context
    )

    response = llm.invoke([
        SystemMessage(content=ARBITER_SYSTEM),
        HumanMessage(content=user_prompt),
    ])

    try:
        result = json.loads(
            response.content.strip().lstrip("```json").rstrip("```").strip()
        )
        winner_en = result.get("winner_en", conflict["existing_en"])
        reason = result.get("reason", "LLM chose based on evaluation criteria.")
    except (json.JSONDecodeError, AttributeError):
        # Fallback: keep existing (first-write-wins) on parse failure
        winner_en = conflict["existing_en"]
        reason = "Arbiter failed to produce valid JSON; kept existing translation."

    # Determine winner/loser
    winner_normalized = winner_en.strip().lower()
    existing_normalized = conflict["existing_en"].strip().lower()
    proposed_normalized = conflict["proposed_en"].strip().lower()

    if winner_normalized == existing_normalized:
        loser_en = conflict["proposed_en"]
    elif winner_normalized == proposed_normalized:
        loser_en = conflict["existing_en"]
    else:
        # LLM returned something unexpected — treat as keeping existing
        loser_en = conflict["proposed_en"]
        winner_en = conflict["existing_en"]
        reason = "Arbiter returned an unrecognized translation; kept existing."

    return {
        "term_cn": conflict["term_cn"],
        "winner_en": winner_en,
        "loser_en": loser_en,
        "reason": reason,
    }


def _format_chapter_list(chapters) -> str:
    """Format chapter numbers for display in the arbiter prompt.

    Accepts a single int, a list of ints, or a comma-separated string.
    """
    if isinstance(chapters, int):
        return str(chapters)
    if isinstance(chapters, list):
        nums = sorted(chapters)
        if len(nums) <= 3:
            return ", ".join(str(c) for c in nums)
        return f"{nums[0]}-{nums[-1]}"
    return str(chapters)


def arbitrate_terms_node(
    state: TranslatorState,
    exact_store: ExactGlossary,
    semantic_store: "SemanticGlossary | None" = None,
) -> dict:
    """Resolve conflicting term translations via LLM arbitration.

    For each conflict in state["term_conflicts"]:
    1. Call LLM to pick the best translation
    2. Update exact_store with the winner (overwrites if winner differs)
    3. Record the decision for backward correction

    Args:
        state: Current TranslatorState with populated term_conflicts.
        exact_store: The ExactGlossary instance for the pipeline.
        semantic_store: The SemanticGlossary instance (unused by this node,
            but accepted for interface consistency with other nodes).

    Returns:
        dict with keys:
            - resolved_conflicts: list of {term_cn, correct_en, wrong_en, reason}
            - glossary_snapshot_json: updated snapshot after corrections
    """
    conflicts = state.get("term_conflicts", [])
    target_lang = state.get("target_lang", "en-US")

    if not conflicts:
        return {
            "resolved_conflicts": [],
            "glossary_snapshot_json": exact_store.snapshot(),
        }

    resolved = []
    for conflict in conflicts:
        decision = _arbitrate_single_conflict(conflict, state)
        resolved.append({
            "term_cn": decision["term_cn"],
            "correct_en": decision["winner_en"],
            "wrong_en": decision["loser_en"],
            "reason": decision["reason"],
        })

        # Update the exact_store if the winner differs from what's stored
        existing = exact_store.get(conflict["term_cn"])
        if existing and existing.strip().lower() != decision["winner_en"].strip().lower():
            # Retrieve the stored term's category/context for the update
            term_info = exact_store.get_term_info(conflict["term_cn"], target_lang)
            exact_store.add(
                term_cn=conflict["term_cn"],
                term_en=decision["winner_en"],
                category=term_info.get("category", "culture") if term_info else "culture",
                context=term_info.get("context", "") if term_info else "",
                chapter=term_info.get("chapter_first_seen", 0) if term_info else 0,
                note=f"Arbitrated from '{conflict['existing_en']}' → '{decision['winner_en']}': {decision['reason']}",
                target_lang=target_lang,
            )

    return {
        "resolved_conflicts": resolved,
        "glossary_snapshot_json": exact_store.snapshot(),
    }
