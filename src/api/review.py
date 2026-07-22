"""Review API — human-in-the-loop glossary curation endpoints."""

from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from ..glossary.exact_store import ExactGlossary

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
def reject_term(term_cn: str):
    """Reject a term — deletes it from the glossary entirely."""
    store = _get_store()
    store.load_from_db()
    store.reject_term(term_cn)
    return {"ok": True, "term_cn": term_cn, "status": "rejected"}
