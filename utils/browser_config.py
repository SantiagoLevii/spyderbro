"""Centralized browser fetch configuration for maximum scraping performance.

Builds the keyword arguments passed to Scrapling's fetchers in one place so the
speed knobs (network-idle waiting, resource/ad blocking, timeouts) are
consistent across every scraper and driven by ``config.settings``.

Note: Scrapling exposes resource blocking as ``disable_resources`` (a bool that
drops fonts/images/media/stylesheets/etc.) plus ``blocked_domains`` (a set) and
``block_ads``. The public ``block_resources`` toggle here maps onto all three.

NOTE: some sources need ``network_idle=True`` regardless of the global setting
(they render their content with heavy JS). Those scrapers must pass an explicit
``network_idle=True`` override to :func:`get_stealth_fetch_kwargs` rather than
relying on ``settings.NETWORK_IDLE``. See ``scrapers/mercadolibre.py``.
"""
from config.settings import settings

# Tracking / analytics / ad domains worth blocking on every request.
BLOCKED_DOMAINS = {
    "google-analytics.com",
    "googletagmanager.com",
    "facebook.net",
    "doubleclick.net",
    "googlesyndication.com",
    "hotjar.com",
    "segment.io",
    "mixpanel.com",
    "amplitude.com",
    "sentry.io",
}

# Resource types Scrapling drops when disable_resources is on (informational —
# the actual list is internal to Scrapling; kept here for the metrics estimate).
BLOCKED_RESOURCE_TYPES = [
    "image", "media", "font", "stylesheet", "texttrack",
    "object", "beacon", "csp_report", "imageset",
]


def get_stealth_fetch_kwargs(
    network_idle: bool | None = None,
    block_resources: bool | None = None,
    timeout: int = 12000,
    solve_cloudflare: bool = False,
) -> dict:
    """Build optimized kwargs for ``StealthyFetcher.fetch`` / ``DynamicFetcher.fetch``.

    Args:
        network_idle: Wait for network idle (slower, more complete). Defaults to
            ``settings.NETWORK_IDLE``.
        block_resources: Drop images/CSS/fonts/ads (faster). Defaults to
            ``settings.BLOCK_RESOURCES``.
        timeout: Timeout in milliseconds.
        solve_cloudflare: Enable Cloudflare challenge solving.

    Returns:
        A kwargs dict ready to splat into a Scrapling fetch call. Callers add
        per-scraper extras (``cookies``, ``proxy``, ``page_action``).
    """
    network_idle = settings.NETWORK_IDLE if network_idle is None else network_idle
    block_resources = settings.BLOCK_RESOURCES if block_resources is None else block_resources

    kwargs: dict = {"headless": True, "network_idle": network_idle, "timeout": timeout}
    if solve_cloudflare:
        kwargs["solve_cloudflare"] = True
    if block_resources:
        kwargs["disable_resources"] = True
        kwargs["blocked_domains"] = set(BLOCKED_DOMAINS)
        kwargs["block_ads"] = True
    return kwargs


def get_fetch_kwargs(timeout: int = 8000) -> dict:
    """Build optimized kwargs for static ``Fetcher.get`` requests.

    Args:
        timeout: Timeout in seconds (Fetcher.get takes seconds, not ms).

    Returns:
        A kwargs dict for ``Fetcher.get``.
    """
    return {"timeout": timeout, "stealthy_headers": True}
