import pytest

from models.lead import Lead
from scrapers.google_maps import GoogleMapsScraper


def test_lead_to_dict_fields():
    lead = Lead(name="Test Gym", category="gym", rating=4.5, source="google_maps")
    d = lead.to_dict()
    assert d["name"] == "Test Gym"
    assert d["category"] == "gym"
    assert d["rating"] == 4.5
    assert d["source"] == "google_maps"


def test_lead_defaults():
    lead = Lead(name="Minimal")
    assert lead.email == ""
    assert lead.phone == ""
    assert lead.website == ""
    assert lead.raw_data == {}


def test_scraper_instantiates():
    scraper = GoogleMapsScraper()
    assert scraper is not None


ITEM_HTML = """
<html><body>
<div role="article">
  <a href="https://www.google.com/maps/place/Miami+Strong+Gym/data=xyz">link</a>
  <div class="qBF1Pd">Miami Strong Gym</div>
  <span class="MW4etd">4,6</span>
  <div class="W4Efsd">rating block</div>
  <div class="W4Efsd">combined row</div>
  <div class="W4Efsd"><span>Gimnasio</span><span>·</span><span>1830 N Bayshore Dr</span></div>
</div>
</body></html>
"""

DETAIL_HTML = """
<html><body>
<button data-item-id="phone:tel:+13055046980">Call</button>
<a data-item-id="authority" href="https://miamistronggym.com">Website</a>
</body></html>
"""


def _page(html: str):
    from tests.conftest import FakePage
    return FakePage(html)


def test_parse_item_extracts_fields():
    item = _page(ITEM_HTML).find('[role="article"]')
    lead = GoogleMapsScraper()._parse_item(item)
    assert lead is not None
    assert lead.name == "Miami Strong Gym"
    assert lead.rating == 4.6
    assert lead.category == "Gimnasio"
    assert lead.address == "1830 N Bayshore Dr"
    assert lead.source == "google_maps"


def test_get_detail_url():
    item = _page(ITEM_HTML).find('[role="article"]')
    url = GoogleMapsScraper._get_detail_url(item)
    assert "google.com/maps/place" in url


def test_extract_phone_from_detail():
    assert GoogleMapsScraper._extract_phone(_page(DETAIL_HTML)) == "+13055046980"


def test_extract_website_from_detail():
    assert GoogleMapsScraper._extract_website(_page(DETAIL_HTML)) == "https://miamistronggym.com"


def test_extract_phone_missing():
    assert GoogleMapsScraper._extract_phone(_page("<html></html>")) == ""


def test_parse_item_without_name_returns_none():
    item = _page('<html><div role="article"><span>no name</span></div></html>').find('[role="article"]')
    assert GoogleMapsScraper()._parse_item(item) is None
