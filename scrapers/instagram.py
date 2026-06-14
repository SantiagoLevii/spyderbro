import json
import logging
import random
import re
import time

from scrapling import StealthyFetcher

from config.settings import settings
from models.lead import Lead
from utils.abort import AbortMixin
from utils.browser_config import get_stealth_fetch_kwargs
from utils.cookies import load_cookies
from utils.rate_limiter import RateLimiter
from utils.retry import sync_retry
from utils.validators import is_valid_email, normalize_phone

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 10

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_PATTERN = re.compile(r"\+?\d[\d\s().\-]{7,}\d")
USERNAME_PATTERN = re.compile(r'"username"\s*:\s*"([a-zA-Z0-9._]+)"')

RATE_LIMIT_WAIT_SECONDS = 30
MIN_DELAY_SECONDS = 3.0
MAX_DELAY_SECONDS = 7.0


class InstagramScraper(AbortMixin):
    """Scrapes business leads from public Instagram profiles.

    Only public data is accessed — no login, no official API. Follower lists
    and hashtag feeds are login-walled by Instagram, so those methods are
    best-effort and may return empty results.
    """

    PROFILE_URL = "https://www.instagram.com/{username}/"
    FOLLOWERS_URL = "https://www.instagram.com/{username}/followers/"
    HASHTAG_URL = "https://www.instagram.com/explore/tags/{hashtag}/"
    TIMEOUT_MS = 12000

    SOURCE = "instagram"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)
        self.cookies = load_cookies(self.SOURCE)
        self.source = self.SOURCE
        self.aborted_reason = ""

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """Dispatch by query prefix.

        '@account' scrapes followers of that account, '#tag' scrapes recent
        hashtag posts, a bare username scrapes that single profile.

        Args:
            query: '@username', '#hashtag', or plain username.
            limit: Maximum number of leads to return.

        Returns:
            List of Lead objects with source='instagram'.
        """
        if query.startswith("@"):
            return self.scrape_followers_of(query[1:], limit)
        if query.startswith("#"):
            return self.search_by_hashtag(query[1:], limit)

        lead = self.scrape_profile_bio(query)
        return [lead] if lead else []

    def scrape_profile_bio(self, username: str) -> Lead | None:
        """Extract lead data from a public Instagram business profile.

        Args:
            username: Instagram handle without the '@'.

        Returns:
            A Lead built from the profile bio, or None if the profile is
            private, unavailable, or login-walled.
        """
        url = self.PROFILE_URL.format(username=username)
        page = self._fetch_with_retry(url)
        if page is None:
            return None

        html = page.html_content

        if self._is_login_wall(page):
            logger.warning("Instagram login wall hit for profile %r — skipping", username)
            return None

        if '"is_private":true' in html:
            logger.info("Profile %r is private — skipping", username)
            return None

        full_name = self._json_value(html, "full_name")
        biography = self._json_value(html, "biography")
        external_url = self._json_value(html, "external_url")
        category = self._json_value(html, "category_name")
        business_email = self._json_value(html, "business_email")

        if not biography and not full_name:
            meta = page.find('meta[property="og:description"]')
            biography = meta.attrib.get("content", "") if meta else ""

        email = business_email if is_valid_email(business_email) else self._extract_email(biography)
        phone = self._extract_phone(biography)

        if not (full_name or biography):
            logger.warning("No profile data found for %r — page may have changed", username)
            return None

        lead = Lead(
            name=full_name or username,
            email=email,
            phone=phone,
            website=external_url,
            category=category,
            source="instagram",
            raw_data={"username": username, "bio": biography[:300]},
        )
        logger.info("Profile scraped: %r email=%r phone=%r", username, email, phone)
        return lead

    def scrape_followers_of(self, username: str, limit: int) -> list[Lead]:
        """Extract leads from the followers of an Instagram account.

        Instagram only exposes follower lists to logged-in users. Without
        authentication this hits a login wall, in which case an empty list
        is returned. Followers without email or phone in their bio are
        discarded.

        Args:
            username: Account whose followers to scrape, without '@'.
            limit: Maximum number of leads to return.

        Returns:
            Leads for followers that have email or phone in their bio.
        """
        url = self.FOLLOWERS_URL.format(username=username)
        page = self._fetch_with_retry(url)
        if page is None:
            return []

        if self._is_login_wall(page):
            logger.warning(
                "Instagram requires login to view followers of %r — "
                "follower scraping is not possible without authentication",
                username,
            )
            return []

        usernames = self._extract_usernames(page.html_content, exclude=username)
        if not usernames:
            logger.warning("No follower usernames found for %r", username)
            return []

        return self._scrape_profiles(usernames, limit, require_contact=True)

    def search_by_hashtag(self, hashtag: str, limit: int) -> list[Lead]:
        """Extract leads from recent posts under a hashtag.

        Instagram login-walls hashtag pages for anonymous visitors. If the
        wall is hit, an empty list is returned.

        Args:
            hashtag: Hashtag without the '#'.
            limit: Maximum number of leads to return.

        Returns:
            Leads built from the profiles of recent posters.
        """
        url = self.HASHTAG_URL.format(hashtag=hashtag)
        page = self._fetch_with_retry(url)
        if page is None:
            return []

        if self._is_login_wall(page):
            logger.warning(
                "Instagram requires login for hashtag #%s — "
                "hashtag scraping is not possible without authentication",
                hashtag,
            )
            return []

        usernames = self._extract_usernames(page.html_content)
        if not usernames:
            logger.warning("No post authors found for #%s — page may be login-walled or empty", hashtag)
            return []

        return self._scrape_profiles(usernames, limit, require_contact=False)

    def _scrape_profiles(self, usernames: list[str], limit: int, require_contact: bool) -> list[Lead]:
        """Scrape a list of profiles with conservative delays.

        Args:
            usernames: Profile handles to visit.
            limit: Stop after collecting this many leads.
            require_contact: If True, keep only leads with email or phone.

        Returns:
            Collected leads.
        """
        leads: list[Lead] = []
        self._start_guard()
        for username in usernames:
            if len(leads) >= limit or self._should_abort():
                break
            self._random_delay()
            try:
                lead = self.scrape_profile_bio(username)
            except Exception as exc:
                logger.error("Error scraping profile %r: %s", username, exc)
                self._record_fetch(False)
                continue
            self._record_fetch(lead is not None)
            if lead is None:
                continue
            if require_contact and not (lead.email or lead.phone):
                logger.debug("Skipping %r: no contact info in bio", username)
                continue
            leads.append(lead)
        return leads

    def _fetch_with_retry(self, url: str):
        """Fetch a URL with StealthyFetcher, retrying once after 30s on HTTP 429.

        Args:
            url: Page to fetch.

        Returns:
            The response, or None if the fetch failed twice or errored.
        """
        for attempt in (1, 2):
            try:
                self.rate_limiter.acquire_sync()
                fetch_kwargs = get_stealth_fetch_kwargs(timeout=self.TIMEOUT_MS, solve_cloudflare=True)
                fetch_kwargs["cookies"] = self.cookies
                page = sync_retry(
                    lambda: StealthyFetcher.fetch(url, **fetch_kwargs),
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
                    time.sleep(RATE_LIMIT_WAIT_SECONDS)
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
        """Detect whether Instagram redirected to or rendered a login page."""
        final_url = str(getattr(page, "url", ""))
        if "/accounts/login" in final_url:
            return True
        title = page.find("title")
        title_text = title.text.lower() if title else ""
        return "login" in title_text or "iniciar sesión" in title_text

    @staticmethod
    def _json_value(html: str, key: str) -> str:
        """Extract a string value for a key from embedded JSON in the page HTML."""
        match = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
        if not match:
            return ""
        try:
            return json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            return match.group(1)

    @staticmethod
    def _extract_usernames(html: str, exclude: str = "") -> list[str]:
        """Extract unique usernames from embedded JSON, preserving order."""
        seen: dict[str, None] = {}
        for name in USERNAME_PATTERN.findall(html):
            if name != exclude:
                seen[name] = None
        return list(seen)

    @staticmethod
    def _extract_email(text: str) -> str:
        """Return the first valid email found in text, or empty string."""
        for candidate in EMAIL_PATTERN.findall(text or ""):
            if is_valid_email(candidate):
                return candidate.lower()
        return ""

    @staticmethod
    def _extract_phone(text: str) -> str:
        """Return the first normalizable phone found in text, or empty string."""
        for candidate in PHONE_PATTERN.findall(text or ""):
            normalized = normalize_phone(candidate)
            if normalized:
                return normalized
        return ""

    @staticmethod
    def _random_delay() -> None:
        """Sleep 3-7s (or the configured delays if higher) between profiles."""
        lo = max(MIN_DELAY_SECONDS, settings.SCRAPING_DELAY_MIN)
        hi = max(MAX_DELAY_SECONDS, settings.SCRAPING_DELAY_MAX, lo)
        time.sleep(random.uniform(lo, hi))
