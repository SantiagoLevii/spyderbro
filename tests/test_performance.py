"""Sprint L tests — limit distribution, email-scrape gating, shared state, cache."""
import asyncio
import threading

from models.lead import Lead
from pipeline.async_pipeline import (
    AsyncScrapingPipeline,
    distribute_limit,
    should_email_scrape,
)
from ui.progress import ScrapingState
from utils.session_cache import SessionCache


# --- Limit distribution -------------------------------------------------------

def test_distribute_limit_single_source():
    assert distribute_limit(50, ["google_maps"]) == {"google_maps": 50}


def test_distribute_limit_five_sources():
    sources = ["a", "b", "c", "d", "e"]
    assert distribute_limit(50, sources) == {s: 10 for s in sources}


def test_distribute_limit_fifteen_sources():
    sources = [f"s{i}" for i in range(15)]
    dist = distribute_limit(50, sources)
    assert all(v == 3 for v in dist.values())  # max(3, 50//15=3)


def test_distribute_limit_respects_minimum():
    sources = [f"s{i}" for i in range(10)]
    dist = distribute_limit(1, sources)  # 1//10 == 0 -> floored at 3
    assert all(v == 3 for v in dist.values())


def test_email_scraper_skips_visited_domain():
    from scrapers.email_scraper import EmailScraper

    scraper = EmailScraper()
    calls = {"n": 0}

    def fake_scan(url: str) -> str:
        calls["n"] += 1
        return "info@biz.com"

    scraper._scan_website = fake_scan
    first = scraper.extract_from_website("https://biz.com/")
    second = scraper.extract_from_website("https://biz.com/contact")
    assert first == "info@biz.com"
    assert second == "info@biz.com"  # served from the domain cache
    assert calls["n"] == 1  # the domain was scanned only once


# --- Email-scrape gating ------------------------------------------------------

def test_should_email_scrape_many_sources():
    assert should_email_scrape(["a", "b", "c", "d"], 10) is False


def test_should_email_scrape_single_source():
    assert should_email_scrape(["google_maps"], 20) is True


def test_should_email_scrape_user_disabled():
    assert should_email_scrape(["google_maps"], 10, user_disabled=True) is False


def test_should_email_scrape_high_limit():
    assert should_email_scrape(["google_maps"], 50) is False


# --- ScrapingState ------------------------------------------------------------

def test_scraping_state_add_lead_thread_safe():
    state = ScrapingState()

    def worker() -> None:
        for i in range(100):
            state.add_lead(Lead(name=f"L{i}", source="x"), "x")

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thr in threads:
        thr.start()
    for thr in threads:
        thr.join()
    assert len(state.leads) == 500
    assert state.source_progress["x"][0] == 500


def test_scraping_state_error_buffer_max_3():
    state = ScrapingState()
    for i in range(6):
        state.add_error(f"error {i}")
    assert len(state.errors) == 3
    assert state.errors == ["error 3", "error 4", "error 5"]


# --- SessionCache -------------------------------------------------------------

def test_session_cache_miss():
    SessionCache.clear()
    assert SessionCache.get("google_maps", "gyms") is None


def test_session_cache_hit():
    SessionCache.clear()
    leads = [Lead(name="A", source="google_maps")]
    SessionCache.set("google_maps", "Gyms", leads)
    assert SessionCache.get("google_maps", "gyms") == leads  # case-insensitive


# --- on_lead callback ---------------------------------------------------------

class _FakeScraper:
    def scrape(self, query: str, limit: int) -> list[Lead]:
        return [Lead(name=f"Lead{i}", phone=f"+54911000000{i}", source="fake")
                for i in range(limit)]


def test_on_lead_callback_called_per_lead():
    pipeline = AsyncScrapingPipeline(registry={"fake": _FakeScraper}, cache=None)
    seen: list[Lead] = []
    leads = asyncio.run(
        pipeline.run(["fake"], "q", 3, on_lead=lambda source, lead: seen.append(lead))
    )
    assert len(seen) == 3
    assert len(leads) == 3


# --- Source status propagation (Sprint N, Fix 2) ------------------------------

class _BlockedScraper:
    aborted_reason = "blocked"

    def scrape(self, query: str, limit: int) -> list[Lead]:
        return []


def test_pipeline_emits_blocked_status():
    pipeline = AsyncScrapingPipeline(registry={"b": _BlockedScraper}, cache=None)
    statuses: list[str] = []
    asyncio.run(
        pipeline.run(["b"], "q", 5,
                     on_source_status=lambda source, status, target=0: statuses.append(status))
    )
    assert "blocked" in statuses
    assert "done" not in statuses


def test_pipeline_emits_done_for_normal_source():
    pipeline = AsyncScrapingPipeline(registry={"fake": _FakeScraper}, cache=None)
    statuses: list[str] = []
    asyncio.run(
        pipeline.run(["fake"], "q", 2,
                     on_source_status=lambda source, status, target=0: statuses.append(status))
    )
    assert "done" in statuses
