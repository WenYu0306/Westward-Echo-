"""Node 1: READ — Chinese web novel reader analysis.

Does NOT translate. Reads the chapter, experiences it, and produces a
structured analysis that becomes the creative brief for the WRITE agent.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ...circuit_breaker import CircuitBreakerOpenError, get_breaker
from ...config import node_llm_kwargs
from ...cultural_rules import (
    format_fidelity_for_prompt,
    format_rules_as_bullets,
    load_fidelity_rules,
    load_rules,
)
from ...glossary.exact_store import ExactGlossary
from ...glossary.semantic_store import SemanticGlossary
from ...stats import TranslationStats
from ... import token_ledger
from ..prompts.personas import build_prompt_context
from ..prompts.registry import get_prompt_set
from ..state import TranslatorState

logger = logging.getLogger(__name__)


def _build_context_signals(state: TranslatorState) -> str:
    """Aggregate all 9 context signal detectors into one text block.

    Each detector is called and its output (if any) is collected. The READ
    agent uses these as STARTING POINTS for its own investigation, not as
    conclusions. Signals may be incomplete, wrong, or irrelevant to this
    specific chapter.
    """
    from ...dialect import build_dialect_context, has_system_text
    from ...idioms import build_idiom_context
    from ...measurements import build_measurements_hint
    from ...onomatopoeia import build_onomatopoeia_context
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
    # Model / provider / reasoning control come from the per-node routing table
    # (config.node_llm_kwargs) so a tier change is one env var, not a code edit.
    # Generation parameters below are READ's own (deep cultural-gap analysis).
    provider_kwargs = node_llm_kwargs("read", api_key_override=state.get("api_key", ""))
    llm = ChatOpenAI(  # type: ignore[call-arg]
        temperature=0.3,
        max_tokens=16384,
        request_timeout=900,
        max_retries=0,
        **provider_kwargs,
    )
    used_model = provider_kwargs["model"]

    target_lang = state.get("target_lang", "en-US")
    genre = state.get("genre", "romance_ceo")

    # Load cultural rules for the READ agent's reference. book_id is passed so
    # a book's own proper nouns (see load_rules docstring) are applied to that
    # book only, not to every other book sharing its genre.
    rules = load_rules(target_lang=target_lang, genre=genre,
                       book_id=state.get("book_id", ""))
    cultural_rules_bullets = format_rules_as_bullets(rules)

    # Strategy-level cultural fidelity rules (how to render a CATEGORY of terms)
    fidelity_rules = format_fidelity_for_prompt(
        load_fidelity_rules(target_lang),
    ) or "(No cultural fidelity rules for this language.)"

    # Aggregate context signals
    context_signals = _build_context_signals(state)

    # Build exact matches text from glossary
    exact_text = state.get("exact_matches_text", "(No glossary terms yet.)")

    memo = state.get("style_memo", "(No style memo yet — this is the first chapter.)")
    prompts = get_prompt_set(state.get("content_type", "novel"))
    # Per-genre / per-language parameters for the system prompt. The system
    # prompt embeds JSON examples with doubled braces, so .format() renders the
    # placeholders and leaves the JSON literal.
    ctx = build_prompt_context(genre, target_lang)
    user_prompt = prompts.read_user.format(
        style_memo=memo,
        chapter_number=state["chapter_number"],
        chapter_title=state["chapter_title"],
        genre=genre,
        target_language=target_lang,
        audience=ctx["audience"],
        previous_summary=state.get("previous_chapter_summary", "(This is the first chapter.)"),
        exact_matches=exact_text,
        cultural_rules_table=(
            cultural_rules_bullets if cultural_rules_bullets else "(No genre-specific rules.)"
        ),
        fidelity_rules=fidelity_rules,
        context_signals=context_signals,
        chapter_content=state["chapter_content"],
    )

    messages = [
        SystemMessage(content=prompts.read_system.format(**ctx)),
        HumanMessage(content=user_prompt),
    ]

    breaker = get_breaker(target_lang)
    try:
        TranslationStats.record_api_call(target_lang)
        response = breaker.call(llm.invoke, messages)
        TranslationStats.record_api_success(target_lang)
        _capture_read_tokens(
            response,
            model=used_model,
            book_id=state.get("book_id", ""),
            chapter=state.get("chapter_number", 0),
        )
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


def _capture_read_tokens(response, model: str, *, book_id: str = "", chapter: int = 0) -> None:
    """Record READ token usage — per node/chapter in the ledger, plus legacy counters.

    ``model`` is the model that actually served the chapter, not a constant: the
    routing table can send READ to another tier, and booking its tokens against a
    hard-coded name would put the cost on the wrong model — the same defect
    write.py already fixed with ``used_model``.

    The ledger row is written even when the provider sent no usage payload: it is
    recorded as unmetered so the gap is countable instead of invisible.
    """
    usage_in, usage_out = token_ledger.extract_usage(response)
    token_ledger.record("read", model, usage_in, usage_out,
                        book_id=book_id, chapter=chapter)
    # Half a usage payload is not a figure worth accumulating into the
    # legacy counters; the ledger row above already carries whatever
    # arrived, plus the fact that it was incomplete.
    if usage_in is None or usage_out is None:
        return
    try:
        TranslationStats.record_tokens(
            input_tokens=usage_in,
            output_tokens=usage_out,
            tier="pro",
        )
    except Exception:
        pass


def _parse_read_response(content: str) -> dict:
    """Parse the READ agent's JSON output with fallback."""
    from ..parse_utils import parse_llm_json

    fallback = {
        "emotional_arc": "Parse failed — READ agent output could not be parsed as JSON.",
        "cultural_gaps": [],
        "terminology_decisions": [],
        "pacing_notes": "",
        "crafted_moments": [],
        "image_gaps": [],
    }
    result, _ = parse_llm_json(content, fallback)
    return result
