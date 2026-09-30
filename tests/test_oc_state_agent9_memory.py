"""Agent 9 — qubit-ledger ``memory`` area: remember/search/forget.

These rows were UNVERIFIED for lack of a production-caller test. This drives
the real voice backend (``voicebridge.voice_commands._b_memory`` over
``MemoryStore``) and checks persistence, secret rejection, and the
idempotent negative path.
"""
from __future__ import annotations

from pathlib import Path


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _voice(cfg, entry_id, session="v-memory", **args):
    from voicebridge import voice_commands as VC

    entry = VC.REGISTRY[entry_id]
    ctx = VC.make_ctx(cfg, actor="voice:user", session=session, source="user")
    out = VC.execute(ctx, VC.Resolution(VC.MATCH, entry=entry, args=args))
    if out.kind == VC.NEEDS_CONFIRM:
        out = VC.confirm_pending(ctx, out.confirmation_id)
    return out


def test_remember_search_forget_roundtrip_and_persistence(tmp_path,
                                                          monkeypatch):
    from voicebridge.memory import MemoryStore

    cfg = _cfg(tmp_path, monkeypatch)
    remembered = _voice(cfg, "memory_remember", text="the sky is blue")
    assert remembered.kind == "executed", remembered.speech
    assert remembered.receipt["result"]["remembered"] is True

    hit = _voice(cfg, "memory_search", query="sky")
    results = hit.receipt["result"]["results"]
    assert [r["text"] for r in results] == ["the sky is blue"]
    memory_id = results[0]["id"]

    # Persistence: a fresh store over the same state sees the memory.
    store = MemoryStore(Path(cfg.state_dir()) / "memory" / "memory.db")
    try:
        assert store.get(memory_id)["text"] == "the sky is blue"
    finally:
        store.close()

    forgotten = _voice(cfg, "memory_forget", memory_id=memory_id)
    assert forgotten.receipt["result"]["forgotten"] is True
    assert _voice(cfg, "memory_search", query="sky").receipt["result"][
        "results"] == []
    # Forgetting an unknown id is an idempotent, typed no-op, never a crash.
    unknown = _voice(cfg, "memory_forget", memory_id=99999)
    assert unknown.kind in ("executed", "failed")


def test_secret_shaped_text_is_refused(tmp_path, monkeypatch):
    from voicebridge.memory import MemoryStore

    cfg = _cfg(tmp_path, monkeypatch)
    secret = "sk-abcdefghijklmnop123456"
    out = _voice(cfg, "memory_remember", text=f"token {secret}")
    assert out.kind == "failed"
    assert "SecretRejected" in str(out.receipt.get("code", ""))
    store = MemoryStore(Path(cfg.state_dir()) / "memory" / "memory.db")
    try:
        assert store.search("token") == []
    finally:
        store.close()
