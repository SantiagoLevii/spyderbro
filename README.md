# ScrapBro 🕷️
> Multi-source B2B Lead Scraper — Argentina & Global

![tests](https://img.shields.io/badge/tests-263%20passing-brightgreen)
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
- **Interactive TUI** (`python tui.py`) — Matrix-green terminal app with an
  animated spider intro, custom prompt_toolkit menus, auto/manual cookie setup,
  multi-query searches, a **live progress feed** (per-source bars, leads as they
  arrive, non-blocking error notices) and an in-terminal results table.
- **Speed controls** — website email lookup is the slowest step, so it is
  auto-disabled for broad runs (>3 sources or limit >30), toggleable with
  `--no-email-scraping` / the TUI Fast/Complete option; the total `--limit` is
  distributed across sources instead of fetched per-source.
- **Automatic cookie detection** — read your logged-in session straight from
  Chrome/Edge/Firefox (`browser-cookie3`), paste it manually, or run
  `scripts/get_cookies.js` in the browser console (the cookie screen shows both).
- **Hardware-aware performance** — worker counts sized to your CPU+RAM
  (`psutil`), image/CSS/font/ad blocking, aggressive timeouts and a TUI
  performance screen; tuneable via `WORKERS`/`NETWORK_IDLE`/`BLOCK_RESOURCES`.
- **Resilient pipeline** — a blocked source (repeated 403/429) fast-fails after
  3 consecutive errors or a 2-minute budget instead of stalling the run; the live
  screen marks it `✗ blocked` / `✗ timeout` / `~ partial` and keeps going.
- **Never lose leads** — Ctrl+C / closing the window triggers an emergency save of
  whatever was collected; no empty workbook is written when a run finds nothing.
- **Filters are opt-in** — every TUI filter starts unchecked and the confirmation
  screen warns when active filters could shrink the results.

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

## 🖥️ Interactive mode (TUI)

```bash
python tui.py
```

A Matrix-green terminal app, built on raw `prompt_toolkit` Applications so the
theme (phosphor green on black) is enforced everywhere. Pick a language, watch
the animated spider, then step through source selection, optional cookie setup,
search configuration, a confirmation screen, a live progress screen and an
in-terminal results table — looping for new searches until you quit.

Controls (uniform across every menu):

```
[Space] toggle / select      [Enter] confirm / OK
[Esc]   back to previous      [Q] quit (with confirmation)
[A]     select all (sources)  [N] deselect all (sources)
[↑][↓] navigate
```

- **Paste-your-cookie flow** — for sources that need a session (Instagram,
  Facebook, LinkedIn, Twitter/X, MercadoLibre) you paste the Cookie-Editor JSON
  directly; it is validated and saved to `.cookies/{source}.json` and loaded by
  the scraper on its next run.
- **Multi-query** — separate several searches with ` -- `
  (e.g. `inmobiliaria lujan -- santiago gomez`); each runs independently, results
  are combined and deduplicated, and the Excel gets a `Query` column.
- **Output filename = your query** — the workbook is named after what you
  searched (`inmobiliaria_lujan__santiago_gomez.xlsx`).

Requires an interactive terminal; in non-interactive contexts it prints a hint
to use the CLI. The CLI below remains the path for scripting and automation.

## 🚀 Quick start (CLI)

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
| `--query` | Search string (min 2 chars), or a CUIT/DNI with `--dateas-lookup`. Split several searches with ` -- ` (run independently, merged + deduped) |
| `--location` | Province/city for AR sources that need it |
| `--limit` | **Total** leads across all sources, 1–1000 (default: 50). With N sources it is split ~`limit/N` per source |
| `--limit-per-source` | Leads **per source**, ignoring `--limit` (e.g. `50` with 3 sources → up to 150 total) |
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
| `--no-email-scraping` | Skip visiting websites for emails (much faster); also auto-off for >3 sources or limit >30 |
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

263 tests, fully offline (network fetchers are mocked).

## 📝 Known limitations

- **mercadolibre** — "snoopy" anti-bot serves a JS shell to datacenter IPs;
  needs a residential `PROXY_URL`.
- **doctoralia** — geo-blocks datacenter IPs; replaced by `topdoctors_ar`.
- **topdoctors_ar / dateas** — registry-style sources: name + location but no
  per-record phone/email/web (gated behind paid reports / shared booking lines).
- **clutch** — removed per-country directories; useful for global/US rankings.
- **linkedin / instagram / facebook / twitter** — limited to direct public
  pages/profiles without a proxy/login.
- **email validator** — Sentry DSNs, CDN/PaaS hosts and placeholder domains are
  rejected via a technical-domain blacklist (`utils/validators.py`).

## 🗺️ Roadmap

- [ ] AI enrichment layer (LLM-based): lead classifier, quality scoring, cold-email generation
- [ ] SaaS packaging: API, web dashboard, billing
- [ ] Residential-proxy integration to unblock MercadoLibre / LinkedIn / Instagram

## 📄 License

MIT
