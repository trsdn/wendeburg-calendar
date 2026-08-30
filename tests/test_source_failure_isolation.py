from __future__ import annotations

from datetime import datetime

from wendeburg_calendar.config import AppConfig, SourceConfig, load_config
from wendeburg_calendar.harvest.coverage import Coverage
from wendeburg_calendar.harvest.pipeline import HarvestRunResult, harvest_all
from wendeburg_calendar.harvest.reconcile import reconcile_source
from wendeburg_calendar.http.fetcher import FixtureFetcher
from wendeburg_calendar.model.event import ExtractionMethod, NormalizedEvent
from wendeburg_calendar.util.time import BERLIN

from tests.conftest import MULTI_SOURCE_FIXTURE


def test_one_source_failure_does_not_abort_later_sources(monkeypatch, repo):
    config = AppConfig(
        sources=[
            SourceConfig(id="broken", type="unused", allowed_hosts=["broken.test"]),
            SourceConfig(id="healthy", type="unused", allowed_hosts=["healthy.test"]),
        ]
    )
    calls: list[str] = []

    def fake_harvest_source(
        app_config,
        source_config,
        fetcher,
        repository,
        llm_client,
        rate_limiter=None,
    ):
        calls.append(source_config.id)
        if source_config.id == "broken":
            raise RuntimeError("simulated source failure")
        return HarvestRunResult(
            source_id="healthy",
            coverage=Coverage.COMPLETE,
            events_seen=1,
            reconcile_summary={
                "created": 1,
                "updated": 0,
                "cancelled": 0,
                "observed": 1,
            },
        )

    monkeypatch.setattr(
        "wendeburg_calendar.harvest.pipeline.harvest_source",
        fake_harvest_source,
    )

    results = harvest_all(config, object(), repo, llm_client=None)

    assert calls == ["broken", "healthy"]
    assert results[0].coverage == Coverage.PARTIAL
    assert "Source failure exception=RuntimeError" in (results[0].error or "")
    assert "simulated source failure" not in (results[0].error or "")
    assert results[1].coverage == Coverage.COMPLETE


def test_real_adapter_continues_after_unknown_source_and_preserves_failed_state(repo):
    existing = NormalizedEvent(
        title="Existing broken-source event",
        start=datetime(2026, 12, 1, 18, 0, tzinfo=BERLIN),
        source_id="broken",
        source_url="https://broken.test/event",
        event_url="https://broken.test/event",
        source_x_id="broken-1",
        extraction_method=ExtractionMethod.STRUCTURED_HTML,
        raw_content_hash="broken",
    )
    reconcile_source(repo, "broken", [existing], Coverage.COMPLETE, 3, 7)

    fixture_config = load_config(MULTI_SOURCE_FIXTURE / "config.toml")
    healthy = next(
        source
        for source in fixture_config.sources
        if source.id == "tourismus-peine"
    )
    fixture_config.sources = [
        SourceConfig(
            id="broken",
            type="does-not-exist",
            seed_urls=["https://broken.test/events"],
            allowed_hosts=["broken.test"],
        ),
        healthy,
    ]

    results = harvest_all(
        fixture_config,
        FixtureFetcher(MULTI_SOURCE_FIXTURE),
        repo,
        llm_client=None,
    )

    assert results[0].error is not None
    assert results[1].source_id == "tourismus-peine"
    assert results[1].coverage == Coverage.COMPLETE
    broken_record = next(
        record
        for record in repo.list_all_events()
        if record.source_id == "broken"
    )
    assert broken_record.missing_count == 0
