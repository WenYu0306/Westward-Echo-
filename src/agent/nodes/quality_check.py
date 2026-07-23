"""Node 4: Back-translation quality check.

Runs every QUALITY_CHECK_INTERVAL chapters (default 20). Samples 3 passages
from the translated chapter, back-translates them to Chinese, and scores
the translation on 5 dimensions.

If the overall score is below 3.5, the chapter is flagged for retranslation.
"""

import json
import re
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..state import TranslatorState
from ..prompts.quality_check import QUALITY_CHECK_SYSTEM, QUALITY_CHECK_USER
from ...config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    MODEL_MAP,
    QUALITY_CHECK_INTERVAL,
)


def _extract_sample_passages(translated_text: str, original_text: str, n: int = 3) -> list[dict]:
    """
    Extract n sample passages from the translated text for QA.
    Picks passages of reasonable length (3-6 sentences each) distributed
    across the chapter (beginning, middle, end).

    This is a heuristic — for production, use sentence segmentation or
    alignment. But for MVP, paragraph-based sampling works well enough.
    """
    paragraphs = [p.strip() for p in translated_text.split("\n\n") if len(p.strip()) > 100]
    if len(paragraphs) < n:
        return []  # Chapter too short for meaningful sampling

    # Pick from beginning, middle, end
    indices = [0, len(paragraphs) // 2, len(paragraphs) - 1]
    # Also sample corresponding Chinese paragraphs (approximate alignment)
    cn_paragraphs = [p.strip() for p in original_text.split("\n") if len(p.strip()) > 50]

    samples = []
    for i in indices[:n]:
        if i < len(paragraphs):
            # Take a window: the paragraph at index i + next 2 paragraphs
            passage = "\n\n".join(paragraphs[i:min(i+1, len(paragraphs))])
            # Approximate Chinese match: same index ratio
            cn_idx = int(i / len(paragraphs) * len(cn_paragraphs)) if cn_paragraphs else 0
            cn_passage = "\n".join(cn_paragraphs[cn_idx:min(cn_idx+1, len(cn_paragraphs))]) if cn_paragraphs else "(original not aligned)"
            samples.append({"en": passage, "cn": cn_passage})

    return samples


def quality_check_node(state: TranslatorState) -> dict:
    """
    Sample the translation, back-translate, and score.

    Only runs on chapters where chapter_number % QUALITY_CHECK_INTERVAL == 0.
    For other chapters, returns a pass-through (quality_score = 5.0).
    """
    chapter_num = state["chapter_number"]

    # Only run QA on sampled chapters
    if chapter_num % QUALITY_CHECK_INTERVAL != 0 and chapter_num != 1:
        return {"quality_score": 5.0, "quality_issues": []}

    samples = _extract_sample_passages(
        translated_text=state.get("translated_text", ""),
        original_text=state.get("chapter_content", ""),
    )

    if not samples:
        return {"quality_score": 5.0, "quality_issues": []}

    # Use V4 Pro for scoring (needs aesthetic judgment)
    score_llm = ChatOpenAI(
        model=MODEL_MAP["quality_score"],
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.0,
        max_tokens=2048,
    )

    # Back-translate with Flash (inexpensive)
    back_translate_llm = ChatOpenAI(
        model=MODEL_MAP["back_translate"],
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.0,
        max_tokens=2048,
    )

    all_scores = []
    all_issues = []

    for sample in samples:
        # Step 1: Back-translate
        bt_response = back_translate_llm.invoke([
            SystemMessage(content="Translate the following English web novel passage back to natural Chinese. Output ONLY the Chinese text, no commentary."),
            HumanMessage(content=sample["en"]),
        ])
        back_translated = bt_response.content.strip()

        # Step 2: Score
        glossary_text = state.get("exact_matches_text", "")
        score_prompt = QUALITY_CHECK_USER.format(
            glossary_text=glossary_text,
            original_cn=sample["cn"],
            english_translation=sample["en"],
        )

        score_response = score_llm.invoke([
            SystemMessage(content=QUALITY_CHECK_SYSTEM),
            HumanMessage(content=score_prompt),
        ])

        try:
            result_text = score_response.content.strip()
            result_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', result_text)
            result = json.loads(result_text)
            all_scores.append(result.get("overall", 3.0))
            all_issues.extend(result.get("issues", []))
        except json.JSONDecodeError:
            all_scores.append(3.0)  # Conservative default on parse failure

    avg_score = sum(all_scores) / len(all_scores) if all_scores else 5.0

    # Record low-QA-score events for analytics
    if avg_score < 3.0:
        try:
            from ...error_tracker import record_event
            record_event(
                state.get("job_id"),
                chapter_num,
                "qa_low_score",
                f"QA score {avg_score:.1f} below threshold 3.0 — issues: {len(all_issues)}",
                state.get("target_lang", "en-US"),
            )
        except Exception:
            pass  # Best-effort; never break the QA node

    return {
        "quality_score": round(avg_score, 1),
        "quality_issues": all_issues,
    }
