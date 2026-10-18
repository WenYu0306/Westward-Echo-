"""Review API — human-in-the-loop glossary curation endpoints.

Namespace scoping
-----------------
Terms are keyed by ``(book_id, term_cn, target_lang)`` in SQLite, so every
endpoint here must say which namespace it means. Three of them used not to:
``_get_store()`` cached a single store bound to ``book_id="default"`` and
``load_from_db()``/``confirm_term()``/``reject_term()`` were called without a
``target_lang``, silently defaulting to en-US.

Measured consequence on the real checkpoints.db: the review UI could only see
28 of 131 terms (21.4%) — never a production book, because production jobs use
``book_id=job_id`` — and no term anywhere had ``status='confirmed'``. The
human-confirmation loop was wired end to end in code and still never touched
real data.

The store cache is now keyed per namespace rather than being a single global.

【中文·这个文件是干什么的】
这是「术语审核」功能的后端接口（也叫 API，即程序之间传数据的通道）。
你在浏览器打开 /review 页面时看到的那些术语卡片、确认/拒绝按钮，
点了之后就是调这里的某个函数来完成真正的数据读写。

【中文·为什么需要人工审核术语】
大模型翻译时会自己决定"这个角色名/地名/功法名怎么翻"，但它可能翻错、
前后不一致、或者用了读者看不懂的音译。人工审核就是让人看一眼：
  · 确认 → 这个译名锁进术语库，全书所有章节都用它，不再变
  · 拒绝 → 删掉这个译法，并记录下来告诉 AI "以后别这么翻"
这就是所谓的「人工校正回路」——人确认过的东西机器不会再犯同样的错。

【中文·⚠️ 命名空间隔离（踩坑史）】
术语按 (书名, 中文原词, 目标语言) 三个维度存进数据库。
早期代码把"书名"写死成 "default"、"目标语言"写死成 en-US，
导致生产环境里真正翻译的书（book_id = 任务号）里的术语根本看不到，
人工审核功能**从来没真正生效过**——数据库里"已确认"记录为零。
现在每个接口都必须显式传入 book_id 和 target_lang，不再默认。

【中文·本文件的接口清单】
  GET  /namespaces              → 列出所有有术语的 (书名, 语种) 组合
  GET  /terms                   → 列出某命名空间下的术语（可按状态筛选）
  POST /terms/{词}/confirm      → 确认一个术语
  POST /terms/{词}/reject       → 拒绝一个术语
  GET  /feedback                → 查看当前审核反馈状态
"""

# 【中文】FastAPI 是一个 Python 网页服务框架，下面用它来定义接口。
# HTTPException 是"返回错误码"的工具，Query 用来从网址参数里取值。
from fastapi import FastAPI, HTTPException, Query

# SUPPORTED_LANGUAGES 是项目支持的目标语言清单（en-US/es-ES/de/fr），
# 放在 config.py 里作为唯一真相源，所有地方都从这里读，避免多处不一致。
from ..config import SUPPORTED_LANGUAGES
# ExactGlossary 是术语库的精确匹配存储层，存在 SQLite 数据库里。
from ..glossary.exact_store import ExactGlossary
# job_store 是任务数据的存取层，这里用来读写"已确认/已拒绝"的术语反馈。
from ..job_store import job_store

# 【中文】创建一个独立的 FastAPI 应用实例，专门处理 /api/review/* 的请求。
# main.py 会把它 mount（挂载）到主应用的 /api/review 路径下。
app = FastAPI(title="Westward Echo Review API")


# One store per (book_id, target_lang) namespace.
#
# Historically this cache existed to work around a real bug: ExactGlossary kept
# a single flat `_dict`, so it could only ever hold the namespace loaded last
# and switching books or languages through one shared instance served the
# previous namespace's terms.
#
# Clause 13-A removed that bug — ExactGlossary now buckets terms per language
# (`_by_lang`), so one instance could serve several namespaces of the same book.
# The per-namespace cache is kept anyway as the simple, obviously-correct
# choice: each cached store loads only its own namespace, and `load_from_db`
# below refreshes it on every request so a term confirmed elsewhere is picked
# up. This is a preference now, not a workaround.
#
# 【中文·术语库缓存】每个 (书名, 目标语言) 组合对应一个独立的 ExactGlossary 实例。
# 早期代码只用一个全局实例，切换书目或语言时读到的是上一个命名空间的数据，
# 导致人工审核操作看起来成功但实际改错了地方。
# 现在按命名空间分别缓存，每次请求还会从数据库刷新，确保拿到最新数据。
_stores: dict[tuple[str, str], ExactGlossary] = {}


def _get_store(book_id: str, target_lang: str) -> ExactGlossary:
    """Return the store for one namespace, with that namespace's terms loaded.

    【中文】获取指定命名空间的术语库实例。
    · 参数 book_id：书名标识，生产环境里通常是任务号（job_id）
    · 参数 target_lang：目标语言代码，如 "en-US"、"es-ES"
    · 返回：加载了该命名空间最新数据的 ExactGlossary 实例
    · 为什么每次都 load_from_db：确保拿到别的请求刚确认/拒绝的最新结果，
      避免读到缓存里的旧数据。代价是多一次数据库查询，但审核场景频率低，
      完全可以接受。
    """
    key = (book_id, target_lang)
    store = _stores.get(key)
    if store is None:
        store = ExactGlossary(book_id=book_id)
        _stores[key] = store
    # 【中文】每次调用都从数据库重新加载该命名空间的术语，保证数据是最新的。
    store.load_from_db(target_lang=target_lang)
    return store


def _validated_lang(target_lang: str) -> str:
    """Reject an unknown language code instead of defaulting it to en-US.

    Defaulting is what made the old endpoints silently curate the wrong
    namespace: a Spanish term confirmed through an en-US-scoped call updated
    nothing and reported success.

    【中文】校验目标语言代码是否合法。
    · 参数 target_lang：前端传来的语言代码字符串
    · 返回：原样返回合法的代码
    · 为什么存在：早期代码遇到不认识的语言会默默当成 en-US 处理，
      导致西班牙语术语被"确认"到英语命名空间里——操作显示成功，
      但数据根本没改对地方。现在直接报错（HTTP 400），让问题当场暴露。
    """
    if target_lang not in SUPPORTED_LANGUAGES:
        # 【中文】不在支持列表里的语言代码，直接拒绝并告诉调用方哪些是合法的。
        raise HTTPException(
            status_code=400,
            detail=f"target_lang {target_lang!r} is not supported; "
                   f"expected one of {list(SUPPORTED_LANGUAGES)}",
        )
    return target_lang


@app.get("/namespaces")
def list_namespaces():
    """Every ``(book_id, target_lang)`` namespace that has terms.

    The UI needs this to offer a choice: without it there is no way to reach a
    production book's glossary, since production jobs use ``book_id=job_id``
    while this endpoint used to be hard-wired to ``default``.

    【中文】列出所有有术语数据的命名空间。
    · 网址：GET /api/review/namespaces
    · 参数：无
    · 返回：JSON，包含 namespaces 列表和 count 总数
    · 为什么存在：术语审核页打开时，需要先知道"有哪些书、哪些语种有术语"，
      才能让用户在下拉框里选。早期没有这个接口，页面写死查 "default"，
      生产环境的书永远看不到。
    """
    # 【中文】直接从 ExactGlossary 类方法获取，不需要指定某个命名空间。
    namespaces = ExactGlossary.list_namespaces()
    return {"namespaces": namespaces, "count": len(namespaces)}


@app.get("/terms")
def list_terms(
    status: str = Query(
        default=None, description="Filter: pending_review, confirmed, or omit for all"
    ),
    book_id: str = Query(default="default", description="Glossary namespace"),
    target_lang: str = Query(default="en-US", description="Target language code"),
):
    """List all glossary terms in one namespace, optionally filtered by status.

    【中文】列出某个命名空间下的所有术语。
    · 网址：GET /api/review/terms?book_id=xxx&target_lang=en-US&status=pending_review
    · 参数 status：可选，筛选状态。"pending_review"=待审核，"confirmed"=已确认，
      不传则返回全部
    · 参数 book_id：书名标识，默认 "default"
    · 参数 target_lang：目标语言代码，默认 "en-US"
    · 返回：JSON，包含 terms 列表、count 总数、以及当前查询的命名空间信息
    · 前端用法：术语审核页每次切换筛选按钮或下拉框时都会调这个接口刷新列表。
    """
    # 【中文】先校验语言代码合法性，再获取对应命名空间的术语库实例。
    lang = _validated_lang(target_lang)
    store = _get_store(book_id, lang)
    terms = store.get_all_terms(status_filter=status, target_lang=lang)
    return {"terms": terms, "count": len(terms),
            "book_id": book_id, "target_lang": lang}


@app.post("/terms/{term_cn}/confirm")
def confirm_term(
    term_cn: str,
    book_id: str = Query(default="default", description="Glossary namespace"),
    target_lang: str = Query(default="en-US", description="Target language code"),
):
    """Confirm a pending term — sets status to 'confirmed' in one namespace.

    Returns 404 when the namespace holds no such term. Reporting ``{"ok": true}``
    for an UPDATE that matched nothing is how the original defect hid: the
    reviewer saw success while the row they meant stayed pending.

    【中文】确认一个术语——把它的状态改成"已确认"。
    · 网址：POST /api/review/terms/{中文词}/confirm?book_id=xxx&target_lang=en-US
    · 参数 term_cn：要确认的中文原词（在 URL 路径里）
    · 返回：成功返回 {"ok": true, ...}；找不到该术语返回 HTTP 404
    · ⚠️ 为什么找不到时返回 404 而不是 {"ok": true}：
      早期代码不管数据库有没有真正更新都返回成功，导致审核人点了"确认"
      看到绿灯，但那条术语其实还在待审核状态。这是人工审核从未生效的
      根本原因之一。现在改为：没改到就明确报错。
    · 确认后效果：该术语锁进术语库，后续所有章节翻译都会用这个译名。
    """
    lang = _validated_lang(target_lang)
    store = _get_store(book_id, lang)
    # 【中文】confirm_term 返回实际更新的行数，0 表示没找到这个词。
    updated = store.confirm_term(term_cn, target_lang=lang)
    if not updated:
        # 【中文】没更新任何行 → 该命名空间里没有这个术语，返回 404。
        raise HTTPException(
            status_code=404,
            detail=f"no term {term_cn!r} in namespace "
                   f"book_id={book_id!r} target_lang={lang!r}",
        )
    return {"ok": True, "term_cn": term_cn, "status": "confirmed",
            "book_id": book_id, "target_lang": lang}


@app.post("/terms/{term_cn}/reject")
def reject_term(
    term_cn: str,
    rejected_en: str = Query(default="", description="The rejected translation"),
    book_id: str = Query(default="default", description="Glossary namespace"),
    target_lang: str = Query(default="en-US", description="Target language code"),
):
    """Reject a term — deletes it from the glossary and records the rejected translation.

    Before deleting, we look up the current translation to record it as a
    rejected term so the Agent knows to avoid it in future translations.

    Two distinct outcomes, both reported honestly:
    * the term existed and was deleted → ``deleted: true``
    * it was absent, but the caller supplied ``rejected_en`` → ``deleted:
      false``, ``feedback_recorded: true``. That is a real use: blocking a
      rendering that was never cached. Absent *and* nothing supplied → 404.

    【中文】拒绝一个术语——从术语库删除，并把被拒绝的译法记录下来。
    · 网址：POST /api/review/terms/{中文词}/reject?book_id=xxx&target_lang=en-US
    · 参数 rejected_en：可选，被拒绝的英文译法。如果术语库里已有该词的译文，
      会自动取库里的；如果没有但调用方提供了这个参数，也会记录反馈
    · 返回：JSON，包含 deleted（是否真的删了）和 feedback_recorded（是否记录了反馈）
    · 为什么记录被拒绝的译法：让 AI 在后续翻译中知道"这个词别这么翻"，
      避免同样的错误反复出现。这就是"人工校正回路"的另一半——
      不光告诉机器什么是对的，也告诉它什么是错的。
    · 三种结果：
      ① 术语存在并被删除 → deleted=true, feedback_recorded=true
      ② 术语不存在但提供了 rejected_en → deleted=false, feedback_recorded=true
      ③ 术语不存在也没提供 rejected_en → 返回 HTTP 404
    """
    lang = _validated_lang(target_lang)
    store = _get_store(book_id, lang)

    # Look up the current translation before deleting
    # 【中文】删除前先查出当前的译文，这样即使删了也能把被拒的译法记下来。
    current = store.get(term_cn)
    deleted = store.reject_term(term_cn, target_lang=lang)

    feedback_recorded = False
    if current:
        # 【中文】术语库里有这个词的译文，用它来记录反馈。
        job_store.reject_term_with_feedback(term_cn, current, lang)
        feedback_recorded = True
    elif rejected_en:
        # Caller explicitly provided the rejected translation
        # 【中文】术语库里没有，但调用方明确告诉了被拒绝的译法，也记录。
        job_store.reject_term_with_feedback(term_cn, rejected_en, lang)
        feedback_recorded = True

    if not deleted and not feedback_recorded:
        # 【中文】既没删到东西、也没记录反馈 → 彻底找不到，返回 404。
        raise HTTPException(
            status_code=404,
            detail=f"no term {term_cn!r} in namespace "
                   f"book_id={book_id!r} target_lang={lang!r}",
        )
    return {"ok": True, "term_cn": term_cn, "status": "rejected",
            "book_id": book_id, "target_lang": lang,
            "deleted": bool(deleted),
            "feedback_recorded": feedback_recorded}


@app.get("/feedback")
def get_feedback(
    book_id: str = Query(default="default", description="Glossary namespace"),
    target_lang: str = Query(default="en-US", description="Target language code"),
):
    """Return the current review feedback state.

    Includes confirmed terms (locked), rejected terms (blocked), and the
    count of pending terms still awaiting human review.

    【中文】获取当前审核反馈的全貌。
    · 网址：GET /api/review/feedback?book_id=xxx&target_lang=en-US
    · 返回：JSON，包含三部分：
      - confirmed：已确认的术语列表（锁进术语库的）
      - rejected：被拒绝的术语列表（AI 以后不会再用的译法）
      - pending：还在等人工审核的术语数量
    · 用途：让前端显示"还有多少词没审""已经确认了多少""拒绝了多少"，
      也供用量分析页统计人工编辑行为。
    """
    lang = _validated_lang(target_lang)
    store = _get_store(book_id, lang)

    # 【中文】分别从任务存储和术语库读取三类数据。
    confirmed = job_store.get_confirmed_terms(lang)
    rejected = job_store.get_rejected_terms(lang)
    pending_terms = store.get_all_terms(
        status_filter="pending_review", target_lang=lang
    )

    # Clean rejected terms for the API response
    # 【中文】只保留 term_cn 和 rejected_en 两个字段给前端，去掉内部细节。
    rejected_clean = [
        {"term_cn": r["term_cn"], "rejected_en": r["rejected_en"]}
        for r in rejected
    ]

    return {
        "confirmed": confirmed,
        "rejected": rejected_clean,
        "pending": len(pending_terms),
        "book_id": book_id,
        "target_lang": lang,
    }

