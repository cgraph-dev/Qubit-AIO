"""`qubit restart` — the fleet fan-out over the ONE authority path.

Drives ``voicebridge.gateway_supervisor.restart_fleet`` with injected
runner/spawner so the fan-out, the confirm preview, the e-stop latch, and the
per-unit typed results are deterministic and touch no host service. Also pins
the CLI verb's preview contract via ``_cmd_restart``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _estop_clear(monkeypatch):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_estop", lambda _cfg: False)


class _Cfg:
    """Minimal cfg with a ``.get(*path, default=)`` read (matches the suite)."""

    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def _show(active, pid=4242):
    return (0,
            f"ActiveState={'active' if active else 'inactive'}\n"
            f"SubState={'running' if active else 'dead'}\n"
            f"MainPID={pid if active else 0}\n"
            f"ExecMainStartTimestamp=Mon 2026-09-21\n",
            "")


def _runner():
    return lambda argv, t: _show(True)


def test_fleet_selection_all_is_ordered_and_named():
    from voicebridge import gateway_supervisor as GS

    names, err = GS._fleet_selection("all")
    assert err is None
    assert names is not None
    assert names == [GS.FLEET_UNITS[k] for k in GS.FLEET_ORDER]
    assert "qubit-voicebridge.service" in names
    assert "qubit-gateway.service" in names


def test_fleet_selection_single_and_bad():
    from voicebridge import gateway_supervisor as GS

    names, err = GS._fleet_selection("voicebridge")
    assert err is None and names == ["qubit-voicebridge.service"]

    bad, code = GS._fleet_selection("nope")
    assert bad is None and code == GS.BAD_INPUT_CODE


def test_restart_fleet_is_detached_for_every_unit():
    from voicebridge import gateway_supervisor as GS

    cfg = _Cfg({"gateway": {"service_unit": "qubit-voicebridge.service"}})
    spawned = []
    out = GS.restart_fleet(cfg, only="all", confirmed=True,
                           runner=_runner(), probe=lambda h, p: True,
                           spawner=lambda argv: spawned.append(list(argv)))
    assert out["ok"] is True and out["state"] == "RESTARTING"
    assert out["detached"] is True
    assert out["units"] == [GS.FLEET_UNITS[k] for k in GS.FLEET_ORDER]
    # One detached systemctl restart per selected unit.
    assert len(spawned) == len(out["units"])
    for unit in out["units"]:
        assert any(unit in argv[-1] for argv in spawned)


def test_restart_fleet_only_narrows_to_one_unit():
    from voicebridge import gateway_supervisor as GS

    cfg = _Cfg({"gateway": {"service_unit": "qubit-voicebridge.service"}})
    spawned = []
    out = GS.restart_fleet(cfg, only="gateway", confirmed=True,
                           runner=_runner(), probe=lambda h, p: True,
                           spawner=lambda argv: spawned.append(list(argv)))
    assert out["ok"] is True
    assert out["units"] == ["qubit-gateway.service"]
    assert len(spawned) == 1
    assert "qubit-gateway.service" in spawned[0][-1]


def test_restart_fleet_requires_confirmation_and_touches_nothing():
    from voicebridge import gateway_supervisor as GS

    cfg = _Cfg({"gateway": {"service_unit": "qubit-voicebridge.service"}})
    spawned = []
    out = GS.restart_fleet(cfg, only="all", confirmed=False,
                           runner=_runner(), probe=lambda h, p: True,
                           spawner=lambda argv: spawned.append(list(argv)))
    assert out["ok"] is False
    assert out["state"] == "CONFIRM_REQUIRED"
    assert out["code"] == GS.CONFIRM_CODE
    # Every unit refused; nothing was dispatched.
    assert spawned == []
    assert all(r.get("code") == GS.CONFIRM_CODE for r in out["results"])


def test_restart_fleet_respects_estop(monkeypatch):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_estop", lambda _cfg: True)
    cfg = _Cfg({"gateway": {"service_unit": "qubit-voicebridge.service"}})
    spawned = []
    out = GS.restart_fleet(cfg, only="all", confirmed=True,
                           runner=_runner(), probe=lambda h, p: True,
                           spawner=lambda argv: spawned.append(list(argv)))
    assert out["ok"] is False
    assert spawned == []


def test_restart_fleet_bad_only_is_typed():
    from voicebridge import gateway_supervisor as GS

    cfg = _Cfg({"gateway": {"service_unit": "qubit-voicebridge.service"}})
    out = GS.restart_fleet(cfg, only="bogus", confirmed=True,
                           runner=_runner(), spawner=lambda argv: None)
    assert out["ok"] is False and out["code"] == GS.BAD_INPUT_CODE


def test_fleet_does_not_mutate_the_caller_cfg():
    from voicebridge import gateway_supervisor as GS

    cfg = {"gateway": {"service_unit": "qubit-voicebridge.service"}}
    before = json.dumps(cfg, sort_keys=True)
    GS.restart_fleet(cfg, only="gateway", confirmed=True,
                     runner=_runner(), probe=lambda h, p: True,
                     spawner=lambda argv: None)
    assert json.dumps(cfg, sort_keys=True) == before


# ── CLI verb: the preview contract (no --confirm => nothing touched) ──────────
class _Args:
    def __init__(self, only="all", confirm=False):
        self.only = only
        self.confirm = confirm


def test_cmd_restart_preview_names_units_and_exits_3(capsys):
    from voicebridge import cli

    cfg = _Cfg({"gateway": {"service_unit": "qubit-voicebridge.service"}})
    rc = cli._cmd_restart(cfg, _Args(confirm=False))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert payload["state"] == "CONFIRM_REQUIRED"
    assert "qubit-voicebridge.service" in payload["units"]
    assert "--confirm" in payload["command"]


def test_cmd_restart_bad_only_is_typed(capsys):
    from voicebridge import cli

    cfg = _Cfg({"gateway": {"service_unit": "qubit-voicebridge.service"}})
    rc = cli._cmd_restart(cfg, _Args(only="nope", confirm=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["code"] == "GATEWAY_BAD_INPUT"


def test_restart_fleet_surfaces_specific_unavailable_code(monkeypatch):
    # Under the test guard every unit reports UNAVAILABLE; the aggregate must
    # carry that specific code (CLI maps it to exit 2), not the generic
    # GATEWAY_CONTROL_FAILED (exit 1).
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setenv("QUBIT_GATEWAY_SUPERVISION_DISABLED", "1")
    cfg = _Cfg({"gateway": {"service_unit": "qubit-voicebridge.service"}})
    out = GS.restart_fleet(cfg, only="gateway", confirmed=True)
    assert out["ok"] is False
    assert out["code"] == GS.UNAVAILABLE_CODE
    assert out["state"] == "UNAVAILABLE"

