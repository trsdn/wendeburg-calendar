"""Shared per-host crawl-delay enforcement with injectable time primitives."""

from __future__ import annotations

import time
from collections.abc import Callable


class HostRateLimiter:
    """Enforce a minimum interval between request starts for each host.

    The limiter is shared across all source clients in one harvest run.
    Tests can inject a fake monotonic clock and sleeper, so a ten-second
    robots.txt crawl delay is verified without real waiting.
    """

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._last_started: dict[str, float] = {}
        self._known_delays: dict[str, float] = {}

    def wait(self, host_key: str, delay_seconds: float | None) -> None:
        if delay_seconds is not None:
            self._known_delays[host_key] = max(
                self._known_delays.get(host_key, 0.0),
                max(0.0, delay_seconds),
            )
        delay = self._known_delays.get(host_key, 0.0)
        now = self._monotonic()
        previous = self._last_started.get(host_key)
        if previous is not None and delay > 0:
            remaining = delay - (now - previous)
            if remaining > 0:
                self._sleeper(remaining)
                now = self._monotonic()
        self._last_started[host_key] = now
