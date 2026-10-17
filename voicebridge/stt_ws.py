"""Authenticated streaming STT WebSocket on the one Qubit listener.

Adds **no listener**: it reuses the RFC 6455 framing, the shared single-use
ticket authority (scope ``stt``), and the origin/peer/e-stop checks owned by
:mod:`voicebridge.pty_ws`. Audio is transcribed by the ONE local ASR authority
via :func:`voicebridge.asr.streaming.open_streaming_session` -- there is no
second speech backend and no second session/transcript store.

Protocol (client -> server)
----------------------------
* text ``{"type":"start","session_id":...,"request_id":...,"format":
  "pcm_s16le","sample_rate":16000,"sequence":0}``
* binary ``[uint32 BE sequence][mono s16le PCM]``  (or the equivalent
  ``{"type":"audio","sequence":N,"audio":"<base64>"}`` text frame)
* text ``{"type":"stop","sequence":N}`` / ``{"type":"cancel","sequence":N}``

Protocol (server -> client), every event carrying ``request_id``, ``sequence``,
``engine``, ``timestamp`` (ms), optional ``confidence``, typed ``code``::

    stt.started | stt.partial | stt.final | stt.completed | stt.error

Transcripts are never persisted here: the socket only returns text to the
caller. A final utterance writes one audit record (char count -- never the text)
and nothing else. Raw audio is never logged.
"""
from __future__ import annotations

import json
import struct
import threading
import time
from typing import Any, Dict, Optional

import numpy as np

from . import pty_ws as P
from .logging_setup import log_event

SCOPE_STT = "stt"

MAX_AUDIO_BYTES = 1 << 20          # per-frame cap (1 MiB ~= 32 s @16k mono s16)
MAX_UTTERANCE_S = 120.0            # hard cap on one request's audio
#: Buffered PCM kept for the accurate rescore (2 MiB ~= 64 s @16k mono
#: s16). Past the cap the streaming final stands alone and the rescore is
#: skipped with a reason — bounding server RAM per request.
RESCORE_MAX_BYTES = 1 << 21
IDLE_TIMEOUT_S = 20.0             # no client frames -> close
SUPPORTED_FORMATS = frozenset({"pcm_s16le", "pcm16", "s16le", "pcm"})
MIN_SAMPLE_RATE = 8000
MAX_SAMPLE_RATE = 48000
TARGET_SR = 16000


class SttWsError(Exception):
    """Typed refusal inside an STT session (mapped to an ``stt.error`` event)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


def _encode(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=True, sort_keys=False).encode("utf-8")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _resample_mono(pcm: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Linear resample of a mono float32 signal; identity when rates match."""
    if src_sr == dst_sr or pcm.size == 0:
        return pcm
    n_out = int(round(pcm.size * dst_sr / float(src_sr)))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.linspace(0.0, 1.0, num=pcm.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, pcm).astype(np.float32)


def _s16le_to_float(payload: bytes) -> np.ndarray:
    if len(payload) % 2 != 0:
        raise SttWsError("STT_BAD_AUDIO",
                         "PCM payload length must be an even byte count")
    if not payload:
        raise SttWsError("STT_BAD_AUDIO", "empty audio frame")
    return np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0


class SttWsServer:
    """One authenticated streaming-STT WebSocket authority for a listener."""

    def __init__(self, *, cfg: Any = None, tickets: Any = None,
                 estop: Optional[Any] = None,
                 session_factory: Optional[Any] = None,
                 idle_timeout_s: float = IDLE_TIMEOUT_S) -> None:
        self.cfg = cfg
        self.tickets = tickets
        self._estop = estop
        self._factory = session_factory  # test seam; None -> real factory
        self._idle = float(idle_timeout_s)
        self._lock = threading.Lock()
        self._active = 0

    # -- plumbing --------------------------------------------------------
    def _estop_engaged(self) -> bool:
        if self._estop is not None:
            try:
                return bool(self._estop(self.cfg))
            except Exception:
                return True
        try:
            from .broker import estop_engaged
            return bool(estop_engaged(self.cfg))
        except Exception:
            return True

    def _open_session(self, engine: Optional[str] = None):
        if self._factory is not None:
            return self._factory(engine)
        from .asr.streaming import open_streaming_session
        return open_streaming_session(self.cfg, engine=engine)

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active

    # -- session ---------------------------------------------------------
    def serve(self, handler: Any, *, ticket_value: str, origin_ok: bool,
              peer_ok: bool) -> None:
        if not peer_ok:
            P._reject(handler, 403, "PEER_REFUSED",
                      "STT WebSocket is bound to the approved local interface.")
            return
        if not origin_ok:
            P._reject(handler, 403, "ORIGIN_REFUSED",
                      "STT WebSocket origin does not match the bound host.")
            return
        if self.tickets is None:
            P._reject(handler, 503, "TICKET_STORE_UNAVAILABLE",
                      "No ticket authority is attached to the STT socket.")
            return
        ticket = self.tickets.consume(ticket_value, scope=SCOPE_STT)
        if ticket is None:
            P._reject(handler, 401, "TICKET_REQUIRED",
                      "A short-lived scoped STT ticket is required.")
            return
        if self._estop_engaged():
            P._reject(handler, 423, "ESTOP_ENGAGED",
                      "Qubit emergency stop is engaged; STT socket refused.")
            return
        try:
            P.handshake(handler)
        except P.WsProtocolError as exc:
            P._reject(handler, 400, "WS_HANDSHAKE", str(exc))
            return
        with self._lock:
            self._active += 1
        try:
            self._pump(handler, ticket)
        finally:
            with self._lock:
                self._active -= 1

    def _pump(self, handler: Any, ticket: Any) -> None:
        sock = handler.connection
        sock.settimeout(P.POLL_TIMEOUT)
        st: Dict[str, Any] = {
            "req": None, "session": None, "seq": -1,
            "sr": TARGET_SR, "fmt": "pcm_s16le",
            "started": False, "audio_bytes": 0, "stop_at": None,
            "engine": "local", "pcm": [], "pcm_bytes": 0,
            "pcm_capped": False,
        }
        last_activity = time.monotonic()
        try:
            while True:
                if self._estop_engaged():
                    P.close_frame(sock, 1011, "emergency stop")
                    return
                if time.monotonic() - last_activity > self._idle:
                    self._emit_error(sock, st, "STT_TIMEOUT",
                                     "no audio received before the idle timeout")
                    P.close_frame(sock, 1000, "idle timeout")
                    return
                import select
                # Block (up to 200 ms) rather than spin: a zero timeout here
                # would peg one core per idle connection, which is material on
                # the low-spec target. The timeout also bounds how often the
                # e-stop / idle checks run.
                try:
                    readable, _, _ = select.select([sock], [], [], 0.2)
                except (OSError, ValueError):
                    return
                if not readable:
                    continue
                frame = P.read_frame(sock)
                if frame is None:
                    self._cancel_session(st)
                    P.close_frame(sock, 1000, "session ended")
                    return
                opcode, payload = frame
                if opcode == P._OP_CLOSE:
                    self._cancel_session(st)
                    P.close_frame(sock, 1000, "bye")
                    return
                if opcode == P._OP_PING:
                    P.write_frame(sock, P._OP_PONG, payload)
                    continue
                if opcode == P._OP_PONG:
                    continue
                last_activity = time.monotonic()
                try:
                    if opcode == P._OP_BIN:
                        self._on_binary(sock, st, payload)
                    elif opcode == P._OP_TEXT:
                        self._on_text(sock, st, payload)
                except SttWsError as exc:
                    self._emit_error(sock, st, exc.code, exc.detail)
                    self._cancel_session(st)
                    P.close_frame(sock, 1002, exc.code)
                    return
        finally:
            self._cancel_session(st)

    # -- frame handlers --------------------------------------------------
    def _on_text(self, sock: Any, st: Dict[str, Any], payload: bytes) -> None:
        try:
            msg = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise SttWsError("STT_BAD_REQUEST", "frame is not JSON")
        if not isinstance(msg, dict):
            raise SttWsError("STT_BAD_REQUEST", "frame must be a JSON object")
        kind = str(msg.get("type", "") or "").strip()
        if kind == "start":
            self._on_start(sock, st, msg)
        elif kind == "audio":
            import base64
            raw = msg.get("audio")
            if not isinstance(raw, str):
                raise SttWsError("STT_BAD_AUDIO", "audio frame needs base64 data")
            try:
                data = base64.b64decode(raw, validate=True)
            except Exception:
                raise SttWsError("STT_BAD_AUDIO", "audio frame is not valid base64")
            self._on_audio(sock, st, data, int(msg.get("sequence", -1)))
        elif kind == "stop":
            self._on_stop(sock, st, int(msg.get("sequence", -1)))
        elif kind == "cancel":
            self._on_cancel(sock, st, int(msg.get("sequence", -1)))
        else:
            raise SttWsError("STT_BAD_REQUEST", f"unknown frame type '{kind}'")

    def _on_binary(self, sock: Any, st: Dict[str, Any], payload: bytes) -> None:
        # [uint32 BE sequence][mono s16le PCM]
        if len(payload) < 4:
            raise SttWsError("STT_BAD_AUDIO", "binary frame needs a sequence header")
        seq = struct.unpack("!I", payload[:4])[0]
        self._on_audio(sock, st, payload[4:], int(seq))

    def _on_start(self, sock: Any, st: Dict[str, Any], msg: Dict[str, Any]) -> None:
        if st["started"]:
            raise SttWsError("STT_ALREADY_STARTED",
                             "a request is already active; stop or cancel it first")
        req = str(msg.get("request_id", "") or "").strip()
        if not req:
            raise SttWsError("STT_BAD_REQUEST", "start requires request_id")
        fmt = str(msg.get("format", "pcm_s16le") or "pcm_s16le").strip().lower()
        if fmt not in SUPPORTED_FORMATS:
            raise SttWsError("STT_BAD_REQUEST",
                             f"unsupported audio format '{fmt}' (use pcm_s16le)")
        try:
            sr = int(msg.get("sample_rate", TARGET_SR))
        except (TypeError, ValueError):
            raise SttWsError("STT_BAD_REQUEST", "sample_rate must be an integer")
        if not (MIN_SAMPLE_RATE <= sr <= MAX_SAMPLE_RATE):
            raise SttWsError("STT_SAMPLE_RATE",
                             f"sample_rate {sr} outside {MIN_SAMPLE_RATE}-{MAX_SAMPLE_RATE}")
        engine = str(msg.get("engine", "") or "").strip() or None
        # Record the request identity BEFORE opening the session so a typed
        # refusal is emitted exactly once (by the pump) with the right
        # request_id, then closes the socket.
        st.update(req=req, engine=engine or "local")
        try:
            session = self._open_session(engine)
        except Exception as exc:  # typed refusal, never a fake transcript
            code = getattr(exc, "code", None) or "STT_UNAVAILABLE"
            raise SttWsError(
                str(code), str(getattr(exc, "detail", "") or exc)[:200])
        st.update(session=session, seq=-1, sr=sr, fmt=fmt,
                  started=True, audio_bytes=0, stop_at=None,
                  engine=str(getattr(session, "engine", "local")))
        self._send(sock, st, "stt.started", sequence=0,
                   sample_rate=TARGET_SR, format="pcm_s16le")

    def _on_audio(self, sock: Any, st: Dict[str, Any], payload: bytes,
                  sequence: int) -> None:
        if not st["started"] or st["session"] is None:
            raise SttWsError("STT_START_REQUIRED", "send a start frame first")
        if sequence >= 0:
            if sequence <= st["seq"]:
                # Stale/duplicate frame (retransmit or an out-of-order arrival
                # from a superseded request) -- drop it, never double-feed.
                return
            st["seq"] = sequence
        nbytes = st["audio_bytes"] + len(payload)
        if len(payload) > MAX_AUDIO_BYTES:
            raise SttWsError("STT_FRAME_TOO_LARGE", "audio frame exceeds the cap")
        if nbytes > MAX_AUDIO_BYTES * 4:  # ~120 s of 16-bit audio
            raise SttWsError("STT_TOO_LONG", "utterance exceeds the duration cap")
        st["audio_bytes"] = nbytes
        # Buffer raw frames for the accurate rescore (bounded; the streaming
        # session still gets every frame either way).
        if not st.get("pcm_capped"):
            if st.get("pcm_bytes", 0) + len(payload) > RESCORE_MAX_BYTES:
                st["pcm_capped"] = True
            else:
                st.setdefault("pcm", []).append(bytes(payload))
                st["pcm_bytes"] = st.get("pcm_bytes", 0) + len(payload)
        pcm = _s16le_to_float(payload)
        pcm = _resample_mono(pcm, int(st["sr"]), TARGET_SR)
        result = st["session"].accept(pcm)
        if result is not None and not result.empty:
            self._send(sock, st, "stt.partial", sequence=sequence,
                       text=result.text, confidence=result.confidence,
                       segment=result.segment, replace=result.replace)

    def _accurate_rescore(self, st: Dict[str, Any]) -> Dict[str, str]:
        """Whole-utterance faster-whisper rescore of the buffered audio.

        Returns ``{"text": ..., "engine": ..., "reason": ...}``: ``text`` is
        "" when the rescore is disabled, unbuffered, capped, or failed — the
        caller then keeps the streaming final. Mirrors the desktop helper's
        two-stage design (streaming partials live, accurate final wins), so
        the browser local-STT path gets the same words + punctuation the
        Right-Ctrl path already enjoys. Never raises.
        """
        try:
            mode = str((self.cfg.get("voice_edit", "stt_mode", default="accurate")
                        if self.cfg is not None else "accurate") or "accurate"
                       ).strip().lower()
        except Exception:  # noqa: BLE001 - config-less read keeps rescore on
            mode = "accurate"
        if mode != "accurate":
            return {"text": "", "engine": "", "reason": "stt_mode=" + mode}
        chunks = st.get("pcm") or []
        if not chunks:
            if st.get("pcm_capped"):
                return {"text": "", "engine": "",
                        "reason": "utterance past rescore buffer cap"}
            return {"text": "", "engine": "", "reason": "no buffered audio"}
        try:
            from .voice_edit.stt_client import transcribe_accurate
        except Exception as exc:  # noqa: BLE001 - no rescore path available
            return {"text": "", "engine": "",
                    "reason": f"accurate unavailable: {type(exc).__name__}"}
        try:
            text, reason = transcribe_accurate(
                self.cfg, b"".join(chunks),
                sample_rate=int(st.get("sr") or TARGET_SR))
        except Exception as exc:  # noqa: BLE001 - streaming final stands
            return {"text": "", "engine": "",
                    "reason": f"accurate failed: {type(exc).__name__}"}
        text = str(text or "").strip()
        if not text:
            return {"text": "", "engine": "",
                    "reason": reason or "accurate empty"}
        return {"text": text, "engine": "accurate", "reason": ""}

    def _on_stop(self, sock: Any, st: Dict[str, Any], sequence: int) -> None:
        if not st["started"] or st["session"] is None:
            raise SttWsError("STT_START_REQUIRED", "no active request to stop")
        if sequence >= 0:
            st["seq"] = max(st["seq"], sequence)
        st["stop_at"] = time.monotonic()
        session = st["session"]
        try:
            final = session.finalize(st["stop_at"])
        finally:
            session.close()
        final_text = str(final.text).strip() if final is not None else ""
        final_conf = float(final.confidence) if final is not None else 0.0
        final_seg = int(final.segment) if final is not None else 0
        winner = st.get("engine") or "streaming"
        rescore_reason = ""
        # The accurate rescore refines (or rescues, when streaming came back
        # empty) the streaming final: words + punctuation from faster-whisper
        # instead of the raw transducer hypothesis. A failed rescore keeps
        # the streaming text.
        res = self._accurate_rescore(st)
        if res["text"]:
            final_text, winner = res["text"], "accurate"
        else:
            rescore_reason = res["reason"]
        if final_text:
            st["engine"] = winner
            self._send(sock, st, "stt.final", sequence=sequence,
                       text=final_text, confidence=final_conf,
                       segment=final_seg)
            # One audit record -- char count only, never the transcript text.
            log_event("stt.final", engine=st["engine"], request_id=st["req"],
                      chars=len(final_text),
                      audio_ms=session.metrics.as_dict(st["stop_at"]).get("audio_ms"),
                      rescore=winner, rescore_reason=rescore_reason or None)
        metrics = session.metrics.as_dict(st["stop_at"])
        extra: Dict[str, Any] = {}
        if not final_text and rescore_reason:
            # Silent completion, explained: the socket yielded no transcript
            # because nothing was buffered (the client sent no audio) or the
            # rescore failed. Carried on the event (not just the journal) so
            # the UI can say WHICH instead of going quiet.
            extra["warning_code"] = "STT_NO_TRANSCRIPT"
            extra["warning"] = rescore_reason
        self._send(sock, st, "stt.completed", sequence=sequence,
                   final_text=final_text,
                   cancelled=False, metrics=metrics, **extra)
        self._reset_request(st)

    def _on_cancel(self, sock: Any, st: Dict[str, Any], sequence: int) -> None:
        had = st["started"]
        self._cancel_session(st)
        self._send(sock, {**st, "started": had}, "stt.completed",
                   sequence=sequence, final_text="", cancelled=True,
                   metrics={})
        self._reset_request(st)

    # -- helpers ---------------------------------------------------------
    def _cancel_session(self, st: Dict[str, Any]) -> None:
        session = st.get("session")
        if session is not None:
            try:
                session.cancel()
                session.close()
            except Exception:
                pass
        if st.get("started"):
            log_event("stt.cancelled", engine=st.get("engine", "local"),
                      request_id=st.get("req") or "")

    @staticmethod
    def _reset_request(st: Dict[str, Any]) -> None:
        st.update(req=None, session=None, seq=-1, started=False,
                  audio_bytes=0, stop_at=None, pcm=[], pcm_bytes=0,
                  pcm_capped=False)

    def _emit_error(self, sock: Any, st: Dict[str, Any], code: str,
                    detail: str) -> None:
        self._send(sock, st, "stt.error", sequence=st.get("seq", -1) or 0,
                   code=code, detail=str(detail)[:200])

    def _send(self, sock: Any, st: Dict[str, Any], event: str, *,
              sequence: int = 0, **fields: Any) -> None:
        payload: Dict[str, Any] = {
            "type": event,
            "request_id": st.get("req") or "",
            "sequence": int(sequence) if sequence and sequence >= 0 else 0,
            "engine": st.get("engine") or "local",
            "timestamp": _now_ms(),
        }
        for k, v in fields.items():
            if v is not None:
                payload[k] = v
        P.write_frame(sock, P._OP_TEXT, _encode(payload))


__all__ = ["SttWsServer", "SCOPE_STT", "SttWsError"]
