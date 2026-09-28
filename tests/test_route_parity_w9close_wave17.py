"""Wave 17 — close the 6 MISSING reference routes (W9 slice-0 unblock).

The parity inventory (``docs/generated/parity_inventory.json``) listed six
reference routes with no Qubit counterpart. W9 slice-0 vendored
``qubit_state_timeline`` and this wave ports the remaining 0.21.4 modules, so
every one now resolves through the production compat dispatcher:

* ``GET  /api/sessions/{id}/timeline``          -> ``h_session_timeline`` (real)
* ``GET  /api/sessions/{id}/messages/around``   -> ``h_session_messages_around``
* ``GET  /api/audio/voice-live/status``         -> ``h_audio_voice_live_status``
* ``GET  /api/health/idle``                     -> ``h_health_idle`` (read-only)
* ``POST /api/audio/voice-live/session``        -> typed ``VOICE_LIVE_VENDOR_DIRECT``
* ``POST /api/health/retirement``               -> typed ``RETIREMENT_OPERATOR_OWNED``

Reads are served over the canonical Qubit session DB (``state.db``) and the
ported vendored modules; the two mutating routes are typed refusals because
they are direct-vendor-key / process-ownership surfaces, not missing features.
No test reads source text; every assertion is behavioral through ``dispatch``.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    qhome = tmp_path / "qhome"
    qhome.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(qhome))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed_session(cfg, session_id="chat-1"):
    from voicebridge import chat_transport as CT

    store = CT.store(cfg)
    assert store.create("Chat One", session_id=session_id)["state"] == "OK"
    assert store.append(session_id, "user", "hello timeline probe")["state"] == "OK"
    assert store.append(session_id, "assistant", "hi there")["state"] == "OK"
    return store


# --------------------------------------------------------------- registration

def test_all_six_routes_are_registered_with_expected_disposition():
    from voicebridge import qubit_compat as HC

    expected = {
        ("GET", "/api/sessions/_p_/timeline"): ("implemented", "h_session_timeline"),
        ("GET", "/api/sessions/_p_/messages/around"):
            ("implemented", "h_session_messages_around"),
        ("GET", "/api/audio/voice-live/status"):
            ("implemented", "h_audio_voice_live_status"),
        ("GET", "/api/health/idle"): ("implemented", "h_health_idle"),
        ("POST", "/api/audio/voice-live/session"): ("gated", "__gate__"),
        ("POST", "/api/health/retirement"): ("gated", "__gate__"),
    }
    for (method, path), (disp, handler) in expected.items():
        route, params = HC.match(method, path)
        assert route is not None, f"{method} {path} not registered"
        assert route["disposition"] == disp, (method, path, route)
        assert route["handler"] == handler, (method, path, route)
        if "_p_" in path:
            assert params == {"id": "_p_"}, (method, path, params)


# ----------------------------------------------------------------- real reads

def test_timeline_reads_the_canonical_session_store(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_session(cfg)

    code, out = HC.dispatch(cfg, "GET", "/api/sessions/chat-1/timeline", {}, {})
    assert code == 200 and out["ok"] is True, out
    assert out["session_id"] == "chat-1"
    assert out["store_authority"].endswith("QubitSessionStore")
    entries = out["entries"]
    assert entries, out
    assert entries[0]["preview"] == "hello timeline probe"
    assert isinstance(entries[0]["row_id"], int) and entries[0]["row_id"] >= 1
    assert out["pagination"]["total"] == 1


def test_messages_around_reads_from_an_exact_timeline_prompt(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_session(cfg)

    _, tl = HC.dispatch(cfg, "GET", "/api/sessions/chat-1/timeline", {}, {})
    row_id = tl["entries"][0]["row_id"]

    code, out = HC.dispatch(
        cfg, "GET", "/api/sessions/chat-1/messages/around",
        {"row_id": str(row_id), "limit": "120"}, {})
    assert code == 200 and out["ok"] is True, out
    assert out["session_id"] == "chat-1"
    assert out["pagination"]["row_id"] == row_id
    roles = [m.get("role") for m in out["messages"]]
    assert "user" in roles and "assistant" in roles, out["messages"]


def test_messages_around_requires_a_positive_row_id(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_session(cfg)
    code, out = HC.dispatch(
        cfg, "GET", "/api/sessions/chat-1/messages/around", {}, {})
    assert code == 400 and out["code"] == "MALFORMED", out


def test_timeline_unknown_session_is_a_typed_404(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_session(cfg)
    code, out = HC.dispatch(
        cfg, "GET", "/api/sessions/does-not-exist/timeline", {}, {})
    assert code == 404 and out["code"] == "SESSION_NOT_FOUND", out


def test_voice_live_status_is_non_secret_readiness(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/audio/voice-live/status", {}, {})
    assert code == 200 and out["ok"] is True, out
    assert out["mode"] in ("chained", "gpt-live")
    assert isinstance(out["available"], bool)
    assert out["model"] and out["voice"]

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert str(k).lower() not in (
                    "api_key", "key", "token", "access_token", "secret"), (k, obj)
                yield from _walk(v)
        elif isinstance(obj, list):
            for x in obj:
                yield from _walk(x)
        elif isinstance(obj, str):
            yield obj

    for value in _walk(out):
        assert not value.startswith("sk-"), value


def test_health_idle_is_an_honest_snapshot(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/health/idle", {}, {})
    assert code == 200 and out["ok"] is True, out
    # True (provably idle) / False (busy) / None (cannot prove) — never coerced.
    assert out["idle"] in (True, False, None), out
    assert "reason" in out


# ---------------------------------------------------------------- typed gates

def test_voice_live_session_is_a_typed_vendor_direct_refusal(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "POST", "/api/audio/voice-live/session",
        {}, {"sdp": "v=0\r\n"})
    assert code == 403, out
    assert out["code"] == "VOICE_LIVE_VENDOR_DIRECT"
    assert "voice-live/status" in out["qubit_counterpart"]


def test_health_retirement_is_operator_owned(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "POST", "/api/health/retirement", {}, {"action": "prepare"})
    assert code == 403, out
    assert out["code"] == "RETIREMENT_OPERATOR_OWNED"
    assert "qubit gateway stop" in out["qubit_counterpart"]


# ------------------------------------------------------------- ported modules

def test_ported_modules_import_and_idle_ledger_is_present():
    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()
    from tools.voice_live import resolve_gpt_live_status  # type: ignore
    from qubit_cli.backend_retirement import retirement  # type: ignore
    from qubit_cli.web_server_idle_proof import idle_proof  # type: ignore
    from qubit_cli.web_server_idle_exit import busy_ledger  # type: ignore

    assert isinstance(resolve_gpt_live_status(), dict)
    assert retirement.active_count() == 0
    assert callable(busy_ledger)
    # An injected probe keeps the proof deterministic and independent of the
    # host ledgers: True when both probes are clear.
    assert idle_proof(turn_probe=lambda: False,
                      input_probe=lambda: 0) == {"idle": True, "reason": None}
    assert idle_proof(turn_probe=lambda: None,
                      input_probe=lambda: 0)["idle"] is None
