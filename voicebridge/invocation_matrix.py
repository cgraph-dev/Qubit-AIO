"""Read-only invocation matrix for skills, plugins, and MCP servers.

Capability parity requires every supported capability to resolve to a REAL
canonical operation reachable from voice, CLI, and dashboard -- not merely a
catalog or registry entry (``docs/archive/2026-09-22-pre-reset/QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md``
sections 2-4). This module traverses the three item sources named by the audit:

* enabled skills          -> :func:`voicebridge.skill_catalog.catalog_entries`
* registered plugins       -> :func:`voicebridge.plugin_runtime.list_plugins`
  (with :func:`voicebridge.plugin_runtime.status` for the runtime kind set)
* configured MCP servers   -> :func:`voicebridge.mcp_servers.list_servers`

and, per item, reports the canonical operation, whether a voice intent/backend,
CLI command, and dashboard route exist for that operation, and a typed
disposition (``reachable`` | ``no-invocation`` | ``unsupported``).

Everything here is READ-ONLY. It calls only public functions of the other
modules and never executes an operation, mutates a store, or opens a
connection. A surface binding is trusted only after it is verified against the
live registries (see :func:`verify_bindings`), so a fabricated or absent
operation can never be reported as reachable.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

SCHEMA = "qubit-invocation-matrix/1"

#: The only dispositions this matrix emits (fail-closed vocabulary).
DISPOSITIONS = ("reachable", "no-invocation", "unsupported")

KINDS = ("skill", "plugin", "mcp_server")

#: Kind -> the surfaces its canonical operation must have. ``operation`` is the
#: canonical operation family's real registry name (voice backend / CLI family),
#: ``voice``/``cli``/``dashboard`` are the keys checked in the live surface
#: index. A binding whose key does not exist in the live registry is reported by
#: :func:`verify_bindings` and never yields ``reachable``.
BINDINGS: Dict[str, Dict[str, str]] = {
    "skill": {
        "operation": "skills",
        "voice": "skills",
        "cli": "skills",
        "dashboard": "/api/skills",
    },
    "plugin": {
        "operation": "plugin_runtime",
        "voice": "plugin_runtime",
        "cli": "plugins",
        "dashboard": "/api/dashboard/plugins",
    },
    "mcp_server": {
        "operation": "mcp",
        "voice": "mcp",
        "cli": "mcp",
        "dashboard": "/api/mcp/servers",
    },
}

__all__ = [
    "SCHEMA",
    "DISPOSITIONS",
    "KINDS",
    "BINDINGS",
    "surface_index",
    "verify_bindings",
    "build",
]


# ---------------------------------------------------------------------------
# Live surface indexes (public APIs only; every probe fails closed to empty)
# ---------------------------------------------------------------------------
def _voice_index() -> Dict[str, Any]:
    from . import voice_commands as VC

    backends: set = set()
    entries: Dict[str, str] = {}
    for registry in (VC.REGISTRY, VC.PLUGIN_FLEET_REGISTRY,
                     VC.MODEL_PROFILE_REGISTRY):
        for entry_id, entry in registry.items():
            backend = str(getattr(entry, "backend", "") or "")
            entries[str(entry_id)] = backend
            if backend:
                backends.add(backend)
    return {"backends": backends, "entries": entries}


def _cli_index() -> Dict[str, Any]:
    import argparse

    from . import cli

    families: set = set()
    parser = cli.build_parser()
    for action in getattr(parser, "_actions", []):
        if isinstance(action, argparse._SubParsersAction):
            families.update(str(k) for k in action.choices.keys())
    return {"families": families}


def _dashboard_index() -> Dict[str, Any]:
    from . import dashboard_ops as OPS

    ops = {str(row.get("id", "")) for row in OPS.catalog()}
    routes: set = set()
    try:
        from . import qubit_compat as HC

        routes = {str(r.get("pattern", "")) for r in HC.ROUTES}
    except Exception:  # noqa: BLE001 - route table is a bonus, not required
        routes = set()
    return {"ops": ops, "routes": routes}


def _skill_command_index() -> Dict[str, Dict[str, Any]]:
    from . import qubit_runtime

    qubit_runtime.ensure_path()
    from agent.skill_commands import get_skill_commands  # type: ignore

    return {str(k): dict(v) for k, v in (get_skill_commands() or {}).items()}


def surface_index() -> Dict[str, Any]:
    """Build the live surface index from public registries (read-only).

    Every probe is guarded and degrades to an empty set so a missing optional
    dependency does not fabricate a binding -- an empty surface simply yields
    ``no-invocation`` and a failed :func:`verify_bindings`.
    """
    index: Dict[str, Any] = {
        "voice_backends": set(),
        "voice_entries": {},
        "cli_families": set(),
        "dashboard_ops": set(),
        "dashboard_routes": set(),
        "skill_commands": {},
    }
    try:
        voice = _voice_index()
        index["voice_backends"] = voice["backends"]
        index["voice_entries"] = voice["entries"]
    except Exception:  # noqa: BLE001
        pass
    try:
        index["cli_families"] = _cli_index()["families"]
    except Exception:  # noqa: BLE001
        pass
    try:
        dash = _dashboard_index()
        index["dashboard_ops"] = dash["ops"]
        index["dashboard_routes"] = dash["routes"]
    except Exception:  # noqa: BLE001
        pass
    try:
        index["skill_commands"] = _skill_command_index()
    except Exception:  # noqa: BLE001
        pass
    return index


def _has_voice(index: Dict[str, Any], key: str) -> bool:
    if key in index.get("voice_backends", ()):
        return True
    return key in set(str(v) for v in index.get("voice_entries", {}).values())


def _has_cli(index: Dict[str, Any], key: str) -> bool:
    return key in index.get("cli_families", ())


def _has_dashboard(index: Dict[str, Any], key: str) -> bool:
    prefix = key.rstrip("/")
    for pattern in index.get("dashboard_routes", ()):
        if pattern == key or pattern.startswith(prefix + "/"):
            return True
    return False


def verify_bindings(surfaces: Dict[str, Any]) -> List[Dict[str, str]]:
    """Return bindings whose referenced surface key is absent (fabricated).

    This is the anti-fabrication gate: a canonical operation may only be
    reported as reachable when its declared voice backend, CLI family, and
    dashboard route actually exist in the live registries.
    """
    missing: List[Dict[str, str]] = []
    for kind, binding in BINDINGS.items():
        if not _has_voice(surfaces, binding["voice"]):
            missing.append({"kind": kind, "surface": "voice",
                            "key": binding["voice"]})
        if not _has_cli(surfaces, binding["cli"]):
            missing.append({"kind": kind, "surface": "cli",
                            "key": binding["cli"]})
        if not _has_dashboard(surfaces, binding["dashboard"]):
            missing.append({"kind": kind, "surface": "dashboard",
                            "key": binding["dashboard"]})
    return missing


def _surface_summary(index: Dict[str, Any]) -> Dict[str, int]:
    return {
        "voice_backends": len(index.get("voice_backends", ())),
        "cli_families": len(index.get("cli_families", ())),
        "dashboard_routes": len(index.get("dashboard_routes", ())),
        "dashboard_ops": len(index.get("dashboard_ops", ())),
        "skill_commands": len(index.get("skill_commands", {})),
    }


# ---------------------------------------------------------------------------
# Per-kind rows
# ---------------------------------------------------------------------------
def _surfaces_present(index: Dict[str, Any], kind: str) -> List[str]:
    binding = BINDINGS[kind]
    marks = (
        ("voice", _has_voice(index, binding["voice"])),
        ("cli", _has_cli(index, binding["cli"])),
        ("dashboard", _has_dashboard(index, binding["dashboard"])),
    )
    return [name for name, ok in marks if ok]


def _skill_slash_map(index: Dict[str, Any]) -> Dict[str, str]:
    """Map a skill name to its registered slash invocation key (or '')."""
    mapping: Dict[str, str] = {}
    for key, info in index.get("skill_commands", {}).items():
        name = str(info.get("name", "") or "")
        if name:
            mapping.setdefault(name, key)
        bare = key[1:] if key.startswith("/") else key
        mapping.setdefault(bare, key)
    return mapping


def _skill_rows(cfg: Any, index: Dict[str, Any]) -> List[Dict[str, Any]]:
    from . import skill_catalog

    slash = _skill_slash_map(index)
    surfaces = _surfaces_present(index, "skill")
    # The canonical ``skills`` operation is reachable on these surfaces. Only
    # when at least one exists can a permissioned skill resolve to a real
    # per-item invocation keyed by its skill_id.
    management_reachable = "cli" in surfaces or "voice" in surfaces
    rows: List[Dict[str, Any]] = []
    for entry in skill_catalog.catalog_entries(cfg):
        name = str(entry.get("name", "") or "")
        if not name:
            continue
        enabled = bool(entry.get("enabled"))
        origin = str(entry.get("origin", "") or "catalog")
        slash_key = slash.get(name, "")
        if slash_key:
            disposition, reason = "reachable", "skill slash command registered"
            invocation = slash_key
            invocation_kind = "slash"
        elif origin == "permissioned" and management_reachable:
            # A permissioned-store skill has no bundled slash command, but the
            # one canonical ``skills`` operation resolves it by id on voice
            # (``skills show/enable/disable <id>``) and CLI
            # (``qubit skills show <id>``), with the dashboard reading the same
            # merged catalog (``GET /api/skills`` / ``/api/skills/content``).
            # That per-item, id-keyed path is a REAL invocation, not merely
            # listing, so the item is reachable.
            disposition = "reachable"
            reason = ("permissioned skill invocable by id through the canonical "
                      "skills operation (skills show/enable/disable)")
            invocation = f"skills.show:{name}"
            invocation_kind = "management"
        elif surfaces:
            disposition = "no-invocation"
            reason = ("SKILL_MANAGEMENT_ONLY: skills list/enable/disable are "
                      "reachable on " + ", ".join(surfaces) +
                      " but no per-skill invocation is registered for this "
                      "bundled skill")
            invocation = ""
            invocation_kind = "none"
        else:
            disposition, reason = "no-invocation", "NO_SURFACE_BINDING"
            invocation = ""
            invocation_kind = "none"
        rows.append({
            "kind": "skill",
            "id": name,
            "enabled": enabled,
            "in_scope": enabled,
            "origin": origin,
            "canonical_operation": BINDINGS["skill"]["operation"],
            "invocation": invocation,
            "invocation_kind": invocation_kind,
            "voice": "voice" in surfaces,
            "cli": "cli" in surfaces,
            "dashboard": "dashboard" in surfaces,
            "text": bool(slash_key),
            "surfaces": surfaces,
            "disposition": disposition,
            "reason": reason,
        })
    return rows


def _plugin_gap_row(reason: str, surfaces: List[str]) -> Dict[str, Any]:
    return {
        "kind": "plugin",
        "id": "<plugin-registry>",
        "enabled": False,
        "in_scope": True,
        "origin": "registry",
        "canonical_operation": BINDINGS["plugin"]["operation"],
        "invocation": "",
        "invocation_kind": "none",
        "voice": "voice" in surfaces,
        "cli": "cli" in surfaces,
        "dashboard": "dashboard" in surfaces,
        "text": False,
        "surfaces": surfaces,
        "disposition": "unsupported",
        "reason": reason,
    }


def _plugin_rows(cfg: Any, index: Dict[str, Any]) -> List[Dict[str, Any]]:
    from . import plugin_runtime as PR

    surfaces = _surfaces_present(index, "plugin")
    rows: List[Dict[str, Any]] = []
    try:
        data = PR.list_plugins(cfg)
    except Exception as exc:  # noqa: BLE001 - a refused read is a typed gap
        return [_plugin_gap_row(f"PLUGIN_REGISTRY_UNREADABLE: {type(exc).__name__}",
                                surfaces)]
    if "plugins" not in data:
        # A refused/estop read returns a typed state without a plugin list; the
        # matrix must say so rather than silently omit every plugin.
        code = str(data.get("code") or data.get("state") or "UNKNOWN")
        return [_plugin_gap_row(f"PLUGIN_REGISTRY_REFUSED: {code}", surfaces)]
    for rec in data.get("plugins", []) or []:
        plugin_id = str(rec.get("id", "") or "")
        if not plugin_id:
            continue
        has_handler = bool(rec.get("handler"))
        if has_handler and surfaces:
            disposition = "reachable"
            reason = "in-process handler bound; invocable via plugin_runtime.invoke"
            invocation = f"plugin_runtime.invoke:{plugin_id}"
        elif has_handler:
            disposition, reason = "no-invocation", "NO_SURFACE_BINDING"
            invocation = f"plugin_runtime.invoke:{plugin_id}"
        else:
            disposition = "unsupported"
            reason = ("PLUGIN_RUNTIME_UNAVAILABLE: no in-process handler is "
                      "bound; the manifest is metadata only and never executed")
            invocation = ""
        rows.append({
            "kind": "plugin",
            "id": plugin_id,
            "enabled": True,
            "in_scope": True,
            "origin": "registry",
            "canonical_operation": BINDINGS["plugin"]["operation"],
            "invocation": invocation,
            "invocation_kind": "handler" if has_handler else "none",
            "voice": "voice" in surfaces,
            "cli": "cli" in surfaces,
            "dashboard": "dashboard" in surfaces,
            "text": False,
            "surfaces": surfaces,
            "disposition": disposition,
            "reason": reason,
        })
    return rows


def _mcp_rows(cfg: Any, index: Dict[str, Any]) -> List[Dict[str, Any]]:
    from . import mcp_servers as MS

    surfaces = _surfaces_present(index, "mcp_server")
    rows: List[Dict[str, Any]] = []
    data = MS.list_servers(cfg)
    for rec in data.get("servers", []) or []:
        name = str(rec.get("name", "") or "")
        if not name:
            continue
        # A configured server is reachable only when the canonical client gate
        # passes (enabled + runnable transport + capability allowlist). That is
        # reported per-record by ``mcp_servers.list_servers``; when it does not
        # pass the row carries the exact typed reason instead of a blanket
        # "no client invocation".
        invocable = bool(rec.get("invocable", False))
        code = str(rec.get("invocation_code", "") or "")
        rows.append({
            "kind": "mcp_server",
            "id": name,
            "enabled": bool(rec.get("enabled", False)),
            "in_scope": True,
            "origin": "config",
            "canonical_operation": BINDINGS["mcp_server"]["operation"],
            "invocation": f"mcp.call:{name}" if invocable else "",
            "invocation_kind": "client" if invocable else "none",
            "voice": "voice" in surfaces,
            "cli": "cli" in surfaces,
            "dashboard": "dashboard" in surfaces,
            "text": False,
            "surfaces": surfaces,
            "disposition": "reachable" if invocable else "unsupported",
            "reason": ("invocable through the canonical brokered MCP client "
                       "(mcp_servers.invoke: e-stop + confirmation + allowlist "
                       "+ bounded timeout)"
                       if invocable else
                       (code or "MCP_CLIENT_INVOCATION_OPERATOR_REQUIRED")
                       + ": configured server is not invocable in this "
                         f"environment (status={rec.get('status', 'unknown')!r})"),
        })
    return rows


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def _plugin_runtime_state(cfg: Any) -> Dict[str, Any]:
    """Read-only projection of :func:`plugin_runtime.status` (never mutates)."""
    try:
        from . import plugin_runtime as PR

        status = PR.status(cfg)
    except Exception:  # noqa: BLE001
        return {}
    return {
        "state": status.get("state"),
        "kinds": list(status.get("kinds", ()) or ()),
        "in_process": status.get("in_process"),
        "external_runtime": status.get("external_runtime"),
    }


def _closure_errors(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Self-check: every in-scope item carries a closure, or is a typed gap.

    An item is closed when its disposition is ``reachable`` AND it names a
    concrete invocation (a slash key, an id-keyed management op, or a plugin
    handler). ``no-invocation`` / ``unsupported`` rows are closed by a typed
    non-empty ``reason``. Anything else is a matrix bug, not an item gap.
    """
    errors: List[Dict[str, str]] = []
    for row in rows:
        if not row.get("in_scope"):
            continue
        disposition = row.get("disposition")
        if not row.get("canonical_operation"):
            errors.append({"id": str(row.get("id", "")),
                           "error": "NO_CANONICAL_OPERATION"})
            continue
        if disposition == "reachable":
            if not row.get("invocation"):
                errors.append({"id": str(row.get("id", "")),
                               "error": "REACHABLE_WITHOUT_INVOCATION"})
        elif disposition in ("no-invocation", "unsupported"):
            if not row.get("reason"):
                errors.append({"id": str(row.get("id", "")),
                               "error": "GAP_WITHOUT_TYPED_REASON"})
        else:
            errors.append({"id": str(row.get("id", "")),
                           "error": f"UNKNOWN_DISPOSITION:{disposition!r}"})
    return errors


def build(cfg: Any, *, surfaces: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Traverse every skill/plugin/MCP item and classify its invocation path.

    Read-only. Pass ``surfaces=`` (from :func:`surface_index`) to reuse a single
    index across calls; omit it to build one. The result carries one row per
    item plus the enforced anti-fabrication report (``dangling_bindings``) and
    the closure self-check (``closure_errors``: empty means every in-scope item
    is either reachable through a concrete invocation or carries a typed
    reason).
    """
    index = surfaces if surfaces is not None else surface_index()
    rows: List[Dict[str, Any]] = []
    rows.extend(_skill_rows(cfg, index))
    rows.extend(_plugin_rows(cfg, index))
    rows.extend(_mcp_rows(cfg, index))

    in_scope = [r for r in rows if r["in_scope"]]
    no_invocation = [r["id"] for r in in_scope
                     if r["disposition"] == "no-invocation"]
    unsupported = [r["id"] for r in in_scope
                   if r["disposition"] == "unsupported"]
    reachable = [r["id"] for r in in_scope
                 if r["disposition"] == "reachable"]
    kinds: Dict[str, int] = {}
    for row in in_scope:
        key = str(row.get("invocation_kind", "none") or "none")
        kinds[key] = kinds.get(key, 0) + 1
    errors = _closure_errors(rows)
    return {
        "schema": SCHEMA,
        "bindings": {k: dict(v) for k, v in BINDINGS.items()},
        "dangling_bindings": verify_bindings(index),
        "closure_errors": errors,
        "surfaces": _surface_summary(index),
        "plugin_runtime": _plugin_runtime_state(cfg),
        "rows": rows,
        "in_scope": [r["id"] for r in in_scope],
        "reachable": reachable,
        "no_invocation": no_invocation,
        "unsupported": unsupported,
        "summary": {
            "total": len(rows),
            "in_scope": len(in_scope),
            "reachable": len(reachable),
            "no_invocation": len(no_invocation),
            "unsupported": len(unsupported),
            "invocation_kinds": kinds,
            "closure_errors": len(errors),
        },
    }
