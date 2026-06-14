import asyncio
import logging
import random
import re

from scrapling import StealthyFetcher

from config.settings import settings
from models.lead import Lead
from utils.abort import AbortMixin
from utils.browser_config import get_stealth_fetch_kwargs
from utils.cookies import load_cookies
from utils.rate_limiter import RateLimiter
from utils.retry import async_retry
from utils.validators import is_valid_email, normalize_phone

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 10

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_PATTERN = re.compile(r"\+?\d[\d\s().\-]{7,}\d")
USERNAME_IN_TITLE = re.compile(r"\(@([A-Za-z0-9_]+)\)")
SCREEN_NAME_PATTERN = re.compile(r'"screen_name"\s*:\s*"([A-Za-z0-9_]+)"')

MAX_CONCURRENT_REQUESTS = 3
RATE_LIMIT_WAIT_SECONDS = 60
MIN_DELAY_SECONDS = 4.0
MAX_DELAY_SECONDS = 8.0


def normalize_username(query: str) -> str:
    """Strip the leading '@' and surrounding whitespace from a username."""
    return query.strip().lstrip("@")


def extract_email_from_bio(bio: str) -> str:
    """Return the first valid email found in a bio text, or empty string."""
    for candidate in EMAIL_PATTERN.findall(bio or ""):
        if is_valid_email(candidate):
            return candidate.lower()
    return ""


def extract_phone_from_bio(bio: str) -> str:
    """Return the first normalizable phone found in a bio text, or empty string."""
    for candidate in PHONE_PATTERN.findall(bio or ""):
        normalized = normalize_phone(candidate)
        if normalized:
            return normalized
    return ""


class TwitterScraper(AbortMixin):
    """Scrapes business leads from public Twitter/X profiles.

    No API key and no login — public pages only. X login-walls search and
    most content for anonymous visitors, so results are best-effort.
    """

    PROFILE_URL = "https://x.com/{username}"
    SEARCH_URL = "https://x.com/search?q={keyword}&f=user"
    TIMEOUT_MS = 12000

    SOURCE = "twitter"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)
        self.cookies = load_cookies(self.SOURCE)
        self.source = self.SOURCE
        self.aborted_reason = ""

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """Synchronous entry point for the CLI scraper registry.

        '@username' scrapes that single profile; anything else runs a
        keyword search for user profiles.

        Args:
            query: '@username' or a keyword.
            limit: Maximum number of leads to return.

        Returns:
            List of Lead objects with source='twitter'.
        """
        return asyncio.run(self.scrape_async(query, limit))

    async def scrape_async(self, query: str, limit: int) -> list[Lead]:
        """Async dispatch: '@user' -> single profile, otherwise keyword search."""
        if query.startswith("@"):
            lead = await self.scrape_profile(normalize_username(query))
            return [lead] if lead else []
        return await self.search_by_keyword(query, limit)

    async def scrape_profile(self, username: str) -> Lead | None:
        """Extract lead data from a public Twitter/X profile.

        Args:
            username: Handle without the '@'.

        Returns:
            A Lead built from the profile bio, or None if the profile is
            unavailable or login-walled.
        """
        username = normalize_username(username)
        url = self.PROFILE_URL.format(username=username)
        page = await self._fetch_with_retry(url)
        if page is None:
            return None

        if self._is_login_wall(page):
            logger.warning("X login wall hit for profile %r — skipping", username)
            return None

        name, bio = self._extract_name_and_bio(page, username)
        if not name and not bio:
            logger.warning("No profile data found for %r — page may be walled or changed", username)
            return None

        website = self._extract_website(page.html_content)

        lead = Lead(
            name=name or username,
            email=extract_email_from_bio(bio),
            phone=extract_phone_from_bio(bio),
            website=website,
            source="twitter",
            raw_data={"username": username, "bio": bio[:300]},
        )
        logger.info("Profile scraped: %r email=%r phone=%r", username, lead.email, lead.phone)
        return lead

    async def search_by_keyword(self, keyword: str, limit: int) -> list[Lead]:
        """Search public X user results for a keyword and scrape each profile.

        X requires login for search; if the wall is hit an empty list is
        returned with a clear log message.

        Args:
            keyword: Search term.
            limit: Maximum number of leads to return.

        Returns:
            Leads for the profiles found.
        """
        url = self.SEARCH_URL.format(keyword=keyword.replace(" ", "%20"))
        page = await self._fetch_with_retry(url)
        if page is None:
            return []

        if self._is_login_wall(page):
            logger.warning(
                "X requires login for search — keyword search is not possible "
                "without authentication"
            )
            return []

        usernames = self._extract_usernames(page.html_content)
        if not usernames:
            logger.warning("No user results found for keyword %r", keyword)
            return []

        leads = await self.scrape_profiles_batch(usernames[:limit])
        return leads[:limit]

    async def scrape_profiles_batch(self, usernames: list[str]) -> list[Lead]:
        """Scrape multiple profiles concurrently, max 3 at a time.

        Args:
            usernames: Handles to scrape, with or without '@'.

        Returns:
            Leads for the profiles that could be scraped.
        """
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._start_guard()

        async def scrape_one(username: str) -> Lead | None:
            async with semaphore:
                if self._should_abort():
                    return None
                await self._random_delay()
                try:
                    lead = await self.scrape_profile(username)
                except Exception as exc:
                    logger.error("Error scraping profile %r: %s", username, exc)
                    self._record_fetch(False)
                    return None
                self._record_fetch(lead is not None)
                return lead

        results = await asyncio.gather(*(scrape_one(u) for u in usernames))
        return [lead for lead in results if lead is not None]

    async def _fetch_with_retry(self, url: str):
        """Fetch a URL, retrying once after 60s on HTTP 429.

        StealthyFetcher is sync (Playwright/Camoufox), so it runs in a
        thread to avoid blocking the event loop.
        """
        for attempt in (1, 2):
            try:
                await self.rate_limiter.acquire()
                fetch_kwargs = get_stealth_fetch_kwargs(timeout=self.TIMEOUT_MS, solve_cloudflare=True)
                fetch_kwargs["cookies"] = self.cookies
                page = await async_retry(
                    lambda: asyncio.to_thread(StealthyFetcher.fetch, url, **fetch_kwargs),
                    max_retries=2,
                )
            except Exception as exc:
                logger.error("Failed to fetch %s: %s", url, exc, exc_info=True)
                return None

            status = getattr(page, "status", 200)
            if status == 429:
                if attempt == 1:
                    logger.warning("Rate limited (429) on %s — waiting %ds and retrying once",
                                   url, RATE_LIMIT_WAIT_SECONDS)
                    await asyncio.sleep(RATE_LIMIT_WAIT_SECONDS)
                    continue
                logger.error("Still rate limited after retry on %s — giving up", url)
                return None

            if status != 200:
                logger.warning("Non-200 status %s for %s", status, url)
                return None

            return page
        return None

    @staticmethod
    def _is_login_wall(page) -> bool:
        """Detect whether X redirected to or rendered a login page."""
        final_url = str(getattr(page, "url", ""))
        if any(marker in final_url for marker in ("/login", "/i/flow", "mode=login", "/onboarding")):
            return True
        title = page.find("title")
        title_text = title.text.lower() if title else ""
        return "log in" in title_text or "iniciar sesión" in title_text

    @staticmethod
    def _clean_display_name(raw: str) -> str:
        """Strip '(@user)', 'on X' / '/ X' suffixes and extra spaces from a title."""
        name = USERNAME_IN_TITLE.sub("", raw).split("/")[0]
        name = re.sub(r"\s+on X\s*$", "", name, flags=re.IGNORECASE)
        return re.sub(r"\s{2,}", " ", name).strip()

    @classmethod
    def _extract_name_and_bio(cls, page, username: str) -> tuple[str, str]:
        """Extract (display name, bio) from og: meta tags or the title."""
        name = ""
        bio = ""

        meta_title = page.find('meta[property="og:title"]')
        if meta_title:
            name = cls._clean_display_name(meta_title.attrib.get("content", ""))

        meta_desc = page.find('meta[property="og:description"]') or page.find('meta[name="description"]')
        if meta_desc:
            bio = meta_desc.attrib.get("content", "").strip()

        if not name:
            title = page.find("title")
            if title and username.lower() in title.text.lower():
                name = cls._clean_display_name(title.text)

        return name, bio

    @staticmethod
    def _extract_website(html: str) -> str:
        """Extract the profile's website from embedded JSON expanded_url, if any."""
        match = re.search(r'"expanded_url"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
        if not match:
            return ""
        url = match.group(1).replace("\\/", "/")
        if "x.com" in url or "twitter.com" in url or "t.co" in url:
            return ""
        return url

    @staticmethod
    def _extract_usernames(html: str) -> list[str]:
        """Extract unique screen names from embedded JSON, preserving order."""
        seen: dict[str, None] = {}
        for name in SCREEN_NAME_PATTERN.findall(html):
            seen[name] = None
        return list(seen)

    @staticmethod
    async def _random_delay() -> None:
        """Async sleep 4-8s (or the configured delays if higher) between requests."""
        lo = max(MIN_DELAY_SECONDS, settings.SCRAPING_DELAY_MIN)
        hi = max(MAX_DELAY_SECONDS, settings.SCRAPING_DELAY_MAX, lo)
        await asyncio.sleep(random.uniform(lo, hi))
