"""Scene-level completeness check: did every source scene survive?

Why this exists
---------------
The WRITE agent is granted real creative authority — it may restructure,
compress, expand, cut and invent (see ``src/agent/prompts/write.py``). That
freedom is what makes the output read like native English fiction instead of a
translation, and it is the whole point of the pipeline.

But the prompt bounds that freedom in the same breath:

    "The translated_text field must contain the COMPLETE chapter. Do not
     abbreviate, summarize, or skip passages. Every scene in the source must
     have an equivalent in the output — even compressed scenes still convey
     their story function. If you received a reader analysis that says to cut
     something, compress it creatively, don't delete it silently."

Nothing verified that bound. The READBACK agent cannot: by design it never sees
the source ("READBACK is blind to the source", src/agent/graph.py), so it can
judge feel but never fidelity. The truncation guard only counts words. So a
chapter where WRITE silently dropped half the scenes passed every check the
pipeline had.

This module supplies the missing mirror: it compares the source chapter against
the output with signals that need no model and no API call.

Signals
-------
``glossary_missing``
    Glossary terms that occur in the source chapter but whose established
    English rendering never appears in the output. The prompt calls glossary
    terms "LAW", so a missing one is either a dropped scene or a broken
    consistency promise.
``dialogue_ratio``
    Opening-quote count in the output vs the source. Dropped conversations
    show up here before they show up anywhere else.
``length_ratio``
    Output characters per source Chinese character.
``para_ratio``
    Output paragraphs vs source paragraphs.

Calibration policy
------------------
This module **reports**; it does not yet gate. Thresholds for corpus-level
fiction vary by genre and by how much compression a given voice legitimately
does, and guessing them would repeat the project's oldest mistake — shipping a
number nobody validated. Record the metrics for a while, look at the
distribution, then set the gate from evidence.

Usage::

    from src.completeness import check_completeness
    report = check_completeness(source_text, translated_text, glossary)
    if report["flags"]:
        ...
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, Mapping

logger = logging.getLogger(__name__)

CJK = re.compile(r"[\u4e00-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")

# Opening quotes only — counting pairs double-counts and hides a dropped line.
SRC_OPEN_QUOTES = ("\u201c", "\u300c", "\u300e")      # “ 「 『
OUT_OPEN_QUOTES = ('"', "\u201c", "\u300c", "\u300e")

# A rendering like "Wuxin (No-Heart)" is stored WITH its gloss, but the prose
# normally uses only the bare name. Matching the gloss verbatim reported a
# false "missing term" for the protagonist of a 2301-chapter book, so strip
# parentheticals and trailing dash-clauses before searching.
_PAREN = re.compile(r"\([^)]*\)")
_DASH_TAIL = re.compile(r"[\u2013\u2014-].*$")
_SLASH_ALTS = "/"


def base_renderings(rendering: str) -> list[str]:
    """Bare English forms to search for, from a glossary rendering.

    ``"Wuxin (No-Heart)"``        -> ``["Wuxin"]``
    ``"Potter's Field / the common graves"`` -> both alternatives
    ``"Huang Chao – the Butcher of Chang'an"`` -> ``["Huang Chao"]``
    """
    out: list[str] = []
    for alt in (rendering or _SLASH_ALTS.join([])).split(_SLASH_ALTS):
        b = _PAREN.sub("", alt)
        b = _DASH_TAIL.sub("", b)
        b = b.strip(" ,;:")
        # Too short to search safely (would match inside other words).
        if len(b) >= 3:
            out.append(b)
    return out


def count_dialogue_spans(
    text: str,
    quotes: Iterable[str] = OUT_OPEN_QUOTES,
    *,
    ascii_halved: bool = False,
) -> int:
    """Count spoken spans — a proxy for how many lines of dialogue there are.

    Chinese prose marks speech with a distinct opening quote (``“``), so each
    occurrence is one span. ASCII uses the same character for open and close, so
    raw occurrences are DOUBLE the span count — comparing a Chinese source
    (1 per span) against an ASCII output (2 per span) inflated the ratio and
    made the output look like it carried more dialogue than it did. Pass
    ``ascii_halved=True`` when counting an English output.

    An odd count rounds up, so a single unbalanced quote still registers as one
    span rather than vanishing.
    """
    total = 0
    for q in quotes:
        c = text.count(q)
        if ascii_halved and q == '"':
            c = (c + 1) // 2
        total += c
    return total


def count_paragraphs(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def check_completeness(
    source_text: str,
    translated_text: str,
    glossary: Mapping[str, str] | None = None,
    *,
    min_term_len: int = 2,
    min_length_ratio: float | None = 2.0,
) -> dict:
    """Compare a source chapter against its English output.

    Args:
        source_text: the Chinese chapter as fed to WRITE.
        translated_text: WRITE's English output for that chapter.
        glossary: ``{chinese_term: english_rendering}`` accumulated so far.
            Terms not present in *source_text* are ignored — only terms this
            chapter actually uses can tell us anything about this chapter.
        min_term_len: ignore glossary keys shorter than this; single characters
            match too much Chinese prose to be informative.

    Returns:
        A report dict. ``flags`` is empty when nothing looked wrong.
    """
    src = source_text or ""
    out = translated_text or ""
    glossary = glossary or {}

    src_cjk = len(CJK.findall(src))
    out_chars = len(out)
    length_ratio = round(out_chars / src_cjk, 2) if src_cjk else None

    # ── glossary coverage ────────────────────────────────────────────────
    used_terms = {cn: en for cn, en in glossary.items()
                  if cn and len(cn) >= min_term_len and cn in src}
    glossary_missing: list[str] = []
    for cn, rendering in used_terms.items():
        forms = base_renderings(rendering)
        if forms and not any(f in out for f in forms):
            glossary_missing.append(cn)

    # ── dialogue + structure ─────────────────────────────────────────────
    dlg_src = count_dialogue_spans(src, SRC_OPEN_QUOTES)
    dlg_out = count_dialogue_spans(out, OUT_OPEN_QUOTES, ascii_halved=True)
    dialogue_ratio = round(dlg_out / dlg_src, 2) if dlg_src else None
    paras_src = count_paragraphs(src)
    paras_out = count_paragraphs(out)
    para_ratio = round(paras_out / paras_src, 2) if paras_src else None

    flags: list[str] = []
    if not out.strip():
        flags.append("EMPTY_OUTPUT")
    if dlg_src >= 4 and dlg_out == 0:
        flags.append("DIALOGUE_DROPPED")
    if (min_length_ratio is not None and length_ratio is not None
            and src_cjk >= 400 and length_ratio < min_length_ratio):
        flags.append("LENGTH_RATIO_LOW")

    # NOT a flag: `glossary_missing`.
    # Calibrated against 《地府》 — the 11 chapters known to be truncated had
    # 8-24 missing terms, but so did all 8 healthy control chapters (5-16).
    # The ranges overlap, so the signal cannot separate defect from normal.
    # The cause is by design: WRITE is granted authority to compress and cut
    # minor mentions, so "term appears in source, not in output" is expected
    # behaviour, not evidence of a dropped scene. It is reported as a metric
    # only; do not promote it to a flag without a fresh calibration.

    report = {
        "src_cjk": src_cjk,
        "out_chars": out_chars,
        "length_ratio": length_ratio,
        "paras_src": paras_src,
        "paras_out": paras_out,
        "para_ratio": para_ratio,
        "dialogue_src": dlg_src,
        "dialogue_out": dlg_out,
        "dialogue_ratio": dialogue_ratio,
        "glossary_used": len(used_terms),
        "glossary_missing": glossary_missing,
        "flags": flags,
    }
    if flags:
        logger.warning(
            "COMPLETENESS: %s (cjk=%d out=%d ratio=%s dialogue %d->%d "
            "terms %d/%d present)",
            ",".join(flags), src_cjk, out_chars, length_ratio,
            dlg_src, dlg_out, len(used_terms) - len(glossary_missing),
            len(used_terms),
        )
    return report
