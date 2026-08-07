"""Backpressure guard — reject new work when the queue is too deep.

Prevents overload by returning HTTP 503 before the system accepts work it
cannot reasonably complete.
"""

import threading


class BackpressureGuard:
    """Prevent overload by rejecting new work when the queue is too deep.

    Example::

        guard = BackpressureGuard(max_queue_depth=100)
        if not guard.try_accept():
            return HTTP_503  # "Service overloaded, try again later"
        try:
            do_work()
        finally:
            guard.release()
    """

    def __init__(self, max_queue_depth: int = 100):
        self._max_queue_depth = max_queue_depth
        self._pending_count: int = 0
        self._lock = threading.Lock()

    def try_accept(self) -> bool:
        """Return True if new work can be accepted. Increments pending count.

        Must be paired with a matching ``release()`` call when work completes,
        succeeds, or fails.
        """
        with self._lock:
            if self._pending_count >= self._max_queue_depth:
                return False
            self._pending_count += 1
            return True

    def release(self):
        """Decrement pending count when work completes (success or failure)."""
        with self._lock:
            if self._pending_count > 0:
                self._pending_count -= 1

    def is_backpressured(self) -> bool:
        """True when the queue is at or above the threshold."""
        with self._lock:
            return self._pending_count >= self._max_queue_depth

    @property
    def queue_depth(self) -> int:
        """Current number of in-flight / pending items."""
        with self._lock:
            return self._pending_count

    @property
    def max_queue_depth(self) -> int:
        return self._max_queue_depth

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "queue_depth": self._pending_count,
                "max_queue_depth": self._max_queue_depth,
                "backpressured": self._pending_count >= self._max_queue_depth,
            }


# Module-level singleton
backpressure = BackpressureGuard(max_queue_depth=100)
