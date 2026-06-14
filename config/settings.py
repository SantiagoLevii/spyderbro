import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to default on missing or bad values."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid value for %s=%r — using default %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    """Read an int env var, falling back to default on missing or bad values."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid value for %s=%r — using default %s", name, raw, default)
        return default


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean env var (true/1/yes/on), falling back to default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    """Application settings loaded from environment variables via .env."""

    SCRAPING_DELAY_MIN: float = _env_float("SCRAPING_DELAY_MIN", 2.0)
    SCRAPING_DELAY_MAX: float = _env_float("SCRAPING_DELAY_MAX", 5.0)
    DEFAULT_LIMIT: int = _env_int("DEFAULT_LIMIT", 50)
    OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "output")
    SERPER_API_KEY: str = os.getenv("SERPER_API_KEY", "")
    PROXY_URL: str = os.getenv("PROXY_URL", "")
    APIFY_TOKEN: str = os.getenv("APIFY_TOKEN", "")

    # Email scraping — the heaviest enrichment step (one extra site visit per
    # lead with a website). Tunable / disableable for speed.
    EMAIL_SCRAPING_ENABLED: bool = _env_bool("EMAIL_SCRAPING_ENABLED", True)
    EMAIL_SCRAPING_TIMEOUT: int = _env_int("EMAIL_SCRAPING_TIMEOUT", 3)
    EMAIL_SCRAPING_MAX_PAGES: int = _env_int("EMAIL_SCRAPING_MAX_PAGES", 1)
    EMAIL_SCRAPING_MAX_CONCURRENT: int = _env_int("EMAIL_SCRAPING_MAX_CONCURRENT", 10)

    # Performance (0 = auto from the detected hardware profile).
    WORKERS: int = _env_int("WORKERS", 0)
    NETWORK_IDLE: bool = _env_bool("NETWORK_IDLE", False)
    BLOCK_RESOURCES: bool = _env_bool("BLOCK_RESOURCES", True)
    BROWSER_REUSE: bool = _env_bool("BROWSER_REUSE", True)
    MAX_CONCURRENT_SOURCES: int = _env_int("MAX_CONCURRENT_SOURCES", 0)

    # Runtime-only options set by the CLI (not env-backed). LOCATION feeds the
    # Argentina-pack scrapers that need a province/city; DATEAS_TYPE selects the
    # Dateas search mode. Defaults keep them harmless when unset.
    LOCATION: str = ""
    DATEAS_TYPE: str = "empresas"
    DATEAS_LOOKUP: str = "name"
    ML_OFFICIAL_ONLY: bool = False

    def get_safe_proxy_url(self) -> str:
        """Return ``PROXY_URL`` with the password redacted, for safe logging.

        Example: ``http://user:secret@host:8080`` -> ``http://user:***@host:8080``.

        Returns:
            The proxy URL with its password masked, or "" when no proxy is set.
        """
        if not self.PROXY_URL:
            return ""
        try:
            from urllib.parse import urlparse, urlunparse

            parsed = urlparse(self.PROXY_URL)
            if parsed.password:
                host = parsed.hostname or ""
                netloc = f"{parsed.username}:***@{host}"
                if parsed.port:
                    netloc += f":{parsed.port}"
                return urlunparse(parsed._replace(netloc=netloc))
        except Exception:  # noqa: BLE001 - redaction must never raise
            return "***"
        return self.PROXY_URL


settings = Settings()


def validate_settings() -> None:
    """Validate critical settings at startup.

    Logs warnings for unset optional variables and fixes inconsistent
    values. Never raises for optional configuration.
    """
    if settings.SCRAPING_DELAY_MIN > settings.SCRAPING_DELAY_MAX:
        logger.warning(
            "SCRAPING_DELAY_MIN (%s) > SCRAPING_DELAY_MAX (%s) — swapping",
            settings.SCRAPING_DELAY_MIN, settings.SCRAPING_DELAY_MAX,
        )
        settings.SCRAPING_DELAY_MIN, settings.SCRAPING_DELAY_MAX = (
            settings.SCRAPING_DELAY_MAX, settings.SCRAPING_DELAY_MIN,
        )

    if settings.DEFAULT_LIMIT < 1:
        logger.warning("DEFAULT_LIMIT < 1 — resetting to 50")
        settings.DEFAULT_LIMIT = 50

    if not settings.SERPER_API_KEY:
        logger.info(
            "SERPER_API_KEY not set — Dorks scraper will fall back to DuckDuckGo "
            "(slower, conservative delays)"
        )
    if not settings.PROXY_URL:
        logger.info("PROXY_URL not set — LinkedIn scraper will run in conservative mode")
    if not settings.APIFY_TOKEN:
        logger.debug("APIFY_TOKEN not set — Apify-based scraping unavailable (optional)")
