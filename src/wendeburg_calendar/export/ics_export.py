"""RFC 5545 feed export.

- UID is built from the *persisted* internal UUID plus the configured
  domain, so it is stable across every future run regardless of how the
  event's title/date/content may change.
- SEQUENCE and STATUS (including CANCELLED tombstones) are taken directly
  from the persisted record - cancelled events are still exported so
  calendar clients correctly remove/gray them out instead of just seeing
  them silently vanish.
- URL comes from persisted user-visible event metadata, never from the
  retrieval URL used for identity.
- UTF-8 encoding, CRLF line endings, and text escaping are all handled by
  the `icalendar` library, not reimplemented here.
- Output is written atomically (temp file + os.replace) so a reader never
  observes a partially written feed.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from icalendar import Calendar, Event, vCalAddress, vText

from wendeburg_calendar.db.models import EventRecord
from wendeburg_calendar.util.time import BERLIN


def build_calendar(
    events: list[EventRecord],
    domain: str,
    calendar_name: str = "Wendeburg Veranstaltungen",
) -> Calendar:
    ordered = sorted(events, key=lambda r: (r.start_utc, r.title, r.id))

    cal = Calendar()
    cal.add("prodid", f"-//Wendeburg Calendar//{domain}//DE")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", calendar_name)
    cal.add("x-wr-timezone", "Europe/Berlin")

    for record in ordered:
        cal.add_component(_build_event(record, domain))
    return cal


def _build_event(record: EventRecord, domain: str) -> Event:
    event = Event()
    event.add("uid", f"{record.id}@{domain}")
    event.add("summary", record.title)
    event.add("dtstamp", record.dtstamp_utc)
    event.add("sequence", record.sequence)
    event.add("status", record.status.value)
    event.add("last-modified", record.last_modified_utc)

    start_berlin = record.start_utc.astimezone(BERLIN)
    if record.all_day:
        event.add("dtstart", start_berlin.date())
        if record.end_utc is not None:
            event.add("dtend", record.end_utc.astimezone(BERLIN).date())
    else:
        event.add("dtstart", start_berlin)
        if record.end_utc is not None:
            event.add("dtend", record.end_utc.astimezone(BERLIN))

    if record.location:
        event.add("location", record.location)
    if record.description:
        event.add("description", record.description)
    if record.organizer:
        # RFC 5545 ORGANIZER requires a URI; we don't have a real mailbox
        # for scraped organizers, so a syntactically valid, clearly
        # synthetic address on the configured domain is used, with the
        # human-readable name carried in the CN parameter.
        organizer = vCalAddress(f"mailto:noreply@{domain}")
        organizer.params["cn"] = vText(record.organizer)
        event.add("organizer", organizer)
    if record.event_url:
        event.add("url", record.event_url)

    return event


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


def export_calendar(
    events: list[EventRecord],
    domain: str,
    output_path: str | Path,
    calendar_name: str = "Wendeburg Veranstaltungen",
) -> Path:
    cal = build_calendar(events, domain, calendar_name)
    data = cal.to_ical()
    target = Path(output_path)
    atomic_write_bytes(target, data)
    return target
