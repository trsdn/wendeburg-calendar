from __future__ import annotations

from icalendar import Calendar

from wendeburg_calendar.export.ics_export import export_calendar
from wendeburg_calendar.harvest.coverage import Coverage
from wendeburg_calendar.harvest.identity import candidate_aliases
from wendeburg_calendar.harvest.reconcile import reconcile_source
from wendeburg_calendar.parsing.ics_parser import parse_ics

SOURCE_ID = "wendeburg"
DOMAIN = "wendeburg-calendar.test"
PUBLIC_URL = "http://www.tsv-wendezelle.de"
RETRIEVAL_URL_1 = (
    "https://www.wendeburg.de/veranstaltungen/veranstaltung/"
    "termin-900001086-26610.ical"
)
RETRIEVAL_URL_2 = (
    "https://www.wendeburg.de/veranstaltungen/veranstaltung/"
    "termin-900001084-26610.ical"
)


def _ics(*, uid: str, x_id: str, summary: str, start: str) -> bytes:
    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Regression Test//EN
BEGIN:VEVENT
UID:{uid}
X-ID:{x_id}
SUMMARY:{summary}
DTSTART:{start}
URL:{PUBLIC_URL}
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
""".encode()


def _events():
    first = parse_ics(
        _ics(
            uid="26610_900001086@wendeburg.de",
            x_id="26610_900001086",
            summary="Wendezeller Sommerfest",
            start="20260822T140000",
        ),
        source_id=SOURCE_ID,
        source_url=RETRIEVAL_URL_1,
    )[0]
    second = parse_ics(
        _ics(
            uid="26610_900001084@wendeburg.de",
            x_id="26610_900001084",
            summary="Wendezeller Herbstmarkt",
            start="20260919T100000",
        ),
        source_id=SOURCE_ID,
        source_url=RETRIEVAL_URL_2,
    )[0]
    return first, second


def test_shared_vevent_url_does_not_merge_distinct_events(repo, tmp_path):
    first, second = _events()

    assert first.source_url == RETRIEVAL_URL_1
    assert second.source_url == RETRIEVAL_URL_2
    assert first.event_url == second.event_url == PUBLIC_URL
    assert ("source_url", PUBLIC_URL) not in candidate_aliases(first)
    assert ("source_url", PUBLIC_URL) not in candidate_aliases(second)

    first_run = reconcile_source(
        repo, SOURCE_ID, [first, second], Coverage.COMPLETE, 3, 7
    )
    second_run = reconcile_source(
        repo, SOURCE_ID, [first, second], Coverage.COMPLETE, 3, 7
    )

    records = repo.list_all_events()
    assert first_run["created"] == 2
    assert second_run["created"] == 0
    assert second_run["updated"] == 0
    assert len(records) == 2
    assert {record.sequence for record in records} == {0}
    assert {record.source_url for record in records} == {
        RETRIEVAL_URL_1,
        RETRIEVAL_URL_2,
    }
    assert {record.event_url for record in records} == {PUBLIC_URL}

    output_path = tmp_path / "calendar.ics"
    export_calendar(records, DOMAIN, output_path)
    exported = list(Calendar.from_ical(output_path.read_bytes()).walk("VEVENT"))

    assert len(exported) == 2
    assert {str(event["URL"]) for event in exported} == {PUBLIC_URL}
    assert {int(event["SEQUENCE"]) for event in exported} == {0}


def test_next_reconciliation_repairs_legacy_collapsed_aliases(repo):
    first, second = _events()
    reconcile_source(repo, SOURCE_ID, [first], Coverage.COMPLETE, 3, 7)
    legacy_event_id = repo.list_all_events()[0].id

    # Simulate the pre-fix state: the second event's strong aliases and the
    # shared VEVENT URL all point at the first event's record.
    legacy_second = second.model_copy(update={"source_url": PUBLIC_URL})
    for alias_type, alias_value in candidate_aliases(legacy_second):
        repo.upsert_alias(alias_type, alias_value, SOURCE_ID, legacy_event_id)

    repaired = reconcile_source(
        repo, SOURCE_ID, [first, second], Coverage.COMPLETE, 3, 7
    )
    repeated = reconcile_source(
        repo, SOURCE_ID, [first, second], Coverage.COMPLETE, 3, 7
    )

    records = repo.list_all_events()
    assert repaired["created"] == 1
    assert repeated["created"] == 0
    assert repeated["updated"] == 0
    assert len(records) == 2
    assert {record.sequence for record in records} == {0}
