"""Tests for the fast-fail abort guard (Sprint N, Fix 1)."""
import time

from utils.abort import AbortMixin, MAX_CONSECUTIVE_ERRORS, MAX_SOURCE_TIMEOUT_SECONDS


class _Guarded(AbortMixin):
    SOURCE = "demo"


def test_should_abort_on_consecutive_errors():
    g = _Guarded()
    g._start_guard()
    assert g._should_abort(consecutive_errors=MAX_CONSECUTIVE_ERRORS - 1, elapsed=0) is False
    assert g._should_abort(consecutive_errors=MAX_CONSECUTIVE_ERRORS, elapsed=0) is True
    assert g.aborted_reason == "blocked"


def test_should_abort_on_timeout():
    g = _Guarded()
    g._start_guard()
    assert g._should_abort(consecutive_errors=0, elapsed=MAX_SOURCE_TIMEOUT_SECONDS + 1) is True
    assert g.aborted_reason == "timeout"


def test_record_fetch_resets_streak():
    g = _Guarded()
    g._start_guard()
    g._record_fetch(False)
    g._record_fetch(False)
    assert g._consecutive_errors == 2
    g._record_fetch(True)
    assert g._consecutive_errors == 0
    assert g._should_abort() is False


def test_tracked_abort_after_three_failures():
    g = _Guarded()
    g._start_guard()
    for _ in range(MAX_CONSECUTIVE_ERRORS):
        g._record_fetch(False)
    assert g._should_abort() is True
    assert g.aborted_reason == "blocked"


def test_guard_elapsed_advances():
    g = _Guarded()
    g._start_guard()
    assert g._guard_elapsed() >= 0.0
    time.sleep(0.01)
    assert g._guard_elapsed() > 0.0


def test_real_scraper_aborts_on_repeated_fetch_failure(monkeypatch):
    """Zonaprop should stop quickly (not loop) when every fetch fails."""
    from scrapers.zonaprop import ZonapropScraper

    scraper = ZonapropScraper()
    monkeypatch.setattr(scraper, "_fetch", lambda url: None)
    monkeypatch.setattr(scraper, "_random_delay", lambda: None)
    leads = scraper.search_agents("inmuebles", "venta", "palermo", limit=20)
    assert leads == []
    assert scraper.aborted_reason == "blocked"
