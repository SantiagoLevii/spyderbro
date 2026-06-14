"""Helpers for loading per-source browser cookies saved by the TUI.

The TUI writes validated Cookie-Editor JSON exports to ``.cookies/{source}.json``.
Scrapers that hit login walls (Instagram, Facebook, LinkedIn, Twitter/X,
MercadoLibre) load them here and pass them to the fetcher so authenticated
content becomes reachable.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

COOKIE_DIR = Path(".cookies")


def load_cookies(source: str) -> list[dict] | None:
    """Load saved cookies for a source from ``.cookies/{source}.json``.

    Accepts both a bare list (Cookie-Editor export) and an object with a
    ``"cookies"`` key.

    Args:
        source: Source key (e.g. "instagram").

    Returns:
        A list of cookie dicts, or None when no usable cookie file exists.
    """
    path = COOKIE_DIR / f"{source}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to load cookies for %s: %s", source, exc)
        return None
    cookies = data.get("cookies", data) if isinstance(data, dict) else data
    if isinstance(cookies, list) and cookies:
        logger.info("Loaded %d cookies for %s", len(cookies), source)
        return cookies
    return None
