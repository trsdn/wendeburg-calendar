"""Bounded HTTP status retries shared by robots and content requests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time

from wendeburg_calendar.http.errors import FetchPolicyError, FetchTransportError
from wendeburg_calendar.http.fetcher import Fetcher, RawResponse

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    fallback_backoff_cap_seconds: float = 2.0
    total_wait_budget_seconds: float = 30.0


@dataclass(frozen=True)
class RetryOutcome:
    response: RawResponse
    attempts: int


class RetryExecutor:
    """Perform one-hop GETs with bounded, standards-aware status retries."""

    def __init__(
        self,
        *,
        policy: RetryPolicy = RetryPolicy(),
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._policy = policy
        self._sleeper = sleeper
        self._clock = clock

    def get_single(
        self,
        fetcher: Fetcher,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
        before_attempt: Callable[[], None] | None = None,
    ) -> RetryOutcome:
        waited = 0.0
        for attempt in range(1, self._policy.max_attempts + 1):
            if before_attempt is not None:
                before_attempt()
            try:
                response = fetcher.get_single(url, extra_headers=extra_headers)
            except FetchTransportError as exc:
                exception_class = (
                    getattr(exc, "transport_exception_class", None)
                    or type(exc).__name__
                )
                raise FetchTransportError(
                    url=url,
                    exception_class=exception_class,
                    attempts=attempt,
                ) from exc
            except FetchPolicyError:
                raise
            except Exception as exc:
                raise FetchTransportError(
                    url=url,
                    exception_class=type(exc).__name__,
                    attempts=attempt,
                ) from exc

            if (
                response.status_code not in _RETRYABLE_STATUS_CODES
                or attempt >= self._policy.max_attempts
            ):
                return RetryOutcome(response=response, attempts=attempt)

            delay = self._retry_delay(response, attempt)
            remaining_budget = self._policy.total_wait_budget_seconds - waited
            if delay > remaining_budget:
                # Do not retry before the server's requested time, and do not
                # exceed the bounded wait budget for this request.
                return RetryOutcome(response=response, attempts=attempt)
            if delay > 0:
                self._sleeper(delay)
                waited += delay

        raise AssertionError("retry loop must return or raise")

    def _retry_delay(self, response: RawResponse, attempt: int) -> float:
        if response.status_code == 429:
            retry_after = _header_value(response.headers, "retry-after")
            parsed = _parse_retry_after(retry_after, self._clock())
            if parsed is not None:
                return parsed
        fallback = float(2 ** (attempt - 1))
        return min(fallback, self._policy.fallback_backoff_cap_seconds)


def _header_value(headers: dict[str, str], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return value
    return None


def _parse_retry_after(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return float(int(stripped))
    try:
        target = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, OverflowError):
        return None
    if target is None:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (target - now).total_seconds())
