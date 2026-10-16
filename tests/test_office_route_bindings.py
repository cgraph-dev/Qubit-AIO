"""The last mile: the vendored office client's LITERAL ``/api/*`` front door.

WHY THIS FILE EXISTS
--------------------
The vendored Habblaud app runs in the office iframe as a plain document whose
``fetch`` is the browser's NATIVE fetch: the host rewrite bridge
(``game/app/api-bridge.ts``) is installed on the iframe's ``contentWindow``
before ``src`` is set, and the navigation replaces that realm, so the patch is
discarded. A live CDP sweep of the served page (Network.enable) shows the frame
requests the upstream dialect verbatim::

    GET /api/access             200
    GET /api/stream?token=…     200
    GET /api/agents/3           401   <- tokenless iframe, header gate
    GET /api/timeline/days      404   <- do_GET fallthrough
    GET /api/stats/days         404
    GET /api/spawn/folders      404
    GET /api/dictation          404
    GET /api/deliveries         404

Every 404 is a SILENT failure: the repo rule is that an unsupported capability
must be VISIBLY unavailable, and "a typed refusal is never a working
capability". This file pins the front door that fixes exactly that:

1. **Every literal ``/api/*`` path the shipped bundle requests is recognised**
   by the handler's allowlist, read from the SERVED chunks so the list cannot
   drift away from the artifact the page actually loads.
2. **A bare read maps onto the SAME compat family the bridge path uses**
   (``/api/<suffix>`` -> ``/api/office/habblaud/<suffix>``) and returns the
   real Qubit office store data, so the two paths can never disagree.
3. **An unbound client path is a typed 501 JSON refusal**, never a bare 404.
4. **The read gate is the tight loopback + same-origin + own-app triple** and
   never a token bypass; query-token policy is unchanged; POST cannot ride it.

Hermetic: tmp ``XDG_*`` only; no network, no live service.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from voicebridge import webui as W  # noqa: E402

pytestmark = pytest.mark.unit

APP = "http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/"
BUNDLE = (ROOT / "frontend/qubit-app/plugins/qubit-office/habblaud/bundle")

#: A stand-in for the server-issued office load nonce. The office gates require
#: it IN ADDITION to the loopback/Host/Referer triple, because the Referer alone
#: was forgeable (the bug this file's `test_forged_referer_without_nonce_is_refused`
#: pins). The mocked app carries it in its nonce store and the request echoes it
#: as the cookie a real browser would send.
_VALID_NONCE = "test-office-nonce-0123456789"


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------
def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed(cfg):
    """A real org + a real event + a real ledger day — nothing fabricated."""
    from voicebridge.office import agent_memory as MEM
    from voicebridge.office import ledger, org as O
    from voicebridge.office import store as OSS

    ledger.health(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    dept = O.department_create(cfg, "ops", name="Operations",
                               kill_criteria="net negative for 30 days",
                               confirmed=True)["id"]
    room = O.room_create(cfg, dept, "workshop", confirmed=True)["id"]
    wk = O.hire(cfg, "wk-1", seat_type="worker", supervisor_id=ceo,
                department_id=dept, room_id=room, confirmed=True)["id"]
    MEM.emit_event(cfg, "run_started", agent_id=wk)
    ledger.record(cfg, "revenue", 1500, agent_id=wk, department_id=dept,
                  note="seed", recorded_by="test",
                  ts=OSS.now() - 3 * 86400.0)
    return wk


class _Handler:
    """A bare WebUIHandler with the socket bits faked and the body captured."""

    def __init__(self, cfg, *, path="/api/timeline/days", method="GET",
                 peer="127.0.0.1", host="127.0.0.1:8890", referer=APP,
                 token="configured", header=None, body=b"", query="",
                 nonce=_VALID_NONCE):
        h = object.__new__(W.WebUIHandler)
        h.command = method
        h.path = path + (("?" + query) if query else "")
        h.client_address = (peer, 9000)
        h.headers = {"Host": host, "Content-Length": str(len(body))}
        if referer is not None:
            h.headers["Referer"] = referer
        if header is not None:
            h.headers["X-Qubit-Session-Token"] = header
        if nonce is not None:
            h.headers["Cookie"] = "%s=%s" % (W.WebUIHandler.OFFICE_NONCE_COOKIE,
                                             nonce)
        h.rfile = __import__("io").BytesIO(body)
        h.wfile = __import__("io").BytesIO()
        # `app` is read as `self.app.orch.cfg` and `self.app.api_token`, and the
        # office gates now consult `app`'s live nonce store.
        import threading as _t
        import time as _time
        app = type("A", (), {
            "api_token": token,
            "orch": type("O", (), {"cfg": cfg})(),
            "_office_nonces": ({_VALID_NONCE: _time.monotonic() + 3600}
                               if nonce else {}),
            "_office_nonce_lock": _t.Lock(),
        })()
        h.server = type("S", (), {"app": app})()
        h._api_token_ok = lambda: False
        h._qubit_token_ok = lambda: header is not None and header == token
        h._sent = None

        def _send(code, body_, ctype="text/plain", extra_headers=None):
            h._sent = (code, body_, ctype)

        h._send = _send
        self.h = h

    def run(self, method):
        self.h._serve_office_client_api(method)
        return self.h._sent

    def _office_client_read_ok(self):
        return self.h._office_client_read_ok()


def _json_body(sent):
    return json.loads(sent[1].decode())


# --------------------------------------------------------------------------
# 1. the allowlist covers exactly what the shipped bundle requests
# --------------------------------------------------------------------------
def _bundle_api_paths():
    """Every literal ``/api/...`` path in the three served chunks.

    Query strings and template holes are normalised away: the front door
    matches on the PATH only, so ``/api/stats?day=…`` is just ``/api/stats``.
    """
    paths = set()
    for name in ("app-U4Pwh2XA.js", "ptypane-DxPqTDKo.js",
                 "daystats-CUw1NUBG.js"):
        js = (BUNDLE / name).read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"`(/api/[^`]*)`", js):
            p = m.group(1).split("?", 1)[0]        # drop the query
            p = re.sub(r"\$\{[^}]*\}", "X", p)     # a template hole -> a segment
            paths.add(p)
    return {p for p in paths if not p.startswith("/api/orders")}


def test_every_bundle_api_path_is_recognised_by_the_front_door():
    """A literal mismatch means a real client request would 404 again.

    ``/api/access`` and ``/api/stream`` are excluded: they have DEDICATED
    branches in ``do_GET`` (the access envelope and the SSE feed) that run
    before the front door, so they never need the allowlist.
    """
    h = object.__new__(W.WebUIHandler)
    dedicated = {"/api/access", "/api/stream", "/api/snapshot"}
    missed = [p for p in _bundle_api_paths()
              if p not in dedicated and not h._is_office_client_path(p)]
    assert not missed, f"front door would not recognise: {sorted(missed)}"


def test_front_door_does_not_shadow_unrelated_api_paths():
    h = object.__new__(W.WebUIHandler)
    for p in ("/api/config", "/api/office/tower",
              "/api/model/info", "/api/webhooks", "/api/random",
              "/api/tools/toolsets"):
        assert not h._is_office_client_path(p), p


def test_office_client_root_needs_the_slash_subpath():
    """``/api/sessions`` bare and ``/api/sessionsX`` are NOT client paths."""
    h = object.__new__(W.WebUIHandler)
    assert h._is_office_client_path("/api/sessions/recent")
    assert h._is_office_client_path("/api/sessions/a/b/terminal")
    assert not h._is_office_client_path("/api/sessionsX")


# --------------------------------------------------------------------------
# 2. the read gate: tight triple, not a token bypass
# --------------------------------------------------------------------------
def test_read_gate_is_loopback_same_origin_own_app():
    cfg = type("C", (), {})()
    assert _Handler(cfg)._office_client_read_ok()
    assert not _Handler(cfg, peer="192.168.1.9")._office_client_read_ok()
    assert not _Handler(cfg, host="evil.example:8890")._office_client_read_ok()
    assert not _Handler(cfg, referer="http://127.0.0.1:8890/chat") \
        ._office_client_read_ok()
    assert not _Handler(cfg, referer=None)._office_client_read_ok()


def test_forged_referer_without_a_live_nonce_is_refused():
    """THE regression this file exists to pin.

    A bare local process can send ANY header, so
    ``Referer: .../plugin-apps/qubit-office/habblaud/`` on a loopback request
    used to authorise the office read surface: a real transcript
    (``/api/agents/ceo`` -> 200) with no credential. Without the server-issued
    nonce cookie that request must now be REFUSED — the Referer is still
    required, but it is no longer sufficient.
    """
    cfg = type("C", (), {})()
    # The forged-header request: loopback peer, loopback Host, the app's own
    # Referer, but no nonce — exactly what `curl -H 'Referer: ...'` sends.
    forged = _Handler(cfg, referer=APP, nonce=None)
    assert forged._office_client_read_ok() is False, (
        "a forged Referer with no nonce cookie authorised the office read "
        "surface — the transcript bypass is open again")
    assert forged.run("GET")[0] == 401, (
        "the read front door answered a forged-Referer, nonce-less request "
        "with something other than 401")
    # A nonce that the server never issued is equally refused.
    assert _Handler(cfg, referer=APP, nonce="attacker-invented"). \
        _office_client_read_ok() is False
    # The full triple PLUS a live nonce is what the real app sends.
    assert _Handler(cfg, referer=APP)._office_client_read_ok() is True


def test_read_gate_refuses_a_query_token_but_accepts_a_header_token():
    cfg = type("C", (), {})()
    # The stream's query-token exception stays confined to /api/stream.
    assert not _Handler(cfg, query="token=configured") \
        ._office_client_read_ok()
    assert _Handler(cfg, header="configured")._office_client_read_ok()
    assert not _Handler(cfg, header="wrong")._office_client_read_ok()


def test_read_gate_is_never_available_to_a_mutating_verb():
    cfg = type("C", (), {})()
    assert not _Handler(cfg, method="POST")._office_client_read_ok()
    assert not _Handler(cfg, method="PUT")._office_client_read_ok()


def test_general_query_token_policy_is_unchanged():
    # The front door must not have touched the shared token policy.
    assert W.token_ok("configured", "configured",
                      query_token_present=True) == (False,
                                                     "query-token-rejected")


def test_front_door_401s_an_unauthenticated_read(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    h = _Handler(cfg, path="/api/timeline/days", referer="http://x/other",
                 token="configured")
    code, _, _ = _Handler.run(h, "GET")
    assert code == 401


# --------------------------------------------------------------------------
# 3. a bare read maps onto the SAME compat family the bridge uses
# --------------------------------------------------------------------------
@pytest.mark.parametrize("bare,suffix", [
    ("/api/timeline/days", "/timeline/days"),
    ("/api/stats/days", "/stats/days"),
    ("/api/spawn/folders", "/spawn/folders"),
    ("/api/dictation", "/dictation"),
    ("/api/deliveries", "/deliveries"),
])
def test_bare_read_dispatches_to_the_office_compat_family(
        tmp_path, monkeypatch, bare, suffix):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    from voicebridge import qubit_compat as HC

    sent = _Handler.run(_Handler(cfg, path=bare), "GET")
    assert sent[0] == 200, sent
    assert sent[2] == "application/json"
    # Identical to the bridge path's own answer: one handler, one source.
    want = HC.dispatch(cfg, "GET", "/api/office/habblaud" + suffix)[1]
    assert _json_body(sent) == json.loads(json.dumps(want))


def test_bare_agent_read_returns_the_real_seat(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    wk = _seed(cfg)
    sent = _Handler.run(_Handler(cfg, path=f"/api/agents/{wk}"), "GET")
    assert sent[0] == 200, sent
    body = _json_body(sent)
    assert body["ok"] is True
    assert str(body.get("history") is not None)
    # The agent read answers the SAME record the bridge path answers.
    from voicebridge import qubit_compat as HC
    want = HC.dispatch(cfg, "GET", f"/api/office/habblaud/agents/{wk}")[1]
    assert body == json.loads(json.dumps(want))


def test_bare_stats_read_preserves_the_query(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    sent = _Handler.run(
        _Handler(cfg, path="/api/stats", query="day=20738&tz=UTC"), "GET")
    assert sent[0] == 200, sent
    assert _json_body(sent)["ok"] is True


# --------------------------------------------------------------------------
# 4. an unbound client path is a TYPED refusal, never a bare 404
# --------------------------------------------------------------------------
def test_unbound_client_path_is_a_typed_501_not_a_bare_404(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # `/api/pty` is the client's raw-terminal dialect: recognised by the
    # front door, but Qubit binds no such capability under the office family
    # for a GET, so it must answer a typed refusal with a code.
    sent = _Handler.run(_Handler(cfg, path="/api/pty"), "GET")
    assert sent[0] != 404, "a bare 404 is the silent failure this fixes"
    assert sent[0] in (403, 405, 501), sent
    body = _json_body(sent)
    assert body.get("ok") is False
    assert body.get("code"), sent


# --------------------------------------------------------------------------
# 5. the wired reads the page actually depends on answer real data
# --------------------------------------------------------------------------
def test_the_six_live_404s_are_now_answered(tmp_path, monkeypatch):
    """The exact set a CDP sweep caught as 404/401 must no longer be either."""
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    for bare in ("/api/timeline/days", "/api/stats/days",
                 "/api/spawn/folders", "/api/dictation", "/api/deliveries",
                 "/api/sessions/recent"):
        sent = _Handler.run(_Handler(cfg, path=bare), "GET")
        assert sent[0] == 200, (bare, sent)
        assert _json_body(sent).get("ok") is True, bare
