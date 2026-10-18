"""Onomatopoeia detection and English equivalents for Chinese web novels.

Maps common Chinese sound words to natural English equivalents so the LLM
can choose context-appropriate translations. Injected as a context hint
alongside the dialect context during translation.

【中文·这个检测器检测什么】
检测中文原文里的**拟声词**（也叫"象声词"），就是那些模仿声音的词，
比如"轰隆隆""咔嚓""哗啦啦"。

【中文·为什么需要它】
中文和英文的拟声词体系完全不同。中文写"轰隆隆"，如果 AI 逐字翻译成
"hong long long"，英文读者完全看不懂。英文里打雷的声音是 "rumble"，
爆炸的声音是 "boom"——同样是"轰隆隆"，场景不同译法就不同。
这个检测器找到拟声词后，给 AI 一份"候选译法清单"，让它根据上下文选最合适的。

【中文·它在流程里的位置】
在 READ 阶段被 `_build_context_signals()` 调用（见 src/agent/nodes/read.py）。
检测器扫描中文原文，把发现的拟声词和对应英文提示拼成一段文本，
作为 READ 角色的"起步线索"之一。READ 读了原文后再判断是否采纳。

【中文·输出格式】
返回一段 Markdown 格式的提示文本，例如：
    ## ONOMATOPEEIA CONTEXT
    - **轰隆隆** (appears 3x): rumble / boom (thunder=rumble, explosion=boom)
    - **咔嚓**: crack / snap / click (break=crack, photo=click)
如果没有发现任何拟声词，返回空字符串。
"""


# 【中文·拟声词对照表】
# 一本查询手册：左边是中文拟声词，右边是对应的英文译法和场景说明。
# 每个词条里用括号标注了不同场景下的选择，比如"哗啦啦"在水边是 splash，
# 风吹树叶时是 rustle。AI 根据上下文从中挑最贴切的。
# ⚠️ 这是业务数据，改动会直接影响翻译质量，不要修改内容。
ONOMATOPOEIA_MAP: dict[str, str] = {
    # Nature sounds
    "哗啦啦": "whoosh / splash / rustle "
    "(choose by context: water=splash, wind=whoosh, leaves=rustle)",
    "轰隆隆": "rumble / boom (thunder=rumble, explosion=boom)",
    "呼呼": "whoosh / howl (wind=howl, breathing=wheeze)",
    "淅淅沥沥": "pitter-patter (rain)",
    "沙沙": "rustle (leaves, fabric)",
    "滴滴答答": "drip-drop / tick-tock (water=drip, clock=tick)",

    # Action sounds
    "啪": "slap / crack / snap (hit=slap, break=crack, finger=snap)",
    "砰": "bang / thud (door=bang, bodyfall=thud)",
    "咔嚓": "crack / snap / click (break=crack, photo=click)",
    "嗖": "whoosh / zip (fast movement, like an arrow or person dashing)",
    "咣当": "clang / crash (metal=clang, fall=crash)",
    "叮": "ting / ping (small bell=ting, notification=ping)",

    # Speech/body sounds
    "噗": "pfft (disbelief) / splurt (liquid) / thump (impact)",
    "咳咳": "ahem / cough cough",
    "咕噜": "gurgle / rumble (stomach=growl, water=gurgle)",
    "吧唧": "smack / chomp (eating sounds)",
    "嘎吱": "creak (door, floorboard)",
    "咯噔": "thump (heartbeat skip, sudden dread)",
}


def detect_onomatopoeia(text: str) -> list[str]:
    """Return list of Chinese onomatopoeia found in text.

    Each entry is the matched Chinese sound word. Duplicates are preserved
    (each occurrence is listed separately) so the caller can gauge frequency.

    【中文】扫描整段文字，找出所有出现在对照表里的拟声词。
    · 参数 text：要扫描的中文原文（通常是一整章）。
    · 返回：一个列表，里面是找到的拟声词。同一个词出现多次就列多次，
      这样下游能知道哪个声音在这章里特别频繁（比如打斗场面"砰"出现 20 次）。
    · 为什么保留重复：频率信息帮助 AI 判断这个声音是不是本章的重要音效，
      如果只出现一次可能是偶然的，出现十几次就需要认真对待。
    """
    found: list[str] = []
    for sound in ONOMATOPOEIA_MAP:
        # 统计每个拟声词在原文中出现的次数，
        # 出现 N 次就往结果列表里加 N 个副本
        count = text.count(sound)
        found.extend([sound] * count)
    return found


def build_onomatopoeia_context(text: str) -> str:
    """Build context hints for the LLM about onomatopoeia in the source text.

    Returns an empty string if no known onomatopoeia are found.

    【中文】把检测到的拟声词整理成一段格式化的提示文本，准备塞进翻译提示词里。
    · 参数 text：要扫描的中文原文。
    · 返回：Markdown 格式的提示块；没发现拟声词则返回空字符串 ""。
    · 为什么存在：AI 不知道中文拟声词该怎么变成英文，这段提示告诉它
      "这章里有这些声音词，英文可以这样表达"，避免 AI 瞎编或逐字音译。
    · 坑：去重是为了显示简洁，但计数信息保留了（显示 "appears Nx"），
      让 AI 知道哪些声音是高频的。排序按出现次数从高到低，最重要的排前面。
    """
    found = detect_onomatopoeia(text)
    if not found:
        return ""

    # 先去重再按出现次数降序排列，让最常见的拟声词排在最前面
    unique = sorted(set(found), key=lambda s: -found.count(s))
    lines = []
    for sound in unique:
        count = found.count(sound)
        hint = ONOMATOPOEIA_MAP[sound]
        # 出现多次时标注次数，帮助 AI 判断重要程度
        if count > 1:
            lines.append(f"- **{sound}** (appears {count}x): {hint}")
        else:
            lines.append(f"- **{sound}**: {hint}")

    header = (
        "## ONOMATOPOEIA CONTEXT\n"
        "The source text contains Chinese sound words (onomatopoeia). "
        "Use the suggested English equivalents, choosing the variant that "
        "best fits the scene context.\n\n"
    )
    return header + "\n".join(lines)
