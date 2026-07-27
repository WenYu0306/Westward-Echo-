"""Node 3: READBACK — cold reader with no prior knowledge.

Reads the English output as a naive American reader. Does NOT see the Chinese
original. Does NOT know this is a translation. Reports honest experience.

Replaces: quality_check
"""

import json
import re
import logging
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..state import TranslatorState
from ..prompts.readback import READBACK_SYSTEM, READBACK_USER
from ...config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP
from ...circuit_breaker import get_breaker, CircuitBreakerOpenError
from ...stats import TranslationStats

logger = logging.getLogger(__name__)


def readback_node(state: TranslatorState) -> dict:
    """READBACK: Cold-read the English chapter as a naive American reader.

    Returns a dict with:
        - readback_feedback: cold reader's structured reaction
        - quality_score: mapped from the verdict (PASS=5.0, NEEDS_FIX=2.0)
        - quality_issues: comprehension/engagement issues if any
    """
    translated_text = state.get("translated_text", "")

    # Skip if the chapter is empty or too short
    if not translated_text or len(translated_text.strip()) < 50:
        return {
            "readback_feedback": {
                "overall_impression": "Chapter is empty or too short to evaluate.",
                "verdict": "NEEDS_FIX",
                "would_keep_reading": False,
            },
            "quality_score": 0.0,
            "quality_issues": ["EMPTY: Chapter output is empty or too short."],
        }

    llm = ChatOpenAI(
        model=MODEL_MAP["readback"],
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.1,
        max_tokens=2048,
        request_timeout=120,
        max_retries=0,
    )

    # Build previous context for the cold reader
    cold_context = state.get("cold_read_context", "")
    if not cold_context:
        cold_context = ""

    user_prompt = READBACK_USER.format(
        previous_context=cold_context,
        chapter_content=translated_text,
    )

    messages = [
        SystemMessage(content=READBACK_SYSTEM),
        HumanMessage(content=user_prompt),
    ]

    target_lang = state.get("target_lang", "en-US")
    breaker = get_breaker(target_lang)

    try:
        TranslationStats.record_api_call(target_lang)
        response = breaker.call(llm.invoke, messages)
        TranslationStats.record_api_success(target_lang)
        _capture_readback_tokens(response)
    except CircuitBreakerOpenError:
        TranslationStats.record_api_failure(target_lang)
        raise
    except Exception:
        TranslationStats.record_api_failure(target_lang)
        raise

    feedback = _parse_readback_response(response.content)

    verdict = feedback.get("verdict", "PASS")
    quality_score = 5.0 if verdict == "PASS" else 2.0

    issues = []
    for ci in feedback.get("comprehension_issues", []):
        issues.append(f"COLD_READER: {ci.get('passage', '?')[:80]} — {ci.get('issue', '?')}")
    for eg in feedback.get("engagement_gaps", []):
        issues.append(f"BORED: {eg.get('passage', '?')[:80]} — {eg.get('issue', '?')}")

    logger.info(
        "READBACK ch%d: verdict=%s, would_keep=%s, issues=%d",
        state["chapter_number"],
        verdict,
        feedback.get("would_keep_reading", False),
        len(issues),
    )

    if verdict == "NEEDS_FIX":
        try:
            from ...error_tracker import record_event
            record_event(
                None, state["chapter_number"],
                "cold_read_fail",
                f"Cold reader verdict: NEEDS_FIX — {len(issues)} issues",
                target_lang,
            )
        except Exception:
            pass

    return {
        "readback_feedback": feedback,
        "quality_score": quality_score,
        "quality_issues": issues,
    }


def _capture_readback_tokens(response) -> None:
    """Record READBACK token usage — Flash tier."""
    try:
        usage = response.response_metadata.get("token_usage", {})
        if usage:
            TranslationStats.record_tokens(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                tier="flash",
            )
    except Exception:
        pass


def _parse_readback_response(content: str) -> dict:
    """Parse the READBACK agent's JSON output with fallback.

    Falls back to NEEDS_FIX on parse failure — a garbled LLM response
    must NOT silently pass quality gate.
    """
    from ..parse_utils import parse_llm_json

    fallback = {
        "overall_impression": "Parse failed — could not evaluate chapter.",
        "verdict": "NEEDS_FIX",
        "would_keep_reading": False,
        "comprehension_issues": [],
        "engagement_gaps": [],
        "standout_moments": [],
        "character_tracking": "",
        "world_comprehension": "",
    }
    result, _ = parse_llm_json(content, fallback)
    return result
