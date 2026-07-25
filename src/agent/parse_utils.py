"""Shared JSON parse utility for agent node responses.

All four nodes (read, write, readback, fix) receive LLM outputs that
are expected to be JSON but may arrive as markdown-wrapped, code-fenced,
or malformed.  This module provides a single parse function with a
multi-layer fallback so every node handles failures consistently.

The caller supplies a *fallback* dict — what to return when every parse
layer fails.  For READBACK, that fallback should be NEEDS_FIX, not PASS
(the dangerous optimistic default in the old per-node implementations).
"""

import json
import re
import logging

logger = logging.getLogger(__name__)


def parse_llm_json(content: str, fallback: dict) -> tuple[dict, bool]:
    """Parse an LLM's JSON response with multi-layer fallback.

    Layers (tried in order):
      1. Strip markdown code fences, then ``json.loads``.
      2. Regex-extract the first ``{...}`` object, then ``json.loads``.
      3. Return *fallback* — parsing failed.

    Args:
        content: Raw LLM response text.
        fallback: Dict to return when all parse layers fail.

    Returns:
        ``(parsed_dict, is_fallback)``.  ``is_fallback`` is ``True`` when
        every parse layer failed and *fallback* was returned.
    """
    text = content.strip() if content else ""

    # Layer 0: strip code fences
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1])
        else:
            text = "\n".join(lines[1:])
        text = text.strip()

    # Layer 1: strict JSON
    try:
        return json.loads(text), False
    except (json.JSONDecodeError, ValueError):
        pass

    # Layer 2: regex-extract the widest JSON object
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group()), True
        except (json.JSONDecodeError, ValueError):
            pass

    # Layer 3: give up — caller's fallback
    logger.debug("parse_llm_json: all layers failed, returning fallback")
    return fallback, True
