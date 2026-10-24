"""Unit tests for job_store.py — SQLite-backed job persistence."""

import json
import os
import tempfile

import pytest

# Patch DB_PATH before importing job_store so it uses a temp file instead of
# the real data/jobs.db in the project directory.
_tempdir = tempfile.mkdtemp()
_temp_db = os.path.join(_tempdir, "test_jobs.db")

with pytest.MonkeyPatch.context() as _mp:
    _mp.setattr("src.job_store.DB_PATH", _temp_db)
    from src.job_store import JobStore, job_store


@pytest.fixture
def store():
    """Return a fresh JobStore instance pointing at a temp database."""
    # Reset the module-level connection so it picks up the patched DB_PATH
    import src.job_store as js
    js._local.conn = None
    s = JobStore()
    # Clear tables for isolation
    conn = js._get_conn()
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM glossary_presets")
    conn.execute("DELETE FROM rejected_terms")
    conn.commit()
    return s


# ── Job CRUD ──────────────────────────────────────────────────────

class TestJobCreate:
    def test_create_job_returns_eight_char_id(self, store):
        jid = store.create_job("test.txt", "en-US", 100)
        assert len(jid) == 8
        assert jid.isalnum()

    def test_create_job_persists(self, store):
        jid = store.create_job("test.txt", "en-US", 100)
        job = store.get_job(jid)
        assert job is not None
        assert job["filename"] == "test.txt"
        assert job["target_lang"] == "en-US"
        assert job["total_chapters"] == 100
        assert job["status"] == "queued"

    def test_create_job_with_project_id(self, store):
        jid = store.create_job("test.txt", "en-US", 5, project_id="proj01")
        job = store.get_job(jid)
        assert job["project_id"] == "proj01"

    def test_create_job_defaults(self, store):
        jid = store.create_job("x.txt", "en-US", 1)
        job = store.get_job(jid)
        assert job["content_type"] == "novel"
        assert job["completed_chapters"] == 0
        assert "tokens_input" not in job
        assert "tokens_output" not in job


class TestJobUpdate:
    def test_update_progress_sets_translating(self, store):
        jid = store.create_job("test.txt", "en-US", 50)
        store.update_progress(jid, 5, 50, "Chapter 5")
        job = store.get_job(jid)
        assert job["status"] == "translating"
        assert job["completed_chapters"] == 5
        assert job["current_chapter_title"] == "Chapter 5"

    def test_complete_job(self, store):
        jid = store.create_job("test.txt", "en-US", 10)
        store.complete_job(jid, "/tmp/output.md", 42)
        job = store.get_job(jid)
        assert job["status"] == "complete"
        assert job["output_path"] == "/tmp/output.md"
        assert job["glossary_count"] == 42
        assert job["completed_at"] is not None

    def test_fail_job(self, store):
        jid = store.create_job("test.txt", "en-US", 10)
        store.fail_job(jid, "Something broke")
        job = store.get_job(jid)
        assert job["status"] == "failed"
        assert job["error_message"] == "Something broke"
        assert job["completed_at"] is not None


class TestJobQuery:
    def test_get_job_nonexistent(self, store):
        assert store.get_job("deadbeef") is None

    def test_list_jobs_orders_newest_first(self, store):
        a = store.create_job("a.txt", "en-US", 1)
        import time
        time.sleep(1.1)  # created_at has second granularity
        b = store.create_job("b.txt", "en-US", 1)
        jobs = store.list_jobs(limit=10)
        ids = [j["job_id"] for j in jobs]
        assert ids[0] == b
        assert ids[1] == a

    def test_list_jobs_respects_limit(self, store):
        for _ in range(10):
            store.create_job("x.txt", "en-US", 1)
        jobs = store.list_jobs(limit=3)
        assert len(jobs) == 3

    def test_delete_job(self, store):
        jid = store.create_job("test.txt", "en-US", 1)
        store.delete_job(jid)
        assert store.get_job(jid) is None

    def test_delete_nonexistent_job_no_error(self, store):
        store.delete_job("deadbeef")  # must not raise
        assert store.get_job("deadbeef") is None  # idempotent: still absent

    def test_get_incomplete_jobs(self, store):
        jid = store.create_job("test.txt", "en-US", 10)
        store.update_progress(jid, 3, 10, "ch3")
        incomplete = store.get_incomplete_jobs()
        assert any(j["job_id"] == jid for j in incomplete)


# ── Token tracking ────────────────────────────────────────────────

class TestTokenColumnsAreDropped:
    """The jobs table's dead token columns are gone.

    update_token_usage() and get_job_cost() were deleted: the writer had zero
    callers, so both columns were permanently 0, and the reader priced those
    zeros at DeepSeek's abandoned USD rates. The columns were then dropped via
    the migration framework. These tests pin that they stay gone — real per-job
    cost comes from token_ledger.summary(), exercised by tests/test_token_ledger.py
    and the /jobs/{id}/cost endpoint test.
    """

    def test_new_job_has_no_token_columns(self, store):
        jid = store.create_job("test.txt", "en-US", 10)
        job = store.get_job(jid)
        assert "tokens_input" not in job
        assert "tokens_output" not in job

    def test_complete_job_still_has_no_token_columns(self, store):
        jid = store.create_job("test.txt", "en-US", 1)
        store.complete_job(jid, "/tmp/out.md", 5)
        job = store.get_job(jid)
        assert job["status"] == "complete"
        assert "tokens_input" not in job
        assert "tokens_output" not in job

    def test_no_token_writer_method_remains(self, store):
        """Deleting the writer is only durable if it stays deleted."""
        assert not hasattr(store, "update_token_usage")

    def test_no_deepseek_priced_cost_method_remains(self, store):
        assert not hasattr(store, "get_job_cost")


# ── Glossary presets ─────────────────────────────────────────────

class TestGlossaryPresets:
    def test_save_and_load_preset(self, store):
        jid = store.create_job("test.txt", "en-US", 1)
        glossary = {"苏念": "Su Nian", "霸总": "Alpha CEO"}
        store.save_glossary_as_preset(
            jid, "my_preset", "Test preset",
            json.dumps(glossary, ensure_ascii=False),
        )
        loaded = store.load_glossary_preset("my_preset")
        assert loaded == glossary

    def test_load_nonexistent_preset(self, store):
        assert store.load_glossary_preset("nope") == {}

    def test_list_presets(self, store):
        jid = store.create_job("test.txt", "en-US", 1)
        store.save_glossary_as_preset(jid, "preset_a", "A", "{}")
        store.save_glossary_as_preset(jid, "preset_b", "B", "{}")
        presets = store.list_glossary_presets()
        names = [p["preset_name"] for p in presets]
        assert "preset_a" in names
        assert "preset_b" in names

    def test_delete_preset(self, store):
        jid = store.create_job("test.txt", "en-US", 1)
        store.save_glossary_as_preset(jid, "to_delete", "", "{}")
        store.delete_glossary_preset("to_delete")
        assert store.load_glossary_preset("to_delete") == {}

    def test_overwrite_preset(self, store):
        jid = store.create_job("test.txt", "en-US", 1)
        store.save_glossary_as_preset(jid, "p", "", '{"a":"b"}')
        store.save_glossary_as_preset(jid, "p", "", '{"c":"d"}')
        loaded = store.load_glossary_preset("p")
        assert loaded == {"c": "d"}

    def test_load_preset_raw(self, store):
        jid = store.create_job("test.txt", "en-US", 1)
        store.save_glossary_as_preset(jid, "raw_test", "", '{"key":"val"}')
        raw = store.load_glossary_preset_raw("raw_test")
        assert raw == '{"key":"val"}'

    def test_load_preset_raw_nonexistent(self, store):
        assert store.load_glossary_preset_raw("nope") is None

    def test_load_preset_invalid_json(self, store):
        jid = store.create_job("test.txt", "en-US", 1)
        store.save_glossary_as_preset(jid, "broken", "", "not json")
        assert store.load_glossary_preset("broken") == {}


# ── Projects / multi-language ─────────────────────────────────────

class TestProjects:
    def test_create_project(self, store):
        pid = store.create_project("novel.txt")
        assert len(pid) == 8

    def test_add_language_job(self, store):
        pid = store.create_project("novel.txt")
        jid = store.add_language_job(pid, "es-ES", "novel.txt", 100)
        job = store.get_job(jid)
        assert job["project_id"] == pid
        assert job["target_lang"] == "es-ES"

    def test_get_project_jobs(self, store):
        pid = store.create_project("novel.txt")
        j1 = store.add_language_job(pid, "en-US", "novel.txt", 100)
        j2 = store.add_language_job(pid, "es-ES", "novel.txt", 100)
        jobs = store.get_project_jobs(pid)
        assert len(jobs) == 2
        langs = {j["target_lang"] for j in jobs}
        assert langs == {"en-US", "es-ES"}

    def test_list_projects(self, store):
        pid = store.create_project("novel.txt")
        store.add_language_job(pid, "en-US", "novel.txt", 10)
        projects = store.list_projects(limit=10)
        assert any(p["project_id"] == pid for p in projects)

    def test_get_project_jobs_empty(self, store):
        assert store.get_project_jobs("nope") == []


# ── Rejected terms ────────────────────────────────────────────────

class TestRejectedTerms:
    def test_reject_and_retrieve(self, store):
        store.reject_term_with_feedback("霸总", "CEO", "en-US")
        terms = store.get_rejected_terms("en-US")
        assert len(terms) == 1
        assert terms[0]["term_cn"] == "霸总"
        assert terms[0]["rejected_en"] == "CEO"

    def test_reject_overwrites_same_rejected_en(self, store):
        """Rejecting same (cn, en, lang) overwrites the timestamp."""
        store.reject_term_with_feedback("霸总", "CEO", "en-US")
        store.reject_term_with_feedback("霸总", "CEO", "en-US")
        terms = store.get_rejected_terms("en-US")
        assert len([t for t in terms if t["term_cn"] == "霸总"]) == 1

    def test_reject_different_en_creates_separate_row(self, store):
        """Different rejected translations for same CN term are distinct rows."""
        store.reject_term_with_feedback("霸总", "CEO", "en-US")
        store.reject_term_with_feedback("霸总", "Alpha CEO", "en-US")
        terms = store.get_rejected_terms("en-US")
        assert len([t for t in terms if t["term_cn"] == "霸总"]) == 2

    def test_rejected_terms_per_language(self, store):
        store.reject_term_with_feedback("霸总", "CEO", "en-US")
        store.reject_term_with_feedback("霸总", "PDG", "es-ES")
        assert len(store.get_rejected_terms("en-US")) == 1
        assert len(store.get_rejected_terms("es-ES")) == 1
        assert len(store.get_rejected_terms("de")) == 0


# ── Default listing limits ────────────────────────────────────────

class TestDefaultListingLimits:
    """The default limits are the dashboard's paging contract."""

    def test_list_jobs_defaults_to_fifty(self, store):
        for i in range(51):
            store.create_job(f"f{i}.txt", "en-US", 1)
        assert len(store.list_jobs()) == 50

    def test_list_projects_defaults_to_twenty(self, store):
        # A project only appears once it has a language job.
        for i in range(21):
            pid = store.create_project(f"p{i}.txt")
            store.add_language_job(pid, "en-US", f"p{i}.txt", 1)
        assert len(store.list_projects()) == 20

    def test_explicit_limit_still_wins(self, store):
        for i in range(5):
            store.create_job(f"g{i}.txt", "en-US", 1)
        assert len(store.list_jobs(limit=2)) == 2


# ── Confirmed terms (human-approved glossary) ─────────────────────

class TestConfirmedTerms:
    """get_confirmed_terms feeds the never-overwrite glossary contract."""

    def test_returns_only_confirmed_terms(self, store, tmp_path, monkeypatch):
        import src.config as config
        from src.glossary.exact_store import ExactGlossary

        db = tmp_path / "ckpt.db"
        monkeypatch.setattr(config, "CHECKPOINT_DB_PATH", str(db))

        eg = ExactGlossary(db_path=str(db), book_id="confirmed-terms-test")
        eg.add("聋婆婆", "Deaf Granny", category="character")
        eg.add("王三", "Wang San", category="character")
        eg.confirm_term("聋婆婆", target_lang="en-US")

        confirmed = store.get_confirmed_terms("en-US", book_id="confirmed-terms-test")

        assert confirmed == {"聋婆婆": "Deaf Granny"}

    def test_returns_empty_dict_when_nothing_confirmed(self, store, tmp_path, monkeypatch):
        import src.config as config
        from src.glossary.exact_store import ExactGlossary

        db = tmp_path / "ckpt2.db"
        monkeypatch.setattr(config, "CHECKPOINT_DB_PATH", str(db))
        ExactGlossary(db_path=str(db), book_id="confirmed-terms-empty").add(
            "王三", "Wang San"
        )

        assert store.get_confirmed_terms("en-US", book_id="confirmed-terms-empty") == {}

    def test_returns_empty_dict_when_glossary_table_absent(self, store, tmp_path, monkeypatch):
        """The branch the two tests above never reach.

        Both existing cases construct an ExactGlossary first, which CREATES the
        exact_glossary table — so they exercise "table exists, nothing confirmed"
        and never "table does not exist at all". That missing-table branch is the
        one the docstring says matters most: write_node calls this before any
        ExactGlossary is guaranteed to exist, and the old code raised
        sqlite3.OperationalError there and failed the whole chapter.

        Pointing at a database that has no such table forces the branch. A fresh
        sqlite3.connect creates an empty file, so sqlite_master has no rows and
        `exists` is None. Mutation testing found `return {}` -> `return None`
        survived here; asserting the exact empty dict (not merely falsiness) is
        what kills it, since None is falsy too and would pass an `== {}` check
        written as `not result`.
        """
        import src.config as config

        db = tmp_path / "no_glossary_table.db"
        monkeypatch.setattr(config, "CHECKPOINT_DB_PATH", str(db))

        result = store.get_confirmed_terms("en-US")

        assert result == {}
        assert isinstance(result, dict)


# ── Connection bootstrap ──────────────────────────────────────────

class TestConnectionBootstrap:
    def test_missing_parent_directories_are_created(self, tmp_path, monkeypatch):
        """A first run must create its own nesting, not crash on mkdir."""
        import src.job_store as js

        nested = tmp_path / "deep" / "nested" / "jobs.db"
        monkeypatch.setattr(js, "DB_PATH", str(nested))
        monkeypatch.setattr(js._local, "conn", None)

        assert js.JobStore().create_job("boot.txt", "en-US", 1)
        assert nested.exists()

    def test_connection_created_in_one_thread_is_usable_in_another(
        self, tmp_path, monkeypatch
    ):
        """``check_same_thread=False`` is load-bearing, not cargo-cult.

        Connections are handed out through ``threading.local()``, so in the
        current call graph no connection ever crosses a thread boundary and the
        flag looks removable. It is not: flipping it to the default ``True``
        makes sqlite3 raise ``ProgrammingError`` the moment a connection built
        on one thread is used on another, and nothing else in the suite
        exercises that path — which is exactly why ``const bool->True at 26``
        survived mutation testing.

        This test pins the capability the flag buys. The module deliberately
        allows a connection to be used off its creating thread; if that ever
        stops being true, this test should fail and the flag should go with it.
        """
        import threading

        import src.job_store as js

        monkeypatch.setattr(js, "DB_PATH", str(tmp_path / "cross_thread.db"))
        monkeypatch.setattr(js._local, "conn", None, raising=False)

        conn = js._get_conn()  # created on THIS thread
        errors: dict = {}

        def worker():
            try:
                conn.execute("SELECT 1").fetchone()
            except Exception as exc:  # noqa: BLE001 - recorded, asserted below
                errors["exc"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        try:
            assert "exc" not in errors, (
                f"connection could not be used from another thread: {errors.get('exc')!r}"
            )
        finally:
            monkeypatch.setattr(js._local, "conn", None, raising=False)
            conn.close()
