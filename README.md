# ScrapBro

Multi-source B2B lead scraper. Extrae nombre, email, teléfono, web, dirección, categoría y rating de negocios desde Google Maps, Instagram, Facebook, Twitter/X, Google Dorks y LinkedIn.

Los emails se extraen del sitio web de cada negocio y se validan; los teléfonos se normalizan a formato E.164. Los leads de múltiples fuentes se deduplican y mergean automáticamente. Las sesiones interrumpidas se pueden resumir desde checkpoints.

## Instalación

```bash
# 1. Clonar / copiar el proyecto y entrar al directorio
cd leadflow-scraper

# 2. Crear y activar el entorno virtual (Windows)
python -m venv venv
.\venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Instalar el navegador de Scrapling (primera vez)
scrapling install
```

Requiere Python 3.13+.

## Configuración

```bash
# Copiar la plantilla de variables de entorno
cp .env.example .env
```

Editar `.env`:

| Variable | Obligatoria | Descripción |
|---|---|---|
| `SERPER_API_KEY` | No | API de Serper.dev para el scraper de Dorks (resultados de Google). Gratis 2500 queries en https://serper.dev. Sin key, Dorks usa DuckDuckGo con delays conservadores. |
| `PROXY_URL` | No | Proxy residencial (`http://usuario:password@host:puerto`). Mejora LinkedIn/Instagram/Facebook. |
| `APIFY_TOKEN` | No | Token de Apify para Instagram a escala (futuro). |
| `SCRAPING_DELAY_MIN/MAX` | No | Delays entre requests en segundos (default 2-5). |
| `DEFAULT_LIMIT` | No | Límite de leads por defecto (default 50). |
| `OUTPUT_DIR` | No | Carpeta de salida (default `output/`). |

## Uso

```bash
# Básico
python main.py --source google_maps --query "gyms in Miami" --limit 10 --output csv

# Multi-fuente (corre en paralelo y deduplica automáticamente)
python main.py --source google_maps,dorks,linkedin --query "gyms in Miami" --limit 15 --output csv

# Filtros
python main.py --source google_maps --query "gyms in Miami" --filter-has-email --filter-min-rating 4.0

# Caché y checkpoints
python main.py --source dorks --query "gyms in Miami" --no-cache     # fuerza re-scraping
python main.py --clear-cache                                          # limpia caché y sale
python main.py --source google_maps --query "..." --no-resume         # ignora checkpoints
```

| Flag | Descripción |
|---|---|
| `--source` | Fuentes separadas por coma: `google_maps`, `instagram`, `facebook`, `twitter`, `dorks`, `linkedin` |
| `--query` | Búsqueda (mín. 2 caracteres). Instagram/Twitter: `@cuenta`, `#hashtag` o username |
| `--limit` | Máximo de leads, 1-1000 (default 50) |
| `--output` | `csv` (genera `.xlsx` con formato Excel) o `json` (default csv) |
| `--filter-complete` | Solo leads con teléfono + web + email |
| `--filter-has-phone/-email/-website` | Solo leads con ese campo |
| `--filter-min-rating FLOAT` | Solo leads con rating >= valor |
| `--no-cache` / `--clear-cache` | Control de caché (TTL 24h) |
| `--no-resume` | Ignorar checkpoints |

## Fuentes disponibles

| Fuente | Qué extrae | Limitaciones |
|---|---|---|
| `google_maps` | Nombre, dirección, categoría, rating, teléfono, web, email (vía sitio web) | Selectores CSS pueden cambiar; ~10 resultados por scroll |
| `instagram` | Nombre, bio, email/teléfono de la bio, link in bio, categoría | Solo perfiles públicos; seguidores y hashtags requieren login (devuelve vacío) |
| `facebook` | Nombre, categoría, dirección, teléfono, web, email | Solo páginas públicas; la búsqueda requiere login (devuelve vacío) |
| `twitter` | Nombre, bio, email/teléfono de la bio, web | Solo perfiles públicos; la búsqueda requiere login (devuelve vacío) |
| `dorks` | Nombre, web, email (vía EmailScraper), teléfono del snippet | Con SERPER_API_KEY usa Google; sin key, DuckDuckGo con delays 8-15s |
| `linkedin` | Empresa: nombre, industria, web. Perfil: nombre, cargo, ubicación | Authwall agresivo sin proxy; modo conservador con delays 10-20s |

## Arquitectura

```
main.py                  CLI: argparse, validación de inputs, orquestación
config/settings.py       Variables de entorno (.env) + validate_settings()
models/lead.py           Dataclass Lead
scrapers/                Un módulo por fuente + email_scraper (enriquecimiento)
pipeline/
  async_pipeline.py      Scraping concurrente multi-fuente (asyncio + semáforo)
  deduplicator.py        Dedup por teléfono/dominio/email/fuzzy nombre + merge
exporters/               Excel .xlsx (openpyxl) y JSON
utils/
  terminal.py            Banner, progress bar, resumen de sesión
  validators.py          Validación de emails, normalización E.164
  cache.py               Caché JSON con TTL 24h (.cache/)
  checkpoint.py          Sesiones resumibles con TTL 2h (.checkpoints/)
  rate_limiter.py        Rate limiting por scraper (sliding window)
  retry.py               Retry con backoff exponencial (sync y async)
  file_utils.py          sanitize_filename, ensure_dir
tests/                   pytest + pytest-asyncio, mocks offline (sin red)
```

## Tests

```bash
python -m pytest tests/ -v --cov=. --cov-report=term-missing
```

## Seguridad

- Sin secrets hardcodeados — todo via `.env` (gitignored), leído en `config/settings.py`
- Las API keys nunca se loggean ni aparecen en outputs
- Inputs del CLI validados (query, limit, source, output)
- Timeouts explícitos en todos los requests
- Rate limiting por fuente para no saturar los sitios
