"""Wave 20 — sessions-store bindings + operator-only CLI-literal guards.

The parity inventory's last tractable cluster is the ``qubit sessions <sub>``
store subcommands: the vendored ``qubit_cli.sessions_cmd`` dispatcher is the
EXACT handler the CLI runs against the one canonical ``state.db``, so voice
binds the non-interactive, deterministic subcommands through that same handler
(``_b_sessions_cli``) instead of re-implementing them. Everything else in the
cluster is an interactive/offline-only shape (browse/import/recover/journal-mode)
and stays a typed refusal naming the counterpart.

This file proves:

* the real sessions intents resolve to ``sessions_cli`` (never a guard) and are
  confirm-gated where they mutate;
* the guard intents resolve to an audited ``voice_unsupported`` refusal carrying
  a real guidance string (never fabricated success);
* ``_b_sessions_cli`` drives the canonical vendored handler (a real read on a
  temp ``QUBIT_HOME``), and refuses an un-routed action instead of guessing;
* no alias collides across registries and no entry references a dead backend.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

try:  # resolve() recompiles ~2k alias regexes; perf tweak only, no result change.
    if getattr(re, "_MAXCACHE", 0) < 20000:
        re._MAXCACHE = 20000
except Exception:  # pragma: no cover
    pass

# intent literal -> (entry id, sessions_action, confirm)
REAL_SESSIONS = {
    "qubit sessions pinned": ("cli_sessions_pinned", "pinned", False),
    "qubit sessions pin": ("cli_sessions_pin", "pin", True),
    "qubit sessions unpin": ("cli_sessions_unpin", "unpin", True),
    "qubit sessions prune": ("cli_sessions_prune", "prune", True),
    "qubit sessions optimize": ("cli_sessions_optimize", "optimize", True),
    "qubit sessions optimize-storage": (
        "cli_sessions_optimize_storage", "optimize-storage", True),
    "qubit sessions clean-markers": (
        "cli_sessions_clean_markers", "clean-markers", True),
    "qubit sessions repair": ("cli_sessions_repair", "repair", True),
    "qubit sessions repair-routing": (
        "cli_sessions_repair_routing", "repair-routing", True),
    "qubit sessions retitle-skills": (
        "cli_sessions_retitle_skills", "retitle-skills", True),
    "qubit sessions repair-profiles": (
        "cli_sessions_repair_profiles", "repair-profiles", True),
}

SESSIONS_GUARDS = (
    "qubit sessions browse",
    "qubit sessions import",
    "qubit sessions recover",
    "qubit sessions set-journal-mode",
)

# Representative operator-only / external clusters guarded with a counterpart.
OPERATOR_GUARDS = (
    "qubit mcp login",
    "qubit egress start",
    "qubit sync push",
    "qubit portal login",
    "qubit auth add",
    "qubit gateway restart",
    "qubit moa list",
    "qubit skills search",
    "qubit kanban tail",
    "qubit cron tick",
    "qubit guard file patch",
    "qubit env reveal",
    "qubit pty",
)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_real_sessions_intents_resolve_to_the_bound_backend():
    from voicebridge import voice_commands as VC

    problems = []
    for text, (eid, action, confirm) in REAL_SESSIONS.items():
        res = VC.resolve(text)
        entry = res.entry
        if entry is None or entry.id != eid:
            problems.append((text, "entry", eid, res.kind,
                             entry.id if entry else None))
            continue
        if entry.backend != "sessions_cli" or "sessions_cli" not in VC._BACKENDS:
            problems.append((text, "backend", entry.backend))
        if dict(entry.fixed).get("sessions_action") != action:
            problems.append((text, "action", dict(entry.fixed)))
        if bool(entry.confirm) != confirm:
            problems.append((text, "confirm", entry.confirm, confirm))
    assert not problems, problems


def test_sessions_and_operator_guards_are_typed_refusals_with_guidance():
    from voicebridge import voice_commands as VC

    problems = []
    for text in (*SESSIONS_GUARDS, *OPERATOR_GUARDS):
        res = VC.resolve(text)
        entry = res.entry
        if entry is None or entry.backend != "voice_unsupported":
            problems.append((text, "not-guard",
                             entry.id if entry else None,
                             entry.backend if entry else None))
            continue
        fixed = dict(entry.fixed)
        if not str(fixed.get("guidance", "")).strip():
            problems.append((text, "no-guidance"))
        if not str(fixed.get("code", "")).strip():
            problems.append((text, "no-code"))
    assert not problems, problems


def test_sessions_cli_backend_drives_the_canonical_handler(tmp_path, monkeypatch):
    """A real read through the vendored ``qubit_cli.sessions_cmd`` dispatcher."""
    from types import SimpleNamespace

    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = SimpleNamespace(cfg=cfg, actor="voice:user", session="voice",
                          source="user")
    out = VC._b_sessions_cli(ctx, {"sessions_action": "pinned"})
    result = out["sessions_cli"]
    assert result["state"] == "OK"
    assert result["action"] == "pinned"
    assert isinstance(result["output"], str)


def test_sessions_cli_backend_refuses_an_unrouted_action(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = SimpleNamespace(cfg=cfg, actor="voice:user", session="voice",
                          source="user")
    with pytest.raises(ValueError):
        VC._b_sessions_cli(ctx, {"sessions_action": "not-a-real-action"})


def test_no_alias_collision_or_dead_backend_across_all_registries():
    from voicebridge import voice_commands as VC

    owners: dict = {}
    for entry in VC._iter_entries():
        for alias in entry.aliases:
            owners.setdefault(alias, []).append(entry.id)
    collisions = {a: o for a, o in owners.items() if len(o) > 1}
    assert collisions == {}, f"voice alias collisions: {collisions}"

    dead = []
    for registry in (VC.REGISTRY, VC.PLUGIN_FLEET_REGISTRY,
                     VC.MODEL_PROFILE_REGISTRY, VC.CLI_BRIDGE_REGISTRY):
        for entry in registry.values():
            if entry.backend not in VC._BACKENDS:
                dead.append((entry.id, entry.backend))
    assert dead == [], f"entries referencing unknown backends: {dead}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
