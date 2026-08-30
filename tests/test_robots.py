from __future__ import annotations

import pytest

from wendeburg_calendar.http.errors import (
    FetchTransportError,
    HostNotAllowedError,
    InvalidSchemeError,
    RobotsDisallowedError,
)
from wendeburg_calendar.http.client import HarvestClient
from wendeburg_calendar.http.fetcher import RawResponse
from wendeburg_calendar.http.retry import RetryExecutor
from wendeburg_calendar.http.robots import RobotsChecker, parse_robots_txt
from wendeburg_calendar.http.throttle import HostRateLimiter

OUR_UA = "WendeburgCalendarBot/0.1 (+https://example.org/contact)"

ROBOTS_TXT = """
User-agent: WebCopier
User-agent: HTTrack
Disallow: /

User-agent: *
Disallow: /barrierefreiheit/barriere_melden.html
Disallow: /portal/kontakt.html
Disallow: /portal/suche.html
Disallow: /portal/suche2.html
Disallow: /portal/weiterempfehlen.html
Disallow: /allris/___tmp/
""".strip()


class FakeFetcher:
    """Canned single-hop fetcher for exercising RobotsChecker in isolation."""

    is_offline = True

    def __init__(
        self,
        responses: dict[str, RawResponse | Exception | list[RawResponse | Exception]],
    ):
        self._responses = responses
        self.calls: list[str] = []

    def get_single(self, url: str, extra_headers=None) -> RawResponse:
        self.calls.append(url)
        outcome = self._responses[url]
        if isinstance(outcome, list):
            outcome = outcome.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ok(url: str, body: str) -> RawResponse:
    return RawResponse(status_code=200, url=url, headers={}, content=body.encode("utf-8"))


def test_parse_robots_txt_selects_wildcard_group_for_our_bot():
    rules = parse_robots_txt(ROBOTS_TXT, OUR_UA)
    assert rules.deny_all is False
    assert rules.is_allowed("/freizeit-kultur/veranstaltungen/veranstaltungen/") is True
    assert rules.is_allowed("/portal/kontakt.html") is False
    assert rules.is_allowed("/portal/suche.html") is False
    assert rules.is_allowed("/allris/___tmp/anything") is False
    assert rules.is_allowed("/barrierefreiheit/barriere_melden.html") is False


def test_parse_robots_txt_disallows_all_for_webcopier_and_httrack():
    rules_webcopier = parse_robots_txt(ROBOTS_TXT, "WebCopier/5.0")
    assert rules_webcopier.deny_all is True
    assert rules_webcopier.is_allowed("/anything-at-all.html") is False

    rules_httrack = parse_robots_txt(ROBOTS_TXT, "HTTrack/3.49")
    assert rules_httrack.deny_all is True


def test_robots_checker_allows_permitted_path():
    fetcher = FakeFetcher({"https://www.wendeburg.de/robots.txt": _ok("https://www.wendeburg.de/robots.txt", ROBOTS_TXT)})
    checker = RobotsChecker(fetcher, OUR_UA)
    checker.check("https://www.wendeburg.de/freizeit-kultur/veranstaltungen/veranstaltungen/")  # must not raise


def test_robots_checker_blocks_disallowed_path():
    fetcher = FakeFetcher({"https://www.wendeburg.de/robots.txt": _ok("https://www.wendeburg.de/robots.txt", ROBOTS_TXT)})
    checker = RobotsChecker(fetcher, OUR_UA)
    with pytest.raises(RobotsDisallowedError):
        checker.check("https://www.wendeburg.de/portal/kontakt.html")


def test_404_robots_txt_means_no_restrictions():
    fetcher = FakeFetcher(
        {"https://example.invalid/robots.txt": RawResponse(404, "https://example.invalid/robots.txt", {}, b"")}
    )
    checker = RobotsChecker(fetcher, OUR_UA)
    checker.check("https://example.invalid/anything")  # must not raise


def test_410_robots_txt_means_no_restrictions():
    fetcher = FakeFetcher(
        {"https://example.invalid/robots.txt": RawResponse(410, "https://example.invalid/robots.txt", {}, b"")}
    )
    checker = RobotsChecker(fetcher, OUR_UA)
    checker.check("https://example.invalid/anything")  # must not raise


def test_server_error_fetching_robots_txt_fails_closed():
    fetcher = FakeFetcher(
        {"https://example.invalid/robots.txt": RawResponse(500, "https://example.invalid/robots.txt", {}, b"")}
    )
    checker = RobotsChecker(fetcher, OUR_UA)
    with pytest.raises(RobotsDisallowedError) as raised:
        checker.check("https://example.invalid/anything")
    assert "HTTP 500 Internal Server Error" in str(raised.value)
    assert "path=/robots.txt" in str(raised.value)
    assert "attempts=3" in str(raised.value)


def test_transport_error_fetching_robots_txt_fails_closed():
    fetcher = FakeFetcher(
        {"https://example.invalid/robots.txt": FetchTransportError("timeout")}
    )
    checker = RobotsChecker(fetcher, OUR_UA)
    with pytest.raises(RobotsDisallowedError) as raised:
        checker.check("https://example.invalid/anything")
    assert "exception=FetchTransportError" in str(raised.value)
    assert "path=/robots.txt" in str(raised.value)


def test_robots_txt_is_only_fetched_once_per_host(monkeypatch):
    fetcher = FakeFetcher({"https://example.invalid/robots.txt": _ok("https://example.invalid/robots.txt", "User-agent: *\nAllow: /\n")})
    checker = RobotsChecker(fetcher, OUR_UA)
    checker.check("https://example.invalid/a")
    checker.check("https://example.invalid/b")
    assert fetcher.calls == ["https://example.invalid/robots.txt"]


def test_harvest_client_rejects_non_http_scheme():
    fetcher = FakeFetcher({})
    checker = RobotsChecker(fetcher, OUR_UA)
    client = HarvestClient(fetcher, checker, allowed_hosts=set(), max_content_bytes=1_000_000)
    with pytest.raises(InvalidSchemeError):
        client.get("file:///etc/passwd")


def test_harvest_client_rejects_disallowed_host():
    fetcher = FakeFetcher({})
    checker = RobotsChecker(fetcher, OUR_UA)
    client = HarvestClient(fetcher, checker, allowed_hosts={"www.wendeburg.de"}, max_content_bytes=1_000_000)
    with pytest.raises(HostNotAllowedError):
        client.get("https://evil.example.invalid/page.html")


def test_harvest_client_revalidates_each_redirect_hop_against_allowlist():
    fetcher = FakeFetcher(
        {
            "https://www.wendeburg.de/robots.txt": _ok("https://www.wendeburg.de/robots.txt", "User-agent: *\nAllow: /\n"),
            "https://www.wendeburg.de/a": RawResponse(
                301, "https://www.wendeburg.de/a", {"location": "https://evil.example.invalid/b"}, b""
            ),
        }
    )
    checker = RobotsChecker(fetcher, OUR_UA)
    client = HarvestClient(fetcher, checker, allowed_hosts={"www.wendeburg.de"}, max_content_bytes=1_000_000)
    with pytest.raises(HostNotAllowedError):
        client.get("https://www.wendeburg.de/a")


def test_robots_wildcards_block_peine_query_urls_but_allow_event_details():
    rules = parse_robots_txt(
        """
        User-agent: *
        Disallow: /typo3/
        Disallow: /*?id=*
        Disallow: /*?*tx_solr
        """,
        OUR_UA,
    )

    assert rules.is_allowed("/d1i-item-page/example/?tx_toujou[type]=2")
    assert not rules.is_allowed("/typo3/index.php")
    assert not rules.is_allowed("/page?id=123")
    assert not rules.is_allowed("/search/?q=musik&tx_solr[page]=2")


def test_crawl_delay_is_enforced_with_injected_clock_without_real_sleep():
    class FakeClock:
        def __init__(self):
            self.now = 0.0
            self.sleeps: list[float] = []

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now += seconds

    robots_text = "User-agent: *\nCrawl-delay: 10\nAllow: /\n"
    fetcher = FakeFetcher(
        {
            "https://www.zweidorf-online.de/robots.txt": _ok(
                "https://www.zweidorf-online.de/robots.txt",
                robots_text,
            ),
            "https://www.zweidorf-online.de/Veranstaltungen/": _ok(
                "https://www.zweidorf-online.de/Veranstaltungen/",
                "<html>events</html>",
            ),
            "https://www.zweidorf-online.de/Startseite/": _ok(
                "https://www.zweidorf-online.de/Startseite/",
                "<html>home</html>",
            ),
        }
    )
    clock = FakeClock()
    limiter = HostRateLimiter(monotonic=clock.monotonic, sleeper=clock.sleep)
    checker = RobotsChecker(fetcher, OUR_UA, rate_limiter=limiter)
    client = HarvestClient(
        fetcher,
        checker,
        allowed_hosts={"www.zweidorf-online.de"},
        max_content_bytes=1_000_000,
        rate_limiter=limiter,
    )

    client.get("https://www.zweidorf-online.de/Veranstaltungen/", use_cache=False)
    client.get("https://www.zweidorf-online.de/Startseite/", use_cache=False)

    assert clock.sleeps == [10.0, 10.0]


def test_source_delay_uses_stricter_maximum_with_robots_crawl_delay():
    class FakeClock:
        def __init__(self):
            self.now = 0.0
            self.sleeps: list[float] = []

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now += seconds

    robots_text = "User-agent: *\nCrawl-delay: 3\nAllow: /\n"
    fetcher = FakeFetcher(
        {
            "https://example.test/robots.txt": _ok(
                "https://example.test/robots.txt",
                robots_text,
            ),
            "https://example.test/one": _ok(
                "https://example.test/one",
                "one",
            ),
            "https://example.test/two": _ok(
                "https://example.test/two",
                "two",
            ),
        }
    )
    clock = FakeClock()
    limiter = HostRateLimiter(monotonic=clock.monotonic, sleeper=clock.sleep)
    retry = RetryExecutor(sleeper=clock.sleep)
    checker = RobotsChecker(
        fetcher,
        OUR_UA,
        rate_limiter=limiter,
        min_request_delay_seconds=5,
        retry_executor=retry,
    )
    client = HarvestClient(
        fetcher,
        checker,
        allowed_hosts={"example.test"},
        max_content_bytes=1_000_000,
        rate_limiter=limiter,
        min_request_delay_seconds=5,
        retry_executor=retry,
    )

    client.get("https://example.test/one", use_cache=False)
    client.get("https://example.test/two", use_cache=False)

    assert clock.sleeps == [5.0, 5.0]


def test_robots_checker_uses_shared_retry_policy_for_retryable_status():
    robots_url = "https://example.test/robots.txt"
    fetcher = FakeFetcher(
        {
            robots_url: [
                RawResponse(503, robots_url, {}, b""),
                _ok(robots_url, "User-agent: *\nAllow: /\n"),
            ]
        }
    )
    sleeps: list[float] = []
    checker = RobotsChecker(
        fetcher,
        OUR_UA,
        retry_executor=RetryExecutor(sleeper=sleeps.append),
    )

    checker.check("https://example.test/events")

    assert fetcher.calls == [robots_url, robots_url]
    assert sleeps == [1.0]


def test_source_pacing_runs_before_each_retry_attempt():
    class FakeClock:
        def __init__(self):
            self.now = 0.0
            self.sleeps: list[float] = []

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now += seconds

    robots_url = "https://example.test/robots.txt"
    event_url = "https://example.test/events"
    fetcher = FakeFetcher(
        {
            robots_url: _ok(robots_url, "User-agent: *\nAllow: /\n"),
            event_url: [
                RawResponse(503, event_url, {}, b""),
                _ok(event_url, "event"),
            ],
        }
    )
    clock = FakeClock()
    limiter = HostRateLimiter(monotonic=clock.monotonic, sleeper=clock.sleep)
    retry = RetryExecutor(sleeper=clock.sleep)
    checker = RobotsChecker(
        fetcher,
        OUR_UA,
        rate_limiter=limiter,
        min_request_delay_seconds=5,
        retry_executor=retry,
    )
    client = HarvestClient(
        fetcher,
        checker,
        allowed_hosts={"example.test"},
        max_content_bytes=1_000_000,
        rate_limiter=limiter,
        min_request_delay_seconds=5,
        retry_executor=retry,
    )

    client.get(event_url, use_cache=False)

    # 5s from robots.txt to attempt 1, 1s retry backoff, then another
    # 4s of pacing so request starts remain five seconds apart.
    assert clock.sleeps == [5.0, 1.0, 4.0]


def test_repeated_matching_groups_are_merged_conservatively():
    rules = parse_robots_txt(
        """
        User-agent: *
        Disallow: /private/
        Crawl-delay: 3

        User-agent: *
        Allow: /private/public/
        Disallow: /tmp/
        Crawl-delay: 10
        """,
        OUR_UA,
    )

    assert rules.crawl_delay == 10
    assert not rules.is_allowed("/private/secret")
    assert rules.is_allowed("/private/public/page")
    assert not rules.is_allowed("/tmp/file")


def test_rate_limiter_retains_known_host_delay_when_next_call_has_none():
    now = [0.0]
    sleeps: list[float] = []

    def monotonic() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = HostRateLimiter(monotonic=monotonic, sleeper=sleep)
    limiter.wait("example.test", 10)
    limiter.wait("example.test", None)

    assert sleeps == [10.0]


def test_robots_redirect_to_non_allowlisted_host_fails_closed():
    fetcher = FakeFetcher(
        {
            "https://www.wendeburg.de/robots.txt": RawResponse(
                301,
                "https://www.wendeburg.de/robots.txt",
                {"location": "http://127.0.0.1/internal"},
                b"",
            ),
        }
    )
    checker = RobotsChecker(
        fetcher,
        OUR_UA,
        allowed_hosts={"www.wendeburg.de"},
    )

    with pytest.raises(RobotsDisallowedError):
        checker.check("https://www.wendeburg.de/events")

    assert fetcher.calls == ["https://www.wendeburg.de/robots.txt"]
