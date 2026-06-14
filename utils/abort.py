"""Fast-fail guard so a blocked source never stalls the whole pipeline.

Scrapers behind aggressive anti-bot (Zonaprop, TripAdvisor, MercadoLibre, the
social sources) can answer 403/429 on every request and otherwise keep retrying
detail pages for many minutes. :class:`AbortMixin` gives them a uniform way to
give up early: after a few consecutive fetch failures, or once a hard time
budget is exceeded, the scraper aborts and returns whatever it has collected.

After a scrape, ``self.aborted_reason`` holds ``""`` (finished normally),
``"blocked"`` (too many consecutive errors) or ``"timeout"`` (time budget hit),
which the pipeline maps to the live source status shown in the TUI.
"""
import logging
import time

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_ERRORS = 3
MAX_SOURCE_TIMEOUT_SECONDS = 120


class AbortMixin:
    """Mixin that lets a scraper abort early when a source is stuck.

    Usage: call :meth:`_start_guard` at the top of the search method, then
    :meth:`_record_fetch` after every fetch (True = got a page, False = failed),
    and break the loop when :meth:`_should_abort` returns True.
    """

    MAX_CONSECUTIVE_ERRORS = MAX_CONSECUTIVE_ERRORS
    MAX_SOURCE_TIMEOUT_SECONDS = MAX_SOURCE_TIMEOUT_SECONDS

    def _start_guard(self) -> None:
        """Reset the abort counters at the start of a scrape."""
        self._guard_started = time.monotonic()
        self._consecutive_errors = 0
        self.aborted_reason = ""

    def _record_fetch(self, ok: bool) -> None:
        """Record a fetch outcome (resets the streak on success)."""
        if ok:
            self._consecutive_errors = 0
        else:
            self._consecutive_errors = getattr(self, "_consecutive_errors", 0) + 1

    def _guard_elapsed(self) -> float:
        """Seconds since :meth:`_start_guard` was last called."""
        return time.monotonic() - getattr(self, "_guard_started", time.monotonic())

    def _should_abort(self, consecutive_errors: int | None = None,
                      elapsed: float | None = None) -> bool:
        """Decide whether the scraper should abort.

        Args:
            consecutive_errors: Consecutive fetch failures. Defaults to the
                internally tracked count from :meth:`_record_fetch`.
            elapsed: Seconds spent so far. Defaults to the internally tracked
                time since :meth:`_start_guard`.

        Returns:
            True if there have been ``MAX_CONSECUTIVE_ERRORS`` consecutive
            errors or more than ``MAX_SOURCE_TIMEOUT_SECONDS`` have elapsed.
        """
        if consecutive_errors is None:
            consecutive_errors = getattr(self, "_consecutive_errors", 0)
        if elapsed is None:
            elapsed = self._guard_elapsed()

        name = getattr(self, "SOURCE", None) or getattr(self, "source", None) or type(self).__name__
        if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
            self.aborted_reason = "blocked"
            logger.warning("%s aborting: %d consecutive errors", name, consecutive_errors)
            return True
        if elapsed > self.MAX_SOURCE_TIMEOUT_SECONDS:
            self.aborted_reason = "timeout"
            logger.warning("%s aborting: timeout after %.0fs", name, elapsed)
            return True
        return False
