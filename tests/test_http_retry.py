from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
from pydantic import ValidationError

from wendeburg_calendar.config import SourceConfig
from wendeburg_calendar.harvest.pipeline import _bounded_diagnostics
from wendeburg_calendar.http.errors import (
    FetchTransportError,
    HttpStatusError,
)
from wendeburg_calendar.http.fetcher import RawResponse
from wendeburg_calendar.http.retry import RetryExecutor, RetryPolicy


class SequenceFetcher:
    is_offline = True

    def __init__(self, outcomes: list[RawResponse | Exception]):
        self._outcomes = list(outcomes)
        self.calls = 0

    def get_single(self, url: str, extra_headers=None) -> RawResponse:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(status: int, headers: dict[str, str] | None = None) -> RawResponse:
    return RawResponse(
        status_code=status,
        url="https://example.test/events",
        headers=headers or {},
        content=b"",
    )


def test_retry_after_numeric_is_honored():
    sleeps: list[float] = []
    fetcher = SequenceFetcher(
        [_response(429, {"Retry-After": "7"}), _response(200)]
    )

    outcome = RetryExecutor(sleeper=sleeps.append).get_single(
        fetcher,
        "https://example.test/events",
    )

    assert outcome.response.status_code == 200
    assert outcome.attempts == 2
    assert fetcher.calls == 2
    assert sleeps == [7.0]


def test_retry_after_http_date_is_honored():
    now = datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc)
    retry_at = format_datetime(now + timedelta(seconds=9), usegmt=True)
    sleeps: list[float] = []
    fetcher = SequenceFetcher(
        [_response(429, {"retry-after": retry_at}), _response(200)]
    )

    outcome = RetryExecutor(
        sleeper=sleeps.append,
        clock=lambda: now,
    ).get_single(fetcher, "https://example.test/events")

    assert outcome.attempts == 2
    assert sleeps == [9.0]


def test_retries_are_bounded_to_three_attempts_with_capped_fallback():
    sleeps: list[float] = []
    fetcher = SequenceFetcher(
        [_response(500), _response(502), _response(504), _response(200)]
    )

    outcome = RetryExecutor(sleeper=sleeps.append).get_single(
        fetcher,
        "https://example.test/events",
    )

    assert outcome.response.status_code == 504
    assert outcome.attempts == 3
    assert fetcher.calls == 3
    assert sleeps == [1.0, 2.0]


def test_retryable_5xx_can_recover():
    fetcher = SequenceFetcher([_response(503), _response(200)])

    outcome = RetryExecutor(sleeper=lambda _seconds: None).get_single(
        fetcher,
        "https://example.test/events",
    )

    assert outcome.response.status_code == 200
    assert outcome.attempts == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
def test_permanent_http_errors_are_not_retried(status: int):
    fetcher = SequenceFetcher([_response(status)])

    outcome = RetryExecutor(sleeper=lambda _seconds: None).get_single(
        fetcher,
        "https://example.test/events",
    )

    assert outcome.response.status_code == status
    assert outcome.attempts == 1
    assert fetcher.calls == 1


def test_retry_after_beyond_wait_budget_returns_without_retrying_early():
    sleeps: list[float] = []
    fetcher = SequenceFetcher([_response(429, {"retry-after": "31"})])

    outcome = RetryExecutor(
        policy=RetryPolicy(total_wait_budget_seconds=30),
        sleeper=sleeps.append,
    ).get_single(fetcher, "https://example.test/events")

    assert outcome.response.status_code == 429
    assert outcome.attempts == 1
    assert fetcher.calls == 1
    assert sleeps == []


def test_transport_diagnostic_reports_class_without_raw_exception_text():
    url = "https://example.test/events?token=super-secret#private"
    fetcher = SequenceFetcher(
        [
            FetchTransportError(
                "raw timeout text with super-secret",
                exception_class="ReadTimeout",
            )
        ]
    )

    with pytest.raises(FetchTransportError) as raised:
        RetryExecutor(sleeper=lambda _seconds: None).get_single(fetcher, url)

    diagnostic = str(raised.value)
    assert "exception=ReadTimeout" in diagnostic
    assert "host=example.test path=/events" in diagnostic
    assert "super-secret" not in diagnostic
    assert "token=" not in diagnostic
    assert "#private" not in diagnostic


def test_unwrapped_transport_exception_is_sanitized_by_class():
    url = "https://example.test/events?token=super-secret"
    fetcher = SequenceFetcher([TimeoutError("raw super-secret timeout")])

    with pytest.raises(FetchTransportError) as raised:
        RetryExecutor(sleeper=lambda _seconds: None).get_single(fetcher, url)

    diagnostic = str(raised.value)
    assert "exception=TimeoutError" in diagnostic
    assert "super-secret" not in diagnostic


def test_status_diagnostic_redacts_userinfo_query_fragment_and_sensitive_path():
    error = HttpStatusError(
        "https://user:password@example.test/events/token/super-secret"
        "?api_key=query-secret#fragment-secret",
        403,
        attempts=1,
    )

    diagnostic = str(error)
    assert "HTTP 403 Forbidden" in diagnostic
    assert "host=example.test" in diagnostic
    assert "user" not in diagnostic
    assert "password" not in diagnostic
    assert "super-secret" not in diagnostic
    assert "query-secret" not in diagnostic
    assert "fragment-secret" not in diagnostic
    assert "path=/events/<redacted>/<redacted>" in diagnostic


def test_source_minimum_request_delay_must_be_nonnegative():
    with pytest.raises(ValidationError):
        SourceConfig(
            id="invalid-delay",
            type="structured-html",
            allowed_hosts=["example.test"],
            min_request_delay_seconds=-0.1,
        )


def test_failure_diagnostics_volume_is_bounded_with_omission_count():
    rendered = _bounded_diagnostics(
        [f"failure-{index} " + ("x" * 1_000) for index in range(20)]
    )

    assert len(rendered) <= 2_000
    assert "omitted=" in rendered
