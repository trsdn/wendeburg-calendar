from __future__ import annotations

import sqlite3

from wendeburg_calendar.db.repository import Repository
from wendeburg_calendar.db.schema import CURRENT_SCHEMA_VERSION, _MIGRATIONS


def _create_populated_v1_database(path, *, partial_event_url: str | None = None) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_MIGRATIONS[0][1])
    if partial_event_url is not None:
        conn.execute("ALTER TABLE events ADD COLUMN event_url TEXT")

    timestamp = "2026-08-17T08:00:00+00:00"
    conn.execute(
        """
        INSERT INTO events (
            id, source_id, title, start_utc, end_utc, all_day, location,
            description, organizer, source_url, status, cancelled_reason,
            sequence, semantic_hash, extraction_method, extraction_confidence,
            missing_count, first_missing_at_utc, dtstamp_utc, last_modified_utc,
            created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, NULL, 0, NULL, NULL, NULL, ?, 'CONFIRMED',
                  NULL, 4, ?, 'ics', 1.0, 0, NULL, ?, ?, ?, ?)
        """,
        (
            "existing-event",
            "wendeburg",
            "Bestandstermin",
            "2026-09-01T16:00:00+00:00",
            "https://www.wendeburg.de/veranstaltungen/veranstaltung/bestand-1-26610.ical",
            "legacy-semantic-hash",
            timestamp,
            timestamp,
            timestamp,
            timestamp,
        ),
    )
    if partial_event_url is not None:
        conn.execute(
            "UPDATE events SET event_url = ? WHERE id = 'existing-event'",
            (partial_event_url,),
        )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


def test_v1_database_is_additively_migrated_and_backfilled(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    _create_populated_v1_database(db_path)

    repo = Repository.connect(db_path)
    record = repo.get_event("existing-event")
    version = repo.conn.execute("PRAGMA user_version").fetchone()[0]
    columns = {
        row["name"] for row in repo.conn.execute("PRAGMA table_info(events)").fetchall()
    }
    repo.close()

    assert version == CURRENT_SCHEMA_VERSION
    assert "event_url" in columns
    assert record is not None
    assert record.event_url == record.source_url
    assert record.sequence == 4
    assert record.semantic_hash == "legacy-semantic-hash"


def test_partially_applied_v2_migration_is_retry_safe(tmp_path):
    db_path = tmp_path / "partial.sqlite3"
    public_url = "https://example.test/events/bestand"
    _create_populated_v1_database(db_path, partial_event_url=public_url)

    repo = Repository.connect(db_path)
    first_record = repo.get_event("existing-event")
    repo.close()

    repo = Repository.connect(db_path)
    second_record = repo.get_event("existing-event")
    version = repo.conn.execute("PRAGMA user_version").fetchone()[0]
    repo.close()

    assert version == CURRENT_SCHEMA_VERSION
    assert first_record is not None
    assert second_record is not None
    assert first_record.event_url == public_url
    assert second_record.event_url == public_url
