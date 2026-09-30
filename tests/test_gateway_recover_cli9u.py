"""9u — ``qubit gateway recover`` CLI wiring.

The daemon unit parks in failed state after its restart burst and names
exactly this verb (reset-failed, then start; healthy = no-op). These tests
prove the verb parses, requires confirmation like every other lifecycle
transition, and maps ``GS.recover`` outcomes to exit codes — without
touching any host service.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _Cfg:
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


def _cfg(tmp_path):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return _Cfg({"gateway": {"service_unit": "qubit-gateway.service",
                             "system_scope": "user"},
                 "webui": {"host": "127.0.0.1", "port": 48999}}, state)


def _args(**kw):
    kw.setdefault("gateway_command", "recover")
    kw.setdefault("confirm", False)
    return types.SimpleNamespace(**kw)


def test_recover_parses_with_confirm_flag():
    from voicebridge import cli as CLI

    parser = CLI.build_parser()
    ns = parser.parse_args(["gateway", "recover"])
    assert ns.gateway_command == "recover"
    assert ns.confirm is False
    ns = parser.parse_args(["gateway", "recover", "--confirm"])
    assert ns.confirm is True


def test_recover_without_confirm_is_refused_and_runs_nothing(tmp_path,
                                                             monkeypatch,
                                                             capsys):
    from voicebridge import cli as CLI
    from voicebridge import gateway_supervisor as GS

    # Real supervisor: the refusal happens inside _authorize_control, before
    # any runner/systemctl use, so no host service is touched.
    monkeypatch.setattr(GS, "_estop", lambda _cfg: False)
    rc = CLI._cmd_gateway(_cfg(tmp_path), _args())
    out = capsys.readouterr().out
    assert rc == 3, out
    assert "GATEWAY_CONFIRM_REQUIRED" in out, out


def test_recover_maps_supervisor_outcomes_to_exit_codes(tmp_path, monkeypatch,
                                                        capsys):
    from voicebridge import cli as CLI
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_estop", lambda _cfg: False)
    seen = {}

    def _fake(cfg, **kw):
        seen.update(kw)
        return dict(_fake.result)

    monkeypatch.setattr(GS, "recover", _fake)
    _fake.result = {"ok": True, "state": "OK", "action": "recover"}
    assert CLI._cmd_gateway(_cfg(tmp_path), _args(confirm=True)) == 0
    assert seen.get("confirmed") is True and seen.get("actor") == "cli:user"
    _fake.result = {"ok": False, "code": "GATEWAY_ESTOP", "action": "recover"}
    assert CLI._cmd_gateway(_cfg(tmp_path), _args(confirm=True)) == 3
    _fake.result = {"ok": False, "code": "FAILED", "action": "recover"}
    assert CLI._cmd_gateway(_cfg(tmp_path), _args(confirm=True)) == 1
    capsys.readouterr()
