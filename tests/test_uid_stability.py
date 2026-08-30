from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from wendeburg_calendar.harvest.identity import resolve_or_create
from wendeburg_calendar.harvest.reconcile import reconcile_source
from wendeburg_calendar.harvest.coverage import Coverage
from wendeburg_calendar.model.event import EventStatus, ExtractionMethod, NormalizedEvent

BERLIN = ZoneInfo("Europe/Berlin")
SOURCE_ID = "wendeburg"


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
        source_event_uid=None,
        source_x_id=None,
        extraction_method=ExtractionMethod.ICS,
        extraction_confidence=1.0,
        raw_content_hash="hash-1",
    )
    defaults.update(overrides)
    return NormalizedEvent(**defaults)


def test_same_ics_uid_resolves_to_same_event_id(repo):
    event = make_event(source_event_uid="herbstfest-42@wendeburg.de")
    first_id, is_new_1 = resolve_or_create(repo, SOURCE_ID, event)
    second_id, is_new_2 = resolve_or_create(repo, SOURCE_ID, event)

    assert is_new_1 is True
    assert is_new_2 is False
    assert first_id == second_id


def test_weak_fingerprint_unifies_events_with_unstable_urls_and_no_uid(repo):
    # Same real-world event, but the source exposes different transient URLs
    # across two harvests and no UID/X-ID at all -> fingerprint is the only
    # thing that can unify them.
    event_a = make_event(
        source_url="https://www.wendeburg.de/a/transient-1.html",
        source_event_uid=None,
        source_x_id=None,
    )
    event_b = make_event(
        source_url="https://www.wendeburg.de/a/transient-2.html",
        source_event_uid=None,
        source_x_id=None,
    )

    id_a, is_new_a = resolve_or_create(repo, SOURCE_ID, event_a)
    id_b, is_new_b = resolve_or_create(repo, SOURCE_ID, event_b)

    assert is_new_a is True
    assert is_new_b is False
    assert id_a == id_b


def test_strong_alias_upgrades_a_previously_weak_fingerprint_match(repo):
    # Day 1: only a weak fingerprint identity is available (no uid/x_id, and
    # a URL that will not recur).
    weak_event = make_event(
        source_url="https://www.wendeburg.de/day1/only-seen-once.html",
        source_event_uid=None,
        source_x_id=None,
    )
    weak_id, _ = resolve_or_create(repo, SOURCE_ID, weak_event)

    # Day 2: the same event now appears with a stable ICS UID and a
    # completely different URL - only the fingerprint (title+date) ties it
    # back to the same internal event.
    strong_event = make_event(
        source_url="https://www.wendeburg.de/day2/different-url.ical",
        source_event_uid="herbstfest-42@wendeburg.de",
        source_x_id="wendeburg-42",
    )
    strong_id, is_new = resolve_or_create(repo, SOURCE_ID, strong_event)

    assert is_new is False
    assert strong_id == weak_id

    # Day 3: only the strong UID is presented (yet another different URL,
    # different fingerprint-relevant title casing) - it must resolve
    # directly via the now-persisted ics_uid alias without needing the
    # fingerprint at all.
    day3_event = make_event(
        source_url="https://www.wendeburg.de/day3/yet-another-url.ical",
        source_event_uid="herbstfest-42@wendeburg.de",
        source_x_id=None,
        title="HERBSTFEST AM DORFPLATZ (Update)",
    )
    day3_id, is_new_day3 = resolve_or_create(repo, SOURCE_ID, day3_event)
    assert is_new_day3 is False
    assert day3_id == weak_id


def test_feed_uid_is_stable_across_idempotent_harvest_runs(repo):
    event = make_event(source_event_uid="herbstfest-42@wendeburg.de")

    summary_1 = reconcile_source(repo, SOURCE_ID, [event], Coverage.COMPLETE, 3, 7)
    summary_2 = reconcile_source(repo, SOURCE_ID, [event], Coverage.COMPLETE, 3, 7)

    assert summary_1["created"] == 1
    assert summary_2["created"] == 0
    assert summary_2["updated"] == 0  # fully unchanged -> no semantic update either

    records = repo.list_all_events()
    assert len(records) == 1
    assert records[0].sequence == 0


def test_title_and_date_alone_are_never_used_as_the_feed_uid(repo):
    """The persisted UUID (not title+date) must back the feed UID, so a
    title correction does not change identity, and two different real
    events that coincidentally share a title+date are still distinguished
    once either has a stronger identifier."""
    event_1 = make_event(
        title="Sommerfest",
        source_url="https://www.wendeburg.de/loc-a.ical",
        source_event_uid="sommerfest-a@wendeburg.de",
    )
    event_2 = make_event(
        title="Sommerfest",
        source_url="https://www.wendeburg.de/loc-b.ical",
        source_event_uid="sommerfest-b@wendeburg.de",
    )

    id_1, _ = resolve_or_create(repo, SOURCE_ID, event_1)
    id_2, _ = resolve_or_create(repo, SOURCE_ID, event_2)

    assert id_1 != id_2
