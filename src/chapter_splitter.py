"""Chapter splitter for Chinese web novels.

Splits a raw .txt file into chapters using regex patterns, and classifies
non-standard paragraphs (prologues, extras, author notes, etc.).

【中文·这个文件是干什么的】
把一整本中文网络小说的纯文本文件，按"第X章"这类标题切成一章一章。
同时识别那些不是正文章节的特殊段落（序章、番外、作者请假条等），
给它们打上标签，决定要不要翻译、要不要提取术语。

【中文·它在整个流程里的位置】
上游：拿到原始 txt 文件后，第一个处理步骤就是它。
下游：切好的 Chapter 对象列表会交给 graph.py 的 translate_chapter()，
      逐章送进翻译流水线。

【中文·核心概念】
① 正则表达式（regex）—— 一种"文字花样搜索规则"，可以一次匹配一类
   符合某种模式的文字。比如 r'第[一二三…0-9]+章' 就能同时匹配
   "第1章""第三章""第123章"等各种写法。
② 章节分类 —— 网文不只有正文章节，还有楔子、番外、作者感言等。
   不同类型的段落处理方式不同：有的要翻、有的跳过、有的翻但不提术语。
③ ParagraphTag —— 一个枚举（enum），就是"预定义好的几个标签值"，
   用来标记段落的类型和对应的翻译动作。
"""

import re
from dataclasses import dataclass
from enum import Enum


class ParagraphTag(str, Enum):
    """Pre-defined tags for chapter types and translation actions.

    【中文】段落标签枚举，同时承担两个职责：
      · 标记段落是什么类型（章节 / 序章 / 番外 / 作者公告 / 特别篇）
      · 标记对这段该做什么动作（翻译 / 翻译但不提术语 / 跳过）

    为什么用 str 子类：这样标签值本身就是字符串（如 "chapter"），
    存数据库、写 JSON 时不需要额外转换。
    """

    # ── 段落类型标签 ──
    CHAPTER = "chapter"             # Normal chapter / 正文章节
    PROLOGUE = "prologue"           # 楔子 / 序章 / 引子
    EXTRA = "extra"                 # 番外 / 外传 / IF线
    AUTHOR_NOTE = "author_note"     # 请假条 / 上架感言 / 更新公告
    SPECIAL = "special"             # 节日特别篇 / 加更

    # ── 翻译动作标签 ──
    # Actions
    TRANSLATE = "translate"
    # Translate but skip term extraction
    # 【中文】翻译但不从这段里提取新术语。
    # 用于番外、特别篇等非正文内容——里面的用语可能不规范，
    # 提取出来反而会污染术语库。
    TRANSLATE_NO_EXTRACT = "translate_no_extract"
    # Don't translate
    # 【中文】直接跳过不翻译。用于作者的请假条、更新公告等，
    # 这些内容跟故事无关，翻了也没意义。
    SKIP = "skip"


# --- Regex patterns ---
# 【中文·下面这些正则表达式就是"章节标题识别规则"】
# 每条规则匹配一类特定格式的标题文字。

# Standard chapter headers: "第123章", "第一百二十章", "第三章 标题"
# 【中文】匹配标准章节标题。拆解：
#   ^\s*        → 行首允许有空白
#   第          → 必须以"第"字开头
#   [一二三四五六七八九十百千0-9]+ → 中间是中文数字或阿拉伯数字
#   [章节回话]  → 以"章""节""回""话"之一结尾（覆盖不同小说的习惯）
#   \s*.*       → 后面可以有标题文字，也可以没有
#   re.MULTILINE → 在多行文本中逐行匹配，而不是只匹配全文开头
CHAPTER_PATTERN = re.compile(
    r'^\s*(第[一二三四五六七八九十百千0-9]+[章节回话]\s*.*)', re.MULTILINE
)

# Non-standard section classification
# 【中文】非正文章节的分类规则字典。
# 键是段落类型标签，值是匹配该类型的正则表达式。
# 注意：这些规则只匹配标题行的开头或包含关系，
# 和上面的 CHAPTER_PATTERN 互补——先按标准格式切，再对切出的标题做分类。
NON_CHAPTER_PATTERNS = {
    # 楔子/序章/引子：出现在正文之前的铺垫性内容
    ParagraphTag.PROLOGUE: re.compile(r'^(楔子|序章|引子|第[零0〇]+章)'),
    # 番外/外传/IF线/小剧场/后记：正文之外的补充故事
    ParagraphTag.EXTRA:     re.compile(r'^(番外|外传|IF线|小剧场|后记)'),
    # 作者公告类：请假条、上架感言、更新通知等
    ParagraphTag.AUTHOR_NOTE: re.compile(r'(请假|更新公告|上架感言|入V|V章|停更|恢复更新)'),
    # 节日特别篇/加更/福利章节
    ParagraphTag.SPECIAL:   re.compile(r'(七夕|春节|元旦|中秋|国庆|圣诞|特别篇|加更|福利)'),
}

# Short-text threshold for author notes (skip translation)
# 【中文】作者公告的长度阈值（500 字）。
# 短于这个长度的公告类段落直接跳过不翻译；
# 长于它的可能是正经内容只是标题带了"公告"字样，仍然翻译。
AUTHOR_NOTE_MIN_LENGTH = 500


@dataclass
class Chapter:
    """A single chapter (or special section) from the novel.

    【中文】代表小说中的一个章节（或特殊段落）的数据容器。
    用 @dataclass 装饰后，Python 自动生成 __init__、__repr__ 等方法，
    省去手写样板代码。

    【字段说明】
      index   章节序号，从 1 开始；楔子为 0
      title   原始标题行文字（如"第三章 重生"）
      content 章节正文（不含标题行本身）
      tag     段落类型标签（CHAPTER / PROLOGUE / EXTRA 等）
      action  翻译动作标签（TRANSLATE / TRANSLATE_NO_EXTRACT / SKIP）
    """

    index: int                      # 1-based chapter number
    title: str                      # Original chapter title line
    content: str                    # Full body text
    tag: ParagraphTag = ParagraphTag.CHAPTER
    action: ParagraphTag = ParagraphTag.TRANSLATE

    @property
    def word_count(self) -> int:
        """Approximate Chinese character count.

        【中文】粗略统计中文字数。
        去掉换行和空格后算字符长度。注意这不是精确的"汉字计数"，
        标点符号也会被算进去，但对于判断章节长短已经够用。
        """
        return len(self.content.replace('\n', '').replace(' ', ''))

    def preview(self, n: int = 80) -> str:
        """First n characters of content for display.

        【中文】取正文前 n 个字符作为预览，用于日志或界面展示。
        默认 80 字，够看一眼就知道这章大概讲什么。
        """
        return self.content.strip()[:n]


def classify_paragraph(title: str, content: str) -> tuple[ParagraphTag, ParagraphTag]:
    """Classify a section and decide its translation action.

    Returns (tag, action).

    【中文·给一个段落定性：它是什么类型、该怎么处理】

    按优先级依次检查标题是否匹配各类非正文模式：
      ① 作者公告 + 短文本 → 跳过不翻译
      ② 楔子/序章 → 正常翻译（此时术语库还没积累，相当于冷启动）
      ③ 番外/外传 → 翻译但不提取术语
      ④ 节日特别篇 → 翻译但不提取术语
      ⑤ 都不匹配 → 当作正文章节，正常翻译+提取术语

    【参数】
      title   段落标题
      content 段落正文（用于计算长度，判断短公告）

    【返回】(段落类型标签, 翻译动作标签) 的元组

    【坑】检查顺序很重要！作者公告的判断放在最前面，
    因为一个标题可能同时命中多个模式（比如"春节请假条"既含节日又含请假），
    此时应该优先按"作者公告→跳过"处理，而不是按"特别篇→翻译"处理。
    """
    title_lower = title.lower()
    # 【中文】计算去空白后的纯文本长度，用于判断是否为短公告
    content_len = len(content.replace('\n', '').replace(' ', ''))

    # Author notes: short + contains announcement keywords
    # 【中文】作者公告判定：标题含公告关键词 **且** 正文很短（<500字）→ 跳过。
    # 两个条件缺一不可：只有关键词但很长，可能是正经内容；
    # 只有短文本但没有关键词，可能是正常的短章节。
    if (
        content_len < AUTHOR_NOTE_MIN_LENGTH
        and NON_CHAPTER_PATTERNS[ParagraphTag.AUTHOR_NOTE].search(title)
    ):
        return ParagraphTag.AUTHOR_NOTE, ParagraphTag.SKIP

    # Prologue: translate normally (no glossary yet, so it acts as a cold-start chapter)
    # 【中文】楔子/序章：正常翻译。
    # 注意此时术语库还是空的，所以楔子相当于"冷启动"章节，
    # 里面出现的术语不会被后续章节复用（除非楔子里也定义了术语）。
    if NON_CHAPTER_PATTERNS[ParagraphTag.PROLOGUE].search(title_lower):
        return ParagraphTag.PROLOGUE, ParagraphTag.TRANSLATE

    # Extras: translate but don't extract new terms
    # 【中文】番外/外传：翻译但不提取术语。
    # 番外经常用戏谑、非正式的用语，提取出来会污染术语库。
    if NON_CHAPTER_PATTERNS[ParagraphTag.EXTRA].search(title_lower):
        return ParagraphTag.EXTRA, ParagraphTag.TRANSLATE_NO_EXTRACT

    # Holiday specials: translate but no term extraction
    # 【中文】节日特别篇/加更：同番外，翻译但不提术语。
    if NON_CHAPTER_PATTERNS[ParagraphTag.SPECIAL].search(title):
        return ParagraphTag.SPECIAL, ParagraphTag.TRANSLATE_NO_EXTRACT

    # Default: normal chapter
    # 【中文】以上都不匹配 → 当作正文章节处理。
    return ParagraphTag.CHAPTER, ParagraphTag.TRANSLATE


def split_chapters(text: str) -> list[Chapter]:
    """Split a full novel text into chapters.

    Uses the chapter-title regex to find boundaries, then between boundaries
    collects the body. Anything before the first chapter header becomes a
    preamble (tagged PROLOGUE).

    【中文·把整本小说文本切成一章一章】

    这是本文件的核心函数。工作流程：
      ① 用正则表达式在全文中找出所有"第X章"标题的位置
      ② 相邻两个标题之间的文字就是一章的正文
      ③ 第一个标题之前的文字，如果足够长（≥50 个汉字），当作楔子
      ④ 对每一章调用 classify_paragraph 确定类型和处理方式

    【参数】
      text  整本小说的纯文本内容（通常是 UTF-8 编码的 txt 文件读出来的字符串）

    【返回】Chapter 对象的列表，按出现顺序排列。
      每个 Chapter 包含：序号、标题、正文、类型标签、翻译动作。

    【坑】
      · 如果全文找不到任何章节标题，会把整篇文本当作一个"正文"章节返回，
        而不是报错——这样即使格式不规范的小说也能处理。
      · 空章节（标题后面没有正文内容）会被跳过，不会产生 Chapter 对象。
      · 楔子的序号固定为 0，正文章节从 1 开始递增。
    """
    # Find all chapter header positions
    # 【中文】用正则扫描全文，找到所有匹配"第X章"的位置。
    # finditer 返回迭代器，每个元素包含匹配的起止位置和匹配到的文字。
    matches = list(CHAPTER_PATTERN.finditer(text))
    chapters = []
    index = 0

    if not matches:
        # No chapter headers found — treat entire text as one section
        # 【中文】全文没有任何章节标题 → 把整篇当作一个章节返回。
        # 用"正文"作为默认标题，让下游至少能正常处理。
        tag, action = classify_paragraph("正文", text)
        return [Chapter(index=1, title="正文", content=text.strip(), tag=tag, action=action)]

    # Preamble (before first chapter header)
    # 【中文】处理第一个章节标题之前的文字（可能是楔子、卷分隔符等）。
    if matches[0].start() > 0:
        preamble = text[:matches[0].start()].strip()
        if preamble:
            # Skip volume separators / non-prose front matter (e.g.
            # "------------ 正文卷 ------------"): fewer than 50 CJK
            # characters means it is not a real prologue, just a divider.
            #
            # 【中文】过滤掉卷分隔符之类的非正文内容。
            # 判断方法：数一下里面有多少个汉字（Unicode 范围 一~鿿），
            # 少于 50 个汉字的大概率只是"──── 正文卷 ────"这种分隔线，
            # 不是真正的楔子，直接丢弃。
            cn_chars = sum(1 for c in preamble if '一' <= c <= '鿿')
            if cn_chars >= 50:
                tag, action = classify_paragraph("楔子", preamble)
                chapters.append(
                    Chapter(index=0, title="楔子", content=preamble, tag=tag, action=action)
                )
                index = 1

    # Extract chapters between headers
    # 【中文】遍历所有匹配到的章节标题，提取每章的正文。
    for i, match in enumerate(matches):
        # group(1) 取正则第一个括号捕获的内容，即完整标题行
        title = match.group(1).strip()
        # 本章正文从标题结束处开始
        start = match.end()
        # 到下一个标题开始前结束；如果是最后一章，就到全文末尾
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()

        if not content:
            continue  # Skip empty chapters (e.g. placeholder headers)
            # 【中文】跳过空章节。有些小说会有占位标题（如"待补"），
            # 后面没有实际内容，这种不应该产生 Chapter 对象。

        tag, action = classify_paragraph(title, content)
        chapters.append(Chapter(
            index=index + 1,
            title=title,
            content=content,
            tag=tag,
            action=action,
        ))
        index += 1

    return chapters


def merge_chapters(translations: list[str]) -> str:
    """Merge translated chapter strings into a single book file.

    【中文·把各章译文拼回一整本书】
    各章之间用两个换行符分隔（空一行），保持可读性。
    这是切分的逆操作：split_chapters 把书拆成章，merge_chapters 把章拼回书。
    """
    return "\n\n".join(translations)
