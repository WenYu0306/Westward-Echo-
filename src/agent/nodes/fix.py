"""Node 4: FIX — editor who repairs based on cold reader feedback.

Receives the cold reader's specific complaints and fixes only what's broken.
Does NOT blindly re-translate. Fixes are targeted and surgical.

Replaces: polish_node
"""

import json
import re
import logging
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..state import TranslatorState
from ..prompts.fix import FIX_SYSTEM, FIX_USER
from ...config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP
from ...circuit_breaker import get_breaker, CircuitBreakerOpenError
from ...stats import TranslationStats

logger = logging.getLogger(__name__)


def fix_node(state: TranslatorState) -> dict:
    """FIX: Repair specific issues the cold reader identified.

    Returns a dict with:
        - translated_text: the fixed English chapter
        - adaptation_notes: what was changed and why
    """
    llm = ChatOpenAI(
        model=MODEL_MAP["translate_critical"],  # Pro — editing needs precision
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.1,
        max_tokens=16384,
        request_timeout=120,
    )

    # Format the cold reader's feedback for the editor
    feedback = state.get("readback_feedback", {})
    feedback_text = _format_readback_feedback(feedback)

    target_lang = state.get("target_lang", "en-US")

    user_prompt = FIX_USER.format(
        original_cn=state["chapter_content"],
        current_en=state.get("translated_text", ""),
        reader_feedback=feedback_text,
        glossary_text=state.get("exact_matches_text", "(No glossary)"),
    )

    messages = [
        SystemMessage(content=FIX_SYSTEM),
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

    # Reset quality issues so READBACK gets a clean slate on re-check
    return {
        "translated_text": polished,
        "adaptation_notes": changes,
        "quality_issues": [],
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

    return "\n\n".join(parts) if parts else "(No specific issues — the reader just didn't enjoy it.)"


def _parse_fix_response(content: str) -> dict:
    """Parse the FIX agent's JSON output with fallback."""
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
        "polished_text": content,
        "changes_made": ["(Parser fallback — raw response returned)"],
    }
