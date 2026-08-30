from __future__ import annotations

from datetime import date

import pytest

from wendeburg_calendar.model.event import EventStatus, ExtractionMethod
from wendeburg_calendar.parsing.ics_parser import IcsParseError, extract_x_id_from_url, parse_ics

TIMED_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:herbstfest-42@wendeburg.de
X-ID:wendeburg-42
SUMMARY:Herbstfest am Dorfplatz
DTSTART:20261015T180000
DTEND:20261015T220000
LOCATION:Dorfplatz Wendeburg
DESCRIPTION:Musik und Essen
ORGANIZER;CN=Gemeinde Wendeburg:mailto:info@wendeburg.de
STATUS:CONFIRMED
SEQUENCE:2
LAST-MODIFIED:20260801T120000Z
END:VEVENT
END:VCALENDAR
"""

ALL_DAY_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:adventsmarkt-99@wendeburg.de
SUMMARY:Adventsmarkt
DTSTART;VALUE=DATE:20261206
DTEND;VALUE=DATE:20261207
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

CANCELLED_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:abgesagt-1@wendeburg.de
SUMMARY:Abgesagtes Konzert
DTSTART:20261101T190000
STATUS:CANCELLED
SEQUENCE:1
END:VEVENT
END:VCALENDAR
"""

MALFORMED = b"this is not a calendar at all"

SOURCE_URL = "https://www.wendeburg.de/veranstaltungen/veranstaltung/herbstfest-42-26610.ical"


def test_parses_timed_event_fields():
    events = parse_ics(TIMED_ICS, source_id="wendeburg", source_url=SOURCE_URL)
    assert len(events) == 1
    event = events[0]

    assert event.title == "Herbstfest am Dorfplatz"
    assert event.all_day is False
    assert event.start.isoformat() == "2026-10-15T18:00:00+02:00"
    assert event.end.isoformat() == "2026-10-15T22:00:00+02:00"
    assert event.location == "Dorfplatz Wendeburg"
    assert event.organizer == "Gemeinde Wendeburg"
    assert event.status == EventStatus.CONFIRMED
    assert event.source_event_uid == "herbstfest-42@wendeburg.de"
    assert event.source_x_id == "wendeburg-42"
    assert event.source_sequence == 2
    assert event.extraction_method == ExtractionMethod.ICS
    assert event.extraction_confidence == 1.0
    assert event.source_last_modified is not None


def test_all_day_event_preserves_date_semantics():
    events = parse_ics(ALL_DAY_ICS, source_id="wendeburg", source_url="https://www.wendeburg.de/x")
    assert len(events) == 1
    event = events[0]

    assert event.all_day is True
    assert event.start.date() == date(2026, 12, 6)
    # RFC 5545 all-day DTEND is exclusive; we preserve it as-is.
    assert event.end.date() == date(2026, 12, 7)


def test_cancelled_status_is_recognized():
    events = parse_ics(CANCELLED_ICS, source_id="wendeburg", source_url="https://www.wendeburg.de/y")
    assert events[0].status == EventStatus.CANCELLED


def test_x_id_falls_back_to_url_pattern_when_absent():
    # adventsmarkt ICS has no X-ID property; the URL pattern should supply one.
    url = "https://www.wendeburg.de/veranstaltungen/veranstaltung/adventsmarkt-99-26610.ical"
    events = parse_ics(ALL_DAY_ICS, source_id="wendeburg", source_url=url)
    assert events[0].source_x_id == "wendeburg:99"


def test_vevent_url_is_separate_from_retrieval_source_url():
    public_url = "http://www.tsv-wendezelle.de"
    ics = TIMED_ICS.replace(
        b"STATUS:CONFIRMED",
        f"URL:{public_url}\nSTATUS:CONFIRMED".encode(),
    )

    event = parse_ics(ics, source_id="wendeburg", source_url=SOURCE_URL)[0]

    assert event.source_url == SOURCE_URL
    assert event.event_url == public_url


def test_extract_x_id_from_url_helper():
    url = "https://www.wendeburg.de/veranstaltungen/veranstaltung/herbstfest-42-26610.ical"
    assert extract_x_id_from_url(url) == "wendeburg:42"
    assert extract_x_id_from_url("https://www.wendeburg.de/nope.html") is None


def test_malformed_ics_raises_parse_error():
    with pytest.raises(IcsParseError):
        parse_ics(MALFORMED, source_id="wendeburg", source_url="https://www.wendeburg.de/z")


def test_event_missing_summary_is_skipped():
    ics = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:no-summary@wendeburg.de
DTSTART:20261015T180000
END:VEVENT
END:VCALENDAR
"""
    events = parse_ics(ics, source_id="wendeburg", source_url="https://www.wendeburg.de/z")
    assert events == []
