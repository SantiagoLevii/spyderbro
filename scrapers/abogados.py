import logging
import random
import re
import time

from scrapling import Fetcher

from config.settings import settings
from models.lead import Lead
from scrapers.query_utils import split_query
from utils.rate_limiter import RateLimiter
from utils.retry import sync_retry
from utils.validators import is_valid_email, normalize_phone

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 20
TIMEOUT_SECONDS = 10
SOURCE = "abogados"

# Domains to exclude when picking a firm's own website from its detail page.
_SOCIAL_DOMAINS = (
    "facebook.",
    "twitter.",
    "x.com/",
    "linkedin.",
    "youtube.",
    "instagram.",
    "abogados.com.ar",
)


class AbogadosScraper:
    """Scrapes law firms from Abogados.com.ar (Argentina).

    The site is a legacy PHP/jQuery directory (no ``__NEXT_DATA__``). Leads are
    extracted in two steps: a specialty listing page yields firm detail URLs,
    and each detail page exposes the firm's name, phone, website and address.
    """

    BASE_URL = "https://abogados.com.ar"
    HOME_URL = BASE_URL + "/"
    DIRECTORIO_URL = BASE_URL + "/directorio"
    AREA_URL = BASE_URL + "/area/{slug}/{id}"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point. Query is '<specialty> <province>'."""
        specialty, province = split_query(query)
        return self.search(specialty, province, limit)

    def search(self, specialty: str, province: str, limit: int) -> list[Lead]:
        """Search law firms in Argentina by legal specialty.

        Args:
            specialty: Legal specialty (e.g. "laboral", "civil", "penal").
            province: Province/city; used only for best-effort address filtering
                because the directory is not segmented by province.
            limit: Maximum number of leads.

        Returns:
            List of Leads with name, phone, website, address, category.
        """
        listing_url = self._resolve_listing_url(specialty)
        listing = self._fetch(listing_url)
        if listing is None:
            return []

        firm_urls = self._collect_firm_urls(listing)
        if not firm_urls:
            logger.info("No firm URLs found on %s", listing_url)
            return []

        leads: list[Lead] = []
        seen: set[str] = set()
        category = specialty.strip().title()

        for url in firm_urls:
            if len(leads) >= limit:
                break
            detail = self._fetch(url)
            if detail is None:
                continue
            lead = self._parse_detail(detail, category)
            if lead is None:
                continue
            key = lead.name.lower()
            if key in seen:
                continue
            if not self._province_matches(lead.address, province):
                continue
            seen.add(key)
            leads.append(lead)
            self._random_delay()

        logger.info("Abogados scrape complete: %d leads", len(leads))
        return leads[:limit]

    def _resolve_listing_url(self, specialty: str) -> str:
        """Map a specialty term to its ``/area/{slug}/{id}`` listing URL.

        Falls back to the full ``/directorio`` listing when no specialty matches.
        """
        term = specialty.strip().lower().replace(" ", "-")
        if not term:
            return self.DIRECTORIO_URL

        home = self._fetch(self.HOME_URL)
        if home is None:
            return self.DIRECTORIO_URL

        area_links = [l for l in home.css("a::attr(href)").getall() if "/area/" in l]
        for link in area_links:
            # /area/{slug}/{id}
            parts = link.rstrip("/").split("/area/")[-1].split("/")
            slug = parts[0] if parts else ""
            if term in slug or slug in term:
                return link if link.startswith("http") else self.BASE_URL + link
        logger.info("No specialty match for %r — using full directorio", specialty)
        return self.DIRECTORIO_URL

    @staticmethod
    def _province_matches(address: str, province: str) -> bool:
        """Best-effort province filter; lenient because the directory is not
        segmented by province and addresses use mixed forms (CABA, BUENOS AIRES).

        Returns True when no province is requested, the address is empty, or the
        normalized province token (or a Buenos Aires synonym) is found.
        """
        prov = (province or "").strip().lower().replace("-", " ")
        if not prov or not address:
            return True
        addr = address.lower()
        ba_synonyms = ("buenos aires", "caba", "capital federal", "ciudad autonoma")
        if prov in ("buenos aires", "capital federal", "caba"):
            return any(s in addr for s in ba_synonyms)
        return prov in addr

    def _collect_firm_urls(self, page) -> list[str]:
        """Collect unique firm detail URLs (``/directorio/{slug}/{id}``)."""
        urls: list[str] = []
        seen: set[str] = set()
        for href in page.css("a::attr(href)").getall():
            if "/directorio/" not in href:
                continue
            tail = href.split("/directorio/")[-1]
            # Real firm pages end in /{numeric id}; skip search/filter links.
            if "?" in tail or "/" not in tail:
                continue
            if not tail.rstrip("/").split("/")[-1].isdigit():
                continue
            full = href if href.startswith("http") else self.BASE_URL + href
            if full not in seen:
                seen.add(full)
                urls.append(full)
        return urls

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

    def _parse_detail(self, page, category: str) -> Lead | None:
        """Parse a firm detail page into a Lead.

        Maps: h1 -> name, ``<address>`` -> address, ``tel:`` link -> phone,
        first non-social external link -> website, ``mailto:`` -> email.
        """
        name = (page.css("h1::text").get() or "").strip()
        if not name:
            return None

        addr_el = page.find("address")
        address = ""
        if addr_el:
            address = " ".join(t.strip() for t in addr_el.css("::text").getall() if t.strip())
            address = re.sub(r"\s+", " ", address).strip().rstrip(",").strip()

        phone = ""
        tel = page.css('a[href^="tel:"]::attr(href)').get()
        if tel:
            phone = normalize_phone(tel.removeprefix("tel:").strip(), default_country="AR") or ""

        email = ""
        mailto = page.css('a[href^="mailto:"]::attr(href)').get()
        if mailto:
            candidate = mailto.removeprefix("mailto:").split("?")[0].strip()
            if is_valid_email(candidate):
                email = candidate.lower()

        website = ""
        for href in page.css("a::attr(href)").getall():
            if href.startswith("http") and not any(s in href for s in _SOCIAL_DOMAINS):
                website = href.strip()
                break

        return Lead(
            name=name,
            email=email,
            phone=phone,
            website=website,
            address=address,
            category=category,
            source=SOURCE,
        )

    @staticmethod
    def _random_delay() -> None:
        """Sleep a random interval between requests using the configured delays."""
        lo = max(0.0, settings.SCRAPING_DELAY_MIN)
        hi = max(lo, settings.SCRAPING_DELAY_MAX)
        time.sleep(random.uniform(lo, hi))
