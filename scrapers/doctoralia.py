import logging
import random
import time
from urllib.parse import quote

from scrapling import StealthyFetcher

from config.settings import settings
from models.lead import Lead
from scrapers.guia_oleo import split_query
from utils.rate_limiter import RateLimiter
from utils.retry import sync_retry
from utils.validators import normalize_phone

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 10
MAX_PAGES = 20
TIMEOUT_MS = 60000
SOURCE = "doctoralia"


class DoctoraliaScraper:
    """Scrapes health professionals from Doctoralia Argentina.

    Uses StealthyFetcher (JS/anti-bot). Searched by specialty and city,
    paginated up to the requested limit.
    """

    BASE_URL = "https://www.doctoralia.com.ar"
    SEARCH_URL = BASE_URL + "/buscar?q={specialty}&loc={location}"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point. Query is '<specialty> <city>'."""
        specialty, location = split_query(query)
        return self.search(specialty, location, limit)

    def search(self, specialty: str, location: str, limit: int) -> list[Lead]:
        """Search doctors and health professionals on Doctoralia.

        Args:
            specialty: Medical specialty (e.g. "dentista", "nutricionista").
            location: City (e.g. "buenos-aires", "cordoba", "rosario").
            limit: Maximum number of leads.

        Returns:
            List of Leads with name, category, address, phone, website, rating.
        """
        leads: list[Lead] = []
        seen: set[str] = set()
        spec = quote(specialty.strip())
        loc = quote((location or "").strip())

        for page_num in range(1, MAX_PAGES + 1):
            if len(leads) >= limit:
                break
            url = self.SEARCH_URL.format(specialty=spec, location=loc)
            if page_num > 1:
                url += f"&page={page_num}"

            page = self._fetch(url)
            if page is None:
                break

            added = 0
            for lead in self._parse_results(page, specialty):
                key = lead.name.lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                leads.append(lead)
                added += 1
                if len(leads) >= limit:
                    break
            if added == 0:
                logger.info("No new results on page %d for %r — stopping", page_num, specialty)
                break
            self._random_delay()

        logger.info("Doctoralia scrape complete: %d leads", len(leads))
        return leads[:limit]

    def _fetch(self, url: str):
        """Fetch a URL with StealthyFetcher, returning None on any failure.

        Doctoralia drops connections from datacenter IPs (geo/IP block); set
        ``PROXY_URL`` to a residential Argentine proxy to reach it.
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
        return page

    def _parse_results(self, page, specialty: str) -> list[Lead]:
        """Parse doctor cards from a results page into Leads."""
        cards = page.find_all(".doctor-card") or page.find_all('[itemtype*="Physician"]')
        leads: list[Lead] = []
        for card in cards:
            lead = self._parse_card(card, specialty)
            if lead is not None:
                leads.append(lead)
        return leads

    @staticmethod
    def _parse_card(card, specialty: str) -> Lead | None:
        """Parse a single doctor card element into a Lead."""
        name_el = card.find(".doctor-name") or card.find('[itemprop="name"]') or card.find("h3")
        name = name_el.text.strip() if name_el else ""
        if not name:
            return None

        spec_el = card.find(".doctor-specialty") or card.find('[itemprop="medicalSpecialty"]')
        category = spec_el.text.strip() if spec_el else specialty

        addr_el = card.find(".doctor-address") or card.find('[itemprop="address"]')
        address = addr_el.text.strip() if addr_el else ""

        phone = ""
        phone_el = card.find('[itemprop="telephone"]') or card.find(".doctor-phone")
        if phone_el:
            phone = normalize_phone(phone_el.text.strip(), default_country="AR") or ""
        if not phone:
            tel = card.find('a[href^="tel:"]')
            if tel:
                phone = normalize_phone(tel.attrib.get("href", "").removeprefix("tel:"),
                                        default_country="AR") or ""

        website = ""
        web_el = card.find("a.doctor-website") or card.find('a[itemprop="url"]')
        if web_el:
            website = web_el.attrib.get("href", "").strip()

        rating = 0.0
        rating_el = card.find(".doctor-rating") or card.find('[itemprop="ratingValue"]')
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
