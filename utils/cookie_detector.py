"""Automatic cookie detection from installed browsers.

Reads the session cookies for a source's domain straight off disk via
``browser-cookie3`` (Chrome, Edge, Firefox on Windows). Everything degrades
gracefully when the optional dependency is missing or no browser session
exists, so the manual paste flow always remains available.
"""
import json
import logging
import os
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

try:
    import browser_cookie3
    BROWSER_COOKIE3_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    browser_cookie3 = None
    BROWSER_COOKIE3_AVAILABLE = False

logger = logging.getLogger(__name__)

COOKIE_DIR = Path(".cookies")

# Acceptable cookie domains per source, most specific / current first. Twitter/X
# serves its session on x.com (with legacy twitter.com cookies still around), so
# both are accepted and x.com is tried first for auto-detection.
SOURCE_DOMAINS = {
    "instagram": (".instagram.com",),
    "facebook": (".facebook.com",),
    "linkedin": (".linkedin.com",),
    "twitter": (".x.com", ".twitter.com"),
    "mercadolibre": (".mercadolibre.com.ar",),
}


def detect_cookies(
    source: str, on_progress: Callable[[str], None] | None = None,
) -> tuple[bool, str, list[dict] | None]:
    """Detect browser cookies for a source by trying Chrome, Edge then Firefox.

    Args:
        source: Source key (instagram, facebook, linkedin, twitter, mercadolibre).
        on_progress: Optional callback that receives short progress strings.

    Returns:
        ``(success, message, cookies)`` — cookies is a Scrapling-compatible list
        of dicts on success, otherwise None.
    """
    if not BROWSER_COOKIE3_AVAILABLE:
        return False, "browser-cookie3 not installed", None

    domains = SOURCE_DOMAINS.get(source)
    if not domains:
        return False, f"Source {source} has no automatic-cookie support", None

    browsers = [
        ("Chrome", browser_cookie3.chrome),
        ("Edge", browser_cookie3.edge),
        ("Firefox", browser_cookie3.firefox),
    ]

    for name, loader in browsers:
        if on_progress:
            on_progress(f"{name}...")
        cookies: list[dict] = []
        errored = False
        # Try each acceptable domain (e.g. x.com then twitter.com) and keep the
        # first one that yields a session.
        for domain in domains:
            try:
                cookiejar = loader(domain_name=domain)
                cookies = _cookiejar_to_list(cookiejar)
            except Exception as exc:  # noqa: BLE001 - browser may be absent/locked
                logger.debug("Failed to read %s cookies for %s: %s", name, domain, exc)
                errored = True
                continue
            if cookies:
                break
        if cookies:
            if on_progress:
                on_progress(f"{name}... {len(cookies)} cookies")
            return True, f"Session found in {name} ({len(cookies)} cookies)", cookies
        if on_progress:
            on_progress(f"{name}... error" if errored else f"{name}... no session")

    return False, "No active session found in any browser", None


def _cookiejar_to_list(cookiejar) -> list[dict]:
    """Convert a CookieJar to a list of Scrapling-compatible cookie dicts."""
    return [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path,
            "secure": bool(c.secure),
        }
        for c in cookiejar
        if c.value
    ]


def validate_cookie_domain(source: str, cookies: list[dict]) -> tuple[bool, str]:
    """Check that pasted/detected cookies actually belong to the source's domain.

    Guards against pasting one site's session into another by mistake.

    Args:
        source: Source key (instagram, facebook, ...).
        cookies: Parsed list of cookie dicts.

    Returns:
        ``(True, message)`` when at least one cookie matches the expected domain
        (or the source has no known domain), otherwise ``(False, message)``.
    """
    expected = tuple(d.lstrip(".") for d in SOURCE_DOMAINS.get(source, ()))
    if not expected:
        return True, f"{len(cookies)} cookies"
    matching = [
        c for c in cookies
        if any(domain in (c.get("domain", "") or "") for domain in expected)
    ]
    if not matching:
        return False, f"No cookies for {expected[0]} — wrong site?"
    return True, f"{len(matching)} cookies for {expected[0]}"


def restrict_permissions(path: Path) -> None:
    """Restrict a file so only the current user can read/write it.

    Browser session cookies are credential-grade secrets, so the on-disk copy
    must not be world-readable. Best-effort: failures are logged, not raised, so
    the cookie flow keeps working even on locked-down or exotic filesystems.

    Args:
        path: Path to the cookie file to lock down.
    """
    try:
        if os.name == "nt":  # Windows: drop inheritance, grant only current user.
            user = os.environ.get("USERNAME", "")
            if user:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r",
                     "/grant:r", f"{user}:F"],
                    capture_output=True, check=False,
                )
        else:  # POSIX: chmod 600 (owner read/write only).
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception as exc:  # noqa: BLE001 - permission hardening is best-effort
        logger.warning("Could not restrict cookie file permissions on %s: %s", path, exc)


def save_cookies(source: str, cookies: list[dict]) -> Path:
    """Save detected cookies to ``.cookies/{source}.json`` and return the path.

    The file is written with restrictive permissions (owner-only) because it
    contains live session credentials.
    """
    COOKIE_DIR.mkdir(exist_ok=True)
    path = COOKIE_DIR / f"{source}.json"
    path.write_text(json.dumps(cookies, indent=2, ensure_ascii=False), encoding="utf-8")
    restrict_permissions(path)
    logger.info("Saved %d cookies for %s to %s (permissions restricted)", len(cookies), source, path)
    return path
