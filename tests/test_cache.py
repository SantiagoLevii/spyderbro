import time

from models.lead import Lead
from utils.cache import ScrapingCache


def test_miss_on_empty_cache(tmp_path):
    cache = ScrapingCache(cache_dir=str(tmp_path))
    assert cache.get("google_maps", "gyms") is None


def test_set_then_get_roundtrip(tmp_path, sample_lead):
    cache = ScrapingCache(cache_dir=str(tmp_path))
    cache.set("google_maps", "gyms", [sample_lead])
    leads = cache.get("google_maps", "gyms")
    assert leads is not None
    assert leads[0].name == sample_lead.name
    assert leads[0].rating == sample_lead.rating


def test_expired_entry_is_miss(tmp_path, sample_lead):
    fresh = ScrapingCache(cache_dir=str(tmp_path))
    fresh.set("google_maps", "gyms", [sample_lead])
    expired = ScrapingCache(cache_dir=str(tmp_path), ttl_seconds=0)
    time.sleep(0.05)
    assert expired.get("google_maps", "gyms") is None


def test_age_seconds(tmp_path, sample_lead):
    cache = ScrapingCache(cache_dir=str(tmp_path))
    assert cache.age_seconds("google_maps", "gyms") is None
    cache.set("google_maps", "gyms", [sample_lead])
    age = cache.age_seconds("google_maps", "gyms")
    assert age is not None and age < 5


def test_clear_by_source(tmp_path, sample_lead):
    cache = ScrapingCache(cache_dir=str(tmp_path))
    cache.set("google_maps", "gyms", [sample_lead])
    cache.set("instagram", "gyms", [sample_lead])
    assert cache.clear("instagram") == 1
    assert cache.get("google_maps", "gyms") is not None
    assert cache.get("instagram", "gyms") is None


def test_clear_all(tmp_path, sample_lead):
    cache = ScrapingCache(cache_dir=str(tmp_path))
    cache.set("google_maps", "gyms", [sample_lead])
    cache.set("instagram", "gyms", [sample_lead])
    assert cache.clear() == 2


def test_corrupt_file_is_miss(tmp_path):
    cache = ScrapingCache(cache_dir=str(tmp_path))
    cache.set("google_maps", "gyms", [Lead(name="A")])
    for f in tmp_path.glob("*.json"):
        f.write_text("{not json", encoding="utf-8")
    assert cache.get("google_maps", "gyms") is None
