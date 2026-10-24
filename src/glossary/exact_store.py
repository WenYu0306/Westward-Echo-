"""Exact-match glossary layer.

O(1) lookup via Python dict, persisted to SQLite for crash recovery.
This layer guarantees that character names, place names, and proper nouns
are translated identically across all chapters — vector search alone cannot
provide this guarantee.

【中文·精确术语库层】
一、这个文件是干什么的？
    术语库的第一层——"精确匹配层"。
    像查字典一样，中文原词一模一样才命中，一个字都不能差。
    保证人名、地名等专有名词在全书所有章节中译法完全一致。

二、为什么需要这一层？
    向量库（语义匹配层）靠"意思相近"检索，可能把"苏沐橙"和"苏沐秋"
    搞混。只有精确匹配才能保证：同一个中文词永远对应同一个译文。

三、核心概念：
    · SQLite 数据库：一个单文件的小型数据库，所有数据都存在一个 .db 文件里，
      断电重启也不丢。这里用来持久化存储术语。
    · 内存字典：Python 的 dict，查找速度 O(1)（不管多少条都是瞬间返回）。
      翻译时从内存查，不用每次都读磁盘。
    · 按语言分格存放：每种目标语言有自己的"格子"（bucket），
      英语术语和西班牙语术语互不干扰。
      ⚠️ 历史上曾是一个扁平字典，导致西班牙语术语被存进英语格，
      全部匹配不上。现在已修复。

四、它在整个流程里的位置：
    · graph.py 的 _make_state() 调用 match_in_text() 扫描章节中的已知术语
    · graph.py 的 _post_process() 调用 add() 把新术语入库
    · prefetch.py 的后台线程也调用 match_in_text() 做预取
    · to_formatted_text_with_notes() 生成带文化备注的术语表注入 AI 提示词

五、本文件的类 ExactGlossary 主要方法导览：
    · 语言管理：set_active_lang / loaded_languages
    · 增删查改：add / add_batch / get / match_in_text / reject_term
    · 格式化输出：to_formatted_text / to_formatted_text_with_notes
    · 存档恢复：snapshot / restore_snapshot / load_from_db
    · 审核接口：list_namespaces / get_all_terms / confirm_term / reject_term
"""

import json
import sqlite3
import typing

from ..config import CHECKPOINT_DB_PATH, target_language_label
from ..migrations import migrate


def _rename_legacy_exact_glossary(conn) -> None:
    """Rename the pre-``book_id`` table out of the way (one-time data migration).

    【中文】如果存在没有 book_id 列的旧 exact_glossary 表（book_id 隔离
    之前的旧数据），把它重命名为 exact_glossary_legacy，腾出名字给新表。
    只跑一次（记入 schema_migrations），新表随后用复合主键重建。
    """
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='exact_glossary'"
    ).fetchone()
    if existing:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(exact_glossary)").fetchall()]
        if "book_id" not in cols:
            conn.execute("ALTER TABLE exact_glossary RENAME TO exact_glossary_legacy")


_CREATE_EXACT_GLOSSARY = """
CREATE TABLE IF NOT EXISTS exact_glossary (
    book_id TEXT NOT NULL DEFAULT 'default',
    term_cn TEXT NOT NULL,
    term_en TEXT NOT NULL,
    category TEXT DEFAULT 'culture',
    context TEXT DEFAULT '',
    chapter_first_seen INTEGER DEFAULT 0,
    note TEXT DEFAULT '',
    status TEXT DEFAULT 'pending_review',
    target_lang TEXT DEFAULT 'en-US',
    PRIMARY KEY (book_id, term_cn, target_lang)
)
"""

# 【中文】命名迁移清单。迁移 1 把旧表改名（幂等：没有旧表则什么都不做），
# 迁移 2 建新表。两者都记入 checkpoints.db 的 schema_migrations 表，
# 与 celery_app 的 translation_checkpoint 迁移互不冲突（各自独立命名）。
_MIGRATIONS = [
    ("exact_glossary_rename_legacy", _rename_legacy_exact_glossary),
    ("create_exact_glossary", lambda conn: conn.executescript(_CREATE_EXACT_GLOSSARY)),
]


class ExactGlossary:
    """
    Order-1 exact-match glossary.

    - In-memory dict for O(1) lookup during translation
    - SQLite-backed persistence so crash recovery keeps the glossary intact
    - String-contains matching: if a term_cn appears anywhere in the source
      text, it is injected into the LLM prompt as a mandatory constraint.

    【中文】精确匹配术语库类。
    · 内存中用字典做 O(1) 快速查找
    · SQLite 做持久化，崩溃重启后能恢复
    · 字符串包含匹配：只要中文原词出现在原文任何位置，
      就会被注入 AI 提示词作为强制约束

    对外提供的主要能力：
    · add() / add_batch()              —— 添加术语
    · match_in_text()                  —— 扫描文本中的已知术语
    · to_formatted_text()              —— 生成 Markdown 表格（简版）
    · to_formatted_text_with_notes()   —— 生成带文化备注的表格（详版）
    · snapshot() / restore_snapshot()  —— JSON 序列化/反序列化
    · confirm_term() / reject_term()   —— 人工审核操作
    """

    def __init__(self, db_path: typing.Optional[str] = None, book_id: str = "default",
                 target_lang: str = "en-US"):
        # Terms are bucketed per target language: {target_lang: {term_cn: term_en}}.
        #
        # This used to be a single flat ``_dict``, which could only ever hold one
        # language at a time even though the SQLite primary key has carried
        # ``target_lang`` since the composite-key migration. Every reader then had
        # to guess which language the instance happened to be holding, and the
        # review API could only cope by keeping one store instance per
        # ``(book_id, target_lang)`` — a workaround, not a fix.
        #
        # 【中文】术语按目标语言分格存放：{语言代码: {中文词: 译文}}。
        # ⚠️ 历史踩坑：以前是一个扁平字典 _dict，同一时刻只能装一种语言。
        # 虽然 SQLite 主键早就带了 target_lang，但内存结构没跟上，
        # 导致"西班牙语术语被存进英语格"的事故。
        # 现在改成嵌套字典，每种语言有自己的格子，互不干扰。
        self._by_lang: dict[str, dict[str, str]] = {}
        self._active_lang = target_lang
        self._db_path = db_path or CHECKPOINT_DB_PATH
        self._book_id = book_id
        self._init_db()

    # ------------------------------------------------------------------
    # Language scoping
    # 【中文】语言作用域管理
    # ------------------------------------------------------------------

    @property
    def active_lang(self) -> str:
        """The language used by calls that do not name one explicitly.

        【中文】当前活跃语言。不指定 target_lang 的方法调用都用这个。
        """
        return self._active_lang

    def set_active_lang(self, target_lang: str):
        """Point the instance at another already-loaded language.

        Switching languages must not require a reload or a second instance: that
        is what forced the review API to cache one store per namespace.

        【中文】切换活跃语言。
        · 参数 target_lang：要切换到的语言代码
        · 不需要重新加载或创建新实例，直接切就行
        """
        self._active_lang = target_lang

    def loaded_languages(self) -> list[str]:
        """Languages currently held in memory, sorted for deterministic output.

        【中文】返回当前内存中已加载的所有语言代码，排序后返回。
        """
        return sorted(self._by_lang)

    def _bucket(self, target_lang: typing.Optional[str] = None) -> dict[str, str]:
        """Read-only view of one language's terms.

        Deliberately does *not* create the bucket: a read against an unloaded
        language must return empty rather than silently materialise a namespace
        that then shows up in ``loaded_languages()`` and ``snapshot()``.

        【中文】获取某种语言的术语字典（只读视图）。
        · 如果该语言还没加载过，返回空字典 {}
        · ⚠️ 故意不自动创建格子：读取一个未加载的语言应该返回空，
          而不是默默创建一个空格子，否则 loaded_languages() 和 snapshot()
          会多出从未真正使用过的语言。
        """
        return self._by_lang.get(target_lang or self._active_lang, {})

    def _writable_bucket(self, target_lang: typing.Optional[str] = None) -> dict[str, str]:
        """【中文】获取某种语言的可写字典。
        与 _bucket 的区别：如果格子不存在会自动创建（setdefault）。
        只在需要写入时用这个方法。
        """
        lang = target_lang or self._active_lang
        return self._by_lang.setdefault(lang, {})

    # ------------------------------------------------------------------
    # SQLite persistence
    # 【中文】SQLite 持久化层
    # ------------------------------------------------------------------

    def _init_db(self):
        """【中文】初始化数据库：建表 + 设置 WAL 模式 + 旧表迁移。
        · WAL 模式允许多个进程同时读写同一个数据库文件
          （Celery worker 和 API 服务可能同时访问）
        · 如果检测到旧版表结构（没有 book_id 列），自动重命名为 legacy
        · 新表用 (book_id, term_cn, target_lang) 三元组做主键
        """
        # 【中文】初始化数据库：建表 + 设置 WAL 模式 + 旧表迁移，全部交给
        # 迁移框架（migrate() 内部处理 WAL、目录创建、并发安全、迁移记录）。
        migrate(self._db_path, _MIGRATIONS)

    def load_from_db(self, target_lang: typing.Optional[str] = None):
        """Restore one language's terms from SQLite into memory.

        Loading a language makes it active, so callers that follow the historical
        ``load_from_db()`` → ``match_in_text()`` pattern keep working unchanged.
        Terms already held for *other* languages are left alone: this is a
        per-namespace load, not a reset of the whole instance.

        【中文】从 SQLite 加载某种语言的术语到内存。
        · 参数 target_lang：要加载的语言，默认用当前活跃语言
        · 加载后自动设为活跃语言
        · 其他语言的术语不受影响（增量加载，不是全量重置）
        · 整格替换：如果某个术语在数据库里被删了，内存里也会消失
        """
        lang = target_lang or self._active_lang
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT term_cn, term_en FROM exact_glossary "
                "WHERE book_id = ? AND target_lang = ?",
                (self._book_id, lang),
            ).fetchall()
        # Replace this language's bucket wholesale (a term deleted from the DB
        # must not survive in memory), but keep the other languages' buckets.
        self._by_lang[lang] = {row[0]: row[1] for row in rows}
        self._active_lang = lang

    def _persist_term(self, term_cn: str, term_en: str, category: str = "culture",
                      context: str = "", chapter: int = 0, note: str = "",
                      status: str = "pending_review", target_lang: str = "en-US"):
        """【中文】把一个术语写入 SQLite 数据库（内部方法）。
        · INSERT OR REPLACE：如果同 (book_id, term_cn, target_lang) 的记录
          已存在就覆盖，不存在就插入
        · 每次写入后立即 commit，确保崩溃不丢数据
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO exact_glossary
                   (book_id, term_cn, term_en, category, context, chapter_first_seen, note, status, \
target_lang)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self._book_id, term_cn, term_en, category, context, chapter, note, status,
                 target_lang),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # 【中文】公开接口
    # ------------------------------------------------------------------

    def add(self, term_cn: str, term_en: str, category: str = "culture",
            context: str = "", chapter: int = 0, note: str = "",
            target_lang: typing.Optional[str] = None):
        """Add a term to the exact layer (memory + SQLite).

        ``target_lang=None`` means the active language. The term lands in that
        language's bucket *and* in the DB row for the same language, so memory
        and persistence cannot disagree about which namespace was written — the
        disagreement that made ``confirm_term`` return success while updating
        zero rows.

        【中文】添加一个术语到精确库（同时写内存和数据库）。
        · 参数 term_cn：中文原词
        · 参数 term_en：目标语言译文
        · 参数 category：分类标签，默认 "culture"
        · 参数 context：上下文说明
        · 参数 chapter：首次出现的章节号
        · 参数 note：文化备注（供 to_formatted_text_with_notes 使用）
        · 参数 target_lang：目标语言，None 表示用当前活跃语言
        · ⚠️ 内存和数据库必须写同一个语言格子！
          历史上因为不一致，confirm_term 报告成功但实际更新了 0 行。
        """
        lang = target_lang or self._active_lang
        self._writable_bucket(lang)[term_cn] = term_en
        self._persist_term(term_cn, term_en, category, context, chapter, note,
                           "pending_review", lang)

    def add_batch(self, terms: list[dict], chapter: int = 0,
                  target_lang: typing.Optional[str] = None):
        """Bulk-add terms extracted from a chapter.

        【中文】批量添加一章提取出的术语。
        · 参数 terms：术语字典列表，每个含 term_cn、term_en，
          可选 category、context、note
        · 参数 chapter：章节号
        · 参数 target_lang：目标语言
        内部逐条调用 add()。
        """
        for t in terms:
            self.add(
                term_cn=t["term_cn"],
                term_en=t["term_en"],
                category=t.get("category", "culture"),
                context=t.get("context", ""),
                chapter=chapter,
                note=t.get("note", ""),
                target_lang=target_lang,
            )

    def match_in_text(self, text: str,
                      target_lang: typing.Optional[str] = None) -> dict[str, str]:
        """
        Scan source text for all known terms via string-contains.
        Returns {term_cn: term_en} for every term found in the text.

        Scans one language's bucket, so a store holding both en-US and es-ES
        terms cannot leak Spanish renderings into an English prompt.

        Complexity: O(G * L) where G = glossary size, L = average term length.
        For a glossary of ~500 terms this takes < 1ms on typical hardware.

        【中文】扫描源文本，找出所有已知术语。
        · 参数 text：中文原文
        · 参数 target_lang：要扫描哪种语言的术语库
        · 返回：{中文词: 译文} 字典，只包含在文本中找到的术语
        · 原理：对术语库里每个中文词做 "cn in text" 子串检查
        · 只扫描一种语言的格子，不会把西班牙语术语泄漏到英语提示词里
        · 复杂度 O(G×L)，500 条术语在普通硬件上 < 1ms
        """
        matched = {}
        for cn, en in self._bucket(target_lang).items():
            if cn in text:
                matched[cn] = en
        return matched

    def get(self, term_cn: str, target_lang: typing.Optional[str] = None
            ) -> typing.Optional[str]:
        """【中文】查询单个术语的译文。
        · 参数 term_cn：中文原词
        · 返回：译文字符串，找不到返回 None
        """
        return self._bucket(target_lang).get(term_cn)

    def get_term_info(self, term_cn: str, target_lang: typing.Optional[str] = None
                    ) -> typing.Optional[dict]:
        """Return full metadata for a term (category, context, chapter, status, etc.).

        【中文】查询术语的完整元数据。
        · 返回字典含：term_cn、term_en、category、context、
          chapter_first_seen、note、status、target_lang
        · 直接从 SQLite 查（不走内存），所以能拿到所有字段
        · 找不到返回 None
        """
        lang = target_lang or self._active_lang
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT term_cn, term_en, category, context, chapter_first_seen,
                          note, status, target_lang
                   FROM exact_glossary
                   WHERE book_id = ? AND term_cn = ? AND target_lang = ?""",
                (self._book_id, term_cn, lang),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_status(self, term_cn: str, target_lang: typing.Optional[str] = None
                   ) -> typing.Optional[str]:
        """Return the status of a term ('confirmed', 'pending_review', or None if absent).

        【中文】查询术语的审核状态。
        · 返回："confirmed"（已确认）、"pending_review"（待审核）、
          或 None（术语不存在）
        """
        lang = target_lang or self._active_lang
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT status FROM exact_glossary "
                "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                (self._book_id, term_cn, lang),
            ).fetchone()
        if row is None:
            return None
        return row[0]  # type: ignore[no-any-return]

    def find_chapters_with_term(self, term_cn: str, target_lang: typing.Optional[str] = None
                                  ) -> list[int]:
        """
        Return the list of chapter numbers where this term appears.
        This is a simplified version — in production this would query a
        chapter-term mapping table. For now it returns the chapter_first_seen.

        Returns a list so the caller can format it for display.

        【中文】查找术语出现在哪些章节。
        · 目前是简化版：只返回 chapter_first_seen（首次出现章节）
        · 生产环境应该有独立的章节-术语映射表，但目前还没建
        · 返回列表格式方便调用方展示
        """
        lang = target_lang or self._active_lang
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT chapter_first_seen FROM exact_glossary "
                "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                (self._book_id, term_cn, lang),
            ).fetchone()
        if row is None:
            return []
        return [row[0]] if row[0] else []

    def to_dict(self, target_lang: typing.Optional[str] = None) -> dict[str, str]:
        """One language's ``{term_cn: term_en}`` map — the active one by default.

        Deliberately returns a single language: callers write this to
        ``*_glossary.json`` and to the state's ``exact_glossary``, both of which
        are per-job (one target language per job_id). Flattening every loaded
        language into one dict would silently mix Spanish and English renderings
        of the same Chinese term under a single key.

        【中文】返回某种语言的 {中文: 译文} 字典副本。
        · 默认返回当前活跃语言
        · 故意只返回一种语言：如果把所有语言压平到一个字典里，
          同一个中文词在不同语言下的译文会互相覆盖。
        """
        return dict(self._bucket(target_lang))

    def to_formatted_text(self, matched_terms: typing.Optional[dict[str, str]] = None,
                          target_lang: typing.Optional[str] = None) -> str:
        """
        Format glossary as a markdown table for prompt injection.
        If matched_terms is provided, only those terms are listed.

        ``None`` means "no filter — dump the whole glossary"; ``{}`` means "this
        chapter matched nothing". The two used to be conflated by
        ``matched_terms or self._dict``, so a chapter with zero matches
        received the entire glossary instead of the placeholder — measured at
        41,824 characters for a 2,000-term book, injected into every such
        chapter's WRITE prompt. The existing placeholder test did not catch it
        because it ran against an *empty* glossary.

        The target-language column is labelled from ``target_lang``: it used to
        be hard-coded "English", which told the model the Spanish column was
        English. The unfiltered branch reads the *same* ``target_lang`` bucket,
        so the label and the terms cannot come from two different languages.

        【中文】把术语格式化为 Markdown 表格，用于注入 AI 提示词（简版）。
        · 参数 matched_terms：本章匹配到的术语字典
          - None = 不过滤，输出整个术语库
          - {} = 本章没匹配到任何术语，返回占位提示
          ⚠️ 这两个曾经被 ``matched_terms or self._dict`` 搞混，
          导致零匹配的章节收到了整本术语库（2000 条 = 41824 字符），
          白白浪费 token。
        · 参数 target_lang：目标语言
        · 返回：Markdown 表格字符串
        · ⚠️ 表头语言标签从 target_lang 动态生成！
          曾经硬编码为 "English"，导致西班牙语列也被标成 "English"，
          AI 以为那是英语译文。
        · 术语按中文词长度降序排列：长的排前面，
          因为 AI 更容易忽略短词（如单字名）
        """
        lang = target_lang or self._active_lang
        terms = self._bucket(lang) if matched_terms is None else matched_terms
        if not terms:
            return "(No glossary terms matched for this chapter.)"

        target = target_language_label(lang)
        lines = [f"| Chinese | {target} |", "|----------|---------|"]
        for cn, en in sorted(terms.items(), key=lambda x: len(x[0]), reverse=True):
            # Sort by length descending so longer terms appear first in prompt
            # 【中文】按中文词长度降序排列，长词优先
            lines.append(f"| {cn} | {en} |")
        return "\n".join(lines)

    def to_formatted_text_with_notes(
        self, matched_terms: typing.Optional[dict[str, str]] = None,
        target_lang: typing.Optional[str] = None,
    ) -> str:
        """Format glossary as a contextualized list with cultural notes.

        Each entry includes the translation AND the accumulated cultural
        understanding from the chapter where it was first established.
        This gives future WRITE agents not just the WHAT but the WHY.

        If matched_terms is provided, only those terms are listed.

        Confusable pairs (terms sharing a Chinese prefix or an English first
        word, e.g. 苏沐橙/苏沐秋 or Wei Cao/Wei Chen) get an explicit
        "do not confuse" warning appended — the LLM otherwise misattributes
        one character's name to the other when both appear in a chapter.

        ``None`` vs ``{}`` is distinguished exactly as in ``to_formatted_text``:
        this is the path graph.py actually takes for a chapter with matches, but
        it is public API and must not carry the same whole-glossary dump on an
        empty match set.

        【中文】把术语格式化为带文化备注的 Markdown 表格（详版）。
        · 比 to_formatted_text 多一列 Context（文化备注）
        · 每条术语附带首次建立时的文化理解，让 WRITE 不仅知道"怎么翻"
          还知道"为什么这么翻"
        · 如果没有备注，显示 "(see prior chapters)"
        · 末尾追加易混淆术语对的警告（如 苏沐橙/苏沐秋），
          防止 AI 张冠李戴
        · None vs {} 的处理逻辑与 to_formatted_text 完全一致
        · graph.py 实际用的是这个方法（有匹配时）
        """
        lang = target_lang or self._active_lang
        terms = self._bucket(lang) if matched_terms is None else matched_terms
        if not terms:
            return "(No glossary terms matched for this chapter.)"

        # Fetch notes from the DB for each matched term
        # 【中文】从数据库查出每个匹配术语的文化备注
        notes_map: dict[str, str] = {}
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            for cn in terms:
                row = conn.execute(
                    "SELECT note FROM exact_glossary "
                    "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                    (self._book_id, cn, lang),
                ).fetchone()
                if row and row["note"]:
                    notes_map[cn] = row["note"]

        lines = [
            f"| Chinese | {target_language_label(lang)} | Context |",
            "|----------|---------|---------|",
        ]
        for cn, en in sorted(terms.items(), key=lambda x: len(x[0]), reverse=True):
            note = notes_map.get(cn, "")
            if note:
                lines.append(f"| {cn} | {en} | {note} |")
            else:
                lines.append(f"| {cn} | {en} | (see prior chapters) |")

        # ── Confusable-pair warnings ──
        # 【中文】检测并追加易混淆术语对的警告。
        confusable = self._detect_confusable_pairs(terms)
        if confusable:
            lines.append("")
            lines.append("**DO NOT CONFUSE — these are DIFFERENT entities.** "
                         "Match each name to the exact source character:")
            for cn1, en1, cn2, en2 in confusable:
                lines.append(f"- {cn1} = {en1}  ≠  {cn2} = {en2}")

        return "\n".join(lines)

    @staticmethod
    def _detect_confusable_pairs(terms: dict[str, str]) -> list[tuple[str, str, str, str]]:
        """Find term pairs the LLM is likely to confuse.

        Two heuristics, kept conservative:
          1. Shared 2-char Chinese prefix (苏沐橙/苏沐秋 → "苏沐",
             百花战队/百花缭乱 → "百花")
          2. Same English first word, excluding stopwords
             (微草战队 "Wei Cao" / 魏琛 "Wei Chen" → "Wei")

        Returns [(cn1, en1, cn2, en2), ...] sorted by Chinese.

        【中文】检测容易被 AI 搞混的术语对。
        两条启发式规则：
          ① 共享 2 字中文前缀（如 苏沐橙/苏沐秋 共享"苏沐"）
          ② 英文译名的第一个单词相同（排除 the/a/an 等停用词）
             如 "Wei Cao" 和 "Wei Chen" 都以 "Wei" 开头
        · 返回：[(中文1, 英文1, 中文2, 英文2), ...] 列表
        · 两两比较，O(n²) 复杂度，但术语数通常 < 100，可接受
        """
        _STOPWORDS = frozenset({"the", "a", "an"})
        items = sorted(terms.items())
        pairs: list[tuple[str, str, str, str]] = []
        for i in range(len(items)):
            cn1, en1 = items[i]
            for j in range(i + 1, len(items)):
                cn2, en2 = items[j]
                # Shared 2-char Chinese prefix. The slice comparison alone is
                # sufficient — no length guard is needed, and adding one creates
                # an unkillable mutant. Proof: keys are unique so cn1 != cn2; if
                # cn1[:2] == cn2[:2] then either both are >= 2 chars, or one is
                # shorter and a shorter slice can only equal a longer one when
                # both are the same string, which uniqueness forbids. Verified by
                # exhaustive enumeration: 79,092,650 term dicts over a 4-char
                # alphabet and 10,726,600 over a 4,433-word mixed CJK/ASCII
                # pool — zero behavioural differences with the guards removed.
                #
                # `len(cn2) >= 2 -> >= 3` survived mutation testing precisely
                # because the guard was unreachable-but-true: when len(cn2) == 2
                # and the prefix matches, cn1 must equal cn2, impossible for
                # distinct dict keys. A condition that cannot be false cannot be
                # killed, so it was removed rather than registered as equivalent.
                #
                # 【中文】检查中文前缀是否相同。
                # 不需要额外检查长度：字典的 key 互不相同，
                # 如果 cn1[:2] == cn2[:2]，要么两者都 ≥ 2 字符，
                # 要么其中一个是另一个的前缀——但 key 唯一性排除了后者。
                # 经 7900 万种组合穷举验证，加不加长度检查结果完全一致。
                if cn1[:2] == cn2[:2]:
                    pairs.append((cn1, en1, cn2, en2))
                    continue
                # Same English first word (excluding stopwords)
                # 【中文】检查英文首词是否相同（排除停用词）
                w1 = en1.split()[0].lower() if en1.split() else ""
                w2 = en2.split()[0].lower() if en2.split() else ""
                if w1 and w1 == w2 and w1 not in _STOPWORDS:
                    pairs.append((cn1, en1, cn2, en2))
        return pairs

    def snapshot(self, target_lang: typing.Optional[str] = None) -> str:
        """JSON snapshot for checkpoint persistence.

        Emits the *flat* ``{term_cn: term_en}`` shape for one language, not the
        nested per-language structure. Every existing ``_checkpoint.json`` in
        production holds that flat shape (the difu one has 1,467 entries), and
        checkpoints are written per job — one job is one target language — so
        nesting would break resume for every in-flight job while adding nothing.

        【中文】生成 JSON 快照，用于断点续翻（checkpoint）存档。
        · 输出扁平的 {中文: 译文} 格式，不是嵌套的多语言结构
        · 原因：生产环境中所有 checkpoint 文件都是扁平格式，
          且每个任务只对应一种语言，嵌套只会破坏已有存档的恢复
        · 返回：JSON 字符串
        """
        return json.dumps(self._bucket(target_lang), ensure_ascii=False)

    def restore_snapshot(self, snapshot: str,
                         target_lang: typing.Optional[str] = None):
        """Restore one language's terms from a JSON snapshot string.

        Accepts the flat shape produced by ``snapshot`` (including the historical
        checkpoints written before terms were bucketed per language) and loads it
        into the given language, or the active one. A nested
        ``{lang: {term: translation}}`` mapping is also accepted so a caller that
        did snapshot every language can restore all of them in one call.

        【中文】从 JSON 快照恢复术语。
        · 参数 snapshot：JSON 字符串
        · 参数 target_lang：恢复到哪种语言
        · 支持两种格式：
          - 扁平 {中文: 译文}：恢复到指定语言
          - 嵌套 {语言: {中文: 译文}}：一次恢复所有语言
        · 恢复后自动设为活跃语言
        · 非字符串值会被过滤掉（防御脏数据）
        """
        data = json.loads(snapshot)
        if not isinstance(data, dict):
            raise TypeError(f"glossary snapshot must be a JSON object, got {type(data).__name__}")
        lang = target_lang or self._active_lang
        # 判断是否为嵌套格式：所有值都是字典 → 嵌套
        nested = data and all(isinstance(v, dict) for v in data.values())
        if nested:
            # 嵌套格式：逐个语言恢复
            for loaded_lang, terms in data.items():
                self._by_lang[loaded_lang] = dict(terms)
            # Active lang is honoured as given. If it is not among the restored
            # languages, reads return empty rather than silently falling back to
            # some other language's terms.
            self._active_lang = lang
        else:
            # 扁平格式：恢复到指定语言，过滤非字符串值
            self._by_lang[lang] = {
                cn: en for cn, en in data.items() if isinstance(en, str)
            }
            self._active_lang = lang

    # ------------------------------------------------------------------
    # Review API — human-in-the-loop glossary curation
    # 【中文】人工审核接口——让人类参与术语质量管理
    # ------------------------------------------------------------------

    @classmethod
    def list_namespaces(cls, db_path: typing.Optional[str] = None) -> list[dict]:
        """List every ``(book_id, target_lang)`` namespace that has terms.

        Class method on purpose: an instance is already scoped to one book_id,
        and the review UI needs to see *all* of them to offer a choice. Without
        this the UI could only ever show whichever book_id its store was
        constructed with — measured as 28 of 131 rows (21.4%) on the real
        checkpoints.db, which is why its confirm/reject loop never touched a
        production book and `status='confirmed'` matched zero rows.

        Returns [{"book_id", "target_lang", "term_count"}] ordered by size, so
        the most-used namespace is the default choice.

        【中文】列出数据库中所有有术语的 (book_id, target_lang) 命名空间。
        · 这是类方法（classmethod），不需要先创建实例
        · 为什么用类方法：实例已经绑定了一个 book_id，
          但审核界面需要看到所有书的术语才能让用户选择
        · 返回：[{"book_id", "target_lang", "term_count"}, ...] 列表，
          按术语数量降序排列
        · ⚠️ 历史踩坑：以前不是类方法，审核界面只能看到构造时传入的那本书，
          实测 131 行中只有 28 行（21.4%）能被看到，
          导致 confirm/reject 操作从未触及生产数据。
        """
        path = db_path or CHECKPOINT_DB_PATH
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                """SELECT book_id, target_lang, COUNT(*) AS n
                   FROM exact_glossary
                   GROUP BY book_id, target_lang
                   ORDER BY n DESC, book_id, target_lang""",
            ).fetchall()
        return [{"book_id": b, "target_lang": lang, "term_count": n}
                for b, lang, n in rows]

    def get_all_terms(self, status_filter: typing.Optional[str] = None,
                      target_lang: typing.Optional[str] = None) -> list[dict]:
        """List all terms, optionally filtered by status.

        【中文】列出所有术语，可按状态过滤。
        · 参数 status_filter：状态过滤值（如 "confirmed"、"pending_review"），
          None 表示不过滤
        · 参数 target_lang：目标语言
        · 返回：术语字典列表，含完整元数据
        · 无过滤时按 status 再按 term_cn 排序；有过滤时只按 term_cn 排序
        """
        lang = target_lang or self._active_lang
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            if status_filter:
                rows = conn.execute(
                    """SELECT term_cn, term_en, category, context, chapter_first_seen,
                              note, status, target_lang
                       FROM exact_glossary
                       WHERE book_id = ? AND status = ? AND target_lang = ?
                       ORDER BY term_cn""",
                    (self._book_id, status_filter, lang),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT term_cn, term_en, category, context, chapter_first_seen,
                              note, status, target_lang
                       FROM exact_glossary
                       WHERE book_id = ? AND target_lang = ?
                       ORDER BY status, term_cn""",
                    (self._book_id, lang),
                ).fetchall()
        return [dict(row) for row in rows]

    def confirm_term(self, term_cn: str, target_lang: typing.Optional[str] = None) -> int:
        """Set a term's status to 'confirmed'. Returns the rows updated.

        The count matters: terms are keyed by (book_id, term_cn, target_lang),
        so a call naming the wrong namespace matches nothing. Returning 0 lets
        the caller say "no such term" instead of reporting success for an
        update that never happened — which is exactly what the review API did
        before it started checking this.

        【中文】把术语标记为"已确认"。
        · 参数 term_cn：要确认的中文词
        · 参数 target_lang：目标语言
        · 返回：受影响的行数（0 = 没找到这个术语）
        · ⚠️ 返回值很重要：如果传错了语言，更新 0 行，
          调用方可以据此告诉用户"没有这个术语"而不是假装成功。
          以前审核 API 不检查返回值，导致确认操作从未真正生效。
        """
        lang = target_lang or self._active_lang
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "UPDATE exact_glossary SET status = 'confirmed' "
                "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                (self._book_id, term_cn, lang),
            )
            conn.commit()
            return cur.rowcount

    def reject_term(self, term_cn: str, target_lang: typing.Optional[str] = None) -> int:
        """Delete a term from the exact_store entirely. Returns the rows deleted.

        See confirm_term: a namespace mismatch deletes nothing, and the caller
        must be able to tell that apart from a successful rejection.

        The in-memory eviction is scoped to the same language as the DELETE.
        Against the old flat ``_dict`` it removed the term from whatever language
        the instance happened to hold, so rejecting a Spanish term could evict
        the English rendering of the same Chinese source while leaving the
        Spanish one in memory.

        【中文】从术语库中彻底删除一个术语（否决）。
        · 参数 term_cn：要删除的中文词
        · 参数 target_lang：目标语言
        · 返回：删除的行数（0 = 没找到）
        · 同时从内存和数据库中删除
        · ⚠️ 内存删除必须限定在同一语言格子里！
          旧的扁平字典实现中，删除西班牙语术语会把英语译文也从内存踢掉，
          而西班牙语的反而还在。
        """
        lang = target_lang or self._active_lang
        # 只从对应语言的格子里删除，不影响其他语言
        self._by_lang.get(lang, {}).pop(term_cn, None)
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM exact_glossary "
                "WHERE book_id = ? AND term_cn = ? AND target_lang = ?",
                (self._book_id, term_cn, lang),
            )
            conn.commit()
            return cur.rowcount

    def stats(self, target_lang: typing.Optional[str] = None) -> dict:
        """【中文】返回术语统计信息。
        · 返回：{"total_terms": N}，N 是指定语言的术语总数
        """
        return {"total_terms": len(self._bucket(target_lang))}

    def __len__(self) -> int:
        """Term count for the active language.

        Not the sum across languages: ``routes.py`` writes this to a per-job
        glossary file and reports it as that job's term count, so counting
        languages the job never targeted would overstate it.

        【中文】返回当前活跃语言的术语数量。
        不是所有语言的总和：routes.py 把这个数字写入每任务的术语文件，
        如果把所有语言加起来会虚报。
        """
        return len(self._bucket())

    def __contains__(self, term_cn: str) -> bool:
        """【中文】支持 ``term_cn in glossary`` 语法，检查术语是否存在于当前活跃语言。"""
        return term_cn in self._bucket()
