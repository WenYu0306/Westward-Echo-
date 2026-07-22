"""LangGraph graph assembly for the translation agent.

Builds a state graph with 4 nodes and conditional routing:
  START → fetch_glossary → translate → update_glossary → quality_check
                                                              ↓
                                              (score < 3.5) → retranslate → END
                                              (score ≥ 3.5) → END
"""

from langgraph.graph import StateGraph, END

from .state import TranslatorState
from .nodes.fetch_glossary import fetch_glossary_node
from .nodes.translate import translate_node
from .nodes.update_glossary import update_glossary_node
from .nodes.quality_check import quality_check_node
from ..glossary.exact_store import ExactGlossary
from ..glossary.semantic_store import SemanticGlossary
from ..config import MAX_RETRANSLATION_ATTEMPTS


def _should_retranslate(state: TranslatorState) -> str:
    """Conditional routing after QA.

    If the quality score is below 3.5 AND we haven't exceeded max retries,
    route back to the translate node. Otherwise, end.
    """
    score = state.get("quality_score", 5.0)
    retries = state.get("retranslation_count", 0)

    if score < 3.5 and retries < MAX_RETRANSLATION_ATTEMPTS:
        return "translate_node"
    return END


def build_graph(
    exact_store: ExactGlossary,
    semantic_store: SemanticGlossary,
) -> StateGraph:
    """
    Build and compile the translation agent graph.

    The exact_store and semantic_store are injected as closures so each node
    can access them without global state.
    """
    builder = StateGraph(TranslatorState)

    # --- Nodes ---
    builder.add_node(
        "fetch_glossary",
        lambda s: fetch_glossary_node(s, exact_store, semantic_store),
    )
    builder.add_node("translate_node", translate_node)
    builder.add_node(
        "update_glossary",
        lambda s: update_glossary_node(s, exact_store, semantic_store),
    )
    builder.add_node("quality_check", quality_check_node)

    # --- Edges ---
    builder.set_entry_point("fetch_glossary")
    builder.add_edge("fetch_glossary", "translate_node")
    builder.add_edge("translate_node", "update_glossary")
    builder.add_edge("update_glossary", "quality_check")

    # --- Conditional: retranslate if QA fails ---
    builder.add_conditional_edges(
        "quality_check",
        _should_retranslate,
        {
            "translate_node": "translate_node",
            END: END,
        },
    )

    # When retranslating, go back to update_glossary → quality_check again
    # (translate → update → QA → ...)

    return builder.compile()


class TranslationAgent:
    """
    High-level wrapper around the LangGraph graph.

    Usage:
        agent = TranslationAgent()
        result = agent.translate_chapter(
            chapter_title="第一章 穿成霸总文女主",
            chapter_content="...",
            chapter_number=1,
            previous_summary="",
            target_lang="en-US",
        )
    """

    def __init__(self):
        self.exact_store = ExactGlossary()
        self.semantic_store = SemanticGlossary()
        self.graph = build_graph(self.exact_store, self.semantic_store)

    def load_glossary(self, target_lang: str = "en-US"):
        """Restore glossary from SQLite on startup / resume."""
        self.exact_store.load_from_db(target_lang)

    def translate_chapter(
        self,
        chapter_title: str,
        chapter_content: str,
        chapter_number: int,
        previous_summary: str = "",
        target_lang: str = "en-US",
    ) -> dict:
        """
        Translate a single chapter through the full agent pipeline.

        Returns a dict with keys:
        - translated_text: The English chapter
        - new_terms_found: New glossary terms discovered
        - adaptation_notes: Cultural adaptation decisions
        - chapter_summary: Summary for next chapter's context
        - quality_score: QA score (1-5)
        - quality_issues: QA issues found
        - glossary_snapshot_json: Current exact glossary state (for checkpoint)
        """
        initial_state: TranslatorState = {
            "chapter_title": chapter_title,
            "chapter_content": chapter_content,
            "chapter_number": chapter_number,
            "target_lang": target_lang,
            "exact_glossary": self.exact_store.to_dict(),
            "semantic_terms": [],
            "exact_matches_text": "",
            "semantic_matches_text": "",
            "translated_text": "",
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "",
            "previous_chapter_summary": previous_summary,
            "quality_score": 5.0,
            "quality_issues": [],
            "retranslation_count": 0,
            "glossary_snapshot_json": "",
        }

        result = self.graph.invoke(initial_state)
        return result
