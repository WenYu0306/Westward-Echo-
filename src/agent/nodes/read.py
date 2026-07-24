"""Node 1: READ — Chinese web novel reader analysis.

Does NOT translate. Reads the chapter, experiences it, and produces a
structured analysis that becomes the creative brief for the WRITE agent.

Replaces: fetch_glossary + the pre-reading/cultural-context phase of translate_node
"""

import json
import logging
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..state import TranslatorState
from ..prompts.read import READ_SYSTEM, READ_USER
from ...config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP
from ...glossary.exact_store import ExactGlossary
from ...glossary.semantic_store import SemanticGlossary
from ...cultural_rules import load_rules, format_rules_as_bullets
from ...circuit_breaker import get_breaker, CircuitBreakerOpenError
from ...stats import TranslationStats

logger = logging.getLogger(__name__)


def _build_context_signals(state: TranslatorState) -> str:
    """Aggregate all 9 context signal detectors into one text block.

    Each detector is called and its output (if any) is collected. The READ
    agent uses these as STARTING POINTS for its own investigation, not as
    conclusions. Signals may be incomplete, wrong, or irrelevant to this
    specific chapter.
    """
    from ...dialect import build_dialect_context, has_system_text
    from ...measurements import build_measurements_hint
    from ...onomatopoeia import build_onomatopoeia_context
    from ...idioms import build_idiom_context
    from ...sensitive_terms import build_sensitive_term_context

    chapter = state["chapter_content"]
    target_lang = state.get("target_lang", "en-US")

    signals = []

    d = build_dialect_context(chapter)
    if d:
        signals.append(d)

    if has_system_text(chapter):
        signals.append(
            "[AUTO-DETECTED] Game-like system notification markers found in "
            "this chapter — possible LitRPG elements. Verify by reading."
        )

    m = build_measurements_hint(chapter)
    if m:
        signals.append(m)

    o = build_onomatopoeia_context(chapter)
    if o:
        signals.append(o)

    i = build_idiom_context(chapter)
    if i:
        signals.append(i)

    s = build_sensitive_term_context(chapter, target_lang)
    if s:
        signals.append(s)

    header = (
        "[AUTO-DETECTED SIGNALS — starting points for your reading, "
        "not conclusions. Verify by reading the actual text.]\n\n"
    ) if signals else ""
    return header + "\n\n".join(signals) if signals else "(No auto-detected signals.)"


def read_node(
    state: TranslatorState,
    exact_store: ExactGlossary,
    semantic_store: SemanticGlossary,
) -> dict:
    """READ: Analyze the chapter as a Chinese web novel reader.

    Returns a dict with:
        - read_analysis: the READ agent's structured analysis (JSON)
        - context_signals: aggregated context signal text
    """
    llm = ChatOpenAI(
        model=MODEL_MAP.get("read", MODEL_MAP["translate"]),
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.3,
        max_tokens=4096,
    )

    target_lang = state.get("target_lang", "en-US")
    genre = state.get("genre", "romance_ceo")

    # Load cultural rules for the READ agent's reference
    rules = load_rules(target_lang=target_lang, genre=genre)
    cultural_rules_bullets = format_rules_as_bullets(rules)

    # Aggregate context signals
    context_signals = _build_context_signals(state)

    # Build exact matches text from glossary
    exact_text = state.get("exact_matches_text", "(No glossary terms yet.)")

    # Also run semantic search for additional context
    semantic_hits = semantic_store.search(
        state["chapter_content"], top_k=10, target_lang=target_lang
    )

    memo = state.get("style_memo", "(No style memo yet — this is the first chapter.)")
    user_prompt = READ_USER.format(
        style_memo=memo,
        chapter_number=state["chapter_number"],
        chapter_title=state["chapter_title"],
        genre=genre,
        target_language=target_lang,
        previous_summary=state.get("previous_chapter_summary", "(This is the first chapter.)"),
        exact_matches=exact_text,
        cultural_rules_table=cultural_rules_bullets if cultural_rules_bullets else "(No genre-specific rules.)",
        context_signals=context_signals,
        chapter_content=state["chapter_content"],
    )

    messages = [
        SystemMessage(content=READ_SYSTEM),
        HumanMessage(content=user_prompt),
    ]

    breaker = get_breaker(target_lang)
    try:
        TranslationStats.record_api_call(target_lang)
        response = breaker.call(llm.invoke, messages)
        TranslationStats.record_api_success(target_lang)
    except CircuitBreakerOpenError:
        TranslationStats.record_api_failure(target_lang)
        raise
    except Exception:
        TranslationStats.record_api_failure(target_lang)
        raise

    # Parse the READ agent's analysis
    analysis = _parse_read_response(response.content)

    logger.info(
        "READ ch%d: %d cultural gaps, %d image gaps, %d terms",
        state["chapter_number"],
        len(analysis.get("cultural_gaps", [])),
        len(analysis.get("image_gaps", [])),
        len(analysis.get("terminology_decisions", [])),
    )

    return {
        "read_analysis": analysis,
        "context_signals": context_signals,
        "image_gaps": analysis.get("image_gaps", []),
    }


def _parse_read_response(content: str) -> dict:
    """Parse the READ agent's JSON output with fallback."""
    import re
    text = content.strip()

    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        text = text.strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group())
        except (json.JSONDecodeError, ValueError):
            pass

    return {
        "emotional_arc": "Parse failed — READ agent output could not be parsed as JSON.",
        "cultural_gaps": [],
        "terminology_decisions": [],
        "pacing_notes": "",
        "crafted_moments": [],
    }
