"""Timezone helpers.

All persisted timestamps are stored as UTC ISO-8601 strings in SQLite.
All user-facing event times are normalized to Europe/Berlin (the only
timezone this project currently cares about) but kept timezone-aware
throughout so DST transitions are handled correctly by `zoneinfo`.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc


def now_utc() -> datetime:
    """Current time, timezone-aware, UTC."""
    return datetime.now(tz=UTC)


def to_utc(dt: datetime) -> datetime:
    """Convert an aware datetime to UTC. Raises on naive input."""
    if dt.tzinfo is None:
        raise ValueError("to_utc() requires a timezone-aware datetime")
    return dt.astimezone(UTC)


def to_berlin(dt: datetime) -> datetime:
    """Convert an aware datetime to Europe/Berlin. Raises on naive input."""
    if dt.tzinfo is None:
        raise ValueError("to_berlin() requires a timezone-aware datetime")
    return dt.astimezone(BERLIN)


def iso_utc(dt: datetime) -> str:
    """Serialize an aware datetime as a UTC ISO-8601 string for storage."""
    return to_utc(dt).isoformat()


def parse_iso_utc(value: str) -> datetime:
    """Parse a UTC ISO-8601 string previously produced by iso_utc()."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def date_to_berlin_midnight(d: date) -> datetime:
    """Turn a plain date (all-day event marker) into a Berlin midnight datetime."""
    return datetime(d.year, d.month, d.day, tzinfo=BERLIN)
