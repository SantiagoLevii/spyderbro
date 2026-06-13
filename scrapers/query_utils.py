"""Shared query-parsing helpers for the Argentina-pack scrapers."""
from config.settings import settings


def split_query(query: str) -> tuple[str, str]:
    """Split a 'term location' query into (term, location).

    The first whitespace token is the term; the remainder is the location.
    When ``settings.LOCATION`` is set it overrides the parsed location.

    Args:
        query: Raw query string, e.g. "sushi palermo".

    Returns:
        Tuple of (term, location).
    """
    parts = query.strip().split()
    term = parts[0] if parts else query.strip()
    parsed_location = " ".join(parts[1:]) if len(parts) > 1 else ""
    location = settings.LOCATION or parsed_location
    return term, location
