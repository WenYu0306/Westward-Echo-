"""Startup health checks for Westward Echo subsystems.

Runs pre-flight checks at startup and provides a detailed health report.
"""

import logging
import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import httpx

from .config import (
    CHECKPOINT_DB_PATH,
    CHROMA_PERSIST_PATH,
    LLM_API_KEY,
    LLM_BASE_URL,
    OUTPUT_DIR,
    QWEN_MODEL,
)

if TYPE_CHECKING:
    from .glossary.semantic_store import SemanticGlossary

logger = logging.getLogger("westward_echo.health")


class HealthChecker:
    """Runs all subsystem health checks and returns a structured report.

    The report has three overall statuses:
    - healthy:   all checks pass
    - degraded:  Chroma or LLM check fails but core functionality works
    - unhealthy: disk full, SQLite locked, or config missing — app should not start

    ⚠️ Cost rule: ``check_all()`` backs ``GET /api/health`` (no auth, no rate
    limit) and the dashboard, which polls every 5 seconds. It must never spend
    money. The live LLM probe is opt-in only — see :meth:`check_llm_api`.
    """

    def __init__(
        self,
        exact_store=None,
        semantic_store: Optional["SemanticGlossary"] = None,
        live_llm: bool = False,
    ):
        """Stores allow the checker to probe live subsystems.

        If stores are not supplied, checks that depend on them gracefully
        report a degraded result rather than crashing.

        ``live_llm=True`` opts in to a real (billable) 1-token LLM call. It
        defaults to False so the unauthenticated ``/api/health`` endpoint and
        the 5-second dashboard poll cannot silently burn API credits.
        """
        self._exact_store = exact_store
        self._semantic_store = semantic_store
        self._live_llm = live_llm

    # ── public entry point ──────────────────────────────────────────

    def check_all(self) -> dict:
        """Run every health check and return the composite report."""
        checks = {}
        checks["disk_space"] = self.check_disk_space()
        checks["config"] = self.check_config()
        checks["output_dir"] = self.check_output_dir_writable()
        checks["sqlite"] = self.check_sqlite()
        checks["llm_api"] = self.check_llm_api()
        checks["memory"] = check_memory()
        checks["disk_io"] = check_disk_io()
        checks["chroma"] = self.check_chroma(exact_store=self._exact_store,
                                              semantic_store=self._semantic_store)

        # Determine overall status
        ok_count = sum(1 for c in checks.values() if c["status"] == "ok")
        warn_count = sum(1 for c in checks.values() if c["status"] == "warn")
        error_count = sum(1 for c in checks.values() if c["status"] == "error")

        if any(c["status"] == "error" and c.get("critical", False)
               for c in checks.values()):
            overall = "unhealthy"
        elif error_count > 0 or warn_count > 0:
            overall = "degraded"
        else:
            overall = "healthy"

        return {
            "status": overall,
            "checks": checks,
            "summary": {
                "ok": ok_count,
                "warn": warn_count,
                "error": error_count,
                "total": len(checks),
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    # ── individual checks ────────────────────────────────────────────

    def check_disk_space(self, min_free_mb: int = 500) -> dict:
        """Check that the data volume has enough free space."""
        start = time.monotonic()
        try:
            path = Path(CHROMA_PERSIST_PATH).parent  # data/ directory
            path.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(path)
            free_mb = usage.free // (1024 * 1024)
            if free_mb < min_free_mb:
                elapsed = round((time.monotonic() - start) * 1000, 1)
                return {
                    "status": "error",
                    "message": f"Only {free_mb} MB free (threshold: {min_free_mb} MB)",
                    "latency_ms": elapsed,
                    "critical": True,
                }
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {
                "status": "ok",
                "message": f"{free_mb} MB free",
                "latency_ms": elapsed,
            }
        except Exception as exc:
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "error", "message": str(exc), "latency_ms": elapsed, "critical": True}

    def check_config(self) -> dict:
        """Verify the environment variables the pipeline actually uses.

        Both providers are load-bearing in MODEL_MAP: Qwen (LLM_*) serves
        READ/READBACK/REVIEW_TERMS and GLM (GLM_*) serves WRITE/FIX. Either one
        missing means the pipeline genuinely cannot run, so both are critical.

        This used to require DEEPSEEK_API_KEY — a provider the project abandoned
        in 2026-09. The stale requirement made the check pass on an unused key
        while never validating a single credential that is really called, and it
        made a fresh GLM+Qwen-only deployment refuse to start.
        """
        start = time.monotonic()

        # Read from the environment (not module-level constants) so this reports
        # what a deployment actually has, and so tests can inject values.
        critical_vars = {
            "LLM_API_KEY": os.getenv("LLM_API_KEY", ""),
            "GLM_API_KEY": os.getenv("GLM_API_KEY", ""),
        }
        optional_vars = {
            "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
            "REDIS_URL": os.getenv("REDIS_URL", ""),
        }
        # Retired 2026-09. Present in .env but read by nothing in src/.
        deprecated_vars = {
            "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY", ""),
            "DEEPSEEK_BASE_URL": os.getenv("DEEPSEEK_BASE_URL", ""),
        }

        missing_critical = [k for k, v in critical_vars.items() if not v]
        missing_optional = [k for k, v in optional_vars.items() if not v]
        stale = [k for k, v in deprecated_vars.items() if v]

        elapsed = round((time.monotonic() - start) * 1000, 1)

        if missing_critical:
            return {
                "status": "error",
                "message": f"Missing required: {', '.join(missing_critical)}",
                "latency_ms": elapsed,
                "critical": True,
            }
        notes = []
        if missing_optional:
            notes.append(f"Missing optional: {', '.join(missing_optional)}")
        if stale:
            notes.append(
                f"Deprecated vars still set (unused since the DeepSeek removal): "
                f"{', '.join(stale)}"
            )
        if notes:
            return {
                "status": "warn",
                "message": " | ".join(notes),
                "latency_ms": elapsed,
            }
        return {"status": "ok", "message": "All required env vars present", "latency_ms": elapsed}

    def check_output_dir_writable(self) -> dict:
        """Verify the output directory exists and is writable."""
        start = time.monotonic()
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            probe = OUTPUT_DIR / ".health_probe"
            probe.write_text("ok")
            probe.unlink()
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "ok", "message": str(OUTPUT_DIR), "latency_ms": elapsed}
        except Exception as exc:
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "error", "message": str(exc), "latency_ms": elapsed, "critical": True}

    def check_sqlite(self) -> dict:
        """Verify the SQLite checkpoint database is accessible."""
        start = time.monotonic()
        try:
            import sqlite3
            db_path = CHECKPOINT_DB_PATH
            conn = sqlite3.connect(db_path)
            conn.execute("SELECT 1")
            conn.close()
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "ok", "message": str(db_path), "latency_ms": elapsed}
        except Exception as exc:
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "error", "message": str(exc), "latency_ms": elapsed, "critical": True}

    def check_llm_api(self) -> dict:
        """Report whether the LLM provider is *configured*, without calling it.

        Probing LLM_BASE_URL + QWEN_MODEL (the Qwen 3.8 max judging tier) with a
        real 1-token call is the same OpenAI-compatible base_url
        READBACK/REVIEW_TERMS use — but it costs money on every invocation, and
        ``check_all()`` backs an unauthenticated endpoint plus a dashboard that
        polls every 5 seconds. So the real call now requires ``live_llm=True``.

        The default path is a zero-cost configuration check: it verifies the key
        is present and the base_url is well formed, which catches the failure
        mode that actually takes production down (missing/rotated credential).
        """
        start = time.monotonic()
        try:
            if not LLM_API_KEY:
                elapsed = round((time.monotonic() - start) * 1000, 1)
                return {
                    "status": "error", "message": "LLM_API_KEY not set",
                    "latency_ms": elapsed,
                    "critical": True,
                }

            base_url = LLM_BASE_URL.rstrip("/")
            if not base_url.startswith(("http://", "https://")):
                elapsed = round((time.monotonic() - start) * 1000, 1)
                return {
                    "status": "error",
                    "message": f"LLM_BASE_URL is not a usable URL: {LLM_BASE_URL!r}",
                    "latency_ms": elapsed,
                    "critical": True,
                }

            if not self._live_llm:
                elapsed = round((time.monotonic() - start) * 1000, 1)
                return {
                    "status": "ok",
                    "message": (
                        f"configured ({QWEN_MODEL} @ {base_url}); live probe skipped "
                        "— pass live_llm=True to spend a real call"
                    ),
                    "latency_ms": elapsed,
                    "live_probe": False,
                }

            url = f"{base_url}/chat/completions"

            payload = {
                "model": QWEN_MODEL,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 1,
                "temperature": 0,
            }
            headers = {
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json",
            }

            with httpx.Client(timeout=5.0) as client:
                resp = client.post(url, json=payload, headers=headers)

            elapsed = round((time.monotonic() - start) * 1000, 1)

            if resp.status_code == 200:
                return {"status": "ok", "message": "LLM API responding", "latency_ms": elapsed,
                        "live_probe": True}
            else:
                body = resp.text[:200]
                return {
                    "status": "error",
                    "message": f"HTTP {resp.status_code}: {body}",
                    "latency_ms": elapsed,
                    "live_probe": True,
                }
        except httpx.TimeoutException:
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {
                "status": "error", "message": "LLM API timed out (5s)", "latency_ms": elapsed,
                "live_probe": True,
            }
        except Exception as exc:
            # Do NOT downgrade to "warn": a failed live probe means the judging
            # nodes cannot reach the provider. Silently reporting warn here is
            # what let /api/health pass while every real call was failing.
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "error", "message": f"{type(exc).__name__}: {exc}",
                    "latency_ms": elapsed, "live_probe": True}

    def check_chroma(self, exact_store=None,
                     semantic_store: Optional["SemanticGlossary"] = None) -> dict:
        """Verify Chroma persistence and (if a semantic store is supplied) embedding health."""
        start = time.monotonic()
        try:
            # Check Chroma persistence directory is writable
            Path(CHROMA_PERSIST_PATH).mkdir(parents=True, exist_ok=True)

            # If a semantic store instance is available, probe its ready flag
            if semantic_store is not None:
                if semantic_store.is_healthy():
                    elapsed = round((time.monotonic() - start) * 1000, 1)
                    return {"status": "ok", "message": "Chroma ready with embeddings",
                            "latency_ms": elapsed}
                else:
                    elapsed = round((time.monotonic() - start) * 1000, 1)
                    return {"status": "warn",
                            "message": "Chroma initialised but embeddings unavailable",
                            "latency_ms": elapsed}

            # Without a live instance, just report that the path is writable
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {
                "status": "ok",
                "message": f"Chroma persist path writable ({CHROMA_PERSIST_PATH})",
                "latency_ms": elapsed,
            }
        except Exception as exc:
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "warn", "message": str(exc), "latency_ms": elapsed}


# ═══════════════════════════════════════════════════════════════
# Module-level resource monitoring functions
# ═══════════════════════════════════════════════════════════════

def check_memory() -> dict:
    """Return current memory usage.

    Uses psutil if available, otherwise returns a degraded status with
    the reason the check could not run.
    """
    start = time.monotonic()
    try:
        import psutil
        mem = psutil.virtual_memory()
        elapsed = round((time.monotonic() - start) * 1000, 1)
        used_gb = round(mem.used / (1024 ** 3), 2)
        total_gb = round(mem.total / (1024 ** 3), 2)
        pct = mem.percent
        if pct > 95:
            return {
                "status": "error",
                "message": f"Memory usage critical: {used_gb}/{total_gb} GB ({pct}%)",
                "used_gb": used_gb,
                "total_gb": total_gb,
                "percent": pct,
                "latency_ms": elapsed,
            }
        elif pct > 80:
            return {
                "status": "warn",
                "message": f"Memory usage high: {used_gb}/{total_gb} GB ({pct}%)",
                "used_gb": used_gb,
                "total_gb": total_gb,
                "percent": pct,
                "latency_ms": elapsed,
            }
        return {
            "status": "ok",
            "message": f"{used_gb}/{total_gb} GB ({pct}%)",
            "used_gb": used_gb,
            "total_gb": total_gb,
            "percent": pct,
            "latency_ms": elapsed,
        }
    except ImportError:
        elapsed = round((time.monotonic() - start) * 1000, 1)
        return {"status": "warn", "message": "psutil not installed — memory check skipped",
                "latency_ms": elapsed}
    except Exception as exc:
        elapsed = round((time.monotonic() - start) * 1000, 1)
        return {"status": "warn", "message": str(exc), "latency_ms": elapsed}


def check_disk_io() -> dict:
    """Check if the output directory is on a healthy filesystem.

    Performs a small write + read + delete probe to verify the underlying
    filesystem responds within a reasonable deadline (5 seconds).
    """
    start = time.monotonic()
    try:
        probe_file = OUTPUT_DIR / ".disk_io_probe"
        data = b"health-check:" + os.urandom(64).hex().encode()
        probe_file.write_bytes(data)
        read_back = probe_file.read_bytes()
        probe_file.unlink()
        elapsed = round((time.monotonic() - start) * 1000, 1)
        if read_back != data:
            return {
                "status": "error",
                "message": "Read-back mismatch — possible filesystem corruption",
                "latency_ms": elapsed,
                "critical": True,
            }
        if elapsed > 5000:
            return {
                "status": "warn",
                "message": f"Disk I/O slow ({elapsed}ms) but functional",
                "latency_ms": elapsed,
            }
        return {"status": "ok", "message": f"Disk I/O healthy ({elapsed:.1f} ms)",
                "latency_ms": elapsed}
    except Exception as exc:
        elapsed = round((time.monotonic() - start) * 1000, 1)
        return {"status": "error", "message": str(exc), "latency_ms": elapsed, "critical": True}
