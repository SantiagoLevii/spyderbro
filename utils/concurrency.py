import os


def get_optimal_workers() -> int:
    """Calculate optimal thread workers for I/O-bound scraping.

    Formula: min(32, (os.cpu_count() or 1) * 4). For I/O-bound web
    scraping, 4x CPUs is the sweet spot.

    Returns:
        Number of workers to use in ThreadPoolExecutor.
    """
    return min(32, (os.cpu_count() or 1) * 4)
