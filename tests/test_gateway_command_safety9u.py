"""Gateway detached-control shell-safety regression (owner scope: supervisor).

``stop``/``restart`` are dispatched detached through ``sh -c "sleep …;
systemctl …"`` so the caller's HTTP response is flushed before the service
process is replaced. The unit name is config-controlled, so it must be
shell-quoted: a unit name carrying shell metacharacters must reach ``systemctl``
as one literal argument and must never be executed by the shell.

The proof runs the *real* detached command against a fake ``systemctl`` on PATH
(no host service is touched) and asserts the injected payload created no file.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_FAKE_SYSTEMCTL = """#!/bin/sh
printf '%s\\n' "$*" >> "$QUBIT_FAKE_SYSTEMCTL_LOG"
exit 0
"""


class _Cfg:
    def __init__(self, data, state):
        self._d = data
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


def _show_active(_argv, _t):
    return (0, "ActiveState=active\nSubState=running\nMainPID=4242\n"
               "ExecMainStartTimestamp=Mon 2026-09-23\n"
               "NRestarts=0\nExecMainStatus=0\n", "")


def test_detached_control_shell_quotes_the_unit_name(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS

    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    log = tmp_path / "systemctl.log"
    log.touch()
    script = bindir / "systemctl"
    script.write_text(_FAKE_SYSTEMCTL)
    script.chmod(0o755)
    monkeypatch.setenv("QUBIT_FAKE_SYSTEMCTL_LOG", str(log))
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])

    payload = f"qubit-evil; touch {tmp_path}/pwned #"
    state = tmp_path / "state"
    state.mkdir()
    cfg = _Cfg({"gateway": {"daemon_service_unit": payload,
                            "system_scope": "user"}}, state)

    monkeypatch.setattr(GS, "_estop", lambda _cfg: False)
    monkeypatch.setattr(GS, "_real_control_disabled", lambda: False)
    monkeypatch.setattr(GS, "DETACH_DELAY_S", 0)

    ran: list[list[str]] = []

    def spawner(argv):
        ran.append(list(argv))
        subprocess.run(list(argv), check=False, timeout=10)

    out = GS.control(cfg, "restart", confirmed=True, runner=_show_active,
                     probe=lambda h, p: True, spawner=spawner)
    assert out["ok"] is True and out["state"] == "RESTARTING", out
    assert len(ran) == 1 and ran[0][0] == "sh", ran

    # The shell must NOT have executed the injected ``touch``.
    assert not (tmp_path / "pwned").exists(), "unit name was executed by the shell"

    # ``systemctl`` received the whole malicious name as ONE literal argument.
    line = log.read_text().strip()
    assert "restart" in line, line
    assert payload in line, line
