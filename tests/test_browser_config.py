"""Tests for centralized browser fetch configuration (Sprint M)."""
from utils.browser_config import (
    BLOCKED_RESOURCE_TYPES,
    get_fetch_kwargs,
    get_stealth_fetch_kwargs,
)


def test_get_stealth_fetch_kwargs_default():
    kwargs = get_stealth_fetch_kwargs(network_idle=False, block_resources=True)
    assert kwargs["network_idle"] is False
    assert kwargs["disable_resources"] is True
    assert isinstance(kwargs["blocked_domains"], set)
    assert kwargs["block_ads"] is True


def test_get_stealth_fetch_kwargs_complete_mode():
    kwargs = get_stealth_fetch_kwargs(network_idle=True, block_resources=False)
    assert kwargs["network_idle"] is True
    assert "disable_resources" not in kwargs


def test_get_stealth_fetch_kwargs_solve_cloudflare():
    assert get_stealth_fetch_kwargs(solve_cloudflare=True)["solve_cloudflare"] is True
    assert "solve_cloudflare" not in get_stealth_fetch_kwargs(solve_cloudflare=False)


def test_blocked_resource_types_not_empty():
    assert BLOCKED_RESOURCE_TYPES
    assert "image" in BLOCKED_RESOURCE_TYPES


def test_get_fetch_kwargs_static():
    kwargs = get_fetch_kwargs(timeout=5)
    assert kwargs["timeout"] == 5
    assert kwargs["stealthy_headers"] is True
