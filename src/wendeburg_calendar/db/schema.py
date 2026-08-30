"""Versioned SQLite schema, applied via PRAGMA user_version-driven migrations.

Plain `sqlite3` is used throughout the project - no ORM. Every migration is
retry-safe so running init on an already-initialized or partially migrated
database is always safe.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """Separate the exported event URL from the identity/provenance URL.

    The column check makes this safe after an interrupted or manually
    partially applied migration. Backfilling preserves the URL currently
    exported by version-1 databases without changing hashes or SEQUENCE.
    """
    if not _has_column(conn, "events", "event_url"):
        conn.execute("ALTER TABLE events ADD COLUMN event_url TEXT")
    conn.execute("UPDATE events SET event_url = source_url WHERE event_url IS NULL")


Migration = str | Callable[[sqlite3.Connection], None]

# Ordered list of (target_version, migration) steps. Add new entries at the
# end; never edit an already-shipped migration.
_MIGRATIONS: list[tuple[int, Migration]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            title TEXT NOT NULL,
            start_utc TEXT NOT NULL,
            end_utc TEXT,
            all_day INTEGER NOT NULL DEFAULT 0,
            location TEXT,
            description TEXT,
            organizer TEXT,
            source_url TEXT,
            status TEXT NOT NULL DEFAULT 'CONFIRMED',
            cancelled_reason TEXT,
            sequence INTEGER NOT NULL DEFAULT 0,
            semantic_hash TEXT NOT NULL,
            extraction_method TEXT NOT NULL,
            extraction_confidence REAL NOT NULL DEFAULT 1.0,
            missing_count INTEGER NOT NULL DEFAULT 0,
            first_missing_at_utc TEXT,
            dtstamp_utc TEXT NOT NULL,
            last_modified_utc TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_events_source ON events(source_id);
        CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_utc);

        CREATE TABLE IF NOT EXISTS event_aliases (
            alias_type TEXT NOT NULL,
            alias_value TEXT NOT NULL,
            source_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            PRIMARY KEY (alias_type, alias_value, source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_event_aliases_event ON event_aliases(event_id);

        CREATE TABLE IF NOT EXISTS resource_cache (
            url TEXT PRIMARY KEY,
            etag TEXT,
            last_modified TEXT,
            content_hash TEXT,
            fetched_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS llm_cache (
            cache_key TEXT PRIMARY KEY,
            result_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS harvest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            started_at_utc TEXT NOT NULL,
            finished_at_utc TEXT,
            coverage TEXT NOT NULL,
            events_seen INTEGER NOT NULL DEFAULT 0,
            notes TEXT
        );
        """,
    ),
    (2, _migrate_to_v2),
]

CURRENT_SCHEMA_VERSION = _MIGRATIONS[-1][0]


def migrate(conn: sqlite3.Connection) -> None:
    """Bring the database schema up to CURRENT_SCHEMA_VERSION."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for target_version, migration in _MIGRATIONS:
        if target_version <= current:
            continue
        with conn:
            if isinstance(migration, str):
                conn.executescript(migration)
            else:
                migration(conn)
            conn.execute(f"PRAGMA user_version = {target_version}")
        current = target_version
