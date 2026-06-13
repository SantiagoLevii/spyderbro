import pytest
from scrapling.parser import Selector

from models.lead import Lead


class FakePage:
    """Page-like object backed by Scrapling's Selector for offline tests.

    Mimics the interface scrapers use from fetcher responses: find,
    find_all, html_content, status, and url.
    """

    def __init__(self, html: str, status: int = 200, url: str = "https://example-biz.io") -> None:
        self._selector = Selector(content=html or "<html></html>")
        self.html_content = html
        self.status = status
        self.url = url

    def find(self, *args, **kwargs):
        return self._selector.find(*args, **kwargs)

    def find_all(self, *args, **kwargs):
        return self._selector.find_all(*args, **kwargs)

    def css(self, *args, **kwargs):
        return self._selector.css(*args, **kwargs)


class FakeFetcher:
    """Drop-in replacement for Scrapling fetchers in tests.

    Configure with a FakePage, an exception to raise, or a list of pages
    returned in sequence (for retry scenarios).
    """

    pages: list = []
    error: Exception | None = None
    calls: int = 0

    @classmethod
    def configure(cls, pages: list | None = None, error: Exception | None = None) -> None:
        cls.pages = list(pages or [])
        cls.error = error
        cls.calls = 0

    @classmethod
    def _next(cls):
        cls.calls += 1
        if cls.error is not None:
            raise cls.error
        if not cls.pages:
            raise AssertionError("FakeFetcher not configured with pages")
        return cls.pages.pop(0) if len(cls.pages) > 1 else cls.pages[0]

    @classmethod
    def fetch(cls, url, **kwargs):
        return cls._next()

    @classmethod
    def get(cls, url, **kwargs):
        return cls._next()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    """Remove retry/backoff sleeps so tests run fast."""
    monkeypatch.setattr("utils.retry.time.sleep", lambda s: None)


@pytest.fixture
def sample_lead() -> Lead:
    """Lead de prueba con todos los campos completos."""
    return Lead(
        name="Miami Strong Gym",
        email="info@miamistronggym.com",
        phone="+13055046980",
        website="https://miamistronggym.com",
        address="1830 N Bayshore Dr, Miami",
        category="Gimnasio",
        rating=4.8,
        source="google_maps",
    )


@pytest.fixture
def sample_leads() -> list[Lead]:
    """Lista de 5 leads de prueba con variación de campos."""
    return [
        Lead(name="Gym A", phone="+13055046980", website="https://gyma.com",
             email="a@gyma.com", rating=4.5, source="google_maps"),
        Lead(name="Gym B", phone="+13055046981", source="google_maps"),
        Lead(name="Gym C", email="c@gymc.com", source="instagram"),
        Lead(name="Gym D", website="https://gymd.com", rating=3.9, source="facebook"),
        Lead(name="Gym E", source="twitter"),
    ]


@pytest.fixture
def mock_html_response() -> str:
    """HTML mock de una página de negocio con email y teléfono."""
    return """
    <html>
      <head><title>Miami Strong Gym | Best Gym in Miami</title></head>
      <body>
        <h1>Miami Strong Gym</h1>
        <p>Call us at (305) 504-6980 or write to
           <a href="mailto:info@miamistronggym.com">info@miamistronggym.com</a></p>
        <a href="tel:+13055046980">Call now</a>
      </body>
    </html>
    """
