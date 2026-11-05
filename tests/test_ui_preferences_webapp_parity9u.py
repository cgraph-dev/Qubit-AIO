"""Reference parity: profile-local UI preferences + honest Webapp access status.

Hermes serves ``/api/ui-preferences`` and ``/api/webapp-access/status`` at
``:9119``; Qubit lacked both. These tests pin the Qubit compatibility surface:

* ``GET/PUT/PATCH /api/ui-preferences`` is a bounded, profile-local document
  with the reference shape (``exists``/``revision``/``values``), typed
  validation, no secret-shaped keys or values, and no cross-profile leak.
* ``GET /api/webapp-access/status`` reports the local/token state and a typed
  UNAVAILABLE for remote Webapp launch/auth, which Qubit does not ship.
* both routes are in the Qubit read authority and require dashboard auth.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch, name="state", token="test-token"):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    state = tmp_path / name
    state.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(
        "logging:\n"
        "  jsonl: true\n"
        f"  jsonl_path: '{state.as_posix()}'\n"
        "webui:\n"
        f"  token: {token}\n"
    )
    return C.Config.load(p)


def _start(tmp_path):
    from voicebridge.webui import WebUI

    cfg = _cfg(tmp_path, _NoOpMonkey())
    state = tmp_path / "state"

    class _FakeOrch:
        _route = "local"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


class _NoOpMonkey:
    def setenv(self, *_args, **_kwargs):
        return None


def _http(port, method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method=method, data=data)
    if token:
        req.add_header("X-Qubit-Session-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        code = e.code
        e.close()
        try:
            return code, json.loads(raw)
        except ValueError:
            return code, {"detail": raw.decode("utf-8", "replace")}


def test_routes_are_in_the_qubit_authority():
    from voicebridge import qubit_compat as QC
    from voicebridge import webui

    for path in ("/api/ui-preferences", "/api/webapp-access/status"):
        assert path in webui.QUBIT_READ_AUTHORITY
    expected = {
        ("GET", "/api/ui-preferences"): "h_ui_preferences",
        ("PUT", "/api/ui-preferences"): "h_ui_preferences_write",
        ("PATCH", "/api/ui-preferences"): "h_ui_preferences_write",
        ("GET", "/api/webapp-access/status"): "h_webapp_access_status",
    }
    for (method, path), handler in expected.items():
        route, _ = QC.match(method, path)
        assert route is not None, (method, path)
        assert route["handler"] == handler
        assert route["disposition"] == "implemented"


def test_get_put_patch_shape_and_persistence(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = QC.dispatch(cfg, "GET", "/api/ui-preferences")
    assert code == 200
    assert out == {"exists": False, "revision": 0, "values": {}}

    code, out = QC.dispatch(
        cfg, "PUT", "/api/ui-preferences", {},
        {"values": {"qubit.theme": "dark", "qubit.sidebar": "wide"}})
    assert code == 200, out
    assert out["exists"] is True
    assert out["revision"] == 1
    assert out["values"] == {"qubit.theme": "dark", "qubit.sidebar": "wide"}
    generation = out["generation"]
    assert isinstance(generation, str) and generation

    code, out = QC.dispatch(cfg, "GET", "/api/ui-preferences")
    assert code == 200
    assert out["revision"] == 1
    assert out["values"] == {"qubit.theme": "dark", "qubit.sidebar": "wide"}
    assert out["generation"] == generation

    code, out = QC.dispatch(
        cfg, "PATCH", "/api/ui-preferences", {},
        {"values": {"qubit.theme": None, "qubit.font": "mono"}})
    assert code == 200, out
    assert out["revision"] == 2
    assert out["values"] == {"qubit.sidebar": "wide", "qubit.font": "mono"}
    assert out["generation"] == generation

    code, out = QC.dispatch(
        cfg, "PUT", "/api/ui-preferences", {}, {"qubit.zoom": "1.1"})
    assert code == 200, out
    assert out["values"]["qubit.zoom"] == "1.1"

    path = Path(cfg.state_dir()) / "ui-preferences.json"
    assert path.is_file()
    if hasattr(path.stat(), "st_mode"):
        assert path.stat().st_mode & 0o777 == 0o600
    doc = json.loads(path.read_text())
    assert doc["version"] == 1
    assert doc["generation"] == generation
    assert doc["values"]["qubit.zoom"] == "1.1"


def test_seed_only_adds_missing_keys(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    QC.dispatch(cfg, "PUT", "/api/ui-preferences", {},
                {"values": {"qubit.theme": "dark"}})
    code, out = QC.dispatch(
        cfg, "PATCH", "/api/ui-preferences", {},
        {"seed": True,
         "values": {"qubit.theme": "light",
                    "qubit.rail": "collapsed",
                    "qubit.deleted": None}})
    assert code == 200, out
    assert out["values"] == {"qubit.theme": "dark",
                             "qubit.rail": "collapsed"}


def test_typed_validation_and_bounds(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC
    from voicebridge import ui_preferences as UP

    cfg = _cfg(tmp_path, monkeypatch)
    cases = [
        {"values": ["not", "a", "map"]},
        {"values": {"qubit.count": 3}},
        {"values": {"x" * (UP.MAX_KEY_CHARS + 1): "v"}},
        {"values": {"qubit.long": "x" * (UP.MAX_VALUE_CHARS + 1)}},
        {"values": {"qubit.nul": "a\x00b"}},
        {"values": {"qubit.pem": "-----BEGIN RSA PRIVATE KEY-----\nabc\n"}},
        {"values": {"qubit.bearer": "Bearer " + "a" * 40}},
        {"values": {("k%03d" % i): "v" for i in range(UP.MAX_KEYS + 1)}},
    ]
    for body in cases:
        code, out = QC.dispatch(cfg, "PUT", "/api/ui-preferences", {}, body)
        assert code == 400, (body, code, out)
        assert out["code"] == "UI_PREFERENCES_INVALID"
    code, out = QC.dispatch(cfg, "GET", "/api/ui-preferences")
    assert out["values"] == {}


@pytest.mark.parametrize("key", [
    "auth.token", "api_key", "desktop.password", "provider.secret",
    "qubit.private-key", "credentials",
])
def test_secret_like_keys_are_rejected(tmp_path, monkeypatch, key):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = QC.dispatch(
        cfg, "PUT", "/api/ui-preferences", {}, {"values": {key: "value"}})
    assert code == 400
    assert out["code"] == "UI_PREFERENCES_INVALID"
    code, out = QC.dispatch(cfg, "GET", "/api/ui-preferences")
    assert out["values"] == {}
    assert key not in json.dumps(out)


def test_get_never_serves_environment_or_config_secrets(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    monkeypatch.setenv("QUBIT_TEST_SECRET", "SUPER_SECRET_VALUE_123")
    cfg = _cfg(tmp_path, monkeypatch)
    QC.dispatch(cfg, "PUT", "/api/ui-preferences", {},
                {"values": {"qubit.theme": "dark"}})
    code, out = QC.dispatch(cfg, "GET", "/api/ui-preferences")
    assert code == 200
    blob = json.dumps(out)
    assert "SUPER_SECRET_VALUE_123" not in blob
    assert "test-token" not in blob
    assert out["values"] == {"qubit.theme": "dark"}


def test_store_is_profile_local(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    one = _cfg(tmp_path, monkeypatch, name="state-one")
    two = _cfg(tmp_path, monkeypatch, name="state-two")
    assert one.state_dir() != two.state_dir()

    QC.dispatch(one, "PUT", "/api/ui-preferences", {},
                {"values": {"qubit.theme": "dark"}})
    code, out = QC.dispatch(two, "GET", "/api/ui-preferences")
    assert code == 200
    assert out == {"exists": False, "revision": 0, "values": {}}

    QC.dispatch(two, "PUT", "/api/ui-preferences", {},
                {"values": {"qubit.theme": "light"}})
    code, out = QC.dispatch(one, "GET", "/api/ui-preferences")
    assert out["values"] == {"qubit.theme": "dark"}


def test_webapp_status_is_typed_unavailable(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(QC, "_nous_signed_in", lambda: False)
    monkeypatch.setattr(QC, "_lan_address", lambda: None)

    code, out = QC.dispatch(cfg, "GET", "/api/webapp-access/status")
    assert code == 200
    assert out["ok"] is False
    assert out["state"] == "UNAVAILABLE"
    assert out["code"] == "WEBAPP_ACCESS_NOT_IMPLEMENTED"
    assert out["implemented"] is False
    assert out["available"] is False
    assert out["enabled"] is False
    assert out["mode"] is None
    assert out["public_url"] == ""
    assert out["gateway"]["state"] == "unavailable"
    assert out["gateway"]["url"] is None
    assert out["local"]["host"] == "127.0.0.1"
    assert out["local"]["port"] == 8890
    assert out["local"]["loopback_only"] is True
    assert out["local"]["token_configured"] is True
    assert out["local"]["token_required"] is True
    assert isinstance(out["nous_signed_in"], bool)
    assert out["lan_address"] is None
    blob = json.dumps(out)
    assert "test-token" not in blob


def test_http_routes_require_auth_and_persist(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    monkeypatch.setattr(QC, "_nous_signed_in", lambda: False)
    monkeypatch.setattr(QC, "_lan_address", lambda: None)
    ui, port = _start(tmp_path)
    try:
        for path in ("/api/ui-preferences", "/agent/api/ui-preferences",
                     "/api/webapp-access/status",
                     "/agent/api/webapp-access/status"):
            status, _ = _http(port, "GET", path)
            assert status == 401, path

        status, out = _http(port, "GET", "/api/ui-preferences",
                            token="test-token")
        assert status == 200
        assert out["exists"] is False

        status, out = _http(port, "PUT", "/api/ui-preferences",
                            {"values": {"qubit.theme": "dark"}},
                            token="test-token")
        assert status == 200, out
        assert out["values"] == {"qubit.theme": "dark"}

        status, out = _http(port, "PATCH", "/agent/api/ui-preferences",
                            {"values": {"qubit.theme": "light"}},
                            token="test-token")
        assert status == 200, out
        assert out["values"] == {"qubit.theme": "light"}

        status, out = _http(port, "GET", "/agent/api/ui-preferences",
                            token="test-token")
        assert status == 200
        assert out["values"] == {"qubit.theme": "light"}

        status, out = _http(port, "GET", "/api/webapp-access/status",
                            token="test-token")
        assert status == 200, out
        assert out["code"] == "WEBAPP_ACCESS_NOT_IMPLEMENTED"

        status, _ = _http(port, "PUT", "/api/ui-preferences",
                          {"values": {"qubit.theme": "hijack"}})
        assert status == 401
        status, out = _http(port, "GET", "/api/ui-preferences",
                            token="test-token")
        assert out["values"] == {"qubit.theme": "light"}
    finally:
        ui.stop()
