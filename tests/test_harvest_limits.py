from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from wendeburg_calendar.config import AppConfig, HarvestConfig, SourceConfig
from wendeburg_calendar.harvest.coverage import (
    Coverage,
    DiscoveryResult,
    SeedFetchOutcome,
)
from wendeburg_calendar.harvest.pipeline import harvest_source
from wendeburg_calendar.harvest.reconcile import reconcile_source
from wendeburg_calendar.model.event import ExtractionMethod, NormalizedEvent
from wendeburg_calendar.sources.base import CandidateFetchResult, SourceAdapter
from wendeburg_calendar.sources.registry import register
from wendeburg_calendar.util.time import BERLIN


def _event(title: str, x_id: str) -> NormalizedEvent:
    return NormalizedEvent(
        title=title,
        start=datetime(2026, 10, 1, 18, 0, tzinfo=BERLIN),
        source_id="limited",
        source_url=f"https://limited.test/{x_id}",
        event_url=f"https://limited.test/events/{x_id}",
        source_x_id=x_id,
        extraction_method=ExtractionMethod.STRUCTURED_HTML,
        raw_content_hash=x_id,
    )


@register("test-multi-limit")
class _LimitAdapter(SourceAdapter):
    def discover(self, client):
        return DiscoveryResult(
            seed_outcomes=[
                SeedFetchOutcome(
                    seed_url=self.source_config.seed_urls[0],
                    ok=True,
                    urls=[
                        "https://limited.test/one",
                        "https://limited.test/two",
                    ],
                )
            ]
        )

    def fetch_candidate(self, client, url):
        return CandidateFetchResult.success(
            [_event(url.rsplit("/", 1)[-1], url.rsplit("/", 1)[-1])]
        )


class _UnusedFetcher:
    def get_single(self, url, extra_headers=None):
        raise AssertionError("The limit adapter must not perform HTTP requests")


def test_non_positive_event_limit_is_rejected():
    with pytest.raises(ValidationError):
        HarvestConfig(max_events_per_source=0)


def test_candidate_limit_marks_partial_and_does_not_advance_missing(repo):
    old = _event("Old event", "old")
    reconcile_source(repo, "limited", [old], Coverage.COMPLETE, 3, 7)
    config = AppConfig(
        harvest=HarvestConfig(max_events_per_source=1),
        sources=[
            SourceConfig(
                id="limited",
                type="test-multi-limit",
                seed_urls=["https://limited.test/list"],
                allowed_hosts=["limited.test"],
            )
        ],
    )

    result = harvest_source(
        config,
        config.sources[0],
        _UnusedFetcher(),
        repo,
        llm_client=None,
    )

    assert result.coverage == Coverage.PARTIAL
    assert result.events_seen == 1
    old_record = next(record for record in repo.list_all_events() if record.title == "Old event")
    assert old_record.missing_count == 0
