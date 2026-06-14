import json
import logging
import random
import re
import time

from scrapling import StealthyFetcher

from config.settings import settings
from models.lead import Lead
from scrapers.query_utils import split_query
from utils.abort import AbortMixin
from utils.browser_config import get_stealth_fetch_kwargs
from utils.rate_limiter import RateLimiter
from utils.validators import normalize_phone

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 8
TIMEOUT_MS = 25000
MAX_PAGES = 5
RESULTS_PER_PAGE = 30
FETCH_ATTEMPTS = 4
SOURCE = "tripadvisor_ar"

# TripAdvisor geo ids for the restaurant listing URL. Defaults to Buenos Aires;
# unknown locations fall back to it (the listing still covers the whole city).
_GEO_IDS = {
    "buenos aires": "g312741",
    "caba": "g312741",
    "capital federal": "g312741",
    "palermo": "g312741",
    "cordoba": "g312789",
    "rosario": "g312875",
    "mendoza": "g312766",
    "mar del plata": "g312762",
}
_DEFAULT_GEO = "g312741"
_GEO_SLUG = "Buenos_Aires_Capital_Federal_District"


class TripAdvisorARScraper(AbortMixin):
    """Scrapes restaurants from TripAdvisor Argentina.

    TripAdvisor is behind Cloudflare/anti-bot, so pages are fetched with
    ``StealthyFetcher`` (``solve_cloudflare=True``). A city listing yields
    restaurant detail URLs (filtered by the query term); each detail page
    exposes a ``FoodEstablishment`` JSON-LD block with name, telephone, address
    and rating. Detail pages occasionally answer 403 — ``sync_retry`` handles it.
    """

    BASE_URL = "https://www.tripadvisor.com.ar"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)
        self.source = SOURCE
        self.aborted_reason = ""

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point. Query is '<cuisine> <location>'."""
        term, location = split_query(query)
        return self.search_restaurants(term, location, limit)

    def search_restaurants(self, query: str, location: str, limit: int) -> list[Lead]:
        """Search restaurants on TripAdvisor Argentina.

        Args:
            query: Cuisine type or name (e.g. "sushi", "parrilla", "cafe").
            location: City or neighborhood (e.g. "Buenos Aires", "Palermo").
            limit: Maximum number of leads to return.

        Returns:
            List of Leads with name, phone, address, website, category, rating.
        """
        geo = _GEO_IDS.get((location or "").strip().lower(), _DEFAULT_GEO)
        term = (query or "").strip().lower()

        detail_urls: list[str] = []
        seen_urls: set[str] = set()
        self._start_guard()
        for page_num in range(MAX_PAGES):
            if len(detail_urls) >= limit or self._should_abort():
                break
            listing = self._fetch(
                self._listing_url(geo, page_num),
                validator=lambda p: bool(p.css('a[href*="Restaurant_Review"]::attr(href)').getall()),
            )
            self._record_fetch(listing is not None)
            if listing is None:
                break
            found = self._collect_restaurant_urls(listing, term)
            new = [u for u in found if u not in seen_urls]
            if not new:
                logger.info("No new matching restaurants on listing page %d", page_num + 1)
                break
            for u in new:
                seen_urls.add(u)
                detail_urls.append(u)
            self._random_delay()

        leads: list[Lead] = []
        seen_names: set[str] = set()
        for url in detail_urls:
            if len(leads) >= limit or self._should_abort():
                break
            lead = self.scrape_restaurant_detail(url)
            self._record_fetch(lead is not None)
            if lead is None or not lead.name:
                continue
            key = lead.name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            leads.append(lead)
            self._random_delay()

        logger.info("TripAdvisor scrape complete: %d leads", len(leads))
        return leads[:limit]

    def scrape_restaurant_detail(self, restaurant_url: str) -> Lead | None:
        """Extract a restaurant's data from its TripAdvisor detail page.

        Args:
            restaurant_url: Full TripAdvisor restaurant review URL.

        Returns:
            A Lead built from the ``FoodEstablishment`` JSON-LD, or None.
        """
        page = self._fetch(
            restaurant_url,
            validator=lambda p: self._food_establishment_ld(p) is not None,
        )
        if page is None:
            return None
        data = self._food_establishment_ld(page)
        if not data:
            return None

        name = (data.get("name") or "").strip()
        if not name:
            return None

        phone = normalize_phone(str(data.get("telephone") or ""), default_country="AR") or ""

        address = ""
        addr = data.get("address")
        if isinstance(addr, dict):
            parts = [addr.get("streetAddress"), addr.get("addressLocality"),
                     addr.get("addressRegion")]
            address = ", ".join(p for p in parts if p)
        elif isinstance(addr, str):
            address = addr

        cuisine = data.get("servesCuisine")
        if isinstance(cuisine, list):
            category = ", ".join(str(c) for c in cuisine)
        else:
            category = str(cuisine) if cuisine else "Restaurante"

        rating = 0.0
        agg = data.get("aggregateRating")
        if isinstance(agg, dict):
            try:
                rating = float(str(agg.get("ratingValue")).replace(",", "."))
            except (ValueError, TypeError):
                rating = 0.0

        website = self._extract_website(page)

        return Lead(
            name=name,
            phone=phone,
            website=website,
            address=address,
            category=category,
            rating=rating,
            source=SOURCE,
            raw_data={"tripadvisor_url": restaurant_url},
        )

    def _listing_url(self, geo: str, page_num: int) -> str:
        """Build the restaurant listing URL for a geo id and 0-based page."""
        if page_num == 0:
            return f"{self.BASE_URL}/Restaurants-{geo}-{_GEO_SLUG}.html"
        offset = page_num * RESULTS_PER_PAGE
        return f"{self.BASE_URL}/Restaurants-{geo}-oa{offset}-{_GEO_SLUG}.html"

    @classmethod
    def _collect_restaurant_urls(cls, page, term: str) -> list[str]:
        """Collect unique restaurant detail URLs from a listing page.

        Restaurants are deduplicated by their ``-d{id}-`` key. The query term is
        used as a soft prioritizer (matches on the URL slug come first), not a
        hard filter — TripAdvisor's cuisine filtering requires opaque cuisine
        codes, so a strict term filter would drop most real leads.
        """
        matches: list[str] = []
        rest: list[str] = []
        seen_ids: set[str] = set()

        for href in page.css('a[href*="Restaurant_Review"]::attr(href)').getall():
            clean = href.split("#")[0].split("?")[0]
            m = re.search(r"-d(\d+)-", clean)
            key = m.group(1) if m else clean
            if key in seen_ids:
                continue
            seen_ids.add(key)
            full = clean if clean.startswith("http") else cls.BASE_URL + clean
            if term and term in clean.lower():
                matches.append(full)
            else:
                rest.append(full)
        return matches + rest

    @staticmethod
    def _food_establishment_ld(page) -> dict | None:
        """Return the FoodEstablishment/Restaurant JSON-LD object, if present."""
        for raw in page.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            candidates = data if isinstance(data, list) else [data]
            for obj in candidates:
                if not isinstance(obj, dict):
                    continue
                type_str = str(obj.get("@type", ""))
                if "Restaurant" in type_str or "FoodEstablishment" in type_str or obj.get("telephone"):
                    return obj
        return None

    @staticmethod
    def _extract_website(page) -> str:
        """Best-effort extraction of the restaurant's own website link."""
        for href in page.css('a[href^="http"]::attr(href)').getall():
            low = href.lower()
            if "tripadvisor" in low or "/redirect" in low or "booking" in low:
                continue
            if any(s in low for s in ("facebook.", "instagram.", "twitter.", "x.com/")):
                continue
            return href.strip()
        return ""

    def _fetch(self, url: str, validator=None):
        """Fetch a Cloudflare-protected URL with StealthyFetcher.

        TripAdvisor intermittently answers 403 or a soft-blocked 200 with no
        listing content, so this retries on both transport failures and pages
        that fail ``validator`` (a callable returning True when the page has the
        expected content). Returns the page on success, or None after exhausting
        ``FETCH_ATTEMPTS``.
        """
        # network_idle=False: the data we need (review links, FoodEstablishment
        # JSON-LD) is server-rendered in the initial HTML, so waiting for network
        # idle on these heavy pages only adds latency and bot-detection surface.
        kwargs = get_stealth_fetch_kwargs(timeout=TIMEOUT_MS, solve_cloudflare=True)
        if settings.PROXY_URL:
            kwargs["proxy"] = settings.PROXY_URL
            # Log the proxy with its password redacted (OWASP A09).
            logger.debug("TripAdvisor using proxy %s", settings.get_safe_proxy_url())

        for attempt in range(1, FETCH_ATTEMPTS + 1):
            self.rate_limiter.acquire_sync()
            try:
                page = StealthyFetcher.fetch(url, **kwargs)
            except Exception as exc:
                logger.warning("Fetch error %s (attempt %d/%d): %s",
                               url, attempt, FETCH_ATTEMPTS, exc)
                self._random_delay()
                continue

            status = getattr(page, "status", 200)
            if status != 200:
                logger.warning("Non-200 status %s for %s (attempt %d/%d)",
                               status, url, attempt, FETCH_ATTEMPTS)
                self._random_delay()
                continue

            if validator is not None and not validator(page):
                logger.info("Soft-block / unexpected content for %s (attempt %d/%d)",
                            url, attempt, FETCH_ATTEMPTS)
                self._random_delay()
                continue

            return page

        logger.error("All %d fetch attempts failed for %s", FETCH_ATTEMPTS, url)
        return None

    @staticmethod
    def _random_delay() -> None:
        """Sleep a random interval between requests using the configured delays."""
        lo = max(0.0, settings.SCRAPING_DELAY_MIN)
        hi = max(lo, settings.SCRAPING_DELAY_MAX)
        time.sleep(random.uniform(lo, hi))
