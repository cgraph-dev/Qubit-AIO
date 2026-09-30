"""OpenClaw closure wave 2 — node-host / ACP / cloud-worker proving tests.

One proving test per targeted runtime row. Every test drives a real
production function and mocks only the external device/provider boundary via
an injected transport. No paired device, harness binary, or cloud account is
required, and Qubit never spawns a process or opens a listener.

Rows: OC-06, OC-56, OC-58, OC-D06.
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ------------------------------------------------------------- OC-06/OC-56
def test_oc06_oc56_node_pairing_capabilities_and_media_transport(
        tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    enrolled = NH.enroll(
        cfg, name="studio", endpoint="https://node.example:8443",
        capabilities=["screen", "audio", "camera", "media_playback"],
        confirmed=True, source="cli")
    assert enrolled["state"] == "OK"
    nid = NH.list_nodes(cfg)["nodes"][0]["id"]

    rows = {r["capability"]: r for r in NH.capabilities(cfg, nid)["capabilities"]}
    for cap in ("screen", "audio", "camera", "media_playback"):
        assert rows[cap]["state"] == "UNAVAILABLE", cap
        assert rows[cap]["code"] == NH.NODE_CAPABILITY_UNAVAILABLE
    assert rows["device.inventory"]["state"] == "READY"

    # No injected transport: remote actuator dispatch stays a typed refusal.
    refused = NH.dispatch(cfg, nid, "screen", confirmed=True)
    assert refused["code"] == NH.NODE_DISPATCH_REFUSED
    # Injected transport still requires explicit confirmation.
    denial = NH.dispatch(cfg, nid, "screen", confirmed=False,
                         transport=lambda req: {"ok": True})
    assert denial["code"] == NH.NODE_DISPATCH_REFUSED

    seen = {}

    def transport(request):
        seen.update(request)
        return {"state": "captured", "bytes": 128}

    out = NH.dispatch(cfg, nid, "screen", confirmed=True, transport=transport)
    assert out["state"] == "OK" and out["result"]["bytes"] == 128
    assert seen["type"] == "node.invoke" and seen["capability"] == "screen"
    # Local-safe reads still execute without any transport.
    local = NH.dispatch(cfg, nid, "device.inventory")
    assert local["state"] == "OK" and "machine_id" in local["result"]


# ------------------------------------------------------------------ OC-58
def test_oc58_acp_adapter_session_loop(tmp_path, monkeypatch):
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

    requests = []

    def transport(req):
        requests.append(req)
        return {"jsonrpc": "2.0", "id": req.get("id"),
                "result": {"ok": True}}

    out = AC.adapter_session(cfg, harness, prompt="hi", confirmed=True,
                             transport=transport)
    assert out["state"] == "OK" and out["spawned"] is False
    assert len(requests) == 2, "initialize + session/prompt"
    assert requests[0]["method"] == "initialize"


# ---------------------------------------------------------------- OC-D06
def test_ocd06_cloud_worker_admission_and_local_cells(tmp_path, monkeypatch):
    from voicebridge import runtime_images as RI

    cfg = _cfg(tmp_path, monkeypatch)
    assert RI.cloud_worker_admission(cfg)["code"] == \
        RI.RUNTIME_CLOUD_UNAVAILABLE

    seen = {}

    def transport(request):
        seen.update(request)
        return {"admitted": True, "worker": "w-1",
                "warm_images": ["opencode-binary"]}

    out = RI.cloud_worker_admission(cfg, provider="aws", transport=transport)
    assert out["state"] == "OK" and out["worker"] == "w-1"
    assert seen["type"] == "admission.request" and seen["provider"] == "aws"

    # Local prepared-cell reuse is independent of the cloud transport.
    inventory = RI.cells(cfg)
    assert inventory["backend"] == "local-process"
    assert RI.refuse_container("fleet-up")["code"] == \
        RI.RUNTIME_CONTAINER_FORBIDDEN
