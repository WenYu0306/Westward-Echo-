"""Per-genre / per-language parameters for the four node prompts.

Why this module exists
----------------------
The four system prompts used to hardcode one genre (supernatural horror) and
one audience (American). That was never true of the corpus: the five books in
``scripts/run_novel.py`` are configured ``urban`` ×3, ``folk_religion``,
``scifi`` — not one is horror — and ``cultural_rules.json`` has no horror
entry at all. So every chapter of every book was written under a creative
brief describing a different genre, and the cold reader was told to judge a
book it was given the wrong title for.

The parameters could not simply be interpolated: all four SYSTEM prompts embed
JSON output examples, so ``str.format()`` raises ``KeyError`` on their literal
braces. Those braces are now escaped in the prompt modules and every system
prompt is rendered through :func:`build_prompt_context`.

Design rules
------------
- **Genre and language are orthogonal.** A genre profile is written once in
  English and reused for every target language; only the audience wording
  changes per language. The prompts themselves stay in English regardless of
  output language — only the instructed output language changes.
- **Unknown genre must not silently become another genre.** It falls back to
  :data:`GENERIC_GENRE`, whose text says "the genre is unspecified" rather
  than asserting the wrong conventions.
- **Unknown language falls back to en-US but is logged.** Silently writing
  "American readers" into a Spanish job is exactly the class of drift this
  module exists to remove.
- ``LANGUAGE_NAMES`` is imported, not re-declared: one source of truth, the
  same rule that fixed the ``寸`` unit-list drift in ``src/measurements.py``.
"""

from __future__ import annotations

import logging

from .translation import LANGUAGE_NAMES

logger = logging.getLogger(__name__)

# Fallback audience when a caller passes an unknown/empty target_lang. Genre has
# no such constant: nodes keep their existing ``state.get("genre", ...)``
# fallback so this refactor changes no default behaviour.
DEFAULT_LANG = "en-US"


# ── Language → audience wording ─────────────────────────────────────────
# Each entry supplies every grammatical form the prompts need, so the prompt
# text never has to build an article or a plural on its own (which is how
# "a American" style bugs appear when parameterising prose).
_LANG_PROFILES: dict[str, dict[str, str]] = {
    "en-US": {
        "audience": "American",
        "readers": "American readers",
        "reader": "an American reader",
        "Reader": "An American reader",
        "locale": "America",
        "output_language": "English",
    },
    "es-ES": {
        "audience": "Spanish-speaking",
        "readers": "Spanish-speaking readers",
        "reader": "a Spanish-speaking reader",
        "Reader": "A Spanish-speaking reader",
        "locale": "the Spanish-speaking world",
        "output_language": "Spanish",
    },
    "de": {
        "audience": "German",
        "readers": "German readers",
        "reader": "a German reader",
        "Reader": "A German reader",
        "locale": "the German-speaking world",
        "output_language": "German",
    },
    "fr": {
        "audience": "French",
        "readers": "French readers",
        "reader": "a French reader",
        "Reader": "A French reader",
        "locale": "the Francophone world",
        "output_language": "French",
    },
}


# ── Genre profiles ──────────────────────────────────────────────────────
# Keys must stay in sync with the ``genres`` section of cultural_rules.json
# and with ``_GENRE_SIGNALS`` in src/cultural_rules.py. A test asserts that
# three-way agreement, because drift between them is precisely how ``urban``
# ended up missing from _GENRE_SIGNALS (making detect_genre unable to ever
# return the genre three of the five books are configured with).
#
# Fields:
#   name        — the genre as prose, for "You write {name} fiction"
#   tradition   — the storytelling lineage the writer is cast as fluent in
#   craft       — bullet list of what this genre's readers reward / punish
#   concepts    — quoted Chinese terms for the image-gap explanation
#   image_case  — one worked example showing term → full sensory world
#   emotion     — the core feeling the chapter is built to deliver
GENRE_PROFILES: dict[str, dict[str, str]] = {
    "folk_religion": {
        "name": "folk-religion and spirit-medium fiction",
        "tradition": (
            "You grew up reading both Chinese web novels and folk horror — the "
            "village-ritual dread of M. R. James and Arthur Machen, and films "
            "like The Wicker Man, where a community's belief system is the "
            "antagonist. You move comfortably between both traditions."
        ),
        "craft": (
            "- The ritual logic IS the plot logic. A reader who does not grasp "
            "why an offering must face a certain way will read the climax as "
            "arbitrary. Convey the rule through a character's automatic "
            "behaviour, never through narration.\n"
            "- Hierarchy is visible and physical: who enters first, who sits "
            "where, who may not speak. Show rank through blocking, not labels.\n"
            "- Dread comes from procedure, not from gore. The wrong step, "
            "performed calmly, is more frightening than any monster.\n"
            "- Do not modernise the villagers' speech into scepticism. Their "
            "belief is not a character flaw to be overcome."
        ),
        "concepts": '"出马弟子", "四梁八柱", "鬼节"',
        "image_case": (
            'When the text says "Southern Mao, Northern Ma" or "Four Pillars '
            'and Eight Columns" — the Chinese reader does not just understand '
            "these terms. They SEE the incense altar, the spirit tablets, the "
            "village dynamic, the old woman's authority radiating from her "
            "household shrine. The target reader sees only the label."
        ),
        "emotion": "dread",
    },
    "urban": {
        "name": "urban fantasy",
        "tradition": (
            "You grew up reading both Chinese web novels and contemporary "
            "urban fantasy — the hidden-world city fiction of Neil Gaiman and "
            "China Miéville, where the supernatural runs underneath ordinary "
            "street life. You move comfortably between both traditions."
        ),
        "craft": (
            "- Keep the modern texture concrete and unglamorous: phone battery, "
            "rent, subway delays, convenience-store light. The supernatural is "
            "more credible when the mundane is specific.\n"
            "- Characters who live in both worlds should treat the strange one "
            "as boring bureaucracy. Familiarity, not amazement, sells it.\n"
            "- Do not explain the hidden world's rules in a block. Let a "
            "character break one and pay for it.\n"
            "- Power scaling is a contract with the reader: what costs the "
            "protagonist something in chapter 3 must still cost the same in "
            "chapter 300."
        ),
        "concepts": '"气", "丹田", "世家", "风水"',
        "image_case": (
            'When the text says "气沉丹田" or names a character\'s "世家" — the '
            "Chinese reader does not just parse the words. They feel the body "
            "map behind the breath, and the weight of a family whose name "
            "opens doors and creates enemies. The target reader sees a term "
            "and a surname."
        ),
        "emotion": "tension",
    },
    "scifi": {
        "name": "science fiction",
        "tradition": (
            "You grew up reading both Chinese web novels and literary science "
            "fiction — the idea-driven work of Ted Chiang and Liu Cixin, where "
            "a single technological premise reorganises human behaviour. You "
            "move comfortably between both traditions."
        ),
        "craft": (
            "- One premise, followed honestly. The reader forgives an "
            "implausible technology; they do not forgive its consequences "
            "being ignored when inconvenient.\n"
            "- Institutional texture carries scale: ranks, protocols, chain of "
            "command, procurement. A fleet feels real through its paperwork.\n"
            "- Keep jargon loadable from context. A term used correctly three "
            "times teaches itself; a glossary paragraph stops the story.\n"
            "- Combat is decision-making under information loss, not a list of "
            "weapons firing."
        ),
        "concepts": '"机甲", "跃迁", "联邦", "基因改造"',
        "image_case": (
            'When the text says "跃迁" or "机甲师" — the Chinese reader does not '
            "just decode the term. They feel the compression of a jump, the "
            "claustral intimacy of a cockpit that answers to nerve rather than "
            "lever, the social standing of someone licensed to wear it. The "
            "target reader sees two syllables of jargon."
        ),
        "emotion": "wonder",
    },
    "xianxia": {
        "name": "xianxia cultivation",
        "tradition": (
            "You grew up reading both Chinese web novels and progression "
            "fantasy — the disciplined power-ladder storytelling of Will "
            "Wight and Andrew Rowe, where advancement is earned and the cost "
            "is itemised. You move comfortably between both traditions."
        ),
        "craft": (
            "- The cultivation ladder must stay legible. Every realm name is a "
            "promise about what the protagonist can now survive; blur the "
            "ladder and every fight becomes noise.\n"
            "- Breakthroughs are earned, narrated as cost: years, resources, "
            "bodies, relationships spent. A free breakthrough reads as a cheat.\n"
            "- Sect politics are the real antagonist. Face, seniority, and "
            "obligation move more than any technique.\n"
            "- Do not inflate the prose to match the power level. Restraint "
            "makes scale readable."
        ),
        "concepts": '"金丹", "元婴", "渡劫", "宗门"',
        "image_case": (
            'When the text says "金丹" or "渡劫" — the Chinese reader does not '
            "just recognise a stage label. They feel a lifetime compressed into "
            "an object in the abdomen, and the sky itself turning hostile "
            "during an ascent that either founds a lineage or ends one. The "
            "target reader sees an unfamiliar noun."
        ),
        "emotion": "awe",
    },
    "romance_ceo": {
        "name": "romance",
        "tradition": (
            "You grew up reading both Chinese web novels and contemporary "
            "romance — the emotionally precise work of Helen Hoang and Ali "
            "Hazelwood, where interiority and social obstacle do the work that "
            "plot twists do elsewhere. You move comfortably between both "
            "traditions."
        ),
        "craft": (
            "- Interiority is the action. A withheld sentence, a misread "
            "gesture, and a decision not to speak are beats, not filler.\n"
            "- Status and face are the obstacle course: public reputation, "
            "family obligation, and contractual ties must press on the "
            "relationship from outside.\n"
            "- Consent and agency are non-negotiable in the modern register. "
            "Coercive tropes from the source need reframing, not deletion — "
            "keep the emotional beat, remove the coercion.\n"
            "- Pacing owes the reader a payoff for every delay. Misunderstanding "
            "without resolution is not tension, it is stalling."
        ),
        "concepts": '"霸总", "隐婚", "白月光", "豪门"',
        "image_case": (
            'When the text says "白月光" or "豪门" — the Chinese reader does not '
            "just translate a phrase. They feel an entire unreachable first "
            "love preserved in amber, and the suffocating etiquette of a family "
            "whose wealth is also a cage. The target reader sees 'white "
            "moonlight' and 'wealthy family'."
        ),
        "emotion": "longing",
    },
}

# Used when a caller passes a genre this module does not know. Deliberately
# asserts nothing about conventions: inventing a genre's rules would be worse
# than admitting the gap, and the READ agent is the authority anyway.
GENERIC_GENRE: dict[str, str] = {
    "name": "genre fiction",
    "tradition": (
        "You grew up reading both Chinese web novels and literary genre "
        "fiction in translation. You move comfortably between both traditions."
    ),
    "craft": (
        "- The genre is not specified for this book. Infer its conventions "
        "from the chapter itself — what it rewards, what it withholds, what its "
        "readers turn up for — and stay consistent with what you infer.\n"
        "- Prefer concrete scene work over exposition in every case.\n"
        "- Do not import conventions from a genre this text does not exhibit."
    ),
    "concepts": '"堂口", "世家", "宗门"',
    "image_case": (
        "When the text names a culturally loaded concept, the Chinese reader "
        "does not just decode the words: a whole world of sensory and social "
        "detail arrives for free. The target reader sees only a label. Find "
        "those labels and rebuild the world behind each one."
    ),
    "emotion": "the chapter's central feeling",
}


def _genre_profile(genre: str | None) -> tuple[str, dict[str, str]]:
    """Return ``(genre_key, profile)``, falling back to the generic profile."""
    key = (genre or "").strip().lower()
    if key in GENRE_PROFILES:
        return key, GENRE_PROFILES[key]
    if key:
        logger.warning(
            "Unknown genre %r — falling back to the generic profile. Known "
            "genres: %s. Add it to GENRE_PROFILES, cultural_rules.json and "
            "_GENRE_SIGNALS, or the creative brief will not describe this book.",
            genre, ", ".join(sorted(GENRE_PROFILES)),
        )
    return "", GENERIC_GENRE


def _lang_profile(target_lang: str | None) -> dict[str, str]:
    """Return the audience wording for a language, falling back to en-US."""
    key = (target_lang or "").strip()
    if key in _LANG_PROFILES:
        return _LANG_PROFILES[key]
    if key:
        logger.warning(
            "Unknown target_lang %r — audience wording falls back to %s, so the "
            "prompts will address %s readers. Known languages: %s.",
            target_lang, DEFAULT_LANG,
            _LANG_PROFILES[DEFAULT_LANG]["audience"],
            ", ".join(sorted(_LANG_PROFILES)),
        )
    return _LANG_PROFILES[DEFAULT_LANG]


def known_genres() -> list[str]:
    """Genre keys this module can brief. Used by the sync test."""
    return sorted(GENRE_PROFILES)


def known_languages() -> list[str]:
    """Language keys this module can address. Used by the sync test."""
    return sorted(_LANG_PROFILES)


def build_prompt_context(genre: str | None = None,
                         target_lang: str | None = None) -> dict[str, str]:
    """Build every placeholder the four system prompts need.

    Returns a flat ``str → str`` mapping so nodes can render with
    ``template.format(**ctx)``. Also includes ``language_name`` (from
    ``LANGUAGE_NAMES``) and ``genre_key`` for logging/telemetry.
    """
    lang_key = (target_lang or DEFAULT_LANG).strip()
    lang = _lang_profile(lang_key)
    _genre_key, g = _genre_profile(genre)

    return {
        # language / audience
        "audience": lang["audience"],
        "readers": lang["readers"],
        "reader": lang["reader"],
        "Reader": lang["Reader"],
        "locale": lang["locale"],
        "output_language": lang["output_language"],
        "language_name": LANGUAGE_NAMES.get(lang_key, lang_key),
        # genre
        "genre_name": g["name"],
        "genre_tradition": g["tradition"],
        "genre_craft": g["craft"],
        "genre_concepts": g["concepts"],
        "genre_image_case": g["image_case"],
        "genre_emotion": g["emotion"],
    }
