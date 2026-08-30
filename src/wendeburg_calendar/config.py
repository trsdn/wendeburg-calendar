"""Configuration loading.

Configuration is TOML-only. All relative filesystem paths inside the
config file (database, output, ...) are resolved relative to the
directory containing the config file itself - NOT the process current
working directory - so the same config works no matter where the CLI is
invoked from.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    profile: str | None = None
    enabled: bool = True
    seed_urls: list[str] = Field(default_factory=list)
    allowed_hosts: list[str] = Field(default_factory=list)
    min_request_delay_seconds: float = Field(default=0.0, ge=0)

    @field_validator("seed_urls")
    @classmethod
    def _urls_are_http(cls, urls: list[str]) -> list[str]:
        for u in urls:
            if not (u.startswith("http://") or u.startswith("https://")):
                raise ValueError(f"seed_urls must be http(s) URLs, got: {u!r}")
        return urls

    @field_validator("allowed_hosts")
    @classmethod
    def _normalize_allowed_hosts(cls, hosts: list[str]) -> list[str]:
        normalized = [host.strip().lower() for host in hosts if host.strip()]
        if not normalized:
            raise ValueError("allowed_hosts must contain at least one hostname")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def _seed_hosts_are_allowed(self) -> "SourceConfig":
        allowed = set(self.allowed_hosts)
        if not allowed:
            raise ValueError("allowed_hosts must contain at least one hostname")
        for url in self.seed_urls:
            host = urlsplit(url).hostname
            if host is None or host.lower() not in allowed:
                raise ValueError(
                    f"Seed URL host {host!r} is not present in allowed_hosts"
                )
        return self


class HarvestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missing_threshold: int = 3
    missing_grace_days: int = 7
    max_events_per_source: int = Field(default=500, gt=0)
    request_timeout_seconds: float = 15.0
    max_content_bytes: int = 5_000_000


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    default_model: str = "gpt-5.6-luna"
    max_input_chars: int = 6000


class GeneralConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = "wendeburg-calendar.local"
    database: str = "data/wendeburg.sqlite3"
    output: str = "data/calendar.ics"
    user_agent: str = "WendeburgCalendarBot/0.1 (+contact unset)"
    timezone: str = "Europe/Berlin"


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    harvest: HarvestConfig = Field(default_factory=HarvestConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    sources: list[SourceConfig] = Field(default_factory=list)

    # Populated after load(); not part of the TOML schema itself.
    config_dir: Path = Field(default=Path("."), exclude=True)

    @property
    def database_path(self) -> Path:
        return self._resolve(self.general.database)

    @property
    def output_path(self) -> Path:
        return self._resolve(self.general.output)

    def _resolve(self, value: str) -> Path:
        p = Path(value).expanduser()
        if p.is_absolute():
            return p
        return (self.config_dir / p).resolve()


def load_config(path: str | Path) -> AppConfig:
    """Load and validate a TOML config file."""
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("rb") as f:
        raw = tomllib.load(f)
    cfg = AppConfig.model_validate(raw)
    cfg.config_dir = config_path.parent
    return cfg
