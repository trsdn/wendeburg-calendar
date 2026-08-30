from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from wendeburg_calendar.harvest.coverage import Coverage
from wendeburg_calendar.harvest.reconcile import reconcile_source
from wendeburg_calendar.model.event import EventStatus, ExtractionMethod, NormalizedEvent

BERLIN = ZoneInfo("Europe/Berlin")
SOURCE_ID = "wendeburg"
MISSING_THRESHOLD = 3
MISSING_GRACE_DAYS = 7


def make_event(**overrides) -> NormalizedEvent:
    defaults = dict(
        title="Herbstfest am Dorfplatz",
        start=datetime(2026, 10, 15, 18, 0, tzinfo=BERLIN),
        end=datetime(2026, 10, 15, 22, 0, tzinfo=BERLIN),
        all_day=False,
        location="Dorfplatz",
        description=None,
        organizer=None,
        status=EventStatus.CONFIRMED,
        source_id=SOURCE_ID,
        source_url="https://www.wendeburg.de/veranstaltungen/veranstaltung/herbstfest-42-26610.ical",
        source_event_uid="herbstfest-42@wendeburg.de",
        source_x_id="wendeburg-42",
        extraction_method=ExtractionMethod.ICS,
        extraction_confidence=1.0,
        raw_content_hash="hash-1",
    )
    defaults.update(overrides)
    return NormalizedEvent(**defaults)


class Clock:
    def __init__(self, start: datetime):
        self.current = start

    def advance_days(self, days: float) -> None:
        self.current += timedelta(days=days)

    def now(self) -> datetime:
        return self.current


@pytest.fixture()
def clock(monkeypatch):
    c = Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr("wendeburg_calendar.harvest.reconcile.now_utc", c.now)
    return c


def _run(repo, events, coverage):
    return reconcile_source(repo, SOURCE_ID, events, coverage, MISSING_THRESHOLD, MISSING_GRACE_DAYS)


def test_unchanged_event_does_not_bump_sequence(repo, clock):
    event = make_event()
    _run(repo, [event], Coverage.COMPLETE)
    record = repo.list_all_events()[0]
    assert record.sequence == 0

    # Re-harvest the identical event.
    _run(repo, [event], Coverage.COMPLETE)
    record = repo.list_all_events()[0]
    assert record.sequence == 0


def test_semantic_change_bumps_sequence_exactly_once(repo, clock):
    event = make_event()
    _run(repo, [event], Coverage.COMPLETE)

    changed = make_event(description="Neu: jetzt mit Livemusik!")
    summary = _run(repo, [changed], Coverage.COMPLETE)
    assert summary["updated"] == 1
    record = repo.list_all_events()[0]
    assert record.sequence == 1
    assert record.description == "Neu: jetzt mit Livemusik!"

    # Harvesting the same (already-updated) content again must not bump again.
    summary_again = _run(repo, [changed], Coverage.COMPLETE)
    assert summary_again["updated"] == 0
    record = repo.list_all_events()[0]
    assert record.sequence == 1


def test_fetch_metadata_alone_does_not_bump_sequence(repo, clock):
    event = make_event(raw_content_hash="hash-1", extraction_confidence=1.0)
    _run(repo, [event], Coverage.COMPLETE)

    # Same semantic fields, but different raw content hash / extraction
    # confidence (e.g. whitespace-only change in the raw page, or a re-run
    # of the same deterministic parser) - must not bump SEQUENCE.
    same_semantics_different_metadata = make_event(
        raw_content_hash="hash-2", extraction_confidence=0.9
    )
    summary = _run(repo, [same_semantics_different_metadata], Coverage.COMPLETE)
    assert summary["updated"] == 0
    record = repo.list_all_events()[0]
    assert record.sequence == 0


def test_event_url_change_bumps_sequence_once(repo, clock):
    event = make_event(event_url="https://example.test/events/herbstfest")
    _run(repo, [event], Coverage.COMPLETE)

    changed = make_event(event_url="https://example.test/events/herbstfest-neu")
    summary = _run(repo, [changed], Coverage.COMPLETE)
    record = repo.list_all_events()[0]

    assert summary["updated"] == 1
    assert record.event_url == "https://example.test/events/herbstfest-neu"
    assert record.sequence == 1

    summary_again = _run(repo, [changed], Coverage.COMPLETE)
    assert summary_again["updated"] == 0
    assert repo.list_all_events()[0].sequence == 1


def test_source_url_change_is_persisted_without_bumping_sequence(repo, clock):
    public_url = "https://example.test/events/herbstfest"
    event = make_event(event_url=public_url)
    _run(repo, [event], Coverage.COMPLETE)

    moved = make_event(
        source_url="https://www.wendeburg.de/veranstaltungen/veranstaltung/herbstfest-neu-42-26610.ical",
        event_url=public_url,
    )
    summary = _run(repo, [moved], Coverage.COMPLETE)
    record = repo.list_all_events()[0]

    assert summary["updated"] == 0
    assert record.source_url == moved.source_url
    assert record.event_url == public_url
    assert record.sequence == 0


def test_explicit_source_cancellation_is_immediate(repo, clock):
    event = make_event()
    _run(repo, [event], Coverage.COMPLETE)

    cancelled = make_event(status=EventStatus.CANCELLED)
    summary = _run(repo, [cancelled], Coverage.COMPLETE)
    assert summary["updated"] == 1
    record = repo.list_all_events()[0]
    assert record.status == EventStatus.CANCELLED
    assert record.cancelled_reason == "source"
    assert record.sequence == 1

    # Re-harvesting the same cancellation must not bump again.
    summary_again = _run(repo, [cancelled], Coverage.COMPLETE)
    assert summary_again["updated"] == 0
    assert repo.list_all_events()[0].sequence == 1


def test_single_miss_does_not_cancel(repo, clock):
    event = make_event()
    _run(repo, [event], Coverage.COMPLETE)

    _run(repo, [], Coverage.COMPLETE)  # event absent once
    record = repo.list_all_events()[0]
    assert record.status == EventStatus.CONFIRMED
    assert record.missing_count == 1


def test_partial_and_unchanged_runs_never_advance_missing_counters(repo, clock):
    event = make_event()
    _run(repo, [event], Coverage.COMPLETE)

    _run(repo, [], Coverage.PARTIAL)
    assert repo.list_all_events()[0].missing_count == 0

    _run(repo, [], Coverage.UNCHANGED)
    assert repo.list_all_events()[0].missing_count == 0

    # Sanity: a COMPLETE absence *does* advance it.
    _run(repo, [], Coverage.COMPLETE)
    assert repo.list_all_events()[0].missing_count == 1


def test_threshold_and_grace_days_both_required_before_auto_cancel(repo, clock):
    event = make_event()
    _run(repo, [event], Coverage.COMPLETE)  # created

    _run(repo, [], Coverage.COMPLETE)  # miss 1, day 0
    assert repo.list_all_events()[0].missing_count == 1
    assert repo.list_all_events()[0].status == EventStatus.CONFIRMED

    clock.advance_days(8)
    _run(repo, [], Coverage.COMPLETE)  # miss 2, day 8 (grace satisfied, threshold not yet)
    record = repo.list_all_events()[0]
    assert record.missing_count == 2
    assert record.status == EventStatus.CONFIRMED

    clock.advance_days(1)
    _run(repo, [], Coverage.COMPLETE)  # miss 3, day 9: threshold AND grace satisfied
    record = repo.list_all_events()[0]
    assert record.missing_count == 3
    assert record.status == EventStatus.CANCELLED
    assert record.cancelled_reason == "absence"
    assert record.sequence == 1  # exactly one bump for the cancellation


def test_reappearance_clears_missing_state_and_revives_absence_cancellation(repo, clock):
    event = make_event()
    _run(repo, [event], Coverage.COMPLETE)

    _run(repo, [], Coverage.COMPLETE)
    clock.advance_days(8)
    _run(repo, [], Coverage.COMPLETE)
    clock.advance_days(1)
    _run(repo, [], Coverage.COMPLETE)
    record = repo.list_all_events()[0]
    assert record.status == EventStatus.CANCELLED
    assert record.cancelled_reason == "absence"
    assert record.sequence == 1

    # The event reappears in the source.
    summary = _run(repo, [event], Coverage.COMPLETE)
    assert summary["updated"] == 1
    record = repo.list_all_events()[0]
    assert record.status == EventStatus.CONFIRMED
    assert record.cancelled_reason is None
    assert record.missing_count == 0
    assert record.first_missing_at_utc is None
    assert record.sequence == 2  # one more bump for the revival


def test_cancelled_tombstone_remains_in_database_and_therefore_exportable(repo, clock):
    event = make_event()
    _run(repo, [event], Coverage.COMPLETE)
    cancelled = make_event(status=EventStatus.CANCELLED)
    _run(repo, [cancelled], Coverage.COMPLETE)

    # Even after further COMPLETE runs where it is (naturally) absent, the
    # tombstone must remain present in the database.
    _run(repo, [], Coverage.COMPLETE)
    _run(repo, [], Coverage.COMPLETE)
    records = repo.list_all_events()
    assert len(records) == 1
    assert records[0].status == EventStatus.CANCELLED


def test_new_event_always_starts_at_sequence_zero(repo, clock):
    event = make_event(description="Initial description already present")
    _run(repo, [event], Coverage.COMPLETE)
    assert repo.list_all_events()[0].sequence == 0
