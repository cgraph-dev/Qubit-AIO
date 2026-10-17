"""PTY replay ring: output printed while a browser tab is gone must survive.

Upstream parity (``PtySession.buffer``, 1 MB): a refresh/close DETACHES the
socket while the turn keeps printing, and the pump only forwards new bytes, so
without a ring every line printed during the gap was lost from the transcript.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge import pty_ws as P  # noqa: E402


def _session():
    return P._Session(
        bridge=object(),
        ticket=P._Ticket(scope="pty", session="s", owner="o", target="t",
                         expires_at=float("inf")),
    )


def test_buffer_keeps_everything_under_the_cap():
    s = _session()
    P._buffer_output(s, b"hello ")
    P._buffer_output(s, b"world")
    assert bytes(s.buffer) == b"hello world"


def test_buffer_drops_oldest_bytes_past_the_cap():
    s = _session()
    P._buffer_output(s, b"a" * (P.PTY_BUFFER_CAP - 3))
    P._buffer_output(s, b"bcdef")
    assert len(s.buffer) == P.PTY_BUFFER_CAP
    assert bytes(s.buffer).endswith(b"bcdef")
    # The oldest bytes are the ones dropped (2 of them here): the tail is
    # intact and the ring is still exactly at the cap.
    assert bytes(s.buffer)[:5] == b"aaaaa"


def test_replay_writes_the_ring_once(monkeypatch):
    s = _session()
    P._buffer_output(s, b"console output")
    calls = []
    monkeypatch.setattr(P, "write_frame",
                        lambda sock, op, payload=b"": calls.append((sock, op, payload)) or True)
    assert P._replay_buffer(s, "SOCK") is True
    assert calls == [("SOCK", P._OP_BIN, b"console output")]


def test_replay_of_empty_ring_is_a_no_op(monkeypatch):
    s = _session()
    calls = []
    monkeypatch.setattr(P, "write_frame", lambda *a, **k: calls.append(a) or True)
    assert P._replay_buffer(s, "SOCK") is True
    assert calls == []


def test_replay_failure_is_reported_not_raised(monkeypatch):
    s = _session()
    P._buffer_output(s, b"x")

    def _boom(*_a, **_k):
        raise OSError("socket gone")

    monkeypatch.setattr(P, "write_frame", _boom)
    assert P._replay_buffer(s, "SOCK") is False
