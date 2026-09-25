"""Node 2: WRITE — bilingual genre writer who retells the chapter in English.

Receives the READ agent's analysis as creative brief. Does NOT use tool calls.
Is a storyteller, not a translation machine.
"""

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ...circuit_breaker import CircuitBreakerOpenError, get_breaker
from ...completeness import check_completeness
from ...config import node_llm_kwargs
from ...cultural_rules import format_fidelity_for_prompt, load_fidelity_rules
from ...job_store import job_store
from ...stats import TranslationStats
from ... import token_ledger
from ..prompts.personas import build_prompt_context
from ..prompts.registry import get_prompt_set
from ..prompts.translation import LANGUAGE_STYLE_NOTES
from ..state import TranslatorState

logger = logging.getLogger(__name__)

# Outputs shorter than this are treated as empty and trigger a retry.
RETRY_THRESHOLD = 10


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
    # Which model actually served this chapter. The GLM-1301 fallback below
    # switches providers mid-chapter; without this the fallback call was booked
    # against GLM and its cost landed on the wrong model.
    # Model / provider / reasoning control come from the per-node routing table.
    provider_kwargs = node_llm_kwargs("write", api_key_override=state.get("api_key", ""))
    used_model = provider_kwargs["model"]

    llm = ChatOpenAI(  # type: ignore[call-arg]
        temperature=0.2,
        max_tokens=16384,
        request_timeout=300,
        max_retries=0,
        model_kwargs={"response_format": {"type": "json_object"}},
        **provider_kwargs,
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
    fidelity_all = load_fidelity_rules(target_lang)
    fidelity_brief = {
        k: v for k, v in fidelity_all.items()
        if k in ("character_names", "terms_of_address")
    }
    fidelity_text = format_fidelity_for_prompt(
        fidelity_brief,
    ) or "(No cultural fidelity rules for this language.)"
    image_gaps_text = _format_image_gaps(state.get("image_gaps", []))

    memo = state.get("style_memo", "(No style memo yet — this is the first chapter.)")
    prompts = get_prompt_set(state.get("content_type", "novel"))
    genre = state.get("genre", "romance_ceo")
    # Per-genre / per-language parameters. The system prompt embeds JSON
    # examples with doubled braces, so .format() renders placeholders and
    # leaves the JSON literal. Rendered once here and reused by the empty-output
    # retry path below so both calls see identical instructions.
    ctx = build_prompt_context(genre, target_lang)
    system_prompt = prompts.write_system.format(**ctx)
    user_prompt = prompts.write_user.format(
        style_memo=memo,
        reader_analysis=analysis_text,
        image_gaps=image_gaps_text,
        chapter_number=chapter_number,
        chapter_title=state["chapter_title"],
        genre=genre,
        exact_matches=state.get("exact_matches_text", "(No glossary terms yet.)"),
        semantic_matches=state.get("semantic_matches_text", "(No semantic matches.)"),
        previous_summary=state.get("previous_chapter_summary", "(This is the first chapter.)"),
        confirmed_terms=confirmed_text,
        rejected_terms=rejected_text,
        regional_style=regional_style if regional_style else "(Standard American English.)",
        fidelity_rules=fidelity_text,
        output_language=ctx["output_language"],
        reader=ctx["reader"],
        readers=ctx["readers"],
        chapter_content=state["chapter_content"],
    )

    messages = [
        SystemMessage(content=system_prompt),
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
    except Exception as exc:
        TranslationStats.record_api_failure(target_lang)
        if not _is_content_filter_error(exc):
            raise
        # The safety layer rejected this chapter (Zhipu code 1301). Same-provider
        # retry cannot help, and editing the source to slip past the filter would
        # mean shipping a censored translation — the one thing this pipeline must
        # not do. Fall back to the other configured provider for this chapter
        # only, and record it so a corpus-level shift in filter behaviour is
        # visible rather than silent.
        fallback_kwargs = node_llm_kwargs(
            "write_fallback", api_key_override=state.get("api_key", "")
        )
        fallback_model = fallback_kwargs["model"]
        logger.warning(
            "WRITE ch%d: content filter rejected the chapter (1301) — retrying "
            "once on %s", chapter_number, fallback_model,
        )
        try:
            from ...error_tracker import record_event
            record_event(None, chapter_number, "content_filter_fallback",
                         f"GLM 1301 on ch{chapter_number}; retried on {fallback_model}",
                         target_lang)
        except Exception:
            pass
        fallback_key = fallback_kwargs["api_key"]
        if not fallback_key:
            raise
        response = breaker.call(
            _build_fallback_writer(**fallback_kwargs).invoke, messages
        )
        used_model = fallback_model
        TranslationStats.record_api_success(target_lang)

    _capture_response_tokens(
        response,
        model=used_model,
        tier="flash" if flash else "pro",
        book_id=state.get("book_id", ""),
        chapter=chapter_number,
    )

    result = _parse_write_response(
        response.content,
        chapter_number=chapter_number,
        target_lang=state.get("target_lang", "en-US"),
    )

    translated_text = result.get("translated_text", "")

    # A response cut off at the token ceiling is not a translation. Blank it so
    # the empty-output retry below fires, instead of shipping the partial text.
    truncated = _was_truncated(response)
    if truncated:
        logger.warning(
            "WRITE ch%d: TRUNCATED response (finish_reason=%s, %d chars of "
            "partial output discarded) — treating as failed output",
            chapter_number, truncated, len(translated_text),
        )
        try:
            from ...error_tracker import record_event
            record_event(None, chapter_number, "output_truncated",
                         f"WRITE finish_reason={truncated}", target_lang)
        except Exception:
            pass
        translated_text = ""

    # Output quality guard: check for obvious garbage
    from ...output_guard import (
        check_and_record,
        has_json_envelope_residue,
        has_meta_text_leak,
        has_untranslated_chinese,
        sanitize_translation,
        strip_chinese_residue,
    )

    if has_untranslated_chinese(translated_text) and target_lang != "zh-CN":
        cleaned, removed = strip_chinese_residue(translated_text)
        logger.warning(
            "WRITE ch%d: %d Chinese chars stripped (%d chars before → %d after)",
            chapter_number, removed, len(translated_text), len(cleaned),
        )
        translated_text = cleaned

    warnings = check_and_record(
        translated_text,
        chapter_num=chapter_number,
        target_lang=target_lang,
    )
    if warnings:
        for w in warnings:
            logger.warning("Output guard ch%d: %s", chapter_number, w)

    translated_text = sanitize_translation(translated_text)

    # JSON-envelope residue / scaffolding leak guard. Same recovery contract as
    # the truncation guard above: discard the bad body so the empty-output retry
    # below fires, rather than shipping it. These two defect classes reached a
    # delivered file (Ch366 ```json fence, Ch1472 unpacked envelope, Ch1077 /
    # Ch1286 "End of Chapter." scaffolding) because nothing checked for them at
    # production time — scan_deliverable.py only found them after the fact. The
    # judgements are shared with that scanner (output_guard is the single
    # source), so the two can never drift.
    residue = (
        "json_envelope" if has_json_envelope_residue(translated_text)
        else "meta_text" if has_meta_text_leak(translated_text)
        else ""
    )
    if residue:
        logger.warning(
            "WRITE ch%d: %s residue in output (%d chars discarded) — treating as "
            "failed output", chapter_number, residue, len(translated_text),
        )
        try:
            from ...error_tracker import record_event
            record_event(None, chapter_number, f"{residue}_residue",
                         f"WRITE ch{chapter_number}: {residue} in finished body",
                         target_lang)
        except Exception:
            pass
        translated_text = ""

    # Auto-retry if output is empty (up to 2 additional attempts)
    if not translated_text or len(translated_text.strip()) < RETRY_THRESHOLD:
        for retry_num in range(1, 3):  # Retry 1, Retry 2
            logger.warning(
                "WRITE ch%d: empty/short output (%d chars). Retry %d/2.",
                chapter_number, len(translated_text) if translated_text else 0, retry_num,
            )
            try:
                retry_messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt
                                  + "\n\nCRITICAL: Your previous response was empty. "
                                  "Output the complete translated chapter as JSON NOW. "
                                  "The translated_text field MUST contain the full chapter."),
                ]
                TranslationStats.record_api_call(target_lang)
                retry_response = breaker.call(llm.invoke, retry_messages)
                TranslationStats.record_api_success(target_lang)
                _capture_response_tokens(
                    retry_response,
                    # The retry reuses the *primary* client, so it is booked
                    # against the primary tier even when an earlier 1301 sent
                    # this chapter's first attempt to the fallback provider.
                    model=provider_kwargs["model"],
                    tier="flash" if flash else "pro",
                    book_id=state.get("book_id", ""),
                    chapter=chapter_number,
                )
                retry_result = _parse_write_response(
                    retry_response.content, chapter_number, target_lang
                )
                retry_text = retry_result.get("translated_text", "")
                retry_truncated = _was_truncated(retry_response)
                if retry_truncated:
                    logger.warning(
                        "WRITE ch%d retry%d: TRUNCATED again (finish_reason=%s) "
                        "— discarding partial output", chapter_number,
                        retry_num, retry_truncated,
                    )
                    retry_text = ""
                if retry_text and len(retry_text.strip()) >= RETRY_THRESHOLD:
                    if has_untranslated_chinese(retry_text) and target_lang != "zh-CN":
                        retry_text, removed = strip_chinese_residue(retry_text)
                        logger.warning(
                            "WRITE ch%d retry%d: %d Chinese chars stripped",
                            chapter_number, retry_num, removed,
                        )
                    translated_text = sanitize_translation(retry_text)
                    # Symmetric with the first-attempt guard: a retry that
                    # re-emits the envelope or scaffolding is not a success.
                    # `continue` falls through to the next attempt; if all
                    # retries are exhausted the for-else below logs the skip.
                    retry_residue = (
                        "json_envelope" if has_json_envelope_residue(translated_text)
                        else "meta_text" if has_meta_text_leak(translated_text)
                        else ""
                    )
                    if retry_residue:
                        logger.warning(
                            "WRITE ch%d retry%d: %s residue again — discarding",
                            chapter_number, retry_num, retry_residue,
                        )
                        translated_text = ""
                        continue
                    result = retry_result
                    logger.info("WRITE ch%d: retry %d successful", chapter_number, retry_num)
                    break  # Success — exit retry loop
            except Exception as retry_exc:
                logger.error("WRITE ch%d retry %d failed: %s", chapter_number, retry_num, retry_exc)
        else:
            # All retries exhausted — log and continue (chapter will be empty/short)
            logger.error(
                "WRITE ch%d: ALL RETRIES EXHAUSTED — chapter skipped (0 chars). "
                "Next chapter will continue normally.",
                chapter_number,
            )

    # Scene-level completeness. WRITE may compress and cut, but the prompt
    # requires every source scene to keep an equivalent, and until now nothing
    # checked that: READBACK is blind to the source by design, and the word
    # guard only counted output. Report-only for now — the thresholds were
    # calibrated on 《地府》 and want a wider corpus before they gate a delivery.
    try:
        completeness = check_completeness(
            state.get("chapter_content", ""),
            translated_text,
            state.get("exact_glossary") or {},
        )
        if completeness["flags"]:
            try:
                from ...error_tracker import record_event
                record_event(
                    None, chapter_number, "completeness",
                    f"WRITE completeness flags: {','.join(completeness['flags'])} "
                    f"(ratio={completeness['length_ratio']}, "
                    f"dialogue {completeness['dialogue_src']}->"
                    f"{completeness['dialogue_out']})",
                    target_lang,
                )
            except Exception:
                pass
    except Exception as exc:  # never let an audit break the pipeline
        logger.debug("WRITE ch%d: completeness check failed: %s", chapter_number, exc)

    return {
        "translated_text": translated_text,
        "chapter_title_en": result.get("chapter_title_en", ""),
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
                f"**BUILD WITH:** "
                f"{g.get('sensory_anchors', 'use universal textures, sounds, colors')}"
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


def _capture_response_tokens(response, model: str, tier: str = "flash",
                             *, book_id: str = "", chapter: int = 0) -> None:
    """Record WRITE token usage against the model that actually served it.

    The ledger always gets a row, even when the provider sent no usage payload
    (recorded as unmetered, so the gap is countable). The legacy ``TranslationStats``
    counter can only hold numbers, so it is updated only when the payload was
    complete — half a usage payload is not a figure worth accumulating.
    """
    usage_in, usage_out = token_ledger.extract_usage(response)
    token_ledger.record("write", model, usage_in, usage_out,
                        book_id=book_id, chapter=chapter)
    if usage_in is None or usage_out is None:
        return
    try:
        TranslationStats.record_tokens(
            input_tokens=usage_in,
            output_tokens=usage_out,
            tier=tier,
        )
    except Exception:
        pass


# Providers report "I stopped because I hit the token ceiling" here.
_TRUNCATED_FINISH_REASONS = {"length", "max_tokens", "max_output_tokens",
                             "content_filter"}


def _was_truncated(response) -> str:
    """Return the truncating finish_reason, or "" if the response completed.

    Nothing in this pipeline used to look at ``finish_reason``, so a response
    cut off at ``max_tokens`` was indistinguishable from a complete one: the
    multi-layer parser below would happily hand back the partial text (Layer 5
    returns the raw content), and the chapter shipped short.

    That is the failure the DeepSeek-era adversarial test recorded as "empty
    translations with Chinese character residue" — see src/chapter_slicer.py.
    Slicing reduced the odds but was never a detector, and its budget model was
    calibrated for DeepSeek's context window, not for GLM/Qwen plus the
    reasoning tokens the new stack spends.
    """
    try:
        meta = getattr(response, "response_metadata", None) or {}
        reason = meta.get("finish_reason")
        if reason is None:
            reason = (getattr(response, "additional_kwargs", None) or {}).get(
                "finish_reason"
            )
        reason = str(reason or "").lower()
        return reason if reason in _TRUNCATED_FINISH_REASONS else ""
    except Exception:
        return ""


def _is_content_filter_error(exc: Exception) -> bool:
    """True when the provider's safety layer rejected the request.

    Zhipu returns HTTP 400 with ``code 1301`` and a ``contentFilter`` block:

        {"contentFilter": [{"level": 1, "role": "assistant"}],
         "error": {"code": "1301", "message": "系统检测到输入或生成内容可能包含不安全或敏感内容"}}

    Retrying the same provider does not help — the filter is deterministic on
    the same text — and rewriting the source to appease it would mean shipping
    a censored translation. Measured behaviour: it rejects mainstream published
    novels, not only profanity. Running 《间客》 (Mao Ni, published) through the
    normal pipeline lost 1 of 2 chapters to this.
    """
    text = f"{type(exc).__name__}: {exc}"
    return "1301" in text or "contentFilter" in text or "不安全" in text


def _build_fallback_writer(**provider_kwargs) -> ChatOpenAI:
    """A WRITE client on the routed fallback provider, for filter fallback only.

    Same generation parameters as the primary writer so a fallback chapter is not
    silently written in a different register. Model / base_url / api_key and the
    provider's reasoning control all arrive pre-resolved from
    ``node_llm_kwargs("write_fallback")`` — this function must not pick a
    provider itself, or re-tiering the fallback would not take effect.
    """
    return ChatOpenAI(  # type: ignore[call-arg]
        temperature=0.2,
        max_tokens=16384,
        request_timeout=300,
        max_retries=0,
        model_kwargs={"response_format": {"type": "json_object"}},
        **provider_kwargs,
    )


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
        return json.loads(text)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, ValueError):
        pass

    # Layer 2: Regex-extracted JSON object
    m = re.search(r'\{[\s\S]*"translated_text"[\s\S]*\}', text)
    if m:
        try:
            result = json.loads(m.group())
            record_event(None, chapter_number, "parse_fallback",
                         "Layer 2: regex-extracted JSON object", target_lang)
            return result  # type: ignore[no-any-return]
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

    # ── Envelope-residue guard (before the prose/markdown rescue layers) ──
    # Reaching here means Layer 1-3 all failed: the reply is NOT a parseable
    # envelope. A *well-formed* envelope would have returned at Layer 1 (strict
    # JSON), so any envelope key still sitting in the text now belongs to a
    # malformed or truncated one — exactly the case Layer 4 ("markdown-as-
    # translation") and Layer 5 ("prose rescue") used to swallow whole:
    #   • Ch366  — prose ending in a ```json fence + bare `{`, rescued by Layer 4
    #     because the body started with a capital letter.
    #   • Ch1472 — a compact, truncated `{"translated_text": "..."}` rescued by
    #     Layer 5, whose own log line claimed "no JSON envelope".
    # Both shipped the envelope into the deliverable. Discard instead so the
    # empty-output retry in write_node fires. The judgement is shared with the
    # production guard and scan_deliverable (output_guard is the single source).
    from ...output_guard import json_residue_on_line
    for line in text.split("\n"):
        if json_residue_on_line(line):
            record_event(None, chapter_number, "parse_fallback",
                         "envelope residue in malformed reply — discarded, "
                         "not rescued as prose", target_lang)
            return {
                "translated_text": "",
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

    # Layer 5: the model ignored the JSON envelope and just wrote prose
    # ("I'd be happy to translate this chapter for you! ... Su Nian woke up...").
    # That is worth rescuing — but only when it actually looks like prose.
    #
    # The old behaviour stamped ANY unparseable response with
    # translated_text = raw content, so a truncated or malformed reply became a
    # "translation" that nothing downstream could tell from a real one. The
    # token-ceiling case is now caught earlier by _was_truncated(); this guard
    # covers the remaining ways a response can be junk (symbol soup, a stub,
    # a non-language payload).
    body = content.lstrip("```json").lstrip("```").strip()
    latin_letters = len(re.findall(r"[A-Za-z]", body))
    word_count = len(body.split())
    if latin_letters >= 40 and word_count >= 10:
        record_event(None, chapter_number, "parse_fallback",
                     "Layer 5: prose rescue (no JSON envelope)", target_lang)
        return {
            "translated_text": body,
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "",
        }

    record_event(None, chapter_number, "parse_fallback",
                 f"Layer 5: unparseable response discarded "
                 f"({latin_letters} letters, {word_count} words)",
                 target_lang)
    return {
        "translated_text": "",
        "new_terms_found": [],
        "adaptation_notes": [],
        "chapter_summary": "",
    }
