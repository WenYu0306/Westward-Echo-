"""Node 2: WRITE — bilingual genre writer who retells the chapter in English.

Receives the READ agent's analysis as creative brief. Does NOT use tool calls.
Is a storyteller, not a translation machine.

════════════════════════════════════════════════════════════════════════
【中文·WRITE 节点：英文类型小说作家】
════════════════════════════════════════════════════════════════════════

一、这个文件是干什么的？
    实现流水线里的 WRITE 角色——「英文类型小说作家」。
    它拿到 READ（中文网文老书虫）的文化分析、画面缺口、术语表、
    翻译经验本，然后用英文把这一章"重讲"一遍。
    注意：它的身份是**作家**不是**翻译**，允许重构语序、压缩啰嗦、
    补上必要的画面感，目标是让英文读者读起来像原生英文小说。

二、它在整个流程里的位置：
    上游：review_terms_node（术语复核）→ 把审核过的术语和分析传进来
    下游：readback_node（冷读者挑刺）← 把写好的英文稿交出去
    分流：写完后可走快速模式跳过冷读（skip_readback=True → END），
          或走完整质量检查（→ readback_node）。
    调用者：graph.py 里 build_graph() 把它注册为 "write_node"。

三、核心概念解释：
    · 画面缺口（image gap）：中文读者看到"鬼节"自动脑补香火纸钱，
      英文读者只看到一个标签，场景就废了。READ 找出这些缺口，
      WRITE 负责用感官细节重建画面。
    · 内容过滤兜底（1301 fallback）：GLM 的安全层有时会误拦真实网文
      （错误码 1301），此时自动切换到 Qwen 重写本章，避免交付被审查
      删减的译文。这是本项目"绝不交付删减版"的核心保障。
    · 多层 JSON 解析：AI 返回的 JSON 经常格式不完美（多了 markdown
      围栏、截断、字段逃逸等），所以用了 5 层递进式解析来抢救。
    · 输出质量守卫：写完还要过几道检查——有没有残留中文、有没有
      JSON 外壳泄漏、有没有被截断，不合格就清空触发重试。

四、本文件里几个主要函数分别干什么：
    write_node()             —— 主入口，串起整章写作+质量检查+重试
    _format_read_analysis()  —— 把 READ 的分析字典格式化成提示词文本
    _format_image_gaps()     —— 把画面缺口列表格式化成感官重建指令
    _capture_response_tokens()—— 记录本次调用的 token 用量和费用
    _was_truncated()         —— 检测 AI 回复是否被 token 上限截断
    _is_content_filter_error()—— 判断异常是否是 GLM 1301 内容过滤
    _build_fallback_writer() —— 构建兜底用的 Qwen 客户端
    _parse_write_response()  —— 5 层递进式 JSON 解析，抢救各种畸形回复
"""

# 【中文·导入区】下面这些 import 是 WRITE 节点用到的全部外部零件。
import json  # JSON 解析：AI 返回的是 JSON 格式，需要用它提取译文
import logging  # 日志：记录重试、截断、过滤等关键事件
import re  # 正则表达式：多层 JSON 解析的抢救手段之一

# LangChain 的消息类型和 OpenAI 兼容客户端
# （虽然实际调用的是 GLM/Qwen，但它们都兼容 OpenAI 接口协议）
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

# Token 账本：精确记录每次调用的 token 用量，用于算钱
from ... import token_ledger

# 熔断器：当某个 provider 连续失败时自动"跳闸"，防止反复烧钱
from ...circuit_breaker import CircuitBreakerOpenError, get_breaker

# 完整性检查：对比原文和译文，看有没有整段被漏掉
from ...completeness import check_completeness

# 按节点名取模型配置（WRITE 用 GLM flash，兜底用 Qwen）
from ...config import node_llm_kwargs

# 文化忠实度规则：确保人名/称谓不被简单音译成拼音
from ...cultural_rules import format_fidelity_for_prompt, load_fidelity_rules

# 任务存储：读取人工确认/拒绝的术语对
from ...job_store import job_store

# 统计计数器：记录 API 调用次数和成功/失败
from ...stats import TranslationStats

# 共享的内容过滤错误判断（GLM 1301 / Qwen 审查），四个节点共用
from ..llm_fallback import is_content_filter_error as _is_content_filter_error

# 构建提示词里的角色/语种上下文（如"你是写给美国读者看的"）
from ..prompts.personas import build_prompt_context

# 按内容类型（novel/poetry 等）取对应的提示词模板集
from ..prompts.registry import get_prompt_set

# 各目标语言的文体风格备注（如西班牙语要用 tú 还是 usted）
from ..prompts.translation import LANGUAGE_STYLE_NOTES

# 流水线的共享状态数据结构
from ..state import TranslatorState

logger = logging.getLogger(__name__)

# Outputs shorter than this are treated as empty and trigger a retry.
# 【中文】AI 返回的译文如果少于 10 个字符，视为"空输出"，触发重试。
# 为什么不是 0：有时 AI 会返回几个空格或一个标点，技术上非空但毫无内容，
# 设一个低阈值比判空更稳健。
RETRY_THRESHOLD = 10


def write_node(state: TranslatorState) -> dict:
    """WRITE: Retell the chapter in English using the READ agent's analysis.

    Returns a dict with:
        - translated_text: the complete English chapter
        - new_terms_found: newly discovered terms for the glossary
        - adaptation_notes: significant adaptation decisions
        - chapter_summary: 3-4 sentence plot summary for next chapter

    【中文·WRITE 节点主入口：用英文重讲一章】

    作用：拿到 READ 的分析结果，调一次大模型写出英文成品。
    这是整条流水线里**唯一产出译文**的节点，也是最贵的一步。

    参数：
      state —— 流水线共享状态（字典），里面装着：
        · chapter_number   当前章号
        · chapter_content  中文原文
        · chapter_title    章节标题
        · read_analysis    READ 角色的分析结果（文化缺口、术语建议等）
        · image_gaps       画面缺口列表（需要感官重建的段落）
        · style_memo       翻译经验本（前面所有章节积累的经验）
        · exact_matches_text   精确匹配的术语表文本
        · semantic_matches_text 语义匹配的术语表文本
        · previous_chapter_summary 上一章的剧情摘要
        · target_lang      目标语言代码（如 "en-US"）
        · content_type     内容类型（novel/poetry 等）
        · genre            题材标签（romance_ceo/xianxia 等）
        · skip_readback    是否跳过冷读（快速模式）
        · api_key          可选的临时 API key

    返回：一个字典，包含 5 个字段：
      · translated_text    英文译文正文（最核心的产出）
      · chapter_title_en   英文章节标题
      · new_terms_found    本章新发现的术语（存入术语库供后续章节使用）
      · adaptation_notes   重大改编决策记录
      · chapter_summary    3~4 句剧情摘要（喂给下一章的 WRITE 做上下文）

    为什么存在：READ 只分析不写，WRITE 只写不分析，分工明确才能各自优化。
    """
    chapter_number = state["chapter_number"]
    # Which model actually served this chapter. The GLM-1301 fallback below
    # switches providers mid-chapter; without this the fallback call was booked
    # against GLM and its cost landed on the wrong model.
    # Model / provider / reasoning control come from the per-node routing table.
    #
    # 【中文】记录"这一章实际用了哪个模型"。
    # 因为下面 1301 兜底逻辑可能中途把 GLM 换成 Qwen，
    # 如果不单独记，兜底那次调用的费用就会错算到 GLM 头上。
    # provider_kwargs 从全局路由表取，保证改配置就能换模型，不用改代码。
    provider_kwargs = node_llm_kwargs("write", api_key_override=state.get("api_key", ""))
    used_model = provider_kwargs["model"]

    # 【中文·创建大模型客户端】
    # ChatOpenAI 是 LangChain 的"万能客户端"，虽然名字叫 OpenAI，
    # 但通过 base_url 可以对接任何兼容 OpenAI 协议的 provider（GLM/Qwen）。
    llm = ChatOpenAI(  # type: ignore[call-arg]
        temperature=0.2,       # 低温度 = 更忠实原文、更少自由发挥
        max_tokens=16384,      # 单次回复最多 16K token（约 1 万字英文）
        request_timeout=300,   # 5 分钟超时（长章节生成可能很慢）
        max_retries=0,         # ⚠️ 禁用 SDK 内置重试！因为本项目有自己的
                               # 熔断器+空输出重试逻辑，SDK 重试会绕过它们，
                               # 导致在 provider 宕机时白白等很久。
        # 要求 AI 返回 JSON 格式（不是纯文本），方便程序化提取译文。
        model_kwargs={"response_format": {"type": "json_object"}},
        **provider_kwargs,     # 展开模型名/base_url/api_key/reasoning_effort
    )
    # Format the READ analysis as context
    # 【中文】把 READ 的分析结果（字典）格式化成一段可读文本，
    # 塞进提示词里让 WRITE 知道"这一章有哪些文化梗要处理"。
    read_analysis = state.get("read_analysis", {})
    analysis_text = _format_read_analysis(read_analysis)

    target_lang = state.get("target_lang", "en-US")

    # Build confirmed/rejected terms text
    # 【中文·构建人工审核过的术语列表】
    # 这个项目有个"编辑台"功能：人类编辑可以确认或否决某个术语的译法。
    # 比如确认"鬼节"必须译为 "Ghost Festival"，或否决把"师父"译成 "master"。
    # 这些人工决定会存进 job_store，每次写译文时都塞进提示词里，
    # 确保 AI 不会再犯已经被纠正过的错误。
    confirmed = job_store.get_confirmed_terms(target_lang, book_id=state.get("book_id", ""))
    confirmed_text = ""
    if confirmed:
        # 格式化成提示词里的条目，语气强硬（MUST / do NOT change），
        # 因为 AI 对温和措辞容易"打折扣"。
        confirmed_text = "\n".join(
            f"- '{cn}' MUST be '{en}'. Human-confirmed — do NOT change."
            for cn, en in confirmed.items()
        )
    else:
        confirmed_text = "(No human-confirmed terms yet.)"

    # 被否决的术语：告诉 AI "这个译法已经被人类否了，别再这么翻"
    rejected = job_store.get_rejected_terms(target_lang)
    rejected_text = ""
    if rejected:
        rejected_text = "\n".join(
            f"- DO NOT use '{r['rejected_en']}' for '{r['term_cn']}'. Human-rejected."
            for r in rejected
        )
    else:
        rejected_text = "(No human-rejected terms.)"

    # 【中文·加载语种相关的文体风格和忠实度规则】
    # regional_style：比如西班牙语用 tú 还是 usted、德语的正式程度等
    regional_style = LANGUAGE_STYLE_NOTES.get(target_lang, "")
    # fidelity_rules：文化忠实度规则，防止人名/称谓被简单音译成拼音
    # 比如"李逍遥"不能变成 "Li Xiaoyao"，要保留文化含义
    fidelity_all = load_fidelity_rules(target_lang)
    # 只取 character_names 和 terms_of_address 两类规则给 WRITE，
    # 其他类（如地名、功法名）由 READ 处理，WRITE 不需要知道。
    fidelity_brief = {
        k: v for k, v in fidelity_all.items()
        if k in ("character_names", "terms_of_address")
    }
    fidelity_text = format_fidelity_for_prompt(
        fidelity_brief,
    ) or "(No cultural fidelity rules for this language.)"
    # 【中文·格式化画面缺口】把 READ 找出的画面缺口列表转成文本指令，
    # 告诉 WRITE "这些地方要用感官细节重建画面"。
    image_gaps_text = _format_image_gaps(state.get("image_gaps", []))

    # 翻译经验本：前面所有章节积累的翻译经验和教训
    memo = state.get("style_memo", "(No style memo yet — this is the first chapter.)")
    # 按内容类型取提示词模板集（小说/诗歌/散文各有不同的模板）
    prompts = get_prompt_set(state.get("content_type", "novel"))
    genre = state.get("genre", "romance_ceo")
    # Per-genre / per-language parameters. The system prompt embeds JSON
    # examples with doubled braces, so .format() renders placeholders and
    # leaves the JSON literal. Rendered once here and reused by the empty-output
    # retry path below so both calls see identical instructions.
    #
    # 【中文·构建提示词】
    # ctx 包含角色设定和目标语种信息（如"你是写给美国读者看的类型小说作家"）。
    # 提示词模板里有 {genre}、{reader} 等占位符，.format(**ctx) 把它们填上。
    # ⚠️ 模板里的 JSON 示例用了双花括号 {{}}，这样 .format() 不会误解析它们。
    # 这里渲染一次，后面重试时复用同一份提示词，保证两次调用看到完全相同的指令。
    ctx = build_prompt_context(genre, target_lang)
    system_prompt = prompts.write_system.format(**ctx)
    # 【中文·填充用户提示词模板】把所有上下文信息塞进模板的占位符里。
    # 这些占位符对应模板文件里的 {style_memo}、{reader_analysis} 等。
    # 每个字段的含义：
    #   style_memo       翻译经验本（越翻越厚的"老手笔记"）
    #   reader_analysis  READ 的文化分析文本
    #   image_gaps       画面缺口感官重建指令
    #   chapter_number   当前章号
    #   chapter_title    中文章节标题
    #   genre            题材标签
    #   exact_matches    精确术语匹配结果（如"金丹 → Golden Core"）
    #   semantic_matches 语义术语匹配结果（意思相近的也算）
    #   previous_summary 上一章剧情摘要（让 WRITE 知道前情）
    #   confirmed_terms  人工确认的术语
    #   rejected_terms   人工否决的术语
    #   regional_style   目标语言的文体风格备注
    #   fidelity_rules   文化忠实度规则
    #   output_language  输出语言名称（如 "English"）
    #   reader/readers   目标读者描述（如 "an American reader"）
    #   chapter_content  中文原文全文
    user_prompt = prompts.write_user.format(
        style_memo=memo,
        reader_analysis=analysis_text,
        image_gaps=image_gaps_text,
        chapter_number=chapter_number,
        chapter_title=state["chapter_title"],
        genre=genre,
        exact_matches=state.get("exact_matches_text", "(No glossary terms yet.)"),
        semantic_matches=state.get("semantic_matches_text", "(No semantic matches.)"),
        previous_summary=state.get("previous_chapter_summary", "(This is the first chapter.)"),
        confirmed_terms=confirmed_text,
        rejected_terms=rejected_text,
        regional_style=regional_style if regional_style else "(Standard American English.)",
        fidelity_rules=fidelity_text,
        output_language=ctx["output_language"],
        reader=ctx["reader"],
        readers=ctx["readers"],
        chapter_content=state["chapter_content"],
    )

    # 【中文·组装消息列表】大模型的对话格式：一条系统消息 + 一条用户消息。
    # SystemMessage 定义角色身份和行为准则，HumanMessage 给出具体任务。
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    target_lang = state.get("target_lang", "en-US")
    # 【中文·获取熔断器】熔断器（circuit breaker）像电路里的保险丝：
    # 当某个 provider 连续失败太多次，它就"跳闸"拒绝后续调用，
    # 防止在 provider 宕机时反复烧钱。按目标语言分别计数。
    breaker = get_breaker(target_lang)

    try:
        # 记录一次 API 调用尝试（用于统计成功率）
        TranslationStats.record_api_call(target_lang)
        # 通过熔断器调用大模型。breaker.call() 会在熔断器打开时直接抛异常，
        # 不真正发请求。
        response, elapsed = token_ledger.timed_call(breaker.call, llm.invoke, messages)
        TranslationStats.record_api_success(target_lang)
    except CircuitBreakerOpenError:
        # 熔断器已跳闸，provider 处于冷却期，直接向上抛让流水线处理
        TranslationStats.record_api_failure(target_lang)
        raise
    except Exception as exc:
        TranslationStats.record_api_failure(target_lang)
        # 【中文·判断是否是内容过滤错误】
        # 如果不是 1301 内容过滤，就是普通网络/服务端错误，直接向上抛。
        if not _is_content_filter_error(exc):
            raise
        # The safety layer rejected this chapter (Zhipu code 1301). Same-provider
        # retry cannot help, and editing the source to slip past the filter would
        # mean shipping a censored translation — the one thing this pipeline must
        # not do. Fall back to the other configured provider for this chapter
        # only, and record it so a corpus-level shift in filter behaviour is
        # visible rather than silent.
        #
        # 【中文·GLM 1301 内容过滤兜底】
        # GLM（智谱）的安全层有时会误拦真实网文（错误码 1301），
        # 比如《间客》这种已出版的正规小说也会被拦。
        # ⚠️ 同一个 provider 重试没用——过滤器对同一段文字是确定性的。
        # ⚠️ 更不能改原文去绕过过滤——那等于交付被审查删减的译文，
        #    这是本项目绝对不能做的事。
        # 正确做法：切换到另一个 provider（Qwen）重写本章。
        # 只影响这一章，不影响后续章节的路由。
        fallback_kwargs = node_llm_kwargs(
            "write_fallback", api_key_override=state.get("api_key", "")
        )
        fallback_model = fallback_kwargs["model"]
        logger.warning(
            "WRITE ch%d: content filter rejected the chapter (1301) — retrying "
            "once on %s", chapter_number, fallback_model,
        )
        # 把这次兜底事件记入错误追踪器，方便事后统计哪些章节触发了过滤
        try:
            from ...error_tracker import record_event
            record_event(None, chapter_number, "content_filter_fallback",
                         f"GLM 1301 on ch{chapter_number}; retried on {fallback_model}",
                         target_lang)
        except Exception:
            pass
        # 如果兜底 provider 也没有配 API key，那就真的没办法了，向上抛
        fallback_key = fallback_kwargs["api_key"]
        if not fallback_key:
            raise
        # 用兜底 provider 重新调用，同样的提示词、同样的消息
        response, elapsed = token_ledger.timed_call(
            breaker.call, _build_fallback_writer(**fallback_kwargs).invoke, messages
        )
        # 更新实际使用的模型名，确保 token 费用记到正确的 provider 头上
        used_model = fallback_model
        TranslationStats.record_api_success(target_lang)

    # 【中文·记录 token 用量】把这次调用的输入/输出 token 数记入账本，
    # 用于算钱和监控成本趋势。
    _capture_response_tokens(
        response,
        model=used_model,
        book_id=state.get("book_id", ""),
        chapter=chapter_number,
        elapsed_s=elapsed,
    )

    # 【中文·解析 AI 返回的 JSON】AI 被要求返回 JSON 格式，但经常不完美，
    # 所以 _parse_write_response 用了 5 层递进式解析来抢救。
    result = _parse_write_response(
        response.content,
        chapter_number=chapter_number,
        target_lang=state.get("target_lang", "en-US"),
    )

    translated_text = result.get("translated_text", "")

    # A response cut off at the token ceiling is not a translation. Blank it so
    # the empty-output retry below fires, instead of shipping the partial text.
    #
    # 【中文·截断检测】如果 AI 回复被 max_tokens 上限截断了，
    # 拿到的就是半截译文，不能交付。这里把译文清空，
    # 让下面的"空输出重试"逻辑自动触发重新生成。
    # 为什么不直接报错：重试可能成功（比如第二次生成了更紧凑的版本），
    # 比直接跳过这一章好。
    truncated = _was_truncated(response)
    if truncated:
        logger.warning(
            "WRITE ch%d: TRUNCATED response (finish_reason=%s, %d chars of "
            "partial output discarded) — treating as failed output",
            chapter_number, truncated, len(translated_text),
        )
        try:
            from ...error_tracker import record_event
            record_event(None, chapter_number, "output_truncated",
                         f"WRITE finish_reason={truncated}", target_lang)
        except Exception:
            pass
        translated_text = ""

    # Output quality guard: check for obvious garbage
    # 【中文·输出质量守卫】AI 写的译文可能有各种"脏东西"混进去，
    # 这里依次检查并清理。这些守卫函数来自 output_guard 模块，
    # 和交付扫描器 scan_deliverable.py 共用同一套判断逻辑，
    # 保证生产时检查和事后扫描不会"各说各话"。
    from ...output_guard import (
        check_and_record,  # 综合检查并记录警告
        has_json_envelope_residue,  # 检测 JSON 外壳残留
        has_meta_text_leak,  # 检测元文本泄漏（如脚手架文字）
        has_untranslated_chinese,  # 检测未翻译的中文残留
        sanitize_translation,  # 清理常见格式问题
        strip_chinese_residue,  # 剥离零散中文字符
    )

    # 【中文·检查 1：中文残留】目标语言不是中文时，译文里不该有中文。
    # AI 有时会漏翻某些片段，这里尝试剥离残留字符。
    if has_untranslated_chinese(translated_text) and target_lang != "zh-CN":
        cleaned, removed = strip_chinese_residue(translated_text)
        logger.warning(
            "WRITE ch%d: %d Chinese chars stripped (%d chars before → %d after)",
            chapter_number, removed, len(translated_text), len(cleaned),
        )
        translated_text = cleaned

    # 【中文·检查 2：综合质量检查】跑一组规则检查，返回警告列表。
    # 比如检测到重复段落、异常短的段落等。只记日志，不阻断流程。
    warnings = check_and_record(
        translated_text,
        chapter_num=chapter_number,
        target_lang=target_lang,
    )
    if warnings:
        for w in warnings:
            logger.warning("Output guard ch%d: %s", chapter_number, w)

    # 【中文·检查 3：通用清理】去除多余空白、修正格式等
    translated_text = sanitize_translation(translated_text)

    # JSON-envelope residue / scaffolding leak guard. Same recovery contract as
    # the truncation guard above: discard the bad body so the empty-output retry
    # below fires, rather than shipping it. These two defect classes reached a
    # delivered file (Ch366 ```json fence, Ch1472 unpacked envelope, Ch1077 /
    # Ch1286 "End of Chapter." scaffolding) because nothing checked for them at
    # production time — scan_deliverable.py only found them after the fact. The
    # judgements are shared with that scanner (output_guard is the single
    # source), so the two can never drift.
    #
    # 【中文·检查 4：JSON 外壳残留 / 元文本泄漏】
    # ⚠️ 真实踩过的坑：第 366 章译文末尾多了 ```json 围栏；
    # 第 1472 章整个 JSON 外壳没被剥掉；第 1077/1286 章混入了脚手架文字。
    # 处理方式：清空译文 → 触发下面的空输出重试，不带着脏数据交付。
    residue = (
        "json_envelope" if has_json_envelope_residue(translated_text)
        else "meta_text" if has_meta_text_leak(translated_text)
        else ""
    )
    if residue:
        logger.warning(
            "WRITE ch%d: %s residue in output (%d chars discarded) — treating as "
            "failed output", chapter_number, residue, len(translated_text),
        )
        try:
            from ...error_tracker import record_event
            record_event(None, chapter_number, f"{residue}_residue",
                         f"WRITE ch{chapter_number}: {residue} in finished body",
                         target_lang)
        except Exception:
            pass
        translated_text = ""

    # Auto-retry if output is empty (up to 2 additional attempts)
    # 【中文·空输出自动重试】如果译文为空或太短（< 10 字符），最多重试 2 次。
    # 为什么 AI 会返回空输出：有时模型抽风、有时提示词太长被截断、
    # 有时安全过滤静默吞掉了回复。重试通常能解决偶发问题。
    # 为什么上限是 2 次：超过 2 次还失败说明问题不在随机性，
    # 继续重试只会烧钱。
    if not translated_text or len(translated_text.strip()) < RETRY_THRESHOLD:
        for retry_num in range(1, 3):  # Retry 1, Retry 2
            logger.warning(
                "WRITE ch%d: empty/short output (%d chars). Retry %d/2.",
                chapter_number, len(translated_text) if translated_text else 0, retry_num,
            )
            try:
                # 【中文·构建重试消息】在原始用户提示词后面追加一段紧急催促，
                # 告诉 AI "你上次返回了空内容，这次必须给出完整译文"。
                # 系统消息不变，保持角色设定一致。
                retry_messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt
                                  + "\n\nCRITICAL: Your previous response was empty. "
                                  "Output the complete translated chapter as JSON NOW. "
                                  "The translated_text field MUST contain the full chapter."),
                ]
                TranslationStats.record_api_call(target_lang)
                retry_response, retry_elapsed = token_ledger.timed_call(
                    breaker.call, llm.invoke, retry_messages
                )
                TranslationStats.record_api_success(target_lang)
                _capture_response_tokens(
                    retry_response,
                    # The retry reuses the *primary* client, so it is booked
                    # against the primary tier even when an earlier 1301 sent
                    # this chapter's first attempt to the fallback provider.
                    #
                    # 【中文】重试用的是主客户端（不是兜底客户端），
                    # 所以 token 费用记到主模型头上，即使第一次尝试走了兜底。
                    model=provider_kwargs["model"],
                    book_id=state.get("book_id", ""),
                    chapter=chapter_number,
                    elapsed_s=retry_elapsed,
                )
                # 用同样的 5 层解析器处理重试结果
                retry_result = _parse_write_response(
                    retry_response.content, chapter_number, target_lang
                )
                retry_text = retry_result.get("translated_text", "")
                # 重试也可能被截断，同样要检查
                retry_truncated = _was_truncated(retry_response)
                if retry_truncated:
                    logger.warning(
                        "WRITE ch%d retry%d: TRUNCATED again (finish_reason=%s) "
                        "— discarding partial output", chapter_number,
                        retry_num, retry_truncated,
                    )
                    retry_text = ""
                # 重试结果够长才算有效
                if retry_text and len(retry_text.strip()) >= RETRY_THRESHOLD:
                    # 重试也要过中文残留检查
                    if has_untranslated_chinese(retry_text) and target_lang != "zh-CN":
                        retry_text, removed = strip_chinese_residue(retry_text)
                        logger.warning(
                            "WRITE ch%d retry%d: %d Chinese chars stripped",
                            chapter_number, retry_num, removed,
                        )
                    translated_text = sanitize_translation(retry_text)
                    # Symmetric with the first-attempt guard: a retry that
                    # re-emits the envelope or scaffolding is not a success.
                    # `continue` falls through to the next attempt; if all
                    # retries are exhausted the for-else below logs the skip.
                    #
                    # 【中文】重试也要过 JSON 外壳/元文本泄漏检查。
                    # 如果重试还是有脏数据，continue 跳到下一次重试；
                    # 如果所有重试都失败了，for-else 会记录日志。
                    retry_residue = (
                        "json_envelope" if has_json_envelope_residue(translated_text)
                        else "meta_text" if has_meta_text_leak(translated_text)
                        else ""
                    )
                    if retry_residue:
                        logger.warning(
                            "WRITE ch%d retry%d: %s residue again — discarding",
                            chapter_number, retry_num, retry_residue,
                        )
                        translated_text = ""
                        continue
                    # 重试成功！更新结果并跳出循环
                    result = retry_result
                    logger.info("WRITE ch%d: retry %d successful", chapter_number, retry_num)
                    break  # Success — exit retry loop
            except Exception as retry_exc:
                logger.error("WRITE ch%d retry %d failed: %s", chapter_number, retry_num, retry_exc)
        else:
            # All retries exhausted — log and continue (chapter will be empty/short)
            # 【中文·for-else 语法】Python 的 for-else：循环正常结束（没 break）
            # 才执行 else 块。也就是说 2 次重试都失败了才会走到这里。
            # 处理方式：记一条错误日志，然后继续翻下一章。
            # ⚠️ 这一章会是空的或很短，但不会卡住整本书。
            logger.error(
                "WRITE ch%d: ALL RETRIES EXHAUSTED — chapter skipped (0 chars). "
                "Next chapter will continue normally.",
                chapter_number,
            )

    # Scene-level completeness. WRITE may compress and cut, but the prompt
    # requires every source scene to keep an equivalent, and until now nothing
    # checked that: READBACK is blind to the source by design, and the word
    # guard only counted output. Report-only for now — the thresholds were
    # calibrated on 《地府》 and want a wider corpus before they gate a delivery.
    #
    # 【中文·场景级完整性检查】WRITE 允许压缩和删减，但提示词要求
    # 原文每个场景都要有对应的英文段落。之前没人检查这一点：
    # READBACK 看不到原文（设计上就是盲的），字数守卫只数输出长度。
    # 目前只是记录警告，不阻断交付——阈值是在《地府叫我小先生》上校准的，
    # 需要在更多书上验证后才能作为硬性关卡。
    try:
        completeness = check_completeness(
            state.get("chapter_content", ""),  # 中文原文
            translated_text,                    # 英文译文
            state.get("exact_glossary") or {},  # 术语表（用于排除术语差异）
        )
        # 如果有完整性标记（如漏了对话、长度比异常低），记入错误追踪器
        if completeness["flags"]:
            try:
                from ...error_tracker import record_event
                record_event(
                    None, chapter_number, "completeness",
                    f"WRITE completeness flags: {','.join(completeness['flags'])} "
                    f"(ratio={completeness['length_ratio']}, "
                    f"dialogue {completeness['dialogue_src']}->"
                    f"{completeness['dialogue_out']})",
                    target_lang,
                )
            except Exception:
                pass
    except Exception as exc:  # never let an audit break the pipeline
        # 【中文】完整性检查是"锦上添花"，绝不能因为它的 bug 阻断流水线。
        # 所以用 debug 级别日志，连 warning 都不算。
        logger.debug("WRITE ch%d: completeness check failed: %s", chapter_number, exc)

    # 【中文·返回结果】把译文和相关元数据写回共享状态，供下游节点使用。
    # · translated_text   → readback_node 拿去冷读
    # · chapter_title_en  → 最终交付文件的章节标题
    # · new_terms_found   → 存入术语库，后续章节可用
    # · adaptation_notes  → 记录重大改编决策，供人类编辑审阅
    # · chapter_summary   → 喂给下一章的 WRITE 做前情提要
    return {
        "translated_text": translated_text,
        "chapter_title_en": result.get("chapter_title_en", ""),
        "new_terms_found": result.get("new_terms_found", []),
        "adaptation_notes": result.get("adaptation_notes", []),
        "chapter_summary": result.get("chapter_summary", ""),
    }


def _format_read_analysis(analysis: dict) -> str:
    """Format the READ agent's analysis dict as readable text for the WRITE prompt.

    【中文·把 READ 的分析字典格式化成提示词文本】

    作用：READ 返回的是一个结构化字典，但提示词需要纯文本。
    这个函数把字典里的各个字段拼成 markdown 格式的段落，
    让 WRITE 能读懂"哪些文化梗要处理、哪些术语怎么译"。

    参数：
      analysis —— READ 节点输出的分析字典，可能包含以下键：
        · emotional_arc         情感弧线（本章情绪走向）
        · crafted_moments       精心打造的时刻（WRITE 必须保留的亮点）
        · cultural_gaps         文化缺口列表（每个含元素名、中外读者差异、桥接策略）
        · terminology_decisions 术语翻译建议（中文原词 → 英文建议 + 理由）
        · pacing_notes          节奏备注（哪里该快、哪里该慢）

    返回：一段 markdown 格式的文本，直接塞进 WRITE 的用户提示词里。
    如果某个字段不存在就跳过，不会报错。
    """
    parts = []

    # 情感弧线：告诉 WRITE 这一章的情绪走向（如"从紧张到释然"）
    if analysis.get("emotional_arc"):
        parts.append(f"## EMOTIONAL ARC\n{analysis['emotional_arc']}")

    # 精心打造的时刻：原文中写得特别好的片段，WRITE 要保留其效果
    if analysis.get("crafted_moments"):
        parts.append("## CRAFTED MOMENTS (preserve these)")
        for m in analysis["crafted_moments"]:
            parts.append(f"- {m}")

    # 文化缺口：这是最核心的部分——告诉 WRITE 哪些地方外国读者看不懂
    if analysis.get("cultural_gaps"):
        parts.append("## CULTURAL GAPS (bridge these)")
        for g in analysis["cultural_gaps"]:
            # 每个缺口包含：文化元素名、中国读者看到什么、外国读者缺什么、
            # 桥接策略（如"加上下文""用类比"）、具体指导
            parts.append(
                f"\n### {g.get('element', 'Unknown')}\n"
                f"- CN reader gets: {g.get('cn_reader_gets', '?')}\n"
                f"- EN reader misses: {g.get('en_reader_misses', '?')}\n"
                f"- Strategy: {g.get('bridge_strategy', 'context')}\n"
                f"- Guidance: {g.get('bridge_guidance', 'Use your judgment.')}"
            )

    # 术语翻译建议：READ 认为某个词应该怎么译，附带理由
    if analysis.get("terminology_decisions"):
        parts.append("## TERMINOLOGY RECOMMENDATIONS")
        for t in analysis["terminology_decisions"]:
            parts.append(
                f"- **{t.get('term_cn', '?')}** → {t.get('proposed_en', '?')}"
                f"{' — ' + t['reasoning'] if t.get('reasoning') else ''}"
            )

    # 节奏备注：比如"这段打斗要快节奏短句""这段回忆要慢下来"
    if analysis.get("pacing_notes"):
        parts.append(f"## PACING NOTES\n{analysis['pacing_notes']}")

    return "\n\n".join(parts)


def _format_image_gaps(gaps: list[dict]) -> str:
    """Format image gaps as sensory rebuilding instructions for the WRITER.

    【中文·把画面缺口列表格式化成感官重建指令】

    作用：READ 找出的"画面缺口"是一个字典列表，每个描述了一个
    "中国读者脑补得出但外国读者看不到"的场景。这个函数把它们
    按优先级分三档（critical/high/medium），格式化成 WRITE 能读懂的指令。

    参数：
      gaps —— 画面缺口列表，每个字典包含：
        · priority        优先级（critical/high/medium）
        · passage         原文段落
        · cn_reader_sees  中国读者看到的画面
        · en_reader_gets  外国读者只看到什么（通常是一个干巴巴的标签）
        · sensory_anchors 感官锚点（用什么细节重建画面，如"潮湿的泥土味""远处的狗吠"）

    返回：分优先级的 markdown 文本。如果没有缺口就返回一句"自由发挥"。

    为什么分三档：WRITE 的注意力有限，critical 必须全力重建，
    medium 只需加一个感官细节，避免提示词太长分散重点。
    """
    if not gaps:
        return "(No image gaps detected — write freely.)"

    # 按优先级分组，从高到低排列
    critical = [g for g in gaps if g.get("priority") == "critical"]
    high = [g for g in gaps if g.get("priority") == "high"]
    medium = [g for g in gaps if g.get("priority") == "medium"]

    parts = []

    # critical 级：不重建画面整个场景就废了
    if critical:
        parts.append("### CRITICAL — These scenes FAIL without sensory rebuilding:")
        for g in critical:
            parts.append(
                f"\n**PASSAGE:** {g.get('passage', '?')}\n"
                f"**CN READER SEES:** {g.get('cn_reader_sees', '?')}\n"
                f"**EN READER GETS:** {g.get('en_reader_gets', '?')}\n"
                f"**BUILD WITH:** "
                f"{g.get('sensory_anchors', 'use universal textures, sounds, colors')}"
            )

    # high 级：重要的感官时刻，需要重建
    if high:
        parts.append("\n### HIGH — Important sensory moments to rebuild:")
        for g in high:
            parts.append(
                f"\n**PASSAGE:** {g.get('passage', '?')}\n"
                f"**CN READER SEES:** {g.get('cn_reader_sees', '?')}\n"
                f"**BUILD WITH:** {g.get('sensory_anchors', 'add one vivid sensory detail')}"
            )

    # medium 级：加一个感官细节即可
    if medium:
        parts.append("\n### MEDIUM — Add one sensory detail each:")
        for g in medium:
            parts.append(
                f"- {g.get('passage', '?')} — anchor: "
                f"{g.get('sensory_anchors', 'one sensory detail')}"
            )

    return "\n".join(parts)


def _capture_response_tokens(response, model: str,
                             *, book_id: str = "", chapter: int = 0,
                             elapsed_s: float | None = None) -> None:
    """Record WRITE token usage against the model that actually served it.

    The ledger always gets a row, even when the provider sent no usage payload
    (recorded as unmetered, so the gap is countable). The legacy ``TranslationStats``
    counter can only hold numbers, so it is updated only when the payload was
    complete — half a usage payload is not a figure worth accumulating.

    【中文·记录 token 用量】

    作用：把这次 AI 调用的输入/输出 token 数记入两套账本。
    为什么要两套：token_ledger 是精确账本（每行一条记录，能算钱），
    TranslationStats 是汇总计数器（只看总数，给 dashboard 用）。

    参数：
      response  —— AI 的回复对象（里面藏着 usage 字段）
      model     —— 实际使用的模型名（可能是兜底模型）
      book_id   —— 书籍 ID（可选）
      chapter   —— 章号（可选）

    为什么即使没有 usage 也要记账：provider 有时不返回用量数据，
    记一条"未计量"的记录，事后才能发现"有多少次没拿到用量"。
    """
    # 从回复对象里提取输入/输出 token 数（可能为 None）
    usage_in, usage_out = token_ledger.extract_usage(response)
    # 精确账本：无论有没有用量数据都记一行
    token_ledger.record("write", model, usage_in, usage_out,
                        book_id=book_id, chapter=chapter, elapsed_s=elapsed_s)
    # 没有用量数据就不更新汇总计数器（半条数据没法累加）
    if usage_in is None or usage_out is None:
        return
    try:
        TranslationStats.record_tokens(
            input_tokens=usage_in,
            output_tokens=usage_out,
        )
    except Exception:
        pass


# Providers report "I stopped because I hit the token ceiling" here.
# 【中文】这些是各大 provider 表示"回复被 token 上限截断了"的标志值。
# 不同 provider 用不同的字符串，这里统一收集成一个集合方便判断。
# content_filter 也算截断：安全过滤中途打断回复，效果和 token 上限一样。
_TRUNCATED_FINISH_REASONS = {"length", "max_tokens", "max_output_tokens",
                             "content_filter"}


def _was_truncated(response) -> str:
    """Return the truncating finish_reason, or "" if the response completed.

    Nothing in this pipeline used to look at ``finish_reason``, so a response
    cut off at ``max_tokens`` was indistinguishable from a complete one: the
    multi-layer parser below would happily hand back the partial text (Layer 5
    returns the raw content), and the chapter shipped short.

    That is the failure the DeepSeek-era adversarial test recorded as "empty
    translations with Chinese character residue" — see src/chapter_slicer.py.
    Slicing reduced the odds but was never a detector, and its budget model was
    calibrated for DeepSeek's context window, not for GLM/Qwen plus the
    reasoning tokens the new stack spends.

    【中文·检测回复是否被截断】

    作用：检查 AI 回复的 finish_reason 字段，判断回复是不是因为
    token 上限被截断了。如果是，返回截断原因字符串；否则返回空串。

    为什么需要这个检查：以前流水线不看 finish_reason，半截回复和完整回复
    长得一样，多层解析器会把半截文本当正常译文交付出去。
    DeepSeek 时代的对抗测试就发现了"空译文+中文残留"的问题。

    参数：
      response —— AI 回复对象

    返回：截断原因字符串（如 "length"、"max_tokens"），正常完成则返回 ""。
    """
    try:
        # finish_reason 在不同 provider 的回复对象里位置不同，
        # 先查 response_metadata，再查 additional_kwargs
        meta = getattr(response, "response_metadata", None) or {}
        reason = meta.get("finish_reason")
        if reason is None:
            reason = (getattr(response, "additional_kwargs", None) or {}).get(
                "finish_reason"
            )
        reason = str(reason or "").lower()
        # 只在已知的截断标志集合里才算截断，其他值（如 "stop"）表示正常完成
        return reason if reason in _TRUNCATED_FINISH_REASONS else ""
    except Exception:
        return ""


def _build_fallback_writer(**provider_kwargs) -> ChatOpenAI:
    """A WRITE client on the routed fallback provider, for filter fallback only.

    Same generation parameters as the primary writer so a fallback chapter is not
    silently written in a different register. Model / base_url / api_key and the
    provider's reasoning control all arrive pre-resolved from
    ``node_llm_kwargs("write_fallback")`` — this function must not pick a
    provider itself, or re-tiering the fallback would not take effect.

    【中文·构建兜底写作客户端】

    作用：当 GLM 触发 1301 内容过滤时，用这个函数创建一个 Qwen 客户端来重写。
    参数和主客户端完全一致（temperature/max_tokens/timeout 等），
    确保兜底写出的译文风格和主客户端一样，不会因为换了模型就变调。

    参数：
      **provider_kwargs —— 从 node_llm_kwargs("write_fallback") 预解析好的
        模型名、base_url、api_key、reasoning_effort/thinking_budget。
        这个函数**不能自己选 provider**，否则改配置换兜底模型就不生效了。

    返回：一个配好参数的 ChatOpenAI 客户端，可以直接 .invoke(messages)。
    """
    return ChatOpenAI(  # type: ignore[call-arg]
        temperature=0.2,       # 和主客户端保持一致
        max_tokens=16384,      # 同上
        request_timeout=300,   # 同上
        max_retries=0,         # 同上：禁用 SDK 内置重试
        model_kwargs={"response_format": {"type": "json_object"}},  # 同上：要求 JSON
        **provider_kwargs,     # 展开兜底模型的连接参数
    )


def _parse_write_response(
    content: str,
    chapter_number: int = 0,
    target_lang: str = "en-US",
) -> dict:
    """Parse the WRITE agent's JSON output with multi-layer fallback.

    【中文·5 层递进式 JSON 解析器】

    作用：AI 被要求返回 JSON，但经常不完美。这个函数用 5 层策略依次尝试，
    尽可能抢救出有效译文，实在不行就返回空译文触发重试。

    参数：
      content        —— AI 回复的原始文本
      chapter_number —— 章号（用于记录事件）
      target_lang    —— 目标语言代码

    返回：一个字典，至少包含 translated_text 字段。
    其他字段（new_terms_found / adaptation_notes / chapter_summary）
    在低层解析时可能丢失，会返回默认空值。

    5 层策略从严格到宽松：
      Layer 1  标准 JSON 解析（最理想的情况）
      Layer 2  正则提取 JSON 对象（AI 在 JSON 前后加了废话）
      Layer 3  正则提取 translated_text 字段值（JSON 结构损坏但字段还在）
      ── 外壳残留守卫 ──（防止把畸形 JSON 外壳当译文）
      Layer 4  当 markdown/散文处理（AI 完全忽略了 JSON 格式要求）
      Layer 5  散文抢救（最后手段，需要满足最低字数门槛）
    """
    from ...error_tracker import record_event

    text = content.strip()

    # 【中文·预处理：剥离 markdown 代码围栏】
    # AI 有时会把 JSON 包在 ```json ... ``` 里，先剥掉这层壳。
    if text.startswith("```"):
        lines = text.split("\n")
        # 如果最后一行是闭合的 ```，去掉首尾两行；否则只去掉第一行
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        text = text.strip()

    # Layer 1: Strict JSON
    # 【中文·第 1 层：标准 JSON 解析】直接用 json.loads 解析。
    # 如果 AI 乖乖返回了纯 JSON，这一步就成功了。
    try:
        return json.loads(text)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, ValueError):
        pass

    # Layer 2: Regex-extracted JSON object
    # 【中文·第 2 层：正则提取 JSON 对象】AI 可能在 JSON 前后加了
    # "Here is the translation:" 之类的废话，导致 json.loads 失败。
    # 用正则找到包含 "translated_text" 的最外层 {...}，再尝试解析。
    m = re.search(r'\{[\s\S]*"translated_text"[\s\S]*\}', text)
    if m:
        try:
            result = json.loads(m.group())
            record_event(None, chapter_number, "parse_fallback",
                         "Layer 2: regex-extracted JSON object", target_lang)
            return result  # type: ignore[no-any-return]
        except (json.JSONDecodeError, ValueError):
            pass

    # Layer 3: Regex field extraction for translated_text
    # 【中文·第 3 层：正则提取单个字段】JSON 整体结构已损坏，
    # 但 "translated_text": "..." 这个键值对还在。
    # 用正则直接提取引号内的内容，手动还原转义字符。
    # ⚠️ 这一层只能拿到译文，其他字段（术语、摘要等）全部丢失。
    m = re.search(r'"translated_text"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        # 还原 JSON 字符串里的转义：\" → " 和 \n → 换行符
        tt = m.group(1).replace('\\"', '"').replace('\\n', '\n')
        record_event(None, chapter_number, "parse_fallback",
                     "Layer 3: regex field extraction", target_lang)
        return {
            "translated_text": tt,
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "",
        }

    # ── Envelope-residue guard (before the prose/markdown rescue layers) ──
    # Reaching here means Layer 1-3 all failed: the reply is NOT a parseable
    # envelope. A *well-formed* envelope would have returned at Layer 1 (strict
    # JSON), so any envelope key still sitting in the text now belongs to a
    # malformed or truncated one — exactly the case Layer 4 ("markdown-as-
    # translation") and Layer 5 ("prose rescue") used to swallow whole:
    #   • Ch366  — prose ending in a ```json fence + bare `{`, rescued by Layer 4
    #     because the body started with a capital letter.
    #   • Ch1472 — a compact, truncated `{"translated_text": "..."}` rescued by
    #     Layer 5, whose own log line claimed "no JSON envelope".
    # Both shipped the envelope into the deliverable. Discard instead so the
    # empty-output retry in write_node fires. The judgement is shared with the
    # production guard and scan_deliverable (output_guard is the single source).
    #
    # 【中文·外壳残留守卫（在散文抢救之前）】
    # 走到这里说明 Layer 1~3 都失败了，回复不是合法的 JSON 外壳。
    # 但如果文本里还残留着 JSON 外壳的痕迹（如 {"translated_text":），
    # 说明这是一个畸形或截断的外壳——以前 Layer 4/5 会把它当散文救回来，
    # 结果把 JSON 语法混进了交付文件（第 366 章和第 1472 章的真实事故）。
    # 现在改为：检测到残留就返回空译文，让 write_node 的重试逻辑接管。
    from ...output_guard import json_residue_on_line
    for line in text.split("\n"):
        if json_residue_on_line(line):
            record_event(None, chapter_number, "parse_fallback",
                         "envelope residue in malformed reply — discarded, "
                         "not rescued as prose", target_lang)
            return {
                "translated_text": "",
                "new_terms_found": [],
                "adaptation_notes": [],
                "chapter_summary": "",
            }

    # Layer 4: Markdown-as-translation
    # 【中文·第 4 层：当 markdown/散文处理】AI 完全忽略了 JSON 格式要求，
    # 直接写了 markdown 格式的译文。判断依据：文本以 # 标题、> 引用、
    # ** 加粗、或大写字母开头的单词起始。
    # ⚠️ 这一层假设整个文本就是译文，没有术语/摘要等元数据。
    if re.match(r'^(#+\s|>|\*\*|[A-Z][a-z])', text):
        record_event(None, chapter_number, "parse_fallback",
                     "Layer 4: markdown-as-translation", target_lang)
        return {
            "translated_text": text,
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "",
        }

    # Layer 5: the model ignored the JSON envelope and just wrote prose
    # ("I'd be happy to translate this chapter for you! ... Su Nian woke up...").
    # That is worth rescuing — but only when it actually looks like prose.
    #
    # The old behaviour stamped ANY unparseable response with
    # translated_text = raw content, so a truncated or malformed reply became a
    # "translation" that nothing downstream could tell from a real one. The
    # token-ceiling case is now caught earlier by _was_truncated(); this guard
    # covers the remaining ways a response can be junk (symbol soup, a stub,
    # a non-language payload).
    #
    # 【中文·第 5 层：散文抢救（最后手段）】
    # AI 有时会忽略 JSON 格式，直接写一段散文（甚至带一句开场白
    # "I'd be happy to translate this chapter for you!"）。
    # 这种情况值得抢救——但前提是它真的像散文，而不是乱码或截断碎片。
    # 判断标准：至少 40 个拉丁字母 + 至少 10 个单词。
    # 以前的旧逻辑会把任何无法解析的内容都当译文，导致垃圾数据流入交付。
    body = content.lstrip("```json").lstrip("```").strip()
    latin_letters = len(re.findall(r"[A-Za-z]", body))
    word_count = len(body.split())
    # 40 个字母 ≈ 几个英文句子；10 个词 ≈ 不是碎片。
    # 这两个阈值是在实际生产数据上校准的。
    if latin_letters >= 40 and word_count >= 10:
        record_event(None, chapter_number, "parse_fallback",
                     "Layer 5: prose rescue (no JSON envelope)", target_lang)
        return {
            "translated_text": body,
            "new_terms_found": [],
            "adaptation_notes": [],
            "chapter_summary": "",
        }

    # 【中文·所有层都失败了】返回空译文，让 write_node 触发重试。
    # 记录字母数和词数，方便事后分析"什么样的回复彻底没救了"。
    record_event(None, chapter_number, "parse_fallback",
                 f"Layer 5: unparseable response discarded "
                 f"({latin_letters} letters, {word_count} words)",
                 target_lang)
    return {
        "translated_text": "",
        "new_terms_found": [],
        "adaptation_notes": [],
        "chapter_summary": "",
    }
