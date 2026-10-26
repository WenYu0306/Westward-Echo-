"""Review API — namespace isolation for human glossary curation.

Why this file exists
--------------------
``src/api/review.py`` had **zero test coverage**, so the following could all be
true while every suite stayed green (they were, and it did):

* ``app.mount("/api", api_router)`` was registered *before*
  ``app.mount("/api/review", ...)``. Starlette matches mounts in registration
  order, so the catch-all swallowed the more specific sub-app and every
  ``/api/review/*`` request 404'd. The review page's three fetch calls
  (web_ui.py:1837/1886/1897) never reached the server — ``/api/editor/*`` was
  dead the same way. ``/api/cms`` survived only because it was mounted first.
* ``_get_store()`` cached one ``ExactGlossary`` bound to ``book_id="default"``,
  while production jobs run under ``book_id=job_id``. Measured on the real
  checkpoints.db: the API could reach **28 of 131 terms (21.4%)** and no
  production book at all.
* ``load_from_db()`` / ``confirm_term()`` / ``reject_term()`` took no
  ``target_lang``, silently defaulting to en-US, so confirming a Spanish term
  updated nothing and still returned ``{"ok": true}``.
* Composite effect: ``SELECT COUNT(*) FROM exact_glossary WHERE
  status='confirmed'`` returned **0 rows** — the human curation loop had never
  once run against real data.

Terms are keyed by ``(book_id, term_cn, target_lang)``; these tests pin that all
three parts are honoured and that an unusable language code is rejected.

Namespace hygiene
-----------------
The fixtures deliberately avoid ``book_id="default"``: it is the module-level
default of ``ExactGlossary()``, so unrelated suites (test_dual_model,
test_prompt_personas, test_fidelity_injection, …) write terms into it through
``read_node``. Asserting exact term sets against ``default`` passed in isolation
and failed under a full run — the same cross-namespace bleed this file is about.
The backward-compatibility class still exercises the real defaults, but asserts
on the echoed namespace and on one distinctive term instead of on a set it does
not own.
"""

import re

import pytest
from fastapi.testclient import TestClient

from src.glossary.exact_store import ExactGlossary

# Unique to this suite so no other test can pollute the namespaces under test.
BOOK_A = "rev_ns_a"
BOOK_B = "rev_ns_b"
# Store-level tests get their own book again, separate from the API tests': two
# suites writing the same (book_id, target_lang) key is exactly the collision
# this file is meant to expose, and it must not happen between the fixtures.
BOOK_STORE = "rev_ns_store"
# Seeded into the real "default" namespace to prove the parameter defaults, while
# staying immune to whatever else lands there.
DEFAULT_MARKER = "审校隔离标记词"
DEFAULT_RENDERING = "review-namespace-marker"


@pytest.fixture(autouse=True)
def _clear_store_cache():
    """review.py caches one store per namespace for the process lifetime.

    Without clearing it, a namespace created by an earlier test would be reused
    with its in-memory dict still loaded, and cross-test leakage would look like
    the exact staleness bug these tests are meant to catch.
    """
    import src.api.review as review
    review._stores.clear()
    yield
    review._stores.clear()


@pytest.fixture(autouse=True)
def _disable_celery():
    import src.api.routes as routes
    routes._has_celery = False


@pytest.fixture
def client():
    from src.main import create_app
    return TestClient(create_app())


def _reset_status(book_id: str, terms: list[str] | None = None) -> None:
    """Return this suite's seeded rows to ``pending_review``.

    The store now preserves a human-confirmed status across re-adds (by design:
    an INSERT OR REPLACE used to silently un-confirm curated terms). These
    namespaces belong to this suite, so resetting them explicitly is what keeps
    each test starting from the same row state.
    """
    import sqlite3

    from src.config import CHECKPOINT_DB_PATH
    with sqlite3.connect(CHECKPOINT_DB_PATH) as conn:
        has_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='exact_glossary'").fetchone()
        if not has_table:
            return          # fresh per-test DB; the store will create it
        if terms is None:
            conn.execute("UPDATE exact_glossary SET status='pending_review' "
                         "WHERE book_id=?", (book_id,))
        else:
            conn.executemany(
                "UPDATE exact_glossary SET status='pending_review' "
                "WHERE book_id=? AND term_cn=?",
                [(book_id, term) for term in terms])


@pytest.fixture
def seed():
    """Two books × two languages, sharing 他 so scoping is observable.

    Both languages carry both books' terms: an earlier version of this fixture
    gave BOOK_B a Spanish 地府 but no Spanish 他, so the "confirm only the
    requested language" test silently confirmed a row that did not exist and
    then asserted on the one that did — a fixture defect that read like a
    product bug.
    """
    _reset_status(BOOK_A)
    _reset_status(BOOK_B)
    _reset_status("default", [DEFAULT_MARKER])
    store_a = ExactGlossary(book_id=BOOK_A)
    store_a.add("他", "he", target_lang="en-US")
    store_a.add("他", "Él", target_lang="es-ES")

    store_b = ExactGlossary(book_id=BOOK_B)
    store_b.add("他", "he", target_lang="en-US")
    store_b.add("他", "Él", target_lang="es-ES")
    store_b.add("地府", "the Bureau", target_lang="en-US")
    store_b.add("地府", "el Inframundo", target_lang="es-ES")

    # One distinctive term in the real default namespace, for the
    # backward-compatibility tests below.
    ExactGlossary(book_id="default").add(
        DEFAULT_MARKER, DEFAULT_RENDERING, target_lang="en-US")

    return {"a": store_a, "b": store_b}


def _statuses(book_id: str, term_cn: str) -> dict[str, str]:
    """Read a term's status per language straight from the store."""
    out = {}
    for lang in ("en-US", "es-ES"):
        store = ExactGlossary(book_id=book_id)
        store.load_from_db(target_lang=lang)
        out[lang] = store.get_status(term_cn, target_lang=lang)
    return out


def _terms(client, **params) -> set:
    response = client.get("/api/review/terms", params=params)
    assert response.status_code == 200
    return {t["term_cn"] for t in response.json()["terms"]}


# ── the sub-app is reachable at all ──────────────────────────────────────


class TestReviewApiIsMounted:
    """The mount-order defect, pinned: a catch-all ``/api`` mount registered
    first makes this whole file 404."""

    def test_namespaces_endpoint_is_reachable(self, client, seed):
        response = client.get("/api/review/namespaces")
        assert response.status_code == 200

    def test_terms_endpoint_is_reachable(self, client, seed):
        assert client.get("/api/review/terms").status_code == 200

    def test_feedback_endpoint_is_reachable(self, client, seed):
        assert client.get("/api/review/feedback").status_code == 200

    def test_sibling_api_routes_still_resolve(self, client, seed):
        """Moving the catch-all mount last must not break what it used to serve,
        nor the directly-registered /api/* routes that precede it."""
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/dashboard/metrics").status_code == 200
        assert client.get("/api/cms/sources").status_code == 200


# ── the default namespace is no longer the only reachable one ────────────


class TestNamespacesAreEnumerable:
    def test_namespaces_endpoint_lists_every_book_and_language(self, client, seed):
        response = client.get("/api/review/namespaces")
        pairs = {(n["book_id"], n["target_lang"])
                 for n in response.json()["namespaces"]}
        assert (BOOK_A, "en-US") in pairs
        assert (BOOK_A, "es-ES") in pairs
        assert (BOOK_B, "en-US") in pairs
        assert (BOOK_B, "es-ES") in pairs

    def test_namespace_rows_carry_a_term_count(self, client, seed):
        response = client.get("/api/review/namespaces")
        by_pair = {(n["book_id"], n["target_lang"]): n["term_count"]
                   for n in response.json()["namespaces"]}
        assert by_pair[(BOOK_B, "en-US")] == 2      # 他 + 地府
        assert by_pair[(BOOK_B, "es-ES")] == 2      # 他 + 地府
        assert by_pair[(BOOK_A, "es-ES")] == 1      # 他 only

    def test_namespaces_are_ordered_by_size(self, client, seed):
        """The UI defaults to the first entry, so the most-used one leads."""
        response = client.get("/api/review/namespaces")
        counts = [n["term_count"] for n in response.json()["namespaces"]]
        assert counts == sorted(counts, reverse=True)

    def test_a_job_scoped_book_is_reachable_at_all(self, client, seed):
        """The defect in one assertion: production jobs use ``book_id=job_id``,
        which the review API could not address while its store was hard-wired to
        ``default``."""
        assert "地府" in _terms(client, book_id=BOOK_B, target_lang="en-US")


# ── terms are scoped by both book and language ───────────────────────────


class TestTermsAreNamespaced:
    def test_terms_are_filtered_by_book(self, client, seed):
        a = _terms(client, book_id=BOOK_A, target_lang="en-US")
        b = _terms(client, book_id=BOOK_B, target_lang="en-US")
        assert b - a == {"地府"}

    def test_terms_are_filtered_by_language(self, client, seed):
        en = client.get("/api/review/terms",
                        params={"book_id": BOOK_B,
                                "target_lang": "en-US"}).json()["terms"]
        es = client.get("/api/review/terms",
                        params={"book_id": BOOK_B,
                                "target_lang": "es-ES"}).json()["terms"]
        assert {t["term_cn"]: t["term_en"] for t in en}["地府"] == "the Bureau"
        assert {t["term_cn"]: t["term_en"] for t in es}["地府"] == "el Inframundo"

    def test_response_echoes_the_namespace_it_served(self, client, seed):
        """A caller must be able to tell which namespace it got. The old
        response carried no such field, so a mis-scoped reply looked normal."""
        payload = client.get("/api/review/terms",
                             params={"book_id": BOOK_B,
                                     "target_lang": "es-ES"}).json()
        assert payload["book_id"] == BOOK_B
        assert payload["target_lang"] == "es-ES"

    def test_status_filter_works_within_a_namespace(self, client, seed):
        response = client.get("/api/review/terms",
                              params={"book_id": BOOK_B,
                                      "target_lang": "en-US",
                                      "status": "confirmed"})
        assert response.json()["count"] == 0

    def test_switching_namespace_does_not_serve_stale_terms(self, client, seed):
        """The store cache is keyed per namespace; reusing one instance across
        namespaces would serve the previous book's in-memory dict."""
        assert _terms(client, book_id=BOOK_B, target_lang="en-US") == {"他", "地府"}
        assert _terms(client, book_id=BOOK_A, target_lang="en-US") == {"他"}
        # …and back again, in case the cache repopulates in the wrong order.
        assert _terms(client, book_id=BOOK_B, target_lang="en-US") == {"他", "地府"}


# ── confirm / reject honour the full key ────────────────────────────────


class TestConfirmIsNamespaced:
    def test_confirm_updates_only_the_requested_language(self, client, seed):
        response = client.post("/api/review/terms/他/confirm",
                               params={"book_id": BOOK_B, "target_lang": "es-ES"})
        assert response.status_code == 200
        assert response.json()["status"] == "confirmed"
        statuses = _statuses(BOOK_B, "他")
        assert statuses["es-ES"] == "confirmed"
        assert statuses["en-US"] == "pending_review", (
            "confirming the Spanish term must not confirm the English one")

    def test_confirm_updates_only_the_requested_book(self, client, seed):
        client.post("/api/review/terms/他/confirm",
                    params={"book_id": BOOK_B, "target_lang": "en-US"})
        assert _statuses(BOOK_B, "他")["en-US"] == "confirmed"
        assert _statuses(BOOK_A, "他")["en-US"] == "pending_review", (
            "confirming in one book must not reach another namespace")

    def test_confirm_response_echoes_its_namespace(self, client, seed):
        payload = client.post("/api/review/terms/他/confirm",
                              params={"book_id": BOOK_B,
                                      "target_lang": "es-ES"}).json()
        assert payload["book_id"] == BOOK_B
        assert payload["target_lang"] == "es-ES"

    def test_confirmed_term_becomes_visible_under_the_status_filter(self, client, seed):
        """This is the loop that never closed: after a confirm, the term must
        show up as confirmed in the same namespace the reviewer was in."""
        client.post("/api/review/terms/地府/confirm",
                    params={"book_id": BOOK_B, "target_lang": "es-ES"})
        confirmed = client.get("/api/review/terms",
                               params={"book_id": BOOK_B,
                                       "target_lang": "es-ES",
                                       "status": "confirmed"}).json()["terms"]
        assert [t["term_cn"] for t in confirmed] == ["地府"]

    def test_confirming_an_absent_term_is_404_not_ok(self, client, seed):
        """Reporting ``{"ok": true}`` for an UPDATE that matched zero rows is how
        the original defect hid — the reviewer saw success while the row stayed
        pending."""
        response = client.post("/api/review/terms/不存在的词/confirm",
                               params={"book_id": BOOK_B,
                                       "target_lang": "en-US"})
        assert response.status_code == 404
        assert "不存在的词" in response.json()["detail"]

    def test_confirming_in_the_wrong_language_is_404(self, client, seed):
        """BOOK_A has no German entry for 他, so a de-scoped confirm matches
        nothing — it must not silently succeed."""
        response = client.post("/api/review/terms/他/confirm",
                               params={"book_id": BOOK_A, "target_lang": "de"})
        assert response.status_code == 404
        assert _statuses(BOOK_A, "他")["en-US"] == "pending_review"


class TestRejectIsNamespaced:
    def test_reject_deletes_only_the_requested_language(self, client, seed):
        response = client.post("/api/review/terms/地府/reject",
                               params={"book_id": BOOK_B, "target_lang": "es-ES"})
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        statuses = _statuses(BOOK_B, "地府")
        assert statuses["es-ES"] is None, "rejected term must be gone"
        assert statuses["en-US"] == "pending_review", (
            "the English rendering must survive rejecting the Spanish one")

    def test_reject_does_not_touch_another_book(self, client, seed):
        client.post("/api/review/terms/他/reject",
                    params={"book_id": BOOK_B, "target_lang": "en-US"})
        assert _statuses(BOOK_B, "他")["en-US"] is None
        assert _statuses(BOOK_A, "他")["en-US"] == "pending_review"

    def test_reject_records_the_rejected_rendering_for_its_language(self, client, seed):
        """The Agent reads rejected_terms so it never re-proposes a bad
        rendering. Recording it under the wrong language would block the term in
        English too, or block nothing at all."""
        from src.job_store import job_store
        client.post("/api/review/terms/地府/reject",
                    params={"book_id": BOOK_B, "target_lang": "es-ES"})
        rejected_es = {r["term_cn"]: r["rejected_en"]
                       for r in job_store.get_rejected_terms("es-ES")}
        assert rejected_es.get("地府") == "el Inframundo"
        assert "地府" not in {r["term_cn"]
                              for r in job_store.get_rejected_terms("en-US")}

    def test_reject_uses_the_stored_rendering_when_caller_omits_it(self, client, seed):
        from src.job_store import job_store
        client.post("/api/review/terms/他/reject",
                    params={"book_id": BOOK_A, "target_lang": "en-US"})
        rejected = {r["term_cn"]: r["rejected_en"]
                    for r in job_store.get_rejected_terms("en-US")}
        assert rejected.get("他") == "he"

    def test_reject_of_a_stored_term_reports_feedback_recorded(self, client, seed):
        """The ``if current:`` branch — a term that really was in the glossary.

        Asserted separately from the ``elif rejected_en`` branch below because
        they set the same flag from different code paths; covering only one left
        the other a permanent mutation survivor.
        """
        response = client.post("/api/review/terms/地府/reject",
                               params={"book_id": BOOK_B,
                                       "target_lang": "en-US"})
        payload = response.json()
        assert payload["ok"] is True
        assert payload["deleted"] is True
        assert payload["feedback_recorded"] is True

    def test_successful_responses_report_ok_true(self, client, seed):
        """``ok`` is what the web UI branches on (web_ui.py:1886/1897), so a
        flipped flag is a silent no-op for the reviewer. It was never asserted
        anywhere, which is why both mutations survived the first run."""
        confirm = client.post("/api/review/terms/地府/confirm",
                              params={"book_id": BOOK_B,
                                      "target_lang": "en-US"})
        assert confirm.json()["ok"] is True
        reject = client.post("/api/review/terms/他/reject",
                             params={"book_id": BOOK_B,
                                     "target_lang": "en-US"})
        assert reject.json()["ok"] is True

    def test_supplied_rendering_is_only_a_fallback(self, client, seed):
        """When the term exists, the stored rendering wins — ``rejected_en`` is
        for a rendering nobody cached. Asserting the opposite here was an
        incorrect expectation in the first draft of this file."""
        from src.job_store import job_store
        client.post("/api/review/terms/他/reject",
                    params={"book_id": BOOK_A, "target_lang": "es-ES",
                            "rejected_en": "Él mismo"})
        rejected = {r["term_cn"]: r["rejected_en"]
                    for r in job_store.get_rejected_terms("es-ES")}
        assert rejected.get("他") == "Él"

    def test_reject_can_block_a_rendering_that_was_never_cached(self, client, seed):
        """The legitimate use of ``rejected_en``: nothing to delete, something
        worth recording."""
        from src.job_store import job_store
        response = client.post("/api/review/terms/龙傲天/reject",
                               params={"book_id": BOOK_A,
                                       "target_lang": "es-ES",
                                       "rejected_en": "el Dragón Arrogante"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["deleted"] is False, "nothing was in the glossary"
        assert payload["feedback_recorded"] is True
        rejected = {r["term_cn"]: r["rejected_en"]
                    for r in job_store.get_rejected_terms("es-ES")}
        assert rejected.get("龙傲天") == "el Dragón Arrogante"

    def test_reject_with_neither_term_nor_rendering_is_404(self, client, seed):
        """Nothing to delete and nothing to record — must not report success."""
        response = client.post("/api/review/terms/不存在的词/reject",
                               params={"book_id": BOOK_B, "target_lang": "en-US"})
        assert response.status_code == 404
        assert "不存在的词" in response.json()["detail"]


# ── an unusable language code must not silently become en-US ────────────


class TestUnknownLanguageIsRejected:
    @pytest.mark.parametrize("endpoint", ["/api/review/terms", "/api/review/feedback"])
    def test_get_endpoints_return_400(self, client, seed, endpoint):
        response = client.get(endpoint, params={"target_lang": "pt-BR"})
        assert response.status_code == 400
        assert "pt-BR" in response.json()["detail"]

    @pytest.mark.parametrize("action", ["confirm", "reject"])
    def test_post_endpoints_return_400(self, client, seed, action):
        response = client.post(f"/api/review/terms/他/{action}",
                               params={"target_lang": "pt-BR"})
        assert response.status_code == 400

    def test_bare_language_code_is_also_rejected(self, client, seed):
        """``en`` is not a registry entry — round7 removed the bare codes."""
        assert client.get("/api/review/terms",
                          params={"target_lang": "en"}).status_code == 400

    def test_rejected_language_leaves_data_untouched(self, client, seed):
        client.post("/api/review/terms/他/confirm", params={"target_lang": "pt-BR"})
        assert _statuses(BOOK_A, "他") == {
            "en-US": "pending_review", "es-ES": "pending_review"}


# ── backward compatibility ───────────────────────────────────────────────


class TestDefaultsStayBackwardCompatible:
    """The web UI calls these endpoints with no namespace parameters
    (web_ui.py:1837/1886/1897), so omitting them must keep working.

    These assert on the echoed namespace and on one distinctive term rather than
    on the whole ``default`` set: that namespace is shared with every other
    suite that constructs ``ExactGlossary()`` without a book_id, so its contents
    are not this file's to pin.
    """

    def test_terms_defaults_to_the_default_book_and_en_us(self, client, seed):
        payload = client.get("/api/review/terms").json()
        assert payload["book_id"] == "default"
        assert payload["target_lang"] == "en-US"
        assert DEFAULT_MARKER in {t["term_cn"] for t in payload["terms"]}

    def test_confirm_defaults_to_the_default_book_and_en_us(self, client, seed):
        response = client.post(f"/api/review/terms/{DEFAULT_MARKER}/confirm")
        assert response.status_code == 200
        assert response.json()["book_id"] == "default"
        assert response.json()["target_lang"] == "en-US"
        store = ExactGlossary(book_id="default")
        assert store.get_status(DEFAULT_MARKER) == "confirmed"

    def test_reject_defaults_to_the_default_book_and_en_us(self, client, seed):
        response = client.post(f"/api/review/terms/{DEFAULT_MARKER}/reject")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        store = ExactGlossary(book_id="default")
        assert store.get_status(DEFAULT_MARKER) is None

    def test_feedback_defaults_to_the_default_book_and_en_us(self, client, seed):
        payload = client.get("/api/review/feedback").json()
        assert payload["book_id"] == "default"
        assert payload["target_lang"] == "en-US"
        assert payload["pending"] >= 1


class TestFeedbackIsNamespaced:
    def test_pending_count_reflects_only_the_requested_namespace(self, client, seed):
        en = client.get("/api/review/feedback",
                        params={"book_id": BOOK_B, "target_lang": "en-US"}).json()
        es = client.get("/api/review/feedback",
                        params={"book_id": BOOK_B, "target_lang": "es-ES"}).json()
        a = client.get("/api/review/feedback",
                       params={"book_id": BOOK_A, "target_lang": "en-US"}).json()
        assert en["pending"] == 2          # 他 + 地府
        assert es["pending"] == 2          # 他 + 地府
        assert a["pending"] == 1           # 他 only

    def test_pending_count_drops_after_a_confirm(self, client, seed):
        params = {"book_id": BOOK_B, "target_lang": "en-US"}
        before = client.get("/api/review/feedback", params=params).json()["pending"]
        client.post("/api/review/terms/地府/confirm", params=params)
        after = client.get("/api/review/feedback", params=params).json()["pending"]
        assert after == before - 1

    def test_feedback_echoes_its_namespace(self, client, seed):
        payload = client.get("/api/review/feedback",
                             params={"book_id": BOOK_A,
                                     "target_lang": "es-ES"}).json()
        assert payload["book_id"] == BOOK_A
        assert payload["target_lang"] == "es-ES"


# ── the store-level primitive the API depends on ─────────────────────────


class TestStoreReportsAffectedRows:
    """confirm_term/reject_term used to return None, so a namespace mismatch was
    indistinguishable from success. These pin the counts directly.

    Each test seeds its own rows. An earlier draft read BOOK_A/BOOK_B without
    asking for ``seed`` and passed the full file run purely on rows the
    API tests had left behind — it failed the moment the class ran alone. Same
    cross-namespace bleed this file exists to prevent.
    """

    @pytest.fixture
    def row(self):
        """One term, one language, in a namespace nothing else touches.

        Its own book_id on purpose: pointing this at BOOK_A let the API tests'
        ``seed`` leak a Spanish 他 in here, so the "wrong language deletes
        nothing" test passed alone and failed in a full run. Status is reset
        explicitly because re-adding a term no longer clears it.
        """
        _reset_status(BOOK_STORE)
        store = ExactGlossary(book_id=BOOK_STORE)
        store.add("他", "he", target_lang="en-US")
        return store

    def test_confirm_returns_one_for_a_real_term(self, row):
        assert row.confirm_term("他", target_lang="en-US") == 1

    def test_confirm_returns_zero_for_an_absent_term(self, row):
        assert row.confirm_term("不存在的词", target_lang="en-US") == 0

    def test_confirm_returns_zero_in_the_wrong_language(self, row):
        """The en-US row exists; the de row does not. A mismatched language must
        report zero rather than silently succeeding against the wrong row."""
        assert row.confirm_term("他", target_lang="de") == 0
        assert row.get_status("他", target_lang="en-US") == "pending_review"

    def test_confirm_returns_zero_in_the_wrong_book(self, row):
        assert ExactGlossary(book_id="rev_ns_absent").confirm_term(
            "他", target_lang="en-US") == 0

    def test_reject_returns_one_for_a_real_term(self, row):
        assert row.reject_term("他", target_lang="en-US") == 1
        assert row.get_status("他", target_lang="en-US") is None

    def test_reject_returns_zero_for_an_absent_term(self, row):
        assert row.reject_term("不存在的词", target_lang="en-US") == 0

    def test_reject_returns_zero_in_the_wrong_language(self, row):
        assert row.reject_term("他", target_lang="es-ES") == 0
        assert row.get_status("他", target_lang="en-US") == "pending_review", (
            "the en-US row must survive a Spanish-scoped delete")


# ── frontend wiring ─────────────────────────────────────────────────────


class TestReviewPageSendsItsNamespace:
    """The review page's fetches must carry book_id and target_lang.

    Fixing the API alone is not enough: the page used to call
    ``/api/review/terms`` with no parameters, so even a correctly scoped
    backend would still serve it ``default``/``en-US`` and production books
    would stay unreachable. These are source-text assertions, matching the
    pattern test_language_registry.py already uses for web_ui.py, because the
    page's JavaScript cannot execute under pytest.

    The behaviour itself was verified separately by replaying the page's exact
    request sequence (loadNamespaces → fetchTerms → confirm/reject) against the
    live app, and by running the ``nsQuery`` function under node to check its
    URL encoding.
    """

    @staticmethod
    def _review_page() -> str:
        from src.web_ui import REVIEW_PAGE
        return REVIEW_PAGE

    def test_namespace_selectors_exist(self):
        page = self._review_page()
        for element_id in ("ns-book", "ns-lang"):
            assert f'id="{element_id}"' in page, (
                f"missing #{element_id} selector on the review page")

    def test_nsquery_always_sends_book_and_language(self):
        """The helper that builds every review request must pin both parameters.
        Dropping either silently reverts the page to the default namespace."""
        page = self._review_page()
        helper = re.search(r"function nsQuery\(extra\)\{(.*?)\n\}", page, re.S)
        assert helper, "nsQuery helper not found in the review page"
        body = helper.group(1)
        assert "book_id:currentBook" in body
        assert "target_lang:currentLang" in body

    def test_terms_request_uses_nsquery(self):
        page = self._review_page()
        assert "'/api/review/terms?'+nsQuery(" in page

    def test_confirm_request_uses_nsquery(self):
        page = self._review_page()
        assert re.search(
            r"/confirm\?'\+nsQuery\(\)", page), (
            "confirm must be scoped, or it confirms in default/en-US")

    def test_reject_request_uses_nsquery(self):
        page = self._review_page()
        assert re.search(
            r"/reject\?'\+nsQuery\(\)", page), (
            "reject must be scoped, or it deletes from default/en-US")

    def test_namespace_list_is_loaded_before_terms(self):
        """fetchTerms refuses to run without a namespace, so the page must
        resolve /namespaces first — otherwise the first paint requests
        default/en-US regardless of what the reviewer later selects."""
        page = self._review_page()
        assert "await loadNamespaces()" in page
        assert "if(!currentBook||!currentLang)return" in page

    def test_changing_book_repopulates_languages(self):
        """A book need not have every language; offering an unavailable
        combination would 404 on load."""
        page = self._review_page()
        assert "repopulateLangs()" in page
        assert "function langsFor(bookId)" in page

    def test_no_review_fetch_is_left_unscoped(self):
        """Every /api/review/ call in the page must go through nsQuery.

        This is the drift guard: a new fetch added later without parameters
        would reintroduce the default-namespace bug in one place while the
        tests above still pass for the three they know about.

        The one exception is /namespaces itself, which cannot be scoped — it is
        the endpoint that enumerates the scopes the other calls then choose
        between. Allowing it explicitly (rather than by a loose pattern) keeps
        the guard meaningful: the first version of this test used a regex that
        captured trailing punctuation and failed on this legitimate call.
        """
        page = self._review_page()
        calls = re.findall(r"fetch\('(/api/review/[a-z_/{}+-]*)'", page)
        assert calls, "no review fetch calls found — pattern needs updating"
        for path in calls:
            if path.endswith("/namespaces"):
                continue
            # The scoped calls build their query separately, so check the line
            # the call lives on rather than the path literal alone.
            line = next(line for line in page.splitlines() if path in line)
            assert "nsQuery" in line, f"unscoped review fetch: {line.strip()}"

    def test_namespaces_fetch_is_deliberately_unscoped(self):
        """Pin the exception so it stays an exception: if someone later adds
        nsQuery to the namespaces call the page would ask "which namespaces
        exist in this namespace", which cannot answer."""
        page = self._review_page()
        line = next(line for line in page.splitlines()
                    if "/api/review/namespaces" in line)
        assert "nsQuery" not in line
