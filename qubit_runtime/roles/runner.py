"""Execute one agent role through the existing Qubit turn/recipe surfaces.

There is one agent loop: a native role is a ``TurnRequest`` on the
``RuntimePort`` with the role's system prompt and toolsets; a recipe-backed
role dispatches the existing recipe runner (which owns its own prompt). A
non-native engine that cannot honour the role refuses typed instead of
silently dropping toolsets or falling back to another engine.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from agent.runtime_port import TurnEvent, TurnRequest
from roles.base import AgentRole
from roles.registry import get_role

_DEFAULT_PROMPT = "Proceed with your role."
_JSON_INSTRUCTION = "\n\nReturn only one JSON object; no prose around it."
_ACTIVE_TIERS = ("api", "")


def _json_instruction(json_mode: bool) -> str:
    return _JSON_INSTRUCTION if json_mode else ""


def _config_get(config: Any, *path: str, default: Any = None) -> Any:
    """Read a dotted path from a dict config or a Config-like object."""
    if config is None:
        return default
    if isinstance(config, dict):
        node: Any = config
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            return getter(*path, default=default)
        except TypeError:
            try:
                return getter(*path)
            except Exception:  # noqa: BLE001 - a config read never fails a turn
                return default
        except Exception:  # noqa: BLE001
            return default
    return default


def _load_tier_config(config: Any) -> Any:
    if config is not None:
        return config
    try:
        from qubit_cli.config import load_config

        return load_config()
    except Exception:  # noqa: BLE001 - the active model is the honest default
        return {}


def _tier_map(config: Any) -> dict[str, Any]:
    value = _config_get(config, "roles", "model_tiers", default={})
    return value if isinstance(value, dict) else {}


def _parse_tier_entry(entry: Any) -> tuple[str | None, str | None] | None:
    """Normalize one map entry to ``(provider, model)``.

    ``{provider, model}`` selects both explicitly; a string is a model-profile
    id (a model name) run on the active provider. Anything else is treated as
    "no map" so a malformed entry never fails a turn.
    """
    if isinstance(entry, dict):
        provider = str(entry.get("provider") or "").strip() or None
        model = str(entry.get("model") or "").strip() or None
        return (provider, model) if (provider or model) else None
    if isinstance(entry, str):
        model = entry.strip() or None
        return (None, model) if model else None
    return None


def resolve_model_selection(
    role: AgentRole,
    *,
    model_override: str | None = None,
    provider_override: str | None = None,
    config: Any = None,
) -> dict[str, Any]:
    """Resolve the role's tier through ``roles.model_tiers``.

    Returns the effective ``requested`` provider and ``target_model`` plus an
    honest ``applied``/``note`` pair. The active model is the fallback in every
    unmapped case, so a missing map can never fail a role turn. CLI overrides
    (``--model``/``--provider``) win over the map.
    """
    resolved_config = _load_tier_config(config)
    model_cfg = _config_get(resolved_config, "model", default={}) or {}
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    active_provider = str(model_cfg.get("provider") or "").strip() or None
    active_model = str(model_cfg.get("default") or "").strip() or None

    tier = str(role.model_tier or "")
    requested = active_provider
    target_model = active_model
    applied = False
    note = ""

    parsed = _parse_tier_entry(_tier_map(resolved_config).get(tier))
    if parsed is not None:
        mapped_provider, mapped_model = parsed
        requested = mapped_provider or active_provider
        target_model = mapped_model
        applied = True
        note = f"roles.model_tiers[{tier!r}]"
    elif tier in _ACTIVE_TIERS:
        applied = True
        note = f"{tier or 'empty'} tier uses the active model"
    else:
        note = (f"no roles.model_tiers entry for tier {tier!r}; "
                "using the active model")

    if provider_override or model_override:
        requested = provider_override or requested
        target_model = model_override or target_model
        applied = True
        note = "CLI --provider/--model override"

    return {
        "requested": requested,
        "target_model": target_model,
        "applied": applied,
        "note": note,
    }


def build_turn_request(role: AgentRole, prompt: str = "", *,
                       json_mode: bool = False) -> TurnRequest:
    """A single turn carrying the role's posture, nothing else."""
    system = str(role.system_prompt or "")
    if json_mode:
        system = f"{system}{_json_instruction(True)}".strip()
    return TurnRequest(
        prompt=str(prompt or "").strip() or _DEFAULT_PROMPT,
        session_id=f"role-{role.id}-{uuid.uuid4().hex[:12]}",
        system=system or None,
    )


def _native_role_agent(role: AgentRole, request: TurnRequest,
                       capture: dict[str, Any] | None = None, *,
                       selection: dict[str, Any] | None = None,
                       config: Any = None,
                       model_override: str | None = None,
                       provider_override: str | None = None) -> Any:
    """Native factory: the same AIAgent path, with the role's toolsets.

    ``enabled_toolsets`` is passed explicitly when the role declares a set;
    an empty tuple means "use the runtime default" rather than silently
    disabling every tool. ``selection`` is the resolved tier (see
    ``resolve_model_selection``); resolving here keeps direct callers working.
    """
    from qubit_cli.runtime_provider import resolve_runtime_provider
    from run_agent import AIAgent

    if config is None:
        config = _load_tier_config(None)
    if selection is None:
        selection = resolve_model_selection(
            role, model_override=model_override,
            provider_override=provider_override, config=config)
    requested = selection.get("requested")
    target_model = selection.get("target_model")
    runtime = resolve_runtime_provider(requested=requested, target_model=target_model)
    agent = AIAgent(
        api_key=runtime.get("api_key"),
        base_url=runtime.get("base_url"),
        provider=runtime.get("provider"),
        requested_provider=runtime.get("requested_provider"),
        api_mode=runtime.get("api_mode"),
        model=target_model or runtime.get("model"),
        enabled_toolsets=list(role.toolsets) or None,
        quiet_mode=True,
        platform="cli",
        skip_context_files=True,
        skip_memory=True,
    )
    if capture is not None:
        capture["agent"] = agent
    return agent


def _event_field(events: list[TurnEvent], key: str) -> Any:
    for event in reversed(events):
        data = event.data if isinstance(event.data, dict) else {}
        if data.get(key) not in (None, ""):
            return data[key]
        usage = data.get("usage")
        if isinstance(usage, dict) and usage.get(key) not in (None, ""):
            return usage[key]
    return None


def _agent_field(agent: Any, key: str) -> Any:
    if agent is None:
        return None
    value = getattr(agent, key, None)
    return value if value not in (None, "") else None


def _base_result(role: AgentRole, engine: str, *, elapsed_ms: float = 0.0,
                 json_mode: bool = False,
                 selection: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "role": role.id,
        "engine": engine,
        "text": "",
        "model": None,
        "provider": None,
        "elapsed_ms": round(max(0.0, elapsed_ms), 3),
        "json_mode": bool(json_mode),
        "model_tier": role.model_tier,
        "model_tier_applied": bool(selection.get("applied")) if selection else False,
        "model_tier_note": str(selection.get("note") or "") if selection else "",
    }


def _refusal(role: AgentRole, engine: str, code: str, message: str,
             hint: str = "", *, elapsed_ms: float = 0.0,
             json_mode: bool = False,
             selection: dict[str, Any] | None = None) -> dict[str, Any]:
    result = _base_result(role, engine, elapsed_ms=elapsed_ms,
                          json_mode=json_mode, selection=selection)
    result.update({
        "refusal": code,
        "hint": hint,
        "detail": message,
        "terminal": "error",
        "cancelled": False,
        "failed": False,
    })
    return result


def _run_recipe_role(role: AgentRole, *, engine: str, cfg: Any,
                     json_mode: bool, recipe_registry: Any,
                     recipe_runner: Callable[..., Any]) -> dict[str, Any]:
    from recipes.registry import build_recipe_registry
    from recipes.runner import run_recipe

    registry = recipe_registry or build_recipe_registry()
    spec = registry.get(role.recipe_id)
    runner = recipe_runner or run_recipe
    started = time.perf_counter()
    run = runner(spec, engine=engine)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    result = _base_result(role, engine, elapsed_ms=elapsed_ms,
                          json_mode=json_mode)
    if role.recipe_id:
        result["model_tier_applied"] = False
        result["model_tier_note"] = (
            "recipe role: the recipe runner selects its own model")
    result.update({
        "recipe": role.recipe_id,
        "text": str(getattr(run, "brief", "") or ""),
        "terminal": str(getattr(run, "terminal", "") or ""),
        "refusal": str(getattr(run, "refusal", "") or ""),
        "hint": str(getattr(run, "hint", "") or ""),
        "failed": bool(getattr(run, "failed", False)),
        "cancelled": bool(getattr(run, "cancelled", False)),
        "sections": [
            section.as_dict() for section in getattr(run, "sections", ())
        ],
    })
    events = tuple(getattr(run, "events", ()))
    result["model"] = _event_field(list(events), "model")
    result["provider"] = _event_field(list(events), "provider")
    result["ok"] = (
        not result["refusal"] and not result["failed"]
        and not result["cancelled"] and bool(result["text"].strip()))
    return result


def run_role(
    role_id: str,
    prompt: str = "",
    *,
    engine: str = "native",
    cfg: Any = None,
    config: Any = None,
    model: str | None = None,
    provider: str | None = None,
    json_mode: bool = False,
    registry: Any | None = None,
    agent_factory: Callable[[TurnRequest], Any] | None = None,
    recipe_registry: Any | None = None,
    recipe_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run one role and return a JSON-safe result.

    ``UnknownRoleError`` / ``UnknownRuntimeError`` are typed refusals for an
    unknown role or engine (the CLI maps them to exit 2). A registered
    non-native engine that does not advertise role support refuses with
    ``role_engine_unsupported`` before any turn is attempted.

    ``model``/``provider`` are explicit overrides from ``qubit agent run``;
    they win over the ``roles.model_tiers`` map. ``config`` is an optional
    already-loaded config (tests); the runner loads it otherwise.
    """
    role = get_role(role_id)
    engine_name = str(engine or "native").strip() or "native"

    if role.recipe_id:
        return _run_recipe_role(
            role, engine=engine_name, cfg=cfg, json_mode=json_mode,
            recipe_registry=recipe_registry, recipe_runner=recipe_runner)

    selection = resolve_model_selection(
        role, model_override=model, provider_override=provider,
        config=config)
    if engine_name != "native":
        selection = {
            "requested": None,
            "target_model": None,
            "applied": False,
            "note": (f"engine {engine_name!r} does not use the role tier map"),
        }

    capture: dict[str, Any] = {}
    factory = agent_factory or (
        lambda request: _native_role_agent(
            role, request, capture, selection=selection, config=config))
    if registry is None and engine_name == "native":
        # Avoid importing the optional out-of-process engines on the native
        # path: a role never needs them, and this keeps the role path free of
        # any engine probe or subprocess code.
        from agent.runtime_native import NativeRuntime

        backend = NativeRuntime(factory, stream=True)
    else:
        if registry is None:
            from agent.runtime_registry import build_registry

            registry = build_registry(factory, native_stream=True)
        backend = registry.get(engine_name)
    if engine_name != "native" and not bool(
            getattr(backend, "supports_roles", False)):
        return _refusal(
            role, engine_name, "role_engine_unsupported",
            f"Engine {engine_name!r} cannot honour a role's system prompt and "
            "toolsets.",
            "Use --engine native (the role contract is owned by the native "
            "turn path).", selection=selection)

    request = build_turn_request(role, prompt, json_mode=json_mode)
    started = time.perf_counter()
    events: list[TurnEvent] = []
    for event in backend.run_turn(request):
        events.append(event)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    agent = capture.get("agent")

    terminal = events[-1] if events else None
    result = _base_result(role, engine_name, elapsed_ms=elapsed_ms,
                          json_mode=json_mode, selection=selection)
    result["model"] = _agent_field(agent, "model") or _event_field(events, "model")
    result["provider"] = (
        _agent_field(agent, "provider") or _event_field(events, "provider"))
    result["events"] = [
        {"kind": event.kind, "text": event.text, "data": event.data}
        for event in events
    ]
    if terminal is None or terminal.kind not in ("done", "error"):
        result.update({"terminal": "", "failed": True})
        return result
    if terminal.kind == "error":
        data = terminal.data if isinstance(terminal.data, dict) else {}
        refusal = str(data.get("refusal") or "")
        cancelled = bool(data.get("cancelled")) or bool(data.get("interrupted"))
        result.update({
            "terminal": "error",
            "refusal": refusal,
            "hint": str(data.get("hint") or ""),
            "cancelled": cancelled,
            "failed": not refusal and not cancelled,
        })
        return result

    text = str(terminal.text or "")
    result.update({
        "terminal": "done",
        "text": text,
        "refusal": "",
        "failed": False,
        "cancelled": False,
        "ok": bool(text.strip()),
    })
    return result


__all__ = ["build_turn_request", "resolve_model_selection", "run_role"]
