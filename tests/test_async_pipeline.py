import asyncio

from models.lead import Lead
from pipeline.async_pipeline import AsyncScrapingPipeline
from utils.checkpoint import ScrapingCheckpoint


class SyncMockScraper:
    def scrape(self, query: str, limit: int) -> list[Lead]:
        return [
            Lead(name="Gym A", phone="+13055046980", website="https://gyma.com", source="mock_sync"),
            Lead(name="Gym B", phone="+13055046981", source="mock_sync"),
        ]


class AsyncMockScraper:
    async def scrape_async(self, query: str, limit: int) -> list[Lead]:
        return [
            Lead(name="gym a official", phone="+13055046980", email="info@gyma.com", source="mock_async"),
            Lead(name="Gym C", email="c@gymc.com", source="mock_async"),
        ]


def test_parallel_scrapers_combined():
    pipeline = AsyncScrapingPipeline(registry={"s1": SyncMockScraper, "s2": AsyncMockScraper})
    leads = asyncio.run(pipeline.run(["s1", "s2"], "gyms", 10))
    names = {l.name for l in leads}
    assert "Gym B" in names
    assert "Gym C" in names
    assert len(leads) == 3


def test_pipeline_deduplicates_across_sources():
    pipeline = AsyncScrapingPipeline(registry={"s1": SyncMockScraper, "s2": AsyncMockScraper})
    leads = asyncio.run(pipeline.run(["s1", "s2"], "gyms", 10))
    assert pipeline.duplicates_removed == 1
    merged = next(l for l in leads if l.phone == "+13055046980")
    assert merged.email == "info@gyma.com"
    assert merged.website == "https://gyma.com"
    assert sorted(merged.raw_data["merged_from"]) == ["mock_async", "mock_sync"]


def test_pipeline_survives_scraper_failure():
    class BrokenScraper:
        def scrape(self, query: str, limit: int) -> list[Lead]:
            raise RuntimeError("boom")

    pipeline = AsyncScrapingPipeline(registry={"ok": SyncMockScraper, "broken": BrokenScraper})
    leads = asyncio.run(pipeline.run(["ok", "broken"], "gyms", 10))
    assert len(leads) == 2


def test_checkpoint_save_load_clear(tmp_path):
    cp = ScrapingCheckpoint(checkpoint_dir=str(tmp_path))
    leads = [Lead(name="Gym A", phone="+13055046980", source="google_maps")]

    assert cp.load("google_maps", "gyms") is None

    cp.save(leads, 7, "google_maps", "gyms")
    state = cp.load("google_maps", "gyms")
    assert state is not None
    loaded, page = state
    assert loaded[0].name == "Gym A"
    assert page == 7

    cp.clear("google_maps", "gyms")
    assert cp.load("google_maps", "gyms") is None


def test_checkpoint_expires(tmp_path):
    cp = ScrapingCheckpoint(checkpoint_dir=str(tmp_path), ttl_seconds=0)
    cp.save([Lead(name="Gym A", source="google_maps")], 1, "google_maps", "gyms")
    import time
    time.sleep(0.05)
    assert cp.load("google_maps", "gyms") is None
