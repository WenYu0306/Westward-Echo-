"""Node: REVIEW_TERMS — cross-check READ's terminology decisions with Qwen.

READ (DeepSeek) decides how to render names and culture terms, but its
decision is stochastic — the same term can come out as a faithful adaptation
one run and a literal/pinyin rendering the next (e.g. 南茅北马 → "Southern
Mao, Northern Ma"). This node runs AFTER READ and BEFORE WRITE, using Qwen as
an independent second opinion: for character/culture terms only, it flags
literal/pinyin renderings and replaces them with a faithful adaptation.

Fail-safe: any LLM error or parse failure leaves the decisions untouched and
returns an empty update — the pipeline continues, just without the correction.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ...config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from ..parse_utils import parse_llm_json
from ..state import TranslatorState

logger = logging.getLogger(__name__)

# Only these categories are worth reviewing. location/technique/item/era are
# legitimately transliterated or literal — no cultural meaning to lose.
_REVIEW_CATEGORIES = frozenset({"character", "culture"})

_REVIEW_SYSTEM = """\
You are a cultural-compilation quality reviewer. Given a Chinese term and its \
proposed English rendering, decide whether the rendering is a faithful \
adaptation (conveys the cultural meaning) or a lazy literal/pinyin rendering \
(an English reader would not understand what it actually means).

Rules:
- If the rendering conveys the cultural meaning → verdict "pass".
- If it is a literal translation or bare pinyin → verdict "fail" and give a \
  "corrected" faithful adaptation.
- Only fix literal/pinyin renderings. Do NOT touch a rendering that is already \
  a faithful adaptation.
"""

_REVIEW_USER = """\
Review these terms and their proposed renderings:

{terms_list}

Return STRICT JSON only (no preamble, no markdown fences):
{{"reviews": [{{"term_cn": "...", "verdict": "pass", "corrected": ""}}]}}
"""


def _clean_rendering(proposed_en: str) -> str:
    """Take only the rendering part, dropping any trailing explanation.

    READ sometimes crams "rendering — explanation" into proposed_en. The
    reviewer must judge the rendering alone, or a long trailing explanation
    will make it look faithful and the literal rendering slips through.
    """
    return proposed_en.split("—")[0].strip()


def _format_terms(decisions: list[dict]) -> str:
    lines = []
    for d in decisions:
        cn = d.get("term_cn", "")
        en = _clean_rendering(d.get("proposed_en", ""))
        note = d.get("cultural_note", "") or d.get("reasoning", "")
        line = f'- {cn} → "{en}"'
        if note:
            line += f"  ({note[:120]})"
        lines.append(line)
    return "\n".join(lines)


def _parse_review(content: str) -> dict[str, str]:
    """Parse the reviewer's JSON into {term_cn: corrected} for fail terms only."""
    fallback = {"reviews": []}
    result, _ = parse_llm_json(content, fallback)
    corrected = {}
    for r in result.get("reviews", []):
        cn = r.get("term_cn", "")
        verdict = r.get("verdict", "pass")
        fix = r.get("corrected", "")
        if cn and verdict == "fail" and fix:
            corrected[cn] = fix
    return corrected


def review_terms_node(state: TranslatorState) -> dict:
    """Review READ's character/culture term decisions and fix literal ones."""
    read_analysis = state.get("read_analysis", {})
    decisions = read_analysis.get("terminology_decisions", [])
    if not decisions:
        return {}

    to_review = [d for d in decisions if d.get("category", "") in _REVIEW_CATEGORIES]
    if not to_review:
        return {}

    # No key → no review (dev/test mode). Fail-safe, like the invoke() guard.
    if not LLM_API_KEY:
        logger.warning("REVIEW_TERMS: no LLM key configured, skipping review")
        return {}

    llm = ChatOpenAI(  # type: ignore[call-arg]
        model=LLM_MODEL,
        api_key=LLM_API_KEY,  # type: ignore[arg-type]
        base_url=LLM_BASE_URL,
        temperature=0.1,
        max_tokens=2048,
        request_timeout=120,
        max_retries=0,
        model_kwargs={"response_format": {"type": "json_object"}},
    )

    user_prompt = _REVIEW_USER.format(terms_list=_format_terms(to_review))
    messages = [
        SystemMessage(content=_REVIEW_SYSTEM),
        HumanMessage(content=user_prompt),
    ]

    try:
        response = llm.invoke(messages)
    except Exception as exc:  # fail-safe: leave decisions untouched
        logger.warning("REVIEW_TERMS: LLM call failed, skipping review: %s", exc)
        return {}

    corrected = _parse_review(response.content)
    if not corrected:
        return {}

    fixed = 0
    for d in decisions:
        cn = d.get("term_cn", "")
        if cn in corrected:
            old = d.get("proposed_en", "")
            d["proposed_en"] = corrected[cn]
            logger.info("REVIEW_TERMS: fixed '%s' → '%s' (was '%s')", cn, corrected[cn], old)
            fixed += 1

    if fixed:
        return {"read_analysis": read_analysis}
    return {}
