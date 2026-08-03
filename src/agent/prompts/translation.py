"""Regional style constraints for the WRITE agent.

Only LANGUAGE_STYLE_NOTES is imported by write_node.  The old v0.12
TRANSLATION_SYSTEM / TRANSLATION_USER prompts (single-pass, tool-calling)
were removed in v0.15.1 — the pipeline now uses separate READ / WRITE /
READBACK / FIX prompts.
"""

LANGUAGE_NAMES = {
    "en-US": "English (American audience)",
    "es-ES": "Spanish (Latin American / European audience)",
    "de": "German (DACH region)",
    "fr": "French (Francophonie)",
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
    "de": (
        "## Regional Style (German)\n"
        "- Use High German (Hochdeutsch) readable across Germany, Austria, and "
        "Switzerland. Avoid regional dialect unless the source character is "
        "explicitly marked as speaking a dialect.\n"
        "- NEVER leave untranslated Chinese characters or pinyin in the output.\n"
        "- Chinese idioms must become NATURAL German equivalents, not literal "
        "translations. '画蛇添足' → 'Eulen nach Athen tragen', not "
        "'einer Schlange Beine malen'.\n"
        "- Watch noun genders and case agreement — German readers notice "
        "every der/die/das error.\n"
        "\n"
        "### Formality (Sie / du)\n"
        "- Strangers, authority figures, formal settings → 'Sie'.\n"
        "- Friends, family, children, close colleagues → 'du'.\n"
        "- The switch from 'Sie' to 'du' is a character-beat. If the original "
        "shows a relationship shifting from formal to intimate, reflect it.\n"
        "\n"
        "### Profanity & Vulgarity\n"
        "- NEVER sanitize profanity. Match the original's intensity.\n"
        "- Working-class/miner/gangster characters: 'Scheiße', 'verdammt', "
        "'Hölle', 'Arschloch', 'verfickt'.\n"
        "- Middle/upper-class or formal characters: 'Mist', 'verflixt', 'verdammt'.\n"
        "- Profanity level IS character development — a thug dropping 'Scheiße' "
        "every sentence is different from a CEO saying 'Mist' once in 500 chapters.\n"
    ),
    "fr": (
        "## Regional Style (French)\n"
        "- Use neutral French readable across France, Belgium, Switzerland, "
        "and Francophone markets. Avoid verlan, Québécois expressions, or "
        "Parisian slang unless the character calls for it.\n"
        "- NEVER leave untranslated Chinese characters or pinyin in the output.\n"
        "- Chinese idioms must become NATURAL French equivalents, not literal "
        "translations. '画蛇添足' → 'c'est la cerise sur le gâteau en trop', "
        "not 'dessiner des pattes sur un serpent'.\n"
        "- Watch grammatical gender and past-participle agreement.\n"
        "\n"
        "### Formality (tu / vous)\n"
        "- Strangers, authority figures, formal settings → 'vous'.\n"
        "- Friends, family, children, close colleagues → 'tu'.\n"
        "- The switch from 'vous' to 'tu' is a character-beat.\n"
        "\n"
        "### Profanity & Vulgarity\n"
        "- NEVER sanitize profanity. Match the original's intensity.\n"
        "- Working-class/miner/gangster characters: 'putain', 'merde', 'bordel', "
        "'fait chier', 'connard'.\n"
        "- Middle/upper-class or formal characters: 'zut', 'mince', 'bon sang'.\n"
        "- French profanity uses religious/swear words differently than English — "
        "keep the FEELING of the curse, not the literal word class.\n"
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
