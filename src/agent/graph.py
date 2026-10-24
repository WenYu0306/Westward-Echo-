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
from ..config import MAX_RETRANSLATION_ATTEMPTS

# 术语库两层存储：
#   ExactGlossary   —— 精确匹配（中文原词一模一样才命中），存在 SQLite 数据库里
#   SemanticGlossary—— 语义相近匹配（意思相关的也算），存在向量库里
from ..glossary.exact_store import ExactGlossary
from ..glossary.semantic_store import SemanticGlossary

# 文化忠实度检查：确认 WRITE 没把有含义的名字简单音译成拼音
# （已随 _post_process 一起抽到 post_process.py）
# 流水线的"共享白板"数据结构：各角色往上读写同一份状态
from .cold_read import build_cold_read_context

# 4 个角色的具体实现（+ 1 个术语复核角色）
from .nodes.fix import fix_node
from .nodes.read import read_node
from .nodes.readback import readback_node
from .nodes.review_terms import review_terms_node
from .nodes.write import write_node
from .post_process import post_process_chapter
from .state import TranslatorState
from .state_builder import build_state, format_semantic

logger = logging.getLogger(__name__)

# Max FIX → READBACK loops per chapter
# 【中文】一章最多允许「FIX 修补 → READBACK 复检」来回循环几次。
# 超过次数就**带着现有问题放行**（业内叫"强制接受"），并记一条警告日志。
# 为什么要有上限：不设限的话，遇到 AI 反复挑剔的章节会无限循环，
# 一章就能烧掉几十次调用的钱，而且卡住整本书的进度。
# 实践上"修 2 次还不合格"说明问题不在文字层面，硬修也没用。
_MAX_FIX_ATTEMPTS = MAX_RETRANSLATION_ATTEMPTS

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
        """Build a one-page briefing for the cold reader — delegates to cold_read.

        【中文·给冷读者写"读者须知"】逻辑已抽到 cold_read.build_cold_read_context，
        这里只把 agent 的缓冲区、术语库、经验本传进去。
        """
        return build_cold_read_context(
            self._chapter_context, self.exact_store, self.style_memo,
            chapter_number=chapter_number, target_lang=target_lang,
        )

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
                content_type=content_type,
            )
        else:
            result = self._translate_once(
                chapter_title, chapter_content, chapter_number,
                previous_summary, target_lang, genre, skip_readback,
                content_type=content_type,
            )

        return result

    def _make_state(
        self, title, content, number, prev_summary, lang, genre,
        skip_readback=False, content_type="novel",
    ) -> TranslatorState:
        """Build the initial state — delegates to state_builder.build_state.

        【中文·填写"共享白板"】拼装逻辑已抽到 state_builder.build_state，
        这里只负责把 agent 自己的字段（术语库、经验本、prefetch 缓存）取出来
        传进去。prefetch 的「读后清空」留在这里。
        """
        prefetched_exact = self._prefetched_exact
        prefetched_semantic = self._prefetched_semantic
        self._prefetched_exact = None
        self._prefetched_semantic = None
        return build_state(
            title=title, content=content, number=number, prev_summary=prev_summary,
            lang=lang, genre=genre, skip_readback=skip_readback,
            content_type=content_type,
            exact_store=self.exact_store, semantic_store=self.semantic_store,
            style_memo=self.style_memo, api_key=self.api_key,
            book_id=getattr(self, "book_id", ""),
            prefetched_exact=prefetched_exact, prefetched_semantic=prefetched_semantic,
            cold_read_context=self._build_cold_read_context(
                chapter_number=number, target_lang=lang
            ),
        )

    def _translate_once(
        self, title, content, number, prev_summary, lang, genre,
        skip_readback=False, content_type="novel",
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
                                 skip_readback, content_type)
        result = self.graph.invoke(state, config={"recursion_limit": 100})
        return self._post_process(result, lang)

    def _translate_split(
        self, title, content, number, prev_summary, lang, genre,
        skip_readback=False, content_type="novel",
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
                skip_readback, content_type=content_type,
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

        【中文·收尾工序】逻辑已抽到 post_process.post_process_chapter，
        这里只负责把 agent 自己的字段（经验本、两个术语库、前情回调）传进去。
        """
        return post_process_chapter(
            result, target_lang,
            style_memo=self.style_memo, exact_store=self.exact_store,
            semantic_store=self.semantic_store, record_context=self._update_context,
        )

    @staticmethod
    def _format_semantic(terms: list[dict], target_lang: str = "en-US") -> str:
        """Format semantic results as a markdown table (delegates to state_builder).

        【中文·把语义术语拼成 Markdown 表格】逻辑已抽到 state_builder.format_semantic，
        这里保留为薄委托，供既有调用方（如测试）继续使用。
        """
        return format_semantic(terms, target_lang)

