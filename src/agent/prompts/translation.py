"""Regional style constraints for the WRITE agent.

Only LANGUAGE_STYLE_NOTES is imported by write_node.  The old v0.12
TRANSLATION_SYSTEM / TRANSLATION_USER prompts (single-pass, tool-calling)
were removed in v0.15.1 — the pipeline now uses separate READ / WRITE /
READBACK / FIX prompts.

【中文·这个文件是干什么的】
本文件为 WRITE 角色（英文类型小说作家）提供**各目标语言的区域风格约束**。
所谓"区域风格约束"，就是告诉 AI："你写给西班牙读者看时，别用只有墨西哥人
才懂的俚语；写给德国读者看时，注意名词的性数格别搞错。"

【中文·它在整个流程里的位置】
只有 WRITE 节点（src/agent/nodes/write.py）会导入本文件的
LANGUAGE_STYLE_NOTES，把它拼进 WRITE 角色的系统提示词里。
其他角色不需要这些约束——READ 读的是中文原文，READBACK 和 FIX
只看已经写好的英文成品，不涉及"用什么方言写"的问题。

【中文·为什么需要这个文件】
同一种语言在不同地区差异很大。比如西班牙语：西班牙本土说"tener miga"，
拉美读者完全听不懂；中文的"卧槽"翻成"Oh my god"就太温和了，
原文的情绪强度被抹掉了。这些约束确保译文对目标读者来说既自然又准确。

【中文·历史变更】
v0.12 时代这里曾存放一整套翻译提示词（TRANSLATION_SYSTEM / TRANSLATION_USER），
当时是"一次性翻译"模式。v0.15.1 改为四角色流水线后，那些提示词已删除，
本文件现在只保留语言名称映射和区域风格约束两块数据。
"""

# 【中文·语言名称映射表】
# 键是语言代码（与 config.py 的 SUPPORTED_LANGUAGES 一一对应），
# 值是给人看的语言描述，包含目标受众地区信息。
# 被 personas.py 的 build_prompt_context() 引用，用来填充提示词里的
# {language_name} 占位符——告诉 AI "你现在写给谁看"。
LANGUAGE_NAMES = {
    "en-US": "English (American audience)",
    "es-ES": "Spanish (Latin American / European audience)",
    "de": "German (DACH region)",
    "fr": "French (Francophonie)",
}

# 【中文·区域风格约束（按目标语言分）】
# 这是一组喂给大模型的提示词片段，不是给人读的文档。
# ⚠️ 里面的英文内容是业务数据，一个字都不能改——改了直接影响翻译质量。
#
# 每个语言的约束覆盖三个方面：
#   ① 区域中立性：避免使用只有某个地区才懂的方言/俚语
#   ② 文化概念处理：中文成语/拼音必须转成目标语言的自然表达，不能留着不翻
#   ③ 粗口/脏话分级：不同社会阶层的角色用不同强度的粗口，
#      且必须匹配原文的情绪强度，不能"净化"（sanitize）
#
# 运行时由 write_node 根据 target_lang 取出对应条目，
# 拼接到 WRITE 角色的系统提示词末尾。
# 如果 target_lang 不在这张表里，write_node 取到空字符串，
# 等于不加任何额外约束（回退到提示词本身的默认行为）。
LANGUAGE_STYLE_NOTES = {
    # ── 西班牙语 ──
    # 要求使用拉美和西班牙都能看懂的"中性西班牙语"；
    # 粗口默认用拉美版本（因为覆盖面更广），除非原文明确设定在西班牙。
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
    # ── 德语 ──
    # 要求使用标准高地德语（Hochdeutsch），德/奥/瑞通用；
    # 特别注意名词的性（der/die/das）和格变化，德语读者对此极其敏感；
    # 还规定了 Sie（您）/ du（你）的切换规则——这在德语里是重要的人物关系信号。
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
    # ── 法语 ──
    # 要求使用法国/比利时/瑞士/法语区通用的中性法语；
    # 同样规定了 tu（你）/ vous（您）的切换规则；
    # 特别提醒：法语粗口的宗教色彩和英语不同，要保留"感觉"而非字面对应。
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
    # ── 美式英语 ──
    # 要求使用美式英语，避免英式用法；
    # 所有中文概念必须有英文等价物或简短解释，不能留拼音或汉字。
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
