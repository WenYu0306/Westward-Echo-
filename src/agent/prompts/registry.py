"""Prompt registry — selects the per-node prompt set by content type.

Branching mechanism (v0.16+): the pipeline supports parallel content
types — ``novel`` (the validated web-novel path) and, later, ``script``
(vertical short drama) and ``game``. Each type maps to a :class:`PromptSet`
holding the four nodes' SYSTEM/USER templates.

Invariants (enforced by tests/test_prompt_registry.py):

- ``novel`` returns the exact module-level constants that existed before
  this registry was introduced — the web-novel path is byte-identical.
- Unknown content types fall back to ``novel``.
- Every prompt set MUST keep the same ``.format()`` placeholder signature
  and the same output JSON schema as the novel set, so node logic
  (including all parse fallbacks) is shared unchanged.

【中文·这个文件是干什么的】
本文件是一个"提示词注册表"——你可以把它理解成一本菜单：
不同的内容类型（小说、短剧、游戏……）对应不同的"套餐"，
每个套餐里装着四个角色各自需要的提示词（系统提示 + 用户提示）。

【中文·为什么需要注册表】
目前流水线只支持"网络小说"一种内容类型，但架构上预留了扩展能力。
将来如果要翻译竖屏短剧剧本或游戏文本，只需在 _REGISTRY 里加一行，
指向一套新的提示词即可，节点代码完全不用改。

【中文·它在整个流程里的位置】
四个角色节点（read / write / readback / fix）在运行时会调用
get_prompt_set(content_type) 来取自己需要的提示词。
content_type 来自任务状态（state），默认是 "novel"。

【中文·三条不变量（测试强制保证）】
① "novel" 返回的提示词和引入注册表之前完全一样（逐字节一致），
   确保这次重构没有悄悄改变已有行为。
② 不认识的内容类型会静默回退到 "novel"，不会报错崩溃。
③ 所有提示词集合必须保持相同的 .format() 占位符签名和 JSON 输出格式，
   这样节点的解析逻辑才能通用，不用为每种内容类型写一套解析。
"""

from dataclasses import dataclass

# 【中文】下面四个 import 分别从各角色的提示词文件里取出两样东西：
# · *_SYSTEM —— 系统提示词（告诉 AI "你是谁、你该怎么想"）
# · *_USER   —— 用户提示词模板（告诉 AI "这一章的具体任务是什么"）
# 它们都是大段英文字符串，属于业务数据，不能改动。
from .fix import FIX_SYSTEM, FIX_USER
from .read import READ_SYSTEM, READ_USER
from .readback import READBACK_SYSTEM, READBACK_USER
from .write import WRITE_SYSTEM, WRITE_USER


# 【中文·提示词集合的数据结构】
# 用 frozen=True 表示创建后不可修改——提示词是常量，不应该被运行时篡改。
# 8 个字段对应 4 个角色 × 2 种提示词（系统 + 用户）。
@dataclass(frozen=True)
class PromptSet:
    """Immutable bundle of the four nodes' SYSTEM + USER prompt templates."""
    read_system: str       # READ 角色的系统提示词
    read_user: str         # READ 角色的用户提示词模板
    write_system: str      # WRITE 角色的系统提示词
    write_user: str        # WRITE 角色的用户提示词模板
    readback_system: str   # READBACK 角色的系统提示词
    readback_user: str     # READBACK 角色的用户提示词模板
    fix_system: str        # FIX 角色的系统提示词
    fix_user: str          # FIX 角色的用户提示词模板


# 【中文·网络小说提示词集合】
# 把四个角色的提示词打包成一个 PromptSet。
# 这是目前唯一已实现的提示词集合，也是未知内容类型的默认回退目标。
NOVEL_PROMPTS = PromptSet(
    read_system=READ_SYSTEM,
    read_user=READ_USER,
    write_system=WRITE_SYSTEM,
    write_user=WRITE_USER,
    readback_system=READBACK_SYSTEM,
    readback_user=READBACK_USER,
    fix_system=FIX_SYSTEM,
    fix_user=FIX_USER,
)

# 【中文·注册表本体】
# 键是内容类型字符串（对应 state["content_type"]），值是对应的提示词集合。
# 目前只有 "novel" 一个条目；将来加短剧/游戏时在这里追加即可。
_REGISTRY: dict[str, PromptSet] = {
    "novel": NOVEL_PROMPTS,
}


def get_prompt_set(content_type: str = "novel") -> PromptSet:
    """Return the prompt set for a content type; unknown types get novel.

    【中文】根据内容类型从注册表中取出对应的提示词集合。

    参数：
      content_type: 内容类型字符串，如 "novel"。
                    默认值是 "novel"；传 None 或空串也会回退到 "novel"。

    返回：
      一个 PromptSet 对象，包含四个角色各自的系统提示词和用户提示词模板。

    ⚠️ 不认识的内容类型不会报错，而是静默返回 NOVEL_PROMPTS。
    这是有意为之的设计——保证流水线不会因为一个陌生的 content_type 就崩掉，
    但也意味着如果你拼错了类型名（比如写成 "Novel"），你不会收到任何警告，
    只会默默拿到小说的提示词。
    """
    key = (content_type or "novel").strip().lower()
    return _REGISTRY.get(key, NOVEL_PROMPTS)
