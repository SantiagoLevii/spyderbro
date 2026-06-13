import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from scrapling import Fetcher

from utils.concurrency import get_optimal_workers
from utils.rate_limiter import RateLimiter
from utils.validators import is_valid_email

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 15

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

SOCIAL_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "tiktok.com",
)

CONTACT_PATHS = ("/contact", "/contacto", "/contact-us", "/about")

ASSET_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")


class EmailScraper:
    """Extracts contact emails from business websites using static fetching."""

    TIMEOUT_SECONDS = 8
    MAX_PAGES = 3

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)

    def extract_from_website(self, url: str) -> str:
        """Visit a business website and look for a contact email.

        Checks the homepage first (mailto links, then text patterns), then
        falls back to common contact pages. Visits at most MAX_PAGES pages.

        Args:
            url: Business website URL.

        Returns:
            The first valid email found, or an empty string.
        """
        if not url:
            return ""

        if self._is_social_url(url):
            logger.debug("Skipping social network URL: %s", url)
            return ""

        pages_visited = 0

        page = self._fetch(url)
        pages_visited += 1
        if page is None:
            logger.debug("Homepage did not load, skipping contact pages: %s", url)
            return ""

        email = self._find_email(page)
        if email:
            logger.info("Email found on homepage %s: %s", url, email)
            return email

        for path in CONTACT_PATHS:
            if pages_visited >= self.MAX_PAGES:
                break
            contact_url = urljoin(url, path)
            page = self._fetch(contact_url)
            pages_visited += 1
            if page is None:
                continue
            email = self._find_email(page)
            if email:
                logger.info("Email found on %s: %s", contact_url, email)
                return email

        logger.debug("No email found for %s after %d pages", url, pages_visited)
        return ""

    def extract_batch(self, urls: list[str]) -> dict[str, str]:
        """Extract emails from many websites in parallel.

        Args:
            urls: Website URLs to scan.

        Returns:
            Mapping of url -> email found ('' when none). The rate limiter
            still paces individual requests across all workers.
        """
        unique_urls = list(dict.fromkeys(url for url in urls if url))
        if not unique_urls:
            return {}

        results: dict[str, str] = {}
        workers = min(get_optimal_workers(), len(unique_urls))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self.extract_from_website, url): url for url in unique_urls
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    results[url] = future.result()
                except Exception as exc:
                    logger.warning("Email batch task failed for %s: %s", url, exc)
                    results[url] = ""

        found = sum(1 for email in results.values() if email)
        logger.info("Email batch finished: %d/%d sites with email", found, len(unique_urls))
        return results

    def _fetch(self, url: str):
        """Fetch a page with Fetcher, returning None on any failure.

        Args:
            url: Page URL to fetch.

        Returns:
            The Scrapling response, or None if the request failed, returned a
            non-200 status, or redirected to a social network.
        """
        try:
            self.rate_limiter.acquire_sync()
            response = Fetcher.get(
                url,
                timeout=self.TIMEOUT_SECONDS,
                stealthy_headers=True,
                retries=1,
            )
        except Exception as exc:
            logger.debug("Failed to fetch %s: %s", url, exc)
            return None

        status = getattr(response, "status", 200)
        if status != 200:
            logger.debug("Non-200 status %s for %s", status, url)
            return None

        final_url = str(getattr(response, "url", url))
        if self._is_social_url(final_url):
            logger.debug("Redirected to social network: %s -> %s", url, final_url)
            return None

        return response

    def _find_email(self, page) -> str:
        """Search a fetched page for a valid email.

        Priority: mailto links first, then regex matches over the page HTML.

        Args:
            page: Scrapling response object.

        Returns:
            First valid email found, or an empty string.
        """
        for link in page.find_all('a[href^="mailto:"]'):
            href = link.attrib.get("href", "")
            candidate = href.removeprefix("mailto:").split("?")[0].strip()
            if self._is_usable(candidate):
                return candidate.lower()

        for candidate in EMAIL_PATTERN.findall(page.html_content):
            if self._is_usable(candidate):
                return candidate.lower()

        return ""

    @staticmethod
    def _is_usable(candidate: str) -> bool:
        """Check that a candidate passes validation and is not an asset path."""
        if candidate.lower().endswith(ASSET_EXTENSIONS):
            return False
        return is_valid_email(candidate)

    @staticmethod
    def _is_social_url(url: str) -> bool:
        """Check whether a URL points to a social network instead of a real site."""
        host = urlparse(url).netloc.lower().removeprefix("www.")
        return any(host == domain or host.endswith("." + domain) for domain in SOCIAL_DOMAINS)
