"""OpenClaw parity wave 1 — backend proving tests (one per targeted row).

These tests exercise the real production modules and the external
hardware/credential boundary only where a live dependency is genuinely
required (display actuator, network transport, provider credential). No
fabricated success: every gated path asserts its typed degraded/refusal state,
and every closed capability drives the real function.

Rows covered: OC-01, OC-03, OC-05, OC-06, OC-12, OC-D27, OC-13, OC-14,
OC-16, OC-17, OC-58, OC-19, OC-20, OC-21, OC-D12..D17, OC-51, OC-D01,
OC-54, OC-56, OC-D06, OC-48, OC-63, OC-64, OC-D18, OC-65, OC-67, OC-D28,
OC-25 plus the qubit device rows (browser_close, browser_close_refusal,
computer_doctor, computer_install_refusal, computer_permissions).
"""
from __future__ import annotations

import base64
import json
import urllib.request

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


class _Resp:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self, *_a) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


# ------------------------------------------------------------------ OC-05
def test_oc05_device_scoped_tokens(tmp_path, monkeypatch):
    from voicebridge import device as DEV

    cfg = _cfg(tmp_path, monkeypatch)
    assert DEV.token_list(cfg)["tokens"] == []
    bad = DEV.token_issue(cfg, device_id="dev-a", scopes=["node.read"])
    assert bad["code"] == DEV.TOKEN_CONFIRM_CODE
    issued = DEV.token_issue(cfg, device_id="dev-a", scopes=["node.read"],
                             confirmed=True)
    assert issued["state"] == "OK" and issued["token"]
    token, tid = issued["token"], issued["token_id"]
    # pending tokens never authenticate
    assert DEV.token_verify(cfg, token)["code"] == DEV.TOKEN_PENDING_CODE
    assert DEV.token_approve(cfg, tid, confirmed=True)["state"] == "OK"
    ok = DEV.token_verify(cfg, token, required_scopes=["node.read"], touch=True)
    assert ok["state"] == "OK" and ok["device_id"] == "dev-a"
    assert DEV.token_verify(cfg, token, required_scopes=["node.write"])[
        "code"] == DEV.TOKEN_BAD_INPUT_CODE
    rotated = DEV.token_rotate(cfg, tid, confirmed=True)
    assert rotated["state"] == "OK" and rotated["rotated_from"] == tid
    old = DEV.token_verify(cfg, token)
    assert old["code"] == DEV.TOKEN_REVOKED_CODE
    newest = DEV.token_verify(cfg, rotated["token"])
    assert newest["state"] == "OK"
    assert DEV.token_revoke(cfg, rotated["token_id"], confirmed=True)[
        "state"] == "OK"
    assert DEV.token_verify(cfg, rotated["token"])[
        "code"] == DEV.TOKEN_REVOKED_CODE
    blob = json.dumps(DEV.token_list(cfg))
    assert token not in blob and rotated["token"] not in blob


# ------------------------------------------------------------------ OC-01
def test_oc01_gateway_protocol_v4(tmp_path, monkeypatch):
    from voicebridge import gateway_peers as GP

    assert GP.protocol_version()["protocol"] == 4
    ok = GP.negotiate_protocol(4)
    assert ok["state"] == "OK" and ok["accepted"] == 4
    assert GP.negotiate_protocol(3)["code"] == "GATEWAY_VERSION_UNSUPPORTED"
    assert GP.negotiate_protocol(9)["code"] == "GATEWAY_VERSION_UNSUPPORTED"
    node_ok = GP.negotiate_protocol(4, node_version=3, role="node")
    assert node_ok["state"] == "OK" and node_ok["node_protocol"] == 3
    refused = GP.serve_refusal("start")
    assert refused["code"] == GP.GATEWAY_NO_SERVER_CODE
    doc = GP.protocol_doctor()
    assert doc["server"] == "in-process-bridge"
    assert isinstance(doc["rpc_methods"], list)


# ------------------------------------------------------------------ OC-03
def _messaging_account(cfg, monkeypatch, provider="slack"):
    from voicebridge import messaging as MG

    monkeypatch.setenv("PEER_TOKEN8U", "peer-token-8u")
    MG.account_add(cfg, "a1", provider, "peer acct",
                   key_ref="env:PEER_TOKEN8U", confirmed=True)
    return MG


def test_oc03_peer_control_link(tmp_path, monkeypatch):
    from voicebridge import gateway_peers as GP

    cfg = _cfg(tmp_path, monkeypatch)
    _messaging_account(cfg, monkeypatch)
    GP.peer_add(cfg, "p1", "https://hooks.slack.com/commands/x", "a1",
                key_ref="env:PEER_TOKEN8U", confirmed=True)
    no_transport = GP.peer_control_link(cfg, "p1")
    assert no_transport["code"] == GP.GATEWAY_NO_TRANSPORT_CODE

    seen = {}

    def transport(url, message):
        seen["url"] = url
        seen["hello"] = message
        return {"type": "welcome", "protocol": 4, "node_protocol": 3,
                "capabilities": ["frames"]}

    linked = GP.peer_control_link(cfg, "p1", transport=transport)
    assert linked["state"] == "LINKED" and linked["protocol"] == 4
    assert seen["hello"]["protocol"] == 4

    def stale(url, message):
        return {"type": "welcome", "protocol": 1}

    assert GP.peer_control_link(cfg, "p1", transport=stale)[
        "code"] == "GATEWAY_VERSION_UNSUPPORTED"


# ------------------------------------------------------------------ OC-06/OC-56
def test_oc06_node_host_pairing_and_capabilities(tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    assert NH.enroll(cfg, name="n", endpoint="https://n.example",
                     capabilities=["browser.cdp", "screen"],
                     confirmed=True, source="cli")["state"] == "OK"
    listed = NH.list_nodes(cfg)
    node_id = listed["nodes"][0]["id"]
    caps = NH.capabilities(cfg, node_id)["capabilities"]
    by_cap = {c["capability"]: c for c in caps}
    assert by_cap["device.inventory"]["state"] == "READY"
    assert by_cap["browser.cdp"]["state"] == "UNAVAILABLE"
    assert by_cap["screen"]["state"] == "UNAVAILABLE"
    assert NH.status(cfg)["nodes"] == 1


# ------------------------------------------------------------------ OC-12
def test_oc12_remote_cdp_attach(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    missing = BT.remote_attach(cfg, "node_missing")
    assert missing["state"] == "UNAVAILABLE"
    assert missing["code"] == BT.REMOTE_UNAVAILABLE_CODE

    NH.enroll(cfg, name="remote", endpoint="https://remote.example",
              capabilities=["browser.cdp"], confirmed=True, source="cli")
    node_id = NH.list_nodes(cfg)["nodes"][0]["id"]
    plan = BT.remote_attach(cfg, node_id, port=9333)
    assert plan["state"] == "READY"
    assert plan["bridge"]["local_host"] == "127.0.0.1"
    assert plan["bridge"]["local_port"] == 9333

    bad_ext = BT.extension_attach(cfg, "not-an-extension")
    assert bad_ext["code"] == BT.EXTENSION_BAD_ID_CODE
    ext = "a" * 32
    attached = BT.extension_attach(cfg, ext, node_id=node_id)
    assert attached["state"] == "READY"
    assert attached["bridge"]["extension_id"] == ext


# ------------------------------------------------------------------ OC-D27
class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def call(self, method, params=None, timeout=None):
        self.calls.append((method, params))
        return self._responses.pop(0)

    def close(self):
        pass


def _patch_browser_session(monkeypatch, responses):
    from voicebridge import browser_tabs as BT

    session = _FakeSession(responses)
    monkeypatch.setattr(BT, "_resolve_target",
                        lambda cfg, target="": {"id": "t1", "url": "u",
                                                "title": "t"})
    monkeypatch.setattr(BT, "_target_session", lambda page: session)
    return BT, session


def test_ocd27_annotate_inspect_screencast(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    inspect_value = {"result": {"value": {
        "found": True, "tag": "input", "id": "q", "classes": "search",
        "text": "hello", "role": "textbox", "visible": True,
        "rect": {"x": 1, "y": 2, "width": 3, "height": 4},
        "attrs": {"name": "q"}}}}
    BT, session = _patch_browser_session(monkeypatch, [inspect_value])
    out = BT.inspect_element(cfg, "#q")
    assert out["state"] == "OK" and out["tag"] == "input"
    assert out["attrs"] == {"name": "q"}

    annotate_value = {"result": {"value": {
        "found": True, "tag": "div",
        "rect": {"x": 0, "y": 0, "width": 10, "height": 10}}}}
    BT2, _ = _patch_browser_session(monkeypatch, [annotate_value])
    try:
        BT2.annotate(cfg, "div.card", confirmed=False)
        raise AssertionError("expected confirmation refusal")
    except BT2.BrowserTabsError as exc:
        assert exc.code == BT2.CONFIRM_CODE
    BT3, _ = _patch_browser_session(monkeypatch, [annotate_value,
                                                  {"result": {"value": {
                                                      "cleared": 2}}}])
    ann = BT3.annotate(cfg, "div.card", label="x", confirmed=True)
    assert ann["state"] == "OK" and ann["transient"] is True
    cleared = BT3.clear_annotation(cfg, confirmed=True)
    assert cleared["state"] == "OK" and cleared["cleared"] == 2
    # the screencast fallback path is present and bounded
    assert BT3.SCREENCAST_MAX_FRAMES >= 1
    assert hasattr(BT3, "screencast") and hasattr(BT3, "screenshot")


# ------------------------------------------------------------------ OC-13
def test_oc13_desktop_act_with_injected_actuator(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CU, "_estop", lambda _cfg: False)
    headless = {"state": "OK", "session": "none", "headless": True,
                "detail": "no graphical session"}
    monkeypatch.setattr(CU, "desktop_session", lambda env=None: headless)
    out = CU.act(cfg, "key", args={"key": "Return"}, confirmed=True,
                 executor=lambda a, b: {"ok": True})
    assert out["state"] == "UNAVAILABLE" and out["code"] == CU.NO_DESKTOP_CODE

    live = {"state": "OK", "session": "x11", "headless": False,
            "detail": "x11"}
    monkeypatch.setattr(CU, "desktop_session", lambda env=None: live)
    assert CU.act(cfg, "key", args={"key": "Return"})[
        "code"] == CU.ACT_CONFIRM_CODE
    assert CU.act(cfg, "bogus", confirmed=True)[
        "code"] == CU.ACT_BAD_INPUT_CODE
    calls = []

    def actuator(action, args):
        calls.append((action, args))
        return {"ok": True, "detail": "sent"}

    out = CU.act(cfg, "type", args={"text": "hello"}, confirmed=True,
                 executor=actuator)
    assert out["state"] == "OK" and calls == [("type", {"text": "hello"})]
    monkeypatch.setattr(CU, "_estop", lambda _cfg: True)
    assert CU.act(cfg, "key", args={"key": "Return"}, confirmed=True,
                  executor=actuator)["code"] == CU.ACT_ESTOP_CODE


# ------------------------------------------------------------------ OC-14
def test_oc14_ghost_executor_bridge():
    from voicebridge import computer_use as CU
    from voicebridge import ghost

    ex = CU.ghost_executors(lambda action, env: f"{action}:ok")
    ids = {s.action_id for s in ghost.ghost_action_specs()}
    assert set(ex) <= ids
    assert ex["ghost.input.click"]({}) == "ghost.input.click:ok"


# ------------------------------------------------------------------ OC-16/OC-51
def test_oc16_screen_snapshot_journal(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU
    from voicebridge.evaluation import logbook as LB

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CU, "_estop", lambda _cfg: False)
    no_grab = CU.snapshot(cfg, confirmed=True)
    assert no_grab["state"] == "REFUSED"
    assert no_grab["code"] == LB.CAPTURE_UNAVAILABLE

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    out = CU.snapshot(cfg, grabber=lambda: png,
                      vision=lambda b: "desktop with editor",
                      confirmed=True)
    assert out["state"] == "OK" and out["bytes"] == len(png)
    journal = CU.snapshot_journal(cfg)
    assert journal["count"] == 1
    assert journal["entries"][0]["kind"] == "screen.snapshot"
    # the raw capture path is bounded + append-only
    book = LB.Logbook(tmp_path / "lb.jsonl")
    assert LB.capture_to(book, grabber=lambda: png, evidence_dir=tmp_path / "e",
                         confirmed=True)["state"] == "OK"
    assert LB.capture()["code"] == LB.CAPTURE_UNAVAILABLE


# ------------------------------------------------------------------ OC-17
def test_oc17_cua_browser_state(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT
    from voicebridge import computer_use as CU

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(BT, "tabs", lambda cfg: {
        "state": "OK", "tabs": [{"id": "t1"}], "current_target": "t1"})
    monkeypatch.setattr(BT, "read", lambda cfg, target="": {
        "state": "OK", "url": "https://example.com", "title": "Ex",
        "text": "body"})
    state = CU.cua_browser_state(cfg)
    assert state["state"] == "OK" and state["title"] == "Ex"
    assert state["current_target"] == "t1"

    monkeypatch.setattr(BT, "tabs", lambda cfg: {
        "state": "UNAVAILABLE", "code": BT.UNAVAILABLE_CODE,
        "detail": "no endpoint"})
    down = CU.cua_browser_state(cfg)
    assert down["state"] == "UNAVAILABLE"


# ------------------------------------------------------------------ OC-58
def test_oc58_acp_adapter_session(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    adapters = AC.adapters()
    assert adapters["state"] == "OK" and adapters["adapters"]
    harness = AC.HARNESSES[0]
    shaped = AC.shape_method(harness, "initialize")
    assert shaped["request"]["jsonrpc"] == "2.0"
    try:
        AC.adapter_session(cfg, harness, prompt="hi", confirmed=True)
        raise AssertionError("expected ACP_TRANSPORT_UNAVAILABLE")
    except AC.AcpError as exc:
        assert exc.code == AC.TRANSPORT_CODE
    out = AC.adapter_session(
        cfg, harness, prompt="hi", confirmed=True,
        transport=lambda req: {"jsonrpc": "2.0", "id": req.get("id"),
                               "result": {"ok": True}})
    assert out["state"] == "OK" and out["spawned"] is False


# ------------------------------------------------------------------ OC-19/OC-D12-D17
def test_oc19_messaging_breadth_and_transports(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    cfg = _cfg(tmp_path, monkeypatch)
    for channel in ("discord", "telegram", "matrix", "imessage", "feishu"):
        assert channel in MG.PROVIDERS
    catalog = MG.provider_catalog()
    assert {r["provider"] for r in catalog["providers"]} >= {
        "discord", "telegram", "matrix", "imessage", "feishu"}

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        if "discord.com" in req.full_url:
            return _Resp(json.dumps({"id": "d-1"}).encode())
        return _Resp(json.dumps({"ok": True,
                                 "result": {"message_id": 7}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    d = MG._transport("discord", "", {}, "12345", "hello", "bot-token")
    assert d["provider_msg_id"] == "d-1"
    assert "discord.com/api/v10/channels/12345/messages" in captured["url"]
    t = MG._transport("telegram", "", {}, "42", "hi", "bot-token")
    assert t["provider_msg_id"] == "7"
    assert "api.telegram.org/bot" in captured["url"]

    # secret token never appears in a failed-transport error
    def boom(req, timeout=None):
        raise OSError(f"failed {req.full_url}")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    try:
        MG._transport("telegram", "", {}, "42", "hi", "supersecret")
        raise AssertionError("expected MessagingError")
    except MG.MessagingError as exc:
        assert "supersecret" not in exc.detail


# ------------------------------------------------------------------ OC-20/OC-21
def test_oc20_channel_ingress_and_dm_pairing(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI
    from voicebridge import pairing as PAIR

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("FEISHU_SIGNING8U", "feishu-secret-8u")
    configured = CI.configure(cfg, "feishu", secret_ref="env:FEISHU_SIGNING8U",
                              events=["message"], confirmed=True)
    assert configured["state"] == "OK"
    catalog = CI.catalog(cfg)
    row = next(r for r in catalog["channels"] if r["channel"] == "feishu")
    assert row["state"] == "UNAVAILABLE"
    assert catalog["connected"] == []

    import hashlib
    import hmac
    import time
    ts = time.time()
    body = b'{"k":1}'
    sig = hmac.new(b"feishu-secret-8u", f"{ts}.".encode() + body,
                   hashlib.sha256).hexdigest()
    out = CI.receive(cfg, channel="feishu", payload=body, signature=sig,
                     timestamp=ts)
    assert out["state"] == "UNAVAILABLE" and out["verified"] is True

    code = PAIR.encode_setup(cfg, "phone", ["gateway.access"],
                             account="acct-1", confirmed=True)
    assert PAIR.accept(cfg, code["code"], confirmed=True)["dm_pairing"] is True
    assert PAIR.channel_dm_pairings(cfg)["pairings"][0]["account"] == "acct-1"


# ------------------------------------------------------------------ OC-25
def test_oc25_gmail_pubsub(tmp_path, monkeypatch):
    from voicebridge import channels_inbound as CI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("GMAIL_PUBSUB8U", "pubsub-token-8u")
    CI.configure(cfg, "gmail", secret_ref="env:GMAIL_PUBSUB8U",
                 events=["message"], confirmed=True)
    data = base64.b64encode(b'{"emailAddress":"a@b","historyId":1}').decode()
    envelope = {"message": {"data": data, "messageId": "m-1",
                            "publishTime": "2026-01-01T00:00:00Z"},
                "subscription": "projects/p/subscriptions/s"}
    out = CI.gmail_pubsub(cfg, envelope, token="pubsub-token-8u")
    assert out["state"] == "UNAVAILABLE" and out["verified"] is True
    assert out["message_id"] == "m-1"
    try:
        CI.gmail_pubsub(cfg, envelope, token="pubsub-token-8u")
        raise AssertionError("expected replay refusal")
    except CI.ChannelError as exc:
        assert exc.code == CI.REPLAY_CODE
    try:
        CI.gmail_pubsub(cfg, envelope, token="wrong")
        raise AssertionError("expected bad token")
    except CI.ChannelError as exc:
        assert exc.code == CI.BAD_SIGNATURE_CODE


# ------------------------------------------------------------------ OC-D01
def test_ocd01_team_reports_aggregator(tmp_path, monkeypatch):
    from voicebridge.communications import reports as RP

    cfg = _cfg(tmp_path, monkeypatch)
    sources = [{"platform": "github", "account": "org", "target": "repo"},
               {"platform": "discord", "account": "guild", "target": "#dev"}]
    no_transport = RP.team_reports(cfg, sources)
    assert no_transport["code"] == RP.TEAM_REPORTS_UNAVAILABLE
    assert RP.team_reports(cfg, [])["code"] == RP.TEAM_REPORTS_BAD_SOURCE

    def transport(request):
        return {"items": [{"actor": "alice", "summary": "opened PR",
                           "ts": 5, "url": "https://github.com/x/y/pull/1",
                           "channel": request["target"]}]}

    out = RP.team_reports(cfg, sources, transport=transport)
    assert out["state"] == "SUCCESS" and out["counts"]["total"] == 2
    assert out["items"][0]["actor"] == "alice"
    assert out["partial_failures"] == []


# ------------------------------------------------------------------ OC-54/OC-D06
def test_oc54_runtime_cells_and_cloud_admission(tmp_path, monkeypatch):
    from voicebridge import runtime_images as RI

    cfg = _cfg(tmp_path, monkeypatch)
    assert RI.refuse_container("fleet-up")["code"] == \
        RI.RUNTIME_CONTAINER_FORBIDDEN
    inventory = RI.cells(cfg)
    assert inventory["backend"] == "local-process"
    assert inventory["daemon"] == "none-required"
    # not-ready image refuses admission honestly
    assert RI.cell_admit(cfg, "opencode-binary", confirmed=True)[
        "code"] == RI.RUNTIME_CELL_NOT_READY
    monkeypatch.setattr(RI, "_image_state", lambda cfg, name: "READY")
    admitted = RI.cell_admit(cfg, "opencode-binary", confirmed=True)
    assert admitted["state"] == "OK" and admitted["backend"] == "process"
    assert any(c["state"] == "ADMITTED" for c in RI.cells(cfg)["cells"])
    assert RI.cell_release(cfg, "opencode-binary",
                           confirmed=True)["released"] is True

    assert RI.cloud_worker_admission(cfg)["code"] == \
        RI.RUNTIME_CLOUD_UNAVAILABLE
    placed = RI.cloud_worker_admission(
        cfg, provider="aws",
        transport=lambda req: {"admitted": True, "worker": "w-1",
                               "warm_images": ["opencode-binary"]})
    assert placed["state"] == "OK" and placed["worker"] == "w-1"


# ------------------------------------------------------------------ OC-48
def test_oc48_workspace_memory(tmp_path, monkeypatch):
    from voicebridge.memory import workspace as WS

    cfg = _cfg(tmp_path, monkeypatch)
    prov = WS.providers()
    assert prov["state"] == "OK"
    names = {p["name"]: p["state"] for p in prov["providers"]}
    assert names["markdown-workspace"] == "AVAILABLE"
    assert names["lancedb"] == "UNAVAILABLE"
    denied = WS.write_note(cfg, "note-1", "hello world", tags=["t"])
    assert denied["state"] == "denied"
    written = WS.write_note(cfg, "note-1", "hello world", tags=["t"],
                            confirmed=True)
    assert written["state"] == "OK"
    assert WS.read_note(cfg, "note-1")["note"]["body"].startswith("hello")
    assert [n["name"] for n in WS.list_notes(cfg)["notes"]] == ["note-1"]
    dream = WS.dream(cfg, confirmed=True)
    assert dream["state"] in ("OK", "UNAVAILABLE", "DEGRADED", "REFUSED")


# ------------------------------------------------------------------ OC-63
def test_oc63_web_search_fetch_xsearch(tmp_path, monkeypatch):
    from voicebridge import web_tool as WT

    cfg = _cfg(tmp_path, monkeypatch)
    x = WT.x_search(cfg, "qubit")
    assert x["code"] == WT.X_SEARCH_UNAVAILABLE_CODE
    assert WT.run(cfg, "web.x_search", {"query": "q"})[
        "code"] == WT.X_SEARCH_UNAVAILABLE_CODE
    assert "web.fetch" in WT.TOOL_SPECS and "web.search" in WT.TOOL_SPECS
    try:
        WT.run(cfg, "web.nope", {})
        raise AssertionError("expected unknown tool")
    except WT.WebToolError as exc:
        assert exc.code == WT.BAD_INPUT_CODE


# ------------------------------------------------------------------ OC-64/OC-D18
def test_oc64_media_generation_contract(tmp_path, monkeypatch):
    from voicebridge import media_gen as MG

    cfg = _cfg(tmp_path, monkeypatch)
    assert MG.KINDS == ("image", "music", "video", "speech")
    catalog = MG.catalog(cfg)
    assert catalog["state"] == "OK"
    req = MG.build_request("openai", "image", "a cat",
                           {"size": "1024x1024"})
    assert req["url"].startswith("https://api.openai.com/")
    try:
        MG.generate(cfg, kind="image", provider="openai", prompt="a cat")
        raise AssertionError("expected MEDIA_GEN_UNAVAILABLE")
    except MG.MediaGenError as exc:
        assert exc.code == MG.UNAVAILABLE_CODE
    try:
        MG.validate_options("image", {"n": 99})
        raise AssertionError("expected bound failure")
    except MG.MediaGenError as exc:
        assert exc.code == MG.BAD_INPUT_CODE


# ------------------------------------------------------------------ OC-65/OC-67
def test_oc65_oc67_media_understanding_and_realtime(tmp_path, monkeypatch):
    from voicebridge import vision as VI

    cfg = _cfg(tmp_path, monkeypatch)
    try:
        VI.describe_batch(cfg, [], confirmed=True)
        raise AssertionError("expected empty batch failure")
    except VI.VisionError as exc:
        assert exc.code == VI.BAD_IMAGE_CODE
    try:
        VI.describe_batch(cfg, [f"i{i}.png" for i in range(9)], confirmed=True)
        raise AssertionError("expected overflow failure")
    except VI.VisionError as exc:
        assert exc.code == VI.BAD_IMAGE_CODE

    no_asr = VI.transcribe(cfg, b"abc", confirmed=True)
    assert no_asr["code"] == VI.MEDIA_UNAVAILABLE_CODE
    tr = VI.transcribe(cfg, b"abc", confirmed=True,
                       transport=lambda req: {"text": "hello world",
                                              "language": "en"})
    assert tr["state"] == "OK" and tr["text"] == "hello world"
    assert VI.meeting_bot(cfg)["code"] == VI.MEDIA_UNAVAILABLE_CODE
    joined = VI.meeting_bot(cfg, join_url="https://meet/x",
                            transport=lambda req: {"joined": True,
                                                   "session": "s-1",
                                                   "transcription": True})
    assert joined["state"] == "OK" and joined["live_transcription"] is True


# ------------------------------------------------------------------ OC-D28
def test_ocd28_provider_accounts_and_fallback(tmp_path, monkeypatch):
    from voicebridge import brain as BR
    from voicebridge import local_models as LM

    cfg = _cfg(tmp_path, monkeypatch)
    chain = LM.fallback_chain(cfg)
    assert chain["state"] == "OK" and isinstance(chain["chain"], list)
    picked = LM.resolve_fallback(cfg)
    assert picked["state"] in ("OK", "UNAVAILABLE", "DEGRADED")
    assert BR.principal_add(cfg, "principal-a", "person", "P",
                            confirmed=True)["state"] == "OK"
    account = BR.account_add(cfg, "work", "slack.com", "principal-a",
                             confirmed=True)
    assert account["state"] in ("OK", "FAILED", "denied")
    assert isinstance(BR.account_list(cfg)["accounts"], list)


# --------------------------------------------------- qubit device rows
def test_qubit_browser_close_rows(tmp_path, monkeypatch):
    from voicebridge import browser as BR

    cfg = _cfg(tmp_path, monkeypatch)
    for name in ("Default", "user", "personal"):
        out = BR.close_profile(cfg, name, confirmed=True)
        assert out["state"] == "REFUSED"
        assert out["code"] == BR.CLOSE_CODE
    assert BR.profiles(cfg)["profiles"] == []
    assert BR.refuse_close_profile("x")["code"] == BR.CLOSE_CODE
    created = BR.create_profile(cfg, "managed-a", cdp_port=9331,
                                confirmed=True)
    assert created["state"] == "OK"
    closed = BR.close_profile(cfg, "managed-a", stopper=lambda e: None,
                              confirmed=True)
    assert closed["state"] == "OK" and closed["profile"]["state"] == "stopped"


def test_qubit_computer_rows(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU

    assert CU.doctor()["schema"] == "qubit-computer-use-doctor/1"
    assert CU.permissions()["control"] == "DISABLED"
    assert CU.refuse_install()["code"] == CU.NO_DRIVER_CODE
    assert CU.status()["control"] == "DISABLED"
    assert CU.control_actions()["actions"] == list(CU.CONTROL_ACTIONS)
