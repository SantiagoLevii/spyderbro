"""ScrapBro ASCII banner with a typewriter reveal (Matrix green)."""
import sys
import time

from ui.theme import ANSI_GREEN, ANSI_RESET

BANNER_EN = r"""
 ____                      ____
/ ___|  ___ _ __ __ _ _ __| __ ) _ __ ___
\___ \ / __| '__/ _` | '_ \  _ \| '__/ _ \
 ___) | (__| | | (_| | |_) | |_) | | | (_) |
|____/ \___|_|  \__,_| .__/|____/|_|  \___/
                      |_|
        Multi-source B2B Lead Scraper
        ================================
        v0.1.0  |  15 sources  |  AR + Global
"""

BANNER_ES = r"""
 ____                      ____
/ ___|  ___ _ __ __ _ _ __| __ ) _ __ ___
\___ \ / __| '__/ _` | '_ \  _ \| '__/ _ \
 ___) | (__| | | (_| | |_) | |_) | | | (_) |
|____/ \___|_|  \__,_| .__/|____/|_|  \___/
                      |_|
        Scraper de Leads B2B Multi-fuente
        ===================================
        v0.1.0  |  15 fuentes  |  AR + Global
"""

_CHAR_DELAY = 0.002


def print_banner(language: str = "en") -> None:
    """Print the ScrapBro banner with a per-character typewriter effect.

    Falls back to an instant print when stdout is not a TTY.

    Args:
        language: "es" for the Spanish banner, otherwise English.
    """
    banner = BANNER_ES if language == "es" else BANNER_EN

    if not sys.stdout.isatty():
        sys.stdout.write(banner + "\n")
        sys.stdout.flush()
        return

    sys.stdout.write(ANSI_GREEN)
    for char in banner:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(_CHAR_DELAY)
    sys.stdout.write(ANSI_RESET + "\n")
    sys.stdout.flush()
