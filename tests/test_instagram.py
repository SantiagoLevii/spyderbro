import pytest

from scrapers.instagram import InstagramScraper
from tests.conftest import FakeFetcher, FakePage

PROFILE_HTML = """
<html><head><title>Miami Strong Gym (@gymmiami)</title></head>
<body><script>
{"full_name":"Miami Strong Gym","biography":"Best gym in Miami 💪 info@gymmiami.com (305) 504-6980",
 "external_url":"https://gymmiami.com","category_name":"Gym","is_private":false}
</script></body></html>
"""

PRIVATE_HTML = '<html><body><script>{"full_name":"Hidden","is_private":true}</script></body></html>'

LOGIN_HTML = "<html><head><title>Login • Instagram</title></head><body></body></html>"


@pytest.fixture
def scraper(monkeypatch):
    monkeypatch.setattr("scrapers.instagram.StealthyFetcher", FakeFetcher)
    monkeypatch.setattr("scrapers.instagram.RATE_LIMIT_WAIT_SECONDS", 0)
    return InstagramScraper()


def test_profile_extraction_success(scraper):
    FakeFetcher.configure(pages=[FakePage(PROFILE_HTML, url="https://www.instagram.com/gymmiami/")])
    lead = scraper.scrape_profile_bio("gymmiami")
    assert lead is not None
    assert lead.name == "Miami Strong Gym"
    assert lead.email == "info@gymmiami.com"
    assert lead.phone == "+13055046980"
    assert lead.website == "https://gymmiami.com"
    assert lead.category == "Gym"


def test_source_field(scraper):
    FakeFetcher.configure(pages=[FakePage(PROFILE_HTML, url="https://www.instagram.com/gymmiami/")])
    lead = scraper.scrape_profile_bio("gymmiami")
    assert lead.source == "instagram"


def test_private_profile_skipped(scraper):
    FakeFetcher.configure(pages=[FakePage(PRIVATE_HTML, url="https://www.instagram.com/x/")])
    assert scraper.scrape_profile_bio("x") is None


def test_login_wall_returns_none(scraper):
    FakeFetcher.configure(
        pages=[FakePage(LOGIN_HTML, url="https://www.instagram.com/accounts/login/")]
    )
    assert scraper.scrape_profile_bio("x") is None


def test_timeout_returns_none(scraper):
    FakeFetcher.configure(error=TimeoutError("page load timed out"))
    assert scraper.scrape_profile_bio("x") is None


def test_429_retries_then_gives_up(scraper, monkeypatch):
    monkeypatch.setattr("scrapers.instagram.time.sleep", lambda s: None)
    FakeFetcher.configure(pages=[FakePage("<html></html>", status=429),
                                 FakePage("<html></html>", status=429)])
    assert scraper.scrape_profile_bio("x") is None
    assert FakeFetcher.calls >= 2


def test_empty_page_returns_none(scraper):
    FakeFetcher.configure(pages=[FakePage("<html><body></body></html>",
                                          url="https://www.instagram.com/x/")])
    assert scraper.scrape_profile_bio("x") is None
