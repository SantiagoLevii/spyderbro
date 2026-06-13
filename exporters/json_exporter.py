import json
import logging
from pathlib import Path

from config.settings import settings
from models.lead import Lead
from utils.file_utils import ensure_dir

logger = logging.getLogger(__name__)


class JSONExporter:
    """Exports a list of Lead objects to a JSON file."""

    def export(self, leads: list[Lead], filename: str) -> str:
        """Save leads to a JSON file inside the configured output directory.

        Args:
            leads: List of Lead objects to export.
            filename: Base filename (without path). Will be saved as output/{filename}.

        Returns:
            Absolute path to the generated JSON file.
        """
        path = ensure_dir(Path(settings.OUTPUT_DIR)) / filename

        if not leads:
            logger.warning("No leads to export — writing empty file at %s", path)

        data = [lead.to_dict() for lead in leads]

        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("JSON exported: %s (%d leads)", path, len(leads))
        return str(path.resolve())
