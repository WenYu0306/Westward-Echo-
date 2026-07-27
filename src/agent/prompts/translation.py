"""Regional style constraints for the WRITE agent.

Only LANGUAGE_STYLE_NOTES is imported by write_node.  The old v0.12
TRANSLATION_SYSTEM / TRANSLATION_USER prompts (single-pass, tool-calling)
were removed in v0.15.1 — the pipeline now uses separate READ / WRITE /
READBACK / FIX prompts.
"""

LANGUAGE_NAMES = {
    "en-US": "English (American audience)",
    "es-ES": "Spanish (Latin American / European audience)",
    "ar-SA": "Arabic (Middle Eastern audience)",
}

# Regional style constraints — injected into system prompt per target language
# to prevent region-inappropriate slang, literal idiom translation, etc.
LANGUAGE_STYLE_NOTES = {
    "es-ES": (
        "## Regional Style (Spanish)\n"
        "- Use NEUTRAL Spanish comprehensible across Latin America AND Spain.\n"
        "- Avoid Peninsular-specific idioms (tener miga, estar al loro) unless "
        "the character is explicitly Spanish. Prefer vocabulary shared across "
        "the Spanish-speaking world.\n"
        "- NEVER leave untranslated Chinese pinyin or characters in the output — "
        "all cultural concepts must have Spanish equivalents or brief explanations.\n"
        "- Adapt Chinese idioms to NATURAL Spanish equivalents, not literal translations.\n"
        "\n"
        "### Profanity & Vulgarity\n"
        "- Profanity MUST match the original's intensity and the character's social class. "
        "NEVER sanitize. '卧槽' must be as strong in Spanish as in Chinese.\n"
        "- Working-class characters (miners, street kids, gangsters) use low-class "
        "vulgarity: 'joder', 'mierda', 'me cago en...', 'puta madre'.\n"
        "- Middle/upper-class characters use milder expletives or understatement.\n"
        "- Peninsular vs LATAM: If the source is SET in Spain, use Peninsular curses. "
        "If the source is Chinese (any region), default to LATAM curses because they "
        "are understood across the Spanish-speaking world and don't pin the "
        "character to a specific Spanish geography.\n"
    ),
    "ar-SA": (
        "## Regional Style (Arabic)\n"
        "- Use Modern Standard Arabic (fusha) with vocabulary familiar to Gulf "
        "and Levant readers. Avoid Egyptian-colloquial-specific expressions.\n"
        "- NEVER translate Chinese idioms literally into Arabic. "
        "Metaphors like '像小猪一样' must use Arabic-cultural equivalents "
        "(e.g., 'like helpless children', NOT literal 'like little pigs' which "
        "is culturally confusing).\n"
        "- Watch grammatical gender agreement — especially for adjectives "
        "modifying masculine nouns.\n"
        "- NEVER leave untranslated Chinese characters or pinyin in the output.\n"
        "- Respect Islamic cultural norms: avoid normalizing alcohol, pork, or "
        "casual physical intimacy unless contextually justified by the source.\n"
        "\n"
        "### Profanity & Vulgarity\n"
        "- Match the original's intensity. Chinese web novels are raw — Arabic "
        "translation must preserve that rawness. Use street-level Arabic curses "
        "for working-class characters, not polite fusha avoidance.\n"
        "- Avoid religious blasphemy (كفر, سب الدين) unless the character would "
        "realistically say it — and even then, consider the audience.\n"
        "- street/military/gangster characters: 'ابن الكلب', 'يا خرابي', 'اللعنة'.\n"
    ),
    "en-US": (
        "## Regional Style (English)\n"
        "- Use American English for a US audience. Avoid British-isms unless "
        "the character explicitly has a British voice.\n"
        "- NEVER leave untranslated Chinese characters or pinyin in the output. "
        "This includes place names, organization names, and cultural terms — "
        "ALL must have English equivalents or brief inline explanations.\n"
        "- Chinese idioms must become NATURAL English equivalents, not literal "
        "translations. '画蛇添足' → 'gilding the lily', not 'draw legs on a snake'.\n"
        "\n"
        "### Profanity & Vulgarity\n"
        "- NEVER sanitize profanity. '卧槽' → 'Holy shit', not 'Oh my god'. "
        "'他妈的' → 'Fuck' or 'Goddammit' depending on context.\n"
        "- Working-class/miner/gangster characters: 'fuck', 'shit', 'goddamn', "
        "'son of a bitch', 'the hell'.\n"
        "- Middle/upper-class or formal characters: 'damn', 'hell', 'crap'.\n"
        "- The profanity level IS character development. A thug dropping F-bombs "
        "every sentence is different from a CEO saying 'damn' once in 500 chapters.\n"
    ),
}
