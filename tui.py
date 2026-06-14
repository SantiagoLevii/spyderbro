#!/usr/bin/env python3
"""ScrapBro TUI — interactive terminal interface (Matrix-green aesthetic).

Run with::

    python tui.py

For CLI mode (scripting / automation) use ``python main.py --help``. The TUI is
an additive layer on top of the existing CLI orchestration — it does not replace
it. If the terminal cannot host an interactive UI, it falls back to a short
message pointing at the CLI.
"""
import argparse
import atexit
import logging
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import main as cli
from config.settings import settings
from exporters.csv_exporter import CSVExporter, build_output_filename
from exporters.json_exporter import JSONExporter
from pipeline.deduplicator import Deduplicator
from utils.hardware import detect_hardware
from ui import menus, progress
from ui.banner import print_banner
from ui.menus import QUIT
from ui.progress import ScrapingState
from ui.spider import animate_spider
from ui.theme import ANSI_GREEN, ANSI_RED, ANSI_RESET
from utils.session_cache import SessionCache

logger = logging.getLogger(__name__)

_NOISY_LOGGERS = ("scrapling", "playwright", "asyncio", "httpx", "httpcore",
                  "urllib3", "aiohttp", "curl_cffi")


def _silence_terminal_logging() -> None:
    """Send all logging to scraping.log only — the TUI owns the screen."""
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, logging.StreamHandler)
                     or isinstance(h, logging.FileHandler)]
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.CRITICAL)


def _namespace(config: dict, sources: list[str], query: str) -> argparse.Namespace:
    """Build a CLI-equivalent Namespace from the TUI config for one query."""
    return argparse.Namespace(
        source=",".join(sources),
        query=query,
        location=config.get("location", ""),
        limit=config.get("limit", 50),
        output=config.get("output", "csv"),
        dateas_type=config.get("dateas_type", "ambos"),
        dateas_lookup=config.get("dateas_lookup", "name"),
        ml_official_only=False,
        filter_complete=False,
        filter_has_phone=config.get("filter_has_phone", False),
        filter_has_email=config.get("filter_has_email", False),
        filter_has_website=config.get("filter_has_website", False),
        filter_min_rating=config.get("filter_min_rating"),
        filter_has_cuit=config.get("filter_has_cuit", False),
        filter_has_dni=False,
        filter_entity_type="ambos",
        filter_province=None,
        filter_locality=None,
        no_cache=False,
        clear_cache=False,
        no_resume=True,
    )


def _scrape_one(query: str, config: dict, sources: list[str], cache, state: ScrapingState) -> list:
    """Scrape a single query across the sources (session-cached, with callbacks)."""
    args = _namespace(config, sources, query)
    cache_key = ",".join(sorted(sources))
    cached = SessionCache.get(cache_key, query)
    if cached is not None:
        for lead in cached:
            state.add_lead(lead, lead.source)
        return cached

    def on_lead(source: str, lead) -> None:
        state.add_lead(lead, source)

    def on_status(source: str, status: str, target: int = 0) -> None:
        state.set_source_status(source, status, target)

    per_source_limit = config.get("limit_per_source", 0)
    if len(sources) > 1:
        pipeline = cli.AsyncScrapingPipeline(registry=cli.SCRAPERS, cache=cache)
        leads = cli.asyncio.run(
            pipeline.run(sources, query, args.limit, on_lead=on_lead,
                         on_source_status=on_status, limit_per_source=per_source_limit)
        )
    else:
        if per_source_limit:
            args.limit = per_source_limit
        all_leads, _ = cli.scrape_sources(
            sources, args, cache, on_lead=on_lead, on_source_status=on_status, quiet=True
        )
        leads, _dups = Deduplicator().deduplicate(all_leads)

    SessionCache.set(cache_key, query, leads)
    return leads


def _run_scrape(config: dict, sources: list[str], state: ScrapingState) -> dict:
    """Run the scrape for every query (reusing CLI orchestration) and return stats."""
    base = _namespace(config, sources, config.get("query", ""))
    settings.LOCATION = (base.location or "").strip()
    settings.DATEAS_TYPE = base.dateas_type
    settings.DATEAS_LOOKUP = base.dateas_lookup
    settings.ML_OFFICIAL_ONLY = base.ml_official_only
    settings.EMAIL_SCRAPING_ENABLED = cli.should_email_scrape(
        sources, base.limit, user_disabled=not config.get("email_scraping", False)
    )
    settings.WORKERS = config.get("workers", 0)
    settings.NETWORK_IDLE = config.get("network_idle", False)
    settings.BLOCK_RESOURCES = config.get("block_resources", True)

    queries = config.get("queries") or [config.get("query", "")]
    multi = len(queries) > 1
    cache = cli.ScrapingCache()
    start = time.time()

    combined: list = []
    per_query: list[tuple[str, int]] = []
    for index, query in enumerate(queries, 1):
        state.set_query(query, index, len(queries))
        leads = _scrape_one(query, config, sources, cache, state)
        if multi:
            for lead in leads:
                lead.raw_data.setdefault("query", query)
        per_query.append((query, len(leads)))
        combined.extend(leads)

    if multi:
        unique_leads, cross_dups = Deduplicator().deduplicate(combined)
    else:
        unique_leads, cross_dups = combined, 0

    by_source = dict(Counter(l.source for l in unique_leads))
    unique = len(unique_leads)
    filtered, _label = cli.apply_filters(unique_leads, base)

    filename = build_output_filename(config.get("query", ""), base.output)
    output_path = cli.EXPORTERS[base.output]().export(filtered, filename) or ""

    elapsed_s = time.time() - start
    mins, secs = divmod(int(elapsed_s), 60)
    workers = settings.WORKERS or detect_hardware().recommended_workers
    return {
        "total": len(filtered),
        "unique": unique,
        "duplicates": cross_dups,
        "with_email": sum(1 for l in filtered if l.email),
        "with_phone": sum(1 for l in filtered if l.phone),
        "with_website": sum(1 for l in filtered if l.website),
        "elapsed": f"{mins}m {secs:02d}s",
        "output_path": output_path,
        "per_query": per_query,
        "by_source": by_source,
        "leads": filtered,
        # Performance metrics.
        "workers": workers,
        "parallel_sources": min(len(sources), 5),
        "leads_per_sec": round(len(filtered) / elapsed_s, 2) if elapsed_s > 0 else 0.0,
        "network_idle": settings.NETWORK_IDLE,
        "block_resources": settings.BLOCK_RESOURCES,
    }


def _emergency_save(state: ScrapingState, filename: str, _guard: dict) -> None:
    """Export whatever leads have been collected so far (Ctrl+C / window close).

    Args:
        state: Shared scraping state holding the leads found so far.
        filename: Output filename to write the rescue workbook to.
        _guard: One-shot guard dict so the save runs at most once per session.
    """
    if _guard.get("saved") or not state or not state.leads:
        return
    _guard["saved"] = True
    try:
        exporter = JSONExporter() if filename.endswith(".json") else CSVExporter()
        result = exporter.export(list(state.leads), filename)
        if result:
            sys.stdout.write(
                f"\n{ANSI_GREEN}[ScrapBro] Guardado de emergencia: "
                f"{len(state.leads)} leads en {result}{ANSI_RESET}\n"
            )
            sys.stdout.flush()
    except Exception as exc:  # noqa: BLE001 - last-resort save must never raise
        logger.error("Emergency save failed: %s", exc)


# Only one emergency-save handler is ever registered with atexit; each new search
# replaces the previous one so handlers never accumulate across the session loop.
_current_emergency_handler = None


def _register_emergency_save(state: ScrapingState, rescue_name: str, guard: dict) -> None:
    """Register the emergency-save atexit handler, replacing any previous one."""
    global _current_emergency_handler
    if _current_emergency_handler is not None:
        try:
            atexit.unregister(_current_emergency_handler)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass

    def handler() -> None:
        _emergency_save(state, rescue_name, guard)

    _current_emergency_handler = handler
    atexit.register(handler)


def _open_folder(path: str) -> None:
    """Open the output folder in the OS file explorer (best-effort)."""
    folder = str(Path(path).resolve().parent)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", folder])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception as exc:
        logger.warning("Could not open folder %s: %s", folder, exc)


def _session(lang: str) -> None:
    """Run repeated search sessions until the user quits."""
    while True:
        sources = menus.select_sources(lang)
        if sources == QUIT or not sources:
            return
        menus.setup_cookies(sources, lang)  # optional; never blocks the flow

        config = menus.configure_search(sources, lang)
        if config == QUIT:
            return
        if config is None:
            continue  # Esc / back -> return to source selection
        if not config.get("queries"):
            return

        action = menus.confirm(config, sources, lang)
        if action == "quit":
            return
        if action == "back":
            continue

        state = ScrapingState()
        handler = progress.StateLogHandler(state)
        logging.getLogger().addHandler(handler)

        # Register an emergency save so Ctrl+C / window close never loses leads.
        rescue_name = build_output_filename(config.get("query", ""), config.get("output", "csv"))
        guard = {"saved": False}
        prev_sigint = signal.getsignal(signal.SIGINT)

        def _on_sigint(signum, frame) -> None:
            _emergency_save(state, rescue_name, guard)
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, _on_sigint)
        _register_emergency_save(state, rescue_name, guard)
        try:
            stats = progress.run_with_progress(
                lambda: _run_scrape(config, sources, state), state, sources, lang,
                query=config.get("query", ""),
            )
        except KeyboardInterrupt:
            _emergency_save(state, rescue_name, guard)
            return
        except Exception as exc:
            logger.error("Scrape failed: %s", exc, exc_info=True)
            sys.stdout.write(f"{ANSI_RED}Scrape failed: {exc}{ANSI_RESET}\n")
            sys.stdout.flush()
            return
        finally:
            # The real run already wrote the full file; skip the rescue save.
            guard["saved"] = True
            signal.signal(signal.SIGINT, prev_sigint)
            logging.getLogger().removeHandler(handler)

        progress.print_results(stats, lang)
        choice = menus.summary_actions(lang)
        if choice == "open":
            _open_folder(stats["output_path"])
            return
        if choice != "new":
            return


def main() -> None:
    """Entry point for the ScrapBro interactive TUI.

    Flow: language → spider + banner → (sources → cookies → config → confirm →
    live scrape → results table → next action) repeated → farewell. Falls back to
    a CLI hint when the terminal cannot host the interactive UI.
    """
    cli.configure_logging()
    _silence_terminal_logging()
    SessionCache.clear()
    menus.SessionMemory.reset()
    menus.init_windows_compat()

    if not sys.stdout.isatty() or not sys.stdin.isatty():
        sys.stdout.write(
            f"{ANSI_GREEN}ScrapBro TUI needs an interactive terminal.\n"
            f"Use the CLI instead:  python main.py --help{ANSI_RESET}\n"
        )
        sys.stdout.flush()
        return

    try:
        lang = menus.select_language()
        if lang is None:
            # Q / Esc on the first screen quits cleanly instead of defaulting.
            progress.farewell(menus.SessionMemory.last_language)
            return
        menus.SessionMemory.remember(language=lang)
        animate_spider()
        print_banner(lang)
        time.sleep(0.5)
        _session(lang)
        progress.farewell(lang)
    except (KeyboardInterrupt, EOFError):
        sys.stdout.write(f"\n{ANSI_GREEN}Bye.{ANSI_RESET}\n")
        sys.stdout.flush()
    except Exception as exc:  # noqa: BLE001 - last-resort graceful exit
        logger.error("TUI error: %s", exc, exc_info=True)
        sys.stdout.write(
            f"{ANSI_RED}TUI error: {exc}{ANSI_RESET}\n"
            f"{ANSI_GREEN}Fall back to the CLI:  python main.py --help{ANSI_RESET}\n"
        )
        sys.stdout.flush()


if __name__ == "__main__":
    main()
