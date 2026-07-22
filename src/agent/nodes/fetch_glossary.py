"""Node 1: Double-layer glossary retrieval.

Fetches relevant glossary terms before each chapter translation:
- Exact layer: O(1) string-contains match against the chapter text
- Semantic layer: Chroma vector search for culturally relevant terms
"""

from ..state import TranslatorState
from ...glossary.exact_store import ExactGlossary
from ...glossary.semantic_store import SemanticGlossary


def _format_matches(terms: dict[str, str]) -> str:
    """Format {cn: en} as a markdown table."""
    if not terms:
        return "(No exact glossary matches for this chapter.)"
    lines = ["| Chinese | English |", "|----------|---------|"]
    for cn, en in sorted(terms.items(), key=lambda x: len(x[0]), reverse=True):
        lines.append(f"| {cn} | {en} |")
    return "\n".join(lines)


def _format_semantic(terms: list[dict]) -> str:
    """Format semantic results as a markdown table."""
    if not terms:
        return "(No semantic matches.)"
    lines = ["| Chinese | English | Category |", "|----------|---------|----------|"]
    for t in terms:
        lines.append(f"| {t['term_cn']} | {t['term_en']} | {t.get('category', 'culture')} |")
    return "\n".join(lines)


def fetch_glossary_node(
    state: TranslatorState,
    exact_store: ExactGlossary,
    semantic_store: SemanticGlossary,
) -> dict:
    """
    Query both glossary layers and format results for prompt injection.

    Exact matches are mandatory — they appear in the chapter text and
    MUST be used by the LLM. Semantic matches are advisory — culturally
    relevant terms for the chapter's theme.
    """
    chapter_text = state["chapter_content"]
    target_lang = state.get("target_lang", "en-US")

    # Exact layer: string-contains scan
    exact_matches = exact_store.match_in_text(chapter_text)

    # Semantic layer: vector search
    semantic_hits = semantic_store.search(chapter_text, top_k=15, target_lang=target_lang)

    # Filter out semantic hits that are already in exact matches
    semantic_hits = [t for t in semantic_hits if t["term_cn"] not in exact_matches]

    return {
        "exact_glossary": exact_store.to_dict(),
        "semantic_terms": semantic_hits,
        "exact_matches_text": _format_matches(exact_matches),
        "semantic_matches_text": _format_semantic(semantic_hits),
    }
