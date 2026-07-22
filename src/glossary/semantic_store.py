"""Semantic glossary layer backed by Chroma.

This layer handles fuzzy / context-dependent term retrieval. When the exact
layer misses a term (or when the chapter's theme suggests additional cultural
terms that don't literally appear in the text), Chroma finds semantically
related entries via vector search.
"""

import logging
import typing
import os as _os
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

# Redirect Chroma's ONNX model cache to project dir — the sandbox
# blocks writes to ~/.cache where Chroma defaults.
_ONNX_CACHE = Path(__file__).resolve().parent.parent.parent / "data" / "onnx_cache"
_onnx_path = str(_ONNX_CACHE / "all-MiniLM-L6-v2")
_os.makedirs(_onnx_path, exist_ok=True)

from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
ONNXMiniLM_L6_V2.DOWNLOAD_PATH = _onnx_path

import chromadb
from chromadb.config import Settings

from ..config import CHROMA_PERSIST_PATH

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

    def __init__(self, persist_path: typing.Optional[str] = None):
        path = persist_path or CHROMA_PERSIST_PATH
        import os
        os.makedirs(path, exist_ok=True)

        self._ready = False
        self._warned = False  # guard against log spam per instance
        self.client: typing.Optional[chromadb.PersistentClient] = None

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

    @_INIT_RETRY
    def _init_chroma(self, path: str) -> chromadb.PersistentClient:
        """Initialise the Chroma persistent client and probe the embedding model.

        Wrapped with tenacity retry: up to 3 attempts with exponential backoff
        (2s, 4s, 8s) to survive transient ONNX download failures.
        """
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

        Intended for API health-checks so operators know whether semantic
        search is actually working.
        """
        return self._ready and self.client is not None

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
        """Each target language gets its own collection for isolation."""
        name = f"terms_{target_lang.replace('-', '_')}"
        return self.client.get_or_create_collection(name=name)

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
            metas.append({"term_cn": cn, "term_en": t["term_en"], "category": t.get("category", "culture")})
            ids.append(cn)
        if ids:
            collection.upsert(documents=docs, metadatas=metas, ids=ids)

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
            {"term_cn": m["term_cn"], "term_en": m["term_en"], "category": m.get("category", "culture")}
            for m in results["metadatas"][0]
        ]

    def count(self, target_lang: str = "en-US") -> int:
        if not self._ready:
            return 0
        return self.get_or_create_collection(target_lang).count()
