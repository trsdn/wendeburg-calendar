"""Event identity resolution.

Precedence (strongest to weakest):
  1. ICS UID
  2. Recognized X-ID (source-specific stable identifier)
  3. Source ID + canonical event URL
  4. Weak fingerprint (normalized title + start date) - last resort only

Whichever alias matches an existing event wins, and *all* of the current
extraction's candidate aliases are then (re-)attached to that same event.
This is what lets a weak, fingerprint-only match made on day 1 upgrade
itself once a stronger identifier (e.g. an ICS UID) becomes available on
day 2, without ever minting a duplicate event.

Fingerprint collisions are an inherent risk of any weak, content-based
identifier (two unrelated events can coincidentally share a title and
date). To guard against silently merging two events that each carry their
OWN distinct, stable strong identifier (ICS UID / X-ID), a fingerprint
match is only accepted if it does not conflict with a *different* already
-persisted UID/X-ID belonging to the matched event. `source_url` is
deliberately excluded from this conflict check because it can legitimately
change over time for the very same event (site redesigns, differently
formatted links, ...), so a differing URL alone must never veto an
otherwise-valid upgrade.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Protocol
from uuid import uuid4

from wendeburg_calendar.model.event import NormalizedEvent
from wendeburg_calendar.util.fingerprint import weak_fingerprint

# Alias types whose values are trusted enough that a mismatch between what
# an event currently presents and what is already on file for another
# candidate event rules out reusing that event via a weaker (fingerprint)
# match. Deliberately excludes "source_url", which is allowed to drift.
_CONFLICT_CHECK_TYPES = frozenset({"ics_uid", "x_id"})


class AliasStore(Protocol):
    def find_event_id_by_alias(
        self, alias_type: str, alias_value: str, source_id: str
    ) -> str | None: ...

    def upsert_alias(
        self, alias_type: str, alias_value: str, source_id: str, event_id: str
    ) -> None: ...

    def get_alias_value_for_event(
        self, event_id: str, alias_type: str, source_id: str
    ) -> str | None: ...


def candidate_aliases(normalized: NormalizedEvent) -> list[tuple[str, str]]:
    """All identity candidates for one extraction, strongest first, with the
    weak fingerprint always last."""
    candidates: list[tuple[str, str]] = []
    if normalized.source_event_uid:
        candidates.append(("ics_uid", normalized.source_event_uid))
    if normalized.source_x_id:
        candidates.append(("x_id", normalized.source_x_id))
    if normalized.source_url:
        candidates.append(("source_url", normalized.source_url))
    candidates.append(("fingerprint", weak_fingerprint(normalized.title, normalized.start)))
    return candidates


def _attach_all(store: AliasStore, source_id: str, event_id: str, candidates: list[tuple[str, str]]) -> None:
    for alias_type, alias_value in candidates:
        store.upsert_alias(alias_type, alias_value, source_id, event_id)


def _conflicts_with_matched_event(
    store: AliasStore, source_id: str, event_id: str, strong_candidates: list[tuple[str, str]]
) -> bool:
    for alias_type, alias_value in strong_candidates:
        if alias_type not in _CONFLICT_CHECK_TYPES:
            continue
        existing_value = store.get_alias_value_for_event(event_id, alias_type, source_id)
        if existing_value is not None and existing_value != alias_value:
            return True
    return False


def resolve_or_create(
    store: AliasStore,
    source_id: str,
    normalized: NormalizedEvent,
    excluded_event_ids: Collection[str] = (),
) -> tuple[str, bool]:
    """Resolve `normalized` to a stable internal event id.

    Returns (event_id, is_new). All candidate aliases are attached to the
    resolved event id, upgrading any weaker alias that previously pointed
    elsewhere or existed only in isolation. Reconciliation may exclude an
    event already claimed by a disjoint identity in the same batch; this
    repairs legacy rows collapsed by the former VEVENT-URL alias bug.
    """
    candidates = candidate_aliases(normalized)
    strong_candidates = [c for c in candidates if c[0] != "fingerprint"]
    fingerprint_candidate = candidates[-1]

    for alias_type, alias_value in strong_candidates:
        event_id = store.find_event_id_by_alias(alias_type, alias_value, source_id)
        if event_id is not None and event_id not in excluded_event_ids:
            _attach_all(store, source_id, event_id, candidates)
            return event_id, False

    fp_type, fp_value = fingerprint_candidate
    event_id = store.find_event_id_by_alias(fp_type, fp_value, source_id)
    if (
        event_id is not None
        and event_id not in excluded_event_ids
        and not _conflicts_with_matched_event(
            store, source_id, event_id, strong_candidates
        )
    ):
        _attach_all(store, source_id, event_id, candidates)
        return event_id, False

    event_id = str(uuid4())
    _attach_all(store, source_id, event_id, candidates)
    return event_id, True
