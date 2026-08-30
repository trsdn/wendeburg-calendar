"""Weak fingerprinting for last-resort event identity matching.

This is intentionally the *weakest* identity signal in the resolution
chain (see harvest.identity). It is only ever used when a source gives us
no stable UID / X-ID / canonical URL at all, and it is designed so a
later, stronger identifier can be attached to the same internal event
once it becomes available (see harvest.identity.resolve_or_create).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize_title(title: str) -> str:
    lowered = title.strip().lower()
    no_punct = _PUNCT_RE.sub("", lowered)
    return _WHITESPACE_RE.sub(" ", no_punct).strip()


def weak_fingerprint(title: str, start: datetime | date) -> str:
    """Compute a stable-but-weak fingerprint from normalized title + start date.

    Deliberately coarse (date-only, not time) so minor time corrections on
    the source website do not spuriously mint a new fingerprint identity.
    """
    day = start.date() if isinstance(start, datetime) else start
    payload = f"{_normalize_title(title)}|{day.isoformat()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
