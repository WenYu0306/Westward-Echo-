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
"""

from fastapi import FastAPI, HTTPException, Query

from ..config import SUPPORTED_LANGUAGES
from ..glossary.exact_store import ExactGlossary
from ..job_store import job_store

app = FastAPI(title="Westward Echo Review API")


# One store per (book_id, target_lang) namespace. A single shared store was the
# bug: ExactGlossary keeps an in-memory `_dict` loaded for whichever namespace
# was last requested, so switching books or languages through one cached
# instance served stale terms from the previous one.
_stores: dict[tuple[str, str], ExactGlossary] = {}


def _get_store(book_id: str, target_lang: str) -> ExactGlossary:
    """Return the store for one namespace, with its in-memory dict loaded."""
    key = (book_id, target_lang)
    store = _stores.get(key)
    if store is None:
        store = ExactGlossary(book_id=book_id)
        _stores[key] = store
    store.load_from_db(target_lang=target_lang)
    return store


def _validated_lang(target_lang: str) -> str:
    """Reject an unknown language code instead of defaulting it to en-US.

    Defaulting is what made the old endpoints silently curate the wrong
    namespace: a Spanish term confirmed through an en-US-scoped call updated
    nothing and reported success.
    """
    if target_lang not in SUPPORTED_LANGUAGES:
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
    """
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
    """List all glossary terms in one namespace, optionally filtered by status."""
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
    """
    lang = _validated_lang(target_lang)
    store = _get_store(book_id, lang)
    updated = store.confirm_term(term_cn, target_lang=lang)
    if not updated:
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
    """
    lang = _validated_lang(target_lang)
    store = _get_store(book_id, lang)

    # Look up the current translation before deleting
    current = store.get(term_cn)
    deleted = store.reject_term(term_cn, target_lang=lang)

    feedback_recorded = False
    if current:
        job_store.reject_term_with_feedback(term_cn, current, lang)
        feedback_recorded = True
    elif rejected_en:
        # Caller explicitly provided the rejected translation
        job_store.reject_term_with_feedback(term_cn, rejected_en, lang)
        feedback_recorded = True

    if not deleted and not feedback_recorded:
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
    """
    lang = _validated_lang(target_lang)
    store = _get_store(book_id, lang)

    confirmed = job_store.get_confirmed_terms(lang)
    rejected = job_store.get_rejected_terms(lang)
    pending_terms = store.get_all_terms(status_filter="pending_review", target_lang=lang)

    # Clean rejected terms for the API response
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

