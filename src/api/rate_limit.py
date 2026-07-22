"""Token-bucket rate limiter middleware for translation API endpoints."""

import time
import threading
from typing import List, Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import RATE_LIMIT_RPM, RATE_LIMIT_ENABLED
from .logging import logger


class TokenBucket:
    """Thread-safe token bucket rate limiter.

    Tokens are replenished continuously at the configured rate.
    """

    def __init__(self, rate: int, capacity: Optional[int] = None):
        """
        Args:
            rate: Tokens replenished per minute.
            capacity: Maximum burst size. Defaults to rate if omitted.
        """
        self._rate = rate  # tokens per minute
        self._capacity = capacity if capacity is not None else rate
        self._tokens: float = float(self._capacity)
        self._last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, now: Optional[float] = None) -> bool:
        """Attempt to consume 1 token. Returns True if allowed, False if denied."""
        if now is None:
            now = time.monotonic()

        with self._lock:
            # Refill tokens based on elapsed time
            elapsed = now - self._last_refill
            refill_amount = elapsed * (self._rate / 60.0)  # tokens per second
            self._tokens = min(self._capacity, self._tokens + refill_amount)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    @property
    def remaining(self) -> int:
        """Approximate remaining tokens (for informational headers)."""
        with self._lock:
            return max(0, int(self._tokens))

    @property
    def seconds_until_next_token(self) -> float:
        """Estimated time in seconds until a token is available."""
        with self._lock:
            if self._tokens >= 1.0:
                return 0.0
            deficit = 1.0 - self._tokens
            return deficit / (self._rate / 60.0)


# Singleton bucket shared across all workers (thread-safe for single-process).
_bucket: Optional[TokenBucket] = None
_bucket_lock = threading.Lock()


def _get_bucket() -> TokenBucket:
    """Lazy-init the shared token bucket."""
    global _bucket
    if _bucket is None:
        with _bucket_lock:
            if _bucket is None:
                _bucket = TokenBucket(rate=RATE_LIMIT_RPM, capacity=RATE_LIMIT_RPM)
    return _bucket


_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Token-bucket rate limiter for translation API endpoints.

    Only rate-limits mutating endpoints (POST, PUT, DELETE, PATCH). Read endpoints
    (GET /health, GET /, GET /api/translate/{job_id}) pass through unthrottled.

    Configurable via env vars:
    - RATE_LIMIT_RPM: max requests per minute (default: 30)
    - RATE_LIMIT_ENABLED: "true" or "false" (default: true)
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if not RATE_LIMIT_ENABLED or request.method in _READ_METHODS:
            return await call_next(request)

        bucket = _get_bucket()
        now = time.monotonic()

        if not bucket.consume(now):
            retry_after = max(1, int(bucket.seconds_until_next_token) + 1)
            logger.warning(
                "Rate limit hit — %s %s (retry after %ds, remaining=%d)",
                request.method,
                request.url.path,
                retry_after,
                bucket.remaining,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "retry_after_seconds": retry_after,
                },
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(retry_after),
                },
            )

        response = await call_next(request)

        # Attach rate-limit headers to the response
        response.headers["X-RateLimit-Remaining"] = str(bucket.remaining)
        response.headers["X-RateLimit-Reset"] = str(
            max(0, int(bucket.seconds_until_next_token))
        )

        return response
