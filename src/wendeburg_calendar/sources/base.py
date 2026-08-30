"""Narrow adapter interface every event source must implement.

Adapters receive only a `HarvestClient` (never a raw Fetcher), so they are
structurally unable to bypass robots.txt / host allowlist / size limits -
those are enforced centrally regardless of what an adapter tries to do.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from wendeburg_calendar.config import SourceConfig
from wendeburg_calendar.db.models import EventRecord
from wendeburg_calendar.harvest.coverage import DiscoveryResult
from wendeburg_calendar.http.client import HarvestClient
from wendeburg_calendar.http.errors import failure_diagnostic
from wendeburg_calendar.llm.client import LlmClient
from wendeburg_calendar.model.event import NormalizedEvent
from wendeburg_calendar.util.time import to_berlin


@dataclass
class AdapterContext:
    """Everything an adapter needs beyond its own source-specific config."""

    llm_client: LlmClient | None
    repository: object | None  # db.repository.Repository, kept loosely typed to avoid a cyclic import
    llm_enabled: bool
    max_input_chars: int
    max_events_per_source: int


@dataclass(frozen=True)
class CandidateFetchResult:
    """Typed result for one discovered resource.

    A single candidate can yield several events (for example a structured
    listing page or an ICS feed). ``ok=False`` means the candidate could not
    be fetched or parsed reliably; already-extracted events may still be
    returned so the source can retain useful partial coverage.
    """

    events: tuple[NormalizedEvent, ...] = field(default_factory=tuple)
    ok: bool = True
    note: str = ""

    @classmethod
    def success(cls, events: list[NormalizedEvent] | tuple[NormalizedEvent, ...]) -> "CandidateFetchResult":
        return cls(events=tuple(events), ok=True)

    @classmethod
    def failure(
        cls,
        note: str,
        events: list[NormalizedEvent] | tuple[NormalizedEvent, ...] = (),
    ) -> "CandidateFetchResult":
        return cls(events=tuple(events), ok=False, note=note)


def record_to_normalized(
    record: EventRecord,
    source_id: str,
    repository: object | None,
) -> NormalizedEvent:
    """Reconstruct one observed event after an HTTP 304 response.

    Stable aliases are restored from SQLite so several events sharing one
    listing resource remain distinct on an unchanged run.
    """

    aliases: dict[str, str] = {}
    if repository is not None:
        list_aliases = getattr(repository, "list_aliases_for_event", None)
        if callable(list_aliases):
            aliases = list_aliases(record.id, source_id)

    return NormalizedEvent(
        title=record.title,
        # Reconciliation hashes freshly parsed events in the application's
        # canonical Europe/Berlin timezone. SQLite stores UTC, so restore the
        # canonical timezone before hashing a 304 snapshot; otherwise the
        # same instant has a different ISO string and spuriously bumps
        # SEQUENCE on every conditional-GET reuse.
        start=to_berlin(record.start_utc),
        end=to_berlin(record.end_utc) if record.end_utc else None,
        all_day=record.all_day,
        location=record.location,
        description=record.description,
        organizer=record.organizer,
        status=record.status,
        source_id=source_id,
        source_url=record.source_url or "",
        event_url=record.event_url,
        source_event_uid=aliases.get("ics_uid"),
        source_x_id=aliases.get("x_id"),
        extraction_method=record.extraction_method,
        extraction_confidence=record.extraction_confidence,
        raw_content_hash="unchanged:" + record.semantic_hash,
    )


class SourceAdapter(ABC):
    def __init__(self, source_config: SourceConfig, context: AdapterContext):
        self.source_config = source_config
        self.context = context

    @abstractmethod
    def discover(self, client: HarvestClient) -> DiscoveryResult:
        """Fetch the source's seed page(s) and return candidate event URLs."""

    @abstractmethod
    def fetch_candidate(self, client: HarvestClient, url: str) -> CandidateFetchResult:
        """Fetch and normalize zero or more events from one candidate resource."""

    def reuse_unchanged(self, url: str) -> CandidateFetchResult:
        repo = self.context.repository
        if repo is None:
            return CandidateFetchResult.failure(
                failure_diagnostic(
                    url,
                    category="No repository snapshot for unchanged resource",
                )
            )
        records = [
            record
            for record in repo.list_events_for_source(self.source_config.id)
            if record.source_url == url
        ]
        if not records:
            return CandidateFetchResult.failure(
                failure_diagnostic(
                    url,
                    category="No stored events for unchanged resource",
                )
            )
        return CandidateFetchResult.success(
            [
                record_to_normalized(record, self.source_config.id, repo)
                for record in records
            ]
        )
