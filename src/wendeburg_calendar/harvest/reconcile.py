"""Reconciliation: turn a batch of freshly normalized events into database
writes, applying the semantic-change/SEQUENCE rules and the absence/
cancellation policy.

Rules enforced here (see project spec):
  - A brand new event starts at SEQUENCE 0.
  - A change in exported, user-visible fields (title, start, end, all_day,
    location, description, organizer, status, event_url) bumps SEQUENCE,
    exactly once per reconciliation. Non-semantic identity/provenance and
    fetch bookkeeping (including source_url) never bumps it.
  - An explicit source cancellation (STATUS:CANCELLED from the source)
    takes effect immediately, on the next harvest that observes it.
  - Ordinary absence does NOT cancel on a single miss. An event must be
    missing across `missing_threshold` consecutive COMPLETE harvests *and*
    at least `missing_grace_days` must have elapsed since it was first
    observed missing before it is auto-cancelled.
  - PARTIAL and UNCHANGED coverage never advance missing counters.
  - Reappearance clears missing state. An event previously auto-cancelled
    for absence is revived if it reappears; cancelled tombstones stay in
    the database (and therefore stay exportable) either way.
"""

from __future__ import annotations


from wendeburg_calendar.harvest.coverage import Coverage
from wendeburg_calendar.harvest.identity import candidate_aliases, resolve_or_create
from wendeburg_calendar.model.event import EventStatus, NormalizedEvent
from wendeburg_calendar.util.hashing import cache_key
from wendeburg_calendar.util.time import iso_utc, now_utc


def semantic_hash_of(normalized: NormalizedEvent) -> str:
    fields = normalized.semantic_fields()
    return cache_key(*[str(f) for f in fields])


def _stable_aliases_conflict(
    prior: set[tuple[str, str]], current: set[tuple[str, str]]
) -> bool:
    """Whether two same-batch observations present different stable IDs."""
    prior_types = {alias_type for alias_type, _ in prior}
    current_types = {alias_type for alias_type, _ in current}
    return (
        bool(prior)
        and bool(current)
        and prior.isdisjoint(current)
        and not prior_types.isdisjoint(current_types)
    )


def reconcile_source(
    repo,
    source_id: str,
    normalized_events: list[NormalizedEvent],
    coverage: Coverage,
    missing_threshold: int,
    missing_grace_days: int,
) -> dict:
    """Reconcile one source's freshly harvested events within one transaction.

    Returns a small summary dict (mostly useful for logging/tests).
    """
    observed_ids: set[str] = set()
    observed_stable_aliases: dict[str, set[tuple[str, str]]] = {}
    created = 0
    updated = 0
    cancelled = 0

    with repo.conn:
        for normalized in normalized_events:
            stable_aliases = {
                alias
                for alias in candidate_aliases(normalized)
                if alias[0] in {"ics_uid", "x_id"}
            }
            excluded_event_ids = {
                event_id
                for event_id, prior_aliases in observed_stable_aliases.items()
                if _stable_aliases_conflict(prior_aliases, stable_aliases)
            }
            event_id, is_new = resolve_or_create(
                repo,
                source_id,
                normalized,
                excluded_event_ids=excluded_event_ids,
            )
            observed_ids.add(event_id)
            observed_stable_aliases.setdefault(event_id, set()).update(stable_aliases)
            sem_hash = semantic_hash_of(normalized)
            event_url = normalized.event_url or normalized.source_url

            if is_new:
                cancelled_reason = "source" if normalized.status == EventStatus.CANCELLED else None
                repo.create_event(
                    event_id,
                    source_id=source_id,
                    title=normalized.title,
                    start_utc=normalized.start,
                    end_utc=normalized.end,
                    all_day=normalized.all_day,
                    location=normalized.location,
                    description=normalized.description,
                    organizer=normalized.organizer,
                    source_url=normalized.source_url,
                    event_url=event_url,
                    status=normalized.status.value,
                    sequence=0,
                    semantic_hash=sem_hash,
                    extraction_method=normalized.extraction_method.value,
                    extraction_confidence=normalized.extraction_confidence,
                    cancelled_reason=cancelled_reason,
                )
                created += 1
                continue

            existing = repo.get_event(event_id)
            was_missing = existing.missing_count > 0
            semantic_changed = sem_hash != existing.semantic_hash
            event_url_changed = event_url != existing.event_url
            was_absence_cancelled = (
                existing.status == EventStatus.CANCELLED
                and existing.cancelled_reason == "absence"
            )
            revived = was_absence_cancelled and normalized.status != EventStatus.CANCELLED

            if semantic_changed or event_url_changed or revived:
                if normalized.status == EventStatus.CANCELLED:
                    cancelled_reason = "source"
                elif revived:
                    cancelled_reason = None
                else:
                    cancelled_reason = existing.cancelled_reason
                repo.update_event_semantic(
                    event_id,
                    title=normalized.title,
                    start_utc=normalized.start,
                    end_utc=normalized.end,
                    all_day=normalized.all_day,
                    location=normalized.location,
                    description=normalized.description,
                    organizer=normalized.organizer,
                    source_url=normalized.source_url,
                    event_url=event_url,
                    status=normalized.status.value,
                    sequence=existing.sequence + 1,
                    semantic_hash=sem_hash,
                    extraction_method=normalized.extraction_method.value,
                    extraction_confidence=normalized.extraction_confidence,
                    cancelled_reason=cancelled_reason,
                )
                updated += 1
            else:
                repo.touch_event_seen(
                    event_id,
                    source_url=normalized.source_url,
                    event_url=event_url,
                )

            if was_missing:
                repo.reset_missing_state(event_id)

        if coverage == Coverage.COMPLETE:
            now = now_utc()
            for record in repo.list_events_for_source(source_id):
                if record.id in observed_ids:
                    continue
                if record.status == EventStatus.CANCELLED:
                    continue  # already a terminal tombstone either way

                first_missing_at = record.first_missing_at_utc or now
                repo.increment_missing(record.id, iso_utc(first_missing_at))
                new_missing_count = record.missing_count + 1

                elapsed_days = (now - first_missing_at).total_seconds() / 86400.0
                if new_missing_count >= missing_threshold and elapsed_days >= missing_grace_days:
                    repo.cancel_event(record.id, reason="absence", sequence=record.sequence + 1)
                    cancelled += 1

    return {
        "created": created,
        "updated": updated,
        "cancelled": cancelled,
        "observed": len(observed_ids),
    }
