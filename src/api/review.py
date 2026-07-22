"""Review API — human-in-the-loop glossary curation endpoints."""

from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from ..glossary.exact_store import ExactGlossary
from ..job_store import job_store

app = FastAPI(title="Westward Echo Review API")


_term_store: Optional[ExactGlossary] = None


def _get_store() -> ExactGlossary:
    global _term_store
    if _term_store is None:
        _term_store = ExactGlossary()
    return _term_store


@app.get("/terms")
def list_terms(status: str = Query(default=None, description="Filter: pending_review, confirmed, or omit for all")):
    """List all glossary terms, optionally filtered by status."""
    store = _get_store()
    store.load_from_db()
    terms = store.get_all_terms(status_filter=status)
    return {"terms": terms, "count": len(terms)}


@app.post("/terms/{term_cn}/confirm")
def confirm_term(term_cn: str):
    """Confirm a pending term — sets status to 'confirmed'."""
    store = _get_store()
    store.load_from_db()
    store.confirm_term(term_cn)
    return {"ok": True, "term_cn": term_cn, "status": "confirmed"}


@app.post("/terms/{term_cn}/reject")
def reject_term(term_cn: str, rejected_en: str = Query(default="", description="The rejected English translation")):
    """Reject a term — deletes it from the glossary and records the rejected translation.

    Before deleting, we look up the current English translation to record it
    as a rejected term so the Agent knows to avoid it in future translations.
    """
    store = _get_store()
    store.load_from_db()

    # Look up the current translation before deleting
    current_en = store.get(term_cn)
    if current_en:
        job_store.reject_term_with_feedback(term_cn, current_en)
    elif rejected_en:
        # Caller explicitly provided the rejected translation
        job_store.reject_term_with_feedback(term_cn, rejected_en)

    store.reject_term(term_cn)
    return {"ok": True, "term_cn": term_cn, "status": "rejected"}


@app.get("/feedback")
def get_feedback(target_lang: str = Query(default="en-US", description="Target language code")):
    """Return the current review feedback state.

    Includes confirmed terms (locked), rejected terms (blocked), and the
    count of pending terms still awaiting human review.
    """
    store = _get_store()
    store.load_from_db(target_lang=target_lang)

    confirmed = job_store.get_confirmed_terms(target_lang)
    rejected = job_store.get_rejected_terms(target_lang)
    pending_terms = store.get_all_terms(status_filter="pending_review", target_lang=target_lang)

    # Clean rejected terms for the API response
    rejected_clean = [
        {"term_cn": r["term_cn"], "rejected_en": r["rejected_en"]}
        for r in rejected
    ]

    return {
        "confirmed": confirmed,
        "rejected": rejected_clean,
        "pending": len(pending_terms),
    }
