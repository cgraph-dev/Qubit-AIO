"""9u — gateway control integration through the real compat dispatcher.

Drives dashboard -> ``voicebridge.qubit_compat.dispatch`` ->
``voicebridge.gateway_supervisor`` with an injected fake ``systemctl`` on PATH
and an injected detached spawner; the fail-closed guard is lifted only inside
this process. No host service is ever started, stopped, or restarted.

Owner Decision #10: every lifecycle transition runs through the shared durable
confirmation authority, so the transition tests present ``{"confirm": true}``
(``test_control_without_confirmation_is_refused`` proves the bare-flag refusal).
Owner Decision #16: ``qubit gateway`` control targets the messaging-gateway
DAEMON unit (default ``qubit-gateway.service``), so ``gateway.service_unit`` in
the config names the web-UI unit and the control argv carries the daemon unit.
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

UNIT = "qubit-gateway.service"
WEB_UNIT = "qubit-voicebridge.service"

# Test double: records argv, emits plausible `show` output, flips a state file.
_FAKE_SYSTEMCTL = """#!/bin/sh
printf '%s\\n' "$*" >> "$QUBIT_FAKE_SYSTEMCTL_LOG"
for a in "$@"; do case "$a" in --*) ;; *) cmd="$a"; break;; esac; done
case "$cmd" in
  show)
    if [ "$(cat "$QUBIT_FAKE_SYSTEMCTL_STATE" 2>/dev/null)" = active ]; then
      printf 'ActiveState=active\\nSubState=running\\nMainPID=4242\\n'
      printf 'ExecMainStartTimestamp=Mon 2026-09-21 00:00:00 UTC\\n'
    else
      printf 'ActiveState=inactive\\nSubState=dead\\nMainPID=0\\n'
      printf 'ExecMainStartTimestamp=\\n'
    fi;;
  start|restart) printf 'active\\n' > "$QUBIT_FAKE_SYSTEMCTL_STATE";;
  stop) printf 'inactive\\n' > "$QUBIT_FAKE_SYSTEMCTL_STATE";;
esac
exit 0
"""


def _cfg(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = tmp_path / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(target))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / ".hermes"))
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl: false\nwebui:\n  token: t\n"
                 f"  host: 127.0.0.1\n  port: 48999\n"
                 f"gateway:\n  service_unit: {WEB_UNIT}\n  system_scope: user\n")
    from voicebridge.config import Config

    return Config.load(p)


def _fake_systemctl(tmp_path, monkeypatch):
    """Put an executable fake ``systemctl`` first on PATH; return (log, state)."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    log, state = tmp_path / "systemctl.log", tmp_path / "systemctl.state"
    log.touch()
    state.write_text("inactive\n")
    script = bindir / "systemctl"
    script.write_text(_FAKE_SYSTEMCTL)
    script.chmod(0o755)
    monkeypatch.setenv("QUBIT_FAKE_SYSTEMCTL_LOG", str(log))
    monkeypatch.setenv("QUBIT_FAKE_SYSTEMCTL_STATE", str(state))
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    import shutil

    assert Path(shutil.which("systemctl")).resolve() == script.resolve()
    return log, state


def _patch_supervisor(monkeypatch, *, spawned=None):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_real_control_disabled", lambda: False)
    monkeypatch.setattr(GS, "_estop", lambda _cfg: False)
    if spawned is not None:
        monkeypatch.setattr(
            GS, "_default_spawner", lambda argv: spawned.append(list(argv)))


def _dispatch(cfg, method, path, body=None):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, method, path, {}, body or {})


def _cmds(log):
    """Subcommands the fake ``systemctl`` saw (first non-flag token per line)."""
    return {next(t for t in line.split() if not t.startswith("-"))
            for line in log.read_text().splitlines() if line.strip()}


# --- GET /api/gateway: the SPA refusal signal -------------------------------
def test_get_gateway_supervision_empty_when_available(tmp_path, monkeypatch):
    _fake_systemctl(tmp_path, monkeypatch)
    status, body = _dispatch(_cfg(tmp_path, monkeypatch), "GET", "/api/gateway")
    assert status == 200 and body["ok"] is True, (status, body)
    assert body["supervision"] == "", body["supervision"]
    assert body["process"]["available"] is True, body["process"]
    assert body["process"]["service_unit"] == UNIT, body["process"]


def test_get_gateway_supervision_typed_when_unavailable(tmp_path, monkeypatch):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    status, body = _dispatch(_cfg(tmp_path, monkeypatch), "GET", "/api/gateway")
    assert status == 200 and body["ok"] is True, (status, body)
    assert body["supervision"].startswith("GATEWAY_SUPERVISION_UNAVAILABLE"), body
    assert body["process"]["available"] is False, body["process"]


# --- POST start: real transition vs idempotent no-op ------------------------
def test_start_inactive_unit_runs_start_command(tmp_path, monkeypatch):
    log, state = _fake_systemctl(tmp_path, monkeypatch)
    _patch_supervisor(monkeypatch)
    status, body = _dispatch(_cfg(tmp_path, monkeypatch),
                             "POST", "/api/gateway/start", {"confirm": True})
    assert status == 200 and body["state"] == "OK", (status, body)
    assert any(line.endswith(f"start {UNIT}") for line in
               log.read_text().splitlines()), log.read_text()
    assert state.read_text().strip() == "active"
    assert body["status"]["running"] is True, body["status"]
    assert _cmds(log) <= {"show", "start"}, _cmds(log)


def test_start_on_active_unit_is_idempotent_noop(tmp_path, monkeypatch):
    log, state = _fake_systemctl(tmp_path, monkeypatch)
    state.write_text("active\n")
    _patch_supervisor(monkeypatch)
    status, body = _dispatch(_cfg(tmp_path, monkeypatch),
                             "POST", "/api/gateway/start", {"confirm": True})
    assert status == 200 and body["noop"] is True, (status, body)
    assert _cmds(log) == {"show"}, _cmds(log)


# --- POST restart/stop: detached, never synchronous -------------------------
def test_restart_dispatches_detached_and_returns_restarting(tmp_path,
                                                            monkeypatch):
    log, state = _fake_systemctl(tmp_path, monkeypatch)
    state.write_text("active\n")
    spawned = []
    _patch_supervisor(monkeypatch, spawned=spawned)
    status, body = _dispatch(_cfg(tmp_path, monkeypatch),
                             "POST", "/api/gateway/restart", {"confirm": True})
    assert status == 200 and body["state"] == "RESTARTING", (status, body)
    assert body["detached"] is True, body
    detached = " ".join(map(str, spawned[0]))
    assert len(spawned) == 1 and "restart" in detached and UNIT in detached, spawned
    assert "sleep" in detached, detached
    assert _cmds(log) == {"show"}, _cmds(log)  # not run in the request


def test_stop_active_unit_maps_to_detached_stopping(tmp_path, monkeypatch):
    log, state = _fake_systemctl(tmp_path, monkeypatch)
    state.write_text("active\n")
    spawned = []
    _patch_supervisor(monkeypatch, spawned=spawned)
    status, body = _dispatch(_cfg(tmp_path, monkeypatch),
                             "POST", "/api/gateway/stop", {"confirm": True})
    assert status == 200 and body["state"] == "STOPPING", (status, body)
    assert body["detached"] is True, body
    detached = " ".join(map(str, spawned[0]))
    assert len(spawned) == 1 and "stop" in detached and UNIT in detached, spawned
    assert _cmds(log) == {"show"}, _cmds(log)


def test_control_without_confirmation_is_refused(tmp_path, monkeypatch):
    log, _ = _fake_systemctl(tmp_path, monkeypatch)
    _patch_supervisor(monkeypatch)
    status, body = _dispatch(_cfg(tmp_path, monkeypatch),
                             "POST", "/api/gateway/restart", {"confirm": False})
    assert status == 409, (status, body)
    assert body["code"] == "GATEWAY_CONFIRM_REQUIRED", body
    assert _cmds(log) == set(), _cmds(log)  # refused before any systemctl call
