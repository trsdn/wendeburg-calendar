"""HTML -> bounded plain text sanitization for the LLM fallback path.

Security rationale: web content is untrusted. We never hand raw HTML
(with tags, attributes, scripts, or links) to the LLM. Instead we extract
visible text only, drop anything that could carry executable content or
links (script/style/iframe/object/embed/svg and all attributes are
discarded by construction since only .get_text() output is kept), and
hard-truncate to a configured character budget.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

_STRIP_TAGS = ["script", "style", "noscript", "template", "iframe", "object", "embed", "svg"]


def sanitize_to_text(html: str, max_chars: int) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag_name in _STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    non_empty = [line for line in lines if line]
    collapsed = "\n".join(non_empty)

    if len(collapsed) > max_chars:
        collapsed = collapsed[:max_chars]
    return collapsed
