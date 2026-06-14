"""Hardware detection for optimal scraping configuration.

Detects physical/logical CPU count and available RAM, then recommends worker
and concurrency counts. Used by the scrapers and the TUI performance screen to
size thread pools to the machine instead of a fixed constant.
"""
import logging
from dataclasses import dataclass

import psutil

logger = logging.getLogger(__name__)

# Each headless Playwright worker costs roughly this much RAM.
RAM_PER_WORKER_MB = 150
WORKER_CAP = 64
RECOMMENDED_CAP = 32
MIN_WORKERS = 4


@dataclass
class HardwareProfile:
    """System hardware profile for scraping optimization."""

    cpu_count: int
    cpu_count_logical: int
    ram_total_gb: float
    ram_available_gb: float
    recommended_workers: int
    max_workers: int
    recommended_concurrent_sources: int


def detect_hardware() -> HardwareProfile:
    """Detect available hardware and compute optimal worker counts.

    Returns:
        A :class:`HardwareProfile` with CPU/RAM facts and recommendations.
        ``max_workers`` = ``min(64, logical_cpus * 8)``; ``recommended_workers``
        is half of that, capped by available RAM and 32, floored at 4.
    """
    cpu_physical = psutil.cpu_count(logical=False) or 1
    cpu_logical = psutil.cpu_count(logical=True) or 1
    vm = psutil.virtual_memory()
    ram_total = vm.total / (1024 ** 3)
    ram_available = vm.available / (1024 ** 3)

    max_workers = min(WORKER_CAP, cpu_logical * 8)
    ram_workers = int(ram_available * 1024 / RAM_PER_WORKER_MB)
    recommended = max(MIN_WORKERS, min(max_workers // 2, ram_workers, RECOMMENDED_CAP))
    concurrent_sources = max(2, min(8, recommended // 4))

    profile = HardwareProfile(
        cpu_count=cpu_physical,
        cpu_count_logical=cpu_logical,
        ram_total_gb=round(ram_total, 1),
        ram_available_gb=round(ram_available, 1),
        recommended_workers=recommended,
        max_workers=max_workers,
        recommended_concurrent_sources=concurrent_sources,
    )
    logger.debug("Hardware profile: %s", profile)
    return profile
