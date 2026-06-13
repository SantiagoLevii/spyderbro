import logging
import random
import time
from urllib.parse import quote

from scrapling import StealthyFetcher

from config.settings import settings
from models.lead import Lead
from utils.rate_limiter import RateLimiter
from utils.retry import sync_retry
from utils.validators import is_valid_email

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 15
MAX_PAGES = 20
RESULTS_PER_PAGE = 50
TIMEOUT_MS = 60000
SOURCE = "mercadolibre"

# Markers of MercadoLibre's "snoopy" anti-bot micro-landing shell, served with
# HTTP 200 in place of the real listing when automation is detected.
_ANTIBOT_MARKERS = ("micro-landing-container", "snoopy-script")


class MercadoLibreScraper:
    """Scrapes sellers (official stores / high-volume) from MercadoLibre Argentina.

    The lead is the seller/store, not the product. Leads are deduplicated by
    store name before returning.
    """

    BASE_URL = "https://www.mercadolibre.com.ar"
    LISTADO_URL = "https://listado.mercadolibre.com.ar"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point. Query is the product category."""
        return self.search_sellers(query, limit)

    def search_sellers(self, category: str, limit: int) -> list[Lead]:
        """Extract official-store sellers from MercadoLibre.

        The lead is the seller/store, not the product.

        Args:
            category: Product category (e.g. "electronica", "ropa", "hogar").
            limit: Maximum number of unique-seller leads.

        Returns:
            List of unique Leads — one per seller.
        """
        leads: list[Lead] = []
        seen: set[str] = set()
        cat = quote(category.strip().replace(" ", "-"))

        for page_num in range(1, MAX_PAGES + 1):
            if len(leads) >= limit:
                break
            url = f"{self.LISTADO_URL}/{cat}"
            if page_num > 1:
                offset = (page_num - 1) * RESULTS_PER_PAGE + 1
                url += f"_Desde_{offset}"

            page = self._fetch(url)
            if page is None:
                break

            added = 0
            for lead in self._parse_sellers(page, category):
                key = lead.name.lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                leads.append(lead)
                added += 1
                if len(leads) >= limit:
                    break
            if added == 0:
                logger.info("No new sellers on page %d for %r — stopping", page_num, category)
                break
            self._random_delay()

        logger.info("MercadoLibre scrape complete: %d unique sellers", len(leads))
        return leads[:limit]

    def _fetch(self, url: str):
        """Fetch a URL with StealthyFetcher, returning None on any failure.

        MercadoLibre's "snoopy" anti-bot serves a tiny micro-landing shell (HTTP
        200) instead of the listing when it detects automation; that case is
        logged explicitly. Set ``PROXY_URL`` to a residential proxy to improve
        the odds of receiving the real page.
        """
        kwargs = dict(headless=True, solve_cloudflare=True, timeout=TIMEOUT_MS)
        if settings.PROXY_URL:
            kwargs["proxy"] = settings.PROXY_URL
        try:
            self.rate_limiter.acquire_sync()
            page = sync_retry(
                lambda: StealthyFetcher.fetch(url, **kwargs),
                max_retries=2,
            )
        except Exception as exc:
            logger.error("Failed to fetch %s: %s", url, exc, exc_info=True)
            return None

        status = getattr(page, "status", 200)
        if status != 200:
            logger.warning("Non-200 status %s for %s", status, url)
            return None

        html = page.html_content or ""
        if any(marker in html for marker in _ANTIBOT_MARKERS):
            logger.warning(
                "MercadoLibre anti-bot (snoopy) blocked %s — served micro-landing "
                "shell instead of listing; set PROXY_URL to bypass", url
            )
            return None
        return page

    def _parse_sellers(self, page, category: str) -> list[Lead]:
        """Extract sellers/stores from product result cards."""
        cards = page.find_all(".ui-search-result__wrapper") or page.find_all(".andes-card")
        leads: list[Lead] = []
        for card in cards:
            lead = self._parse_seller(card, category)
            if lead is not None:
                leads.append(lead)
        return leads

    @staticmethod
    def _parse_seller(card, category: str) -> Lead | None:
        """Parse the seller/store from a single product card."""
        store_el = (
            card.find(".ui-search-official-store-label")
            or card.find(".ui-search-item__group__element--seller")
            or card.find(".store-name")
        )
        name = store_el.text.strip() if store_el else ""
        if not name:
            return None
        name = name.removeprefix("Por ").removeprefix("Tienda oficial de ").strip()
        if not name:
            return None

        website = ""
        link = card.find("a.ui-search-official-store-label") or card.find("a.store-link")
        if link:
            website = link.attrib.get("href", "").strip()

        rating = 0.0
        rating_el = card.find(".ui-search-reviews__rating-number") or card.find(".store-reputation")
        if rating_el:
            try:
                rating = float(rating_el.text.strip().replace(",", "."))
            except ValueError:
                rating = 0.0

        email = ""
        mail_el = card.find('a[href^="mailto:"]')
        if mail_el:
            candidate = mail_el.attrib.get("href", "").removeprefix("mailto:").split("?")[0].strip()
            if is_valid_email(candidate):
                email = candidate.lower()

        return Lead(
            name=name,
            email=email,
            website=website,
            category=category,
            rating=rating,
            source=SOURCE,
        )

    @staticmethod
    def _random_delay() -> None:
        """Sleep a random interval between requests using the configured delays."""
        lo = max(0.0, settings.SCRAPING_DELAY_MIN)
        hi = max(lo, settings.SCRAPING_DELAY_MAX)
        time.sleep(random.uniform(lo, hi))
