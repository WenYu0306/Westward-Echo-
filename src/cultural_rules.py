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
    # urban was present in cultural_rules.json and is the configured genre for
    # three of the five corpus books (run_novel.py: 无限恐怖, 全职高手番外,
    # 愿你灿烂如阳), but it was missing from _GENRE_SIGNALS — so detect_genre
    # could NEVER return it. Added in clause 11; a test asserts the three genre
    # vocabularies (rules.json / _GENRE_SIGNALS / personas.GENRE_PROFILES) stay
    # in sync. Keywords are urban-fantasy markers (兵王/赘婿/异能子类), chosen
    # to not collide with the xianxia cultivation vocabulary or any existing
    # detect_genre test fixture.
    "urban": [
        "都市", "异能", "觉醒", "隐世", "古武", "内劲", "世家", "风水",
        "兵王", "赘婿", "特种兵", "保镖", "集团", "透视", "鉴宝", "扮猪吃虎",
    ],
}

# Minimum total signal count across all genres to consider it a match
_MIN_GENRE_SIGNALS = 5
# NOTE: a sibling constant `_MIN_UNIQUE_TERMS = 3` used to sit here, documented as
# "minimum unique terms before we report a best guess". Nothing ever read it —
# detect_genre only compares `score >= _MIN_GENRE_SIGNALS` — so the promise in its
# comment was never implemented and mutating 3→4 was provably unkillable. Removed
# rather than registered as equivalent: an unreachable knob is not a behaviour to
# preserve, and keeping it invites the next reader to believe the guard exists.


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
               path: Optional[str] = None, book_id: str = "") -> dict:
    """Load cultural adaptation rules for a specific language, genre and book.

    Parameters
    ----------
    target_lang : str
        Language-region code, e.g. ``"en-US"``, ``"es-ES"``.
    genre : str
        Genre key, e.g. ``"romance_ceo"``, ``"xianxia"``, ``"urban"``.
    path : str or None
        Override the JSON file path.  Defaults to ``$CULTURAL_RULES_PATH``
        or ``<project_root>/cultural_rules.json``.
    book_id : str
        Optional per-book key.  When given and ``books.<book_id>.<lang>``
        exists, those terms are overlaid last and win over genre terms.
        Omitting it reproduces the previous two-layer behaviour exactly.

    Returns
    -------
    dict
        Merged mapping of ``{source_term: {target: str, note: str}}``.
        Precedence: common < genre-specific < book-specific.

    Why the book layer exists
    -------------------------
    ``genres.urban`` once held 150 terms that were machine-extracted from a
    single book (无限恐怖, an infinite-flow survival story), including
    established Western IP names it crosses over into (生化危机 → Biohazard,
    异形 → Xenomorph, Licker, Red Queen, Desert Eagle). Three corpus books are
    configured ``urban`` — the other two are an esports story and a romance —
    so both were being briefed with Resident Evil terminology, and any
    translation added to that slot would have propagated the mismatch into
    three languages at once. Those terms now live under
    ``books.limitless_horror`` and reach only the book they came from.

    The ``urban`` genre key is deliberately kept (now empty rather than
    removed): ``list_known_genres``/``is_known_genre`` enumerate ``genres``,
    so deleting the key would report urban as an unknown genre and break the
    three-way genre sync invariant.
    """
    data = _load_raw_data(path)

    # Start with common (applies to all genres)
    merged: dict = {}
    common = data.get("common", {}).get(target_lang, {})
    merged.update(common)

    # Overlay genre-specific
    genre_rules = data.get("genres", {}).get(genre, {}).get(target_lang, {})
    merged.update(genre_rules)

    # Overlay book-specific last: a single book's own proper nouns outrank
    # genre-wide guidance.
    if book_id:
        book_rules = data.get("books", {}).get(book_id, {}).get(target_lang, {})
        merged.update(book_rules)

    return merged


# Rendered in place of a translation for source terms that are load-bearing but
# have no vetted rendering in the target language (de/fr: see cultural_rules.json).
# Deliberately NOT a guess at the term — the point of these entries is to flag
# "this needs care", never to supply an unvetted translation the model would
# then lock into the glossary. English on purpose: it is an instruction to the
# model, not text that reaches the reader.
_NO_TARGET = "(no fixed term — adapt, do not translate literally)"


def _rule_parts(entry) -> tuple[str, str]:
    """Normalise one rule entry to ``(target, note)`` strings.

    Tolerates the two shapes ``load_rules`` can yield: a ``{"target", "note"}``
    dict, or a bare string value (target only).  A dict missing ``target`` —
    the de/fr source-term-list case — returns an empty target.
    """
    if isinstance(entry, dict):
        target = entry.get("target", "")
        note = entry.get("note", "")
        return (target if isinstance(target, str) else "",
                note if isinstance(note, str) else "")
    return (entry if isinstance(entry, str) else "", "")


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

    Entries may legitimately carry an empty ``target`` — see ``_NO_TARGET``.
    When any entry does, the header drops its "(USE THIS)" instruction, since
    prescribing a translation that does not exist would be a lie to the model.
    Input where every entry has a translation renders byte-identically to the
    pre-de/fr version of this function.
    """
    if not rules:
        return ""

    parts = {term: _rule_parts(entry) for term, entry in rules.items()}
    prescriptive = all(target for target, _ in parts.values())

    if prescriptive:
        lines = ["| 中文 | Adapted (USE THIS) | Note |",
                 "|------|--------------------|------|"]
    else:
        lines = ["| 中文 | Adapted | Note |",
                 "|------|---------|------|"]

    for cn_term, (target, note) in parts.items():
        lines.append(f"| {cn_term} | {target or _NO_TARGET} | {note} |")

    return "\n".join(lines)


def format_rules_as_bullets(rules: dict) -> str:
    """Format cultural rules as bullet points for term extraction prompts.

    This is the renderer the READ node actually injects (see
    ``agent/nodes/read.py``), so it must handle empty targets too — otherwise
    the de/fr source-term lists would reach the model as bare arrows.

    Parameters
    ----------
    rules : dict
        ``{source_term: {target, note}}`` as returned by :func:`load_rules`.

    Returns
    -------
    str
        Bulleted lines, one per rule, e.g. ``- 霸总 → "Alpha CEO" (note)``.
        Returns an empty string when *rules* is empty.  Input where every
        entry has a translation renders byte-identically to before.
    """
    if not rules:
        return ""

    bullets = []
    for cn_term, entry in rules.items():
        target, note = _rule_parts(entry)
        body = f'- {cn_term} → "{target}"' if target else f"- {cn_term} → {_NO_TARGET}"
        if note:
            bullets.append(f"{body} ({note})")
        else:
            bullets.append(body)
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
