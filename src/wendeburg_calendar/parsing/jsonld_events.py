"""Deterministic schema.org JSON-LD event extraction."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup

from wendeburg_calendar.model.event import EventStatus, ExtractionMethod, NormalizedEvent
from wendeburg_calendar.util.hashing import sha256_hex
from wendeburg_calendar.util.time import BERLIN


def _has_event_type(value: Any) -> bool:
    types = value if isinstance(value, list) else [value]
    return any(str(item).rsplit("/", 1)[-1].casefold() == "event" for item in types)


def _walk_json(value: Any):
    if isinstance(value, dict):
        if _has_event_type(value.get("@type")):
            yield value
        for nested in value.values():
            yield from _walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_json(nested)


def _parse_datetime(value: str) -> tuple[datetime, bool]:
    text = value.strip()
    if "T" not in text:
        parsed_date = date.fromisoformat(text)
        return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=BERLIN), True
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BERLIN)
    else:
        parsed = parsed.astimezone(BERLIN)
    return parsed, False


def _clean_html(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return " ".join(text.split()) or None


def _named(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        names = [name for item in value if (name := _named(item))]
        return ", ".join(dict.fromkeys(names)) or None
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _location(value: Any) -> str | None:
    if isinstance(value, list):
        locations = [text for item in value if (text := _location(item))]
        return " / ".join(dict.fromkeys(locations)) or None
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None

    parts: list[str] = []
    name = _named(value)
    if name:
        parts.append(name)
    address = value.get("address")
    if isinstance(address, str):
        parts.append(address.strip())
    elif isinstance(address, dict):
        street = str(address.get("streetAddress") or "").strip()
        postal = str(address.get("postalCode") or "").strip()
        locality = str(address.get("addressLocality") or "").strip()
        line = " ".join(part for part in (postal, locality) if part)
        if street:
            parts.append(street)
        if line:
            parts.append(line)
    return ", ".join(dict.fromkeys(part for part in parts if part)) or None


def _identifier(value: Any) -> str | None:
    if isinstance(value, (str, int)):
        return str(value).strip() or None
    if isinstance(value, list):
        for item in value:
            if identifier := _identifier(item):
                return identifier
    if isinstance(value, dict):
        for key in ("value", "@value", "name"):
            if identifier := _identifier(value.get(key)):
                return identifier
    return None


def _status(value: Any) -> EventStatus:
    tail = str(value or "").rsplit("/", 1)[-1].casefold()
    if tail in {"eventcancelled", "cancelled"}:
        return EventStatus.CANCELLED
    if tail in {"eventpostponed", "eventrescheduled", "tentative"}:
        return EventStatus.TENTATIVE
    return EventStatus.CONFIRMED


def parse_jsonld_events(
    html: str,
    *,
    source_id: str,
    source_url: str,
) -> list[NormalizedEvent]:
    soup = BeautifulSoup(html, "html.parser")
    raw_hash = sha256_hex(html)
    events: list[NormalizedEvent] = []
    seen: set[tuple[str, str, str]] = set()

    for script in soup.find_all("script", type="application/ld+json"):
        payload_text = script.string or script.get_text()
        try:
            payload = json.loads(payload_text)
        except (json.JSONDecodeError, TypeError):
            continue

        for item in _walk_json(payload):
            title = _named(item.get("name"))
            start_value = item.get("startDate")
            if not title or not isinstance(start_value, str):
                continue
            try:
                start, all_day = _parse_datetime(start_value)
            except (TypeError, ValueError):
                continue

            end: datetime | None = None
            end_value = item.get("endDate")
            if isinstance(end_value, str):
                try:
                    end, end_all_day = _parse_datetime(end_value)
                    if all_day and end_all_day and end >= start:
                        end += timedelta(days=1)
                except (TypeError, ValueError):
                    end = None
            if all_day and end is None:
                end = start + timedelta(days=1)

            event_url_value = item.get("url")
            event_url = (
                event_url_value.strip()
                if isinstance(event_url_value, str)
                and event_url_value.startswith(("http://", "https://"))
                else source_url
            )
            identifier = _identifier(item.get("identifier"))
            # Some destination.one pages expose one Event object per
            # occurrence while reusing the same series identifier. Include
            # the occurrence start so recurring date ranges do not collapse
            # into one record; source_url still preserves update continuity
            # for ordinary single-event detail pages.
            source_x_id = (
                f"jsonld:{identifier}:{start.isoformat()}"
                if identifier
                else None
            )
            dedup_key = (source_x_id or "", title.casefold(), start.isoformat())
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            events.append(
                NormalizedEvent(
                    title=title,
                    start=start,
                    end=end,
                    all_day=all_day,
                    location=_location(item.get("location")),
                    description=_clean_html(item.get("description")),
                    organizer=_named(item.get("organizer")),
                    status=_status(item.get("eventStatus")),
                    source_id=source_id,
                    source_url=source_url,
                    event_url=event_url,
                    source_x_id=source_x_id,
                    extraction_method=ExtractionMethod.JSON_LD,
                    extraction_confidence=1.0,
                    raw_content_hash=raw_hash,
                )
            )
    return events
