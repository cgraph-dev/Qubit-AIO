"""P7-06 — deterministic simulation audit and replay of office interactions.

The Business OS event log is authoritative. Simulation state, effects and FX
are *projections* over the ``office.interaction.*`` event family, and this
module rebuilds them without writing anything, calling a model, touching the
network or committing an external effect.

Contract
--------
Interactions are replayed in causal order: events sort by ``occurred_at``,
then aggregate sequence, then id, and an explicit ``causation_id`` always
precedes its effect. A malformed cycle falls back to the plain order instead
of dropping records.

Recognised event types (the prefix is canonical for the interaction engine):

* ``office.interaction.started``   — opens an interaction
* ``office.interaction.effect``    — applies need/relationship deltas
* ``office.interaction.fx``        — emits one deterministic FX event
* ``office.interaction.completed`` — closes an interaction
* ``office.interaction.cancelled`` — closes an interaction as cancelled
* ``office.interaction.failed``    — closes an interaction as failed
* ``office.interaction.note``      — records a note (count only)

Any other event is ignored by the reducer (``include_all_events`` keeps it as
a no-op frame so order can still be audited).

Guarantees
----------
* **Deterministic.** The same ordered history and seed produce byte-identical
  frame, effect, FX and final-state digests. FX seeds come from the payload or
  a stable hash of ``(seed_base, event_id, animation)`` — never ``random``.
* **Stubbed effects.** Replay reports ``effects: "stubbed"``. Needs and
  relationships are simulation presentation, not business records.
* **No fabricated work or business truth.** The module never inserts an
  artifact, receipt, task, run, ledger row or audit row. A completion claim
  that names business evidence is counted as ignored and stays unprojected.
* **Divergence is an incident.** A recorded projection that does not match
  becomes a typed ``SIMULATION_DIVERGENCE`` incident carrying the event ids,
  expected/actual digests, seed and clock. It is never reported as success.
* **Cancel and replay.** A cancel hook stops a run with ``status="cancelled"``;
  a fresh run from the same history is unaffected.
* **Bounded retention.** :class:`AuditLedger` retains a bounded number of
  tenant-scoped runs and incidents, prunes oldest-first and exports only the
  requested tenant.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterable, Mapping

from . import eventlog as EVENTLOG
from .errors import BAD_INPUT_CODE, BusinessError
from .ids import fingerprint
from .util import json_dumps, json_loads, now_iso, redact, require_text

SCHEMA = "qubit-office-simulation-audit/1"
PROJECTION_SCHEMA = "qubit-office-simulation-projection/1"
INCIDENT_SCHEMA = "qubit-office-simulation-incident/1"
EXPORT_SCHEMA = "qubit-office-simulation-audit-export/1"
LEDGER_SCHEMA = "qubit-office-simulation-audit-ledger/1"

INTERACTION_PREFIX = "office.interaction."
INTERACTION_TYPES = frozenset({
    "office.interaction.started",
    "office.interaction.effect",
    "office.interaction.consequence",
    "office.interaction.fx",
    "office.interaction.completed",
    "office.interaction.cancelled",
    "office.interaction.failed",
    "office.interaction.note",
})

NEEDS = (
    "energy",
    "hunger",
    "thirst",
    "hygiene",
    "social",
    "stress",
    "focus",
    "morale",
    "health",
)
NEED_SET = frozenset(NEEDS)
RELATIONSHIP_KEYS = (
    "trust",
    "respect",
    "attraction",
    "resentment",
    "friendship",
    "rivalry",
)
RELATIONSHIP_SET = frozenset(RELATIONSHIP_KEYS)
BASELINE_NEED = 50
NEED_MIN = 0
NEED_MAX = 100
RELATIONSHIP_MIN = -100
RELATIONSHIP_MAX = 100

DEFAULT_LIMIT = 2000
MAX_EVENTS = 5000
MAX_FRAMES = 5000
MAX_AGENTS = 500
MAX_ACTIVE = 1000
MAX_FX = 5000
MAX_INCIDENTS = 20
MAX_LEDGER_RUNS = 64
MAX_LEDGER_INCIDENTS = 256

TENANT_MISMATCH_CODE = "SIMULATION_TENANT_MISMATCH"
LIMIT_CODE = "SIMULATION_LIMIT_EXCEEDED"
CANCELLED_CODE = "SIMULATION_REPLAY_CANCELLED"
DIVERGENCE_CODE = "SIMULATION_DIVERGENCE"

#: Completion-shaped payload keys that belong to business truth, not the
#: simulation. They are deliberately ignored and counted, never projected.
BUSINESS_CLAIM_KEYS = (
    "artifact_id",
    "receipt_id",
    "provider_request_id",
    "provider_receipt",
    "external_id",
    "message_id",
    "ticket_id",
    "order_id",
    "transaction_id",
    "ledger_entry_id",
)


def _bad(detail: str) -> BusinessError:
    return BusinessError(BAD_INPUT_CODE, detail)


def _text(value: Any, label: str, *, max_len: int = 256) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise _bad(str(exc)) from exc


def _optional_text(value: Any, *, max_len: int = 256) -> str | None:
    if value is None:
        return None
    try:
        return require_text(value, "value", max_len=max_len)
    except ValueError as exc:
        raise _bad(str(exc)) from exc


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _digest(value: Any) -> str:
    return fingerprint(value)


def _seed_from(*parts: Any) -> int:
    return int(fingerprint(list(parts))[:16], 16)


def _bounded(value: Any, limit: int = 900) -> Any:
    text = json_dumps(value)
    if len(text) <= limit:
        return value
    if isinstance(value, Mapping):
        return {"_truncated": True, "keys": sorted(str(k) for k in value)[:20]}
    if isinstance(value, (list, tuple)):
        return {"_truncated": True, "count": len(value),
                "sample": list(value)[:10]}
    return text[:limit]


def _check_limit(value: int, limit: int, label: str) -> None:
    if value > limit:
        raise BusinessError(
            LIMIT_CODE,
            f"{label} exceeds the replay bound of {limit}",
        )


def _claim_present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _cancel_requested(cancel: Any) -> bool:
    if cancel is None:
        return False
    if callable(cancel):
        return bool(cancel())
    checker = getattr(cancel, "is_set", None)
    if callable(checker):
        return bool(checker())
    return bool(cancel)


# ---------------------------------------------------------------------------
# Event normalization and causal order
# ---------------------------------------------------------------------------
def _get(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(key, default)
    return getattr(record, key, default)


def normalize_event(record: Any) -> dict[str, Any]:
    """Return one event as plain data, refusing a structurally invalid row."""
    event_id = _text(_get(record, "event_id"), "event_id", max_len=120)
    tenant_id = _text(_get(record, "tenant_id"), "tenant_id", max_len=80)
    event_type = _text(
        _get(record, "type", _get(record, "event_type")),
        "event type", max_len=128,
    )
    sequence = _get(record, "sequence", _get(record, "seq", 0))
    payload = _get(record, "payload", None)
    if payload is None:
        payload = json_loads(_get(record, "payload_json"), {})
    if not isinstance(payload, Mapping):
        raise _bad(f"event {event_id} payload must be a mapping")
    return {
        "event_id": event_id,
        "tenant_id": tenant_id,
        "aggregate_id": str(_get(record, "aggregate_id", "") or ""),
        "sequence": _int(sequence),
        "type": event_type,
        "actor": str(_get(record, "actor", "system") or "system"),
        "correlation_id": _optional_text(_get(record, "correlation_id")),
        "causation_id": _optional_text(_get(record, "causation_id")),
        "occurred_at": _text(
            _get(record, "occurred_at"), "occurred_at", max_len=64),
        "payload": dict(payload),
        "payload_hash": _get(record, "payload_hash"),
        "retention_class": _get(record, "retention_class"),
        "tombstoned_at": _get(record, "tombstoned_at"),
    }


def causal_order(events: Iterable[Any]) -> list[dict[str, Any]]:
    """Order events by clock/sequence/id, never placing an effect first."""
    import heapq

    ordered = sorted(
        (normalize_event(event) for event in events),
        key=lambda ev: (
            ev["occurred_at"],
            ev["sequence"],
            ev["event_id"],
        ),
    )
    if not ordered:
        return ordered
    by_id = {ev["event_id"]: ev for ev in ordered}
    position = {ev["event_id"]: index for index, ev in enumerate(ordered)}
    children: dict[str, list[str]] = {}
    indegree = {ev["event_id"]: 0 for ev in ordered}
    for event in ordered:
        cause = event["causation_id"]
        if cause and cause in by_id and cause != event["event_id"]:
            children.setdefault(cause, []).append(event["event_id"])
            indegree[event["event_id"]] += 1
    heap = [
        (position[event_id], event_id)
        for event_id, degree in indegree.items()
        if degree == 0
    ]
    heapq.heapify(heap)
    result: list[dict[str, Any]] = []
    while heap:
        _, event_id = heapq.heappop(heap)
        result.append(by_id[event_id])
        for child in children.get(event_id, ()):
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(heap, (position[child], child))
    if len(result) != len(ordered):
        return ordered
    return result


def load_history(
    store: Any,
    tenant_id: str,
    *,
    aggregate_id: str | None = None,
    limit: Any = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Read a bounded, tenant-gated authoritative history for replay.

    Reading is the only store interaction this module performs. An event that
    carries a different tenant id is a typed refusal, never silently mixed.
    """
    tenant = _text(tenant_id, "tenant_id", max_len=80)
    try:
        bound = int(limit)
    except (TypeError, ValueError) as exc:
        raise _bad("limit must be an integer") from exc
    if bound < 1 or bound > MAX_EVENTS:
        raise _bad(f"limit must be between 1 and {MAX_EVENTS}")
    store.require_tenant(tenant)
    if aggregate_id:
        events = EVENTLOG.read_events(
            store, tenant, _text(aggregate_id, "aggregate_id", max_len=200),
            after_seq=0,
        )
    else:
        events = EVENTLOG.list_events(store, tenant, limit=bound)
    normalized = causal_order(events)
    _check_limit(len(normalized), MAX_EVENTS, "event history")
    for event in normalized:
        if event["tenant_id"] != tenant:
            raise BusinessError(
                TENANT_MISMATCH_CODE,
                f"event {event['event_id']} belongs to tenant "
                f"{event['tenant_id']!r}, not {tenant!r}",
            )
    return normalized


# ---------------------------------------------------------------------------
# Deterministic simulation reducer
# ---------------------------------------------------------------------------
def _new_state() -> dict[str, Any]:
    return {
        "agents": {},
        "rooms": {},
        "active": {},
        "counters": {
            "started": 0,
            "completed": 0,
            "cancelled": 0,
            "failed": 0,
            "effects": 0,
            "fx": 0,
            "fx_cancelled": 0,
            "notes": 0,
            "ignored": 0,
            "duplicate_starts": 0,
            "orphan_closures": 0,
            "claimed_business_truth_ignored": 0,
        },
    }


def _agent(state: dict[str, Any], agent_id: str) -> dict[str, Any]:
    agents = state["agents"]
    existing = agents.get(agent_id)
    if existing is not None:
        return existing
    _check_limit(len(agents) + 1, MAX_AGENTS, "agent count")
    existing = {
        "needs": {need: BASELINE_NEED for need in NEEDS},
        "relationships": {},
    }
    agents[agent_id] = existing
    return existing


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _fx_event(
    event: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    interaction_id: str | None,
    seed_base: int,
    reduced_motion: bool,
    source: str,
) -> dict[str, Any]:
    animation = _optional_text(
        payload.get("animation") or payload.get("anim"), max_len=120)
    seed_value = payload.get("seed")
    if seed_value is None:
        seed = _seed_from(seed_base, event["event_id"], animation or "")
    else:
        if isinstance(seed_value, bool) or not isinstance(seed_value, int):
            raise _bad("fx seed must be an integer")
        if seed_value < 0 or seed_value >= 2 ** 63:
            raise _bad("fx seed is outside the accepted range")
        seed = int(seed_value)
    return {
        "fx_event_id": "fx_" + _digest({
            "event_id": event["event_id"],
            "animation": animation,
            "seed": seed,
        })[:24],
        "event_id": event["event_id"],
        "interaction_id": interaction_id,
        "agent_id": _optional_text(payload.get("agent_id")),
        "room_id": _optional_text(payload.get("room_id")),
        "animation": animation,
        "particle": _optional_text(payload.get("particle"), max_len=120),
        "sound": _optional_text(payload.get("sound"), max_len=120),
        "emote": _optional_text(payload.get("emote"), max_len=120),
        "priority": _int(payload.get("priority"), 0),
        "seed": seed,
        "cancellation": _bool(payload.get("cancellation")),
        "reduced_motion": (
            reduced_motion or _bool(payload.get("reduced_motion"))),
        "source": source,
        "stubbed": True,
    }


def _apply_effects(
    state: dict[str, Any],
    event: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    interaction_id: str | None,
) -> dict[str, Any] | None:
    agent_id = _optional_text(payload.get("agent_id"))
    if agent_id is None and interaction_id:
        active = state["active"].get(interaction_id) or {}
        agent_id = active.get("agent_id")
    if agent_id is None:
        return None
    agent = _agent(state, agent_id)
    applied: dict[str, Any] = {}
    raw_effects = payload.get("effects")
    if isinstance(raw_effects, Mapping):
        for need, delta in raw_effects.items():
            if need not in NEED_SET:
                continue
            if isinstance(delta, bool) or not isinstance(delta, int):
                continue
            before = int(agent["needs"][need])
            after = _clamp(before + delta, NEED_MIN, NEED_MAX)
            agent["needs"][need] = after
            applied[need] = {
                "delta": delta,
                "applied_delta": after - before,
                "before": before,
                "after": after,
            }
    relationships: dict[str, Any] = {}
    raw_relations = payload.get("relationships")
    if isinstance(raw_relations, Mapping):
        for target, deltas in raw_relations.items():
            target_id = _optional_text(target, max_len=200)
            if target_id is None or not isinstance(deltas, Mapping):
                continue
            bucket = agent["relationships"].setdefault(target_id, {})
            for key, delta in deltas.items():
                if key not in RELATIONSHIP_SET:
                    continue
                if isinstance(delta, bool) or not isinstance(delta, int):
                    continue
                before = int(bucket.get(key, 0))
                after = _clamp(
                    before + delta, RELATIONSHIP_MIN, RELATIONSHIP_MAX)
                bucket[key] = after
                relationships.setdefault(target_id, {})[key] = {
                    "delta": delta,
                    "applied_delta": after - before,
                    "before": before,
                    "after": after,
                }
    if not applied and not relationships:
        return None
    return {
        "event_id": event["event_id"],
        "interaction_id": interaction_id,
        "agent_id": agent_id,
        "effects": applied,
        "relationships": relationships,
        "stubbed": True,
        "business_truth": False,
    }


def _close(
    state: dict[str, Any], interaction_id: str, outcome: str
) -> None:
    entry = state["active"].pop(interaction_id, None)
    state["counters"][outcome] += 1
    if entry is None:
        state["counters"]["orphan_closures"] += 1
        return
    room_id = entry.get("room_id")
    if room_id:
        room = state["rooms"].get(room_id)
        if room is not None:
            room["interactions"] = max(0, int(room["interactions"]) - 1)


def _apply_event(
    state: dict[str, Any],
    event: dict[str, Any],
    *,
    seed_base: int,
    reduced_motion: bool,
) -> dict[str, Any]:
    payload = event["payload"]
    result: dict[str, Any] = {
        "applied": False,
        "fx": None,
        "effects": None,
    }
    event_type = event["type"]
    if event_type not in INTERACTION_TYPES:
        state["counters"]["ignored"] += 1
        return result
    interaction_id = (
        _optional_text(payload.get("interaction_id"), max_len=200)
        or event["correlation_id"]
        or event["event_id"]
    )
    if any(_claim_present(payload.get(key)) for key in BUSINESS_CLAIM_KEYS):
        state["counters"]["claimed_business_truth_ignored"] += 1
    result["interaction_id"] = interaction_id

    if event_type == "office.interaction.started":
        if interaction_id in state["active"]:
            state["counters"]["duplicate_starts"] += 1
            result["applied"] = True
            return result
        _check_limit(len(state["active"]) + 1, MAX_ACTIVE, "active interactions")
        agent_id = _optional_text(payload.get("agent_id"))
        room_id = _optional_text(payload.get("room_id"))
        state["active"][interaction_id] = {
            "interaction_id": interaction_id,
            "agent_id": agent_id,
            "room_id": room_id,
            "action": _optional_text(payload.get("action"), max_len=120),
            "target_agent_id": _optional_text(payload.get("target_agent_id")),
            "started_event_id": event["event_id"],
            "started_at": event["occurred_at"],
        }
        if agent_id:
            _agent(state, agent_id)
        if room_id:
            room = state["rooms"].setdefault(
                room_id, {"interactions": 0})
            room["interactions"] = int(room["interactions"]) + 1
        state["counters"]["started"] += 1
        if payload.get("animation") or payload.get("anim"):
            fx = _fx_event(
                event, payload,
                interaction_id=interaction_id,
                seed_base=seed_base,
                reduced_motion=reduced_motion,
                source="started",
            )
            result["fx"] = fx
            state["counters"]["fx"] += 1
            if fx["cancellation"]:
                state["counters"]["fx_cancelled"] += 1
        result["applied"] = True
        return result

    if event_type in (
        "office.interaction.effect", "office.interaction.consequence",
    ):
        effects = _apply_effects(
            state, event, payload, interaction_id=interaction_id)
        if effects is not None:
            state["counters"]["effects"] += 1
            result["effects"] = effects
        result["applied"] = True
        return result

    if event_type == "office.interaction.fx":
        fx = _fx_event(
            event, payload,
            interaction_id=interaction_id,
            seed_base=seed_base,
            reduced_motion=reduced_motion,
            source="event",
        )
        result["fx"] = fx
        state["counters"]["fx"] += 1
        if fx["cancellation"]:
            state["counters"]["fx_cancelled"] += 1
        result["applied"] = True
        return result

    if event_type == "office.interaction.completed":
        _close(state, interaction_id, "completed")
        result["applied"] = True
        return result

    if event_type == "office.interaction.cancelled":
        _close(state, interaction_id, "cancelled")
        result["applied"] = True
        return result

    if event_type == "office.interaction.failed":
        _close(state, interaction_id, "failed")
        result["applied"] = True
        return result

    if event_type == "office.interaction.note":
        state["counters"]["notes"] += 1
        result["applied"] = True
        return result

    state["counters"]["ignored"] += 1
    return result


def _frame_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "agents": len(state["agents"]),
        "rooms": len(state["rooms"]),
        "active": len(state["active"]),
        "counters": dict(state["counters"]),
    }


def project(
    events: Iterable[Any],
    *,
    tenant_id: str | None = None,
    seed: Any = None,
    cancel: Any = None,
    clock: str | None = None,
    include_all_events: bool = False,
    reduced_motion: bool = False,
    mode: str = "replay",
) -> dict[str, Any]:
    """Rebuild simulation state, effects and FX from an ordered history.

    The return value is a projection document, never a store write. ``cancel``
    is a callable, an event-like object with ``is_set`` or a truthy value; it
    is checked before each event, and a cancelled projection keeps only the
    frames it completed.
    """
    ordered = causal_order(events)
    _check_limit(len(ordered), MAX_EVENTS, "event history")
    tenant = (
        _optional_text(tenant_id, max_len=80)
        if tenant_id is not None else None
    )
    for event in ordered:
        if tenant is not None and event["tenant_id"] != tenant:
            raise BusinessError(
                TENANT_MISMATCH_CODE,
                f"event {event['event_id']} belongs to tenant "
                f"{event['tenant_id']!r}, not {tenant!r}",
            )
    if tenant is None and ordered:
        tenant = ordered[0]["tenant_id"]
        for event in ordered:
            if event["tenant_id"] != tenant:
                raise BusinessError(
                    TENANT_MISMATCH_CODE,
                    f"event {event['event_id']} belongs to tenant "
                    f"{event['tenant_id']!r}, not {tenant!r}",
                )
    if seed is None:
        seed_base = _seed_from("simulation-audit", tenant or "")
    else:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise _bad("seed must be an integer")
        if seed < 0 or seed >= 2 ** 63:
            raise _bad("seed is outside the accepted range")
        seed_base = int(seed)
    stamp = clock or now_iso()
    state = _new_state()
    frames: list[dict[str, Any]] = []
    fx_timeline: list[dict[str, Any]] = []
    effect_samples: list[dict[str, Any]] = []
    effect_totals = {need: 0 for need in NEEDS}
    event_ids: list[str] = []
    cancelled_at: int | None = None
    cancelled = False

    for index, event in enumerate(ordered):
        if _cancel_requested(cancel):
            cancelled = True
            cancelled_at = index
            break
        event_ids.append(event["event_id"])
        applied = _apply_event(
            state, event, seed_base=seed_base, reduced_motion=reduced_motion)
        if event["type"] not in INTERACTION_TYPES and not include_all_events:
            continue
        fx = applied.get("fx")
        if fx is not None:
            _check_limit(len(fx_timeline) + 1, MAX_FX, "fx timeline")
            fx_timeline.append(fx)
        effects = applied.get("effects")
        if effects is not None:
            _check_limit(len(effect_samples) + 1, MAX_FX, "effect samples")
            effect_samples.append(effects)
            for need, detail in effects["effects"].items():
                effect_totals[need] += int(detail["applied_delta"])
        state_digest = _digest(state)
        effects_digest = _digest({
            "count": len(effect_samples),
            "totals": effect_totals,
            "last": effects,
        })
        fx_digest = _digest({
            "count": len(fx_timeline),
            "last": fx,
            "seeds": [entry["seed"] for entry in fx_timeline],
        })
        frames.append({
            "index": index,
            "event_id": event["event_id"],
            "sequence": event["sequence"],
            "type": event["type"],
            "correlation_id": event["correlation_id"],
            "causation_id": event["causation_id"],
            "applied": bool(applied.get("applied")),
            "state_digest": state_digest,
            "effects_digest": effects_digest,
            "fx_digest": fx_digest,
            "digest": _digest({
                "index": index,
                "state": state_digest,
                "effects": effects_digest,
                "fx": fx_digest,
            }),
            "summary": _frame_summary(state),
        })
        _check_limit(len(frames), MAX_FRAMES, "frame count")

    final_digest = _digest(state)
    if cancelled:
        status = "cancelled"
    elif not frames and not ordered:
        status = "empty"
    elif not ordered:
        status = "empty"
    else:
        status = "ok"
    return {
        "schema": PROJECTION_SCHEMA,
        "mode": mode,
        "tenant_id": tenant,
        "state": status,
        "seed": seed_base,
        "clock": stamp,
        "event_count": len(ordered),
        "event_ids": event_ids,
        "frames": frames,
        "frame_count": len(frames),
        "final_state": state,
        "final_digest": final_digest,
        "effects": {
            "mode": "stubbed",
            "applied_events": len(effect_samples),
            "totals": effect_totals,
            "sample": effect_samples[-20:],
            "business_truth": False,
        },
        "fx": {
            "mode": "stubbed",
            "count": len(fx_timeline),
            "seeds": [entry["seed"] for entry in fx_timeline],
            "timeline": fx_timeline[-50:],
            "real_calls": False,
        },
        "cancelled": cancelled,
        "cancelled_at_index": cancelled_at,
        "external_side_effects": False,
        "fabricated_work": False,
        "reduced_motion": bool(reduced_motion),
        "warnings": _projection_warnings(state, cancelled),
    }


def _projection_warnings(
    state: Mapping[str, Any], cancelled: bool
) -> list[str]:
    warnings: list[str] = []
    counters = state["counters"]
    if counters["orphan_closures"]:
        warnings.append(
            f"{counters['orphan_closures']} closure event(s) had no open"
            " interaction; state is still rebuilt in order")
    if counters["duplicate_starts"]:
        warnings.append(
            f"{counters['duplicate_starts']} duplicate start event(s) kept the"
            " first open interaction")
    if counters["claimed_business_truth_ignored"]:
        warnings.append(
            f"{counters['claimed_business_truth_ignored']} completion claim(s)"
            " named business evidence; replay did not project any")
    if cancelled:
        warnings.append("replay was cancelled; the result is partial")
    return warnings


def record_projection(events: Iterable[Any], **kwargs: Any) -> dict[str, Any]:
    """Record the deterministic projection a later replay is compared to."""
    kwargs.pop("mode", None)
    kwargs.pop("cancel", None)
    return project(events, mode="record", **kwargs)


# ---------------------------------------------------------------------------
# Comparison and typed divergence incidents
# ---------------------------------------------------------------------------
def _incident(
    kind: str,
    *,
    event: Mapping[str, Any] | None,
    expected: Any,
    actual: Any,
    seed: int,
    clock: str,
    index: int | None = None,
    recorded: Any = None,
    replayed: Any = None,
) -> dict[str, Any]:
    event = event or {}
    body = {
        "schema": INCIDENT_SCHEMA,
        "code": DIVERGENCE_CODE,
        "kind": kind,
        "event_id": event.get("event_id"),
        "event_ids": [event["event_id"]] if event.get("event_id") else [],
        "sequence": event.get("sequence"),
        "correlation_id": event.get("correlation_id"),
        "causation_id": event.get("causation_id"),
        "frame_index": index,
        "expected": _bounded(expected),
        "actual": _bounded(actual),
        "seed": seed,
        "clock": clock,
        "recorded_digest": recorded,
        "replayed_digest": replayed,
    }
    body["incident_id"] = "inc_" + _digest(body)[:24]
    return body


def _normalize_recorded(recorded: Any) -> dict[str, Any]:
    if not isinstance(recorded, Mapping):
        raise _bad("recorded projection must be a mapping")
    document = dict(recorded)
    frames = document.get("frames")
    if frames is None and isinstance(document.get("projection"), Mapping):
        document = dict(document["projection"])
        frames = document.get("frames")
    if frames is not None and not isinstance(frames, (list, tuple)):
        raise _bad("recorded frames must be a list")
    return document


def _frame_fields(frame: Mapping[str, Any]) -> dict[str, Any]:
    state = frame.get("state_digest")
    effects = frame.get("effects_digest")
    fx = frame.get("fx_digest")
    if state is None and isinstance(frame.get("state"), Mapping):
        state = _digest(frame["state"])
    return {
        "state_digest": state,
        "effects_digest": effects,
        "fx_digest": fx,
        "digest": frame.get("digest"),
    }


def replay(
    events: Iterable[Any],
    recorded: Any,
    *,
    tenant_id: str | None = None,
    seed: Any = None,
    cancel: Any = None,
    clock: str | None = None,
    include_all_events: bool = False,
    reduced_motion: bool = False,
    max_incidents: int = MAX_INCIDENTS,
) -> dict[str, Any]:
    """Replay history and compare it with a recorded projection.

    A mismatch is a typed ``SIMULATION_DIVERGENCE`` incident list; it is never
    folded into ``match``. ``status`` is ``empty`` for no events, ``cancelled``
    when the cancel hook fired, ``divergent`` for any incident and ``match``
    only when every recorded field agrees.
    """
    document = _normalize_recorded(recorded)
    recorded_tenant = _optional_text(document.get("tenant_id"), max_len=80)
    if (
        tenant_id is not None
        and recorded_tenant is not None
        and str(tenant_id) != recorded_tenant
    ):
        raise BusinessError(
            TENANT_MISMATCH_CODE,
            f"recorded projection belongs to tenant {recorded_tenant!r}, not"
            f" {tenant_id!r}",
        )
    projection = project(
        events,
        tenant_id=tenant_id,
        seed=seed,
        cancel=cancel,
        clock=clock,
        include_all_events=include_all_events,
        reduced_motion=reduced_motion,
    )
    if (
        recorded_tenant is not None
        and projection["tenant_id"] is not None
        and recorded_tenant != projection["tenant_id"]
    ):
        raise BusinessError(
            TENANT_MISMATCH_CODE,
            f"recorded projection belongs to tenant {recorded_tenant!r}, not"
            f" {projection['tenant_id']!r}",
        )
    stamp = projection["clock"]
    computed_seed = projection["seed"]
    incidents: list[dict[str, Any]] = []
    total_incidents = 0
    frames_checked = 0

    def add(kind: str, **kwargs: Any) -> None:
        nonlocal total_incidents
        total_incidents += 1
        if len(incidents) < max(1, int(max_incidents)):
            incidents.append(_incident(
                kind, seed=computed_seed, clock=stamp, **kwargs))

    if projection["cancelled"]:
        return _result(
            projection, document, incidents, total_incidents, frames_checked,
            status="cancelled", clock=stamp,
        )

    recorded_seed = document.get("seed")
    if recorded_seed is not None and recorded_seed != computed_seed:
        add("seed", event=None, expected=recorded_seed,
            actual=computed_seed)

    recorded_event_ids = document.get("event_ids")
    if isinstance(recorded_event_ids, (list, tuple)) or isinstance(
        recorded_event_ids, str
    ):
        if recorded_event_ids != projection["event_ids"]:
            index = _first_mismatch(
                list(recorded_event_ids)
                if not isinstance(recorded_event_ids, str)
                else [recorded_event_ids],
                projection["event_ids"],
            )
            event = (
                projection["frames"][index]
                if index is not None and index < len(projection["frames"])
                else None
            )
            add(
                "event_order",
                event=event,
                index=index,
                expected=(list(recorded_event_ids)
                          if not isinstance(recorded_event_ids, str)
                          else [recorded_event_ids])[:50],
                actual=projection["event_ids"][:50],
            )

    recorded_frames = document.get("frames")
    if recorded_frames is not None:
        recorded_frames = list(recorded_frames)
        if len(recorded_frames) != len(projection["frames"]):
            add(
                "frame_count",
                event=None,
                expected=len(recorded_frames),
                actual=len(projection["frames"]),
            )
        for index, actual_frame in enumerate(projection["frames"]):
            if index >= len(recorded_frames):
                break
            frames_checked += 1
            expected_frame = recorded_frames[index]
            if not isinstance(expected_frame, Mapping):
                add(
                    "frame_shape", event=actual_frame, index=index,
                    expected="mapping", actual=type(expected_frame).__name__,
                )
                continue
            expected_fields = _frame_fields(expected_frame)
            actual_fields = _frame_fields(actual_frame)
            for field in ("state_digest", "effects_digest", "fx_digest"):
                if expected_fields[field] is None:
                    continue
                if expected_fields[field] != actual_fields[field]:
                    add(
                        field,
                        event=actual_frame,
                        index=index,
                        expected=expected_fields[field],
                        actual=actual_fields[field],
                        recorded=expected_frame.get("digest"),
                        replayed=actual_frame.get("digest"),
                    )
            if (
                expected_fields["digest"] is not None
                and expected_fields["digest"] != actual_frame["digest"]
            ):
                add(
                    "frame_digest",
                    event=actual_frame,
                    index=index,
                    expected=expected_fields["digest"],
                    actual=actual_frame["digest"],
                )

    recorded_final_state = document.get("final_state")
    if isinstance(recorded_final_state, Mapping):
        if _digest(recorded_final_state) != projection["final_digest"]:
            add(
                "final_state",
                event=None,
                expected=_digest(recorded_final_state),
                actual=projection["final_digest"],
            )
    recorded_final_digest = document.get("final_digest")
    if (
        recorded_final_digest is not None
        and recorded_final_digest != projection["final_digest"]
    ):
        add(
            "final_digest",
            event=None,
            expected=recorded_final_digest,
            actual=projection["final_digest"],
        )

    if projection["state"] == "empty" and not incidents:
        status = "empty"
    elif incidents:
        status = "divergent"
    else:
        status = "match"
    return _result(
        projection, document, incidents, total_incidents, frames_checked,
        status=status, clock=stamp,
    )


def _first_mismatch(expected: list[Any], actual: list[Any]) -> int | None:
    for index in range(max(len(expected), len(actual))):
        if index >= len(expected) or index >= len(actual):
            return index
        if expected[index] != actual[index]:
            return index
    return None


def _result(
    projection: Mapping[str, Any],
    recorded: Mapping[str, Any],
    incidents: list[dict[str, Any]],
    total_incidents: int,
    frames_checked: int,
    *,
    status: str,
    clock: str,
) -> dict[str, Any]:
    recorded_digest = (
        recorded.get("final_digest")
        or (
            _digest(recorded["final_state"])
            if isinstance(recorded.get("final_state"), Mapping) else None
        )
        or (
            _digest({
                "frames": [
                    _frame_fields(frame)
                    for frame in recorded.get("frames", [])
                    if isinstance(frame, Mapping)
                ],
                "seed": recorded.get("seed"),
            })
            if recorded.get("frames") is not None else None
        )
    )
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "tenant_id": projection["tenant_id"],
        "status": status,
        "mode": "replay",
        "clock": clock,
        "seed": projection["seed"],
        "recorded": {
            "digest": recorded_digest,
            "frame_count": (
                len(recorded["frames"])
                if recorded.get("frames") is not None else None
            ),
            "seed": recorded.get("seed"),
            "state": recorded.get("state"),
        },
        "replayed": {
            "digest": projection["final_digest"],
            "frame_count": projection["frame_count"],
            "seed": projection["seed"],
            "state": projection["state"],
        },
        "frames_checked": frames_checked,
        "incidents": incidents,
        "incident_count": total_incidents,
        "divergence": incidents[0] if incidents else None,
        "effects": "stubbed",
        "external_side_effects": False,
        "fabricated_work": False,
        "warnings": list(projection.get("warnings") or []),
        "replay": {
            "enabled": True,
            "effects": "stubbed",
            "real_calls": False,
            "label": (
                "Simulation replay: state/effects/FX rebuilt from the event"
                " log; effects stubbed; no real action performed"
            ),
            "note": (
                "A divergence is an incident. Replay never executes, approves,"
                " spends, resumes or writes business truth."
            ),
        },
        "cancelled": projection["cancelled"],
        "cancelled_at_index": projection["cancelled_at_index"],
        "projection": {
            "frame_count": projection["frame_count"],
            "effect_events": projection["effects"]["applied_events"],
            "fx_events": projection["fx"]["count"],
            "fx_seeds": projection["fx"]["seeds"],
        },
    }
    result["run_id"] = _run_id(result)
    return result


def _run_id(result: Mapping[str, Any]) -> str:
    return "run_" + _digest({
        "tenant": result.get("tenant_id"),
        "status": result.get("status"),
        "seed": result.get("seed"),
        "recorded": result.get("recorded", {}).get("digest"),
        "replayed": result.get("replayed", {}).get("digest"),
        "frames_checked": result.get("frames_checked"),
        "incidents": [incident.get("incident_id")
                      for incident in result.get("incidents", [])],
    })[:24]


# ---------------------------------------------------------------------------
# Bounded, tenant-scoped audit retention/export
# ---------------------------------------------------------------------------
class AuditLedger:
    """Bounded in-memory retention for replay results (tenant-scoped).

    Retention is explicit and observable: recording past ``max_runs`` drops
    the oldest run and the export reports both the retained and dropped
    counts. No record is ever served to a different tenant.
    """

    def __init__(
        self,
        *,
        max_runs: int = MAX_LEDGER_RUNS,
        max_incidents: int = MAX_LEDGER_INCIDENTS,
        clock: str | None = None,
    ) -> None:
        try:
            runs = int(max_runs)
            incidents = int(max_incidents)
        except (TypeError, ValueError) as exc:
            raise _bad("retention bounds must be integers") from exc
        if isinstance(max_runs, bool) or runs < 1:
            raise _bad("max_runs must be an integer >= 1")
        if isinstance(max_incidents, bool) or incidents < 0:
            raise _bad("max_incidents must be an integer >= 0")
        self.max_runs = runs
        self.max_incidents = incidents
        self._clock = clock
        self._runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.dropped_runs = 0
        self.replaced_runs = 0

    def _stamp(self, value: Any = None) -> str:
        if value:
            return str(value)
        return self._clock or now_iso()

    def record(
        self,
        result: Mapping[str, Any],
        *,
        recorded_at: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            raise _bad("result must be a mapping")
        tenant = _optional_text(result.get("tenant_id"), max_len=80)
        if tenant is None:
            raise _bad("result has no tenant_id")
        status = _text(result.get("status") or "unknown", "status", max_len=40)
        incidents = [
            redact(dict(item))
            for item in list(result.get("incidents") or [])[: self.max_incidents]
            if isinstance(item, Mapping)
        ]
        key = run_id or str(result.get("run_id") or _run_id(result))
        record = {
            "schema": LEDGER_SCHEMA,
            "tenant_id": tenant,
            "run_id": key,
            "status": status,
            "seed": result.get("seed"),
            "clock": _optional_text(result.get("clock"), max_len=64),
            "recorded_digest": (
                result.get("recorded") or {}).get("digest")
            if isinstance(result.get("recorded"), Mapping) else None,
            "replayed_digest": (
                result.get("replayed") or {}).get("digest")
            if isinstance(result.get("replayed"), Mapping) else None,
            "frames_checked": _int(result.get("frames_checked")),
            "incident_count": _int(result.get("incident_count")),
            "incidents": incidents,
            "incidents_truncated": (
                _int(result.get("incident_count")) > len(incidents)),
            "recorded_at": self._stamp(recorded_at),
        }
        if key in self._runs:
            self._runs.pop(key)
            self.replaced_runs += 1
        self._runs[key] = record
        while len(self._runs) > self.max_runs:
            self._runs.popitem(last=False)
            self.dropped_runs += 1
        return dict(record)

    def get(self, run_id: str, *, tenant_id: str) -> dict[str, Any] | None:
        probe = _text(run_id, "run_id", max_len=120)
        tenant = _text(tenant_id, "tenant_id", max_len=80)
        record = self._runs.get(probe)
        if record is None or record["tenant_id"] != tenant:
            return None
        return dict(record)

    def prune(self, *, retain: int | None = None) -> int:
        target = self.max_runs if retain is None else int(retain)
        if target < 0:
            raise _bad("retain must be >= 0")
        dropped = 0
        while len(self._runs) > target:
            self._runs.popitem(last=False)
            self.dropped_runs += 1
            dropped += 1
        return dropped

    def counts(self, *, tenant_id: str | None = None) -> dict[str, int]:
        tenant = (
            _optional_text(tenant_id, max_len=80) if tenant_id else None)
        runs = [
            record for record in self._runs.values()
            if tenant is None or record["tenant_id"] == tenant
        ]
        return {
            "runs": len(runs),
            "max_runs": self.max_runs,
            "dropped_runs": self.dropped_runs,
            "replaced_runs": self.replaced_runs,
            "incidents": sum(record["incident_count"] for record in runs),
            "divergent_runs": sum(
                1 for record in runs if record["status"] == "divergent"),
        }

    def export(
        self, *, tenant_id: str, limit: Any = None
    ) -> dict[str, Any]:
        tenant = _text(tenant_id, "tenant_id", max_len=80)
        if limit is None:
            bound = self.max_runs
        else:
            try:
                bound = int(limit)
            except (TypeError, ValueError) as exc:
                raise _bad("limit must be an integer") from exc
            if bound < 1 or bound > self.max_runs:
                raise _bad(f"limit must be between 1 and {self.max_runs}")
        rows = [
            dict(record) for record in self._runs.values()
            if record["tenant_id"] == tenant
        ]
        rows = rows[-bound:]
        incidents = [
            incident
            for row in rows for incident in row.get("incidents", [])
        ][: self.max_incidents]
        return {
            "schema": EXPORT_SCHEMA,
            "tenant_id": tenant,
            "state": "ok" if rows else "empty",
            "generated_at": self._stamp(),
            "counts": {
                "runs": len(rows),
                "incidents": sum(
                    int(row.get("incident_count") or 0) for row in rows),
                "divergent_runs": sum(
                    1 for row in rows if row["status"] == "divergent"),
            },
            "runs": rows,
            "incidents": incidents,
            "retention": {
                "max_runs": self.max_runs,
                "max_incidents": self.max_incidents,
                "dropped_runs": self.dropped_runs,
                "replaced_runs": self.replaced_runs,
            },
            "tenant_scoped": True,
        }


def export_audit(
    ledger: AuditLedger, *, tenant_id: str, limit: Any = None
) -> dict[str, Any]:
    """Export only one tenant's bounded audit records."""
    if not isinstance(ledger, AuditLedger):
        raise _bad("ledger must be an AuditLedger")
    return ledger.export(tenant_id=tenant_id, limit=limit)


__all__ = [
    "AuditLedger",
    "BUSINESS_CLAIM_KEYS",
    "CANCELLED_CODE",
    "DIVERGENCE_CODE",
    "EXPORT_SCHEMA",
    "INCIDENT_SCHEMA",
    "INTERACTION_TYPES",
    "LEDGER_SCHEMA",
    "LIMIT_CODE",
    "MAX_EVENTS",
    "MAX_FRAMES",
    "MAX_INCIDENTS",
    "NEEDS",
    "PROJECTION_SCHEMA",
    "RELATIONSHIP_KEYS",
    "SCHEMA",
    "TENANT_MISMATCH_CODE",
    "causal_order",
    "export_audit",
    "load_history",
    "normalize_event",
    "project",
    "record_projection",
    "replay",
]
