"""Interactive menus for the ScrapBro TUI.

The interactive steps are built on raw ``prompt_toolkit.Application`` instances
(not the high-level shortcuts) so the Matrix-green theme is enforced everywhere —
the shortcuts ship their own light style that overrides ours. Controls are
uniform across every menu:

    [Space] toggle / select      [Enter] confirm / OK
    [Esc]   back to previous      [Q] quit (with confirmation)
    [A]     select all (sources)  [N] deselect all (sources)
    [↑][↓] navigate

Pure helpers (source catalog, cookie validation, config defaults, summary
builder, Windows compat) are kept side-effect free so they can be unit tested
without a TTY.
"""
import json
import logging
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.widgets import Frame, TextArea

from ui.theme import SCRAPBRO_STYLE
from utils import cookie_detector
from utils.hardware import detect_hardware

logger = logging.getLogger(__name__)

COOKIE_DIR = Path(".cookies")

# Sentinel returned by every interactive menu when the user presses Q to quit.
QUIT = "__quit__"
_NOTHING = object()

# (key, label_en, label_es, needs_cookie, group)
SOURCE_CATALOG: list[tuple[str, str, str, bool, str]] = [
    ("google_maps", "Google Maps — businesses worldwide", "Google Maps — negocios", False, "global"),
    ("dorks", "Dorks (Serper/DDG) — web search for emails", "Dorks — búsqueda web de emails", False, "global"),
    ("twitter", "Twitter / X — public profiles", "Twitter / X — perfiles públicos", False, "global"),
    ("linkedin", "LinkedIn — companies & people", "LinkedIn — empresas y personas", True, "global"),
    ("instagram", "Instagram — business profiles", "Instagram — perfiles de negocio", True, "global"),
    ("facebook", "Facebook Pages — business pages", "Facebook — páginas de negocio", True, "global"),
    ("paginas_amarillas", "Páginas Amarillas — negocios por rubro", "Páginas Amarillas — negocios por rubro", False, "argentina"),
    ("dateas", "Dateas — personas y empresas (DNI/CUIT)", "Dateas — personas y empresas (DNI/CUIT)", False, "argentina"),
    ("zonaprop", "Zonaprop — agentes inmobiliarios", "Zonaprop — agentes inmobiliarios", False, "argentina"),
    ("argenprop", "Argenprop — agentes inmobiliarios", "Argenprop — agentes inmobiliarios", False, "argentina"),
    ("tripadvisor_ar", "TripAdvisor AR — restaurantes", "TripAdvisor AR — restaurantes", False, "argentina"),
    ("topdoctors_ar", "Top Doctors AR — médicos y especialistas", "Top Doctors AR — médicos", False, "argentina"),
    ("abogados", "Abogados.com.ar — estudios jurídicos", "Abogados.com.ar — estudios jurídicos", False, "argentina"),
    ("mercadolibre", "MercadoLibre — vendedores", "MercadoLibre — vendedores", True, "argentina"),
    ("clutch", "Clutch — digital agencies  ⓘ best for global searches",
     "Clutch — agencias digitales  ⓘ mejor para búsquedas globales", False, "argentina"),
]

ALL_SOURCE_KEYS: list[str] = [row[0] for row in SOURCE_CATALOG]
COOKIE_SOURCES: set[str] = {row[0] for row in SOURCE_CATALOG if row[3]}


class SessionMemory:
    """Remembers the last search configuration within the current TUI session.

    Used to pre-fill defaults when the user starts a new search without quitting,
    so repeated runs don't re-type the same sources / limit / performance options.
    """

    last_sources: list[str] = []
    last_limit: int = 50
    last_workers: int = 0
    last_network_idle: bool = False
    last_block_resources: bool = True
    last_language: str = "es"

    @classmethod
    def remember(cls, sources: list[str] | None = None, config: dict | None = None,
                 language: str | None = None) -> None:
        """Store the parts of a finished configuration worth pre-loading next time."""
        if sources is not None:
            cls.last_sources = list(sources)
        if config:
            cls.last_limit = config.get("limit", cls.last_limit)
            cls.last_workers = config.get("workers", cls.last_workers)
            cls.last_network_idle = config.get("network_idle", cls.last_network_idle)
            cls.last_block_resources = config.get("block_resources", cls.last_block_resources)
        if language is not None:
            cls.last_language = language

    @classmethod
    def reset(cls) -> None:
        """Clear remembered values (called at TUI startup)."""
        cls.last_sources = []
        cls.last_limit = 50
        cls.last_workers = 0
        cls.last_network_idle = False
        cls.last_block_resources = True
        cls.last_language = "es"

_T = {
    "en": {
        "lang_title": "ScrapBro — language",
        "lang_intro": "Choose the interface language.",
        "sources_title": "Select sources",
        "sources_help": "[Space] toggle  [A] all  [N] none  [Enter] confirm  [Esc] back  [Q] quit",
        "nav_help": "[↑↓] navigate  [Space] select  [Enter] OK  [Esc] back  [Q] quit",
        "query_title": "Search configuration",
        "query_intro": "What do you want to search?",
        "query_multi": "Separate multiple searches with  --",
        "query_example": "Example: inmobiliaria lujan -- santiago gomez",
        "query_detected": "Detected searches:",
        "location": "Location (optional — empty for global):",
        "limit_title": "Maximum leads",
        "limit_intro": "How many leads per search? (1-1000)",
        "limit_mode_intro": "How should the limit be applied across sources?",
        "limit_mode_total": "Total — split across all sources (deduped on merge)",
        "limit_mode_per": "Per source — each source fetches this many leads",
        "output_title": "Output format",
        "filters_title": "Filters (optional)",
        "filters_help": "[Space] toggle  [Enter] confirm  [Esc] back  [Q] quit",
        "dateas_title": "Dateas — search options",
        "dateas_search": "Search by:",
        "dateas_entity": "Entity type:",
        "cookie_title": "Cookie",
        "cookie_how": "How to get your cookie:",
        "cookie_steps": [
            "Option A — Cookie-Editor extension:",
            "  1. Install \"Cookie-Editor\" in Chrome/Firefox",
            "  2. Open the site logged in -> click it -> Export -> Copy",
            "Option B — Browser console:",
            "  1. Open the site logged in, press F12 -> Console",
            "  2. Paste the contents of: scripts/get_cookies.js",
            "  3. Copy the JSON it prints",
            "Then paste the JSON below and press [Enter].",
            "⚠ This is a session credential — it is saved locally with",
            "  restricted permissions. Never share it or commit it to git.",
        ],
        "cookie_paste": "Paste the cookie JSON:",
        "cookie_help": "[Enter] validate & continue   [Esc] skip",
        "cookie_method": "How do you want to set the cookie?",
        "cookie_auto": "Auto-detect from my browser (Chrome/Edge/Firefox)",
        "cookie_manual": "Enter manually (paste Cookie-Editor JSON)",
        "cookie_skip": "Skip — limited scraping without cookie",
        "cookie_searching": "Searching for an active session...",
        "cookie_use": "Use this session?",
        "cookie_use_yes": "Yes, use it",
        "cookie_use_no": "No, enter manually",
        "cookie_failed": "No active session detected in any browser.",
        "speed_title": "Speed",
        "speed_fast": "Fast — skip website email lookup",
        "speed_full": "Complete — look up emails (slower, ~2min extra/source)",
        "per_source": "Per source",
        "perf_title": "Performance",
        "perf_workers": "Workers to use:",
        "perf_auto": "Automatic (recommended: {n})",
        "perf_manual": "Manual",
        "perf_manual_n": "Number of workers (1-64):",
        "perf_load": "Load mode:",
        "perf_load_fast": "Fast — don't wait for extra resources",
        "perf_load_full": "Complete — wait for full page load (if data is missing)",
        "perf_block": "Block images and CSS:",
        "perf_block_yes": "Yes — faster (recommended)",
        "perf_block_no": "No — if the site needs CSS to render",
        "confirm_title": "Ready to scrape",
        "confirm_help": "[↑↓] choose  [Enter] OK",
        "start": "Start", "back": "Back", "quit": "Quit",
        "new_search": "New search", "open_folder": "Open output folder",
        "quit_confirm": "Quit? [S/N]",
        "needs_cookie": "needs cookie",
    },
    "es": {
        "lang_title": "ScrapBro — idioma",
        "lang_intro": "Elegí el idioma de la interfaz.",
        "sources_title": "Elegí las fuentes",
        "sources_help": "[Espacio] marcar  [A] todas  [N] ninguna  [Enter] confirmar  [Esc] volver  [Q] salir",
        "nav_help": "[↑↓] navegar  [Espacio] seleccionar  [Enter] OK  [Esc] volver  [Q] salir",
        "query_title": "Configuración de búsqueda",
        "query_intro": "¿Qué querés buscar?",
        "query_multi": "Podés separar múltiples búsquedas con  --",
        "query_example": "Ejemplo: inmobiliaria lujan -- santiago gomez",
        "query_detected": "Búsquedas detectadas:",
        "location": "Ubicación (opcional — vacío = global):",
        "limit_title": "Máximo de leads",
        "limit_intro": "¿Cuántos leads por búsqueda? (1-1000)",
        "limit_mode_intro": "¿Cómo se aplica el límite entre las fuentes?",
        "limit_mode_total": "Total — repartido entre todas las fuentes (dedup al unir)",
        "limit_mode_per": "Por fuente — cada fuente busca esta cantidad de leads",
        "output_title": "Formato de salida",
        "filters_title": "Filtros (opcional)",
        "filters_help": "[Espacio] marcar  [Enter] confirmar  [Esc] volver  [Q] salir",
        "dateas_title": "Dateas — opciones de búsqueda",
        "dateas_search": "Buscar por:",
        "dateas_entity": "Tipo de entidad:",
        "cookie_title": "Cookie",
        "cookie_how": "Cómo obtener tu cookie:",
        "cookie_steps": [
            "Opción A — Extensión Cookie-Editor:",
            "  1. Instalá \"Cookie-Editor\" en Chrome/Firefox",
            "  2. Abrí el sitio con sesión -> click -> Export -> Copy",
            "Opción B — Consola del navegador:",
            "  1. Abrí el sitio con sesión, F12 -> Console",
            "  2. Pegá el contenido de: scripts/get_cookies.js",
            "  3. Copiá el JSON que aparece",
            "Después pegá el JSON aquí abajo y presioná [Enter].",
            "⚠ Es una credencial de sesión — se guarda localmente con",
            "  permisos restringidos. No la compartas ni la subas a git.",
        ],
        "cookie_paste": "Pegá el JSON de cookies:",
        "cookie_help": "[Enter] validar y continuar   [Esc] saltar",
        "cookie_method": "¿Cómo querés configurar la cookie?",
        "cookie_auto": "Detectar automáticamente desde mi navegador (Chrome/Edge/Firefox)",
        "cookie_manual": "Ingresar manualmente (pegar JSON de Cookie-Editor)",
        "cookie_skip": "Omitir — scraping limitado sin cookie",
        "cookie_searching": "Buscando sesión activa...",
        "cookie_use": "¿Usar esta sesión?",
        "cookie_use_yes": "Sí, usarla",
        "cookie_use_no": "No, ingresar manualmente",
        "cookie_failed": "No se detectó sesión activa en ningún navegador.",
        "speed_title": "Velocidad",
        "speed_fast": "Rápido — sin buscar emails en sitios web",
        "speed_full": "Completo — buscar emails (más lento, ~2min extra por fuente)",
        "per_source": "Por fuente",
        "perf_title": "Configuración de rendimiento",
        "perf_workers": "Workers a usar:",
        "perf_auto": "Automático (recomendado: {n})",
        "perf_manual": "Manual",
        "perf_manual_n": "Cantidad de workers (1-64):",
        "perf_load": "Modo de carga:",
        "perf_load_fast": "Rápido — sin esperar recursos extra",
        "perf_load_full": "Completo — esperar carga total (si faltan datos)",
        "perf_block": "Bloquear imágenes y CSS:",
        "perf_block_yes": "Sí — más rápido (recomendado)",
        "perf_block_no": "No — si el sitio requiere CSS para renderizar",
        "confirm_title": "Listo para scrapear",
        "confirm_help": "[↑↓] elegir  [Enter] OK",
        "start": "Empezar", "back": "Volver", "quit": "Salir",
        "new_search": "Nueva búsqueda", "open_folder": "Abrir carpeta de salida",
        "quit_confirm": "¿Salir? [S/N]",
        "needs_cookie": "necesita cookie",
    },
}


def t(lang: str, key: str):
    """Translate a UI key for the given language (falls back to English)."""
    return _T.get(lang, _T["en"]).get(key, _T["en"].get(key, key))


def normalize_language(value: str) -> str:
    """Normalize any input to a supported language code ('en' or 'es')."""
    return "es" if str(value).strip().lower() in ("es", "español", "espanol", "spanish") else "en"


def init_windows_compat() -> bool:
    """Initialize colorama so ANSI codes render on Windows terminals.

    Returns:
        True if colorama was initialized, False if unavailable.
    """
    try:
        import colorama
        colorama.init(convert=True)
        return True
    except Exception as exc:  # pragma: no cover - colorama is a dependency
        logger.warning("colorama init failed: %s", exc)
        return False


# --- Cookie validation --------------------------------------------------------

def validate_cookie_json(raw: str) -> tuple[bool, str]:
    """Validate a pasted Cookie-Editor JSON string structurally.

    Args:
        raw: The raw JSON text the user pasted.

    Returns:
        (True, "✓ ...") when it parses as a non-empty list of cookie objects
        exposing at least one of name/value/domain; otherwise (False, "✗ ...")
        with a human-readable reason.
    """
    text = (raw or "").strip()
    if not text:
        return False, "✗ Empty input"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return False, f"✗ Invalid JSON: {exc}"
    cookies = data.get("cookies", data) if isinstance(data, dict) else data
    if isinstance(cookies, list) and cookies:
        first = cookies[0]
        if isinstance(first, dict) and any(k in first for k in ("name", "value", "domain")):
            return True, f"✓ Valid cookie — {len(cookies)} cookies found"
    return False, "✗ Does not look like a Cookie-Editor export"


def validate_cookie_file(path: str) -> tuple[bool, str]:
    """Validate a browser cookie export file (Cookie-Editor JSON) structurally.

    Retained for back-compat / scripting. Checks the file exists, is valid
    JSON, and is a non-empty list of cookie objects with at least a ``name``
    field.

    Args:
        path: Path to the cookie JSON file.

    Returns:
        Tuple of (is_valid, human-readable message).
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return False, "File not found"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return False, f"Invalid JSON: {exc}"
    cookies = data.get("cookies", data) if isinstance(data, dict) else data
    if not isinstance(cookies, list) or not cookies:
        return False, "Empty or not a cookie list"
    if not all(isinstance(c, dict) and c.get("name") for c in cookies):
        return False, "Malformed cookie entries"
    return True, f"Valid cookie file ({len(cookies)} cookies)"


# --- Config helpers -----------------------------------------------------------

def default_search_config() -> dict:
    """Return the default search configuration used by the TUI."""
    return {
        "query": "",
        "queries": [],
        "location": "",
        "limit": 50,
        "output": "csv",
        "filter_has_phone": False,
        "filter_has_email": False,
        "filter_has_website": False,
        "filter_has_cuit": False,
        "filter_min_rating": None,
        "dateas_type": "ambos",
        "dateas_lookup": "name",
        "email_scraping": False,
        "limit_per_source": 0,
        "workers": 0,
        "network_idle": False,
        "block_resources": True,
    }


def build_confirm_summary(config: dict, sources: list[str], lang: str) -> str:
    """Build the human-readable confirmation summary text.

    Args:
        config: Search configuration dict.
        sources: Selected source keys.
        lang: UI language.

    Returns:
        Multi-line summary string listing every configured field.
    """
    filters = [name for name, key in (
        ("phone", "filter_has_phone"), ("email", "filter_has_email"),
        ("website", "filter_has_website"), ("cuit", "filter_has_cuit"),
    ) if config.get(key)]
    if config.get("filter_min_rating") is not None:
        filters.append(f"rating>={config['filter_min_rating']}")

    queries = config.get("queries") or [config.get("query", "")]
    query_line = config.get("query", "")
    if len(queries) > 1:
        query_line = " | ".join(f"[{i}] {q}" for i, q in enumerate(queries, 1))

    from pipeline.async_pipeline import distribute_limit

    limit = config.get("limit", 50)
    per_source_limit = config.get("limit_per_source", 0)
    if per_source_limit:
        # Per-source mode: each source fetches per_source_limit leads, so the
        # real total is per_source_limit × number of sources.
        total_leads = per_source_limit * len(sources) if sources else per_source_limit
        limit_line = f"Limit:     {per_source_limit}/source  ({total_leads} total before dedup)"
    else:
        per_source = distribute_limit(limit, sources).get(sources[0], limit) if sources else limit
        limit_line = f"Limit:     {limit} total  (~{per_source}/source, deduped on merge)"
    speed = "full" if config.get("email_scraping") else "fast"

    es = lang == "es"
    filters_label = ", ".join(filters) if filters else ("ninguno" if es else "none")
    lines = [
        f"Sources:   {', '.join(sources)} ({len(sources)})",
        f"Query:     {query_line!r}",
        f"Location:  {config.get('location') or '-'}",
        limit_line,
        f"Output:    {config.get('output')}",
        f"Speed:     {speed}  (email lookup {'on' if speed == 'full' else 'off'})",
        f"Filters:   {filters_label}",
    ]
    if "dateas" in sources:
        lines.append(f"Dateas:    type={config.get('dateas_type')} lookup={config.get('dateas_lookup')}")
    if filters:
        lines.append("")
        lines.append("⚠ " + ("Los filtros activos pueden reducir significativamente los resultados."
                             if es else
                             "Active filters may significantly reduce the results."))
    return "\n".join(lines)


# --- prompt_toolkit application builders --------------------------------------

def _run_app(root, kb: KeyBindings, full_screen: bool = True) -> None:
    """Build and run a Matrix-themed application (full-screen by default)."""
    app = Application(
        layout=Layout(HSplit([root])),
        key_bindings=kb,
        style=SCRAPBRO_STYLE,
        full_screen=full_screen,
        color_depth=ColorDepth.DEPTH_24_BIT,
        mouse_support=False,
    )
    app.run()


def _quit_bindings(kb: KeyBindings, state: dict) -> None:
    """Attach the shared Q-quit-with-confirmation key bindings."""
    @kb.add("q")
    @kb.add("Q")
    def _(event) -> None:
        state["confirm_quit"] = True

    @kb.add("s")
    @kb.add("S")
    @kb.add("y")
    @kb.add("Y")
    def _(event) -> None:
        if state.get("confirm_quit"):
            state["result"] = QUIT
            event.app.exit()

    @kb.add("n")
    @kb.add("N")
    def _(event) -> None:
        if state.get("confirm_quit"):
            state["confirm_quit"] = False
        else:
            handler = state.get("on_n")
            if handler:
                handler()


def _footer(state: dict, lang: str, help_key: str) -> list:
    """Render the footer help line, or the quit confirmation when pending."""
    if state.get("confirm_quit"):
        return [("class:warning", "  " + t(lang, "quit_confirm"))]
    return [("class:help", "  " + t(lang, help_key))]


def _radiolist(
    title: str, intro: list[str], options: list[tuple], lang: str,
    default: int = 0, help_key: str = "nav_help", full_screen: bool = True,
):
    """Single-select menu. Returns the chosen value, None (Esc) or QUIT."""
    state = {"index": default if 0 <= default < len(options) else 0,
             "result": _NOTHING, "confirm_quit": False}
    kb = KeyBindings()

    @kb.add("up")
    def _(event) -> None:
        state["index"] = (state["index"] - 1) % len(options)

    @kb.add("down")
    def _(event) -> None:
        state["index"] = (state["index"] + 1) % len(options)

    @kb.add(" ")
    @kb.add("enter")
    def _(event) -> None:
        if state.get("confirm_quit"):
            return
        state["result"] = options[state["index"]][0]
        event.app.exit()

    @kb.add("escape")
    def _(event) -> None:
        state["result"] = None
        event.app.exit()

    _quit_bindings(kb, state)

    def render() -> list:
        frags: list = []
        for line in intro:
            frags.append(("class:intro", "  " + line + "\n"))
        if intro:
            frags.append(("", "\n"))
        for i, (_value, label) in enumerate(options):
            marker = "(*)" if i == state["index"] else "( )"
            if i == state["index"]:
                frags.append(("class:item.selected", f"  {marker} {label}  "))
            else:
                frags.append(("class:radio.off", f"  {marker} {label}"))
            frags.append(("", "\n"))
        frags.append(("", "\n"))
        frags.extend(_footer(state, lang, help_key))
        return frags

    root = Frame(Window(FormattedTextControl(render, focusable=True), always_hide_cursor=True),
                 title=title)
    _run_app(root, kb, full_screen=full_screen)
    result = state["result"]
    return None if result is _NOTHING else result


def _checkboxlist(
    title: str, intro: list[str], options: list[tuple], lang: str,
    preselected: set[int] | None = None, help_key: str = "sources_help",
    allow_all_none: bool = True,
):
    """Multi-select menu. Returns the chosen value list, None (Esc) or QUIT."""
    selected: set[int] = set(preselected or set())
    state = {"index": 0, "result": _NOTHING, "confirm_quit": False}
    kb = KeyBindings()

    @kb.add("up")
    def _(event) -> None:
        state["index"] = (state["index"] - 1) % len(options)

    @kb.add("down")
    def _(event) -> None:
        state["index"] = (state["index"] + 1) % len(options)

    @kb.add(" ")
    def _(event) -> None:
        if state.get("confirm_quit"):
            return
        i = state["index"]
        selected.discard(i) if i in selected else selected.add(i)

    @kb.add("enter")
    def _(event) -> None:
        if state.get("confirm_quit"):
            return
        state["result"] = [options[i][0] for i in sorted(selected)]
        event.app.exit()

    @kb.add("escape")
    def _(event) -> None:
        state["result"] = None
        event.app.exit()

    if allow_all_none:
        @kb.add("a")
        @kb.add("A")
        def _(event) -> None:
            if not state.get("confirm_quit"):
                selected.update(range(len(options)))
        # 'n' deselects all unless a quit confirmation is pending.
        state["on_n"] = selected.clear

    _quit_bindings(kb, state)

    def render() -> list:
        frags: list = []
        for line in intro:
            frags.append(("class:intro", "  " + line + "\n"))
        if intro:
            frags.append(("", "\n"))
        for i, (_value, label) in enumerate(options):
            checked = i in selected
            box = "[*]" if checked else "[ ]"
            if i == state["index"]:
                frags.append(("class:item.selected", f"  {box} {label}  "))
            else:
                frags.append(("class:checkbox.checked" if checked else "class:checkbox.unchecked",
                              f"  {box} "))
                frags.append(("class:item", label))
            frags.append(("", "\n"))
        frags.append(("", "\n"))
        frags.extend(_footer(state, lang, help_key))
        return frags

    root = Frame(Window(FormattedTextControl(render, focusable=True), always_hide_cursor=True),
                 title=title)
    _run_app(root, kb)
    result = state["result"]
    return None if result is _NOTHING else result


def _text_input(title: str, intro: list[str], lang: str, default: str = ""):
    """Single-line text input. Returns the text, or None (Esc)."""
    state = {"result": _NOTHING}
    area = TextArea(text=default, multiline=False, style="class:text-area", wrap_lines=False)
    kb = KeyBindings()

    @kb.add("enter", eager=True)
    def _(event) -> None:
        state["result"] = area.text
        event.app.exit()

    @kb.add("escape", eager=True)
    def _(event) -> None:
        state["result"] = None
        event.app.exit()

    intro_window = Window(
        FormattedTextControl(lambda: [("class:intro", "  " + line + "\n") for line in intro]),
        height=len(intro) + 1, always_hide_cursor=True,
    )
    root = Frame(HSplit([intro_window, area]), title=title)
    _run_app(root, kb)
    result = state["result"]
    return None if result is _NOTHING else result


# --- Interactive steps (require a TTY) ----------------------------------------

def select_language() -> str | None:
    """STEP 0 — choose the UI language.

    Returns:
        'en' or 'es' on selection, or None when the user quits (Q) or backs out
        (Esc) — the caller exits the program instead of defaulting to English.
    """
    result = _radiolist(
        "ScrapBro",
        ["[↑↓] Select language / Seleccioná el idioma"],
        [("en", "English"), ("es", "Español")],
        "en",
    )
    if result in (None, QUIT):
        return None
    return normalize_language(result)


def select_sources(lang: str):
    """STEP 2 — multi-select sources. Returns source keys, [] (back) or QUIT."""
    options = []
    for key, label_en, label_es, needs_cookie, _group in SOURCE_CATALOG:
        label = label_es if lang == "es" else label_en
        if needs_cookie:
            label += f"   ⚠ {t(lang, 'needs_cookie')}"
        options.append((key, label))
    preselected = {i for i, (key, _) in enumerate(options) if key in SessionMemory.last_sources}
    result = _checkboxlist(t(lang, "sources_title"), [t(lang, "sources_help")], options, lang,
                           preselected=preselected)
    if result == QUIT:
        return QUIT
    if not result:
        return []
    chosen = [s for s in result if s in ALL_SOURCE_KEYS]
    SessionMemory.remember(sources=chosen)
    return chosen


def _cookie_textarea(source: str, lang: str, error: str):
    """Render the cookie-paste screen once. Returns pasted text or None (skip)."""
    state = {"result": _NOTHING}
    area = TextArea(multiline=True, style="class:text-area", wrap_lines=True, height=8)
    kb = KeyBindings()

    @kb.add("enter", eager=True)
    def _(event) -> None:
        state["result"] = area.text
        event.app.exit()

    @kb.add("escape", eager=True)
    def _(event) -> None:
        state["result"] = None
        event.app.exit()

    def header() -> list:
        frags = [("class:title", f"  {t(lang, 'cookie_title')} — {source}\n\n"),
                 ("class:intro", f"  {t(lang, 'cookie_how')}\n")]
        for step in t(lang, "cookie_steps"):
            frags.append(("class:intro", f"  {step}\n"))
        frags.append(("", "\n"))
        if error:
            frags.append(("class:error", f"  {error}\n\n"))
        frags.append(("class:intro", f"  {t(lang, 'cookie_paste')}\n"))
        return frags

    header_window = Window(FormattedTextControl(header), always_hide_cursor=True)
    footer_window = Window(
        FormattedTextControl(lambda: [("class:help", "  " + t(lang, "cookie_help"))]),
        height=1,
    )
    root = Frame(HSplit([header_window, area, footer_window]), title="ScrapBro")
    _run_app(root, kb)
    result = state["result"]
    return None if result is _NOTHING else result


def _cookie_paste(source: str, lang: str) -> tuple[str, list[dict]] | None:
    """Run the paste-and-validate loop.

    Validates both the Cookie-Editor JSON structure and that the cookies belong
    to the source's domain; any failure (bad JSON or a cross-site paste) is shown
    on-screen and the user is re-prompted.

    Returns:
        ``(raw_json, parsed_cookies)`` on success, or None when the user skips
        (Esc / empty input). The parsed list is returned so the caller does not
        re-parse the JSON.
    """
    error = ""
    while True:
        raw = _cookie_textarea(source, lang, error)
        if raw is None or not raw.strip():
            return None  # skip
        valid, message = validate_cookie_json(raw)
        if not valid:
            error = message
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            error = f"✗ Invalid JSON: {exc}"
            continue
        cookies = data.get("cookies", data) if isinstance(data, dict) else data
        if not isinstance(cookies, list):
            error = "✗ Does not look like a Cookie-Editor export"
            continue
        # Surface a cross-site paste on-screen instead of silently saving it.
        ok, domain_message = cookie_detector.validate_cookie_domain(source, cookies)
        if not ok:
            error = f"✗ {domain_message}"
            continue
        return raw.strip(), cookies


def setup_cookies(sources: list[str], lang: str) -> dict[str, str]:
    """STEP 3 — configure cookies for sources that need them.

    For each cookie source the user chooses auto-detection (read from an
    installed browser), manual paste (Cookie-Editor JSON), or skip. Detected or
    pasted cookies are saved to ``.cookies/{source}.json``. Any failure falls
    back to the manual paste flow; nothing here blocks the rest of the run.

    Returns:
        Mapping of source -> saved cookie file path (skipped sources omitted).
    """
    needed = [s for s in sources if s in COOKIE_SOURCES]
    saved: dict[str, str] = {}
    if not needed:
        return saved
    COOKIE_DIR.mkdir(exist_ok=True)
    for source in needed:
        title = f"{t(lang, 'cookie_title')} — {source}"
        method = _radiolist(
            title, [t(lang, "cookie_method")],
            [("auto", t(lang, "cookie_auto")), ("manual", t(lang, "cookie_manual")),
             ("skip", t(lang, "cookie_skip"))],
            lang, help_key="confirm_help",
        )
        if method in (QUIT, None, "skip"):
            continue

        if method == "auto":
            ok, message, cookies = cookie_detector.detect_cookies(source)
            if ok and cookies:
                use = _radiolist(
                    title, [message, t(lang, "cookie_use")],
                    [("yes", t(lang, "cookie_use_yes")), ("no", t(lang, "cookie_use_no"))],
                    lang, help_key="confirm_help",
                )
                if use == "yes":
                    path = cookie_detector.save_cookies(source, cookies)
                    saved[source] = str(path)
                    continue
            else:
                nxt = _radiolist(
                    title, [message, t(lang, "cookie_failed")],
                    [("manual", t(lang, "cookie_manual")), ("skip", t(lang, "cookie_skip"))],
                    lang, help_key="confirm_help",
                )
                if nxt != "manual":
                    continue
            method = "manual"

        if method == "manual":
            result = _cookie_paste(source, lang)
            if result:
                # JSON structure and domain were already validated in the paste
                # loop; no re-parsing here.
                raw, _cookies = result
                path = COOKIE_DIR / f"{source}.json"
                path.write_text(raw, encoding="utf-8")
                cookie_detector.restrict_permissions(path)
                saved[source] = str(path)
                logger.info("Cookie saved for %s (permissions restricted)", source)
    return saved


def _query_input(lang: str):
    """Query step with live multi-search detection. Returns raw query or None."""
    state = {"result": _NOTHING}
    area = TextArea(multiline=False, style="class:text-area", wrap_lines=False)
    kb = KeyBindings()

    @kb.add("enter", eager=True)
    def _(event) -> None:
        state["result"] = area.text
        event.app.exit()

    @kb.add("escape", eager=True)
    def _(event) -> None:
        state["result"] = None
        event.app.exit()

    def header() -> list:
        return [
            ("class:intro", f"  {t(lang, 'query_intro')}\n"),
            ("class:intro", f"  {t(lang, 'query_multi')}\n"),
            ("class:help", f"  {t(lang, 'query_example')}\n"),
        ]

    def preview() -> list:
        queries = [q.strip() for q in area.text.split("--") if q.strip()]
        frags = [("", "\n"), ("class:intro", f"  {t(lang, 'query_detected')}\n")]
        if not queries:
            frags.append(("class:help", "  -\n"))
        for i, query in enumerate(queries, 1):
            frags.append(("class:detected", f"  [{i}] \"{query}\"\n"))
        return frags

    header_window = Window(FormattedTextControl(header), height=4, always_hide_cursor=True)
    preview_window = Window(FormattedTextControl(preview), always_hide_cursor=True)
    root = Frame(HSplit([header_window, area, preview_window]), title=t(lang, "query_title"))
    _run_app(root, kb)
    result = state["result"]
    return None if result is _NOTHING else result


def configure_search(sources: list[str], lang: str):
    """STEP 4 — gather query (multi), location, limit, output, filters, Dateas.

    Returns the config dict, None (Esc/back) or QUIT.
    """
    config = default_search_config()

    raw_query = _query_input(lang)
    if raw_query is None:
        return None
    config["query"] = raw_query.strip()
    config["queries"] = [q.strip() for q in raw_query.split("--") if q.strip()]
    if not config["queries"]:
        return None

    location = _text_input(t(lang, "query_title"), [t(lang, "location")], lang)
    if location is None:
        return None
    config["location"] = location.strip()

    limit = _text_input(t(lang, "limit_title"), [t(lang, "limit_intro")], lang,
                        default=str(SessionMemory.last_limit))
    if limit is None:
        return None
    try:
        config["limit"] = max(1, min(1000, int(limit.strip() or "50")))
    except ValueError:
        config["limit"] = 50

    # Only ask total-vs-per-source when more than one source is selected; with a
    # single source the two modes are identical.
    if len(sources) > 1:
        limit_mode = _radiolist(
            t(lang, "limit_title"), [t(lang, "limit_mode_intro")],
            [("total", t(lang, "limit_mode_total")), ("per_source", t(lang, "limit_mode_per"))],
            lang, help_key="confirm_help",
        )
        if limit_mode == QUIT:
            return QUIT
        if limit_mode is None:
            return None
        config["limit_per_source"] = config["limit"] if limit_mode == "per_source" else 0
    else:
        config["limit_per_source"] = 0

    output = _radiolist(t(lang, "output_title"), [], [("csv", "Excel (.xlsx)"), ("json", "JSON (.json)")],
                        lang, help_key="confirm_help")
    if output == QUIT:
        return QUIT
    if output is None:
        return None
    config["output"] = output or "csv"

    # Speed vs completeness. Default to Fast for broad/large runs (email scraping
    # is the slowest step), Complete for focused single/few-source searches.
    fast_default = len(sources) > 3 or config["limit"] > 30
    speed = _radiolist(
        t(lang, "speed_title"), [],
        [("fast", t(lang, "speed_fast")), ("full", t(lang, "speed_full"))],
        lang, default=0 if fast_default else 1, help_key="confirm_help",
    )
    if speed == QUIT:
        return QUIT
    if speed is None:
        return None
    config["email_scraping"] = speed == "full"

    filter_options = [
        ("filter_has_phone", "Only leads with phone"),
        ("filter_has_email", "Only leads with email"),
        ("filter_has_website", "Only leads with website"),
    ]
    if "dateas" in sources:
        filter_options.append(("filter_has_cuit", "Only leads with CUIT (Dateas)"))
    chosen = _checkboxlist(t(lang, "filters_title"), [t(lang, "filters_help")], filter_options, lang,
                           help_key="filters_help", allow_all_none=False)
    if chosen == QUIT:
        return QUIT
    if chosen is None:
        return None
    for key in (chosen or []):
        config[key] = True

    if "dateas" in sources:
        dtype = _radiolist(
            t(lang, "dateas_title"), [t(lang, "dateas_entity")],
            [("ambos", "Empresas y personas"), ("juridica", "Solo empresas (jurídicas)"),
             ("fisica", "Solo personas (físicas)")],
            lang, help_key="confirm_help",
        )
        if dtype == QUIT:
            return QUIT
        if dtype is None:
            return None
        config["dateas_type"] = {"juridica": "empresas", "fisica": "personas"}.get(dtype, "ambos")
        lookup = _radiolist(
            t(lang, "dateas_title"), [t(lang, "dateas_search")],
            [("name", "Nombre / Razón social"), ("cuit", "CUIT / CUIL"), ("dni", "DNI")],
            lang, help_key="confirm_help",
        )
        if lookup == QUIT:
            return QUIT
        if lookup is None:
            return None
        config["dateas_lookup"] = lookup or "name"

    perf = configure_performance(lang)
    if perf == QUIT:
        return QUIT
    if perf is None:
        return None
    config.update(perf)

    SessionMemory.remember(config=config)
    return config


def configure_performance(lang: str):
    """STEP 4b — workers / load mode / resource blocking. Returns dict, None or QUIT."""
    profile = detect_hardware()
    info = [
        f"CPUs: {profile.cpu_count} / {profile.cpu_count_logical} "
        + ("lógicos" if lang == "es" else "logical"),
        f"RAM: {profile.ram_available_gb} GB / {profile.ram_total_gb} GB",
        f"Workers max: {profile.max_workers}",
    ]
    perf = {"workers": 0, "network_idle": False, "block_resources": True}
    default_workers = SessionMemory.last_workers or profile.recommended_workers

    wmode = _radiolist(
        t(lang, "perf_title"), info + ["", t(lang, "perf_workers")],
        [("auto", t(lang, "perf_auto").format(n=profile.recommended_workers)),
         ("manual", t(lang, "perf_manual"))],
        lang, default=1 if SessionMemory.last_workers else 0, help_key="confirm_help",
    )
    if wmode == QUIT:
        return QUIT
    if wmode is None:
        return None
    if wmode == "manual":
        raw = _text_input(t(lang, "perf_title"), [t(lang, "perf_manual_n")], lang,
                          default=str(default_workers))
        if raw is None:
            return None
        try:
            perf["workers"] = max(1, min(64, int(raw.strip())))
        except ValueError:
            perf["workers"] = profile.recommended_workers

    load = _radiolist(
        t(lang, "perf_title"), [t(lang, "perf_load")],
        [("fast", t(lang, "perf_load_fast")), ("full", t(lang, "perf_load_full"))],
        lang, default=1 if SessionMemory.last_network_idle else 0, help_key="confirm_help",
    )
    if load == QUIT:
        return QUIT
    if load is None:
        return None
    perf["network_idle"] = load == "full"

    block = _radiolist(
        t(lang, "perf_title"), [t(lang, "perf_block")],
        [("yes", t(lang, "perf_block_yes")), ("no", t(lang, "perf_block_no"))],
        lang, default=0 if SessionMemory.last_block_resources else 1, help_key="confirm_help",
    )
    if block == QUIT:
        return QUIT
    if block is None:
        return None
    perf["block_resources"] = block != "no"
    return perf


def confirm(config: dict, sources: list[str], lang: str) -> str:
    """STEP 5 — confirm before scraping. Returns 'start', 'back' or 'quit'."""
    intro = build_confirm_summary(config, sources, lang).splitlines()
    result = _radiolist(
        t(lang, "confirm_title"), intro,
        [("start", t(lang, "start")), ("back", t(lang, "back")), ("quit", t(lang, "quit"))],
        lang, help_key="confirm_help",
    )
    if result in (None, "back"):
        return "back"
    if result in (QUIT, "quit"):
        return "quit"
    return "start"


def summary_actions(lang: str) -> str:
    """STEP 7 — offer next action (rendered inline below the results table).

    Returns 'new', 'open' or 'quit'.
    """
    result = _radiolist(
        "ScrapBro", [],
        [("new", t(lang, "new_search")), ("open", t(lang, "open_folder")), ("quit", t(lang, "quit"))],
        lang, help_key="confirm_help", full_screen=False,
    )
    if result == "open":
        return "open"
    if result == "new":
        return "new"
    return "quit"
