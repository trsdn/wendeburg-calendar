"""Exceptions raised by the centralized HTTP/policy layer.

Adapters are expected to catch these (usually broadly, via `FetchPolicyError`)
and treat them as a failed fetch, which the harvest pipeline turns into
PARTIAL coverage rather than a crash or a silent "event is gone" signal.
"""

from __future__ import annotations

from http import HTTPStatus
import re
from urllib.parse import unquote, urlsplit

_MAX_PATH_CHARS = 180
_SENSITIVE_PATH_MARKERS = re.compile(
    r"(?:^|[-_.])(api[-_]?key|auth|bearer|key|password|secret|signature|token)(?:$|[-_.])",
    re.IGNORECASE,
)


def sanitized_endpoint(url: str) -> str:
    """Return a bounded host/path-only description safe for diagnostics."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "<unknown-host>").lower()
        try:
            if parts.port is not None:
                host = f"{host}:{parts.port}"
        except ValueError:
            host = "<invalid-host>"
        raw_segments = (parts.path or "/").split("/")
    except (TypeError, ValueError):
        return "host=<invalid-host> path=/"

    safe_segments: list[str] = []
    redact_next = False
    for segment in raw_segments:
        decoded = unquote(segment)
        looks_sensitive = (
            redact_next
            or bool(_SENSITIVE_PATH_MARKERS.search(decoded))
            or len(decoded) > 80
            or decoded.count(".") == 2 and len(decoded) > 40
        )
        safe_segments.append("<redacted>" if looks_sensitive else segment)
        redact_next = bool(_SENSITIVE_PATH_MARKERS.search(decoded))

    path = "/".join(safe_segments) or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    if len(path) > _MAX_PATH_CHARS:
        path = f"{path[:_MAX_PATH_CHARS - 3]}..."
    return f"host={host} path={path}"


def http_status_diagnostic(url: str, status_code: int, attempts: int = 1) -> str:
    try:
        reason = HTTPStatus(status_code).phrase
    except ValueError:
        reason = "Unknown Status"
    return (
        f"HTTP {status_code} {reason}; {sanitized_endpoint(url)}; "
        f"attempts={max(1, attempts)}"
    )


def failure_diagnostic(
    url: str,
    *,
    category: str,
    exception: BaseException | None = None,
    attempts: int = 1,
) -> str:
    exception_name = type(exception).__name__ if exception is not None else None
    detail = f" exception={exception_name};" if exception_name else ";"
    return (
        f"{category}{detail} {sanitized_endpoint(url)}; "
        f"attempts={max(1, attempts)}"
    )


def diagnostic_for_exception(
    url: str,
    exception: BaseException,
    *,
    category: str = "Fetch failure",
) -> str:
    if isinstance(exception, FetchPolicyError):
        diagnostic = getattr(exception, "diagnostic", None)
        if isinstance(diagnostic, str) and diagnostic:
            return diagnostic
    return failure_diagnostic(url, category=category, exception=exception)


class FetchPolicyError(Exception):
    """Base class for any policy-level refusal to fetch a URL."""


class InvalidSchemeError(FetchPolicyError):
    """URL scheme is not http/https."""


class HostNotAllowedError(FetchPolicyError):
    """URL host is not in the source's configured allowlist."""


class RobotsDisallowedError(FetchPolicyError):
    """robots.txt (or a fail-closed policy decision) forbids this URL."""

    def __init__(self, url: str, policy_diagnostic: str | None = None):
        request = sanitized_endpoint(url)
        if policy_diagnostic:
            diagnostic = f"Robots denied request; {request}; policy=({policy_diagnostic})"
        else:
            diagnostic = f"Robots disallowed; {request}; attempts=1"
        super().__init__(diagnostic)
        self.diagnostic = diagnostic


class FetchTooLargeError(FetchPolicyError):
    """Response body exceeded the configured byte limit."""


class TooManyRedirectsError(FetchPolicyError):
    """Redirect chain exceeded the configured hop limit."""


class FetchTransportError(FetchPolicyError):
    """Network-level failure (timeout, connection error, DNS, ...)."""

    def __init__(
        self,
        message: str | None = None,
        *,
        url: str | None = None,
        exception_class: str | None = None,
        attempts: int = 1,
    ):
        # ``message`` remains accepted for compatibility with existing custom
        # fetchers, but is intentionally never included in diagnostics.
        del message
        self.transport_exception_class = exception_class or type(self).__name__
        self.attempts = max(1, attempts)
        if url is None:
            diagnostic = (
                "Transport failure; "
                f"exception={self.transport_exception_class}; attempts={self.attempts}"
            )
        else:
            diagnostic = (
                "Transport failure; "
                f"exception={self.transport_exception_class}; "
                f"{sanitized_endpoint(url)}; attempts={self.attempts}"
            )
        super().__init__(diagnostic)
        self.diagnostic = diagnostic


class HttpStatusError(FetchPolicyError):
    """Upstream returned an unexpected/unusable HTTP status code."""

    def __init__(self, url: str, status_code: int, attempts: int = 1):
        diagnostic = http_status_diagnostic(url, status_code, attempts)
        super().__init__(diagnostic)
        self.url = url
        self.status_code = status_code
        self.attempts = max(1, attempts)
        self.diagnostic = diagnostic
