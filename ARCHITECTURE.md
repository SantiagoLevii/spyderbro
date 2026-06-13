# ScrapBro — Architecture & Project Reference

## What is ScrapBro

ScrapBro (formerly LeadFlow) is a multi-source B2B lead scraper CLI with 15 active sources: 6 global (Google Maps, Instagram, Facebook, Twitter/X, Google Dorks, LinkedIn) plus the 9-source Argentina pack (paginas_amarillas, dateas, zonaprop, argenprop, guia_oleo, doctoralia, mercadolibre, clutch, abogados). It extracts business name, email, phone, website, address, category, and rating. Emails are scraped from each business website and validated; phones are normalized to E.164. Within a source, detail fetching and email scraping run in parallel (ThreadPoolExecutor); multi-source runs execute scrapers concurrently (asyncio) and deduplicate automatically. Results are cached for 24h, and interrupted sessions can resume from checkpoints. Output is a styled Excel `.xlsx` (via `--output csv`) or JSON. It is the foundation of a future SaaS product; for now it runs fully local via CLI.

## How to run

```bash
# Activate venv (Windows)
.\venv\Scripts\activate

# Basic usage
python main.py --source google_maps --query "gyms in Miami" --limit 10 --output csv

# Multi-source (concurrent scrape via AsyncScrapingPipeline + automatic deduplication)
python main.py --source google_maps,dorks,twitter --query "gyms in Miami" --limit 30 --output csv

# Instagram query forms
python main.py --source instagram --query "miamistronggym"  # single profile
python main.py --source instagram --query "@gymmiami"       # followers (login-walled: returns empty)
python main.py --source instagram --query "#gymmiami"       # hashtag (login-walled: returns empty)

# Twitter/X query forms
python main.py --source twitter --query "@gymmiami"         # single profile (works anonymously)
python main.py --source twitter --query "fitness miami"     # keyword search (login-walled: returns empty)

# Google Dorks (DuckDuckGo free, or Serper.dev API if SERPER_API_KEY is in .env)
python main.py --source dorks --query "gyms in Miami" --limit 10 --output csv

# Argentina pack (9 AR-focused sources). --location and --dateas-type are AR-pack args.
python main.py --source paginas_amarillas --query "restaurantes" --location "buenos-aires" --limit 50 --output csv
python main.py --source dateas --query "contador" --location "Rosario" --dateas-type personas --limit 30 --output csv
python main.py --source zonaprop --query "venta capital-federal" --limit 50 --output csv   # query = "{operacion} {zona}"
python main.py --source guia_oleo --query "sushi palermo" --limit 30 --output csv           # query = "{rubro} {zona}"
python main.py --source argentina --query "inmobiliarias" --location "buenos-aires" --limit 100 --output csv  # alias = all 9 AR sources

# Arguments
--source              Comma-separated. Global: google_maps | instagram | facebook | twitter | dorks | linkedin.
                      Argentina: paginas_amarillas | dateas | zonaprop | argenprop | guia_oleo | doctoralia |
                      mercadolibre | clutch | abogados. Alias: argentina (= all 9 AR sources). (default: google_maps)
--query               Search string (min 2 chars)
--location            Province/city for AR sources that need it (paginas_amarillas, dateas, guia_oleo, ...).
                      Ignored silently by sources that don't use it.
--dateas-type         dateas only: empresas (default) | personas | ambos
--limit               Max leads to extract, 1-1000                              (default: 50)
--output              csv (-> styled .xlsx) | json                             (default: csv)
--filter-complete     Only leads with phone AND website AND email
--filter-has-phone    Only leads with phone
--filter-has-email    Only leads with email
--filter-has-website  Only leads with website
--filter-min-rating   Only leads with rating >= value (e.g. 4.0)
--no-cache            Ignore cache and force re-scraping
--clear-cache         Delete all cached results and exit
--no-resume           Ignore checkpoints and always start from scratch
```

For sources whose query embeds the location (zonaprop, argenprop, guia_oleo, doctoralia, abogados, clutch), the
first token is the primary term and the rest is the location; an explicit `--location` overrides that parsing.
`--location`/`--dateas-type` are stored on the `settings` singleton at runtime so both the single-source path and
the async pipeline read them without changing the `scrape(query, limit)` signature; the pipeline is reused unchanged
by injecting `main.SCRAPERS` as its registry.

Filters apply after scraping, before export. The final summary shows how many leads were discarded.

## Cache

- Results are cached as JSON in `.cache/{source}_{md5(query)}.json` (gitignored) with a 24h TTL.
- A fresh cache hit skips scraping and prints a `[CACHE]` notice with the entry age.
- `--no-cache` forces re-scraping; `--clear-cache` wipes the cache and exits.
- `ScrapingCache.clear(source)` can clear a single source programmatically.

## Concurrency

Two levels of parallelism:
- **Within a source:** `google_maps` parses all list items first, then fetches every detail page (phone + website) concurrently via `ThreadPoolExecutor`; emails for all sites are scraped in a second parallel batch (`EmailScraper.extract_batch`). `dorks` runs its 5 generated queries with `asyncio.gather` and builds leads concurrently. Worker count is `utils/concurrency.get_optimal_workers()` = `min(32, cpu_count*4)`. A `threading.Semaphore` caps simultaneous browser instances and the per-source `RateLimiter` paces request starts, so parallelism never bypasses rate limits.
- **Across sources:** with multiple `--source` values, `pipeline/async_pipeline.py` (`AsyncScrapingPipeline`) runs all sources concurrently with `asyncio.gather` (max 3 via semaphore). Native-async scrapers (twitter, dorks) run on the loop; sync Scrapling scrapers run in threads (`asyncio.to_thread`).

The combined pool is deduplicated at the end. The summary shows workers used (single-source), parallel scraper count (multi-source), and estimated speedup vs sequential (sum of fetch wall-times / actual wall time). Resume prompts are disabled in parallel mode.

## Logging

`main.configure_logging()` runs as the first line of `main.py`, before scraper imports. Dual handlers on root: a `FileHandler` -> `scraping.log` at DEBUG (full detail, gitignored) and a `StreamHandler` -> terminal at WARNING only. All noisy third-party loggers (scrapling, playwright, asyncio, httpx, httpcore, urllib3, curl_cffi, aiohttp, ...) are forced to WARNING and re-silenced after imports (Scrapling installs its own handler at import time). The terminal therefore shows only banner + tqdm progress + per-lead lines + summary + real warnings/errors.

## Excel export

`exporters/csv_exporter.py` (still named `CSVExporter`) now writes a styled `.xlsx` via openpyxl — `--output csv` produces `.xlsx` because a formatted sheet is more useful than flat CSV. Sheet `ScrapBro Leads`: dark-blue (`#1F3864`) bold white headers, frozen at A2, auto-filter on row 1, alternating row fills (`#F2F2F2` odd), fixed column widths (Name 30, Email 30, Phone 18, Website 35, Address 40, Category 20, Rating 10, Source 15), grey outer border, and a merged dark-blue totals row (`Total: N leads | Con email: N (X%) | ...`). `--output json` is unchanged.

## Checkpoints (resumable mode)

`utils/checkpoint.py` (`ScrapingCheckpoint`) saves progress to `.checkpoints/{source}_{md5(query)}.json` (gitignored, 2h TTL). google_maps saves every 10 leads and on Ctrl+C; dorks saves after each dork query. On the next run with the same source+query, the CLI asks whether to resume ([S/n]). `--no-resume` skips the prompt and starts fresh. Checkpoints are cleared on successful completion. Instagram/Facebook/Twitter don't checkpoint (their batch paths are login-walled).

## Google Dorks engine

`scrapers/dorks.py` generates 5 dork variants per query (e.g. `"X" "contact" email`, `inurl:contact`, `site:yelp.com ...`) and never hardcodes the engine:
- `SERPER_API_KEY` set in .env → Serper.dev API (POST https://google.serper.dev/search, 2500 free queries at https://serper.dev)
- No key → DuckDuckGo HTML endpoint (html.duckduckgo.com) with conservative 8-15s delays
Each result URL is enriched with EmailScraper; `source` is `dorks_serper` or `dorks_duckduckgo`.

## Environment variables (.env)

`SERPER_API_KEY` (Dorks via Google), `PROXY_URL` (residential proxy for LinkedIn/Instagram/Facebook), `APIFY_TOKEN` (optional, future), `SCRAPING_DELAY_MIN/MAX`, `DEFAULT_LIMIT`, `OUTPUT_DIR`. `validate_settings()` runs at CLI startup: warns about unset optionals, never crashes for them.

## Security practices

- No hardcoded secrets — everything via `.env` (gitignored, `.env.*` too), read only in `config/settings.py`
- API keys are sent only as request headers and never logged (enforced by `tests/test_security.py`)
- CLI input validation: query >= 2 chars, 1 <= limit <= 1000, whitelisted sources; invalid input → red error + exit 1
- Explicit timeouts on every request; per-scraper rate limiters (`utils/rate_limiter.py`); exponential-backoff retries (`utils/retry.py`)
- All exception handlers log with context — no silent failures

## Running tests

```bash
python -m pytest tests/ -v --cov=. --cov-report=term-missing
```

Tests are offline (fetchers mocked via `tests/conftest.py` FakeFetcher/FakePage backed by Scrapling's Selector). pytest-asyncio runs async tests (`asyncio_mode = auto` in pytest.ini).

## Deduplication

`pipeline/deduplicator.py` runs automatically before export, on every run. Match priority:
1. Same E.164 phone
2. Same website domain (ignores www/scheme)
3. Same email (case-insensitive)
4. Fuzzy name >= 85% (rapidfuzz token_sort_ratio) AND same city (last comma segment of address)

Duplicates merge into the most complete lead; empty fields are filled from the duplicate and `raw_data["merged_from"]` lists the contributing sources. The summary shows removed/merged counts.

Output files land in `output/` with pattern `{source}_{query}{ext}`.

Copy `.env.example` to `.env` to override defaults:

```bash
cp .env.example .env
```

## Project structure

```
leadflow-scraper/
├── scrapers/            # One module per data source
│   ├── google_maps.py   # IMPLEMENTED — DynamicFetcher, scroll, PARALLEL detail fetch + email enrichment
│   ├── email_scraper.py # IMPLEMENTED — EmailScraper: mailto + regex; extract_batch() runs in parallel
│   ├── instagram.py     # IMPLEMENTED — StealthyFetcher, public profiles only (followers/hashtag login-walled)
│   ├── facebook.py      # IMPLEMENTED — StealthyFetcher, public pages + About (search login-walled)
│   ├── twitter.py       # IMPLEMENTED — async, public profiles (search login-walled), batch with Semaphore(3)
│   ├── dorks.py         # IMPLEMENTED — async, Serper.dev API or DuckDuckGo, 5 auto-generated dork variants
│   ├── linkedin.py      # IMPLEMENTED — curl_cffi TLS impersonation, public pages (authwall-aware)
│   ├── paginas_amarillas.py # IMPLEMENTED — Fetcher, parses Next.js __NEXT_DATA__ JSON (real leads), 20/min
│   ├── dateas.py        # IMPLEMENTED — Fetcher, empresas/personas/ambos, CUIT in raw_data, 15/min
│   ├── zonaprop.py      # IMPLEMENTED — StealthyFetcher, agents not properties, dedup by name, 12/min
│   ├── argenprop.py     # IMPLEMENTED — StealthyFetcher, same agent logic as zonaprop, 12/min
│   ├── guia_oleo.py     # IMPLEMENTED — Fetcher, restaurants by cuisine+zone, 20/min; exports split_query()
│   ├── doctoralia.py    # IMPLEMENTED — StealthyFetcher, health pros by specialty+city, 10/min
│   ├── mercadolibre.py  # IMPLEMENTED — StealthyFetcher, official-store sellers, dedup by store, 15/min
│   ├── clutch.py        # IMPLEMENTED — Fetcher, digital agencies by service+country, 15/min
│   └── abogados.py      # IMPLEMENTED — Fetcher, lawyers by specialty+province, 20/min
├── pipeline/
│   ├── deduplicator.py  # Deduplicator: phone/domain/email/fuzzy-name matching + field merge
│   └── async_pipeline.py# AsyncScrapingPipeline: concurrent sources, semaphore, dedup, speedup stats
├── exporters/
│   ├── csv_exporter.py  # CSVExporter -> styled .xlsx (openpyxl), despite the name
│   └── json_exporter.py # JSONExporter.export(leads, filename) -> str
├── models/
│   └── lead.py          # Lead dataclass with to_dict()
├── utils/
│   ├── terminal.py      # Banner, tqdm progress bar, per-lead result lines, session summary, cache/source notices
│   ├── validators.py    # is_valid_email() and normalize_phone() (E.164 via phonenumbers)
│   ├── cache.py         # ScrapingCache: 24h TTL JSON cache in .cache/
│   ├── checkpoint.py    # ScrapingCheckpoint: resumable sessions, 2h TTL, .checkpoints/
│   ├── rate_limiter.py  # RateLimiter: sliding-window per-scraper limits (sync + async)
│   ├── retry.py         # sync_retry / async_retry with exponential backoff + jitter
│   ├── concurrency.py   # get_optimal_workers() = min(32, cpu_count*4)
│   └── file_utils.py    # sanitize_filename, ensure_dir (pathlib)
├── config/
│   └── settings.py      # Env-backed settings (delay, limit, output dir)
├── tests/               # 143 tests, offline (conftest FakeFetcher/FakePage), pytest-asyncio
│   ├── conftest.py      # fixtures: sample_lead(s), mock_html_response, FakeFetcher/FakePage
│   ├── test_google_maps.py / test_instagram.py / test_facebook.py / test_twitter.py
│   ├── test_dorks.py / test_linkedin.py / test_email_scraper.py
│   ├── test_deduplicator.py / test_validators.py / test_async_pipeline.py
│   ├── test_cache.py / test_checkpoint.py / test_retry.py / test_rate_limiter.py
│   ├── test_exporters.py / test_file_utils.py / test_security.py
├── main.py              # CLI entry: configure_logging() first, validation, orchestration
├── .env.example / requirements.txt / pytest.ini / README.md
├── scraping.log         # DEBUG log (gitignored)
└── output/              # Generated .xlsx / .json (gitignored)
```

## Development status

| Component | Status |
|---|---|
| `models/lead.py` | Done |
| `config/settings.py` | Done |
| `scrapers/google_maps.py` | Done — PARALLEL detail fetch + email enrichment (ThreadPoolExecutor); selector tuning if Google changes DOM |
| `scrapers/email_scraper.py` | Done — home + up to 2 contact pages, 8s timeout, skips social URLs, `extract_batch()` parallel |
| `utils/terminal.py` | Done — banner, ScrapeProgress (tqdm), SessionStats, print_summary, workers/speedup lines |
| `utils/validators.py` | Done — is_valid_email (format + trap/example rules), normalize_phone (phonenumbers) |
| `exporters/csv_exporter.py` | Done — writes styled `.xlsx` (openpyxl) |
| `exporters/json_exporter.py` | Done |
| `main.py` CLI | Done — configure_logging, input validation, async multi-source, cache/resume flags, filters, perf summary |
| `scrapers/instagram.py` | Done — public profile bios work; followers/hashtags hit Instagram's login wall and return empty |
| `scrapers/facebook.py` | Done — public pages work; search is login-walled and returns empty |
| `scrapers/twitter.py` | Done — async; public profiles work; keyword search is login-walled and returns empty |
| `scrapers/dorks.py` | Done — async; Serper.dev or DuckDuckGo auto-detected; 5 dorks via asyncio.gather; EmailScraper enrichment |
| `scrapers/linkedin.py` | Done — curl_cffi TLS impersonation, public pages; anonymous search authwall-limited |
| `scrapers/paginas_amarillas.py` | Done — parses live Next.js `__NEXT_DATA__` JSON (returns real leads with phone+website) |
| `scrapers/dateas.py` | **LIVE (Sprint G)** — parses the real results `<table>` (rows linking to `/es/empresa/`/`/es/persona/`): name + CUIT + province/locality. No phone/web (CUIT registry, contact paywalled). Province filtered client-side (the `provincia` query param breaks the search). |
| `scrapers/abogados.py` | **LIVE (Sprint G)** — PHP/jQuery directory, NOT Next.js. 2-step: specialty `/area/{slug}/{id}` → firm detail `/directorio/{slug}/{id}` (h1 name, `<address>`, `tel:`, website). 5/5 real leads in test, 100% phone+web. |
| `scrapers/zonaprop.py` / `argenprop.py` | **LIVE (Sprint G)** — StealthyFetcher (Cloudflare). Agency name is hidden on listing cards, so 2-step: listing → per-posting detail, where the agency comes from the `/inmobiliarias/...` link (name) and zonaprop's embedded `"telephone"` JSON (phone). Dedup by name. 4/4 real leads each in test. |
| `scrapers/clutch.py` | **Scraper LIVE, AR has no data (Sprint G)** — StealthyFetcher (Cloudflare). Parses `.provider` cards (name, website from `r.clutch.co/redirect?u=`, location, rating). Works for global/US service rankings (6/6 leads) but Clutch removed per-country directory URLs (`/argentina` → 404) and the geo param no longer filters, so `country=argentina` yields 0. |
| `scrapers/doctoralia.py` | **BLOCKED (Sprint G)** — `doctoralia.com.ar` drops TCP connections from datacenter IPs (geo/IP block); unreachable without a residential AR proxy. Timeout raised + `PROXY_URL` support added; parser unverified. |
| `scrapers/mercadolibre.py` | **BLOCKED (Sprint G)** — "snoopy" anti-bot serves a micro-landing shell (HTTP 200, no listing) and the public API now returns 403 (OAuth). Shell detection + `PROXY_URL` support added; returns empty with a clear warning. |
| `scrapers/guia_oleo.py` | **DISCONTINUED SOURCE (Sprint G)** — `guiaoleo.com.ar` is no longer the AR restaurant directory; it is now an SEO content blog (WordPress) with no business listings. Scraper left as graceful-empty; needs a replacement source. |
| `pipeline/deduplicator.py` | Done — cross-source dedup + merge |
| `pipeline/async_pipeline.py` | Done — concurrent sources with semaphore + speedup stats; registry injected from `main.SCRAPERS` |
| `utils/cache.py` | Done — 24h TTL JSON cache |
| `utils/checkpoint.py` | Done — resumable sessions (google_maps, dorks) |
| Tests | 180/180 passing (`tests/test_argentina_pack.py` restructured in Sprint G: single-page scrapers in the parametrized suite, two-step scrapers — abogados/zonaprop/argenprop — and Clutch in dedicated listing→detail tests) |

## Terminal UI

- Cyan banner on startup (`print_banner`).
- tqdm progress bar during scraping: green normal, yellow when a lead lacks phone/email, red on errors.
- One line per lead: `[✓]` green = has phone AND website, `[!]` yellow = partial, `[✗]` red = error.
- Final summary block: totals, unique/deduped, percentages per field, elapsed time, workers/speedup, output path, filter stats.
- Terminal logging is WARNING-only (full DEBUG detail goes to `scraping.log`), so logs never break the progress bar.

## Code conventions

- Type hints on all functions and methods
- Docstrings on all public classes and functions (Google style)
- `logging` module only — visual terminal output is the responsibility of `utils/terminal.py`
- All logs and comments in English
- Explicit exception handling with descriptive messages — never silent failures
- No external database — all output goes to `output/` as `.xlsx` or `.json`
- `pathlib.Path` for all filesystem paths

## Stack

- Python 3.13.3
- Scrapling 0.4.9 — `DynamicFetcher` (Google Maps), `StealthyFetcher` (Instagram/Facebook/Twitter), `Fetcher` (email scraping, DuckDuckGo)
- curl_cffi — TLS-impersonation HTTP client for LinkedIn
- aiohttp + asyncio — Serper.dev API client and concurrent pipeline
- openpyxl — styled `.xlsx` export
- colorama — terminal colors
- tqdm — progress bar
- phonenumbers — E.164 phone normalization
- rapidfuzz — fuzzy name matching for deduplication
- python-dotenv — env var loading
- Standard library: `json`, `argparse`, `logging`, `dataclasses`, `re`, `hashlib`, `concurrent.futures`, `threading`, `pathlib`

## Next steps

1. Sprint 6 — AI enrichment layer (LLM-based): lead classifier, quality scoring, cold-email generation
2. Sprint 7 — SaaS packaging with web UI
3. Known tech debt: `is_valid_email` lets Sentry DSNs (`hash@*.wixpress.com`) through; LinkedIn anonymous search needs a residential proxy (direct `linkedin.com/company/...` URLs work); Instagram/Facebook/Twitter limited to direct public profiles/pages without login
4. Argentina pack status after Sprint G (full per-scraper detail in `SPRINT_G_REPORT.md`): **5/9 return real leads** — `paginas_amarillas`, `dateas`, `abogados`, `zonaprop`, `argenprop`. `clutch` works technically but Clutch has no AR data (country directories removed). **3 blocked/dead:** `doctoralia` (IP/geo block — needs residential AR proxy), `mercadolibre` (snoopy anti-bot + API behind OAuth), `guia_oleo` (domain repurposed into an SEO blog — needs a new source). Key lesson: most AR sites are NOT Next.js — `dateas`/`abogados` are server-rendered HTML, `zonaprop`/`argenprop` expose data via per-posting JSON-LD/`"telephone"` plus the `/inmobiliarias/` link, not `__NEXT_DATA__`. `StealthyFetcher.fetch(timeout=...)` is in milliseconds (use 60000 for Cloudflare).
