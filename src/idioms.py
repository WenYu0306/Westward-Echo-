"""Chinese idiom (成语/俗语) detection and translation hints.

Chinese web novels drop idioms every few paragraphs -- 画蛇添足,
此地无银三百两, 掩耳盗铃, 塞翁失马, etc. The LLM sometimes translates
them literally (making nonsense) when it should convey the meaning.

This module scans chapter text for common Chinese idioms and generates
context hints so the LLM can use natural English equivalents instead of
producing word-for-word gibberish.

【中文·这个检测器检测什么】
检测中文原文里的**成语和俗语**。成语是中文里固定的四字（或多字）短语，
每个都有典故和引申义，比如"画蛇添足"不是说真的在画蛇加脚，
而是说"多此一举、弄巧成拙"。

【中文·为什么需要它】
AI 经常把成语逐字直译，闹出笑话。举几个具体例子：
  · "画蛇添足" → AI 可能译成 "draw a snake and add feet"
    （英文读者完全看不懂），正确应该是 "gilding the lily"。
  · "对牛弹琴" → AI 可能译成 "play piano to a cow"，
    正确应该是 "casting pearls before swine"。
  · "破釜沉舟" → AI 可能译成 "break pots and sink boats"，
    正确应该是 "burning your boats / point of no return"。
这个检测器找到成语后，给 AI 一份正确的英文对应说法，避免逐字翻译的灾难。

【中文·它在流程里的位置】
在 READ 阶段被 `_build_context_signals()` 调用（见 src/agent/nodes/read.py）。
扫描原文，把发现的成语和对应英文提示拼成一段文本，作为 READ 的起步线索。

【中文·输出格式】
返回一段 Markdown 格式的提示文本，例如：
    ## IDIOM CONTEXT
    - **画蛇添足** → gilding the lily (NOT a literal translation)
    - **破釜沉舟** → burning your boats (NOT a literal translation)
如果没有发现任何成语，返回空字符串。

【中文·关于 COMMON_IDIOMS 数据表】
下面的 COMMON_IDIOMS 字典是一张几百条的成语对照表，属于业务数据。
只在文件头和数据块开头说明用途，不给每条成语单独写注释。
⚠️ 绝对不要修改这张表的内容。
"""

from __future__ import annotations

# ── Common idioms that appear frequently in web novels ─────────────────
# Mapping: Chinese idiom → English meaning / equivalent
# 【中文】成语对照表：左边是中文成语，右边是对应的英文含义或等价表达。
# 按语义分类排列（行为类、情境类、性格类、关系类、计谋类）。
# ⚠️ 这是业务数据，改动会直接影响翻译质量，不要修改内容。

COMMON_IDIOMS: dict[str, str] = {
    # Actions / behavior
    "画蛇添足": "gilding the lily / over-egging it (ruining something by adding too much)",
    "此地无银三百两": "the lady doth protest too much / a guilty person gives themselves away",
    "掩耳盗铃": "burying your head in the sand / self-deception",
    "对牛弹琴": "casting pearls before swine / talking to a brick wall",
    "亡羊补牢": "better late than never / closing the barn door after the horse has bolted",
    "守株待兔": "waiting for luck to fall in your lap / passive wishful thinking",
    "叶公好龙": "professing love for something you actually fear",
    "纸上谈兵": "all talk, no action / armchair general",
    "破釜沉舟": "burning your boats / point of no return / all-in commitment",
    "过河拆桥": "burning bridges / abandoning your helpers once you've succeeded",
    "落井下石": "kicking someone when they're down",
    "火上加油": "adding fuel to the fire",
    "趁火打劫": "looting during a fire / taking advantage of chaos",
    "抛砖引玉": "throwing out a brick to attract jade / a modest opening to invite better ideas",
    "推波助澜": "fanning the flames / making things worse",
    "小题大做": "making a mountain out of a molehill",
    "画龙点睛": "the finishing touch that brings something to life",
    "虎头蛇尾": "starting strong but fizzling out / anticlimactic",
    "全力以赴": "giving it your all / going all out",
    "披荆斩棘": "hacking through thorns and brambles / overcoming great obstacles",
    "赴汤蹈火": "go through fire and water / willing to risk everything",
    "废寝忘食": "so absorbed you forget to eat or sleep",
    "孤注一掷": "going all-in / a desperate final gamble",
    "雪中送炭": "timely help / help when it's most needed",
    "锦上添花": "icing on the cake / making something already good even better",

    # Situations / states
    "塞翁失马": "a blessing in disguise / every cloud has a silver lining",
    "井底之蛙": "a frog in a well / someone with a narrow worldview",
    "一箭双雕": "kill two birds with one stone",
    "一举两得": "kill two birds with one stone",
    "杯弓蛇影": "seeing danger where there is none / paranoia",
    "四面楚歌": "surrounded by enemies / completely isolated",
    "胸有成竹": "having a well-thought-out plan / confident in one's approach",
    "骑虎难下": "riding a tiger and can't dismount / stuck in a dangerous situation",
    "左右为难": "between a rock and a hard place",
    "进退两难": "damned if you do, damned if you don't",
    "千钧一发": "hanging by a thread / a very close call",
    "刻不容缓": "can't afford to delay / extremely urgent",
    "迫不及待": "can't wait / itching to do something",

    # Character / personality
    "笑里藏刀": "a wolf in sheep's clothing / hiding malice behind a smile",
    "口蜜腹剑": "honey-tongued with a dagger behind the back",
    "见异思迁": "fickle / constantly chasing the next shiny thing",
    "耳濡目染": "picked up through constant exposure / learned by osmosis",
    "百折不挠": "indomitable / never give up despite countless setbacks",
    "小心翼翼": "with extreme care / walking on eggshells",
    "得意洋洋": "smug / puffed up with pride",
    "心花怒放": "heart bursting with joy / over the moon",
    "怒发冲冠": "so angry your hair lifts your hat / absolutely furious",
    "鹤立鸡群": "a crane among chickens / outstanding in a mediocre crowd",

    # Relationships / romance
    "门当户对": "well-matched in social status (marriage)",
    "一见钟情": "love at first sight",
    "日久生情": "love grows with time",
    "青梅竹马": "childhood sweethearts",

    # Deception / conflict
    "掩人耳目": "pulling the wool over people's eyes",
    "浑水摸鱼": "fishing in troubled waters / profiting from chaos",
    "声东击西": "a diversionary tactic / feint",
    "欲盖弥彰": "the more you try to hide, the more you reveal",
    "自相矛盾": "contradicting yourself / self-contradictory",
    "弄巧成拙": "outsmarting yourself / trying to be clever but making things worse",
    "打草惊蛇": "tipping off the enemy / alerting the prey",
    "班门弄斧": "teaching your grandmother to suck eggs / showing off before an expert",
    "东施效颦": "a poor imitation / blindly copying someone and looking worse",
    "螳臂当车": "a futile resistance / tilting at windmills",
}


def detect_idioms(text: str) -> list[tuple[str, str]]:
    """Return [(idiom, meaning)] for idioms found in *text*.

    Idioms are returned in order of first appearance. Each idiom
    is only reported once (deduplicated).

    【中文】扫描原文，找出所有出现在对照表里的成语。
    · 参数 text：要扫描的中文原文（通常是一整章）。
    · 返回：一个列表，每项是 (成语, 英文含义) 的元组。
      按成语在原文中首次出现的顺序排列，每个成语只报告一次。
    · 为什么按长度降序匹配：有些短成语是长成语的子串，
      比如"一举"可能出现在"一举两得"里。先匹配长的，
      避免把"一举两得"误拆成"一举"+"两得"两个碎片。
    · 为什么最后按位置排序：匹配时是按长度排的，但给 AI 的提示
      应该按阅读顺序排，这样 AI 处理时跟原文节奏一致。
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    # 按长度降序排列成语，长的优先匹配，避免子串误匹配
    for idiom in sorted(COMMON_IDIOMS, key=len, reverse=True):
        # Sort by length descending so longer idioms (e.g. multi-character)
        # are matched before shorter substrings
        if idiom in text and idiom not in seen:
            found.append((idiom, COMMON_IDIOMS[idiom]))
            seen.add(idiom)

    # 按在原文中的首次出现位置排序，保持阅读顺序
    def _first_pos(entry: tuple[str, str]) -> int:
        return text.index(entry[0])

    found.sort(key=_first_pos)
    return found


def build_idiom_context(text: str) -> str:
    """Build context block for the translation prompt.

    Returns an empty string when no known idioms are found in *text*.
    Otherwise returns a hint block that can be prepended to the user
    prompt so the LLM translates idioms by meaning, not literally.

    【中文】把检测到的成语整理成一段格式化的提示文本，准备塞进翻译提示词里。
    · 参数 text：要扫描的中文原文。
    · 返回：Markdown 格式的提示块；没发现成语则返回空字符串 ""。
    · 为什么存在：成语是最容易逐字翻译出错的语言现象之一，
      必须在提示词里明确告诉 AI "这些成语要用意译，不要直译"。
    · 坑：提示文本里特意加了 "(NOT a literal word-for-word translation)"
      这句话，因为 AI 有时候即使看到了正确译法，还是会忍不住直译。
      反复强调才能压住它的直译倾向。
    """
    found = detect_idioms(text)
    if not found:
        return ""

    lines = [
        "## IDIOM CONTEXT",
        "This chapter contains Chinese idioms. DO NOT translate literally. "
        "Use the English equivalent or convey the meaning naturally:",
        "",
    ]
    for idiom, meaning in found:
        lines.append(f"- **{idiom}** → {meaning} (NOT a literal word-for-word translation)")

    return "\n".join(lines)
