"""Circuit breaker for preventing cascading failures when the LLM API is unreliable.

States: CLOSED (normal) -> OPEN (tripped, fast-fail) -> HALF_OPEN (testing recovery)

One circuit breaker per language — if en-US fails, es-ES, de, and fr continue unaffected.
"""

import threading
import time
from collections import deque
from typing import Callable


class CircuitBreakerOpenError(Exception):
    """Raised when a call is attempted while the circuit breaker is OPEN."""


class CircuitBreaker:
    """Prevent cascading failures when the LLM API is unreliable.

    The breaker trips after ``failure_threshold`` failures **within a rolling
    ``window_seconds``**, then fast-fails every call for ``recovery_timeout``
    seconds. After that it admits up to ``half_open_max`` probe calls
    (HALF_OPEN): a successful probe closes the circuit, a failed one re-opens it.

    Why a rolling window and not a consecutive-failure counter
    ----------------------------------------------------------
    This used to count *consecutive* failures and clear the counter on any
    success. That has a measured cost: an API failing sporadically — say 4
    failures per 5 calls, forever — never reaches the threshold, because each
    interleaved success wipes the count. The pipeline keeps paying a broken
    provider indefinitely, which is exactly what a circuit breaker exists to
    stop. Failures now age out of the window on their own; a success no longer
    erases them.

    A successful **HALF_OPEN probe** still clears the window (see
    ``record_success``) — otherwise recovery would be impossible, since the
    failures that caused the trip would still be in-window and the very next
    failure would re-trip immediately.

    Thread-safe via ``threading.Lock``. The lock is NOT reentrant, so code that
    already holds it must never touch the ``_failure_count`` property or the
    ``state`` property — use the internal ``_failure_times`` deque directly.
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
            failure_threshold: Failures within the rolling window needed to trip.
            recovery_timeout: Seconds to wait before transitioning OPEN -> HALF_OPEN.
            half_open_max: Probe calls admitted concurrently while HALF_OPEN.
                The N+1-th caller fast-fails instead of also hitting the API.
            window_seconds: Rolling window over which failures are counted.
                Old failures age out on their own; successes do not clear them.
        """
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max
        self._window_seconds = window_seconds

        self._lock = threading.Lock()
        self._state: str = self.CLOSED
        # Monotonic timestamps of failures still inside the window, oldest
        # first. Replaces a bare int counter: `len()` gives the in-window count,
        # and popleft() ages failures out instead of waiting for a success to
        # erase them. Bounded by pruning on every read/write, never by maxlen —
        # a maxlen would silently drop in-window failures.
        self._failure_times: deque = deque()
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

        Raises ``CircuitBreakerOpenError`` immediately if the circuit is OPEN,
        or if the circuit is HALF_OPEN and its ``half_open_max`` probe slots are
        already taken. Raises ``CircuitBreakerOpenError`` if the breaker opens
        *during* a HALF_OPEN probe failure.

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
                # Probe throttling. _half_open_attempts counts *admitted*
                # probes, so the check happens before the increment: with
                # half_open_max=1 the first caller is admitted and every
                # concurrent caller fast-fails instead of also hitting a
                # provider we are actively suspecting. Without this, N threads
                # would all probe at once and the "max" was a config value
                # nobody read.
                if self._half_open_attempts >= self._half_open_max:
                    self._total_failures += 1
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is HALF_OPEN and its "
                        f"{self._half_open_max} probe slot(s) are taken; "
                        f"fast-failing instead of piling onto a suspect API."
                    )
                self._half_open_attempts += 1

        # Execute the call outside the lock so we don't hold it during I/O.
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise

        self.record_success()
        return result

    def record_success(self) -> None:
        """Report a successful call — reset the circuit to CLOSED.

        Whether the failure window is cleared depends on *what* succeeded:

        * A success while CLOSED (ordinary traffic) does **not** clear the
          window. Sporadic failures interleaved with successes are the exact
          pattern a rolling window exists to catch; letting one success erase
          the history is what made such an API never trip and keep billing.
        * A success while HALF_OPEN/OPEN is a **recovery probe** proving the
          provider is back, so the window is cleared. Keeping those failures
          would re-trip on the next single failure and make recovery_timeout
          pointless.
        """
        with self._lock:
            recovering = self._state != self.CLOSED
            self._state = self.CLOSED
            self._half_open_attempts = 0
            self._total_successes += 1
            if recovering:
                self._failure_times.clear()

    def record_failure(self) -> None:
        """Report a failed call — may trip the breaker."""
        with self._lock:
            now = time.monotonic()
            self._failure_times.append(now)
            self._prune_window(now)
            self._total_failures += 1

            if self._state == self.HALF_OPEN:
                # Probe failed — back to OPEN
                self._state = self.OPEN
                self._opened_at = now
                return

            in_window = len(self._failure_times)
            if in_window >= self._failure_threshold and self._state == self.CLOSED:
                self._state = self.OPEN
                self._opened_at = now
                self._open_transitions += 1

                # Record trip event for analytics. Uses the deque directly:
                # we hold the lock, and the _failure_count property takes it
                # again (threading.Lock is not reentrant).
                try:
                    from .error_tracker import record_event
                    record_event(
                        None, None, "circuit_breaker",
                        f"{self.name} breaker OPEN after {in_window} failures "
                        f"in the last {self._window_seconds:.0f}s",
                        self.name,
                    )
                except Exception:
                    pass  # Event recording must never block circuit breaker logic

    @property
    def _failure_count(self) -> int:
        """Failures still inside the rolling window.

        Read-only on purpose: it used to be a plain int that tests assigned to
        directly (``breaker._failure_count = 0``), which silently desynced it
        from the window. Use ``_reset()`` to clear state.
        """
        with self._lock:
            self._prune_window(time.monotonic())
            return len(self._failure_times)

    def _reset(self) -> None:
        """Return the breaker to a pristine CLOSED state.

        For test teardown: a breaker left OPEN (or holding in-window failures)
        poisons every later test that fetches the same language from the
        module-level registry. Prefer this over poking private fields.
        """
        with self._lock:
            self._state = self.CLOSED
            self._failure_times.clear()
            self._half_open_attempts = 0
            self._opened_at = 0.0

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
            # would deadlock since we already hold it. Same for the failure
            # count: _failure_count is a property that takes the lock again.
            st = self._state
            if st == self.OPEN and self._should_attempt_recovery():
                st = self.HALF_OPEN
            self._prune_window(time.monotonic())
            return {
                "name": self.name,
                "state": st,
                "failure_count": len(self._failure_times),
                "total_successes": self._total_successes,
                "total_failures": self._total_failures,
                "total_calls": self._total_calls,
                "open_transitions": self._open_transitions,
                "failure_threshold": self._failure_threshold,
                "recovery_timeout": self._recovery_timeout,
                # Surfaced now that both are real behaviour, not dead config:
                # the dashboard previously could not show why a breaker was
                # counting failures the way it did.
                "window_seconds": self._window_seconds,
                "half_open_max": self._half_open_max,
            }

    # ── internal helpers ───────────────────────────────────────

    def _prune_window(self, now: float) -> None:
        """Drop failures older than ``now - window_seconds``.

        Caller MUST hold ``_lock``. Entries were appended in non-decreasing
        time order (``time.monotonic`` never goes backwards), so only the left
        end can be expired and ``popleft`` in a loop is sufficient — no scan.
        """
        cutoff = now - self._window_seconds
        while self._failure_times and self._failure_times[0] < cutoff:
            self._failure_times.popleft()

    def _should_attempt_recovery(self) -> bool:
        """Check if enough time has passed to try a recovery probe.

        Caller MUST hold ``_lock``.
        """
        return (time.monotonic() - self._opened_at) >= self._recovery_timeout

    def _time_until_recovery(self) -> float:
        """Seconds remaining before the next recovery attempt.

        Caller MUST hold ``_lock``.
        """
        return max(0.0, self._recovery_timeout - (time.monotonic() - self._opened_at))


# ═══════════════════════════════════════════════════════════════
# Per-language singleton registry
# ═══════════════════════════════════════════════════════════════

_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()
_DEFAULT_THRESHOLD = 5
_DEFAULT_RECOVERY = 30.0
_WINDOW_SECONDS = 60.0
_DEFAULT_HALF_OPEN_MAX = 1


def get_breaker(
    language: str,
    failure_threshold: int = _DEFAULT_THRESHOLD,
    recovery_timeout: float = _DEFAULT_RECOVERY,
    window_seconds: float = _WINDOW_SECONDS,
    half_open_max: int = _DEFAULT_HALF_OPEN_MAX,
) -> CircuitBreaker:
    """Return (or create) a CircuitBreaker per language.

    >>> breaker = get_breaker("en-US")
    >>> breaker = get_breaker("es-ES")  # Different breaker — independent state

    ``half_open_max`` was accepted by the constructor but not by this factory,
    so probe throttling could not be configured through the registry — the same
    "config value nobody reads" gap the rolling window had.
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
                    half_open_max=half_open_max,
                )
    return _breakers[key]


def get_all_breakers() -> dict:
    """Return snapshots of all registered circuit breakers."""
    with _breakers_lock:
        return {lang: cb.snapshot() for lang, cb in _breakers.items()}
