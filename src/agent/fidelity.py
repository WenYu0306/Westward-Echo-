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


import re


_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_NUMERAL_CHARS = "零〇一二三四五六七八九两十百千万亿"


def _cn_numeral_to_int(run: str) -> int:
    """Parse a Chinese numeral run. Handles both readings.

    Digit-by-digit (no place marker): 三零一 -> 301, 六 -> 6
    With place markers:               二十三 -> 23, 二百零一 -> 201

    The digit-by-digit form matters: 三零一度 → "301 Degrees" is a real
    glossary entry, and parsing 三零一 as 1 produced a false "invented number"
    verdict on it.
    """
    if not any(c in "十百千万亿" for c in run):
        digits = "".join(str(_CN_DIGITS[c]) for c in run if c in _CN_DIGITS)
        return int(digits) if digits else 0

    total = section = number = 0
    for ch in run:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch == "十":
            section += (number or 1) * 10
            number = 0
        elif ch == "百":
            section += (number or 1) * 100
            number = 0
        elif ch == "千":
            section += (number or 1) * 1000
            number = 0
        elif ch == "万":
            total += (section + number) * 10000
            section = number = 0
        elif ch == "亿":
            total = (total + section + number) * 100000000
            section = number = 0
    return total + section + number


def _numbers_in(s: str) -> set[int]:
    """Every number expressed in *s*, in Arabic digits or Chinese numerals.

    Replaces a check that only looked for Arabic digit characters. The old
    question was "does the SOURCE contain an Arabic digit (0-9)?", so a term
    written 联邦二十三频道 ("Federal Channel twenty-three") looked digit-free
    while its CORRECT rendering "Federal Channel 23" looked invented, and the
    curation gate deleted it.

    Not a corner case: Chinese writes counts, tiers, years and names as numerals
    constantly. Measured on 《间客》 chapter 1 — the orphans' rallying cry
    我们要看联邦二十三频道! is the plot's spine, and the gate dropped the term,
    locking every later chapter out of using it.
    """
    out: set[int] = set()
    for m in re.finditer(r"\d+", s or ""):
        out.add(int(m.group()))
    for m in re.finditer(f"[{_CN_NUMERAL_CHARS}]+", s or ""):
        value = _cn_numeral_to_int(m.group())
        if value:
            out.add(value)
    return out


def _invents_number(term_cn: str, rendering: str) -> bool:
    """True when *rendering* states a number the source term does not contain.

    Still catches genuine invention (a year the source never had) while no
    longer mistaking 二十三 for an invented 23.
    """
    return bool(_numbers_in(rendering) - _numbers_in(term_cn))


# A rendering shaped like a designation rather than a name: "M3", "B12", "7X".
_CODE_LIKE = re.compile(r"^[A-Za-z]{1,3}\d+$|^\d+[A-Za-z]{1,3}$")


def _is_code_like(rendering: str) -> bool:
    """True for a bare letter+digit designation.

    This replaces what the digit rule used to catch by accident. 王三 → "M3" is
    wrong because "M3" is a code, not because a number was invented: 王三
    contains 三, so the 3 is faithfully present. The old rule conflated the two.

    Deliberately narrow, and calibrated rather than guessed: across 1936 real
    glossary entries this pattern matches ZERO, while every legitimate
    number-bearing rendering carries word structure — "Refuge Base 7",
    "Nemesis-2", "Riffraff No.1", "Chaonei Street 130", "301 Degrees".
    """
    return bool(_CODE_LIKE.match((rendering or "").strip()))


def _is_cut(proposed_en: str) -> bool:
    """READ explicitly decided this term needs no English rendering.

    e.g. ``(cut — no English rendering needed)``. Such a decision is not a
    failure to honor — it is an instruction to omit — so it must be skipped.
    """
    low = proposed_en.lower()
    return "(cut" in low or "no english rendering" in low or "no translation" in low


def _is_explanatory(rendering: str) -> bool:
    """True if a rendering is a gloss rather than a single usable token.

    A term cached in the glossary is re-injected as "must use" for every
    later chapter, so only a token WRITE can drop into prose verbatim may be
    cached. These are glosses, not tokens:

      "Chuma Shaman / spirit medium"                  → alternatives
      "Qingfeng — the spirits of the violently dead"  → em-dash gloss
      "Ghost Festival (chapter title: 'Ghost Festival')" → parenthetical gloss
      "Ghost Festival: the night the dead walk"       → colon gloss
    """
    if any(mark in rendering for mark in ("/", "—", "–", "(", ")", ":")):
        return True
    return " - " in rendering


def is_single_rendering(proposed_en: str) -> bool:
    """True if proposed_en is one clean rendering, not alternatives/explanation.

    e.g. ``"Deaf Granny"`` is clean; ``"Chuma Shaman / spirit medium"`` and
    ``"Qingfeng — the spirits of the violently dead"`` are not. The gate uses
    this to avoid overwriting a WRITE rendering with a sloppy multi-candidate
    or explanatory string.
    """
    return bool(proposed_en.strip()) and not _is_explanatory(proposed_en)


# Tone-marked Latin vowels — the tell of a pinyin transliteration (Lóng Pópo).
_TONE_MARKED = frozenset("áàǎāéèěēíìǐīóòǒōúùǔūǘǜǚǖü")


def is_valid_rendering(term_cn: str, term_en: str) -> tuple[bool, str]:
    """Hard rule-check for whether a term's English rendering is acceptable.

    This is the deterministic gate (unlike the LLM review node): a rendering
    that fails here is *obviously* wrong and must not be cached into the
    glossary, where it would be locked in as "must use" for the whole book.

    Returns (ok, reason). ``ok`` is False when the rendering is empty, contains
    Chinese, contains tone-marked pinyin, contains a digit that the source
    term doesn't have, or is a gloss/explanation rather than a single token.
    """
    en = term_en.strip()
    if not en:
        return False, "empty rendering"
    if any("一" <= ch <= "鿿" for ch in en):
        return False, "contains Chinese"
    if any(ch in _TONE_MARKED for ch in en):
        return False, "contains tone-marked pinyin"
    if _invents_number(term_cn, en):
        return False, "a number appeared that the source doesn't have"
    if _is_code_like(en):
        return False, "code-like designation, not a name"
    if _is_explanatory(en):
        return False, "explanatory rendering, not a single token"
    return True, ""


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

        # Suspicious rendering: the English states a number the source term
        # does not (e.g. 联邦23频道 → "Channel 24"), or is a bare designation
        # rather than a name (e.g. 王三 → "M3"). Wrong regardless of whether
        # WRITE used it — flag it outright.
        if _invents_number(term_cn, proposed_en):
            failures.append(
                f"READ proposed a suspicious rendering '{term_cn}' → "
                f"'{proposed_en}' (a number appeared that the source doesn't have)"
            )
            continue
        if _is_code_like(proposed_en):
            failures.append(
                f"READ proposed a suspicious rendering '{term_cn}' → "
                f"'{proposed_en}' (code-like designation, not a name)"
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
