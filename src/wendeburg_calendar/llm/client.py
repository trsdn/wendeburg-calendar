"""Capability-tolerant OpenAI-compatible Chat Completions client.

Fallback ladder (per project spec):
  1. JSON Schema / Structured Outputs (response_format={"type": "json_schema", ...})
  2. JSON mode (response_format={"type": "json_object"})
  3. Plain chat (no response_format), only attempted once the format
     capability has been *explicitly* rejected by the backend - never as
     a first choice.

At every tier, and regardless of which tier ultimately produced the text,
the caller (llm.extractor) re-validates the parsed JSON locally against
`LlmEventExtraction` (extra="forbid"). No tools/tool-calls are ever
attached to any request.

Credentials and endpoint are read ONLY from the environment
(OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL) - never from files, never
hardcoded, never logged.
"""

from __future__ import annotations

import os
from typing import Protocol

DEFAULT_MODEL = "gpt-5.6-luna"

# Substrings observed in OpenAI-compatible 400 error messages when a given
# response_format capability tier is not supported by the backend. Matching
# is intentionally conservative: we only treat an error as a "capability
# rejection" (safe to fall back) when it clearly references the format
# machinery itself, never for unrelated errors (auth, quota, network...).
_FORMAT_REJECTION_MARKERS = (
    "response_format",
    "json_schema",
    "json_object",
    "structured output",
    "structured outputs",
)
_REJECTION_VERBS = (
    "not support",
    "unsupported",
    "unknown parameter",
    "unrecognized",
    "invalid value",
    "does not support",
    "not a valid",
)


class FormatUnsupportedError(Exception):
    """Raised by a ChatBackend when a response_format tier is rejected."""


class ChatBackend(Protocol):
    def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        response_format: dict | None,
    ) -> str:
        """Return the raw assistant message text content for one turn."""
        ...


def classify_format_rejection(exc: Exception) -> bool:
    """True if `exc` looks like an explicit rejection of the response_format
    capability (safe to try the next, less capable tier), False if it is
    some other error (auth/network/quota/...) that must not be swallowed."""
    message = str(exc).lower()
    has_marker = any(marker in message for marker in _FORMAT_REJECTION_MARKERS)
    has_verb = any(verb in message for verb in _REJECTION_VERBS)
    status_code = getattr(exc, "status_code", None)
    return has_marker and has_verb and (status_code in (None, 400))


class OpenAiChatBackend:
    """Real backend talking to an OpenAI-compatible Chat Completions API."""

    def __init__(self, base_url: str | None, api_key: str):
        from openai import OpenAI  # imported lazily; only needed when LLM is used

        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)

    def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        response_format: dict | None,
    ) -> str:
        kwargs: dict = {"model": model, "messages": messages}
        if response_format is not None:
            kwargs["response_format"] = response_format
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - reclassified below
            if response_format is not None and classify_format_rejection(exc):
                raise FormatUnsupportedError(str(exc)) from exc
            raise
        content = response.choices[0].message.content
        return content or ""


class LlmClient:
    """Owns the capability-fallback ladder. Never touches tools/tool_calls."""

    def __init__(self, backend: ChatBackend, model: str):
        self._backend = backend
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def complete_json(self, system_prompt: str, user_content: str, json_schema_payload: dict) -> str | None:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            return self._backend.complete(
                model=self._model,
                messages=messages,
                response_format={"type": "json_schema", "json_schema": json_schema_payload},
            )
        except FormatUnsupportedError:
            pass

        try:
            return self._backend.complete(
                model=self._model,
                messages=messages,
                response_format={"type": "json_object"},
            )
        except FormatUnsupportedError:
            pass

        try:
            return self._backend.complete(model=self._model, messages=messages, response_format=None)
        except Exception:  # noqa: BLE001 - a genuine failure at the last tier; caller treats as no extraction
            return None


def create_llm_client_from_env(default_model: str = DEFAULT_MODEL) -> LlmClient | None:
    """Build an LlmClient from OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL.

    Returns None if no API key is configured - callers must treat this as
    "LLM fallback unavailable" (skip the item) rather than raising, so a
    missing/optional LLM configuration cannot crash a harvest run.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = os.environ.get("OPENAI_BASE_URL") or None
    model = os.environ.get("OPENAI_MODEL") or default_model
    backend = OpenAiChatBackend(base_url=base_url, api_key=api_key)
    return LlmClient(backend, model)
