import re
from pathlib import Path

INVALID_CHARS = re.compile(r'[<>:"/\\|?*#@\s]+')
MAX_FILENAME_LENGTH = 100


def sanitize_filename(name: str) -> str:
    """Clean a string for safe use as a filename.

    Replaces invalid and whitespace characters with underscores, collapses
    repeats, and truncates to 100 characters.

    Args:
        name: Raw string (e.g. a search query).

    Returns:
        Filesystem-safe filename fragment.
    """
    cleaned = INVALID_CHARS.sub("_", name.strip()).strip("_")
    return cleaned[:MAX_FILENAME_LENGTH] or "output"


def ensure_dir(path: Path) -> Path:
    """Create a directory (and parents) if it does not exist.

    Args:
        path: Directory path.

    Returns:
        The same path, guaranteed to exist.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path
