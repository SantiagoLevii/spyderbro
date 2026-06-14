import asyncio
import logging
import time

from models.lead import Lead
from pipeline.deduplicator import Deduplicator
from utils.cache import ScrapingCache

logger = logging.getLogger(__name__)

MAX_SOURCES_FOR_EMAIL = 3
MAX_LIMIT_FOR_EMAIL = 30
MIN_LEADS_PER_SOURCE = 3

# Substrings that mark an exception as an anti-bot block vs a network timeout.
_BLOCKED_TOKENS = ("403", "429", "forbidden", "too many requests", "blocked", "proxy")
_TIMEOUT_TOKENS = ("timeout", "timed out")


def _classify_exception(exc: Exception) -> str:
    """Map an unexpected scraper exception to a live-status reason.

    Returns ``"timeout"`` for network timeouts, ``"blocked"`` for HTTP
    403/429/proxy rejections, and ``"error"`` for everything else (code bugs,
    parsing failures) so the UI never mislabels a crash as anti-bot.
    """
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    msg = str(exc).lower()
    if any(token in msg for token in _TIMEOUT_TOKENS):
        return "timeout"
    if any(token in msg for token in _BLOCKED_TOKENS):
        return "blocked"
    return "error"


def should_email_scrape(sources: list[str], limit: int, user_disabled: bool = False) -> bool:
    """Decide whether website email scraping should run for this request.

    Email scraping visits one extra website per lead, so it is auto-disabled
    for broad/large runs. It stays on for focused single/few-source searches.

    Args:
        sources: Selected source names.
        limit: Total lead limit requested.
        user_disabled: True when the user passed --no-email-scraping / chose
            "fast" mode in the TUI.

    Returns:
        True if email scraping should run, False otherwise.
    """
    if user_disabled:
        return False
    if len(sources) > MAX_SOURCES_FOR_EMAIL:
        logger.info("Email scraping auto-disabled — %d sources active (>%d)",
                    len(sources), MAX_SOURCES_FOR_EMAIL)
        return False
    if limit > MAX_LIMIT_FOR_EMAIL:
        logger.info("Email scraping auto-disabled — limit %d (>%d)", limit, MAX_LIMIT_FOR_EMAIL)
        return False
    return True


def _notify(callback, source: str, status: str, target: int = 0) -> None:
    """Fire a source-status callback, swallowing any callback error."""
    if callback is None:
        return
    try:
        callback(source, status, target)
    except Exception as exc:  # noqa: BLE001 - UI callbacks must never break scraping
        logger.debug("on_source_status callback failed: %s", exc)


def _emit(callback, source: str, lead: Lead) -> None:
    """Fire a per-lead callback, swallowing any callback error."""
    if callback is None:
        return
    try:
        callback(source, lead)
    except Exception as exc:  # noqa: BLE001 - UI callbacks must never break scraping
        logger.debug("on_lead callback failed: %s", exc)


def distribute_limit(total_limit: int, sources: list[str]) -> dict[str, int]:
    """Split a total lead limit across the active sources.

    Each source gets ``max(MIN_LEADS_PER_SOURCE, total // n)``; the combined
    total may slightly exceed the limit before deduplication, which is expected.

    Args:
        total_limit: Total leads the user asked for.
        sources: Active source names.

    Returns:
        Mapping of source -> per-source limit.
    """
    if not sources:
        return {}
    per_source = max(MIN_LEADS_PER_SOURCE, total_limit // len(sources))
    return {source: per_source for source in sources}


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
        max_concurrent: int = 5,
        on_lead=None,
        on_source_status=None,
        limit_per_source: int = 0,
    ) -> list[Lead]:
        """Run all requested sources concurrently and deduplicate the pool.

        Args:
            sources: Source names present in the registry.
            query: Search query passed to every scraper.
            limit: Total lead limit, distributed across sources via
                :func:`distribute_limit` (unless ``limit_per_source`` is set).
            max_concurrent: Maximum scrapers running at the same time.
            on_lead: Optional ``(source, lead)`` callback fired for every lead as
                each source's batch arrives (drives the live feed).
            on_source_status: Optional ``(source, status, target)`` callback
                ('starting' | 'working' | 'done' | 'error').
            limit_per_source: When > 0, ignore ``limit`` and give each source
                this many leads independently (total may reach n×value).

        Returns:
            Deduplicated, merged list of leads from all sources.
        """
        semaphore = asyncio.Semaphore(min(max_concurrent, max(1, len(sources))))
        self.scraper_seconds = 0.0
        self.duplicates_removed = 0
        if limit_per_source > 0:
            per_source = {source: limit_per_source for source in sources}
        else:
            per_source = distribute_limit(limit, sources)

        for source in sources:
            _notify(on_source_status, source, "starting", per_source.get(source, limit))

        async def run_source(source: str) -> list[Lead]:
            async with semaphore:
                _notify(on_source_status, source, "working", per_source.get(source, limit))
                started = time.monotonic()
                leads, reason = await self._scrape_source(source, query, per_source.get(source, limit))
                elapsed = time.monotonic() - started
                self.scraper_seconds += elapsed
                for lead in leads:
                    _emit(on_lead, source, lead)
                if reason in ("blocked", "timeout", "error"):
                    status = "partial" if leads else reason
                else:
                    status = "done"
                _notify(on_source_status, source, status)
                logger.info("Source %s finished: %d leads in %.1fs (status=%s)",
                            source, len(leads), elapsed, status)
                return leads

        # as_completed lets us collect each source's batch the moment it finishes
        # instead of blocking on the slowest one before any processing starts.
        all_leads: list[Lead] = []
        for coro in asyncio.as_completed([run_source(s) for s in sources]):
            all_leads.extend(await coro)

        unique, removed = self.deduplicator.deduplicate(all_leads)
        self.duplicates_removed = removed
        return unique

    async def _scrape_source(self, source: str, query: str, limit: int) -> tuple[list[Lead], str]:
        """Scrape one source, using the cache when possible. Never raises.

        Returns:
            ``(leads, reason)`` where ``reason`` is ``""`` (finished normally),
            ``"blocked"`` (HTTP 403/429 or proxy rejection), ``"timeout"`` (network
            timeout / time budget exceeded) or ``"error"`` (unexpected exception /
            code bug). The reason drives the live source status.
        """
        if self.cache is not None:
            cached = self.cache.get(source, query)
            if cached is not None:
                logger.info("Using cached results for %s/%r", source, query)
                return cached, ""

        try:
            scraper = self.registry[source]()
        except Exception as exc:
            # Instantiation failures are configuration/code bugs, not anti-bot.
            logger.error("Could not instantiate scraper %r: %s", source, exc)
            return [], "error"

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
            reason = _classify_exception(exc)
            logger.error("Scraper %s failed (%s): %s", source, reason, exc)
            return [], reason

        reason = getattr(scraper, "aborted_reason", "")
        if leads and self.cache is not None:
            self.cache.set(source, query, leads)
        return leads, reason
