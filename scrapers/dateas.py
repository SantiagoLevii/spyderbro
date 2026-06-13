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

REQUESTS_PER_MINUTE = 10
MAX_PAGES = 20
TIMEOUT_SECONDS = 10
PAGE_DELAY_MIN = 3.0
PAGE_DELAY_MAX = 5.0
SOURCE = "dateas"

CUIT_PATTERN = re.compile(r"\b(\d{2}-?\d{8}-?\d)\b")
# CUIT/CUIL prefixes that identify a natural person (vs a company).
_PERSON_PREFIXES = ("20", "23", "24", "27")
_CUIT_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)

TYPE_MAP = {
    "empresas": "juridicas",
    "personas": "fisicas",
    "ambos": "fisicas-juridicas",
}


class DateasScraper:
    """Scrapes public company/person records from Dateas (Argentina).

    Only public listing data is read — no login. For each result the public
    fields (name, CUIT/CUIL, derived DNI, age, province, locality, entity type)
    are stored in ``raw_data``. Supports name search, paginated, plus direct
    lookup by CUIT or DNI. Everything else (phone, exact address, employer,
    credit/legal records) requires Dateas' paid report and is not scraped.
    """

    BASE_URL = "https://www.dateas.com"
    SEARCH_URL = BASE_URL + "/es/consulta_cuit_cuil?name={name}&tipo={tipo}"
    LOOKUP_URL = BASE_URL + "/es/consulta_cuit_cuil?cuit={cuit}&tipo={tipo}"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point.

        Mode comes from ``settings.DATEAS_LOOKUP`` (name | cuit | dni) and, for
        name search, ``settings.DATEAS_TYPE`` (empresas | personas | ambos).

        Args:
            query: Name, CUIT or DNI depending on the lookup mode.
            limit: Maximum number of leads to return.

        Returns:
            List of Lead objects with source='dateas'.
        """
        lookup = (getattr(settings, "DATEAS_LOOKUP", "name") or "name").lower()
        if lookup == "cuit":
            lead = self.lookup_by_cuit(query)
            return [lead] if lead else []
        if lookup == "dni":
            lead = self.lookup_by_dni(query)
            return [lead] if lead else []

        province = settings.LOCATION or ""
        mode = (settings.DATEAS_TYPE or "empresas").lower()
        if mode == "personas":
            return self.search_people(query, province, limit)
        if mode == "ambos":
            companies = self.search_companies(query, province, "", limit)
            people = self.search_people(query, province, max(0, limit - len(companies)))
            return (companies + people)[:limit]
        return self.search_companies(query, province, "", limit)

    def search_people(self, query: str, province: str, limit: int) -> list[Lead]:
        """Search natural persons on Dateas by name.

        Extracts all public fields without login: name, DNI, CUIT/CUIL, age,
        province, locality.

        Args:
            query: Full or partial name (e.g. "santiago levi").
            province: Province to filter by (e.g. "Buenos Aires"; "" for all AR).
            limit: Maximum number of leads.

        Returns:
            Leads with name=full name, address=locality+province, and raw_data
            holding dni, cuit, age, province, locality, entity_type.
        """
        return self._search(query, TYPE_MAP["personas"], province, limit)

    def search_companies(
        self, query: str, province: str, activity: str, limit: int
    ) -> list[Lead]:
        """Search companies on Dateas by name or razón social.

        Extracts: razón social, CUIT, province, locality. (Activity/rubro is not
        exposed publicly without a paid report, so ``activity`` is accepted for
        API compatibility but not used as a server-side filter.)

        Args:
            query: Company name or razón social.
            province: Province (e.g. "Buenos Aires"; "" for all AR).
            activity: Economic activity (unused — kept for compatibility).
            limit: Maximum number of leads.

        Returns:
            Leads with name=razón social, address=locality+province, raw_data
            holding cuit, province, locality, entity_type.
        """
        return self._search(query, TYPE_MAP["empresas"], province, limit)

    def lookup_by_cuit(self, cuit: str) -> Lead | None:
        """Look up a person or company by exact CUIT/CUIL.

        Args:
            cuit: CUIT/CUIL with or without dashes (e.g. "20-43982658-5").

        Returns:
            The matching Lead, or None if not found / invalid.
        """
        digits = re.sub(r"\D", "", cuit or "")
        if len(digits) != 11:
            logger.warning("Invalid CUIT for lookup: %r", cuit)
            return None
        formatted = f"{digits[:2]}-{digits[2:10]}-{digits[10]}"
        tipo = "fisicas" if digits[:2] in _PERSON_PREFIXES else "juridicas"
        page = self._fetch(self.LOOKUP_URL.format(cuit=quote(formatted), tipo=tipo))
        if page is None:
            return None
        leads = self._parse_results(page)
        return leads[0] if leads else None

    def lookup_by_dni(self, dni: str) -> Lead | None:
        """Look up a person by exact DNI.

        Dateas has no DNI search param, so the DNI is converted to candidate
        CUILs (prefixes 20/27/23/24 with the computed check digit) and each is
        looked up by CUIT until a match is found.

        Args:
            dni: DNI without dots/spaces (e.g. "43982658").

        Returns:
            The matching Lead, or None if not found / invalid.
        """
        d = re.sub(r"\D", "", dni or "")
        if not (7 <= len(d) <= 8):
            logger.warning("Invalid DNI for lookup: %r", dni)
            return None
        d8 = d.zfill(8)
        for prefix in _PERSON_PREFIXES:
            candidate = self._cuit_from_dni(prefix, d8)
            if candidate is None:
                continue
            lead = self.lookup_by_cuit(candidate)
            if lead is not None:
                return lead
        logger.info("No person found on Dateas for DNI %r", dni)
        return None

    def get_detail(self, dateas_url: str) -> dict:
        """Fetch a Dateas profile page and return any extra public fields.

        Most detail fields are behind a paid report; this returns the publicly
        visible fields (best-effort), keyed for merging into a Lead's raw_data.

        Args:
            dateas_url: Full Dateas profile URL.

        Returns:
            Dict of extra public fields (may be empty).
        """
        page = self._fetch(dateas_url)
        if page is None:
            return {}
        leads = self._parse_results(page)
        detail = dict(leads[0].raw_data) if leads else {}
        detail["dateas_url"] = dateas_url
        return detail

    def _search(self, query: str, tipo: str, province: str, limit: int) -> list[Lead]:
        """Shared paginated name search for both companies and people.

        Note: Dateas has no working ``provincia`` query param (passing one
        returns an empty result set), so province is applied as a lenient
        client-side filter on each row's province/locality instead.
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
            if settings.SCRAPING_DELAY_MAX > 0:
                time.sleep(random.uniform(PAGE_DELAY_MIN, PAGE_DELAY_MAX))

        logger.info("Dateas scrape complete: %d leads (tipo=%s)", len(leads), tipo)
        return leads[:limit]

    @staticmethod
    def _province_matches(address: str, prov_norm: str) -> bool:
        """Lenient client-side province filter over a row's province/locality."""
        if not prov_norm or not address:
            return True
        addr = address.lower()
        prov = prov_norm.replace("-", " ")
        if prov in ("buenos aires", "capital federal", "caba"):
            return any(s in addr for s in
                       ("buenos aires", "capital federal", "caba", "ciudad autonoma"))
        return prov in addr

    @staticmethod
    def _cuit_from_dni(prefix: str, dni8: str) -> str | None:
        """Build a CUIT/CUIL from a prefix and 8-digit DNI, or None if invalid.

        Uses the standard mod-11 check-digit algorithm; returns None when the
        check digit resolves to 10 (that DNI uses a different prefix).
        """
        base = prefix + dni8
        total = sum(int(c) * w for c, w in zip(base, _CUIT_WEIGHTS))
        check = 11 - (total % 11)
        if check == 11:
            check = 0
        elif check == 10:
            return None
        return f"{prefix}-{dni8}-{check}"

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
        Other tables on the page (currency quotes) lack that link and are skipped.
        """
        leads: list[Lead] = []
        for row in page.find_all("tr"):
            href = row.css("a::attr(href)").get() or ""
            if "/es/empresa/" not in href and "/es/persona/" not in href:
                continue
            lead = self._parse_row(row, href)
            if lead is not None:
                leads.append(lead)
        return leads

    @classmethod
    def _parse_row(cls, row, href: str) -> Lead | None:
        """Parse a single Dateas result ``<tr>`` into a Lead.

        Columns: name | CUIT/CUIL/CDI | age | province | locality | "Ver Más".
        """
        cells = [" ".join(c.css("::text").getall()).strip() for c in row.find_all("td")]
        if not cells or not cells[0].strip():
            return None
        name = cells[0].strip()

        cuit = ""
        match = CUIT_PATTERN.search(" ".join(cells))
        if match:
            cuit = match.group(1)
        cuit_digits = re.sub(r"\D", "", cuit)

        entity_type = "fisica" if "/es/persona/" in href else "juridica"

        dni = ""
        if entity_type == "fisica" and len(cuit_digits) == 11:
            dni = cuit_digits[2:10]

        age = ""
        if len(cells) > 2:
            age_digits = re.sub(r"\D", "", cells[2])
            age = age_digits

        province = cls._clean_province(cells[3]) if len(cells) > 3 else ""
        locality = cells[4].strip() if len(cells) > 4 else ""
        address = ", ".join(p for p in (locality, province) if p)

        dateas_url = href if href.startswith("http") else cls.BASE_URL + href

        raw_data = {
            "dni": dni,
            "cuit": cuit,
            "age": age,
            "province": province,
            "locality": locality,
            "entity_type": entity_type,
            "dateas_url": dateas_url,
        }
        return Lead(name=name, address=address, source=SOURCE, raw_data=raw_data)

    @staticmethod
    def _clean_province(value: str) -> str:
        """Normalize a province cell, dropping the ``(Pcia)`` suffix."""
        return re.sub(r"\s*\(Pcia\)\s*$", "", value.strip()).strip()

    @staticmethod
    def _random_delay() -> None:
        """Sleep a random interval between requests using the configured delays."""
        lo = max(0.0, settings.SCRAPING_DELAY_MIN)
        hi = max(lo, settings.SCRAPING_DELAY_MAX)
        time.sleep(random.uniform(lo, hi))
