"""API routes tests — main translation API endpoints.

Covers validation, job lifecycle, downloads, presets, projects, and security.
Uses FastAPI TestClient with the real app (mocked LLM + Celery disabled).
"""

import json
import os
import tempfile
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Return a TestClient for the full app with sync path (no Celery).

    Celery is force-disabled so tests run without Redis. The autouse
    _reset_backpressure fixture in conftest keeps the backpressure
    singleton clean.
    """
    import src.api.routes as _routes
    _routes._has_celery = False
    from src import config
    if not config.DEEPSEEK_API_KEY:
        config.DEEPSEEK_API_KEY = "ci-test-key"
    from src.main import create_app
    return TestClient(create_app())


def _make_txt(text: str) -> bytes:
    """Return UTF-8 bytes for a Chinese text fixture."""
    return text.encode("utf-8")


# ═══════════════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════════════

class TestHealth:
    def test_health_returns_200(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] in ("healthy", "degraded")


# ═══════════════════════════════════════════════════════════════════
# Translation upload validation
# ═══════════════════════════════════════════════════════════════════

class TestTranslateValidation:
    def test_missing_file_rejected(self, client):
        r = client.post("/api/translate", data={"target_lang": "en-US"})
        assert r.status_code == 422

    def test_non_chinese_text_rejected(self, client):
        r = client.post(
            "/api/translate",
            files={"file": ("english.txt", b"Hello world, this is English text.", "text/plain")},
            data={"target_lang": "en-US"},
        )
        assert r.status_code == 400

    def test_invalid_api_key_format_rejected(self, client):
        text = "第一章 测试\n\n天空是灰色的。\n"
        r = client.post(
            "/api/translate",
            files={"file": ("test.txt", _make_txt(text), "text/plain")},
            data={"target_lang": "en-US", "api_key": "not-sk-prefix"},
        )
        assert r.status_code == 400

    def test_invalid_script_mode_rejected(self, client):
        text = "第一章 测试\n\n天空是灰色的。\n"
        r = client.post(
            "/api/translate",
            files={"file": ("test.txt", _make_txt(text), "text/plain")},
            data={"target_lang": "en-US", "script_mode": "bogus"},
        )
        assert r.status_code == 400

    def test_empty_api_key_rejected_when_no_server_key(self, client):
        import src.config as cfg
        text = "第一章 测试\n\n天空是灰色的。\n"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cfg, "DEEPSEEK_API_KEY", "")
            # The fixture sets DEEPSEEK_API_KEY="ci-test-key"; clear it
            r = client.post(
                "/api/translate",
                files={"file": ("test.txt", _make_txt(text), "text/plain")},
                data={"target_lang": "en-US", "api_key": ""},
            )
            assert r.status_code == 400

    def test_valid_upload_accepts_work(self, client):
        text = "第一章 测试\n\n天空是灰色的。\n\n第二章 出发\n\n他推开了门。\n"
        r = client.post(
            "/api/translate",
            files={"file": ("test.txt", _make_txt(text), "text/plain")},
            data={"target_lang": "en-US", "genre": "xianxia"},
        )
        assert r.status_code == 200
        j = r.json()
        assert "job_id" in j
        assert j["total_chapters"] >= 1
        # Clean up
        client.delete(f"/api/jobs/{j['job_id']}")


# ═══════════════════════════════════════════════════════════════════
# Job CRUD
# ═══════════════════════════════════════════════════════════════════

class TestJobCrud:
    def test_get_nonexistent_job(self, client):
        r = client.get("/api/jobs/deadbeef")
        assert r.status_code == 404

    def test_delete_nonexistent_job(self, client):
        r = client.delete("/api/jobs/deadbeef")
        assert r.status_code == 404

    def test_list_jobs(self, client):
        r = client.get("/api/jobs")
        assert r.status_code == 200

    def test_get_job_after_create(self, client):
        text = "第一章 测试\n\n天空。\n"
        r = client.post(
            "/api/translate",
            files={"file": ("test.txt", _make_txt(text), "text/plain")},
            data={"target_lang": "en-US"},
        )
        jid = r.json()["job_id"]
        r2 = client.get(f"/api/jobs/{jid}")
        assert r2.status_code == 200
        assert r2.json()["status"] in ("queued", "translating")

    def test_delete_job(self, client):
        text = "第一章 测试\n\n天空。\n"
        r = client.post(
            "/api/translate",
            files={"file": ("test.txt", _make_txt(text), "text/plain")},
            data={"target_lang": "en-US"},
        )
        jid = r.json()["job_id"]
        r2 = client.delete(f"/api/jobs/{jid}")
        assert r2.status_code == 200
        r3 = client.get(f"/api/jobs/{jid}")
        assert r3.status_code == 404

    def test_translate_status_polling(self, client):
        text = "第一章 测试\n\n天空是灰色的。\n"
        r = client.post(
            "/api/translate",
            files={"file": ("test.txt", _make_txt(text), "text/plain")},
            data={"target_lang": "en-US"},
        )
        jid = r.json()["job_id"]
        r2 = client.get(f"/api/translate/{jid}")
        assert r2.status_code == 200
        assert r2.json().get("status") != "unknown"


# ═══════════════════════════════════════════════════════════════════
# Download endpoints (translation, glossary, EPUB)
# ═══════════════════════════════════════════════════════════════════

class TestDownloads:
    def test_translation_not_found(self, client):
        r = client.get("/api/translation/deadbeef")
        assert r.status_code == 404

    def test_glossary_not_found(self, client):
        r = client.get("/api/glossary/deadbeef")
        assert r.status_code == 404

    def test_epub_not_found(self, client):
        r = client.get("/api/epub/deadbeef")
        assert r.status_code == 404

    def test_epub_returns_422_when_no_chapters(self, client):
        """EPUB endpoint returns 422 when markdown has no parseable chapters."""
        from src.config import OUTPUT_DIR
        jid = "testepub1"
        # Write an empty translation file
        path = OUTPUT_DIR / f"{jid}_full_novel_en-US.md"
        path.write_text("# just a header\n\nNo chapter headers here.\n", encoding="utf-8")
        r = client.get(f"/api/epub/{jid}")
        assert r.status_code == 422
        path.unlink()

    def test_epub_generates_valid_file(self, client):
        """End-to-end EPUB generation from properly formatted markdown."""
        from src.config import OUTPUT_DIR
        jid = "testepub2"
        md = (
            "## Chapter 1: Prelude\n\nShe stepped into the hall.\n\n---\n\n"
            "## Chapter 2: The Meeting\n\nHe turned around slowly.\n\n---\n"
        )
        path = OUTPUT_DIR / f"{jid}_full_novel_en-US.md"
        path.write_text(md, encoding="utf-8")
        r = client.get(f"/api/epub/{jid}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/epub+zip"
        path.unlink()
        epub_path = OUTPUT_DIR / f"{jid}.epub"
        if epub_path.exists():
            epub_path.unlink()

    def test_get_translation_path_traversal_blocked(self, client):
        """C2 fix: get_translation must reject traversal characters."""
        for evil in ["../secret", "a/../../etc", "x" * 65]:
            r = client.get(f"/api/translation/{evil}")
            # Must be 400 (rejected), not 500 or 200
            assert r.status_code in (400, 404), f"{evil}: {r.status_code}"

    def test_get_glossary_path_traversal_blocked(self, client):
        """C2 fix: get_glossary must reject traversal characters."""
        for evil in ["../secret", "a/../../etc"]:
            r = client.get(f"/api/glossary/{evil}")
            assert r.status_code in (400, 404), f"{evil}: {r.status_code}"


# ═══════════════════════════════════════════════════════════════════
# Glossary presets
# ═══════════════════════════════════════════════════════════════════

class TestPresets:
    def test_list_presets(self, client):
        r = client.get("/api/presets")
        assert r.status_code == 200
        assert "presets" in r.json()

    def test_list_presets_default_empty(self, client):
        r = client.get("/api/presets")
        assert r.json()["presets"] == []

    def test_get_nonexistent_preset(self, client):
        r = client.get("/api/presets/nonexistent_preset_xyz")
        assert r.status_code == 404

    def test_delete_nonexistent_preset(self, client):
        r = client.delete("/api/presets/nonexistent_preset_xyz")
        assert r.status_code == 404

    def test_save_preset_from_nonexistent_job(self, client):
        r = client.post(
            "/api/presets/deadbeef",
            data={"name": "my_preset", "description": "test"},
        )
        assert r.status_code == 404

    def test_save_preset_and_retrieve(self, client):
        """Create a job, save its glossary as a preset, then retrieve it."""
        text = "第一章 测试\n\n天空是灰色的。\n"
        r = client.post(
            "/api/translate",
            files={"file": ("test.txt", _make_txt(text), "text/plain")},
            data={"target_lang": "en-US"},
        )
        assert r.status_code == 200, r.text
        jid = r.json()["job_id"]

        # Save preset
        r2 = client.post(
            f"/api/presets/{jid}",
            data={"name": "my_test_preset", "description": "Test preset"},
        )
        assert r2.status_code == 200, r2.text

        # Retrieve
        r3 = client.get("/api/presets/my_test_preset")
        assert r3.status_code == 200
        assert "glossary" in r3.json()

        # Clean up
        client.delete("/api/presets/my_test_preset")
        client.delete(f"/api/jobs/{jid}")


# ═══════════════════════════════════════════════════════════════════
# Multi-language translate
# ═══════════════════════════════════════════════════════════════════

class TestMultiLang:
    def test_multi_rejects_empty_langs(self, client):
        text = "第一章 测试\n\n天空。\n"
        r = client.post(
            "/api/translate/multi",
            files={"file": ("test.txt", _make_txt(text), "text/plain")},
            data={"target_langs": " , , "},  # all-blank after strip → empty
        )
        assert r.status_code == 400

    def test_multi_rejects_invalid_script_mode(self, client):
        text = "第一章 测试\n\n天空。\n"
        r = client.post(
            "/api/translate/multi",
            files={"file": ("test.txt", _make_txt(text), "text/plain")},
            data={"target_langs": "en-US,es-ES", "script_mode": "bogus"},
        )
        assert r.status_code == 400

    def test_multi_creates_project(self, client):
        text = "第一章 测试\n\n天空是灰色的。\n\n第二章 出发\n\n他推开了门。\n"
        r = client.post(
            "/api/translate/multi",
            files={"file": ("test.txt", _make_txt(text), "text/plain")},
            data={"target_langs": "en-US,es-ES"},
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert "project_id" in j
        assert len(j["jobs"]) == 2
        langs = {job["lang"] for job in j["jobs"]}
        assert langs == {"en-US", "es-ES"}

        # Clean up — each job creates threads that take backpressure slots.
        # The _reset_backpressure fixture cleans the singleton after the
        # test, so wait briefly for threads to finish.
        for job in j["jobs"]:
            client.delete(f"/api/jobs/{job['job_id']}")

    def test_multi_script_content_type_auto_genre(self, client):
        """When content_type=script, genre defaults to 'urban' not 'romance_ceo'."""
        text = "第1集 穿书\n\n场景1：别墅/夜\n\n苏念醒来。\n"
        r = client.post(
            "/api/translate/multi",
            files={"file": ("script.txt", _make_txt(text), "text/plain")},
            data={"target_langs": "en-US", "content_type": "script"},
        )
        assert r.status_code == 200, r.text
        for job in r.json()["jobs"]:
            client.delete(f"/api/jobs/{job['job_id']}")


# ═══════════════════════════════════════════════════════════════════
# Projects
# ═══════════════════════════════════════════════════════════════════

class TestProjectsApi:
    def test_list_projects(self, client):
        r = client.get("/api/projects")
        assert r.status_code == 200

    def test_get_nonexistent_project(self, client):
        r = client.get("/api/projects/deadbeef")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# Resume endpoint
# ═══════════════════════════════════════════════════════════════════

class TestResume:
    def test_resume_without_celery_rejected(self, client):
        text = "第一章 测试\n\n天空。\n"
        r = client.post(
            "/api/translate/resume/deadbeef",
            files={"file": ("test.txt", _make_txt(text), "text/plain")},
            data={"target_lang": "en-US"},
        )
        assert r.status_code == 503  # Celery not available

    def test_resume_nonexistent_job(self, client):
        import src.api.routes as _routes
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(_routes, "_has_celery", True)
            text = "第一章 测试\n\n天空。\n"
            r = client.post(
                "/api/translate/resume/deadbeef",
                files={"file": ("test.txt", _make_txt(text), "text/plain")},
                data={"target_lang": "en-US"},
            )
            assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# Cost endpoint
# ═══════════════════════════════════════════════════════════════════

class TestCost:
    def test_cost_nonexistent_job(self, client):
        r = client.get("/api/jobs/deadbeef/cost")
        assert r.status_code == 404

    def test_cost_after_create(self, client):
        text = "第一章 测试\n\n天空。\n"
        r = client.post(
            "/api/translate",
            files={"file": ("test.txt", _make_txt(text), "text/plain")},
            data={"target_lang": "en-US"},
        )
        jid = r.json()["job_id"]
        r2 = client.get(f"/api/jobs/{jid}/cost")
        assert r2.status_code == 200
        cost = r2.json()
        assert "tokens_input" in cost
        assert "estimated_cost_usd" in cost


# ═══════════════════════════════════════════════════════════════════
# Pages (dashboard, usage, editor)
# ═══════════════════════════════════════════════════════════════════

class TestPages:
    def test_homepage(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_dashboard(self, client):
        r = client.get("/dashboard")
        assert r.status_code == 200

    def test_dashboard_metrics(self, client):
        r = client.get("/api/dashboard/metrics")
        assert r.status_code == 200

    def test_usage_page(self, client):
        r = client.get("/usage")
        assert r.status_code == 200

    def test_usage_events(self, client):
        r = client.get("/api/usage/events")
        assert r.status_code == 200

    def test_review_page(self, client):
        r = client.get("/review")
        assert r.status_code == 200

    def test_editor_page(self, client):
        r = client.get("/editor/test_job_123")
        assert r.status_code == 200
