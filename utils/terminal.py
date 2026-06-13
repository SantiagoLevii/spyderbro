import sys
from dataclasses import dataclass

from colorama import Fore, Style, init as colorama_init
from tqdm import tqdm

from models.lead import Lead

colorama_init(autoreset=True)

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

VERSION = "0.1.0"

BANNER = f"""
╔══════════════════════════════════════╗
║         ScrapBro v{VERSION}              ║
║   Multi-source B2B Lead Scraper      ║
╚══════════════════════════════════════╝
"""

BAR_FORMAT = "{desc} |{bar:10}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"

NAME_WIDTH = 28


@dataclass
class SessionStats:
    """Aggregated statistics for a completed scraping session."""

    total: int = 0
    with_phone: int = 0
    with_website: int = 0
    with_email: int = 0
    complete: int = 0
    elapsed_seconds: float = 0.0
    output_path: str = ""
    filter_label: str = ""
    leads_before_filter: int = 0
    duplicates_removed: int = 0
    leads_merged: int = 0
    sources_label: str = ""
    dorks_engine: str = ""
    raw_total: int = 0
    parallel_scrapers: int = 0
    speedup: float = 0.0
    workers_used: int = 0
    cpu_count: int = 0


def print_banner() -> None:
    """Print the ScrapBro startup banner in cyan."""
    print(Fore.CYAN + BANNER + Style.RESET_ALL)


class ScrapeProgress:
    """tqdm progress bar with colored per-lead result lines.

    The bar stays green while leads come out complete, turns yellow when a
    lead is missing phone or email, and red when a lead errors out.
    """

    def __init__(self, total: int) -> None:
        """Create the progress bar for an expected number of leads.

        Args:
            total: Expected number of leads to process.
        """
        self._bar = tqdm(
            total=total,
            desc="Scraping leads...",
            unit="leads",
            colour="green",
            bar_format=BAR_FORMAT,
            file=sys.stdout,
        )

    def lead_done(self, lead: Lead) -> None:
        """Report a successfully extracted lead and print its result line.

        Args:
            lead: The extracted lead. Printed as [✓] if it has both phone
                and website, [!] otherwise.
        """
        name = lead.name[:NAME_WIDTH].ljust(NAME_WIDTH)
        phone = lead.phone or f"{Fore.YELLOW}sin teléfono{Style.RESET_ALL}"
        website = self._strip_url(lead.website) or f"{Fore.YELLOW}sin web{Style.RESET_ALL}"
        line = f"{name} | {phone} | {website} | {lead.category} | ★{lead.rating}"

        if lead.phone and lead.website:
            self._bar.write(f"{Fore.GREEN}[✓]{Style.RESET_ALL} {line}")
        else:
            self._bar.write(f"{Fore.YELLOW}[!]{Style.RESET_ALL} {line}")
            self._bar.colour = "yellow"
        if not lead.email or not lead.phone:
            self._bar.colour = "yellow"
        self._bar.update(1)

    def lead_error(self, name: str, message: str) -> None:
        """Report a lead that failed to extract.

        Args:
            name: Business name, or a placeholder if unknown.
            message: Short description of the error.
        """
        display = name[:NAME_WIDTH] if name else "(desconocido)"
        self._bar.write(
            f"{Fore.RED}[✗] ERROR: {display.ljust(NAME_WIDTH)} | {message}{Style.RESET_ALL}"
        )
        self._bar.colour = "red"
        self._bar.update(1)

    def close(self) -> None:
        """Close the underlying progress bar."""
        self._bar.close()

    @staticmethod
    def _strip_url(url: str) -> str:
        """Return a short display form of a URL (no scheme, no trailing slash)."""
        return url.replace("https://", "").replace("http://", "").rstrip("/")


def _pct(part: int, total: int) -> str:
    """Format part/total as a percentage string."""
    if total == 0:
        return "0%"
    return f"{round(part * 100 / total)}%"


def _fmt_elapsed(seconds: float) -> str:
    """Format seconds as 'Xm Ys'."""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s"


def print_summary(stats: SessionStats) -> None:
    """Print the end-of-session summary block.

    Args:
        stats: Aggregated session statistics.
    """
    rule = "━" * 40
    total = stats.total
    raw_total = stats.raw_total or total
    speed = total / stats.elapsed_seconds if stats.elapsed_seconds > 0 else 0.0

    lines = [
        rule,
        "  ScrapBro — Sesión completada",
        rule,
    ]

    if stats.sources_label:
        lines.append(f"  Fuentes usadas:       {stats.sources_label}")
    if stats.dorks_engine:
        lines.append(f"  Motor Dorks:          {stats.dorks_engine}")

    lines += [
        f"  Total leads:          {raw_total}",
        f"  Leads únicos:         {total}  (deduplicados: {stats.duplicates_removed})",
        f"  Leads mergeados:      {stats.leads_merged}  (completados con datos de otra fuente)",
        f"  Con teléfono:         {stats.with_phone}  ({_pct(stats.with_phone, total)})",
        f"  Con sitio web:        {stats.with_website}  ({_pct(stats.with_website, total)})",
        f"  Con email:            {stats.with_email}  ({_pct(stats.with_email, total)})",
        f"  Leads completos:      {stats.complete}  ({_pct(stats.complete, total)})",
        "",
        "  Performance:",
        f"  Tiempo total:         {_fmt_elapsed(stats.elapsed_seconds)}",
    ]

    if stats.parallel_scrapers > 1:
        lines.append(f"  Scrapers en paralelo: {stats.parallel_scrapers}")

    if stats.workers_used > 0:
        lines.append(
            f"  Workers usados:       {stats.workers_used} (auto — {stats.cpu_count} CPUs detectados)"
        )

    lines.append(f"  Velocidad:            {speed:.2f} leads/s")

    if stats.speedup > 1:
        lines.append(f"  Speedup estimado:     ~{stats.speedup:.1f}x vs secuencial")

    lines += [
        "",
        "  Archivos guardados:",
        f"  → {stats.output_path}  ({total} leads)",
    ]

    if stats.filter_label:
        discarded = stats.leads_before_filter - total
        lines += [
            "",
            f"  Filtro aplicado:    {stats.filter_label}",
            f"  Leads antes:        {stats.leads_before_filter}",
            f"  Leads después:      {total}",
            f"  Descartados:        {discarded}",
        ]

    lines.append(rule)
    print(Fore.CYAN + "\n".join(lines) + Style.RESET_ALL)


def print_source_start(source: str, index: int, total: int) -> None:
    """Announce which source is about to be scraped.

    Args:
        source: Source name (e.g. "google_maps").
        index: 1-based position of this source in the run.
        total: Total number of sources in the run.
    """
    print(f"\n{Fore.CYAN}▶ Fuente {index}/{total}: {source}{Style.RESET_ALL}")


def print_cache_notice(query: str, age_seconds: float) -> None:
    """Tell the user cached results are being used and how old they are.

    Args:
        query: The search query the cache entry belongs to.
        age_seconds: Age of the cache entry in seconds.
    """
    hours, remainder = divmod(int(age_seconds), 3600)
    minutes = remainder // 60
    age = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
    print(f'{Fore.YELLOW}[CACHE] Usando resultados cacheados para "{query}" (hace {age})')
    print(f"        Para forzar re-scraping usá --no-cache{Style.RESET_ALL}")


def print_cache_cleared(deleted: int) -> None:
    """Report how many cache files were deleted."""
    print(f"{Fore.CYAN}Caché limpiada: {deleted} archivos eliminados{Style.RESET_ALL}")


def ask_resume(query: str, age_seconds: float, lead_count: int) -> bool:
    """Ask the user whether to resume a previous interrupted session.

    Args:
        query: Query of the saved session.
        age_seconds: Age of the checkpoint in seconds.
        lead_count: Leads already saved in the checkpoint.

    Returns:
        True if the user wants to resume (default on empty answer).
    """
    hours, remainder = divmod(int(age_seconds), 3600)
    minutes = remainder // 60
    age = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
    print(f'{Fore.YELLOW}[CHECKPOINT] Se encontró una sesión anterior para "{query}" '
          f"(hace {age}, {lead_count} leads guardados){Style.RESET_ALL}")
    try:
        answer = input("             ¿Querés continuar desde donde quedó? [S/n]: ")
    except EOFError:
        return True
    return not answer.strip().lower().startswith("n")


def print_dorks_engine(engine: str) -> None:
    """Show which search engine the Dorks scraper detected and why.

    Args:
        engine: 'serper' or 'duckduckgo'.
    """
    if engine == "serper":
        print(f"{Fore.CYAN}[DORKS] Motor detectado: Serper.dev API (SERPER_API_KEY encontrada)")
        print(f"        → Resultados de Google, sin rate limits agresivos{Style.RESET_ALL}")
    else:
        print(f"{Fore.YELLOW}[DORKS] Motor detectado: DuckDuckGo (sin SERPER_API_KEY en .env)")
        print("        → Delays conservadores activados (8-15s entre queries)")
        print("        → Para mejor performance: agregá SERPER_API_KEY al .env")
        print(f"        → Serper.dev gratis: https://serper.dev (2500 queries){Style.RESET_ALL}")


def print_error(message: str) -> None:
    """Print a fatal input/usage error in red."""
    print(f"{Fore.RED}ERROR: {message}{Style.RESET_ALL}")


def print_linkedin_proxy_warning() -> None:
    """Warn that LinkedIn runs in conservative mode without a proxy."""
    print(f"{Fore.YELLOW}[LINKEDIN] ⚠ Sin proxy configurado — modo conservador activado")
    print("           Delays: 10-20s entre requests")
    print("           Para mejor rendimiento agregá PROXY_URL al .env")
    print(f"           Recomendado: Bright Data, Oxylabs, DataImpulse{Style.RESET_ALL}")
