"""Shared provider-fallback helpers for the LLM nodes.

【中文·这个文件是干什么的】
判断层（read / readback / review_terms）和文字层（write）在调用大模型失败时，
需要统一的「这是不是内容安全过滤错误」判断，以及「换另一个 provider 兜底」的
能力。这里把判断函数集中起来，避免每个节点各写一份、改一处漏一处。

【中文·为什么存在】
WRITE 节点先有了 GLM 1301 → Qwen 的兜底。但判断层三个节点（read/readback/
review_terms）跑在 Qwen 上，Qwen 也有自己的内容安全审查，一旦拦截同样会整章
失败，却没有任何兜底。这个文件把「识别内容过滤错误」提炼成公共函数，让四个
节点都能用同一套判断去触发兜底。
"""
from __future__ import annotations


def is_content_filter_error(exc: Exception) -> bool:
    """True when the provider's safety layer rejected the request.

    GLM (Zhipu) returns HTTP 400 with ``code 1301`` and a ``contentFilter``
    block; Qwen (DashScope) returns its own moderation rejection. Both carry
    content-moderation keywords. The check is deliberately keyword-based because
    LangChain wraps provider errors into various exception types with no common
    catchable class.

    Retrying the same provider does not help — the filter is deterministic on
    the same text — so the only sane recovery is switching provider for that one
    chapter.

    【中文·判断异常是否来自 provider 的内容安全过滤】

    作用：检查异常文本里有没有内容审查关键词。
      · GLM：1301 / contentFilter / 不安全
      · Qwen：不安全 / 敏感 / 违规 / content_filter
    为什么用关键词匹配而不是类型判断：LangChain 把 provider 错误包装成各种
    异常类型，没有统一的异常类可 catch，只能看错误消息内容。
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "1301",           # GLM（智谱）内容过滤错误码
        "contentfilter",  # GLM camelCase（统一转小写后匹配）
        "content_filter",  # OpenAI 风格 finish_reason / Qwen 部分报错
        "content filter",  # 通用英文两词表述
        "不安全",           # GLM / Qwen 中文
        "敏感",             # Qwen 中文
        "违规",             # Qwen 中文
    )
    return any(marker in text for marker in markers)
