"""Guard the explicit tui_gateway WS disposition (B-06W).

The vendored Hermes ``/api/ws`` dialect drives the tui_gateway sidecar and
binds its own session id, so Qubit runs one broker/session/event path and
answers with a typed SAFE_EQUIVALENT refusal. This test fails if the registered
disposition drifts from the live gate table, and proves the working counterpart
(SSE events + Qubit chat) and the implemented WS-ticket mint are actually
reachable on the single listener.
"""
from __future__ import annotations

import http.client
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "docs/archive/2026-09-21" / "QUBIT_WS_DIALECT_DISPOSITION.json"
sys.path.insert(0, str(REPO))


def _gated():
    from voicebridge import qubit_compat as HC

    out = []
    for entry in HC.GATED:
        if len(entry) < 3:
            continue
        method, pattern, target = entry[0], entry[1], entry[2]
        if isinstance(target, dict):
            out.append((method.upper(), pattern, target))
    return out


def test_manifest_registers_a_terminal_disposition_per_surface():
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    allowed = {"SAFE_EQUIVALENT", "OPERATOR_ONLY", "IMPLEMENTED"}
    # PRA-09: an explicit, owner-approved typed safe equivalent is required —
    # the dialect may not be silently refused.
    approval = doc.get("owner_approval") or {}
    assert approval.get("approved") is True, approval
    assert approval.get("approved_by"), approval
    assert approval.get("counterpart_proof"), approval
    for surface in doc["surfaces"]:
        assert surface["disposition"] in allowed, surface
        assert surface.get("qubit_counterpart"), surface
        assert surface.get("verified_by"), surface
        if surface["disposition"] != "IMPLEMENTED":
            assert surface.get("code"), surface
        if surface["disposition"] == "SAFE_EQUIVALENT":
            assert surface.get("owner_approval") is True, surface


def test_each_surface_matches_a_real_typed_gate():
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    gates = _gated()
    for surface in doc["surfaces"]:
        if surface["disposition"] == "IMPLEMENTED":
            # Implemented surfaces must NOT be gated; a real handler serves them.
            assert not [g for g in gates
                        if g[0] == surface["method"].upper()
                        and g[1] == surface["path"]], surface
            continue
        match = [g for g in gates if g[0] == surface["method"].upper()
                 and g[1] == surface["path"]]
        assert match, f"no live gate for {surface['method']} {surface['path']}"
        gate = match[0][2]
        assert gate["code"] == surface["code"], (surface, gate["code"])
        assert gate.get("counterpart"), gate


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\n"
                 f"webui:\n  token: test-token\n  qubit_backend: false\n")
    cfg = Config.load(p)

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


def _req(port, method, path, body=None, headers=None, token="test-token"):
    hdrs = dict(headers or {})
    hdrs.setdefault("X-Api-Token", token)
    hdrs.setdefault("X-Hermes-Session-Token", token)
    data = json.dumps(body).encode() if body is not None else None
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    status, ctype, raw = resp.status, resp.getheader("Content-Type", ""), resp.read()
    conn.close()
    return status, ctype, raw


def test_ws_refusal_is_typed_and_counterpart_is_live(tmp_path):
    ui, port = _start(tmp_path)
    try:
        # The vendored dialect is a typed refusal, never an unsilenced absence.
        status, _, raw = _req(port, "GET", "/agent/api/ws")
        body = json.loads(raw) if raw else {}
        assert status not in (404, 500), (status, raw[:200])
        assert body.get("code") == "WS_SINGLE_PATH", body
        # The working counterpart exists on the same listener: the SSE event
        # feed and the Qubit chat endpoint both resolve (not 404).
        for method, path in (("GET", "/agent/api/events"),
                             ("POST", "/agent/api/qubit/chat")):
            route_status, _, route_raw = _req(port, method, path,
                                              body={} if method == "POST" else None)
            assert route_status != 404, (method, path, route_raw[:200])
        # The WS ticket handshake is genuinely implemented on the listener.
        status, _, raw = _req(
            port, "POST", "/agent/api/auth/ws-ticket",
            body={"scope": "rpc"})
        assert status == 200, (status, raw[:200])
        ticket = json.loads(raw)
        assert ticket.get("ok") is True and ticket.get("ticket"), ticket
        assert ticket.get("scope") == "rpc", ticket
    finally:
        ui.stop()
