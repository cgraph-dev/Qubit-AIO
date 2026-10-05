import asyncio
import time
import pytest
from fastapi import HTTPException
import qubit_cli.config as _cfg_mod
import qubit_cli.web_models as _web_models
import qubit_cli.web_routers.messaging as _rt_messaging
import qubit_cli.web_server_messaging as _web_server_messaging


class _FakeProc:
    def __init__(self, lines=None, returncode=0):
        self.stdout = iter(lines or [])
        self._returncode = returncode
        self.terminated = False
        self.killed = False
        self.pid = 12345

    def poll(self):
        return None if not self.terminated and not self.killed else self._returncode

    def wait(self, timeout=None):
        return self._returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True








def test_apply_whatsapp_onboarding_saves_pairing_policy(monkeypatch):
    from qubit_cli import web_server as ws

    saved = {}
    removed = []
    enabled = []

    monkeypatch.setattr(_cfg_mod, "save_env_value", lambda key, value: saved.setdefault(key, value))
    monkeypatch.setattr(_cfg_mod, "remove_env_value", lambda key: removed.append(key))
    monkeypatch.setattr(_web_server_messaging, "_write_platform_enabled", lambda platform, value: enabled.append((platform, value)))
    monkeypatch.setattr(
        _web_server_messaging,
        "_restart_gateway_after_whatsapp_onboarding",
        lambda profile=None: {"restart_started": True, "restart_pid": 12345},
    )

    record = _web_server_messaging._WhatsAppOnboardingSession(
        proc=None,
        mode="bot",
        allowed_users="",
        session_path="/tmp/session",
        expires_at="2099-01-01T00:00:00Z",
        expires_at_ts=time.time() + 600,
        status="connected",
    )
    _web_server_messaging._whatsapp_onboarding_sessions.clear()
    _web_server_messaging._whatsapp_onboarding_sessions["pairing"] = record

    result = asyncio.run(
        _rt_messaging.apply_whatsapp_onboarding(
            "pairing",
            _web_models.WhatsAppOnboardingApply(mode="bot", allowed_users=""),
        )
    )

    assert result["ok"] is True
    assert saved["WHATSAPP_MODE"] == "bot"
    assert saved["WHATSAPP_DM_POLICY"] == "pairing"
    assert saved["WHATSAPP_ENABLED"] == "true"
    assert "WHATSAPP_ALLOWED_USERS" not in removed
    assert enabled == [("whatsapp", True)]
    assert "pairing" not in _web_server_messaging._whatsapp_onboarding_sessions


def test_start_whatsapp_onboarding_existing_creds_returns_linked_account(monkeypatch, tmp_path):
    from qubit_cli import web_server as ws

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "creds.json").write_text(
        '{"me":{"id":"15551234567:1@s.whatsapp.net","name":"Hermes Bot"}}',
        encoding="utf-8",
    )

    old_proc = _FakeProc(returncode=1)
    old_record = _web_server_messaging._WhatsAppOnboardingSession(
        proc=old_proc,
        mode="bot",
        allowed_users="",
        session_path=str(session_dir),
        expires_at="2099-01-01T00:00:00Z",
        expires_at_ts=time.time() + 600,
    )
    _web_server_messaging._whatsapp_onboarding_sessions.clear()
    _web_server_messaging._whatsapp_onboarding_sessions["old"] = old_record
    monkeypatch.setattr(_web_server_messaging, "_whatsapp_session_path", lambda: session_dir)
    monkeypatch.setattr(ws.secrets, "token_urlsafe", lambda size: "existing-creds")

    result = asyncio.run(
        _rt_messaging.start_whatsapp_onboarding(
            _web_models.WhatsAppOnboardingStart(mode="self-chat", allowed_users="")
        )
    )

    assert result["pairing_id"] == "existing-creds"
    assert result["status"] == "connected"
    assert result["qr_payload"] is None
    assert result["account_id"] == "15551234567:1@s.whatsapp.net"
    assert result["account_name"] == "Hermes Bot"
    assert result["account_phone"] == "15551234567"
    assert old_record.status == "cancelled"
    assert old_proc.terminated is True
    assert _web_server_messaging._whatsapp_onboarding_sessions["existing-creds"].account_phone == "15551234567"
    _web_server_messaging._whatsapp_onboarding_sessions.clear()


def test_start_whatsapp_onboarding_empty_creds_starts_fresh_pairing(monkeypatch, tmp_path):
    from qubit_cli import web_server as ws

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "creds.json").write_text("", encoding="utf-8")

    _web_server_messaging._whatsapp_onboarding_sessions.clear()
    monkeypatch.setattr(_web_server_messaging, "_whatsapp_session_path", lambda: session_dir)
    monkeypatch.setattr(ws.secrets, "token_urlsafe", lambda size: "fresh-creds")
    monkeypatch.setattr(_rt_messaging, "_run_whatsapp_pairing", lambda *args: None)

    result = asyncio.run(
        _rt_messaging.start_whatsapp_onboarding(
            _web_models.WhatsAppOnboardingStart(mode="self-chat", allowed_users="")
        )
    )

    assert result["pairing_id"] == "fresh-creds"
    assert result["status"] != "connected"
    _web_server_messaging._whatsapp_onboarding_sessions.clear()


def test_start_whatsapp_onboarding_garbage_creds_starts_fresh_pairing(monkeypatch, tmp_path):
    from qubit_cli import web_server as ws

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "creds.json").write_text("not-json{{{", encoding="utf-8")

    _web_server_messaging._whatsapp_onboarding_sessions.clear()
    monkeypatch.setattr(_web_server_messaging, "_whatsapp_session_path", lambda: session_dir)
    monkeypatch.setattr(ws.secrets, "token_urlsafe", lambda size: "fresh-creds-2")
    monkeypatch.setattr(_rt_messaging, "_run_whatsapp_pairing", lambda *args: None)

    result = asyncio.run(
        _rt_messaging.start_whatsapp_onboarding(
            _web_models.WhatsAppOnboardingStart(mode="self-chat", allowed_users="")
        )
    )

    assert result["pairing_id"] == "fresh-creds-2"
    assert result["status"] != "connected"
    _web_server_messaging._whatsapp_onboarding_sessions.clear()


def test_start_whatsapp_onboarding_pairs_in_isolated_dir(monkeypatch, tmp_path):
    from qubit_cli import web_server as ws

    session_dir = tmp_path / "session"
    session_dir.mkdir()

    _web_server_messaging._whatsapp_onboarding_sessions.clear()
    monkeypatch.setattr(_web_server_messaging, "_whatsapp_session_path", lambda: session_dir)
    monkeypatch.setattr(ws.secrets, "token_urlsafe", lambda size: "isolated-creds")
    monkeypatch.setattr(_rt_messaging, "_run_whatsapp_pairing", lambda *args: None)

    result = asyncio.run(
        _rt_messaging.start_whatsapp_onboarding(
            _web_models.WhatsAppOnboardingStart(mode="bot", allowed_users="")
        )
    )

    record = _web_server_messaging._whatsapp_onboarding_sessions["isolated-creds"]
    assert record.pairing_session_path is not None
    from pathlib import Path as _Path

    pairing_dir = _Path(record.pairing_session_path)
    assert pairing_dir.parent == session_dir.parent
    assert pairing_dir.name.startswith("session.pairing-")
    assert pairing_dir.is_dir()
    assert result["status"] != "connected"
    _web_server_messaging._whatsapp_onboarding_sessions.clear()


def test_apply_whatsapp_onboarding_promotes_paired_session(monkeypatch, tmp_path):
    from qubit_cli import web_server as ws

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    pairing_dir = tmp_path / "session.pairing-test"
    pairing_dir.mkdir()
    (pairing_dir / "creds.json").write_text(
        '{"me":{"id":"15551234567:1@s.whatsapp.net","name":"Qubit Bot"}}',
        encoding="utf-8",
    )

    saved = {}
    monkeypatch.setattr(_cfg_mod, "save_env_value", lambda key, value: saved.setdefault(key, value))
    monkeypatch.setattr(_cfg_mod, "remove_env_value", lambda key: None)
    monkeypatch.setattr(_web_server_messaging, "_write_platform_enabled", lambda platform, value: None)
    monkeypatch.setattr(
        _web_server_messaging,
        "_restart_gateway_after_whatsapp_onboarding",
        lambda profile=None: {"restart_started": True, "restart_pid": 12345},
    )

    record = _web_server_messaging._WhatsAppOnboardingSession(
        proc=None,
        mode="bot",
        allowed_users="",
        session_path=str(session_dir),
        expires_at="2099-01-01T00:00:00Z",
        expires_at_ts=time.time() + 600,
        status="connected",
        account_id="15551234567:1@s.whatsapp.net",
        pairing_session_path=str(pairing_dir),
    )
    _web_server_messaging._whatsapp_onboarding_sessions.clear()
    _web_server_messaging._whatsapp_onboarding_sessions["pairing"] = record

    result = asyncio.run(
        _rt_messaging.apply_whatsapp_onboarding(
            "pairing",
            _web_models.WhatsAppOnboardingApply(mode="bot", allowed_users=""),
        )
    )

    assert result["ok"] is True
    assert not pairing_dir.exists()
    assert (session_dir / "creds.json").is_file()
    assert saved["WHATSAPP_ENABLED"] == "true"
    assert "pairing" not in _web_server_messaging._whatsapp_onboarding_sessions
    _web_server_messaging._whatsapp_onboarding_sessions.clear()


def test_apply_whatsapp_onboarding_rejects_invalid_paired_creds(monkeypatch, tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    pairing_dir = tmp_path / "session.pairing-bad"
    pairing_dir.mkdir()
    (pairing_dir / "creds.json").write_text("", encoding="utf-8")

    record = _web_server_messaging._WhatsAppOnboardingSession(
        proc=None,
        mode="bot",
        allowed_users="",
        session_path=str(session_dir),
        expires_at="2099-01-01T00:00:00Z",
        expires_at_ts=time.time() + 600,
        status="connected",
        pairing_session_path=str(pairing_dir),
    )
    _web_server_messaging._whatsapp_onboarding_sessions.clear()
    _web_server_messaging._whatsapp_onboarding_sessions["pairing"] = record

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            _rt_messaging.apply_whatsapp_onboarding(
                "pairing",
                _web_models.WhatsAppOnboardingApply(mode="bot", allowed_users=""),
            )
        )

    assert excinfo.value.status_code == 409
    assert not (session_dir / "creds.json").exists()
    _web_server_messaging._whatsapp_onboarding_sessions.clear()


def test_cancel_whatsapp_onboarding_removes_pairing_dir(monkeypatch, tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    pairing_dir = tmp_path / "session.pairing-cancel"
    pairing_dir.mkdir()

    record = _web_server_messaging._WhatsAppOnboardingSession(
        proc=None,
        mode="bot",
        allowed_users="",
        session_path=str(session_dir),
        expires_at="2099-01-01T00:00:00Z",
        expires_at_ts=time.time() + 600,
        pairing_session_path=str(pairing_dir),
    )
    _web_server_messaging._whatsapp_onboarding_sessions.clear()
    _web_server_messaging._whatsapp_onboarding_sessions["pairing"] = record

    result = asyncio.run(_rt_messaging.cancel_whatsapp_onboarding("pairing"))

    assert result["ok"] is True
    assert not pairing_dir.exists()
    assert session_dir.is_dir()
    _web_server_messaging._whatsapp_onboarding_sessions.clear()





