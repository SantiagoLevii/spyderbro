from config.settings import settings
from scrapers.dorks import DorksScraper

DDG_HTML_FIXTURE = """
<div class="result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.rzonefitness.com%2Fcontact-us%2F&rut=abc">
    Contact Us | Best <b>Gyms in Miami</b>, FL
  </a>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.rzonefitness.com%2F">
    Call us at (305) 555-1234 or email <b>info@rzonefitness.com</b>
  </a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://directsite.com/gym">Direct Gym Site</a>
</div>
"""

SERPER_JSON_FIXTURE = {
    "organic": [
        {
            "title": "Miami Strong Gym - Home",
            "link": "https://miamistronggym.com",
            "snippet": "The best gym in Miami. Call +1 305-504-6980",
        },
        {
            "title": "Gym Directory",
            "link": "https://gymdir.com/miami",
            "snippet": "Find gyms near you",
        },
    ]
}


def test_generate_dork_queries():
    scraper = DorksScraper.__new__(DorksScraper)
    dorks = scraper._generate_dork_queries("gyms in Miami")
    assert len(dorks) == 5
    assert len(set(dorks)) == 5
    assert any("inurl:contact" in d for d in dorks)
    assert any("site:yelp.com" in d for d in dorks)
    assert any("@gmail.com" in d for d in dorks)


def test_detect_engine_without_key(monkeypatch):
    monkeypatch.setattr(settings, "SERPER_API_KEY", "")
    scraper = DorksScraper()
    assert scraper.engine == "duckduckgo"
    assert scraper.engine_label == "DuckDuckGo"


def test_detect_engine_with_key(monkeypatch):
    monkeypatch.setattr(settings, "SERPER_API_KEY", "test-key-123")
    scraper = DorksScraper()
    assert scraper.engine == "serper"
    assert scraper.engine_label == "Serper.dev (Google)"


def test_parse_duckduckgo_html():
    results = DorksScraper.parse_duckduckgo_html(DDG_HTML_FIXTURE)
    assert len(results) == 2

    first = results[0]
    assert first["url"] == "https://www.rzonefitness.com/contact-us/"
    assert "Contact Us" in first["title"]
    assert "Gyms in Miami" in first["title"]
    assert "(305) 555-1234" in first["description"]

    assert results[1]["url"] == "https://directsite.com/gym"
    assert results[1]["title"] == "Direct Gym Site"


def test_parse_serper_json():
    results = DorksScraper._parse_serper_json(SERPER_JSON_FIXTURE)
    assert len(results) == 2
    assert results[0]["title"] == "Miami Strong Gym - Home"
    assert results[0]["url"] == "https://miamistronggym.com"
    assert "305-504-6980" in results[0]["description"]


def test_parse_serper_json_empty():
    assert DorksScraper._parse_serper_json({}) == []


def test_name_from_title():
    assert DorksScraper._name_from_title("Miami Strong Gym - Home | Official") == "Miami Strong Gym"
    assert DorksScraper._name_from_title("Contact Us | RZone Fitness") == "Contact Us"


def test_parse_duckduckgo_empty_html():
    assert DorksScraper.parse_duckduckgo_html("<html><body></body></html>") == []


async def test_serper_429_retries_then_gives_up(monkeypatch):
    monkeypatch.setattr(settings, "SERPER_API_KEY", "test-key-123")
    monkeypatch.setattr("scrapers.dorks.RATE_LIMIT_WAIT_SECONDS", 0)
    scraper = DorksScraper()
    calls = {"n": 0}

    async def always_429(dork_query, limit):
        calls["n"] += 1
        return 429, {}

    monkeypatch.setattr(scraper, "_serper_request", always_429)
    assert await scraper._search_serper("query", 5) == []
    assert calls["n"] == 2


async def test_build_leads_source_field(monkeypatch):
    monkeypatch.setattr(settings, "SERPER_API_KEY", "")
    scraper = DorksScraper()
    monkeypatch.setattr(scraper.email_scraper, "extract_from_website", lambda url: "info@gyma.com")
    leads = await scraper._build_leads([
        {"title": "Gym A - Home", "url": "https://gyma.com", "description": "Call (305) 504-6980"},
    ])
    assert len(leads) == 1
    assert leads[0].source == "dorks_duckduckgo"
    assert leads[0].email == "info@gyma.com"
    assert leads[0].phone == "+13055046980"
