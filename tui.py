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
import logging
import subprocess
import sys
import time
from pathlib import Path

import main as cli
from config.settings import settings
from pipeline.deduplicator import Deduplicator
from ui import menus, progress
from ui.banner import print_banner
from ui.spider import animate_spider
from ui.theme import ANSI_GREEN, ANSI_RED, ANSI_RESET

logger = logging.getLogger(__name__)


def _namespace(config: dict, sources: list[str]) -> argparse.Namespace:
    """Build a CLI-equivalent Namespace from the TUI config."""
    return argparse.Namespace(
        source=",".join(sources),
        query=config["query"],
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


def _run_scrape(config: dict, sources: list[str]) -> dict:
    """Run the scrape (reusing the CLI orchestration) and return result stats."""
    args = _namespace(config, sources)
    settings.LOCATION = (args.location or "").strip()
    settings.DATEAS_TYPE = args.dateas_type
    settings.DATEAS_LOOKUP = args.dateas_lookup
    settings.ML_OFFICIAL_ONLY = args.ml_official_only

    cache = cli.ScrapingCache()
    start = time.time()

    if len(sources) > 1:
        pipeline = cli.AsyncScrapingPipeline(registry=cli.SCRAPERS, cache=cache)
        leads = cli.asyncio.run(pipeline.run(sources, args.query, args.limit))
        duplicates = pipeline.duplicates_removed
    else:
        all_leads, _ = cli.scrape_sources(sources, args, cache)
        leads, duplicates = Deduplicator().deduplicate(all_leads)

    unique = len(leads)
    leads, _label = cli.apply_filters(leads, args)

    filename = cli.build_filename("_".join(sources), args.query, args.output)
    output_path = cli.EXPORTERS[args.output]().export(leads, filename)

    mins, secs = divmod(int(time.time() - start), 60)
    return {
        "total": len(leads),
        "unique": unique,
        "duplicates": duplicates,
        "with_email": sum(1 for l in leads if l.email),
        "with_phone": sum(1 for l in leads if l.phone),
        "with_website": sum(1 for l in leads if l.website),
        "elapsed": f"{mins}m {secs:02d}s",
        "output_path": output_path,
    }


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
        if not sources:
            return
        menus.setup_cookies(sources, lang)
        config = menus.configure_search(sources, lang)
        if config is None or not config.get("query"):
            return

        action = menus.confirm(config, sources, lang)
        if action == "quit":
            return
        if action == "back":
            continue

        try:
            stats = progress.run_with_progress(
                lambda: _run_scrape(config, sources), sources, lang
            )
        except Exception as exc:
            logger.error("Scrape failed: %s", exc, exc_info=True)
            sys.stdout.write(f"{ANSI_RED}Scrape failed: {exc}{ANSI_RESET}\n")
            sys.stdout.flush()
            return

        choice = menus.summary_actions(progress.render_summary(stats, lang), lang)
        if choice == "open":
            _open_folder(stats["output_path"])
            return
        if choice != "new":
            return


def main() -> None:
    """Entry point for the ScrapBro interactive TUI.

    Flow: language → spider + banner → (sources → cookies → config → confirm →
    scrape with live progress → results) repeated → farewell. Falls back to a CLI
    hint when the terminal cannot host the interactive UI.
    """
    cli.configure_logging()
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
