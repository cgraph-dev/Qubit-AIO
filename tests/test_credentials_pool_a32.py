"""A32 — Qubit-native credential-pool inventory + removal (redacted)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "h"))
    return C.Config({})


def test_pool_routes_are_registered():
    from voicebridge import qubit_compat as HC

    get_route, _ = HC.match("GET", "/api/qubit/credentials/pool")
    del_route, params = HC.match(
        "DELETE", "/api/qubit/credentials/pool/acme/2")
    assert get_route and get_route["handler"] == "h_credentials_pool"
    assert del_route and del_route["handler"] == "h_credentials_pool_remove"
    assert params.get("provider") == "acme" and params.get("index") == "2"


def test_inventory_is_redacted(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = HC.dispatch(cfg, "GET", "/api/qubit/credentials/pool", {}, {})
    assert code == 200
    assert "providers" in body
    for provider in body["providers"]:
        for entry in provider["entries"]:
            assert entry["token_preview"] == ""
            assert "access_token" not in entry
            assert "api_key" not in entry


def test_remove_requires_confirmation(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = HC.dispatch(
        cfg, "DELETE", "/api/qubit/credentials/pool/acme/1", {}, {})
    assert code == 409
    assert body["code"] == "CREDENTIAL_CONFIRM_REQUIRED"


def test_remove_missing_is_typed(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = HC.dispatch(
        cfg, "DELETE", "/api/qubit/credentials/pool/acme/1", {},
        {"confirm": True})
    assert code in (404, 503)
    assert body["ok"] is False
