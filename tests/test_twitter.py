import pytest

from scrapers.twitter import (
    TwitterScraper,
    extract_email_from_bio,
    extract_phone_from_bio,
    normalize_username,
)
from tests.conftest import FakeFetcher, FakePage

PROFILE_HTML = """
<html><head>
<title>Miami Strong Gym (@gymmiami) on X</title>
<meta property="og:title" content="Miami Strong Gym (@gymmiami) on X"/>
<meta property="og:description" content="Best gym in Miami 💪 info@gymmiami.com"/>
</head><body>
<script>{"expanded_url":"https:\\/\\/gymmiami.com"}</script>
</body></html>
"""

LOGIN_URL = "https://x.com/i/flow/login"


@pytest.fixture
def scraper(monkeypatch):
    monkeypatch.setattr("scrapers.twitter.StealthyFetcher", FakeFetcher)
    monkeypatch.setattr("scrapers.twitter.RATE_LIMIT_WAIT_SECONDS", 0)
    return TwitterScraper()


async def test_profile_extraction_success(scraper):
    FakeFetcher.configure(pages=[FakePage(PROFILE_HTML, url="https://x.com/gymmiami")])
    lead = await scraper.scrape_profile("gymmiami")
    assert lead is not None
    assert lead.name == "Miami Strong Gym"
    assert lead.email == "info@gymmiami.com"
    assert lead.website == "https://gymmiami.com"
    assert lead.source == "twitter"


async def test_login_wall_returns_none(scraper):
    FakeFetcher.configure(pages=[FakePage("<html></html>", url=LOGIN_URL)])
    assert await scraper.scrape_profile("gymmiami") is None


async def test_timeout_returns_none(scraper):
    FakeFetcher.configure(error=TimeoutError("timed out"))
    assert await scraper.scrape_profile("gymmiami") is None


async def test_429_retries_then_gives_up(scraper):
    FakeFetcher.configure(pages=[FakePage("<html></html>", status=429),
                                 FakePage("<html></html>", status=429)])
    assert await scraper.scrape_profile("gymmiami") is None
    assert FakeFetcher.calls >= 2


async def test_empty_page_returns_none(scraper):
    FakeFetcher.configure(pages=[FakePage("<html><body></body></html>",
                                          url="https://x.com/gymmiami")])
    assert await scraper.scrape_profile("gymmiami") is None


@pytest.mark.parametrize("raw,expected", [
    ("@gymmiami", "gymmiami"),
    ("gymmiami", "gymmiami"),
    ("  @GymMiami  ", "GymMiami"),
    ("@@user", "user"),
])
def test_normalize_username(raw, expected):
    assert normalize_username(raw) == expected


def test_extract_email_from_bio():
    bio = "Best gym in Miami 💪 Book now: info@gymmiami.com | Open 24/7"
    assert extract_email_from_bio(bio) == "info@gymmiami.com"


def test_extract_email_from_bio_skips_invalid():
    assert extract_email_from_bio("contact: noreply@gymmiami.com") == ""
    assert extract_email_from_bio("no emails here") == ""
    assert extract_email_from_bio("") == ""


def test_extract_phone_from_bio():
    bio = "Call us: (305) 504-6980 — Miami's #1 gym"
    assert extract_phone_from_bio(bio) == "+13055046980"


def test_extract_phone_from_bio_no_match():
    assert extract_phone_from_bio("just a bio with year 2024") == ""


def test_clean_display_name():
    assert TwitterScraper._clean_display_name("NASA (@NASA) on X") == "NASA"
    assert TwitterScraper._clean_display_name("Gym Miami (@gymmiami) / X") == "Gym Miami"
