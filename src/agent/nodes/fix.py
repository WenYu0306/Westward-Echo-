"""Node 4: FIX — editor who repairs based on cold reader feedback.

Receives the cold reader's specific complaints and fixes only what's broken.
Does NOT blindly re-translate. Fixes are targeted and surgical.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ...circuit_breaker import CircuitBreakerOpenError, get_breaker
from ...config import node_llm_kwargs
from ...stats import TranslationStats
from ... import token_ledger
from ..prompts.personas import build_prompt_context
from ..prompts.registry import get_prompt_set
from ..state import TranslatorState

logger = logging.getLogger(__name__)


def fix_node(state: TranslatorState) -> dict:
    """FIX: Repair specific issues the cold reader identified.

    Returns a dict with:
        - translated_text: the fixed English chapter
        - adaptation_notes: what was changed and why
    """
    # Model / provider / reasoning control come from the per-node routing table.
    # FIX is a deep-repair task, so its default effort stays high (GLM_FIX_EFFORT);
    # generation params below are FIX's own.
    provider_kwargs = node_llm_kwargs("fix", api_key_override=state.get("api_key", ""))
    llm = ChatOpenAI(  # type: ignore[call-arg]
        temperature=0.1,
        max_tokens=16384,
        request_timeout=900,
        max_retries=0,
        **provider_kwargs,
    )
    used_model = provider_kwargs["model"]

    # Format the cold reader's feedback for the editor
    feedback = state.get("readback_feedback", {})
    feedback_text = _format_readback_feedback(feedback)

    target_lang = state.get("target_lang", "en-US")

    prompts = get_prompt_set(state.get("content_type", "novel"))
    # Per-genre / per-language parameters. FIX only needs the output language
    # and audience wording (its brief is the cold reader's feedback, not genre
    # conventions), but the system prompt is rendered through the same context
    # as the other three nodes so the JSON braces stay literal.
    ctx = build_prompt_context(state.get("genre"), target_lang)
    user_prompt = prompts.fix_user.format(
        original_cn=state["chapter_content"],
        current_en=state.get("translated_text", ""),
        reader_feedback=feedback_text,
        glossary_text=state.get("exact_matches_text", "(No glossary)"),
        output_language=ctx["output_language"],
    )

    messages = [
        SystemMessage(content=prompts.fix_system.format(**ctx)),
        HumanMessage(content=user_prompt),
    ]

    breaker = get_breaker(target_lang)

    try:
        TranslationStats.record_api_call(target_lang)
        response = breaker.call(llm.invoke, messages)
        TranslationStats.record_api_success(target_lang)
        _capture_fix_tokens(
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

    result = _parse_fix_response(response.content)

    polished = result.get("polished_text", state.get("translated_text", ""))
    changes = result.get("changes_made", [])

    logger.info(
        "FIX ch%d: %d changes made, %d chars → %d chars",
        state["chapter_number"],
        len(changes),
        len(state.get("translated_text", "")),
        len(polished),
    )

    # Reset quality issues so READBACK gets a clean slate on re-check.
    # Increment retranslation_count so _needs_fix can terminate the
    # FIX→READBACK→FIX loop after _MAX_FIX_ATTEMPTS.
    retries = state.get("retranslation_count", 0) + 1
    return {
        "translated_text": polished,
        "adaptation_notes": changes,
        # Accumulated separately: on a second FIX round, adaptation_notes (a
        # plain field) is overwritten, so the first round's repair knowledge
        # would be lost before anything could read it. fix_lessons keeps every
        # round, and graph._post_process is what finally banks them.
        "fix_lessons": [c.strip() for c in changes if isinstance(c, str) and c.strip()],
        "quality_issues": [],
        "retranslation_count": retries,
    }


def _format_readback_feedback(feedback: dict) -> str:
    """Format the cold reader's structured feedback as actionable editor notes."""
    parts = []

    if feedback.get("overall_impression"):
        parts.append(f"## READER'S OVERALL IMPRESSION\n{feedback['overall_impression']}")

    if feedback.get("comprehension_issues"):
        parts.append("## COMPREHENSION ISSUES (reader was confused)")
        for ci in feedback["comprehension_issues"]:
            parts.append(f"\n**Passage:** {ci.get('passage', '?')}\n"
                        f"**Problem:** {ci.get('issue', '?')}")

    if feedback.get("engagement_gaps"):
        parts.append("## ENGAGEMENT GAPS (reader was bored or wanted to skip)")
        for eg in feedback["engagement_gaps"]:
            parts.append(f"\n**Passage:** {eg.get('passage', '?')}\n"
                        f"**Problem:** {eg.get('issue', '?')}")

    if feedback.get("character_tracking"):
        parts.append(f"## CHARACTER TRACKING\n{feedback['character_tracking']}")

    if feedback.get("world_comprehension"):
        parts.append(f"## WORLD COMPREHENSION\n{feedback['world_comprehension']}")

    if feedback.get("standout_moments"):
        parts.append("## MOMENTS THAT WORKED (preserve these)")
        for m in feedback["standout_moments"]:
            parts.append(f"- {m}")

    return (
        "\n\n".join(parts) if parts else "(No specific issues — the reader just didn't enjoy it.)"
    )


def _capture_fix_tokens(response, model: str, *, book_id: str = "", chapter: int = 0) -> None:
    """Record FIX token usage — per node/chapter in the ledger.

    ``model`` is what actually served the chapter, so a re-tiered FIX is booked
    against the tier it ran on rather than a hard-coded constant.
    """
    usage_in, usage_out = token_ledger.extract_usage(response)
    token_ledger.record("fix", model, usage_in, usage_out,
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
            tier="flash",
        )
    except Exception:
        pass


def _parse_fix_response(content: str) -> dict:
    """Parse the FIX agent's JSON output with fallback."""
    from ..parse_utils import parse_llm_json

    fallback = {
        "polished_text": content,
        "changes_made": ["(Parser fallback — raw response returned)"],
    }
    result, _ = parse_llm_json(content, fallback)
    return result
