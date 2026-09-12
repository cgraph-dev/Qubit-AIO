"""OpenClaw E9 — resumable file-transfer finalization (OC-D03) proofs.

``stream_plan``/``resume`` hand back per-chunk specs; ``verify_transfer`` is
the receiver-side completion that proves a peer assembled a byte-exact copy
against the persisted plan. These tests drive the real production path
(plan -> fetch -> verify) through a confined, enrolled local node and cover
tamper, wrong-length, incomplete, duplicate-index, unknown-transfer,
remote-node, and emergency-stop paths.
"""
from __future__ import annotations

import base64
import hashlib
import json


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _project(cfg, tmp_path, name="Site"):
    from voicebridge import projects as PJ

    root = tmp_path / "work" / "site"
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_bytes(b"hello world\n")
    PJ.create(cfg, name, [str(root)], requested_by="cli:user", confirmed=True)
    return root


def _enroll_local(cfg):
    from voicebridge import node_host as NH

    out = NH.enroll(cfg, name="local", endpoint="http://127.0.0.1:8443/",
                    capabilities=["file.transfer"], confirmed=True)
    assert out["state"] == "OK", out
    return out["node"]["id"]


def _received(cfg, nid, target, size):
    from voicebridge import file_transfer as FT

    plan = FT.stream_plan(cfg, nid, str(target), chunk_size=400)
    got = FT.fetch(cfg, nid, str(target), max_bytes=size, confirmed=True)
    raw = base64.b64decode(got["data"])
    chunks = []
    for chunk in plan["chunks"]:
        seg = raw[chunk["offset"]:chunk["offset"] + chunk["length"]]
        chunks.append({"index": chunk["index"],
                       "data": base64.b64encode(seg).decode("ascii")})
    return plan, chunks


def test_verify_transfer_roundtrip(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)
    payload = bytes((i * 7 + 3) % 256 for i in range(1000))
    target = root / "verify.bin"
    target.write_bytes(payload)

    plan, chunks = _received(cfg, nid, target, len(payload))
    out = FT.verify_transfer(cfg, nid, plan["transfer_id"], received=chunks)
    assert out["state"] == "COMPLETE", out
    assert out["verified"] is True
    assert out["digest"] == plan["digest"] == hashlib.sha256(payload).hexdigest()
    assert out["size"] == len(payload) and out["chunks"] == plan["chunk_count"]


def test_verify_transfer_rejects_tampered_and_short_chunks(tmp_path,
                                                           monkeypatch):
    from voicebridge import file_transfer as FT

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)
    payload = bytes((i % 97) for i in range(900))
    target = root / "tamper.bin"
    target.write_bytes(payload)
    plan, chunks = _received(cfg, nid, target, len(payload))

    # Same-length, different bytes -> chunk digest mismatch.
    bad = [dict(c) for c in chunks]
    flipped = bytearray(base64.b64decode(bad[0]["data"]))
    flipped[0] ^= 0xFF
    bad[0]["data"] = base64.b64encode(bytes(flipped)).decode("ascii")
    res = FT.verify_transfer(cfg, nid, plan["transfer_id"], received=bad)
    assert res["state"] == "REFUSED"
    assert res["code"] == FT.FILE_TRANSFER_CORRUPT

    # Wrong length -> corrupt (plan chunk length mismatch).
    short = [dict(c) for c in chunks]
    short[-1]["data"] = base64.b64encode(b"x").decode("ascii")
    res = FT.verify_transfer(cfg, nid, plan["transfer_id"], received=short)
    assert res["code"] == FT.FILE_TRANSFER_CORRUPT


def test_verify_transfer_rejects_incomplete_and_duplicate(tmp_path,
                                                          monkeypatch):
    from voicebridge import file_transfer as FT

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)
    target = root / "set.bin"
    target.write_bytes(bytes(range(256)) * 4)
    plan, chunks = _received(cfg, nid, target, 1024)

    missing = chunks[:-1]
    res = FT.verify_transfer(cfg, nid, plan["transfer_id"], received=missing)
    assert res["state"] == "REFUSED"
    assert res["code"] == FT.FILE_TRANSFER_BAD_INPUT

    dup = [dict(c) for c in chunks]
    dup[1]["index"] = dup[0]["index"]
    res = FT.verify_transfer(cfg, nid, plan["transfer_id"], received=dup)
    assert res["code"] == FT.FILE_TRANSFER_BAD_INPUT

    bad_b64 = [dict(c) for c in chunks]
    bad_b64[0]["data"] = "not base64!!"
    res = FT.verify_transfer(cfg, nid, plan["transfer_id"], received=bad_b64)
    assert res["code"] == FT.FILE_TRANSFER_BAD_INPUT


def test_verify_transfer_typed_unknown_remote_and_estop(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT
    from voicebridge import node_host as NH
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)
    target = root / "x.bin"
    target.write_bytes(b"abc" * 100)
    plan, chunks = _received(cfg, nid, target, 300)

    unknown = FT.verify_transfer(cfg, nid, "xfer_deadbeef", received=[])
    assert unknown["state"] == "UNKNOWN"
    assert unknown["code"] == FT.FILE_TRANSFER_UNKNOWN_TRANSFER

    remote = NH.enroll(cfg, name="remote", endpoint="http://203.0.113.9:8443/",
                       capabilities=["file.transfer"], confirmed=True)
    rid = remote["node"]["id"]
    out = FT.verify_transfer(cfg, rid, plan["transfer_id"], received=chunks)
    assert out["state"] == "UNAVAILABLE"
    assert out["code"] == FT.FILE_TRANSFER_UNAVAILABLE

    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="user")
    out = FT.verify_transfer(cfg, nid, plan["transfer_id"], received=chunks)
    assert out["state"] == "ESTOP"
    assert out["code"] == FT.FILE_TRANSFER_ESTOP


def test_verify_transfer_detects_plan_tamper(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)
    target = root / "plan.bin"
    target.write_bytes(b"payload" * 50)
    plan, chunks = _received(cfg, nid, target, 350)

    store = cfg.state_dir() / FT.STORE_NAME
    raw = json.loads(store.read_text(encoding="utf-8"))
    raw["transfers"][plan["transfer_id"]]["size"] += 1
    store.write_text(json.dumps(raw), encoding="utf-8")
    out = FT.verify_transfer(cfg, nid, plan["transfer_id"], received=chunks)
    assert out["state"] == "REFUSED"
    assert out["code"] == FT.FILE_TRANSFER_CORRUPT
