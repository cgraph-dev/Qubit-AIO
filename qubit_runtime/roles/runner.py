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


def _json_instruction(json_mode: bool) -> str:
    return _JSON_INSTRUCTION if json_mode else ""


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
                       capture: dict[str, Any] | None = None) -> Any:
    """Native factory: the same AIAgent path, with the role's toolsets.

    ``enabled_toolsets`` is passed explicitly when the role declares a set;
    an empty tuple means "use the runtime default" rather than silently
    disabling every tool.
    """
    from qubit_cli.config import load_config
    from qubit_cli.runtime_provider import resolve_runtime_provider
    from run_agent import AIAgent

    model_cfg = load_config().get("model") or {}
    requested = model_cfg.get("provider")
    target_model = model_cfg.get("default")
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
                 json_mode: bool = False) -> dict[str, Any]:
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
    }


def _refusal(role: AgentRole, engine: str, code: str, message: str,
             hint: str = "", *, elapsed_ms: float = 0.0,
             json_mode: bool = False) -> dict[str, Any]:
    result = _base_result(role, engine, elapsed_ms=elapsed_ms,
                          json_mode=json_mode)
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
    """
    role = get_role(role_id)
    engine_name = str(engine or "native").strip() or "native"

    if role.recipe_id:
        return _run_recipe_role(
            role, engine=engine_name, cfg=cfg, json_mode=json_mode,
            recipe_registry=recipe_registry, recipe_runner=recipe_runner)

    capture: dict[str, Any] = {}
    factory = agent_factory or (
        lambda request: _native_role_agent(role, request, capture))
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
            "turn path).")

    request = build_turn_request(role, prompt, json_mode=json_mode)
    started = time.perf_counter()
    events: list[TurnEvent] = []
    for event in backend.run_turn(request):
        events.append(event)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    agent = capture.get("agent")

    terminal = events[-1] if events else None
    result = _base_result(role, engine_name, elapsed_ms=elapsed_ms,
                          json_mode=json_mode)
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


__all__ = ["build_turn_request", "run_role"]
