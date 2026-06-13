import pytest

from scrapers.facebook import FacebookScraper
from tests.conftest import FakeFetcher, FakePage

PAGE_HTML = """
<html><head>
<title>Miami Strong Gym | Facebook</title>
<meta property="og:title" content="Miami Strong Gym"/>
<meta property="og:description" content="Gym. 1830 N Bayshore Dr, Miami, FL 33132. 4,512 likes"/>
</head><body>
<a href="tel:+13055046980">Call</a>
<a href="https://l.facebook.com/l.php?u=https%3A%2F%2Fmiamistronggym.com%2F&amp;h=xyz">site</a>
<span>info@miamistronggym.com</span>
</body></html>
"""

LOGIN_HTML = """
<html><head><title>Facebook - log in or sign up</title>
<meta property="og:title" content="Facebook"/></head><body></body></html>
"""


@pytest.fixture
def scraper(monkeypatch):
    monkeypatch.setattr("scrapers.facebook.StealthyFetcher", FakeFetcher)
    monkeypatch.setattr(FacebookScraper, "_random_delay", staticmethod(lambda: None))
    return FacebookScraper()


def test_page_extraction_success(scraper):
    FakeFetcher.configure(pages=[FakePage(PAGE_HTML, url="https://www.facebook.com/gym/"),
                                 FakePage(PAGE_HTML, url="https://www.facebook.com/gym/about")])
    lead = scraper.scrape_page("https://www.facebook.com/gym/")
    assert lead is not None
    assert lead.name == "Miami Strong Gym"
    assert lead.phone == "+13055046980"
    assert lead.email == "info@miamistronggym.com"
    assert lead.website == "https://miamistronggym.com/"
    assert lead.category == "Gym"
    assert "Bayshore" in lead.address


def test_source_field(scraper):
    FakeFetcher.configure(pages=[FakePage(PAGE_HTML, url="https://www.facebook.com/gym/")])
    lead = scraper.scrape_page("https://www.facebook.com/gym/")
    assert lead.source == "facebook"


def test_login_wall_returns_none(scraper):
    FakeFetcher.configure(
        pages=[FakePage(LOGIN_HTML, url="https://www.facebook.com/login.php")]
    )
    assert scraper.scrape_page("https://www.facebook.com/gym/") is None


def test_timeout_returns_none(scraper):
    FakeFetcher.configure(error=TimeoutError("timed out"))
    assert scraper.scrape_page("https://www.facebook.com/gym/") is None


def test_429_returns_none(scraper):
    FakeFetcher.configure(pages=[FakePage("<html></html>", status=429)])
    assert scraper.scrape_page("https://www.facebook.com/gym/") is None


def test_empty_page_returns_none(scraper):
    FakeFetcher.configure(pages=[FakePage("<html><body></body></html>",
                                          url="https://www.facebook.com/gym/")])
    assert scraper.scrape_page("https://www.facebook.com/gym/") is None
