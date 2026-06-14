"""Browser pool for reusing Playwright instances across requests.

Opening and closing a stealth browser per request costs 1-3s of pure overhead.
This is the scaffolding for reusing instances; full reuse needs access to
Scrapling's internal Playwright session, so for now it is a safe no-op stub and
the real win comes from resource blocking in :mod:`utils.browser_config`.
"""
import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class BrowserPool:
    """Thread-safe pool of Playwright browsers, created on demand up to a max.

    Each browser costs roughly 200MB RAM. This is a progressive-enhancement
    stub: ``get_browser`` currently yields ``None`` (callers fall back to
    per-request fetching) but the interface is stable for a future upgrade.
    """

    def __init__(self, max_browsers: int = 3) -> None:
        """Args: max_browsers — maximum simultaneous browsers in the pool."""
        self._max = max_browsers
        self._pool: list = []
        self._lock = threading.Lock()
        self._active = 0

    @contextmanager
    def get_browser(self) -> Generator[None, None, None]:
        """Yield a pooled browser (currently ``None`` — delegates to the fetcher)."""
        yield None

    def close_all(self) -> None:
        """Close every browser in the pool."""
        with self._lock:
            self._pool.clear()
            self._active = 0
        logger.info("Browser pool closed")


_pool: BrowserPool | None = None


def get_pool(max_browsers: int = 3) -> BrowserPool:
    """Return the global pool instance, creating it on first use."""
    global _pool
    if _pool is None:
        _pool = BrowserPool(max_browsers=max_browsers)
    return _pool
