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
    for td in decisions:
        term_cn = td.get("term_cn", "")
        proposed_en = td.get("proposed_en", "")
        if not term_cn or not proposed_en:
            continue

        # Skip long/descriptive renderings — those are explanations, not a
        # single token the WRITE output can be checked against.
        if len(proposed_en) > 50 or ". " in proposed_en:
            continue

        candidates = _candidates(proposed_en)
        if not any(c in translated_text for c in candidates):
            failures.append(
                f"READ decided '{term_cn}' → '{proposed_en}' "
                "but WRITE output did not use it"
            )

    return failures
