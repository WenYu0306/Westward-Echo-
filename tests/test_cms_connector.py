"""Unit tests for cms.py — CMS connectors and factory."""

import os
import tempfile

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
