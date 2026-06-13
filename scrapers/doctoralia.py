"""DEPRECATED: doctoralia.py

doctoralia.com.ar blocks datacenter IPs (geo block) and is unreachable without a
residential proxy. This module is a thin compatibility shim that delegates to
``topdoctors_ar``.
"""
import logging

from config.settings import settings
from models.lead import Lead
from scrapers.topdoctors_ar import TopDoctorsARScraper

logger = logging.getLogger(__name__)

__all__ = ["DoctoraliaScraper"]


class DoctoraliaScraper:
    """Deprecated Doctoralia scraper — delegates to :class:`TopDoctorsARScraper`."""

    def __init__(self) -> None:
        logger.warning(
            "doctoralia is blocked by an IP/geo block. "
            "Redirecting automatically to topdoctors_ar."
        )
        self._delegate = TopDoctorsARScraper()

    def scrape(self, query: str, limit: int = settings.DEFAULT_LIMIT) -> list[Lead]:
        """CLI/pipeline entry point — delegates to topdoctors_ar."""
        return self._delegate.scrape(query, limit)

    def search(self, specialty: str, location: str, limit: int) -> list[Lead]:
        """Backward-compatible search — delegates to topdoctors_ar."""
        return self._delegate.search(specialty, location, limit)
