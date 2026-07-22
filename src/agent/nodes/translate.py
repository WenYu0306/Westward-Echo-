"""Node 2: Translation + Cultural Adaptation (core).

The single most important LLM call in the system. Executes the Two-Pass
Method: literal comprehension (in the model's internal processing) followed
by cultural rewriting (the output).

Uses DeepSeek V4 Flash for bulk chapters, Pro for critical chapters
(first chapter, climax chapters flagged by the user, or chapters being
re-translated after QA failure).
"""

import json
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

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


def _get_llm(chapter_number: int, is_retranslation: bool = False) -> ChatOpenAI:
    """Select the model tier for this chapter.

    - Chapter 1 → Pro (sets the quality baseline for the whole book)
    - Retranslation → Pro (Flash already failed once)
    - Everything else → Flash (bulk, cost-optimized)
    """
    if chapter_number == 1 or is_retranslation:
        model = MODEL_MAP["translate_critical"]
    else:
        model = MODEL_MAP["translate"]

    return ChatOpenAI(
        model=model,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.2,
        max_tokens=8192,
    )


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

    # Load cultural rules for the target language and genre
    target_lang = state.get("target_lang", "en-US")
    genre = state.get("genre", "romance_ceo")
    rules = load_rules(target_lang=target_lang, genre=genre)
    cultural_rules_table = format_rules_for_prompt(rules)

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

    result = _parse_llm_response(response.content)

    return {
        "translated_text": result.get("translated_text", ""),
        "new_terms_found": result.get("new_terms_found", []),
        "adaptation_notes": result.get("cultural_adaptation_notes", []),
        "chapter_summary": result.get("chapter_summary", ""),
    }


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
