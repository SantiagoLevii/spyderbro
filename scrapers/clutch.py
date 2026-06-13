import logging
import random
import time
from urllib.parse import parse_qs, quote, unquote, urlparse

from scrapling import StealthyFetcher

from config.settings import settings
from models.lead import Lead
from scrapers.query_utils import split_query
from utils.rate_limiter import RateLimiter
from utils.retry import sync_retry

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 15
MAX_PAGES = 10
TIMEOUT_MS = 60000
SOURCE = "clutch"


class ClutchScraper:
    """Scrapes digital agencies and service firms from Clutch.co.

    Clutch is behind Cloudflare, so pages are fetched with ``StealthyFetcher``
    (``solve_cloudflare=True``). The agency directory at ``/agencies/{service}``
    renders ``.provider`` cards whose data (name, website, location, rating) is
    read directly from the HTML.

    Country filtering note: Clutch removed its per-country directory URLs
    (``/agencies/{service}/{country}`` now 404) and the geo query param no longer
    filters, so ``country`` is applied as a best-effort client-side filter on each
    card's location. The global service ranking is US-dominated, so non-US
    countries (e.g. Argentina) typically yield few or no matches.
    """

    BASE_URL = "https://clutch.co"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point. Query is '<service-type> <country>'."""
        service_type, country = split_query(query)
        return self.search_agencies(service_type, country, limit)

    def search_agencies(self, service_type: str, country: str, limit: int) -> list[Lead]:
        """Search agencies on Clutch by service type, filtered by country.

        Args:
            service_type: Service slug (digital-marketing, web-design, seo, ...).
            country: Country in English; applied as a client-side location filter.
            limit: Maximum number of leads.

        Returns:
            List of Leads with name, website, address, category, rating.
        """
        leads: list[Lead] = []
        seen: set[str] = set()
        service = quote(service_type.strip().replace(" ", "-")) or "digital-marketing"
        country_norm = country.strip().lower()

        for page_num in range(1, MAX_PAGES + 1):
            if len(leads) >= limit:
                break
            url = f"{self.BASE_URL}/agencies/{service}"
            if page_num > 1:
                url += f"?page={page_num - 1}"

            page = self._fetch(url)
            if page is None:
                break

            cards = page.find_all(".provider")
            if not cards:
                logger.info("No provider cards on page %d for %r", page_num, service_type)
                break

            added = 0
            for card in cards:
                lead = self._parse_card(card, service_type)
                if lead is None:
                    continue
                key = lead.name.lower()
                if key in seen:
                    continue
                if country_norm and country_norm not in (lead.address or "").lower():
                    continue
                seen.add(key)
                leads.append(lead)
                added += 1
                if len(leads) >= limit:
                    break
            if added == 0:
                logger.info("No new matching results on page %d for %r — stopping",
                            page_num, service_type)
                break
            self._random_delay()

        logger.info("Clutch scrape complete: %d leads (country filter=%r)", len(leads), country)
        return leads[:limit]

    def _fetch(self, url: str):
        """Fetch a Cloudflare-protected URL with StealthyFetcher, None on failure."""
        try:
            self.rate_limiter.acquire_sync()
            page = sync_retry(
                lambda: StealthyFetcher.fetch(
                    url, headless=True, network_idle=False,
                    solve_cloudflare=True, timeout=TIMEOUT_MS,
                ),
                max_retries=2,
            )
        except Exception as exc:
            logger.error("Failed to fetch %s: %s", url, exc, exc_info=True)
            return None

        status = getattr(page, "status", 200)
        if status != 200:
            logger.warning("Non-200 status %s for %s", status, url)
            return None
        return page

    @staticmethod
    def _extract_website(card) -> str:
        """Extract the agency's real website from a Clutch redirect link.

        Clutch wraps outbound links as ``r.clutch.co/redirect?...&u=<encoded-url>``;
        the real URL is the ``u`` query parameter.
        """
        for href in card.css("a::attr(href)").getall():
            if "r.clutch.co/redirect" in href and "u=" in href:
                params = parse_qs(urlparse(href).query)
                if params.get("u"):
                    return unquote(params["u"][0]).split("?")[0].strip()
        return ""

    def _parse_card(self, card, service_type: str) -> Lead | None:
        """Parse a single ``.provider`` card into a Lead.

        Maps: ``.provider__title-link`` -> name, redirect ``u`` param -> website,
        ``.location`` text -> address, ``.sg-rating__number`` -> rating.
        """
        name_el = card.find(".provider__title-link") or card.find("h3")
        name = name_el.text.strip() if name_el else ""
        if not name:
            return None

        website = self._extract_website(card)

        loc_el = card.find(".location")
        address = ""
        if loc_el:
            address = " ".join(t.strip() for t in loc_el.css("::text").getall() if t.strip())

        rating = 0.0
        rating_el = card.find(".sg-rating__number")
        if rating_el:
            try:
                rating = float(rating_el.text.strip().replace(",", "."))
            except ValueError:
                rating = 0.0

        return Lead(
            name=name,
            website=website,
            address=address,
            category=service_type,
            rating=rating,
            source=SOURCE,
        )

    @staticmethod
    def _random_delay() -> None:
        """Sleep a random interval between requests using the configured delays."""
        lo = max(0.0, settings.SCRAPING_DELAY_MIN)
        hi = max(lo, settings.SCRAPING_DELAY_MAX)
        time.sleep(random.uniform(lo, hi))
