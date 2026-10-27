"""``qubit agent`` handler: list roles and run one role turn."""
from __future__ import annotations

import json
import sys
from typing import Any

from agent.runtime_port import UnknownRuntimeError


def _list_roles(roles=None):
    if roles is not None:
        return list(roles)
    from roles.registry import list_roles

    return list_roles()


def _get_role(role_id: str):
    from roles.registry import get_role

    return get_role(role_id)


def _run(role_id: str, prompt: str, engine: str, **kwargs):
    from roles.runner import run_role

    return run_role(role_id, prompt, engine=engine, **kwargs)


def _print_roles(roles, as_json: bool) -> None:
    rows = [role.to_wire() for role in roles]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False))
        return
    for row in rows:
        aliases = ", ".join(row.get("voice_aliases") or ())
        toolsets = ", ".join(row.get("toolsets") or ()) or "(default)"
        print(f"{row['id']}\t{row['title']}\ttoolsets={toolsets}\t{aliases}")


def _print_run(result: dict[str, Any], role, as_json: bool) -> None:
    if as_json:
        payload = dict(result)
        payload.setdefault("title", getattr(role, "title", result.get("role")))
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return
    text = str(result.get("text") or "")
    if text:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def cmd_agent(
    args,
    *,
    roles=None,
    runner=None,
) -> int:
    action = str(getattr(args, "agent_action", "") or "")

    if action == "roles":
        try:
            listing = _list_roles(roles)
        except Exception as exc:  # noqa: BLE001 - a bad role module is a typed failure
            print(f"agent roles: {exc}", file=sys.stderr)
            return 1
        _print_roles(listing, bool(getattr(args, "json", False)))
        return 0

    role_id = str(getattr(args, "role", "") or "").strip()
    engine = str(getattr(args, "engine", "") or "native").strip() or "native"
    prompt = str(getattr(args, "prompt", "") or "")
    as_json = bool(getattr(args, "json", False))

    from recipes.registry import UnknownRecipeError
    from recipes.runner import RecipeInputError
    from roles.registry import UnknownRoleError

    try:
        role = _get_role(role_id)
    except UnknownRoleError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        result = (runner or _run)(role_id, prompt, engine=engine)
    except (UnknownRoleError, UnknownRuntimeError, UnknownRecipeError,
            RecipeInputError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("agent run: interrupted", file=sys.stderr)
        return 130

    if result.get("cancelled"):
        if as_json:
            _print_run(result, role, True)
        print("agent run: interrupted", file=sys.stderr)
        return 130
    if result.get("refusal"):
        if as_json:
            _print_run(result, role, True)
        else:
            hint = result.get("hint") or result.get("detail") or result["refusal"]
            print(f"[{result['refusal']}] {hint}", file=sys.stderr)
        return 2
    if not result.get("ok"):
        if as_json:
            _print_run(result, role, True)
        else:
            print("agent run: the role turn failed", file=sys.stderr)
        return 1

    _print_run(result, role, as_json)
    return 0


__all__ = ["cmd_agent"]
