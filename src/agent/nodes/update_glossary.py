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


def update_glossary_node(
    state: TranslatorState,
    exact_store: ExactGlossary,
    semantic_store: SemanticGlossary,
) -> dict:
    """
    After translation, extract new terms → validate → write to both layers.

    Exact layer gets: character names, location names (exact matches)
    Semantic layer gets: ALL terms (for future semantic retrieval)
    SQLite gets: ALL terms (for crash recovery)

    Also produces a JSON snapshot of the exact glossary for checkpointing.
    """
    new_terms = state.get("new_terms_found", [])
    target_lang = state.get("target_lang", "en-US")
    chapter_number = state["chapter_number"]

    if not new_terms:
        return {"glossary_snapshot_json": exact_store.snapshot()}

    # Validate new terms
    existing_glossary = exact_store.to_formatted_text()
    validation_result = _validate_terms(new_terms, existing_glossary)
    validated = validation_result["validated_terms"]

    # Split by category
    exact_terms = [t for t in validated if t.get("category") in EXACT_CATEGORIES]
    all_terms = validated  # Everything goes to semantic

    # Write exact layer
    if exact_terms:
        exact_store.add_batch(exact_terms, chapter=chapter_number, target_lang=target_lang)

    # Write semantic layer
    if all_terms:
        semantic_store.add_batch(all_terms, target_lang=target_lang)

    return {"glossary_snapshot_json": exact_store.snapshot()}
