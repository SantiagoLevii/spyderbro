import pytest

from utils.retry import async_retry, sync_retry


def test_sync_retry_succeeds_first_try():
    assert sync_retry(lambda: 42) == 42


def test_sync_retry_recovers_after_failures():
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ValueError("transient")
        return "ok"

    assert sync_retry(flaky, max_retries=3, base_delay=0) == "ok"
    assert attempts["n"] == 3


def test_sync_retry_raises_after_max():
    def always_fails() -> None:
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        sync_retry(always_fails, max_retries=2, base_delay=0)


def test_sync_retry_only_catches_given_exceptions():
    def raises_type_error() -> None:
        raise TypeError("not retried")

    with pytest.raises(TypeError):
        sync_retry(raises_type_error, max_retries=3, exceptions=(ValueError,))


async def test_async_retry_succeeds_first_try():
    async def ok() -> int:
        return 7

    assert await async_retry(ok) == 7


async def test_async_retry_recovers_after_failures(monkeypatch):
    async def no_sleep(_): return None
    monkeypatch.setattr("utils.retry.asyncio.sleep", no_sleep)
    attempts = {"n": 0}

    async def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise ConnectionError("transient")
        return "ok"

    assert await async_retry(flaky, max_retries=3, base_delay=0) == "ok"


async def test_async_retry_raises_after_max(monkeypatch):
    async def no_sleep(_): return None
    monkeypatch.setattr("utils.retry.asyncio.sleep", no_sleep)

    async def always_fails() -> None:
        raise ConnectionError("permanent")

    with pytest.raises(ConnectionError):
        await async_retry(always_fails, max_retries=2, base_delay=0)
