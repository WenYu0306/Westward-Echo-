"""Load and format cultural adaptation rules from a JSON configuration file.

Rules are stored in ``cultural_rules.json`` at the project root by default.
Override the path via the ``CULTURAL_RULES_PATH`` environment variable.

Structure::

    {
      "genres": {
        "<genre>": {
          "<lang>": {
            "<source_term>": {"target": "...", "note": "..."}
          }
        }
      },
      "common": {
        "<lang>": {
          "<source_term>": {"target": "...", "note": "..."}
        }
      }
    }

"common" rules apply to ALL genres.  Genre-specific rules override common
entries when the same source term appears in both.

【中文·这个文件是干什么的】
从 `cultural_rules.json`（123KB 的大配置文件）加载**文化改编规则**，
并格式化成可以塞进 AI 提示词的文本。

【中文·什么是文化改编规则】
中文网文里有大量文化专属概念，直译到英文会让读者困惑甚至误解。
比如"霸总"不是 "domineering president"（霸道总裁？），
在英文言情里应该叫 "Alpha CEO"；"筑基"不是 "foundation building"（盖地基？），
在仙侠里是 "Foundation Establishment"（修炼境界名）。
这些规则的集合就是"文化改编规则"——告诉 AI 遇到特定中文词时该怎么处理。

【中文·规则的三层优先级】
规则按范围分三层，后面的覆盖前面的：
  ① common（通用层）—— 适用于所有小说类型的通用规则
  ② genre（类型层）—— 特定小说类型的规则（如仙侠、都市、言情等）
  ③ book（书籍层）—— 某本书独有的专有名词（如《无限恐怖》里的生化危机角色名）
比如"仙"在 common 里可能定义为 "Immortal"，但在 folk_religion 类型里
被覆盖为 "Spirit Guardian"，因为民间宗教里的"仙"跟仙侠里的不一样。

【中文·它在流程里的位置】
在 READ 阶段被 read_node() 调用（见 src/agent/nodes/read.py）。
load_rules() 加载术语级规则，load_fidelity_rules() 加载策略级规则，
然后分别用 format_rules_as_bullets() 和 format_fidelity_for_prompt() 格式化，
塞进 READ 的系统提示词里。

【中文·按语言分的已知缺口】
规则按目标语言分（en-US / es-ES / de / fr），但 fidelity 块（策略级规则）
目前只有 en-US 有内容。非英语语言调用 load_fidelity_rules() 会返回空字典，
format_fidelity_for_prompt() 会返回空字符串。这是已知缺口，不是 bug。

【中文·本文件的主要函数】
  load_rules()              —— 加载术语级规则（三层合并）
  format_rules_as_bullets() —— 把规则格式化成项目符号列表（READ 实际使用的格式）
  format_rules_for_prompt() —— 把规则格式化成 Markdown 表格
  load_fidelity_rules()     —— 加载策略级规则
  format_fidelity_for_prompt() —— 把策略级规则格式化成 Markdown 块
  detect_genre()            —— 根据关键词自动检测小说类型
  list_known_genres()       —— 列出所有已知小说类型
  is_known_genre()          —— 检查某个类型是否有专属规则
"""

import json
import os
from pathlib import Path
from typing import Optional


def _default_rules_path() -> Path:
    """【中文】返回文化规则 JSON 文件的默认路径。
    优先读环境变量 CULTURAL_RULES_PATH，没设就用项目根目录下的 cultural_rules.json。
    · 为什么支持环境变量：测试时可以指向临时文件，不污染生产数据。
    """
    env = os.getenv("CULTURAL_RULES_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "cultural_rules.json"


def _load_raw_data(path: Optional[str] = None) -> dict:
    """【中文】读取并解析 JSON 文件，返回原始字典。
    · 参数 path：可选的文件路径覆盖。None 时用默认路径。
    · 坑：没有缓存机制，每次调用都重新读文件。
      对于大文件（123KB）这可能有点慢，但目前不是瓶颈。
    """
    file_path = Path(path) if path else _default_rules_path()
    with open(file_path, encoding="utf-8") as fh:
        return json.load(fh)  # type: ignore[no-any-return]


def list_known_genres(path: Optional[str] = None) -> list[str]:
    """Return all genre keys defined in cultural_rules.json.

    【中文】列出 JSON 文件里定义的所有小说类型名称。
    · 返回：类型名列表，如 ["scifi", "folk_religion", "xianxia", ...]。
    """
    data = _load_raw_data(path)
    return list(data.get("genres", {}).keys())


def is_known_genre(genre: str, path: Optional[str] = None) -> bool:
    """Check whether a genre has dedicated rules in cultural_rules.json.

    【中文】检查某个小说类型是否在 JSON 文件里有专属规则。
    · 参数 genre：类型名称，如 "xianxia"。
    · 返回：True 表示有专属规则，False 表示没有。
    """
    return genre in list_known_genres(path)


# ── Genre auto-detection ──────────────────────────────────────
# 【中文·小说类型自动检测】
# 通过统计原文中各类型的标志性关键词出现次数，自动判断小说属于哪个类型。
# 比如一篇文章里频繁出现"修真""金丹""元婴""渡劫"，那大概率是仙侠。

# 【中文·类型关键词表】
# 每种小说类型有一组标志性词汇。检测到足够多（≥5 个）这些词就认为匹配。
# ⚠️ 这是业务数据，改动会影响类型识别准确率，不要修改内容。
_GENRE_SIGNALS: dict[str, list[str]] = {
    "scifi": [
        "机甲", "联邦", "帝国", "战舰", "星舰", "太空港", "跃迁", "基因改造",
        "殖民星", "能量罩", "粒子炮", "首都星", "太空站", "星际", "宇宙",
        "机甲师", "联邦军", "帝国军", "外骨骼", "拟态", "虫洞", "曲速", "量子",
    ],
    "folk_religion": [
        "出马", "仙家", "弟马", "堂口", "请神", "上身", "香主", "老仙",
        "胡仙", "黄仙", "柳仙", "灰仙", "白仙", "常仙", "蟒仙",
        "地府", "阎王", "判官", "鬼差", "城隍", "孟婆", "奈何桥",
        "打表", "顶香", "过阴", "走阴", "拘魂", "送祟", "叫魂",
        "扎纸", "纸人", "冥器", "寿衣", "棺材", "抬棺",
    ],
    "xianxia": [
        "修真", "修仙", "金丹", "元婴", "飞升", "渡劫", "法器", "灵石",
        "宗门", "长老", "弟子", "功法", "炼丹", "御剑", "灵兽", "天劫",
        "道侣", "灵脉", "洞府", "阵法", "仙府", "仙尊", "魔尊",
    ],
    "romance_ceo": [
        "霸总", "总裁", "隐婚", "宠妻", "豪门", "白月光", "虐恋",
        "带球跑", "替身", "联姻", "契约结婚", "先婚后爱",
    ],
    # urban was present in cultural_rules.json and is the configured genre for
    # three of the five corpus books (run_novel.py: 无限恐怖, 全职高手番外,
    # 愿你灿烂如阳), but it was missing from _GENRE_SIGNALS — so detect_genre
    # could NEVER return it. Added in clause 11; a test asserts the three genre
    # vocabularies (rules.json / _GENRE_SIGNALS / personas.GENRE_PROFILES) stay
    # in sync. Keywords are urban-fantasy markers (兵王/赘婿/异能子类), chosen
    # to not collide with the xianxia cultivation vocabulary or any existing
    # detect_genre test fixture.
    # 【中文·历史踩坑记录】urban 类型曾经在 JSON 里有规则但在这里没有关键词，
    # 导致 detect_genre 永远无法返回 "urban"。后来补上了这组关键词。
    "urban": [
        "都市", "异能", "觉醒", "隐世", "古武", "内劲", "世家", "风水",
        "兵王", "赘婿", "特种兵", "保镖", "集团", "透视", "鉴宝", "扮猪吃虎",
    ],
}

# Minimum total signal count across all genres to consider it a match
# 【中文】最低信号阈值：某个类型的关键词至少命中 5 次才算匹配。
# 低于 5 次可能是偶然出现，不足以判定类型。
_MIN_GENRE_SIGNALS = 5
# NOTE: a sibling constant `_MIN_UNIQUE_TERMS = 3` used to sit here, documented as
# "minimum unique terms before we report a best guess". Nothing ever read it —
# detect_genre only compares `score >= _MIN_GENRE_SIGNALS` — so the promise in its
# comment was never implemented and mutating 3→4 was provably unkillable. Removed
# rather than registered as equivalent: an unreachable knob is not a behaviour to
# preserve, and keeping it invites the next reader to believe the guard exists.


def detect_genre(text: str) -> tuple[str, int]:
    """Auto-detect the most likely novel genre from keyword frequency.

    Parameters
    ----------
    text : str
        Novel text sample (first few chapters, ~10K-20K chars).

    Returns
    -------
    tuple[str, int]
        ``(genre_key, confidence_score)``.  Returns ``("", 0)`` when no
        genre has enough signal to be confident.  Confidence is the ratio
        of the winner's signal count to the runner-up's.

    【中文】根据关键词频率自动检测小说类型。
    · 参数 text：小说文本样本（通常取前几章，约 1~2 万字）。
    · 返回：(类型名, 置信度分数)。如果没有类型达到阈值，返回 ("", 0)。
      置信度 = 第一名的关键词命中数 / 第二名的命中数。
    · 为什么需要置信度检查：如果第一名和第二名很接近（比值 < 2.0），
      说明信号模糊，不应该自动判定。比如一篇文章既有仙侠元素又有都市元素，
      强行选一个会导致后续用错规则集。
    · 坑：只返回最可能的一个类型，不支持多类型混合。
    """
    scored = {}
    for genre_name, keywords in _GENRE_SIGNALS.items():
        score = sum(text.count(kw) for kw in keywords)
        if score >= _MIN_GENRE_SIGNALS:
            scored[genre_name] = score

    if not scored:
        return ("", 0)

    # Sort by score descending
    ranked = sorted(scored.items(), key=lambda x: x[1], reverse=True)
    winner_name, winner_score = ranked[0]
    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0

    # Confidence: how much more signal the winner has than runner-up
    if runner_up_score > 0 and winner_score / runner_up_score < 2.0:
        # Signals are ambiguous — don't auto-detect
        return ("", 0)

    return (winner_name, winner_score)


def load_rules(target_lang: str = "en-US", genre: str = "romance_ceo",
               path: Optional[str] = None, book_id: str = "") -> dict:
    """Load cultural adaptation rules for a specific language, genre and book.

    Parameters
    ----------
    target_lang : str
        Language-region code, e.g. ``"en-US"``, ``"es-ES"``.
    genre : str
        Genre key, e.g. ``"romance_ceo"``, ``"xianxia"``, ``"urban"``.
    path : str or None
        Override the JSON file path.  Defaults to ``$CULTURAL_RULES_PATH``
        or ``<project_root>/cultural_rules.json``.
    book_id : str
        Optional per-book key.  When given and ``books.<book_id>.<lang>``
        exists, those terms are overlaid last and win over genre terms.
        Omitting it reproduces the previous two-layer behaviour exactly.

    Returns
    -------
    dict
        Merged mapping of ``{source_term: {target: str, note: str}}``.
        Precedence: common < genre-specific < book-specific.

    Why the book layer exists
    -------------------------
    ``genres.urban`` once held 150 terms that were machine-extracted from a
    single book (无限恐怖, an infinite-flow survival story), including
    established Western IP names it crosses over into (生化危机 → Biohazard,
    异形 → Xenomorph, Licker, Red Queen, Desert Eagle). Three corpus books are
    configured ``urban`` — the other two are an esports story and a romance —
    so both were being briefed with Resident Evil terminology, and any
    translation added to that slot would have propagated the mismatch into
    three languages at once. Those terms now live under
    ``books.limitless_horror`` and reach only the book they came from.

    The ``urban`` genre key is deliberately kept (now empty rather than
    removed): ``list_known_genres``/``is_known_genre`` enumerate ``genres``,
    so deleting the key would report urban as an unknown genre and break the
    three-way genre sync invariant.

    【中文】加载指定语言、类型和书籍的文化改编规则。
    · 参数 target_lang：目标语言代码，如 "en-US"、"es-ES"、"de"、"fr"。
    · 参数 genre：小说类型键，如 "romance_ceo"、"xianxia"、"urban"。
    · 参数 path：可选的 JSON 文件路径覆盖。
    · 参数 book_id：可选的书籍 ID。提供时会在最后叠加该书的专属规则。
    · 返回：合并后的规则字典 {中文词: {target: 英文译法, note: 备注}}。
    · 合并顺序：common → genre → book，后者覆盖前者。
    · 为什么有 book 层：历史上 urban 类型下堆了 150 个从《无限恐怖》提取的词，
      包括生化危机系列的角色名。结果另外两本也配成 urban 的书（电竞故事和言情）
      被迫接收了这些完全不相关的术语。现在这些词移到了 books.limitless_horror 下，
      只影响它们来源的那本书。
    """
    data = _load_raw_data(path)

    # Start with common (applies to all genres)
    merged: dict = {}
    common = data.get("common", {}).get(target_lang, {})
    merged.update(common)

    # Overlay genre-specific
    genre_rules = data.get("genres", {}).get(genre, {}).get(target_lang, {})
    merged.update(genre_rules)

    # Overlay book-specific last: a single book's own proper nouns outrank
    # genre-wide guidance.
    if book_id:
        book_rules = data.get("books", {}).get(book_id, {}).get(target_lang, {})
        merged.update(book_rules)

    return merged


# Rendered in place of a translation for source terms that are load-bearing but
# have no vetted rendering in the target language (de/fr: see cultural_rules.json).
# Deliberately NOT a guess at the term — the point of these entries is to flag
# "this needs care", never to supply an unvetted translation the model would
# then lock into the glossary. English on purpose: it is an instruction to the
# model, not text that reaches the reader.
# 【中文·无固定译法占位符】
# 当某个中文词在目标语言里没有经过审核的译法时（德语/法语常见这种情况），
# 用这段文字代替。故意用英文写，因为它是给 AI 的指令，不是给读者看的正文。
# 目的不是猜一个译法，而是标记"这个词需要特别小心处理"。
_NO_TARGET = "(no fixed term — adapt, do not translate literally)"


def _rule_parts(entry) -> tuple[str, str]:
    """Normalise one rule entry to ``(target, note)`` strings.

    Tolerates the two shapes ``load_rules`` can yield: a ``{"target", "note"}``
    dict, or a bare string value (target only).  A dict missing ``target`` —
    the de/fr source-term-list case — returns an empty target.

    【中文】把一条规则条目标准化为 (target, note) 元组。
    · 参数 entry：规则条目，可能是字典 {"target": ..., "note": ...} 或纯字符串。
    · 返回：(目标译法, 备注) 的元组。缺失字段用空字符串填充。
    · 为什么存在：JSON 里的规则有两种格式（完整字典和简写字符串），
      这个函数统一处理，让下游不需要关心格式差异。
    """
    if isinstance(entry, dict):
        target = entry.get("target", "")
        note = entry.get("note", "")
        return (target if isinstance(target, str) else "",
                note if isinstance(note, str) else "")
    return (entry if isinstance(entry, str) else "", "")


def format_rules_for_prompt(rules: dict) -> str:
    """Format cultural rules as a Markdown table for LLM prompt injection.

    Parameters
    ----------
    rules : dict
        ``{source_term: {target, note}}`` as returned by :func:`load_rules`.

    Returns
    -------
    str
        A Markdown table string suitable for splicing into a system or user
        prompt.  The table has columns: 中文 | Adapted | Note.
        Returns an empty string when *rules* is empty.

    Entries may legitimately carry an empty ``target`` — see ``_NO_TARGET``.
    When any entry does, the header drops its "(USE THIS)" instruction, since
    prescribing a translation that does not exist would be a lie to the model.
    Input where every entry has a translation renders byte-identically to the
    pre-de/fr version of this function.

    【中文】把规则格式化成 Markdown 表格，用于塞进提示词。
    · 参数 rules：load_rules() 返回的规则字典。
    · 返回：Markdown 表格字符串；规则为空则返回空字符串 ""。
    · 坑：如果有任何条目的 target 为空（德语/法语的情况），
      表头会从 "Adapted (USE THIS)" 变成 "Adapted"，
      因为没有译法还标"必须用这个"就是在骗 AI。
    """
    if not rules:
        return ""

    parts = {term: _rule_parts(entry) for term, entry in rules.items()}
    prescriptive = all(target for target, _ in parts.values())

    if prescriptive:
        lines = ["| 中文 | Adapted (USE THIS) | Note |",
                 "|------|--------------------|------|"]
    else:
        lines = ["| 中文 | Adapted | Note |",
                 "|------|---------|------|"]

    for cn_term, (target, note) in parts.items():
        lines.append(f"| {cn_term} | {target or _NO_TARGET} | {note} |")

    return "\n".join(lines)


def format_rules_as_bullets(rules: dict) -> str:
    """Format cultural rules as bullet points for term extraction prompts.

    This is the renderer the READ node actually injects (see
    ``agent/nodes/read.py``), so it must handle empty targets too — otherwise
    the de/fr source-term lists would reach the model as bare arrows.

    Parameters
    ----------
    rules : dict
        ``{source_term: {target, note}}`` as returned by :func:`load_rules`.

    Returns
    -------
    str
        Bulleted lines, one per rule, e.g. ``- 霸总 → "Alpha CEO" (note)``.
        Returns an empty string when *rules* is empty.  Input where every
        entry has a translation renders byte-identically to before.

    【中文】把规则格式化成项目符号列表（READ 节点实际使用的格式）。
    · 参数 rules：load_rules() 返回的规则字典。
    · 返回：每行一个规则的文本；规则为空则返回空字符串 ""。
    · 为什么用项目符号而不是表格：READ 节点的提示词空间有限，
      项目符号比表格更紧凑，且更容易被 AI 逐条处理。
    · 坑：target 为空时使用 _NO_TARGET 占位符，避免生成 "- 霸总 → " 这种裸箭头。
    """
    if not rules:
        return ""

    bullets = []
    for cn_term, entry in rules.items():
        target, note = _rule_parts(entry)
        body = f'- {cn_term} → "{target}"' if target else f"- {cn_term} → {_NO_TARGET}"
        if note:
            bullets.append(f"{body} ({note})")
        else:
            bullets.append(body)
    return "\n".join(bullets)


def load_fidelity_rules(target_lang: str = "en-US",
                        path: Optional[str] = None) -> dict:
    """Load strategy-level cultural-fidelity rules for a target language.

    Unlike :func:`load_rules` (term-level mappings), these are category-level
    rules — how to translate a CLASS of terms (names, honorifics, worldview
    terms), not what a specific word means.

    Parameters
    ----------
    target_lang : str
        Language-region code, e.g. ``"en-US"``.
    path : str or None
        Override the JSON file path.

    Returns
    -------
    dict
        ``{category: {rule: str, examples: [{cn, do, why}]}}``, or ``{}`` if
        no fidelity rules are defined for the language.

    【中文】加载策略级的文化忠实度规则。
    · 与 load_rules 的区别：load_rules 是"这个词怎么译"（术语级），
      这里是"这一类词怎么处理"（策略级）。比如"人名要不要音译""敬称怎么处理"。
    · 参数 target_lang：目标语言代码。
    · 返回：规则字典；该语言没有 fidelity 规则时返回空字典 {}。
    · ⚠️ 已知缺口：目前只有 en-US 有 fidelity 规则，其他语言返回空字典。
    """
    data = _load_raw_data(path)
    return data.get("fidelity", {}).get(target_lang, {})


def format_fidelity_for_prompt(rules: dict) -> str:
    """Format fidelity rules as a compact prompt block for the READ agent.

    Parameters
    ----------
    rules : dict
        ``{category: {rule, examples}}`` as returned by
        :func:`load_fidelity_rules`.

    Returns
    -------
    str
        A markdown block with one section per category: the rule plus one
        worked example. Returns ``""`` when *rules* is empty.

    【中文】把策略级规则格式化成紧凑的 Markdown 块，供 READ 使用。
    · 参数 rules：load_fidelity_rules() 返回的规则字典。
    · 返回：每个类别一个小节（标题 + 规则描述 + 一个示例）；
      规则为空则返回空字符串 ""。
    · 为什么只取第一个示例（[:1]）：提示词空间有限，一个示例足以说明规则，
      多了会挤占其他内容的空间。
    """
    if not rules:
        return ""

    sections = []
    for category, entry in rules.items():
        rule = entry.get("rule", "")
        if not rule:
            continue
        title = category.replace("_", " ").title()
        lines = [f"### {title}", rule]
        for ex in entry.get("examples", [])[:1]:
            cn = ex.get("cn", "")
            do = ex.get("do", "")
            why = ex.get("why", "")
            line = f"  e.g. {cn} → {do}"
            if why:
                line += f"  ({why})"
            lines.append(line)
        sections.append("\n".join(lines))

    return "\n\n".join(sections)
