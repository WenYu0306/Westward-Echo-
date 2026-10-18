"""LangGraph graph assembly — v0.15 Reader-Centric Multi-Agent pipeline.

4-node graph:
  START → READ → WRITE → READBACK → (NEEDS_FIX?) → FIX → READBACK (loop)
                               ↓ (PASS)
                              END

Each agent is a READER, not a worker:
  READ     — Chinese web novel fan, reads and analyzes
  WRITE    — Bilingual genre writer, retells in English
  READBACK — Cold reader, naive American, honest reaction
  FIX      — Editor, repairs based on cold reader feedback

════════════════════════════════════════════════════════════════════════
【中文·这是整个项目最核心的文件，先看这里】
════════════════════════════════════════════════════════════════════════

一、这个项目到底在做什么？
    把中文网络小说翻译成英文（也支持西班牙语/德语/法语）。
    但它不是"逐句翻译"，而是组织 4 个 AI 角色，像一个小编辑部那样
    轮流读稿、写稿、挑刺、修补，最后交出一份"读起来不像翻译"的稿子。

二、4 个角色分别是谁？（本文件把它们串成一条流水线）
    READ     ——「中文网文老书虫」：先读中文章节，分析里面有啥文化梗、
                哪些画面中文读者一看就懂而外国读者完全无感，并定下术语怎么译。
    WRITE    ——「英文类型小说作者」：拿着上面的分析，用英文把这一章"重讲"一遍。
                注意他的身份是**作家**不是**翻译**，允许调整语序、压缩啰嗦、
                补上必要的画面感。
    READBACK ——「毫不知情的外国读者」：只看英文成品，**根本不知道这是翻译稿**，
                凭真实阅读感受回答：我哪儿看糊涂了？哪儿觉得无聊？还想接着看吗？
    FIX      ——「编辑」：拿着上面那位读者的具体抱怨，只修补出问题的地方，
                不整章重写。

三、为什么非要搞个"不知道是翻译"的读者？（这是本项目最核心的设计）
    如果告诉 AI"这是翻译稿，请检查质量"，它会下意识宽容，倾向于说"还不错"。
    只有让它当一个纯粹的外国读者，它才会诚实地说"这段我看不懂"。
    这等于给流水线装了一道**不掺水的质量关卡**。

四、一章的完整流转（对应上面的箭头图）：
      中文章节 → READ 分析 → WRITE 写成英文 → READBACK 冷读打分
                ├─ 判定 PASS（读着顺）      → 收工，进入下一章
                └─ 判定 NEEDS_FIX（有障碍）→ FIX 靶向修补 → 再送回 READBACK 复检
                    （这个"修补→复检"最多循环 2 轮，超了就带着问题放行）

五、本文件里两个主要东西：
      build_graph()      —— 把 4 个角色连成流水线（搭"轨道"）
      TranslationAgent   —— 对外使用的封装类，负责喂章节、取结果、
                            积累术语和翻译经验（跑"列车"）

六、和别的文件的关系：
      4 个角色的具体实现在 src/agent/nodes/ 目录下（read.py / write.py 等）
      喂给角色的提示词在 src/agent/prompts/ 目录下
      本文件只管"谁接谁、走哪条岔路"，不管每个角色内部怎么想。
"""

from __future__ import annotations

import logging

# 【中文·下面这些 import 就是流水线要用到的全部零件】
# LangGraph 是一个"流程图执行引擎"：你告诉它有哪些节点、节点之间怎么连，
# 它负责按顺序跑，并支持根据结果走不同岔路（本项目就靠它实现"修→复检"循环）。
from langgraph.graph import END, StateGraph

# 长章节自动切块（太长的一次喂给 AI 效果差，也贵）
from ..chapter_slicer import build_segment_title, should_split, split_chapter
# 全局配置：最多重修几次、目标语言的显示名
from ..config import MAX_RETRANSLATION_ATTEMPTS, target_language_label
# 术语库两层存储：
#   ExactGlossary   —— 精确匹配（中文原词一模一样才命中），存在 SQLite 数据库里
#   SemanticGlossary—— 语义相近匹配（意思相关的也算），存在向量库里
from ..glossary.exact_store import ExactGlossary
from ..glossary.semantic_store import SemanticGlossary
# 4 个角色的具体实现（+ 1 个术语复核角色）
from .nodes.fix import fix_node
from .nodes.read import read_node
from .nodes.readback import readback_node
from .nodes.review_terms import review_terms_node
from .nodes.write import write_node
# 文化忠实度检查：确认 WRITE 没把有含义的名字简单音译成拼音
from .fidelity import check_cultural_fidelity, is_single_rendering, is_valid_rendering
# 流水线的"共享白板"数据结构：各角色往上读写同一份状态
from .state import TranslatorState

logger = logging.getLogger(__name__)

# Max FIX → READBACK loops per chapter
# 【中文】一章最多允许「FIX 修补 → READBACK 复检」来回循环几次。
# 超过次数就**带着现有问题放行**（业内叫"强制接受"），并记一条警告日志。
# 为什么要有上限：不设限的话，遇到 AI 反复挑剔的章节会无限循环，
# 一章就能烧掉几十次调用的钱，而且卡住整本书的进度。
# 实践上"修 2 次还不合格"说明问题不在文字层面，硬修也没用。
_MAX_FIX_ATTEMPTS = MAX_RETRANSLATION_ATTEMPTS

# Max FIX repairs banked into the style memo per chapter. FIX is asked for one
# line per change and can return many; record_lesson dedups repeats but does not
# bound volume, and bridges.md is already the largest drawer on real books
# (31,818 lines on difu_xiao_xiansheng). The model reports changes in the order
# it made them, which carries no recency signal, so the first N are kept.
#
# 【中文】每章最多把 FIX 的几条"修补经验"存进翻译记忆。
# 背景：这个项目有个"翻译经验本"（style memo），会越记越厚，
# 每次翻译都要把经验本塞进提示词里，所以它一膨胀，**每一章都变贵变慢**。
# 真实数据：《地府叫我小先生》那本，经验本里"文化桥接"这一格已经 31818 行。
# 存经验的方法只会去重、不会限量，所以必须在这里硬性截断。
# 为什么取前 5 条而不是后 5 条：模型是按"它动手的先后"汇报的，
# 这个顺序不代表哪条更重要，所以没有"取最新"的依据，取前几条即可。
_MAX_FIX_LESSONS_PER_CHAPTER = 5


def _should_readback(state: TranslatorState) -> str:
    """Route WRITE output: skip READBACK in fast mode, otherwise check quality.

    【中文·岔路口 1】WRITE 写完英文后，走哪条路？
    这是个"分流函数"——它自己不干活，只负责返回一个字符串，
    告诉流程图引擎"接下来去哪个节点"。

    判断依据：状态里有没有 skip_readback 这个开关（快速模式）。
      开了  → 直接收工（END），跳过冷读者和编辑
      没开  → 送去 READBACK 冷读检查

    【中文·为什么要有快速模式】冷读+修补要多花约一半的调用次数和费用。
    一本 2000 章的书全程冷读太贵，所以实际用法是：
    大部分章节走快速模式省钱，每隔若干章（里程碑章节）才走完整检查。
    """
    if state.get("skip_readback", False):
        return END
    return "readback_node"


def _needs_fix(state: TranslatorState) -> str:
    """Route READBACK failures to the FIX editor.

    If the cold reader says NEEDS_FIX and we haven't exceeded max attempts,
    route to the FIX node — an editor who fixes specific reader complaints.

    【中文·岔路口 2】冷读者看完英文稿后，走哪条路？

    看两样东西：
      ① verdict —— 冷读者的判决，只有两种取值：
         "PASS"      读着顺，收工
         "NEEDS_FIX" 有明显阅读障碍，需要修
      ② retranslation_count —— 这一章已经修过几轮了

    只有"判决是 NEEDS_FIX" **并且** "还没修满 2 次"，才送去 FIX；
    其余情况一律收工（END）。

    【中文·这两个条件缺一不可的原因】
      只看判决 → 会无限循环烧钱（冷读者可能永远挑得出毛病）
      只看次数 → 明明读着顺也要白修一遍，浪费钱
    所以是"有问题"且"还有修的机会"才修。

    【中文·为什么默认值是 PASS 而不是 NEEDS_FIX】
    代码里三处都写了 `or "PASS"` / `.get(..., "PASS")`：
    万一冷读者没返回结果（超时、解析失败），按"通过"处理直接放行。
    这是刻意的取舍——宁可对一章漏检，也不要因为一次网络抖动
    就整本卡死或触发无意义的重修。
    """
    feedback = state.get("readback_feedback", {})
    verdict = feedback.get("verdict", "PASS") if feedback else "PASS"
    retries = state.get("retranslation_count", 0)

    if verdict == "NEEDS_FIX" and retries < _MAX_FIX_ATTEMPTS:
        return "fix_node"
    return END


def build_graph(
    exact_store: ExactGlossary,
    semantic_store: SemanticGlossary,
):
    """Build the v0.15 4-node reader-centric graph.

    【中文·把 4 个角色连成一条流水线（搭"轨道"）】

    打个比方：这个函数就是在画一张地铁线路图。
    它规定"哪几站存在"以及"从哪站能到哪站"，画完就交给引擎去跑。
    画线路图本身不运送任何乘客，所以这个函数**不花钱、不调用 AI**。

    【参数】两个术语库，会一路传给需要它们的角色（主要是 READ）：
      exact_store    精确术语库（中文原词完全一致才命中）
      semantic_store 语义术语库（意思相近也能命中）

    【返回】一张编译好的流程图，之后调 .invoke(状态) 就能跑一章。

    【中文·为什么术语库要当参数传进去，而不是在角色内部自己建】
    因为整本书要共用同一份术语库（第 5 章定的译名，第 500 章还得用）。
    从外面注入同一个对象，各角色才能共享、才能持续积累；
    如果每个角色自己建一份，术语就会各翻各的、前后不一致。
    """

    builder = StateGraph(TranslatorState)

    # ── Nodes ──
    # 【中文·登记站点】告诉引擎"这条线上有这几站，每站由谁值守"。
    # 注意：add_node 只是登记，此刻**一个 AI 都还没调用**。
    builder.add_node(
        "read_node",
        # 【中文】用 lambda 包一层，是为了把术语库"顺手带进去"。
        # 引擎调用节点时只会传一个参数（状态），而 read_node 还需要术语库，
        # 所以这里包一层把两者凑齐。这是依赖注入的常见写法。
        lambda s: read_node(s, exact_store, semantic_store),
    )
    builder.add_node("review_terms_node", review_terms_node)  # 术语复核（第 5 个角色）
    builder.add_node("write_node", write_node)                # 英文写作
    builder.add_node("readback_node", readback_node)          # 冷读者挑刺
    builder.add_node("fix_node", fix_node)                    # 编辑修补

    # ── Edges ──
    # 【中文·铺固定轨道】这几段是"无条件直达"：跑完前一站必然去下一站。
    builder.set_entry_point("read_node")            # 列车从 READ 站发车
    builder.add_edge("read_node", "review_terms_node")      # READ → 术语复核
    builder.add_edge("review_terms_node", "write_node")     # 术语复核 → WRITE

    # ── Conditional: skip READBACK in fast mode, otherwise quality check ──
    # 【中文·铺岔路 1】WRITE 之后要分岔，由上面 _should_readback() 决定去向。
    # 第三个参数是"返回值 → 目的地"的对照表：
    # 函数返回 "readback_node" 就去冷读站，返回 END 就直接收工。
    builder.add_conditional_edges(
        "write_node",
        _should_readback,
        {
            "readback_node": "readback_node",
            END: END,
        },
    )

    # ── Conditional: cold reader fails → FIX, otherwise END ──
    # 【中文·铺岔路 2】冷读之后也要分岔，由 _needs_fix() 决定：
    # 不合格且还有重修机会 → 去 FIX 站；否则 → 收工。
    builder.add_conditional_edges(
        "readback_node",
        _needs_fix,
        {
            "fix_node": "fix_node",
            END: END,
        },
    )

    # FIX → back to READBACK for re-check
    # 【中文·铺回路，形成闭环】FIX 修完不是直接收工，而是**送回冷读者复检**。
    # 这一条边就是整个流水线里唯一的"环"，也是质量能收敛的关键：
    # 修完必须有人验收，验不过还能再修（最多 2 轮）。
    #
    # ⚠️ 有环就必须有出口，否则会无限循环。出口就是上面 _needs_fix() 里的
    # "已修满 2 次就不再修"这个条件——环靠它才能断开。
    builder.add_edge("fix_node", "readback_node")

    # 【中文·编译】把上面画的线路图"定型"成可执行对象。
    # 编译后线路就固定了，运行期不能再改结构，只能往里喂数据。
    return builder.compile()



class TranslationAgent:
    """High-level wrapper around the v0.15 reader-centric LangGraph pipeline.

    【中文·这是"跑列车"的类，外部代码主要通过它来翻译】

    上面 build_graph() 只是画好了线路图，但线路图自己不会跑。
    这个类就是把线路图用起来的那个"调度员"，对外提供三件事：
      1. 建好一切准备工作（术语库、经验本、流程图）—— __init__
      2. 喂进去一章中文，吐出这一章的英文成品 —— translate_chapter
      3. 每翻完一章，把新术语和新经验沉淀下来，供后续章节复用 —— _post_process

    【中文·典型用法（别的文件就是这么调它的）】
        agent = TranslationAgent(book_id="某本书", target_lang="en-US")
        result = agent.translate_chapter(章节标题, 章节正文, 第几章)
        英文稿 = result["translated_text"]

    【中文·为什么要有"经验沉淀"这一步】
    这是本项目能翻完 2301 章还保持质量稳定的关键。
    第 1 章学到的东西（这个人叫什么、这个词怎么译、这种文化梗怎么处理），
    会存进术语库和经验本；翻到第 2000 章时，前面 1999 章的经验都在手上，
    所以译名全书统一、文风不会越翻越飘。没有这一步，每章都是从零开始。
    """

    def __init__(self, book_id: str = "default", api_key: str = "",
                 target_lang: str = "en-US"):
        """【中文·准备工作】建一个调度员，把它要用的东西都配齐。

        【参数】
          book_id     这本书的编号。用来隔离数据：不同书的术语库、
                      经验本、账单记录各存各的，不会串味。
                      默认 "default"，正式翻译时应该给每本书一个唯一编号。
          api_key     调用方自带的 AI 服务密钥（业内叫 BYOK，Bring Your Own Key）。
                      留空就用 .env 里配的默认密钥。
          target_lang 目标语言，取值见 config.SUPPORTED_LANGUAGES：
                      "en-US"（美式英语）、"es-ES"（西班牙语）、"de"（德语）、"fr"（法语）。
        """
        self.book_id = book_id            # token ledger attribution key
        # 【中文】book_id 还兼作"账单归属"：每章花了多少钱，按这个编号记账，
        # 这样多本书同时翻也能算清各自的成本。
        self.target_lang = target_lang
        # The store is constructed with this job's language as its *active*
        # language. Callers that export the glossary without naming a language
        # (`to_dict()`, `snapshot()`, `len()`) then read this job's terms rather
        # than whichever bucket the instance defaulted to. A job translates one
        # language, so active == the job's language is the right default.
        #
        # 【中文·术语库其实按语言分格存放】同一个库对象里可以同时装着
        # 英语、西班牙语等多套术语，各占一格。这里在创建时就把
        # "当前这本书用的语言"设成**默认活跃格**，好处是：
        # 之后导出术语时如果忘了指定语言，读到的也是这本书的术语，
        # 而不是碰巧读错格。一次任务只翻一种语言，所以这个默认值是对的。
        self.exact_store = ExactGlossary(book_id=book_id, target_lang=target_lang)
        self.semantic_store = SemanticGlossary(book_id=book_id)
        # 【中文】把两个术语库交给流程图，让 4 个角色共享同一份
        self.graph = build_graph(self.exact_store, self.semantic_store)
        # 翻译经验本（style memo）：按 book_id 分开存，越翻越厚
        from ..style_memo import StyleMemoStore
        self.style_memo: StyleMemoStore = StyleMemoStore(book_id)
        # 【中文·下面三个是"缓存位"，先留空，运行时才填】
        # 预取结果：术语查询比较慢，可以让后台线程提前查好存这儿，
        # 轮到真正翻译时直接取用，不用干等（见 set_prefetched_glossary）
        self._prefetched_exact: dict | None = None
        self._prefetched_semantic: list[dict] | None = None
        # 最近几章的剧情摘要，喂给冷读者当"前情提要"
        self._chapter_context: list[str] = []
        self.api_key = api_key  # User-provided BYOK key (empty = use config default)

    def _update_context(self, chapter_number: int, summary: str):
        """Accumulate recent chapter summaries for cold reader context.

        【中文·维护"前情提要"缓冲区】把刚翻完这章的剧情摘要存起来，
        下次给冷读者看，这样它读到第 50 章时知道前面发生了什么，
        不会因为"没看过前文"而误判某些情节莫名其妙。

        【中文·两个细节】
        ① 摘要太短（≤20 字）就丢弃：多半是没生成成功，留着反而添乱。
        ② 只保留最近 5 章：前情提要也要塞进提示词，太长就变贵，
           而且太久远的剧情对理解当前章节帮助不大。
        """
        if summary and len(summary) > 20:
            self._chapter_context.append(
                f"[Ch{chapter_number}] {summary.strip()[:300]}"
            )
        # Keep the last 5
        # 【中文】列表切片 [-5:] 的意思是"只留最后 5 个"，前面的自动丢掉
        if len(self._chapter_context) > 5:
            self._chapter_context = self._chapter_context[-5:]

    def _build_cold_read_context(self, chapter_number: int = 0,
                                 target_lang: str = "en-US") -> str:
        """Build a one-page briefing for the cold reader — no LLM needed.

        ``target_lang`` scopes the character roster: a store holding several
        languages must not brief the reader with another job's renderings.

        【中文·给冷读者写一页"读者须知"】

        冷读者的设定是"毫不知情的外国读者"，但也不能让它真的一无所知——
        否则它连人物是谁都不知道，会把"没看过前文"误判成"这段写得烂"。
        所以这里给它一份简短的背景说明，只包含三类信息：
          1. PREVIOUSLY   —— 前情提要（最近几章讲了啥）
          2. CHARACTERS   —— 人物表（这些名字分别是谁）
          3. TERMINOLOGY  —— 已确定的术语表（这些词该怎么理解）

        【中文·注意这份须知里"不该有什么"】
        绝对不能出现中文原文。冷读者必须"盲读"英文成品，
        一旦让它看到中文，它就不是读者而是"对照检查员"了，
        那种宽容的评审就失去意义（下面代码里专门有一处为此做过修正）。

        【中文·这个方法不花钱】纯字符串拼接，不调用任何 AI。

        【参数 target_lang 的作用】术语库可能同时装着好几种语言，
        必须按当前语言取，否则会把别的任务的译名混进来（比如
        翻西班牙语的书却给冷读者看英语人名表）。
        """
        parts = []

        # ── 1. What's happened recently ──
        # 【中文·第一块：前情提要】
        if self._chapter_context:
            # 有存好的摘要就列出来
            parts.append("## PREVIOUSLY (recent chapter summaries)\n")
            for entry in self._chapter_context:
                parts.append(f"- {entry}")
            parts.append("")
        elif chapter_number > 1:
            # 没有摘要但已经翻到第 2 章之后了，至少告诉它"你正在读第 N 章"，
            # 免得它以为是开头，对"没有铺垫"产生错误抱怨。
            parts.append("## PREVIOUSLY\n"
                         f"You are reading Chapter {chapter_number}. "
                         "This is an early chapter.\n")

        # ── 2. Who's who (from exact glossary) ──
        # 【中文·第二块：人物表】从术语库里挑出"看起来像人名"的条目
        known = self.exact_store.to_dict(target_lang=target_lang)
        char_list = []
        for cn, en in known.items():
            if len(cn) >= 2 and len(en) >= 2:
                # Render the English name only. The cold reader is a blind
                # target-language reader, so the trailing Chinese source name
                # `(cn)` was a source-text leak (clause 10) — metadata-level,
                # but stripping it makes the roster strictly blind. The
                # `len(cn) >= 2` guard still reads the source length to tell a
                # real name from a single-character term; it just no longer
                # prints the source.
                #
                # 【中文·这里曾经漏过原文，已修正】
                # 早先人物表每行是「英文名 (中文原名)」，看起来只是附注，
                # 但对"必须盲读"的冷读者来说，这就是原文泄漏——
                # 它一看到中文就能猜出意思，评审就不诚实了。所以现在只印英文名。
                #
                # 【中文·那 len(cn) >= 2 为什么还留着】它是在**用中文的长度**
                # 做筛选判断（两个汉字以上才像人名，单个字多半是术语如"剑""气"），
                # 判断完并不把中文打印出来。"看长度"和"给读者看"是两回事。
                char_list.append(f"  - **{en}**")
            if len(char_list) >= 15:
                break   # 【中文】最多列 15 个，再多就把提示词撑长了
        if char_list:
            parts.append("## CHARACTERS\n" + '\n'.join(char_list) + "\n")

        # ── 3. Key terms from style memo ──
        # 【中文·第三块：已确定的术语】从经验本里捞形如「[原词] → 译法」的行
        terms_text = self.style_memo.read_all()
        term_lines = []
        for line in terms_text.split('\n'):
            stripped = line.strip()
            if stripped.startswith('[') and '→' in stripped:
                term_lines.append(f"- {stripped[:120]}")
                if len(term_lines) >= 8:
                    break   # 【中文】同样限量，最多 8 条
        if term_lines:
            parts.append("## ESTABLISHED TERMINOLOGY\n")
            parts.extend(term_lines)
            parts.append("")

        # A briefing with any one section is still worth sending: an off-by-one
        # here (`<= 1`) silently discarded a chapter-2+ briefing that had only
        # the "early chapter" line, or only a character roster.
        #
        # 【中文·这里踩过"差一个"的经典 bug】
        # 早先的判断条件写成了 len(parts) <= 1，意思是"不足两块就整个不发"。
        # 结果：只有一块内容的须知被默默丢弃了——比如第 2 章之后
        # 只有"你在读第 N 章"这一句、或者只有人物表没有前情提要，
        # 这两种情况本来都该发给冷读者，却被扔了。
        # 现在改成"空才不发"，只要有任何一块内容就照发。
        if not parts:
            return ""
        return '\n'.join(parts)

    def load_glossary(self, target_lang: str = "en-US"):
        """【中文·从数据库把术语读进内存】

        术语平时存在 SQLite 数据库文件里（断电、重启都不丢）。
        开始翻译前调这个方法，把术语加载进内存，
        之后每章匹配术语就是内存操作，快得多。
        """
        self.exact_store.load_from_db(target_lang)

    def load_glossary_snapshot(self, snapshot_json: str, target_lang: str = "en-US"):
        """Restore a checkpoint's glossary into the given language's bucket.

        The flat snapshot shape carries no language of its own, so the caller
        must say which one it belongs to. Restoring a Spanish checkpoint without
        this would file its terms under en-US and then match nothing, since
        every lookup is scoped to the chapter's language.

        【中文·断点续翻时，把术语"恢复"回来】

        什么是断点续翻（checkpoint）：翻译过程中每章都把进度存一份档，
        万一中途崩溃、断电、或你按 Ctrl+C 停掉，重启后能从上次那章接着翻，
        前面几百章的成果和术语一个都不丢。

        存档里就包含当时积累的术语表（一坨 JSON 文本），本方法负责还原它。

        【中文·为什么必须显式传 target_lang（这里踩过坑）】
        存档的 JSON 是"扁平"结构，里面**没有记录这批术语属于哪种语言**。
        所以还原时必须由调用方说明。早先没传这个参数，
        结果还原西班牙语存档时，术语被错放进了英语那一格；
        而查术语时是按章节语言查的，于是**一条也匹配不上**——
        续翻之后术语全乱、前后不一致，还很难查出原因。

        【中文·那个 != "{}" 判断】空存档（没术语）就跳过，不做无意义操作。
        """
        if snapshot_json and snapshot_json != "{}":
            self.exact_store.restore_snapshot(snapshot_json, target_lang=target_lang)

    def set_prefetched_glossary(self, exact_matches: dict, semantic_matches: list[dict]):
        """Accept pre-computed glossary results from ChapterPrefetcher.

        When the prefetcher has already run exact-match and semantic-search
        for this chapter in a background thread, the caller injects the
        results here so _make_state can skip those lookups.

        【中文·提速用：接收"提前查好"的术语结果】

        问题：翻译第 5 章时，机器大部分时间在等 AI 回话；
        而查术语（尤其语义搜索）也要花时间，两件事串行做就是纯浪费。

        解法：趁着 AI 还在写第 5 章，后台线程先把第 6 章的术语查好，
        存到这里。等真要翻第 6 章时直接取现成结果，省掉等待。

        这就是"预取"（prefetch）。本方法只是个"收件箱"：
        外部把查好的结果塞进来，_make_state 看到有货就不再自己查。
        """
        self._prefetched_exact = exact_matches
        self._prefetched_semantic = semantic_matches


    def translate_chapter(
        self,
        chapter_title: str,
        chapter_content: str,
        chapter_number: int,
        previous_summary: str = "",
        target_lang: str = "en-US",
        genre: str = "romance_ceo",
        skip_readback: bool = False,
        use_flash_writer: bool = False,
        content_type: str = "novel",
    ) -> dict:
        """Translate a single chapter through the 5-node reader-centric pipeline.

        Set skip_readback=True for fast mode: READ→WRITE→END, skipping
        the cold reader and editor nodes. Use for non-sample chapters
        to save ~50% API calls and latency.

        content_type selects the prompt branch ("novel" is the only
        validated path; unknown types fall back to novel).

        Returns:
            {translated_text, new_terms_found, adaptation_notes,
             chapter_summary, quality_score, quality_issues,
             glossary_snapshot_json}

        【中文·这是外部最常调用的方法：翻译一章】

        【参数逐个说明】
          chapter_title    章节标题，如"第一章 重生"
          chapter_content  章节正文（中文原文），可能很长
          chapter_number   第几章，用于日志和经验本标记
          previous_summary 上一章的剧情摘要，帮助本章衔接
          target_lang      翻成哪种语言，默认美式英语
          genre            小说题材，如 romance_ceo（都市言情）、xianxia（仙侠）、
                           folk_religion（民俗志怪）、scifi（科幻）。
                           题材会影响提示词——仙侠里的"金丹""元婴"和都市言情里的
                           处理方式完全不同。
          skip_readback    True = 走快速模式，跳过冷读和修补（省约一半费用）
          use_flash_writer WRITE 角色用更便宜的模型（进一步省钱）
          content_type     内容类型，目前只验证过 "novel"（小说）这一条路径，
                           填别的会退回按小说处理

        【返回】一个字典，主要字段：
          translated_text        英文成品正文 ← 你最关心的就是这个
          new_terms_found        本章新发现的术语（已存入术语库）
          adaptation_notes       文化改编说明（哪里为了读者做了调整）
          chapter_summary        本章剧情摘要（供下一章衔接用）
          quality_score          冷读者打的分；0.0 表示"没测过"（快速模式）
          quality_issues         质量问题清单
          glossary_snapshot_json 术语库快照（存档用，断点续翻靠它恢复）
        """
        # ── Auto-split for long chapters ──────────────────────────
        # 【中文·长章节自动切块】
        # 有些章节特别长（网文单章上万字很常见）。一次全喂给 AI 有两个问题：
        #   ① 超出模型单次能吃下的长度，直接失败
        #   ② 太长时模型注意力涣散，后半段质量明显下降
        # 所以先判断要不要切：需要就切成几段分别翻，不需要就整章一次翻。
        # 判断标准在 chapter_slicer.should_split() 里。
        if should_split(chapter_content):
            result = self._translate_split(
                chapter_title, chapter_content, chapter_number,
                previous_summary, target_lang, genre, skip_readback,
                use_flash_writer, content_type=content_type,
            )
        else:
            result = self._translate_once(
                chapter_title, chapter_content, chapter_number,
                previous_summary, target_lang, genre, skip_readback,
                use_flash_writer, content_type=content_type,
            )

        return result

    def _make_state(
        self, title, content, number, prev_summary, lang, genre,
        skip_readback=False, use_flash_writer=False, content_type="novel",
    ) -> TranslatorState:
        """Build the initial state for a chapter translation.

        Populates exact_matches_text and semantic_matches_text from the
        glossary stores — this is the only glossary lookup step (no tool calls).

        【中文·填写"共享白板"，这是流水线开跑前的准备工作】

        什么是"状态"（state）：把它想成一块**所有角色共用的白板**。
        READ 往白板上写它的分析，WRITE 读分析、写英文稿，
        READBACK 读英文稿、写评审意见，FIX 读意见、改稿子……
        4 个角色不直接对话，全靠这块白板传递信息。

        本方法就是在发车前把白板擦干净、写上初始内容：
        原文是什么、第几章、翻成什么语言、有哪些术语可用等。

        【中文·白板内容分四类】（下面 return 的字典里有分组注释）
          输入类   —— 章节原文、标题、章号、题材
          术语类   —— 查好的术语表（本章用得上哪些译名）
          输出类   —— 先留空，等各角色往里填
          上下文类 —— 前情提要、经验本、给冷读者的须知

        【中文·一个重要设计：术语是"提前算好塞进去"的】
        本项目**不让 AI 自己调工具查术语**（no tool calls）。
        而是开跑前就在这里把术语查好、拼成文本，直接写进提示词。
        原因：让 AI 自己决定何时查术语，它经常查错、重复查、或该查时忘了查，
        结果术语不一致。提前算好塞进去，就完全没有这类不确定性。
        """
        # Exact matches: terms that appear in this chapter's text.
        # Use notes-enhanced format so the WRITE agent gets cultural context,
        # not just word-for-word mappings.
        # If ChapterPrefetcher already ran these lookups in a background thread,
        # use the cached results to skip the blocking lookups.
        #
        # 【中文·第一层术语：精确匹配】
        # "精确"指中文原词一模一样才算命中。比如术语库里有"金丹 → Golden Core"，
        # 本章正文出现"金丹"二字就命中。这层快、准，是术语一致性的主力。
        #
        # 下面这个 if 是在问："后台线程是不是已经提前查好了？"
        # 查好了就直接拿现成的（并且把缓存清空，避免下一章误用旧结果），
        # 没查好就当场查——当场查会卡住等结果，所以能预取就预取。
        if self._prefetched_exact is not None:
            exact_matches = self._prefetched_exact
            self._prefetched_exact = None
        else:
            # Scoped to this chapter's language, not the store's active one: a
            # store can now hold several languages at once, and defaulting to
            # the active language would inject another job's renderings into
            # this prompt.
            #
            # 【中文】这里显式传 target_lang=lang 是有原因的：术语库可能同时
            # 装着好几种语言，如果不指定就会用"当前活跃格"，
            # 结果把别的任务的译名混进本章提示词里。
            exact_matches = self.exact_store.match_in_text(content, target_lang=lang)
        # 【中文·把命中的术语拼成给 AI 看的文本】
        # 有命中就用"带注释版"——不只给"A → B"的对照，还附上文化说明
        # （比如这个词有什么典故、为什么这么译）。
        # WRITE 拿到的是背景知识而不是一张冷冰冰的对照表，处理起来更准。
        # 一个都没命中就用"简版"，避免塞一堆空表格浪费提示词空间。
        exact_text = (
            self.exact_store.to_formatted_text_with_notes(exact_matches, target_lang=lang)
            if exact_matches
            else self.exact_store.to_formatted_text(exact_matches, target_lang=lang)
        )

        # Semantic matches: culturally related terms for context
        # 【中文·第二层术语：语义相近匹配】
        # 精确匹配有个盲区：本章写"御剑飞行"，但术语库里只存过"飞剑"，
        # 字面不同就查不到。语义搜索能靠"意思接近"把它捞出来。
        # top_k=15 表示最多取 15 条最相关的。
        if self._prefetched_semantic is not None:
            semantic_hits = self._prefetched_semantic
            self._prefetched_semantic = None
        else:
            semantic_hits = self.semantic_store.search(content, top_k=15, target_lang=lang)
        # Filter out terms already covered by exact matches
        # 【中文·去重】已经被精确匹配捞到的，就别在语义结果里再出现一遍。
        # 同一个术语在提示词里重复出现既占篇幅又可能前后不一致。
        semantic_hits = [t for t in semantic_hits if t["term_cn"] not in exact_matches]
        semantic_text = self._format_semantic(semantic_hits, target_lang=lang)

        # 【中文·下面就是把白板填满。按用途分了组，注释说明每组干什么】
        return {
            # Input
            # 【输入】本章要翻的原始材料
            "chapter_title": title,
            "chapter_content": content,
            "chapter_number": number,
            # getattr: some callers build an agent via object.__new__ to skip
            # chroma/sqlite side effects and never run __init__.
            #
            # 【中文·这里为什么不直接写 self.book_id】
            # 有些调用方（主要是测试）为了避开"连数据库、建向量库"这些副作用，
            # 用特殊手法造出一个空壳对象，**根本没跑过 __init__**，
            # 于是 self.book_id 这个属性压根不存在，直接访问会崩。
            # getattr(对象, 名字, 默认值) 的意思是"有就用，没有就给默认值"，
            # 这里给空字符串，让空壳对象也能正常工作。
            "book_id": getattr(self, "book_id", ""),
            "target_lang": lang,
            "genre": genre,
            "content_type": content_type,
            # Glossary
            # 【术语】上面两层查询的结果，四种形态各有用途：
            #   exact_glossary        原始字典形式（程序内部用）
            #   semantic_terms        语义结果原始列表
            #   exact_matches_text    拼好的文本（塞进提示词给 AI 看）
            #   semantic_matches_text 同上
            "exact_glossary": self.exact_store.to_dict(target_lang=lang),
            "semantic_terms": semantic_hits,
            "exact_matches_text": exact_text,
            "semantic_matches_text": semantic_text,
            # Output (populated by nodes)
            # 【输出】先留空，等各角色跑完往里填
            "translated_text": "",       # WRITE 填英文成品
            "new_terms_found": [],       # READ 填本章新术语
            "adaptation_notes": [],      # WRITE 填文化改编说明
            "fix_lessons": [],           # FIX 填"我改了什么"（会存进经验本）
            "chapter_summary": "",       # 填本章剧情摘要
            # Context
            # 【上下文】上一章摘要，帮本章衔接剧情
            "previous_chapter_summary": prev_summary,
            # Quality / feedback
            # 0.0 = "not measured". readback_node is the only writer of a real
            # score. The old 5.0 initial value meant fast-mode chapters (which
            # skip READBACK) reported a perfect score nobody ever gave them.
            #
            # 【中文·这个初始值曾经是错的，值得留意】
            # 0.0 的含义是"没测过"，只有冷读者（READBACK）能写入真实分数。
            # 早先这里写的是 5.0（满分），于是走快速模式的章节
            # 明明**跳过了质检、没人给过分**，报表上却显示满分。
            # 这类"假满分"比没有分数更危险，因为它会让人误以为质量有保障。
            # 现在改成 0.0，配合 readback_skipped 标记，能明确区分
            # "确实测了但不合格"和"根本没测"。
            "quality_score": 0.0,
            "quality_issues": [],
            "retranslation_count": 0,        # 已经重修几轮（上限见 _MAX_FIX_ATTEMPTS）
            "readback_skipped": skip_readback,  # 标记本章是否跳过了冷读
            "glossary_snapshot_json": "",    # 术语快照，_post_process 里填
            # v0.15 reader-centric fields
            # 【中文·四个角色专属的字段】
            "read_analysis": {},       # READ 的分析结果（文化差异、画面缺口、术语决策）
            "readback_feedback": {},   # READBACK 的评审意见（判决 + 具体问题）
            "context_signals": "",     # 上下文信号（方言、系统面板、单位、拟声词等检测结果）
            "image_gaps": [],          # 画面缺口清单（中文读者秒懂、外国读者无感的那些）
            # read_relevant() takes no chapter context: it returns the newest
            # entries per drawer (recency, not relevance). The chapter's own
            # terms reach the prompt via exact_matches_text (built above from
            # `content`) and its text via chapter_content — passing either one
            # here would imply a matching step that does not exist.
            #
            # 【中文·翻译经验本（style memo）】
            # 这是本项目长期积累的知识库，分六个抽屉存（人物口吻、节奏规律、
            # 文化桥接套路、行文韵律、术语决策等）。翻到后面的章节时，
            # 前面学到的东西都在这里，所以全书风格统一。
            #
            # read_relevant() 名字有点误导：它其实不看本章内容，
            # 只是从每个抽屉里取**最新**的若干条（按新旧，不按相关度）。
            # 本章专属的术语已经通过上面的 exact_matches_text 传进去了，
            # 正文也在 chapter_content 里，不需要再传一次。
            #
            # or 后面那串是"经验本还是空的"（翻第一章）时的兜底提示：
            # 明确告诉 AI"你还没有任何积累，请紧贴原文，
            # 不要自己编画面和内心独白；随着经验本变厚，你的创作权限才会变大"。
            # 这条兜底很重要——否则第一章就可能自由发挥，跑偏之后
            # 后面所有章节都跟着这个错误基调走。
            "style_memo": self.style_memo.read_relevant() or (
                "(No translation memory yet. This is the first chapter. "
                "Stay close to the source text — do not invent sensory details "
                "or interior monologue that the original doesn't contain. "
                "Your creative authority grows as the memo accumulates. "
                "For now: translate faithfully.)"
            ),
            "skip_readback": skip_readback,        # 快速模式开关（岔路口 1 看它）
            "use_flash_writer": use_flash_writer,  # WRITE 是否用便宜模型
            "api_key": self.api_key or "",  # BYOK: user-provided key overrides env
            # 【中文】调用方自带的密钥优先；留空则用 .env 里的默认密钥
            # Cold reader briefing
            # 【中文·给冷读者的"读者须知"】由上面那个方法生成
            "cold_read_context": self._build_cold_read_context(
                chapter_number=number,
                target_lang=lang,
            ),
            "dialect_context": "",   # 方言提示，由方言检测模块后续填充
        }

    def _translate_once(
        self, title, content, number, prev_summary, lang, genre,
        skip_readback=False, use_flash_writer=False, content_type="novel",
    ) -> dict:
        """Run the 4-node pipeline on a single chapter.

        【中文·真正把流水线跑起来的地方（三行代码，但每行都是重头戏）】

        ① _make_state(...)  —— 把白板填好（原文、术语、经验本…）
        ② graph.invoke(...) —— 发车！4 个角色依次上场，中途可能循环修补
        ③ _post_process(...)—— 收尾：存术语、记经验、质检兜底

        第 ② 步是唯一真正花钱的地方（调用大模型）。

        【中文·recursion_limit=100 是什么】
        流程图引擎有个"最多走多少步"的保险，防止环状结构无限循环把机器卡死。
        正常一章只走 4~7 步（READ→复核→WRITE→冷读→[FIX→冷读]×2），
        设 100 是留足余量，真撞到 100 说明出事了，引擎会主动报错停下。
        """
        state = self._make_state(title, content, number, prev_summary, lang, genre,
                                 skip_readback, use_flash_writer, content_type)
        result = self.graph.invoke(state, config={"recursion_limit": 100})
        return self._post_process(result, lang)

    def _translate_split(
        self, title, content, number, prev_summary, lang, genre,
        skip_readback=False, use_flash_writer=False, content_type="novel",
    ) -> dict:
        """Translate a long chapter by splitting into segments.

        Each segment runs through the full 4-node pipeline independently.
        Segments 2+ inherit the glossary accumulated from earlier segments.

        【中文·长章节的分段翻译】

        一章太长时切成若干段（segment），**每段都独立走完整条流水线**
        （都有 READ/WRITE/READBACK/FIX），最后把各段英文拼起来当作整章成品。

        【中文·两个关键设计】
        ① 术语累积：第 2 段能用到第 1 段刚定的术语。
           因为术语库是共享的同一个对象，第 1 段跑完就存进去了，
           第 2 段自然查得到——所以同一章内译名不会前后打架。
        ② 剧情桥接：把上一段结尾的文字带给下一段当"前情"。
           否则第 2 段开头会像凭空冒出来，读着断裂。

        【返回】和 _translate_once 结构一致，但内容是各段合并后的结果。
        """
        segments = split_chapter(content)
        # 【中文·记一条日志】把原字数（去掉空格换行后）和切成几段都记下来，
        # 方便事后核对"切分是否合理"。
        logger.info(
            "Auto-split ch%d (%d chars) into %d segments",
            number, len(content.replace('\n', '').replace(' ', '')), len(segments),
        )

        all_text = []          # 收集各段英文成品，最后拼接
        all_new_terms = []     # 收集各段新发现的术语
        segment_summary = prev_summary   # 桥接用：先放上一章摘要，之后逐段更新
        final_result = {}      # 记住最后一段的完整结果，用于取摘要等字段

        for seg in segments:
            # 给这一段生成标题（形如"第三章（第 2/4 段）"），便于日志和排查
            seg_title = build_segment_title(title, seg)

            result = self._translate_once(
                seg_title, seg["content"], number, segment_summary, lang, genre,
                skip_readback, use_flash_writer, content_type=content_type,
            )

            tt = result.get("translated_text", "")
            all_text.append(tt)
            all_new_terms.extend(result.get("new_terms_found", []))
            final_result = result

            # Build bridging context for the next segment
            # 【中文·为下一段准备"前情提要"】
            # is_last 标记这是不是最后一段；是的话就不用准备了（没有下一段）。
            if not seg["is_last"]:
                # 按空行切段，只留长度超过 50 字的实质段落（过滤掉短句、对话碎片）
                paras = [p for p in tt.split("\n\n") if len(p.strip()) > 50]
                # 【中文·只取最后 1~2 段当桥接】
                # 为什么不给全文：前情提要要塞进提示词，给太多既贵又没必要——
                # 下一段只需要知道"紧接着发生了什么"，最后两段足够衔接。
                bridge = (
                    "\n\n".join(paras[-2:]) if len(paras) >= 2 else (paras[-1] if paras else "")
                )
                if bridge:
                    segment_summary = (
                        f"[Continuing from previous segment.]\n"
                        f"Previous segment ended with:\n{bridge}"
                    )

        return {
            "translated_text": "\n\n".join(all_text),   # 各段用空行分隔拼成整章
            "new_terms_found": all_new_terms,
            "adaptation_notes": final_result.get("adaptation_notes", []),
            "chapter_summary": final_result.get("chapter_summary", ""),
            # No 5.0 fallback: if the score was never measured it must stay 0.0
            # and readback_skipped must say so. A silent 5.0 default is what made
            # fast-mode chapters look reviewed.
            #
            # 【中文·这里刻意不给"满分兜底"】
            # 取不到分数就保持 0.0（含义是"没测过"），并由 readback_skipped
            # 说明原因。早先用 5.0 兜底，导致快速模式的章节看起来"已质检满分"，
            # 实际压根没人看过。宁可显示 0，也不要伪造一个好看的数字。
            "quality_score": final_result.get("quality_score", 0.0),
            "quality_issues": final_result.get("quality_issues", []),
            "readback_skipped": final_result.get("readback_skipped", True),
            "glossary_snapshot_json": final_result.get("glossary_snapshot_json", "{}"),
        }


    def _post_process(self, result: dict, target_lang: str = "en-US") -> dict:
        """After graph execution: persist new terms and update style memo.

        Enriches new terms with cultural notes from the READ agent's
        terminology_decisions so future chapters inherit not just the
        translation but the cultural reasoning behind it.

        【中文·收尾工序：一章翻完后的"沉淀"与"把关"】

        流水线跑完只是拿到了英文稿。这个方法负责把这一章的收获**存下来**，
        让后面的章节能受益，同时做几道不需要 AI 参与的规则检查（不花钱）。

        依次做 7 件事：
          ① 文化忠实度检查 —— WRITE 有没有把有含义的名字偷懒音译成拼音
          ② 强制接受审计日志 —— 修了 2 轮还是不合格，记下来备查
          ③ 存 READ 的分析进经验本 —— **每一章都存**
          ④ 存冷读者的反馈进经验本 —— 只有抽检章节才有
          ⑤ 存 FIX 的修补经验进经验本 —— 闭环关键（下面详解）
          ⑥ 术语入库前的三道关卡 —— 忠实度关、有效性关、分类入库
          ⑦ 生成术语快照 + 更新前情提要

        【中文·为什么"存经验"这么重要】
        这是全书 2301 章能保持质量稳定的根本原因。
        每章学到的东西都写进经验本和术语库，翻到后面的章节时全都在手上。
        不存的话，每章都从零开始，译名会前后不一、文风会越翻越飘。
        """
        # 【中文·从白板上把各角色的产出取出来】
        new_terms = result.get("new_terms_found", [])        # READ 发现的新术语
        read_analysis = result.get("read_analysis", {})      # READ 的完整分析
        readback_feedback = result.get("readback_feedback", {})  # 冷读者的评审
        chapter_number = result.get("chapter_number", 0)
        retries = result.get("retranslation_count", 0)       # 已经重修几轮

        # ── Cultural fidelity check (rule-based, no LLM) ──
        # READBACK is blind to the source, so it cannot catch a meaningful
        # name being flattened to pinyin. Verify WRITE honored READ's
        # terminology decisions; record failures for diagnosis.
        #
        # 【中文·① 文化忠实度检查（纯规则，不花钱）】
        # 这道检查存在的理由很关键：冷读者**看不到中文原文**（它必须盲读），
        # 所以它压根没能力发现"原文里有个有含义的名字，被 WRITE 偷懒音译成拼音了"。
        # 比如"忘忧谷"翻成 Wangyou Valley（拼音），冷读者读着毫无违和，
        # 但中文读者一看就知道丢了"忘记忧愁"这层意思。
        #
        # 所以要在这里用规则补一道：核对 WRITE 是否遵守了 READ 定下的译法。
        # 发现问题不拦着放行，而是记警告日志 + 追加到质量问题清单里，便于事后诊断。
        fidelity_failures = check_cultural_fidelity(
            result.get("read_analysis", {}),
            result.get("translated_text", ""),
        )
        if fidelity_failures:
            for failure in fidelity_failures:
                logger.warning("FIDELITY ch%d: %s", chapter_number, failure)
            # 【中文】把新发现的问题合并进已有清单。
            # 注意用 list(...) 包一层：不复制一份的话，两边会指向同一个列表对象，
            # 后面再改就会互相影响，出现难以追查的串改问题。
            result["quality_issues"] = (
                list(result.get("quality_issues", [])) + fidelity_failures
            )

        # ── Audit log: FORCED_ACCEPT when cold reader still says NEEDS_FIX ──
        # after max retries.  Without this log, we cannot diagnose whether
        # the chapter was too hard, the cold reader was too strict, or the
        # FIX agent is ineffective.
        #
        # 【中文·② 强制接受（FORCED_ACCEPT）审计日志】
        # 场景：冷读者说"不合格"，FIX 已经修满 2 轮，按规则必须放行了。
        # 这时会记一条日志，写明：修了几轮、冷读者提了几条理解障碍、
        # 几条阅读趣味问题、FIX 做了哪些改动。
        #
        # 【中文·为什么非记不可】没有这条日志，事后根本无法判断问题出在哪：
        #   是这章本身太难？  还是冷读者过于挑剔？  还是 FIX 根本不会修？
        # 三种原因的应对方式完全不同（改提示词 / 调宽判定 / 换模型）。
        # 放行是刻意的取舍——不能因为一章不合格就卡死整本书，
        # 但必须留下痕迹，否则问题会被静默吞掉。
        if readback_feedback:
            verdict = readback_feedback.get("verdict", "PASS") if readback_feedback else "PASS"
            if verdict == "NEEDS_FIX" and retries >= MAX_RETRANSLATION_ATTEMPTS:
                logger.warning(
                    "FORCED_ACCEPT ch%d: verdict=NEEDS_FIX after %d retries. "
                    "Cold reader issues: comprehension=%d, engagement=%d. "
                    "FIX changes: %s",
                    chapter_number, retries,
                    len(readback_feedback.get("comprehension_issues", [])),
                    len(readback_feedback.get("engagement_gaps", [])),
                    result.get("adaptation_notes", [])[:3],
                )

        # ── Update style memo from READ analysis (EVERY chapter) ──
        # 【中文·③ 把 READ 的分析存进经验本——注意是"每一章都存"】
        # 这是个刻意的设计决定。早先只在抽检章节（有冷读反馈时）才更新经验本，
        # 结果经验积累得太慢。改成每章都存 READ 的分析和术语决策后，
        # 翻到第 200 章时，WRITE 手上已经有前 199 章的全部经验。
        # READ 的分析不花钱额外调用（它本来就跑），所以这一步几乎零成本。
        if read_analysis:
            self.style_memo.update_from_read_analysis(read_analysis, chapter_number)

        # ── Supplement with cold-reader feedback (sample chapters only) ──
        # 【中文·④ 用冷读者的反馈补充经验本——只有抽检章节才有】
        # 快速模式的章节跳过了冷读，所以这里没有反馈可存，条件自然不成立。
        if readback_feedback:
            self.style_memo.update_from_feedback(
                readback_feedback, read_analysis, chapter_number,
            )

        # ── Bank FIX's repairs (closes the knowledge loop) ──
        # update_from_feedback records what the cold reader *complained about*;
        # this records the *fix that worked*. Without it the memo accumulates
        # problems with no solutions, and every FIX call spends money to
        # rediscover the same repair. bridges.md is defined as "cultural bridge
        # patterns that worked (and didn't)" — that is exactly this data.
        # Bounded per chapter: record_lesson dedups on a 50-char fingerprint, but
        # dedup only stops repeats, not volume, and bridges.md is already the
        # largest drawer on real books (31,818 lines on difu_xiao_xiansheng).
        #
        # 【中文·⑤ 把 FIX 的"修补经验"存起来——这是闭环的关键一环】
        # 上一步（④）存的是"冷读者抱怨了什么"，这一步存的是"用什么办法修好的"。
        #
        # 【中文·只存问题不存解法会怎样】经验本会越攒越多"某某处有问题"，
        # 却没有一条"遇到这类问题该怎么改"。结果每次 FIX 都要花钱
        # 重新摸索同一个修复办法——同一个坑反复交学费。
        #
        # 存进 bridges.md（"文化桥接套路"抽屉），这个抽屉的定义本来就是
        # "哪些文化改编手法有效、哪些无效"，正好对得上。
        #
        # 【中文·为什么这里要截断（见文件开头 _MAX_FIX_LESSONS_PER_CHAPTER）】
        # 存经验的方法只会去重、不会限量，而这个抽屉在真实书上已经 31818 行。
        # 经验本每次都要塞进提示词，它一膨胀就意味着**每一章都变贵变慢**。
        # 所以每章最多存 5 条，[:5] 就是"取前 5 个"的意思。
        fix_lessons = result.get("fix_lessons", [])
        if fix_lessons:
            for lesson in fix_lessons[:_MAX_FIX_LESSONS_PER_CHAPTER]:
                self.style_memo.record_lesson(
                    # 存进 bridges 抽屉，加个 "FIX that worked:" 前缀标明来源，
                    # [:200] 是每条最多留 200 字，防止某条特别长把经验本撑爆
                    "bridges", f"FIX that worked: {lesson[:200]}", chapter_number,
                )
            logger.info(
                "FIX LESSONS ch%d: banked %d of %d repair(s) into bridges.md",
                chapter_number, min(len(fix_lessons), _MAX_FIX_LESSONS_PER_CHAPTER),
                len(fix_lessons),
            )

        # ── Merge READ's cultural notes into new terms ──────────
        # 【中文·⑥-a 给新术语补上"文化说明"】
        # READ 在定术语时往往附了文化背景（这个词有什么典故、为什么这么译）。
        # 这里把那份说明"贴"到术语条目上，一起存进术语库。
        #
        # 【中文·为什么值得这么做】后续章节再遇到这个词时，
        # 拿到的不只是"金丹 → Golden Core"这行对照，还带着当初的 reasoning。
        # WRITE 就能理解为什么这么译，遇到变体（"金丹期""结丹"）也能处理得当，
        # 而不是机械套用。知识连同理由一起传下去，才不会用歪。
        if new_terms and read_analysis:
            # 先把 READ 的文化说明按"中文原词"建成一张速查表
            term_notes = {}
            for td in read_analysis.get("terminology_decisions", []):
                note = td.get("cultural_note", "")
                if note:
                    term_notes[td.get("term_cn", "")] = note
            # 再逐条术语去表里找，找到且该术语还没有说明，就补上
            for t in new_terms:
                cn = t.get("term_cn", "")
                # not t.get("note") 这个条件保证不覆盖已有说明
                if cn in term_notes and not t.get("note"):
                    t["note"] = term_notes[cn]

        # ── Fidelity gate: prefer READ's rendering over WRITE's drift ──
        # READ is the decision point (now guided by the fidelity rules).
        # If WRITE recorded a different rendering (e.g. flattened a pun to
        # pinyin), cache READ's decision instead — a wrong rendering persisted
        # here would propagate to every subsequent chapter.
        #
        # 【中文·⑥-b 忠实度关卡：READ 的译法优先于 WRITE 的漂移】
        # 分工上，**定译名是 READ 的职责**（它有原文、有文化规则、有经验本），
        # WRITE 只负责按定好的译法写。但 WRITE 有时会"自作主张"改动译法，
        # 典型就是把双关语或有含义的名字简化成拼音。
        #
        # 这道关卡就是：发现两边不一致时，采用 READ 的版本。
        #
        # 【中文·为什么必须在入库前拦下】一旦错误译法存进术语库，
        # 它就成了"必须遵守"的铁律，会**传染给后面所有章节**——
        # 第 5 章的一个拼音错误，到第 2000 章还在，而且极难追溯源头。
        # 所以在"存进去"这一刻把关，成本最低、效果最好。
        if new_terms and read_analysis:
            # 建一张"READ 定的译法"速查表
            read_en_by_cn = {}
            for td in read_analysis.get("terminology_decisions", []):
                cn = td.get("term_cn", "")
                en = td.get("proposed_en", "")
                # len(en) <= 50 是防呆：超过 50 字符的"译名"多半是模型跑偏，
                # 吐出了一整句解释而不是一个名字，不能当术语存
                if cn and en and len(en) <= 50:
                    read_en_by_cn[cn] = en
            for t in new_terms:
                cn = t.get("term_cn", "")
                read_en = read_en_by_cn.get(cn, "")
                # is_single_rendering 确认 READ 给的是"单一确定译法"。
                # 如果 READ 自己给了"A 或 B"这种多选，那就没有权威答案可依据，
                # 这时不强行替换，保留 WRITE 的版本。
                if (
                    read_en
                    and read_en != t.get("term_en", "")
                    and is_single_rendering(read_en)
                ):
                    # 记警告日志：留下"这里发生过替换"的痕迹，便于排查
                    logger.warning(
                        "FIDELITY GATE ch%d: '%s' WRITE='%s' READ='%s' — using READ",
                        chapter_number, cn, t.get("term_en", ""), read_en,
                    )
                    t["term_en"] = read_en   # 用 READ 的译法覆盖

        # ── Hard curation gate: drop obviously-invalid renderings ──
        # A rendering that is empty, contains Chinese, tone-marked pinyin, a
        # stray digit, or is a gloss rather than a token must not be cached —
        # it would be locked in as "must use" for the whole book.
        #
        # 【中文·⑥-c 硬性筛选关卡：明显不合格的译法直接丢弃】
        # 上一条是"两个版本选哪个"，这一条是"这玩意儿到底能不能当术语"。
        # 会被丢弃的典型情况：
        #   · 空的（模型没给出译法）
        #   · 里面还有中文（等于没翻）
        #   · 带声调符号的拼音（如 Jīndān，正式出版物不用这种）
        #   · 混进了数字（明显是解析错乱）
        #   · 是一句解释而不是一个词（"a person who cultivates immortality"）
        #
        # 【中文·为什么宁可直接丢弃】理由和上面一样：术语库里的东西
        # 会变成全书强制遵守的铁律。存一条垃圾进去，代价是污染后面所有章节；
        # 丢掉它，最多是这一章少了个术语，下一章遇到还能重新定。
        # 两害相权，丢弃安全得多。
        if new_terms:
            kept = []
            for t in new_terms:
                # 这个函数返回两个值：(是否合格, 不合格的原因)
                ok, reason = is_valid_rendering(t.get("term_cn", ""), t.get("term_en", ""))
                if ok:
                    kept.append(t)
                else:
                    # 丢弃时一定记日志并写明原因，否则术语"凭空消失"很难查
                    logger.warning(
                        "CURATION GATE ch%d: dropping '%s' → '%s' (%s)",
                        chapter_number, t.get("term_cn", ""), t.get("term_en", ""), reason,
                    )
            new_terms = kept
            # Mirror the curated list back into the result: the dropped terms
            # must not be reported as discovered glossary terms either, or a
            # caller (API response, split merge, on-disk artifact) would
            # re-introduce exactly what this gate rejected.
            #
            # 【中文·这一步很容易漏，必须把筛选结果同步回 result】
            # 上面只是改了局部变量 new_terms，result 字典里那份还是旧的（含垃圾）。
            # 如果不同步回去，调用方从 result 里读到的仍是**被本关卡否决的术语**，
            # 于是垃圾又被塞回 API 返回、分段合并结果、或落盘的成品文件里——
            # 关卡等于白设。所以必须显式写回。
            result["new_terms_found"] = kept

        if new_terms:
            # Write character/location terms to exact store
            # 【中文·⑥-d 分类入库：两层术语库各存各的】
            # 精确库只收"人名、地名"这两类。为什么挑这两类：
            # 它们字面固定、必须全书一字不差地统一（主角名字不能一章一个样），
            # 用精确匹配最合适，而且查询快（数据库索引，瞬间命中）。
            exact_terms = [
                t for t in new_terms
                if t.get("category") in ("character", "location")
            ]
            if exact_terms:
                self.exact_store.add_batch(
                    exact_terms, chapter=result.get("chapter_number", 0),
                    target_lang=target_lang,
                )

            # Write ALL terms to semantic store
            # 【中文】语义库则收**全部**术语（含文化词、功法名、物品名等）。
            # 因为它靠"意思相近"检索，能覆盖精确匹配捞不到的情况
            # （本章写"御剑飞行"，能匹配到库里的"飞剑"）。
            # 两层配合：精确库保证核心名称绝对统一，语义库保证相关概念不遗漏。
            self.semantic_store.add_batch(new_terms, target_lang=target_lang)

        # Snapshot exact glossary for checkpoint. Scoped to this chapter's
        # language: `snapshot()` with no argument reads the *active* language,
        # and a Spanish job whose store still had en-US active would checkpoint
        # an empty glossary — losing every term it had accumulated.
        #
        # 【中文·⑦-a 生成术语快照，供断点续翻用】
        # 快照就是把当前术语库整个导出成一坨 JSON 文本，随进度一起存档。
        # 中途崩溃后重启，靠它把术语恢复回来，前面几百章的成果不丢。
        #
        # 【中文·这里又踩过"语言"的坑】不带参数调用 snapshot() 读的是
        # "当前活跃格"。如果一个西班牙语任务，库的活跃格还停在 en-US，
        # 存下来的就是一份**空术语表**——所有积累的术语全部丢失，
        # 而且续翻时不会报错，只是术语莫名其妙全乱了。
        # 所以必须显式传 target_lang。
        result["glossary_snapshot_json"] = self.exact_store.snapshot(
            target_lang=target_lang
        )

        # ── Feed the cold reader's context buffer ──
        # 【中文·⑦-b 更新前情提要】把本章摘要存进缓冲区，
        # 下一章的冷读者就能看到"前面发生了什么"，不会因缺前文而误判。
        chapter_summary = result.get("chapter_summary", "")
        chapter_number = result.get("chapter_number", 0)
        self._update_context(chapter_number, chapter_summary)

        return result

    @staticmethod
    def _format_semantic(terms: list[dict], target_lang: str = "en-US") -> str:
        """Format semantic results as a markdown table.

        The target column is labelled from ``target_lang``; it used to be
        hard-coded "English" (see exact_store.to_formatted_text for the same
        defect on the exact-glossary path).

        【中文·把语义术语拼成一张 Markdown 表格，塞进提示词给 AI 看】

        为什么用表格：模型对表格结构的理解比一长串文字稳定得多，
        "哪个是中文、哪个是译名、属于哪类"一目了然，不容易对错行。

        【中文·这里修过一个"标签写死"的 bug】
        表头第二列早先硬编码成 "English"。在只支持英语时没问题，
        但项目后来支持了西班牙语/德语/法语，于是翻西班牙语的书时，
        表格却告诉模型"这一列是英语"——模型可能因此输出英语而不是西班牙语。
        现在改成按 target_lang 动态取显示名（见 config.target_language_label）。

        【中文·@staticmethod 是什么】表示这个方法不需要访问对象自身的数据
        （没有 self 参数），纯粹是"给一批数据、返回一个字符串"的工具函数。
        放在类里只是为了归类方便。
        """
        if not terms:
            # 一条都没有时返回一句明确的说明，而不是空字符串。
            # 空字符串会让提示词里出现一个光秃秃的表头，模型容易困惑。
            return "(No semantic matches.)"
        target = target_language_label(target_lang)
        lines = [f"| Chinese | {target} | Category |",
                 "|----------|---------|----------|"]   # 第二行是 Markdown 表格的分隔线
        for t in terms:
            lines.append(
                f"| {t['term_cn']} | {t['term_en']} | "
                f"{t.get('category', 'culture')} |"
            )
        # 【中文】把各行用换行符连成一整段文本
        return "\n".join(lines)

