"""Core-path tests for Celery checkpoint persistence.

Covers the checkpoint save/load cycle that the async Celery worker uses:
_save_checkpoint → _load_checkpoint_translations → _load_checkpoint_summary.
Also tests _chapter_md format (the EPUB-parsable header that fixes bug C1)
and TranslationProgress integration with JobStore.
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def temp_checkpoint_db():
    """Redirect CHECKPOINT_DB_PATH to a temp file for isolation."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_checkpoints.db")
    import src.celery_app as celery_mod
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(celery_mod, "CHECKPOINT_DB_PATH", db_path)
        yield db_path
    # Cleanup
    for f in Path(tmpdir).glob("*"):
        f.unlink()
    os.rmdir(tmpdir)


# ═══════════════════════════════════════════════════════════════════
# _chapter_md — EPUB-parseable chapter header format (bug C1 fix)
# ═══════════════════════════════════════════════════════════════════

class TestChapterMd:
    def test_format_with_english_title(self):
        from src.celery_app import _chapter_md
        result = _chapter_md(5, "第五章 测试", "She walked in.", "The Test")
        assert result.startswith("## Chapter 5: The Test")
        assert "She walked in." in result
        assert result.endswith("---")

    def test_format_falls_back_to_chinese_title_truncated(self):
        from src.celery_app import _chapter_md
        long_title = "第两百三十四章 这是一个非常长的标题超过了六十个字符的限制" + "x" * 20
        result = _chapter_md(234, long_title, "content", "")
        # Should use first 60 chars of Chinese title
        assert "## Chapter 234: " in result
        assert len(result.split("\n")[0]) <= 60 + len("## Chapter 234: ")

    def test_format_is_parseable_by_epub_regex(self):
        """The exact regex used by _parse_markdown_chapters in routes.py."""
        import re
        from src.celery_app import _chapter_md

        result = _chapter_md(42, "whatever", " Translated content here. ", "Answer")
        pattern = re.compile(r"^#{1,2}\s+Chapter\s+(\d+):?\s*(.*)", re.IGNORECASE)
        m = pattern.match(result.split("\n")[0].strip())
        assert m is not None
        assert int(m.group(1)) == 42
        assert "Answer" in m.group(2)

    def test_format_no_title_en_uses_cn_title(self):
        from src.celery_app import _chapter_md
        result = _chapter_md(1, "第一章 楔子", "Once upon a time.", "")
        assert "## Chapter 1: 第一章 楔子" in result


# ═══════════════════════════════════════════════════════════════════
# Checkpoint save → load round-trip
# ═══════════════════════════════════════════════════════════════════

class TestCheckpointRoundTrip:
    def test_save_and_load_translations(self, temp_checkpoint_db):
        from src.celery_app import _save_checkpoint, _load_checkpoint_translations

        _save_checkpoint("job-abc", 1, "Chapter one text.", '{"a":"b"}', "summary 1")
        _save_checkpoint("job-abc", 2, "Chapter two text.", '{"a":"c"}', "summary 2")

        loaded = _load_checkpoint_translations("job-abc")
        assert loaded == {1: "Chapter one text.", 2: "Chapter two text."}

    def test_save_overwrites_existing_chapter(self, temp_checkpoint_db):
        from src.celery_app import _save_checkpoint, _load_checkpoint_translations

        _save_checkpoint("job-x", 1, "First attempt.", "{}", "s1")
        _save_checkpoint("job-x", 1, "Second attempt.", "{}", "s2")

        loaded = _load_checkpoint_translations("job-x")
        assert loaded[1] == "Second attempt."

    def test_load_translations_for_unknown_job(self, temp_checkpoint_db):
        from src.celery_app import _load_checkpoint_translations
        assert _load_checkpoint_translations("nonexistent") == {}

    def test_load_translations_no_db(self, temp_checkpoint_db):
        """When the DB file doesn't exist, return empty dict."""
        from src.celery_app import _load_checkpoint_translations
        # temp_checkpoint_db sets the path but doesn't create the file yet
        # _load_checkpoint_translations checks Path.exists()
        import src.celery_app as cm
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cm, "CHECKPOINT_DB_PATH", "/nonexistent/path/db.sqlite")
            assert cm._load_checkpoint_translations("any") == {}

    def test_save_and_load_summary(self, temp_checkpoint_db):
        from src.celery_app import _save_checkpoint, _load_checkpoint_summary

        _save_checkpoint("job-y", 3, "text", "{}", "Hero enters the cave.")
        assert _load_checkpoint_summary("job-y", 3) == "Hero enters the cave."

    def test_load_summary_unknown(self, temp_checkpoint_db):
        from src.celery_app import _load_checkpoint_summary
        assert _load_checkpoint_summary("nojob", 99) == ""

    def test_save_creates_db_if_missing(self, temp_checkpoint_db):
        """_save_checkpoint must create the DB + table when they don't exist."""
        from src.celery_app import _save_checkpoint
        db_path = temp_checkpoint_db
        # Remove the db file if it exists
        if os.path.exists(db_path):
            os.unlink(db_path)
        _save_checkpoint("new-job", 1, "text", "{}", "summary")
        assert os.path.exists(db_path)

    def test_job_isolation(self, temp_checkpoint_db):
        """Different jobs must not see each other's chapters."""
        from src.celery_app import _save_checkpoint, _load_checkpoint_translations

        _save_checkpoint("job-a", 1, "A1", "{}", "sa")
        _save_checkpoint("job-b", 1, "B1", "{}", "sb")
        _save_checkpoint("job-b", 2, "B2", "{}", "sb2")

        assert _load_checkpoint_translations("job-a") == {1: "A1"}
        assert _load_checkpoint_translations("job-b") == {1: "B1", 2: "B2"}

    def test_translated_text_preserves_newlines(self, temp_checkpoint_db):
        from src.celery_app import _save_checkpoint, _load_checkpoint_translations

        multiline = "Line one.\n\nLine two.\nLine three."
        _save_checkpoint("job-ml", 1, multiline, "{}", "s")
        loaded = _load_checkpoint_translations("job-ml")
        assert loaded[1] == multiline


# ═══════════════════════════════════════════════════════════════════
# TranslationProgress — Redis + JobStore integration
# ═══════════════════════════════════════════════════════════════════

class TestTranslationProgress:
    def test_update_calls_job_store(self, temp_checkpoint_db):
        from src.celery_app import TranslationProgress
        import src.celery_app as celery_mod
        import src.job_store as js

        # Set up a real job in the JobStore
        jid = js.job_store.create_job("test.txt", "en-US", 10)

        # Mock the Redis backend so _set doesn't crash
        mock_backend = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(celery_mod, "app", MagicMock())
            celery_mod.app.backend = mock_backend

            progress = TranslationProgress(jid)
            progress.update(3, 10, "Chapter 3")

        # Verify JobStore was updated
        job = js.job_store.get_job(jid)
        assert job["status"] == "translating"
        assert job["completed_chapters"] == 3

        js.job_store.delete_job(jid)

    def test_complete_calls_job_store(self, temp_checkpoint_db):
        from src.celery_app import TranslationProgress
        import src.celery_app as celery_mod
        import src.job_store as js

        jid = js.job_store.create_job("test.txt", "en-US", 5)
        mock_backend = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(celery_mod, "app", MagicMock())
            celery_mod.app.backend = mock_backend
            progress = TranslationProgress(jid)
            progress.complete("/tmp/out.md", 12)

        job = js.job_store.get_job(jid)
        assert job["status"] == "complete"
        assert job["glossary_count"] == 12
        js.job_store.delete_job(jid)

    def test_error_calls_job_store(self, temp_checkpoint_db):
        from src.celery_app import TranslationProgress
        import src.celery_app as celery_mod
        import src.job_store as js

        jid = js.job_store.create_job("test.txt", "en-US", 5)
        mock_backend = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(celery_mod, "app", MagicMock())
            celery_mod.app.backend = mock_backend
            progress = TranslationProgress(jid)
            progress.error("Something broke")

        job = js.job_store.get_job(jid)
        assert job["status"] == "failed"
        assert "Something broke" in job["error_message"]
        js.job_store.delete_job(jid)

    def test_update_survives_job_store_error(self, temp_checkpoint_db):
        """If job_store.update_progress throws, _set still works (non-critical)."""
        from src.celery_app import TranslationProgress
        import src.celery_app as celery_mod
        import src.job_store as js_module

        mock_backend = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(celery_mod, "app", MagicMock())
            celery_mod.app.backend = mock_backend
            # Replace the module-level job_store singleton's update_progress
            mp.setattr(js_module.job_store, "update_progress",
                       MagicMock(side_effect=RuntimeError("DB gone")))
            progress = TranslationProgress("test-id")
            # Must not raise
            progress.update(1, 10, "Ch1")
        # Redis _set should still have been called
        mock_backend.set.assert_called_once()


# ═══════════════════════════════════════════════════════════════════
# Resume: loading checkpoints into a restored agent
# ═══════════════════════════════════════════════════════════════════

class TestResumeCheckpointFlow:
    def test_resume_loads_glossary_snapshot(self, temp_checkpoint_db):
        """Simulate the resume flow: save glossary → restore it into agent."""
        from src.agent.graph import TranslationAgent
        from src.celery_app import _save_checkpoint

        # 1. Translate chapter 1 normally, accumulate terms
        agent = TranslationAgent()
        agent.exact_store.add("苏念", "Su Nian", category="character")
        agent.exact_store.add("霸总", "Alpha CEO", category="culture")
        snapshot = agent.exact_store.snapshot()

        # 2. Save checkpoint
        _save_checkpoint("resume-test", 1, "Translated ch1.", snapshot, "Ch1 summary")

        # 3. Simulate crash — create a new agent and restore snapshot
        agent2 = TranslationAgent()
        agent2.load_glossary_snapshot(snapshot)

        # 4. Verify terms are restored
        assert agent2.exact_store.get("苏念") == "Su Nian"
        assert agent2.exact_store.get("霸总") == "Alpha CEO"

    def test_resume_picks_up_at_correct_chapter(self, temp_checkpoint_db):
        """The resume task reads last completed chapter from checkpoint DB."""
        from src.celery_app import _save_checkpoint, _load_checkpoint_translations

        _save_checkpoint("batch-1", 1, "Ch1 en.", "{}", "s1")
        _save_checkpoint("batch-1", 2, "Ch2 en.", "{}", "s2")
        _save_checkpoint("batch-1", 3, "Ch3 en.", "{}", "s3")

        loaded = _load_checkpoint_translations("batch-1")
        max_idx = max(loaded.keys())  # This is how resume_translate_task works
        assert max_idx == 3
        assert loaded[3] == "Ch3 en."
