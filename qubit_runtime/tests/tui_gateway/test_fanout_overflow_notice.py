"""A saturated session-event subscriber learns it must resume instead of losing its socket.

``gateway.session_events.SessionEvents.publish`` always passes an ``overflow`` notice callable, so a
publish that meets a full fanout mailbox must hand that peer a ``session.replay_gap`` frame naming
that session and leave its socket open. Without a notice (every other caller of
``FanoutTransport.write``) the old contract stands: the peer is handed to the close path so its
client reconnects and replays.

The peer writer is parked for the duration, which is what a kernel-blocked socket does to its
mailbox; membership, backpressure accounting and frame ownership are the production ones.
"""
import threading

from gateway.session_events import SessionEvents
from tui_gateway.transport import FanoutTransport

MAX = FanoutTransport._MAX_PENDING_FRAMES


class RecordingTransport:
    def __init__(self):
        self.frames, self.closed = [], False
        self._lock = threading.Lock()

    def write(self, obj):
        with self._lock:
            self.frames.append(obj)
        return True

    def close(self):
        self.closed = True

    def snapshot(self):
        with self._lock:
            return list(self.frames)


def _parked_writer(monkeypatch, held):
    """Stand-in for the drain thread: park the peer without writing anything.

    ``writing`` is cleared because this stand-in never completes a batch — in production the drain
    loop clears it, and with it held at True the mailbox could never fill.
    """
    def park(_self, peer):
        held.append(peer)
        peer.writing = False

    monkeypatch.setattr(FanoutTransport, "_drain", park)


def _queued(peer):
    return [frame for frame, _size in peer.pending]


def test_full_mailbox_gets_a_replay_gap_and_keeps_its_socket(monkeypatch):
    events = SessionEvents()
    retired = []
    events.on_overflow = retired.append
    peer = RecordingTransport()
    events.fanout.attach(peer)
    held = []
    _parked_writer(monkeypatch, held)

    def refuse(_transport):
        raise AssertionError("a noticed overflow must not close the socket")

    monkeypatch.setattr(FanoutTransport, "_signal_overflow_detach", lambda _self, t: refuse(t))

    for n in range(MAX - 1):  # fill the mailbox to one short of its bound
        events.publish("chat-1", {"delta": n})
    assert events.fanout.write({"jsonrpc": "2.0", "method": "event", "params": {"type": "fill"}}) is True

    # The publish that meets the bound: the peer loses the subscription, not the socket.
    assert events.publish("chat-1", {"delta": "overflow-marker"}) is True  # the gap took the frame
    assert retired == [peer]
    pending = _queued(held[0])  # the notice, and nothing else, is queued for it
    assert [f["params"]["type"] for f in pending] == ["session.replay_gap"]
    assert pending[0]["params"]["session_id"] == "chat-1"
    assert pending[0]["params"]["payload"]["latest_seq"] >= 1
    assert peer.closed is False
    assert peer.snapshot() == []  # nothing of the dropped backlog reached the socket
    events.fanout.close()


def test_without_a_notice_the_peer_is_handed_to_the_close_path(monkeypatch):
    peer = RecordingTransport()
    fanout = FanoutTransport(peer)
    held = []
    _parked_writer(monkeypatch, held)
    closed = []
    monkeypatch.setattr(FanoutTransport, "_signal_overflow_detach", lambda _self, t: closed.append(t))

    for n in range(MAX - 1):
        fanout.write({"jsonrpc": "2.0", "method": "event", "params": {"n": n}})
    fanout.write({"jsonrpc": "2.0", "method": "event", "params": {"type": "fill"}})
    assert fanout.write({"jsonrpc": "2.0", "method": "event", "params": {"n": "overflow"}}) is False
    assert closed == [peer]
    assert fanout.contains(peer) is False
    assert peer.snapshot() == []
    fanout.close()


def test_the_notice_names_the_session_whose_publish_overflowed(monkeypatch):
    events = SessionEvents()
    events.on_overflow = lambda _t: None
    peer = RecordingTransport()
    events.fanout.attach(peer)
    held = []
    _parked_writer(monkeypatch, held)
    monkeypatch.setattr(FanoutTransport, "_signal_overflow_detach", lambda _self, t: None)

    for n in range(MAX - 1):
        events.publish("chat-a", {"delta": n})
    events.fanout.write({"jsonrpc": "2.0", "method": "event", "params": {"type": "fill"}})
    assert events.publish("chat-a", {"delta": "overflow"}) is True  # the replay gap takes the frame
    pending = _queued(held[0])
    assert [f["params"]["type"] for f in pending] == ["session.replay_gap"]
    assert [f["params"]["session_id"] for f in pending] == ["chat-a"]
    assert peer.snapshot() == []  # nothing of chat-a's queued backlog was written
    assert peer.snapshot() == []
    events.fanout.close()
