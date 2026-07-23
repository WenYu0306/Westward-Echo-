"""API key authentication middleware for FastAPI."""

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import API_KEY


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validate X-API-Key header on protected routes.

    If API_KEY is empty in config, auth is disabled (dev mode).
    Only the root page, /health, /api/health, /docs, and /openapi.json
    are always open. Everything else requires authentication when
    API_KEY is set.
    """

    # Only these exact paths are open in production
    OPEN_PATHS = frozenset({"/", "/health", "/api/health", "/docs", "/openapi.json"})

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Open paths — no auth needed
        if path in self.OPEN_PATHS:
            return await call_next(request)

        # Skip auth if not configured (dev mode)
        if not API_KEY:
            return await call_next(request)

        # Everything else requires API key
        client_key = request.headers.get("X-API-Key", "")
        if client_key != API_KEY:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key. Provide X-API-Key header.",
            )

        return await call_next(request)
