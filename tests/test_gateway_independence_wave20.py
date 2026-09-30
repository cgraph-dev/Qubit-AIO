"""Gateway independence + brand guard (Owner Decisions #9/#10/#16).

The Qubit-owned gateway surfaces — the daemon launch boundary
(``voicebridge.gateway_daemon``), the supervisor
(``voicebridge.gateway_supervisor``), the gateway RPC/panel modules
(``voicebridge/gateway/**``), and the orchestrator gateway calls — must never
adopt the operator's read-only reference home (``~/.hermes``) or target the
reference dashboard unit. This pins that no reference-home/-service fallback
exists in those modules and that the control path resolves Qubit-owned units
through the one broker/policy/audit/confirmation/e-stop chain.

Deliberately NOT scanned (genuine wire/compat contract, owner decision D2):
the OpenClaw ``openclaw.*`` RPC method names + the ``openclaw_handlers``
package, and the vendored ``HERMES_*`` env names the fork still reads.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Reference-home/-service *fallback constructs* that must never appear in the
#: Qubit-owned gateway modules. ``~/.hermes`` as prose in a negative-assertion
#: comment (orchestrator) is intentionally not in this list; these are the
#: executable-shape literals.
FORBIDDEN_REFERENCE_LITERALS = (
    "hermes-dashboard",
    "hermes-agent",
    "HERMES_HOME",
    'Path.home() / ".hermes"',
    '".hermes"',
    "'.hermes'",
)


def _ownership_modules() -> list[Path]:
    files = sorted(
        p for p in (REPO_ROOT / "voicebridge" / "gateway").rglob("*.py")
        if "__pycache__" not in p.parts)
    files += [
        REPO_ROOT / "voicebridge" / "gateway_daemon.py",
        REPO_ROOT / "voicebridge" / "gateway_supervisor.py",
        REPO_ROOT / "voicebridge" / "orchestrator.py",
    ]
    return files


class _Cfg:
    """Minimal config double: nested ``get`` + a real state dir."""

    def __init__(self, data, state):
        self._d = data or {}
        self._state = Path(state)

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def state_dir(self):
        return self._state


def _cfg(tmp_path, gateway=None):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return _Cfg({"gateway": gateway or {"system_scope": "user"},
                 "webui": {"host": "127.0.0.1", "port": 8890}}, state)


def _show(argv, _timeout):
    joined = " ".join(str(a) for a in argv)
    if "show" in joined:
        return (0, "ActiveState=active\nSubState=running\nMainPID=4242\n"
                   "ExecMainStartTimestamp=Mon 2026-09-23 00:00:00 UTC\n"
                   "ActiveEnterTimestamp=Mon 2026-09-23 00:00:00 UTC\n"
                   "NRestarts=0\nExecMainStatus=0\n", "")
    return (0, "", "")


# --------------------------------------------------------------- source guard

def test_gateway_modules_have_no_reference_fallback_literal():
    """No reference-home/-service fallback literal in the Qubit-owned modules."""
    offenders: list[tuple[str, str]] = []
    for path in _ownership_modules():
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in FORBIDDEN_REFERENCE_LITERALS:
            if token in text:
                offenders.append((str(path.relative_to(REPO_ROOT)), token))
    assert offenders == [], offenders


def test_gateway_daemon_and_supervisor_name_only_qubit_units():
    from voicebridge import gateway_daemon as GD
    from voicebridge import gateway_supervisor as GS

    assert GD.service_unit_name() == "qubit-gateway.service"
    assert GS.DEFAULT_UNIT == "qubit-voicebridge.service"
    assert GS.DEFAULT_DAEMON_UNIT == "qubit-gateway.service"
    for unit in (GD.service_unit_name(), GS.DEFAULT_UNIT, GS.DEFAULT_DAEMON_UNIT):
        assert "hermes" not in unit.lower()
        assert "nous" not in unit.lower()


# ----------------------------------------------------------- unit resolution

def test_default_units_are_qubit_owned(tmp_path):
    from voicebridge import gateway_supervisor as GS

    cfg = _cfg(tmp_path)
    assert GS.service_unit(cfg) == "qubit-voicebridge.service"
    assert GS.daemon_service_unit(cfg) == "qubit-gateway.service"


def test_daemon_home_never_resolves_to_the_reference_home(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS

    scratch_home = tmp_path / "home"
    scratch_home.mkdir()
    monkeypatch.setenv("HOME", str(scratch_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)

    resolved = GS._daemon_home()
    assert resolved == (tmp_path / "xdg" / "qubit-voicebridge" / "runtime").resolve()
    assert resolved != (scratch_home / ".hermes")


# ------------------------------------------------- one authority path / target

def test_status_and_control_target_the_qubit_daemon_unit(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_estop", lambda _cfg: False)
    monkeypatch.setattr(GS.shutil, "which", lambda _name: "/usr/bin/systemctl")

    seen: list[list[str]] = []

    def runner(argv, timeout):
        seen.append([str(a) for a in argv])
        return _show(argv, timeout)

    st = GS.status(_cfg(tmp_path), runner=runner, probe=lambda h, p: True)
    assert st["service_unit"] == "qubit-gateway.service"
    assert any("show" in " ".join(a) and "qubit-gateway.service" in " ".join(a)
               for a in seen), seen

    spawned: list[list[str]] = []
    out = GS.control(_cfg(tmp_path), "restart", confirmed=True, runner=runner,
                     probe=lambda h, p: True,
                     spawner=lambda argv: spawned.append([str(a) for a in argv]))
    assert out["ok"] is True and out["state"] == "RESTARTING", out
    dispatched = " ".join(spawned[0])
    assert "restart qubit-gateway.service" in dispatched, dispatched
    assert "hermes" not in dispatched.lower()


def test_control_without_confirmation_executes_nothing(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_estop", lambda _cfg: False)
    calls: list[list[str]] = []
    out = GS.control(_cfg(tmp_path), "restart",
                     runner=lambda argv, t: (calls.append(list(argv)), (0, "", ""))[1],
                     spawner=lambda argv: calls.append(list(argv)))
    assert out["ok"] is False and out["code"] == GS.CONFIRM_CODE, out
    assert calls == [], "a confirmation-less control must execute nothing"


def test_control_authority_is_the_shared_broker_store(tmp_path):
    from voicebridge import broker as BR
    from voicebridge import gateway_supervisor as GS

    cfg = _cfg(tmp_path)
    engine = GS._control_engine(cfg)
    assert GS._engine_key(engine.confirmations) == \
        GS._engine_key(BR.confirmation_store_for(cfg))
