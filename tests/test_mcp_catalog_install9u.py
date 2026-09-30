"""MCP catalog install (owner decision: install like Hermes, fetch nothing).

Proves, through the production compat dispatcher on an isolated home:

* unknown entry -> typed 404; no confirm -> canonical 409;
* env outside the entry's declared schema -> typed 400; required declared
  vars missing -> typed 400;
* confirmed install registers the entry's transport in the canonical Qubit
  MCP registry with ``env:NAME`` references only, stores credential values
  in the runtime .env (never echoed), and audits — proven with stubbed
  registry/env boundaries (no network);
* the response never echoes a credential value.
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


def test_install_unknown_entry_is_typed_404(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "POST", "/api/mcp/catalog/install", {},
        {"name": "zzz-nope", "confirm": True})
    assert code == 404
    assert out["code"] == "MCP_CATALOG_NOT_FOUND"


def test_install_without_confirm_stages_409(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "POST", "/api/mcp/catalog/install", {},
        {"name": "alltrails"})
    assert code == 409
    assert out["code"] == "CONFIRM_REQUIRED"


def test_install_rejects_undeclared_and_missing_env(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "POST", "/api/mcp/catalog/install", {},
        {"name": "alltrails", "env": {"EVIL": "x"}, "confirm": True})
    assert code == 400
    assert out["code"] == "MCP_ENV_UNDECLARED"
    code, out = HC.dispatch(
        cfg, "POST", "/api/mcp/catalog/install", {},
        {"name": "asana", "env": {}, "confirm": True})
    assert code == 400
    assert out["code"] == "MCP_ENV_REQUIRED"


def test_install_registers_transport_and_refs_without_echo(tmp_path,
                                                           monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import env_store as ES
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    written = {}

    def _fake_secret(cfg_, name, value):
        written[name] = value
        return {"name": name}

    monkeypatch.setattr(ES, "write_secret", _fake_secret)
    seen = {}

    def _fake_upsert(cfg_, name, fields, confirmed=False):
        seen.update({"name": name, "fields": dict(fields)})
        return {"state": "OK", "server": {"name": name}}

    monkeypatch.setattr(MS, "upsert", _fake_upsert)
    code, out = HC.dispatch(
        cfg, "POST", "/api/mcp/catalog/install", {},
        {"name": "asana",
         "env": {"ASANA_CLIENT_ID": "cid123",
                 "ASANA_CLIENT_SECRET": "sekret456"},
         "confirm": True})
    assert code == 200, out
    assert out["ok"] is True and out["name"] == "asana", out
    assert out["credentials_stored"] == ["ASANA_CLIENT_ID",
                                         "ASANA_CLIENT_SECRET"]
    # Secrets reached the .env writer; the registry got references only.
    assert written == {"ASANA_CLIENT_ID": "cid123",
                       "ASANA_CLIENT_SECRET": "sekret456"}
    assert seen["fields"]["env"] == {"ASANA_CLIENT_ID": "env:ASANA_CLIENT_ID",
                                     "ASANA_CLIENT_SECRET":
                                     "env:ASANA_CLIENT_SECRET"}
    assert seen["fields"]["transport"] == "http"
    assert "sekret456" not in str(out) and "cid123" not in str(out)


def test_install_inlines_non_secret_url_template(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import env_store as ES
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(ES, "write_secret",
                        lambda c, n, v: {"name": n})
    seen = {}

    def _fake_upsert(cfg_, name, fields, confirmed=False):
        seen.update({"name": name, "fields": dict(fields)})
        return {"state": "OK", "server": {"name": name}}

    monkeypatch.setattr(MS, "upsert", _fake_upsert)
    code, out = HC.dispatch(
        cfg, "POST", "/api/mcp/catalog/install", {},
        {"name": "n8n-official",
         "env": {"N8N_MCP_SERVER_URL": "https://n8n.example.com/mcp"},
         "confirm": True})
    assert code == 200, out
    assert seen["fields"]["url"] == "https://n8n.example.com/mcp", seen
    assert "${" not in seen["fields"]["url"]
