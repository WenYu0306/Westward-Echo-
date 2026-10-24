"""Node: REVIEW_TERMS — cross-check READ's terminology decisions with Qwen.

READ (DeepSeek) decides how to render names and culture terms, but its
decision is stochastic — the same term can come out as a faithful adaptation
one run and a literal/pinyin rendering the next (e.g. 南茅北马 → "Southern
Mao, Northern Ma"). This node runs AFTER READ and BEFORE WRITE, using Qwen as
an independent second opinion: for character/culture terms only, it flags
literal/pinyin renderings and replaces them with a faithful adaptation.

Fail-safe: any LLM error or parse failure leaves the decisions untouched and
returns an empty update — the pipeline continues, just without the correction.

【中文·这个文件是干什么的】
这是流水线里的"术语复核员"，排在 READ 之后、WRITE 之前。
它的唯一职责：检查 READ 定下的**人名和文化词译法**是不是偷懒了——
如果 READ 把有文化内涵的词直接音译成拼音或字面翻译，
复核员就用另一个大模型（通常是 Qwen）给出更忠实的意译替换。

【中文·为什么需要这个角色】
READ 角色通常由 DeepSeek 扮演，它的术语决策是**随机的**：
同一个"南茅北马"，这次可能译成 "Southern Mao, Northern Ma"（字面翻译，
英文读者完全看不懂），下次可能译成 "the Southern and Northern Schools
of Mysticism"（忠实意译）。这种不稳定会直接影响翻译质量。
用一个独立的大模型做"第二意见"，能大幅降低偷懒译法的概率。

【中文·它在整个流程里的位置】
在 graph.py 里注册为 "review_terms_node"，位于 read_node → write_node 之间：
  READ → **REVIEW_TERMS** → WRITE
它只修改 state 里的 read_analysis.terminology_decisions，
不改其他任何字段。如果出错或没有需要复核的术语，就返回空字典，
流水线照常往下走——绝不会因为复核失败而卡住整本书。

【中文·设计原则：安全降级（fail-safe）】
这个节点的每一层都有兜底：
  · 没有术语决策 → 跳过
  · 没有 API 密钥 → 跳过
  · 大模型调用失败 → 跳过
  · JSON 解析失败 → 跳过
  · 复核结果全部 pass → 跳过
无论哪种情况，流水线都继续跑，只是少了这一道质检。
宁可漏检，不可阻塞。
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ...config import node_llm_kwargs
from ..llm_fallback import is_content_filter_error
from ..parse_utils import parse_llm_json
from ..state import TranslatorState

logger = logging.getLogger(__name__)


def _build_review_client(**provider_kwargs) -> ChatOpenAI:
    """A REVIEW_TERMS client on the routed provider (primary or content-filter fallback)."""
    return ChatOpenAI(  # type: ignore[call-arg]
        temperature=0.1,       # 极低温度：复核是判断题，不需要创造性
        max_tokens=4096,       # 复核结果很短，4K 足够
        request_timeout=120,   # 超时 2 分钟（比 READ 短很多，因为任务简单）
        max_retries=0,         # 不自动重试：失败了就走兜底跳过
        # 强制要求模型返回 JSON 格式（OpenAI 的结构化输出功能）
        model_kwargs={"response_format": {"type": "json_object"}},
        **provider_kwargs,
    )

# Only these categories are worth reviewing. location/technique/item/era are
# legitimately transliterated or literal — no cultural meaning to lose.
#
# 【中文】只复核"人物"和"文化"两类术语。
# 地名、功法名、物品名、年代等类别本身就适合音译或直译，
# 没有文化内涵丢失的风险，送去复核反而可能被错误"修正"。
# frozenset 是不可变集合，查找效率 O(1)，比 list 的 in 操作快。
_REVIEW_CATEGORIES = frozenset({"character", "culture"})

# 【中文·复核员的系统提示词】告诉大模型扮演什么角色、按什么规则判断。
# 核心指令：只看"译法是否传达了文化含义"，不要碰已经合格的译法。
# ⚠️ 这段英文是喂给大模型的原文，属于业务数据，不能改动。
_REVIEW_SYSTEM = """\
You are a cultural-compilation quality reviewer. Given a Chinese term and its \
proposed English rendering, decide whether the rendering is a faithful \
adaptation (conveys the cultural meaning) or a lazy literal/pinyin rendering \
(an English reader would not understand what it actually means).

Rules:
- If the rendering conveys the cultural meaning → verdict "pass".
- If it is a literal translation or bare pinyin → verdict "fail" and give a \
  "corrected" faithful adaptation.
- Only fix literal/pinyin renderings. Do NOT touch a rendering that is already \
  a faithful adaptation.
"""

# 【中文·复核员的用户提示词模板】{terms_list} 会被替换成待复核术语列表。
# 要求大模型严格返回 JSON，不要加 Markdown 围栏或前后废话。
# ⚠️ 这段英文是喂给大模型的原文，属于业务数据，不能改动。
_REVIEW_USER = """\
Review these terms and their proposed renderings:

{terms_list}

Return STRICT JSON only (no preamble, no markdown fences):
{{"reviews": [{{"term_cn": "...", "verdict": "pass", "corrected": ""}}]}}
"""


def _clean_rendering(proposed_en: str) -> str:
    """Take only the rendering part, dropping any trailing explanation.

    READ sometimes crams "rendering — explanation" into proposed_en. The
    reviewer must judge the rendering alone, or a long trailing explanation
    will make it look faithful and the literal rendering slips through.

    【中文·一句话作用】
    从 READ 给的译法字符串里截掉"— 解释说明"部分，只留纯译法。

    【参数】
      proposed_en  READ 给出的英文译法，可能形如
                   "Ghost Festival — a traditional Chinese holiday for ..."

    【返回】
      破折号前面的纯译法部分，如 "Ghost Festival"。

    【为什么存在】
    READ 有时会把译法和解释塞进同一个字段，用 "—" 分隔。
    如果不截掉解释，复核员看到一大段详细说明，会觉得"这个译法很忠实"，
    实际上前面的译法本身可能是纯拼音——解释掩盖了问题。
    """
    # 按 "—"（em dash）分割取第一段，再去掉首尾空白
    return proposed_en.split("—")[0].strip()


def _format_terms(decisions: list[dict]) -> str:
    """Format terminology decisions into a bullet list for the reviewer prompt.

    【中文·一句话作用】
    把术语决策列表格式化成复核员提示词里的文本清单。

    【参数】
      decisions  术语决策列表，每个元素是一个字典，至少包含：
                 term_cn（中文原词）、proposed_en（英文译法）。
                 可选字段：cultural_note 或 reasoning（文化注释）。

    【返回】
      多行文本，每行格式：- 中文词 → "英文译法"  (注释前120字)
      例：
        - 鬼节 → "Ghost Festival"  (Chinese holiday honoring the dead)
        - 南茅北马 → "Southern Mao, Northern Ma"

    【为什么存在】
    复核员的提示词需要一个结构化的术语清单。
    注释截断到 120 字符是为了控制提示词长度——太长会变贵，
    而且过长的注释反而分散复核员的注意力。
    """
    lines = []
    for d in decisions:
        cn = d.get("term_cn", "")
        # 先清洗译法，去掉可能附带的解释
        en = _clean_rendering(d.get("proposed_en", ""))
        # 优先取 cultural_note，没有就退而取 reasoning
        note = d.get("cultural_note", "") or d.get("reasoning", "")
        line = f'- {cn} → "{en}"'
        if note:
            # 注释最多取前 120 字符，避免提示词过长
            line += f"  ({note[:120]})"
        lines.append(line)
    return "\n".join(lines)


def _parse_review(content: str) -> dict[str, str]:
    """Parse the reviewer's JSON into {term_cn: corrected} for fail terms only.

    【中文·一句话作用】
    解析复核员返回的 JSON，只提取判定为 "fail" 的术语及其修正建议。

    【参数】
      content  复核员返回的原始文本（应该是 JSON）。

    【返回】
      一个字典 {中文原词: 修正后的译法}，只包含需要修正的条目。
      如果解析失败或全部 pass，返回空字典。

    【为什么存在】
    复核员的 JSON 里既有 pass 也有 fail，但下游只需要知道
    "哪些要改、改成什么"。这个函数做了过滤+提取两步合一。
    """
    # 兜底值：如果 JSON 解析失败，当作"没有需要修正的"处理
    fallback: dict = {"reviews": []}
    result, _ = parse_llm_json(content, fallback)
    corrected = {}
    for r in result.get("reviews", []):
        cn = r.get("term_cn", "")
        verdict = r.get("verdict", "pass")
        fix = r.get("corrected", "")
        # 只有三个条件同时满足才收录：
        # ① 有中文原词（否则不知道改哪个）
        # ② 判定为 fail（pass 的不需要动）
        # ③ 给了修正建议（fail 但没给替代方案也没法改）
        if cn and verdict == "fail" and fix:
            corrected[cn] = fix
    return corrected


def review_terms_node(state: TranslatorState) -> dict:
    """Review READ's character/culture term decisions and fix literal ones.

    【中文·一句话作用】
    复核 READ 定下的人物/文化术语译法，把偷懒的音译/直译替换成忠实意译。

    【参数】
      state  流水线共享白板（TranslatorState），从中读取 read_analysis。

    【返回】
      如果有修正：{"read_analysis": 修正后的分析结果}
      如果无需修正或出错：{} （空字典，引擎不做任何更新）

    【为什么存在】
    READ 的术语决策不稳定（同一个词每次可能译得不一样），
    用一个独立模型做交叉验证能提高一致性。
    详见文件头 docstring 的设计说明。

    【坑】
    这个节点直接修改 state 里 read_analysis 的内部列表元素（原地修改），
    而不是创建副本。这意味着即使返回空字典，
    如果中途改了 decisions 里的某个 dict 但最后 fixed==0，
    那些中间修改仍然会留在状态里。不过当前逻辑只在 confirmed fix 时才写入，
    所以实际不会出问题——但如果将来改了循环逻辑要注意这一点。
    """
    # ── 前置检查 1：有没有术语决策可以复核 ──
    # 如果 READ 没产出任何术语决策（比如纯对话章节），直接跳过
    read_analysis = state.get("read_analysis", {})
    decisions = read_analysis.get("terminology_decisions", [])
    if not decisions:
        return {}

    # ── 前置检查 2：筛出值得复核的类别 ──
    # 只复核 character（人物）和 culture（文化词），
    # 地名/功法/物品等类别天然适合音译，送去复核反而添乱
    to_review = [d for d in decisions if d.get("category", "") in _REVIEW_CATEGORIES]
    if not to_review:
        return {}

    # ── 获取模型配置 ──
    # Model / provider / reasoning control come from the per-node routing table.
    # 从全局路由表获取这个节点该用哪个模型、哪个服务商。
    # 好处：切换模型只改环境变量，不用改代码。
    provider_kwargs = node_llm_kwargs("review_terms")

    # No key → no review (dev/test mode). Fail-safe, like the invoke() guard.
    # Checks the key the routed provider actually needs — checking LLM_API_KEY
    # unconditionally would silently skip the node the moment REVIEW_TERMS is
    # re-tiered onto GLM.
    #
    # 【中文·前置检查 3：有没有 API 密钥】
    # 没有密钥就跳过（开发/测试环境常见）。
    # 注意这里检查的是**路由表指定的那个服务商**的密钥，
    # 而不是固定的 LLM_API_KEY。如果将来把复核员从 OpenAI 切到 GLM，
    # 检查 LLM_API_KEY 就会误判为"有密钥"然后调 GLM 失败。
    if not provider_kwargs["api_key"]:
        logger.warning("REVIEW_TERMS: no LLM key configured, skipping review")
        return {}

    # ── 构建大模型客户端 ──
    llm = _build_review_client(**provider_kwargs)

    # ── 组装提示词并调用 ──
    user_prompt = _REVIEW_USER.format(terms_list=_format_terms(to_review))
    messages = [
        SystemMessage(content=_REVIEW_SYSTEM),
        HumanMessage(content=user_prompt),
    ]

    try:
        response = llm.invoke(messages)
    except Exception as exc:  # fail-safe: leave decisions untouched
        # 【中文·内容过滤兜底】Qwen 审查拦截时换 GLM 重试一次；其他错误
        # 照旧失败跳过（fail-safe：不抛异常，不阻塞后续章节）。
        # 这个节点是"软性"复核，失败只意味着术语暂不修正，不会丢章节。
        if not is_content_filter_error(exc):
            logger.warning("REVIEW_TERMS: LLM call failed, skipping review: %s", exc)
            return {}
        fallback_kwargs = node_llm_kwargs("review_terms_fallback")
        if not fallback_kwargs["api_key"]:
            logger.warning("REVIEW_TERMS: content filter rejected, no fallback key — skipping")
            return {}
        logger.warning(
            "REVIEW_TERMS: content filter rejected — retrying once on %s",
            fallback_kwargs["model"],
        )
        try:
            response = _build_review_client(**fallback_kwargs).invoke(messages)
        except Exception as fexc:
            logger.warning("REVIEW_TERMS: fallback call failed, skipping review: %s", fexc)
            return {}

    # ── 解析复核结果并应用修正 ──
    content = response.content
    if not isinstance(content, str):
        content = str(content)
    corrected = _parse_review(content)
    if not corrected:
        # 全部 pass 或解析失败 → 无需修改
        return {}

    # 遍历所有术语决策，把复核员标记为 fail 的替换成修正译法
    fixed = 0
    for d in decisions:
        cn = d.get("term_cn", "")
        if cn in corrected:
            old = d.get("proposed_en", "")
            # ⚠️ 这里是原地修改 state 里的对象（不是副本）
            d["proposed_en"] = corrected[cn]
            logger.info("REVIEW_TERMS: fixed '%s' → '%s' (was '%s')", cn, corrected[cn], old)
            fixed += 1

    # 只有确实改了东西才返回更新后的 read_analysis，
    # 否则返回空字典让引擎不做无意义的状态合并
    if fixed:
        return {"read_analysis": read_analysis}
    return {}
