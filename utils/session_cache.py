"""In-memory cache for the lifetime of a single TUI session.

Faster than the on-disk :mod:`utils.cache` (no JSON I/O, no TTL checks) and
scoped to the running process: it lets a repeated ``source+query`` combination
(e.g. the same search across the multi-query ``--`` flow or a "new search" loop)
reuse already-scraped leads. It is cleared when the TUI starts.
"""
import logging

from models.lead import Lead

logger = logging.getLogger(__name__)


class SessionCache:
    """Process-wide in-memory cache keyed by ``source:query`` (case-insensitive)."""

    _cache: dict[str, list[Lead]] = {}

    @staticmethod
    def _key(source: str, query: str) -> str:
        return f"{source}:{query.lower().strip()}"

    @classmethod
    def get(cls, source: str, query: str) -> list[Lead] | None:
        """Return cached leads for this source+query, or None on a miss."""
        leads = cls._cache.get(cls._key(source, query))
        if leads is not None:
            logger.info("SessionCache hit for %s/%r (%d leads)", source, query, len(leads))
        return leads

    @classmethod
    def set(cls, source: str, query: str, leads: list[Lead]) -> None:
        """Store leads for this source+query for the rest of the session."""
        cls._cache[cls._key(source, query)] = leads

    @classmethod
    def clear(cls) -> None:
        """Drop every cached entry (called when a TUI session starts)."""
        cls._cache.clear()
