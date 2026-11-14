"""Hermetic fixtures for the P7-05 outside-life projection tests."""
from __future__ import annotations

import pytest

from voicebridge.office.world.exterior import build_exterior_map


@pytest.fixture
def map_obj():
    return build_exterior_map()


@pytest.fixture
def source_events():
    return [
        {
            "event_id": "evt-env-day",
            "type": "environment",
            "occurred_at": "2026-01-01T08:00:00Z",
            "payload": {"weather": "clear", "day_night": "day",
                        "season": "spring"},
        },
        {
            "event_id": "evt-env-dusk",
            "type": "environment",
            "occurred_at": "2026-01-01T18:00:00Z",
            "payload": {"weather": "rain", "day_night": "dusk",
                        "season": "autumn"},
        },
        {
            "event_id": "evt-visitor",
            "type": "outside.visitor",
            "occurred_at": "2026-01-01T18:05:00Z",
            "zone_id": "zone-campus",
            "payload": {"kind": "visitor", "count": 2},
        },
        {
            "event_id": "evt-parking",
            "type": "outside.vehicle",
            "occurred_at": "2026-01-01T18:06:00Z",
            "zone_id": "zone-parking",
            "payload": {"kind": "vehicle"},
        },
        {
            "event_id": "evt-unknown-zone",
            "type": "outside.visitor",
            "occurred_at": "2026-01-01T18:07:00Z",
            "zone_id": "zone-nowhere",
            "payload": {"kind": "visitor"},
        },
        {
            "event_id": "evt-friendly",
            "type": "interaction",
            "occurred_at": "2026-01-01T18:10:00Z",
            "payload": {
                "participants": ["agent-a", "agent-b"],
                "department": "engineering",
                "relationship": "friendship",
                "trust_delta": 3,
            },
        },
        {
            "event_id": "evt-rival",
            "type": "simulation.interaction",
            "occurred_at": "2026-01-01T18:11:00Z",
            "payload": {
                "participants": ["agent-c", "agent-d"],
                "department": "sales",
                "relationship": "rivalry",
                "trust_delta": -4,
            },
        },
    ]


__all__ = ["map_obj", "source_events"]
