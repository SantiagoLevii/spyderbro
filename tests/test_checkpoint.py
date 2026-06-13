import time

from utils.checkpoint import ScrapingCheckpoint


def test_load_missing_returns_none(tmp_path):
    cp = ScrapingCheckpoint(checkpoint_dir=str(tmp_path))
    assert cp.load("google_maps", "gyms") is None


def test_save_load_roundtrip(tmp_path, sample_leads):
    cp = ScrapingCheckpoint(checkpoint_dir=str(tmp_path))
    cp.save(sample_leads, 12, "google_maps", "gyms")
    state = cp.load("google_maps", "gyms")
    assert state is not None
    leads, page = state
    assert len(leads) == len(sample_leads)
    assert page == 12
    assert leads[0].name == sample_leads[0].name


def test_expired_checkpoint_is_none(tmp_path, sample_lead):
    cp = ScrapingCheckpoint(checkpoint_dir=str(tmp_path), ttl_seconds=0)
    cp.save([sample_lead], 1, "google_maps", "gyms")
    time.sleep(0.05)
    assert cp.load("google_maps", "gyms") is None


def test_clear_removes_checkpoint(tmp_path, sample_lead):
    cp = ScrapingCheckpoint(checkpoint_dir=str(tmp_path))
    cp.save([sample_lead], 1, "google_maps", "gyms")
    cp.clear("google_maps", "gyms")
    assert cp.load("google_maps", "gyms") is None


def test_clear_missing_does_not_raise(tmp_path):
    cp = ScrapingCheckpoint(checkpoint_dir=str(tmp_path))
    cp.clear("google_maps", "never-saved")


def test_age_seconds(tmp_path, sample_lead):
    cp = ScrapingCheckpoint(checkpoint_dir=str(tmp_path))
    assert cp.age_seconds("google_maps", "gyms") is None
    cp.save([sample_lead], 1, "google_maps", "gyms")
    age = cp.age_seconds("google_maps", "gyms")
    assert age is not None and age < 5
