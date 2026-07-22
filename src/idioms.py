"""Chinese idiom (成语/俗语) detection and translation hints.

Chinese web novels drop idioms every few paragraphs -- 画蛇添足,
此地无银三百两, 掩耳盗铃, 塞翁失马, etc. The LLM sometimes translates
them literally (making nonsense) when it should convey the meaning.

This module scans chapter text for common Chinese idioms and generates
context hints so the LLM can use natural English equivalents instead of
producing word-for-word gibberish.
"""

from __future__ import annotations

from typing import Optional

# ── Common idioms that appear frequently in web novels ─────────────────
# Mapping: Chinese idiom → English meaning / equivalent

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
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    # Scan left-to-right, recording first occurrence
    for idiom in sorted(COMMON_IDIOMS, key=len, reverse=True):
        # Sort by length descending so longer idioms (e.g. multi-character)
        # are matched before shorter substrings
        if idiom in text and idiom not in seen:
            found.append((idiom, COMMON_IDIOMS[idiom]))
            seen.add(idiom)

    # Sort by position in text to preserve reading order
    def _first_pos(entry: tuple[str, str]) -> int:
        return text.index(entry[0])

    found.sort(key=_first_pos)
    return found


def build_idiom_context(text: str) -> str:
    """Build context block for the translation prompt.

    Returns an empty string when no known idioms are found in *text*.
    Otherwise returns a hint block that can be prepended to the user
    prompt so the LLM translates idioms by meaning, not literally.
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
