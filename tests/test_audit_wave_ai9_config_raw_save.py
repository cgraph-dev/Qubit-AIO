"""Audit wave 2026-09-23 (AI9) — raw-YAML config save is a typed, actionable
refusal on the dashboard surface, never a silent/misleading 403.

Defect D-6: ``PUT /agent/api/config/raw`` answered ``SECRET_NEVER_SERVED`` with
a credential-status counterpart (``CLI `qubit auth status` ``), so the
ConfigPage YAML editor only ever showed a "secret" error and the operator was
never pointed at the working confirm-gated path.

The fix is scoped to the Qubit dashboard (SPA ``/agent`` prefix) boundary; the
canonical compat gate (``qubit_compat.dispatch``) is unchanged, so route parity
holds. GET stays a ``SECRET_NEVER_SERVED`` gate because the raw document can
carry secret material.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / "qh"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: test-token\n")
    return C.Config.load(p)


class _FakeOrch:
    def __init__(self, cfg):
        import time

        self.cfg = cfg
        self.task = type("T", (), {"active": False})()
        self._route = "conversation"
        self._t0 = time.time()
        self._web_active = 0
        self._tts = None
        self.shared = None
        self.sticky_route = None
        self.learning = None

    def _needs_answer(self):
        return False


@pytest.fixture()
def ui(tmp_path, monkeypatch):
    from voicebridge.webui import WebUI

    cfg = _cfg(tmp_path, monkeypatch)
    server = WebUI(_FakeOrch(cfg), host="127.0.0.1", port=0, state_dir=tmp_path)
    server.start()
    try:
        yield server, cfg
    finally:
        server.stop()


def _request(ui, method, path, body=None, token="test-token"):
    import urllib.error
    import urllib.request

    data = None
    headers = {"X-Qubit-Session-Token": token}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:{ui._server.server_port}{path}",
        data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def test_put_config_raw_is_actionable_typed_refusal(ui):
    server, _cfg_v = ui
    status, out = _request(
        server, "PUT", "/agent/api/config/raw",
        {"yaml_text": "agent:\n  max_turns: 90\n"})
    assert status == 403, out
    assert out.get("code") == "WRITE_BROKERED", out
    # The refusal is actionable: it names the working confirm-gated path, not
    # the misleading credential-status counterpart.
    assert out.get("qubit_counterpart"), out
    assert "config" in out["qubit_counterpart"].lower(), out
    assert "auth status" not in json.dumps(out).lower(), out


def test_get_config_raw_stays_a_secret_gate(ui):
    server, _cfg_v = ui
    status, out = _request(server, "GET", "/agent/api/config/raw")
    assert status == 403, out
    assert out.get("code") == "SECRET_NEVER_SERVED", out


def test_canonical_compat_config_raw_gate_is_unchanged(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method in ("GET", "PUT"):
        code, out = HC.dispatch(cfg, method, "/api/config/raw", {}, {})
        assert code == 403, (method, out)
        assert out.get("code") == "SECRET_NEVER_SERVED", (method, out)
