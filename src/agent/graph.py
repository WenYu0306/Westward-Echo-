"""LangGraph graph assembly — v0.15 Reader-Centric Multi-Agent pipeline.

4-node graph:
  START → READ → WRITE → READBACK → (NEEDS_FIX?) → FIX → READBACK (loop)
                               ↓ (PASS)
                              END

Each agent is a READER, not a worker:
  READ     — Chinese web novel fan, reads and analyzes
  WRITE    — Bilingual genre writer, retells in English
  READBACK — Cold reader, naive American, honest reaction
  FIX      — Editor, repairs based on cold reader feedback
"""

from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from ..chapter_slicer import build_segment_title, should_split, split_chapter
from ..glossary.exact_store import ExactGlossary
from ..glossary.semantic_store import SemanticGlossary
from .nodes.fix import fix_node
from .nodes.read import read_node
from .nodes.readback import readback_node
from .nodes.write import write_node
from .state import TranslatorState

logger = logging.getLogger(__name__)

# Max FIX → READBACK loops per chapter
_MAX_FIX_ATTEMPTS = 2


def _should_readback(state: TranslatorState) -> str:
    """Route WRITE output: skip READBACK in fast mode, otherwise check quality."""
    if state.get("skip_readback", False):
        return END
    return "readback_node"


def _needs_fix(state: TranslatorState) -> str:
    """Route READBACK failures to the FIX editor.

    If the cold reader says NEEDS_FIX and we haven't exceeded max attempts,
    route to the FIX node — an editor who fixes specific reader complaints.
    """
    feedback = state.get("readback_feedback", {})
    verdict = feedback.get("verdict", "PASS") if feedback else "PASS"
    retries = state.get("retranslation_count", 0)

    if verdict == "NEEDS_FIX" and retries < _MAX_FIX_ATTEMPTS:
        return "fix_node"
    return END


def build_graph(
    exact_store: ExactGlossary,
    semantic_store: SemanticGlossary,
):
    """Build the v0.15 4-node reader-centric graph."""

    builder = StateGraph(TranslatorState)

    # ── Nodes ──
    builder.add_node(
        "read_node",
        lambda s: read_node(s, exact_store, semantic_store),
    )
    builder.add_node("write_node", write_node)
    builder.add_node("readback_node", readback_node)
    builder.add_node("fix_node", fix_node)

    # ── Edges ──
    builder.set_entry_point("read_node")
    builder.add_edge("read_node", "write_node")

    # ── Conditional: skip READBACK in fast mode, otherwise quality check ──
    builder.add_conditional_edges(
        "write_node",
        _should_readback,
        {
            "readback_node": "readback_node",
            END: END,
        },
    )

    # ── Conditional: cold reader fails → FIX, otherwise END ──
    builder.add_conditional_edges(
        "readback_node",
        _needs_fix,
        {
            "fix_node": "fix_node",
            END: END,
        },
    )

    # FIX → back to READBACK for re-check
    builder.add_edge("fix_node", "readback_node")

    return builder.compile()


class TranslationAgent:
    """High-level wrapper around the v0.15 reader-centric LangGraph pipeline."""

    def __init__(self, book_id: str = "default", api_key: str = ""):
        self.exact_store = ExactGlossary()
        self.semantic_store = SemanticGlossary()
        self.graph = build_graph(self.exact_store, self.semantic_store)
        from ..style_memo import StyleMemoStore
        self.style_memo: StyleMemoStore = StyleMemoStore(book_id)
        self._prefetched_exact: dict | None = None
        self._prefetched_semantic: list[dict] | None = None
        self._chapter_context: list[str] = []
        self.api_key = api_key  # User-provided BYOK key (empty = use config default)

    def _update_context(self, chapter_number: int, summary: str):
        """Accumulate recent chapter summaries for cold reader context."""
        if summary and len(summary) > 20:
            self._chapter_context.append(
                f"[Ch{chapter_number}] {summary.strip()[:300]}"
            )
        # Keep the last 5
        if len(self._chapter_context) > 5:
            self._chapter_context = self._chapter_context[-5:]

    def _build_cold_read_context(self, chapter_number: int = 0) -> str:
        """Build a one-page briefing for the cold reader — no LLM needed."""
        parts = []

        # ── 1. What's happened recently ──
        if self._chapter_context:
            parts.append("## PREVIOUSLY (recent chapter summaries)\n")
            for entry in self._chapter_context:
                parts.append(f"- {entry}")
            parts.append("")
        elif chapter_number > 1:
            parts.append("## PREVIOUSLY\n"
                         f"You are reading Chapter {chapter_number}. "
                         "This is an early chapter.\n")

        # ── 2. Who's who (from exact glossary) ──
        known = self.exact_store.to_dict()
        char_list = []
        for cn, en in known.items():
            if len(cn) >= 2 and len(en) >= 2:
                char_list.append(f"  - **{en}** ({cn})")
            if len(char_list) >= 15:
                break
        if char_list:
            parts.append("## CHARACTERS\n" + '\n'.join(char_list) + "\n")

        # ── 3. Key terms from style memo ──
        terms_text = self.style_memo.read_all()
        term_lines = []
        for line in terms_text.split('\n'):
            stripped = line.strip()
            if stripped.startswith('[') and '→' in stripped:
                term_lines.append(f"- {stripped[:120]}")
                if len(term_lines) >= 8:
                    break
        if term_lines:
            parts.append("## ESTABLISHED TERMINOLOGY\n")
            parts.extend(term_lines)
            parts.append("")

        if len(parts) <= 1:
            return ""
        return '\n'.join(parts)

    def load_glossary(self, target_lang: str = "en-US"):
        self.exact_store.load_from_db(target_lang)

    def load_glossary_snapshot(self, snapshot_json: str):
        if snapshot_json and snapshot_json != "{}":
            self.exact_store.restore_snapshot(snapshot_json)

    def set_prefetched_glossary(self, exact_matches: dict, semantic_matches: list[dict]):
        """Accept pre-computed glossary results from ChapterPrefetcher.

        When the prefetcher has already run exact-match and semantic-search
        for this chapter in a background thread, the caller injects the
        results here so _make_state can skip those lookups.
        """
        self._prefetched_exact = exact_matches
        self._prefetched_semantic = semantic_matches

    def translate_chapter(
        self,
        chapter_title: str,
        chapter_content: str,
        chapter_number: int,
        previous_summary: str = "",
        target_lang: str = "en-US",
        genre: str = "romance_ceo",
        skip_readback: bool = False,
        use_flash_writer: bool = False,
        content_type: str = "novel",
        script_mode: str = "full",
    ) -> dict:
        """Translate a single chapter through the 4-node reader-centric pipeline.

        Set skip_readback=True for fast mode: READ→WRITE→END, skipping
        the cold reader and editor nodes. Use for non-sample chapters
        to save ~50% API calls and latency.

        content_type selects the parallel prompt branch ("novel" default,
        "script" for short drama scripts); the novel path is unchanged.

        script_mode only applies when content_type == "script":
        "full" (default) returns the complete shooting script; "dialogue"
        runs the identical validated pipeline but post-filters the output
        down to speaker dialogue + OS lines (dubbing/ADR deliverable).
        The full pipeline always runs because dialogue quality depends on
        the action-line context (who speaks, how, why).

        Returns:
            {translated_text, new_terms_found, adaptation_notes,
             chapter_summary, quality_score, quality_issues,
             glossary_snapshot_json}
        """
        # ── Auto-split for long chapters ──────────────────────────
        if should_split(chapter_content):
            result = self._translate_split(
                chapter_title, chapter_content, chapter_number,
                previous_summary, target_lang, genre, skip_readback,
                content_type=content_type,
            )
        else:
            result = self._translate_once(
                chapter_title, chapter_content, chapter_number,
                previous_summary, target_lang, genre, skip_readback,
                content_type=content_type,
            )

        # ── Dialogue-only deliverable (script branch) ─────────────
        # The full pipeline always runs (dialogue quality depends on the
        # action-line context); the deliverable is filtered afterwards.
        # pre_filter_text carries the complete script so downstream
        # truncation guards can judge against the unfiltered word count.
        if content_type == "script" and script_mode == "dialogue":
            from ..script_splitter import extract_dialogue
            full_text = result.get("translated_text", "")
            result["translated_text"] = extract_dialogue(full_text)
            result["pre_filter_text"] = full_text

        return result

    def _make_state(
        self, title, content, number, prev_summary, lang, genre,
        skip_readback=False, use_flash_writer=False, content_type="novel",
    ) -> TranslatorState:
        """Build the initial state for a chapter translation.

        Populates exact_matches_text and semantic_matches_text from the
        glossary stores — this is the only glossary lookup step (no tool calls).
        """
        # Exact matches: terms that appear in this chapter's text.
        # Use notes-enhanced format so the WRITE agent gets cultural context,
        # not just word-for-word mappings.
        # If ChapterPrefetcher already ran these lookups in a background thread,
        # use the cached results to skip the blocking lookups.
        if self._prefetched_exact is not None:
            exact_matches = self._prefetched_exact
            self._prefetched_exact = None
        else:
            exact_matches = self.exact_store.match_in_text(content)
        exact_text = (
            self.exact_store.to_formatted_text_with_notes(exact_matches, target_lang=lang)
            if exact_matches
            else self.exact_store.to_formatted_text(exact_matches)
        )

        # Semantic matches: culturally related terms for context
        if self._prefetched_semantic is not None:
            semantic_hits = self._prefetched_semantic
            self._prefetched_semantic = None
        else:
            semantic_hits = self.semantic_store.search(content, top_k=15, target_lang=lang)
        # Filter out terms already covered by exact matches
        semantic_hits = [t for t in semantic_hits if t["term_cn"] not in exact_matches]
        semantic_text = self._format_semantic(semantic_hits)

        return {
            # Input
            "chapter_title": title,
            "chapter_content": content,
            "chapter_number": number,
            "target_lang": lang,
            "genre": genre,
            "content_type": content_type,
            # Glossary
            "exact_glossary": self.exact_store.to_dict(),
            "semantic_terms": semantic_hits,
            "exact_matches_text": exact_text,
            "semantic_matches_text": semantic_text,
            # Output (populated by nodes)
            "translated_text": "",
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "",
            # Context
            "previous_chapter_summary": prev_summary,
            # Quality / feedback
            "quality_score": 5.0,
            "quality_issues": [],
            "retranslation_count": 0,
            "glossary_snapshot_json": "",
            # v0.15 reader-centric fields
            "read_analysis": {},
            "readback_feedback": {},
            "context_signals": "",
            "image_gaps": [],
            "style_memo": self.style_memo.read_relevant(
                content, exact_matches,
            ) or (
                "(No translation memory yet. This is the first chapter. "
                "Stay close to the source text — do not invent sensory details "
                "or interior monologue that the original doesn't contain. "
                "Your creative authority grows as the memo accumulates. "
                "For now: translate faithfully.)"
            ),
            "skip_readback": skip_readback,
            "use_flash_writer": use_flash_writer,
            "api_key": self.api_key or "",  # BYOK: user-provided key overrides env
            # Cold reader briefing
            "cold_read_context": self._build_cold_read_context(
                chapter_number=number,
            ),
            # Kept for backward compat (unused by new nodes, harmless)
            "term_conflicts": [],
            "resolved_conflicts": [],
            "dialect_context": "",
        }

    def _translate_once(
        self, title, content, number, prev_summary, lang, genre,
        skip_readback=False, use_flash_writer=False, content_type="novel",
    ) -> dict:
        """Run the 4-node pipeline on a single chapter."""
        state = self._make_state(title, content, number, prev_summary, lang, genre,
                                 skip_readback, use_flash_writer, content_type)
        result = self.graph.invoke(state, config={"recursion_limit": 100})
        return self._post_process(result, lang)

    def _translate_split(
        self, title, content, number, prev_summary, lang, genre,
        skip_readback=False, use_flash_writer=False, content_type="novel",
    ) -> dict:
        """Translate a long chapter by splitting into segments.

        Each segment runs through the full 4-node pipeline independently.
        Segments 2+ inherit the glossary accumulated from earlier segments.
        """
        segments = split_chapter(content)
        logger.info(
            "Auto-split ch%d (%d chars) into %d segments",
            number, len(content.replace('\n', '').replace(' ', '')), len(segments),
        )

        all_text = []
        all_new_terms = []
        segment_summary = prev_summary
        final_result = {}

        for seg in segments:
            seg_title = build_segment_title(title, seg)

            result = self._translate_once(
                seg_title, seg["content"], number, segment_summary, lang, genre,
                skip_readback, use_flash_writer, content_type=content_type,
            )

            tt = result.get("translated_text", "")
            all_text.append(tt)
            all_new_terms.extend(result.get("new_terms_found", []))
            final_result = result

            # Build bridging context for the next segment
            if not seg["is_last"]:
                paras = [p for p in tt.split("\n\n") if len(p.strip()) > 50]
                bridge = (
                    "\n\n".join(paras[-2:]) if len(paras) >= 2 else (paras[-1] if paras else "")
                )
                if bridge:
                    segment_summary = (
                        f"[Continuing from previous segment.]\n"
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

    def _post_process(self, result: dict, target_lang: str = "en-US") -> dict:
        """After graph execution: persist new terms and update style memo.

        Enriches new terms with cultural notes from the READ agent's
        terminology_decisions so future chapters inherit not just the
        translation but the cultural reasoning behind it.
        """
        new_terms = result.get("new_terms_found", [])
        read_analysis = result.get("read_analysis", {})
        readback_feedback = result.get("readback_feedback", {})
        chapter_number = result.get("chapter_number", 0)
        retries = result.get("retranslation_count", 0)

        # ── Audit log: FORCED_ACCEPT when cold reader still says NEEDS_FIX ──
        # after max retries.  Without this log, we cannot diagnose whether
        # the chapter was too hard, the cold reader was too strict, or the
        # FIX agent is ineffective.
        if readback_feedback:
            verdict = readback_feedback.get("verdict", "PASS") if readback_feedback else "PASS"
            if verdict == "NEEDS_FIX" and retries >= 2:
                logger.warning(
                    "FORCED_ACCEPT ch%d: verdict=NEEDS_FIX after %d retries. "
                    "Cold reader issues: comprehension=%d, engagement=%d. "
                    "FIX changes: %s",
                    chapter_number, retries,
                    len(readback_feedback.get("comprehension_issues", [])),
                    len(readback_feedback.get("engagement_gaps", [])),
                    result.get("adaptation_notes", [])[:3],
                )

        # ── Update style memo from READ analysis (EVERY chapter) ──
        if read_analysis:
            self.style_memo.update_from_read_analysis(read_analysis, chapter_number)

        # ── Supplement with cold-reader feedback (sample chapters only) ──
        if readback_feedback:
            self.style_memo.update_from_feedback(
                readback_feedback, read_analysis, chapter_number,
            )

        # ── Merge READ's cultural notes into new terms ──────────
        if new_terms and read_analysis:
            term_notes = {}
            for td in read_analysis.get("terminology_decisions", []):
                note = td.get("cultural_note", "")
                if note:
                    term_notes[td.get("term_cn", "")] = note
            for t in new_terms:
                cn = t.get("term_cn", "")
                if cn in term_notes and not t.get("note"):
                    t["note"] = term_notes[cn]

        if new_terms:
            # Write character/location terms to exact store
            exact_terms = [
                t for t in new_terms
                if t.get("category") in ("character", "location")
            ]
            if exact_terms:
                self.exact_store.add_batch(
                    exact_terms, chapter=result.get("chapter_number", 0),
                    target_lang=target_lang,
                )

            # Write ALL terms to semantic store
            self.semantic_store.add_batch(new_terms, target_lang=target_lang)

        # Snapshot exact glossary for checkpoint
        result["glossary_snapshot_json"] = self.exact_store.snapshot()

        # ── Feed the cold reader's context buffer ──
        chapter_summary = result.get("chapter_summary", "")
        chapter_number = result.get("chapter_number", 0)
        self._update_context(chapter_number, chapter_summary)

        return result

    @staticmethod
    def _format_semantic(terms: list[dict]) -> str:
        """Format semantic results as a markdown table."""
        if not terms:
            return "(No semantic matches.)"
        lines = ["| Chinese | English | Category |",
                 "|----------|---------|----------|"]
        for t in terms:
            lines.append(
                f"| {t['term_cn']} | {t['term_en']} | "
                f"{t.get('category', 'culture')} |"
            )
        return "\n".join(lines)
