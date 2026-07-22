"""Node 3: Glossary update.

Writes newly discovered terms into both glossary layers:
- Exact layer (dict + SQLite): for character names, place names, proper nouns
- Semantic layer (Chroma): for ALL terms, including cultural concepts

Only terms classified as 'character' or 'location' go into the exact layer.
Other categories (culture, technique, item, era) go only into Chroma, because
they benefit from semantic retrieval but shouldn't pollute the exact-match
dict with common words.
"""

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..state import TranslatorState
from ..prompts.term_validation import TERM_VALIDATION_SYSTEM, TERM_VALIDATION_USER
from ...config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP
from ...glossary.exact_store import ExactGlossary
from ...glossary.semantic_store import SemanticGlossary
from ...job_store import job_store


# Categories that get added to the exact-match layer
EXACT_CATEGORIES = {"character", "location"}

# Categories that skip validation (rules-based, not LLM)
SKIP_VALIDATION_CATEGORIES = {"culture", "item", "era"}


def _validate_terms(new_terms: list[dict], existing_glossary: str) -> dict:
    """Run LLM validation on new terms to catch duplicates and misclassifications."""
    # Skip LLM call for culture/era terms — they're inherently fuzzy
    terms_need_validation = [t for t in new_terms if t.get("category") not in SKIP_VALIDATION_CATEGORIES]

    if not terms_need_validation:
        return {"validated_terms": new_terms, "rejected": []}

    llm = ChatOpenAI(
        model=MODEL_MAP["term_validation"],
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.0,
        max_tokens=2048,
    )

    import json
    user_prompt = TERM_VALIDATION_USER.format(
        current_glossary=existing_glossary or "(Empty — first chapter)",
        new_terms=json.dumps(terms_need_validation, ensure_ascii=False, indent=2),
    )

    response = llm.invoke([
        SystemMessage(content=TERM_VALIDATION_SYSTEM),
        HumanMessage(content=user_prompt),
    ])

    try:
        result = json.loads(response.content.strip().lstrip("```json").rstrip("```").strip())
        # Merge validated with non-validated terms
        all_validated = result.get("validated_terms", [])
        all_validated.extend([t for t in new_terms if t.get("category") in SKIP_VALIDATION_CATEGORIES])
        return {"validated_terms": all_validated, "rejected": result.get("rejected", [])}
    except json.JSONDecodeError:
        return {"validated_terms": new_terms, "rejected": []}


def _detect_term_conflicts(
    validated_terms: list[dict],
    exact_store: ExactGlossary,
    chapter_number: int,
    target_lang: str,
) -> list[dict]:
    """Check if any validated term conflicts with an existing glossary entry.

    A conflict exists when:
    - The same term_cn already exists in the exact_store
    - The proposed term_en differs from the stored one (case-insensitive)
    - The existing term's status is NOT "confirmed" (confirmed = human-reviewed)

    Returns a list of conflict dicts:
        [{term_cn, existing_en, proposed_en, chapter_existing, chapter_proposed}]
    """
    conflicts = []
    for term in validated_terms:
        term_cn = term["term_cn"]
        existing_en = exact_store.get(term_cn)
        if existing_en is None:
            continue

        proposed_en = term["term_en"]
        if existing_en.strip().lower() == proposed_en.strip().lower():
            continue  # Same translation, no conflict

        # Check status — confirmed terms are human-approved, don't touch
        status = exact_store.get_status(term_cn, target_lang)
        if status == "confirmed":
            continue

        # Collect chapter info for the existing term
        existing_chapters = exact_store.find_chapters_with_term(term_cn, target_lang)
        conflicts.append({
            "term_cn": term_cn,
            "existing_en": existing_en,
            "proposed_en": proposed_en,
            "chapter_existing": existing_chapters,
            "chapter_proposed": chapter_number,
        })

    return conflicts


def update_glossary_node(
    state: TranslatorState,
    exact_store: ExactGlossary,
    semantic_store: SemanticGlossary,
) -> dict:
    """
    After translation, extract new terms → validate → check conflicts → write to both layers.

    Exact layer gets: character names, location names (exact matches)
    Semantic layer gets: ALL terms (for future semantic retrieval)
    SQLite gets: ALL terms (for crash recovery)

    Also produces a JSON snapshot of the exact glossary for checkpointing.

    NEW: Before writing, detects term conflicts (same term_cn, different term_en)
    and records them in state["term_conflicts"] for arbitration.
    """
    new_terms = state.get("new_terms_found", [])
    target_lang = state.get("target_lang", "en-US")
    chapter_number = state["chapter_number"]

    if not new_terms:
        return {
            "glossary_snapshot_json": exact_store.snapshot(),
            "term_conflicts": [],
        }

    # Validate new terms
    existing_glossary = exact_store.to_formatted_text()
    validation_result = _validate_terms(new_terms, existing_glossary)
    validated = validation_result["validated_terms"]

    # Detect conflicts BEFORE writing (exact-category terms only)
    exact_terms = [t for t in validated if t.get("category") in EXACT_CATEGORIES]
    conflicts = _detect_term_conflicts(exact_terms, exact_store, chapter_number, target_lang)

    all_terms = validated  # Everything goes to semantic

    # ── Guard: never overwrite human-confirmed terms ───────────
    confirmed = job_store.get_confirmed_terms(target_lang)
    if confirmed and exact_terms:
        exact_terms = [t for t in exact_terms if t["term_cn"] not in confirmed]

    # Write exact layer (first-write-wins: existing terms are NOT overwritten here;
    # the arbiter will overwrite if needed)
    if exact_terms:
        exact_store.add_batch(exact_terms, chapter=chapter_number, target_lang=target_lang)

    # Write semantic layer
    if all_terms:
        semantic_store.add_batch(all_terms, target_lang=target_lang)

    return {
        "glossary_snapshot_json": exact_store.snapshot(),
        "term_conflicts": conflicts,
    }
