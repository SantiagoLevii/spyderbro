"""Live progress display and result summary for the ScrapBro TUI.

The existing scrapers return their leads in a single batch rather than streaming
per-lead events, so the in-progress view is an animated Matrix-green status box
shown while the scrape runs on a background thread; the final summary box is
rendered from the real result stats.
"""
import sys
import threading
import time
from collections.abc import Callable

from ui.theme import ANSI_GREEN, ANSI_GREEN_BRIGHT, ANSI_RESET

_SPINNER = "|/-\\"
_BOX_WIDTH = 56


def _line(text: str = "") -> str:
    """Format one bordered box line padded to the box width."""
    return f"  ║  {text:<{_BOX_WIDTH - 8}}║"


def run_with_progress(
    scrape_callable: Callable[[], object], sources: list[str], lang: str
) -> object:
    """Run ``scrape_callable`` on a worker thread while animating a status box.

    Args:
        scrape_callable: Zero-arg callable that performs the scrape and returns
            its result.
        sources: Selected source keys (shown in the box).
        lang: UI language (unused beyond labels; reserved).

    Returns:
        Whatever ``scrape_callable`` returns. Exceptions raised by it are
        re-raised after the animation stops.
    """
    result: dict = {}

    def worker() -> None:
        try:
            result["value"] = scrape_callable()
        except BaseException as exc:  # noqa: BLE001 - surfaced to caller below
            result["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    start = time.time()
    thread.start()

    if not sys.stdout.isatty():
        thread.join()
    else:
        sys.stdout.write("\033[?25l")
        spin = 0
        while thread.is_alive():
            elapsed = time.time() - start
            mins, secs = divmod(int(elapsed), 60)
            bar = _SPINNER[spin % len(_SPINNER)]
            sys.stdout.write("\033[2J\033[H" + ANSI_GREEN)
            sys.stdout.write("  ╔" + "═" * (_BOX_WIDTH - 4) + "╗\n")
            sys.stdout.write(_line(f"{ANSI_GREEN_BRIGHT}ScrapBro — scraping {bar}{ANSI_GREEN}") + "\n")
            sys.stdout.write("  ╠" + "═" * (_BOX_WIDTH - 4) + "╣\n")
            sys.stdout.write(_line(f"Sources: {', '.join(sources)[:40]}") + "\n")
            sys.stdout.write(_line(f"Elapsed: {mins}m {secs:02d}s") + "\n")
            sys.stdout.write(_line("Working... (this can take a few minutes)") + "\n")
            sys.stdout.write("  ╚" + "═" * (_BOX_WIDTH - 4) + "╝" + ANSI_RESET + "\n")
            sys.stdout.flush()
            spin += 1
            time.sleep(0.15)
        sys.stdout.write("\033[?25h" + ANSI_RESET)
        sys.stdout.flush()

    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def render_summary(stats: dict, lang: str) -> str:
    """Build the final results summary text for the summary dialog.

    Args:
        stats: Result statistics (see tui._run_scrape).
        lang: UI language (reserved).

    Returns:
        Multi-line summary string.
    """
    total = stats.get("total", 0)

    def pct(part: int) -> str:
        return f"{round(part * 100 / total)}%" if total else "0%"

    lines = [
        "RESULTS",
        f"  Total leads:    {total}",
        f"  Unique:         {stats.get('unique', total)} "
        f"({stats.get('duplicates', 0)} duplicates removed)",
        f"  With email:     {stats.get('with_email', 0)} ({pct(stats.get('with_email', 0))})",
        f"  With phone:     {stats.get('with_phone', 0)} ({pct(stats.get('with_phone', 0))})",
        f"  With website:   {stats.get('with_website', 0)} ({pct(stats.get('with_website', 0))})",
        "",
        "PERFORMANCE",
        f"  Time elapsed:   {stats.get('elapsed', '0s')}",
        "",
        "OUTPUT",
        f"  -> {stats.get('output_path', '')}",
    ]
    return "\n".join(lines)


def farewell(lang: str) -> None:
    """Print a typewriter goodbye that clears itself after ~1.5s."""
    message = "Thanks for using ScrapBro 🕷  Happy hunting."
    if not sys.stdout.isatty():
        sys.stdout.write(message + "\n")
        sys.stdout.flush()
        return
    sys.stdout.write("\n  " + ANSI_GREEN_BRIGHT)
    for char in message:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(0.01)
    sys.stdout.write(ANSI_RESET)
    sys.stdout.flush()
    time.sleep(1.5)
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
