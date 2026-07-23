"""LangGraph graph assembly — Multi-Agent pipeline.

Builds a state graph with 6 nodes and conditional routing:
  START → fetch_glossary → translate → update_glossary
                                          ↓
                     (has conflicts?) → arbitrate_terms
                                          ↓
                       quality_check ←───┘
                              ↓
         (score < 3.5) → polish_node(editor) → update_glossary → quality_check
         (score ≥ 3.5) → END

The polish node is a different agent with a different prompt and mindset
(editor, not translator) — it fixes specific QA issues instead of blindly
re-translating the same text with the same prompt.
"""

import logging

from langgraph.graph import StateGraph, END

from .state import TranslatorState
from .nodes.fetch_glossary import fetch_glossary_node
from .nodes.translate import translate_node
from .nodes.polish import polish_node
from .nodes.update_glossary import update_glossary_node
from .nodes.quality_check import quality_check_node
from .nodes.arbitrate_terms import arbitrate_terms_node
from ..glossary.exact_store import ExactGlossary
from ..glossary.semantic_store import SemanticGlossary
from ..config import MAX_RETRANSLATION_ATTEMPTS
from ..chapter_slicer import should_split, split_chapter, build_segment_title

logger = logging.getLogger(__name__)


def _should_repair(state: TranslatorState) -> str:
    """Route QA failures to the polish editor agent.

    If score < 3.5 and we haven't exceeded max attempts, route to the
    polish node — a different agent that fixes specific issues rather
    than blindly re-translating.
    """
    score = state.get("quality_score", 5.0)
    retries = state.get("retranslation_count", 0)

    if score < 3.5 and retries < MAX_RETRANSLATION_ATTEMPTS:
        return "polish_node"
    return END


def _has_term_conflicts(state: TranslatorState) -> str:
    """Route to arbitration if term conflicts were detected during glossary update.

    When two chapters translate the same Chinese term differently, the
    arbitration node resolves which translation wins. If no conflicts
    were found, skip straight to quality check.
    """
    conflicts = state.get("term_conflicts", [])
    if conflicts:
        return "arbitrate_terms"
    return "quality_check"


def build_graph(
    exact_store: ExactGlossary,
    semantic_store: SemanticGlossary,
    get_prefetched=None,  # callable() -> (dict|None, list|None) | (None, None)
) -> StateGraph:
    builder = StateGraph(TranslatorState)

    # ── Nodes ──
    builder.add_node(
        "fetch_glossary",
        lambda s: fetch_glossary_node(s, exact_store, semantic_store, get_prefetched),
    )
    builder.add_node("translate_node", translate_node)
    builder.add_node("polish_node", polish_node)
    builder.add_node(
        "update_glossary",
        lambda s: update_glossary_node(s, exact_store, semantic_store),
    )
    builder.add_node(
        "arbitrate_terms",
        lambda s: arbitrate_terms_node(s, exact_store, semantic_store),
    )
    builder.add_node("quality_check", quality_check_node)

    # ── Edges ──
    builder.set_entry_point("fetch_glossary")
    builder.add_edge("fetch_glossary", "translate_node")
    builder.add_edge("translate_node", "update_glossary")

    # ── Conditional: term conflicts → arbitration, otherwise → QA ──
    builder.add_conditional_edges(
        "update_glossary",
        _has_term_conflicts,
        {
            "arbitrate_terms": "arbitrate_terms",
            "quality_check": "quality_check",
        },
    )
    builder.add_edge("arbitrate_terms", "quality_check")

    # ── Conditional: QA failure → polish editor (NOT blind retranslate) ──
    builder.add_conditional_edges(
        "quality_check",
        _should_repair,
        {
            "polish_node": "polish_node",
            END: END,
        },
    )

    # Polish → update glossary (in case edits surface new terms) → QA again
    builder.add_edge("polish_node", "update_glossary")

    return builder.compile()


class TranslationAgent:
    """High-level wrapper around the multi-agent LangGraph pipeline."""

    def __init__(self):
        self.exact_store = ExactGlossary()
        self.semantic_store = SemanticGlossary()
        self.graph = build_graph(self.exact_store, self.semantic_store,
                                 get_prefetched=self.get_and_clear_prefetched)
        # Prefetch slots — set by the orchestration loop when a background
        # prefetch completes before this chapter's turn.  Cleared by
        # fetch_glossary_node after consumption.
        self._prefetched_exact: dict | None = None
        self._prefetched_semantic: list | None = None

    def load_glossary(self, target_lang: str = "en-US"):
        self.exact_store.load_from_db(target_lang)

    def load_glossary_snapshot(self, snapshot_json: str):
        if snapshot_json and snapshot_json != "{}":
            self.exact_store.restore_snapshot(snapshot_json)

    def set_prefetched_glossary(self, exact_matches: dict, semantic_matches: list):
        """Pre-load glossary results so fetch_glossary can skip the lookup.

        Called by the orchestration loop when the background prefetch for the
        next chapter completed ahead of time.  The fetch_glossary node clears
        these slots after consumption.
        """
        self._prefetched_exact = exact_matches
        self._prefetched_semantic = semantic_matches

    def get_and_clear_prefetched(self):
        """Consume the prefetched glossary data (one-shot)."""
        exact = self._prefetched_exact
        semantic = self._prefetched_semantic
        self._prefetched_exact = None
        self._prefetched_semantic = None
        return exact, semantic

    def translate_chapter(
        self,
        chapter_title: str,
        chapter_content: str,
        chapter_number: int,
        previous_summary: str = "",
        target_lang: str = "en-US",
        genre: str = "romance_ceo",
    ) -> dict:
        # ── Auto-split for long chapters ──────────────────────────
        if should_split(chapter_content):
            return self._translate_split(
                chapter_title, chapter_content, chapter_number,
                previous_summary, target_lang, genre,
            )

        return self._translate_once(
            chapter_title, chapter_content, chapter_number,
            previous_summary, target_lang, genre,
        )

    def _make_state(self, title, content, number, prev_summary, lang, genre) -> TranslatorState:
        return {
            "chapter_title": title,
            "chapter_content": content,
            "chapter_number": number,
            "target_lang": lang,
            "genre": genre,
            "exact_glossary": self.exact_store.to_dict(),
            "semantic_terms": [],
            "exact_matches_text": "",
            "semantic_matches_text": "",
            "translated_text": "",
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "",
            "previous_chapter_summary": prev_summary,
            "quality_score": 5.0,
            "quality_issues": [],
            "retranslation_count": 0,
            "glossary_snapshot_json": "",
            "term_conflicts": [],
            "resolved_conflicts": [],
            "dialect_context": "",
        }

    def _translate_once(self, title, content, number, prev_summary, lang, genre) -> dict:
        return self.graph.invoke(self._make_state(title, content, number, prev_summary, lang, genre))

    def _translate_split(self, title, content, number, prev_summary, lang, genre) -> dict:
        """Translate a long chapter by splitting into segments at paragraph boundaries.

        Each segment runs through the full 6-node pipeline independently.
        Segments 2+ inherit the glossary accumulated from earlier segments.
        The last 2 paragraphs of segment N become bridging context for segment N+1.
        """
        segments = split_chapter(content)
        logger.info(
            "Auto-split ch%d (%d chars) into %d segments",
            number, len(content.replace('\n','').replace(' ','')), len(segments),
        )

        all_text = []
        all_new_terms = []
        segment_summary = prev_summary
        final_result = {}

        for seg in segments:
            seg_title = build_segment_title(title, seg)

            result = self.graph.invoke(self._make_state(
                seg_title, seg["content"], number, segment_summary, lang, genre,
            ))

            tt = result.get("translated_text", "")
            all_text.append(tt)
            all_new_terms.extend(result.get("new_terms_found", []))
            final_result = result

            # Build bridging context from this segment's last 2 paragraphs
            # for the NEXT segment's continuity.
            if not seg["is_last"]:
                paras = [p for p in tt.split("\n\n") if len(p.strip()) > 50]
                bridge = "\n\n".join(paras[-2:]) if len(paras) >= 2 else (paras[-1] if paras else "")
                if bridge:
                    segment_summary = (
                        f"[Continuing from previous segment of the same chapter.]\n"
                        f"Previous segment ended with:\n{bridge}"
                    )

        return {
            "translated_text": "\n\n".join(all_text),
            "new_terms_found": all_new_terms,
            "adaptation_notes": final_result.get("adaptation_notes", []),
            "chapter_summary": final_result.get("chapter_summary", ""),
            "quality_score": final_result.get("quality_score", 5.0),
            "quality_issues": final_result.get("quality_issues", []),
            "glossary_snapshot_json": final_result.get("glossary_snapshot_json", "{}"),
        }
