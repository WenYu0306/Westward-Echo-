"""Node 2: Translation + Cultural Adaptation (core).

The single most important LLM call in the system. Executes the Two-Pass
Method: literal comprehension (in the model's internal processing) followed
by cultural rewriting (the output).

Uses DeepSeek V4 Flash for bulk chapters, Pro for critical chapters
(first chapter, climax chapters flagged by the user, or chapters being
re-translated after QA failure).
"""

import json
import logging
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from ..state import TranslatorState
from ..prompts.translation import TRANSLATION_SYSTEM, TRANSLATION_USER
from ...config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    MODEL_MAP,
)
from ...cultural_rules import load_rules, format_rules_for_prompt
from ...job_store import job_store
from ...circuit_breaker import get_breaker, CircuitBreakerOpenError
from ...stats import TranslationStats
from ...tools import ALL_TOOLS, handle_glossary_lookup

logger = logging.getLogger(__name__)


def _get_llm(
    chapter_number: int,
    is_retranslation: bool = False,
    bind_tools: bool = True,
) -> ChatOpenAI:
    """Select the model tier for this chapter.

    - Chapter 1 → Pro (sets the quality baseline for the whole book)
    - Retranslation → Pro (Flash already failed once)
    - Everything else → Flash (bulk, cost-optimized)
    """
    if chapter_number == 1 or is_retranslation:
        model = MODEL_MAP["translate_critical"]
    else:
        model = MODEL_MAP["translate"]

    llm = ChatOpenAI(
        model=model,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.2,
        max_tokens=8192,
    )

    # Optionally bind glossary-lookup tool for supported models.
    # DeepSeek and most OpenAI-compatible endpoints support function calling.
    # If .bind_tools() fails, the LLM falls back to prompt-injected glossary.
    if bind_tools:
        try:
            llm = llm.bind_tools(ALL_TOOLS)
        except (AttributeError, NotImplementedError) as exc:
            logger.debug("bind_tools not supported by this model: %s", exc)

    return llm


def translate_node(state: TranslatorState) -> dict:
    """
    Translate a single chapter with cultural adaptation.

    The LLM is given:
    - The previous chapter summary (for narrative continuity)
    - Exact glossary matches (mandatory translations)
    - Semantic glossary matches (advisory cultural context)
    - The source chapter text

    It outputs:
    - translated_text: The English chapter
    - new_terms_found: Terms to add to the glossary
    - adaptation_notes: Cultural adaptation decisions
    - chapter_summary: Summary for the next chapter's context
    """
    llm = _get_llm(
        chapter_number=state["chapter_number"],
        is_retranslation=state.get("retranslation_count", 0) > 0,
    )

    # Load cultural rules for the target language and genre.
    # If the genre is unknown (no dedicated rules), switch to discovery mode.
    target_lang = state.get("target_lang", "en-US")
    genre = state.get("genre", "romance_ceo")

    from ...cultural_rules import is_known_genre, detect_genre
    discovery_mode = not is_known_genre(genre)

    if discovery_mode:
        # Auto-detect: scan the chapter for genre signals
        detected, confidence = detect_genre(state["chapter_content"])
        if detected and confidence > 0:
            genre = detected

    rules = load_rules(target_lang=target_lang, genre=genre)
    cultural_rules_table = format_rules_for_prompt(rules)

    if discovery_mode:
        # Build a discovery-mode context telling the LLM this is uncharted territory
        existing_terms = list(state.get("exact_glossary", {}).keys())
        term_hint = ""
        if existing_terms:
            sample = existing_terms[:20]
            term_hint = (
                f"So far this novel has introduced these terms: {', '.join(sample)}. "
                "Use them consistently. Extract and record ALL new proper nouns, "
                "genre-specific terms, and recurring concepts as new_terms_found. "
                "This novel genre has no predefined cultural rules — you are the "
                "first pass. Establish consistent translations now."
            )

        discovery_note = (
            "## DISCOVERY MODE\n"
            "This novel's genre has no predefined cultural adaptation rules. "
            "You must establish consistent translations for all proper nouns, "
            "genre-specific terminology, and recurring concepts ON YOUR OWN. "
            "Your choices WILL become the canonical translations for the entire book.\n"
            f"{term_hint}\n"
        )

        # Build few-shot examples from what the LLM itself established in earlier chapters
        discovered_terms = state.get("exact_glossary", {})
        if discovered_terms:
            few_shot_lines = [
                "\n## SELF-DISCOVERED RULES (established in earlier chapters)\n",
                "| 中文 | English (LOCKED — use exactly) |",
                "|------|------|",
            ]
            for cn, en in list(discovered_terms.items())[:30]:
                few_shot_lines.append(f"| {cn} | {en} |")
            discovery_note += "\n".join(few_shot_lines) + "\n"

        cultural_rules_table = discovery_note + "\n" + cultural_rules_table

    # Detect dialect markers and build dialect context
    from ...dialect import build_dialect_context, has_system_text
    dialect_context = build_dialect_context(state["chapter_content"])

    # Detect LitRPG system notification markers
    litrpg_context = ""
    if has_system_text(state["chapter_content"]):
        litrpg_context = (
            "## LITRPG CONTEXT\n"
            "This chapter contains game-like system notifications or status "
            "windows. See Section 7 (Special: System / Game UI Text) in your "
            "system instructions for LitRPG formatting conventions.\n\n"
        )

    # Detect Chinese measurements and build localization hints
    from ...measurements import build_measurements_hint
    measurements_hint = build_measurements_hint(state["chapter_content"])

    # Detect onomatopoeia and build translation hints
    from ...onomatopoeia import build_onomatopoeia_context
    onoma_hint = build_onomatopoeia_context(state["chapter_content"])

    # Detect Chinese idioms (成语) and build translation hints
    from ...idioms import build_idiom_context
    idiom_hint = build_idiom_context(state["chapter_content"])

    system_prompt = TRANSLATION_SYSTEM.format(cultural_rules_table=cultural_rules_table)

    user_prompt = TRANSLATION_USER.format(
        previous_summary=state.get("previous_chapter_summary", "(This is the first chapter — no previous summary.)"),
        exact_matches=state.get("exact_matches_text", "(No glossary terms matched.)"),
        semantic_matches=state.get("semantic_matches_text", "(No semantic matches.)"),
        dialect_context=dialect_context,
        litrpg_context=litrpg_context,
        chapter_number=state["chapter_number"],
        chapter_title=state["chapter_title"],
        chapter_content=state["chapter_content"],
    )

    # ── Inject measurement localization hints ──────
    if measurements_hint:
        user_prompt = measurements_hint + "\n\n" + user_prompt

    # ── Inject onomatopoeia context hints ──────
    if onoma_hint:
        user_prompt = onoma_hint + "\n\n" + user_prompt

    # ── Inject idiom context hints ──────
    if idiom_hint:
        user_prompt = idiom_hint + "\n\n" + user_prompt

    # ── Inject sensitive term warnings ──────
    from ...sensitive_terms import build_sensitive_term_context
    sensitive_ctx = build_sensitive_term_context(state["chapter_content"])
    if sensitive_ctx:
        user_prompt = sensitive_ctx + "\n\n" + user_prompt

    # ── Inject confirmed terms (locked by human reviewer) ──────
    confirmed = job_store.get_confirmed_terms(target_lang)
    if confirmed:
        confirmed_text = "\n".join(
            f"- '{term_cn}' MUST be translated as '{term_en}'. "
            f"Human-confirmed — do NOT change."
            for term_cn, term_en in confirmed.items()
        )
        user_prompt += f"\n\n## CONFIRMED TRANSLATIONS (LOCKED)\n{confirmed_text}"

    # ── Inject rejected terms (blocked by human reviewer) ──────
    rejected = job_store.get_rejected_terms(target_lang)
    if rejected:
        rejected_text = "\n".join(
            f"- DO NOT use '{r['rejected_en']}' for '{r['term_cn']}'. "
            f"It was rejected by a human reviewer."
            for r in rejected
        )
        user_prompt += f"\n\n## REJECTED TRANSLATIONS (DO NOT USE)\n{rejected_text}"

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    # ── Circuit breaker per language ──
    target_lang = state.get("target_lang", "en-US")
    breaker = get_breaker(target_lang)

    try:
        TranslationStats.record_api_call(target_lang)
        response = breaker.call(llm.invoke, messages)
        TranslationStats.record_api_success(target_lang)
    except CircuitBreakerOpenError:
        TranslationStats.record_api_failure(target_lang)
        raise  # Propagate to orchestration loop for graceful skip
    except Exception:
        TranslationStats.record_api_failure(target_lang)
        raise

    # ── Capture token usage from primary LLM call ──────────
    _capture_response_tokens(response)

    # ── Tool Use support (MCP-style glossary lookup) ──────────
    # If the LLM requests a glossary lookup, execute it and re-invoke the LLM
    # with the tool result.  Max 3 round-trips to prevent infinite loops.
    MAX_TOOL_ROUNDS = 3
    tool_round = 0

    while (hasattr(response, 'tool_calls') and response.tool_calls
           and tool_round < MAX_TOOL_ROUNDS):
        tool_round += 1
        logger.debug("LLM tool call round %d: %d calls", tool_round, len(response.tool_calls))

        # Append the assistant message (containing tool_calls) to the conversation
        messages.append(response)

        for tool_call in response.tool_calls:
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", {})
            tool_id = tool_call.get("id", "")

            if tool_name == "lookup_glossary":
                term_cn = tool_args.get("term_cn", "")
                # Use the state's exact_glossary dict for lookups within the node
                exact_glossary = state.get("exact_glossary", {})
                result = exact_glossary.get(term_cn, "NOT_FOUND")
                TranslationStats.record_tool_call()
                logger.debug("lookup_glossary('%s') → %s", term_cn, result)
            else:
                result = f"Unknown tool: {tool_name}"

            messages.append(ToolMessage(content=result, tool_call_id=tool_id))

        # Re-invoke the LLM with the tool results
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

    result = _parse_llm_response(response.content)

    translated_text = result.get("translated_text", "")

    # ── Output quality guard ──────────────────────────────────
    from ...output_guard import check_translation_output, sanitize_translation, MIN_TRANSLATION_CHARS

    warnings = check_translation_output(translated_text)
    if warnings:
        for w in warnings:
            logger.warning("Output guard: ch%d %s", state["chapter_number"], w)
        # Try sanitizing chatter patterns from the translation
        has_short = any(w.startswith("EMPTY:") for w in warnings)
        translated_text = sanitize_translation(translated_text)
        if has_short and (not translated_text or len(translated_text) < MIN_TRANSLATION_CHARS):
            # The translated text is genuinely too short after stripping chatter.
            # Try the LLM's raw response as a last resort — parse it through the
            # same parser in case it contains the translation inside JSON.
            raw_parsed = _parse_llm_response(response.content)
            raw_fallback = raw_parsed.get("translated_text", "")
            if raw_fallback and len(raw_fallback) >= MIN_TRANSLATION_CHARS:
                translated_text = sanitize_translation(raw_fallback)

    return {
        "translated_text": translated_text,
        "new_terms_found": result.get("new_terms_found", []),
        "adaptation_notes": result.get("cultural_adaptation_notes", []),
        "chapter_summary": result.get("chapter_summary", ""),
    }




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
        pass  # Token tracking is best-effort; never break translation for it


def _parse_llm_response(content: str) -> dict:
    """Parse the LLM's JSON output, with multi-layer fallback.

    Tries: strict JSON → regex extraction → field-by-field extraction → raw text.
    """
    import re
    text = content.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        text = text.strip()

    # Layer 1: Strict JSON
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Layer 2: Find JSON object with regex (handles embedded unescaped chars)
    m = re.search(r'\{[^{}]*"translated_text"[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group())
        except (json.JSONDecodeError, ValueError):
            pass

    # Layer 3: Extract translated_text field directly via regex
    m = re.search(r'"translated_text"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        tt = m.group(1).replace('\\"', '"').replace('\\n', '\n')
        return {
            "translated_text": tt,
            "new_terms_found": [],
            "cultural_adaptation_notes": [],
            "chapter_summary": "",
        }

    # Layer 4: If the response starts with markdown (likely already a translation), return as-is
    if re.match(r'^(#+\s|>|\*\*|[A-Z][a-z])', text):
        return {
            "translated_text": text,
            "new_terms_found": [],
            "cultural_adaptation_notes": [],
            "chapter_summary": "",
        }

    # Layer 5: Last resort — return the raw content
    return {
        "translated_text": content.lstrip("```json").lstrip("```").strip(),
        "new_terms_found": [],
        "cultural_adaptation_notes": [],
        "chapter_summary": "",
    }
