"""Agent 9 — OC-48 (no-credential parts): markdown workspace memory +
dreaming, local-only.

Exercises the real ``voicebridge.memory.workspace`` backend (hermetic tmp
state) for persistence, torn-index recovery, confirmation, emergency-stop
fail-closed, access control/negative paths, secret redaction, and the
typed external-provider refusal. No network, no credentials.
"""
from __future__ import annotations

import json
from pathlib import Path


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_write_read_list_forget_roundtrip(tmp_path, monkeypatch):
    from voicebridge.memory import workspace as WS

    cfg = _cfg(tmp_path, monkeypatch)
    out = WS.write_note(cfg, "Status", "line one\nline two",
                        tags=["plan", "plan", "v1"], confirmed=True)
    assert out["state"] == "OK" and out["created"] is True
    assert out["note"]["tags"] == ["plan", "v1"]
    assert out["note"]["size"] == len("line one\nline two")

    read = WS.read_note(cfg, "status")
    assert read["state"] == "OK"
    assert read["note"]["body"] == "line one\nline two"
    assert read["note"]["sha256"] == out["note"]["sha256"]

    listed = WS.list_notes(cfg)
    assert listed["count"] == 1 and listed["notes"][0]["name"] == "status"

    # Same name updates, not duplicates.
    again = WS.write_note(cfg, "status", "updated", confirmed=True)
    assert again["created"] is False
    assert WS.list_notes(cfg)["count"] == 1

    denied = WS.forget_note(cfg, "status")
    assert denied["state"] == "denied" and denied["code"] == WS.CONFIRM_CODE
    assert WS.read_note(cfg, "status")["state"] == "OK"
    gone = WS.forget_note(cfg, "status", confirmed=True)
    assert gone["state"] == "OK"
    assert WS.read_note(cfg, "status")["state"] == "NOT_FOUND"
    assert WS.list_notes(cfg)["count"] == 0


def test_confirmation_and_estop_fail_closed(tmp_path, monkeypatch):
    from voicebridge.memory import workspace as WS

    cfg = _cfg(tmp_path, monkeypatch)
    staged = WS.write_note(cfg, "note", "body")
    assert staged["state"] == "denied" and staged["code"] == WS.CONFIRM_CODE
    assert WS.list_notes(cfg)["count"] == 0, "denied writes persist nothing"

    monkeypatch.setattr(WS, "estop_engaged", lambda _c: True)
    for call in (lambda: WS.write_note(cfg, "note", "x", confirmed=True),
                 lambda: WS.forget_note(cfg, "note", confirmed=True),
                 lambda: WS.dream(cfg, confirmed=True)):
        out = call()
        assert out["state"] == "ESTOP" and out["code"] == WS.ESTOP_CODE


def test_persistence_and_corrupt_index_recovery(tmp_path, monkeypatch):
    from voicebridge.memory import workspace as WS

    cfg = _cfg(tmp_path, monkeypatch)
    WS.write_note(cfg, "alpha", "first", confirmed=True)
    WS.write_note(cfg, "beta", "second", confirmed=True)
    index = Path(WS.workspace_dir(cfg)) / WS.INDEX_NAME
    assert index.is_file()
    index.write_text("{corrupt json")

    # The index self-heals from the durable note files; no note is lost.
    listed = WS.list_notes(cfg)
    assert listed["count"] == 2
    assert {n["name"] for n in listed["notes"]} == {"alpha", "beta"}
    assert WS.read_note(cfg, "alpha")["note"]["body"] == "first"
    # A later write persists on the rebuilt index.
    WS.write_note(cfg, "gamma", "third", confirmed=True)
    assert WS.list_notes(cfg)["count"] == 3


def test_dream_dedupes_and_summarizes(tmp_path, monkeypatch):
    from voicebridge.memory import workspace as WS

    cfg = _cfg(tmp_path, monkeypatch)
    WS.write_note(cfg, "dup-a", "same content", confirmed=True)
    WS.write_note(cfg, "dup-b", "same content", confirmed=True)
    WS.write_note(cfg, "unique", "a different observation", confirmed=True)
    WS.write_note(cfg, "blank", "   ", confirmed=True)

    denied = WS.dream(cfg)
    assert denied["state"] == "denied" and denied["code"] == WS.CONFIRM_CODE

    out = WS.dream(cfg, confirmed=True)
    assert out["state"] == "OK"
    assert out["kept"] == 2
    assert out["removed_empty"] == ["blank"]
    assert len(out["removed_dupe"]) == 1
    summary = Path(WS.workspace_dir(cfg)) / WS.DREAM_NAME
    assert summary.is_file()
    text = summary.read_text()
    assert "Dreaming summary" in text and "unique" in text
    assert WS.list_notes(cfg)["count"] == 2, "dreaming is idempotent on state"


def test_access_control_and_negative(tmp_path, monkeypatch):
    from voicebridge.memory import workspace as WS

    cfg = _cfg(tmp_path, monkeypatch)
    for bad in ("../escape", "Bad Name", "", "a/b", "x" * 65):
        try:
            WS.write_note(cfg, bad, "x", confirmed=True)
        except WS.WorkspaceError as exc:
            assert exc.code == WS.BAD_INPUT_CODE, bad
        else:
            raise AssertionError(f"unsafe name accepted: {bad!r}")
    try:
        WS.write_note(cfg, "ok", "x" * (WS.MAX_NOTE_BODY + 1),
                      confirmed=True)
    except WS.WorkspaceError as exc:
        assert exc.code == WS.BAD_INPUT_CODE
    else:
        raise AssertionError("oversized body accepted")
    assert WS.read_note(cfg, "missing")["state"] == "NOT_FOUND"


def test_secret_redaction_never_persists(tmp_path, monkeypatch):
    from voicebridge.memory import workspace as WS

    cfg = _cfg(tmp_path, monkeypatch)
    secret = "sk-abcdefghijklmnop123456"
    out = WS.write_note(cfg, "leak", f"token={secret}", confirmed=True)
    assert out["state"] == "OK"
    assert secret not in Path(WS.workspace_dir(cfg), "leak.md").read_text()
    assert secret not in json.dumps(WS.read_note(cfg, "leak"))
    assert secret not in json.dumps(WS.list_notes(cfg))


def test_external_providers_are_typed_unavailable():
    from voicebridge.memory import workspace as WS

    out = WS.providers()
    assert out["state"] == "OK"
    by_name = {p["name"]: p for p in out["providers"]}
    assert by_name["markdown-workspace"]["state"] == "AVAILABLE"
    for name in ("lancedb", "wiki", "honcho", "cloud-sync"):
        assert by_name[name]["state"] == "UNAVAILABLE"
        assert by_name[name]["code"] == WS.UNAVAILABLE_CODE
