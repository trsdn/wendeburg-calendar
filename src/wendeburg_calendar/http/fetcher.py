"""Low-level, single-hop HTTP transport abstraction.

Two implementations are provided:

- HttpxFetcher: real network access via httpx, redirects disabled so the
  policy layer (HarvestClient) can revalidate every hop.
- FixtureFetcher: reads canned responses from a small on-disk manifest, for
  fully offline tests and the CLI's `--offline-fixture` mode.

Nothing above this layer should ever talk to `httpx` (or the filesystem, for
fixtures) directly - that is the whole point of centralizing HTTP access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from wendeburg_calendar.http.errors import FetchTooLargeError, FetchTransportError


@dataclass(frozen=True)
class RawResponse:
    status_code: int
    url: str
    headers: dict[str, str]
    content: bytes


class Fetcher(Protocol):
    def get_single(
        self, url: str, extra_headers: dict[str, str] | None = None
    ) -> RawResponse:
        """Perform exactly one HTTP hop. Must NOT follow redirects itself."""
        ...


class HttpxFetcher:
    """Real-network fetcher backed by httpx."""

    def __init__(self, user_agent: str, timeout_seconds: float, max_content_bytes: int):
        import httpx  # imported lazily so fixture-only tests never need it importable

        self._httpx = httpx
        self._client = httpx.Client(
            follow_redirects=False,
            timeout=timeout_seconds,
            headers={"User-Agent": user_agent},
        )
        self._max_content_bytes = max_content_bytes

    def get_single(
        self, url: str, extra_headers: dict[str, str] | None = None
    ) -> RawResponse:
        try:
            with self._client.stream("GET", url, headers=extra_headers or {}) as resp:
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > self._max_content_bytes:
                        raise FetchTooLargeError(
                            f"Response for {url} exceeded {self._max_content_bytes} bytes"
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
                return RawResponse(
                    status_code=resp.status_code,
                    url=str(resp.url),
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    content=content,
                )
        except self._httpx.HTTPError as exc:
            raise FetchTransportError(
                url=url,
                exception_class=type(exc).__name__,
            ) from exc

    def close(self) -> None:
        self._client.close()


@dataclass
class FixtureEntry:
    file: str | None = None
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    redirect_to: str | None = None


class FixtureFetcher:
    """Offline fetcher that serves canned bytes from a manifest.json.

    Manifest format (manifest.json inside `base_dir`):

        {
          "https://example.invalid/robots.txt": {"file": "robots.txt", "status": 200},
          "https://example.invalid/a": {"redirect_to": "https://example.invalid/b", "status": 301},
          "https://example.invalid/b": {"file": "b.html", "status": 200}
        }

    Any URL not present in the manifest is served as a 404 with empty body,
    which mirrors real-world behavior closely enough for offline testing
    (and correctly exercises 404-based robots.txt "no restrictions" logic).
    """

    is_offline = True

    def __init__(self, base_dir: str | Path):
        self._base_dir = Path(base_dir)
        manifest_path = self._base_dir / "manifest.json"
        if manifest_path.is_file():
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            raw = {}
        self._manifest: dict[str, FixtureEntry] = {
            url: FixtureEntry(**entry) for url, entry in raw.items()
        }

    def get_single(
        self, url: str, extra_headers: dict[str, str] | None = None
    ) -> RawResponse:
        entry = self._manifest.get(url)
        if entry is None:
            return RawResponse(status_code=404, url=url, headers={}, content=b"")

        headers = dict(entry.headers)
        if entry.redirect_to:
            headers.setdefault("location", entry.redirect_to)
            return RawResponse(
                status_code=entry.status, url=url, headers=headers, content=b""
            )

        content = b""
        if entry.file:
            file_path = self._base_dir / entry.file
            content = file_path.read_bytes()

        # Rudimentary conditional-GET support for fixtures/tests.
        inm = (extra_headers or {}).get("If-None-Match")
        if inm and headers.get("etag") == inm:
            return RawResponse(status_code=304, url=url, headers=headers, content=b"")

        return RawResponse(
            status_code=entry.status, url=url, headers=headers, content=content
        )
