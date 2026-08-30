"""Row-shaped read model for persisted events (kept separate from the
pydantic NormalizedEvent, which represents freshly-extracted, not-yet-
reconciled data)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from wendeburg_calendar.model.event import EventStatus, ExtractionMethod
from wendeburg_calendar.util.time import parse_iso_utc


@dataclass(frozen=True)
class EventRecord:
    id: str
    source_id: str
    title: str
    start_utc: datetime
    end_utc: datetime | None
    all_day: bool
    location: str | None
    description: str | None
    organizer: str | None
    source_url: str | None
    event_url: str | None
    status: EventStatus
    cancelled_reason: str | None
    sequence: int
    semantic_hash: str
    extraction_method: ExtractionMethod
    extraction_confidence: float
    missing_count: int
    first_missing_at_utc: datetime | None
    dtstamp_utc: datetime
    last_modified_utc: datetime
    created_at_utc: datetime
    updated_at_utc: datetime

    @classmethod
    def from_row(cls, row) -> "EventRecord":
        return cls(
            id=row["id"],
            source_id=row["source_id"],
            title=row["title"],
            start_utc=parse_iso_utc(row["start_utc"]),
            end_utc=parse_iso_utc(row["end_utc"]) if row["end_utc"] else None,
            all_day=bool(row["all_day"]),
            location=row["location"],
            description=row["description"],
            organizer=row["organizer"],
            source_url=row["source_url"],
            event_url=row["event_url"],
            status=EventStatus(row["status"]),
            cancelled_reason=row["cancelled_reason"],
            sequence=row["sequence"],
            semantic_hash=row["semantic_hash"],
            extraction_method=ExtractionMethod(row["extraction_method"]),
            extraction_confidence=row["extraction_confidence"],
            missing_count=row["missing_count"],
            first_missing_at_utc=(
                parse_iso_utc(row["first_missing_at_utc"])
                if row["first_missing_at_utc"]
                else None
            ),
            dtstamp_utc=parse_iso_utc(row["dtstamp_utc"]),
            last_modified_utc=parse_iso_utc(row["last_modified_utc"]),
            created_at_utc=parse_iso_utc(row["created_at_utc"]),
            updated_at_utc=parse_iso_utc(row["updated_at_utc"]),
        )
