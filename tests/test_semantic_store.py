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
