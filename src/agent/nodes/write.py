"""Node 2: WRITE — bilingual genre writer who retells the chapter in English.

Receives the READ agent's analysis as creative brief. Does NOT use tool calls.
Is a storyteller, not a translation machine.

Replaces: translate_node
"""

import json
import logging
import re
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..state import TranslatorState
from ..prompts.write import WRITE_SYSTEM, WRITE_USER
from ..prompts.translation import LANGUAGE_STYLE_NOTES
from ...config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP
from ...circuit_breaker import get_breaker, CircuitBreakerOpenError
from ...stats import TranslationStats
from ...job_store import job_store

logger = logging.getLogger(__name__)


def write_node(state: TranslatorState) -> dict:
    """WRITE: Retell the chapter in English using the READ agent's analysis.

    Returns a dict with:
        - translated_text: the complete English chapter
        - new_terms_found: newly discovered terms for the glossary
        - adaptation_notes: significant adaptation decisions
        - chapter_summary: 3-4 sentence plot summary for next chapter
    """
    chapter_number = state["chapter_number"]
    flash = state.get("use_flash_writer", False)
    model_id = "deepseek-v4-flash" if flash else MODEL_MAP["translate"]

    llm = ChatOpenAI(
        model=model_id,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.3,
        max_tokens=16384,
        request_timeout=120,
    )

    # Format the READ analysis as context
    read_analysis = state.get("read_analysis", {})
    analysis_text = _format_read_analysis(read_analysis)

    target_lang = state.get("target_lang", "en-US")

    # Build confirmed/rejected terms text
    confirmed = job_store.get_confirmed_terms(target_lang)
    confirmed_text = ""
    if confirmed:
        confirmed_text = "\n".join(
            f"- '{cn}' MUST be '{en}'. Human-confirmed — do NOT change."
            for cn, en in confirmed.items()
        )
    else:
        confirmed_text = "(No human-confirmed terms yet.)"

    rejected = job_store.get_rejected_terms(target_lang)
    rejected_text = ""
    if rejected:
        rejected_text = "\n".join(
            f"- DO NOT use '{r['rejected_en']}' for '{r['term_cn']}'. Human-rejected."
            for r in rejected
        )
    else:
        rejected_text = "(No human-rejected terms.)"

    regional_style = LANGUAGE_STYLE_NOTES.get(target_lang, "")
    image_gaps_text = _format_image_gaps(state.get("image_gaps", []))

    memo = state.get("style_memo", "(No style memo yet — this is the first chapter.)")
    user_prompt = WRITE_USER.format(
        style_memo=memo,
        reader_analysis=analysis_text,
        image_gaps=image_gaps_text,
        chapter_number=chapter_number,
        chapter_title=state["chapter_title"],
        genre=state.get("genre", "romance_ceo"),
        exact_matches=state.get("exact_matches_text", "(No glossary terms yet.)"),
        semantic_matches=state.get("semantic_matches_text", "(No semantic matches.)"),
        previous_summary=state.get("previous_chapter_summary", "(This is the first chapter.)"),
        confirmed_terms=confirmed_text,
        rejected_terms=rejected_text,
        regional_style=regional_style if regional_style else "(Standard American English.)",
        chapter_content=state["chapter_content"],
    )

    messages = [
        SystemMessage(content=WRITE_SYSTEM),
        HumanMessage(content=user_prompt),
    ]

    target_lang = state.get("target_lang", "en-US")
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

    _capture_response_tokens(response)

    result = _parse_write_response(
        response.content,
        chapter_number=chapter_number,
        target_lang=state.get("target_lang", "en-US"),
    )

    translated_text = result.get("translated_text", "")

    # Output quality guard: check for obvious garbage
    from ...output_guard import (
        check_and_record, sanitize_translation, has_untranslated_chinese,
    )

    if has_untranslated_chinese(translated_text) and target_lang != "zh-CN":
        logger.warning("WRITE ch%d: untranslated Chinese characters detected", chapter_number)

    warnings = check_and_record(
        translated_text,
        chapter_num=chapter_number,
        target_lang=target_lang,
    )
    if warnings:
        for w in warnings:
            logger.warning("Output guard ch%d: %s", chapter_number, w)

    translated_text = sanitize_translation(translated_text)

    # Auto-retry if output is empty
    RETRY_THRESHOLD = 10
    if not translated_text or len(translated_text.strip()) < RETRY_THRESHOLD:
        logger.warning(
            "WRITE ch%d: empty/short output (%d chars). Retrying once.",
            chapter_number, len(translated_text) if translated_text else 0,
        )
        try:
            retry_messages = [
                SystemMessage(content=WRITE_SYSTEM),
                HumanMessage(content=user_prompt + "\n\nCRITICAL: Your previous response was empty. "
                              "Output the complete translated chapter as JSON NOW. "
                              "The translated_text field MUST contain the full chapter."),
            ]
            TranslationStats.record_api_call(target_lang)
            retry_response = breaker.call(llm.invoke, retry_messages)
            TranslationStats.record_api_success(target_lang)
            _capture_response_tokens(retry_response)
            retry_result = _parse_write_response(retry_response.content, chapter_number, target_lang)
            retry_text = retry_result.get("translated_text", "")
            if retry_text and len(retry_text.strip()) >= RETRY_THRESHOLD:
                translated_text = sanitize_translation(retry_text)
                result = retry_result
                logger.info("WRITE ch%d: retry successful", chapter_number)
        except Exception as retry_exc:
            logger.error("WRITE ch%d: retry failed: %s", chapter_number, retry_exc)

    return {
        "translated_text": translated_text,
        "new_terms_found": result.get("new_terms_found", []),
        "adaptation_notes": result.get("adaptation_notes", []),
        "chapter_summary": result.get("chapter_summary", ""),
    }


def _format_read_analysis(analysis: dict) -> str:
    """Format the READ agent's analysis dict as readable text for the WRITE prompt."""
    parts = []

    if analysis.get("emotional_arc"):
        parts.append(f"## EMOTIONAL ARC\n{analysis['emotional_arc']}")

    if analysis.get("crafted_moments"):
        parts.append("## CRAFTED MOMENTS (preserve these)")
        for m in analysis["crafted_moments"]:
            parts.append(f"- {m}")

    if analysis.get("cultural_gaps"):
        parts.append("## CULTURAL GAPS (bridge these)")
        for g in analysis["cultural_gaps"]:
            parts.append(
                f"\n### {g.get('element', 'Unknown')}\n"
                f"- CN reader gets: {g.get('cn_reader_gets', '?')}\n"
                f"- EN reader misses: {g.get('en_reader_misses', '?')}\n"
                f"- Strategy: {g.get('bridge_strategy', 'context')}\n"
                f"- Guidance: {g.get('bridge_guidance', 'Use your judgment.')}"
            )

    if analysis.get("terminology_decisions"):
        parts.append("## TERMINOLOGY RECOMMENDATIONS")
        for t in analysis["terminology_decisions"]:
            parts.append(
                f"- **{t.get('term_cn', '?')}** → {t.get('proposed_en', '?')}"
                f"{' — ' + t['reasoning'] if t.get('reasoning') else ''}"
            )

    if analysis.get("pacing_notes"):
        parts.append(f"## PACING NOTES\n{analysis['pacing_notes']}")

    return "\n\n".join(parts)


def _format_image_gaps(gaps: list[dict]) -> str:
    """Format image gaps as sensory rebuilding instructions for the WRITER."""
    if not gaps:
        return "(No image gaps detected — write freely.)"

    critical = [g for g in gaps if g.get("priority") == "critical"]
    high = [g for g in gaps if g.get("priority") == "high"]
    medium = [g for g in gaps if g.get("priority") == "medium"]

    parts = []

    if critical:
        parts.append("### CRITICAL — These scenes FAIL without sensory rebuilding:")
        for g in critical:
            parts.append(
                f"\n**PASSAGE:** {g.get('passage', '?')}\n"
                f"**CN READER SEES:** {g.get('cn_reader_sees', '?')}\n"
                f"**EN READER GETS:** {g.get('en_reader_gets', '?')}\n"
                f"**BUILD WITH:** {g.get('sensory_anchors', 'use universal textures, sounds, colors')}"
            )

    if high:
        parts.append("\n### HIGH — Important sensory moments to rebuild:")
        for g in high:
            parts.append(
                f"\n**PASSAGE:** {g.get('passage', '?')}\n"
                f"**CN READER SEES:** {g.get('cn_reader_sees', '?')}\n"
                f"**BUILD WITH:** {g.get('sensory_anchors', 'add one vivid sensory detail')}"
            )

    if medium:
        parts.append("\n### MEDIUM — Add one sensory detail each:")
        for g in medium:
            parts.append(
                f"- {g.get('passage', '?')} — anchor: "
                f"{g.get('sensory_anchors', 'one sensory detail')}"
            )

    return "\n".join(parts)


def _capture_response_tokens(response) -> None:
    """Extract token usage from a LangChain AIMessage and record it."""
    try:
        usage = response.response_metadata.get("token_usage", {})
        if usage:
            TranslationStats.record_tokens(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            )
    except Exception:
        pass


def _parse_write_response(
    content: str,
    chapter_number: int = 0,
    target_lang: str = "en-US",
) -> dict:
    """Parse the WRITE agent's JSON output with multi-layer fallback."""
    from ...error_tracker import record_event

    text = content.strip()

    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        text = text.strip()

    # Layer 1: Strict JSON
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Layer 2: Regex-extracted JSON object
    m = re.search(r'\{[\s\S]*"translated_text"[\s\S]*\}', text)
    if m:
        try:
            result = json.loads(m.group())
            record_event(None, chapter_number, "parse_fallback",
                         "Layer 2: regex-extracted JSON object", target_lang)
            return result
        except (json.JSONDecodeError, ValueError):
            pass

    # Layer 3: Regex field extraction for translated_text
    m = re.search(r'"translated_text"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        tt = m.group(1).replace('\\"', '"').replace('\\n', '\n')
        record_event(None, chapter_number, "parse_fallback",
                     "Layer 3: regex field extraction", target_lang)
        return {
            "translated_text": tt,
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "",
        }

    # Layer 4: Markdown-as-translation
    if re.match(r'^(#+\s|>|\*\*|[A-Z][a-z])', text):
        record_event(None, chapter_number, "parse_fallback",
                     "Layer 4: markdown-as-translation", target_lang)
        return {
            "translated_text": text,
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "",
        }

    # Layer 5: Raw content fallback
    record_event(None, chapter_number, "parse_fallback",
                 "Layer 5: raw content fallback", target_lang)
    return {
        "translated_text": content.lstrip("```json").lstrip("```").strip(),
        "new_terms_found": [],
        "adaptation_notes": [],
        "chapter_summary": "",
    }
