"""Packet 8T-F — Hermes login/logout/portal parity via manual handoff.

Proves enrollment is guidance-only (nothing written, captured, or
opened unless asked), verification is a read-only reference probe, and
secret values never appear in output.
"""
from __future__ import annotations

import io
import json
import socket
import subprocess
from contextlib import redirect_stdout
from pathlib import Path


def _cfg(tmp_path, monkeypatch, providers=None):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    provs = providers if providers is not None else {
        "acme": {"key_ref": "env:ACME_API_KEY"},
        "files": {"key_ref": "file:/tmp/8tf-al-test-key.txt"},
        "bare": {},
    }
    cfg_text = "intent:\n  frontier:\n    enabled: false\n    providers:\n"
    for name, rec in provs.items():
        cfg_text += f"      {name}:\n"
        for k, v in rec.items():
            cfg_text += f"        {k}: \"{v}\"\n"
    p = tmp_path / "voice.yaml"
    p.write_text(cfg_text)
    return C.Config.load(p)


def _before(cfg):
    state = Path(cfg.state_dir())
    return {p.name for p in state.iterdir()} if state.is_dir() else set()


def test_login_plan_env_kind_no_values(tmp_path, monkeypatch):
    from voicebridge import auth_login as AL

    monkeypatch.setenv("ACME_API_KEY", "sk-live-s3cr3t-value-999")
    cfg = _cfg(tmp_path, monkeypatch)
    before = _before(cfg)
    out = AL.login_plan(cfg, "acme")
    assert out["state"] == "OK" and out["key_ref"] == "env"
    assert len(out["steps"]) >= 5
    assert "2FA" in " ".join(out["steps"]) and "CAPTCHA" in " ".join(out["steps"])
    blob = json.dumps(out)
    assert "sk-live-s3cr3t-value-999" not in blob, "values never echoed"
    assert out["values_captured"] is False and out["opened"] is False
    assert _before(cfg) == before, "guidance writes nothing"


def test_login_plan_file_and_missing_kinds(tmp_path, monkeypatch):
    from voicebridge import auth_login as AL

    cfg = _cfg(tmp_path, monkeypatch)
    out = AL.login_plan(cfg, "files")
    assert out["state"] == "OK" and out["key_ref"] == "file"
    assert any("chmod 600" in s for s in out["steps"])
    out = AL.login_plan(cfg, "bare")
    assert out["state"] == "OK" and out["key_ref"] == "missing"
    assert any("key_ref" in s for s in out["steps"])


def test_login_unknown_provider_typed(tmp_path, monkeypatch):
    from voicebridge import auth_login as AL

    cfg = _cfg(tmp_path, monkeypatch)
    out = AL.login_plan(cfg, "nope")
    assert out["state"] == "UNKNOWN" and out["code"] == "AUTH_UNKNOWN_PROVIDER"
    assert set(out["known_providers"]) == {"acme", "files", "bare"}


def test_login_never_touches_network(tmp_path, monkeypatch):
    from voicebridge import auth_login as AL

    cfg = _cfg(tmp_path, monkeypatch)

    def _no_net(*a, **k):
        raise AssertionError("network must never be touched")

    monkeypatch.setattr(socket, "create_connection", _no_net)
    monkeypatch.setattr(subprocess, "run", _no_net)
    out = AL.login_plan(cfg, "acme")
    assert out["state"] == "OK"


def test_login_open_only_opens_documented_portal(tmp_path, monkeypatch):
    import webbrowser

    from voicebridge import auth_login as AL

    cfg = _cfg(tmp_path, monkeypatch, providers={"xai": {"key_ref": "env:XAI_KEY"}})
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    out = AL.login_plan(cfg, "xai", open_browser=True)
    assert out["opened"] is True
    assert opened == ["https://console.x.ai/team/api-keys"]
    out = AL.login_plan(cfg, "xai")
    assert out["opened"] is False and opened == ["https://console.x.ai/team/api-keys"]


def test_verify_transitions(tmp_path, monkeypatch):
    from voicebridge import auth_login as AL

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.delenv("ACME_API_KEY", raising=False)
    assert AL.verify_provider(cfg, "acme")["state"] == "MISSING"
    monkeypatch.setenv("ACME_API_KEY", "present")
    out = AL.verify_provider(cfg, "acme")
    assert out["state"] == "CONFIGURED" and out["allowlisted"] is False
    key = tmp_path / "k.txt"
    cfg2 = _cfg(tmp_path, monkeypatch,
                providers={"f": {"key_ref": f"file:{key}"}})
    assert AL.verify_provider(cfg2, "f")["state"] == "MISSING"
    key.write_text("k")
    assert AL.verify_provider(cfg2, "f")["state"] == "CONFIGURED"
    key.write_text("")
    assert AL.verify_provider(cfg2, "f")["state"] == "MISSING", \
        "empty key files do not count"
    assert AL.verify_provider(cfg2, "ghost")["code"] == "AUTH_UNKNOWN_PROVIDER"


def test_cli_wiring_and_redaction(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    monkeypatch.setenv("ACME_API_KEY", "sk-live-s3cr3t-value-999")
    cfg = _cfg(tmp_path, monkeypatch)

    class _A:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_auth_login(cfg, _A(provider="acme", open=False)) == 0
    assert "sk-live-s3cr3t-value-999" not in buf.getvalue()
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_auth_verify(cfg, _A(provider="acme")) == 0
    assert json.loads(buf.getvalue())["state"] == "CONFIGURED"
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_auth_login(cfg, _A(provider="nope", open=False)) == 2
    assert "auth" in CLI.build_parser().format_help()


def test_capability_catalog_reports_login_implemented():
    from voicebridge.capability_catalog import check

    for token in ("login", "logout", "portal"):
        out = check(token)
        assert out["state"] == "IMPLEMENTED", (token, out)
