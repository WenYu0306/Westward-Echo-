"""API key authentication middleware for FastAPI."""

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import API_KEY


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validate X-API-Key header on protected routes.

    If API_KEY is empty in config, auth is disabled (dev mode).
    Protected routes are anything under /api/translate/* and /api/glossary/*.
    GET / and /health are always open.
    """

    OPEN_PREFIXES = ("/docs", "/openapi.json", "/ws", "/api/health")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip auth for root page and open routes
        if path == "/" or any(path.startswith(p) for p in self.OPEN_PREFIXES):
            return await call_next(request)

        # Skip auth if not configured (dev mode)
        if not API_KEY:
            return await call_next(request)

        # Check API key
        client_key = request.headers.get("X-API-Key", "")
        if client_key != API_KEY:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key. Provide X-API-Key header.",
            )

        return await call_next(request)
