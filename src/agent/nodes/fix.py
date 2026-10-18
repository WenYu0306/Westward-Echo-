"""Node 4: FIX — editor who repairs based on cold reader feedback.

Receives the cold reader's specific complaints and fixes only what's broken.
Does NOT blindly re-translate. Fixes are targeted and surgical.

【中文·这个文件是干什么的】
这是流水线里第四个角色——「编辑」（FIX）的实现。
它拿到冷读者的具体抱怨（"这段看不懂""那段太无聊"），
然后**只修补出问题的地方**，不会整章重写。

【中文·它在整个流程里的位置】
  READBACK 判 NEEDS_FIX → **FIX 靶向修补** → 送回 READBACK 复检
被 graph.py 的 build_graph() 注册为 "fix_node" 节点；
修完后通过一条固定边回到 readback_node，形成"修→检→修"闭环。
这个闭环最多跑 2 轮（由 _MAX_FIX_ATTEMPTS 控制），超了就带着问题放行。

【中文·核心概念：为什么是"靶向修补"而不是"整章重翻"】
整章重翻 = 重新调用 WRITE，贵且慢，而且可能引入新问题。
冷读者已经明确指出了"哪里有问题"，编辑只需要对着那几个点改就行。
这就像杂志编辑拿到审稿意见后只改被指出的段落，不会把整篇文章推倒重来。

【中文·FIX 的经验会沉淀到经验本】
每次修补的具体改动会被记录下来（fix_lessons 字段），
在 graph.py 的 _post_process 里存进经验本的 bridges.md 抽屉。
这样后续章节遇到类似问题时，WRITE 可以直接参考已有的修复方案，
不用每次都花钱重新摸索。每章最多存 5 条（见 _MAX_FIX_LESSONS_PER_CHAPTER）。
"""

import logging

# LangChain 的消息对象：SystemMessage 是"系统指令"，HumanMessage 是"用户消息"
from langchain_core.messages import HumanMessage, SystemMessage
# ChatOpenAI 是大模型的统一客户端
from langchain_openai import ChatOpenAI

# 熔断器：API 连续失败太多次时自动"跳闸"保护
from ...circuit_breaker import CircuitBreakerOpenError, get_breaker
# node_llm_kwargs：根据节点名查出该用哪个模型和服务商
from ...config import node_llm_kwargs
# TranslationStats：统计 API 调用次数和 token 用量
from ...stats import TranslationStats
# token_ledger：按书/章/节点记录 token 消耗
from ... import token_ledger
# build_prompt_context：根据题材和目标语言生成角色身份描述
from ..prompts.personas import build_prompt_context
# get_prompt_set：根据内容类型取一套提示词模板
from ..prompts.registry import get_prompt_set
# TranslatorState：流水线的"共享白板"数据结构
from ..state import TranslatorState

logger = logging.getLogger(__name__)


def fix_node(state: TranslatorState) -> dict:
    """FIX: Repair specific issues the cold reader identified.

    Returns a dict with:
        - translated_text: the fixed English chapter
        - adaptation_notes: what was changed and why

    【中文·一句话说明】拿着冷读者的具体抱怨，让 AI 扮演编辑，
    只修补英文稿里被指出的问题，不整章重写。

    【参数】state —— 流水线共享白板，本函数从中读取：
      · readback_feedback   冷读者的评审意见（包含具体问题列表）
      · chapter_content     中文原文（编辑需要对照原文才能准确修补）
      · translated_text     当前英文稿（要被修补的对象）
      · exact_matches_text  术语表文本（确保修补时不破坏已定译名）
      · content_type        内容类型，决定用哪套提示词
      · genre               题材
      · target_lang         目标语言
      · api_key             调用方自带的密钥（BYOK）
      · book_id / chapter_number  用于记账和日志
      · retranslation_count 已重修几轮（本函数会 +1 后写回）

    【返回】一个字典，写入白板的五个字段：
      · translated_text     修补后的英文稿
      · adaptation_notes    本轮改动清单（可能被下一轮覆盖）
      · fix_lessons         本轮修补经验（累积型，不会被覆盖）
      · quality_issues      清空（给 READBACK 一个干净的复检起点）
      · retranslation_count 重修次数 +1（供 _needs_fix 判断是否还有重修机会）
    """
    # Model / provider / reasoning control come from the per-node routing table.
    # FIX is a deep-repair task, so its default effort stays high (GLM_FIX_EFFORT);
    # generation params below are FIX's own.
    #
    # 【中文·选模型】从配置表里查"fix 这个角色该用哪个模型"。
    # FIX 是深度修补任务，需要理解上下文和精准改写，
    # 所以默认用高能力模型（不像 READBACK 可以降级到便宜模型）。
    provider_kwargs = node_llm_kwargs(
        "fix", api_key_override=state.get("api_key", ""),
    )
    llm = ChatOpenAI(  # type: ignore[call-arg]
        temperature=0.1,     # 低温度 = 输出稳定，修补结果可复现
        max_tokens=16384,    # 编辑可能要输出整章修改稿，给足空间
        request_timeout=900, # 15 分钟超时（比 READBACK 长很多，因为任务更重）
        max_retries=0,       # 不自动重试，由熔断器统一管理
        **provider_kwargs,   # 注入模型名、base_url、api_key 等
    )
    used_model = provider_kwargs["model"]

    # Format the cold reader's feedback for the editor
    # 【中文·把冷读者的反馈格式化成"编辑工单"】
    # 冷读者返回的是结构化 JSON，但编辑需要一份人类可读的工作说明。
    # _format_readback_feedback 把它转成 Markdown 格式的工单。
    feedback = state.get("readback_feedback", {})
    feedback_text = _format_readback_feedback(feedback)

    target_lang = state.get("target_lang", "en-US")

    prompts = get_prompt_set(state.get("content_type", "novel"))
    # Per-genre / per-language parameters. FIX only needs the output language
    # and audience wording (its brief is the cold reader's feedback, not genre
    # conventions), but the system prompt is rendered through the same context
    # as the other three nodes so the JSON braces stay literal.
    #
    # 【中文·构建角色身份】FIX 其实不太依赖题材设定（它的依据是冷读者反馈，
    # 不是题材惯例），但提示词模板和其他角色共用同一套上下文渲染机制，
    # 所以还是调一下 build_prompt_context，确保模板里的花括号不被误解析。
    ctx = build_prompt_context(state.get("genre"), target_lang)
    # 【中文·填充提示词模板】五个变量塞进去：
    #   original_cn      中文原文（编辑要对照着改）
    #   current_en       当前英文稿（要被修补的底稿）
    #   reader_feedback  冷读者的工单（上面格式化好的）
    #   glossary_text    术语表（确保修补时不破坏已定译名）
    #   output_language  目标语言的显示名
    user_prompt = prompts.fix_user.format(
        original_cn=state["chapter_content"],
        current_en=state.get("translated_text", ""),
        reader_feedback=feedback_text,
        glossary_text=state.get("exact_matches_text", "(No glossary)"),
        output_language=ctx["output_language"],
    )

    # 【中文·组装消息】两条消息发给大模型：
    # ① SystemMessage = "你是编辑，请按以下规则修补"
    # ② HumanMessage  = 原文 + 英文稿 + 冷读者工单 + 术语表
    messages = [
        SystemMessage(content=prompts.fix_system.format(**ctx)),
        HumanMessage(content=user_prompt),
    ]

    # 【中文·获取熔断器】按目标语言取对应的熔断器实例
    breaker = get_breaker(target_lang)

    try:
        TranslationStats.record_api_call(target_lang)
        response = breaker.call(llm.invoke, messages)
        TranslationStats.record_api_success(target_lang)
        _capture_fix_tokens(
            response,
            model=used_model,
            book_id=state.get("book_id", ""),
            chapter=state.get("chapter_number", 0),
        )
    except CircuitBreakerOpenError:
        # 熔断器跳闸了，向上抛异常
        TranslationStats.record_api_failure(target_lang)
        raise
    except Exception:
        TranslationStats.record_api_failure(target_lang)
        raise

    # 【中文·解析编辑的回复】期望返回 JSON，包含修补后的全文和改动清单
    result = _parse_fix_response(response.content)

    # 取修补后的英文稿；如果解析失败，保留原来的英文稿（不丢内容）
    polished = result.get(
        "polished_text", state.get("translated_text", ""),
    )
    changes = result.get("changes_made", [])

    logger.info(
        "FIX ch%d: %d changes made, %d chars → %d chars",
        state["chapter_number"],
        len(changes),
        len(state.get("translated_text", "")),
        len(polished),
    )

    # Reset quality issues so READBACK gets a clean slate on re-check.
    # Increment retranslation_count so _needs_fix can terminate the
    # FIX→READBACK→FIX loop after _MAX_FIX_ATTEMPTS.
    #
    # 【中文·准备写回白板的五个字段】
    retries = state.get("retranslation_count", 0) + 1
    return {
        "translated_text": polished,
        # adaptation_notes 是普通字段（非累积型），第二轮 FIX 会覆盖第一轮的值。
        # 所以它只保留"最后一轮"的改动清单，供 _post_process 做审计日志用。
        "adaptation_notes": changes,
        # Accumulated separately: on a second FIX round, adaptation_notes (a
        # plain field) is overwritten, so the first round's repair knowledge
        # would be lost before anything could read it. fix_lessons keeps every
        # round, and graph._post_process is what finally banks them.
        #
        # 【中文·fix_lessons 和 adaptation_notes 的区别】
        # adaptation_notes 是普通字段，第二轮会覆盖第一轮；
        # fix_lessons 用了 Annotated[list, operator.add]（累积型字段），
        # 每轮的结果会自动拼接，不会丢失。
        # graph.py 的 _post_process 最终从 fix_lessons 里取经验存进经验本。
        #
        # 这里还做了清洗：只保留非空字符串，去掉空白条目。
        "fix_lessons": [
            c.strip() for c in changes
            if isinstance(c, str) and c.strip()
        ],
        # 清空质量问题列表：上一轮的问题已经被修补了，
        # 给 READBACK 一个干净的起点重新评估。
        # 如果不清空，旧问题会和新问题混在一起，无法区分。
        "quality_issues": [],
        # 重修次数 +1，供 graph.py 的 _needs_fix() 判断是否还有重修机会。
        # 达到 _MAX_FIX_ATTEMPTS 上限后就不再修了，带着问题放行。
        "retranslation_count": retries,
    }


def _format_readback_feedback(feedback: dict) -> str:
    """Format the cold reader's structured feedback as actionable editor notes.

    【中文·一句话说明】把冷读者的 JSON 评审意见转成 Markdown 格式的"编辑工单"，
    让 FIX 角色能清楚地看到"哪里出了问题、问题是什么"。

    【参数】feedback —— 冷读者返回的结构化字典，可能包含：
      · overall_impression    总体印象
      · comprehension_issues  理解障碍列表（每项有 passage 和 issue）
      · engagement_gaps       趣味缺口列表（同上）
      · character_tracking    人物追踪评价
      · world_comprehension   世界观理解评价
      · standout_moments     亮点列表（这些要保留，别改坏了）

    【返回】Markdown 格式的字符串，按类别分段展示。
    如果反馈为空，返回一句兜底说明。

    【中文·为什么要单独格式化，不直接把 JSON 扔给 FIX】
    大模型对结构化的 Markdown 标题+列表的理解比嵌套 JSON 稳定得多。
    而且分成"理解障碍""趣味缺口""亮点"几个板块后，
    编辑能分清优先级：先修看不懂的，再修无聊的，别碰写得好的。
    """
    parts = []

    # 总体印象（如果有）
    if feedback.get("overall_impression"):
        parts.append(
            f"## READER'S OVERALL IMPRESSION\n"
            f"{feedback['overall_impression']}"
        )

    # 理解障碍：读者看不懂的地方，优先级最高
    if feedback.get("comprehension_issues"):
        parts.append(
            "## COMPREHENSION ISSUES (reader was confused)"
        )
        for ci in feedback["comprehension_issues"]:
            parts.append(
                f"\n**Passage:** {ci.get('passage', '?')}\n"
                f"**Problem:** {ci.get('issue', '?')}"
            )

    # 趣味缺口：读者觉得无聊想跳过的地方
    if feedback.get("engagement_gaps"):
        parts.append(
            "## ENGAGEMENT GAPS (reader was bored or wanted to skip)"
        )
        for eg in feedback["engagement_gaps"]:
            parts.append(
                f"\n**Passage:** {eg.get('passage', '?')}\n"
                f"**Problem:** {eg.get('issue', '?')}"
            )

    # 人物追踪：读者对人物的理解情况
    if feedback.get("character_tracking"):
        parts.append(
            f"## CHARACTER TRACKING\n{feedback['character_tracking']}"
        )

    # 世界观理解：读者对设定的理解情况
    if feedback.get("world_comprehension"):
        parts.append(
            f"## WORLD COMPREHENSION\n{feedback['world_comprehension']}"
        )

    # 亮点：这些地方写得好，编辑修补时要避开，别改坏了
    if feedback.get("standout_moments"):
        parts.append("## MOMENTS THAT WORKED (preserve these)")
        for m in feedback["standout_moments"]:
            parts.append(f"- {m}")

    # 把所有板块用双换行连起来；如果什么都没有，给一句兜底说明
    return (
        "\n\n".join(parts) if parts
        else "(No specific issues — the reader just didn't enjoy it.)"
    )


def _capture_fix_tokens(
    response, model: str, *, book_id: str = "", chapter: int = 0,
) -> None:
    """Record FIX token usage — per node/chapter in the ledger.

    ``model`` is what actually served the chapter, so a re-tiered FIX is booked
    against the tier it ran on rather than a hard-coded constant.

    【中文·一句话说明】把这次编辑修补调用的 token 消耗记到账本里。

    【参数】
      response  大模型的返回对象，里面藏着 token 用量信息
      model     实际使用的模型名（用实际值而非固定常量，
                这样换了模型也能看出成本变化）
      book_id   书编号，用于按书归账
      chapter   章节号，用于按章归账

    【中文·两套记账系统的关系】同 readback.py 的 _capture_readback_tokens：
    精细账本（token_ledger）永远记；汇总统计（TranslationStats）
    只在 token 数据完整时才记。
    """
    usage_in, usage_out = token_ledger.extract_usage(response)
    token_ledger.record(
        "fix", model, usage_in, usage_out,
        book_id=book_id, chapter=chapter,
    )
    # Half a usage payload is not a figure worth accumulating into the
    # legacy counters; the ledger row above already carries whatever
    # arrived, plus the fact that it was incomplete.
    #
    # 【中文·token 数据不完整时跳过汇总统计】
    if usage_in is None or usage_out is None:
        return
    try:
        TranslationStats.record_tokens(
            input_tokens=usage_in,
            output_tokens=usage_out,
            tier="flash",
        )
    except Exception:
        pass  # 统计是辅助功能，失败了不影响主流程


def _parse_fix_response(content: str) -> dict:
    """Parse the FIX agent's JSON output with fallback.

    【中文·一句话说明】把编辑返回的 JSON 文本解析成 Python 字典。

    【参数】content —— 大模型返回的原始文本，期望是 JSON 格式

    【返回】解析后的字典，至少包含：
      · polished_text  修补后的英文全文
      · changes_made   改动清单（字符串列表）

    【中文·解析失败时的降级策略】
    和 readback.py 不同，FIX 的 fallback 把原始文本当作 polished_text 保留。
    理由：编辑已经花了钱调用了大模型，即使 JSON 解析失败，
    返回的文本大概率仍然是修补后的英文稿（只是没包成 JSON）。
    直接丢弃等于白花钱，不如先用着，让下一轮 READBACK 来判断质量。
    """
    from ..parse_utils import parse_llm_json

    fallback = {
        "polished_text": content,
        "changes_made": ["(Parser fallback — raw response returned)"],
    }
    result, _ = parse_llm_json(content, fallback)
    return result
