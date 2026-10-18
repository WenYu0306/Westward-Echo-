"""Shared JSON parse utility for agent node responses.

All four nodes (read, write, readback, fix) receive LLM outputs that
are expected to be JSON but may arrive as markdown-wrapped, code-fenced,
or malformed.  This module provides a single parse function with a
multi-layer fallback so every node handles failures consistently.

The caller supplies a *fallback* dict — what to return when every parse
layer fails.  For READBACK, that fallback should be NEEDS_FIX, not PASS
(the dangerous optimistic default in the old per-node implementations).

【中文·这个文件是干什么的】
这是一个"JSON 解析安全网"。大模型返回的内容**理论上**应该是纯 JSON，
但实际上经常夹带 Markdown 代码围栏（```json ... ```）、前后废话、
甚至格式错误。如果每个角色节点自己写一套解析逻辑，既重复又容易漏掉边界情况。
所以把"从脏文本里捞出 JSON"这件事抽成公共工具，四个角色共用。

【中文·它在整个流程里的位置】
被 src/agent/nodes/ 下的 read.py、write.py、readback.py、fix.py、
review_terms.py 调用。它本身不调用任何大模型、不访问数据库，
只做纯文本处理，所以不花钱、没有副作用。

【中文·核心设计：多层降级】
解析分三层尝试，前一层失败才走下一层：
  第 0 层：剥掉 Markdown 代码围栏 → 直接 json.loads
  第 1 层：正则提取第一个 {...} → json.loads
  第 2 层：全部失败 → 返回调用方提供的兜底值（fallback）
这样即使大模型返回了一堆乱码，流水线也不会崩，
而是拿着兜底值继续往下走（兜底值通常意味着"这步没做好，需要修"）。

【中文·为什么兜底值由调用方决定】
不同角色解析失败的后果不一样：
  · READBACK 解析失败 → 应该当作 NEEDS_FIX（宁可多修一轮，不可漏检）
  · READ 解析失败 → 应该给一份空分析（让 WRITE 凭自己判断）
如果把兜底值写死在本函数里，就没法区分这两种情况了。
"""

import json
import logging
import re

logger = logging.getLogger(__name__)


def parse_llm_json(content: str, fallback: dict) -> tuple[dict, bool]:
    """Parse an LLM's JSON response with multi-layer fallback.

    Layers (tried in order):
      1. Strip markdown code fences, then ``json.loads``.
      2. Regex-extract the first ``{...}`` object, then ``json.loads``.
      3. Return *fallback* — parsing failed.

    Args:
        content: Raw LLM response text.
        fallback: Dict to return when all parse layers fail.

    Returns:
        ``(parsed_dict, is_fallback)``.  ``is_fallback`` is ``True`` when
        every parse layer failed and *fallback* was returned.

    【中文·一句话作用】
    把大模型返回的"可能不太干净的文本"解析成 Python 字典；
    实在解析不出来就返回你给的兜底值，保证调用方永远拿到一个字典。

    【参数】
      content   大模型返回的原始文本。可能是纯 JSON、可能裹着 ```json 围栏、
                也可能前后有解释性文字。None 或空串也能安全处理。
      fallback  所有解析手段都失败时返回的默认字典。
                这个值由调用方根据业务场景决定（见文件头说明）。

    【返回】
      一个二元组 (解析结果, 是否是兜底)：
        · 解析成功 → (解析出的字典, False)
        · 走了正则提取 → (提取到的字典, True)——注意这也算"部分成功"，
          因为正则可能截断了尾部内容
        · 彻底失败 → (fallback, True)

    【为什么存在】
    大模型的输出格式不可靠是行业常态。与其在每个节点里重复写
    "try json.loads → 不行就正则 → 不行就给默认值"，不如集中维护一套。
    改一处就能同时改善四个角色的容错能力。
    """
    # 【中文】先去掉首尾空白。如果 content 是 None 或空串，
    # 后面所有解析都会自然失败，最终走到兜底返回。
    text = content.strip() if content else ""

    # ── Layer 0: strip code fences ──
    # 【中文·第 0 层：剥掉 Markdown 代码围栏】
    # 大模型经常把 JSON 包在 ```json ... ``` 里返回，这是给人看的格式，
    # json.loads 不认识。这里检测开头是否有 ```，有的话就去掉首尾两行。
    # 注意：如果末尾没有单独的 ``` 行（比如模型忘了闭合），
    # 就只去掉开头那一行，剩下的内容仍然可以尝试解析。
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[-1].strip() == "```":
            # 末尾有闭合围栏 → 去掉首尾两行
            text = "\n".join(lines[1:-1])
        else:
            # 末尾没有闭合围栏 → 只去掉开头一行
            text = "\n".join(lines[1:])
        text = text.strip()

    # ── Layer 1: strict JSON ──
    # 【中文·第 1 层：直接当纯 JSON 解析】
    # 如果剥完围栏后是干净的 JSON，这一步就能成功，也是最快的路径。
    # json.JSONDecodeError 和 ValueError 都是"不是合法 JSON"的信号，
    # 捕获后静默进入下一层，不打日志——因为还有后续手段。
    try:
        return json.loads(text), False
    except (json.JSONDecodeError, ValueError):
        pass

    # ── Layer 2: regex-extract the widest JSON object ──
    # 【中文·第 2 层：用正则从文本里"捞"出第一个 JSON 对象】
    # r"\{[\s\S]*\}" 匹配从第一个 { 到最后一个 } 之间的所有内容
    # （[\s\S] 表示"包括换行在内的任意字符"，* 是贪婪匹配）。
    # ⚠️ 贪婪匹配的代价：如果文本里有多个并列的 JSON 对象，
    # 正则会一口气吞掉它们之间的所有内容，解析大概率失败。
    # 但实际场景中，大模型通常只返回一个 JSON 对象，所以这个风险可接受。
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            # 正则提取成功也算"部分成功"，标记 is_fallback=True
            # 提醒调用方：这份数据可能不完整
            return json.loads(m.group()), True
        except (json.JSONDecodeError, ValueError):
            pass

    # ── Layer 3: give up — caller's fallback ──
    # 【中文·第 3 层：全部失败，返回兜底值】
    # 只在 debug 级别记日志，因为生产环境每章都可能触发一次
    # （大模型偶尔就是返回烂格式），info 级别会刷屏。
    logger.debug("parse_llm_json: all layers failed, returning fallback")
    return fallback, True
