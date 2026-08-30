from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from icalendar import Calendar

from wendeburg_calendar.export.ics_export import export_calendar
from wendeburg_calendar.harvest.coverage import Coverage
from wendeburg_calendar.harvest.reconcile import reconcile_source
from wendeburg_calendar.model.event import EventStatus, ExtractionMethod, NormalizedEvent

BERLIN = ZoneInfo("Europe/Berlin")
SOURCE_ID = "wendeburg"
DOMAIN = "wendeburg-calendar.test"


def make_event(**overrides) -> NormalizedEvent:
    defaults = dict(
        title="Herbstfest am Dorfplatz",
        start=datetime(2026, 10, 15, 18, 0, tzinfo=BERLIN),
        end=datetime(2026, 10, 15, 22, 0, tzinfo=BERLIN),
        all_day=False,
        location="Dorfplatz",
        description="Ein Fest fuer die ganze Familie",
        organizer="Gemeinde Wendeburg",
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


def test_export_round_trips_core_fields(repo, tmp_path):
    event = make_event()
    reconcile_source(repo, SOURCE_ID, [event], Coverage.COMPLETE, 3, 7)
    record = repo.list_all_events()[0]

    output_path = tmp_path / "calendar.ics"
    export_calendar(repo.list_all_events(), DOMAIN, output_path)

    raw = output_path.read_bytes()
    assert b"\r\n" in raw  # icalendar uses CRLF line endings

    cal = Calendar.from_ical(raw)
    vevents = list(cal.walk("VEVENT"))
    assert len(vevents) == 1
    vevent = vevents[0]

    assert str(vevent["SUMMARY"]) == "Herbstfest am Dorfplatz"
    assert str(vevent["UID"]) == f"{record.id}@{DOMAIN}"
    assert str(vevent["STATUS"]) == "CONFIRMED"
    assert int(vevent["SEQUENCE"]) == 0
    assert str(vevent["LOCATION"]) == "Dorfplatz"
    assert "ORGANIZER" in vevent
    assert str(vevent["URL"]) == event.source_url


def test_all_day_event_exports_as_date_not_datetime(repo, tmp_path):
    event = make_event(
        all_day=True,
        start=datetime(2026, 12, 6, tzinfo=BERLIN),
        end=datetime(2026, 12, 7, tzinfo=BERLIN),
        source_url="https://www.wendeburg.de/veranstaltungen/veranstaltung/adventsmarkt-99-26610.ical",
        source_event_uid="adventsmarkt-99@wendeburg.de",
        source_x_id=None,
    )
    reconcile_source(repo, SOURCE_ID, [event], Coverage.COMPLETE, 3, 7)

    output_path = tmp_path / "calendar.ics"
    export_calendar(repo.list_all_events(), DOMAIN, output_path)
    cal = Calendar.from_ical(output_path.read_bytes())
    vevent = list(cal.walk("VEVENT"))[0]

    dtstart = vevent["DTSTART"].dt
    assert isinstance(dtstart, date) and not isinstance(dtstart, datetime)
    assert dtstart == date(2026, 12, 6)


def test_cancelled_tombstone_is_exported_with_cancelled_status(repo, tmp_path):
    event = make_event()
    reconcile_source(repo, SOURCE_ID, [event], Coverage.COMPLETE, 3, 7)
    cancelled = make_event(status=EventStatus.CANCELLED)
    reconcile_source(repo, SOURCE_ID, [cancelled], Coverage.COMPLETE, 3, 7)

    output_path = tmp_path / "calendar.ics"
    export_calendar(repo.list_all_events(), DOMAIN, output_path)
    cal = Calendar.from_ical(output_path.read_bytes())
    vevent = list(cal.walk("VEVENT"))[0]

    assert str(vevent["STATUS"]) == "CANCELLED"
    assert int(vevent["SEQUENCE"]) == 1


def test_export_sorts_events_deterministically(repo, tmp_path):
    later = make_event(
        title="Neujahrsempfang",
        start=datetime(2027, 1, 10, 18, 0, tzinfo=BERLIN),
        end=datetime(2027, 1, 10, 20, 0, tzinfo=BERLIN),
        source_url="https://www.wendeburg.de/veranstaltungen/veranstaltung/neujahr-7-26610.ical",
        source_event_uid="neujahr-7@wendeburg.de",
        source_x_id=None,
    )
    earlier = make_event()  # October 2026, earlier than "later"
    reconcile_source(repo, SOURCE_ID, [later, earlier], Coverage.COMPLETE, 3, 7)

    output_path = tmp_path / "calendar.ics"
    export_calendar(repo.list_all_events(), DOMAIN, output_path)
    cal = Calendar.from_ical(output_path.read_bytes())
    summaries = [str(v["SUMMARY"]) for v in cal.walk("VEVENT")]

    assert summaries == ["Herbstfest am Dorfplatz", "Neujahrsempfang"]


def test_export_is_atomic_and_repeatable(repo, tmp_path):
    event = make_event()
    reconcile_source(repo, SOURCE_ID, [event], Coverage.COMPLETE, 3, 7)

    output_path = tmp_path / "calendar.ics"
    export_calendar(repo.list_all_events(), DOMAIN, output_path)
    export_calendar(repo.list_all_events(), DOMAIN, output_path)  # re-run must not corrupt/leave temp files

    leftover_tmp_files = [p for p in tmp_path.iterdir() if p.name != "calendar.ics" and p.name != "test.sqlite3"]
    assert leftover_tmp_files == []
    # File must still parse cleanly after being replaced twice.
    Calendar.from_ical(output_path.read_bytes())
