"""Tests for semantic_store.py — book_id collection isolation + client mode.

These test the concurrency-redesign core: the semantic glossary must scope
its Chroma collection per book_id (so parallel books don't share a term pool)
and must select HTTP vs Persistent client by CHROMA_HOST env var.

Uses a mock client injected via __new__ to avoid loading the ONNX embedding
model — these assertions are about the naming/client-selection logic, not
about Chroma's embedding behaviour.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.glossary.semantic_store import SemanticGlossary


def _make_store(book_id, mock_client):
    """Build a SemanticGlossary with a mock client, skipping real init."""
    s = SemanticGlossary.__new__(SemanticGlossary)
    s._book_id = book_id
    s._ready = True
    s._warned = False
    s._persist_path = "/tmp/unused"
    s.client = mock_client
    return s


class TestCollectionIsolation:
    def test_collection_name_includes_book_id(self):
        mock_client = MagicMock()
        store = _make_store("difu_xiaoxiansheng", mock_client)
        store.get_or_create_collection("en-US")
        name = mock_client.get_or_create_collection.call_args.kwargs["name"]
        assert "difu_xiaoxiansheng" in name
        assert "en_US" in name

    def test_two_books_use_different_collections(self):
        c1, c2 = MagicMock(), MagicMock()
        s1 = _make_store("book_a", c1)
        s2 = _make_store("book_b", c2)
        s1.get_or_create_collection("en-US")
        s2.get_or_create_collection("en-US")
        name1 = c1.get_or_create_collection.call_args.kwargs["name"]
        name2 = c2.get_or_create_collection.call_args.kwargs["name"]
        assert name1 != name2, "different books must NOT share a collection"
        assert "book_a" in name1
        assert "book_b" in name2

    def test_book_id_with_special_chars_is_sanitized(self):
        mock_client = MagicMock()
        store = _make_store("book-id.with.dots", mock_client)
        store.get_or_create_collection("en-US")
        name = mock_client.get_or_create_collection.call_args.kwargs["name"]
        # '-' and '.' are replaced with '_' so Chroma accepts the name
        assert "book_id_with_dots" in name
        assert "-" not in name
        assert "." not in name

    def test_per_language_collection_within_book(self):
        mock_client = MagicMock()
        store = _make_store("book_a", mock_client)
        store.get_or_create_collection("en-US")
        store.get_or_create_collection("es-ES")
        calls = [c.kwargs["name"] for c in mock_client.get_or_create_collection.call_args_list]
        assert "en_US" in calls[0]
        assert "es_ES" in calls[1]
        assert calls[0] != calls[1], "languages within a book must be separate collections"


class TestClientMode:
    def test_http_client_when_chroma_host_set(self, monkeypatch):
        monkeypatch.setenv("CHROMA_HOST", "chroma")
        monkeypatch.setenv("CHROMA_PORT", "8000")
        # Probe after client creation calls get_or_create_collection + upsert + delete
        mock_collection = MagicMock()
        mock_http = MagicMock()
        mock_http.get_or_create_collection.return_value = mock_collection
        with patch("chromadb.HttpClient", return_value=mock_http) as mock_http_cls:
            s = SemanticGlossary.__new__(SemanticGlossary)
            s._book_id = "default"
            s._init_chroma("/tmp/unused")
        mock_http_cls.assert_called_once()
        assert mock_http_cls.call_args[1]["host"] == "chroma"
        assert mock_http_cls.call_args[1]["port"] == 8000

    def test_persistent_client_when_no_chroma_host(self, monkeypatch):
        monkeypatch.delenv("CHROMA_HOST", raising=False)
        mock_collection = MagicMock()
        mock_persistent = MagicMock()
        mock_persistent.get_or_create_collection.return_value = mock_collection
        with patch("chromadb.PersistentClient", return_value=mock_persistent) as mock_pc:
            s = SemanticGlossary.__new__(SemanticGlossary)
            s._book_id = "default"
            s._init_chroma("/tmp/unused")
        mock_pc.assert_called_once()


# ── Init retry policy ─────────────────────────────────────────────

class TestInitRetryPolicy:
    """The ONNX model download is flaky — the retry envelope is a design choice."""

    def _retrying(self):
        return SemanticGlossary._init_chroma.retry

    def test_three_attempts(self):
        assert self._retrying().stop.max_attempt_number == 3

    def test_exponential_backoff_multiplier(self):
        assert self._retrying().wait.multiplier == 2

    def test_backoff_floor_is_two_seconds(self):
        assert self._retrying().wait.min == 2

    def test_backoff_ceiling_is_eight_seconds(self):
        assert self._retrying().wait.max == 8

    def test_original_exception_is_reraised(self):
        # reraise=False would replace the real cause (e.g. a download error)
        # with an opaque RetryError, making the warning log useless.
        assert self._retrying().reraise is True


# ── Constructor / readiness ───────────────────────────────────────

class TestConstructorReadiness:
    def test_successful_init_marks_the_store_ready(self, monkeypatch):
        monkeypatch.setattr(SemanticGlossary, "_init_chroma", lambda self, path: MagicMock())
        s = SemanticGlossary(persist_path="/tmp/unused-ready", book_id="b")
        assert s._ready is True
        assert s.is_healthy() is True

    def test_failed_init_leaves_the_store_unready(self, monkeypatch):
        def boom(self, path):
            raise RuntimeError("onnx unavailable")

        monkeypatch.setattr(SemanticGlossary, "_init_chroma", boom)
        monkeypatch.setattr(SemanticGlossary, "_last_retry_time", 0.0)
        s = SemanticGlossary(persist_path="/tmp/unused-broken", book_id="b")
        assert s._ready is False

    def test_persist_path_defaults_to_the_configured_chroma_path(self, monkeypatch):
        from src.config import CHROMA_PERSIST_PATH

        monkeypatch.setattr(SemanticGlossary, "_init_chroma", lambda self, path: MagicMock())
        s = SemanticGlossary(persist_path=None, book_id="b")
        assert str(s._persist_path) == str(CHROMA_PERSIST_PATH)

    def test_existing_persist_path_is_accepted(self, tmp_path, monkeypatch):
        # mkdir with exist_ok=False would explode on every restart.
        target = tmp_path / "already-there"
        target.mkdir()
        monkeypatch.setattr(SemanticGlossary, "_init_chroma", lambda self, path: MagicMock())
        s = SemanticGlossary(persist_path=str(target), book_id="b")
        assert s._persist_path == str(target)

    def test_store_is_not_ready_during_init(self, monkeypatch):
        """The ``_ready = False`` at the top of __init__ is a fail-safe, not a
        no-op — and it is observable WHILE _init_chroma runs.

        Every writer (add_term / add_batch / search / count) gates on
        ``if not self._ready``. The gate is only safe if the flag starts False:
        ``client`` is still None until the line AFTER _init_chroma returns, and
        get_or_create_collection asserts client is not None. A store that
        reports ready before init completes would let a writer through the gate
        and straight into that assert.

        The existing readiness tests only check the flag AFTER construction, by
        which point the success/failure branches have already overwritten it — so
        they cannot see the initial value, which is why ``const bool->True at 65``
        survived mutation testing. This observes the flag from inside the probe.
        """
        seen = {}

        def probe(self, path):
            seen["ready_during_init"] = self._ready
            seen["client_during_init"] = self.client
            return MagicMock()

        monkeypatch.setattr(SemanticGlossary, "_init_chroma", probe)
        monkeypatch.setattr(SemanticGlossary, "_last_retry_time", 0.0)

        SemanticGlossary(persist_path="/tmp/unused-during-init", book_id="b")

        assert seen["ready_during_init"] is False
        assert seen["client_during_init"] is None

    def test_writer_refuses_while_not_ready(self, monkeypatch):
        """Pins the gate the flag protects: a not-ready store must not touch the
        client. If the ready flag could be True with client still None, add_term
        would reach get_or_create_collection and trip its assertion."""
        s = _make_store("b", None)
        s._ready = False

        # Must return quietly (warn-and-skip), not raise on the None client.
        s.add_term("无常", "Wuchang")

        assert s._ready is False


# ── try_recover / is_healthy ──────────────────────────────────────

class TestRecoveryStateMachine:
    def test_ready_store_reports_recovered(self):
        s = _make_store("b", MagicMock())
        assert s.try_recover() is True

    def test_recent_attempt_is_throttled(self, monkeypatch):
        import time

        s = _make_store("b", None)
        s._ready = False
        monkeypatch.setattr(SemanticGlossary, "_last_retry_time", time.monotonic())
        assert s.try_recover() is False

    def test_attempt_allowed_once_the_throttle_window_passes(self, monkeypatch):
        import time

        s = _make_store("b", None)
        s._ready = False
        monkeypatch.setattr(SemanticGlossary, "_last_retry_time",
                            time.monotonic() - 60.5)
        monkeypatch.setattr(SemanticGlossary, "_init_chroma", lambda self, path: MagicMock())
        assert s.try_recover() is True

    def test_just_inside_the_window_is_still_throttled(self, monkeypatch):
        import time

        s = _make_store("b", None)
        s._ready = False
        monkeypatch.setattr(SemanticGlossary, "_last_retry_time",
                            time.monotonic() - 59.5)
        monkeypatch.setattr(SemanticGlossary, "_init_chroma", lambda self, path: MagicMock())
        assert s.try_recover() is False

    def test_successful_recovery_marks_ready_and_resets_warning(self, monkeypatch):
        s = _make_store("b", None)
        s._ready = False
        s._warned = True
        monkeypatch.setattr(SemanticGlossary, "_last_retry_time", 0.0)
        monkeypatch.setattr(SemanticGlossary, "_init_chroma", lambda self, path: MagicMock())
        assert s.try_recover() is True
        assert s._ready is True
        assert s._warned is False

    def test_failed_recovery_reports_false(self, monkeypatch):
        def boom(self, path):
            raise RuntimeError("still down")

        s = _make_store("b", None)
        s._ready = False
        monkeypatch.setattr(SemanticGlossary, "_last_retry_time", 0.0)
        monkeypatch.setattr(SemanticGlossary, "_init_chroma", boom)
        assert s.try_recover() is False

    def test_healthy_store_with_client_is_healthy(self):
        s = _make_store("b", MagicMock())
        assert s.is_healthy() is True

    def test_ready_without_client_is_not_healthy(self):
        # An instance that lost its client must not be reported healthy.
        s = _make_store("b", None)
        s.try_recover = lambda: False
        assert s.is_healthy() is False

    def test_unready_store_delegates_to_recovery(self):
        s = _make_store("b", MagicMock())
        s._ready = False
        s.try_recover = lambda: True
        assert s.is_healthy() is True

    def test_ready_and_client_set_does_not_attempt_recovery(self):
        s = _make_store("b", MagicMock())
        s.try_recover = lambda: (_ for _ in ()).throw(AssertionError("should not recover"))
        assert s.is_healthy() is True


class TestWarnOnce:
    def test_warns_on_first_use_only(self, caplog):
        import logging

        s = _make_store("b", MagicMock())
        s._ready = False
        with caplog.at_level(logging.WARNING, logger="westward_echo.glossary"):
            s._warn_once()
            s._warn_once()
            s._warn_once()
        messages = [r.getMessage() for r in caplog.records if "not ready" in r.getMessage()]
        assert len(messages) == 1

    def test_warn_flag_starts_clear(self, monkeypatch):
        monkeypatch.setattr(SemanticGlossary, "_init_chroma", lambda self, path: MagicMock())
        s = SemanticGlossary(persist_path="/tmp/unused-warned", book_id="b")
        assert s._warned is False


# ── _init_chroma client selection ─────────────────────────────────

class TestInitChromaProbe:
    def test_returns_the_constructed_client(self, monkeypatch):
        monkeypatch.delenv("CHROMA_HOST", raising=False)
        mock_collection = MagicMock()
        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection
        with patch("chromadb.PersistentClient", return_value=mock_client):
            s = SemanticGlossary.__new__(SemanticGlossary)
            s._book_id = "default"
            assert s._init_chroma("/tmp/unused") is mock_client

    def test_probe_document_is_cleaned_up(self, monkeypatch):
        monkeypatch.delenv("CHROMA_HOST", raising=False)
        mock_collection = MagicMock()
        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection
        with patch("chromadb.PersistentClient", return_value=mock_client):
            s = SemanticGlossary.__new__(SemanticGlossary)
            s._book_id = "default"
            s._init_chroma("/tmp/unused")
        mock_collection.upsert.assert_called_once()
        mock_collection.delete.assert_called_once_with(ids=["test"])

    def test_telemetry_is_disabled_for_persistent_client(self, monkeypatch):
        monkeypatch.delenv("CHROMA_HOST", raising=False)
        mock_client = MagicMock()
        with patch("chromadb.PersistentClient", return_value=mock_client) as mock_pc:
            s = SemanticGlossary.__new__(SemanticGlossary)
            s._book_id = "default"
            s._init_chroma("/tmp/unused")
        assert mock_pc.call_args[1]["settings"].anonymized_telemetry is False

    def test_telemetry_is_disabled_for_http_client(self, monkeypatch):
        monkeypatch.setenv("CHROMA_HOST", "chroma")
        mock_client = MagicMock()
        with patch("chromadb.HttpClient", return_value=mock_client) as mock_http:
            s = SemanticGlossary.__new__(SemanticGlossary)
            s._book_id = "default"
            s._init_chroma("/tmp/unused")
        assert mock_http.call_args[1]["settings"].anonymized_telemetry is False


# ── Store operations (mock collection) ────────────────────────────

class TestSearchBehaviour:
    def _searching_store(self, metadatas=None, count=1):
        client = MagicMock()
        collection = MagicMock()
        collection.count.return_value = count
        collection.query.return_value = {"metadatas": metadatas}
        client.get_or_create_collection.return_value = collection
        return _make_store("b", client), collection

    def test_default_top_k_is_fifteen(self):
        store, collection = self._searching_store(metadatas=[[{"term_cn": "鬼节",
                                                             "term_en": "Ghost Festival",
                                                             "category": "culture"}]])
        store.search("鬼节")
        assert collection.query.call_args.kwargs["n_results"] == 15

    def test_query_text_is_passed_through(self):
        store, collection = self._searching_store(metadatas=[[{"term_cn": "鬼节",
                                                             "term_en": "GF",
                                                             "category": "culture"}]])
        store.search("鬼节的晚上")
        assert collection.query.call_args.kwargs["query_texts"] == ["鬼节的晚上"]

    def test_results_are_mapped_to_dicts(self):
        store, _ = self._searching_store(metadatas=[[
            {"term_cn": "鬼节", "term_en": "Ghost Festival", "category": "culture"},
        ]])
        assert store.search("x") == [
            {"term_cn": "鬼节", "term_en": "Ghost Festival", "category": "culture"}
        ]

    def test_missing_category_defaults_to_culture(self):
        store, _ = self._searching_store(metadatas=[[{"term_cn": "鬼节", "term_en": "GF"}]])
        assert store.search("x")[0]["category"] == "culture"

    def test_unready_store_returns_empty_list(self):
        s = _make_store("b", MagicMock())
        s._ready = False
        assert s.search("x") == []

    def test_unready_store_does_not_query(self):
        client = MagicMock()
        s = _make_store("b", client)
        s._ready = False
        s.search("x")
        client.get_or_create_collection.assert_not_called()

    def test_empty_collection_returns_empty_list(self):
        store, collection = self._searching_store(metadatas=None, count=0)
        assert store.search("x") == []
        collection.query.assert_not_called()

    def test_empty_metadata_list_returns_empty_list(self):
        store, _ = self._searching_store(metadatas=[])
        assert store.search("x") == []

    def test_empty_metadata_inner_list_returns_empty_list(self):
        store, _ = self._searching_store(metadatas=[[]])
        assert store.search("x") == []


class TestAddAndCount:
    def _adding_store(self):
        client = MagicMock()
        collection = MagicMock()
        collection.count.return_value = 7
        client.get_or_create_collection.return_value = collection
        return _make_store("b", client), collection

    def test_add_term_upserts_one_document(self):
        store, collection = self._adding_store()
        store.add_term("鬼节", "Ghost Festival", category="culture")
        kwargs = collection.upsert.call_args.kwargs
        assert kwargs["ids"] == ["鬼节"]
        assert kwargs["metadatas"][0]["term_en"] == "Ghost Festival"
        assert kwargs["documents"] == ["[culture] 鬼节"]

    def test_add_term_includes_context_when_given(self):
        store, collection = self._adding_store()
        store.add_term("鬼节", "Ghost Festival", context="第十五天")
        assert collection.upsert.call_args.kwargs["documents"] == ["[culture] 鬼节: 第十五天"]

    def test_add_batch_upserts_only_when_there_are_terms(self):
        store, collection = self._adding_store()
        store.add_batch([])
        collection.upsert.assert_not_called()

    def test_add_batch_maps_every_term(self):
        store, collection = self._adding_store()
        store.add_batch([
            {"term_cn": "鬼节", "term_en": "Ghost Festival", "category": "culture"},
            {"term_cn": "聋婆婆", "term_en": "Deaf Granny", "category": "character",
             "context": "老槐树下"},
        ])
        kwargs = collection.upsert.call_args.kwargs
        assert kwargs["ids"] == ["鬼节", "聋婆婆"]
        assert kwargs["documents"][1] == "[character] 聋婆婆: 老槐树下"

    def test_unready_store_add_term_is_a_noop(self):
        client = MagicMock()
        s = _make_store("b", client)
        s._ready = False
        s.add_term("鬼节", "Ghost Festival")
        client.get_or_create_collection.assert_not_called()

    def test_unready_store_add_batch_is_a_noop(self):
        client = MagicMock()
        s = _make_store("b", client)
        s._ready = False
        s.add_batch([{"term_cn": "鬼节", "term_en": "GF"}])
        client.get_or_create_collection.assert_not_called()

    def test_count_returns_the_collection_size(self):
        store, _ = self._adding_store()
        assert store.count() == 7

    def test_unready_count_is_zero(self):
        s = _make_store("b", MagicMock())
        s._ready = False
        assert s.count() == 0
