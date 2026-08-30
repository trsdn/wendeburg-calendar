"""Deterministic fallback for destination.one event details without JSON-LD."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from wendeburg_calendar.model.event import ExtractionMethod, NormalizedEvent
from wendeburg_calendar.parsing.german_dates import parse_german_date_range
from wendeburg_calendar.util.hashing import sha256_hex
from wendeburg_calendar.util.time import BERLIN


def _text(node) -> str | None:
    if node is None:
        return None
    value = " ".join(node.get_text(" ", strip=True).split())
    return value or None


def _address_text(address) -> str | None:
    if address is None:
        return None
    values: list[str] = []
    for item in address.select(".d1i-address__info"):
        if "d1i-address__info--with-link" in item.get("class", []):
            continue
        if value := _text(item):
            values.append(value)
    return ", ".join(dict.fromkeys(values)) or None


def _event_times(description: str) -> tuple[int, int, int, int] | None:
    match = re.search(
        r"Beginn(?:\s+ist)?(?:\s+jeweils)?\s+um\s+"
        r"(?P<start_hour>\d{1,2})(?::(?P<start_minute>\d{2}))?\s*Uhr"
        r".{0,120}?Ende(?:\s+gegen|\s+um)?\s+"
        r"(?P<end_hour>\d{1,2})(?::(?P<end_minute>\d{2}))?\s*Uhr",
        description,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return (
        int(match.group("start_hour")),
        int(match.group("start_minute") or 0),
        int(match.group("end_hour")),
        int(match.group("end_minute") or 0),
    )


def parse_peine_d1_event(
    html: str,
    *,
    source_id: str,
    source_url: str,
) -> list[NormalizedEvent]:
    soup = BeautifulSoup(html, "html.parser")
    title = _text(soup.select_one("h1.d1i-head__title"))
    description = _text(soup.select_one("#descriptionText .toujou-text-clamper__content"))
    if not title or not description:
        return []

    try:
        start, end, all_day = parse_german_date_range(description)
    except ValueError:
        return []

    times = _event_times(description)
    if times is not None:
        start_hour, start_minute, end_hour, end_minute = times
        last_day = (end - timedelta(days=1)).date()
        start = datetime(
            start.year,
            start.month,
            start.day,
            start_hour,
            start_minute,
            tzinfo=BERLIN,
        )
        end = datetime(
            last_day.year,
            last_day.month,
            last_day.day,
            end_hour,
            end_minute,
            tzinfo=BERLIN,
        )
        all_day = False

    addresses = soup.select("#d1iAddressesSection address.d1i-address")
    location = _address_text(addresses[0]) if addresses else None
    organizer = None
    for address in addresses:
        subtitle = _text(address.select_one(".d1i-section__subtitle"))
        if subtitle and subtitle.casefold() == "veranstalter":
            info = address.select_one(".d1i-address__info")
            organizer = _text(info)
            break

    event_url = source_url
    for link in soup.select("#d1iSidebarButtonsSection a[href]"):
        if (_text(link) or "").casefold() == "website":
            href = str(link.get("href") or "").strip()
            if href.startswith(("http://", "https://")):
                event_url = href
                break

    id_match = re.search(r"-(\d+)/?$", urlsplit(source_url).path)
    item_id = id_match.group(1) if id_match else title.casefold()
    return [
        NormalizedEvent(
            title=title,
            start=start,
            end=end,
            all_day=all_day,
            location=location,
            description=description,
            organizer=organizer,
            source_id=source_id,
            source_url=source_url,
            event_url=event_url,
            source_x_id=f"peine-d1:{item_id}:{start.isoformat()}",
            extraction_method=ExtractionMethod.STRUCTURED_HTML,
            extraction_confidence=1.0,
            raw_content_hash=sha256_hex(html),
        )
    ]
