"""Entry point — single FastAPI app with Web UI, API routes, auth, and logging."""

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi import Request

from .config import API_PORT, HOST
from .web_ui import PAGE, REVIEW_PAGE
from .editor_ui import EDITOR_PAGE
from .api.routes import app as api_router
from .api.review import app as review_api_router
from .api.editor import app as editor_api_router
from .api.auth import APIKeyMiddleware
from .api.logging import logger
from .health import HealthChecker
from .dashboard import DASHBOARD_PAGE, get_dashboard_data
from .usage_ui import USAGE_PAGE, get_usage_data


def create_app() -> FastAPI:
    """Assemble the full application."""
    app = FastAPI(
        title="Westward Echo",
        description="AI-powered web novel translation with cultural adaptation",
        version="0.12.0",
    )

    # ── Startup health checks ──
    health = HealthChecker()
    report = health.check_all()

    if report["status"] == "unhealthy":
        msg = f"Startup health check FAILED: {report['status']}"
        logger.critical(msg)
        # Log each failing check for diagnostics
        for name, check in report["checks"].items():
            if check["status"] == "error":
                logger.critical("  %s: %s", name, check["message"])
        raise RuntimeError(f"System unhealthy — refusing to start. {report}")

    if report["status"] == "degraded":
        logger.warning("Startup health check DEGRADED")
        for name, check in report["checks"].items():
            if check["status"] in ("warn", "error"):
                logger.warning("  %s: %s", name, check["message"])
    elif report["status"] == "healthy":
        logger.info("Startup health check HEALTHY")

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

    # Glossary review page at /review
    @app.get("/review", response_class=HTMLResponse)
    async def review_page():
        return REVIEW_PAGE

    # Editor page — human-in-the-loop paragraph editing
    @app.get("/editor/{job_id}", response_class=HTMLResponse)
    async def editor_page(job_id: str):
        return EDITOR_PAGE.replace("{JOB_ID}", job_id)

    # Dashboard — observability page (no auth)
    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        return DASHBOARD_PAGE

    @app.get("/api/dashboard/metrics")
    async def dashboard_metrics():
        """JSON endpoint powering the dashboard's auto-refresh."""
        return get_dashboard_data()

    # Usage analytics page — error tracking & editor behavior
    @app.get("/usage", response_class=HTMLResponse)
    async def usage_page():
        return USAGE_PAGE

    @app.get("/api/usage/events")
    async def usage_events_api():
        """JSON endpoint powering the usage page's auto-refresh."""
        return get_usage_data()

    # CMS API
    from .api.cms import app as cms_api
    app.mount("/api/cms", cms_api)

    # API routes
    app.mount("/api", api_router)
    app.mount("/api/review", review_api_router)
    app.mount("/api/editor", editor_api_router)

    logger.info("Westward Echo v0.12.0 (Celery + Redis + Auth + Logging + Health + CMS + Editor)")

    return app


app = create_app()


def main():
    uvicorn.run(app, host=HOST, port=API_PORT)


if __name__ == "__main__":
    main()
