"""Entry point — single FastAPI app with Web UI, API routes, auth, and logging."""

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi import Request

from .config import API_PORT, HOST
from .web_ui import PAGE
from .api.routes import app as api_router
from .api.auth import APIKeyMiddleware
from .api.logging import logger


def create_app() -> FastAPI:
    """Assemble the full application."""
    app = FastAPI(
        title="Westward Echo",
        description="AI-powered web novel translation with cultural adaptation",
        version="0.2.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(APIKeyMiddleware)

    # Rate limiting (token-bucket, configurable via RATE_LIMIT_RPM / RATE_LIMIT_ENABLED)
    try:
        from .api.rate_limit import RateLimitMiddleware
        app.add_middleware(RateLimitMiddleware)
    except ImportError:
        logger.warning("rate_limit module unavailable — running without rate limiting")

    # Web UI at /
    @app.get("/", response_class=HTMLResponse)
    async def index():
        return PAGE

    # API routes
    app.mount("/api", api_router)

    logger.info("Westward Echo v0.2.0 (Celery + Redis + Auth + Logging)")

    return app


app = create_app()


def main():
    uvicorn.run(app, host=HOST, port=API_PORT)


if __name__ == "__main__":
    main()
