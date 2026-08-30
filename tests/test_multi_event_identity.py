from __future__ import annotations

from datetime import datetime

from wendeburg_calendar.config import SourceConfig
from wendeburg_calendar.harvest.coverage import Coverage
from wendeburg_calendar.harvest.reconcile import reconcile_source
from wendeburg_calendar.model.event import ExtractionMethod, NormalizedEvent
from wendeburg_calendar.sources.base import AdapterContext
from wendeburg_calendar.sources.structured_html import StructuredHtmlAdapter
from wendeburg_calendar.util.time import BERLIN


def _event(
    *,
    source_id: str,
    title: str,
    x_id: str,
    event_url: str,
) -> NormalizedEvent:
    return NormalizedEvent(
        title=title,
        start=datetime(2026, 9, 1, 19, 0, tzinfo=BERLIN),
        source_id=source_id,
        source_url="https://example.test/shared-listing",
        event_url=event_url,
        source_x_id=x_id,
        organizer="Gemeinsamer Veranstalter",
        extraction_method=ExtractionMethod.STRUCTURED_HTML,
        raw_content_hash=f"hash-{source_id}-{x_id}",
    )


def test_multi_event_listing_with_shared_resource_stays_distinct(repo):
    first = _event(
        source_id="listing",
        title="Erster Termin",
        x_id="item-1",
        event_url="https://example.test/events/1",
    )
    second = _event(
        source_id="listing",
        title="Zweiter Termin",
        x_id="item-2",
        event_url="https://example.test/events/2",
    )

    reconcile_source(repo, "listing", [first, second], Coverage.COMPLETE, 3, 7)
    reconcile_source(repo, "listing", [first, second], Coverage.COMPLETE, 3, 7)

    assert len(repo.list_all_events()) == 2

    adapter = StructuredHtmlAdapter(
        SourceConfig(
            id="listing",
            type="structured-html",
            profile="tourismus-peine",
            allowed_hosts=["example.test"],
        ),
        AdapterContext(
            llm_client=None,
            repository=repo,
            llm_enabled=False,
            max_input_chars=1000,
            max_events_per_source=100,
        ),
    )
    unchanged = adapter.reuse_unchanged("https://example.test/shared-listing")

    assert unchanged.ok
    assert {event.source_x_id for event in unchanged.events} == {"item-1", "item-2"}


def test_cross_source_events_do_not_merge_on_shared_public_url(repo):
    shared_url = "https://organizer.example/events"
    source_a = _event(
        source_id="source-a",
        title="Sommerfest",
        x_id="a-1",
        event_url=shared_url,
    )
    source_b = _event(
        source_id="source-b",
        title="Sommerfest",
        x_id="b-1",
        event_url=shared_url,
    )

    reconcile_source(repo, "source-a", [source_a], Coverage.COMPLETE, 3, 7)
    reconcile_source(repo, "source-b", [source_b], Coverage.COMPLETE, 3, 7)

    assert len(repo.list_all_events()) == 2
