"""Unit tests for prefetch.py — background chapter glossary prefetcher."""

import time

import pytest

from src.prefetch import ChapterPrefetcher


class FakeExactStore:
    """Minimal fake for ExactGlossary used by the prefetcher."""
    def match_in_text(self, content: str) -> dict:
        # Return some fake matches keyed by content hash
        if "苏念" in content:
            return {"苏念": "Su Nian"}
        return {}


class FakeSemanticStore:
    """Minimal fake for SemanticGlossary used by the prefetcher."""
    def search(self, content: str, top_k: int = 15, target_lang: str = "en-US"):
        if "霸总" in content:
            return [{"term_cn": "霸总", "term_en": "Alpha CEO", "category": "culture"}]
        return []


@pytest.fixture
def prefetcher():
    return ChapterPrefetcher(FakeExactStore(), FakeSemanticStore())


class TestPrefetchBasic:
    def test_get_if_ready_returns_none_before_submit(self, prefetcher):
        assert prefetcher.get_if_ready("第一章 苏念醒来") is None

    def test_submit_and_retrieve(self, prefetcher):
        content = "第一章 苏念醒来，发现自己穿书了。"
        prefetcher.submit_next(content, "en-US")
        time.sleep(0.2)  # Let the background thread finish
        result = prefetcher.get_if_ready(content)
        assert result is not None
        exact, semantic = result
        assert "苏念" in exact

    def test_get_if_ready_only_matches_exact_content(self, prefetcher):
        content_a = "第一章 苏念醒来"
        content_b = "第二章 苏念出门"
        prefetcher.submit_next(content_a, "en-US")
        time.sleep(0.2)
        # Requesting different content returns None
        assert prefetcher.get_if_ready(content_b) is None

    def test_prefetched_cleared_after_retrieval(self, prefetcher):
        content = "第一章 苏念"
        prefetcher.submit_next(content, "en-US")
        time.sleep(0.2)
        prefetcher.get_if_ready(content)
        # Second call returns None
        assert prefetcher.get_if_ready(content) is None

    def test_semantic_dedup_from_exact(self, prefetcher):
        """Semantic hits already in exact matches are filtered out."""
        content = "第一章 苏念遇见霸总"
        prefetcher.submit_next(content, "en-US")
        time.sleep(0.2)
        exact, semantic = prefetcher.get_if_ready(content)
        # "苏念" is in exact, so it should NOT appear in semantic results
        semantic_terms = [t["term_cn"] for t in semantic]
        assert "苏念" not in semantic_terms

    def test_submit_overwrites_previous(self, prefetcher):
        content_a = "第一章 苏念醒来"
        content_b = "第二章 霸总登场"
        prefetcher.submit_next(content_a, "en-US")
        # Overwrite immediately
        prefetcher.submit_next(content_b, "en-US")
        time.sleep(0.2)
        # content_a results are discarded
        assert prefetcher.get_if_ready(content_a) is None
        # content_b results should be available
        result = prefetcher.get_if_ready(content_b)
        assert result is not None

    def test_shutdown(self, prefetcher):
        prefetcher.submit_next("第一章", "en-US")
        prefetcher.shutdown()
        # Must not raise — executor is shut down
