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

【中文·这个文件是干什么的】
本文件定义每个 AI 角色在不同**目标语言**和不同**题材**下的身份设定参数。
你可以把它理解成一本"角色档案册"：翻英语时，WRITE 被告知"你是给美国读者
写作的类型小说作家"；翻西班牙语时，同一个 WRITE 被告知"你是给西班牙语
世界读者写作的作家"。题材也一样：都市奇幻、民间宗教、科幻、仙侠、言情
各有不同的创作传统和技法要求。

【中文·为什么不同语言需要不同的角色设定】
中文读者看到"鬼节"会自动脑补出香火、纸钱、屏息的村庄；英文读者只看到
一个标签，画面建立不起来。同样，西班牙读者和德国读者的文化背景也不同，
对"什么样的故事才算好看"有不同的期待。所以角色的"创作简报"必须随目标
语言调整——不是改提示词本身（提示词始终是英文），而是改里面提到的
"你的读者是谁、他们在哪里、他们用什么语言阅读"这些参数。

【中文·它在整个流程里的位置】
四个角色节点（read / write / readback / fix）在运行时会调用
build_prompt_context(genre, target_lang)，拿到一个字典，
然后用 template.format(**ctx) 把占位符填进各自的提示词模板里。
本文件就是那个字典的唯一来源。

【中文·两条核心设计规则】
① 题材和语言是正交的（互不干扰）。一份题材档案写一次英文，四种语言复用；
   只有"读者是谁"这部分随语言变化。
② 未知的题材不会假装成别的题材，而是回退到一个坦诚说"题材未指定"的
   通用档案；未知的语言会静默回退到 en-US 并记录警告日志。
"""

from __future__ import annotations

import logging

# 【中文】从全局配置取默认目标语言（目前是 "en-US"），
# 而不是在这里硬编码 "en-US"。这样如果将来默认语言变了，
# 只需改 config.py 一处，两边同步更新。
from ...config import DEFAULT_TARGET_LANG

# 【中文】语言名称映射表来自 translation.py，不在这里重复声明。
# 这和 config.py 里 SUPPORTED_LANGUAGES 是同一套"单一真相源"原则：
# 同一份数据只在一个地方定义，其他地方都引用它，避免多处复制后悄悄走样。
from .translation import LANGUAGE_NAMES

logger = logging.getLogger(__name__)

# 【中文·默认语言常量】
# 当调用方传入空值或不认识的语言代码时，回退到这个语言。
# 它是 config.DEFAULT_TARGET_LANG 的别名，不是独立的新常量。
# 注意：题材没有类似的默认常量——节点各自用 state.get("genre", ...) 处理，
# 这样本次重构不会改变任何节点的默认行为。
DEFAULT_LANG = DEFAULT_TARGET_LANG


# ── Language → audience wording ─────────────────────────────────────────
# Each entry supplies every grammatical form the prompts need, so the prompt
# text never has to build an article or a plural on its own (which is how
# "a American" style bugs appear when parameterising prose).
#
# 【中文·语言→受众措辞映射表】
# 每种语言提供提示词所需的全部语法形式（冠词、单复数、大小写等），
# 这样提示词模板里只需写 {reader}、{readers} 这样的占位符，
# 不用自己拼冠词或变复数——否则很容易出现 "a American" 这种语法错误。
#
# 各字段含义：
#   audience        —— 形容词形式，如 "American"，用于 "{audience} readers"
#   readers         —— 复数名词短语，如 "American readers"
#   reader          —— 带不定冠词的单数，如 "an American reader"
#   Reader          —— 同上但首字母大写，用于句首
#   locale          —— 地区描述，如 "America" 或 "the Spanish-speaking world"
#   output_language —— 输出语言名称，如 "English"
#
# ⚠️ 这张表的键必须与 config.py 的 SUPPORTED_LANGUAGES 保持一致。
# tests/test_language_registry.py 会断言两者同步。
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
#
# 【中文·题材档案表】
# 每种题材提供六个字段，用来填充提示词模板里的对应占位符：
#   name        —— 题材名称散文形式，如 "urban fantasy"，
#                  填入 "You write {genre_name} fiction"
#   tradition   —— 创作传统描述，告诉 AI "你从小读哪些书长大"，
#                  让它进入正确的写作心态
#   craft       —— 技法要点列表，这种题材的读者奖励什么、惩罚什么
#   concepts    —— 几个典型中文术语示例，用于解释"画面缺口"概念
#   image_case  —— 一个具体案例，展示"中文读者看到的 vs 外国读者看到的"
#   emotion     —— 这一章要传递的核心情感
#
# ⚠️ 这张表的键必须同时与以下两处保持三方一致：
#   · cultural_rules.json 的 genres 部分
#   · src/cultural_rules.py 的 _GENRE_SIGNALS
# tests/test_language_registry.py 会断言三方同步。
# 曾经出过事故：urban 在 _GENRE_SIGNALS 里缺失，导致 detect_genre()
# 永远无法返回 urban，而五本书里有三本配的就是 urban。
GENRE_PROFILES: dict[str, dict[str, str]] = {
    # ── 民间宗教 / 灵媒题材 ──
    # 核心情感是"恐惧"（dread），恐惧来自仪式程序本身而非血腥场面。
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
    # ── 都市奇幻题材 ──
    # 核心情感是"紧张感"（tension），超自然藏在日常城市生活底下。
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
    # ── 科幻题材 ──
    # 核心情感是"惊叹"（wonder），一个科技前提如何重组人类行为。
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
    # ── 仙侠 / 修真题材 ──
    # 核心情感是"敬畏"（awe），修炼阶梯清晰可见、突破有代价。
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
    # ── 言情 / 霸总题材 ──
    # 核心情感是"渴望"（longing），内心戏就是动作戏。
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

# 【中文·通用题材档案（兜底用）】
# 当调用方传入一个本模块不认识的题材时，使用这份档案。
# 它故意不对任何具体惯例做断言——编造一个题材的规则比承认不知道更糟糕，
# 因为 READ 角色才是分析原文的权威，这里只是提供一个安全的起点。
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
    """Return ``(genre_key, profile)``, falling back to the generic profile.

    【中文】根据题材字符串查找对应的题材档案。

    参数：
      genre: 题材字符串，如 "urban"、"scifi"。可以是 None 或空串。

    返回：
      一个二元组 (genre_key, profile_dict)。
      · 找到了：返回 (原始键, 对应档案字典)
      · 没找到：返回 ("", GENERIC_GENRE)，并记录一条 WARNING 日志

    ⚠️ 未知题材不会报错，而是静默回退到通用档案并打日志。
    日志里会列出所有已知题材名，方便排查是不是拼写错误。
    如果你看到这条日志，说明需要在三个地方同步添加新题材：
    GENRE_PROFILES、cultural_rules.json、_GENRE_SIGNALS。
    """
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
    """Return the audience wording for a language, falling back to en-US.

    【中文】根据目标语言代码查找对应的受众措辞档案。

    参数：
      target_lang: 目标语言代码，如 "en-US"、"es-ES"。可以是 None 或空串。

    返回：
      一个字典，包含 audience / readers / reader / Reader / locale /
      output_language 六个字段。

    ⚠️ 未知语言会静默回退到 en-US（即 DEFAULT_LANG），并记录 WARNING 日志。
    这意味着如果你传了一个不认识的语言代码（比如 "ja-JP"），提示词里会写
    "American readers"——对日语翻译任务来说这是错误的受众描述。
    但这个回退是有意为之的设计选择：比起让流水线崩溃，宁可先用英语设定跑起来，
    同时通过日志提醒你修复。如果需要支持新语言，必须在 _LANG_PROFILES 里
    添加对应条目，并在 config.py 的 SUPPORTED_LANGUAGES 里注册。
    """
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
    """Genre keys this module can brief. Used by the sync test.

    【中文】返回本模块已知的所有题材键（排序后的列表）。
    主要供测试使用：tests/test_language_registry.py 用它来断言
    GENRE_PROFILES 的键与 cultural_rules.json / _GENRE_SIGNALS 三方一致。
    """
    return sorted(GENRE_PROFILES)


def known_languages() -> list[str]:
    """Language keys this module can address. Used by the sync test.

    【中文】返回本模块已知的所有语言键（排序后的列表）。
    主要供测试使用：断言 _LANG_PROFILES 的键与 config.SUPPORTED_LANGUAGES 一致。
    """
    return sorted(_LANG_PROFILES)


def build_prompt_context(genre: str | None = None,
                         target_lang: str | None = None) -> dict[str, str]:
    """Build every placeholder the four system prompts need.

    Returns a flat ``str → str`` mapping so nodes can render with
    ``template.format(**ctx)``. Also includes ``language_name`` (from
    ``LANGUAGE_NAMES``) and ``genre_key`` for logging/telemetry.

    【中文】构建提示词模板所需的全部占位符字典。

    这是本文件对外暴露的核心函数。四个角色节点在运行时调用它，
    拿到一个 {占位符名: 值} 的扁平字典，然后用 template.format(**ctx)
    把值填进各自的提示词模板里。

    参数：
      genre:       题材字符串，如 "urban"。None 或空串时使用通用题材档案。
      target_lang: 目标语言代码，如 "en-US"。None 或空串时回退到 en-US。

    返回：
      一个扁平的 str→str 字典，包含以下占位符：
        语言/受众相关：
          · audience        —— 形容词，如 "American"
          · readers         —— 复数，如 "American readers"
          · reader          —— 带冠词单数，如 "an American reader"
          · Reader          —— 同上首字母大写
          · locale          —— 地区，如 "America"
          · output_language —— 输出语言名，如 "English"
          · language_name   —— 来自 LANGUAGE_NAMES 的完整语言描述
        题材相关：
          · genre_name       —— 题材名称
          · genre_tradition  —— 创作传统描述
          · genre_craft      —— 技法要点列表
          · genre_concepts   —— 典型中文术语示例
          · genre_image_case —— 画面缺口案例
          · genre_emotion    —— 核心情感

    数据来源：
      · 语言部分来自 _lang_profile()（内部查 _LANG_PROFILES 表）
      · 题材部分来自 _genre_profile()（内部查 GENRE_PROFILES 表）
      · language_name 来自 translation.py 的 LANGUAGE_NAMES 表
    """
    lang_key = (target_lang or DEFAULT_LANG).strip()
    lang = _lang_profile(lang_key)
    _genre_key, g = _genre_profile(genre)

    return {
        # ── 语言 / 受众相关占位符 ──
        "audience": lang["audience"],
        "readers": lang["readers"],
        "reader": lang["reader"],
        "Reader": lang["Reader"],
        "locale": lang["locale"],
        "output_language": lang["output_language"],
        # 【中文】language_name 来自 translation.py 的 LANGUAGE_NAMES，
        # 比 _LANG_PROFILES 里的 output_language 更详细（含受众地区信息）。
        # 如果 lang_key 不在 LANGUAGE_NAMES 里，就用原始的 lang_key 字符串。
        "language_name": LANGUAGE_NAMES.get(lang_key, lang_key),
        # ── 题材相关占位符 ──
        "genre_name": g["name"],
        "genre_tradition": g["tradition"],
        "genre_craft": g["craft"],
        "genre_concepts": g["concepts"],
        "genre_image_case": g["image_case"],
        "genre_emotion": g["emotion"],
    }
