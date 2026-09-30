"""Packet 8U / OpenClaw D3 — node-host file transfer (list/fetch/stream/resume).

Real behavior proofs (hermetic, tmp dirs): approved-root confinement with
traversal and symlink escapes rejected, bounded fetch by offset/length with
SHA-256 correctness, resumable stream plans with per-chunk digests and
replay/integrity checking, typed unknown-node/transfer, oversize and
confirmation gates, remote-node UNAVAILABLE (no live transport), estop
fail-closed, redaction, and no network touched.
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import urllib.request


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
    (root / "sub").mkdir(exist_ok=True)
    (root / "sub" / "a.txt").write_text("hello\n")
    PJ.create(cfg, name, [str(root)], requested_by="cli:user", confirmed=True)
    return root


def _enroll_local(cfg):
    from voicebridge import node_host as NH

    out = NH.enroll(cfg, name="local", endpoint="http://127.0.0.1:8443/",
                    capabilities=["file.transfer"], confirmed=True)
    assert out["state"] == "OK", out
    return out["node"]["id"]


def test_status_on_empty_state_never_raises(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT

    cfg = _cfg(tmp_path, monkeypatch)
    out = FT.status(cfg)
    assert out["state"] == "OK"
    assert out["estop"] is False
    assert out["nodes"] == 0 and out["local_nodes"] == 0
    assert out["transfers"] == 0
    assert out["remote_transport"] == "UNAVAILABLE"
    assert out["transfer_state"] == "READY"
    assert set(FT.SUPPORTED_OPS) <= set(out["supported"])


def test_list_directory_confined_and_escape_rejected(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)

    out = FT.list_directory(cfg, nid, str(root))
    assert out["state"] == "OK"
    rows = {e["name"]: e for e in out["entries"]}
    assert rows["notes.txt"]["type"] == "file"
    assert rows["notes.txt"]["size"] == len(b"hello world\n")
    assert rows["sub"]["type"] == "dir"
    assert all({"name", "type", "size", "mtime"} <= set(e)
               for e in out["entries"])

    outside = tmp_path / "outside.txt"
    outside.write_text("private\n")
    for bad in (str(outside), str(root / ".." / "outside.txt"),
                "/etc/hostname"):
        res = FT.list_directory(cfg, nid, bad)
        assert res["state"] == "REFUSED", bad
        assert res["code"] in (FT.FILE_TRANSFER_ESCAPE,
                               FT.FILE_TRANSFER_BAD_INPUT), bad

    link = root / "evil"
    link.symlink_to(outside)
    res = FT.list_directory(cfg, nid, str(link))
    assert res["code"] == FT.FILE_TRANSFER_ESCAPE


def test_list_directory_bounded(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)
    for i in range(5):
        (root / f"f{i}.txt").write_text("x")
    out = FT.list_directory(cfg, nid, str(root), limit=2)
    assert out["state"] == "OK"
    assert out["count"] == 2 and out["truncated"] is True
    assert out["limit"] == 2


def test_fetch_offset_len_and_sha256(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)
    payload = bytes((i % 251) for i in range(1000))
    target = root / "chunk.bin"
    target.write_bytes(payload)

    out = FT.fetch(cfg, nid, str(target), offset=10, max_bytes=50)
    assert out["state"] == "OK"
    assert base64.b64decode(out["data"]) == payload[10:60]
    assert out["sha256"] == hashlib.sha256(payload[10:60]).hexdigest()
    assert out["offset"] == 10 and out["length"] == 50
    assert out["eof"] is False and out["trust"] == "untrusted-file-bytes"

    tail = FT.fetch(cfg, nid, str(target), offset=990, max_bytes=50)
    assert tail["length"] == 10 and tail["eof"] is True
    assert base64.b64decode(tail["data"]) == payload[990:]

    assert FT.fetch(cfg, nid, str(target),
                    offset=1001)["code"] == FT.FILE_TRANSFER_BAD_INPUT
    assert FT.fetch(cfg, nid, str(target),
                    max_bytes=FT.MAX_FETCH_BYTES + 1
                    )["code"] == FT.FILE_TRANSFER_TOO_LARGE


def test_fetch_confirm_gate_and_oversize(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)

    big = root / "big.bin"
    big.write_bytes(b"a" * (FT.MAX_UNCONFIRMED_BYTES + 1))
    gate = FT.fetch(cfg, nid, str(big), max_bytes=16)
    assert gate["state"] == "REFUSED"
    assert gate["code"] == FT.FILE_TRANSFER_CONFIRM_REQUIRED
    ok = FT.fetch(cfg, nid, str(big), max_bytes=16, confirmed=True)
    assert ok["state"] == "OK" and ok["length"] == 16

    huge = root / "huge.bin"
    with open(huge, "wb") as fh:
        fh.truncate(FT.MAX_TRANSFER_BYTES + 1)
    assert FT.fetch(cfg, nid, str(huge), confirmed=True
                    )["code"] == FT.FILE_TRANSFER_TOO_LARGE
    assert FT.stream_plan(cfg, nid, str(huge)
                          )["code"] == FT.FILE_TRANSFER_TOO_LARGE


def test_stream_plan_resume_roundtrip(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)
    payload = bytes((i * 7 + 3) % 256 for i in range(1000))
    target = root / "plan.bin"
    target.write_bytes(payload)

    plan = FT.stream_plan(cfg, nid, str(target), chunk_size=400)
    assert plan["state"] == "OK"
    assert plan["chunk_count"] == 3
    assert plan["size"] == 1000
    assert plan["digest"] == hashlib.sha256(payload).hexdigest()
    for index, chunk in enumerate(plan["chunks"]):
        assert chunk["index"] == index
        start = chunk["offset"]
        end = start + chunk["length"]
        assert chunk["sha256"] == hashlib.sha256(payload[start:end]).hexdigest()
    tid = plan["transfer_id"]

    first = FT.resume(cfg, nid, tid, offset=0)
    assert first["state"] == "OK"
    assert first["chunk"]["offset"] == 0 and first["replayed"] is False
    replayed = FT.resume(cfg, nid, tid, offset=0)
    assert replayed["replayed"] is True
    assert replayed["chunk"] == first["chunk"]

    second = FT.resume(cfg, nid, tid, offset=400)
    assert second["chunk"]["index"] == 1 and second["next_offset"] == 800
    done = FT.resume(cfg, nid, tid, offset=1000)
    assert done["state"] == "COMPLETE" and done["eof"] is True
    assert FT.resume(cfg, nid, tid, offset=100
                     )["code"] == FT.FILE_TRANSFER_BAD_INPUT


def test_resume_detects_file_and_plan_corruption(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)
    payload = bytes((i % 97) for i in range(900))
    target = root / "corrupt.bin"
    target.write_bytes(payload)

    tid = FT.stream_plan(cfg, nid, str(target), chunk_size=300)["transfer_id"]
    target.write_bytes(b"x" * len(payload))
    assert FT.resume(cfg, nid, tid, offset=0
                     )["code"] == FT.FILE_TRANSFER_CORRUPT

    target.write_bytes(payload)
    tid2 = FT.stream_plan(cfg, nid, str(target), chunk_size=300)["transfer_id"]
    store = cfg.state_dir() / FT.STORE_NAME
    raw = json.loads(store.read_text(encoding="utf-8"))
    raw["transfers"][tid2]["chunks"][0]["sha256"] = "0" * 64
    store.write_text(json.dumps(raw), encoding="utf-8")
    assert FT.resume(cfg, nid, tid2, offset=0
                     )["code"] == FT.FILE_TRANSFER_CORRUPT


def test_unknown_node_and_transfer_typed(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)

    for out in (FT.list_directory(cfg, "node_missing", str(root)),
                FT.fetch(cfg, "node_missing", str(root / "notes.txt")),
                FT.stream_plan(cfg, "node_missing", str(root / "notes.txt"))):
        assert out["state"] == "UNKNOWN"
        assert out["code"] == FT.FILE_TRANSFER_UNKNOWN_NODE
    assert FT.resume(cfg, nid, "xfer_deadbeef", offset=0
                     )["code"] == FT.FILE_TRANSFER_UNKNOWN_TRANSFER


def test_remote_node_is_unavailable(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    out = NH.enroll(cfg, name="remote", endpoint="http://203.0.113.9:8443/",
                    confirmed=True)
    rid = out["node"]["id"]

    for res in (FT.list_directory(cfg, rid, str(root)),
                FT.fetch(cfg, rid, str(root / "notes.txt")),
                FT.stream_plan(cfg, rid, str(root / "notes.txt")),
                FT.resume(cfg, rid, "xfer_x", offset=0)):
        assert res["state"] == "UNAVAILABLE", res
        assert res["code"] == FT.FILE_TRANSFER_UNAVAILABLE


def test_estop_fail_closed(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)
    nid = _enroll_local(cfg)
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="user")

    for res in (FT.list_directory(cfg, nid, str(root)),
                FT.fetch(cfg, nid, str(root / "notes.txt")),
                FT.stream_plan(cfg, nid, str(root / "notes.txt")),
                FT.resume(cfg, nid, "xfer_x", offset=0)):
        assert res["state"] == "ESTOP", res
        assert res["code"] == FT.FILE_TRANSFER_ESTOP
    assert FT.status(cfg)["estop"] is True
    assert FT.status(cfg)["transfer_state"] == "ESTOP"


def test_no_network_and_redaction(tmp_path, monkeypatch):
    from voicebridge import file_transfer as FT
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    root = _project(cfg, tmp_path)

    def _boom(*_a, **_k):
        raise AssertionError("file transfer touched the network")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    secret = "sk-abcdef1234567890"
    out = NH.enroll(cfg, name="alpha api_key=" + secret,
                    endpoint="http://127.0.0.1:8443/?api_key=" + secret,
                    confirmed=True)
    nid = out["node"]["id"]
    leak = root / f"leak-api_key={secret}.txt"
    leak.write_text("x")

    listed = FT.list_directory(cfg, nid, str(root))
    blob = json.dumps(listed)
    assert secret not in blob
    assert "[REDACTED]" in blob
    assert secret not in json.dumps(FT.status(cfg))

    assert FT.fetch(cfg, nid, str(root / "notes.txt"))["state"] == "OK"
    assert FT.stream_plan(cfg, nid, str(root / "notes.txt"))["state"] == "OK"
