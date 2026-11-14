"""Shared fixtures for the P7-06 deterministic simulation replay audit.

Hermetic: synthetic event payloads, a throwaway SQLite store under ``tmp_path``
and a fixed clock. No network, no service, no model, no credentials and no
live business data.
"""
from __future__ import annotations


from voicebridge.business import BusinessStore, eventlog

TENANT = "ten_sim_a"
OTHER_TENANT = "ten_sim_b"
CLOCK = "2026-01-01T00:00:00.000Z"
SEED = 424242


class _Cfg:
    def __init__(self, state_dir):
        self._state = state_dir

    def state_dir(self):
        return str(self._state)


def make_event(
    event_id: str,
    event_type: str,
    sequence: int,
    payload: dict,
    *,
    at: str | None = None,
    tenant: str = TENANT,
    correlation_id: str = "corr-coffee",
    causation_id: str | None = None,
    aggregate_id: str = "agg-lounge",
) -> dict:
    return {
        "event_id": event_id,
        "tenant_id": tenant,
        "aggregate_id": aggregate_id,
        "sequence": sequence,
        "type": event_type,
        "actor": "agent-ada",
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "occurred_at": at or f"2026-01-01T00:00:0{sequence}.000Z",
        "payload": payload,
        "payload_hash": None,
        "retention_class": "standard",
        "tombstoned_at": None,
    }


def interaction_history(*, tenant: str = TENANT) -> list[dict]:
    return [
        make_event(
            f"{tenant}-e1",
            "office.interaction.started",
            1,
            {
                "interaction_id": "int-coffee",
                "agent_id": "agent-ada",
                "target_agent_id": "agent-grace",
                "room_id": "room-lounge",
                "action": "coffee",
                "animation": "walk_to_kitchen",
                "seed": 1001,
            },
            tenant=tenant,
        ),
        make_event(
            f"{tenant}-e2",
            "office.interaction.effect",
            2,
            {
                "interaction_id": "int-coffee",
                "agent_id": "agent-ada",
                "effects": {"energy": 5, "stress": -2, "social": 3},
                "relationships": {
                    "agent-grace": {"trust": 2, "friendship": 1},
                },
            },
            tenant=tenant,
            causation_id=f"{tenant}-e1",
        ),
        make_event(
            f"{tenant}-e3",
            "office.interaction.fx",
            3,
            {
                "interaction_id": "int-coffee",
                "agent_id": "agent-ada",
                "room_id": "room-lounge",
                "animation": "sip",
                "particle": "steam",
                "seed": 2002,
                "priority": 2,
            },
            tenant=tenant,
            causation_id=f"{tenant}-e1",
        ),
        make_event(
            f"{tenant}-e4",
            "office.interaction.note",
            4,
            {"interaction_id": "int-coffee", "note": "caught up"},
            tenant=tenant,
            causation_id=f"{tenant}-e2",
        ),
        make_event(
            f"{tenant}-e5",
            "office.interaction.completed",
            5,
            {"interaction_id": "int-coffee"},
            tenant=tenant,
            causation_id=f"{tenant}-e3",
        ),
        make_event(
            f"{tenant}-e6",
            "task.created",
            1,
            {"task_id": "task-1"},
            tenant=tenant,
            at="2026-01-01T00:00:06.000Z",
            aggregate_id="task-1",
        ),
    ]


def with_claim(event_id: str = "claim-e1", *, tenant: str = TENANT) -> list[dict]:
    return [
        make_event(
            event_id,
            "office.interaction.started",
            1,
            {"interaction_id": "int-claim", "agent_id": "agent-ada"},
            tenant=tenant,
        ),
        make_event(
            f"{event_id}-done",
            "office.interaction.completed",
            2,
            {
                "interaction_id": "int-claim",
                "artifact_id": "art-123",
                "provider_receipt": "rcpt-456",
            },
            tenant=tenant,
            causation_id=event_id,
            at="2026-01-01T00:00:02.000Z",
        ),
    ]




def append_history(store: BusinessStore, tenant_id: str, business_id: str):
    events = []
    previous = None
    for index, payload in enumerate(
        [
            {
                "interaction_id": "int-store",
                "agent_id": "agent-ada",
                "room_id": "room-lounge",
                "action": "review",
            },
            {
                "interaction_id": "int-store",
                "agent_id": "agent-ada",
                "effects": {"focus": 4, "stress": -1},
            },
            {
                "interaction_id": "int-store",
                "animation": "typing",
                "seed": 77,
            },
            {"interaction_id": "int-store"},
        ],
        start=1,
    ):
        event_type = [
            "office.interaction.started",
            "office.interaction.effect",
            "office.interaction.fx",
            "office.interaction.completed",
        ][index - 1]
        event = eventlog.append_event(
            store,
            tenant_id,
            business_id,
            event_type,
            payload,
            actor="agent-ada",
            idempotency_key=f"sim-{index}",
            correlation_id="corr-store",
            causation_id=previous,
            now=1_700_000_000.0 + index,
        )
        previous = event.event_id
        events.append(event)
    return events
