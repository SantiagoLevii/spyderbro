import time

from utils.rate_limiter import RateLimiter


def test_allows_up_to_limit_without_waiting():
    limiter = RateLimiter(requests_per_minute=3)
    start = time.monotonic()
    for _ in range(3):
        limiter.acquire_sync()
    assert time.monotonic() - start < 1.0


def test_wait_time_positive_when_window_full():
    limiter = RateLimiter(requests_per_minute=2)
    limiter.acquire_sync()
    limiter.acquire_sync()
    with limiter._lock:
        wait = limiter._wait_time()
    assert wait > 0


def test_window_frees_old_slots():
    limiter = RateLimiter(requests_per_minute=2)
    limiter._timestamps.extend([time.monotonic() - 61, time.monotonic() - 61])
    with limiter._lock:
        wait = limiter._wait_time()
    assert wait == 0


async def test_async_acquire_within_limit_is_fast():
    limiter = RateLimiter(requests_per_minute=5)
    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire()
    assert time.monotonic() - start < 1.0


def test_minimum_one_request_per_minute():
    limiter = RateLimiter(requests_per_minute=0)
    assert limiter.requests_per_minute == 1
