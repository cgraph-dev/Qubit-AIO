"""“new session” is a real create action — never a silent rename.

Live defect: with the L1.5 augmenter enabled, ``new session`` was routed to
``session_rename`` (a write) with the whole utterance as its arguments, and the
entry was not confirmation-gated. Two independent guards now hold:

* the deterministic registry owns the phrase and maps it to a real create
  action on the canonical chat-session store (L1.5 is not consulted);
* L1.5 vetoes rename/create entries even for an adversarial model, and
  ``session_rename`` is confirmation-gated.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

PHRASES = ("new session", "new chat", "start a new session",
           "create a new session", "qubit, new session")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


class _FakeStore:
    def __init__(self):
        self.created = []
        self.listed = []

    def create(self, name="", **kwargs):
        self.created.append(name)
        return {"state": "OK",
                "session": {"id": "chat-new", "name": name or None},
                "messages": []}

    def list(self, **kwargs):
        self.listed.append(kwargs)
        return {"sessions": []}


@pytest.fixture()
def fake_store(monkeypatch):
    from voicebridge import chat_transport

    store = _FakeStore()
    monkeypatch.setattr(chat_transport, "store", lambda cfg: store)
    return store


def _ctx(cfg):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, session="new-session", source="user")


def test_new_session_phrases_route_to_the_create_entry():
    from voicebridge import voice_commands as VC

    for text in PHRASES:
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (text, res.kind, res.speech)
        assert res.entry is not None and res.entry.id == "chat_session_new", (
            text, res.entry.id if res.entry else None)


def test_new_session_executes_a_real_create_not_a_rename(
        tmp_path, monkeypatch, fake_store):
    from voicebridge import voice_commands as VC

    out = VC.execute(_ctx(_cfg(tmp_path, monkeypatch)),
                     VC.resolve("new session"))

    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert out.confirmation_id == "", "create must not stage a rename"
    assert fake_store.created == [""], fake_store.created
    assert out.receipt["result"]["created"] is True
    assert out.receipt["result"]["session"]["state"] == "OK"


def test_rename_session_is_confirmation_gated(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    entry = VC.REGISTRY["session_rename"]
    assert entry.confirm is True
    out = VC.execute(_ctx(_cfg(tmp_path, monkeypatch)),
                     VC.resolve("rename session 12 to invoices"))
    assert out.kind == VC.NEEDS_CONFIRM, (out.kind, out.speech)
    assert out.confirmation_id
