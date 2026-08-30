"""Wendeburg municipal event listing adapter.

Two known listing endpoints act as seeds:
  - https://www.wendeburg.de/freizeit-kultur/veranstaltungen/veranstaltungen/
  - https://www.wendeburg.de/regional/veranstaltungen/suche.html

Each seed page links to either a per-event HTML detail page or, in some
cases, directly to a stable `.ical` export
(".../veranstaltung/{slug}-{event-id}-26610.ical"). Detail HTML pages are
in turn checked for an embedded `.ical` link before ever falling back to
LLM extraction - ICS is always preferred when it is discoverable and
fetchable.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from wendeburg_calendar.harvest.coverage import DiscoveryResult, SeedFetchOutcome
from wendeburg_calendar.http.client import HarvestClient
from wendeburg_calendar.http.errors import (
    FetchPolicyError,
    diagnostic_for_exception,
    failure_diagnostic,
)
from wendeburg_calendar.llm.extractor import extract_via_llm
from wendeburg_calendar.parsing.ics_parser import IcsParseError, parse_ics
from wendeburg_calendar.sources.base import CandidateFetchResult, SourceAdapter
from wendeburg_calendar.sources.registry import register

_DETAIL_PATH_MARKER = "/veranstaltungen/veranstaltung/"
_EMPTY_MARKERS = (
    "keine veranstaltungen",
    "keine ergebnisse",
    "keine treffer",
    "keine einträge",
)
_ICAL_HREF_RE = re.compile(r"\.ical$", re.IGNORECASE)


def _is_candidate_href(absolute_url: str) -> bool:
    lowered = absolute_url.lower()
    return lowered.endswith(".ical") or _DETAIL_PATH_MARKER in lowered


def _parse_listing(seed_url: str, html: str) -> tuple[list[str], bool]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    ordered: list[str] = []
    for a in soup.find_all("a", href=True):
        absolute = urljoin(seed_url, a["href"])
        if _is_candidate_href(absolute) and absolute not in seen:
            seen.add(absolute)
            ordered.append(absolute)

    explicit_empty = False
    if not ordered:
        text = soup.get_text(separator=" ").lower()
        explicit_empty = any(marker in text for marker in _EMPTY_MARKERS)

    return ordered, explicit_empty


def _find_ical_link(detail_url: str, html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        absolute = urljoin(detail_url, a["href"])
        if _ICAL_HREF_RE.search(absolute):
            return absolute
    return None


@register("wendeburg")
class WendeburgAdapter(SourceAdapter):
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

            html = result.content.decode("utf-8", errors="replace")
            urls, explicit_empty = _parse_listing(seed_url, html)
            outcomes.append(
                SeedFetchOutcome(
                    seed_url=seed_url, ok=True, urls=urls, explicit_empty=explicit_empty
                )
            )
        return DiscoveryResult(seed_outcomes=outcomes)

    def fetch_candidate(self, client: HarvestClient, url: str) -> CandidateFetchResult:
        if url.lower().endswith(".ical"):
            return self._fetch_ics_event(client, url)
        return self._fetch_html_event(client, url)

    def _fetch_ics_event(self, client: HarvestClient, url: str) -> CandidateFetchResult:
        try:
            result = client.get(url)
        except FetchPolicyError as exc:
            return CandidateFetchResult.failure(diagnostic_for_exception(url, exc))

        if result.not_modified:
            return self.reuse_unchanged(url)

        try:
            events = parse_ics(result.content, source_id=self.source_config.id, source_url=url)
        except IcsParseError as exc:
            return CandidateFetchResult.failure(
                failure_diagnostic(
                    url,
                    category="ICS parse failure",
                    exception=exc,
                )
            )
        if not events:
            return CandidateFetchResult.failure(
                failure_diagnostic(url, category="No usable VEVENT found")
            )
        return CandidateFetchResult.success(events)

    def _fetch_html_event(self, client: HarvestClient, url: str) -> CandidateFetchResult:
        try:
            result = client.get(url)
        except FetchPolicyError as exc:
            return CandidateFetchResult.failure(diagnostic_for_exception(url, exc))

        if result.not_modified:
            return self.reuse_unchanged(url)

        html = result.content.decode("utf-8", errors="replace")
        ical_link = _find_ical_link(url, html)
        if ical_link is not None:
            # Deterministic ICS is available and preferred; a failure here
            # is reported as a failed fetch, not silently downgraded to LLM.
            return self._fetch_ics_event(client, ical_link)

        if not self.context.llm_enabled or self.context.llm_client is None:
            return CandidateFetchResult.failure(
                failure_diagnostic(
                    url,
                    category="No deterministic ICS link and LLM extraction unavailable",
                )
            )

        event = extract_via_llm(
            html,
            source_id=self.source_config.id,
            source_url=url,
            llm_client=self.context.llm_client,
            max_input_chars=self.context.max_input_chars,
            cache=self.context.repository,
        )
        if event is None:
            return CandidateFetchResult.failure(
                failure_diagnostic(
                    url,
                    category="LLM did not find a reliable event",
                )
            )
        return CandidateFetchResult.success([event])
