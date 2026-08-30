"""Strict robots.txt enforcement.

Design goals (see project spec):

- Centralized: every fetch (except the robots.txt fetch itself) is checked.
- Fail closed: any robots.txt fetch outcome other than a confirmed 200,
  404, or 410 results in a deny-all decision for that host until a
  successful fetch happens (in-memory, per process/run - a fresh process
  will retry).
- 404/410 both mean "no robots.txt restrictions apply" per common
  convention (404 = never existed, 410 = confirmed gone).
- Any other error (5xx, timeouts, malformed responses, oversized bodies)
  is treated as "unknown", which must NOT be interpreted as either
  allowed or as evidence the site is empty - it fails closed to deny.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from urllib.parse import unquote, urljoin, urlsplit

from wendeburg_calendar.http.errors import (
    FetchTooLargeError,
    FetchPolicyError,
    HostNotAllowedError,
    InvalidSchemeError,
    RobotsDisallowedError,
    diagnostic_for_exception,
    failure_diagnostic,
    http_status_diagnostic,
)
from wendeburg_calendar.http.fetcher import Fetcher
from wendeburg_calendar.http.retry import RetryExecutor
from wendeburg_calendar.http.throttle import HostRateLimiter

_MAX_ROBOTS_BYTES = 512_000
_MAX_ROBOTS_REDIRECTS = 3


@dataclass
class _RuleGroup:
    disallow: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    crawl_delay: float | None = None


@dataclass
class RobotsRules:
    """Resolved allow/disallow rule set for one host, already narrowed to
    the group applicable to our own user-agent."""

    deny_all: bool = False
    allow_all: bool = False
    disallow: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    crawl_delay: float | None = None
    failure_diagnostic: str = ""

    def is_allowed(self, path: str) -> bool:
        if self.deny_all:
            return False
        if self.allow_all and not self.disallow and not self.allow:
            return True

        matches: list[tuple[int, bool]] = []
        for pattern in self.disallow:
            if _rule_matches(pattern, path):
                matches.append((_rule_specificity(pattern), False))
        for pattern in self.allow:
            if _rule_matches(pattern, path):
                matches.append((_rule_specificity(pattern), True))
        if not matches:
            return True
        longest = max(length for length, _ in matches)
        # Allow wins ties, matching the robots exclusion protocol.
        return any(allowed for length, allowed in matches if length == longest)


def _rule_specificity(pattern: str) -> int:
    return len(pattern.replace("*", "").removesuffix("$"))


def _rule_matches(pattern: str, path: str) -> bool:
    if not pattern:
        return False
    anchored_end = pattern.endswith("$")
    core = pattern[:-1] if anchored_end else pattern
    regex = "^" + re.escape(core).replace(r"\*", ".*")
    if anchored_end:
        regex += "$"
    return re.search(regex, path) is not None


def _product_token(user_agent: str) -> str:
    """The first whitespace-delimited token, e.g. 'WendeburgCalendarBot/0.1' -> 'WendeburgCalendarBot'."""
    first = user_agent.split()[0] if user_agent.split() else user_agent
    return first.split("/")[0].strip().lower()


def parse_robots_txt(text: str, user_agent: str) -> RobotsRules:
    """Parse robots.txt content and return the rule set applicable to `user_agent`."""
    groups: list[tuple[list[str], _RuleGroup]] = []
    current_agents: list[str] = []
    current_rules = _RuleGroup()
    started_rules = False

    def flush() -> None:
        nonlocal current_agents, current_rules, started_rules
        if current_agents:
            groups.append((current_agents, current_rules))
        current_agents = []
        current_rules = _RuleGroup()
        started_rules = False

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        if key == "user-agent":
            if started_rules:
                # A new "User-agent:" after directives started a new group.
                flush()
            current_agents.append(value.lower())
        elif key == "disallow":
            started_rules = True
            if value:
                current_rules.disallow.append(value)
            else:
                # Empty Disallow means "allow everything" for this group.
                current_rules.allow.append("")
        elif key == "allow":
            started_rules = True
            if value:
                current_rules.allow.append(value)
        elif key == "crawl-delay":
            started_rules = True
            try:
                delay = float(value)
            except ValueError:
                continue
            if delay >= 0:
                current_rules.crawl_delay = delay
        # Other directives (Sitemap, Host, ...) do not affect access checks.
    flush()

    our_token = _product_token(user_agent)

    matching: list[tuple[int, _RuleGroup]] = []
    for agents, rules in groups:
        for agent in agents:
            if agent == "*":
                matching.append((0, rules))
            elif our_token.startswith(agent):
                matching.append((len(agent), rules))
                break

    if not matching:
        return RobotsRules(allow_all=True)

    best_specificity = max(specificity for specificity, _ in matching)
    chosen_groups = [
        rules
        for specificity, rules in matching
        if specificity == best_specificity
    ]
    disallow = [
        pattern
        for rules in chosen_groups
        for pattern in rules.disallow
    ]
    allow = [
        pattern
        for rules in chosen_groups
        for pattern in rules.allow
    ]
    delays = [
        rules.crawl_delay
        for rules in chosen_groups
        if rules.crawl_delay is not None
    ]
    deny_all = any(d == "/" for d in disallow) and not allow
    return RobotsRules(
        deny_all=deny_all,
        allow_all=not disallow,
        disallow=disallow,
        allow=allow,
        crawl_delay=max(delays) if delays else None,
    )


class RobotsChecker:
    """Per-run robots.txt cache and enforcement point."""

    def __init__(
        self,
        fetcher: Fetcher,
        user_agent: str,
        rate_limiter: HostRateLimiter | None = None,
        allowed_hosts: set[str] | None = None,
        min_request_delay_seconds: float = 0.0,
        retry_executor: RetryExecutor | None = None,
    ):
        self._fetcher = fetcher
        self._user_agent = user_agent
        self._rate_limiter = rate_limiter or HostRateLimiter()
        self._min_request_delay_seconds = max(0.0, min_request_delay_seconds)
        if retry_executor is not None:
            self._retry = retry_executor
        elif getattr(fetcher, "is_offline", False):
            self._retry = RetryExecutor(sleeper=lambda _seconds: None)
        else:
            self._retry = RetryExecutor()
        self._allowed_hosts = (
            {host.lower() for host in allowed_hosts}
            if allowed_hosts is not None
            else None
        )
        self._cache: dict[str, RobotsRules] = {}

    def _validate_robots_hop(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise InvalidSchemeError(f"Refusing robots.txt redirect scheme in {url!r}")
        if (
            self._allowed_hosts is not None
            and (
                parts.hostname is None
                or parts.hostname.lower() not in self._allowed_hosts
            )
        ):
            raise HostNotAllowedError(
                f"Robots redirect host {parts.hostname!r} is not allowed"
            )

    def _rules_for_host(self, scheme: str, netloc: str) -> RobotsRules:
        key = f"{scheme}://{netloc}"
        if key in self._cache:
            return self._cache[key]

        rules = self._fetch_rules(scheme, netloc)
        self._cache[key] = rules
        return rules

    def _fetch_rules(self, scheme: str, netloc: str) -> RobotsRules:
        url = f"{scheme}://{netloc}/robots.txt"
        try:
            for _ in range(_MAX_ROBOTS_REDIRECTS + 1):
                self._validate_robots_hop(url)
                parts = urlsplit(url)
                host_key = (parts.hostname or parts.netloc).lower()
                outcome = self._retry.get_single(
                    self._fetcher,
                    url,
                    before_attempt=lambda: self._rate_limiter.wait(
                        host_key,
                        self._min_request_delay_seconds,
                    ),
                )
                resp = outcome.response
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        return RobotsRules(
                            deny_all=True,
                            failure_diagnostic=http_status_diagnostic(
                                url,
                                resp.status_code,
                                outcome.attempts,
                            ),
                        )
                    url = urljoin(url, location)
                    continue
                if resp.status_code == 200:
                    if len(resp.content) > _MAX_ROBOTS_BYTES:
                        # Fail closed: cannot safely trust a truncated policy.
                        return RobotsRules(
                            deny_all=True,
                            failure_diagnostic=failure_diagnostic(
                                url,
                                category="Robots response too large",
                                exception=FetchTooLargeError(),
                                attempts=outcome.attempts,
                            ),
                        )
                    text = resp.content.decode("utf-8", errors="replace")
                    return parse_robots_txt(text, self._user_agent)
                if resp.status_code in (404, 410):
                    # Confirmed absence of a robots policy -> no restrictions.
                    return RobotsRules(allow_all=True)
                # Any other status (403, 5xx, ...) -> fail closed.
                return RobotsRules(
                    deny_all=True,
                    failure_diagnostic=http_status_diagnostic(
                        url,
                        resp.status_code,
                        outcome.attempts,
                    ),
                )
            # Redirect loop exhausted -> fail closed.
            return RobotsRules(
                deny_all=True,
                failure_diagnostic=failure_diagnostic(
                    url,
                    category="Robots redirect limit exceeded",
                    attempts=1,
                ),
            )
        except FetchPolicyError as exc:
            # Transport/size policy failure fetching robots.txt -> fail closed.
            return RobotsRules(
                deny_all=True,
                failure_diagnostic=diagnostic_for_exception(
                    url,
                    exc,
                    category="Robots fetch failure",
                ),
            )

    def check(self, url: str) -> RobotsRules:
        """Raise RobotsDisallowedError if `url` is not allowed; return its rules."""
        parts = urlsplit(url)
        rules = self._rules_for_host(parts.scheme, parts.netloc)
        path = unquote(parts.path or "/")
        if parts.query:
            path = f"{path}?{unquote(parts.query)}"
        if not rules.is_allowed(path):
            raise RobotsDisallowedError(url, rules.failure_diagnostic or None)
        return rules
