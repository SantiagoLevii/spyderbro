import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def async_retry(
    func: Callable[[], Awaitable[T]],
    max_retries: int = 3,
    base_delay: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Run an async callable with exponential backoff retries.

    Delay between attempts: base_delay * (2 ** attempt) + random jitter.
    Every retry is logged with the attempt number and the error.

    Args:
        func: Zero-argument async callable to run.
        max_retries: Maximum number of attempts.
        base_delay: Base delay in seconds for the backoff.
        exceptions: Exception types that trigger a retry.

    Returns:
        The return value of func.

    Raises:
        The last exception if all attempts fail.
    """
    last_error: BaseException | None = None
    for attempt in range(max_retries):
        try:
            return await func()
        except exceptions as exc:
            last_error = exc
            if attempt == max_retries - 1:
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(
                "Attempt %d/%d failed (%s) — retrying in %.1fs",
                attempt + 1, max_retries, exc, delay,
            )
            await asyncio.sleep(delay)

    logger.error("All %d attempts failed: %s", max_retries, last_error, exc_info=last_error)
    raise last_error  # type: ignore[misc]


def sync_retry(
    func: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Run a sync callable with exponential backoff retries.

    Args:
        func: Zero-argument callable to run.
        max_retries: Maximum number of attempts.
        base_delay: Base delay in seconds for the backoff.
        exceptions: Exception types that trigger a retry.

    Returns:
        The return value of func.

    Raises:
        The last exception if all attempts fail.
    """
    last_error: BaseException | None = None
    for attempt in range(max_retries):
        try:
            return func()
        except exceptions as exc:
            last_error = exc
            if attempt == max_retries - 1:
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(
                "Attempt %d/%d failed (%s) — retrying in %.1fs",
                attempt + 1, max_retries, exc, delay,
            )
            time.sleep(delay)

    logger.error("All %d attempts failed: %s", max_retries, last_error, exc_info=last_error)
    raise last_error  # type: ignore[misc]
