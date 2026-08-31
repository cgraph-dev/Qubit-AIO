"""Regression tests for the barge-in stream pause/join contract (repair B).

The join-aware ``_pause_barge_stream`` must be the ONLY implementation: it
stops+closes the barge InputStream AND joins its monitor thread so the
PipeWire node is fully released before the capture stream opens (two
concurrent streams on the Scarlett corrupted PortAudio). A stale non-join
duplicate must not exist.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.orchestrator as O  # noqa: E402


class _Stream:
    def __init__(self) -> None:
        self.stopped = False
        self.closed = False

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def _stub(barge_stream, monitor):
    return type("Stub", (), {
        "_barge_stream": barge_stream,
        "_barge_monitor": monitor,
    })()


def test_pause_barge_stream_closes_and_joins_monitor():
    stream = _Stream()

    def _mon_work():
        # like the real barge monitor: it exits once its stream is closed
        while not stream.closed:
            time.sleep(0.01)

    mon = threading.Thread(target=_mon_work, daemon=True)
    mon.start()
    time.sleep(0.05)
    stub = _stub(stream, mon)
    was_open = O.VoiceOrchestrator._pause_barge_stream(stub)
    assert was_open is True
    assert stream.stopped and stream.closed
    assert stub._barge_stream is None
    # the monitor thread was joined (bounded): node fully released
    assert not mon.is_alive(), "monitor must be joined before capture opens"


def test_pause_barge_stream_returns_false_when_not_open():
    stub = _stub(None, None)
    assert O.VoiceOrchestrator._pause_barge_stream(stub) is False


def test_pause_barge_stream_without_monitor_is_safe():
    stream = _Stream()
    stub = _stub(stream, None)
    assert O.VoiceOrchestrator._pause_barge_stream(stub) is True
    assert stream.closed


def test_only_one_pause_barge_stream_definition():
    """The non-join duplicate must not exist: exactly one implementation."""
    import inspect
    src = inspect.getsource(O.VoiceOrchestrator)
    # count definitions of the method body start
    assert src.count("def _pause_barge_stream(self) -> bool:") == 1
    assert "JOINS the barge monitor thread" in src