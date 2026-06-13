from pathlib import Path

import pytest

from config.settings import settings
from scrapers.linkedin import LinkedInScraper

COMPANY_HTML = """
<html><head>
<meta property="og:title" content="Miami Strong Gym | LinkedIn"/>
<meta property="og:description" content="The strongest gym in Miami."/>
</head><body>
<script>{"industry":"Health, Wellness & Fitness","staffCountRange":"11-50",
"callToAction":{"type":"VIEW_WEBSITE","url":"https:\\/\\/miamistronggym.com"}}</script>
</body></html>
"""

PROFILE_HTML = """
<html><head>
<meta property="og:title" content="Jane Doe - Owner at Miami Strong Gym | LinkedIn"/>
<meta property="og:description" content="Owner at Miami Strong Gym · Miami, FL"/>
</head><body></body></html>
"""


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200,
                 url: str = "https://www.linkedin.com/company/gym/"):
        self.text = text
        self.status_code = status_code
        self.url = url


@pytest.fixture
def scraper(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_URL", "")
    monkeypatch.setattr("scrapers.linkedin.AUTHWALL_WAIT_SECONDS", 0)
    instance = LinkedInScraper.__new__(LinkedInScraper)
    instance.proxy_url = ""
    instance.delay_range = (0, 0)
    from utils.rate_limiter import RateLimiter
    import threading
    instance.rate_limiter = RateLimiter(1000)
    instance._semaphore = threading.Semaphore(2)
    return instance


def test_uses_curl_cffi_not_requests():
    source = (Path(__file__).parent.parent / "scrapers" / "linkedin.py").read_text(encoding="utf-8")
    assert "curl_cffi" in source
    assert "import requests\n" not in source
    assert "import httpx" not in source


def test_company_extraction_success(scraper, monkeypatch):
    monkeypatch.setattr(scraper, "_request", lambda url: FakeResponse(COMPANY_HTML))
    lead = scraper.scrape_company("https://www.linkedin.com/company/miamistronggym/")
    assert lead is not None
    assert lead.name == "Miami Strong Gym"
    assert lead.website == "https://miamistronggym.com"
    assert lead.category == "Health, Wellness & Fitness"
    assert lead.source == "linkedin"
    assert lead.raw_data["company_size"] == "11-50"


def test_profile_extraction_success(scraper, monkeypatch):
    monkeypatch.setattr(scraper, "_request",
                        lambda url: FakeResponse(PROFILE_HTML, url="https://www.linkedin.com/in/janedoe/"))
    lead = scraper.scrape_profile("https://www.linkedin.com/in/janedoe/")
    assert lead is not None
    assert lead.name == "Jane Doe"
    assert "Owner" in lead.category
    assert lead.source == "linkedin"


def test_authwall_redirect_returns_none(scraper, monkeypatch):
    monkeypatch.setattr(
        scraper, "_request",
        lambda url: FakeResponse("", status_code=200,
                                 url="https://www.linkedin.com/authwall?trk=x"),
    )
    assert scraper.scrape_company("https://www.linkedin.com/company/gym/") is None


def test_429_retries_then_gives_up(scraper, monkeypatch):
    calls = {"n": 0}

    def rate_limited(url):
        calls["n"] += 1
        return FakeResponse("", status_code=429)

    monkeypatch.setattr(scraper, "_request", rate_limited)
    assert scraper.scrape_company("https://www.linkedin.com/company/gym/") is None
    assert calls["n"] == 2


def test_timeout_returns_none(scraper, monkeypatch):
    def raises(url):
        raise TimeoutError("timed out")

    monkeypatch.setattr(scraper, "_request", raises)
    assert scraper.scrape_company("https://www.linkedin.com/company/gym/") is None


def test_proxy_warning_printed_without_proxy(monkeypatch, capsys):
    monkeypatch.setattr(settings, "PROXY_URL", "")
    LinkedInScraper()
    output = capsys.readouterr().out
    assert "Sin proxy configurado" in output
    assert "PROXY_URL" in output


def test_no_warning_with_proxy(monkeypatch, capsys):
    monkeypatch.setattr(settings, "PROXY_URL", "http://user:pass@proxy.example.io:8080")
    scraper = LinkedInScraper()
    assert "Sin proxy" not in capsys.readouterr().out
    assert scraper.delay_range == (3.0, 6.0)


def test_scrape_dispatches_by_url(scraper, monkeypatch):
    monkeypatch.setattr(scraper, "_request", lambda url: FakeResponse(COMPANY_HTML))
    leads = scraper.scrape("https://www.linkedin.com/company/miamistronggym/", 5)
    assert len(leads) == 1
    assert leads[0].name == "Miami Strong Gym"
