"""Normalized event model.

This is the single shape that every source adapter and every extraction
path (deterministic ICS parsing or LLM fallback) must converge on before
the event ever reaches the reconciliation / persistence layer. Keeping one
strict, validated shape here is what lets the rest of the pipeline stay
source-agnostic.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class EventStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    TENTATIVE = "TENTATIVE"
    CANCELLED = "CANCELLED"


class ExtractionMethod(str, Enum):
    ICS = "ics"
    JSON_LD = "json-ld"
    STRUCTURED_HTML = "structured-html"
    LLM = "llm"


class NormalizedEvent(BaseModel):
    """A single event as understood by this system, independent of source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    start: datetime
    end: datetime | None = None
    all_day: bool = False

    location: str | None = None
    description: str | None = None
    organizer: str | None = None

    status: EventStatus = EventStatus.CONFIRMED

    # Source / identity information used by harvest.identity for matching.
    # source_url is the fetched detail/.ical resource and must never be
    # replaced by a VEVENT URL. event_url is user-visible metadata exported
    # as URL; when absent, reconciliation falls back to source_url.
    source_id: str
    source_url: str
    event_url: str | None = None
    source_event_uid: str | None = None  # ICS UID, if the source exposed one
    source_x_id: str | None = None  # recognized stable X-ID (see source docs)

    # Provenance / bookkeeping.
    extraction_method: ExtractionMethod
    extraction_confidence: float = 1.0
    raw_content_hash: str
    source_sequence: int | None = None
    source_last_modified: datetime | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank")
        return v

    @field_validator("start", "end", "source_last_modified")
    @classmethod
    def _must_be_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime fields must be timezone-aware")
        return v

    @field_validator("extraction_confidence")
    @classmethod
    def _confidence_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("extraction_confidence must be within [0, 1]")
        return v

    @model_validator(mode="after")
    def _end_after_start(self) -> "NormalizedEvent":
        if self.end is not None and self.end < self.start:
            raise ValueError("end must not be before start")
        return self

    def semantic_fields(self) -> tuple:
        """Fields stored in the legacy semantic hash. Order matters.

        event_url is compared separately during reconciliation because it is
        user-visible and must bump SEQUENCE, while preserving existing hashes
        across the additive database migration.
        """
        return (
            self.title,
            self.start.isoformat(),
            self.end.isoformat() if self.end else "",
            self.all_day,
            (self.location or "").strip(),
            (self.description or "").strip(),
            (self.organizer or "").strip(),
            self.status.value,
        )
