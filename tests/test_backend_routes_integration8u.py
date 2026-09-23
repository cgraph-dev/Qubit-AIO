"""Backend route integrations (wave 17 completion pass).

Pins the S1 compat-layer integrations against the real canonical modules:

* A25 — the root ``/`` redirect lands on ``/sessions`` (upstream Hermes
  ``RootRedirect`` behavior), never the old ``/chat``.
* A8  — the dashboard pins the ONE fork home
  (``qubit_runtime.default_home``), never a second ``<state>/qubit``.
* A10 — session reads resolve the store through
  ``auth_status.session_authority()`` and fail closed on contract drift.
* S5  — chat projection reads (activity / drafts / comments / CI) delegate to
  the ``chat_transport`` authority adapters.
* S8  — desktop/browser routes forward to the canonical operation-catalog ids
  (shared confirm/e-stop/broker), never a re-implemented operation.
* S9  — ``/api/dashboard/agent-plugins/install`` and the plugins hub use
  ``plugin_runtime.install``/``plugin_catalog``.
* A29 — ``/api/qubit/update/check`` stays a typed 200 read while the mutation
  is a real confirmed handler (CONFIRM_REQUIRED without confirm).

Hermetic: isolated XDG dirs; no network; no browser/CDP/desktop boundary is
ever opened (typed refusals are asserted where a boundary is absent).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

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


def _dispatch(cfg, method, path, query=None, body=None):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, method, path, query or {}, body or {})


def _start_ui(tmp_path, monkeypatch):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "local"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    return WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state), state


# ------------------------------------------------------------------- A25
def test_root_redirects_to_sessions_not_chat(tmp_path, monkeypatch):
    import http.client

    ui, _state = _start_ui(tmp_path, monkeypatch)
    ui.start()
    try:
        port = ui._server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
        conn.request("GET", "/")
        resp = conn.getresponse()
        loc = resp.getheader("Location")
        resp.read()
        conn.close()
        assert resp.status == 302, resp.status
        assert loc == "/sessions", loc
    finally:
        ui.stop()


# --------------------------------------------------------------------- A8
def test_dashboard_pins_the_single_fork_home(tmp_path, monkeypatch):
    from voicebridge import qubit_asgi as HA
    from voicebridge import qubit_runtime as HR

    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.setattr(HA, "_client", None)

    ui, state = _start_ui(tmp_path, monkeypatch)
    try:
        # PRB-03: the bridge pins the runtime home for THIS instance's config
        # (cfg.state_dir()/runtime), which equals default_home() for the default
        # profile and follows a custom state dir otherwise.
        expected = HR.runtime_home_for_cfg(ui.orch.cfg)
        assert os.environ["QUBIT_HOME"] == str(expected)
        assert os.environ["QUBIT_HOME"] != str(HR._external_default_home())
        # The old second home is never created/pinned.
        assert not (state / "qubit").exists()
        assert str(state / "qubit") != os.environ["QUBIT_HOME"]
    finally:
        ui.stop()


# -------------------------------------------------------------------- A10
def test_session_reads_resolve_the_single_authority(tmp_path, monkeypatch):
    from voicebridge import auth_status as AS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    contract = HC.session_authority_contract()
    assert contract == AS.session_authority()
    assert contract["writable_authorities"]["receipt_lifecycle"] == \
        HC.RECEIPT_SESSION_AUTHORITY == "voicebridge.auth_status.SessionStore"
    assert contract["writable_authorities"]["chat_transcript"] == \
        HC.CHAT_SESSION_AUTHORITY == \
        "voicebridge.qubit_session_store.QubitSessionStore"

    # The unified session readers resolve BOTH declared stores (never the
    # fork's state.db): the canonical chat transcript authority plus the
    # receipt lifecycle authority.
    for path in ("/api/sessions", "/api/sessions/stats",
                 "/api/profiles/sessions", "/api/profiles/sessions/sidebar"):
        code, out = _dispatch(cfg, "GET", path)
        assert code == 200, (path, code, out)
        authority = str(out.get("store_authority") or "")
        assert HC.CHAT_SESSION_AUTHORITY in authority, (path, out)
        assert HC.RECEIPT_SESSION_AUTHORITY in authority, (path, out)


def test_session_reader_fails_closed_on_authority_drift(tmp_path, monkeypatch):
    from voicebridge import auth_status as AS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    tampered = json.loads(json.dumps(AS.session_authority()))
    tampered["writable_authorities"]["receipt_lifecycle"] = "evil.Store"
    monkeypatch.setattr(AS, "session_authority", lambda: tampered)

    code, out = _dispatch(cfg, "GET", "/api/sessions")
    assert code == 500, (code, out)
    assert out["code"] == "EXECUTOR_ERROR", out


def test_sessions_surface_reads_the_canonical_chat_transcript(tmp_path,
                                                               monkeypatch):
    """The dashboard Sessions surface must show the real chat conversations.

    Regression guard for the split-brain where /qubit-chat listed the user's
    chats but /sessions said "No sessions yet" because it read the fork's empty
    state.db. The list/detail/messages must all resolve the canonical chat
    authority.
    """
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    sid = CT.store(cfg).create("Split Brain")["session"]["id"]
    CT.store(cfg).append(sid, "user", "hello there")
    CT.store(cfg).append(sid, "assistant", "canonical reply")

    code, listed = _dispatch(cfg, "GET", "/api/sessions")
    assert code == 200, listed
    rows = {r["id"]: r for r in listed["sessions"]}
    assert sid in rows, listed
    assert rows[sid]["source"] == "qubit-chat"
    assert rows[sid]["message_count"] == 2

    code, detail = _dispatch(cfg, "GET", f"/api/sessions/{sid}")
    assert code == 200 and detail["source"] == "qubit-chat", detail
    assert detail["message_count"] == 2

    code, msgs = _dispatch(cfg, "GET", f"/api/sessions/{sid}/messages")
    assert code == 200, msgs
    assert [m["content"] for m in msgs["messages"]] == \
        ["hello there", "canonical reply"]


def test_model_info_is_served_by_the_qubit_resolver(tmp_path, monkeypatch):
    """Model identity must come from Qubit, never the unconfigured fork."""
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge.providers import resolver as R

    code, out = _dispatch(cfg, "GET", "/api/model/info")
    assert code == 200 and out.get("ok") is True, out
    # The provider/model MUST be the canonical resolver's effective selection,
    # not a hard-coded or fork-derived value.
    eff = R.resolve(cfg, role=R.DEFAULT_ROLE)
    assert out.get("model") == eff.model, out
    assert out.get("provider") == eff.provider, out


# --------------------------------------------------------------------- S5
def test_chat_projection_reads_are_real(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import session_activity as SA

    cfg = _cfg(tmp_path, monkeypatch)
    sid = CT.store(cfg).create("Projection", session_id="s1")["session"]["id"]

    CT.draft_store(cfg).put("s1", "src/app.py", "print('x')\n")
    code, out = _dispatch(cfg, "GET", "/api/qubit/chat/sessions/s1/drafts")
    assert code == 200 and out["total"] == 1, out
    code, out = _dispatch(cfg, "GET", "/api/qubit/chat/sessions/s1/draft",
                          {"path": "src/app.py"})
    assert code == 200 and out["draft"]["content"] == "print('x')\n", out

    store = CT.comment_store(cfg)
    store.add_comment("s1", "please review", target="src/app.py")
    store.add_ci_detail("s1", check="pytest", status="success",
                        detail="41 passed")
    code, out = _dispatch(cfg, "GET", "/api/qubit/chat/sessions/s1/comments")
    assert code == 200 and out["total"] == 1, out
    code, out = _dispatch(cfg, "GET", "/api/qubit/chat/sessions/s1/ci")
    assert code == 200 and out["total"] == 1, out

    SA.store(cfg).record("s1", "note", detail="shipped the fix")
    code, out = _dispatch(cfg, "GET", "/api/qubit/chat/sessions/s1/activity")
    assert code == 200 and out["schema"] == SA.SCHEMA, out
    assert out["state"] == "OK" and out["counts"]["note"] == 1, out
    assert sid == "s1"


# --------------------------------------------------------------------- S8
CATALOG_OPS = {
    "computer_ops": False,
    "computer_launch": True,
    "computer_pip": True,
    "computer_present": True,
    "computer_presentation_status": False,
    "browser_favicon": False,
    "browser_favicon_cache": False,
    "browser_download": True,
}


def test_desktop_browser_ops_are_in_the_catalog_with_confirm():
    from voicebridge import dashboard_ops as OPS

    cat = {o["id"]: o for o in OPS.catalog()}
    for op_id, confirm in CATALOG_OPS.items():
        assert op_id in cat, op_id
        assert cat[op_id]["confirm"] is confirm, (op_id, cat[op_id]["confirm"])


def test_desktop_browser_routes_use_the_canonical_op(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    code, out = _dispatch(cfg, "GET", "/api/qubit/desktop/ops")
    assert code == 200 and out["ok"] is True, (code, out)
    ops = out["result"]["desktop"]["ops"]
    assert set(ops) == {"desktop.launch", "desktop.picture_in_picture",
                        "desktop.present"}, ops

    code, out = _dispatch(cfg, "GET", "/api/qubit/desktop/presentation")
    assert code == 200 and out["result"]["desktop"]["state"] == "OK", out

    # Confirmed mutations stage on the SHARED journal (409, no fake 2xx).
    code, out = _dispatch(cfg, "POST", "/api/qubit/desktop/launch",
                          body={"app": "browser"})
    assert code == 409 and out["state"] == "NEEDS_CONFIRMATION", (code, out)
    assert out["confirmation_id"], out
    code, out = _dispatch(cfg, "POST", "/api/qubit/browser/download",
                          body={"dest": "/tmp/qubit-page.html"})
    assert code == 409 and out["state"] == "NEEDS_CONFIRMATION", (code, out)

    # A bounded read-only op still honours the emergency stop.
    from voicebridge import browser_tabs as BT

    monkeypatch.setattr(BT, "estop_engaged", lambda _cfg: True)
    code, out = _dispatch(cfg, "GET", "/api/qubit/browser/favicon")
    assert code == 200, (code, out)
    assert out["result"]["favicon"]["code"] == BT.FAVICON_ESTOP_CODE, out


# --------------------------------------------------------------------- S9
def _seed_catalog_manifest(cfg, identifier="acme"):
    from voicebridge import plugin_runtime as PR

    cat_dir = Path(cfg.state_dir()) / PR.CATALOG_DIR
    cat_dir.mkdir(parents=True, exist_ok=True)
    (cat_dir / f"{identifier}.json").write_text(json.dumps({
        "id": "acme.tool", "kind": "tool",
        "entrypoint": "acme.plugin:main", "digest": "a" * 64,
        "label": "Acme", "version": "1.0.0", "capabilities": ["run"],
        "description": "Qubit reviewed tool"}), encoding="utf-8")


def test_plugin_hub_and_install_use_plugin_runtime(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_catalog_manifest(cfg)

    code, out = _dispatch(cfg, "GET", "/api/dashboard/plugins/hub")
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["install_capability"] == PR.INSTALL_CAPABILITY, out
    assert out["catalog_remote"]["state"] == "REFUSED", out
    assert any(r["identifier"] == "acme" and r["can_install"] and not
               r["installed"] for r in out["catalog"]), out["catalog"]

    # Install is confirm-gated through the canonical plugin_runtime op.
    code, out = _dispatch(cfg, "POST", "/api/dashboard/agent-plugins/install",
                          body={"identifier": "acme"})
    assert code == 409 and out["state"] == "NEEDS_CONFIRMATION", (code, out)
    code, out = _dispatch(cfg, "POST", "/api/dashboard/agent-plugins/install",
                          body={"identifier": "acme", "confirm": True})
    assert code == 200 and out["installed"] is True, (code, out)
    assert out["plugin"]["id"] == "acme.tool", out
    assert out["install_capability"] == PR.INSTALL_CAPABILITY, out
    assert any(r["id"] == "acme.tool" for r in PR.list_plugins(cfg)["plugins"])

    # Remote identifiers stay a typed refusal; nothing is fetched.
    code, out = _dispatch(cfg, "POST", "/api/dashboard/agent-plugins/install",
                          body={"identifier": "owner/repo", "confirm": True})
    assert code == 403 and out["code"] == "BLOCKED_REMOTE_PLUGIN", (code, out)


# -------------------------------------------------------------------- A29
def test_update_check_stays_typed_200(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "GET", "/api/qubit/update/check")
    assert code == 200, (code, out)
    for key in ("install_method", "current_version", "behind",
                "update_available", "can_apply", "update_command", "message"):
        assert key in out, (key, out)
    assert out["behind"] is None and out["update_available"] is False
    assert out["can_apply"] is False and out["current_version"]

    # The in-place mutation is a real, confirmed, non-destructive handler:
    # without confirm it refuses typed CONFIRM_REQUIRED (409), never a success.
    code, out = _dispatch(cfg, "POST", "/api/qubit/update")
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
