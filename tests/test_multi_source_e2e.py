from __future__ import annotations

from icalendar import Calendar

from wendeburg_calendar.cli import main
from wendeburg_calendar.db.repository import Repository

from tests.conftest import MULTI_SOURCE_FIXTURE


def test_deterministic_multi_source_e2e_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    db_path = tmp_path / "multi.sqlite3"
    output_path = tmp_path / "multi.ics"

    exit_code = main(
        [
            "--config",
            str(MULTI_SOURCE_FIXTURE / "config.toml"),
            "--database",
            str(db_path),
            "--output",
            str(output_path),
            "--offline-fixture",
            str(MULTI_SOURCE_FIXTURE),
            "run",
        ]
    )

    assert exit_code == 0
    repo = Repository.connect(db_path)
    records = repo.list_all_events()
    repo.close()

    assert len(records) == 10
    assert {record.source_id for record in records} == {
        "peine-erleben",
        "kulturring-peine",
        "tourismus-peine",
        "zweidorf-online",
        "kirche-wendeburg",
        "kirche-bortfeld",
    }
    exported = list(Calendar.from_ical(output_path.read_bytes()).walk("VEVENT"))
    assert len(exported) == 10


def test_multi_source_e2e_is_idempotent(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    db_path = tmp_path / "multi.sqlite3"
    output_path = tmp_path / "multi.ics"
    argv = [
        "--config",
        str(MULTI_SOURCE_FIXTURE / "config.toml"),
        "--database",
        str(db_path),
        "--output",
        str(output_path),
        "--offline-fixture",
        str(MULTI_SOURCE_FIXTURE),
        "run",
    ]

    assert main(argv) == 0
    capsys.readouterr()
    assert main(argv) == 0
    second_output = capsys.readouterr().out
    repo = Repository.connect(db_path)
    records = repo.list_all_events()
    repo.close()

    harvest_lines = [
        line for line in second_output.splitlines() if line.startswith("[harvest]")
    ]
    assert len(harvest_lines) == 6
    assert all("coverage=COMPLETE" in line for line in harvest_lines)
    assert any(
        "source=peine-erleben coverage=COMPLETE events_seen=2" in line
        for line in harvest_lines
    )
    assert len(records) == 10
    assert {record.sequence for record in records} == {0}
