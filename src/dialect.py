"""Dialect detection and mapping for character voice preservation.

Detects regional Chinese dialect markers in source text and maps them to
appropriate English dialect equivalents. Injects dialect context into the
translation prompt so the LLM can preserve each character's unique voice.

【中文·这个检测器检测什么】
检测中文原文里的**方言标记词**。中国各地方言有自己的特色词汇，
比如东北人说"嘎哈"（干什么）、四川人说"巴适"（舒服）、
北京人说"甭"（不用）、上海人说"侬"（你）、粤语说"冇"（没有）。
这些词出现在角色对白里，说明这个角色说的是方言。

【中文·为什么需要它】
中文网文经常用方言来塑造角色个性——一个说东北话的角色通常是豪爽直率的，
一个说上海话的角色通常是精明讲究的。如果翻译时把所有方言都抹平成标准英语，
角色的"声音"就丢了，所有角色听起来都一样。
这个检测器做了 5 组中英方言映射：
  · 东北话 → 美国南方英语（y'all, ain't）—— 都是朴实直爽的感觉
  · 四川话 → 德州口音 / 爱尔兰英语 —— 都是悠闲中带点幽默
  · 京片子 → 布鲁克林 / 纽约工人阶级英语 —— 都是老江湖的自信
  · 上海话 → 曼哈顿商务精英英语 —— 都是都市精致感
  · 粤语   → 英国俚语 / 伦敦口语 —— 都是色彩丰富、身份感强
注意：这不是逐词翻译，而是告诉 AI "用这种语气和风格来说话"。

【中文·它在流程里的位置】
在 READ 阶段被 `_build_context_signals()` 调用（见 src/agent/nodes/read.py）。
本文件还包含 LitRPG 系统面板标记检测（has_system_text），也在同一处被调用。

【中文·输出格式】
返回一段 Markdown 格式的提示文本，例如：
    ## DIALECT CONTEXT
    - Characters speaking **东北话** (5 markers found): **Southern American English**...
如果没有发现任何方言，返回空字符串。
"""

from typing import Optional

# ── Dialect detection patterns ────────────────────────────────
# Each dialect has a set of marker words/phrases that signal the
# character is speaking in that dialect.
# 【中文·方言标记词表】
# 每种方言有一组"标志性词汇"，只要原文里出现足够多（≥2 个）这些词，
# 就判定这段文字里有该方言。这些词是各方言最独特、最不容易跟普通话混淆的词。
# ⚠️ 这是业务数据，改动会直接影响方言识别准确率，不要修改内容。

DIALECT_MARKERS: dict[str, list[str]] = {
    "dongbei": [
        # 东北话 (Dongbei / Northeastern Mandarin)
        "整", "啥", "咋", "俺", "唠嗑", "得劲儿", "忽悠", "老鼻子",
        "嘎哈", "咋地", "蔫吧", "磕碜", "贼", "嗯呐", "拉倒",
        "扒瞎", "膈应", "磨叽", "扯犊子", "滚犊子",
    ],
    "sichuan": [
        # 四川话 (Sichuanese)
        "啥子", "咋子", "哪个", "巴适", "安逸", "要得", "晓得不",
        "耍", "瓜娃子", "摆龙门阵", "雄起", "莫得",
    ],
    "beijing": [
        # 京片子 (Beijing dialect)
        "您", "爷们", "甭", "嘛呢", "忒", "倍儿", "颠儿了",
        "瓷器", "侃大山", "撮一顿", "门儿清", "遛弯儿",
    ],
    "shanghai": [
        # 上海话标记 (Shanghainese markers in Mandarin text)
        "侬", "阿拉", "伐", "好伐", "拎不清", "捣糨糊",
    ],
    "cantonese": [
        # 粤语标记 (Cantonese markers in Mandarin text)
        "冇", "乜", "佢", "哋", "嘅", "咁", "咩",
        "顶你个肺", "食饭", "饮茶", "靓仔", "靓女",
    ],
}

# ── Dialect → English equivalent mapping ───────────────────────
# These describe the TONE and style the LLM should use, not
# literal word-for-word dialect substitutions.
# 【中文·方言映射表】
# 每种中文方言对应一种英文方言/口音风格。不是逐词替换，
# 而是告诉 AI "这个角色说话应该用什么语气、什么节奏、什么标志性表达"。
# 每组包含四个字段：
#   dialect_en —— 对应的英文方言名称
#   markers    —— 英文方言的标志性表达示例
#   tone       —— 语气描述（AI 用来把握整体感觉）
#   note       —— 为什么这样映射的解释
# ⚠️ 这是业务数据，改动会直接影响翻译风格，不要修改内容。

DIALECT_MAPPING: dict[str, dict[str, str]] = {
    "dongbei": {
        "dialect_en": "Southern American English",
        "markers": "y'all, fixin' to, ain't, reckon, done gone, might could, right quick",
        "tone": "blunt, warm, down-to-earth, sometimes gruff but with a big heart. "
                "Speaks in short, punchy sentences. Loves exaggeration for effect.",
        "note": "Dongbei dialect signals a salt-of-the-earth, no-nonsense character. "
                "Southern American English carries the same combination of warmth + blunt honesty.",
    },
    "sichuan": {
        "dialect_en": "Texas drawl or Irish English",
        "markers": "y'all, fixin' to, gonna, oughta, right kindly",
        "tone": "laid-back, humorous, enjoys life, a bit lazy-sounding but sharp underneath. "
                "Sentences have a lilting, casual rhythm.",
        "note": "Sichuan dialect has a distinctive melodic quality and laid-back attitude. "
                "Texas drawl captures the 'relaxed but sharp' personality.",
    },
    "beijing": {
        "dialect_en": "Brooklyn / working-class New York English",
        "markers": "fuggedaboutit, y'know what I'm sayin, the thing is, listen here",
        "tone": "chatty, street-smart, loves to talk, drops cultural references constantly. "
                "Confident, a bit cocky, knows everyone and everything.",
        "note": "Beijing dialect is the original 'smooth-talking city slicker' voice. "
                "Brooklyn carries the same old-school urban credibility.",
    },
    "shanghai": {
        "dialect_en": "Polished metropolitan English (think Manhattan finance)",
        "markers": "frankly speaking, look, the thing is, honestly, indeed",
        "tone": "refined, slightly aloof, businesslike, values efficiency and sophistication. "
                "Chooses words carefully. Never vulgar.",
        "note": "Shanghainese signals urban sophistication and financial savvy. "
                "Manhattan professional English carries the same signaling.",
    },
    "cantonese": {
        "dialect_en": "British slang / London vernacular",
        "markers": "mate, bloody, proper, innit, sorted, cheers, blimey, knackered",
        "tone": "direct, pragmatic, generous with insults and praise alike. "
                "Colorful expressions. Strong sense of local identity.",
        "note": "Cantonese has a punchy, colorful quality with vivid local expressions. "
                "British slang carries similar energy — blunt, colorful, identity-heavy.",
    },
}


# ── LitRPG / System text detection ──────────────────────────
# 【中文·LitRPG 系统面板标记】
# LitRPG 是一种小说类型，故事里有类似游戏的系统界面（等级、技能、任务等）。
# 中文网文里这类文字通常有特殊标记，比如"叮——""系统提示""【系统】"。
# 检测到这些标记后，AI 就知道该用游戏 UI 的排版惯例来处理，
# 而不是把这些文字当成普通叙述或对话。
# ⚠️ 这是业务数据，改动会影响系统面板识别，不要修改内容。

SYSTEM_TEXT_MARKERS = [
    "叮——", "系统提示", "系统通知", "【系统】", "好感度", "主线任务",
    "支线任务", "经验值", "技能", "成就", "道具",
]


def has_system_text(text: str) -> bool:
    """Detect if chapter contains LitRPG-style system popups.

    Returns True when any of the common Chinese web novel system notification
    markers are found in the text. These indicate game-UI elements (status
    windows, skill acquisition notices, stat changes) that should be rendered
    using LitRPG formatting conventions rather than as prose dialogue.

    【中文】检查原文里有没有游戏系统面板的标记词。
    · 参数 text：要扫描的中文原文。
    · 返回：True 表示发现了至少一个标记词，False 表示没有。
    · 为什么存在：LitRPG 网文的系统面板有特殊的排版惯例（方框、加粗、分隔线等），
      AI 需要知道"这段是系统界面文字"才能用正确的格式呈现。
      比如"叮——恭喜宿主获得新技能"应该渲染成系统通知样式，不是普通旁白。
    · 坑：只返回 True/False，不像其他检测器返回格式化文本。
      调用方（read.py）需要自己构造提示消息。
    """
    return any(m in text for m in SYSTEM_TEXT_MARKERS)


def detect_dialects(text: str) -> dict[str, int]:
    """Scan text for dialect markers. Returns {dialect_name: marker_count}.

    Only returns dialects with >= 2 markers found (filters noise).

    【中文】扫描原文，统计每种方言标记词出现了多少个。
    · 参数 text：要扫描的中文原文。
    · 返回：一个字典，键是方言名（如 "dongbei"），值是命中的标记词数量。
      只保留命中 ≥2 个标记词的方言，过滤掉偶然出现的单个词（噪声）。
    · 为什么阈值是 2：单个标记词可能是巧合（比如"啥"在普通话里也偶尔用），
      但同时出现"嘎哈""咋地""唠嗑"就基本确定是东北话了。
    """
    results = {}
    for dialect_name, markers in DIALECT_MARKERS.items():
        count = sum(1 for m in markers if m in text)
        if count >= 2:
            results[dialect_name] = count
    return results


def get_dialect_hint(dialect_name: str) -> Optional[str]:
    """Return a formatted dialect hint for injection into the translation prompt.

    Returns None if the dialect is unknown.

    【中文】根据方言名，从映射表里取出对应的英文方言提示信息。
    · 参数 dialect_name：方言标识符，如 "dongbei"、"sichuan"。
    · 返回：一段格式化的提示字符串，包含英文方言名、标志性表达、语气描述；
      如果方言名不在映射表里，返回 None。
    · 为什么存在：把映射表里的多个字段拼成一段连贯的文字，
      方便直接塞进提示词，调用方不需要自己拼接。
    """
    mapping = DIALECT_MAPPING.get(dialect_name)
    if not mapping:
        return None

    return (
        f"**{mapping['dialect_en']}** dialect. "
        f"Use: {mapping['markers']}. "
        f"Tone: {mapping['tone']} "
        f"({mapping['note']})"
    )


def build_dialect_context(chapter_text: str) -> str:
    """Build the full dialect context block for the translation prompt.

    Returns empty string if no dialects detected.

    【中文】完整的方言上下文构建器：检测 → 排序 → 格式化 → 拼接标题头。
    这是对外暴露的主入口函数，read.py 直接调用它。
    · 参数 chapter_text：要扫描的中文原文（一整章）。
    · 返回：Markdown 格式的方言提示块；没发现方言则返回空字符串 ""。
    · 为什么按标记词数量降序排列：信号最强的方言排前面，
      AI 优先关注最确定的方言，避免被弱信号干扰。
    · 坑：内部有一个硬编码的方言名→中文名映射（dongbei→东北话 等），
      如果 DIALECT_MARKERS 新增了方言但忘了在这里加中文名，
      显示时会 fallback 到英文标识符（如 "new_dialect"），不会报错但不美观。
    """
    detected = detect_dialects(chapter_text)
    if not detected:
        return ""

    # Sort by marker count descending (strongest signal first)
    parts = []
    for dialect_name in sorted(detected, key=detected.get, reverse=True):
        hint = get_dialect_hint(dialect_name)
        if hint:
            # 硬编码的方言中文名映射，用于在提示里显示中文名称
            dialect_cn = {
                "dongbei": "东北话",
                "sichuan": "四川话",
                "beijing": "京片子",
                "shanghai": "上海话",
                "cantonese": "粤语",
            }.get(dialect_name, dialect_name)
            parts.append(
                f"- Characters speaking **{dialect_cn}** ({detected[dialect_name]} markers found): "
                f"{hint}"
            )

    if not parts:
        return ""

    header = (
        "## DIALECT CONTEXT\n"
        "The source text contains regional Chinese dialects. "
        "To preserve character voice, translate dialect speech using the "
        "mapped English dialect. Maintain consistency: the same character "
        "should use the same dialect throughout.\n\n"
    )
    return header + "\n".join(parts)
