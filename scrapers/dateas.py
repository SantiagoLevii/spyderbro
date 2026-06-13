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

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 15
MAX_PAGES = 20
TIMEOUT_SECONDS = 10
SOURCE = "dateas"

CUIT_PATTERN = re.compile(r"\b(\d{2}-?\d{8}-?\d)\b")

TYPE_MAP = {
    "empresas": "juridicas",
    "personas": "fisicas",
    "ambos": "fisicas-juridicas",
}


class DateasScraper:
    """Scrapes public company/person records from Dateas (Argentina).

    Only public listing data is read — no login. CUIT/CUIL is stored in
    raw_data['cuit']. Results are paginated up to the requested limit.
    """

    BASE_URL = "https://www.dateas.com"
    SEARCH_URL = BASE_URL + "/es/consulta_cuit_cuil?name={name}&tipo={tipo}"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point. Mode comes from settings.DATEAS_TYPE.

        Args:
            query: Name or rubro to search.
            limit: Maximum number of leads to return.

        Returns:
            List of Lead objects with source='dateas'.
        """
        province = settings.LOCATION or ""
        mode = (settings.DATEAS_TYPE or "empresas").lower()

        if mode == "personas":
            return self.search_people(query, province, 0, 0, limit)
        if mode == "ambos":
            companies = self.search_companies(query, province, "", limit)
            people = self.search_people(query, province, 0, 0, max(0, limit - len(companies)))
            return (companies + people)[:limit]
        return self.search_companies(query, province, "", limit)

    def search_companies(self, query: str, province: str, activity: str, limit: int) -> list[Lead]:
        """Search companies on Dateas by name/rubro and province.

        Args:
            query: Company name or rubro.
            province: Province (e.g. "Ciudad Autónoma de Buenos Aires").
            activity: Economic activity (optional, empty string if N/A).
            limit: Maximum number of leads.

        Returns:
            List of Leads with name, phone, address, category, source.
        """
        return self._search(query, TYPE_MAP["empresas"], province, limit)

    def search_people(
        self, query: str, province: str, age_from: int, age_to: int, limit: int
    ) -> list[Lead]:
        """Search people on Dateas by name and filters.

        Args:
            query: Person name.
            province: Province.
            age_from: Minimum age (0 = no filter).
            age_to: Maximum age (0 = no filter).
            limit: Maximum number of leads.

        Returns:
            List of Leads with name, address, category (activity), source.
        """
        return self._search(query, TYPE_MAP["personas"], province, limit)

    def _search(self, query: str, tipo: str, province: str, limit: int) -> list[Lead]:
        """Shared paginated search for both companies and people.

        Note: Dateas has no working ``provincia`` query param (passing one
        returns an empty result set), so province is applied as a lenient
        client-side filter on each row's province/locality column instead.
        """
        leads: list[Lead] = []
        seen: set[str] = set()
        name = quote(query.strip())
        prov_norm = (province or "").strip().lower()

        for page_num in range(1, MAX_PAGES + 1):
            if len(leads) >= limit:
                break
            url = self.SEARCH_URL.format(name=name, tipo=tipo)
            if page_num > 1:
                url += f"&page={page_num}"

            page = self._fetch(url)
            if page is None:
                break

            added = 0
            for lead in self._parse_results(page):
                key = (lead.raw_data.get("cuit") or lead.name).lower()
                if not key or key in seen:
                    continue
                if not self._province_matches(lead.address, prov_norm):
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

        logger.info("Dateas scrape complete: %d leads (tipo=%s)", len(leads), tipo)
        return leads[:limit]

    @staticmethod
    def _province_matches(address: str, prov_norm: str) -> bool:
        """Lenient client-side province filter over a row's province/locality.

        Returns True when no province is requested or the address is empty. For
        Buenos Aires / CABA the common synonyms are accepted.
        """
        if not prov_norm or not address:
            return True
        addr = address.lower()
        prov = prov_norm.replace("-", " ")
        if prov in ("buenos aires", "capital federal", "caba"):
            return any(s in addr for s in
                       ("buenos aires", "capital federal", "caba", "ciudad autonoma"))
        return prov in addr

    def get_company_detail(self, dateas_url: str) -> Lead | None:
        """Extract a company's public details from its Dateas profile page.

        Reads only public fields: name, CUIT, address, phone, activity.

        Args:
            dateas_url: Full Dateas profile URL.

        Returns:
            A Lead with the public data, or None if the page is unavailable.
        """
        page = self._fetch(dateas_url)
        if page is None:
            return None
        leads = self._parse_results(page)
        return leads[0] if leads else None

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
        """Parse result rows from a Dateas listing page into Leads.

        Dateas renders results in a server-side HTML table whose rows link to
        ``/es/empresa/{slug}-{cuit}`` (companies) or ``/es/persona/...`` (people).
        Other tables on the page (currency quotes) are skipped by requiring that
        link in each row.
        """
        leads: list[Lead] = []
        for row in page.find_all("tr"):
            href = row.css("a::attr(href)").get() or ""
            if "/es/empresa/" not in href and "/es/persona/" not in href:
                continue
            lead = self._parse_row(row)
            if lead is not None:
                leads.append(lead)
        return leads

    @staticmethod
    def _parse_row(row) -> Lead | None:
        """Parse a single Dateas result ``<tr>`` into a Lead.

        Columns: name | CUIT/CUIL/CDI | age | province | locality | "Ver Más".
        """
        cells = [" ".join(c.css("::text").getall()).strip() for c in row.find_all("td")]
        if not cells:
            return None

        name = cells[0].strip()
        if not name:
            return None

        cuit = ""
        match = CUIT_PATTERN.search(" ".join(cells))
        if match:
            cuit = match.group(1)

        # Province (idx 3) and locality (idx 4) form the address.
        province = cells[3].strip() if len(cells) > 3 else ""
        locality = cells[4].strip() if len(cells) > 4 else ""
        address = ", ".join(p for p in (locality, province) if p)

        return Lead(
            name=name,
            address=address,
            source=SOURCE,
            raw_data={"cuit": cuit} if cuit else {},
        )

    @staticmethod
    def _random_delay() -> None:
        """Sleep a random interval between requests using the configured delays."""
        lo = max(0.0, settings.SCRAPING_DELAY_MIN)
        hi = max(lo, settings.SCRAPING_DELAY_MAX)
        time.sleep(random.uniform(lo, hi))
