"""Full offline end-to-end test: CLI `run` (harvest + export) driven entirely
from the on-disk fixture in tests/fixtures/wendeburg_basic, with no network
access whatsoever (FixtureFetcher serves every URL, including robots.txt,
from local files).
"""

from __future__ import annotations

from icalendar import Calendar

from wendeburg_calendar.cli import main
from wendeburg_calendar.db.repository import Repository

from tests.conftest import WENDEBURG_BASIC_FIXTURE


def test_offline_e2e_run_creates_database_and_feed(tmp_path):
    db_path = tmp_path / "wendeburg.sqlite3"
    output_path = tmp_path / "calendar.ics"

    exit_code = main(
        [
            "--config",
            str(WENDEBURG_BASIC_FIXTURE / "config.toml"),
            "--database",
            str(db_path),
            "--output",
            str(output_path),
            "--offline-fixture",
            str(WENDEBURG_BASIC_FIXTURE),
            "run",
        ]
    )

    assert exit_code == 0
    assert db_path.is_file()
    assert output_path.is_file()

    repo = Repository.connect(db_path)
    records = repo.list_all_events()
    repo.close()

    assert len(records) == 2
    titles = sorted(r.title for r in records)
    assert titles == ["Adventsmarkt", "Herbstfest am Dorfplatz"]

    cal = Calendar.from_ical(output_path.read_bytes())
    vevents = list(cal.walk("VEVENT"))
    assert len(vevents) == 2
    statuses = {str(v["STATUS"]) for v in vevents}
    assert statuses == {"CONFIRMED"}


def test_offline_e2e_is_idempotent_across_repeated_runs(tmp_path):
    db_path = tmp_path / "wendeburg.sqlite3"
    output_path = tmp_path / "calendar.ics"
    argv = [
        "--config",
        str(WENDEBURG_BASIC_FIXTURE / "config.toml"),
        "--database",
        str(db_path),
        "--output",
        str(output_path),
        "--offline-fixture",
        str(WENDEBURG_BASIC_FIXTURE),
        "run",
    ]

    assert main(argv) == 0
    repo = Repository.connect(db_path)
    first_records = {r.id: r.sequence for r in repo.list_all_events()}
    repo.close()

    assert main(argv) == 0
    repo = Repository.connect(db_path)
    second_records = {r.id: r.sequence for r in repo.list_all_events()}
    repo.close()

    assert first_records == second_records
    assert len(second_records) == 2


def test_offline_e2e_harvest_and_export_subcommands_work_independently(tmp_path):
    db_path = tmp_path / "wendeburg.sqlite3"
    output_path = tmp_path / "calendar.ics"
    base_argv = [
        "--config",
        str(WENDEBURG_BASIC_FIXTURE / "config.toml"),
        "--database",
        str(db_path),
        "--output",
        str(output_path),
        "--offline-fixture",
        str(WENDEBURG_BASIC_FIXTURE),
    ]

    assert main(base_argv + ["harvest"]) == 0
    assert db_path.is_file()
    assert not output_path.exists()

    assert main(base_argv + ["export"]) == 0
    assert output_path.is_file()
