import logging
import random
import time
from urllib.parse import quote

from scrapling import Fetcher

from config.settings import settings
from models.lead import Lead
from utils.rate_limiter import RateLimiter
from utils.retry import sync_retry
from utils.validators import normalize_phone

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 20
MAX_PAGES = 20
TIMEOUT_SECONDS = 10
SOURCE = "guia_oleo"


def split_query(query: str) -> tuple[str, str]:
    """Split a 'term location' query into (term, location).

    The first whitespace token is the term; the remainder is the location.
    When settings.LOCATION is set it overrides the parsed location.
    """
    parts = query.strip().split()
    term = parts[0] if parts else query.strip()
    parsed_location = " ".join(parts[1:]) if len(parts) > 1 else ""
    location = settings.LOCATION or parsed_location
    return term, location


class GuiaOleoScraper:
    """Scrapes restaurants and bars from Guía Oleo (Argentina).

    Static HTML site searched by cuisine/name and zone, paginated up to the
    requested limit.
    """

    BASE_URL = "https://www.guiaoleo.com.ar"
    SEARCH_URL = BASE_URL + "/buscar?q={query}&zona={zona}"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point. Query is '<cuisine> <zone>'."""
        term, location = split_query(query)
        return self.search(term, location, limit)

    def search(self, query: str, location: str, limit: int) -> list[Lead]:
        """Search restaurants and bars on Guía Oleo.

        Args:
            query: Cuisine type or name (e.g. "sushi", "parrilla", "cafe").
            location: Neighborhood or city (e.g. "palermo", "recoleta").
            limit: Maximum number of leads.

        Returns:
            List of Leads with name, phone, address, website, category, rating.
        """
        leads: list[Lead] = []
        seen: set[str] = set()
        q = quote(query.strip())
        zona = quote((location or "").strip())

        for page_num in range(1, MAX_PAGES + 1):
            if len(leads) >= limit:
                break
            url = self.SEARCH_URL.format(query=q, zona=zona)
            if page_num > 1:
                url += f"&page={page_num}"

            page = self._fetch(url)
            if page is None:
                break

            added = 0
            for lead in self._parse_results(page):
                key = lead.name.lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                leads.append(lead)
                added += 1
                if len(leads) >= limit:
                    break
            if added == 0:
                logger.info("No new results on page %d for %r — stopping", page_num, query)
                break
            self._random_delay()

        logger.info("Guía Oleo scrape complete: %d leads", len(leads))
        return leads[:limit]

    def _fetch(self, url: str):
        """Fetch a URL with Fetcher, returning None on any failure."""
        try:
            self.rate_limiter.acquire_sync()
            page = sync_retry(
                lambda: Fetcher.get(url, timeout=TIMEOUT_SECONDS, stealthy_headers=True, retries=1),
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

    def _parse_results(self, page) -> list[Lead]:
        """Parse restaurant cards from a results page into Leads."""
        cards = page.find_all(".resto-card") or page.find_all('[itemtype*="Restaurant"]')
        leads: list[Lead] = []
        for card in cards:
            lead = self._parse_card(card)
            if lead is not None:
                leads.append(lead)
        return leads

    @staticmethod
    def _parse_card(card) -> Lead | None:
        """Parse a single restaurant card element into a Lead."""
        name_el = card.find(".resto-name") or card.find('[itemprop="name"]') or card.find("h2")
        name = name_el.text.strip() if name_el else ""
        if not name:
            return None

        addr_el = card.find(".resto-address") or card.find('[itemprop="address"]')
        address = addr_el.text.strip() if addr_el else ""

        cuisine_el = card.find(".resto-cuisine") or card.find('[itemprop="servesCuisine"]')
        category = cuisine_el.text.strip() if cuisine_el else ""

        phone = ""
        phone_el = card.find('[itemprop="telephone"]') or card.find(".resto-phone")
        if phone_el:
            phone = normalize_phone(phone_el.text.strip(), default_country="AR") or ""
        if not phone:
            tel = card.find('a[href^="tel:"]')
            if tel:
                phone = normalize_phone(tel.attrib.get("href", "").removeprefix("tel:"),
                                        default_country="AR") or ""

        website = ""
        web_el = card.find("a.resto-website") or card.find('a[itemprop="url"]')
        if web_el:
            website = web_el.attrib.get("href", "").strip()

        rating = 0.0
        rating_el = card.find(".resto-rating") or card.find('[itemprop="ratingValue"]')
        if rating_el:
            try:
                rating = float(rating_el.text.strip().replace(",", "."))
            except ValueError:
                rating = 0.0

        return Lead(
            name=name,
            phone=phone,
            website=website,
            address=address,
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
