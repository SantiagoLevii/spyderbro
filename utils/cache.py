import hashlib
import json
import logging
import time
from pathlib import Path

from models.lead import Lead
from utils.file_utils import ensure_dir

logger = logging.getLogger(__name__)

CACHE_DIR = ".cache"
TTL_SECONDS = 24 * 60 * 60


class ScrapingCache:
    """File-based JSON cache for scraping results with a 24-hour TTL."""

    def __init__(self, cache_dir: str = CACHE_DIR, ttl_seconds: int = TTL_SECONDS) -> None:
        """Create the cache handler.

        Args:
            cache_dir: Directory where cache files are stored.
            ttl_seconds: Maximum age of a cache entry before it is ignored.
        """
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds

    def get(self, source: str, query: str) -> list[Lead] | None:
        """Return cached leads if they exist and are fresher than the TTL.

        Args:
            source: Scraper source name.
            query: Search query used for the scrape.

        Returns:
            Cached leads, or None if there is no cache or it is expired.
        """
        path = self._path(source, query)
        age = self.age_seconds(source, query)

        if age is None:
            return None
        if age > self.ttl_seconds:
            logger.info("Cache expired for %s/%r (%.0fh old)", source, query, age / 3600)
            return None

        try:
            with path.open(encoding="utf-8") as f:
                payload = json.load(f)
            leads = [Lead(**item) for item in payload["leads"]]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Could not read cache file %s: %s", path, exc)
            return None

        logger.info("Cache hit for %s/%r: %d leads", source, query, len(leads))
        return leads

    def set(self, source: str, query: str, leads: list[Lead]) -> None:
        """Store leads as JSON in .cache/{source}_{query_hash}.json.

        Args:
            source: Scraper source name.
            query: Search query used for the scrape.
            leads: Leads to cache.
        """
        ensure_dir(self.cache_dir)
        path = self._path(source, query)

        payload = {
            "source": source,
            "query": query,
            "cached_at": time.time(),
            "leads": [lead.to_dict() for lead in leads],
        }

        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("Could not write cache file %s: %s", path, exc)
            return

        logger.info("Cached %d leads for %s/%r at %s", len(leads), source, query, path)

    def clear(self, source: str | None = None) -> int:
        """Delete cache files, all of them or only those of one source.

        Args:
            source: If given, only clear entries for this source.

        Returns:
            Number of cache files deleted.
        """
        if not self.cache_dir.is_dir():
            return 0

        deleted = 0
        prefix = f"{source}_" if source else ""
        for entry in self.cache_dir.glob("*.json"):
            if prefix and not entry.name.startswith(prefix):
                continue
            try:
                entry.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning("Could not delete cache file %s: %s", entry.name, exc)

        logger.info("Cache cleared: %d files deleted (source=%s)", deleted, source or "all")
        return deleted

    def age_seconds(self, source: str, query: str) -> float | None:
        """Return the age in seconds of a cache entry, or None if missing."""
        path = self._path(source, query)
        try:
            with path.open(encoding="utf-8") as f:
                payload = json.load(f)
            return time.time() - float(payload["cached_at"])
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return None

    def _path(self, source: str, query: str) -> Path:
        """Build the cache file path for a source/query pair."""
        query_hash = hashlib.md5(query.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{source}_{query_hash}.json"
