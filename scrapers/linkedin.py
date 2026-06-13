import logging
import random
import re
import threading
import time
from urllib.parse import quote

from curl_cffi import requests as curl_requests

from config.settings import settings
from models.lead import Lead
from utils.rate_limiter import RateLimiter
from utils.retry import sync_retry
from utils.terminal import print_linkedin_proxy_warning

logger = logging.getLogger(__name__)

AUTHWALL_MARKER = "/authwall"
AUTHWALL_WAIT_SECONDS = 120

NO_PROXY_DELAY_RANGE = (10.0, 20.0)
PROXY_DELAY_RANGE = (3.0, 6.0)
NO_PROXY_REQUESTS_PER_MINUTE = 5
PROXY_REQUESTS_PER_MINUTE = 12
MAX_CONCURRENT_REQUESTS = 2

COMPANY_URL_PATTERN = re.compile(r"linkedin\.com/company/([^/?#]+)")
PROFILE_URL_PATTERN = re.compile(r"linkedin\.com/in/([^/?#]+)")
LINKEDIN_SUFFIX = re.compile(r"\s*\|\s*LinkedIn\s*$")


class LinkedInScraper:
    """Scraper de LinkedIn para perfiles públicos y páginas de empresa.

    Usa curl_cffi para TLS fingerprinting — NO usar requests ni httpx.
    Requiere proxies residenciales para escala (PROXY_URL en .env).
    Sin proxies: funciona para búsquedas pequeñas con delays conservadores.
    """

    SEARCH_COMPANIES = "https://www.linkedin.com/search/results/companies/?keywords={query}"
    SEARCH_PEOPLE = "https://www.linkedin.com/search/results/people/?keywords={query}"
    TIMEOUT_SECONDS = 15

    def __init__(self) -> None:
        self.proxy_url = settings.PROXY_URL
        if self.proxy_url:
            self.delay_range = PROXY_DELAY_RANGE
            self.rate_limiter = RateLimiter(PROXY_REQUESTS_PER_MINUTE)
        else:
            self.delay_range = NO_PROXY_DELAY_RANGE
            self.rate_limiter = RateLimiter(NO_PROXY_REQUESTS_PER_MINUTE)
            print_linkedin_proxy_warning()
            logger.info("LinkedIn running without proxy — conservative mode (10-20s delays)")
        self._semaphore = threading.Semaphore(MAX_CONCURRENT_REQUESTS)

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI entry point: dispatch by query shape.

        LinkedIn URLs go straight to company/profile scraping; anything
        else runs a company search.

        Args:
            query: LinkedIn URL or search keywords.
            limit: Maximum number of leads to return.

        Returns:
            List of Lead objects with source='linkedin'.
        """
        if COMPANY_URL_PATTERN.search(query):
            lead = self.scrape_company(query)
            return [lead] if lead else []
        if PROFILE_URL_PATTERN.search(query):
            lead = self.scrape_profile(query)
            return [lead] if lead else []
        return self.search_companies(query, limit)

    def scrape_company(self, company_url: str) -> Lead | None:
        """Extrae datos de una página de empresa de LinkedIn.

        Args:
            company_url: URL completa de la empresa en LinkedIn.

        Returns:
            Lead con nombre, industria, tamaño, web y descripción, o None si
            la página está detrás del authwall o no carga.
        """
        page_html = self._fetch(company_url)
        if page_html is None:
            return None

        name = LINKEDIN_SUFFIX.sub("", self._meta_content(page_html, "og:title")).strip()
        if not name:
            logger.warning("Could not extract company name from %s", company_url)
            return None

        description = self._meta_content(page_html, "og:description")
        website = self._extract_company_website(page_html)
        industry = self._extract_json_field(page_html, "industry")

        lead = Lead(
            name=name,
            website=website,
            category=industry,
            source="linkedin",
            raw_data={
                "linkedin_url": company_url,
                "description": description[:300],
                "company_size": self._extract_json_field(page_html, "staffCountRange"),
            },
        )
        logger.info("Company scraped: %r website=%r industry=%r", name, website, industry)
        return lead

    def scrape_profile(self, profile_url: str) -> Lead | None:
        """Extrae datos de un perfil público de LinkedIn.

        Args:
            profile_url: URL completa del perfil.

        Returns:
            Lead con nombre, cargo/headline y URL del perfil, o None si el
            perfil está detrás del authwall o no carga.
        """
        page_html = self._fetch(profile_url)
        if page_html is None:
            return None

        title = self._meta_content(page_html, "og:title")
        if not title:
            logger.warning("Could not extract profile data from %s", profile_url)
            return None

        name = re.split(r"\s+[-|–]\s+", LINKEDIN_SUFFIX.sub("", title))[0].strip()
        headline = self._meta_content(page_html, "og:description")

        lead = Lead(
            name=name,
            category=headline[:100],
            source="linkedin",
            raw_data={"linkedin_url": profile_url, "headline": headline[:300]},
        )
        logger.info("Profile scraped: %r", name)
        return lead

    def search_companies(self, query: str, limit: int) -> list[Lead]:
        """Busca empresas en LinkedIn por query.

        LinkedIn redirige las búsquedas anónimas al authwall; en ese caso
        se loggea y se retorna lista vacía.

        Args:
            query: Término de búsqueda.
            limit: Máximo de empresas a scrapear.

        Returns:
            Leads de las empresas encontradas.
        """
        return self._search(self.SEARCH_COMPANIES, query, limit, self.scrape_company, "company")

    def search_people(self, query: str, limit: int) -> list[Lead]:
        """Busca personas en LinkedIn por query.

        Args:
            query: Término de búsqueda.
            limit: Máximo de perfiles a scrapear.

        Returns:
            Leads de los perfiles encontrados.
        """
        return self._search(self.SEARCH_PEOPLE, query, limit, self.scrape_profile, "in")

    def _search(self, url_template: str, query: str, limit: int, scrape_one, kind: str) -> list[Lead]:
        """Run an anonymous search and scrape each result URL found.

        Args:
            url_template: Search URL with a {query} placeholder.
            query: Search keywords.
            limit: Maximum results to scrape.
            scrape_one: Callable that scrapes a single result URL.
            kind: URL path segment ('company' or 'in').

        Returns:
            Leads for the results that could be scraped.
        """
        page_html = self._fetch(url_template.format(query=quote(query)))
        if page_html is None:
            logger.warning(
                "LinkedIn search hit the authwall — anonymous search is not possible. "
                "Provide specific linkedin.com/%s/ URLs as --query instead.", kind,
            )
            return []

        slug_pattern = re.compile(rf"linkedin\.com/{kind}/([a-zA-Z0-9\-_%]+)")
        slugs = list(dict.fromkeys(slug_pattern.findall(page_html)))
        if not slugs:
            logger.warning("No %s results found for %r", kind, query)
            return []

        leads: list[Lead] = []
        for slug in slugs[:limit]:
            lead = scrape_one(f"https://www.linkedin.com/{kind}/{slug}/")
            if lead is not None:
                leads.append(lead)
        return leads

    def _fetch(self, url: str) -> str | None:
        """Fetch a LinkedIn URL with TLS impersonation and authwall handling.

        Waits 120s and retries once on HTTP 429 or an authwall redirect.

        Args:
            url: LinkedIn URL to fetch.

        Returns:
            The page HTML, or None if unreachable or login-walled.
        """
        for attempt in (1, 2):
            with self._semaphore:
                self.rate_limiter.acquire_sync()
                self._random_delay()
                try:
                    response = sync_retry(lambda: self._request(url), max_retries=2)
                except Exception as exc:
                    logger.error("Failed to fetch %s: %s", url, exc, exc_info=True)
                    return None

            final_url = str(getattr(response, "url", url))
            blocked = response.status_code == 429 or AUTHWALL_MARKER in final_url

            if blocked:
                if attempt == 1:
                    logger.warning(
                        "LinkedIn blocked request (status=%s, authwall=%s) — "
                        "waiting %ds and retrying once",
                        response.status_code, AUTHWALL_MARKER in final_url, AUTHWALL_WAIT_SECONDS,
                    )
                    time.sleep(AUTHWALL_WAIT_SECONDS)
                    continue
                logger.error("LinkedIn still blocking after retry on %s — giving up", url)
                return None

            if response.status_code != 200:
                logger.warning("Non-200 status %s for %s", response.status_code, url)
                return None

            return response.text
        return None

    def _request(self, url: str):
        """Send one GET via curl_cffi impersonating Chrome's TLS fingerprint."""
        proxies = (
            {"http": self.proxy_url, "https": self.proxy_url} if self.proxy_url else None
        )
        return curl_requests.get(
            url,
            impersonate="chrome",
            proxies=proxies,
            timeout=self.TIMEOUT_SECONDS,
            allow_redirects=True,
        )

    @staticmethod
    def _meta_content(html: str, property_name: str) -> str:
        """Extract the content of a meta property tag from raw HTML."""
        escaped = re.escape(property_name)
        match = re.search(
            rf'<meta[^>]+property="{escaped}"[^>]+content="([^"]*)"', html
        ) or re.search(
            rf'<meta[^>]+content="([^"]*)"[^>]+property="{escaped}"', html
        )
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_company_website(html: str) -> str:
        """Extract the company's external website from embedded JSON."""
        match = (
            re.search(r'"callToAction"[^}]*"url"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
            or re.search(r'"companyPageUrl"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
        )
        if not match:
            return ""
        url = match.group(1).replace("\\/", "/")
        return "" if "linkedin.com" in url else url

    @staticmethod
    def _extract_json_field(html: str, key: str) -> str:
        """Extract a simple string value for a key from embedded JSON."""
        match = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
        return match.group(1) if match else ""

    def _random_delay(self) -> None:
        """Sleep within the configured delay range (proxy-dependent)."""
        time.sleep(random.uniform(*self.delay_range))
