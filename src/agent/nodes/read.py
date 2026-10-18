"""Node 1: READ — Chinese web novel reader analysis.

Does NOT translate. Reads the chapter, experiences it, and produces a
structured analysis that becomes the creative brief for the WRITE agent.

【中文·这个文件是干什么的】
这是流水线里的第一个角色——"中文网文老书虫"（READ）。
它**不做翻译**，只做分析：读完一章中文原文后，输出一份结构化的"创作简报"，
告诉下游的 WRITE 角色：这一章有哪些文化梗、哪些画面缺口需要补、
术语该怎么译、节奏怎么把控。

【中文·它在整个流程里的位置】
在 graph.py 里注册为 "read_node"，是流水线的入口节点：
  START → **READ** → REVIEW_TERMS → WRITE → ...
被 build_graph() 用 lambda 包装后注册（因为除了 state 还需要术语库参数）。
它的产出（read_analysis）会被 REVIEW_TERMS 复核术语、被 WRITE 当作创作依据。

【中文·核心概念：画面缺口（sensory image gap）】
中文读者看到"鬼节"会自动脑补出香火、纸钱、屏息的村庄；
英文读者只看到一个标签 "Ghost Festival"，画面建立不起来，场景就废了。
READ 的职责就是找出这些"中文读者自动脑补、但英文读者看不到"的感官细节，
给出"感官锚点"（比如：the acrid smoke of joss paper, the hushed village），
让 WRITE 能在英文版里重建同样的画面感。

【中文·本文件里三个函数】
  _build_context_signals()  汇总 9 个自动检测器的信号（方言、成语、拟声词等）
  read_node()              主函数：组装提示词 → 调用大模型 → 解析结果
  _capture_read_tokens()   记录本次调用的 token 用量（记账）
  _parse_read_response()   解析大模型返回的 JSON 分析结果
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

# 【中文·熔断器（circuit breaker）】一种保护机制：
# 如果大模型 API 连续失败多次，熔断器会"跳闸"，后续调用直接报错而不发请求。
# 防止 API 已经挂了还不停重试，白白烧钱和浪费时间。
# 恢复一段时间后自动尝试重新接通。
from ...circuit_breaker import CircuitBreakerOpenError, get_breaker
# 【中文·节点级模型路由表】每个角色可以用不同的模型/服务商。
# node_llm_kwargs("read") 返回 READ 角色该用的模型名、API 密钥、base_url 等。
# 好处：切换模型只改环境变量，不用改代码。
from ...config import node_llm_kwargs
# 【中文·文化规则加载器】根据目标语言和小说类型，加载对应的文化适配规则。
# 比如翻仙侠时"筑基"不能译成 "foundation building"（字面意思完全不对）。
from ...cultural_rules import (
    format_fidelity_for_prompt,
    format_rules_as_bullets,
    load_fidelity_rules,
    load_rules,
)
# 【中文·术语库两层存储】
# ExactGlossary   —— 精确匹配（中文原词一模一样才命中），存在 SQLite 里
# SemanticGlossary—— 语义相近匹配（意思相关的也算），存在向量库里
# 两者配合：精确匹配保证已知术语一致，语义匹配兜底新出现的近义表达。
from ...glossary.exact_store import ExactGlossary
from ...glossary.semantic_store import SemanticGlossary
# 【中文·翻译统计计数器】记录 API 调用次数、成功/失败次数、token 用量。
from ...stats import TranslationStats
# 【中文·token 账本】按 book_id + chapter 粒度记录每次调用的 token 消耗，
# 用于成本核算。和 TranslationStats 的区别：Stats 是全局汇总，ledger 是逐条明细。
from ... import token_ledger
# 【中文·提示词上下文构建器】根据小说类型和目标语言，生成系统提示词需要的
# 占位符值（如 audience 描述、语言名称等）。
from ..prompts.personas import build_prompt_context
# 【中文·提示词注册表】根据 content_type 获取对应的一套提示词模板。
# 目前只有 "novel" 一种经过验证的分支。
from ..prompts.registry import get_prompt_set
# 【中文·共享白板数据结构】见 state.py 的详细注释。
from ..state import TranslatorState

logger = logging.getLogger(__name__)


def _build_context_signals(state: TranslatorState) -> str:
    """Aggregate all 9 context signal detectors into one text block.

    Each detector is called and its output (if any) is collected. The READ
    agent uses these as STARTING POINTS for its own investigation, not as
    conclusions. Signals may be incomplete, wrong, or irrelevant to this
    specific chapter.

    【中文·一句话作用】
    跑一遍 9 个自动检测器，把有输出的拼成一段文本，作为 READ 的"起步线索"。

    【参数】
      state  共享白板，从中取 chapter_content（章节原文）和 target_lang。

    【返回】
      拼接后的信号文本。如果所有检测器都没输出，返回 "(No auto-detected signals.)"。

    【为什么存在】
    有些语言现象可以用规则快速检测（不需要调大模型），比如：
    有没有方言对白、有没有系统面板标记（LitRPG）、有没有度量衡单位、
    有没有拟声词、有没有成语、有没有敏感词。
    这些检测结果不是定论，只是给 READ 一个"你可能需要注意这里"的提示，
    READ 自己读了原文后再判断是否采纳。

    【中文·9 个检测器分别是】
    ① dialect        —— 方言检测（粤语、四川话等）
    ② system_text    —— 游戏系统面板标记（[系统提示]、【任务完成】等）
    ③ measurements   —— 度量衡单位（斤、两、丈等中国传统单位）
    ④ onomatopoeia   —— 拟声词（哗啦、轰隆等）
    ⑤ idioms         —— 成语/俗语
    ⑥ sensitive_terms—— 敏感词（政治、宗教等在目标语言中需谨慎处理的词）
    其余 3 个检测器在其他模块中，此处通过 import 按需调用。

    【中文·为什么 import 放在函数内部而不是文件顶部】
    这些模块（dialect、idioms 等）只在构建信号时才需要，
    放在函数内实现延迟导入（lazy import）：如果某个检测器模块有 bug
    或缺失依赖，不影响 read_node 的其他功能。
    """
    # 延迟导入：只在需要时才加载这些模块
    from ...dialect import build_dialect_context, has_system_text
    from ...idioms import build_idiom_context
    from ...measurements import build_measurements_hint
    from ...onomatopoeia import build_onomatopoeia_context
    from ...sensitive_terms import build_sensitive_term_context

    chapter = state["chapter_content"]
    target_lang = state.get("target_lang", "en-US")

    # 收集所有非空的检测结果
    signals = []

    # ① 方言检测
    d = build_dialect_context(chapter)
    if d:
        signals.append(d)

    # ② 游戏系统面板标记检测
    # has_system_text 只返回 True/False，不像其他检测器返回格式化文本，
    # 所以这里手动构造一条提示消息
    if has_system_text(chapter):
        signals.append(
            "[AUTO-DETECTED] Game-like system notification markers found in "
            "this chapter — possible LitRPG elements. Verify by reading."
        )

    # ③ 度量衡单位检测
    m = build_measurements_hint(chapter)
    if m:
        signals.append(m)

    # ④ 拟声词检测
    o = build_onomatopoeia_context(chapter)
    if o:
        signals.append(o)

    # ⑤ 成语/俗语检测
    i = build_idiom_context(chapter)
    if i:
        signals.append(i)

    # ⑥ 敏感词检测（需要知道目标语言，因为不同语言的敏感点不同）
    s = build_sensitive_term_context(chapter, target_lang)
    if s:
        signals.append(s)

    # 拼接所有信号，加上醒目的标题头提醒 READ "这只是线索不是定论"
    header = (
        "[AUTO-DETECTED SIGNALS — starting points for your reading, "
        "not conclusions. Verify by reading the actual text.]\n\n"
    ) if signals else ""
    return header + "\n\n".join(signals) if signals else "(No auto-detected signals.)"


def read_node(
    state: TranslatorState,
    exact_store: ExactGlossary,
    semantic_store: SemanticGlossary,
) -> dict:
    """READ: Analyze the chapter as a Chinese web novel reader.

    Returns a dict with:
        - read_analysis: the READ agent's structured analysis (JSON)
        - context_signals: aggregated context signal text

    【中文·一句话作用】
    扮演"中文网文老书虫"读一章原文，输出一份结构化分析供 WRITE 使用。

    【参数】
      state          共享白板，包含章节原文、语言、类型等全部输入信息。
      exact_store    精确术语库（中文原词完全一致才命中）。
                     虽然本函数不直接查询它，但通过 state 里的
                     exact_matches_text 间接使用了查询结果。
      semantic_store 语义术语库（意思相近也能命中）。同上。

    【返回】
      一个字典，会被引擎合并到共享白板上：
        read_analysis    READ 的结构化分析（dict），包含情感走向、文化差异、
                         画面缺口、术语决策等
        context_signals  9 个检测器的汇总信号文本
        image_gaps       画面缺口列表（从 read_analysis 中提取，方便下游直接读取）

    【为什么存在】
    这是整条流水线的起点。没有 READ 的分析，WRITE 就只能盲翻，
    无法处理文化差异和画面缺口——而这恰恰是本项目的核心价值。

    【坑】
    · max_retries=0：不让 LangChain 自动重试。重试逻辑由熔断器统一管理，
      避免两套重试机制叠加导致等待时间不可控。
    · temperature=0.3：较低但不为零。READ 需要一定的创造性来发现画面缺口，
      纯确定性输出（temperature=0）可能遗漏微妙的文化细节。
    · request_timeout=900：超时 15 分钟。READ 是最耗时的节点（要读完整章+深度分析），
      比其他节点的超时都长。
    """
    # ── 构建大模型客户端 ──
    # Model / provider / reasoning control come from the per-node routing table
    # (config.node_llm_kwargs) so a tier change is one env var, not a code edit.
    # Generation parameters below are READ's own (deep cultural-gap analysis).
    #
    # 【中文】从路由表获取 READ 该用的模型配置（模型名、密钥、base_url）。
    # api_key_override：如果用户在界面上提供了自己的密钥（BYOK），优先用它。
    provider_kwargs = node_llm_kwargs("read", api_key_override=state.get("api_key", ""))
    llm = ChatOpenAI(  # type: ignore[call-arg]
        temperature=0.3,       # 低但有创造性：分析需要洞察，不是纯判断题
        max_tokens=16384,      # READ 的输出很长（详细分析），给足空间
        request_timeout=900,   # 15 分钟超时：READ 是最慢的节点
        max_retries=0,         # 不自动重试，由熔断器统一管理
        **provider_kwargs,     # 展开路由表的配置（model、api_key、base_url 等）
    )
    # 记录实际使用的模型名（路由表可能把 READ 分配到不同档次的模型）
    used_model = provider_kwargs["model"]

    target_lang = state.get("target_lang", "en-US")
    genre = state.get("genre", "romance_ceo")

    # ── 加载文化规则 ──
    # Load cultural rules for the READ agent's reference. book_id is passed so
    # a book's own proper nouns (see load_rules docstring) are applied to that
    # book only, not to every other book sharing its genre.
    #
    # 【中文】加载这本书+这个类型的文化适配规则。
    # 传入 book_id 是为了隔离：同一类型下不同书的专有名词不会互相污染。
    # 比如两本仙侠小说，A 书的"青云门"和 B 书的"青云门"可能是完全不同的东西。
    rules = load_rules(target_lang=target_lang, genre=genre,
                       book_id=state.get("book_id", ""))
    cultural_rules_bullets = format_rules_as_bullets(rules)

    # Strategy-level cultural fidelity rules (how to render a CATEGORY of terms)
    # 【中文】加载"策略级"文化忠实度规则。
    # 和上面的具体规则不同，这里是**类别级别**的指导方针，
    # 比如"人名应该意译还是音译""功法名应该怎么处理"这类通用原则。
    fidelity_rules = format_fidelity_for_prompt(
        load_fidelity_rules(target_lang),
    ) or "(No cultural fidelity rules for this language.)"

    # ── 汇总自动检测信号 ──
    # Aggregate context signals
    context_signals = _build_context_signals(state)

    # ── 准备提示词素材 ──
    # Build exact matches text from glossary
    # 【中文】从状态里取已格式化好的术语表文本。
    # 这些文本是在 read_node 之前由 TranslationAgent 查好术语后填入的，
    # READ 自己不做术语查询（职责分离：查询归 Agent，分析归 READ）。
    exact_text = state.get("exact_matches_text", "(No glossary terms yet.)")

    # 【中文】翻译经验本：前面所有章节积累的翻译教训。
    # 第一章时为空，越翻越厚。塞进提示词让 READ 不重复前人的错误。
    memo = state.get("style_memo", "(No style memo yet — this is the first chapter.)")
    # 【中文】根据内容类型获取对应的提示词模板集。
    prompts = get_prompt_set(state.get("content_type", "novel"))
    # Per-genre / per-language parameters for the system prompt. The system
    # prompt embeds JSON examples with doubled braces, so .format() renders the
    # placeholders and leaves the JSON literal.
    #
    # 【中文】构建系统提示词的上下文参数（受众描述、语言名称等）。
    # 注意：提示词模板里的 JSON 示例用了双花括号 {{ }}，
    # Python 的 .format() 会把双花括号渲染成单花括号，不会误当成占位符。
    ctx = build_prompt_context(genre, target_lang)
    # 【中文】把所有素材填入用户提示词模板。
    # 这是一个大型模板渲染：章节号、标题、类型、目标语言、受众、
    # 前章摘要、术语表、文化规则、忠实度规则、检测信号、原文全文，
    # 全部注入到一个长提示词里发给大模型。
    user_prompt = prompts.read_user.format(
        style_memo=memo,
        chapter_number=state["chapter_number"],
        chapter_title=state["chapter_title"],
        genre=genre,
        target_language=target_lang,
        audience=ctx["audience"],
        previous_summary=state.get("previous_chapter_summary", "(This is the first chapter.)"),
        exact_matches=exact_text,
        cultural_rules_table=(
            cultural_rules_bullets if cultural_rules_bullets else "(No genre-specific rules.)"
        ),
        fidelity_rules=fidelity_rules,
        context_signals=context_signals,
        chapter_content=state["chapter_content"],
    )

    # ── 组装消息并调用大模型 ──
    messages = [
        # 系统消息：设定 READ 的角色身份和行为准则
        SystemMessage(content=prompts.read_system.format(**ctx)),
        # 用户消息：本章的全部上下文和分析指令
        HumanMessage(content=user_prompt),
    ]

    # 【中文·通过熔断器调用大模型】
    # breaker.call() 代替直接 llm.invoke()：如果 API 连续失败，
    # 熔断器会跳闸，后续调用直接抛 CircuitBreakerOpenError 而不发请求。
    breaker = get_breaker(target_lang)
    try:
        # 记录一次 API 调用（不管成功失败都计数）
        TranslationStats.record_api_call(target_lang)
        response = breaker.call(llm.invoke, messages)
        # 调用成功，记录成功计数
        TranslationStats.record_api_success(target_lang)
        # 记录 token 消耗到明细账本
        _capture_read_tokens(
            response,
            model=used_model,
            book_id=state.get("book_id", ""),
            chapter=state.get("chapter_number", 0),
        )
    except CircuitBreakerOpenError:
        # 熔断器跳闸：API 近期连续失败，暂时不再尝试
        TranslationStats.record_api_failure(target_lang)
        raise  # 向上抛出，由 TranslationAgent 决定怎么处理
    except Exception:
        # 其他异常（网络超时、API 返回错误等）
        TranslationStats.record_api_failure(target_lang)
        raise

    # ── 解析大模型的 JSON 分析结果 ──
    # Parse the READ agent's analysis
    analysis = _parse_read_response(response.content)

    # 记一条日志，方便排查"READ 是不是漏分析了什么"
    logger.info(
        "READ ch%d: %d cultural gaps, %d image gaps, %d terms",
        state["chapter_number"],
        len(analysis.get("cultural_gaps", [])),
        len(analysis.get("image_gaps", [])),
        len(analysis.get("terminology_decisions", [])),
    )

    # 返回三个字段，引擎会把它们合并到共享白板上
    return {
        "read_analysis": analysis,           # 完整分析结果
        "context_signals": context_signals,  # 检测信号（留给下游参考）
        "image_gaps": analysis.get("image_gaps", []),  # 单独提取画面缺口
    }


def _capture_read_tokens(response, model: str, *, book_id: str = "", chapter: int = 0) -> None:
    """Record READ token usage — per node/chapter in the ledger, plus legacy counters.

    ``model`` is the model that actually served the chapter, not a constant: the
    routing table can send READ to another tier, and booking its tokens against a
    hard-coded name would put the cost on the wrong model — the same defect
    write.py already fixed with ``used_model``.

    The ledger row is written even when the provider sent no usage payload: it is
    recorded as unmetered so the gap is countable instead of invisible.

    【中文·一句话作用】
    把这次 READ 调用的 token 消耗记到账本里（明细 + 汇总两处都记）。

    【参数】
      response  大模型返回的响应对象，里面可能包含 usage 字段（token 用量）。
      model     实际提供服务的模型名（不是写死的常量）。
                ⚠️ 必须传真实模型名：路由表可能把 READ 分到不同档次的模型，
                如果用固定名字记账，成本就会算到错误的模型头上。
                write.py 曾经因为这个 bug 修过一次，这里沿用了同样的修法。
      book_id   这本书的编号，用于账单归属。
      chapter   章节序号，用于定位"哪一章花了多少钱"。

    【返回】无（纯副作用函数）。

    【为什么存在】
    翻译一本书要花不少钱，必须精确记录每次调用的 token 消耗，
    才能算清每本书、每章、每个角色的成本。
    同时记两处：token_ledger（逐条明细）和 TranslationStats（全局汇总），
    前者用于精细核算，后者用于看板展示。

    【坑】
    当大模型没返回 usage 字段时（某些服务商或模型不支持），
    仍然会在 ledger 里记一行"未计量"（unmetered），
    这样事后能统计出"有多少次调用没拿到用量数据"，而不是默默丢失。
    """
    # 从响应对象里提取 input/output token 数
    # 如果服务商没返回 usage，两个值都是 None
    usage_in, usage_out = token_ledger.extract_usage(response)
    # 写入明细账本（即使 usage 是 None 也会记一行 unmetered）
    token_ledger.record("read", model, usage_in, usage_out,
                        book_id=book_id, chapter=chapter)
    # Half a usage payload is not a figure worth accumulating into the
    # legacy counters; the ledger row above already carries whatever
    # arrived, plus the fact that it was incomplete.
    #
    # 【中文】如果 usage 不完整（只有一个方向有值或都没有），
    # 就不往汇总计数器里加了——半截数据加进去反而让统计失真。
    # 明细账本那一行已经记录了实际到达的数据和不完整的事实。
    if usage_in is None or usage_out is None:
        return
    try:
        # 写入全局汇总计数器（tier="pro" 表示这是主力模型的用量）
        TranslationStats.record_tokens(
            input_tokens=usage_in,
            output_tokens=usage_out,
            tier="pro",
        )
    except Exception:
        # 汇总计数失败不影响主流程（明细已经记了）
        pass


def _parse_read_response(content: str) -> dict:
    """Parse the READ agent's JSON output with fallback.

    【中文·一句话作用】
    解析 READ 返回的 JSON 分析结果；解析失败就给一份空壳分析。

    【参数】
      content  READ 大模型返回的原始文本。

    【返回】
      解析出的分析字典。如果解析失败，返回兜底值：
        emotional_arc 标记为解析失败、各列表字段为空、pacing_notes 为空。

    【为什么存在】
    把解析逻辑和兜底值集中在一处，read_node 主函数保持简洁。
    兜底值的设计原则：让 WRITE 能继续工作（哪怕没有分析指导），
    而不是让整个流水线崩掉。

    【坑】
    兜底值里 emotional_arc 写了明确的错误提示文本，
    这样如果下游看到了这段文字，就知道 READ 的分析没拿到，
    而不是误以为"这一章没有情感走向"。
    """
    from ..parse_utils import parse_llm_json

    # 兜底值：解析失败时给 WRITE 一份"最低限度可用"的空壳分析
    fallback = {
        "emotional_arc": "Parse failed — READ agent output could not be parsed as JSON.",
        "cultural_gaps": [],
        "terminology_decisions": [],
        "pacing_notes": "",
        "crafted_moments": [],
        "image_gaps": [],
    }
    # parse_llm_json 会尝试多层解析（去围栏 → 直接 loads → 正则提取 → 兜底）
    result, _ = parse_llm_json(content, fallback)
    return result
