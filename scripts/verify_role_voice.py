#!/usr/bin/env python3
"""Verify Qubit's voice bridges for the eight native agent roles.

The check is deterministic and offline by default: it installs the additive
role bridges into ``voicebridge.voice_commands`` and resolves every alias each
role declares. For each alias it reports the owning entry, its backend, its
fixed arguments, whether the owner is the role bridge or an existing recipe
bridge, and whether the owner requires confirmation.

The audit exits 0 only when every required role is reachable by text/voice and
no alias has two owners. Duplicate, unresolved and conflicting aliases are
reported as gaps with exit 1. Nothing here calls a model or the network unless
``--execute ROLE --prompt TEXT`` is passed explicitly, which then calls the
real ``roles.runner.run_role`` once.

Usage:
    .venv/bin/python scripts/verify_role_voice.py
    .venv/bin/python scripts/verify_role_voice.py --json
    .venv/bin/python scripts/verify_role_voice.py --execute simple --prompt "Reply with OK"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

REQUIRED_ROLES: tuple[str, ...] = (
    "orchestrator",
    "react",
    "operative",
    "openhands",
    "simple",
    "researcher",
    "digest",
    "monitor",
)

MUTATING_ROLES: frozenset[str] = frozenset(
    {"orchestrator", "react", "operative", "openhands"})

RECIPE_BRIDGES: dict[str, str] = {
    "recipe_morning_digest": "morning_digest",
    "recipe_monitor_operative": "monitor_operative",
}


def _ensure_path() -> None:
    root = Path(__file__).resolve().parents[1]
    for path in (str(root), str(root / "qubit_runtime")):
        if path not in sys.path:
            sys.path.insert(0, path)


def _load_voice_commands():
    _ensure_path()
    from voicebridge import voice_commands

    return voice_commands


def _installed_owners(voice_commands) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for registry in (
        getattr(voice_commands, "REGISTRY", {}),
        getattr(voice_commands, "PLUGIN_FLEET_REGISTRY", {}),
        getattr(voice_commands, "MODEL_PROFILE_REGISTRY", {}),
        getattr(voice_commands, "CLI_BRIDGE_REGISTRY", {}),
    ):
        for entry_id, entry in registry.items():
            for alias in entry.aliases:
                owners.setdefault(alias, []).append(entry_id)
    return owners


def _fixed_map(entry) -> dict[str, str]:
    return {str(key): str(value) for key, value in (entry.fixed or ())}


def _classify_alias(voice_commands, role_id: str, role_alias: str,
                    owners: dict[str, list[str]]) -> dict[str, Any]:
    resolution = voice_commands.resolve(role_alias)
    record: dict[str, Any] = {
        "alias": role_alias,
        "resolution": resolution.kind,
        "owner": None,
        "owner_kind": "none",
        "backend": "",
        "fixed": [],
        "confirm": False,
        "recipe": "",
        "problem": "",
    }
    entry = resolution.entry
    if resolution.kind not in (voice_commands.MATCH, voice_commands.CLARIFY) \
            or entry is None:
        record["problem"] = f"unresolved ({resolution.kind})"
        return record

    fixed = _fixed_map(entry)
    record.update({
        "owner": entry.id,
        "backend": entry.backend,
        "fixed": sorted(fixed.items()),
        "confirm": bool(entry.confirm),
        "recipe": fixed.get("recipe", ""),
    })

    own_entry_id = f"role_{role_id}"
    if (entry.backend == "agent_role" and entry.id == own_entry_id
            and fixed.get("role") == role_id):
        record["owner_kind"] = "role"
    elif entry.backend == "recipe":
        record["owner_kind"] = "recipe"
        expected = RECIPE_BRIDGES.get(entry.id, "")
        if expected and record["recipe"] != expected:
            record["problem"] = (
                f"recipe mismatch: {entry.id} runs "
                f"{record['recipe']!r}, not {expected!r}")
    elif entry.backend == "agent_role":
        record["owner_kind"] = "other"
        record["problem"] = (
            f"conflict: alias is owned by another role bridge "
            f"({entry.id} -> role {fixed.get('role')!r})")
    else:
        record["owner_kind"] = "other"
        record["problem"] = (
            f"conflict: alias is owned by non-role entry {entry.id}")

    if len(owners.get(role_alias, ())) > 1:
        record["problem"] = (
            f"duplicate: alias has {len(owners[role_alias])} owners "
            f"({', '.join(sorted(owners[role_alias]))})")
    return record


def _role_recipe_entry(voice_commands, recipe_id: str):
    if not recipe_id:
        return None
    for entry in voice_commands.CLI_BRIDGE_REGISTRY.values():
        if entry.backend != "recipe":
            continue
        if _fixed_map(entry).get("recipe") == recipe_id:
            return entry
    return None


def _audit_role(voice_commands, role, owners: dict[str, list[str]]) -> dict[str, Any]:
    aliases = [
        _classify_alias(voice_commands, role.id, alias, owners)
        for alias in role.voice_aliases
    ]
    reachable = any(
        alias["owner_kind"] in ("role", "recipe") and not alias["problem"]
        for alias in aliases
    )
    mutating = role.id in MUTATING_ROLES
    expects_confirmation = role.confirmation != "auto"
    own_entry = voice_commands.CLI_BRIDGE_REGISTRY.get(f"role_{role.id}")
    recipe_entry = _role_recipe_entry(voice_commands, role.recipe_id)

    problems: list[str] = []
    for alias in aliases:
        if alias["problem"]:
            problems.append(f"alias {alias['alias']!r}: {alias['problem']}")
    if not reachable:
        problems.append("role has no reachable text/voice alias")

    if mutating and role.confirmation == "auto":
        problems.append("mutating role must not use confirmation='auto'")
    if not mutating and role.confirmation != "auto":
        problems.append(
            f"non-mutating role must use confirmation='auto' "
            f"(found {role.confirmation!r})")

    confirmation_ok = True
    if own_entry is not None:
        if bool(own_entry.confirm) != expects_confirmation:
            confirmation_ok = False
            problems.append(
                f"role bridge confirmation is {bool(own_entry.confirm)}, "
                f"expected {expects_confirmation}")
    elif not role.recipe_id:
        confirmation_ok = False
        problems.append("no role bridge is installed")

    if role.recipe_id:
        if recipe_entry is None:
            confirmation_ok = False
            problems.append(
                f"no recipe bridge runs {role.recipe_id!r}")
        elif recipe_entry.confirm:
            confirmation_ok = False
            problems.append(
                f"recipe bridge {recipe_entry.id} requires confirmation, "
                "expected read-only auto")

    return {
        "id": role.id,
        "title": role.title,
        "confirmation": role.confirmation,
        "mutating": mutating,
        "expects_confirmation": expects_confirmation,
        "confirmation_ok": confirmation_ok,
        "recipe_id": role.recipe_id,
        "role_entry": own_entry.id if own_entry is not None else None,
        "recipe_entry": recipe_entry.id if recipe_entry is not None else None,
        "reachable": reachable,
        "aliases": aliases,
        "problems": problems,
    }


def audit(voice_commands=None, roles: Iterable[Any] | None = None) -> dict[str, Any]:
    """Run the offline voice verification and return a JSON-safe report."""
    if voice_commands is None:
        voice_commands = _load_voice_commands()
    if roles is None:
        from roles.registry import list_roles

        roles = list_roles()
    roles = list(roles)

    from voicebridge import role_bridges

    install_error = ""
    try:
        role_bridges.install(voice_commands, roles=roles)
    except Exception as exc:  # a broken bridge is a reported gap, not a crash
        install_error = (
            f"role bridge install failed: {type(exc).__name__}: {exc}")

    owners = _installed_owners(voice_commands)
    records = [_audit_role(voice_commands, role, owners) for role in roles]
    records.sort(key=lambda row: row["id"])

    found_ids = {row["id"] for row in records}
    missing = [role_id for role_id in REQUIRED_ROLES
               if role_id not in found_ids]
    duplicates = {
        alias: sorted(entry_ids)
        for alias, entry_ids in sorted(owners.items())
        if len(entry_ids) > 1
    }

    gaps: list[str] = []
    if install_error:
        gaps.append(install_error)
    for role_id in missing:
        gaps.append(f"missing required role: {role_id}")
    for row in records:
        gaps.extend(f"role {row['id']}: {problem}" for problem in row["problems"])
    for alias, entry_ids in duplicates.items():
        gaps.append(
            f"duplicate alias {alias!r}: {', '.join(entry_ids)}")

    gaps = list(dict.fromkeys(gaps))
    return {
        "required": list(REQUIRED_ROLES),
        "mutating_roles": sorted(MUTATING_ROLES),
        "found": sorted(found_ids),
        "covered": f"{len(found_ids & set(REQUIRED_ROLES))}/{len(REQUIRED_ROLES)}",
        "missing": missing,
        "duplicate_aliases": duplicates,
        "roles": records,
        "gaps": gaps,
        "verdict": "CLEAN" if not gaps else "GAPS",
        "exit_code": 0 if not gaps else 1,
    }


def execute_role(role_id: str, prompt: str = "") -> dict[str, Any]:
    """Run one role through the real runner (explicit opt-in only)."""
    _ensure_path()
    from roles.runner import run_role

    return run_role(role_id, prompt, engine="native")


def _print_report(report: dict[str, Any]) -> None:
    headers = ("ROLE", "MUTATING", "CONFIRM", "REACHABLE", "BRIDGE")
    rows = []
    for role in report["roles"]:
        rows.append((
            role["id"],
            "yes" if role["mutating"] else "no",
            "yes" if role["expects_confirmation"] else "no",
            "ok" if role["reachable"] else "GAP",
            role["role_entry"] or role["recipe_entry"] or "-",
        ))
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    print("  ".join(header.ljust(widths[index])
                    for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[index])
                        for index, cell in enumerate(row)))
    print()
    for role in report["roles"]:
        print(f"{role['id']}:")
        for alias in role["aliases"]:
            owner = alias["owner"] or "-"
            fixed = " ".join(f"{k}={v}" for k, v in alias["fixed"]) or "-"
            state = alias["problem"] or "ok"
            print(f"  {alias['alias']!r} -> {owner} "
                  f"[{alias['owner_kind']}/{alias['backend'] or '-'}] "
                  f"confirm={alias['confirm']} fixed={fixed} {state}")
    print()
    print(f"covered:  {report['covered']}")
    print(f"missing:  {', '.join(report['missing']) or '-'}")
    duplicates = ", ".join(report["duplicate_aliases"]) or "-"
    print(f"duplicate aliases: {duplicates}")
    if report["gaps"]:
        print("gaps:")
        for gap in report["gaps"]:
            print(f"  - {gap}")
    print(f"verdict:  {report['verdict']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify voice reachability for Qubit's eight agent roles "
                    "(offline unless --execute is passed).")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON")
    parser.add_argument("--execute", metavar="ROLE",
                        help="explicitly run this role through the real runner")
    parser.add_argument("--prompt", default="",
                        help="prompt for --execute (default: the role default)")
    args = parser.parse_args(argv)

    if args.execute:
        try:
            result = execute_role(args.execute, args.prompt)
        except Exception as exc:  # typed refusals share the CLI exit mapping
            print(f"[{type(exc).__name__}] {exc}", file=sys.stderr)
            return 1
        ok = bool(result.get("ok"))
        if args.json:
            print(json.dumps({
                "role": args.execute,
                "ok": ok,
                "refusal": result.get("refusal", ""),
                "result": result,
            }, indent=2, sort_keys=True))
        else:
            print(f"role:    {args.execute}")
            print(f"state:   {'ok' if ok else 'failed'}")
            if result.get("refusal"):
                print(f"refusal: {result['refusal']}")
            if result.get("text"):
                print(f"text:    {result['text']}")
        return 0 if ok else 1

    report = audit()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_report(report)
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
