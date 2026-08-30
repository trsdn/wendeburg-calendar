"""Harvest coverage classification.

COMPLETE  - the source's seed pages were freshly and fully fetched/parsed
            (or confirmed empty via an explicit "no events" marker); safe
            to use for missing/absence bookkeeping.
UNCHANGED - all seed pages returned HTTP 304 Not Modified; nothing new was
            observed, but this is equivalent to a prior COMPLETE state and
            must NOT be treated as evidence of absence either way.
PARTIAL   - anything failed, was ambiguous (a mix of unchanged and fresh
            seed pages that can't be safely reconciled), or produced a
            suspicious zero-event extraction from an otherwise normal
            response. Must never advance missing counters and must never
            be treated as confirming absence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Coverage(str, Enum):
    COMPLETE = "COMPLETE"
    UNCHANGED = "UNCHANGED"
    PARTIAL = "PARTIAL"


@dataclass
class SeedFetchOutcome:
    seed_url: str
    ok: bool
    not_modified: bool = False
    urls: list[str] = field(default_factory=list)
    explicit_empty: bool = False
    note: str = ""


@dataclass
class DiscoveryResult:
    seed_outcomes: list[SeedFetchOutcome]

    @property
    def urls(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for outcome in self.seed_outcomes:
            for u in outcome.urls:
                if u not in seen:
                    seen.add(u)
                    ordered.append(u)
        return ordered


def discovery_coverage(discovery: DiscoveryResult) -> Coverage:
    outcomes = discovery.seed_outcomes
    if not outcomes:
        return Coverage.PARTIAL
    if any(not o.ok for o in outcomes):
        return Coverage.PARTIAL
    if all(o.not_modified for o in outcomes):
        return Coverage.UNCHANGED
    if any(o.not_modified for o in outcomes):
        # Mixed 304/200 across seeds: we cannot safely reconstruct the full
        # discovered set for the unchanged seed(s), so this is ambiguous.
        return Coverage.PARTIAL
    if not discovery.urls and not any(o.explicit_empty for o in outcomes):
        # Zero candidate events extracted from an otherwise-OK response,
        # with no explicit "no events" confirmation -> suspicious.
        return Coverage.PARTIAL
    return Coverage.COMPLETE
