"""Qubit-chat report envelope persistence (item 2: /chat reports in /qubit-chat).

A finished ``/chat``-worker build turn persists as an assistant message whose
turn envelope (``route=task`` + ``task=<key>`` + ``phase``/``model``/``sources``)
is what the ``/qubit-chat`` surface maps to its report index
(``ChatPage.toChatMessage`` -> ``report``/``taskKey``; ``reports.ts`` ->
``ReportList``). The vendored message row has no route/task columns, so the
adapter must carry the envelope in the ``display_metadata`` sidecar —
previously ``append()`` forwarded only tool_name/tool_calls/tool_call_id and
every report/task receipt vanished on reload (and on the live-voice poll,
which reads the same endpoint).

Hermetic: isolated XDG dirs; real store + real compat handlers; no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

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


def _scratch(cfg, sid="scratch-report-probe"):
    from voicebridge import chat_transport as CT

    st = CT.store(cfg)
    created = st.create("", session_id=sid)
    assert created.get("state") == "OK", created
    return st


def test_task_envelope_survives_append_and_read(tmp_path, monkeypatch):
    """route/task/phase/model/sources persist and read back top-level."""
    cfg = _cfg(tmp_path, monkeypatch)
    st = _scratch(cfg)
    st.append("scratch-report-probe", "assistant",
               "Working on it in /chat (task t1). I'll report back here.",
               meta={"route": "task", "task": "t1", "phase": "started"})
    st.append("scratch-report-probe", "assistant", "FULL REPORT BODY",
               meta={"route": "task", "task": "t1", "phase": "report",
                     "model": "m", "elapsed_ms": 5,
                     "sources": ["https://example.com/x"]})
    msgs = st.messages("scratch-report-probe")["messages"]
    assert len(msgs) == 2
    assert msgs[0]["meta"]["route"] == "task"
    assert msgs[0]["meta"]["task"] == "t1"
    assert msgs[0]["meta"]["phase"] == "started"
    body = msgs[1]["meta"]
    assert (body["route"], body["task"], body["phase"]) == (
        "task", "t1", "report")
    assert body["model"] == "m"
    assert body["elapsed_ms"] == 5
    assert body["sources"] == ["https://example.com/x"]


def test_terminal_shaped_row_reads_back_unchanged(tmp_path, monkeypatch):
    """Rows without an envelope gain no new meta keys (/chat byte-identical).

    A row the terminal/TUI wrote carries no ``qubit_chat`` sidecar, so the
    restore path must leave it exactly as before (only the row id sidecar).
    """
    cfg = _cfg(tmp_path, monkeypatch)
    st = _scratch(cfg)
    st.append("scratch-report-probe", "assistant", "plain terminal row")
    (msg,) = st.messages("scratch-report-probe")["messages"]
    assert msg["content"] == "plain terminal row"
    assert set(msg["meta"]) == {"id"}, msg["meta"]


def test_messages_endpoint_carries_envelope(tmp_path, monkeypatch):
    """GET .../messages serves the envelope the composer maps to reports."""
    from voicebridge import qubit_compat as qc

    cfg = _cfg(tmp_path, monkeypatch)
    _scratch(cfg)
    st_code = qc.h_chat_session_messages  # noqa: F841
    from voicebridge import chat_transport as CT

    CT.store(cfg).append(
        "scratch-report-probe", "assistant", "FULL REPORT BODY",
        meta={"route": "task", "task": "t9", "phase": "report"})
    code, body = qc.h_chat_session_messages(cfg, {}, {}, {
        "id": "scratch-report-probe"})
    assert code == 200, body
    (row,) = [m for m in body["messages"]
              if (m.get("content") or "") == "FULL REPORT BODY"]
    assert row["meta"]["route"] == "task"
    assert row["meta"]["task"] == "t9"
    assert row["meta"]["phase"] == "report"


def test_slash_envelope_survives(tmp_path, monkeypatch):
    """Slash-turn receipts (route=slash/entry) persist for the transcript."""
    cfg = _cfg(tmp_path, monkeypatch)
    st = _scratch(cfg)
    st.append("scratch-report-probe", "assistant", "Effective chat model: x",
               meta={"route": "slash", "entry": "model"})
    (msg,) = st.messages("scratch-report-probe")["messages"]
    assert msg["meta"]["route"] == "slash"
    assert msg["meta"]["entry"] == "model"


def test_envelope_never_aborts_append(tmp_path, monkeypatch):
    """Non-serializable envelope values degrade instead of failing the turn."""
    cfg = _cfg(tmp_path, monkeypatch)
    st = _scratch(cfg)
    st.append("scratch-report-probe", "assistant", "hi",
               meta={"route": "task", "task": "t1",
                     "sources": [{"nested": object()}]})
    (msg,) = st.messages("scratch-report-probe")["messages"]
    assert msg["content"] == "hi"
    assert msg["meta"]["route"] == "task"
