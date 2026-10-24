"""Node 3: READBACK — cold reader with no prior knowledge.

Reads the English output as a naive American reader. Does NOT see the Chinese
original. Does NOT know this is a translation. Reports honest experience.

Replaces: quality_check

【中文·这个文件是干什么的】
这是流水线里第三个角色——「毫不知情的外国读者」（READBACK）的实现。
它的唯一工作就是：**假装自己是个普通美国读者，读一遍英文成品，
然后诚实地说"哪儿看不懂、哪儿觉得无聊、还想不想接着看"**。

【中文·它在整个流程里的位置】
  WRITE 写完英文 → **READBACK 冷读打分** → 合格就收工 / 不合格就送 FIX 修补
被 graph.py 的 build_graph() 注册为 "readback_node" 节点；
它输出的 readback_feedback 字段会被 _needs_fix() 读取来决定走哪条岔路。

【中文·核心概念：为什么冷读者必须"不知道这是翻译"】
如果告诉 AI"这是翻译稿，请检查质量"，它会下意识宽容，倾向于说"还不错"。
只有让它当一个纯粹的外国读者，它才会诚实说"这段我看不懂"。
这等于给流水线装了一道**不掺水的质量关卡**，是整个项目最关键的设计之一。

【中文·冷读者看不到中文原文】
它只拿到英文成品 + 一份简短的"读者须知"（前情提要 + 人物表 + 术语表），
绝对不包含任何中文。这样它才能模拟真实外国读者的体验。
也正因为如此，它没能力发现"有含义的名字被音译成拼音"这类问题——
那道检查由 fidelity.py 用纯规则补上（见 graph.py _post_process）。
"""

import logging

# LangChain 的消息对象：SystemMessage 是"系统指令"（告诉 AI 扮演谁），
# HumanMessage 是"用户消息"（这里指喂给冷读者的章节内容）
from langchain_core.messages import HumanMessage, SystemMessage
# ChatOpenAI 是大模型的统一客户端，不管底层是 GLM 还是 Qwen，
# 都用同一个接口调用（类似"万能遥控器"）
from langchain_openai import ChatOpenAI

# 熔断器：当某个语言的 API 连续失败太多次时自动"跳闸"，
# 避免反复重试烧钱。CircuitBreakerOpenError 就是"跳闸了"的信号
from ...circuit_breaker import CircuitBreakerOpenError, get_breaker
# node_llm_kwargs：根据节点名（"readback"）查出该用哪个模型、
# 哪个服务商、什么密钥等配置，实现"每个角色可以用不同模型"
from ...config import node_llm_kwargs
from ..llm_fallback import is_content_filter_error
# TranslationStats：统计 API 调用次数和 token 用量，用于看板展示
from ...stats import TranslationStats
# token_ledger：按书/章/节点记录每次调用的 token 消耗，用于成本核算
from ... import token_ledger
# build_prompt_context：根据题材和目标语言，生成角色身份描述
# （比如"你是一个爱看奇幻小说的美国读者"）
from ..prompts.personas import build_prompt_context
# get_prompt_set：根据内容类型取一套提示词模板
from ..prompts.registry import get_prompt_set
# TranslatorState：流水线的"共享白板"数据结构
from ..state import TranslatorState

logger = logging.getLogger(__name__)


def readback_node(state: TranslatorState) -> dict:
    """READBACK: Cold-read the chapter as a naive reader in the target audience.

    Returns a dict with:
        - readback_feedback: cold reader's structured reaction
        - quality_score: mapped from the verdict (PASS=5.0, NEEDS_FIX=2.0)
        - quality_issues: comprehension/engagement issues if any

    【中文·一句话说明】让 AI 扮演一个毫不知情的外国读者，读完英文成品后
    给出结构化评价：读着顺不顺、有没有看不懂的地方、还想不想继续看。

    【参数】state —— 流水线共享白板，本函数从中读取：
      · translated_text     英文成品（必填，就是要评审的对象）
      · cold_read_context   读者须知（前情提要+人物表+术语表）
      · content_type        内容类型，决定用哪套提示词
      · genre               题材，决定冷读者的身份设定
      · target_lang         目标语言，决定用哪个熔断器和记账归属
      · api_key             调用方自带的密钥（BYOK）
      · book_id / chapter_number  用于记账和日志

    【返回】一个字典，写入白板的三个字段：
      · readback_feedback  冷读者的完整评价（含 verdict 判决）
      · quality_score      量化分数：PASS=5.0，NEEDS_FIX=2.0
      · quality_issues     具体问题列表（供后续诊断用）

    【中文·为什么 verdict 默认是 PASS】
    代码多处写了 `.get(..., "PASS")`：万一冷读者没返回结果（超时、解析失败），
    按"通过"处理直接放行。宁可对一章漏检，也不要因为一次网络抖动
    就整本卡死或触发无意义的重修。
    """
    translated_text = state.get("translated_text", "")

    # Skip if the chapter is empty or too short
    # 【中文·空章保护】如果英文成品为空或不到 50 字符，直接判 NEEDS_FIX。
    # 这种情况通常是 WRITE 节点出了问题（超时、返回空），不是真的"写得好"。
    # 50 字符大约是一句话的长度，比这还短说明内容缺失。
    if not translated_text or len(translated_text.strip()) < 50:
        return {
            "readback_feedback": {
                "overall_impression": "Chapter is empty or too short to evaluate.",
                "verdict": "NEEDS_FIX",
                "would_keep_reading": False,
            },
            "quality_score": 0.0,
            "quality_issues": ["EMPTY: Chapter output is empty or too short."],
        }

    # Model / provider / reasoning control come from the per-node routing table.
    # READBACK is the primary cost lever (config.py MODEL_MAP): it only judges
    # "can a reader follow this?", so XIDU_NODE_MODEL_READBACK can drop it to a
    # flash tier without a code change. Generation params below are READBACK's own.
    #
    # 【中文·选模型】从配置表里查"readback 这个角色该用哪个模型"。
    # READBACK 只需要判断"读者能不能跟上"，不需要创作能力，
    # 所以可以用更便宜的"闪速模型"（flash tier），省不少钱。
    # 改环境变量 XIDU_NODE_MODEL_READBACK 就能换模型，不用改代码。
    provider_kwargs = node_llm_kwargs(
        "readback", api_key_override=state.get("api_key", ""),
    )
    llm = _build_readback_client(**provider_kwargs)
    used_model = provider_kwargs["model"]

    # Build previous context for the cold reader
    # 【中文·取"读者须知"】这份须知由 graph.py 的 _build_cold_read_context()
    # 提前拼好，包含前情提要、人物表、术语表。
    # 如果没准备好（比如快速模式跳过了），就用空字符串兜底。
    cold_context = state.get("cold_read_context", "")
    if not cold_context:
        cold_context = ""

    prompts = get_prompt_set(state.get("content_type", "novel"))
    # Per-genre / per-language parameters for the cold reader's identity.
    # book_title stays neutral: the English titles for the corpus books are
    # still pending (§五 of the round-5 handoff), and a cold reader being told
    # a specific title gains nothing — "this book" keeps the reader blind and
    # avoids naming a genre the chapter may not be. Wire in state's title once
    # name_en is settled.
    #
    # 【中文·构建角色身份】根据题材和目标语言，生成冷读者的人设描述。
    # 注意 book_title 故意写成 "this book" 而不是真实书名：
    # ① 书的英文名还没定下来；② 告诉冷读者具体书名可能暴露题材，
    #    影响它的"盲读"体验。等书名确定后再换成真实值。
    ctx = build_prompt_context(
        state.get("genre"), state.get("target_lang", "en-US"),
    )
    ctx["book_title"] = "this book"
    # 【中文·填充提示词模板】把读者须知、章节内容、读者身份塞进模板
    user_prompt = prompts.readback_user.format(
        previous_context=cold_context,
        chapter_content=translated_text,
        reader=ctx["reader"],
    )

    # 【中文·组装消息】两条消息发给大模型：
    # ① SystemMessage = "你是谁、该怎么评"（角色指令）
    # ② HumanMessage  = "请读这一章"（实际内容）
    messages = [
        SystemMessage(content=prompts.readback_system.format(**ctx)),
        HumanMessage(content=user_prompt),
    ]

    # 【中文·获取熔断器】按目标语言取对应的熔断器实例。
    # 每种语言独立计数，英语挂了不影响西班牙语的翻译。
    target_lang = state.get("target_lang", "en-US")
    breaker = get_breaker(target_lang)

    try:
        # 记一笔"发起了一次 API 调用"
        TranslationStats.record_api_call(target_lang)
        # 通过熔断器调用大模型。如果熔断器已跳闸，这里会直接抛异常
        response = breaker.call(llm.invoke, messages)
        # 调用成功，记一笔
        TranslationStats.record_api_success(target_lang)
    except CircuitBreakerOpenError:
        # 熔断器跳闸了：该语言的 API 连续失败太多，暂时停止调用
        TranslationStats.record_api_failure(target_lang)
        raise  # 向上抛，让流水线知道这章失败了
    except Exception as exc:
        # 其他异常（网络超时、模型返回错误等）
        TranslationStats.record_api_failure(target_lang)
        # 【中文·Qwen 内容过滤兜底】Qwen 审查拦截时换 GLM 重试一次，
        # 其他错误仍向上抛。GLM 比 Qwen 更严，此兜底是"尽力而为"。
        if not is_content_filter_error(exc):
            raise
        fallback_kwargs = node_llm_kwargs(
            "readback_fallback", api_key_override=state.get("api_key", "")
        )
        used_model = fallback_kwargs["model"]
        logger.warning(
            "READBACK ch%d: content filter rejected — retrying once on %s",
            state["chapter_number"], used_model,
        )
        try:
            from ...error_tracker import record_event
            record_event(
                None, state["chapter_number"], "content_filter_fallback",
                f"Qwen content filter on ch{state['chapter_number']}; retried on {used_model}",
                target_lang,
            )
        except Exception:
            pass
        if not fallback_kwargs["api_key"]:
            raise
        response = breaker.call(_build_readback_client(**fallback_kwargs).invoke, messages)
        TranslationStats.record_api_success(target_lang)

    # 记录本次调用的 token 消耗（主 provider 或兜底 provider 的最终结果）
    _capture_readback_tokens(
        response,
        model=used_model,
        book_id=state.get("book_id", ""),
        chapter=state.get("chapter_number", 0),
    )

    # 【中文·解析冷读者的回复】大模型返回的是 JSON 格式的评审意见，
    # 这里把它解析成 Python 字典。解析失败时降级为 NEEDS_FIX（宁可误杀不可漏放）
    feedback = _parse_readback_response(response.content)

    # 【中文·把判决映射成分数】
    # PASS = 5.0（满分），NEEDS_FIX = 2.0（不及格）
    # 这个分数会写入白板，供看板和诊断使用
    verdict = feedback.get("verdict", "PASS")
    quality_score = 5.0 if verdict == "PASS" else 2.0

    # 【中文·提取具体问题】从冷读者的反馈里挑出两类问题，
    # 格式化成人类可读的字符串列表：
    #   COLD_READER: 理解障碍（读者看不懂的地方）
    #   BORED:       趣味缺口（读者觉得无聊想跳过的地方）
    # [:80] 截断引用的原文片段，防止某段特别长把日志撑爆
    issues = []
    for ci in feedback.get("comprehension_issues", []):
        issues.append(
            f"COLD_READER: {ci.get('passage', '?')[:80]}"
            f" — {ci.get('issue', '?')}"
        )
    for eg in feedback.get("engagement_gaps", []):
        issues.append(
            f"BORED: {eg.get('passage', '?')[:80]}"
            f" — {eg.get('issue', '?')}"
        )

    # 写一条摘要日志，方便在运行日志里快速看到每章的冷读结果
    logger.info(
        "READBACK ch%d: verdict=%s, would_keep=%s, issues=%d",
        state["chapter_number"],
        verdict,
        feedback.get("would_keep_reading", False),
        len(issues),
    )

    # 【中文·不合格时记一条错误事件】
    # 方便事后统计"哪些章节被冷读者打回过"，用于分析翻译质量趋势。
    # record_event 可能不存在（老版本没有这个模块），所以用 try/except 兜底。
    if verdict == "NEEDS_FIX":
        try:
            from ...error_tracker import record_event
            record_event(
                None, state["chapter_number"],
                "cold_read_fail",
                f"Cold reader verdict: NEEDS_FIX — {len(issues)} issues",
                target_lang,
            )
        except Exception:
            pass  # 错误追踪是辅助功能，失败了不影响主流程

    # 【中文·把结果写回白板】这三个字段会被后续的岔路口和 _post_process 读取
    return {
        "readback_feedback": feedback,
        "quality_score": quality_score,
        "quality_issues": issues,
    }


def _build_readback_client(**provider_kwargs) -> ChatOpenAI:
    """A READBACK client on the routed provider (primary or content-filter fallback)."""
    return ChatOpenAI(  # type: ignore[call-arg]
        temperature=0.1,    # 低温度 = 输出稳定，评审结果可复现
        max_tokens=4096,    # 评审意见不需要太长，4096 够用
        request_timeout=120,  # 2 分钟超时，防卡死
        max_retries=0,      # 不自动重试，由熔断器统一管理
        **provider_kwargs,  # 注入模型名、base_url、api_key 等
    )


def _capture_readback_tokens(
    response, model: str, *, book_id: str = "", chapter: int = 0,
) -> None:
    """Record READBACK token usage — per node/chapter in the ledger.

    ``model`` is what actually served the chapter: READBACK is the node most
    likely to be re-tiered, and booking its tokens against a constant would hide
    exactly the saving the re-tier was meant to produce.

    【中文·一句话说明】把这次冷读调用的 token 消耗记到账本里。

    【参数】
      response  大模型的返回对象，里面藏着 token 用量信息
      model     实际使用的模型名（不是固定常量，因为 READBACK 最可能被
                换成便宜模型，记固定名就看不出省了多少）
      book_id   书编号，用于按书归账
      chapter   章节号，用于按章归账

    【中文·为什么用实际模型名而不是常量】
    READBACK 是最常被"降级"到便宜模型的节点。如果记账时写死模型名，
    就看不出降级到底省了多少钱——而这正是降级要验证的核心指标。

    【中文·两套记账系统的关系】
    ① token_ledger.record()  精细账本，按书/章/节点/模型四维记录
    ② TranslationStats       汇总统计，只看总量，用于看板展示
    精细账本永远记；汇总统计只在 token 数据完整时才记（缺一半就不记，
    免得污染平均值）。
    """
    usage_in, usage_out = token_ledger.extract_usage(response)
    token_ledger.record(
        "readback", model, usage_in, usage_out,
        book_id=book_id, chapter=chapter,
    )
    # Half a usage payload is not a figure worth accumulating into the
    # legacy counters; the ledger row above already carries whatever
    # arrived, plus the fact that it was incomplete.
    #
    # 【中文·token 数据不完整时跳过汇总统计】
    # 有些模型返回的用量信息只有输入或只有输出，这种"半截数据"
    # 不适合累加到汇总计数器里（会拉低平均值）。
    # 精细账本已经记了原始值，不缺这一份。
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


def _parse_readback_response(content: str) -> dict:
    """Parse the READBACK agent's JSON output with fallback.

    Falls back to NEEDS_FIX on parse failure — a garbled LLM response
    must NOT silently pass quality gate.

    【中文·一句话说明】把冷读者返回的 JSON 文本解析成 Python 字典。

    【参数】content —— 大模型返回的原始文本，期望是 JSON 格式

    【返回】解析后的字典，至少包含以下字段：
      · overall_impression  总体印象
      · verdict             判决："PASS" 或 "NEEDS_FIX"
      · would_keep_reading  是否愿意继续读
      · comprehension_issues  理解障碍列表
      · engagement_gaps       趣味缺口列表
      · standout_moments      亮点列表
      · character_tracking    人物追踪评价
      · world_comprehension   世界观理解评价

    【中文·解析失败时的降级策略】
    如果大模型返回的不是合法 JSON（比如吐了一段自然语言），
    parse_llm_json 会返回下面定义的 fallback 字典。
    ⚠️ fallback 的 verdict 是 NEEDS_FIX 而不是 PASS：
    解析失败意味着"我们不知道冷读者说了什么"，
    这种情况绝不能当作"通过了"放行，否则质量关卡形同虚设。
    宁可多修一轮（花点钱），也不能让有问题的章节溜过去。
    """
    from ..parse_utils import parse_llm_json

    fallback = {
        "overall_impression": "Parse failed — could not evaluate chapter.",
        "verdict": "NEEDS_FIX",
        "would_keep_reading": False,
        "comprehension_issues": [],
        "engagement_gaps": [],
        "standout_moments": [],
        "character_tracking": "",
        "world_comprehension": "",
    }
    result, _ = parse_llm_json(content, fallback)
    return result
