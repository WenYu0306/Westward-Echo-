"""Semantic glossary layer backed by Chroma.

This layer handles fuzzy / context-dependent term retrieval. When the exact
layer misses a term (or when the chapter's theme suggests additional cultural
terms that don't literally appear in the text), Chroma finds semantically
related entries via vector search.
"""

import logging
import os as _os
import typing
from pathlib import Path

from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from ..config import CHROMA_PERSIST_PATH

# Redirect Chroma's ONNX model cache to project dir — the sandbox
# blocks writes to ~/.cache where Chroma defaults. The cache path must
# exist BEFORE importing chromadb, and DOWNLOAD_PATH must be patched
# before first use, so the imports below are intentionally deferred (E402).
_ONNX_CACHE = Path(__file__).resolve().parent.parent.parent / "data" / "onnx_cache"
_onnx_path = str(_ONNX_CACHE / "all-MiniLM-L6-v2")
_os.makedirs(_onnx_path, exist_ok=True)

from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2  # noqa: E402

ONNXMiniLM_L6_V2.DOWNLOAD_PATH = Path(_onnx_path)

import chromadb  # noqa: E402
from chromadb.config import Settings  # noqa: E402

logger = logging.getLogger("westward_echo.glossary")

# Retry decorator for Chroma / ONNX model initialisation.
# 3 attempts with exponential backoff: 2s, 4s, 8s.
_INIT_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=8),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


class SemanticGlossary:
    """
    Chroma-backed semantic glossary.

    Used for:
    - Retrieving culturally relevant terms for a chapter's theme
      (e.g. a cultivation chapter pulls in cultivation-related terms even
      if the exact character names don't appear)
    - Fallback fuzzy matching when exact string-contains fails
    """

    # Class-level flag shared across instances so recovery is attempted
    # at most once per process lifecycle (or after a 60s cooldown).
    _last_retry_time: float = 0.0

    def __init__(self, persist_path: typing.Optional[str] = None, book_id: str = "default"):
        path = persist_path or CHROMA_PERSIST_PATH
        import os
        os.makedirs(path, exist_ok=True)

        self._ready = False
        self._warned = False  # guard against log spam per instance
        self._persist_path = path
        self._book_id = book_id
        self.client: typing.Any = None

        try:
            self.client = self._init_chroma(path)
            self._ready = True
            logger.info("Chroma semantic store initialised successfully")
        except Exception as exc:
            self._ready = False
            logger.warning(
                "Chroma semantic store failed to initialise: %s. "
                "Semantic term retrieval and fuzzy matching are unavailable. "
                "Exact term lookups, translation, and chapter processing are unaffected.",
                exc,
            )

    def try_recover(self) -> bool:
        """Attempt re-initialisation if the store was previously unhealthy.

        Returns True on successful recovery, False if still unhealthy.
        """
        import time as _time
        now = _time.monotonic()
        if self._ready:
            # Already healthy — nothing to do but update the retry timestamp
            # so is_healthy() re-checks correctly.
            return True
        if now - SemanticGlossary._last_retry_time < 60:
            # Throttle recovery attempts to once every 60 seconds
            return False
        SemanticGlossary._last_retry_time = now
        try:
            self.client = self._init_chroma(self._persist_path)
            self._ready = True
            self._warned = False
            logger.info("Chroma semantic store recovered")
            return True
        except Exception as exc:
            logger.warning(
                "Chroma semantic store recovery failed: %s", exc
            )
            return False

    @_INIT_RETRY
    def _init_chroma(self, path: str) -> typing.Any:
        """Initialise the Chroma client and probe the embedding model.

        Two modes, selected by CHROMA_HOST env var:
          - CHROMA_HOST set → HTTP client (production, standalone Chroma server
            so multiple worker processes share one Chroma safely)
          - CHROMA_HOST unset → PersistentClient (local dev / tests fallback)

        Wrapped with tenacity retry: up to 3 attempts with exponential backoff
        (2s, 4s, 8s) to survive transient ONNX download failures.
        """
        import os as _os
        host = _os.getenv("CHROMA_HOST", "")
        port = _os.getenv("CHROMA_PORT", "8000")

        if host:
            client = chromadb.HttpClient(
                host=host,
                port=int(port),
                settings=Settings(anonymized_telemetry=False),
            )
        else:
            client = chromadb.PersistentClient(
                path=path,
                settings=Settings(
                    anonymized_telemetry=False,
                    persist_directory=path,
                ),
            )

        # Probe embedding to trigger model download early
        coll = client.get_or_create_collection("probe_init")
        coll.upsert(documents=["test"], ids=["test"])
        coll.delete(ids=["test"])
        return client

    def is_healthy(self) -> bool:
        """Return True when Chroma is initialised and the embedding model is loaded.

        If the store was previously unhealthy, automatically attempt recovery
        (throttled to once every 60 seconds).

        Intended for API health-checks so operators know whether semantic
        search is actually working.
        """
        if self._ready and self.client is not None:
            return True
        # Auto-attempt recovery if enough time has passed
        return self.try_recover()

    def _warn_once(self):
        """Emit a single WARNING when ops are attempted on an unready store."""
        if not self._warned:
            logger.warning(
                "Chroma semantic store is not ready — operation skipped. "
                "Semantic term retrieval is unavailable; exact term lookups, "
                "translation, and chapter processing are unaffected."
            )
            self._warned = True

    def get_or_create_collection(self, target_lang: str = "en-US") -> chromadb.Collection:
        """Each book + target language gets its own collection for isolation."""
        safe_book = self._book_id.replace('-', '_').replace('.', '_')
        name = f"terms_{safe_book}_{target_lang.replace('-', '_')}"
        assert self.client is not None
        return self.client.get_or_create_collection(name=name)  # type: ignore[no-any-return]

    def add_term(self, term_cn: str, term_en: str, category: str = "culture",
                 context: str = "", target_lang: str = "en-US"):
        if not self._ready:
            self._warn_once()
            return
        collection = self.get_or_create_collection(target_lang)
        doc_text = f"[{category}] {term_cn}: {context}" if context else f"[{category}] {term_cn}"
        collection.upsert(
            documents=[doc_text],
            metadatas=[{"term_cn": term_cn, "term_en": term_en, "category": category}],
            ids=[term_cn],
        )

    def add_batch(self, terms: list[dict], target_lang: str = "en-US"):
        if not self._ready:
            self._warn_once()
            return
        collection = self.get_or_create_collection(target_lang)
        docs, metas, ids = [], [], []
        for t in terms:
            cn = t["term_cn"]
            doc_text = f"[{t.get('category', 'culture')}] {cn}"
            if t.get("context"):
                doc_text += f": {t['context']}"
            docs.append(doc_text)
            metas.append(
                {"term_cn": cn, "term_en": t["term_en"], "category": t.get("category", "culture")}
            )
            ids.append(cn)
        if ids:
            collection.upsert(documents=docs, metadatas=metas, ids=ids)  # type: ignore[arg-type]

    def search(self, query_text: str, top_k: int = 15,
               target_lang: str = "en-US") -> list[dict]:
        if not self._ready:
            self._warn_once()
            return []
        collection = self.get_or_create_collection(target_lang)
        if collection.count() == 0:
            return []
        results = collection.query(query_texts=[query_text], n_results=top_k)
        if not results["metadatas"] or not results["metadatas"][0]:
            return []
        return [
            {
                "term_cn": m["term_cn"], "term_en": m["term_en"],
                "category": m.get("category", "culture"),
            }
            for m in results["metadatas"][0]
        ]

    def count(self, target_lang: str = "en-US") -> int:
        if not self._ready:
            return 0
        return self.get_or_create_collection(target_lang).count()
