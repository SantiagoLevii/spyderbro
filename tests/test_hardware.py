"""Tests for hardware detection and worker sizing (Sprint M)."""
import psutil

from config.settings import settings
from scrapers.google_maps import GoogleMapsScraper
from utils.hardware import detect_hardware


def test_detect_hardware_returns_profile():
    p = detect_hardware()
    assert p.cpu_count_logical > 0
    assert p.ram_total_gb > 0
    assert p.recommended_workers > 0
    assert p.max_workers > 0


def test_recommended_workers_within_bounds():
    p = detect_hardware()
    assert 4 <= p.recommended_workers <= 64


def test_max_workers_is_cpu_x8():
    p = detect_hardware()
    logical = psutil.cpu_count(logical=True) or 1
    assert p.max_workers == min(64, logical * 8)


def test_manual_workers_overrides_auto(monkeypatch):
    monkeypatch.setattr(settings, "WORKERS", 10)
    assert GoogleMapsScraper._optimal_workers() == 10
    monkeypatch.setattr(settings, "WORKERS", 100)  # capped at 64
    assert GoogleMapsScraper._optimal_workers() == 64
