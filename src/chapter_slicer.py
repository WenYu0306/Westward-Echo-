"""Adaptive chapter splitting for chapters that would exceed output token limits.

The adversarial test proved that chapters outputting >12K English characters
(~4000 output tokens) combined with growing prompt overhead (~5000 input tokens)
can exhaust DeepSeek V4's context window, producing empty translations with
Chinese character residue.

This module auto-detects chapters that would exceed the safe threshold and
splits them at paragraph boundaries. Each segment is translated independently
with bridging context from the previous segment, then merged.

【中文·这个文件是干什么的】
把过长的单章再切成小段（segment）。注意它和 chapter_splitter.py 的区别：
  · chapter_splitter.py → 把整本书切成一章一章（按"第X章"标题切）
  · chapter_slicer.py   → 把某一章切成几段（因为这一章太长了）

【中文·为什么需要切块】
大模型单次能处理的文字量有上限（叫"上下文窗口"）。
一章如果超过 4500 个汉字，翻成英文后加上提示词的开销，
就可能撑爆模型的窗口，导致翻译失败或后半段质量严重下降。
所以要在喂给 AI 之前，先把长章切成安全大小的小段。

【中文·它在整个流程里的位置】
被 graph.py 的 translate_chapter() 调用：
  should_split()    → 判断这一章要不要切
  split_chapter()   → 执行切分，返回段落列表
  build_segment_title() → 给切出的段生成标题（如"第三章 [Part 2/4]"）
切分后的逐段翻译、剧情桥接逻辑在 graph.py 的 _translate_split() 里，
本文件只负责"怎么切"，不负责"切完怎么翻"。

【中文·切分策略】
优先在段落边界（空行）处切，尽量不把一个自然段拆成两半。
如果一个段落本身就超长，再退而求其次在句子边界（。！？）处切。
如果连一个句子都超长，才按字符数硬切。
这样能最大程度保证每段的语义完整性。
"""

import re

# ── Thresholds ────────────────────────────────────────────────
# 【中文·两个关键阈值】
# 这两个数字是通过实测得出的经验值，不是理论计算。

# Max Chinese chars per segment — empirically, 3000 CN chars → ~9000 EN chars
# → ~3000 output tokens. With ~5000 prompt overhead → ~8000 total, well within
# the 16384 limit with margin for noise.
#
# 【中文】每段最多 3000 个汉字。
# 换算链：3000 汉字 → 约 9000 英文字符 → 约 3000 输出 token，
# 加上提示词开销约 5000 token，总计约 8000 token，
# 远低于模型 16384 token 的上限，留有足够余量。
MAX_CHARS_PER_SEGMENT = 3000

# If a chapter is below this, it doesn't need splitting (even with overhead,
# 4000 CN chars × 3x EN ratio = 12000 EN chars ~= 4000 tokens, plus overhead
# ~5000 tokens = 9000 total — safe.)
#
# 【中文】切分触发阈值：4500 汉字。
# 低于这个值的章节不需要切——即使全部翻完也不会超限。
# 4500 汉字 × 3 倍英汉比 ≈ 13500 英文字符 ≈ 4500 token，
# 加 5000 提示词开销 ≈ 9500 token，仍在安全范围内。
# 注意触发阈值（4500）比每段上限（3000）高，
# 这样切出来的段数不会太多，减少翻译轮次和费用。
SPLIT_THRESHOLD_CHARS = 4500


def should_split(chapter_content: str) -> bool:
    """Return True if this chapter would risk exhausting the output token limit.

    【中文·判断一章是否需要切块】
    算法很简单：去掉换行和空格后数汉字数量，超过 4500 就切。
    这个函数只做判断，不做切分——切分由 split_chapter() 负责。

    【参数】chapter_content  章节正文（中文原文）
    【返回】True = 需要切块，False = 不需要
    """
    cn_chars = len(chapter_content.replace("\n", "").replace(" ", ""))
    return cn_chars > SPLIT_THRESHOLD_CHARS


def split_chapter(
    chapter_content: str, max_segment_chars: int = MAX_CHARS_PER_SEGMENT
) -> list[dict]:
    """Split a long chapter into segments at natural boundaries.

    Priority: paragraph break (\\n\\n) → sentence break (。！？) → character limit.
    When a single paragraph exceeds the limit, it is split at sentence boundaries.
    When a single sentence exceeds the limit, it is split at the character limit.

    Returns a list of dicts: [{index, total, content, is_first, is_last}].
    Returns a single segment for empty/whitespace content.

    【中文·把长章节切成若干小段】

    切分优先级（从高到低）：
      ① 段落边界（\\n\\n，即空行）→ 最理想的切点，不会打断任何句子
      ② 句子边界（。！？）→ 段落本身太长时，在句号等处切
      ③ 字符上限硬切 → 极端情况，一个句子就超过 3000 字时才用

    【参数】
      chapter_content     章节正文
      max_segment_chars   每段最大汉字数，默认 3000（见上方常量说明）

    【返回】字典列表，每个字典代表一段：
      index    段序号，从 1 开始
      total    总段数
      content  这段的正文内容
      is_first 是否是第一段（用于判断是否需要前情桥接）
      is_last  是否是最后一段（用于判断是否需要准备下一段桥接）

    【坑】空内容或纯空白内容会返回一个单段列表，而不是空列表。
    这样下游代码不需要额外处理"零段"的边界情况。
    """
    # 【中文】空内容保护：如果传入空字符串或纯空白，直接返回一个占位段。
    if not chapter_content or not chapter_content.strip():
        return [{
            "index": 1, "total": 1, "content": chapter_content or "",
            "is_first": True, "is_last": True,
        }]

    # 【中文】第一步：按空行把全文拆成段落块（block）。
    # 每个 block 是一个自然段，strip 去掉首尾空白，过滤掉空块。
    blocks = [b.strip() for b in chapter_content.split("\n\n") if b.strip()]
    segments = []
    current_blocks: list[str] = []  # Full paragraphs accumulated so far
    # 【中文】current_blocks 是当前正在攒的段，还没到字数上限就先往里加段落。
    current_chars = 0

    def _flush_segment():
        """Flush accumulated blocks into a segment.

        【中文】把当前攒着的段落块打包成一个段，清空缓冲区。
        用 nonlocal 声明是因为要修改外层函数的变量。
        """
        nonlocal current_blocks, current_chars
        if current_blocks:
            segments.append("\n\n".join(current_blocks))
            current_blocks = []
            current_chars = 0

    def _split_block_at_sentences(block: str) -> list[str]:
        """Split a single oversized block at sentence boundaries.

        【中文·把一个超长段落按句子边界切开】
        当单个段落就超过字数上限时，退而求其次在句号、感叹号、问号处切。
        使用正则 (?<=[。！？]) 做"后向断言"分割——切分点保留在前一段尾部，
        这样标点不会丢失。

        如果某个句子本身就超过上限（比如一整段没有句号的意识流），
        那就只能硬切了——这种情况在实际网文中极少出现。
        """
        # 【中文】(?<=[。！？]) 是"后向断言"：在每个句号/叹号/问号**之后**切，
        # 标点符号留在前一段的末尾，不会变成下一段的开头。
        sentences = re.split(r'(?<=[。！？])', block)
        result = []
        buf = ""
        buf_chars = 0
        for s in sentences:
            sc = len(s.replace("\n", "").replace(" ", ""))
            # 【中文】如果加上这句就超上限了，先把缓冲区的内容存下来，
            # 然后开一个新的缓冲。
            if buf_chars + sc > max_segment_chars and buf:
                result.append(buf.strip())
                buf = s
                buf_chars = sc
            else:
                buf += s
                buf_chars += sc
        if buf.strip():
            result.append(buf.strip())
        return result

    # 【中文】第二步：遍历所有段落块，按字数上限攒段。
    for block in blocks:
        block_chars = len(block.replace("\n", "").replace(" ", ""))

        # A single block is too large → split at sentence boundaries
        # 【中文】这个段落本身就超过了单段上限 → 必须按句子切。
        if block_chars > max_segment_chars:
            # 先把之前攒好的段落打包成一个段
            _flush_segment()
            # 再把这个超长段落按句子边界切成多个段
            for sb in _split_block_at_sentences(block):
                segments.append(sb)
            continue

        # Normal case: block fits, check if adding it exceeds the limit
        # 【中文】正常情况：这个段落没超上限，但加上它可能超。
        # 如果加了会超，就先打包当前的，再开始攒新的一段。
        if current_chars + block_chars > max_segment_chars:
            _flush_segment()

        current_blocks.append(block)
        current_chars += block_chars

    # 【中文】别忘了最后一段——循环结束后缓冲区里可能还有剩余段落。
    _flush_segment()

    # 【中文】第三步：把纯文本段列表包装成带元信息的字典列表。
    total = len(segments)
    return [
        {
            "index": i + 1,
            "total": total,
            "content": seg,
            "is_first": i == 0,
            "is_last": i == total - 1,
        }
        for i, seg in enumerate(segments)
    ]


def build_segment_title(original_title: str, segment: dict) -> str:
    """Generate a chapter title for a segment.

    【中文·给切出的段生成标题】
    格式："原标题 [Part 段序号/总段数]"，例如"第三章 重生 [Part 2/4]"。
    这个标题主要用于日志追踪和调试，让开发者一眼看出这是某章的第几段。
    翻译流水线中 READ/WRITE 角色看到的也是这个标题，
    帮助它们理解自己正在处理的是长章的一部分而非完整章节。

    【参数】
      original_title  原始章节标题
      segment         split_chapter 返回的段字典（至少含 index 和 total）

    【返回】拼接后的段标题字符串
    """
    return f"{original_title} [Part {segment['index']}/{segment['total']}]"
