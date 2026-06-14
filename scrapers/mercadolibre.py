import logging
import random
import time
from urllib.parse import quote

from scrapling import StealthyFetcher

from config.settings import settings
from models.lead import Lead
from scrapers.email_scraper import EmailScraper
from utils.abort import AbortMixin
from utils.browser_config import get_stealth_fetch_kwargs
from utils.cookies import load_cookies
from utils.rate_limiter import RateLimiter
from utils.validators import is_valid_email

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 10
# ML renders with heavy JS and needs a generous timeout (single source of truth).
TIMEOUT_MS = 25000
MAX_PAGES = 5
RESULTS_PER_PAGE = 50
SOURCE = "mercadolibre"

# Markers of MercadoLibre's "snoopy" anti-bot micro-landing shell, served with
# HTTP 200 in place of the real page when automation is detected. Confirmed to
# block StealthyFetcher from datacenter IPs — a residential PROXY_URL is needed.
_ANTIBOT_MARKERS = ("micro-landing-container", "snoopy-script", "requires javascript")


class MercadoLibreScraper(AbortMixin):
    """Scrapes sellers (stores) from MercadoLibre Argentina via public HTML.

    The official API now requires OAuth2, so this scrapes the public site with
    ``StealthyFetcher``: a category listing yields seller nicknames, and each
    ``/perfil/{nickname}`` page yields the store's name, website and reputation.
    Sellers are deduplicated by nickname; if a seller exposes a website, emails
    are enriched via :class:`EmailScraper`.

    Note: MercadoLibre's "snoopy" anti-bot serves a JS shell to detected
    automation; from blocked IPs this returns an empty list with a clear warning.
    Set ``PROXY_URL`` to a residential proxy to receive the real pages.
    """

    BASE_URL = "https://www.mercadolibre.com.ar"
    LISTADO_URL = "https://listado.mercadolibre.com.ar"

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)
        self.email_scraper = EmailScraper()
        self.cookies = load_cookies(SOURCE)
        self.source = SOURCE
        self.aborted_reason = ""

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point. Query is the product category.

        Whether to restrict to official stores is read from
        ``settings.ML_OFFICIAL_ONLY`` (set by the ``--ml-official-only`` flag).
        """
        official_only = bool(getattr(settings, "ML_OFFICIAL_ONLY", False))
        return self.search_sellers(query, official_only, limit)

    def search_sellers(
        self, category: str, official_stores_only: bool, limit: int
    ) -> list[Lead]:
        """Extract unique sellers from MercadoLibre listings by category.

        Args:
            category: Product category (e.g. "electronica", "ropa", "hogar").
            official_stores_only: If True, restrict to official stores
                (``_Tienda_oficial`` listing).
            limit: Maximum number of unique-seller leads.

        Returns:
            List of unique Leads — one per seller/store.
        """
        cat = quote(category.strip().replace(" ", "-"))
        leads: list[Lead] = []
        seen: set[str] = set()
        self._start_guard()

        for page_num in range(MAX_PAGES):
            if len(leads) >= limit or self._should_abort():
                break
            url = f"{self.LISTADO_URL}/{cat}"
            if official_stores_only:
                url += "_Tienda_oficial"
            if page_num > 0:
                offset = page_num * RESULTS_PER_PAGE + 1
                url += f"_Desde_{offset}"

            listing = self._fetch(url)
            self._record_fetch(listing is not None)
            if listing is None:
                break

            nicknames = self._collect_seller_nicknames(listing)
            if not nicknames:
                logger.info("No seller links on listing page %d for %r", page_num + 1, category)
                break

            added = 0
            for nick in nicknames:
                if len(leads) >= limit or self._should_abort():
                    break
                if nick in seen:
                    continue
                seen.add(nick)
                lead = self.scrape_seller_profile(nick, category)
                self._record_fetch(lead is not None)
                if lead is None:
                    continue
                leads.append(lead)
                added += 1
                self._random_delay()
            if added == 0:
                logger.info("No new sellers on page %d for %r — stopping", page_num + 1, category)
                break

        logger.info("MercadoLibre scrape complete: %d unique sellers", len(leads))
        return leads[:limit]

    def scrape_seller_profile(self, nickname: str, category: str = "") -> Lead | None:
        """Extract a seller's public profile from MercadoLibre.

        Args:
            nickname: Exact seller nickname (e.g. "SAMSUNG").
            category: Product category to label the lead with.

        Returns:
            A Lead with name, website, email, rating, category, or None.
        """
        url = f"{self.BASE_URL}/perfil/{nickname}"
        page = self._fetch(url)
        if page is None:
            return None

        name = ""
        h1 = page.find("h1")
        if h1:
            name = h1.text.strip()
        if not name:
            title = page.css("title::text").get() or ""
            name = title.split("|")[0].strip()
        if not name:
            name = nickname

        website = ""
        for href in page.css('a[href^="http"]::attr(href)').getall():
            low = href.lower()
            if "mercadolibre" in low or "mercadolivre" in low or "mlstatic" in low:
                continue
            if any(s in low for s in ("facebook.", "instagram.", "twitter.", "x.com/")):
                continue
            website = href.strip()
            break

        rating = 0.0
        rating_el = page.find('[class*="rating"]') or page.find('[itemprop="ratingValue"]')
        if rating_el:
            try:
                rating = float(rating_el.text.strip().replace(",", "."))
            except (ValueError, AttributeError):
                rating = 0.0

        email = ""
        if website and settings.EMAIL_SCRAPING_ENABLED:
            try:
                found = self.email_scraper.extract_from_website(website)
                if found and is_valid_email(found):
                    email = found.lower()
            except Exception as exc:
                logger.warning("Email enrichment failed for %s: %s", website, exc)

        return Lead(
            name=name,
            email=email,
            website=website,
            rating=rating,
            category=category,
            source=SOURCE,
            raw_data={"profile_url": url, "nickname": nickname},
        )

    @classmethod
    def _collect_seller_nicknames(cls, page) -> list[str]:
        """Collect unique seller nicknames from ``/perfil/{nickname}`` links."""
        nicks: list[str] = []
        seen: set[str] = set()
        for href in page.css('a[href*="/perfil/"]::attr(href)').getall():
            tail = href.split("/perfil/")[-1].split("?")[0].split("#")[0].strip("/")
            if not tail or "/" in tail:
                continue
            if tail in seen:
                continue
            seen.add(tail)
            nicks.append(tail)
        return nicks

    def _fetch(self, url: str):
        """Fetch a URL with StealthyFetcher; None on failure or anti-bot shell.

        MercadoLibre renders its listings with heavy JS, so it always needs
        ``network_idle=True`` (and a longer timeout) regardless of the global
        ``settings.NETWORK_IDLE`` default — otherwise the page is the anti-bot
        micro-landing shell and the source fast-fails as "blocked".
        """
        kwargs = get_stealth_fetch_kwargs(
            network_idle=True, timeout=TIMEOUT_MS, solve_cloudflare=True,
        )
        if settings.PROXY_URL:
            kwargs["proxy"] = settings.PROXY_URL
            # Log the proxy with its password redacted (OWASP A09).
            logger.debug("MercadoLibre using proxy %s", settings.get_safe_proxy_url())
        if self.cookies:
            kwargs["cookies"] = self.cookies
        try:
            self.rate_limiter.acquire_sync()
            page = StealthyFetcher.fetch(url, **kwargs)
        except Exception as exc:
            logger.error("Failed to fetch %s: %s", url, exc, exc_info=True)
            return None

        status = getattr(page, "status", 200)
        if status != 200:
            logger.warning("Non-200 status %s for %s", status, url)
            return None

        html = (page.html_content or "").lower()
        if any(marker in html for marker in _ANTIBOT_MARKERS):
            logger.warning(
                "MercadoLibre anti-bot (snoopy) blocked %s — served micro-landing "
                "shell instead of content; set PROXY_URL to bypass", url
            )
            return None
        return page

    @staticmethod
    def _random_delay() -> None:
        """Sleep a random interval between requests using the configured delays."""
        lo = max(0.0, settings.SCRAPING_DELAY_MIN)
        hi = max(lo, settings.SCRAPING_DELAY_MAX)
        time.sleep(random.uniform(lo, hi))
