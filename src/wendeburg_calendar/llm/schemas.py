"""Strict local validation schema for LLM extraction output.

Regardless of which response-format capability tier the backend actually
used (structured output / JSON mode / plain chat), the raw JSON text is
*always* re-validated locally against this Pydantic model with
`extra="forbid"` before it is trusted. This is the final backstop against
a misbehaving or manipulated model response.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

# Bump whenever the wire schema shape changes, so cached LLM results keyed
# on it are automatically invalidated.
SCHEMA_VERSION = "1"

# JSON Schema payload used for the "Structured Outputs" capability tier.
# Deliberately hand-written (not auto-derived from the pydantic model)
# so it can satisfy OpenAI's strict-mode requirements (every property
# listed in "required", additionalProperties: false) independently of
# how the pydantic model chooses to expose optionality.
JSON_SCHEMA_PAYLOAD: dict = {
    "name": "wendeburg_event_extraction",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "found": {
                "type": "boolean",
                "description": "True only if the text describes one concrete, dated public event.",
            },
            "title": {"type": ["string", "null"]},
            "start": {
                "type": ["string", "null"],
                "description": "ISO-8601 date or date-time, e.g. 2026-08-17 or 2026-08-17T19:00:00.",
            },
            "end": {"type": ["string", "null"]},
            "all_day": {"type": "boolean"},
            "location": {"type": ["string", "null"]},
            "description": {"type": ["string", "null"]},
            "organizer": {"type": ["string", "null"]},
        },
        "required": [
            "found",
            "title",
            "start",
            "end",
            "all_day",
            "location",
            "description",
            "organizer",
        ],
    },
    "strict": True,
}


class LlmEventExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool
    title: str | None = None
    start: str | None = None
    end: str | None = None
    all_day: bool = False
    location: str | None = None
    description: str | None = None
    organizer: str | None = None

    @model_validator(mode="after")
    def _required_when_found(self) -> "LlmEventExtraction":
        if self.found:
            if not self.title or not self.title.strip():
                raise ValueError("title is required when found=true")
            if not self.start or not self.start.strip():
                raise ValueError("start is required when found=true")
        return self
