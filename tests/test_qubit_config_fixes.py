"""Impl-4 Hermes compat defect repairs: UF-1, UF-2, UF-3.

Hermetic: temp XDG dirs + a real ``voicebridge.config.Config``; the kanban
attachment success case seeds the real vendored kanban store through the real
engine. No network.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed_vendored_attachment(cfg) -> int:
    from voicebridge import qubit_plugins as HP

    engine = HP._kanban_engine(cfg)
    kb = engine.kanban_db
    with engine._board_conn(None) as (board, conn):
        kb.init_db(board=board)
        task_id = kb.create_task(conn, title="impl4 attachment", board=board)
        return kb.store_attachment_bytes(
            conn, task_id, "hello.txt", b"impl4-bytes",
            content_type="text/plain", uploaded_by="test", board=board)


# ---------------------------------------------------------------------------
# UF-3 — /api/config|/defaults|/schema return the real wire shape
# ---------------------------------------------------------------------------
def test_config_schema_has_real_fields_and_order(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/config/schema", {}, {})
    assert code == 200, out
    assert isinstance(out["fields"], dict) and out["fields"], out
    assert "agent.enabled" in out["fields"]
    assert out["fields"]["agent.enabled"]["type"] == "boolean"
    assert isinstance(out["category_order"], list) and out["category_order"]
    assert "agent" in out["category_order"], out["category_order"]


def test_config_read_returns_live_record(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert code == 200 and isinstance(out, dict), (code, out)
    assert out["agent"]["enabled"] is True, out
    code, defaults = HC.dispatch(cfg, "GET", "/api/config/defaults", {}, {})
    assert code == 200 and "agent" in defaults, (code, defaults)


# ---------------------------------------------------------------------------
# UF-2 — PUT /api/config never fabricates success
# ---------------------------------------------------------------------------
def test_config_put_applies_nested_body_and_reads_back(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    body = {"config": {"vad": {"enabled": False}}}
    # Without confirm -> typed staging, never fabricated success.
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {}, body)
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    # Confirm -> the nested body really applies and persists.
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {**body, "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["count"] == 1 and out["config"]["vad.enabled"] is False, out
    assert out["rollback"], out
    code, live = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert live["vad"]["enabled"] is False, live.get("vad")
    # A body with nothing applicable is a typed refusal, not ok=True count=0.
    code, empty = HC.dispatch(cfg, "PUT", "/api/config", {},
                              {"config": {}, "confirm": True})
    assert code != 200 and empty["ok"] is False, (code, empty)
    assert empty["code"] == "CONFIG_NO_FIELDS", empty


def test_config_put_secret_never_persisted(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"api_key": "sk-live", "confirm": True})
    assert (code, out["code"]) == (403, "SECRET_WRITE_REFUSED"), (code, out)


# ---------------------------------------------------------------------------
# UF-1 — kanban attachment download is a real backend operation
# ---------------------------------------------------------------------------
def test_kanban_attachment_route_is_registered():
    from voicebridge import qubit_compat as HC

    route, params = HC.match("GET", "/api/plugins/kanban/attachments/7")
    assert route is not None, "attachment download must not be a dead 404"
    assert route["handler"] == "h_plugin_kanban_attachment_download", route
    assert route["disposition"] == "implemented", route
    assert params == {"attachment_id": "7"}, params
    assert callable(HC.HANDLERS.get(route["handler"]))


def test_kanban_attachment_download_serves_real_bytes(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    att_id = _seed_vendored_attachment(cfg)
    code, out = HC.dispatch(
        cfg, "GET", f"/api/plugins/kanban/attachments/{att_id}", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["filename"] == "hello.txt", out
    assert out["content_type"] == "text/plain", out
    assert base64.b64decode(out["data_base64"]) == b"impl4-bytes", out


def test_kanban_attachment_missing_is_typed(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/kanban/attachments/999999", {}, {})
    assert code == 404 and out["ok"] is False, (code, out)
    assert out["code"] == "PLUGIN_HTTP", out


# ---------------------------------------------------------------------------
# UF-2 hardening — unknown/mistyped settings are typed refusals, not success
# ---------------------------------------------------------------------------
def test_config_put_rejects_unknown_and_malformed(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"config": {"typo": {"enabled": True}},
                             "confirm": True})
    assert code == 403 and out["code"] == "UNKNOWN_CONFIG_KEY", (code, out)
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"config": "nope", "confirm": True})
    assert code == 400 and out["code"] == "MALFORMED", (code, out)
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"config": {"agent": {"enabled": "yes"}},
                             "confirm": True})
    assert code == 403 and out["code"] == "BAD_CONFIG_TYPE", (code, out)
    live = HC.dispatch(cfg, "GET", "/api/config", {}, {})[1]
    assert "typo" not in live


def test_config_put_list_settings_are_typed(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"config": {"safety": {"denylist": "not-a-list"}},
                             "confirm": True})
    assert code == 403 and out["code"] == "BAD_CONFIG_TYPE", (code, out)
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"config": {"safety": {"denylist": ["x", "y"]}},
                             "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    live = HC.dispatch(cfg, "GET", "/api/config", {}, {})[1]
    assert live["safety"]["denylist"] == ["x", "y"]


# ---------------------------------------------------------------------------
# voice.input_engine — canonical speech-INPUT selection (browser|local|auto)
# ---------------------------------------------------------------------------
def test_voice_input_engine_is_a_real_served_setting(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, live = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert code == 200, live
    assert live["voice"]["input_engine"] == "auto", live.get("voice")
    # The Settings form derives descriptors from the same DEFAULTS tree.
    code, schema = HC.dispatch(cfg, "GET", "/api/config/schema", {}, {})
    assert code == 200, schema
    assert "voice.input_engine" in schema["fields"], schema["fields"].keys()
    assert "voice" in schema["category_order"], schema["category_order"]


def test_voice_input_engine_normalizes_out_of_contract_values():
    from voicebridge.config import VOICE_INPUT_ENGINES, normalize_input_engine

    assert VOICE_INPUT_ENGINES == ("browser", "local", "auto")
    for value in VOICE_INPUT_ENGINES:
        assert normalize_input_engine(value) == value
    for bad in (None, "", "BROWSER", "remote", 3):
        assert normalize_input_engine(bad) == "auto", bad


def test_voice_input_engine_put_round_trips(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"config": {"voice": {"input_engine": "local"}},
                             "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    live = HC.dispatch(cfg, "GET", "/api/config", {}, {})[1]
    assert live["voice"]["input_engine"] == "local", live.get("voice")


def test_served_proxy_config_fills_qubit_owned_defaults():
    """The default proxy-first runtime must still serve `voice.input_engine`.

    `_fill_config_defaults` fills absent Qubit keys from `config.DEFAULTS`
    without overwriting any value the fork payload served.
    """
    from voicebridge.webui import _fill_config_defaults

    served = {"agent": {"enabled": False}, "voice": {"input_engine": "local",
                                                       "record_key": "ctrl+b"}}
    filled = _fill_config_defaults(served)
    assert filled["voice"]["input_engine"] == "local"       # never overwritten
    assert filled["voice"]["record_key"] == "ctrl+b"         # fork key kept
    assert filled["agent"]["enabled"] is False               # other keys untouched
    assert "asr" not in filled                               # not a broad fill
    # A payload that omits `voice` entirely still gets the canonical key.
    bare = _fill_config_defaults({"agent": {}})
    assert bare["voice"]["input_engine"] == "auto", bare.get("voice")
    # Non-dict payloads pass through untouched.
    assert _fill_config_defaults("nope") == "nope"
