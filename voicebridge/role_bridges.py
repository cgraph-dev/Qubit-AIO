"""Additive voice bridges for Qubit-native agent roles.

Every role declares ``voice_aliases``; this module turns each one into a normal
``CLI_BRIDGE_REGISTRY`` entry whose backend calls the real ``roles.runner``.
There is no second router, no confirmation bypass, and no fabricated success:
a role refusal is returned as a typed ``UNAVAILABLE`` result and a failed turn
raises so the voice pipeline reports failure rather than speaking a brief.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Sequence


def _b_agent_role(ctx, args: Dict[str, Any]):
    """Run one role through the same runner the CLI uses."""
    from . import qubit_runtime as QR

    QR.ensure_path()
    from roles.registry import UnknownRoleError, get_role
    from roles.runner import run_role

    role_id = str(args.get("role", "") or "").strip()
    prompt = str(args.get("prompt", "") or "").strip()
    try:
        role = get_role(role_id)
    except UnknownRoleError as exc:
        raise ValueError(str(exc)) from exc

    result = run_role(role.id, prompt, engine="native")
    body = {
        "id": role.id,
        "title": role.title,
        "state": "OK",
        "engine": result.get("engine"),
        "text": str(result.get("text") or ""),
        "model": result.get("model"),
        "provider": result.get("provider"),
        "elapsed_ms": result.get("elapsed_ms"),
    }
    if result.get("refusal"):
        body.update({
            "state": "UNAVAILABLE",
            "code": result["refusal"],
            "detail": result.get("hint") or result.get("detail") or "",
        })
        return {"agent_role": body}
    if result.get("cancelled"):
        raise RuntimeError("the role turn was interrupted")
    if not result.get("ok"):
        raise RuntimeError("the role turn failed")
    return {"agent_role": body}


def _sum_agent_role(result: Dict[str, Any]) -> str:
    body = result.get("agent_role", {}) if isinstance(result, dict) else {}
    if not isinstance(body, dict):
        return "That role returned nothing."
    title = str(body.get("title") or body.get("id") or "That role").strip()
    if body.get("state") == "UNAVAILABLE":
        return f"{title} is unavailable: {body.get('code', 'unavailable')}."
    text = str(body.get("text") or "").strip()
    return text or f"{title} returned no text."


def _registries(vc):
    for name in ("REGISTRY", "PLUGIN_FLEET_REGISTRY",
                 "MODEL_PROFILE_REGISTRY", "CLI_BRIDGE_REGISTRY"):
        yield getattr(vc, name, {})


def _alias_owner(vc, alias: str, entry_id: str) -> str:
    for registry in _registries(vc):
        for other_id, entry in registry.items():
            if other_id != entry_id and alias in entry.aliases:
                return other_id
    return ""


def _install(voice_commands, roles: Sequence[Any]) -> list[str]:
    vc = voice_commands
    vc._BACKENDS.setdefault("agent_role", _b_agent_role)
    vc._SUMMARIES.setdefault("agent_role", _sum_agent_role)

    # A role's aliases must not silently collide with another command (or a
    # previously installed role). The existing owner keeps the alias; the role
    # keeps the remaining declared ones. Recipe aliases already spoken for by
    # the recipe bridges (digest/monitor/deep research) stay where they are.
    installed: list[str] = []
    for role in roles:
        entry_id = f"role_{role.id}"
        aliases = tuple(
            alias for alias in role.voice_aliases
            if not _alias_owner(vc, alias, entry_id))
        if not aliases:
            continue
        examples = tuple(
            f"qubit, {alias}" for alias in aliases
            if not str(alias).startswith("rx:"))[:2]
        vc.CLI_BRIDGE_REGISTRY[entry_id] = vc.VoiceCommand(
            id=entry_id,
            title=role.title,
            aliases=aliases,
            args=(vc._R("prompt", required=False),),
            backend="agent_role",
            fixed=(("role", role.id),),
            scope=("voice",),
            confirm=role.confirmation != "auto",
            summary="agent_role",
            examples=examples,
            parity=(),
            help=(f"Runs the {role.title} role through the native agent loop "
                  f"({role.description}); provenance: {role.source}."),
        )
        installed.append(entry_id)
    return installed


def install(voice_commands=None, roles: Iterable[Any] | None = None) -> list[str]:
    """Register one bridge per declared role alias (idempotent)."""
    if voice_commands is None:
        from . import voice_commands as _vc

        voice_commands = _vc
    if roles is None:
        from roles.registry import list_roles

        roles = list_roles()
    return _install(voice_commands, list(roles))


__all__ = ["install"]
