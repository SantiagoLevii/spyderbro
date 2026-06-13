"""Interactive menus for the ScrapBro TUI.

The interactive steps use prompt_toolkit dialogs (arrow navigation, space to
toggle, enter to confirm) styled with the Matrix-green theme. Pure helpers
(source catalog, cookie validation, config defaults, summary builder) are kept
side-effect free so they can be unit tested without a TTY.
"""
import json
import logging
from pathlib import Path

from prompt_toolkit.shortcuts import (
    button_dialog,
    checkboxlist_dialog,
    input_dialog,
    radiolist_dialog,
)

from ui.theme import SCRAPBRO_STYLE

logger = logging.getLogger(__name__)

COOKIE_DIR = Path(".cookies")

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
    ("clutch", "Clutch — digital agencies", "Clutch — agencias digitales", False, "argentina"),
]

ALL_SOURCE_KEYS: list[str] = [row[0] for row in SOURCE_CATALOG]
COOKIE_SOURCES: set[str] = {row[0] for row in SOURCE_CATALOG if row[3]}

_T = {
    "en": {
        "lang_title": "Select language",
        "sources_title": "Select sources to scrape",
        "sources_text": "[Space] toggle · [Enter] confirm",
        "query": "What are you looking for?",
        "location": "Location (optional — empty for global):",
        "limit": "Maximum leads to scrape:",
        "output": "Output format:",
        "filters": "Apply filters (optional):",
        "confirm_title": "Ready to scrape!",
        "start": "Start", "back": "Back", "quit": "Quit",
        "new_search": "New search", "open_folder": "Open output folder",
        "needs_cookie": "needs cookie",
    },
    "es": {
        "lang_title": "Seleccioná el idioma",
        "sources_title": "Elegí las fuentes a scrapear",
        "sources_text": "[Espacio] marcar · [Enter] confirmar",
        "query": "¿Qué estás buscando?",
        "location": "Ubicación (opcional — vacío = global):",
        "limit": "Máximo de leads:",
        "output": "Formato de salida:",
        "filters": "Aplicar filtros (opcional):",
        "confirm_title": "¡Listo para scrapear!",
        "start": "Empezar", "back": "Volver", "quit": "Salir",
        "new_search": "Nueva búsqueda", "open_folder": "Abrir carpeta de salida",
        "needs_cookie": "necesita cookie",
    },
}


def t(lang: str, key: str) -> str:
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


def validate_cookie_file(path: str) -> tuple[bool, str]:
    """Validate a browser cookie export (Cookie-Editor JSON) structurally.

    Checks the file exists, is valid JSON, and is a non-empty list of cookie
    objects with at least a ``name`` field. A live-session check would require a
    network request and is intentionally out of scope.

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


def default_search_config() -> dict:
    """Return the default search configuration used by the TUI."""
    return {
        "query": "",
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

    lines = [
        f"Sources:   {', '.join(sources)}",
        f"Query:     {config.get('query', '')!r}",
        f"Location:  {config.get('location') or '-'}",
        f"Limit:     {config.get('limit')}",
        f"Output:    {config.get('output')}",
        f"Filters:   {', '.join(filters) or '-'}",
    ]
    if "dateas" in sources:
        lines.append(f"Dateas:    type={config.get('dateas_type')} lookup={config.get('dateas_lookup')}")
    return "\n".join(lines)


# --- Interactive steps (require a TTY) ----------------------------------------

def select_language() -> str:
    """STEP 0 — choose the UI language. Returns 'en' or 'es'."""
    result = radiolist_dialog(
        title="ScrapBro",
        text="[↑↓] Select language / Seleccioná el idioma",
        values=[("en", "English"), ("es", "Español")],
        style=SCRAPBRO_STYLE,
    ).run()
    return normalize_language(result or "en")


def select_sources(lang: str) -> list[str]:
    """STEP 2 — multi-select sources. Returns a list of valid source keys."""
    values = []
    for key, label_en, label_es, needs_cookie, _group in SOURCE_CATALOG:
        label = label_es if lang == "es" else label_en
        if needs_cookie:
            label += f"  ⚠ {t(lang, 'needs_cookie')}"
        values.append((key, label))
    selected = checkboxlist_dialog(
        title=t(lang, "sources_title"),
        text=t(lang, "sources_text"),
        values=values,
        style=SCRAPBRO_STYLE,
    ).run()
    return [s for s in (selected or []) if s in ALL_SOURCE_KEYS]


def setup_cookies(sources: list[str], lang: str) -> dict[str, str]:
    """STEP 3 — collect & validate cookie files for sources that need them.

    Returns a mapping of source -> validated cookie file path (skipped sources
    are omitted).
    """
    needed = [s for s in sources if s in COOKIE_SOURCES]
    cookies: dict[str, str] = {}
    if not needed:
        return cookies
    COOKIE_DIR.mkdir(exist_ok=True)
    for source in needed:
        path = input_dialog(
            title=f"Cookie — {source}",
            text=("Paste the path to the cookie JSON (Cookie-Editor export), "
                  "or leave empty to skip:"),
            style=SCRAPBRO_STYLE,
        ).run()
        if not path:
            continue
        valid, message = validate_cookie_file(path)
        if valid:
            cookies[source] = str(Path(path).expanduser())
        else:
            logger.warning("Cookie for %s rejected: %s", source, message)
    return cookies


def configure_search(sources: list[str], lang: str) -> dict | None:
    """STEP 4 — gather query, location, limit, output and filters.

    Returns the config dict, or None if the user cancelled.
    """
    config = default_search_config()

    query = input_dialog(title="ScrapBro", text=t(lang, "query"), style=SCRAPBRO_STYLE).run()
    if query is None:
        return None
    config["query"] = query.strip()

    location = input_dialog(title="ScrapBro", text=t(lang, "location"), style=SCRAPBRO_STYLE).run()
    config["location"] = (location or "").strip()

    limit = input_dialog(title="ScrapBro", text=t(lang, "limit"), default="50",
                         style=SCRAPBRO_STYLE).run()
    try:
        config["limit"] = max(1, min(1000, int((limit or "50").strip())))
    except ValueError:
        config["limit"] = 50

    output = radiolist_dialog(
        title="ScrapBro", text=t(lang, "output"),
        values=[("csv", "Excel (.xlsx)"), ("json", "JSON (.json)")],
        style=SCRAPBRO_STYLE,
    ).run()
    config["output"] = output or "csv"

    filter_values = [
        ("filter_has_phone", "Only leads with phone"),
        ("filter_has_email", "Only leads with email"),
        ("filter_has_website", "Only leads with website"),
    ]
    if "dateas" in sources:
        filter_values.append(("filter_has_cuit", "Only leads with CUIT (Dateas)"))
    chosen = checkboxlist_dialog(
        title="ScrapBro", text=t(lang, "filters"), values=filter_values, style=SCRAPBRO_STYLE,
    ).run()
    for key in (chosen or []):
        config[key] = True

    if "dateas" in sources:
        dtype = radiolist_dialog(
            title="Dateas", text="Tipo:",
            values=[("ambos", "Empresas y personas"), ("empresas", "Solo empresas"),
                    ("personas", "Solo personas")],
            style=SCRAPBRO_STYLE,
        ).run()
        config["dateas_type"] = dtype or "ambos"
        lookup = radiolist_dialog(
            title="Dateas", text="Search by:",
            values=[("name", "Name / Razón social"), ("cuit", "CUIT / CUIL"), ("dni", "DNI")],
            style=SCRAPBRO_STYLE,
        ).run()
        config["dateas_lookup"] = lookup or "name"

    return config


def confirm(config: dict, sources: list[str], lang: str) -> str:
    """STEP 5 — confirm before scraping. Returns 'start', 'back' or 'quit'."""
    return button_dialog(
        title=t(lang, "confirm_title"),
        text=build_confirm_summary(config, sources, lang),
        buttons=[(t(lang, "start"), "start"), (t(lang, "back"), "back"), (t(lang, "quit"), "quit")],
        style=SCRAPBRO_STYLE,
    ).run() or "quit"


def summary_actions(summary_text: str, lang: str) -> str:
    """STEP 7 — show results and offer next action. Returns 'new', 'open' or 'quit'."""
    return button_dialog(
        title="ScrapBro",
        text=summary_text,
        buttons=[(t(lang, "new_search"), "new"), (t(lang, "open_folder"), "open"),
                 (t(lang, "quit"), "quit")],
        style=SCRAPBRO_STYLE,
    ).run() or "quit"
