"""Generic adapter for known, stable structured HTML event-list profiles."""

from __future__ import annotations

from wendeburg_calendar.harvest.coverage import DiscoveryResult, SeedFetchOutcome
from wendeburg_calendar.http.client import HarvestClient
from wendeburg_calendar.http.errors import (
    FetchPolicyError,
    diagnostic_for_exception,
    failure_diagnostic,
)
from wendeburg_calendar.parsing.structured_html import (
    discover_kulturring_pages,
    parse_structured_events,
    profile_has_event_markup,
)
from wendeburg_calendar.sources.base import CandidateFetchResult, SourceAdapter
from wendeburg_calendar.sources.registry import register

_EMPTY_MARKERS = (
    "keine veranstaltungen",
    "keine termine",
    "keine einträge",
)


@register("structured-html")
class StructuredHtmlAdapter(SourceAdapter):
    def __init__(self, source_config, context):
        super().__init__(source_config, context)
        if not source_config.profile:
            raise ValueError("structured-html sources require a profile")
        self._profile = source_config.profile
        self._discovery_content: dict[str, bytes] = {}

    def discover(self, client: HarvestClient) -> DiscoveryResult:
        outcomes: list[SeedFetchOutcome] = []
        for seed_url in self.source_config.seed_urls:
            try:
                result = client.get_discovery(seed_url)
            except FetchPolicyError as exc:
                outcomes.append(
                    SeedFetchOutcome(
                        seed_url=seed_url,
                        ok=False,
                        note=diagnostic_for_exception(seed_url, exc),
                    )
                )
                continue

            self._discovery_content[seed_url] = result.content
            html = result.content.decode("utf-8", errors="replace")
            urls = [seed_url]
            if self._profile == "kulturring-peine":
                urls.extend(discover_kulturring_pages(seed_url, html))
            urls = urls[: self.context.max_events_per_source + 1]
            lowered = _page_text(html)
            explicit_empty = any(marker in lowered for marker in _EMPTY_MARKERS)
            if not profile_has_event_markup(self._profile, html) and not explicit_empty:
                outcomes.append(
                    SeedFetchOutcome(
                        seed_url=seed_url,
                        ok=False,
                        note=failure_diagnostic(
                            seed_url,
                            category=f"Expected {self._profile!r} event markup was not found",
                        ),
                    )
                )
                continue
            outcomes.append(
                SeedFetchOutcome(
                    seed_url=seed_url,
                    ok=True,
                    urls=urls,
                    explicit_empty=explicit_empty,
                )
            )
        return DiscoveryResult(seed_outcomes=outcomes)

    def fetch_candidate(self, client: HarvestClient, url: str) -> CandidateFetchResult:
        content = self._discovery_content.pop(url, None)
        if content is None:
            try:
                result = client.get(url)
            except FetchPolicyError as exc:
                return CandidateFetchResult.failure(diagnostic_for_exception(url, exc))
            if result.not_modified:
                return self.reuse_unchanged(url)
            content = result.content

        html = content.decode("utf-8", errors="replace")
        try:
            events = parse_structured_events(
                self._profile,
                html,
                source_id=self.source_config.id,
                source_url=url,
            )
        except (TypeError, ValueError) as exc:
            return CandidateFetchResult.failure(
                failure_diagnostic(
                    url,
                    category="Structured event parse failure",
                    exception=exc,
                )
            )
        if not events:
            return CandidateFetchResult.failure(
                failure_diagnostic(url, category="No exact events extracted")
            )
        return CandidateFetchResult.success(events)


def _page_text(html: str) -> str:
    """Cheap marker text normalization without exposing parser details."""
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True).casefold()
