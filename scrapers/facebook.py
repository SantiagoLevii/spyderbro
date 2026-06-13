import logging
import random
import re
import time
from urllib.parse import parse_qs, quote, urlparse

from scrapling import StealthyFetcher

from config.settings import settings
from models.lead import Lead
from utils.rate_limiter import RateLimiter
from utils.retry import sync_retry
from utils.validators import is_valid_email, normalize_phone

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 8

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_PATTERN = re.compile(r"\+?\d[\d\s().\-]{7,}\d")
PAGE_LINK_PATTERN = re.compile(r'https://www\.facebook\.com/([a-zA-Z0-9.\-]+)/?(?:\?|")')

EXCLUDED_SLUGS = {
    "login", "recover", "reg", "help", "policies", "privacy", "watch",
    "marketplace", "groups", "gaming", "pages", "search", "people", "public",
    "events", "business", "legal", "about", "directory", "hashtag",
}

MIN_DELAY_SECONDS = 4.0
MAX_DELAY_SECONDS = 8.0


class FacebookScraper:
    """Scrapes business leads from public Facebook Pages.

    Only public pages are accessed — no login, no personal profiles, no
    groups. Facebook aggressively login-walls anonymous traffic, so results
    may be empty if the wall is hit.
    """

    SEARCH_URL = "https://www.facebook.com/search/pages/?q={query}"
    PAGE_URL = "https://www.facebook.com/{slug}/"
    TIMEOUT_MS = 15000

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """Search Facebook Pages and extract lead data from each result.

        Args:
            query: Search term (e.g. "gyms in Miami").
            limit: Maximum number of leads to return.

        Returns:
            List of Lead objects with source='facebook'.
        """
        return self.search_pages(query, limit)

    def search_pages(self, query: str, limit: int) -> list[Lead]:
        """Search business pages on Facebook by query and scrape each one.

        Args:
            query: Search term.
            limit: Maximum number of pages to scrape.

        Returns:
            Leads for the pages found. Empty if Facebook shows a login wall.
        """
        url = self.SEARCH_URL.format(query=quote(query))
        page = self._fetch(url)
        if page is None:
            return []

        if self._is_login_wall(page):
            logger.warning(
                "Facebook redirected the search to a login wall — "
                "page search is not possible without authentication. "
                "Try scraping specific page URLs instead."
            )
            return []

        slugs = self._extract_page_slugs(page.html_content)
        if not slugs:
            logger.warning("No page links found in search results for %r", query)
            return []

        leads: list[Lead] = []
        for slug in slugs:
            if len(leads) >= limit:
                break
            self._random_delay()
            try:
                lead = self.scrape_page(self.PAGE_URL.format(slug=slug))
            except Exception as exc:
                logger.error("Error scraping page %r: %s", slug, exc)
                continue
            if lead is not None:
                leads.append(lead)

        return leads

    def scrape_page(self, page_url: str) -> Lead | None:
        """Extract business data from a public Facebook Page.

        Visits the page and its About section to collect name, email, phone,
        website, address, and category. Fields not exposed publicly are left
        empty.

        Args:
            page_url: Full Facebook page URL.

        Returns:
            A Lead with whatever public data was found, or None if the page
            is unavailable or login-walled.
        """
        page = self._fetch(page_url)
        if page is None:
            return None

        if self._is_login_wall(page):
            logger.warning("Facebook login wall hit for %s — skipping", page_url)
            return None

        name = self._extract_name(page)
        if not name:
            logger.warning("Could not extract page name from %s", page_url)
            return None

        html = page.html_content
        email = self._first_valid_email(html)
        phone = self._first_valid_phone(html)
        website = self._extract_external_website(html)
        category, address = self._extract_meta_details(page, name)

        about_url = page_url.rstrip("/") + "/about"
        self._random_delay()
        about = self._fetch(about_url)
        if about is not None and not self._is_login_wall(about):
            about_html = about.html_content
            email = email or self._first_valid_email(about_html)
            phone = phone or self._first_valid_phone(about_html)
            website = website or self._extract_external_website(about_html)
        else:
            logger.info("About section not accessible for %s — keeping partial data", page_url)

        lead = Lead(
            name=name,
            email=email,
            phone=phone,
            website=website,
            address=address,
            category=category,
            source="facebook",
            raw_data={"page_url": page_url},
        )
        logger.info("Page scraped: %r email=%r phone=%r website=%r", name, email, phone, website)
        return lead

    def _fetch(self, url: str):
        """Fetch a URL with StealthyFetcher, returning None on any failure."""
        try:
            self.rate_limiter.acquire_sync()
            page = sync_retry(
                lambda: StealthyFetcher.fetch(
                    url,
                    headless=True,
                    solve_cloudflare=True,
                    timeout=self.TIMEOUT_MS,
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

    @staticmethod
    def _is_login_wall(page) -> bool:
        """Detect whether Facebook redirected to or rendered a login page."""
        final_url = str(getattr(page, "url", ""))
        if "/login" in final_url or "login.php" in final_url:
            return True
        meta = page.find('meta[property="og:title"]')
        if meta and meta.attrib.get("content", "").strip().lower() not in ("", "facebook"):
            return False
        title = page.find("title")
        title_text = title.text.lower() if title else ""
        return "log in" in title_text or "iniciar sesión" in title_text or "inicia sesión" in title_text

    @staticmethod
    def _extract_name(page) -> str:
        """Extract the page name from og:title or the document title."""
        meta = page.find('meta[property="og:title"]')
        if meta:
            name = meta.attrib.get("content", "").strip()
            if name and name.lower() != "facebook":
                return name
        title = page.find("title")
        if title:
            text = title.text.split("|")[0].strip()
            if text and text.lower() != "facebook":
                return text
        return ""

    @staticmethod
    def _extract_meta_details(page, name: str) -> tuple[str, str]:
        """Extract (category, address) from the og:description meta tag.

        Public pages expose a description like:
        "Gym. 1830 N Bayshore Dr, Miami, FL 33132. Rated 4.8/5..."
        Segments with like/follower counters and the page name itself are
        skipped.
        """
        meta = page.find('meta[property="og:description"]')
        if not meta:
            return "", ""
        content = meta.attrib.get("content", "")
        noise = ("likes", "talking about", "followers", "rated", "me gusta", "seguidores")
        parts = [
            p.strip() for p in content.split(".")
            if p.strip()
            and p.strip().lower() != name.lower()
            and not any(n in p.lower() for n in noise)
        ]
        category = ""
        address = ""
        for part in parts:
            if not category and not any(ch.isdigit() for ch in part) and len(part) <= 60:
                category = part
            elif not address and any(ch.isdigit() for ch in part) and "," in part:
                address = part
        return category, address

    @staticmethod
    def _extract_external_website(html: str) -> str:
        """Find the page's external website via Facebook's outbound link redirector.

        Facebook wraps page websites in l.facebook.com/l.php?u=<encoded-url>
        links; the first one is taken as the business website.
        """
        for match in re.finditer(r'https?://l\.facebook\.com/l\.php\?[^"\'<>\\\s]+', html):
            redirector = match.group(0).replace("&amp;", "&").replace("\\/", "/")
            target = parse_qs(urlparse(redirector).query).get("u", [""])[0]
            if not target:
                continue
            host = urlparse(target).netloc.lower()
            if not host or any(s in host for s in (
                "facebook", "fbcdn", "fb.com", "instagram", "whatsapp", "messenger", "meta.com",
            )):
                continue
            return target.split("?fbclid")[0]
        return ""

    def _extract_page_slugs(self, html: str) -> list[str]:
        """Extract candidate page slugs from search result HTML, deduplicated."""
        seen: dict[str, None] = {}
        for slug in PAGE_LINK_PATTERN.findall(html):
            slug = slug.rstrip("/")
            if slug.lower() in EXCLUDED_SLUGS or "." in slug[:1]:
                continue
            seen[slug] = None
        return list(seen)

    @staticmethod
    def _first_valid_email(html: str) -> str:
        """Return the first valid email in the HTML, or empty string."""
        for candidate in EMAIL_PATTERN.findall(html):
            if is_valid_email(candidate):
                return candidate.lower()
        return ""

    @staticmethod
    def _first_valid_phone(html: str) -> str:
        """Return the first normalizable phone in visible tel: links or text."""
        for match in re.finditer(r'tel:([+\d\s().\-]+)', html):
            normalized = normalize_phone(match.group(1))
            if normalized:
                return normalized
        return ""

    @staticmethod
    def _random_delay() -> None:
        """Sleep 4-8s (or the configured delays if higher) between pages."""
        lo = max(MIN_DELAY_SECONDS, settings.SCRAPING_DELAY_MIN)
        hi = max(MAX_DELAY_SECONDS, settings.SCRAPING_DELAY_MAX, lo)
        time.sleep(random.uniform(lo, hi))
