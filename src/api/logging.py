"""Structured logging for the translation service."""

import logging
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ..config import LOG_LEVEL, LOG_FILE


def setup_logging():
    """Configure structured JSON-line logging to file + human-readable to console."""

    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("westward_echo")
    root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    # File handler: JSON Lines
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_JSONFormatter())
    root.addHandler(fh)

    # Console handler: human-readable
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(ch)

    return root


class _JSONFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["error"] = str(record.exc_info[1])
        return json.dumps(entry, ensure_ascii=False)


logger = setup_logging()


# ── Helper decorator for task metrics ──

def log_duration(name: str):
    """Decorator that logs the duration of a function call."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            try:
                result = fn(*args, **kwargs)
                elapsed = time.monotonic() - start
                logger.info("%s completed in %.2fs", name, elapsed)
                return result
            except Exception:
                elapsed = time.monotonic() - start
                logger.error("%s failed after %.2fs", name, elapsed, exc_info=True)
                raise
        return wrapper
    return decorator
