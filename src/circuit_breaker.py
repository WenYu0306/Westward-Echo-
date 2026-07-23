"""Circuit breaker for preventing cascading failures when the LLM API is unreliable.

States: CLOSED (normal) -> OPEN (tripped, fast-fail) -> HALF_OPEN (testing recovery)

One circuit breaker per language — if en-US fails, es-ES and ar-SA continue unaffected.
"""

import threading
import time
from typing import Callable, Dict, Optional


class CircuitBreakerOpenError(Exception):
    """Raised when a call is attempted while the circuit breaker is OPEN."""


class CircuitBreaker:
    """Prevent cascading failures when the LLM API is unreliable.

    After `failure_threshold` consecutive failures in the tracking window,
    the circuit opens and all calls fast-fail without hitting the API.
    After `recovery_timeout` seconds, a single probe call is allowed
    (HALF_OPEN). If the probe succeeds the circuit closes; if it fails
    the circuit re-opens.

    Thread-safe via ``threading.Lock``.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max: int = 1,
        window_seconds: float = 60.0,
    ):
        """
        Args:
            name: Human-readable name for this breaker (e.g. ``"en-US"``).
            failure_threshold: Consecutive failures needed to trip the breaker.
            recovery_timeout: Seconds to wait before transitioning OPEN -> HALF_OPEN.
            half_open_max: Max calls allowed while HALF_OPEN before deciding.
            window_seconds: Rolling window for counting failures (resets on success).
        """
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max
        self._window_seconds = window_seconds

        self._lock = threading.Lock()
        self._state: str = self.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._opened_at: float = 0.0
        self._half_open_attempts: int = 0

        # History for observability
        self._total_successes: int = 0
        self._total_failures: int = 0
        self._total_calls: int = 0
        self._open_transitions: int = 0

    # ── public API ──────────────────────────────────────────────

    def call(self, fn: Callable, *args, **kwargs):
        """Execute ``fn(*args, **kwargs)`` if the circuit allows it.

        Raises ``CircuitBreakerOpenError`` immediately if the circuit is OPEN.
        Raises ``CircuitBreakerOpenError`` if the breaker opens *during* a
        HALF_OPEN probe failure.

        Returns the result of ``fn`` on success.
        """
        with self._lock:
            self._total_calls += 1
            if self._state == self.OPEN:
                if self._should_attempt_recovery():
                    self._state = self.HALF_OPEN
                    self._half_open_attempts = 0
                else:
                    self._total_failures += 1
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"{self._time_until_recovery():.0f}s until recovery attempt."
                    )

            if self._state == self.HALF_OPEN:
                self._half_open_attempts += 1

        # Execute the call outside the lock so we don't hold it during I/O.
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise

        self.record_success()
        return result

    def record_success(self):
        """Report a successful call — reset the circuit to CLOSED."""
        with self._lock:
            self._state = self.CLOSED
            self._failure_count = 0
            self._half_open_attempts = 0
            self._total_successes += 1

    def record_failure(self):
        """Report a failed call — may trip the breaker."""
        with self._lock:
            now = time.monotonic()
            self._failure_count += 1
            self._last_failure_time = now
            self._total_failures += 1

            if self._state == self.HALF_OPEN:
                # Probe failed — back to OPEN
                self._state = self.OPEN
                self._opened_at = now
                return

            if self._failure_count >= self._failure_threshold and self._state == self.CLOSED:
                self._state = self.OPEN
                self._opened_at = now
                self._open_transitions += 1

                # Record trip event for analytics
                try:
                    from .error_tracker import record_event
                    record_event(
                        None, None, "circuit_breaker",
                        f"{self.name} breaker OPEN after {self._failure_count} consecutive failures",
                        self.name,
                    )
                except Exception:
                    pass  # Event recording must never block circuit breaker logic

    def is_open(self) -> bool:
        """Return True if the circuit is currently OPEN (fast-fail mode)."""
        with self._lock:
            if self._state == self.OPEN:
                if self._should_attempt_recovery():
                    return False
                return True
            return False

    @property
    def state(self) -> str:
        """Current state: ``'closed'``, ``'open'``, or ``'half_open'``."""
        with self._lock:
            if self._state == self.OPEN and self._should_attempt_recovery():
                return self.HALF_OPEN
            return self._state

    def snapshot(self) -> dict:
        """Return a lightweight metrics snapshot for the dashboard."""
        with self._lock:
            # Access _state directly — self.state acquires _lock which
            # would deadlock since we already hold it.
            st = self._state
            if st == self.OPEN and self._should_attempt_recovery():
                st = self.HALF_OPEN
            return {
                "name": self.name,
                "state": st,
                "failure_count": self._failure_count,
                "total_successes": self._total_successes,
                "total_failures": self._total_failures,
                "total_calls": self._total_calls,
                "open_transitions": self._open_transitions,
                "failure_threshold": self._failure_threshold,
                "recovery_timeout": self._recovery_timeout,
            }

    # ── internal helpers ───────────────────────────────────────

    def _should_attempt_recovery(self) -> bool:
        """Check if enough time has passed to try a recovery probe."""
        return (time.monotonic() - self._opened_at) >= self._recovery_timeout

    def _time_until_recovery(self) -> float:
        """Seconds remaining before the next recovery attempt."""
        return max(0.0, self._recovery_timeout - (time.monotonic() - self._opened_at))


# ═══════════════════════════════════════════════════════════════
# Per-language singleton registry
# ═══════════════════════════════════════════════════════════════

_breakers: Dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()
_DEFAULT_THRESHOLD = 5
_DEFAULT_RECOVERY = 30.0
_WINDOW_SECONDS = 60.0


def get_breaker(
    language: str,
    failure_threshold: int = _DEFAULT_THRESHOLD,
    recovery_timeout: float = _DEFAULT_RECOVERY,
    window_seconds: float = _WINDOW_SECONDS,
) -> CircuitBreaker:
    """Return (or create) a CircuitBreaker per language.

    >>> breaker = get_breaker("en-US")
    >>> breaker = get_breaker("es-ES")  # Different breaker — independent state
    """
    key = language.lower()
    if key not in _breakers:
        with _breakers_lock:
            if key not in _breakers:
                _breakers[key] = CircuitBreaker(
                    name=language,
                    failure_threshold=failure_threshold,
                    recovery_timeout=recovery_timeout,
                    window_seconds=window_seconds,
                )
    return _breakers[key]


def get_all_breakers() -> dict:
    """Return snapshots of all registered circuit breakers."""
    with _breakers_lock:
        return {lang: cb.snapshot() for lang, cb in _breakers.items()}
