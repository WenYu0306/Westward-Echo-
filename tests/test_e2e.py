"""End-to-end test: upload → translate → download. Uses FastAPI TestClient.

Run with:  pytest tests/test_e2e.py -v -k "not requires_api_key"
or:        python3 tests/test_e2e.py
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from src.api import routes as _api_routes  # noqa: I001

# Force sync path in CI — Celery imports but no Redis available
_api_routes._has_celery = False


@pytest.fixture
def client():
    from src import config
    if not config.DEEPSEEK_API_KEY:
        config.DEEPSEEK_API_KEY = "ci-test-key"
    from src.main import create_app

    return TestClient(create_app())


@pytest.fixture
def novel_txt():
    content = """第一章 穿越

天空是灰色的。

李明站在窗边，手里端着一杯咖啡。他穿越到这个世界已经三个月了。

"该出去赚点灵石了。"他推开了门。

第二章 坊市

坊市里人声鼎沸。

李明摸了摸口袋里仅剩的五块灵石，叹了口气。
"""
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "test_novel.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    yield path
    os.remove(path)
    os.rmdir(tmpdir)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] in ("healthy", "degraded")


@pytest.mark.requires_api_key
def test_upload_and_poll(client, novel_txt):
    with open(novel_txt, "rb") as f:
        r = client.post(
            "/api/translate",
            files={"file": ("test.txt", f, "text/plain")},
            data={"target_lang": "en-US", "genre": "xianxia", "translate_mode": "fast"},
        )
    assert r.status_code == 200
    j = r.json()
    assert "job_id" in j
    assert j["total_chapters"] == 2
    assert j["status"] in ("translating", "queued")

    job_id = j["job_id"]

    # Polling should return valid status (not 'unknown')
    for _ in range(5):
        r = client.get(f"/api/translate/{job_id}")
        assert r.status_code == 200
        status = r.json()
        assert status.get("status") != "unknown", "Expected real status, got unknown"
        if status.get("status") == "complete":
            break
        time.sleep(1)

    # Clean up
    client.delete(f"/api/jobs/{job_id}")


def test_script_upload_splits_by_episode(client):
    """content_type=script must split by episodes and route to the script branch.

    LLM calls are mocked so no API quota is consumed.
    """
    from unittest.mock import MagicMock, patch

    script_content = """第1集 穿书

场景1：裴家别墅-主卧/夜

苏念醒来，发现自己躺在陌生的大床上。
【系统绑定成功，当前好感度：-50】

第2集 契约

场景1：裴家客厅/清晨

裴衍舟递出一份契约。苏念扫了一眼，笑了。
"""
    read_out = json.dumps({
        "emotional_arc": "Hook and reversal.",
        "cultural_gaps": [],
        "crafted_moments": [],
        "image_gaps": [],
        "pacing_notes": "",
        "terminology_decisions": [],
    })
    write_out = json.dumps({
        "translated_text": "Su Nian wakes up in the penthouse and sees the system panel. " * 12,
        "chapter_title_en": "Transmigrated",
        "new_terms_found": [],
        "adaptation_notes": [],
        "chapter_summary": "Su Nian transmigrates.",
    })

    def _mock_llm(out):
        resp = MagicMock()
        resp.content = out
        resp.response_metadata = {}
        llm = MagicMock()
        llm.invoke.return_value = resp
        return llm

    with patch("src.agent.nodes.read.ChatOpenAI", return_value=_mock_llm(read_out)), \
         patch("src.agent.nodes.write.ChatOpenAI", return_value=_mock_llm(write_out)), \
         patch("src.agent.nodes.readback.ChatOpenAI", return_value=_mock_llm(read_out)), \
         patch("src.agent.nodes.fix.ChatOpenAI", return_value=_mock_llm(write_out)):

        r = client.post(
            "/api/translate",
            files={"file": ("script.txt", script_content.encode("utf-8"), "text/plain")},
            data={"target_lang": "en-US", "genre": "romance_ceo", "content_type": "script"},
        )
        assert r.status_code == 200
        j = r.json()
        assert "job_id" in j
        # Two episodes, split by the script splitter (not chapter splitter)
        assert j["total_chapters"] == 2
        assert j["status"] in ("translating", "queued")

        job_id = j["job_id"]

        # Job record must carry the content type
        from src.job_store import job_store
        job = job_store.get_job(job_id)
        assert job["content_type"] == "script"

        # Wait for the sync thread to finish (mocked LLM is fast)
        for _ in range(40):
            r = client.get(f"/api/translate/{job_id}")
            assert r.status_code == 200
            status = r.json()
            if status.get("status") in ("complete", "error"):
                break
            time.sleep(0.5)
        assert status.get("status") == "complete", f"Job did not complete: {status}"

        client.delete(f"/api/jobs/{job_id}")


def test_script_dialogue_mode_filters_output(client):
    """content_type=script + script_mode=dialogue delivers spoken lines only.

    The full pipeline runs (mocked LLM returns a complete script with
    action lines and panels); the deliverable must be dialogue-only.
    """
    from unittest.mock import MagicMock, patch

    script_content = """第1集 测试

场景1：裴家客厅/夜

苏念走进客厅，灯光昏暗。裴衍舟坐在沙发上，手里转着一支钢笔。

苏念：我们谈谈。

裴衍舟：谈什么？

苏念：谈谈我们的婚姻。

裴衍舟冷笑一声，把钢笔拍在茶几上。

【系统提示：好感度 +10】

苏念（内心OS）：他生气了。很好，这正是我要的。
"""
    read_out = json.dumps({
        "emotional_arc": "Confrontation.",
        "cultural_gaps": [],
        "crafted_moments": [],
        "image_gaps": [],
        "pacing_notes": "",
        "terminology_decisions": [],
    })
    full_script_out = json.dumps({
        "translated_text": (
            "Episode 1: The Talk\n\n"
            "Scene 1: PEI LIVING ROOM / NIGHT\n\n"
            "Su Nian walks in. The lights are low. Pei Yanzhou sits on the sofa, "
            "turning a fountain pen between his fingers.\n\n"
            "SU NIAN: We need to talk.\n\n"
            "PEI YANZHOU: About what?\n\n"
            "SU NIAN: About our marriage.\n\n"
            "Pei Yanzhou gives a cold laugh and slaps the pen down on the table.\n\n"
            "【System: Affection +10】\n\n"
            "SU NIAN (OS): He's angry. Good. That's exactly what I wanted.\n"
        ),
        "chapter_title_en": "The Talk",
        "new_terms_found": [],
        "adaptation_notes": [],
        "chapter_summary": "Su Nian confronts him.",
    })

    def _mock_llm(out):
        resp = MagicMock()
        resp.content = out
        resp.response_metadata = {}
        llm = MagicMock()
        llm.invoke.return_value = resp
        return llm

    with patch("src.agent.nodes.read.ChatOpenAI", return_value=_mock_llm(read_out)), \
         patch("src.agent.nodes.write.ChatOpenAI", return_value=_mock_llm(full_script_out)), \
         patch("src.agent.nodes.readback.ChatOpenAI", return_value=_mock_llm(read_out)), \
         patch("src.agent.nodes.fix.ChatOpenAI", return_value=_mock_llm(full_script_out)):

        r = client.post(
            "/api/translate",
            files={"file": ("script.txt", script_content.encode("utf-8"), "text/plain")},
            data={
                "target_lang": "en-US", "genre": "romance_ceo",
                "content_type": "script", "script_mode": "dialogue",
            },
        )
        assert r.status_code == 200, r.text
        j = r.json()
        job_id = j["job_id"]

        from src.job_store import job_store
        job = job_store.get_job(job_id)
        assert job["script_mode"] == "dialogue"

        for _ in range(40):
            r = client.get(f"/api/translate/{job_id}")
            assert r.status_code == 200
            status = r.json()
            if status.get("status") in ("complete", "error", "failed"):
                break
            time.sleep(0.5)
        assert status.get("status") == "complete", f"Job did not complete: {status}"

        # The deliverable must be dialogue-only (re-fetch: output_path is
        # only set when the job completes)
        job = job_store.get_job(job_id)
        out_md = job["output_path"]
        text = open(out_md, encoding="utf-8").read()
        assert "SU NIAN: We need to talk." in text
        assert "Scene 1: PEI LIVING ROOM / NIGHT" in text
        assert "walks in" not in text          # action line dropped
        assert "【" not in text                 # panel dropped

        client.delete(f"/api/jobs/{job_id}")


def test_bad_requests_do_not_leak_backpressure(client):
    """Rejected requests (400) must not consume a queue slot.

    Regression: try_accept() used to run before validation, so every bad
    upload permanently leaked one slot; 100 bad requests bricked the service.
    """
    from src.backpressure import backpressure

    before = backpressure.queue_depth
    novel_bytes = "第一章 测试\n\n内容。\n".encode()

    # Bad API key format → 400
    r = client.post(
        "/api/translate",
        files={"file": ("t.txt", novel_bytes, "text/plain")},
        data={"target_lang": "en-US", "api_key": "not-a-valid-key"},
    )
    assert r.status_code == 400
    assert backpressure.queue_depth == before

    # Invalid script_mode → 400
    r = client.post(
        "/api/translate",
        files={"file": ("t.txt", novel_bytes, "text/plain")},
        data={"target_lang": "en-US", "script_mode": "bogus"},
    )
    assert r.status_code == 400
    assert backpressure.queue_depth == before


def test_job_not_found(client):
    r = client.get("/api/jobs/nonexistent")
    assert r.status_code == 404


def test_translation_not_found(client):
    r = client.get("/api/translation/nonexistent")
    assert r.status_code == 404


def test_glossary_not_found(client):
    r = client.get("/api/glossary/nonexistent")
    assert r.status_code == 404


def test_epub_not_found(client):
    r = client.get("/api/epub/nonexistent")
    assert r.status_code == 404


def test_job_list(client):
    r = client.get("/api/jobs")
    assert r.status_code == 200


def test_presets_list(client):
    r = client.get("/api/presets")
    assert r.status_code == 200


def test_homepage(client):
    r = client.get("/")
    assert r.status_code == 200


if __name__ == "__main__":
    # Quick manual run (no pytest needed)
    print("=== E2E Smoke Test ===\n")
    c = TestClient(__import__("src.main", fromlist=["create_app"]).create_app())

    for name, fn in [
        ("Health", lambda: c.get("/api/health")),
        ("Job list", lambda: c.get("/api/jobs")),
        ("Not found (404)", lambda: c.get("/api/jobs/nonexistent")),
        ("Translation 404", lambda: c.get("/api/translation/nonexistent")),
        ("Homepage", lambda: c.get("/")),
    ]:
        r = fn()
        ok = r.status_code in (200, 404)
        print(f"  {'OK' if ok else 'FAIL'} {name}: {r.status_code}")
    print("Done.")
