# ScrapBro 🕷️
> Multi-source B2B Lead Scraper — Argentina & Global

![tests](https://img.shields.io/badge/tests-181%20passing-brightgreen)
![python](https://img.shields.io/badge/python-3.13-blue)
![license](https://img.shields.io/badge/license-MIT-green)

ScrapBro extracts structured B2B leads (name, email, phone, website, address,
category, rating) from 15 sources — global platforms plus a dedicated Argentina
pack — into a styled Excel workbook, with concurrent scraping, cross-source
deduplication, caching and resumable sessions. It runs fully local from the CLI.

## ✨ Features

- **15 data sources** — Google Maps, Instagram, Facebook, Twitter/X, Google
  Dorks, LinkedIn + the Argentina pack (Páginas Amarillas, Dateas, Zonaprop,
  Argenprop, TripAdvisor AR, Top Doctors AR, MercadoLibre, Clutch, Abogados).
- **Professional Excel export** with dynamic columns per source (Dateas adds
  DNI / CUIT / age / province / locality / entity-type columns automatically).
- **Async pipeline** with automatic parallelism (`min(32, CPUs × 4)` workers).
- **Cross-source deduplication** (phone / domain / email / fuzzy name).
- **24h cache** and **resumable checkpoints** for interrupted runs.
- **Per-source rate limiting**, email validation and **E.164** phone normalization.
- **Direct CUIT/DNI lookup** on Dateas (Argentine tax-ID registry).

## 📦 Installation

```bash
git clone <repo-url> leadflow-scraper
cd leadflow-scraper
python -m venv venv
.\venv\Scripts\activate          # Windows  (source venv/bin/activate on Linux/Mac)
pip install -r requirements.txt
scrapling install                # one-time: installs the stealth browser
cp .env.example .env             # then edit .env (all keys optional)
```

Requires Python 3.13.

## ⚙️ Configuration

All environment variables are optional — ScrapBro runs with sensible defaults.

| Variable | Required | Description | Where to get it |
|----------|----------|-------------|-----------------|
| `SERPER_API_KEY` | optional | Google results for the Dorks source (falls back to DuckDuckGo) | https://serper.dev (2500 free) |
| `PROXY_URL` | optional | Residential proxy for LinkedIn / Instagram / MercadoLibre | any proxy provider |
| `APIFY_TOKEN` | optional | Reserved for future Apify integrations | https://apify.com |
| `SCRAPING_DELAY_MIN` / `MAX` | optional | Random delay range between requests (seconds) | default 2 / 5 |
| `DEFAULT_LIMIT` | optional | Default `--limit` | default 50 |
| `OUTPUT_DIR` | optional | Where `.xlsx` / `.json` files are written | default `output/` |

## 🚀 Quick start

```bash
# Basic search
python main.py --source google_maps --query "gyms in Miami" --limit 20 --output csv

# Multi-source (concurrent + auto-dedup)
python main.py --source google_maps,dorks,twitter --query "gyms in Miami" --limit 30

# Argentina pack (all 9 AR sources)
python main.py --source argentina --query "restaurantes" --location "buenos-aires" --limit 30

# With filters
python main.py --source google_maps --query "dentists Miami" --filter-complete --limit 50

# Dateas: search people and filter by province
python main.py --source dateas --query "garcia" --dateas-type personas \
  --location "Buenos Aires" --filter-has-cuit --limit 20

# Dateas: direct lookup by CUIT or DNI
python main.py --source dateas --query "20-43982658-5" --dateas-lookup cuit --limit 1
python main.py --source dateas --query "43982658" --dateas-lookup dni --limit 1

# JSON output
python main.py --source paginas_amarillas --query "contadores" --location "rosario" --output json
```

## 📋 Sources

| Source | Data extracted | Requires | Status |
|--------|----------------|----------|--------|
| google_maps | name, phone, web, address, category, rating | – | ✅ |
| paginas_amarillas | name, phone, address, web, category | – | ✅ |
| dateas | name, DNI, CUIT, age, province, locality | – | ✅ (registry, no direct contact) |
| zonaprop | agency, phone, zone | – | ✅ |
| argenprop | agency, address, zone | – | ✅ |
| tripadvisor_ar | name, phone, address, web, cuisine, rating | – | ✅ |
| topdoctors_ar | name, specialty, city | – | ✅ (no per-doctor contact) |
| abogados | firm, phone, address, specialty, web | – | ✅ |
| clutch | agency, web, location, rating | – | ✅ (global; AR has no data) |
| dorks | email, web, name | `SERPER_API_KEY` (optional) | ✅ |
| instagram | bio, email, phone | – | ⚠️ direct public profiles only |
| facebook | name, phone, web | – | ⚠️ direct public pages only |
| twitter | bio, email, web | – | ⚠️ no anonymous search |
| linkedin | name, role, company | `PROXY_URL` | ⚠️ authwall without proxy |
| mercadolibre | store, web, rating | `PROXY_URL` | ⚠️ anti-bot needs residential proxy |

Alias: `--source argentina` expands to the 9 AR sources. The legacy `guia_oleo`
and `doctoralia` are deprecated aliases that redirect to `tripadvisor_ar` and
`topdoctors_ar` respectively.

## 🔧 CLI flags

| Flag | Description |
|------|-------------|
| `--source` | Comma-separated sources, or `argentina` (default: `google_maps`) |
| `--query` | Search string (min 2 chars), or a CUIT/DNI with `--dateas-lookup` |
| `--location` | Province/city for AR sources that need it |
| `--limit` | Max leads, 1–1000 (default: 50) |
| `--output` | `csv` (→ styled `.xlsx`) or `json` (default: `csv`) |
| `--dateas-type` | `empresas` \| `personas` \| `ambos` (default: `empresas`) |
| `--dateas-lookup` | `name` (default) \| `cuit` \| `dni` |
| `--ml-official-only` | MercadoLibre: official stores only |
| `--filter-complete` | Only leads with phone AND website AND email |
| `--filter-has-phone` / `--filter-has-email` / `--filter-has-website` | Single-field filters |
| `--filter-min-rating FLOAT` | Only leads with rating ≥ value |
| `--filter-has-cuit` / `--filter-has-dni` | Dateas: require CUIT / DNI |
| `--filter-entity-type` | `fisica` \| `juridica` \| `ambos` (Dateas) |
| `--filter-province` / `--filter-locality` | Exact-match location filters (Dateas) |
| `--no-cache` / `--clear-cache` | Ignore / wipe the 24h cache |
| `--no-resume` | Ignore checkpoints, start fresh |

## 📊 Excel output

`--output csv` writes a styled `.xlsx` to `output/`:

```
┌─────────────┬─────────────┬───────────┬─────────┬──────────┬─────┬──────┐
│ Name        │ Email       │ Phone     │ Website │ Address  │ ... │ Tipo │   ← dark-blue header, frozen
├─────────────┼─────────────┼───────────┼─────────┼──────────┼─────┼──────┤
│ ...leads (alternating row shading, auto-filter)...                      │
├────────────────────────────────────────────────────────────────────────┤
│ Total: N leads | Con CUIT: N (X%) | Con email: N (X%) | ...             │   ← merged totals row
└────────────────────────────────────────────────────────────────────────┘
```

Columns are **dynamic**: the base 8 columns are always present; when the result
set contains Dateas leads, six extra columns (DNI, CUIT/CUIL, Edad, Provincia,
Localidad, Tipo) are appended and the totals row reports CUIT/DNI coverage.

## 🏗️ Architecture

```
CLI → validation → Scraper(s) → Async Pipeline → Deduplicator → Validators → Exporter → .xlsx / .json
                                  (concurrent,        (phone/domain/   (email,
                                   semaphore)          email/fuzzy)     E.164)
```

- `scrapers/` — one module per source (plus `query_utils`, `email_scraper`).
- `pipeline/` — `async_pipeline` (concurrency) + `deduplicator` (merge).
- `exporters/` — styled `.xlsx` (`csv_exporter`) and `json_exporter`.
- `utils/` — cache, checkpoints, rate limiter, retry, validators, concurrency.
- `config/settings.py` — env-backed settings; `models/lead.py` — the Lead model.

## 🧪 Tests

```bash
python -m pytest tests/ -v
python -m pytest tests/ -v --cov=. --cov-report=term-missing
```

181 tests, fully offline (network fetchers are mocked).

## 📝 Known limitations

- **mercadolibre** — "snoopy" anti-bot serves a JS shell to datacenter IPs;
  needs a residential `PROXY_URL`.
- **doctoralia** — geo-blocks datacenter IPs; replaced by `topdoctors_ar`.
- **topdoctors_ar / dateas** — registry-style sources: name + location but no
  per-record phone/email/web (gated behind paid reports / shared booking lines).
- **clutch** — removed per-country directories; useful for global/US rankings.
- **linkedin / instagram / facebook / twitter** — limited to direct public
  pages/profiles without a proxy/login.
- **email validator** — occasional false positives on Sentry DSNs (`*.wixpress.com`).

## 🗺️ Roadmap

- [ ] AI enrichment layer (LLM-based): lead classifier, quality scoring, cold-email generation
- [ ] SaaS packaging: API, web dashboard, billing
- [ ] Residential-proxy integration to unblock MercadoLibre / LinkedIn / Instagram

## 📄 License

MIT
