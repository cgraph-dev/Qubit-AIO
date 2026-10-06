"""Output-activity tracker for the voice orb (OpenClaw semantics port).

Semantics ported from the upstream ``talk/output-activity-tracker.ts``
(that file, not copied): a small mutable tracker accumulates output
counters (``audioMs`` / ``sourceAudioBytes`` / ``sinkAudioBytes`` /
``chunks``) and playback state (``playbackStarted`` / ``streamEnding``)
and answers whether assistant output is active *right now*.

The orb uses it to keep the ``speaking`` state exact when a TTS journal
event is missed: while reply frames are observed the tracker marks
``audio`` (refreshing :meth:`OutputActivityTracker.is_active`), a speak
marker marks playback started, and a speak_done/expiry marks the stream
ending. ``is_active`` stays true while audio flows and for a short
``tail_s`` after the last observed frame, then goes false — so green
ends at the audio tail instead of waiting out a fixed event window.

Pure: no I/O, no widget, never raises on junk. The clock is injectable
for deterministic tests (default wall-clock seconds, matching
``resolve_orb_state``'s ``now``).
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, Optional

#: Default tail after the last observed audio frame during which output
#: still reads active. Bridges discrete frames / the poll cadence so a
#: playing reply never flickers to idle between samples.
DEFAULT_TAIL_S = 0.12


def _finite(value: Any, default: float = 0.0) -> float:
    """Coerce ``value`` to a finite float, else ``default``. Never raises."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    except Exception:
        return default
    if not math.isfinite(num):
        return default
    return num


class OutputActivityTracker:
    """Mutable output counters + playback state for one output stream."""

    def __init__(self, now: Optional[Callable[[], float]] = None) -> None:
        self._now: Callable[[], float] = now if callable(now) else time.time
        self._audio_ms: float = 0.0
        self._chunks: int = 0
        self._source_bytes: float = 0.0
        self._sink_bytes: float = 0.0
        self._playback_started: bool = False
        self._stream_ending: bool = False
        self._last_audio_at: Optional[float] = None
        self._playback_started_at: Optional[float] = None

    # -- internal ------------------------------------------------------
    def _clock(self) -> Optional[float]:
        """Current injectable-clock value as a finite float, else None."""
        try:
            value = float(self._now())
        except Exception:
            return None
        if not math.isfinite(value):
            return None
        return value

    # -- transitions ---------------------------------------------------
    def mark_stream_opened(self) -> None:
        """A new stream: clear playback markers, keep cumulative counters.

        Mirrors upstream: totals survive until :meth:`reset`, so callers
        can preserve output stats across stream opens.
        """
        self._stream_ending = False
        self._playback_started = False
        self._playback_started_at = None
        self._last_audio_at = None

    def mark_stream_ending(self) -> None:
        """The output stream is ending (speak_done / watchdog expiry)."""
        self._stream_ending = True

    def mark_playback_started(self) -> None:
        """Record the first playback start of this stream (idempotent)."""
        if self._playback_started:
            return
        self._playback_started = True
        self._playback_started_at = self._clock()

    def mark_audio(self, delta: Any = None) -> None:
        """Record one output audio chunk; refreshes the activity clock.

        ``delta`` is an upstream-style mapping (``audioMs`` /
        ``sourceAudioBytes`` / ``sinkAudioBytes``); negative or malformed
        values clamp to zero while the chunk is still counted. A plain
        number is accepted as ``audioMs``. Never raises.
        """
        audio_ms: Any = 0
        source_bytes: Any = 0
        sink_bytes: Any = 0
        try:
            if hasattr(delta, "get"):
                audio_ms = delta.get("audioMs", 0)
                source_bytes = delta.get("sourceAudioBytes", 0)
                sink_bytes = delta.get("sinkAudioBytes", 0)
            elif delta is not None:
                audio_ms = delta
        except Exception:
            audio_ms = source_bytes = sink_bytes = 0
        self._audio_ms += max(0.0, _finite(audio_ms))
        self._source_bytes += max(0.0, _finite(source_bytes))
        self._sink_bytes += max(0.0, _finite(sink_bytes))
        self._chunks += 1
        self._last_audio_at = self._clock()

    def reset(self) -> None:
        """Clear every counter and playback marker (fresh session)."""
        self._audio_ms = 0.0
        self._chunks = 0
        self._source_bytes = 0.0
        self._sink_bytes = 0.0
        self._playback_started = False
        self._stream_ending = False
        self._last_audio_at = None
        self._playback_started_at = None

    # -- queries -------------------------------------------------------
    def is_active(self, now: Any, *, tail_s: Any = DEFAULT_TAIL_S) -> bool:
        """True while output is flowing or within ``tail_s`` of it.

        False when no audio has ever been observed, when the last frame
        is older than the tail, or when the clock is unreadable (fail
        closed so a junk ``now`` never forces green). A stamp in the
        future is treated as live (clock skew, still activity). Never
        raises.
        """
        try:
            now_f = float(now)
        except (TypeError, ValueError):
            return False
        except Exception:
            return False
        if not math.isfinite(now_f):
            return False
        tail = _finite(tail_s, DEFAULT_TAIL_S)
        if tail < 0.0:
            tail = 0.0
        last = self._last_audio_at
        if last is None or not math.isfinite(last):
            return False
        age = now_f - last
        if age < 0.0:
            return True
        return age <= tail

    def elapsed_playback_s(self, now: Any = None) -> float:
        """Seconds since playback started (0 when it never did). Never raises."""
        start = self._playback_started_at
        if start is None or not math.isfinite(start):
            return 0.0
        try:
            now_f = float(now) if now is not None else _finite(self._clock())
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(now_f):
            return 0.0
        elapsed = now_f - start
        return elapsed if elapsed > 0.0 else 0.0

    def playback_watchdog_delay_s(self, now: Any, margin_s: Any = 0.0,
                                  min_s: Any = 1.0) -> Optional[float]:
        """Delay before playback is judged overdue vs emitted audio.

        Upstream port: ``max(min_s, audio_s - elapsed + margin_s)`` while
        playback has started and audio was emitted; ``None`` otherwise.
        Never raises.
        """
        start = self._playback_started_at
        if start is None or not math.isfinite(start) or self._audio_ms <= 0.0:
            return None
        elapsed = self.elapsed_playback_s(now)
        emitted = self._audio_ms / 1000.0
        delay = emitted - elapsed + _finite(margin_s)
        return max(_finite(min_s, 1.0), delay)

    def snapshot(self) -> Dict[str, Any]:
        """Current counters + playback timestamps (upstream key names).

        ``lastAudioAt`` / ``playbackStartedAt`` are omitted when unset,
        exactly like the upstream optional fields. Never raises.
        """
        snap: Dict[str, Any] = {
            "audioMs": self._audio_ms,
            "chunks": self._chunks,
            "sourceAudioBytes": self._source_bytes,
            "sinkAudioBytes": self._sink_bytes,
            "playbackStarted": self._playback_started,
            "streamEnding": self._stream_ending,
        }
        if self._last_audio_at is not None:
            snap["lastAudioAt"] = self._last_audio_at
        if self._playback_started_at is not None:
            snap["playbackStartedAt"] = self._playback_started_at
        return snap


__all__ = ["DEFAULT_TAIL_S", "OutputActivityTracker"]
