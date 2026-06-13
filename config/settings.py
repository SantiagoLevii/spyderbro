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


class Settings:
    """Application settings loaded from environment variables via .env."""

    SCRAPING_DELAY_MIN: float = _env_float("SCRAPING_DELAY_MIN", 2.0)
    SCRAPING_DELAY_MAX: float = _env_float("SCRAPING_DELAY_MAX", 5.0)
    DEFAULT_LIMIT: int = _env_int("DEFAULT_LIMIT", 50)
    OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "output")
    SERPER_API_KEY: str = os.getenv("SERPER_API_KEY", "")
    PROXY_URL: str = os.getenv("PROXY_URL", "")
    APIFY_TOKEN: str = os.getenv("APIFY_TOKEN", "")

    # Runtime-only options set by the CLI (not env-backed). LOCATION feeds the
    # Argentina-pack scrapers that need a province/city; DATEAS_TYPE selects the
    # Dateas search mode. Defaults keep them harmless when unset.
    LOCATION: str = ""
    DATEAS_TYPE: str = "empresas"


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
