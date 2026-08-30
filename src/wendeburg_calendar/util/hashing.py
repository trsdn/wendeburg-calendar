"""Generic content hashing helpers used for HTTP caching and LLM caching."""

from __future__ import annotations

import hashlib


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def cache_key(*parts: str) -> str:
    """Build a stable cache key from an ordered list of string parts."""
    joined = "\x1f".join(parts)  # unit separator avoids ambiguous concatenation
    return sha256_hex(joined)
