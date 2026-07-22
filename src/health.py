"""Startup health checks for Westward Echo subsystems.

Runs pre-flight checks at startup and provides a detailed health report.
"""

import os
import time
import shutil
import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import httpx

from .config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    CHROMA_PERSIST_PATH,
    CHECKPOINT_DB_PATH,
    OUTPUT_DIR,
    ANTHROPIC_API_KEY,
)

if TYPE_CHECKING:
    from .glossary.semantic_store import SemanticGlossary

logger = logging.getLogger("westward_echo.health")


class HealthChecker:
    """Runs all subsystem health checks and returns a structured report.

    The report has three overall statuses:
    - healthy:   all checks pass
    - degraded:  Chroma or DeepSeek check fails but core functionality works
    - unhealthy: disk full, SQLite locked, or config missing — app should not start
    """

    def __init__(
        self,
        exact_store=None,
        semantic_store: Optional["SemanticGlossary"] = None,
    ):
        """Stores allow the checker to probe live subsystems.

        If stores are not supplied, checks that depend on them gracefully
        report a degraded result rather than crashing.
        """
        self._exact_store = exact_store
        self._semantic_store = semantic_store

    # ── public entry point ──────────────────────────────────────────

    def check_all(self) -> dict:
        """Run every health check and return the composite report."""
        checks = {}
        checks["disk_space"] = self.check_disk_space()
        checks["config"] = self.check_config()
        checks["output_dir"] = self.check_output_dir_writable()
        checks["sqlite"] = self.check_sqlite()
        checks["deepseek_api"] = self.check_deepseek_api()
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
        label = "disk_space"
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
        """Verify all required environment variables are present."""
        label = "config"
        start = time.monotonic()

        critical_vars = {
            "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY", ""),
        }
        optional_vars = {
            "DEEPSEEK_BASE_URL": os.getenv("DEEPSEEK_BASE_URL", ""),
            "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
            "REDIS_URL": os.getenv("REDIS_URL", ""),
        }

        missing_critical = [k for k, v in critical_vars.items() if not v]
        missing_optional = [k for k, v in optional_vars.items() if not v]

        elapsed = round((time.monotonic() - start) * 1000, 1)

        if missing_critical:
            return {
                "status": "error",
                "message": f"Missing required: {', '.join(missing_critical)}",
                "latency_ms": elapsed,
                "critical": True,
            }
        if missing_optional:
            return {
                "status": "warn",
                "message": f"Missing optional: {', '.join(missing_optional)}",
                "latency_ms": elapsed,
            }
        return {"status": "ok", "message": "All required env vars present", "latency_ms": elapsed}

    def check_output_dir_writable(self) -> dict:
        """Verify the output directory exists and is writable."""
        label = "output_dir"
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
        label = "sqlite"
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

    def check_deepseek_api(self) -> dict:
        """Test the DeepSeek API with a minimal 1-token call."""
        label = "deepseek_api"
        start = time.monotonic()
        try:
            api_key = os.getenv("DEEPSEEK_API_KEY", "")
            if not api_key:
                elapsed = round((time.monotonic() - start) * 1000, 1)
                return {"status": "error", "message": "DEEPSEEK_API_KEY not set", "latency_ms": elapsed}

            base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
            url = f"{base_url}/v1/chat/completions"

            payload = {
                "model": os.getenv("DEEPSEEK_FLASH_MODEL", "deepseek-v4-flash"),
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 1,
                "temperature": 0,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            with httpx.Client(timeout=5.0) as client:
                resp = client.post(url, json=payload, headers=headers)

            elapsed = round((time.monotonic() - start) * 1000, 1)

            if resp.status_code == 200:
                return {"status": "ok", "message": "DeepSeek API responding", "latency_ms": elapsed}
            else:
                body = resp.text[:200]
                return {
                    "status": "error",
                    "message": f"HTTP {resp.status_code}: {body}",
                    "latency_ms": elapsed,
                }
        except httpx.TimeoutException:
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "warn", "message": "DeepSeek API timed out (5s)", "latency_ms": elapsed}
        except Exception as exc:
            elapsed = round((time.monotonic() - start) * 1000, 1)
            return {"status": "warn", "message": str(exc), "latency_ms": elapsed}

    def check_chroma(self, exact_store=None,
                     semantic_store: Optional["SemanticGlossary"] = None) -> dict:
        """Verify Chroma persistence and (if a semantic store is supplied) embedding health."""
        label = "chroma"
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
            return {"status": "ok", "message": f"Chroma persist path writable ({CHROMA_PERSIST_PATH})",
                    "latency_ms": elapsed}
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
