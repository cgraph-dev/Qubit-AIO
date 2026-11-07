"""Reproducer for the PTY-lock stall in ``voicebridge.chat_transport``.

Measured defect: a chat turn can sit ``active`` for 120s+ emitting nothing
because the pump thread is blocked in ``futex_do_wait`` holding
``chat_transport._pty_lock``. ``pty_session`` held that module lock across
``AgentPtySession(...)`` construction, so ONE slow constructor blocked every
other turn process-wide — and the pump's own watchdog could never fire,
because the generator blocks *inside* ``next()``.

These tests prove both halves of the defect against the real module:

1. ``test_pty_lock_not_held_across_slow_constructor`` — a stub session class
   that sleeps inside ``__init__`` for key A must not block a concurrent
   ``pty_session`` call for key B. Fails while construction happens under the
   lock (thread B waits for the whole sleep), passes once it happens outside.
2. ``test_pty_lock_scope_single_construction_on_race`` — two threads racing
   for the SAME key still construct exactly one session and both receive the
   same instance (the double-check install), so the fix does not change the
   one-session-per-key contract.
3. ``test_stream_turn_honours_timeout_s`` — a turn whose producer never emits
   anything must yield a terminal ``TURN_TIMEOUT`` error within ``timeout_s``
   instead of blocking forever.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

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


class _SlowSession:
    """Stub standing in for ``AgentPtySession`` with a slow constructor."""

    def __init__(self, cfg, session_id, *, model=None, delay=0.0):
        time.sleep(delay)  # the "agent/network constructor" that can stall
        self.cfg = cfg
        self.session_id = session_id
        self.model = model
        self.alive = True


def test_pty_lock_not_held_across_slow_constructor(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    held = threading.Event()
    release = threading.Event()

    def _slow_ctor(cfg, sid, *, model=None):
        if sid == "sess-a":
            held.set()
            # Only A stalls: hold it open until the test proves B is through.
            release.wait(timeout=3.0)
        return _SlowSession(cfg, sid, model=model)

    monkeypatch.setattr(CT, "AgentPtySession", _slow_ctor)
    monkeypatch.setattr(CT, "_pty_sessions", {}, raising=False)

    cfg = object()  # only ``state_dir`` presence matters; none here -> "global"

    started_a = threading.Thread(target=lambda: CT.pty_session(cfg, "sess-a"),
                                 name="ctor-a", daemon=True)
    started_a.start()
    assert held.wait(timeout=2.0), "slow constructor never started"

    # While A is still inside its constructor, B must not be blocked.
    got_b = {}

    def _grab_b():
        t0 = time.monotonic()
        got_b["sess"] = CT.pty_session(cfg, "sess-b")
        got_b["elapsed"] = time.monotonic() - t0

    thread_b = threading.Thread(target=_grab_b, name="ctor-b", daemon=True)
    thread_b.start()
    thread_b.join(timeout=1.0)
    blocked = thread_b.is_alive()
    elapsed_b = got_b.get("elapsed")
    release.set()
    started_a.join(timeout=5.0)
    thread_b.join(timeout=5.0)

    assert not blocked, (
        "pty_session() for sess-b blocked while sess-a's constructor was "
        f"running ({elapsed_b!r}s) — _pty_lock is held across construction"
    )
    assert got_b["sess"].session_id == "sess-b"


def test_pty_lock_scope_single_construction_on_race(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    built = []
    barrier = threading.Barrier(4, timeout=5.0)

    def _ctor(cfg, sid, *, model=None):
        try:
            barrier.wait()  # all four constructors must be live at once
        except threading.BrokenBarrierError:
            pass  # serialised constructors: the race is still exercised
        time.sleep(0.05)
        sess = _SlowSession(cfg, sid, model=model)
        built.append(sess)
        return sess

    monkeypatch.setattr(CT, "AgentPtySession", _ctor)
    monkeypatch.setattr(CT, "_pty_sessions", {}, raising=False)

    cfg = object()
    results = []
    lock = threading.Lock()

    def _grab():
        sess = CT.pty_session(cfg, "raced")
        with lock:
            results.append(sess)

    threads = [threading.Thread(target=_grab, daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert len(results) == 4
    # One session per key survives: every caller gets the instance the
    # registry holds (double-check install; losers discard their own).
    final = CT.pty_session(cfg, "raced")
    assert all(r is final for r in results), (
        "a racing caller received a discarded session instance"
    )
    assert len(CT._pty_sessions) == 1


def test_stream_turn_honours_timeout_s(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge.providers import gateway as _gateway

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CT, "_estop", lambda _cfg: False)
    monkeypatch.setattr(CT, "_agent_enabled", lambda _cfg: True)
    monkeypatch.setattr(CT, "_agent_chat", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(CT, "_decide_branch",
                        lambda text, cfg: {"action": "answer_local",
                                           "kind": "conversation",
                                           "task_text": text, "params": {}})

    class _NeverDone:
        ok = False
        text = ""
        code = "MODEL_UNAVAILABLE"
        message = "never returns"

    def _hang(*args, **kwargs):
        time.sleep(60.0)  # the real stall: worker never puts a queue item
        return _NeverDone()

    monkeypatch.setattr(_gateway, "run_role", _hang)

    t0 = time.monotonic()
    events = list(CT.stream_turn(cfg, "ping", session="t-timeout",
                                 timeout_s=0.5))
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, f"stream_turn blocked {elapsed:.1f}s despite timeout_s=0.5"
    assert any(e.get("event") == "error" and e.get("code") == "TURN_TIMEOUT"
               for e in events), events
