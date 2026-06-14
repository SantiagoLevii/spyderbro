import os


def get_optimal_workers(multiplier: int = 4, cap: int = 32) -> int:
    """Calculate optimal thread workers for I/O-bound scraping.

    Formula: min(cap, (os.cpu_count() or 1) * multiplier). For I/O-bound web
    scraping, 4x CPUs is a safe default; heavier browser fan-out (Google Maps)
    uses a higher multiplier/cap.

    Args:
        multiplier: CPU multiplier (default 4).
        cap: Hard ceiling on the worker count (default 32).

    Returns:
        Number of workers to use in ThreadPoolExecutor.
    """
    return min(cap, (os.cpu_count() or 1) * multiplier)
