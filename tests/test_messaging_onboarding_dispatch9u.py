"""Onboarding dispatch proof (Telegram/WhatsApp QR family).

The compat handlers (``h_tg_ob_*``/``h_wa_ob_*``) delegate to the vendored
onboarding implementation. These tests prove, through the production
dispatcher and without touching the network, that unknown sessions get
honest misses (not fabricated pendings), cancel is a real removal, and
apply validates before touching state.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_onboarding_routes_are_registered_live(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    _cfg(tmp_path, monkeypatch)
    for method, path, handler in [
        ("POST", "/api/messaging/telegram/onboarding/start", "h_tg_ob_start"),
        ("GET", "/api/messaging/telegram/onboarding/abc", "h_tg_ob_status"),
        ("POST", "/api/messaging/telegram/onboarding/abc/apply", "h_tg_ob_apply"),
        ("DELETE", "/api/messaging/telegram/onboarding/abc", "h_tg_ob_cancel"),
        ("POST", "/api/messaging/whatsapp/onboarding/start", "h_wa_ob_start"),
        ("GET", "/api/messaging/whatsapp/onboarding/abc", "h_wa_ob_status"),
        ("POST", "/api/messaging/whatsapp/onboarding/abc/apply", "h_wa_ob_apply"),
        ("DELETE", "/api/messaging/whatsapp/onboarding/abc", "h_wa_ob_cancel"),
    ]:
        route, _ = HC.match(method, path)
        assert route is not None and route["disposition"] == "implemented", (
            method, path)
        assert route["handler"] == handler, (method, path, route)


def test_onboarding_unknown_sessions_are_honest_misses(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "GET", "/api/messaging/telegram/onboarding/nope", {}, {})
    assert code == 404, (code, out)
    code, out = HC.dispatch(
        cfg, "GET", "/api/messaging/whatsapp/onboarding/nope", {}, {})
    assert code == 404, (code, out)


def test_onboarding_cancel_unknown_is_ok_false(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for path in ("/api/messaging/telegram/onboarding/nope",
                 "/api/messaging/whatsapp/onboarding/nope"):
        code, out = HC.dispatch(cfg, "DELETE", path, {}, {})
        assert code == 200 and out.get("ok") is True, (path, code, out)


def test_onboarding_apply_validates_before_state(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    # Telegram: non-numeric ids are a 400, before any session lookup.
    code, out = HC.dispatch(
        cfg, "POST", "/api/messaging/telegram/onboarding/nope/apply", {},
        {"allowed_user_ids": ["not-a-number"]})
    assert code == 400, (code, out)
    # Telegram: a well-formed body against an unknown session is a 404.
    code, out = HC.dispatch(
        cfg, "POST", "/api/messaging/telegram/onboarding/nope/apply", {},
        {"allowed_user_ids": ["123456"]})
    assert code == 404, (code, out)
    # WhatsApp: a well-formed apply against an unknown session is a 404.
    code, out = HC.dispatch(
        cfg, "POST", "/api/messaging/whatsapp/onboarding/nope/apply", {},
        {"mode": "bot", "allowed_users": ""})
    assert code == 404, (code, out)
