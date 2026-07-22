"""Load and format cultural adaptation rules from a JSON configuration file.

Rules are stored in ``cultural_rules.json`` at the project root by default.
Override the path via the ``CULTURAL_RULES_PATH`` environment variable.

Structure::

    {
      "genres": {
        "<genre>": {
          "<lang>": {
            "<source_term>": {"target": "...", "note": "..."}
          }
        }
      },
      "common": {
        "<lang>": {
          "<source_term>": {"target": "...", "note": "..."}
        }
      }
    }

"common" rules apply to ALL genres.  Genre-specific rules override common
entries when the same source term appears in both.
"""

import json
import os
from pathlib import Path
from typing import Optional


def _default_rules_path() -> Path:
    env = os.getenv("CULTURAL_RULES_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "cultural_rules.json"


def load_rules(target_lang: str = "en-US", genre: str = "romance_ceo",
               path: Optional[str] = None) -> dict:
    """Load cultural adaptation rules for a specific language and genre.

    Parameters
    ----------
    target_lang : str
        Language-region code, e.g. ``"en-US"``, ``"es-ES"``.
    genre : str
        Genre key, e.g. ``"romance_ceo"``, ``"xianxia"``, ``"urban"``.
    path : str or None
        Override the JSON file path.  Defaults to ``$CULTURAL_RULES_PATH``
        or ``<project_root>/cultural_rules.json``.

    Returns
    -------
    dict
        Merged mapping of ``{source_term: {target: str, note: str}}``.
        Common rules come first; genre-specific rules override matching keys.
    """
    file_path = Path(path) if path else _default_rules_path()

    with open(file_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    # Start with common (applies to all genres)
    merged: dict = {}
    common = data.get("common", {}).get(target_lang, {})
    merged.update(common)

    # Overlay genre-specific
    genre_rules = data.get("genres", {}).get(genre, {}).get(target_lang, {})
    merged.update(genre_rules)

    return merged


def format_rules_for_prompt(rules: dict) -> str:
    """Format cultural rules as a Markdown table for LLM prompt injection.

    Parameters
    ----------
    rules : dict
        ``{source_term: {target, note}}`` as returned by :func:`load_rules`.

    Returns
    -------
    str
        A Markdown table string suitable for splicing into a system or user
        prompt.  The table has columns: 中文 | Adapted | Note.
        Returns an empty string when *rules* is empty.
    """
    if not rules:
        return ""

    lines = ["| 中文 | Adapted (USE THIS) | Note |",
             "|------|--------------------|------|"]

    for cn_term, entry in rules.items():
        target = entry.get("target", entry) if isinstance(entry, dict) else entry
        note = entry.get("note", "") if isinstance(entry, dict) else ""
        lines.append(f"| {cn_term} | {target} | {note} |")

    return "\n".join(lines)


def format_rules_as_bullets(rules: dict) -> str:
    """Format cultural rules as bullet points for term extraction prompts.

    Parameters
    ----------
    rules : dict
        ``{source_term: {target, note}}`` as returned by :func:`load_rules`.

    Returns
    -------
    str
        Bulleted lines, one per rule, e.g. ``- 霸总 > "Alpha CEO" (note)``.
        Returns an empty string when *rules* is empty.
    """
    if not rules:
        return ""

    bullets = []
    for cn_term, entry in rules.items():
        target = entry.get("target", entry) if isinstance(entry, dict) else entry
        note = entry.get("note", "") if isinstance(entry, dict) else ""
        if note:
            bullets.append(f'- {cn_term} → "{target}" ({note})')
        else:
            bullets.append(f'- {cn_term} → "{target}"')
    return "\n".join(bullets)
