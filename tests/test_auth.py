"""Unit tests for api/auth.py — API key authentication middleware."""

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


@pytest.fixture
def auth_app():
    """Return a FastAPI app with the API key middleware mounted."""
    from src.api.auth import APIKeyMiddleware
    app = FastAPI()
    app.add_middleware(APIKeyMiddleware)

    @app.get("/")
    async def root():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/api/health")
    async def api_health():
        return {"status": "ok"}

    @app.post("/api/translate")
    async def translate():
        return {"job_id": "test123"}

    @app.get("/api/translate/abc123")
    async def get_translation():
        return {"status": "done"}

    return app


class TestAuthDisabled:
    """When API_KEY is empty (dev mode), all routes are open."""

    def test_root_accessible(self, auth_app):
        import src.api.auth as auth_mod
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(auth_mod, "API_KEY", "")
            client = TestClient(auth_app)
            r = client.get("/")
            assert r.status_code == 200

    def test_protected_route_accessible_without_key(self, auth_app):
        import src.api.auth as auth_mod
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(auth_mod, "API_KEY", "")
            client = TestClient(auth_app)
            r = client.post("/api/translate")
            assert r.status_code == 200


class TestAuthEnabled:
    """When API_KEY is set, protected routes require X-API-Key header."""

    def test_open_paths_always_accessible(self, auth_app):
        import src.api.auth as auth_mod
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(auth_mod, "API_KEY", "secret-key-123")
            client = TestClient(auth_app)
            assert client.get("/").status_code == 200
            assert client.get("/health").status_code == 200
            assert client.get("/api/health").status_code == 200

    def test_protected_route_rejected_without_key(self, auth_app):
        import src.api.auth as auth_mod
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(auth_mod, "API_KEY", "secret-key-123")
            client = TestClient(auth_app)
            r = client.post("/api/translate")
            assert r.status_code == 401

    def test_protected_route_rejected_with_wrong_key(self, auth_app):
        import src.api.auth as auth_mod
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(auth_mod, "API_KEY", "secret-key-123")
            client = TestClient(auth_app)
            r = client.post("/api/translate", headers={"X-API-Key": "wrong-key"})
            assert r.status_code == 401

    def test_protected_route_accepted_with_correct_key(self, auth_app):
        import src.api.auth as auth_mod
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(auth_mod, "API_KEY", "secret-key-123")
            client = TestClient(auth_app)
            r = client.post("/api/translate", headers={"X-API-Key": "secret-key-123"})
            assert r.status_code == 200

    def test_get_endpoint_also_protected(self, auth_app):
        import src.api.auth as auth_mod
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(auth_mod, "API_KEY", "secret-key-123")
            client = TestClient(auth_app)
            r = client.get("/api/translate/abc123")
            assert r.status_code == 401

    def test_openapi_json_is_open(self, auth_app):
        import src.api.auth as auth_mod
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(auth_mod, "API_KEY", "secret-key-123")
            client = TestClient(auth_app)
            r = client.get("/openapi.json")
            assert r.status_code == 200
