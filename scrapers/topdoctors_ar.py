import logging
import random
import time

from scrapling import Fetcher

from config.settings import settings
from models.lead import Lead
from scrapers.query_utils import split_query
from utils.rate_limiter import RateLimiter
from utils.validators import normalize_phone

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 15
TIMEOUT_SECONDS = 15
FETCH_ATTEMPTS = 3
SOURCE = "topdoctors_ar"

# Lay search term -> Top Doctors Argentina specialty slug (pages live at /{slug}/).
# Unmapped terms are slugified and tried directly.
_SPECIALTY_SLUGS = {
    "dentista": "odontologia",
    "odontologo": "odontologia",
    "odontologia": "odontologia",
    "dermatologo": "dermatologia",
    "dermatologia": "dermatologia",
    "cardiologo": "cardiologia-adultos",
    "cardiologia": "cardiologia-adultos",
    "endocrinologo": "endocrinologia",
    "endocrinologia": "endocrinologia",
    "traumatologo": "traumatologia",
    "traumatologia": "traumatologia",
    "ginecologo": "ginecologia-y-obstetricia",
    "ginecologia": "ginecologia-y-obstetricia",
    "oftalmologo": "oftalmologia",
    "oftalmologia": "oftalmologia",
    "psicologo": "psicologia",
    "psicologia": "psicologia",
    "cirujano": "cirugia-general",
    "cirugia": "cirugia-general",
    "nutricionista": "nutricion-y-dietetica",
    "pediatra": "pediatria",
    "neurologo": "neurologia",
}


class TopDoctorsARScraper:
    """Scrapes health professionals from Top Doctors Argentina.

    A specialty listing page (``/{specialty-slug}/``) links to doctor profiles
    (``/doctor/{slug}/``); each profile yields the doctor's name, city/address
    and specialty. The phone exposed on profiles is Top Doctors' central booking
    line (shared across doctors), stored as the contact channel for the lead.
    Static HTML, fetched with ``Fetcher``.
    """

    BASE_URL = "https://www.topdoctors.com.ar"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point. Query is '<specialty> <city>'."""
        specialty, location = split_query(query)
        return self.search(specialty, location, limit)

    def search(self, specialty: str, location: str, limit: int) -> list[Lead]:
        """Search doctors on Top Doctors Argentina by specialty.

        Args:
            specialty: Specialty term (e.g. "dermatologo", "cardiologo"). Mapped
                to a Top Doctors slug; unmapped terms are slugified and tried.
            location: City (best-effort filter on the doctor's address).
            limit: Maximum number of leads.

        Returns:
            List of Leads with name, phone, address, category, rating.
        """
        slug = self._specialty_slug(specialty)
        listing = self._fetch(f"{self.BASE_URL}/{slug}/")
        if listing is None:
            logger.warning("No listing for specialty %r (slug %r) on Top Doctors",
                           specialty, slug)
            return []

        profile_urls = self._collect_doctor_urls(listing)
        if not profile_urls:
            logger.info("No doctor profiles on Top Doctors page /%s/", slug)
            return []

        leads: list[Lead] = []
        seen: set[str] = set()
        loc = (location or "").strip().lower().replace("-", " ")
        category = specialty.strip().title()

        for url in profile_urls:
            if len(leads) >= limit:
                break
            lead = self.scrape_doctor_detail(url, category)
            if lead is None or not lead.name:
                continue
            key = lead.name.lower()
            if key in seen:
                continue
            if loc and lead.address and loc not in lead.address.lower():
                continue
            seen.add(key)
            leads.append(lead)
            self._random_delay()

        logger.info("Top Doctors scrape complete: %d leads", len(leads))
        return leads[:limit]

    def scrape_doctor_detail(self, doctor_url: str, category: str = "") -> Lead | None:
        """Extract a doctor's data from their Top Doctors profile page.

        Args:
            doctor_url: Full profile URL (``/doctor/{slug}/``).
            category: Specialty label to assign to the lead.

        Returns:
            A Lead with name, phone, address, category, or None.
        """
        page = self._fetch(doctor_url)
        if page is None:
            return None

        name = ""
        h1 = page.find("h1")
        if h1:
            name = h1.text.strip()
        if not name:
            return None

        addr_el = page.find('[itemprop="address"]') or page.find('[class*="address"]')
        address = ""
        if addr_el:
            address = " ".join(t.strip() for t in addr_el.css("::text").getall() if t.strip())
            address = address.replace("Ver en mapa", "").strip()

        # The on-page phone is Top Doctors' shared booking line (identical for
        # every doctor), so it is NOT used as Lead.phone — doing so would make the
        # deduplicator merge all doctors into one. It is kept in raw_data instead.
        booking_phone = ""
        tel = page.css('a[href^="tel:"]::attr(href)').get()
        if tel:
            booking_phone = normalize_phone(tel.removeprefix("tel:").strip(),
                                            default_country="AR") or ""

        rating = 0.0
        rating_el = page.find('[itemprop="ratingValue"]')
        if rating_el:
            try:
                rating = float(rating_el.text.strip().replace(",", "."))
            except (ValueError, AttributeError):
                rating = 0.0

        raw: dict = {"profile_url": doctor_url}
        if booking_phone:
            raw["booking_phone"] = booking_phone

        return Lead(
            name=name,
            address=address,
            category=category,
            rating=rating,
            source=SOURCE,
            raw_data=raw,
        )

    @classmethod
    def _specialty_slug(cls, specialty: str) -> str:
        """Map a specialty term to a Top Doctors slug (slugified if unmapped)."""
        term = specialty.strip().lower()
        if term in _SPECIALTY_SLUGS:
            return _SPECIALTY_SLUGS[term]
        return term.replace(" ", "-")

    @classmethod
    def _collect_doctor_urls(cls, page) -> list[str]:
        """Collect unique doctor profile URLs from a specialty listing page."""
        urls: list[str] = []
        seen: set[str] = set()
        for href in page.css('a[href*="/doctor/"]::attr(href)').getall():
            clean = href.split("#")[0].split("?")[0]
            if clean.rstrip("/").endswith("/doctor") or clean.rstrip("/") == "/doctor":
                continue
            full = clean if clean.startswith("http") else cls.BASE_URL + clean
            if full in seen:
                continue
            seen.add(full)
            urls.append(full)
        return urls

    def _fetch(self, url: str):
        """Fetch a URL with Fetcher, retrying on transport errors and 429."""
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            self.rate_limiter.acquire_sync()
            try:
                page = Fetcher.get(url, timeout=TIMEOUT_SECONDS, stealthy_headers=True)
            except Exception as exc:
                logger.warning("Fetch error %s (attempt %d/%d): %s",
                               url, attempt, FETCH_ATTEMPTS, exc)
                self._random_delay()
                continue

            status = getattr(page, "status", 200)
            if status == 429:
                logger.warning("Rate-limited (429) on %s (attempt %d/%d)",
                               url, attempt, FETCH_ATTEMPTS)
                time.sleep(5.0)
                continue
            if status != 200:
                logger.warning("Non-200 status %s for %s", status, url)
                return None
            return page

        logger.error("All %d fetch attempts failed for %s", FETCH_ATTEMPTS, url)
        return None

    @staticmethod
    def _random_delay() -> None:
        """Sleep a random interval between requests using the configured delays."""
        lo = max(0.0, settings.SCRAPING_DELAY_MIN)
        hi = max(lo, settings.SCRAPING_DELAY_MAX)
        time.sleep(random.uniform(lo, hi))
