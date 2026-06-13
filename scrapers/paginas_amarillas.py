import json
import logging
import random
import re
import time
from urllib.parse import quote

from scrapling import Fetcher

from config.settings import settings
from models.lead import Lead
from utils.rate_limiter import RateLimiter
from utils.retry import sync_retry
from utils.validators import is_valid_email, normalize_phone

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 20
MAX_PAGES = 20
TIMEOUT_SECONDS = 10
SOURCE = "paginas_amarillas"

# The site is a Next.js app: business records live in the embedded
# __NEXT_DATA__ JSON at props.pageProps.results, not in CSS card markup.
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class PaginasAmarillasScraper:
    """Scrapes business leads from Páginas Amarillas Argentina.

    Static HTML site (no anti-bot), searched by rubro and province. Results
    are paginated until the requested limit is reached.
    """

    BASE_URL = "https://www.paginasamarillas.com.ar"
    SEARCH_URL = BASE_URL + "/buscar/{rubro}/{provincia}"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point. Location comes from settings.LOCATION.

        Args:
            query: Rubro to search (e.g. "restaurantes").
            limit: Maximum number of leads to return.

        Returns:
            List of Lead objects with source='paginas_amarillas'.
        """
        return self.search(query, settings.LOCATION or "argentina", limit)

    def search(self, query: str, location: str, limit: int) -> list[Lead]:
        """Search businesses on Páginas Amarillas by rubro and location.

        Handles pagination automatically up to the limit.

        Args:
            query: Rubro to search (e.g. "restaurantes", "inmobiliarias").
            location: Province or city (e.g. "buenos-aires", "cordoba").
            limit: Maximum number of leads to return.

        Returns:
            List of Leads with name, phone, address, website, category, source.
        """
        leads: list[Lead] = []
        seen: set[str] = set()
        rubro = quote(query.strip().replace(" ", "-"))
        provincia = quote((location or "argentina").strip().replace(" ", "-"))

        for page_num in range(1, MAX_PAGES + 1):
            if len(leads) >= limit:
                break
            url = self.SEARCH_URL.format(rubro=rubro, provincia=provincia)
            if page_num > 1:
                url = f"{url}?page={page_num}"

            page = self._fetch(url)
            if page is None:
                break

            new = self._collect(page, leads, seen, limit)
            if new == 0:
                logger.info("No new results on page %d for %r — stopping", page_num, query)
                break
            self._random_delay()

        logger.info("Páginas Amarillas scrape complete: %d leads", len(leads))
        return leads[:limit]

    def _collect(self, page, leads: list[Lead], seen: set[str], limit: int) -> int:
        """Parse one results page and append new unique leads. Returns count added."""
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
        return added

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
        """Parse business records from the page's __NEXT_DATA__ JSON into Leads."""
        match = NEXT_DATA_RE.search(page.html_content or "")
        if not match:
            logger.debug("No __NEXT_DATA__ block found — page shape may have changed")
            return []
        try:
            results = json.loads(match.group(1))["props"]["pageProps"].get("results", [])
        except (ValueError, KeyError) as exc:
            logger.warning("Could not read results from __NEXT_DATA__: %s", exc)
            return []

        leads: list[Lead] = []
        for record in results:
            lead = self._record_to_lead(record)
            if lead is not None:
                leads.append(lead)
        return leads

    @staticmethod
    def _record_to_lead(record: dict) -> Lead | None:
        """Convert one __NEXT_DATA__ business record into a Lead."""
        name = (record.get("name") or "").strip()
        if not name:
            return None

        address_block = record.get("mainAddress") or {}

        phone = ""
        all_phones = address_block.get("allPhones") or []
        if all_phones:
            raw = all_phones[0].get("number") or ""
            phone = normalize_phone(raw, default_country="AR") or raw

        contact_map = record.get("contactMap") or {}
        website = ""
        web_list = contact_map.get("WEB") or []
        if web_list:
            website = (web_list[0] or "").strip()

        email = ""
        for key in ("MAIL", "EMAIL"):
            mail_list = contact_map.get(key) or []
            if mail_list and is_valid_email(mail_list[0]):
                email = mail_list[0].lower()
                break

        street = " ".join(
            str(p) for p in (address_block.get("streetName"), address_block.get("streetNumber")) if p
        ).strip()
        locality = (address_block.get("localityToShow") or "").strip()
        address = ", ".join(p for p in (street, locality) if p)

        category = (record.get("infoLine") or record.get("productType") or "").strip()

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
