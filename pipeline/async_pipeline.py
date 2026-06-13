import asyncio
import logging
import time

from models.lead import Lead
from pipeline.deduplicator import Deduplicator
from utils.cache import ScrapingCache

logger = logging.getLogger(__name__)


def _default_registry() -> dict:
    """Build the default source -> scraper class registry.

    Imported lazily to keep pipeline imports light and avoid import cycles.
    """
    from scrapers.dorks import DorksScraper
    from scrapers.facebook import FacebookScraper
    from scrapers.google_maps import GoogleMapsScraper
    from scrapers.instagram import InstagramScraper
    from scrapers.linkedin import LinkedInScraper
    from scrapers.twitter import TwitterScraper

    return {
        "google_maps": GoogleMapsScraper,
        "instagram": InstagramScraper,
        "facebook": FacebookScraper,
        "twitter": TwitterScraper,
        "dorks": DorksScraper,
        "linkedin": LinkedInScraper,
    }


class AsyncScrapingPipeline:
    """Async pipeline that coordinates multiple scrapers in parallel.

    Native-async scrapers (Twitter, Dorks) run on the event loop; sync
    scrapers (Google Maps, Instagram, Facebook — Scrapling/Playwright) run
    in worker threads so they do not block the loop. After all sources
    finish, the combined pool is deduplicated.

    Attributes set after run(): duplicates_removed, scraper_seconds (sum of
    per-scraper wall time, used to estimate speedup vs sequential),
    engine_labels (extra info per source, e.g. the Dorks engine).
    """

    def __init__(self, registry: dict | None = None, cache: ScrapingCache | None = None) -> None:
        """Create the pipeline.

        Args:
            registry: source name -> scraper class. Defaults to all built-in
                scrapers; injectable for testing.
            cache: Optional cache. When given, fresh cached results are used
                instead of scraping and new results are stored.
        """
        self.registry = registry if registry is not None else _default_registry()
        self.deduplicator = Deduplicator()
        self.cache = cache
        self.duplicates_removed = 0
        self.scraper_seconds = 0.0
        self.engine_labels: dict[str, str] = {}

    async def run(
        self,
        sources: list[str],
        query: str,
        limit: int,
        max_concurrent: int = 3,
    ) -> list[Lead]:
        """Run all requested sources concurrently and deduplicate the pool.

        Args:
            sources: Source names present in the registry.
            query: Search query passed to every scraper.
            limit: Per-source lead limit.
            max_concurrent: Maximum scrapers running at the same time.

        Returns:
            Deduplicated, merged list of leads from all sources.
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        self.scraper_seconds = 0.0
        self.duplicates_removed = 0

        async def run_source(source: str) -> list[Lead]:
            async with semaphore:
                started = time.monotonic()
                leads = await self._scrape_source(source, query, limit)
                elapsed = time.monotonic() - started
                self.scraper_seconds += elapsed
                logger.info("Source %s finished: %d leads in %.1fs", source, len(leads), elapsed)
                return leads

        results = await asyncio.gather(*(run_source(s) for s in sources))
        all_leads = [lead for batch in results for lead in batch]

        unique, removed = self.deduplicator.deduplicate(all_leads)
        self.duplicates_removed = removed
        return unique

    async def _scrape_source(self, source: str, query: str, limit: int) -> list[Lead]:
        """Scrape one source, using the cache when possible. Never raises."""
        if self.cache is not None:
            cached = self.cache.get(source, query)
            if cached is not None:
                logger.info("Using cached results for %s/%r", source, query)
                return cached

        try:
            scraper = self.registry[source]()
        except Exception as exc:
            logger.error("Could not instantiate scraper %r: %s", source, exc)
            return []

        if hasattr(scraper, "allow_resume"):
            scraper.allow_resume = False

        if hasattr(scraper, "engine_label"):
            self.engine_labels[source] = scraper.engine_label

        try:
            if hasattr(scraper, "scrape_async"):
                leads = await scraper.scrape_async(query, limit)
            else:
                leads = await asyncio.to_thread(scraper.scrape, query, limit)
        except Exception as exc:
            logger.error("Scraper %s failed: %s", source, exc)
            return []

        if leads and self.cache is not None:
            self.cache.set(source, query, leads)
        return leads
