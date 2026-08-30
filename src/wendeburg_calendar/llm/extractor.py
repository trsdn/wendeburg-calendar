"""LLM fallback extraction: HTML -> sanitized text -> validated NormalizedEvent.

Only reached when deterministic ICS parsing is unavailable for a detail
page. Every LLM result, cached or fresh, passes through strict local
Pydantic validation (`LlmEventExtraction`, extra="forbid") before being
converted into the same `NormalizedEvent` shape ICS parsing produces.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol

from pydantic import ValidationError

from wendeburg_calendar.llm.client import LlmClient
from wendeburg_calendar.llm.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_message
from wendeburg_calendar.llm.schemas import JSON_SCHEMA_PAYLOAD, SCHEMA_VERSION, LlmEventExtraction
from wendeburg_calendar.model.event import ExtractionMethod, NormalizedEvent
from wendeburg_calendar.parsing.html_sanitize import sanitize_to_text
from wendeburg_calendar.util.hashing import cache_key, sha256_hex
from wendeburg_calendar.util.time import BERLIN


class LlmResultCache(Protocol):
    def get_llm_cache(self, key: str) -> dict | None: ...
    def set_llm_cache(self, key: str, result: dict) -> None: ...


# LLM-extracted events are inherently less certain than deterministic ICS
# parsing; this fixed confidence value reflects that in the persisted record.
_LLM_CONFIDENCE = 0.6


def _parse_iso_local(value: str, tz) -> datetime:
    dt = datetime.fromisoformat(value.strip())
    return dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)


def extract_via_llm(
    html: str,
    *,
    source_id: str,
    source_url: str,
    llm_client: LlmClient,
    max_input_chars: int,
    cache: LlmResultCache | None = None,
    tz=BERLIN,
) -> NormalizedEvent | None:
    """Returns a NormalizedEvent, or None if no reliable event could be extracted."""
    sanitized = sanitize_to_text(html, max_input_chars)
    if not sanitized.strip():
        return None

    raw_hash = sha256_hex(html.encode("utf-8", errors="ignore"))
    key = cache_key(sanitized, llm_client.model, SCHEMA_VERSION, PROMPT_VERSION)

    extraction: LlmEventExtraction | None = None
    cached_data = cache.get_llm_cache(key) if cache is not None else None
    if cached_data is not None:
        try:
            extraction = LlmEventExtraction.model_validate(cached_data)
        except ValidationError:
            extraction = None  # stale/invalid cache entry; fall through to a fresh call

    if extraction is None:
        try:
            raw_text = llm_client.complete_json(
                SYSTEM_PROMPT, build_user_message(sanitized), JSON_SCHEMA_PAYLOAD
            )
        except Exception:
            # A genuine backend failure (auth, network, quota, ...) here must
            # not crash the whole harvest run - this is a best-effort
            # fallback path; the item is simply treated as extraction-failed.
            return None
        if not raw_text:
            return None
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            return None
        try:
            extraction = LlmEventExtraction.model_validate(data)
        except ValidationError:
            return None
        if cache is not None:
            cache.set_llm_cache(key, extraction.model_dump())

    if not extraction.found or not extraction.title or not extraction.start:
        return None

    try:
        start = _parse_iso_local(extraction.start, tz)
        end = _parse_iso_local(extraction.end, tz) if extraction.end else None
    except ValueError:
        return None

    try:
        return NormalizedEvent(
            title=extraction.title,
            start=start,
            end=end,
            all_day=extraction.all_day,
            location=extraction.location,
            description=extraction.description,
            organizer=extraction.organizer,
            source_id=source_id,
            source_url=source_url,
            event_url=source_url,
            source_event_uid=None,
            source_x_id=None,
            extraction_method=ExtractionMethod.LLM,
            extraction_confidence=_LLM_CONFIDENCE,
            raw_content_hash=raw_hash,
        )
    except ValidationError:
        return None
