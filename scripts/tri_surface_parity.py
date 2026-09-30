"""Tri-surface parity harness: CLI / dashboard / voice -> one canonical op.

The single operation contract (``docs/archive/2026-09-21/QUBIT_LATEST_UPSTREAM_1TO1_EXECUTION_PLAN_2026-09-16.md``
item 9) says CLI, dashboard, voice, and external adapters must reach the *same*
canonical operation, arguments, confirmation/cancel semantics, state, and audit.

This harness instruments the one production chokepoint every surface uses:

* dashboard ``voicebridge.dashboard_ops.run_op`` -> ``run_registry_op`` ->
  ``voicebridge.voice_commands.execute``;
* voice ``voicebridge.voice_commands.voice_say`` -> ``resolve`` -> the same
  ``dashboard_ops.run_op`` -> ``voice_commands.execute``;
* CLI ``qubit <family> <sub>`` -> ``cli._it_run_op`` -> the same
  ``dashboard_ops.run_op`` -> ``voice_commands.execute`` (for the ``_OP_CLI``
  bindings), and native CLI families that call the shared backend module
  directly (recorded as ``SAFE_EQUIVALENT`` divergences, never hidden).

By wrapping ``voice_commands.execute`` / ``confirm_pending`` /
``cancel_pending`` we capture the exact ``(op_id, args, backend, source,
session)`` each surface resolved to.  The harness then:

1. compares those identities for a representative read/write/confirm/cancel/
   recovery battery;
2. drives cross-surface confirmation/cancellation against the one shared
   ``approvals.jsonl`` journal;
3. proves the mutation and the audit receipt (``events.jsonl``) are shared;
4. records every divergence it finds with its ``file:line`` provenance.

Run standalone (never touches production HOME/XDG)::

    python scripts/tri_surface_parity.py

It is also the engine imported by ``tests/test_tri_surface_parity8u.py``.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Evidence vocabulary from the execution plan: a row is PASS when all three
# surfaces resolve to the identical canonical identity; SAFE_EQUIVALENT when a
# surface uses a deliberate adapter that calls the same backend but is bound
# through a different (documented) entry; OPERATOR_ONLY / FAIL otherwise.
# ---------------------------------------------------------------------------
PASS = "PASS"
SAFE_EQUIVALENT = "SAFE_EQUIVALENT"
FAIL = "FAIL"

_VOLATILE = (
    "generated_at", "session", "entry", "created_at", "updated_at",
    "started_at", "finished_at", "expires_at", "updated", "pid", "ts",
    "confirmation_id",
)


def scrub(value: Any) -> Any:
    """Drop volatile identity/time fields so real results can be compared."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()
                if k not in _VOLATILE and not k.endswith(("_at", "_ms", "_ts"))}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


@dataclass(frozen=True)
class CanonicalEvent:
    """One surface's resolution of one turn to a canonical operation."""

    surface: str
    phase: str          # execute | confirm | cancel
    op_id: Optional[str]
    args: Dict[str, Any]
    backend: Optional[str]
    source: str
    session: str
    actor: str = ""
    confirmation_id: str = ""

    def identity(self) -> Tuple[Optional[str], str, Optional[str]]:
        return (self.op_id, json.dumps(self.args, sort_keys=True,
                                       default=str), self.backend)


class Recorder:
    """Wrap ``voice_commands`` so each surface's canonical op is observable."""

    def __init__(self) -> None:
        self.events: List[CanonicalEvent] = []
        self.surface_name = "?"
        self._installed = False
        self._orig: Optional[Tuple[Any, Any, Any]] = None
        self._VC: Any = None

    def set_surface(self, name: str) -> None:
        self.surface_name = name

    # -- journal lookup -----------------------------------------------------
    def _peek(self, ctx: Any, confirmation_id: Optional[str]
              ) -> Tuple[Optional[str], Dict[str, Any], Optional[str], str]:
        try:
            j = self._VC._journal(ctx.cfg)
            if confirmation_id:
                target = j.get(confirmation_id)
            else:
                pending = self._VC._pending_for(ctx, j)
                target = pending[-1] if pending else None
            if target is None:
                return (None, {}, None, "")
            cap = str(getattr(target, "capability", "") or "")
            op_id = cap[len("voice."):] if cap.startswith("voice.") else cap
            meta = getattr(target, "meta", {}) or {}
            entry = self._VC._lookup_entry(op_id)
            return (op_id, dict(meta.get("args", {}) or {}),
                    getattr(entry, "backend", None),
                    str(getattr(target, "id", "") or ""))
        except Exception:
            return (None, {}, None, "")

    def install(self) -> "Recorder":
        from voicebridge import voice_commands as VC

        self._VC = VC
        self._orig = (VC.execute, VC.confirm_pending, VC.cancel_pending)

        def execute(ctx: Any, resolution: Any) -> Any:
            entry = resolution.entry
            self.events.append(CanonicalEvent(
                self.surface_name, "execute", getattr(entry, "id", None),
                dict(resolution.args), getattr(entry, "backend", None),
                str(getattr(ctx, "source", "")),
                str(getattr(ctx, "session", "")),
                str(getattr(ctx, "actor", ""))))
            return self._orig[0](ctx, resolution)

        def confirm_pending(ctx: Any, confirmation_id: Optional[str] = None
                            ) -> Any:
            op_id, args, backend, cid = self._peek(ctx, confirmation_id)
            self.events.append(CanonicalEvent(
                self.surface_name, "confirm", op_id, args, backend,
                str(getattr(ctx, "source", "")),
                str(getattr(ctx, "session", "")),
                str(getattr(ctx, "actor", "")), cid))
            return self._orig[1](ctx, confirmation_id)

        def cancel_pending(ctx: Any, confirmation_id: Optional[str] = None
                           ) -> Any:
            op_id, args, backend, cid = self._peek(ctx, confirmation_id)
            self.events.append(CanonicalEvent(
                self.surface_name, "cancel", op_id, args, backend,
                str(getattr(ctx, "source", "")),
                str(getattr(ctx, "session", "")),
                str(getattr(ctx, "actor", "")), cid))
            return self._orig[2](ctx, confirmation_id)

        VC.execute = execute
        VC.confirm_pending = confirm_pending
        VC.cancel_pending = cancel_pending
        self._installed = True
        return self

    def uninstall(self) -> None:
        if not self._installed or self._orig is None:
            return
        VC = self._VC
        VC.execute, VC.confirm_pending, VC.cancel_pending = self._orig
        self._installed = False

    # -- queries ------------------------------------------------------------
    def for_phase(self, phase: str, op_id: Optional[str] = None
                  ) -> List[CanonicalEvent]:
        return [e for e in self.events
                if e.phase == phase and (op_id is None or e.op_id == op_id)]

    def by_surface(self, phase: str, op_id: str
                   ) -> Dict[str, CanonicalEvent]:
        out: Dict[str, CanonicalEvent] = {}
        for e in self.events:
            if e.phase == phase and e.op_id == op_id:
                out[e.surface] = e
        return out


class ResolverSpy:
    """Observe the one provider-selection authority (``resolver.set_session_profile``)."""

    def __init__(self, recorder: Recorder) -> None:
        self.rec = recorder
        self.calls: List[Tuple[str, str, str, str, bool]] = []
        self._orig: Optional[Callable[..., Any]] = None

    def install(self) -> "ResolverSpy":
        from voicebridge.providers import resolver as R

        self._orig = R.set_session_profile

        def spy(cfg: Any, session: str, profile_id: str, *, role: str = "chat",
                confirm: bool = False) -> Any:
            self.calls.append((self.rec.surface_name, str(session),
                               str(profile_id), str(role), bool(confirm)))
            return self._orig(cfg, session, profile_id, role=role,
                              confirm=confirm)

        R.set_session_profile = spy
        return self

    def uninstall(self) -> None:
        if self._orig is None:
            return
        from voicebridge.providers import resolver as R

        R.set_session_profile = self._orig
        self._orig = None


class Surfaces:
    """Drive the three production surfaces in-process, tagging the recorder."""

    def __init__(self, cfg: Any, cfgfile: Path, recorder: Recorder) -> None:
        self.cfg = cfg
        self.cfgfile = cfgfile
        self.rec = recorder

    # -- surface entrypoints ------------------------------------------------
    def cli(self, *argv: str) -> Tuple[int, Any]:
        from voicebridge import cli

        self.rec.set_surface("cli")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                rc = cli.main([*argv, "--config", str(self.cfgfile)])
            except SystemExit as exc:  # argparse error path
                rc = getattr(exc, "code", 0) or 0
        text = buf.getvalue().strip()
        try:
            return rc, json.loads(text) if text else None
        except ValueError:
            return rc, text

    def dash(self, op_id: str, args: Optional[Dict[str, Any]] = None, *,
             session: str = "dashboard") -> Dict[str, Any]:
        from voicebridge import dashboard_ops as OPS

        self.rec.set_surface("dashboard")
        return OPS.run_op(self.cfg, op_id, dict(args or {}),
                          actor="dashboard:user", session=session)

    def voice(self, text: str, *, session: str = "voice") -> Dict[str, Any]:
        from voicebridge import voice_commands as VC

        self.rec.set_surface("voice")
        return VC.voice_say(self.cfg, text, actor="voice:user",
                            session=session, source="user")

    def confirm_dash(self, cid: str, *, session: str) -> Dict[str, Any]:
        from voicebridge import dashboard_ops as OPS

        self.rec.set_surface("dashboard")
        return OPS.confirm_op(self.cfg, cid, actor="dashboard:user",
                              session=session)

    def cancel_dash(self, cid: str, *, session: str) -> Dict[str, Any]:
        from voicebridge import dashboard_ops as OPS

        self.rec.set_surface("dashboard")
        return OPS.cancel_op(self.cfg, cid, actor="dashboard:user",
                             session=session)

    def confirm_cli(self, cid: str) -> Tuple[int, Any]:
        return self.cli("approvals", "respond", cid, "approve")

    def cancel_cli(self, cid: str) -> Tuple[int, Any]:
        return self.cli("approvals", "respond", cid, "deny")

    def confirm_voice(self, cid: str, *, session: str) -> Any:
        from voicebridge import voice_commands as VC

        self.rec.set_surface("voice")
        ctx = VC.make_ctx(self.cfg, actor="voice:user", session=session,
                          source="user")
        return VC.confirm_pending(ctx, cid)

    def cancel_voice(self, cid: str, *, session: str) -> Any:
        from voicebridge import voice_commands as VC

        self.rec.set_surface("voice")
        ctx = VC.make_ctx(self.cfg, actor="voice:user", session=session,
                          source="user")
        return VC.cancel_pending(ctx, cid)


# ---------------------------------------------------------------------------
# Representative battery
# ---------------------------------------------------------------------------
# Each entry: (category, op_id, cli argv, dashboard args, voice utterance).
EXECUTE_BATTERY: Tuple[Tuple[str, str, Tuple[str, ...], Dict[str, Any], str],
                       ...] = (
    ("read", "logs", ("logs",), {}, "show logs"),
    ("read", "tools", ("tools",), {}, "list tools"),
    ("read", "reports_list", ("reports", "list"), {}, "list reports"),
    ("read", "prompt_budget", ("prompt-budget",), {}, "prompt budget"),
    ("write", "memory_remember", ("memory", "remember", "parity probe"),
     {"text": "parity probe"}, "remember parity probe"),
    ("recovery", "ghost_action_recover", ("ghost", "action-recover", "r1"),
     {"receipt_id": "r1"}, "recover ghost action r1"),
    ("voice", "marketplace_discovery", ("marketplace", "discovery"),
     {}, "marketplace discovery"),
)

# Deliberate adapters: the CLI family parser calls the shared backend module
# directly instead of entering through the op registry.  Same backend, one
# documented binding.  (op_id, cli argv, dashboard args, voice text, reason,
#  provenance file:line)
SAFE_EQUIVALENT_BATTERY: Tuple[Tuple[str, Tuple[str, ...], Dict[str, Any],
                                     str, str, str], ...] = (
    ("sessions", ("sessions", "list"), {}, "list sessions",
     "native `sessions` family calls auth_status.recent_sessions directly; the "
     "dashboard/voice registry op `sessions` calls the same function",
     "voicebridge/cli.py:3855"),
    ("kanban_list", ("kanban", "list"), {}, "list tasks",
     "native `kanban` family calls kanban.list_tasks directly; the registry op "
     "`kanban_list` calls the same function",
     "voicebridge/cli.py:5347"),
    ("autonomy_list", ("autonomy", "list"), {}, "list autonomy profiles",
     "native `autonomy` family calls autonomy_ops directly; the registry op "
     "`autonomy_list` calls the same module",
     "voicebridge/cli.py:3365"),
    ("plugins_remote", ("plugins", "remote", "pkg"), {"identifier": "pkg"},
     "install remote plugin pkg",
     "three bindings, one refusal backend: CLI native `plugins` family, "
     "dashboard ExtraOp `plugins_remote` (dashboard_ops.py:812), and voice "
     "guard `plugins_remote_refusal` (voice_commands.py:5691) all reach the "
     "same BLOCKED_REMOTE_PLUGIN contract",
     "voicebridge/voice_commands.py:5691"),
)


# Pinned binding pattern for each SAFE_EQUIVALENT probe: the canonical op ids
# each surface is expected to dispatch (empty set = the surface does not enter
# the registry for this capability, it calls the shared backend module).
SAFE_EQUIVALENT_EXPECTED: Dict[str, Dict[str, frozenset]] = {
    "sessions": {"cli": frozenset(), "dashboard": frozenset({"sessions"}),
                 "voice": frozenset({"sessions"})},
    "kanban_list": {"cli": frozenset(),
                    "dashboard": frozenset({"kanban_list"}),
                    "voice": frozenset({"kanban_list"})},
    "autonomy_list": {"cli": frozenset(),
                      "dashboard": frozenset({"autonomy_list"}),
                      "voice": frozenset({"autonomy_list"})},
    # dashboard routes this through an ExtraOp (dashboard_ops.py:812), which
    # does not pass through voice_commands.execute, so the recorder observes no
    # registry execute for it.  The one refusal contract is still shared.
    "plugins_remote": {"cli": frozenset(),
                       "dashboard": frozenset(),
                       "voice": frozenset({"plugins_remote_refusal"})},
}


def observed_bindings(surf: Surfaces, rec: Recorder, op_id: str,
                      argv: Tuple[str, ...], dargs: Dict[str, Any],
                      voice_text: str) -> Dict[str, frozenset]:
    start = len(rec.events)
    surf.cli(*argv)
    surf.dash(op_id, dargs, session="parity")
    surf.voice(voice_text, session="parity")
    out: Dict[str, frozenset] = {}
    for surface in ("cli", "dashboard", "voice"):
        seen = [e for e in rec.events[start:]
                if e.surface == surface and e.phase == "execute"]
        out[surface] = frozenset(e.op_id for e in seen)
    return out


def build_safe_equivalent_matrix(surf: Surfaces,
                                 rec: Recorder) -> List[MatrixRow]:
    rows: List[MatrixRow] = []
    for op_id, argv, dargs, voice_text, reason, prov in SAFE_EQUIVALENT_BATTERY:
        observed = observed_bindings(surf, rec, op_id, argv, dargs, voice_text)
        expected = SAFE_EQUIVALENT_EXPECTED[op_id]
        ok = observed == expected
        rows.append(MatrixRow(
            "adapter", op_id, SAFE_EQUIVALENT if ok else FAIL, {},
            reason if ok else f"binding drift: {observed} != {expected}",
            prov))
    return rows


def probe_provider_selection(surf: Surfaces, rec: Recorder,
                             spy: ResolverSpy
                             ) -> Tuple[List[Tuple[str, str, str, str, bool]],
                                        Dict[str, frozenset]]:
    """All three surfaces must select a provider through the one resolver."""
    from voicebridge.providers import resolver as R

    pid = R.DEFAULT_LOCAL_ID
    spy.calls.clear()
    start = len(rec.events)

    surf.cli("profile", "model", "activate", pid, "--session", "sess1",
             "--role", "chat", "--confirm")
    staged = surf.dash("select_session_model",
                       {"session": "sess1", "profile_id": pid,
                        "role": "chat"}, session="sess1")
    if staged.get("confirmation_id"):
        surf.confirm_dash(staged["confirmation_id"], session="sess1")
    vstaged = surf.voice("use my local brain", session="sess1")
    if vstaged.get("confirmation_id"):
        surf.confirm_voice(vstaged["confirmation_id"], session="sess1")

    bindings: Dict[str, frozenset] = {}
    for surface in ("cli", "dashboard", "voice"):
        bindings[surface] = frozenset(
            e.op_id for e in rec.events[start:]
            if e.surface == surface and e.phase == "execute")
    return list(spy.calls), bindings


def calls_by_surface(calls: List[Tuple[str, str, str, str, bool]],
                     surface: str) -> List[Tuple[str, str, str, bool]]:
    return [c[1:] for c in calls if c[0] == surface]


def make_env(root: Optional[Path] = None,
             monkeypatch: Optional[Any] = None) -> Tuple[Path, Any]:
    """Isolated HOME/XDG config; returns (cfgfile, cfg). Never production.

    When ``monkeypatch`` (a pytest ``MonkeyPatch``) is supplied, env changes go
    through it so they are restored at test teardown; the standalone script
    passes nothing and keeps the process-local override.
    """
    root = Path(root) if root else Path(tempfile.mkdtemp(prefix="tri-parity-"))
    if monkeypatch is not None:
        def setenv(key: str, value: str) -> None:
            monkeypatch.setenv(key, value)
    else:
        def setenv(key: str, value: str) -> None:
            os.environ[key] = value
    for key, sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"),
                     ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d"),
                     ("XDG_CACHE_HOME", "cache")):
        setenv(key, str(root / sub) if sub else str(root))
    from voicebridge.config import Config

    cfgfile = root / "voice.yaml"
    cfgfile.write_text("logging:\n  jsonl: true\n")
    cfg = Config.load(cfgfile)
    from voicebridge import logging_setup

    logging_setup.setup(level="DEBUG", jsonl=True, state_dir=cfg.state_dir())
    return cfgfile, cfg


def read_audit(cfg: Any) -> List[Dict[str, Any]]:
    path = Path(cfg.state_dir()) / "events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------
@dataclass
class MatrixRow:
    category: str
    op_id: str
    verdict: str
    surfaces: Dict[str, CanonicalEvent] = field(default_factory=dict)
    detail: str = ""
    provenance: str = ""

    def identity_strings(self) -> Dict[str, Tuple[Any, ...]]:
        return {s: e.identity() for s, e in self.surfaces.items()}


def build_execute_matrix(surf: Surfaces, rec: Recorder) -> List[MatrixRow]:
    rows: List[MatrixRow] = []
    for category, op_id, argv, dargs, voice_text in EXECUTE_BATTERY:
        start = len(rec.events)
        surf.cli(*argv)
        surf.dash(op_id, dargs, session="parity")
        surf.voice(voice_text, session="parity")
        produced = rec.events[start:]
        by = {e.surface: e for e in produced if e.op_id == op_id}
        ids = {s: e.identity() for s, e in by.items()}
        shared = len(by) == 3 and len(set(ids.values())) == 1
        rows.append(MatrixRow(
            category, op_id, PASS if shared else FAIL, by,
            "" if shared else f"canonical identities differ: {ids}",
            "voicebridge/dashboard_ops.py:256, voicebridge/voice_commands.py:9021"))
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    cfgfile, cfg = make_env()
    rec = Recorder().install()
    spy = ResolverSpy(rec).install()
    try:
        surf = Surfaces(cfg, cfgfile, rec)
        failed = 0

        rows = build_execute_matrix(surf, rec)
        print("op matrix (CLI / dashboard / voice -> one canonical op)")
        print("-" * 78)
        for row in rows:
            mark = row.verdict
            ident = next(iter(row.identity_strings().values()), None)
            print(f"{mark:16s} {row.category:9s} {row.op_id:22s} "
                  f"{ident[0] if ident else '-':22s} backend="
                  f"{ident[2] if ident else '-'}")
            if row.verdict != PASS:
                failed += 1
                print("     " + row.detail)
        print(f"{sum(r.verdict == PASS for r in rows)}/{len(rows)} execute "
              f"operations share one canonical identity")

        print()
        print("deliberate adapters (SAFE_EQUIVALENT, same backend, distinct "
              "binding)")
        print("-" * 78)
        for row in build_safe_equivalent_matrix(surf, rec):
            print(f"{row.verdict:16s} {row.op_id:22s} {row.provenance}")
            print(f"     {row.detail}")
            if row.verdict != SAFE_EQUIVALENT:
                failed += 1

        print()
        print("provider selection (one resolver: set_session_profile)")
        print("-" * 78)
        calls, bindings = probe_provider_selection(surf, rec, spy)
        distinct = {c[1:] for c in calls}
        same = len(calls) == 3 and len(distinct) == 1
        print(f"{'PASS' if same else 'FAIL':16s} one resolver, identical "
              f"arguments on every surface")
        for surface in ("cli", "dashboard", "voice"):
            print(f"     {surface:9s} -> {calls_by_surface(calls, surface)}")
        print("     binding: CLI `profile model activate` "
              "(voicebridge/cli.py:4919) | dashboard ExtraOp "
              "`select_session_model` (voicebridge/dashboard_ops.py:1510) | "
              "voice registry `model_profile_local` "
              "(voicebridge/voice_commands.py:8936)")
        print(f"     observed registry bindings="
              f"{ {k: sorted(v) for k, v in bindings.items()} }")
        if not same:
            failed += 1
        return 1 if failed else 0
    finally:
        spy.uninstall()
        rec.uninstall()


if __name__ == "__main__":
    raise SystemExit(main())
