import logging
import random
import re
import time

from scrapling import StealthyFetcher

from config.settings import settings
from models.lead import Lead
from utils.rate_limiter import RateLimiter
from utils.retry import sync_retry
from utils.validators import normalize_phone

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 12
MAX_PAGES = 20
TIMEOUT_MS = 60000
SOURCE = "argenprop"

_TELEPHONE_RE = re.compile(r'"telephone"\s*:\s*"([^"]+)"')


def parse_property_query(query: str) -> tuple[str, str, str]:
    """Parse a '<operation> <location>' query into (property_type, operation, location).

    property_type defaults to 'departamento'. settings.LOCATION overrides the
    parsed location when set.
    """
    parts = query.strip().split()
    operation = parts[0] if parts else query.strip()
    parsed_location = "-".join(parts[1:]) if len(parts) > 1 else ""
    location = settings.LOCATION or parsed_location
    return "departamento", operation, location


class ArgenpropScraper:
    """Scrapes real-estate agencies (not properties) from Argenprop.

    Like Zonaprop, Argenprop is behind Cloudflare and hides the agency on
    listing cards, so each lead is built in two steps: the listing yields
    property detail URLs, and each detail page exposes the agency via its
    ``/inmobiliarias/{slug}`` link. Leads are deduplicated by agency name.
    """

    BASE_URL = "https://www.argenprop.com"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point. Query is '<operation> <location>'."""
        property_type, operation, location = parse_property_query(query)
        return self.search_agents(property_type, operation, location, limit)

    def search_agents(
        self, property_type: str, operation: str, location: str, limit: int
    ) -> list[Lead]:
        """Extract agencies from Argenprop listings (one lead per agency)."""
        leads: list[Lead] = []
        seen: set[str] = set()

        for page_num in range(1, MAX_PAGES + 1):
            if len(leads) >= limit:
                break
            if location:
                slug = f"{property_type}-{operation}-barrio-{location}"
            else:
                slug = f"{property_type}-{operation}-pais-argentina"
            url = f"{self.BASE_URL}/{slug}"
            if page_num > 1:
                url += f"-pagina-{page_num}"

            page = self._fetch(url)
            if page is None:
                break

            detail_urls = self._collect_detail_urls(page)
            if not detail_urls:
                logger.info("No postings on page %d for %r", page_num, operation)
                break

            added = 0
            for detail_url in detail_urls:
                if len(leads) >= limit:
                    break
                detail = self._fetch(detail_url)
                if detail is None:
                    continue
                lead = self._parse_detail(detail, location or "argentina")
                if lead is None:
                    continue
                key = lead.name.lower()
                if key in seen:
                    continue
                seen.add(key)
                leads.append(lead)
                added += 1
                self._random_delay()
            if added == 0:
                logger.info("No new agencies on page %d for %r — stopping", page_num, operation)
                break

        logger.info("Argenprop scrape complete: %d unique agencies", len(leads))
        return leads[:limit]

    def _collect_detail_urls(self, page) -> list[str]:
        """Collect unique property detail URLs from listing cards."""
        urls: list[str] = []
        seen: set[str] = set()
        for card in page.find_all("[data-item-card]") or page.find_all(".listing__item"):
            link = card.find('a[href*="--"]')
            href = link.attrib.get("href", "") if link else ""
            if not href:
                continue
            full = href if href.startswith("http") else self.BASE_URL + href
            if full in seen:
                continue
            seen.add(full)
            urls.append(full)
        return urls

    def _fetch(self, url: str):
        """Fetch a Cloudflare-protected URL with StealthyFetcher, None on failure."""
        try:
            self.rate_limiter.acquire_sync()
            page = sync_retry(
                lambda: StealthyFetcher.fetch(
                    url, headless=True, solve_cloudflare=True, timeout=TIMEOUT_MS
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

    def _parse_detail(self, page, location: str) -> Lead | None:
        """Parse the publishing agency from a property detail page.

        Maps: ``/inmobiliarias/{slug}`` link -> agency name + Argenprop page,
        first ``h2`` -> property address, embedded ``"telephone"`` JSON -> phone.
        """
        name = ""
        agency_url = ""
        for a in page.find_all("a"):
            href = a.attrib.get("href", "")
            if "inmobiliarias/" in href:
                agency_url = href if href.startswith("http") else self.BASE_URL + "/" + href.lstrip("/")
                text = a.text.strip()
                if text and len(text) > 1:
                    name = text
                else:
                    slug = href.rstrip("/").split("inmobiliarias/")[-1].split("/")[0]
                    name = slug.replace("-", " ").title()
                break
        if not name:
            return None

        addr_el = page.find("h2")
        address = addr_el.text.strip() if addr_el else ""
        if not address:
            address = location

        phone = ""
        tel = page.css('a[href^="tel:"]::attr(href)').get()
        if tel:
            phone = normalize_phone(tel.removeprefix("tel:").strip(), default_country="AR") or ""
        if not phone:
            match = _TELEPHONE_RE.search(page.html_content or "")
            if match:
                phone = normalize_phone(match.group(1).strip(), default_country="AR") or ""

        return Lead(
            name=name,
            phone=phone,
            address=address,
            category="Inmobiliaria",
            source=SOURCE,
            raw_data={"argenprop_url": agency_url} if agency_url else {},
        )

    @staticmethod
    def _random_delay() -> None:
        """Sleep a random interval between requests using the configured delays."""
        lo = max(0.0, settings.SCRAPING_DELAY_MIN)
        hi = max(lo, settings.SCRAPING_DELAY_MAX)
        time.sleep(random.uniform(lo, hi))
