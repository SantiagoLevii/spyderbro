import asyncio
import logging
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60.0


class RateLimiter:
    """Sliding-window rate limiter usable from async and sync code.

    Guarantees no more than requests_per_minute acquisitions in any
    60-second window. Async callers use acquire(); sync callers (thread
    pool scrapers) use acquire_sync(). Both share the same window.
    """

    def __init__(self, requests_per_minute: int = 10) -> None:
        """Create the limiter.

        Args:
            requests_per_minute: Maximum acquisitions per 60s window.
        """
        self.requests_per_minute = max(1, requests_per_minute)
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def _wait_time(self) -> float:
        """Return seconds to wait before the next slot is free (0 if free now).

        Must be called with the lock held. Registers the acquisition when a
        slot is free.
        """
        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] >= WINDOW_SECONDS:
            self._timestamps.popleft()

        if len(self._timestamps) < self.requests_per_minute:
            self._timestamps.append(now)
            return 0.0

        return WINDOW_SECONDS - (now - self._timestamps[0])

    async def acquire(self) -> None:
        """Wait (non-blocking for the event loop) until a request slot is free."""
        while True:
            with self._lock:
                wait = self._wait_time()
            if wait <= 0:
                return
            logger.debug("Rate limit reached — waiting %.1fs", wait)
            await asyncio.sleep(wait)

    def acquire_sync(self) -> None:
        """Block the current thread until a request slot is free."""
        while True:
            with self._lock:
                wait = self._wait_time()
            if wait <= 0:
                return
            logger.debug("Rate limit reached — waiting %.1fs", wait)
            time.sleep(wait)
