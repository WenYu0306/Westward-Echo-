"""End-to-end test: upload → translate → download. Uses FastAPI TestClient.

Run with:  pytest tests/test_e2e.py -v -k "not requires_api_key"
or:        python3 tests/test_e2e.py
"""
import sys, os, json, time, tempfile, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
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
        assert status.get("status") != "unknown", f"Expected real status, got unknown"
        if status.get("status") == "complete":
            break
        time.sleep(1)

    # Clean up
    client.delete(f"/api/jobs/{job_id}")


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
