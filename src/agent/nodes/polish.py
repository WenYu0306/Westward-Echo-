"""Node 2B: Polish Agent — targeted translation editor.

Activates when QA scores a chapter below 3.5. Unlike the translate node
which re-translates from scratch (same prompt, hoping for a different result),
the polish node:
- Receives the original Chinese text, the current flawed translation, and the
  specific QA issues found
- Acts as an editor — fixes what's broken, preserves what works
- Uses V4 Pro (precision editing needs reasoning depth)
- Returns a polished translation that then goes back through QA
"""

import json
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..state import TranslatorState
from ..prompts.polish import POLISH_SYSTEM, POLISH_USER
from ...config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL_MAP


def polish_node(state: TranslatorState) -> dict:
    """Edit a flawed translation using targeted QA feedback.

    This is the Multi-Agent upgrade: instead of blindly retrying the translate
    node, we use a different agent with a different prompt and a different
    mindset (editor, not translator) to fix specific problems.
    """
    llm = ChatOpenAI(
        model=MODEL_MAP["translate_critical"],  # Always Pro — editing needs precision
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.1,  # Very focused — not creative, just fix what's broken
        max_tokens=8192,
    )

    # Format QA issues for the polish prompt
    qa_issues = state.get("quality_issues", [])
    if qa_issues:
        issues_text = "\n".join(
            f"- [{i.get('severity', 'minor').upper()}] {i.get('detail', str(i))}"
            for i in qa_issues
        )
    else:
        issues_text = "- Quality score below threshold (no specific issues recorded)"

    user_prompt = POLISH_USER.format(
        glossary_text=state.get("exact_matches_text", "(No glossary)"),
        original_cn=state["chapter_content"],
        current_en=state.get("translated_text", ""),
        qa_issues=issues_text,
    )

    response = llm.invoke([
        SystemMessage(content=POLISH_SYSTEM),
        HumanMessage(content=user_prompt),
    ])

    result = _parse_polish_response(response.content)

    return {
        "translated_text": result.get("polished_text", state.get("translated_text", "")),
        "adaptation_notes": result.get("changes_made", []),
        "quality_issues": [],   # Reset so next QA run starts fresh
    }


def _parse_polish_response(content: str) -> dict:
    """Parse the polish node's JSON output."""
    import re
    text = content.strip()

    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        text = text.strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: return the content as polished_text
    return {
        "polished_text": content,
        "changes_made": ["(Parser fallback — raw response returned)"],
    }
