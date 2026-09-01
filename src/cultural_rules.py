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


def _load_raw_data(path: Optional[str] = None) -> dict:
    file_path = Path(path) if path else _default_rules_path()
    with open(file_path, encoding="utf-8") as fh:
        return json.load(fh)  # type: ignore[no-any-return]


def list_known_genres(path: Optional[str] = None) -> list[str]:
    """Return all genre keys defined in cultural_rules.json."""
    data = _load_raw_data(path)
    return list(data.get("genres", {}).keys())


def is_known_genre(genre: str, path: Optional[str] = None) -> bool:
    """Check whether a genre has dedicated rules in cultural_rules.json."""
    return genre in list_known_genres(path)


# ── Genre auto-detection ──────────────────────────────────────

_GENRE_SIGNALS: dict[str, list[str]] = {
    "scifi": [
        "机甲", "联邦", "帝国", "战舰", "星舰", "太空港", "跃迁", "基因改造",
        "殖民星", "能量罩", "粒子炮", "首都星", "太空站", "星际", "宇宙",
        "机甲师", "联邦军", "帝国军", "外骨骼", "拟态", "虫洞", "曲速", "量子",
    ],
    "folk_religion": [
        "出马", "仙家", "弟马", "堂口", "请神", "上身", "香主", "老仙",
        "胡仙", "黄仙", "柳仙", "灰仙", "白仙", "常仙", "蟒仙",
        "地府", "阎王", "判官", "鬼差", "城隍", "孟婆", "奈何桥",
        "打表", "顶香", "过阴", "走阴", "拘魂", "送祟", "叫魂",
        "扎纸", "纸人", "冥器", "寿衣", "棺材", "抬棺",
    ],
    "xianxia": [
        "修真", "修仙", "金丹", "元婴", "飞升", "渡劫", "法器", "灵石",
        "宗门", "长老", "弟子", "功法", "炼丹", "御剑", "灵兽", "天劫",
        "道侣", "灵脉", "洞府", "阵法", "仙府", "仙尊", "魔尊",
    ],
    "romance_ceo": [
        "霸总", "总裁", "隐婚", "宠妻", "豪门", "白月光", "虐恋",
        "带球跑", "替身", "联姻", "契约结婚", "先婚后爱",
    ],
}

# Minimum total signal count across all genres to consider it a match
_MIN_GENRE_SIGNALS = 5
# Minimum unique terms (any genre) before we report a best guess
_MIN_UNIQUE_TERMS = 3


def detect_genre(text: str) -> tuple[str, int]:
    """Auto-detect the most likely novel genre from keyword frequency.

    Parameters
    ----------
    text : str
        Novel text sample (first few chapters, ~10K-20K chars).

    Returns
    -------
    tuple[str, int]
        ``(genre_key, confidence_score)``.  Returns ``("", 0)`` when no
        genre has enough signal to be confident.  Confidence is the ratio
        of the winner's signal count to the runner-up's.
    """
    scored = {}
    for genre_name, keywords in _GENRE_SIGNALS.items():
        score = sum(text.count(kw) for kw in keywords)
        if score >= _MIN_GENRE_SIGNALS:
            scored[genre_name] = score

    if not scored:
        return ("", 0)

    # Sort by score descending
    ranked = sorted(scored.items(), key=lambda x: x[1], reverse=True)
    winner_name, winner_score = ranked[0]
    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0

    # Confidence: how much more signal the winner has than runner-up
    if runner_up_score > 0 and winner_score / runner_up_score < 2.0:
        # Signals are ambiguous — don't auto-detect
        return ("", 0)

    return (winner_name, winner_score)


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
    data = _load_raw_data(path)

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


def load_fidelity_rules(target_lang: str = "en-US",
                        path: Optional[str] = None) -> dict:
    """Load strategy-level cultural-fidelity rules for a target language.

    Unlike :func:`load_rules` (term-level mappings), these are category-level
    rules — how to translate a CLASS of terms (names, honorifics, worldview
    terms), not what a specific word means.

    Parameters
    ----------
    target_lang : str
        Language-region code, e.g. ``"en-US"``.
    path : str or None
        Override the JSON file path.

    Returns
    -------
    dict
        ``{category: {rule: str, examples: [{cn, do, why}]}}``, or ``{}`` if
        no fidelity rules are defined for the language.
    """
    data = _load_raw_data(path)
    return data.get("fidelity", {}).get(target_lang, {})


def format_fidelity_for_prompt(rules: dict) -> str:
    """Format fidelity rules as a compact prompt block for the READ agent.

    Parameters
    ----------
    rules : dict
        ``{category: {rule, examples}}`` as returned by
        :func:`load_fidelity_rules`.

    Returns
    -------
    str
        A markdown block with one section per category: the rule plus one
        worked example. Returns ``""`` when *rules* is empty.
    """
    if not rules:
        return ""

    sections = []
    for category, entry in rules.items():
        rule = entry.get("rule", "")
        if not rule:
            continue
        title = category.replace("_", " ").title()
        lines = [f"### {title}", rule]
        for ex in entry.get("examples", [])[:1]:
            cn = ex.get("cn", "")
            do = ex.get("do", "")
            why = ex.get("why", "")
            line = f"  e.g. {cn} → {do}"
            if why:
                line += f"  ({why})"
            lines.append(line)
        sections.append("\n".join(lines))

    return "\n\n".join(sections)
