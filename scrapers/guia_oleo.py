"""DEPRECATED: guia_oleo.py

The site guiaoleo.com.ar was discontinued (it is now an SEO content blog with no
business listings). This module is a thin compatibility shim that delegates to
``tripadvisor_ar``. ``split_query`` is re-exported for backward compatibility.
"""
import logging

from config.settings import settings
from models.lead import Lead
from scrapers.query_utils import split_query  # re-exported for backward compatibility
from scrapers.tripadvisor_ar import TripAdvisorARScraper

logger = logging.getLogger(__name__)

__all__ = ["GuiaOleoScraper", "split_query"]


class GuiaOleoScraper:
    """Deprecated GuiaOleo scraper — delegates to :class:`TripAdvisorARScraper`."""

    def __init__(self) -> None:
        logger.warning(
            "guia_oleo is discontinued (guiaoleo.com.ar no longer lists "
            "businesses). Redirecting automatically to tripadvisor_ar."
        )
        self._delegate = TripAdvisorARScraper()

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point — delegates to tripadvisor_ar."""
        return self._delegate.scrape(query, limit)

    def search(self, query: str, location: str, limit: int) -> list[Lead]:
        """Backward-compatible search — delegates to tripadvisor_ar."""
        return self._delegate.search_restaurants(query, location, limit)
