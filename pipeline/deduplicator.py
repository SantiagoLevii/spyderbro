import logging
from urllib.parse import urlparse

from rapidfuzz import fuzz

from models.lead import Lead

logger = logging.getLogger(__name__)

NAME_SIMILARITY_THRESHOLD = 85.0

MERGEABLE_FIELDS = ("email", "phone", "website", "address", "category")


def _normalize_domain(url: str) -> str:
    """Extract a comparable domain from a URL (no scheme, no www, no path)."""
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.")


def _city_of(address: str) -> str:
    """Best-effort city extraction: last comma-separated segment of the address."""
    if not address or "," not in address:
        return ""
    return address.rsplit(",", 1)[-1].strip().lower()


def _completeness(lead: Lead) -> int:
    """Count how many enrichable fields a lead has filled."""
    score = sum(1 for f in MERGEABLE_FIELDS if getattr(lead, f))
    if lead.rating:
        score += 1
    return score


class Deduplicator:
    """Removes and merges duplicate leads coming from one or more sources."""

    def deduplicate(self, leads: list[Lead]) -> tuple[list[Lead], int]:
        """Collapse duplicate leads into single enriched leads.

        Match priority: normalized phone, website domain, email, then fuzzy
        name (>= 85% similarity) combined with same city in the address.

        Args:
            leads: Leads possibly coming from multiple sources.

        Returns:
            Tuple of (unique leads, number of duplicates removed).
        """
        unique: list[Lead] = []
        removed = 0

        for lead in leads:
            match = self._find_match(lead, unique)
            if match is None:
                unique.append(lead)
                continue

            merged = self._merge(match, lead)
            unique[unique.index(match)] = merged
            removed += 1
            logger.info(
                "Duplicate found: %r (%s) merged into %r (%s)",
                lead.name, lead.source, merged.name, merged.source,
            )

        logger.info("Deduplication: %d leads in, %d out, %d removed", len(leads), len(unique), removed)
        return unique, removed

    def _find_match(self, lead: Lead, candidates: list[Lead]) -> Lead | None:
        """Return the first candidate that matches lead, or None."""
        for candidate in candidates:
            if self._is_duplicate(lead, candidate):
                return candidate
        return None

    @staticmethod
    def _is_duplicate(a: Lead, b: Lead) -> bool:
        """Check whether two leads represent the same business."""
        if a.phone and b.phone and a.phone == b.phone:
            return True

        domain_a, domain_b = _normalize_domain(a.website), _normalize_domain(b.website)
        if domain_a and domain_b and domain_a == domain_b:
            return True

        if a.email and b.email and a.email.lower() == b.email.lower():
            return True

        if a.name and b.name:
            similarity = fuzz.token_sort_ratio(a.name.lower(), b.name.lower())
            city_a, city_b = _city_of(a.address), _city_of(b.address)
            if similarity >= NAME_SIMILARITY_THRESHOLD and city_a and city_a == city_b:
                return True

        return False

    @staticmethod
    def _merge(a: Lead, b: Lead) -> Lead:
        """Merge two duplicate leads, keeping the most complete one as base.

        Empty fields on the base are filled from the duplicate. The base's
        raw_data records every source it was merged from.
        """
        base, other = (a, b) if _completeness(a) >= _completeness(b) else (b, a)

        for field_name in MERGEABLE_FIELDS:
            if not getattr(base, field_name) and getattr(other, field_name):
                setattr(base, field_name, getattr(other, field_name))

        if not base.rating and other.rating:
            base.rating = other.rating

        sources = set(base.raw_data.get("merged_from", []))
        sources.update({base.source, other.source})
        sources.discard("")
        base.raw_data["merged_from"] = sorted(sources)

        return base
