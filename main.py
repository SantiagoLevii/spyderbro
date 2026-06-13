import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

EXTERNAL_LOGGERS = (
    "scrapling",
    "playwright",
    "asyncio",
    "httpx",
    "httpcore",
    "urllib3",
    "curl_cffi",
    "aiohttp",
    "charset_normalizer",
    "filelock",
)

LOG_FILE = Path("scraping.log")


def silence_external_loggers() -> None:
    """Force third-party loggers to WARNING and route them through root.

    Scrapling installs its own handler at import time, so this runs both
    before and after the scraper imports.
    """
    for name in EXTERNAL_LOGGERS:
        external = logging.getLogger(name)
        external.setLevel(logging.WARNING)
        external.handlers.clear()
        external.propagate = True


def configure_logging() -> None:
    """Configure dual logging and silence noisy third-party loggers.

    - FileHandler -> scraping.log -> DEBUG (full detail)
    - StreamHandler -> terminal -> WARNING (only important problems)

    Must run before scraper imports so Scrapling's own handler does not
    pollute the terminal.
    """
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.WARNING)
    stream_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers = [file_handler, stream_handler]

    silence_external_loggers()


configure_logging()

from config.settings import settings, validate_settings
from exporters.csv_exporter import CSVExporter
from exporters.json_exporter import JSONExporter
from models.lead import Lead
from pipeline.async_pipeline import AsyncScrapingPipeline
from pipeline.deduplicator import Deduplicator
from scrapers.abogados import AbogadosScraper
from scrapers.argenprop import ArgenpropScraper
from scrapers.clutch import ClutchScraper
from scrapers.dateas import DateasScraper
from scrapers.doctoralia import DoctoraliaScraper
from scrapers.dorks import DorksScraper
from scrapers.facebook import FacebookScraper
from scrapers.google_maps import GoogleMapsScraper
from scrapers.guia_oleo import GuiaOleoScraper
from scrapers.instagram import InstagramScraper
from scrapers.linkedin import LinkedInScraper
from scrapers.mercadolibre import MercadoLibreScraper
from scrapers.paginas_amarillas import PaginasAmarillasScraper
from scrapers.topdoctors_ar import TopDoctorsARScraper
from scrapers.tripadvisor_ar import TripAdvisorARScraper
from scrapers.twitter import TwitterScraper
from scrapers.zonaprop import ZonapropScraper
from utils.cache import ScrapingCache
from utils.file_utils import sanitize_filename
from utils.terminal import (
    SessionStats,
    print_banner,
    print_cache_cleared,
    print_cache_notice,
    print_dorks_engine,
    print_error,
    print_source_start,
    print_summary,
)

logger = logging.getLogger(__name__)

SCRAPERS = {
    "google_maps": GoogleMapsScraper,
    "instagram": InstagramScraper,
    "facebook": FacebookScraper,
    "twitter": TwitterScraper,
    "dorks": DorksScraper,
    "linkedin": LinkedInScraper,
    "paginas_amarillas": PaginasAmarillasScraper,
    "dateas": DateasScraper,
    "zonaprop": ZonapropScraper,
    "argenprop": ArgenpropScraper,
    "guia_oleo": GuiaOleoScraper,
    "doctoralia": DoctoraliaScraper,
    "mercadolibre": MercadoLibreScraper,
    "clutch": ClutchScraper,
    "abogados": AbogadosScraper,
    "tripadvisor_ar": TripAdvisorARScraper,
    "topdoctors_ar": TopDoctorsARScraper,
}

# Shortcut: --source argentina expands to the full Argentina pack.
ARGENTINA_PACK = [
    "paginas_amarillas", "dateas", "zonaprop", "argenprop",
    "tripadvisor_ar",   # replaces discontinued guia_oleo
    "topdoctors_ar",    # replaces geo-blocked doctoralia
    "mercadolibre", "clutch", "abogados",
]

EXPORTERS = {
    "csv": CSVExporter,
    "json": JSONExporter,
}

EXTENSIONS = {
    "csv": ".xlsx",
    "json": ".json",
}


def build_filename(source: str, query: str, fmt: str) -> str:
    """Generate an output filename from source, query, and format."""
    return f"{source}_{sanitize_filename(query)[:40]}{EXTENSIONS[fmt]}"


def apply_filters(leads: list[Lead], args: argparse.Namespace) -> tuple[list[Lead], str]:
    """Apply CLI output filters to the scraped leads.

    Args:
        leads: Leads returned by the scraper.
        args: Parsed CLI arguments.

    Returns:
        Tuple of (filtered leads, human-readable filter label). The label is
        empty when no filter was requested.
    """
    filters: list[tuple[str, object]] = []

    if args.filter_complete:
        filters.append(("--filter-complete", lambda l: bool(l.phone and l.website and l.email)))
    if args.filter_has_phone:
        filters.append(("--filter-has-phone", lambda l: bool(l.phone)))
    if args.filter_has_email:
        filters.append(("--filter-has-email", lambda l: bool(l.email)))
    if args.filter_has_website:
        filters.append(("--filter-has-website", lambda l: bool(l.website)))
    if args.filter_min_rating is not None:
        filters.append((
            f"--filter-min-rating {args.filter_min_rating}",
            lambda l: l.rating >= args.filter_min_rating,
        ))
    if args.filter_has_cuit:
        filters.append(("--filter-has-cuit", lambda l: bool((l.raw_data or {}).get("cuit"))))
    if args.filter_has_dni:
        filters.append(("--filter-has-dni", lambda l: bool((l.raw_data or {}).get("dni"))))
    if args.filter_entity_type and args.filter_entity_type != "ambos":
        et = args.filter_entity_type
        filters.append((
            f"--filter-entity-type {et}",
            lambda l, et=et: (l.raw_data or {}).get("entity_type") == et,
        ))
    if args.filter_province:
        prov = args.filter_province.strip().lower()
        filters.append((
            f"--filter-province {args.filter_province}",
            lambda l, p=prov: (l.raw_data or {}).get("province", "").strip().lower() == p,
        ))
    if args.filter_locality:
        loc = args.filter_locality.strip().lower()
        filters.append((
            f"--filter-locality {args.filter_locality}",
            lambda l, lo=loc: (l.raw_data or {}).get("locality", "").strip().lower() == lo,
        ))

    if not filters:
        return leads, ""

    label = ", ".join(name for name, _ in filters)
    filtered = [lead for lead in leads if all(check(lead) for _, check in filters)]
    logger.info("Filters %s: %d -> %d leads", label, len(leads), len(filtered))
    return filtered, label


def build_stats(
    leads: list[Lead],
    elapsed: float,
    output_path: str,
    filter_label: str,
    leads_before_filter: int,
    duplicates_removed: int,
    leads_merged: int,
) -> SessionStats:
    """Aggregate session statistics for the final summary."""
    return SessionStats(
        total=len(leads),
        with_phone=sum(1 for l in leads if l.phone),
        with_website=sum(1 for l in leads if l.website),
        with_email=sum(1 for l in leads if l.email),
        complete=sum(1 for l in leads if l.phone and l.website and l.email),
        elapsed_seconds=elapsed,
        output_path=output_path,
        filter_label=filter_label,
        leads_before_filter=leads_before_filter,
        duplicates_removed=duplicates_removed,
        leads_merged=leads_merged,
    )


def validate_args(args: argparse.Namespace) -> list[str]:
    """Validate CLI inputs, printing a red error and exiting 1 on failure.

    Rules: query >= 2 chars, 1 <= limit <= 1000, sources must be known.

    Args:
        args: Parsed CLI arguments.

    Returns:
        List of validated source names.
    """
    if not args.query or len(args.query.strip()) < 2:
        print_error("--query es obligatoria y debe tener al menos 2 caracteres")
        sys.exit(1)

    if not 1 <= args.limit <= 1000:
        print_error(f"--limit debe estar entre 1 y 1000 (recibido: {args.limit})")
        sys.exit(1)

    sources: list[str] = []
    for token in (s.strip() for s in args.source.split(",") if s.strip()):
        if token == "argentina":
            sources.extend(s for s in ARGENTINA_PACK if s not in sources)
        elif token not in sources:
            sources.append(token)

    invalid = [s for s in sources if s not in SCRAPERS]
    if not sources or invalid:
        print_error(
            f"fuente(s) inválida(s): {', '.join(invalid) or args.source} — "
            f"válidas: {', '.join(SCRAPERS)}"
        )
        sys.exit(1)

    return sources


def scrape_sources(
    sources: list[str], args: argparse.Namespace, cache: ScrapingCache
) -> tuple[list[Lead], dict]:
    """Scrape every requested source in sequence, using the cache when fresh.

    Args:
        sources: Validated source names.
        args: Parsed CLI arguments.
        cache: Cache handler.

    Returns:
        Tuple of (combined leads, worker stats: workers_used / fetch_seconds).
    """
    all_leads: list[Lead] = []
    worker_stats = {"workers_used": 0, "fetch_seconds": 0.0}

    for index, source in enumerate(sources, start=1):
        print_source_start(source, index, len(sources))

        if not args.no_cache:
            cached = cache.get(source, args.query)
            if cached is not None:
                age = cache.age_seconds(source, args.query) or 0
                print_cache_notice(args.query, age)
                all_leads.extend(cached)
                continue

        scraper = SCRAPERS[source]()
        if hasattr(scraper, "allow_resume"):
            scraper.allow_resume = not args.no_resume
        if source == "dorks":
            print_dorks_engine(scraper.engine)

        leads = scraper.scrape(query=args.query, limit=args.limit)
        worker_stats["workers_used"] = max(
            worker_stats["workers_used"], getattr(scraper, "workers_used", 0)
        )
        worker_stats["fetch_seconds"] += getattr(scraper, "fetch_seconds_total", 0.0)
        if leads:
            cache.set(source, args.query, leads)
        else:
            logger.warning("No leads found for query %r on source %s", args.query, source)
        all_leads.extend(leads)

    return all_leads, worker_stats


def main() -> None:
    """Entry point for the ScrapBro CLI."""
    parser = argparse.ArgumentParser(
        prog="scrapbro",
        description="ScrapBro — multi-source B2B lead scraper",
    )
    parser.add_argument(
        "--source",
        default="google_maps",
        help=(
            "Data source(s) to scrape, comma-separated for multi-source "
            f"(e.g. google_maps,instagram). Valid: {', '.join(SCRAPERS)} "
            "(default: google_maps)"
        ),
    )
    parser.add_argument(
        "--query",
        help='Search query (e.g. "gyms in Miami"). Instagram: @account, #hashtag, or username',
    )
    parser.add_argument(
        "--location",
        default="",
        help="Province or city for sources that need it (e.g. paginas_amarillas, dateas). "
        "Ignored by sources that don't use it.",
    )
    parser.add_argument(
        "--dateas-type",
        choices=["empresas", "personas", "ambos"],
        default="empresas",
        help="Dateas search mode (default: empresas). Only applies to the dateas source.",
    )
    parser.add_argument(
        "--dateas-lookup",
        choices=["name", "cuit", "dni"],
        default="name",
        help="Dateas lookup mode: name search (default), or exact cuit/dni lookup.",
    )
    parser.add_argument(
        "--ml-official-only",
        action="store_true",
        help="MercadoLibre only: restrict to official stores (default: all sellers).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=settings.DEFAULT_LIMIT,
        help=f"Maximum number of leads to extract (default: {settings.DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--output",
        choices=["csv", "json"],
        default="csv",
        help="Output format (default: csv)",
    )
    parser.add_argument(
        "--filter-complete",
        action="store_true",
        help="Keep only leads that have phone AND website AND email",
    )
    parser.add_argument(
        "--filter-has-phone",
        action="store_true",
        help="Keep only leads that have a phone number",
    )
    parser.add_argument(
        "--filter-has-email",
        action="store_true",
        help="Keep only leads that have an email",
    )
    parser.add_argument(
        "--filter-has-website",
        action="store_true",
        help="Keep only leads that have a website",
    )
    parser.add_argument(
        "--filter-min-rating",
        type=float,
        default=None,
        metavar="FLOAT",
        help="Keep only leads with rating >= FLOAT (e.g. --filter-min-rating 4.0)",
    )
    parser.add_argument(
        "--filter-has-cuit",
        action="store_true",
        help="Keep only leads that have a CUIT/CUIL (Dateas)",
    )
    parser.add_argument(
        "--filter-has-dni",
        action="store_true",
        help="Keep only leads that have a DNI (Dateas)",
    )
    parser.add_argument(
        "--filter-entity-type",
        choices=["fisica", "juridica", "ambos"],
        default="ambos",
        help="Filter Dateas leads by entity type (default: ambos)",
    )
    parser.add_argument(
        "--filter-province",
        default=None,
        metavar="PROVINCE",
        help='Keep only leads from this exact province (e.g. "Buenos Aires")',
    )
    parser.add_argument(
        "--filter-locality",
        default=None,
        metavar="LOCALITY",
        help='Keep only leads from this exact locality (e.g. "General Rodríguez")',
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cached results and force re-scraping",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Delete all cached results and exit",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore checkpoints and always start from scratch",
    )

    args = parser.parse_args()

    silence_external_loggers()
    validate_settings()

    # Runtime-only options consumed by the Argentina-pack scrapers. Stored on
    # the settings singleton so both single-source and the async pipeline can
    # read them without changing the scraper call signature.
    settings.LOCATION = (args.location or "").strip()
    settings.DATEAS_TYPE = args.dateas_type
    settings.DATEAS_LOOKUP = args.dateas_lookup
    settings.ML_OFFICIAL_ONLY = args.ml_official_only

    cache = ScrapingCache()

    if args.clear_cache:
        deleted = cache.clear()
        print_cache_cleared(deleted)
        return

    sources = validate_args(args)

    print_banner()

    logger.info(
        "ScrapBro starting — sources=%s query=%r limit=%d output=%s",
        sources,
        args.query,
        args.limit,
        args.output,
    )

    start_time = time.time()
    scraper_seconds = 0.0
    stats_workers = 0
    parallel = len(sources) > 1

    try:
        if parallel:
            if "dorks" in sources:
                print_dorks_engine("serper" if settings.SERPER_API_KEY else "duckduckgo")
            pipeline = AsyncScrapingPipeline(
                registry=SCRAPERS, cache=None if args.no_cache else cache
            )
            leads = asyncio.run(pipeline.run(sources, args.query, args.limit))
            duplicates_removed = pipeline.duplicates_removed
            scraper_seconds = pipeline.scraper_seconds
        else:
            all_leads, worker_stats = scrape_sources(sources, args, cache)
            leads, duplicates_removed = Deduplicator().deduplicate(all_leads)
            scraper_seconds = worker_stats["fetch_seconds"]
            stats_workers = worker_stats["workers_used"]
    except KeyboardInterrupt:
        print()
        logger.warning(
            "Run interrupted by user — re-run the same command to resume from the checkpoint"
        )
        sys.exit(130)

    leads_merged = sum(1 for l in leads if len(l.raw_data.get("merged_from", [])) > 1)
    raw_total = len(leads) + duplicates_removed

    leads_before_filter = len(leads)
    leads, filter_label = apply_filters(leads, args)

    filename = build_filename("_".join(sources), args.query, args.output)
    exporter = EXPORTERS[args.output]()
    output_path = exporter.export(leads, filename)

    elapsed = time.time() - start_time
    stats = build_stats(
        leads, elapsed, output_path, filter_label,
        leads_before_filter, duplicates_removed, leads_merged,
    )
    stats.sources_label = ", ".join(sources)
    stats.raw_total = raw_total
    if "dorks" in sources:
        stats.dorks_engine = "Serper.dev (Google)" if settings.SERPER_API_KEY else "DuckDuckGo"
    if parallel:
        stats.parallel_scrapers = min(len(sources), 3)
    stats.workers_used = stats_workers
    stats.cpu_count = os.cpu_count() or 1
    if elapsed > 0 and scraper_seconds > elapsed:
        stats.speedup = scraper_seconds / elapsed
    print_summary(stats)


if __name__ == "__main__":
    main()
