"""Cultural-fidelity check — verify WRITE actually executed READ's decisions.

Rule-based (no LLM) to keep cost near zero. Catches the most visible drift:
READ decided a term's rendering (e.g. 聋婆婆 → Deaf Granny) but the WRITE
output dropped it (e.g. wrote ``Lóng Pópo`` instead). This is the class of
failure that READBACK cannot catch — a naive cold reader never sees the
Chinese original, so it cannot tell that a meaningful name was flattened to
pinyin.

Scope note (v1): only ``terminology_decisions`` are checked, and only the
short term-level renderings (names, honorifics). Long descriptive renderings
are skipped. ``cultural_gaps`` landing-verification is intentionally out of
scope for v1 — those gaps are narrative, not a single checkable token.
"""


def _candidates(proposed_en: str) -> list[str]:
    """Split a proposed rendering into candidate strings to search for.

    A rendering may offer alternatives ("Alpha CEO / domineering CEO"), in
    which case any one candidate appearing in the output counts as honored.
    """
    return [c.strip() for c in proposed_en.split("/") if c.strip()]


def _has_digit(s: str) -> bool:
    """True if the string contains any Arabic digit (0-9)."""
    return any(ch.isdigit() for ch in s)


def _is_cut(proposed_en: str) -> bool:
    """READ explicitly decided this term needs no English rendering.

    e.g. ``(cut — no English rendering needed)``. Such a decision is not a
    failure to honor — it is an instruction to omit — so it must be skipped.
    """
    low = proposed_en.lower()
    return "(cut" in low or "no english rendering" in low or "no translation" in low


def is_single_rendering(proposed_en: str) -> bool:
    """True if proposed_en is one clean rendering, not alternatives/explanation.

    e.g. ``"Deaf Granny"`` is clean; ``"Chuma Shaman / spirit medium"`` and
    ``"Qingfeng — the spirits of the violently dead"`` are not. The gate uses
    this to avoid overwriting a WRITE rendering with a sloppy multi-candidate
    or explanatory string.
    """
    return "/" not in proposed_en and "—" not in proposed_en and " - " not in proposed_en


def check_cultural_fidelity(read_analysis: dict, translated_text: str) -> list[str]:
    """Return fidelity-failure strings; empty list means nothing was violated.

    Parameters
    ----------
    read_analysis : dict
        The READ agent's structured analysis, with ``terminology_decisions``.
    translated_text : str
        The WRITE agent's English output.

    Returns
    -------
    list[str]
        One human-readable failure per dropped terminology decision.
    """
    if not read_analysis or not translated_text:
        return []

    decisions = read_analysis.get("terminology_decisions", [])
    if not decisions:
        return []

    failures = []
    low_text = translated_text.lower()
    for td in decisions:
        term_cn = td.get("term_cn", "")
        proposed_en = td.get("proposed_en", "")
        if not term_cn or not proposed_en:
            continue

        # READ decided this term needs no rendering — nothing to check.
        if _is_cut(proposed_en):
            continue

        # Suspicious rendering: a digit appeared in the English but not the
        # source (e.g. 王三 → M3). This is a wrong rendering regardless of
        # whether WRITE used it — flag it outright.
        if _has_digit(proposed_en) and not _has_digit(term_cn):
            failures.append(
                f"READ proposed a suspicious rendering '{term_cn}' → "
                f"'{proposed_en}' (a digit appeared that the source doesn't have)"
            )
            continue

        # Only character names / terms of address are checked for exact-match
        # drift. Terms, concepts, and locations legitimately get equivalent
        # renderings — checking them produces false positives.
        category = td.get("category", "")
        if category in ("location", "technique", "culture", "item", "era"):
            continue

        # Skip long/descriptive renderings — those are explanations, not a
        # single token the WRITE output can be checked against.
        if len(proposed_en) > 50 or ". " in proposed_en:
            continue

        candidates = _candidates(proposed_en)
        if not any(c.lower() in low_text for c in candidates):
            failures.append(
                f"READ decided '{term_cn}' → '{proposed_en}' "
                "but WRITE output did not use it"
            )

    return failures
