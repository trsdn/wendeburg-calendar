"""SQLite repository. Thin, explicit, parameterized SQL - no ORM.

All timestamps are stored as UTC ISO-8601 strings (see util.time). All
write methods use parameterized queries exclusively; nothing here ever
interpolates values into SQL text.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from wendeburg_calendar.db.models import EventRecord
from wendeburg_calendar.db.schema import migrate
from wendeburg_calendar.util.time import iso_utc, now_utc


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        migrate(self.conn)

    @classmethod
    def connect(cls, path: str | Path) -> "Repository":
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Repository":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- identity / aliases --------------------------------------------------

    def find_event_id_by_alias(
        self, alias_type: str, alias_value: str, source_id: str
    ) -> str | None:
        row = self.conn.execute(
            "SELECT event_id FROM event_aliases WHERE alias_type = ? AND alias_value = ? AND source_id = ?",
            (alias_type, alias_value, source_id),
        ).fetchone()
        return row["event_id"] if row else None

    def get_alias_value_for_event(
        self, event_id: str, alias_type: str, source_id: str
    ) -> str | None:
        row = self.conn.execute(
            "SELECT alias_value FROM event_aliases WHERE event_id = ? AND alias_type = ? AND source_id = ?",
            (event_id, alias_type, source_id),
        ).fetchone()
        return row["alias_value"] if row else None

    def list_aliases_for_event(
        self, event_id: str, source_id: str
    ) -> dict[str, str]:
        rows = self.conn.execute(
            """
            SELECT alias_type, alias_value
            FROM event_aliases
            WHERE event_id = ? AND source_id = ?
            """,
            (event_id, source_id),
        ).fetchall()
        return {row["alias_type"]: row["alias_value"] for row in rows}

    def upsert_alias(
        self, alias_type: str, alias_value: str, source_id: str, event_id: str
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO event_aliases (alias_type, alias_value, source_id, event_id, created_at_utc)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (alias_type, alias_value, source_id)
            DO UPDATE SET event_id = excluded.event_id
            """,
            (alias_type, alias_value, source_id, event_id, iso_utc(now_utc())),
        )

    # -- events ----------------------------------------------------------------

    def get_event(self, event_id: str) -> EventRecord | None:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return EventRecord.from_row(row) if row else None

    def create_event(
        self,
        event_id: str,
        *,
        source_id: str,
        title: str,
        start_utc,
        end_utc,
        all_day: bool,
        location: str | None,
        description: str | None,
        organizer: str | None,
        source_url: str | None,
        event_url: str | None,
        status: str,
        sequence: int,
        semantic_hash: str,
        extraction_method: str,
        extraction_confidence: float,
        cancelled_reason: str | None = None,
    ) -> None:
        now = iso_utc(now_utc())
        self.conn.execute(
            """
            INSERT INTO events (
                id, source_id, title, start_utc, end_utc, all_day, location,
                description, organizer, source_url, event_url, status,
                cancelled_reason, sequence, semantic_hash, extraction_method,
                extraction_confidence, missing_count, first_missing_at_utc,
                dtstamp_utc, last_modified_utc, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?)
            """,
            (
                event_id,
                source_id,
                title,
                iso_utc(start_utc),
                iso_utc(end_utc) if end_utc else None,
                int(all_day),
                location,
                description,
                organizer,
                source_url,
                event_url,
                status,
                cancelled_reason,
                sequence,
                semantic_hash,
                extraction_method,
                extraction_confidence,
                now,
                now,
                now,
                now,
            ),
        )

    def update_event_semantic(
        self,
        event_id: str,
        *,
        title: str,
        start_utc,
        end_utc,
        all_day: bool,
        location: str | None,
        description: str | None,
        organizer: str | None,
        source_url: str | None,
        event_url: str | None,
        status: str,
        sequence: int,
        semantic_hash: str,
        extraction_method: str,
        extraction_confidence: float,
        cancelled_reason: str | None,
    ) -> None:
        now = iso_utc(now_utc())
        self.conn.execute(
            """
            UPDATE events SET
                title = ?, start_utc = ?, end_utc = ?, all_day = ?, location = ?,
                description = ?, organizer = ?, source_url = ?, event_url = ?,
                status = ?, cancelled_reason = ?, sequence = ?, semantic_hash = ?,
                extraction_method = ?, extraction_confidence = ?, dtstamp_utc = ?,
                last_modified_utc = ?, updated_at_utc = ?
            WHERE id = ?
            """,
            (
                title,
                iso_utc(start_utc),
                iso_utc(end_utc) if end_utc else None,
                int(all_day),
                location,
                description,
                organizer,
                source_url,
                event_url,
                status,
                cancelled_reason,
                sequence,
                semantic_hash,
                extraction_method,
                extraction_confidence,
                now,
                now,
                now,
                event_id,
            ),
        )

    def touch_event_seen(
        self,
        event_id: str,
        *,
        source_url: str | None,
        event_url: str | None,
    ) -> None:
        """Refresh observed URLs/bookkeeping without bumping SEQUENCE."""
        now = iso_utc(now_utc())
        self.conn.execute(
            """
            UPDATE events SET source_url = ?, event_url = ?,
                dtstamp_utc = ?, updated_at_utc = ?
            WHERE id = ?
            """,
            (source_url, event_url, now, now, event_id),
        )

    def reset_missing_state(self, event_id: str) -> None:
        now = iso_utc(now_utc())
        self.conn.execute(
            "UPDATE events SET missing_count = 0, first_missing_at_utc = NULL, updated_at_utc = ? WHERE id = ?",
            (now, event_id),
        )

    def increment_missing(self, event_id: str, first_missing_at_iso: str) -> None:
        now = iso_utc(now_utc())
        self.conn.execute(
            """
            UPDATE events SET
                missing_count = missing_count + 1,
                first_missing_at_utc = COALESCE(first_missing_at_utc, ?),
                updated_at_utc = ?
            WHERE id = ?
            """,
            (first_missing_at_iso, now, event_id),
        )

    def cancel_event(self, event_id: str, reason: str, sequence: int) -> None:
        now = iso_utc(now_utc())
        self.conn.execute(
            """
            UPDATE events SET
                status = 'CANCELLED', cancelled_reason = ?, sequence = ?,
                dtstamp_utc = ?, last_modified_utc = ?, updated_at_utc = ?
            WHERE id = ?
            """,
            (reason, sequence, now, now, now, event_id),
        )

    def list_events_for_source(self, source_id: str) -> list[EventRecord]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE source_id = ?", (source_id,)
        ).fetchall()
        return [EventRecord.from_row(r) for r in rows]

    def list_all_events(self) -> list[EventRecord]:
        rows = self.conn.execute(
            "SELECT * FROM events ORDER BY start_utc ASC, title ASC, id ASC"
        ).fetchall()
        return [EventRecord.from_row(r) for r in rows]

    # -- HTTP conditional-GET cache (ConditionalCache protocol) -----------------

    def get_cache_entry(self, url: str) -> tuple[str | None, str | None] | None:
        row = self.conn.execute(
            "SELECT etag, last_modified FROM resource_cache WHERE url = ?", (url,)
        ).fetchone()
        if row is None:
            return None
        return row["etag"], row["last_modified"]

    def get_cached_content_hash(self, url: str) -> str | None:
        row = self.conn.execute(
            "SELECT content_hash FROM resource_cache WHERE url = ?", (url,)
        ).fetchone()
        return row["content_hash"] if row else None

    def set_cache_entry(
        self, url: str, etag: str | None, last_modified: str | None, content_hash: str
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO resource_cache (url, etag, last_modified, content_hash, fetched_at_utc)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                content_hash = excluded.content_hash,
                fetched_at_utc = excluded.fetched_at_utc
            """,
            (url, etag, last_modified, content_hash, iso_utc(now_utc())),
        )

    # -- LLM result cache --------------------------------------------------------

    def get_llm_cache(self, cache_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT result_json FROM llm_cache WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def set_llm_cache(self, cache_key: str, result: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO llm_cache (cache_key, result_json, created_at_utc)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                result_json = excluded.result_json, created_at_utc = excluded.created_at_utc
            """,
            (cache_key, json.dumps(result), iso_utc(now_utc())),
        )

    # -- harvest run bookkeeping -------------------------------------------------

    def record_harvest_run(
        self, source_id: str, coverage: str, events_seen: int, notes: str, started_at_iso: str
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO harvest_runs (source_id, started_at_utc, finished_at_utc, coverage, events_seen, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source_id, started_at_iso, iso_utc(now_utc()), coverage, events_seen, notes),
        )
