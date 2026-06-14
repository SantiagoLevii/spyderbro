import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

from scrapling import DynamicFetcher

from config.settings import settings
from models.lead import Lead
from scrapers.email_scraper import EmailScraper
from utils.browser_config import get_stealth_fetch_kwargs
from utils.checkpoint import ScrapingCheckpoint
from utils.hardware import detect_hardware
from utils.rate_limiter import RateLimiter
from utils.retry import sync_retry
from utils.terminal import ScrapeProgress, ask_resume
from utils.validators import is_valid_email, normalize_phone

CHECKPOINT_EVERY = 10
REQUESTS_PER_MINUTE = 20
MAX_CONCURRENT_BROWSERS = 6

logger = logging.getLogger(__name__)


def _has_icon_char(text: str) -> bool:
    return any(0xE000 <= ord(c) <= 0xF8FF for c in text)


def _is_dot_separator(text: str) -> bool:
    return bool(text) and ord(text[0]) in (0x00B7, 0x22C5, 0x2027, 0xFF65, 0x30FB)


class GoogleMapsScraper:
    """Scrapes business leads from Google Maps search results."""

    BASE_URL = "https://www.google.com/maps/search/{query}"

    def __init__(self) -> None:
        self.fetcher = DynamicFetcher
        self.email_scraper = EmailScraper()
        self.checkpoint = ScrapingCheckpoint()
        self.allow_resume = True
        self.rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)
        self.workers_used = 0
        self.fetch_seconds_total = 0.0
        self._fetch_semaphore = threading.Semaphore(MAX_CONCURRENT_BROWSERS)
        self._stats_lock = threading.Lock()

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """Search Google Maps for businesses matching query and return up to limit leads.

        Args:
            query: Search term (e.g. "gyms in Miami").
            limit: Maximum number of leads to return.

        Returns:
            List of Lead objects with phone and website enriched from detail pages.
        """
        leads: list[Lead] = []
        url = self.BASE_URL.format(query=quote(query))
        logger.info("Starting Google Maps scrape: query=%r limit=%d url=%s", query, limit, url)

        resume_leads, start_index = self._check_resume(query)

        try:
            self.rate_limiter.acquire_sync()
            # List page needs the dynamic feed to render before scrolling, so it
            # waits for network idle and keeps CSS (block_resources off here).
            list_kwargs = get_stealth_fetch_kwargs(
                network_idle=True, block_resources=False, timeout=30000
            )
            list_kwargs["page_action"] = self._make_scroll_action(limit)
            page = sync_retry(
                lambda: self.fetcher.fetch(url, **list_kwargs),
                max_retries=2,
            )
        except Exception as exc:
            logger.error("Failed to load Google Maps page: %s", exc, exc_info=True)
            return leads

        try:
            leads = self._extract_leads(page, limit, query, resume_leads, start_index)
        except Exception as exc:
            logger.error("Error during lead extraction: %s", exc)

        self.checkpoint.clear("google_maps", query)
        logger.info("Scrape complete -- %d leads extracted", len(leads))
        return leads

    def _check_resume(self, query: str) -> tuple[list[Lead], int]:
        """Offer to resume a previous interrupted session if a checkpoint exists.

        Args:
            query: Current search query.

        Returns:
            Tuple of (already-scraped leads, index of the next item to
            process). (([], 0)) when starting fresh.
        """
        state = self.checkpoint.load("google_maps", query)
        if state is None:
            return [], 0

        saved_leads, next_index = state
        if not self.allow_resume:
            return [], 0

        age = self.checkpoint.age_seconds("google_maps", query) or 0
        if ask_resume(query, age, len(saved_leads)):
            logger.info("Resuming from checkpoint: %d leads, item %d", len(saved_leads), next_index)
            return saved_leads, next_index

        self.checkpoint.clear("google_maps", query)
        logger.info("User declined resume — starting fresh")
        return [], 0

    def _extract_leads(
        self,
        page,
        limit: int,
        query: str,
        resume_leads: list[Lead] | None = None,
        start_index: int = 0,
    ) -> list[Lead]:
        """Extract leads from the list page, enriching details in parallel.

        Phase A parses all items, then fetches every detail page (phone +
        website) concurrently with a ThreadPoolExecutor; the rate limiter
        paces request starts and a semaphore caps simultaneous browsers.
        Phase B scrapes emails for all websites in parallel.

        Saves a checkpoint every CHECKPOINT_EVERY new leads and on Ctrl+C.
        """
        leads: list[Lead] = list(resume_leads or [])

        result_items = page.find_all('[role="article"]') or page.find_all(".Nv2PK")

        if not result_items:
            logger.warning("No result items found -- selectors may need updating")
            return leads

        parsed: list[tuple[Lead, str]] = []
        for item in result_items[start_index:limit]:
            lead = self._parse_item(item)
            if lead is None:
                continue
            parsed.append((lead, self._get_detail_url(item)))

        if not parsed:
            return leads

        self.workers_used = min(self._optimal_workers(), len(parsed))
        progress = ScrapeProgress(total=len(parsed))
        completed = 0

        executor = ThreadPoolExecutor(max_workers=self.workers_used)
        try:
            futures = {
                executor.submit(self._enrich_contact, url): lead for lead, url in parsed
            }
            for future in as_completed(futures):
                lead = futures[future]
                completed += 1
                try:
                    phone, website = future.result()
                except Exception as exc:
                    logger.error("Error enriching lead %r: %s", lead.name, exc, exc_info=True)
                    progress.lead_error(lead.name, str(exc))
                    continue

                lead.phone = normalize_phone(phone) or phone
                lead.website = website
                leads.append(lead)
                progress.lead_done(lead)
                if len(leads) % CHECKPOINT_EVERY == 0:
                    self.checkpoint.save(leads, start_index + completed, "google_maps", query)
        except KeyboardInterrupt:
            executor.shutdown(wait=False, cancel_futures=True)
            self.checkpoint.save(leads, start_index + completed, "google_maps", query)
            logger.warning(
                "Interrupted — checkpoint saved with %d leads (resume by re-running the same command)",
                len(leads),
            )
            raise
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            progress.close()

        self._enrich_emails(leads)
        return leads[:limit]

    @staticmethod
    def _optimal_workers() -> int:
        """Thread workers for detail fetching: settings override, else hardware."""
        if settings.WORKERS > 0:
            return min(settings.WORKERS, 64)
        return detect_hardware().recommended_workers

    def _enrich_contact(self, detail_url: str) -> tuple[str, str]:
        """Worker task: fetch one detail page and return (phone, website).

        The semaphore caps simultaneous browser instances; the rate limiter
        inside _fetch_detail paces request starts. Fetch wall time is
        accumulated to estimate the speedup vs sequential scraping.
        """
        if not detail_url:
            return "", ""
        with self._fetch_semaphore:
            started = time.monotonic()
            result = self._fetch_detail(detail_url)
            with self._stats_lock:
                self.fetch_seconds_total += time.monotonic() - started
            return result

    def _enrich_emails(self, leads: list[Lead]) -> None:
        """Scrape emails for all lead websites in parallel and assign them."""
        if not settings.EMAIL_SCRAPING_ENABLED:
            logger.info("Email scraping disabled — skipping email enrichment")
            return
        websites = [lead.website for lead in leads if lead.website and not lead.email]
        if not websites:
            return
        emails = self.email_scraper.extract_batch(websites)
        for lead in leads:
            email = emails.get(lead.website, "")
            if not lead.email and is_valid_email(email):
                lead.email = email

    def _fetch_detail(self, url: str) -> tuple[str, str]:
        """Fetch a business detail page and return (phone, website).

        Args:
            url: Full Google Maps place URL.

        Returns:
            Tuple of (phone, website). Each is empty string if not found.
        """
        try:
            self.rate_limiter.acquire_sync()
            # Detail pages: block images/CSS/fonts/ads for speed (text-only parse).
            detail_kwargs = get_stealth_fetch_kwargs(timeout=10000)
            detail = sync_retry(
                lambda: self.fetcher.fetch(url, **detail_kwargs),
                max_retries=2,
            )
        except Exception as exc:
            logger.error("Failed to fetch detail page %s: %s", url, exc, exc_info=True)
            return "", ""

        phone = self._extract_phone(detail)
        website = self._extract_website(detail)
        return phone, website

    @staticmethod
    def _get_detail_url(item) -> str:
        """Return the Google Maps place URL from an article element's first <a> link."""
        link = item.find("a")
        if link:
            href = link.attrib.get("href", "")
            if href and "google.com/maps/place" in href:
                return href
        return ""

    @staticmethod
    def _extract_phone(detail_page) -> str:
        """Extract phone number from a detail page.

        Google Maps stores the phone in: button[data-item-id^="phone:tel:"]
        The raw number is in data-item-id after the "phone:tel:" prefix.
        """
        el = detail_page.find('[data-item-id^="phone:tel:"]')
        if not el:
            return ""
        data_id = el.attrib.get("data-item-id", "")
        raw = data_id.split("phone:tel:")[-1]
        return raw.strip() if raw else ""

    @staticmethod
    def _extract_website(detail_page) -> str:
        """Extract website URL from a detail page.

        Google Maps stores the external website in: a[data-item-id="authority"]
        """
        el = detail_page.find('[data-item-id="authority"]')
        if not el:
            return ""
        return el.attrib.get("href", "").strip()

    @staticmethod
    def _make_scroll_action(limit: int):
        """Return a sync page_action (Playwright sync API) that scrolls the results feed."""
        def scroll_action(page) -> None:
            scrolls = max(2, limit // 5)
            try:
                for _ in range(scrolls):
                    page.evaluate('() => { const f = document.querySelector(\'[role="feed"]\'); if(f) f.scrollBy(0, 600); else window.scrollBy(0, 600); }')
                    page.wait_for_timeout(1500)
            except Exception as exc:
                logger.warning("Scroll action stopped early: %s", exc)
        return scroll_action

    def _parse_item(self, item) -> Lead | None:
        """Parse a single result item element into a Lead.

        DOM structure (verified 2026-06-12):
          .qBF1Pd           -> business name
          .MW4etd           -> rating text, comma as decimal separator ("4,6")
          .W4Efsd[0]        -> rating block (skip)
          .W4Efsd[1]        -> combined row (skip)
          .W4Efsd[2]        -> category + address spans
          .W4Efsd[3]        -> opening hours (skip)
        """
        try:
            name_el = item.find(".qBF1Pd") or item.find(".NrDZNb")
            name = name_el.text.strip() if name_el else ""
            if not name:
                return None

            rating_el = item.find(".MW4etd")
            rating_text = (rating_el.text.strip() if rating_el else "0").replace(",", ".")
            try:
                rating = float(rating_text)
            except ValueError:
                rating = 0.0

            category, address = self._parse_category_address(item)

            return Lead(
                name=name,
                category=category,
                address=address,
                rating=rating,
                source="google_maps",
                raw_data={"html": str(item)[:500]},
            )
        except Exception as exc:
            logger.warning("Failed to parse item: %s", exc)
            return None

    @staticmethod
    def _parse_category_address(item) -> tuple[str, str]:
        """Extract category and address from the third .W4Efsd block.

        Structure: <span>Category</span> [dot] [icon] [dot] <span>Address</span>
        Uses ord() checks for special chars. Uses .attrib for Scrapling Selector compat.
        """
        w4_els = item.find_all(".W4Efsd")
        if len(w4_els) < 3:
            return "", ""

        info_block = w4_els[2]
        plain_spans: list[str] = []
        for span in info_block.find_all("span"):
            if span.attrib.get("class"):
                continue
            text = span.text.strip()
            if not text:
                continue
            if _is_dot_separator(text):
                continue
            if _has_icon_char(text):
                continue
            plain_spans.append(text)

        unique: list[str] = list(dict.fromkeys(plain_spans))

        category = unique[0] if unique else ""
        address = unique[-1] if len(unique) > 1 else ""
        return category, address

