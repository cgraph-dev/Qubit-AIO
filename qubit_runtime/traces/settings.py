"""Opt-in trace settings.

Traces are **off by default**. Enabling requires ``traces.enabled: true`` in
``config.yaml`` or ``QUBIT_TRACES_ENABLED=1`` in the environment. No network,
no upload, no default telemetry: every trace stays in the local SQLite file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DEFAULT_EXCERPT_CHARS = 500
MIN_EXCERPT_CHARS = 16
MAX_EXCERPT_CHARS = 4000
DEFAULT_MAX_RECORDS = 10_000
DEFAULT_MAX_AGE_DAYS = 30

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    return None


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _clamp(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _config_section(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(config, Mapping):
        return {}
    section = config.get("traces")
    return section if isinstance(section, Mapping) else {}


def _load_config() -> Mapping[str, Any] | None:
    """Read the profile config when available; any failure means "not enabled"."""
    try:
        from qubit_cli.config import load_config_readonly

        return load_config_readonly()
    except Exception:  # noqa: BLE001 - opt-in must never break a caller
        return None


@dataclass(frozen=True)
class TraceSettings:
    """Resolved trace policy for one operation."""

    enabled: bool = False
    db_path: str = ""
    max_excerpt_chars: int = DEFAULT_EXCERPT_CHARS
    max_records: int = DEFAULT_MAX_RECORDS
    max_age_days: int = DEFAULT_MAX_AGE_DAYS

    @property
    def path(self) -> Path:
        return Path(self.db_path).expanduser() if self.db_path else default_db_path()


def default_db_path() -> Path:
    """``$QUBIT_HOME/traces.db`` — profile-scoped, never a shared global file."""
    try:
        from qubit_constants import get_qubit_home

        home = get_qubit_home()
    except Exception:  # noqa: BLE001 - keep a deterministic fallback for standalone use
        home = Path.home() / ".qubit"
    return Path(home) / "traces.db"


def _resolve_path(section: Mapping[str, Any]) -> str:
    env_path = os.environ.get("QUBIT_TRACES_DB", "").strip()
    if env_path:
        return str(Path(os.path.expandvars(env_path)).expanduser())
    raw = section.get("db_path")
    if isinstance(raw, str) and raw.strip():
        return str(Path(os.path.expandvars(raw.strip())).expanduser())
    return str(default_db_path())


def resolve_settings(
    config: Mapping[str, Any] | None = None,
    *,
    enabled: bool | None = None,
) -> TraceSettings:
    """Resolve settings with precedence: explicit arg → env → config → default off."""
    section = _config_section(config)
    if enabled is None:
        env_enabled = _env_bool("QUBIT_TRACES_ENABLED")
        if env_enabled is not None:
            enabled = env_enabled
        elif isinstance(section.get("enabled"), bool):
            enabled = bool(section["enabled"])
        else:
            enabled = False
    excerpt = _env_int("QUBIT_TRACES_MAX_EXCERPT_CHARS")
    if excerpt is None:
        excerpt = section.get("max_excerpt_chars")
    max_records = _env_int("QUBIT_TRACES_MAX_RECORDS")
    if max_records is None:
        max_records = section.get("max_records")
    max_age = _env_int("QUBIT_TRACES_MAX_AGE_DAYS")
    if max_age is None:
        max_age = section.get("max_age_days")
    return TraceSettings(
        enabled=bool(enabled),
        db_path=_resolve_path(section),
        max_excerpt_chars=_clamp(
            excerpt, DEFAULT_EXCERPT_CHARS, MIN_EXCERPT_CHARS, MAX_EXCERPT_CHARS),
        max_records=_clamp(max_records, DEFAULT_MAX_RECORDS, 0, 10_000_000),
        max_age_days=_clamp(max_age, DEFAULT_MAX_AGE_DAYS, 0, 3650),
    )


def load_settings(*, enabled: bool | None = None) -> TraceSettings:
    """Resolve settings from the active profile config and environment."""
    return resolve_settings(_load_config(), enabled=enabled)


__all__ = [
    "TraceSettings",
    "DEFAULT_MAX_AGE_DAYS",
    "DEFAULT_MAX_RECORDS",
    "DEFAULT_EXCERPT_CHARS",
    "default_db_path",
    "load_settings",
    "resolve_settings",
]
