"""Hermetic tests for the platform-gated read-only Apple Health connector."""

from __future__ import annotations

import pytest

from connectors.apple_common import (
    REASON_INVALID_RESPONSE,
    REASON_NOT_SUPPORTED,
    REASON_TOOL_ERROR,
    AppleConnectorError,
)
from connectors.apple_health import (
    MAX_DAYS,
    REQUIRED_FRAMEWORK,
    AppleHealthConnector,
)


class _FakeHealthBackend:
    def __init__(self, rows=None, *, error=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.calls: list[dict] = []

    def health_summary(self, *, days):
        self.calls.append({"days": days})
        if self.error is not None:
            raise self.error
        return self.rows

    def write_sample(self, *args, **kwargs):
        raise AssertionError("the connector must never write")


def test_non_darwin_refuses_before_touching_the_adapter():
    backend = _FakeHealthBackend([{"date": "2026-03-15", "steps": 1}])
    connector = AppleHealthConnector(platform="linux", backend=backend)

    with pytest.raises(AppleConnectorError) as caught:
        connector.read_summary(days=7)

    exc = caught.value
    assert exc.reason == REASON_NOT_SUPPORTED
    assert exc.platform == "linux"
    assert exc.required_framework == REQUIRED_FRAMEWORK
    assert "linux" in exc.detail
    assert "HealthKit" in exc.detail
    assert backend.calls == []
    assert connector.available() is False


def test_darwin_maps_the_daily_summary_and_keeps_missing_metrics_null():
    backend = _FakeHealthBackend([
        {
            "date": "2026-03-15T00:00:00Z",
            "steps": 1234.9,
            "sleep_seconds": 7.5,
            "heart_rate": {"avg_bpm": 62.25, "min_bpm": 51, "max_bpm": 99},
            "active_energy_kcal": 321.239,
            "workouts": 2,
        },
        {
            "day": "2026-03-16",
            "steps": -5,
        },
    ])
    connector = AppleHealthConnector(platform="darwin", backend=backend)

    days = connector.read_summary(days=7)

    assert len(days) == 2
    first, second = days
    assert first.date == "2026-03-15"
    assert first.steps == 1234
    assert first.sleep_seconds == 7.5
    assert first.heart_rate_avg_bpm == 62.25
    assert first.heart_rate_min_bpm == 51.0
    assert first.heart_rate_max_bpm == 99.0
    assert first.active_energy_kcal == 321.24
    assert first.workouts == 2
    assert second.date == "2026-03-16"
    assert second.steps is None
    assert second.sleep_seconds is None
    assert second.heart_rate_avg_bpm is None
    assert second.active_energy_kcal is None
    assert second.workouts is None
    assert backend.calls == [{"days": 7}]
    assert connector.available() is True


def test_darwin_clamps_the_day_window():
    backend = _FakeHealthBackend([])
    connector = AppleHealthConnector(platform="darwin", backend=backend)

    connector.read_summary(days=9999)
    connector.read_summary(days=0)
    connector.read_summary(days="nope")

    assert backend.calls == [
        {"days": MAX_DAYS},
        {"days": 1},
        {"days": 7},
    ]


def test_darwin_skips_rows_without_a_date():
    backend = _FakeHealthBackend([
        {"steps": 10},
        object(),
        {"date": "2026-03-15", "steps": 20},
    ])
    connector = AppleHealthConnector(platform="darwin", backend=backend)

    days = connector.read_summary()

    assert [day.date for day in days] == ["2026-03-15"]
    assert days[0].steps == 20


def test_darwin_rejects_an_unexpected_payload():
    backend = _FakeHealthBackend({"days": []})
    connector = AppleHealthConnector(platform="darwin", backend=backend)

    with pytest.raises(AppleConnectorError) as caught:
        connector.read_summary()

    assert caught.value.reason == REASON_INVALID_RESPONSE


def test_darwin_redacts_a_framework_failure():
    backend = _FakeHealthBackend(
        error=RuntimeError("secret=abc /Users/alice/Library/Health"))
    connector = AppleHealthConnector(platform="darwin", backend=backend)

    with pytest.raises(AppleConnectorError) as caught:
        connector.read_summary()

    exc = caught.value
    assert exc.reason == REASON_TOOL_ERROR
    assert "RuntimeError" in exc.detail
    assert "secret" not in exc.detail
    assert "/Users/alice" not in exc.detail


def test_health_surface_has_no_write_operations():
    connector = AppleHealthConnector(platform="darwin", backend=[])
    for name in (
        "write_sample",
        "log_workout",
        "delete_sample",
        "update_summary",
    ):
        assert not hasattr(connector, name)
