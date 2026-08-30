"""Deterministic parsing helpers for German event date labels."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from wendeburg_calendar.util.time import BERLIN

_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

_NUMERIC_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(20\d{2})(?!\d)")
_NUMERIC_RANGE_RE = re.compile(
    r"(?<!\d)(?P<start_day>\d{1,2})\.(?P<start_month>\d{1,2})\.(?P<start_year>20\d{2})"
    r"\s*(?:\+|-)\s*"
    r"(?P<end_day>\d{1,2})\.(?P<end_month>\d{1,2})\.(?:(?P<end_year>20\d{2}))?"
)
_SAME_MONTH_RANGE_RE = re.compile(
    r"(?P<start>\d{1,2})\.\s*(?:\+|-)\s*(?P<end>\d{1,2})\.\s*"
    r"(?P<month>[A-Za-zÄÖÜäöüß]+)\s+(?P<year>20\d{2})",
    re.IGNORECASE,
)
_CROSS_MONTH_RANGE_RE = re.compile(
    r"(?P<start>\d{1,2})\.\s*(?P<start_month>[A-Za-zÄÖÜäöüß]+)\s*"
    r"(?:\+|-)\s*(?P<end>\d{1,2})\.\s*"
    r"(?P<end_month>[A-Za-zÄÖÜäöüß]+)\s+(?P<year>20\d{2})",
    re.IGNORECASE,
)
_SINGLE_TEXT_DATE_RE = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})\.\s*(?P<month>[A-Za-zÄÖÜäöüß]+)\s+"
    r"(?P<year>20\d{2})(?!\d)",
    re.IGNORECASE,
)


def _month_number(value: str) -> int:
    try:
        return _MONTHS[value.casefold()]
    except KeyError as exc:
        raise ValueError(f"Unknown German month: {value!r}") from exc


def berlin_midnight(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=BERLIN)


def parse_german_date_range(text: str) -> tuple[datetime, datetime, bool]:
    """Parse a single date or inclusive German date range.

    The returned all-day end is exclusive, matching RFC 5545 semantics.
    """

    normalized = " ".join(text.replace("\xa0", " ").split())

    match = _NUMERIC_RANGE_RE.search(normalized)
    if match:
        start_year = int(match.group("start_year"))
        end_year = int(match.group("end_year") or start_year)
        start_date = date(
            start_year,
            int(match.group("start_month")),
            int(match.group("start_day")),
        )
        end_date = date(
            end_year,
            int(match.group("end_month")),
            int(match.group("end_day")),
        )
        return berlin_midnight(start_date), berlin_midnight(end_date + timedelta(days=1)), True

    match = _CROSS_MONTH_RANGE_RE.search(normalized)
    if match:
        year = int(match.group("year"))
        start_date = date(
            year,
            _month_number(match.group("start_month")),
            int(match.group("start")),
        )
        end_date = date(
            year,
            _month_number(match.group("end_month")),
            int(match.group("end")),
        )
        return berlin_midnight(start_date), berlin_midnight(end_date + timedelta(days=1)), True

    match = _SAME_MONTH_RANGE_RE.search(normalized)
    if match:
        year = int(match.group("year"))
        month = _month_number(match.group("month"))
        start_date = date(year, month, int(match.group("start")))
        end_date = date(year, month, int(match.group("end")))
        return berlin_midnight(start_date), berlin_midnight(end_date + timedelta(days=1)), True

    match = _NUMERIC_DATE_RE.search(normalized)
    if match:
        value = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        start = berlin_midnight(value)
        return start, start + timedelta(days=1), True

    match = _SINGLE_TEXT_DATE_RE.search(normalized)
    if match:
        value = date(
            int(match.group("year")),
            _month_number(match.group("month")),
            int(match.group("day")),
        )
        start = berlin_midnight(value)
        return start, start + timedelta(days=1), True

    raise ValueError(f"No exact German event date found in {text!r}")


def parse_german_datetime(date_text: str, time_text: str) -> datetime:
    day_match = _NUMERIC_DATE_RE.search(date_text)
    if day_match:
        year = int(day_match.group(3))
        month = int(day_match.group(2))
        day = int(day_match.group(1))
    else:
        text_match = _SINGLE_TEXT_DATE_RE.search(date_text)
        if text_match is None:
            raise ValueError(f"No German date found in {date_text!r}")
        year = int(text_match.group("year"))
        month = _month_number(text_match.group("month"))
        day = int(text_match.group("day"))

    time_match = re.search(r"(?<!\d)(\d{1,2})[:.](\d{2})(?!\d)", time_text)
    if time_match is None:
        raise ValueError(f"No event time found in {time_text!r}")
    return datetime(
        year,
        month,
        day,
        int(time_match.group(1)),
        int(time_match.group(2)),
        tzinfo=BERLIN,
    )
