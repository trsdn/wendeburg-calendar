"""Explicit source adapter registry.

Adapters register themselves via `@register("type-name")`. The harvest
pipeline never imports a concrete adapter directly - it only ever looks
one up by the `type` string configured in `config.toml`, keeping source
selection entirely data-driven.
"""

from __future__ import annotations

from wendeburg_calendar.config import SourceConfig
from wendeburg_calendar.sources.base import AdapterContext, SourceAdapter

_REGISTRY: dict[str, type[SourceAdapter]] = {}


def register(type_name: str):
    def decorator(cls: type[SourceAdapter]) -> type[SourceAdapter]:
        if type_name in _REGISTRY and _REGISTRY[type_name] is not cls:
            raise ValueError(f"Source type {type_name!r} is already registered")
        _REGISTRY[type_name] = cls
        return cls

    return decorator


def create(source_config: SourceConfig, context: AdapterContext) -> SourceAdapter:
    try:
        cls = _REGISTRY[source_config.type]
    except KeyError as exc:
        raise ValueError(
            f"Unknown source type {source_config.type!r}. Registered types: {sorted(_REGISTRY)}"
        ) from exc
    return cls(source_config, context)


def known_types() -> list[str]:
    return sorted(_REGISTRY)
