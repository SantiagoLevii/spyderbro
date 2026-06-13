import asyncio
import html as html_lib
import json
import logging
import random
import re
import unicodedata
from urllib.parse import parse_qs, quote, unquote, urlparse

import aiohttp
from scrapling import Fetcher

from config.settings import settings
from models.lead import Lead
from scrapers.email_scraper import EmailScraper
from utils.checkpoint import ScrapingCheckpoint
from utils.rate_limiter import RateLimiter
from utils.terminal import ask_resume
from utils.validators import normalize_phone

logger = logging.getLogger(__name__)

PHONE_PATTERN = re.compile(r"\+?\d[\d\s().\-]{7,}\d")
TITLE_SEPARATORS = re.compile(r"\s*[|\-–—:·]\s*")

SERPER_ENDPOINT = "https://google.serper.dev/search"
DDG_URL = "https://html.duckduckgo.com/html/?q={query}"

DDG_MIN_DELAY = 8.0
DDG_MAX_DELAY = 15.0
RATE_LIMIT_WAIT_SECONDS = 60
MAX_EMAIL_WORKERS = 3
SERPER_REQUESTS_PER_MINUTE = 30
DDG_REQUESTS_PER_MINUTE = 6

MIN_DORK_LENGTH = 3
MAX_DORK_LENGTH = 500
STOP_WORDS = ("in", "en", "de", "the")

SKIP_DOMAINS = (
    "duckduckgo.com", "google.com", "bing.com", "brave.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "youtube.com", "wikipedia.org", "reddit.com", "tripadvisor.com",
)


class DorksScraper:
    """Finds business leads by running Google-dork-style queries on a search engine.

    Engine selection is dynamic: Serper.dev API (Google results) when
    SERPER_API_KEY is set in the environment, otherwise the free DuckDuckGo
    HTML endpoint with conservative delays.
    """

    TIMEOUT_SECONDS = 15

    def __init__(self) -> None:
        self.engine = self._detect_engine()
        self.email_scraper = EmailScraper()
        self.checkpoint = ScrapingCheckpoint()
        self.allow_resume = True
        requests_per_minute = (
            SERPER_REQUESTS_PER_MINUTE if self.engine == "serper" else DDG_REQUESTS_PER_MINUTE
        )
        self.rate_limiter = RateLimiter(requests_per_minute)

    def _detect_engine(self) -> str:
        """Pick the best available search engine from environment config.

        Priority: Serper.dev API if SERPER_API_KEY is set, otherwise the
        DuckDuckGo HTML endpoint. Never hardcoded.

        Returns:
            'serper' or 'duckduckgo'.
        """
        if settings.SERPER_API_KEY:
            logger.info("Dorks engine: Serper.dev API (SERPER_API_KEY found)")
            return "serper"
        logger.info("Dorks engine: DuckDuckGo HTML (no SERPER_API_KEY in environment)")
        return "duckduckgo"

    @property
    def engine_label(self) -> str:
        """Human-readable engine name for the session summary."""
        return "Serper.dev (Google)" if self.engine == "serper" else "DuckDuckGo"

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """Synchronous entry point for the CLI scraper registry."""
        return asyncio.run(self.search_leads(query, limit))

    async def scrape_async(self, query: str, limit: int) -> list[Lead]:
        """Async entry point used by the async pipeline."""
        return await self.search_leads(query, limit)

    async def search_leads(self, query: str, limit: int) -> list[Lead]:
        """Run generated dork queries and turn results into enriched leads.

        Each result URL is visited with EmailScraper to find a contact
        email; the phone is extracted from the result snippet when present.

        Args:
            query: Plain user query (e.g. "gyms in Miami").
            limit: Maximum number of leads to return.

        Returns:
            List of Lead objects with source='dorks_{engine}'.
        """
        dorks = self._generate_dork_queries(query)
        source_key = f"dorks_{self.engine}"
        leads, start_dork = self._check_resume(source_key, query)
        seen_urls: set[str] = {urlparse(l.website).netloc.lower().removeprefix("www.") for l in leads}

        try:
            results_per_dork = await asyncio.gather(
                *(self._run_dork(dork, index, len(dorks), limit)
                  for index, dork in enumerate(dorks[start_dork:], start=start_dork))
            )

            fresh: list[dict] = []
            for found in results_per_dork:
                for item in found:
                    url = item.get("url", "")
                    domain = urlparse(url).netloc.lower().removeprefix("www.")
                    if not url or domain in seen_urls or self._should_skip(domain):
                        continue
                    seen_urls.add(domain)
                    fresh.append(item)

            budget = max(0, limit - len(leads))
            leads.extend(await self._build_leads(fresh[:budget]))
        except KeyboardInterrupt:
            self.checkpoint.save(leads, start_dork, source_key, query)
            logger.warning("Interrupted — checkpoint saved with %d leads", len(leads))
            raise

        self.checkpoint.clear(source_key, query)
        leads = leads[:limit]
        logger.info("Dorks scrape complete: %d leads", len(leads))
        return leads

    async def _run_dork(self, dork: str, index: int, total: int, limit: int) -> list[dict]:
        """Run one dork query on the active engine, never raising.

        All dork queries run concurrently via asyncio.gather; the engine's
        rate limiter paces the actual requests.
        """
        logger.info("Running dork %d/%d: %s", index + 1, total, dork)
        try:
            if self.engine == "serper":
                return await self._search_serper(dork, limit)
            return await self._search_duckduckgo(dork, limit)
        except Exception as exc:
            logger.error("Dork query failed (%s): %s", dork, exc, exc_info=True)
            return []

    def _check_resume(self, source_key: str, query: str) -> tuple[list[Lead], int]:
        """Offer to resume a previous interrupted dorks session.

        Returns:
            Tuple of (already-built leads, index of the next dork query).
        """
        state = self.checkpoint.load(source_key, query)
        if state is None:
            return [], 0
        if not self.allow_resume:
            return [], 0

        saved_leads, next_dork = state
        age = self.checkpoint.age_seconds(source_key, query) or 0
        if ask_resume(query, age, len(saved_leads)):
            logger.info("Resuming dorks from query %d with %d leads", next_dork, len(saved_leads))
            return saved_leads, next_dork

        self.checkpoint.clear(source_key, query)
        return [], 0

    @staticmethod
    def _sanitize_query(query: str) -> str:
        """Clean a raw user query for safe use inside a dork.

        Normalizes Unicode to NFC (so accented chars and ñ are well-formed),
        removes control characters, and collapses runs of whitespace into a
        single space. Spaces and non-ASCII letters are kept verbatim: Serper
        accepts them and aiohttp encodes the JSON body as UTF-8.

        Args:
            query: Raw query string from the CLI.

        Returns:
            A trimmed, single-spaced, NFC-normalized query.
        """
        normalized = unicodedata.normalize("NFC", query)
        without_controls = re.sub(r"[\x00-\x1f\x7f]", " ", normalized)
        return re.sub(r"\s+", " ", without_controls).strip()

    def _generate_dork_queries(self, query: str) -> list[str]:
        """Generate dork query variants from a plain user query.

        The query is sanitized first. Each candidate is validated to be
        between MIN_DORK_LENGTH and MAX_DORK_LENGTH characters; degenerate
        or duplicate variants are dropped so an empty/blank "q" is never
        sent to the search engine.

        Args:
            query: Plain query like "gyms in Miami".

        Returns:
            List of distinct, length-validated dork queries (up to 5).
        """
        clean = self._sanitize_query(query)
        words = [w for w in clean.split() if w.lower() not in STOP_WORDS]
        head = words[0] if words else clean
        tail = words[-1] if len(words) > 1 else ""

        candidates = [
            f'"{clean}" "contact" email',
            f'"{clean}" "@gmail.com" OR "@yahoo.com"',
            f'"{clean}" inurl:contact',
        ]
        if tail:
            candidates.append(f'site:yelp.com "{head}" "{tail}" phone')
            candidates.append(f'"{head}" "{tail}" filetype:html "email us"')
        else:
            candidates.append(f'site:yelp.com "{head}" phone')
            candidates.append(f'"{head}" filetype:html "email us"')

        valid: list[str] = []
        for dork in candidates:
            dork = dork.strip()
            if not (MIN_DORK_LENGTH <= len(dork) <= MAX_DORK_LENGTH):
                logger.warning("Skipping invalid dork (len=%d): %r", len(dork), dork)
                continue
            if dork not in valid:
                valid.append(dork)
        return valid

    async def _search_serper(self, dork_query: str, limit: int) -> list[dict]:
        """Run a dork query on the Serper.dev API (Google results).

        Retries once after 60s on HTTP 429. The API key is sent only as a
        request header and never logged.

        Args:
            dork_query: Full dork query string.
            limit: Maximum results wanted.

        Returns:
            List of {title, url, description} dicts.
        """
        for attempt in (1, 2):
            status, data = await self._serper_request(dork_query, limit)
            if status == 429:
                if attempt == 1:
                    logger.warning("Serper API rate limited — waiting %ds and retrying once",
                                   RATE_LIMIT_WAIT_SECONDS)
                    await asyncio.sleep(RATE_LIMIT_WAIT_SECONDS)
                    continue
                logger.error("Serper API still rate limited after retry — giving up")
                return []
            if status != 200:
                logger.warning("Serper API returned status %s", status)
                return []
            return self._parse_serper_json(data)
        return []

    async def _serper_request(self, dork_query: str, limit: int) -> tuple[int, dict]:
        """Send one POST to Serper and return (status, parsed JSON or {}).

        An empty/blank query is rejected before the request: Serper answers
        such a body with HTTP 400 "Missing query parameter".
        """
        query = dork_query.strip()
        if not query:
            logger.warning("Skipping Serper request: empty query would return HTTP 400")
            return 400, {}

        payload = {"q": query, "num": min(limit, 20)}
        # API key travels only in the headers, so the full body is safe to log.
        logger.debug(
            "Serper POST %s body=%s",
            SERPER_ENDPOINT,
            json.dumps(payload, ensure_ascii=False),
        )
        headers = {
            "X-API-KEY": settings.SERPER_API_KEY,
            "Content-Type": "application/json",
        }
        await self.rate_limiter.acquire()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                SERPER_ENDPOINT,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS),
            ) as response:
                if response.status != 200:
                    return response.status, {}
                try:
                    return 200, await response.json()
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    logger.error("Serper API returned invalid JSON: %s", exc)
                    return 200, {}

    @staticmethod
    def _parse_serper_json(data: dict) -> list[dict]:
        """Extract {title, url, description} entries from a Serper API response."""
        return [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "description": item.get("snippet", ""),
            }
            for item in data.get("organic", [])
        ]

    async def _search_duckduckgo(self, dork_query: str, limit: int) -> list[dict]:
        """Run a dork query on the DuckDuckGo HTML endpoint.

        Applies a conservative 8-15s delay before each query and retries
        once after 60s on HTTP 429.

        Args:
            dork_query: Full dork query string.
            limit: Maximum results wanted.

        Returns:
            List of {title, url, description} dicts.
        """
        url = DDG_URL.format(query=quote(dork_query))

        for attempt in (1, 2):
            await self.rate_limiter.acquire()
            await asyncio.sleep(random.uniform(DDG_MIN_DELAY, DDG_MAX_DELAY))
            try:
                page = await asyncio.to_thread(
                    Fetcher.get,
                    url,
                    timeout=self.TIMEOUT_SECONDS,
                    stealthy_headers=True,
                    retries=1,
                )
            except Exception as exc:
                logger.error("DuckDuckGo fetch failed: %s", exc)
                return []

            status = getattr(page, "status", 200)
            if status == 429 or status == 403:
                if attempt == 1:
                    logger.warning("DuckDuckGo blocked (status %s) — waiting %ds and retrying once",
                                   status, RATE_LIMIT_WAIT_SECONDS)
                    await asyncio.sleep(RATE_LIMIT_WAIT_SECONDS)
                    continue
                logger.error("DuckDuckGo still blocking after retry — giving up on this query")
                return []
            if status != 200:
                logger.warning("DuckDuckGo returned status %s", status)
                return []

            return self.parse_duckduckgo_html(page.html_content)[:limit]
        return []

    @staticmethod
    def parse_duckduckgo_html(html: str) -> list[dict]:
        """Parse DuckDuckGo HTML results into {title, url, description} dicts.

        Result links use the //duckduckgo.com/l/?uddg=<encoded> redirector;
        the real URL is decoded from the uddg parameter.
        """
        results = []
        blocks = re.findall(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)',
            html,
            flags=re.DOTALL,
        )
        for href, raw_title, trailing in blocks:
            url = href
            if "uddg=" in href:
                query_string = urlparse(href).query
                url = unquote(parse_qs(query_string).get("uddg", [""])[0])
            title = html_lib.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()

            snippet = ""
            snippet_match = re.search(
                r'class="result__snippet"[^>]*>(.*?)</a>', trailing, flags=re.DOTALL
            )
            if snippet_match:
                snippet = html_lib.unescape(re.sub(r"<[^>]+>", "", snippet_match.group(1))).strip()

            if url and title:
                results.append({"title": title, "url": url, "description": snippet})
        return results

    async def _build_leads(self, results: list[dict]) -> list[Lead]:
        """Turn search results into Leads, enriching each URL with EmailScraper.

        Email scraping is sync, so it runs in threads limited to 3 workers.
        """
        semaphore = asyncio.Semaphore(MAX_EMAIL_WORKERS)
        source = f"dorks_{self.engine}"

        async def build_one(item: dict) -> Lead | None:
            url = item["url"]
            async with semaphore:
                try:
                    email = await asyncio.to_thread(self.email_scraper.extract_from_website, url)
                except Exception as exc:
                    logger.warning("Email scrape failed for %s: %s", url, exc)
                    email = ""

            name = self._name_from_title(item.get("title", ""))
            if not name:
                return None

            return Lead(
                name=name,
                email=email,
                phone=self._phone_from_snippet(item.get("description", "")),
                website=url,
                source=source,
                raw_data={"title": item.get("title", ""), "snippet": item.get("description", "")[:300]},
            )

        leads = await asyncio.gather(*(build_one(item) for item in results))
        return [lead for lead in leads if lead is not None]

    @staticmethod
    def _name_from_title(title: str) -> str:
        """Take the first segment of a page title as the business name."""
        first = TITLE_SEPARATORS.split(title)[0].strip()
        return first[:80]

    @staticmethod
    def _phone_from_snippet(snippet: str) -> str:
        """Extract the first normalizable phone from a result snippet."""
        for candidate in PHONE_PATTERN.findall(snippet or ""):
            normalized = normalize_phone(candidate)
            if normalized:
                return normalized
        return ""

    @staticmethod
    def _should_skip(domain: str) -> bool:
        """Skip search engines, social networks, and aggregator domains."""
        return any(domain == d or domain.endswith("." + d) for d in SKIP_DOMAINS)
