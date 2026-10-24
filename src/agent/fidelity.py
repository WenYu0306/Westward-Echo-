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

【中文·这个文件是干什么的】
文化忠实度检查器——用纯规则（不调用 AI、不花钱）核对一件事：
**WRITE 有没有遵守 READ 定下的术语译法**。

【中文·为什么需要这道检查】
冷读者（READBACK）看不到中文原文，所以它压根没能力发现这类问题：
  · "忘忧谷"被 WRITE 翻成 Wangyou Valley（拼音），冷读者读着毫无违和，
    但中文读者一看就知道丢了"忘记忧愁"这层意思。
  · "聋婆婆"被翻成 Lóng Pópo（带声调拼音），而不是 Deaf Granny。
这些是"有含义的名字被偷懒音译"的典型场景，必须靠规则补一道。

【中文·它在整个流程里的位置】
被 graph.py 的 _post_process() 在流水线跑完后调用，属于"收尾质检"。
发现问题不拦着放行，而是记警告日志 + 追加到质量问题清单里，便于事后诊断。
同时，_post_process 还用本文件的 is_single_rendering 和 is_valid_rendering
做术语入库前的筛选关卡（见 graph.py ⑥-b 和 ⑥-c）。

【中文·核心概念：三个公开函数各干什么】
  check_cultural_fidelity  核对 WRITE 是否遵守了 READ 定的译法（主入口）
  is_single_rendering      判断一个译法是不是"单一确定的词"（而非多选或解释）
  is_valid_rendering       判断一个译法是否合格（不含中文/拼音/乱码等）

【中文·v1 的检查范围】
只查 terminology_decisions（术语决策），且只查短译名（人名、称谓等）。
长描述性译法和 cultural_gaps（画面缺口）的落地验证不在 v1 范围内——
那些是叙事层面的问题，没法用一个简单的字符串匹配来验证。
"""

import re


def _candidates(proposed_en: str) -> list[str]:
    """Split a proposed rendering into candidate strings to search for.

    A rendering may offer alternatives ("Alpha CEO / domineering CEO"), in
    which case any one candidate appearing in the output counts as honored.

    【中文·一句话说明】把一个可能包含多个备选译法的字符串拆成列表。

    【参数】proposed_en —— READ 给出的英文译法，可能包含 "/" 分隔的备选项，
    例如 "Alpha CEO / domineering CEO"

    【返回】拆分后的候选列表，每个元素已去除首尾空格。
    只要其中任何一个出现在 WRITE 的输出里，就算"遵守了译法"。

    【中文·为什么允许备选】READ 有时不确定哪个译法更好，
    会给两个选项让 WRITE 挑。这种情况下 WRITE 用哪个都算对。
    """
    return [c.strip() for c in proposed_en.split("/") if c.strip()]


# 【中文·中文数字对照表】把中文数字字符映射成阿拉伯数字。
# "两"也映射到 2，因为口语里"两个"="二个"。
_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
# 【中文·所有可能出现在中文数字串里的字符】用于正则匹配
_CN_NUMERAL_CHARS = "零〇一二三四五六七八九两十百千万亿"


def _cn_numeral_to_int(run: str) -> int:
    """Parse a Chinese numeral run. Handles both readings.

    Digit-by-digit (no place marker): 三零一 -> 301, 六 -> 6
    With place markers:               二十三 -> 23, 二百零一 -> 201

    The digit-by-digit form matters: 三零一度 → "301 Degrees" is a real
    glossary entry, and parsing 三零一 as 1 produced a false "invented number"
    verdict on it.

    【中文·一句话说明】把一段中文数字字符串转成整数。

    【参数】run —— 一段连续的中文数字字符，如 "二十三" 或 "三零一"

    【返回】对应的整数值

    【中文·为什么要支持两种读法】
    中文数字有两种写法：
      ① 位值制："二十三" = 2×10 + 3 = 23（有"十百千"等位标记）
      ② 逐位读："三零一" = 301（没有位标记，像报电话号码一样逐位念）
    如果只支持第 ① 种，"三零一"会被解析成 1（只取最后一个数字），
    然后 _invents_number 会误判"301 Degrees"里的 301 是"凭空编造的数字"，
    导致这条正确的术语被关卡删掉。这在《间客》第一章就出过事故。
    """
    # 先判断有没有位标记（十百千万亿）
    if not any(c in "十百千万亿" for c in run):
        # 没有位标记 → 逐位读：直接把每个数字字符拼起来
        digits = "".join(
            str(_CN_DIGITS[c]) for c in run if c in _CN_DIGITS
        )
        return int(digits) if digits else 0

    # 有位标记 → 按位值制解析
    # total 是最终结果，section 是当前节（万以下/亿以下），number 是当前数字
    total = section = number = 0
    for ch in run:
        if ch in _CN_DIGITS:
            # 遇到数字字符，暂存
            number = _CN_DIGITS[ch]
        elif ch == "十":
            # "十"前面的数字 ×10；如果前面没数字（如"十五"），默认是 1
            section += (number or 1) * 10
            number = 0
        elif ch == "百":
            section += (number or 1) * 100
            number = 0
        elif ch == "千":
            section += (number or 1) * 1000
            number = 0
        elif ch == "万":
            # "万"是一个大节边界：之前的累积 ×10000
            total += (section + number) * 10000
            section = number = 0
        elif ch == "亿":
            # "亿"是更大的节边界：之前的累积 ×100000000
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

    【中文·一句话说明】找出一个字符串里所有的数字（阿拉伯数字和中文数字都算）。

    【参数】s —— 任意字符串（中文或英文）

    【返回】该字符串中出现的所有数值的集合（set），如 {23, 2024}

    【中文·为什么不能只查阿拉伯数字】
    早先的实现只看字符串里有没有 0-9 字符。问题是中文经常用汉字写数字：
    "联邦二十三频道"里没有阿拉伯数字，但它的正确英译 "Federal Channel 23"
    里有 23。旧逻辑会认为 23 是"凭空编造的"，把这条术语删掉。
    这不是边缘情况——《间客》第一章的核心台词就是这个，
    术语被删后后面几百章都用不了这个词。
    """
    out: set[int] = set()
    # 第一步：找所有阿拉伯数字串（如 "23"、"2024"）
    for m in re.finditer(r"\d+", s or ""):
        out.add(int(m.group()))
    # 第二步：找所有中文数字串（如 "二十三"、"三零一"），转成整数
    for m in re.finditer(f"[{_CN_NUMERAL_CHARS}]+", s or ""):
        value = _cn_numeral_to_int(m.group())
        if value:
            out.add(value)
    return out


def _invents_number(term_cn: str, rendering: str) -> bool:
    """True when *rendering* states a number the source term does not contain.

    Still catches genuine invention (a year the source never had) while no
    longer mistaking 二十三 for an invented 23.

    【中文·一句话说明】检查英文译法里有没有"凭空多出"的数字。

    【参数】
      term_cn   中文原词
      rendering 英文译法

    【返回】True = 译法里有中文原词不包含的数字（可能是编造的）

    【中文·判断方法】用集合差运算：
    英文里的数字集合 - 中文里的数字集合 = 多出来的数字。
    如果差集非空，说明英文里出现了中文没有的数字。
    例：中文"联邦二十三频道"含 {23}，英文"Channel 24"含 {24}，
    差集 = {24}，判定为编造。
    """
    return bool(_numbers_in(rendering) - _numbers_in(term_cn))


# A rendering shaped like a designation rather than a name: "M3", "B12", "7X".
# 【中文·代号模式】匹配像 "M3""B12""7X" 这种"字母+数字"的纯代号。
# 这类字符串不像人名或术语，更像是型号编号。
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

    【中文·一句话说明】判断一个译法是不是"纯代号"格式（如 M3、B12）。

    【参数】rendering —— 英文译法字符串

    【返回】True = 这是一个纯代号，不是正常的人名或术语

    【中文·为什么单独设这条规则】
    早先用"数字检查"顺带拦截代号，但逻辑不对：
    "王三"→"M3"的问题不是"多了数字"（三=3 是对的），
    而是"M3"根本不像个人名，像个型号。
    这两类问题应该分开判断。

    【中文·这个正则有多窄】
    只匹配"1~3 个字母 + 纯数字"或"纯数字 + 1~3 个字母"。
    在 1936 条真实术语上测试，命中数为 0——正常的含数字译法都有词结构，
    如 "Refuge Base 7""Nemesis-2""301 Degrees"，不会被误伤。
    """
    return bool(_CODE_LIKE.match((rendering or "").strip()))


def _is_cut(proposed_en: str) -> bool:
    """READ explicitly decided this term needs no English rendering.

    e.g. ``(cut — no English rendering needed)``. Such a decision is not a
    failure to honor — it is an instruction to omit — so it must be skipped.

    【中文·一句话说明】判断 READ 是否决定这个词"不需要翻译"。

    【参数】proposed_en —— READ 给出的英文译法

    【返回】True = READ 明确说这个词不用翻，检查时应跳过

    【中文·为什么会有"不翻译"的决定】
    有些中文词在英文语境里完全不需要出现（比如某些语气助词、
    只在中文里有意义的谐音梗）。READ 会在 proposed_en 里标注
    "(cut — no English rendering needed)" 之类的说明。
    这种情况不算"WRITE 没遵守译法"，应该跳过检查。
    """
    low = proposed_en.lower()
    return (
        "(cut" in low
        or "no english rendering" in low
        or "no translation" in low
    )


def _is_explanatory(rendering: str) -> bool:
    """True if a rendering is a gloss rather than a single usable token.

    A term cached in the glossary is re-injected as "must use" for every
    later chapter, so only a token WRITE can drop into prose verbatim may be
    cached. These are glosses, not tokens:

      "Chuma Shaman / spirit medium"                  → alternatives
      "Qingfeng — the spirits of the violently dead"  → em-dash gloss
      "Ghost Festival (chapter title: 'Ghost Festival')" → parenthetical gloss
      "Ghost Festival: the night the dead walk"       → colon gloss

    【中文·一句话说明】判断一个译法是"解释性文字"还是"可以直接塞进句子的词"。

    【参数】rendering —— 英文译法字符串

    【返回】True = 这是解释/注释，不是一个可以直接使用的术语词

    【中文·为什么要区分】
    术语库里的词会被当作"必须使用"的铁律注入后续每一章的提示词。
    如果存进去的是一句解释（如 "the spirits of the violently dead"），
    WRITE 没法把它当一个词塞进句子里，反而会造成混乱。
    只有"Deaf Granny"这种可以直接放进散文的词才适合缓存。

    【中文·判断依据】包含以下任一标点就视为解释性文字：
      /    表示备选项（"A / B"）
      — –  破折号，后面跟解释
      ()   括号里的补充说明
      :    冒号后面的展开解释
      " - " 空格-连字符-空格，也是解释性分隔
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

    【中文·一句话说明】判断一个译法是不是"单一、干净、可直接使用的词"。

    【参数】proposed_en —— READ 给出的英文译法

    【返回】True = 这是一个干净的单一译法；False = 包含备选或解释

    【中文·在哪里被调用】graph.py 的 _post_process ⑥-b 忠实度关卡：
    当 READ 和 WRITE 给了不同译法时，只有 READ 给的是"单一确定译法"
    才用它覆盖 WRITE 的版本。如果 READ 自己给了"A / B"这种多选，
    就没有权威答案可依据，这时保留 WRITE 的版本。

    【中文·判断逻辑】非空 且 不是解释性文字 → 就是单一译法。
    """
    return bool(proposed_en.strip()) and not _is_explanatory(proposed_en)


# Tone-marked Latin vowels — the tell of a pinyin transliteration (Lóng Pópo).
# 【中文·带声调的拉丁元音集合】这些字符是拼音的标志。
# 正式英文出版物不使用带声调的拼音（如 Lóng Pópo），
# 所以看到这些字符就说明 WRITE 偷懒用了拼音而不是意译。
_TONE_MARKED = frozenset("áàǎāéèěēíìǐīóòǒōúùǔūǘǜǚǖü")


def is_valid_rendering(term_cn: str, term_en: str) -> tuple[bool, str]:
    """Hard rule-check for whether a term's English rendering is acceptable.

    This is the deterministic gate (unlike the LLM review node): a rendering
    that fails here is *obviously* wrong and must not be cached into the
    glossary, where it would be locked in as "must use" for the whole book.

    Returns (ok, reason). ``ok`` is False when the rendering is empty, contains
    Chinese, contains tone-marked pinyin, contains a digit that the source
    term doesn't have, or is a gloss/explanation rather than a single token.

    【中文·一句话说明】用硬性规则判断一个术语译法是否合格，不合格就不能存进术语库。

    【参数】
      term_cn  中文原词
      term_en  英文译法

    【返回】(是否合格, 不合格的原因)。合格时原因是空字符串。

    【中文·为什么这道关卡至关重要】
    术语库里的东西会变成全书强制遵守的铁律。存一条垃圾进去，
    代价是污染后面所有章节；丢掉它，最多是这一章少了个术语，
    下一章遇到还能重新定。两害相权，丢弃安全得多。

    【中文·六条规则逐条解释】
    ① 空值检查：模型没给出译法，不能存空的进去
    ② 含中文：等于没翻，存进去会让后续章节反复"翻译"同一个词
    ③ 带声调拼音：如 Jīndān，正式出版物不用这种写法
    ④ 凭空多出数字：如中文没有年份但英文写了 "Year 2024"
    ⑤ 纯代号格式：如 "M3"，不像人名或术语
    ⑥ 解释性文字：如 "a person who cultivates immortality"，
       这是一句话不是一个词，没法塞进句子里用
    """
    en = term_en.strip()
    # ① 空值
    if not en:
        return False, "empty rendering"
    # ② 含中文字符（Unicode 范围 一~鿿 覆盖了 CJK 统一汉字基本区）
    if any("一" <= ch <= "鿿" for ch in en):
        return False, "contains Chinese"
    # ③ 带声调拼音
    if any(ch in _TONE_MARKED for ch in en):
        return False, "contains tone-marked pinyin"
    # ④ 凭空多出数字
    if _invents_number(term_cn, en):
        return False, "a number appeared that the source doesn't have"
    # ⑤ 纯代号格式
    if _is_code_like(en):
        return False, "code-like designation, not a name"
    # ⑥ 解释性文字
    if _is_explanatory(en):
        return False, "explanatory rendering, not a single token"
    return True, ""


def check_cultural_fidelity(
    read_analysis: dict, translated_text: str,
) -> list[str]:
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

    【中文·一句话说明】核对 WRITE 的英文输出是否遵守了 READ 定下的术语译法，
    返回违规清单（空列表 = 全部合规）。

    【参数】
      read_analysis    READ 角色的结构化分析结果，
                       里面的 terminology_decisions 记录了每个术语的译法决定
      translated_text  WRITE 角色输出的英文成品

    【返回】违规描述字符串列表。每条说明"哪个词、READ 定了什么译法、
    WRITE 没用"。空列表表示全部合规。

    【中文·这个函数不花钱】纯字符串匹配，不调用任何 AI。
    在 graph.py 的 _post_process 里被调用，属于收尾质检。

    【中文·检查流程】
    ① 遍历 READ 定下的每条术语决策
    ② 跳过"不需要翻译"的条目（_is_cut）
    ③ 检查译法本身是否有问题（凭空多数字、纯代号）——有问题直接报错
    ④ 跳过地名/功法/文化词/物品/年代类别——这些允许等价替换
    ⑤ 跳过超长译法（>50 字符或含句号）——这些是解释不是词
    ⑥ 在英文输出里搜索译法的每个候选项，都没找到就报违规
    """
    # 输入为空就直接返回，没什么可查的
    if not read_analysis or not translated_text:
        return []

    decisions = read_analysis.get("terminology_decisions", [])
    if not decisions:
        return []

    failures = []
    # 转小写用于大小写不敏感的搜索
    low_text = translated_text.lower()
    for td in decisions:
        term_cn = td.get("term_cn", "")
        proposed_en = td.get("proposed_en", "")
        # 中文或英文为空就跳过，数据不完整没法查
        if not term_cn or not proposed_en:
            continue

        # READ decided this term needs no rendering — nothing to check.
        # 【中文·跳过"不需要翻译"的条目】
        if _is_cut(proposed_en):
            continue

        # Suspicious rendering: the English states a number the source term
        # does not (e.g. 联邦23频道 → "Channel 24"), or is a bare designation
        # rather than a name (e.g. 王三 → "M3"). Wrong regardless of whether
        # WRITE used it — flag it outright.
        #
        # 【中文·先查译法本身有没有问题】
        # 不管 WRITE 用没用这个译法，如果 READ 定的译法本身就有毛病
        # （凭空多了数字、或者是个纯代号），都要报出来。
        # 这种问题出在 READ 阶段，不是 WRITE 的锅，但同样需要记录。
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
        #
        # 【中文·只查人名和称谓，跳过其他类别】
        # 地名、功法名、文化词、物品名、年代这些类别，
        # WRITE 有权用等价表达替换（比如"京城"翻成 "the capital" 而非 "Jingcheng"）。
        # 对这些类别做精确匹配会产生大量误报。
        # 只有人名和称谓必须一字不差地统一（主角名字不能一章一个样）。
        category = td.get("category", "")
        if category in ("location", "technique", "culture", "item", "era"):
            continue

        # Skip long/descriptive renderings — those are explanations, not a
        # single token the WRITE output can be checked against.
        #
        # 【中文·跳过超长译法】超过 50 字符或包含句号的"译名"，
        # 多半是模型跑偏吐出了一整句解释，不是能塞进句子里的词。
        # 对这种东西做字符串搜索没有意义。
        if len(proposed_en) > 50 or ". " in proposed_en:
            continue

        # 【中文·在英文输出里搜索译法】
        # 把 proposed_en 按 "/" 拆成候选列表，只要有一个出现在输出里就算合规。
        # 用 lower() 做大小写不敏感匹配，避免 "Deaf Granny" vs "deaf granny" 误报。
        candidates = _candidates(proposed_en)
        if not any(c.lower() in low_text for c in candidates):
            failures.append(
                f"READ decided '{term_cn}' → '{proposed_en}' "
                "but WRITE output did not use it"
            )

    return failures
