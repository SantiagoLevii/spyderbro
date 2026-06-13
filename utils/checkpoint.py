import hashlib
import json
import logging
import time
from pathlib import Path

from models.lead import Lead
from utils.file_utils import ensure_dir

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = ".checkpoints"
TTL_SECONDS = 2 * 60 * 60


class ScrapingCheckpoint:
    """Persists scraping progress so an interrupted session can be resumed.

    State files live in .checkpoints/{source}_{query_hash}.json and expire
    after 2 hours.
    """

    def __init__(self, checkpoint_dir: str = CHECKPOINT_DIR, ttl_seconds: int = TTL_SECONDS) -> None:
        """Create the checkpoint handler.

        Args:
            checkpoint_dir: Directory where checkpoint files are stored.
            ttl_seconds: Maximum age before a checkpoint is considered stale.
        """
        self.checkpoint_dir = Path(checkpoint_dir)
        self.ttl_seconds = ttl_seconds

    def save(self, leads_so_far: list[Lead], current_page: int, source: str, query: str) -> None:
        """Save the current scraping state.

        Args:
            leads_so_far: Leads extracted so far.
            current_page: Position marker to resume from (page or item index).
            source: Scraper source name.
            query: Search query of the session.
        """
        ensure_dir(self.checkpoint_dir)
        path = self._path(source, query)
        payload = {
            "source": source,
            "query": query,
            "saved_at": time.time(),
            "current_page": current_page,
            "leads": [lead.to_dict() for lead in leads_so_far],
        }
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except OSError as exc:
            logger.warning("Could not write checkpoint %s: %s", path, exc)
            return
        logger.info("Checkpoint saved: %d leads, page %d (%s/%r)",
                    len(leads_so_far), current_page, source, query)

    def load(self, source: str, query: str) -> tuple[list[Lead], int] | None:
        """Load a previous session state if a fresh checkpoint exists.

        Args:
            source: Scraper source name.
            query: Search query of the session.

        Returns:
            Tuple of (saved leads, current page), or None if there is no
            checkpoint or it is older than 2 hours.
        """
        path = self._path(source, query)
        try:
            with path.open(encoding="utf-8") as f:
                payload = json.load(f)
            age = time.time() - float(payload["saved_at"])
            if age > self.ttl_seconds:
                logger.info("Checkpoint expired for %s/%r (%.0fm old)", source, query, age / 60)
                return None
            leads = [Lead(**item) for item in payload["leads"]]
            page = int(payload["current_page"])
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            logger.warning("Could not read checkpoint %s: %s", path, exc)
            return None

        logger.info("Checkpoint loaded: %d leads, page %d (%s/%r)", len(leads), page, source, query)
        return leads, page

    def clear(self, source: str, query: str) -> None:
        """Delete the checkpoint after a successful run.

        Args:
            source: Scraper source name.
            query: Search query of the session.
        """
        path = self._path(source, query)
        try:
            path.unlink(missing_ok=True)
            logger.info("Checkpoint cleared for %s/%r", source, query)
        except OSError as exc:
            logger.warning("Could not delete checkpoint %s: %s", path, exc)

    def age_seconds(self, source: str, query: str) -> float | None:
        """Return the age in seconds of a checkpoint, or None if missing."""
        path = self._path(source, query)
        try:
            with path.open(encoding="utf-8") as f:
                payload = json.load(f)
            return time.time() - float(payload["saved_at"])
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return None

    def _path(self, source: str, query: str) -> Path:
        """Build the checkpoint file path for a source/query pair."""
        query_hash = hashlib.md5(query.encode("utf-8")).hexdigest()
        return self.checkpoint_dir / f"{source}_{query_hash}.json"
