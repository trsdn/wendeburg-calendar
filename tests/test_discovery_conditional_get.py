from __future__ import annotations

import json
from datetime import date, timedelta

from wendeburg_calendar.config import AppConfig
from wendeburg_calendar.harvest.coverage import Coverage
from wendeburg_calendar.harvest.pipeline import harvest_source
from wendeburg_calendar.http.client import HarvestClient
from wendeburg_calendar.http.fetcher import RawResponse
from wendeburg_calendar.http.robots import RobotsChecker


class ConditionalPeineFetcher:
    """Stateful live-equivalent fetcher with independent discovery/detail ETags."""

    is_offline = True
    robots_url = "https://www.peine-erleben.de/robots.txt"
    root_url = "https://www.peine-erleben.de/sitemap.xml"
    child_url = (
        "https://www.peine-erleben.de/sitemap.xml?sitemap=destinationdataevent"
    )

    def __init__(self, candidate_count: int = 40):
        self.detail_urls = [
            f"https://www.peine-erleben.de/d1i-item-page/event-{index}-{1000 + index}/"
            for index in range(candidate_count)
        ]
        self.detail_versions = {url: 1 for url in self.detail_urls}
        self.requests: list[tuple[str, dict[str, str], int]] = []
        self.force_unconditional_304: set[str] = set()
        self.forced_responses: dict[
            str,
            tuple[int, dict[str, str], bytes],
        ] = {}

    def change_detail(self, index: int) -> None:
        url = self.detail_urls[index]
        self.detail_versions[url] += 1

    def get_single(
        self, url: str, extra_headers: dict[str, str] | None = None
    ) -> RawResponse:
        request_headers = dict(extra_headers or {})

        forced = self.forced_responses.get(url)
        if forced is not None:
            status, headers, body = forced
            return self._record(url, request_headers, status, headers, body)

        if url == self.robots_url:
            return self._record(
                url,
                request_headers,
                200,
                {},
                b"User-agent: *\nAllow: /\n",
            )

        if url == self.root_url:
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<sitemap><loc>{self.child_url}</loc></sitemap>"
                "</sitemapindex>"
            ).encode()
            return self._conditional(url, request_headers, '"root-v1"', body)

        if url == self.child_url:
            locations = "".join(
                f"<url><loc>{detail_url}</loc></url>"
                for detail_url in self.detail_urls
            )
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"{locations}</urlset>"
            ).encode()
            return self._conditional(url, request_headers, '"child-v1"', body)

        if url in self.detail_versions:
            index = self.detail_urls.index(url)
            version = self.detail_versions[url]
            start = date(2026, 9, 1) + timedelta(days=index)
            payload = {
                "@context": "https://schema.org",
                "@type": "Event",
                "identifier": f"event-{index}",
                "name": f"Peine event {index}",
                "startDate": start.isoformat(),
                "description": f"Version {version}",
                "url": url,
            }
            body = (
                '<script type="application/ld+json">'
                f"{json.dumps(payload)}"
                "</script>"
            ).encode()
            return self._conditional(
                url,
                request_headers,
                f'"detail-{index}-v{version}"',
                body,
            )

        return self._record(url, request_headers, 404, {}, b"")

    def _conditional(
        self,
        url: str,
        request_headers: dict[str, str],
        etag: str,
        body: bytes,
    ) -> RawResponse:
        response_headers = {"etag": etag}
        if url in self.force_unconditional_304 or request_headers.get("If-None-Match") == etag:
            return self._record(url, request_headers, 304, response_headers, b"")
        return self._record(url, request_headers, 200, response_headers, body)

    def _record(
        self,
        url: str,
        request_headers: dict[str, str],
        status: int,
        response_headers: dict[str, str],
        body: bytes,
    ) -> RawResponse:
        self.requests.append((url, request_headers, status))
        return RawResponse(
            status_code=status,
            url=url,
            headers=response_headers,
            content=body,
        )


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "general": {
                "user_agent": "WendeburgCalendarBot/0.1 (+https://example.org/contact)"
            },
            "harvest": {"max_events_per_source": 100},
            "llm": {"enabled": False},
            "sources": [
                {
                    "id": "peine-erleben",
                    "type": "peine-erleben",
                    "seed_urls": [ConditionalPeineFetcher.root_url],
                    "allowed_hosts": ["www.peine-erleben.de"],
                }
            ],
        }
    )


def _client(fetcher: ConditionalPeineFetcher, repo, cfg: AppConfig) -> HarvestClient:
    source = cfg.sources[0]
    return HarvestClient(
        fetcher=fetcher,
        robots=RobotsChecker(
            fetcher,
            cfg.general.user_agent,
            allowed_hosts=set(source.allowed_hosts),
        ),
        allowed_hosts=set(source.allowed_hosts),
        max_content_bytes=cfg.harvest.max_content_bytes,
        cache=repo,
    )


def test_repeated_peine_harvest_revisits_all_candidates_and_detects_detail_change(repo):
    cfg = _config()
    source = cfg.sources[0]
    fetcher = ConditionalPeineFetcher(candidate_count=40)

    first = harvest_source(cfg, source, fetcher, repo, llm_client=None)

    assert first.coverage == Coverage.COMPLETE
    assert first.events_seen == 40
    assert first.reconcile_summary["observed"] == 40

    # This is the exact response the old discovery path received on the
    # immediate second run: cached sitemap validators produce an empty 304.
    probe_client = _client(fetcher, repo, cfg)
    assert probe_client.get(fetcher.root_url).not_modified
    assert probe_client.get(fetcher.child_url).not_modified

    fetcher.change_detail(0)
    second_request_start = len(fetcher.requests)
    second = harvest_source(cfg, source, fetcher, repo, llm_client=None)

    assert second.coverage == Coverage.COMPLETE
    assert second.events_seen == 40
    assert second.reconcile_summary["observed"] == 40

    second_requests = fetcher.requests[second_request_start:]
    discovery_requests = [
        request
        for request in second_requests
        if request[0] in {fetcher.root_url, fetcher.child_url}
    ]
    assert [(url, status) for url, _headers, status in discovery_requests] == [
        (fetcher.root_url, 200),
        (fetcher.child_url, 200),
    ]
    assert all(
        "If-None-Match" not in headers and "If-Modified-Since" not in headers
        for _url, headers, _status in discovery_requests
    )

    detail_requests = [
        request for request in second_requests if request[0] in fetcher.detail_urls
    ]
    assert len(detail_requests) == 40
    assert all("If-None-Match" in headers for _url, headers, _status in detail_requests)
    assert sum(status == 304 for _url, _headers, status in detail_requests) == 39
    assert sum(status == 200 for _url, _headers, status in detail_requests) == 1

    records = {record.source_url: record for record in repo.list_events_for_source(source.id)}
    assert len(records) == 40
    assert records[fetcher.detail_urls[0]].description == "Version 2"
    assert records[fetcher.detail_urls[0]].sequence == 1
    assert records[fetcher.detail_urls[1]].description == "Version 1"
    assert records[fetcher.detail_urls[1]].sequence == 0


def test_unconditional_discovery_304_is_partial_and_does_not_mark_events_missing(repo):
    cfg = _config()
    source = cfg.sources[0]
    fetcher = ConditionalPeineFetcher(candidate_count=1)

    first = harvest_source(cfg, source, fetcher, repo, llm_client=None)
    assert first.coverage == Coverage.COMPLETE
    assert first.events_seen == 1

    fetcher.force_unconditional_304.add(fetcher.root_url)
    second = harvest_source(cfg, source, fetcher, repo, llm_client=None)

    assert second.coverage == Coverage.PARTIAL
    assert second.events_seen == 0
    record = repo.list_events_for_source(source.id)[0]
    assert record.missing_count == 0


def test_persistent_candidate_throttle_is_partial_and_retains_absence_safety(repo):
    cfg = _config()
    source = cfg.sources[0]
    fetcher = ConditionalPeineFetcher(candidate_count=5)

    first = harvest_source(cfg, source, fetcher, repo, llm_client=None)
    assert first.coverage == Coverage.COMPLETE
    assert first.events_seen == 5

    throttled_url = fetcher.detail_urls[-1]
    fetcher.forced_responses[throttled_url] = (
        429,
        {"retry-after": "0"},
        b"challenge body must never appear in diagnostics",
    )
    second_request_start = len(fetcher.requests)

    second = harvest_source(cfg, source, fetcher, repo, llm_client=None)

    assert second.coverage == Coverage.PARTIAL
    assert second.events_seen == 4
    assert "HTTP 429 Too Many Requests" in second.notes
    assert "attempts=3" in second.notes
    assert "challenge body" not in second.notes
    stored_notes = repo.conn.execute(
        "SELECT notes FROM harvest_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()["notes"]
    assert stored_notes == second.notes
    second_requests = fetcher.requests[second_request_start:]
    assert sum(url == throttled_url for url, _headers, _status in second_requests) == 3
    records = repo.list_events_for_source(source.id)
    assert len(records) == 5
    assert all(record.missing_count == 0 for record in records)


def test_persistent_child_sitemap_failure_retains_diagnostic_note(repo):
    cfg = _config()
    source = cfg.sources[0]
    fetcher = ConditionalPeineFetcher(candidate_count=2)

    first = harvest_source(cfg, source, fetcher, repo, llm_client=None)
    assert first.coverage == Coverage.COMPLETE

    fetcher.forced_responses[fetcher.child_url] = (
        503,
        {},
        b"private upstream body",
    )
    second_request_start = len(fetcher.requests)

    second = harvest_source(cfg, source, fetcher, repo, llm_client=None)

    assert second.coverage == Coverage.PARTIAL
    assert second.events_seen == 0
    assert "HTTP 503 Service Unavailable" in second.notes
    assert "path=/sitemap.xml" in second.notes
    assert "attempts=3" in second.notes
    assert "private upstream body" not in second.notes
    second_requests = fetcher.requests[second_request_start:]
    assert sum(
        url == fetcher.child_url for url, _headers, _status in second_requests
    ) == 3
    records = repo.list_events_for_source(source.id)
    assert len(records) == 2
    assert all(record.missing_count == 0 for record in records)


def test_http_200_challenge_parse_failure_is_not_retried(repo):
    cfg = _config()
    source = cfg.sources[0]
    fetcher = ConditionalPeineFetcher(candidate_count=1)

    first = harvest_source(cfg, source, fetcher, repo, llm_client=None)
    assert first.coverage == Coverage.COMPLETE

    challenged_url = fetcher.detail_urls[0]
    fetcher.forced_responses[challenged_url] = (
        200,
        {},
        b"<html><title>Automated security challenge</title></html>",
    )
    second_request_start = len(fetcher.requests)

    second = harvest_source(cfg, source, fetcher, repo, llm_client=None)

    assert second.coverage == Coverage.PARTIAL
    assert second.events_seen == 0
    assert "No deterministic event data" in second.notes
    second_requests = fetcher.requests[second_request_start:]
    assert sum(
        url == challenged_url for url, _headers, _status in second_requests
    ) == 1
    record = repo.list_events_for_source(source.id)[0]
    assert record.missing_count == 0
