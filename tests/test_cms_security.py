"""Security tests for CMS source_id validation (path traversal defense).

Covers the vulnerability where an attacker-controlled source_id was joined
into a filesystem path without validation, allowing reads outside the
configured base directory.
"""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from src.cms import FileSystemConnector, _validate_source_id


@pytest.fixture
def novel_dir():
    """A temp base directory containing one legitimate novel."""
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "测试小说.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("第一章 测试\n\n这是正文。\n")
    yield tmpdir


@pytest.fixture
def secret_outside(novel_dir):
    """A secret file OUTSIDE the base dir — must never be readable."""
    secret = os.path.join(os.path.dirname(novel_dir), "secret.txt")
    with open(secret, "w", encoding="utf-8") as f:
        f.write("TOP SECRET")
    yield secret
    os.remove(secret)


class TestValidateSourceId:
    def test_valid_plain_stem(self):
        _validate_source_id("测试小说")          # must not raise
        _validate_source_id("novel-01_v2")       # must not raise

    def test_traversal_rejected(self):
        with pytest.raises(ValueError):
            _validate_source_id("../secret")
        with pytest.raises(ValueError):
            _validate_source_id("../../etc/passwd")
        with pytest.raises(ValueError):
            _validate_source_id("a/../b")

    def test_path_separators_rejected(self):
        with pytest.raises(ValueError):
            _validate_source_id("a/b")
        with pytest.raises(ValueError):
            _validate_source_id("a\\b")

    def test_null_byte_rejected(self):
        with pytest.raises(ValueError):
            _validate_source_id("novel\x00.txt")

    def test_empty_and_overlong_rejected(self):
        with pytest.raises(ValueError):
            _validate_source_id("")
        with pytest.raises(ValueError):
            _validate_source_id("x" * 201)


class TestFileSystemConnector:
    def test_pull_legit_novel(self, novel_dir):
        c = FileSystemConnector(base_dir=novel_dir)
        assert "这是正文" in c.pull_novel("测试小说")

    def test_traversal_cannot_read_outside(self, novel_dir, secret_outside):
        c = FileSystemConnector(base_dir=novel_dir)
        for evil in ["../secret", "../../etc/passwd", "a/../../secret"]:
            with pytest.raises(ValueError):
                c.pull_novel(evil)

    def test_missing_novel_error_hides_full_path(self, novel_dir):
        c = FileSystemConnector(base_dir=novel_dir)
        with pytest.raises(FileNotFoundError) as exc_info:
            c.pull_novel("不存在的书")
        # Error message must not leak the resolved server path
        assert novel_dir not in str(exc_info.value)

    def test_symlink_escape_blocked(self, novel_dir, secret_outside):
        """A symlink inside base_dir pointing outside must not be readable."""
        link = os.path.join(novel_dir, "escape.txt")
        try:
            os.symlink(secret_outside, link)
        except OSError:
            pytest.skip("symlinks not supported on this platform")
        c = FileSystemConnector(base_dir=novel_dir)
        with pytest.raises((ValueError, FileNotFoundError)):
            c.pull_novel("escape")


class TestCmsImportEndpoint:
    """End-to-end: traversal attempts must get HTTP 400, not 404/502."""

    def test_import_rejects_traversal(self, novel_dir):
        import src.api.cms as cms_mod

        connector = FileSystemConnector(base_dir=novel_dir)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.cms.get_connector", lambda: connector)
            mp.setattr(cms_mod, "_has_celery", False)
            client = TestClient(cms_mod.app)
            r = client.post(
                "/import",
                data={"source_type": "file", "source_id": "../secret"},
            )
            assert r.status_code == 400
            assert "Invalid source_id" in r.json()["error"]

    def test_import_accepts_legit_source(self, novel_dir):
        import src.api.cms as cms_mod

        connector = FileSystemConnector(base_dir=novel_dir)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.api.cms.get_connector", lambda: connector)
            mp.setattr(cms_mod, "_has_celery", False)
            client = TestClient(cms_mod.app)
            r = client.post(
                "/import",
                data={"source_type": "file", "source_id": "测试小说"},
            )
            assert r.status_code == 200
            j = r.json()
            assert "job_id" in j
            assert j["total_chapters"] >= 1
