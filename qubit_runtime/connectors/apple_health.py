"""Read-only Apple Health summary connector.

On macOS a caller injects a HealthKit-shaped adapter exposing
``health_summary(days=)`` and returning one row per day. The connector maps
rows to bounded :class:`HealthDaySummary` records; a metric the adapter did not
report stays ``None`` rather than being invented as zero. It has no write,
workout-logging or sample-deletion path.

On any other platform ``read_summary`` raises ``not_supported`` with the host
platform and the required framework before the adapter is touched.

Field shape was informed by OpenJarvis ``0583901``
(``src/openjarvis/connectors/apple_health.py``, Apache-2.0); no upstream code
is copied. See ``connectors/APPLE_PROVENANCE.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from connectors.apple_common import (
    AppleConnectorError,
    backend_rows,
    coerce_days,
    coerce_nonnegative_float,
    coerce_nonnegative_int,
    current_platform,
    ensure_supported,
    map_backend_error,
    normalize_date,
    require_backend,
)

CONNECTOR_NAME = "Apple Health"
REQUIRED_FRAMEWORK = "HealthKit"
SUPPORTED_PLATFORM = "darwin"

DEFAULT_DAYS = 7
MAX_DAYS = 365


@dataclass(frozen=True)
class HealthDaySummary:
    date: str
    steps: Optional[int] = None
    sleep_seconds: Optional[float] = None
    heart_rate_avg_bpm: Optional[float] = None
    heart_rate_min_bpm: Optional[float] = None
    heart_rate_max_bpm: Optional[float] = None
    active_energy_kcal: Optional[float] = None
    workouts: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "steps": self.steps,
            "sleep_seconds": self.sleep_seconds,
            "heart_rate_avg_bpm": self.heart_rate_avg_bpm,
            "heart_rate_min_bpm": self.heart_rate_min_bpm,
            "heart_rate_max_bpm": self.heart_rate_max_bpm,
            "active_energy_kcal": self.active_energy_kcal,
            "workouts": self.workouts,
        }


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


def _summary_from_row(row: dict[str, Any]) -> Optional[HealthDaySummary]:
    day = normalize_date(row.get("date") or row.get("day"))
    if not day:
        return None
    heart = row.get("heart_rate") if isinstance(row.get("heart_rate"), dict) else {}
    return HealthDaySummary(
        date=day,
        steps=coerce_nonnegative_int(row.get("steps")),
        sleep_seconds=_round(coerce_nonnegative_float(row.get("sleep_seconds"))),
        heart_rate_avg_bpm=_round(coerce_nonnegative_float(
            row.get("heart_rate_avg_bpm", heart.get("avg_bpm")))),
        heart_rate_min_bpm=_round(coerce_nonnegative_float(
            row.get("heart_rate_min_bpm", heart.get("min_bpm")))),
        heart_rate_max_bpm=_round(coerce_nonnegative_float(
            row.get("heart_rate_max_bpm", heart.get("max_bpm")))),
        active_energy_kcal=_round(coerce_nonnegative_float(
            row.get("active_energy_kcal", row.get("active_energy")))),
        workouts=coerce_nonnegative_int(row.get("workouts")),
    )


class AppleHealthConnector:
    """Narrow read-only Health summary surface over an injected adapter."""

    connector_id = "apple_health"

    def __init__(self, *, platform: Optional[str] = None, backend: Any = None) -> None:
        self._platform = platform or current_platform()
        self._backend = backend

    @property
    def platform(self) -> str:
        return self._platform

    def available(self) -> bool:
        return self._platform == SUPPORTED_PLATFORM and self._backend is not None

    def read_summary(self, *, days: object = DEFAULT_DAYS) -> list[HealthDaySummary]:
        ensure_supported(
            platform=self._platform,
            required_framework=REQUIRED_FRAMEWORK,
            connector=CONNECTOR_NAME,
        )
        bounded = coerce_days(days, default=DEFAULT_DAYS, maximum=MAX_DAYS, minimum=1)
        backend = require_backend(
            self._backend,
            platform=self._platform,
            required_framework=REQUIRED_FRAMEWORK,
            connector=CONNECTOR_NAME,
        )
        try:
            payload = backend.health_summary(days=bounded)
        except AppleConnectorError:
            raise
        except Exception as exc:
            raise map_backend_error(
                exc,
                connector=CONNECTOR_NAME,
                required_framework=REQUIRED_FRAMEWORK,
                platform=self._platform,
            ) from None
        rows = backend_rows(
            payload,
            connector=CONNECTOR_NAME,
            required_framework=REQUIRED_FRAMEWORK,
            platform=self._platform,
        )
        summaries: list[HealthDaySummary] = []
        for row in rows:
            summary = _summary_from_row(row)
            if summary is not None:
                summaries.append(summary)
        return summaries[:bounded]
