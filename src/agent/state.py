"""LangGraph state definition for the translation agent.

【中文·这个文件是干什么的】
定义整条翻译流水线的"共享白板"（TranslatorState）。
你可以把它想象成编辑部办公室里的一块大白板：
READ、WRITE、READBACK、FIX 四个角色不直接对话，
而是把自己的分析结果、修改意见、质量评分**写到白板上**，
下一个角色从白板上读取前人留下的信息再继续工作。

【中文·它在整个流程里的位置】
被 src/agent/graph.py 在 build_graph() 里传给 StateGraph(TranslatorState)，
告诉流程图引擎"这条流水线上的所有节点共用这份数据结构"。
也被 nodes/ 下的每个角色文件 import，用来读写状态字段。
本文件只定义结构，不包含任何业务逻辑。

【中文·核心概念：TypedDict + Annotated】
  · TypedDict —— Python 的"带名字的字典类型"。和普通 dict 的区别是：
    它规定了"这块白板上有哪些格子、每个格子放什么类型的东西"，
    IDE 能据此提示你字段名、检查你写的类型对不对。
    运行时它仍然是普通字典，不会做强制校验。
  · Annotated[list, operator.add] —— LangGraph 的特殊标记。
    意思是"这个字段不是覆盖写入，而是追加合并"。
    比如 new_terms_found 标记了 operator.add，那么 FIX 第一轮找到 3 个术语、
    第二轮又找到 2 个，最终状态里就是 5 个（而不是被第二轮的 2 个覆盖掉）。
    没有这个标记的字段（如 translated_text），后一次写入会直接替换前一次。

【中文·字段分组说明】
下面的字段按用途分成六组，每组用注释标题隔开：
  Input           → 外部喂进来的原始数据（章节内容、语言、类型等）
  Glossary        → 术语查询结果（精确匹配 + 语义匹配）
  Translation     → 翻译产出物（英文稿、新术语、修补经验、摘要）
  Context         → 跨章连续性（上一章摘要 → 注入下一章）
  Quality         → 质量评估（冷读者打分、问题列表、重修计数）
  v0.15 outputs   → 新版流水线特有的中间产物（READ 分析、画面缺口等）
"""

import operator
from typing import Annotated, TypedDict


class TranslatorState(TypedDict):
    """Shared state schema for all nodes in the translation graph.

    【中文·一句话作用】
    定义流水线上所有角色共用的"白板"有哪些格子、每个格子放什么。

    【中文·为什么用 TypedDict 而不是 dataclass / Pydantic】
    LangGraph 的 StateGraph 要求状态是一个字典（dict），
    因为引擎内部要对字段做合并、序列化、存档等操作，
    字典是最灵活的载体。TypedDict 在保留字典灵活性的同时，
    给开发者提供了类型提示——两全其美。

    【中文·典型用法（graph.py 里就是这么用的）】
      builder = StateGraph(TranslatorState)  # 告诉引擎"白板长这样"
      # 每个节点函数接收 state: TranslatorState，从中读字段、返回新字段
      # 引擎自动把节点返回的字典合并到白板上
    """

    # === Input ===
    # 【中文·输入区】这些字段由外部（TranslationAgent.translate_chapter）
    # 在启动一章翻译时填入，流水线运行期间不会被修改。
    chapter_title: str          # 章节标题，如"第42章 夜探鬼村"
    chapter_content: str        # 章节中文原文全文
    chapter_number: int         # 章节序号（从 1 开始），用于日志和账单归属
    book_id: str                # which book this chapter belongs to (token ledger key)
    # 【中文】这本书的唯一编号。两个用途：
    # ① 术语库按 book_id 隔离，不同书不会串味
    # ② token_ledger 按 book_id 记账，算清每本书花了多少钱
    target_lang: str            # e.g. "en-US"
    # 【中文】目标语言代码。支持的值见 config.SUPPORTED_LANGUAGES：
    # "en-US"（美式英语）、"es-ES"（西班牙语）、"de"（德语）、"fr"（法语）。
    genre: str                  # e.g. "romance_ceo", "xianxia", "urban"
    # 【中文】小说类型标签。影响 READ 加载哪套文化规则、
    # WRITE 用什么文风、提示词里嵌什么示例。
    content_type: str           # "novel" — the only validated prompt branch
    # 【中文】内容类型。目前只有 "novel" 一种经过验证的提示词分支，
    # 预留字段以便将来支持漫画脚本、剧本等其他体裁。

    # === Glossary ===
    # 【中文·术语查询结果区】在 READ 之前，TranslationAgent 会查好术语
    # 填进这几个字段，让 READ 和 WRITE 知道"这些词以前是怎么译的"。
    exact_glossary: dict            # {term_cn: term_en} — exact matches
    # 【中文】精确匹配术语表：中文原词完全一致才命中。
    # 例：{"鬼节": "Ghost Festival"} → 只有原文写"鬼节"才会触发。
    semantic_terms: list[dict]      # [{term_cn, term_en, category}] — semantic hits
    # 【中文】语义匹配术语表：意思相近也能命中（靠向量库）。
    # 例：查"中元节"可能命中"鬼节→Ghost Festival"，因为两者语义相关。
    exact_matches_text: str         # Formatted table for prompt injection
    # 【中文】上面精确术语表的"人类可读版"，已经格式化成文本表格，
    # 直接塞进提示词里给大模型看。
    semantic_matches_text: str      # Formatted table for prompt injection
    # 【中文】语义术语表的人类可读版，同上。

    # === Translation output ===
    # 【中文·翻译产出区】流水线运行过程中逐步填充的结果。
    translated_text: str            # WRITE 产出的英文稿（或目标语言稿）
    new_terms_found: Annotated[list[dict], operator.add]  # Accumulated across retries
    # 【中文】本章发现的新术语。标记了 operator.add，意味着多轮 FIX 循环中
    # 每轮找到的术语会累加，不会被后一轮覆盖。
    adaptation_notes: list[str]     # WRITE 的文化适配笔记
    # FIX's own account of what it changed and why. Separate from
    # adaptation_notes and deliberately accumulated: FIX → READBACK → FIX is a
    # loop, and adaptation_notes is a plain field, so a second FIX round used to
    # overwrite the first round's notes — the paid-for repair knowledge was
    # discarded before anything could read it.
    #
    # 【中文】FIX 自己记录的"我改了什么、为什么改"。
    # ⚠️ 历史踩坑：这个字段曾经不存在，FIX 的修改记录写在 adaptation_notes 里。
    # 但 adaptation_notes 是普通字段（不带 operator.add），第二轮 FIX 写入时
    # 会把第一轮的记录覆盖掉——花钱修来的经验就这么丢了。
    # 所以单独拆出 fix_lessons 并标记为累加模式，确保多轮修补的经验全部保留。
    fix_lessons: Annotated[list[str], operator.add]
    chapter_summary: str            # 本章剧情摘要，供后续章节当"前情提要"

    # === Context continuity ===
    # 【中文·跨章连续性】让当前章节知道"前面发生了什么"。
    previous_chapter_summary: str   # From previous chapter → injected into next
    # 【中文】上一章的剧情摘要。由 TranslationAgent 在翻完上一章后填入，
    # READ 和 READBACK 都会读到它，避免"断章"导致的误判。

    # === Quality ===
    # 【中文·质量评估区】冷读者（READBACK）的打分和问题反馈。
    # quality_score is a MEASUREMENT, not a default. It starts at 0.0 meaning
    # "nobody has read this chapter yet" and is only set by readback_node
    # (PASS=5.0, NEEDS_FIX=2.0, unparseable response=0.0). It used to start at
    # 5.0, which made fast-mode chapters look perfect while never being read —
    # downstream averaging then reported a quality score nobody ever measured.
    #
    # 【中文】质量分数。**这是一个测量值，不是默认值**。
    # 初始值 0.0 表示"还没人读过这一章"。只有 readback_node 才会写入真实分数：
    # PASS=5.0、NEEDS_FIX=2.0、解析失败=0.0。
    # ⚠️ 历史踩坑：这个字段曾经默认值是 5.0。结果快速模式（跳过冷读）的章节
    # 看起来全是满分，下游一平均就虚高——实际上根本没人读过那些章节。
    # 改成 0.0 后，没被读过的章节就不会污染平均分。
    quality_score: float
    quality_issues: list[str]       # 冷读者指出的具体问题列表
    retranslation_count: int        # 本章已经经历了几轮 FIX→READBACK 循环
    # Authoritative flag: True means READBACK never ran for this chapter, so
    # quality_score carries no information. Downstream must check this before
    # treating a score as evidence of quality.
    #
    # 【中文】"冷读者是否跳过"的标志位。True = 这章走了快速模式，
    # 冷读者根本没跑过，quality_score 里的数字毫无意义。
    # 下游代码在做统计或展示前必须先检查这个标志，
    # 否则会把"未检测"误当成"满分"。
    readback_skipped: bool

    # === Glossary snapshot (for checkpoint) ===
    # 【中文·术语库快照】用于断点续翻（checkpoint）。
    glossary_snapshot_json: str     # JSON string of exact glossary for SQLite checkpoint
    # 【中文】精确术语库的 JSON 序列化字符串。
    # LangGraph 的存档机制（checkpoint）会把整个状态存进 SQLite，
    # 但术语库对象本身不能直接序列化，所以先转成 JSON 字符串再存。
    # 恢复时再从字符串反序列化回来。

    # === Dialect adaptation ===
    # 【中文·方言适配】
    dialect_context: str             # Detected dialect hints for this chapter
    # 【中文】本章检测到的方言线索。比如角色说了粤语、四川话等，
    # READ 会据此提示 WRITE 在英文版里做相应的语体调整。

    # === v0.15: Reader-centric agent outputs ===
    # 【中文·v0.15 新版流水线特有字段】
    # 这些字段是"以读者为中心"改版后新增的，承载各角色的结构化产出。
    read_analysis: dict              # READ agent's structured reading analysis
    # 【中文】READ 角色的结构化分析结果。包含：
    # emotional_arc（情感走向）、cultural_gaps（文化差异点）、
    # image_gaps（画面缺口）、terminology_decisions（术语决策）等。
    readback_feedback: dict          # READBACK cold reader's honest reaction
    # 【中文】冷读者的真实反馈。包含 verdict（PASS/NEEDS_FIX）和具体意见。
    context_signals: str             # Aggregated output from 9 signal detectors
    # 【中文】9 个自动检测器（方言、成语、拟声词、度量衡等）的汇总输出。
    # 作为 READ 的"起步线索"，不是定论——READ 需要自己验证。
    image_gaps: list[dict]           # Sensory image gaps: what CN reader sees vs EN reader misses
    # 【中文】画面缺口列表。每条记录一个"中文读者自动脑补、
    # 但英文读者看不到"的感官细节。这是本项目的核心设计理念之一。
    style_memo: str                  # Accumulated translation lessons from prior chapters
    # 【中文】翻译经验本。汇集了前面所有章节积累的翻译教训和经验，
    # 越翻越厚。每次翻译都塞进提示词，让 AI 不重复犯错。
    # When True, skip READBACK+FIX (fast mode for non-sample chapters)
    # 【中文】快速模式开关。True = 跳过冷读和修补，省一半费用。
    # 实际用法：大部分章节走快速模式，里程碑章节才走完整检查。
    skip_readback: bool
    api_key: str                     # BYOK: user-provided API key ("" = use env default)
    # 【中文】用户自带的 API 密钥（BYOK = Bring Your Own Key）。
    # 空串表示使用 .env 里配置的默认密钥。
    cold_read_context: str           # Character roster + recent summaries for READBACK briefing
    # 【中文】给冷读者的"前情提要"。包含已出场角色名单和最近几章摘要，
    # 让冷读者不至于因为不了解前文而误判情节。
