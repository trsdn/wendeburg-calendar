"""Deterministic ICS parsing via `icalendar`.

This is the preferred, high-confidence extraction path. LLM extraction is
only ever a fallback for unstructured HTML (see llm/extractor.py) - any
event exposed as ICS must go through here instead.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from icalendar import Calendar

from wendeburg_calendar.model.event import EventStatus, ExtractionMethod, NormalizedEvent
from wendeburg_calendar.util.hashing import sha256_hex
from wendeburg_calendar.util.time import BERLIN

# Recognizes the stable numeric event id embedded in Wendeburg .ical URLs,
# e.g. ".../herbstfest-42-26610.ical" -> event id "42".
_X_ID_URL_RE = re.compile(r"-(\d+)-\d+\.ical$", re.IGNORECASE)

_VALID_STATUSES = {s.value for s in EventStatus}


class IcsParseError(ValueError):
    """Raised when ICS content cannot be parsed at all (malformed calendar)."""


def extract_x_id_from_url(url: str) -> str | None:
    match = _X_ID_URL_RE.search(url)
    if not match:
        return None
    return f"wendeburg:{match.group(1)}"


def _as_aware(value: datetime | date, tz) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(tz) if value.tzinfo else value.replace(tzinfo=tz)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=tz)
    raise IcsParseError(f"Unsupported date/time value: {value!r}")


def _is_all_day(value) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _text(prop) -> str | None:
    if prop is None:
        return None
    value = str(prop).strip()
    return value or None


def _organizer_name(prop) -> str | None:
    if prop is None:
        return None
    params = getattr(prop, "params", {}) or {}
    cn = params.get("CN")
    if cn:
        return str(cn)
    text = str(prop)
    return text.replace("mailto:", "").replace("MAILTO:", "").strip() or None


def parse_ics(
    content: bytes,
    *,
    source_id: str,
    source_url: str,
    tz=BERLIN,
) -> list[NormalizedEvent]:
    """Parse ICS bytes into zero or more NormalizedEvent instances.

    Events without a usable SUMMARY or DTSTART are skipped rather than
    raising, since a single malformed VEVENT in a feed should not block
    the rest of a harvest run.
    """
    try:
        calendar = Calendar.from_ical(content)
    except (ValueError, IndexError) as exc:
        raise IcsParseError(f"Could not parse ICS content: {exc}") from exc

    raw_hash = sha256_hex(content)
    url_x_id = extract_x_id_from_url(source_url)
    results: list[NormalizedEvent] = []

    for component in calendar.walk("VEVENT"):
        summary = _text(component.get("SUMMARY"))
        dtstart_prop = component.get("DTSTART")
        if not summary or dtstart_prop is None:
            continue

        start_value = dtstart_prop.dt
        all_day = _is_all_day(start_value)
        start = _as_aware(start_value, tz)

        end: datetime | None = None
        dtend_prop = component.get("DTEND")
        if dtend_prop is not None:
            end = _as_aware(dtend_prop.dt, tz)
        else:
            duration_prop = component.get("DURATION")
            if duration_prop is not None and isinstance(duration_prop.dt, timedelta):
                end = start + duration_prop.dt

        uid = _text(component.get("UID"))
        x_id_prop = _text(component.get("X-ID"))
        x_id = x_id_prop or url_x_id

        status = EventStatus.CONFIRMED
        status_text = _text(component.get("STATUS"))
        if status_text and status_text.upper() in _VALID_STATUSES:
            status = EventStatus(status_text.upper())

        sequence_prop = component.get("SEQUENCE")
        source_sequence = int(sequence_prop) if sequence_prop is not None else None

        last_modified_prop = component.get("LAST-MODIFIED")
        source_last_modified = (
            _as_aware(last_modified_prop.dt, tz) if last_modified_prop is not None else None
        )

        url_prop = _text(component.get("URL"))
        event_url = url_prop or source_url

        results.append(
            NormalizedEvent(
                title=summary,
                start=start,
                end=end,
                all_day=all_day,
                location=_text(component.get("LOCATION")),
                description=_text(component.get("DESCRIPTION")),
                organizer=_organizer_name(component.get("ORGANIZER")),
                status=status,
                source_id=source_id,
                source_url=source_url,
                event_url=event_url,
                source_event_uid=uid,
                source_x_id=x_id,
                extraction_method=ExtractionMethod.ICS,
                extraction_confidence=1.0,
                raw_content_hash=raw_hash,
                source_sequence=source_sequence,
                source_last_modified=source_last_modified,
            )
        )

    return results
