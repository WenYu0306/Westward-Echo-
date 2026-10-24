"""Unit tests for cms.py — CMS connectors and factory."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.cms import (
    FileSystemConnector,
    WebhookConnector,
    _validate_source_id,
    get_connector,
)

# ═══════════════════════════════════════════════════════════════
# source_id validation
# ═══════════════════════════════════════════════════════════════

class TestValidateSourceId:
    def test_valid_ids(self):
        _validate_source_id("测试小说")
        _validate_source_id("novel_01")
        _validate_source_id("a-b_c")
        _validate_source_id("全职高手")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            _validate_source_id("")

    def test_overlong_rejected(self):
        with pytest.raises(ValueError):
            _validate_source_id("x" * 201)

    def test_traversal_rejected(self):
        with pytest.raises(ValueError):
            _validate_source_id("../etc")
        with pytest.raises(ValueError):
            _validate_source_id("a/../b")

    def test_path_separator_rejected(self):
        with pytest.raises(ValueError):
            _validate_source_id("a/b")
        with pytest.raises(ValueError):
            _validate_source_id("a\\b")

    def test_null_byte_rejected(self):
        with pytest.raises(ValueError):
            _validate_source_id("nov\x00el")


# ═══════════════════════════════════════════════════════════════
# FileSystemConnector
# ═══════════════════════════════════════════════════════════════

class TestFileSystemConnector:
    @pytest.fixture
    def novel_dir(self):
        tmpdir = tempfile.mkdtemp()
        novel_path = os.path.join(tmpdir, "测试小说.txt")
        with open(novel_path, "w", encoding="utf-8") as f:
            f.write("第一章 测试\n\n这是正文内容。\n")
        yield tmpdir

    def test_pull_novel(self, novel_dir):
        c = FileSystemConnector(base_dir=novel_dir)
        text = c.pull_novel("测试小说")
        assert "这是正文内容" in text

    def test_pull_missing(self, novel_dir):
        c = FileSystemConnector(base_dir=novel_dir)
        with pytest.raises(FileNotFoundError):
            c.pull_novel("不存在的书")

    def test_traversal_blocked(self, novel_dir):
        c = FileSystemConnector(base_dir=novel_dir)
        for evil in ["../secret", "a/../../etc"]:
            with pytest.raises(ValueError):
                c.pull_novel(evil)

    def test_list_sources(self, novel_dir):
        c = FileSystemConnector(base_dir=novel_dir)
        sources = c.list_sources()
        assert "测试小说" in sources

    def test_list_sources_missing_dir(self):
        c = FileSystemConnector(base_dir="/nonexistent/dir/xyz")
        assert c.list_sources() == []

    def test_push_translation(self, novel_dir):
        from src.config import OUTPUT_DIR
        c = FileSystemConnector(base_dir=novel_dir)

        # Create a translation file
        job_id = "testjob99"
        md_path = OUTPUT_DIR / f"{job_id}_full_novel_en-US.md"
        md_path.write_text("# Test\n\nContent.\n", encoding="utf-8")

        try:
            result = c.push_translation(job_id, "web")
            assert result["status"] == "published"
            assert "url" in result
        finally:
            md_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
# get_connector factory
# ═══════════════════════════════════════════════════════════════

class TestGetConnector:
    def test_file_connector(self):
        import src.config as cfg
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cfg, "CMS_SOURCE_TYPE", "file")
            mp.setattr(cfg, "CMS_FILE_BASE_DIR", "/tmp/novels")
            c = get_connector()
            assert isinstance(c, FileSystemConnector)

    def test_webhook_connector(self):
        import src.config as cfg
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cfg, "CMS_SOURCE_TYPE", "webhook")
            mp.setattr(cfg, "CMS_WEBHOOK_URL", "https://example.com/api")
            mp.setattr(cfg, "CMS_WEBHOOK_API_KEY", "")
            c = get_connector()
            assert isinstance(c, WebhookConnector)

    def test_unknown_source_type(self):
        import src.config as cfg
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cfg, "CMS_SOURCE_TYPE", "database")
            with pytest.raises(ValueError, match="Unknown CMS_SOURCE_TYPE"):
                get_connector()


class TestWebhookConnector:
    """WebhookConnector — HTTP pull/push, client caching, error handling."""

    def test_client_lazy_init_and_cached(self):
        wc = WebhookConnector("https://example.com", api_key="secret")
        with patch("src.cms.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            c1 = wc.client
            c2 = wc.client
        assert c1 is c2
        assert mock_client_cls.call_count == 1
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["timeout"] == 30
        assert kwargs["headers"] == {"Authorization": "Bearer secret"}

    def test_client_no_auth_header_without_key(self):
        wc = WebhookConnector("https://example.com")
        with patch("src.cms.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value = MagicMock()
            wc.client
        assert mock_client_cls.call_args.kwargs["headers"] == {}

    def test_pull_novel_returns_text_and_builds_url(self):
        wc = WebhookConnector("https://example.com")
        mock_client = MagicMock()
        mock_client.get.return_value.text = "第一章 测试"
        wc._client = mock_client
        result = wc.pull_novel("test-book")
        assert result == "第一章 测试"
        mock_client.get.assert_called_with("https://example.com/novels/test-book")

    def test_push_translation_success_truncates_message(self):
        wc = WebhookConnector("https://example.com")
        mock_client = MagicMock()
        mock_client.post.return_value.text = "x" * 300
        mock_client.post.return_value.raise_for_status = MagicMock()
        wc._client = mock_client
        result = wc.push_translation("job1", "web")
        assert result["status"] == "published"
        assert result["url"] == "https://example.com/translations/job1"
        assert len(result["platform_message"]) == 200  # truncated to 200 chars

    def test_push_translation_http_error_returns_failed(self):
        wc = WebhookConnector("https://example.com")
        mock_client = MagicMock()
        mock_client.post.return_value.raise_for_status.side_effect = httpx.HTTPError("boom")
        wc._client = mock_client
        result = wc.push_translation("job1", "web")
        assert result["status"] == "failed"
        assert "boom" in result["platform_message"]


class TestConnectorDefaults:
    def test_connector_without_sources_lists_none(self):
        # The base-class default (used by connectors that have no remote
        # source ids) must be an empty LIST, not None.
        from src.cms import CMSConnector

        class _Stub(CMSConnector):
            def pull_novel(self, source_id):
                raise NotImplementedError

            def push_translation(self, job_id, platform, /):
                raise NotImplementedError

        sources = _Stub().list_sources()
        assert sources == []
        assert isinstance(sources, list)

    def test_nested_base_dir_is_created_on_publish(self, tmp_path):
        # A fresh install has no novels/ directory at all.
        from src.cms import FileSystemConnector

        base = tmp_path / "novels" / "nested"
        connector = FileSystemConnector(base_dir=str(base))
        result = connector.push_translation("job123", "web")

        assert result["status"] == "published"
        assert (base / "output").is_dir()

    def test_republishing_into_an_existing_output_dir_works(self, tmp_path):
        # Publishing twice must not explode on the directory that the first
        # publish created.
        from src.cms import FileSystemConnector

        base = tmp_path / "novels"
        connector = FileSystemConnector(base_dir=str(base))
        first = connector.push_translation("job456", "web")
        second = connector.push_translation("job456", "web")
        assert first["status"] == second["status"] == "published"
