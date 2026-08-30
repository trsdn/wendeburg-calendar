"""Shared deterministic parsers for stable public HTML event-list profiles."""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from wendeburg_calendar.model.event import ExtractionMethod, NormalizedEvent
from wendeburg_calendar.parsing.german_dates import (
    parse_german_date_range,
    parse_german_datetime,
)
from wendeburg_calendar.util.hashing import sha256_hex

StructuredParser = Callable[[str, str, str], list[NormalizedEvent]]


def _text(node: Tag | None) -> str | None:
    if node is None:
        return None
    value = " ".join(node.get_text(" ", strip=True).split())
    return value or None


def _slug(value: str) -> str:
    lowered = value.casefold()
    normalized = re.sub(r"[^a-z0-9äöüß]+", "-", lowered).strip("-")
    return normalized[:100]


def _event(
    *,
    source_id: str,
    source_url: str,
    title: str,
    start,
    end=None,
    all_day: bool = False,
    location: str | None = None,
    description: str | None = None,
    organizer: str | None = None,
    event_url: str | None = None,
    source_x_id: str,
    raw_hash: str,
) -> NormalizedEvent:
    return NormalizedEvent(
        title=title,
        start=start,
        end=end,
        all_day=all_day,
        location=location,
        description=description,
        organizer=organizer,
        source_id=source_id,
        source_url=source_url,
        event_url=event_url or source_url,
        source_x_id=source_x_id,
        extraction_method=ExtractionMethod.STRUCTURED_HTML,
        extraction_confidence=1.0,
        raw_content_hash=raw_hash,
    )


def parse_lkcal(html: str, source_id: str, source_url: str) -> list[NormalizedEvent]:
    soup = BeautifulSoup(html, "html.parser")
    raw_hash = sha256_hex(html)
    events: list[NormalizedEvent] = []
    for item in soup.select(".cal-list-event"):
        day = _text(item.select_one(".cal-date-day"))
        month = _text(item.select_one(".cal-date-month"))
        year = _text(item.select_one(".cal-date-year"))
        time = _text(item.select_one(".cal-date-time"))
        title_node = item.select_one(".cal-list-title")
        if not all((day, month, year, time, title_node)):
            continue
        try:
            start = parse_german_datetime(f"{day}. {month} {year}", time)
        except ValueError:
            continue
        title = _text(title_node)
        if not title:
            continue
        detail = item.select_one(".cal-button a[href]")
        event_url = urljoin(source_url, detail["href"]) if detail else source_url
        id_match = re.search(r"/(\d+)/detail(?:$|[?#])", event_url)
        source_x_id = (
            f"lkcal:{id_match.group(1)}"
            if id_match
            else f"lkcal:{_slug(title)}:{start.date().isoformat()}:{start.time().isoformat()}"
        )
        events.append(
            _event(
                source_id=source_id,
                source_url=source_url,
                title=title,
                start=start,
                location=_text(item.select_one(".cal-list-type")),
                description=_text(item.select_one(".cal-list-teaser")),
                organizer=_text(item.select_one(".cal-list-place")),
                event_url=event_url,
                source_x_id=source_x_id,
                raw_hash=raw_hash,
            )
        )
    return events


def parse_bortfeld_table(html: str, source_id: str, source_url: str) -> list[NormalizedEvent]:
    soup = BeautifulSoup(html, "html.parser")
    raw_hash = sha256_hex(html)
    events: list[NormalizedEvent] = []
    for table in soup.select("table.ce-table"):
        for row in table.select("tr"):
            cells = [_text(cell) or "" for cell in row.select("td")]
            if len(cells) < 3 or not cells[2] or cells[2].casefold() == "gottesdienst in bortfeld":
                continue
            date_match = re.search(r"\d{1,2}\.\d{1,2}\.20\d{2}", cells[0])
            time_match = re.search(r"\d{1,2}[.:]\d{2}", cells[1])
            if date_match is None or time_match is None:
                continue
            try:
                start = parse_german_datetime(date_match.group(0), time_match.group(0))
            except ValueError:
                continue
            title = cells[2]
            events.append(
                _event(
                    source_id=source_id,
                    source_url=source_url,
                    title=title,
                    start=start,
                    location="St. Georg, Bortfeld",
                    organizer="Kirchengemeinde Bortfeld",
                    event_url=source_url,
                    source_x_id=f"bortfeld:{start.isoformat()}:{_slug(title)}",
                    raw_hash=raw_hash,
                )
            )
    return events


def parse_kulturring(html: str, source_id: str, source_url: str) -> list[NormalizedEvent]:
    soup = BeautifulSoup(html, "html.parser")
    raw_hash = sha256_hex(html)
    container = soup.select_one(".veranstaltungen")
    if container is None:
        return []

    events: list[NormalizedEvent] = []
    current_date: str | None = None
    for child in container.find_all(recursive=False):
        classes = set(child.get("class", []))
        if "entryHeadDate" in classes:
            current_date = _text(child)
            continue
        if "entryBody" not in classes or not current_date:
            continue

        details = child.select_one(".wglListEntryDetails")
        title_link = details.select_one("h2 a[href]") if details else None
        left = child.select_one(".leftCol")
        title = _text(title_link)
        left_text = _text(left)
        if not title or not left_text:
            continue
        try:
            start = parse_german_datetime(current_date, left_text)
        except ValueError:
            continue

        detail_url = urljoin(source_url, title_link["href"])
        query = parse_qs(urlsplit(detail_url).query)
        detail_id = (query.get("vDetail") or [_slug(title)])[0]
        location_lines = [
            " ".join(line.split())
            for line in left.get_text("\n", strip=True).splitlines()
            if " ".join(line.split())
        ]
        location = next(
            (
                line
                for line in location_lines
                if not re.search(r"\d{1,2}[.:]\d{2}", line)
                and "onlineticket" not in line.casefold()
            ),
            None,
        )
        organizer_text = ""
        if child.contents and isinstance(child.contents[0], str):
            organizer_text = " ".join(child.contents[0].split())
        organizer = organizer_text.removeprefix("Veranstalter:").strip() or None
        description = None
        if details:
            clone = BeautifulSoup(str(details), "html.parser")
            heading = clone.find("h2")
            if heading:
                heading.decompose()
            description = " ".join(clone.get_text(" ", strip=True).split()) or None

        events.append(
            _event(
                source_id=source_id,
                source_url=source_url,
                title=title,
                start=start,
                location=location,
                description=description,
                organizer=organizer,
                event_url=detail_url,
                source_x_id=f"kulturring:{detail_id}:{start.isoformat()}",
                raw_hash=raw_hash,
            )
        )
    return events


def parse_tourismus(html: str, source_id: str, source_url: str) -> list[NormalizedEvent]:
    soup = BeautifulSoup(html, "html.parser")
    raw_hash = sha256_hex(html)
    events: list[NormalizedEvent] = []
    for card in soup.select("main .wrapper-inner"):
        title = _text(card.select_one("h2.ce_headline"))
        date_node = card.select_one(".ce_text strong")
        link = card.select_one('a[title="Zur Veranstaltung"][href]')
        if not title or date_node is None or link is None:
            continue
        try:
            start, end, all_day = parse_german_date_range(_text(date_node) or "")
        except ValueError:
            continue
        event_url = urljoin(source_url, link["href"])
        description_node = card.select_one(".ce_text")
        description = _text(description_node)
        date_text = _text(date_node)
        if description and date_text:
            description = description.replace(date_text, "", 1).lstrip(" :") or None
        events.append(
            _event(
                source_id=source_id,
                source_url=source_url,
                title=title,
                start=start,
                end=end,
                all_day=all_day,
                location="Peiner Land",
                description=description,
                organizer="Tourismus Peine",
                event_url=event_url,
                source_x_id=f"tourismus:{_slug(urlsplit(event_url).path)}:{start.date().isoformat()}",
                raw_hash=raw_hash,
            )
        )
    return events


def parse_zweidorf(html: str, source_id: str, source_url: str) -> list[NormalizedEvent]:
    soup = BeautifulSoup(html, "html.parser")
    raw_hash = sha256_hex(html)
    content = soup.select_one("#content_main")
    if content is None:
        return []
    paragraphs = content.find_all("p", recursive=False)
    events: list[NormalizedEvent] = []
    for index, paragraph in enumerate(paragraphs):
        styled = paragraph.find("span", style=re.compile(r"Kaushan Script", re.IGNORECASE))
        title = _text(styled)
        if not title:
            continue
        following_text: list[str] = []
        exact_date_text: str | None = None
        for later in paragraphs[index + 1 : index + 5]:
            if later.find("span", style=re.compile(r"Kaushan Script", re.IGNORECASE)):
                break
            value = _text(later)
            if not value:
                continue
            following_text.append(value)
            if "Termin" in value and re.search(r"20\d{2}", value):
                exact_date_text = value
        if exact_date_text is None:
            continue
        try:
            start, end, all_day = parse_german_date_range(exact_date_text)
        except ValueError:
            continue
        clean_title = title.rstrip(". ").strip()
        events.append(
            _event(
                source_id=source_id,
                source_url=source_url,
                title=clean_title,
                start=start,
                end=end,
                all_day=all_day,
                location="Zweidorf",
                description=" ".join(following_text) or None,
                organizer="Traditionsgemeinschaft Zweidorf e. V.",
                event_url=source_url,
                source_x_id=f"zweidorf:{start.date().isoformat()}:{_slug(clean_title)}",
                raw_hash=raw_hash,
            )
        )
    return events


PARSERS: dict[str, StructuredParser] = {
    "kirche-lkcal": parse_lkcal,
    "kirche-bortfeld": parse_bortfeld_table,
    "kulturring-peine": parse_kulturring,
    "tourismus-peine": parse_tourismus,
    "zweidorf-online": parse_zweidorf,
}


def parse_structured_events(
    profile: str,
    html: str,
    *,
    source_id: str,
    source_url: str,
) -> list[NormalizedEvent]:
    try:
        parser = PARSERS[profile]
    except KeyError as exc:
        raise ValueError(f"Unknown structured HTML profile: {profile!r}") from exc
    return parser(html, source_id, source_url)


def discover_kulturring_pages(seed_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    pages: set[int] = set()
    for link in soup.select('.veranstaltungen a[href*="page="]'):
        absolute = urljoin(seed_url, link["href"])
        values = parse_qs(urlsplit(absolute).query).get("page", [])
        if values and values[0].isdigit():
            page = int(values[0])
            if page >= 2:
                pages.add(page)
    return [f"{seed_url}?page={page}" for page in sorted(pages)]


def profile_has_event_markup(profile: str, html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    selectors = {
        "kirche-lkcal": ".cal-list-event",
        "kirche-bortfeld": "table.ce-table",
        "kulturring-peine": ".veranstaltungen .entryBody",
        "tourismus-peine": 'main .wrapper-inner a[title="Zur Veranstaltung"]',
        "zweidorf-online": "#content_main",
    }
    selector = selectors.get(profile)
    return bool(selector and soup.select_one(selector))
