"""Live progress, shared scraping state, results table and summary for the TUI.

The scrape runs on a background thread; the UI thread reads a thread-safe
:class:`ScrapingState` ~5×/s and redraws a Matrix-green status box (per-source
bars, a live feed of the latest leads, an elapsed timer and a non-alarming
errors panel). When the scrape finishes, the real stats are rendered as a
summary box, a per-source breakdown (with a Dateas note when relevant) and an
ASCII results table whose columns adapt to the data actually present.
"""
import logging
import sys
import threading
import time
from collections.abc import Callable

from models.lead import Lead
from utils.browser_config import BLOCKED_RESOURCE_TYPES
from ui.theme import (
    ANSI_GREEN,
    ANSI_GREEN_BRIGHT,
    ANSI_GREEN_DIM,
    ANSI_RED,
    ANSI_RESET,
    ANSI_YELLOW,
)

logger = logging.getLogger(__name__)

_BOX_WIDTH = 66
_BAR_WIDTH = 16
_FEED_ROWS = 8
_MAX_ERRORS = 3
_EMPTY = "—"

# Statuses that mean a source has stopped working (used for the X/Y counter).
_TERMINAL_STATUSES = {"done", "blocked", "timeout", "partial", "error"}

# After this many seconds the live box shows a "running long" warning.
SESSION_WARNING_SECONDS = 600


# --- Shared state -------------------------------------------------------------

class ScrapingState:
    """Thread-safe state shared between the scraping thread and the UI thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.leads: list[Lead] = []
        self.source_progress: dict[str, tuple[int, int]] = {}  # source -> (found, target)
        self.source_status: dict[str, str] = {}                # source -> starting|working|done|error
        self.current_query: str = ""
        self.query_index: int = 0
        self.total_queries: int = 0
        self.elapsed_seconds: int = 0
        self.errors: list[str] = []
        self.done: bool = False

    def add_lead(self, lead: Lead, source: str) -> None:
        """Thread-safe append of a found lead (shown immediately in the feed)."""
        with self._lock:
            self.leads.append(lead)
            found, target = self.source_progress.get(source, (0, 0))
            self.source_progress[source] = (found + 1, target)

    def set_source_status(self, source: str, status: str, target: int = 0) -> None:
        """Thread-safe update of a source's status and (optionally) its target."""
        with self._lock:
            self.source_status[source] = status
            found, current_target = self.source_progress.get(source, (0, 0))
            self.source_progress[source] = (found, target if target > 0 else current_target)

    def set_query(self, query: str, index: int, total: int = 0) -> None:
        """Thread-safe update of the currently running query."""
        with self._lock:
            self.current_query = query
            self.query_index = index
            if total:
                self.total_queries = total

    def add_error(self, error: str) -> None:
        """Buffer a short, non-alarming notice (keeps only the last few)."""
        with self._lock:
            self.errors.append(str(error)[:80])
            if len(self.errors) > _MAX_ERRORS:
                self.errors.pop(0)

    def mark_done(self) -> None:
        with self._lock:
            self.done = True

    def snapshot(self) -> dict:
        """Return a consistent copy of the state for rendering."""
        with self._lock:
            return {
                "leads": list(self.leads),
                "source_progress": dict(self.source_progress),
                "source_status": dict(self.source_status),
                "current_query": self.current_query,
                "query_index": self.query_index,
                "total_queries": self.total_queries,
                "errors": list(self.errors),
                "done": self.done,
            }


class StateLogHandler(logging.Handler):
    """Routes WARNING+ log records into a :class:`ScrapingState` errors buffer."""

    def __init__(self, state: ScrapingState) -> None:
        super().__init__(level=logging.WARNING)
        self._state = state

    def emit(self, record: logging.LogRecord) -> None:
        try:
            name = record.name.split(".")[-1]
            self._state.add_error(f"{name}: {record.getMessage()}")
        except Exception:  # noqa: BLE001 - logging must never raise
            pass


# --- ANSI helpers -------------------------------------------------------------

def _strip_len(text: str) -> int:
    """Length of a string ignoring ANSI escape sequences."""
    out = 0
    i = 0
    while i < len(text):
        if text[i] == "\033":
            while i < len(text) and text[i] != "m":
                i += 1
            i += 1
            continue
        out += 1
        i += 1
    return out


def _line(content: str = "", inner: int = _BOX_WIDTH - 6) -> str:
    """Format one bordered box line padded (ANSI-aware) to the inner width."""
    pad = max(0, inner - _strip_len(content))
    return f"  {ANSI_GREEN}║ {content}{' ' * pad} ║{ANSI_RESET}"


def _top() -> str:
    return f"  {ANSI_GREEN}╔{'═' * (_BOX_WIDTH - 4)}╗{ANSI_RESET}"


def _mid() -> str:
    return f"  {ANSI_GREEN}╠{'═' * (_BOX_WIDTH - 4)}╣{ANSI_RESET}"


def _bottom() -> str:
    return f"  {ANSI_GREEN}╚{'═' * (_BOX_WIDTH - 4)}╝{ANSI_RESET}"


def _bar(found: int, target: int) -> str:
    """Render a fixed-width progress bar; indeterminate when target unknown."""
    if target <= 0:
        filled = min(_BAR_WIDTH, found)
    else:
        filled = min(_BAR_WIDTH, round(_BAR_WIDTH * found / target))
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def _status_label(status: str) -> str:
    return {
        "starting": "starting...",
        "working": "working...",
        "done": f"{ANSI_GREEN_BRIGHT}✓ done{ANSI_GREEN}",
        "blocked": f"{ANSI_RED}✗ blocked{ANSI_GREEN}",
        "timeout": f"{ANSI_RED}✗ timeout{ANSI_GREEN}",
        "partial": f"{ANSI_YELLOW}~ partial{ANSI_GREEN}",
        "error": f"{ANSI_YELLOW}✗ error{ANSI_GREEN}",
    }.get(status, status)


def render_live(snap: dict, elapsed: int, lang: str) -> str:
    """Build the full live progress box from a state snapshot."""
    es = lang == "es"
    mins, secs = divmod(elapsed, 60)
    title = "Scraping..." if es else "Scraping..."
    elapsed_label = f"{mins}m {secs:02d}s {'transcurrido' if es else 'elapsed'}"

    out = [_top(),
           _line(f"{ANSI_GREEN_BRIGHT}ScrapBro — {title}{ANSI_GREEN}"
                 f"{' ' * max(0, 30 - len(title))}{elapsed_label}"),
           _mid()]

    progress = snap["source_progress"]
    status = snap["source_status"]
    qi, qn = snap["query_index"], snap["total_queries"]

    # Global progress line: completed sources and current query position.
    total_sources = len(status)
    done_sources = sum(1 for s in status.values() if s in _TERMINAL_STATUSES)
    sources_word = "Fuentes" if es else "Sources"
    completed_word = "completadas" if es else "done"
    global_line = f"{sources_word}: {done_sources}/{total_sources} {completed_word}"
    if qn:
        global_line += f"  |  Query {qi}/{qn}"
    out.append(_line(global_line))
    out.append(_line())

    if snap["current_query"]:
        qpos = f"{qi}/{qn}" if qn else f"{qi}"
        out.append(_line(f"Query {qpos}: \"{snap['current_query'][:44]}\""))
        out.append(_line())
    # Show active sources and any that produced leads (cap at 8 rows).
    shown = [s for s in status if status[s] != "done" or progress.get(s, (0, 0))[0] > 0]
    shown = (shown or list(status))[:8]
    for source in shown:
        found, target = progress.get(source, (0, 0))
        target_txt = f"{found}/{target}" if target else f"{found}"
        out.append(_line(
            f"{source[:14]:<14} {ANSI_GREEN}[{_bar(found, target)}] {target_txt:>7}  "
            f"{_status_label(status.get(source, ''))}"
        ))

    out.append(_line())
    out.append(_line(f"{ANSI_GREEN_BRIGHT}{'LEADS ENCONTRADOS' if es else 'LEADS FOUND'}: "
                     f"{len(snap['leads'])}{ANSI_GREEN}"))
    out.append(_line(f"{ANSI_GREEN_DIM}{'─' * (_BOX_WIDTH - 8)}{ANSI_GREEN}"))
    for lead in snap["leads"][-_FEED_ROWS:]:
        src = f"{ANSI_GREEN_DIM}[{lead.source[:12]:<12}]{ANSI_GREEN}"
        name = f"{lead.name[:28]:<28}"
        phone = (f"{ANSI_GREEN_BRIGHT}{lead.phone}{ANSI_GREEN}" if lead.phone
                 else f"{ANSI_GREEN_DIM}{_EMPTY}{ANSI_GREEN}")
        out.append(_line(f"{src} {name} {phone}"))

    if snap["errors"]:
        out.append(_line())
        out.append(_line(f"{ANSI_YELLOW}{'Avisos' if es else 'Notices'}:{ANSI_GREEN}"))
        for err in snap["errors"][-_MAX_ERRORS:]:
            out.append(_line(f"{ANSI_YELLOW}[!] {err[:54]}{ANSI_GREEN}"))

    if elapsed > SESSION_WARNING_SECONDS:
        mins_run = elapsed // 60
        out.append(_line())
        out.append(_line(f"{ANSI_YELLOW}⚠ {'El scraping lleva más de' if es else 'Scraping has run over'} "
                         f"{mins_run} {'minutos' if es else 'minutes'}.{ANSI_GREEN}"))
        out.append(_line(f"{ANSI_YELLOW}  {'Tenés' if es else 'You have'} {len(snap['leads'])} "
                         f"{'leads guardados — Ctrl+C guarda y termina' if es else 'leads — Ctrl+C saves and exits'}.{ANSI_GREEN}"))

    out.append(_line())
    out.append(_line(f"{ANSI_GREEN_DIM}[Ctrl+C] {'Interrumpir y guardar' if es else 'Interrupt & save'}{ANSI_GREEN}"))
    out.append(_bottom())
    return "\n".join(out)


def run_with_progress(
    scrape_callable: Callable[[], object], state: ScrapingState, sources: list[str],
    lang: str, query: str = "",
) -> object:
    """Run ``scrape_callable`` on a worker thread, live-rendering ``state``.

    Args:
        scrape_callable: Zero-arg callable performing the scrape; it must update
            ``state`` as it goes and return the final stats dict.
        state: Shared state populated by the scrape callbacks.
        sources: Selected sources (used to seed status rows).
        lang: UI language.
        query: Initial query label.

    Returns:
        Whatever ``scrape_callable`` returns. Exceptions are re-raised.
    """
    result: dict = {}
    for source in sources:
        state.set_source_status(source, "starting")
    if query:
        state.set_query(query, 1)

    def worker() -> None:
        try:
            result["value"] = scrape_callable()
        except BaseException as exc:  # noqa: BLE001 - surfaced to caller below
            result["error"] = exc
        finally:
            state.mark_done()

    thread = threading.Thread(target=worker, daemon=True)
    start = time.time()
    thread.start()

    if not sys.stdout.isatty():
        thread.join()
    else:
        sys.stdout.write("\033[?25l")
        while thread.is_alive():
            snap = state.snapshot()
            sys.stdout.write("\033[2J\033[H" + render_live(snap, int(time.time() - start), lang) + "\n")
            sys.stdout.flush()
            time.sleep(0.2)
        sys.stdout.write("\033[?25h" + ANSI_RESET)
        sys.stdout.flush()

    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


# --- Results table ------------------------------------------------------------

def _completeness(lead: Lead) -> int:
    """Score a lead by completeness (contact fields weighted highest)."""
    score = 0
    if lead.email:
        score += 3
    if lead.phone:
        score += 3
    if lead.website:
        score += 2
    if lead.address:
        score += 1
    if lead.rating:
        score += 1
    return score


def _host(url: str) -> str:
    if not url:
        return ""
    return url.split("//", 1)[-1].split("/", 1)[0].removeprefix("www.")


# Column registry: name -> (header, width, getter).
_COLUMNS: dict[str, tuple[str, int, Callable[[Lead], object]]] = {
    "name": ("Nombre", 26, lambda l: l.name),
    "phone": ("Teléfono", 18, lambda l: l.phone),
    "email": ("Email", 22, lambda l: l.email),
    "website": ("Web", 22, lambda l: _host(l.website)),
    "cuit": ("CUIT", 16, lambda l: (l.raw_data or {}).get("cuit", "")),
    "dni": ("DNI", 12, lambda l: (l.raw_data or {}).get("dni", "")),
    "locality": ("Localidad", 18, lambda l: (l.raw_data or {}).get("locality", "")),
    "source": ("Fuente", 12, lambda l: l.source),
}


def _detect_available_columns(leads: list[Lead]) -> list[str]:
    """Detect which optional columns actually have data, for a compact table.

    Always includes ``name`` and ``source``; adds phone/email/website and the
    Dateas columns (cuit/dni/locality) only when at least one lead has them.
    """
    columns = ["name"]
    checks = [
        ("phone", lambda l: l.phone),
        ("email", lambda l: l.email),
        ("website", lambda l: l.website),
        ("cuit", lambda l: (l.raw_data or {}).get("cuit")),
        ("dni", lambda l: (l.raw_data or {}).get("dni")),
        ("locality", lambda l: (l.raw_data or {}).get("locality")),
    ]
    for key, getter in checks:
        if any(getter(l) for l in leads):
            columns.append(key)
    columns.append("source")
    return columns


def _cell(value, width: int) -> str:
    """Format a table cell: truncate with '...', or dim '—' when empty."""
    text = str(value).strip() if value not in (None, "", 0, 0.0) else ""
    if not text:
        return f"{ANSI_GREEN_DIM}{_EMPTY:<{width}}{ANSI_GREEN}"
    if len(text) > width:
        text = text[: width - 3] + "..."
    return f"{text:<{width}}"


def render_results_table(leads: list[Lead], max_rows: int = 15) -> str:
    """Render an ASCII results table in Matrix green with data-adaptive columns.

    The table is always rendered when there are leads (even contact-less Dateas
    rows). The most complete leads come first; at most ``max_rows`` rows show,
    with a "+N more" note when there are more. Empty fields render as a dim '—'.

    Args:
        leads: Leads to display.
        max_rows: Maximum rows to render.

    Returns:
        The table as a printable string (empty string only when no leads).
    """
    if not leads:
        return ""

    keys = _detect_available_columns(leads)
    columns = [_COLUMNS[k] for k in keys]
    widths = [w for _, w, _ in columns]

    ordered = sorted(leads, key=_completeness, reverse=True)
    shown = ordered[:max_rows]

    line_top = "╔" + "╦".join("═" * (w + 2) for w in widths) + "╗"
    line_mid = "╠" + "╬".join("═" * (w + 2) for w in widths) + "╣"
    line_bot = "╚" + "╩".join("═" * (w + 2) for w in widths) + "╝"

    def row(cells: list[str]) -> str:
        body = "║".join(f" {c} " for c in cells)
        return f"{ANSI_GREEN}║{body}{ANSI_GREEN}║{ANSI_RESET}"

    out = [f"{ANSI_GREEN}{line_top}{ANSI_RESET}",
           row([f"{ANSI_GREEN_BRIGHT}{h:<{w}}{ANSI_GREEN}" for h, w, _ in columns]),
           f"{ANSI_GREEN}{line_mid}{ANSI_RESET}"]
    for lead in shown:
        out.append(row([_cell(getter(lead), w) for (_, w, getter) in columns]))
    out.append(f"{ANSI_GREEN}{line_bot}{ANSI_RESET}")
    if len(ordered) > len(shown):
        out.append(f"  {ANSI_GREEN_DIM}... +{len(ordered) - len(shown)} more in the Excel file{ANSI_RESET}")
    return "\n".join(out) + "\n"


# --- Final summary ------------------------------------------------------------

# Sources that are registries (no per-record phone/email/web).
_REGISTRY_SOURCES = {"dateas", "topdoctors_ar"}

# Localized labels for the final summary so nothing leaks English in ES mode.
SUMMARY_LABELS = {
    "en": {
        "results": "RESULTS",
        "total_leads": "Total leads",
        "unique": "Unique",
        "duplicates": "duplicates",
        "with_email": "With email",
        "with_phone": "With phone",
        "with_website": "With website",
    },
    "es": {
        "results": "RESULTADOS",
        "total_leads": "Total de leads",
        "unique": "Únicos",
        "duplicates": "duplicados",
        "with_email": "Con email",
        "with_phone": "Con teléfono",
        "with_website": "Con web",
    },
}


def render_summary(stats: dict, lang: str) -> str:
    """Build the final results summary text (plain, used inside the summary box)."""
    total = stats.get("total", 0)
    es = lang == "es"
    labels = SUMMARY_LABELS.get(lang, SUMMARY_LABELS["en"])

    def pct(part: int) -> str:
        return f"{round(part * 100 / total)}%" if total else "0%"

    lines = [
        labels["results"],
        f"  {labels['total_leads']}:   {total}",
        f"  {labels['unique']}:        {stats.get('unique', total)} "
        f"({stats.get('duplicates', 0)} {labels['duplicates']})",
        f"  {labels['with_email']}:     {stats.get('with_email', 0)} ({pct(stats.get('with_email', 0))})",
        f"  {labels['with_phone']}:  {stats.get('with_phone', 0)} ({pct(stats.get('with_phone', 0))})",
        f"  {labels['with_website']}:    {stats.get('with_website', 0)} ({pct(stats.get('with_website', 0))})",
    ]

    by_source = stats.get("by_source") or {}
    if by_source:
        lines.append("")
        lines.append("POR FUENTE" if es else "PER SOURCE")
        for source, count in sorted(by_source.items(), key=lambda kv: kv[1], reverse=True):
            note = ""
            if source in _REGISTRY_SOURCES:
                note = "  (solo registro, sin contacto)" if es else "  (registry only, no contact)"
            lines.append(f"  {source:<18} -> {count} leads{note}")

    per_query = stats.get("per_query") or []
    if len(per_query) > 1:
        lines.append("")
        lines.append("POR BÚSQUEDA" if es else "PER SEARCH")
        for i, (query, count) in enumerate(per_query, 1):
            lines.append(f"  [{i}] \"{query[:36]}\"  ->  {count} leads")

    if "workers" in stats:
        mode = ("Rápido" if es else "Fast") if not stats.get("network_idle") else ("Completo" if es else "Complete")
        block = ("bloqueo activo" if es else "blocking on") if stats.get("block_resources") else (
            "sin bloqueo" if es else "no blocking")
        blocked_est = stats.get("total", 0) * len(BLOCKED_RESOURCE_TYPES)
        lines += [
            "",
            "RENDIMIENTO" if es else "PERFORMANCE",
            f"  {'Tiempo total' if es else 'Total time'}:      {stats.get('elapsed', '0s')}",
            f"  {'Workers usados' if es else 'Workers used'}:     {stats.get('workers', 0)}",
            f"  {'Fuentes paralelas' if es else 'Parallel sources'}: {stats.get('parallel_sources', 1)}",
            f"  {'Velocidad' if es else 'Speed'}:          {stats.get('leads_per_sec', 0)} leads/s",
            f"  {'Recursos bloqueados' if es else 'Resources blocked'}: ~{blocked_est} "
            f"({'imágenes/CSS/fonts' if es else 'images/CSS/fonts'})",
            f"  {'Modo' if es else 'Mode'}:             {mode} + {block}",
        ]
    lines += [
        "",
        f"{'TIEMPO' if es else 'TIME'}: {stats.get('elapsed', '0s')}",
        f"{'ARCHIVO' if es else 'OUTPUT'}: {stats.get('output_path', '')}",
    ]
    return "\n".join(lines)


def _dateas_warning(leads: list[Lead], lang: str) -> str:
    """Return a yellow note when registry sources dominate the result set."""
    if not leads:
        return ""
    registry = sum(1 for l in leads if l.source in _REGISTRY_SOURCES)
    if registry < len(leads) * 0.5:
        return ""
    es = lang == "es"
    msg = ("⚠ La mayoría de los leads son de registros fiscales (Dateas/Top Doctors): "
           "nombre y datos fiscales, sin teléfono/email directo. Para leads con "
           "contacto combiná con Google Maps o Páginas Amarillas."
           if es else
           "⚠ Most leads come from registries (Dateas/Top Doctors): name and tax data "
           "only, no direct phone/email. For contactable leads combine with Google "
           "Maps or Páginas Amarillas.")
    return f"  {ANSI_YELLOW}{msg}{ANSI_RESET}\n"


def print_results(stats: dict, lang: str) -> None:
    """Print the summary box, per-source breakdown, warnings and results table."""
    output_name = stats.get("output_path", "")
    short = output_name.replace("\\", "/").split("/")[-1]
    leads = stats.get("leads", [])
    logger.debug("Final summary received %d leads", len(leads))

    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
    sys.stdout.write(f"{ANSI_GREEN}{render_summary(stats, lang)}{ANSI_RESET}\n\n")
    sys.stdout.write(_dateas_warning(leads, lang))
    title = f"Top 15 — {short}" if short else ("Resultados" if lang == "es" else "Results")
    sys.stdout.write(f"  {ANSI_GREEN_BRIGHT}{title}{ANSI_RESET}\n")
    sys.stdout.write(render_results_table(leads))
    total = stats.get("total", 0)
    note = (f"  {total} leads totales  |  {output_name}" if lang == "es"
            else f"  {total} total leads  |  {output_name}")
    sys.stdout.write(f"{ANSI_GREEN_DIM}{note}{ANSI_RESET}\n\n")
    sys.stdout.flush()


def farewell(lang: str) -> None:
    """Print a typewriter goodbye that clears itself after ~1.5s."""
    message = ("Gracias por usar ScrapBro 🕷  Feliz caza." if lang == "es"
               else "Thanks for using ScrapBro 🕷  Happy hunting.")
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
