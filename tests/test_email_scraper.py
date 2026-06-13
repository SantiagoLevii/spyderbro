import pytest

from scrapers.email_scraper import EmailScraper
from tests.conftest import FakeFetcher, FakePage

MAILTO_HTML = """
<html><body>
<a href="mailto:info@miamistronggym.com?subject=hi">Email us</a>
</body></html>
"""

TEXT_EMAIL_HTML = """
<html><body><p>Contact: ventas@gymmiami.com for plans. Logo: logo@2x.png</p></body></html>
"""

NO_EMAIL_HTML = "<html><body><p>No contact info here</p></body></html>"


@pytest.fixture
def scraper(monkeypatch):
    monkeypatch.setattr("scrapers.email_scraper.Fetcher", FakeFetcher)
    return EmailScraper()


def test_mailto_extraction(scraper, mock_html_response):
    FakeFetcher.configure(pages=[FakePage(mock_html_response)])
    assert scraper.extract_from_website("https://miamistronggym.com") == "info@miamistronggym.com"


def test_text_email_extraction_skips_assets(scraper):
    FakeFetcher.configure(pages=[FakePage(TEXT_EMAIL_HTML)])
    assert scraper.extract_from_website("https://gymmiami.com") == "ventas@gymmiami.com"


def test_no_email_anywhere(scraper):
    FakeFetcher.configure(pages=[FakePage(NO_EMAIL_HTML)])
    assert scraper.extract_from_website("https://gymmiami.com") == ""


def test_social_url_skipped_without_fetching(scraper):
    FakeFetcher.configure(pages=[FakePage(MAILTO_HTML)])
    assert scraper.extract_from_website("https://www.facebook.com/gym") == ""
    assert FakeFetcher.calls == 0


def test_dead_site_returns_empty(scraper):
    FakeFetcher.configure(error=ConnectionError("could not resolve host"))
    assert scraper.extract_from_website("https://dead-site-zzz.com") == ""


def test_non_200_returns_empty(scraper):
    FakeFetcher.configure(pages=[FakePage(MAILTO_HTML, status=429)])
    assert scraper.extract_from_website("https://gymmiami.com") == ""


def test_redirect_to_social_ignored(scraper):
    FakeFetcher.configure(
        pages=[FakePage(MAILTO_HTML, url="https://www.instagram.com/gymmiami/")]
    )
    assert scraper.extract_from_website("https://gymmiami.com") == ""
