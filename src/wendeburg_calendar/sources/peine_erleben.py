"""Peine erleben adapter using the public event sitemap and JSON-LD details."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit
from xml.etree import ElementTree

from wendeburg_calendar.harvest.coverage import DiscoveryResult, SeedFetchOutcome
from wendeburg_calendar.http.client import HarvestClient
from wendeburg_calendar.http.errors import (
    FetchPolicyError,
    diagnostic_for_exception,
    failure_diagnostic,
)
from wendeburg_calendar.llm.extractor import extract_via_llm
from wendeburg_calendar.parsing.jsonld_events import parse_jsonld_events
from wendeburg_calendar.parsing.peine_d1 import parse_peine_d1_event
from wendeburg_calendar.sources.base import CandidateFetchResult, SourceAdapter
from wendeburg_calendar.sources.registry import register

_SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _sitemap_locations(content: bytes) -> list[str]:
    root = ElementTree.fromstring(content)
    return [
        node.text.strip()
        for node in root.findall(".//s:loc", _SITEMAP_NS)
        if node.text and node.text.strip()
    ]


def _is_event_sitemap(url: str) -> bool:
    values = parse_qs(urlsplit(url).query).get("sitemap", [])
    return any(value.casefold() == "destinationdataevent" for value in values)


def _is_safe_event_url(url: str) -> bool:
    lowered = url.casefold()
    parts = urlsplit(url)
    query = unquote(parts.query).casefold()
    return (
        parts.scheme in {"http", "https"}
        and "/typo3/" not in parts.path.casefold()
        and not query.startswith("id=")
        and "&id=" not in query
        and "tx_solr" not in query
        and "d1i-item-page/" in lowered
    )


@register("peine-erleben")
class PeineErlebenAdapter(SourceAdapter):
    def discover(self, client: HarvestClient) -> DiscoveryResult:
        outcomes: list[SeedFetchOutcome] = []
        for seed_url in self.source_config.seed_urls:
            try:
                root_result = client.get_discovery(seed_url)
            except FetchPolicyError as exc:
                outcomes.append(
                    SeedFetchOutcome(
                        seed_url=seed_url,
                        ok=False,
                        note=diagnostic_for_exception(seed_url, exc),
                    )
                )
                continue

            try:
                child_sitemaps = [
                    url for url in _sitemap_locations(root_result.content) if _is_event_sitemap(url)
                ]
            except ElementTree.ParseError as exc:
                outcomes.append(
                    SeedFetchOutcome(
                        seed_url=seed_url,
                        ok=False,
                        note=failure_diagnostic(
                            seed_url,
                            category="Invalid sitemap XML",
                            exception=exc,
                        ),
                    )
                )
                continue

            event_urls: list[str] = []
            child_failed = False
            child_failure_notes: list[str] = []
            for sitemap_url in child_sitemaps[:3]:
                try:
                    child_result = client.get_discovery(sitemap_url)
                except FetchPolicyError as exc:
                    child_failed = True
                    child_failure_notes.append(
                        diagnostic_for_exception(sitemap_url, exc)
                    )
                    continue
                try:
                    locations = _sitemap_locations(child_result.content)
                except ElementTree.ParseError as exc:
                    child_failed = True
                    child_failure_notes.append(
                        failure_diagnostic(
                            sitemap_url,
                            category="Invalid child sitemap XML",
                            exception=exc,
                        )
                    )
                    continue
                for event_url in locations:
                    if _is_safe_event_url(event_url) and event_url not in event_urls:
                        event_urls.append(event_url)
                        if len(event_urls) > self.context.max_events_per_source:
                            break
                if len(event_urls) > self.context.max_events_per_source:
                    break

            if child_failure_notes:
                outcome_note = " | ".join(child_failure_notes)
            elif not child_sitemaps:
                outcome_note = failure_diagnostic(
                    seed_url,
                    category="No destinationdataevent sitemap advertised",
                )
            else:
                outcome_note = ""
            outcomes.append(
                SeedFetchOutcome(
                    seed_url=seed_url,
                    ok=bool(child_sitemaps) and not child_failed,
                    urls=event_urls,
                    explicit_empty=bool(child_sitemaps) and not event_urls and not child_failed,
                    note=outcome_note,
                )
            )
        return DiscoveryResult(seed_outcomes=outcomes)

    def fetch_candidate(self, client: HarvestClient, url: str) -> CandidateFetchResult:
        try:
            result = client.get(url)
        except FetchPolicyError as exc:
            return CandidateFetchResult.failure(diagnostic_for_exception(url, exc))
        if result.not_modified:
            return self.reuse_unchanged(url)

        html = result.content.decode("utf-8", errors="replace")
        events = parse_jsonld_events(
            html,
            source_id=self.source_config.id,
            source_url=url,
        )
        if events:
            return CandidateFetchResult.success(events)

        events = parse_peine_d1_event(
            html,
            source_id=self.source_config.id,
            source_url=url,
        )
        if events:
            return CandidateFetchResult.success(events)

        if not self.context.llm_enabled or self.context.llm_client is None:
            return CandidateFetchResult.failure(
                failure_diagnostic(
                    url,
                    category="No deterministic event data and LLM extraction unavailable",
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
                    category="No reliable event extracted",
                )
            )
        return CandidateFetchResult.success([event])
