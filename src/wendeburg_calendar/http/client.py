"""HarvestClient: the single, policy-enforcing gateway through which every
source adapter must fetch content.

Adapters never see the raw `Fetcher` - they only ever get a `HarvestClient`
instance, scoped to one source's configured `allowed_hosts`. This is what
makes it structurally impossible for an adapter to "accidentally" bypass
robots.txt, the host allowlist, or the content-size limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from wendeburg_calendar.http.errors import (
    FetchTooLargeError,
    HostNotAllowedError,
    HttpStatusError,
    InvalidSchemeError,
    TooManyRedirectsError,
)
from wendeburg_calendar.http.fetcher import Fetcher
from wendeburg_calendar.http.retry import RetryExecutor
from wendeburg_calendar.http.robots import RobotsChecker
from wendeburg_calendar.http.throttle import HostRateLimiter
from wendeburg_calendar.util.hashing import sha256_hex

_MAX_REDIRECTS = 5
_ALLOWED_SCHEMES = {"http", "https"}


@dataclass(frozen=True)
class FetchResult:
    url: str
    status_code: int
    content: bytes
    headers: dict[str, str]
    not_modified: bool
    content_hash: str
    attempts: int = 1


class ConditionalCache(Protocol):
    """Backing store for HTTP conditional-GET metadata (ETag / Last-Modified)."""

    def get_cache_entry(self, url: str) -> tuple[str | None, str | None] | None:
        """Return (etag, last_modified) for a URL, or None if unknown."""
        ...

    def set_cache_entry(
        self, url: str, etag: str | None, last_modified: str | None, content_hash: str
    ) -> None: ...

    def get_cached_content_hash(self, url: str) -> str | None: ...


class HarvestClient:
    """Robots-aware, host-allowlisted, size-bounded HTTP GET for adapters."""

    def __init__(
        self,
        fetcher: Fetcher,
        robots: RobotsChecker,
        allowed_hosts: set[str],
        max_content_bytes: int,
        cache: ConditionalCache | None = None,
        rate_limiter: HostRateLimiter | None = None,
        min_request_delay_seconds: float = 0.0,
        retry_executor: RetryExecutor | None = None,
    ):
        self._fetcher = fetcher
        self._robots = robots
        self._allowed_hosts = {h.lower() for h in allowed_hosts}
        self._max_content_bytes = max_content_bytes
        self._cache = cache
        self._rate_limiter = rate_limiter or HostRateLimiter()
        self._min_request_delay_seconds = max(0.0, min_request_delay_seconds)
        if retry_executor is not None:
            self._retry = retry_executor
        elif getattr(fetcher, "is_offline", False):
            self._retry = RetryExecutor(sleeper=lambda _seconds: None)
        else:
            self._retry = RetryExecutor()

    def _validate_hop(self, url: str) -> float | None:
        parts = urlsplit(url)
        if parts.scheme not in _ALLOWED_SCHEMES:
            raise InvalidSchemeError(f"Refusing non-http(s) scheme in {url!r}")
        if parts.hostname is None or parts.hostname.lower() not in self._allowed_hosts:
            raise HostNotAllowedError(
                f"Host {parts.hostname!r} is not in the allowlist {sorted(self._allowed_hosts)}"
            )
        rules = self._robots.check(url)
        return max(self._min_request_delay_seconds, rules.crawl_delay or 0.0)

    def get(self, url: str, use_cache: bool = True) -> FetchResult:
        """Fetch `url`, enforcing scheme/host/robots policy on every redirect hop."""
        current = url
        conditional_headers: dict[str, str] = {}

        if use_cache and self._cache is not None:
            entry = self._cache.get_cache_entry(url)
            if entry:
                etag, last_modified = entry
                if etag:
                    conditional_headers["If-None-Match"] = etag
                if last_modified:
                    conditional_headers["If-Modified-Since"] = last_modified

        for hop in range(_MAX_REDIRECTS + 1):
            crawl_delay = self._validate_hop(current)
            current_parts = urlsplit(current)
            host_key = (current_parts.hostname or current_parts.netloc).lower()
            outcome = self._retry.get_single(
                self._fetcher,
                current,
                extra_headers=conditional_headers,
                before_attempt=lambda: self._rate_limiter.wait(host_key, crawl_delay),
            )
            resp = outcome.response

            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    raise HttpStatusError(current, resp.status_code, outcome.attempts)
                current = urljoin(current, location)
                # Redirect revalidation: loop back and re-validate the new
                # hop's scheme/host/robots before following it. Conditional
                # headers are dropped after the first hop's target changes.
                conditional_headers = {}
                continue

            if resp.status_code == 304:
                content_hash = (
                    self._cache.get_cached_content_hash(url)
                    if self._cache is not None
                    else None
                )
                return FetchResult(
                    url=resp.url,
                    status_code=304,
                    content=b"",
                    headers=resp.headers,
                    not_modified=True,
                    content_hash=content_hash or "",
                    attempts=outcome.attempts,
                )

            if resp.status_code != 200:
                raise HttpStatusError(current, resp.status_code, outcome.attempts)

            if len(resp.content) > self._max_content_bytes:
                raise FetchTooLargeError(
                    f"Response for {current} exceeded {self._max_content_bytes} bytes"
                )

            content_hash = sha256_hex(resp.content)
            if self._cache is not None:
                self._cache.set_cache_entry(
                    url,
                    resp.headers.get("etag"),
                    resp.headers.get("last-modified"),
                    content_hash,
                )
            return FetchResult(
                url=resp.url,
                status_code=200,
                content=resp.content,
                headers=resp.headers,
                not_modified=False,
                content_hash=content_hash,
                attempts=outcome.attempts,
            )

        raise TooManyRedirectsError(f"Exceeded {_MAX_REDIRECTS} redirects starting at {url!r}")

    def get_discovery(self, url: str) -> FetchResult:
        """Fetch a seed/listing/sitemap with a response body on every run.

        Discovery resources define the candidate set for a harvest. Reusing
        their ETag/Last-Modified validators can yield a bodyless 304, which
        prevents adapters from revisiting detail resources that may have
        changed independently. Discovery therefore bypasses conditional
        request headers while retaining all normal policy checks and cache
        metadata updates. Detail fetches should continue to use ``get()``.
        """
        result = self.get(url, use_cache=False)
        if result.not_modified:
            # A server must not return 304 to an unconditional request. Treat
            # that as an unusable discovery response so coverage stays
            # PARTIAL rather than silently accepting an empty candidate set.
            raise HttpStatusError(url, result.status_code, result.attempts)
        return result
