"""Offline tests for the Sprint H replacement scrapers.

Covers the deprecation redirects (guia_oleo -> tripadvisor_ar, doctoralia ->
topdoctors_ar), HTML/JSON-LD parsing of the three new/rewritten scrapers, the
MercadoLibre nickname deduplication and anti-bot handling, and the updated
``--source argentina`` alias. All fetching is mocked via FakeFetcher/FakePage.
"""
import json
import logging

import pytest

from config.settings import settings
from scrapers.doctoralia import DoctoraliaScraper
from scrapers.guia_oleo import GuiaOleoScraper
from scrapers.mercadolibre import MercadoLibreScraper
from scrapers.topdoctors_ar import TopDoctorsARScraper
from scrapers.tripadvisor_ar import TripAdvisorARScraper

from tests.conftest import FakeFetcher, FakePage


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    """Zero out delays and reset runtime settings for each test."""
    monkeypatch.setattr(settings, "SCRAPING_DELAY_MIN", 0.0)
    monkeypatch.setattr(settings, "SCRAPING_DELAY_MAX", 0.0)
    monkeypatch.setattr(settings, "LOCATION", "")
    monkeypatch.setattr(settings, "ML_OFFICIAL_ONLY", False)


# --- TripAdvisor (replaces guia_oleo) -----------------------------------------

def test_tripadvisor_detail_parsing(monkeypatch):
    monkeypatch.setattr("scrapers.tripadvisor_ar.StealthyFetcher", FakeFetcher)
    ld = {
        "@context": "https://schema.org",
        "@type": "FoodEstablishment",
        "name": "La Brigada",
        "telephone": "+54 11 4361-4685",
        "address": {"@type": "PostalAddress", "streetAddress": "Estados Unidos 465",
                    "addressLocality": "Buenos Aires"},
        "servesCuisine": ["Parrilla", "Argentina"],
        "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.5"},
    }
    page = FakePage(
        f'<html><body><script type="application/ld+json">{json.dumps(ld)}</script></body></html>'
    )
    FakeFetcher.configure(pages=[page])
    lead = TripAdvisorARScraper().scrape_restaurant_detail(
        "https://www.tripadvisor.com.ar/Restaurant_Review-g1-d1-Reviews-La_Brigada.html"
    )
    assert lead is not None
    assert lead.name == "La Brigada"
    assert lead.source == "tripadvisor_ar"
    assert lead.phone.startswith("+54")
    assert lead.rating == 4.5
    assert "Buenos Aires" in lead.address


def test_guia_oleo_redirects_to_tripadvisor(monkeypatch, caplog):
    monkeypatch.setattr("scrapers.tripadvisor_ar.StealthyFetcher", FakeFetcher)
    FakeFetcher.configure(pages=[FakePage("<html><body></body></html>")])
    with caplog.at_level(logging.WARNING):
        leads = GuiaOleoScraper().scrape("sushi palermo", limit=5)
    assert leads == []  # delegates without crashing
    assert any("tripadvisor_ar" in r.message.lower() for r in caplog.records)


# --- Top Doctors (replaces doctoralia) ----------------------------------------

def test_topdoctors_detail_parsing(monkeypatch):
    monkeypatch.setattr("scrapers.topdoctors_ar.Fetcher", FakeFetcher)
    page = FakePage(
        '<html><body><h1>Dra. Test Medica</h1>'
        '<span itemprop="address">Ciudad Autónoma de Buenos Aires (CABA) Ver en mapa</span>'
        '<a href="tel:+541168419961">turno</a></body></html>'
    )
    FakeFetcher.configure(pages=[page])
    lead = TopDoctorsARScraper().scrape_doctor_detail(
        "https://www.topdoctors.com.ar/doctor/test-medica/", "Dermatologia"
    )
    assert lead is not None
    assert lead.name == "Dra. Test Medica"
    assert lead.source == "topdoctors_ar"
    assert "CABA" in lead.address
    assert "Ver en mapa" not in lead.address
    # shared booking phone is kept out of Lead.phone (so dedup doesn't merge all)
    assert lead.phone == ""
    assert lead.raw_data.get("booking_phone", "").startswith("+54")


def test_doctoralia_redirects_to_topdoctors(monkeypatch, caplog):
    monkeypatch.setattr("scrapers.topdoctors_ar.Fetcher", FakeFetcher)
    FakeFetcher.configure(pages=[FakePage("<html><body></body></html>")])
    with caplog.at_level(logging.WARNING):
        leads = DoctoraliaScraper().scrape("dentista buenos-aires", limit=5)
    assert leads == []  # delegates without crashing
    assert any("topdoctors_ar" in r.message.lower() for r in caplog.records)


# --- MercadoLibre (rewritten, no API) -----------------------------------------

def test_mercadolibre_profile_parsing(monkeypatch):
    monkeypatch.setattr("scrapers.mercadolibre.StealthyFetcher", FakeFetcher)
    page = FakePage("<html><body><h1>Samsung Store Oficial</h1></body></html>")
    FakeFetcher.configure(pages=[page])
    lead = MercadoLibreScraper().scrape_seller_profile("SAMSUNG", "electronica")
    assert lead is not None
    assert lead.name == "Samsung Store Oficial"
    assert lead.source == "mercadolibre"
    assert lead.category == "electronica"
    assert lead.raw_data.get("nickname") == "SAMSUNG"


def test_mercadolibre_nickname_dedup():
    html = (
        '<a href="/perfil/SAMSUNG">a</a>'
        '<a href="/perfil/SAMSUNG?ref=1">b</a>'
        '<a href="/perfil/XIAOMI_AR">c</a>'
    )
    page = FakePage(f"<html><body>{html}</body></html>")
    nicks = MercadoLibreScraper._collect_seller_nicknames(page)
    assert nicks == ["SAMSUNG", "XIAOMI_AR"]


def test_mercadolibre_antibot_returns_empty(monkeypatch):
    monkeypatch.setattr("scrapers.mercadolibre.StealthyFetcher", FakeFetcher)
    shell = FakePage(
        '<html><body><div class="micro-landing-container">'
        '<script src="snoopy-script.js"></script></div></body></html>'
    )
    FakeFetcher.configure(pages=[shell])
    assert MercadoLibreScraper().search_sellers("electronica", False, 5) == []


# --- Updated argentina alias --------------------------------------------------

def test_argentina_pack_has_replacements():
    import main
    for src in ("tripadvisor_ar", "topdoctors_ar", "mercadolibre"):
        assert src in main.ARGENTINA_PACK
        assert src in main.SCRAPERS
    assert "guia_oleo" not in main.ARGENTINA_PACK
    assert "doctoralia" not in main.ARGENTINA_PACK
    assert len(main.ARGENTINA_PACK) == 9
