"""Top-level harvest orchestration for a single source.

Data flow (see project spec):
  config -> source adapter -> robots-aware fetch -> deterministic ICS
  parse (or LLM fallback for unstructured HTML) -> local validation ->
  SQLite reconcile transaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from wendeburg_calendar.config import AppConfig, SourceConfig
from wendeburg_calendar.harvest.coverage import Coverage, discovery_coverage
from wendeburg_calendar.harvest.reconcile import reconcile_source
from wendeburg_calendar.http.client import HarvestClient
from wendeburg_calendar.http.errors import diagnostic_for_exception
from wendeburg_calendar.http.fetcher import Fetcher
from wendeburg_calendar.http.retry import RetryExecutor
from wendeburg_calendar.http.robots import RobotsChecker
from wendeburg_calendar.http.throttle import HostRateLimiter
from wendeburg_calendar.llm.client import LlmClient
from wendeburg_calendar.sources.base import AdapterContext
from wendeburg_calendar.sources.registry import create as create_adapter
from wendeburg_calendar.util.time import iso_utc, now_utc


@dataclass
class HarvestRunResult:
    source_id: str
    coverage: Coverage
    events_seen: int
    reconcile_summary: dict
    error: str | None = None
    notes: str = ""


_MAX_DIAGNOSTICS = 8
_MAX_DIAGNOSTIC_CHARS = 320
_MAX_NOTES_CHARS = 2_000


def _bounded_diagnostics(diagnostics: list[str]) -> str:
    cleaned = [
        " ".join(diagnostic.split())[:_MAX_DIAGNOSTIC_CHARS]
        for diagnostic in diagnostics
        if diagnostic.strip()
    ]
    retained: list[str] = []
    for diagnostic in cleaned[:_MAX_DIAGNOSTICS]:
        candidate = " | ".join([*retained, diagnostic])
        remaining = len(cleaned) - len(retained) - 1
        reserve = len(f" | omitted={remaining}") if remaining else 0
        if len(candidate) + reserve > _MAX_NOTES_CHARS:
            break
        retained.append(diagnostic)
    omitted = len(cleaned) - len(retained)
    if omitted:
        retained.append(f"omitted={omitted}")
    return " | ".join(retained)


def harvest_source(
    app_config: AppConfig,
    source_config: SourceConfig,
    fetcher: Fetcher,
    repo,
    llm_client: LlmClient | None,
    rate_limiter: HostRateLimiter | None = None,
) -> HarvestRunResult:
    started_at = iso_utc(now_utc())

    shared_rate_limiter = rate_limiter or HostRateLimiter()
    retry_sleeper = (
        (lambda _seconds: None)
        if getattr(fetcher, "is_offline", False)
        else None
    )
    retry_executor = (
        RetryExecutor(sleeper=retry_sleeper)
        if retry_sleeper is not None
        else RetryExecutor()
    )
    robots = RobotsChecker(
        fetcher,
        app_config.general.user_agent,
        rate_limiter=shared_rate_limiter,
        allowed_hosts=set(source_config.allowed_hosts),
        min_request_delay_seconds=source_config.min_request_delay_seconds,
        retry_executor=retry_executor,
    )
    client = HarvestClient(
        fetcher=fetcher,
        robots=robots,
        allowed_hosts=set(source_config.allowed_hosts),
        max_content_bytes=app_config.harvest.max_content_bytes,
        cache=repo,
        rate_limiter=shared_rate_limiter,
        min_request_delay_seconds=source_config.min_request_delay_seconds,
        retry_executor=retry_executor,
    )

    context = AdapterContext(
        llm_client=llm_client,
        repository=repo,
        llm_enabled=app_config.llm.enabled,
        max_input_chars=app_config.llm.max_input_chars,
        max_events_per_source=app_config.harvest.max_events_per_source,
    )
    adapter = create_adapter(source_config, context)

    discovery = adapter.discover(client)
    coverage = discovery_coverage(discovery)

    normalized_events = []
    any_item_failure = False
    diagnostics = [
        outcome.note
        for outcome in discovery.seed_outcomes
        if outcome.note
    ]

    if coverage != Coverage.UNCHANGED:
        if len(discovery.urls) > app_config.harvest.max_events_per_source:
            any_item_failure = True
        urls = discovery.urls[: app_config.harvest.max_events_per_source]
        for url in urls:
            try:
                candidate = adapter.fetch_candidate(client, url)
            except Exception as exc:  # isolate one malformed candidate
                any_item_failure = True
                diagnostics.append(
                    diagnostic_for_exception(
                        url,
                        exc,
                        category="Candidate failure",
                    )
                )
                continue
            if not candidate.ok:
                any_item_failure = True
                if candidate.note:
                    diagnostics.append(candidate.note)
            remaining = app_config.harvest.max_events_per_source - len(normalized_events)
            if remaining <= 0:
                any_item_failure = True
                break
            if len(candidate.events) > remaining:
                normalized_events.extend(candidate.events[:remaining])
                any_item_failure = True
                break
            normalized_events.extend(candidate.events)

    if any_item_failure and coverage == Coverage.COMPLETE:
        coverage = Coverage.PARTIAL

    summary = reconcile_source(
        repo,
        source_config.id,
        normalized_events,
        coverage,
        app_config.harvest.missing_threshold,
        app_config.harvest.missing_grace_days,
    )

    diagnostic_notes = _bounded_diagnostics(diagnostics)
    notes = f"discovered={len(discovery.urls)} item_failures={any_item_failure}"
    if diagnostic_notes:
        notes = f"{notes} diagnostics={diagnostic_notes}"
    repo.record_harvest_run(source_config.id, coverage.value, len(normalized_events), notes, started_at)

    return HarvestRunResult(
        source_id=source_config.id,
        coverage=coverage,
        events_seen=len(normalized_events),
        reconcile_summary=summary,
        notes=notes,
    )


def harvest_all(
    app_config: AppConfig,
    fetcher: Fetcher,
    repo,
    llm_client: LlmClient | None,
    source_ids: list[str] | None = None,
) -> list[HarvestRunResult]:
    results = []
    sleeper = (lambda _seconds: None) if getattr(fetcher, "is_offline", False) else None
    rate_limiter = HostRateLimiter(sleeper=sleeper) if sleeper is not None else HostRateLimiter()
    for source_config in app_config.sources:
        if not source_config.enabled:
            continue
        if source_ids and source_config.id not in source_ids:
            continue
        try:
            results.append(
                harvest_source(
                    app_config,
                    source_config,
                    fetcher,
                    repo,
                    llm_client,
                    rate_limiter=rate_limiter,
                )
            )
        except Exception as exc:
            started_at = iso_utc(now_utc())
            diagnostic_url = (
                source_config.seed_urls[0]
                if source_config.seed_urls
                else "https://invalid.local/"
            )
            error = diagnostic_for_exception(
                diagnostic_url,
                exc,
                category="Source failure",
            )
            try:
                repo.record_harvest_run(
                    source_config.id,
                    Coverage.PARTIAL.value,
                    0,
                    f"source_failed={error}",
                    started_at,
                )
            except Exception:
                pass
            results.append(
                HarvestRunResult(
                    source_id=source_config.id,
                    coverage=Coverage.PARTIAL,
                    events_seen=0,
                    reconcile_summary={
                        "created": 0,
                        "updated": 0,
                        "cancelled": 0,
                        "observed": 0,
                    },
                    error=error,
                    notes=f"source_failed={error}",
                )
            )
    return results
