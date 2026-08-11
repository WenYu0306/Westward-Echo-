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

    def test_create_job_with_content_type_script(self, store):
        jid = store.create_job("script.txt", "en-US", 10,
                               content_type="script", script_mode="dialogue")
        job = store.get_job(jid)
        assert job["content_type"] == "script"
        assert job["script_mode"] == "dialogue"

    def test_create_job_with_project_id(self, store):
        jid = store.create_job("test.txt", "en-US", 5, project_id="proj01")
        job = store.get_job(jid)
        assert job["project_id"] == "proj01"

    def test_create_job_defaults(self, store):
        jid = store.create_job("x.txt", "en-US", 1)
        job = store.get_job(jid)
        assert job["content_type"] == "novel"
        assert job["script_mode"] == "full"
        assert job["completed_chapters"] == 0
        assert job["tokens_input"] == 0
        assert job["tokens_output"] == 0


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

    def test_get_incomplete_jobs(self, store):
        jid = store.create_job("test.txt", "en-US", 10)
        store.update_progress(jid, 3, 10, "ch3")
        incomplete = store.get_incomplete_jobs()
        assert any(j["job_id"] == jid for j in incomplete)


# ── Token tracking ────────────────────────────────────────────────

class TestTokenTracking:
    def test_update_token_usage(self, store):
        jid = store.create_job("test.txt", "en-US", 10)
        store.update_token_usage(jid, 1000, 500)
        store.update_token_usage(jid, 2000, 300)
        cost = store.get_job_cost(jid)
        assert cost["tokens_input"] == 3000
        assert cost["tokens_output"] == 800
        assert cost["total"] == 3800

    def test_get_job_cost_nonexistent(self, store):
        cost = store.get_job_cost("deadbeef")
        assert cost["total"] == 0
        assert cost["estimated_cost_usd"] == 0.0

    def test_get_job_cost_calculation(self, store):
        jid = store.create_job("test.txt", "en-US", 1)
        store.update_token_usage(jid, 1_000_000, 1_000_000)
        cost = store.get_job_cost(jid)
        # Flash pricing: $0.14/M in, $0.28/M out
        assert cost["estimated_cost_usd"] == pytest.approx(0.42, rel=0.01)


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
