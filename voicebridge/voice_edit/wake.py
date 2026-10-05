"""Hands-free wake command ("Hey Qubit, ...").

Uses the ONE existing STT authority: a cheap PulseAudio capture + a local
energy VAD detects a spoken segment, then that segment is transcribed through
the existing authenticated STT WebSocket (the same one Right-Ctrl uses). There
is no second speech backend, no second session store, and no always-on cloud
egress.

Privacy/egress: only detected speech segments are transcribed, locally; a
non-wake transcript is discarded immediately and never persisted. The monitor
pauses whenever a Right-Ctrl session is active so the two never both own the
microphone input.

KWS misses get a bounded ASR-gated fallback probe: the spotter is deaf to some
voices at every threshold while local ASR hears them fine, so a short segment
with no keyword hit is transcribed once and matched against the wake aliases.
Long segments are skipped (the spotter-hit path still covers them when it
fires). Disable with ``wake.asr_fallback: false``.

Protocol:
* the wake phrase is stripped from the transcript; the remainder is the command
  ("Hey Qubit, type this for me: X" -> "type this for me: X");
* if the speaker paused after the wake phrase, the NEXT utterance is taken as
  the command (a short armed window);
* a wake with no command is ignored (no accidental typing).
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from . import debug as _dbg
from .sound import beep


def _trace(stage: str, **fields: Any) -> None:
    try:
        from ..logging_setup import log_event
        log_event(f"voice_edit.wake.{stage}", **fields)
    except Exception:
        pass


DEFAULT_PHRASE = "hey qubit"
# Common ASR renderings of the wake word ("hey" is often dropped, "qubit" is
# heard as "cubit"/"qbit").
WAKE_ALIASES = (
    "hey qubit", "hi qubit", "ok qubit", "okay qubit", "hey cubit",
    "hey qbit", "hey qubits", "a qubit", "a cubit", "eh qubit",
    # Common ASR renderings of "Qubit" (faster-whisper heard "Hey Cupid"):
    "hey cupid", "hey cubid", "hey cue bit", "hey cube it", "hey quip it",
    "hi cupid", "ok cupid", "okay cupid", "a cupid", "hey cutie",
)
# Bare wake word at the very start (only when ASR dropped "hey"). Deliberately
# anchored to the first token so "the qubit is fast" never triggers it.
_BARE_WAKE_RE = re.compile(
    r"^\s*(?:qubit|cubit|qbit|cupid|cubid|cue bit|cube it)\b", re.IGNORECASE)
# Leading filler allowed before an alias ("ok, hey qubit ...").
_FILLER_RE = r"^\s*(?:(?:ok|okay|so|um|uh|eh|and|hi|hey)[\s,]+)*"


def strip_wake(text: str, aliases: Optional[List[str]] = None) -> Optional[str]:
    """Return the command text after a wake phrase, or None if not present.

    Matching is anchored to the START of the utterance (wake words are spoken
    first), so a sentence that merely mentions "qubit" never triggers it.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    low = cleaned.lower()
    for phrase in (aliases or list(WAKE_ALIASES)):
        m = re.match(_FILLER_RE + re.escape(phrase.lower()) + r"\b", low)
        if m:
            return cleaned[m.end():].lstrip(" \t,.:;!?—-")
    m = _BARE_WAKE_RE.match(low)
    if m:
        return cleaned[m.end():].lstrip(" \t,.:;!?—-")
    return None


#: Alias first words that carry real directed-speech evidence. A fallback
#: transcript matching one of these ("hey/hi/ok qubit, ...") executes at
#: once; anything weaker ("Qubit, ...", "a cubit, ...") only arms the
#: window (false-fire tightening 2026-10-02: room chatter starting with a
#: bare "Qubit," executed as a command several times a day).
_FULL_WAKE_FIRST_WORDS = frozenset({"hey", "hi", "ok", "okay"})


def wake_match_kind(text: str,
                    aliases: Optional[List[str]] = None) -> Optional[str]:
    """Classify a wake-phrase match as ``"full"`` or ``"bare"``.

    ``"full"`` = a hey/hi/ok-prefixed alias matched at the utterance start
    (directed speech); ``"bare"`` = only the wake word itself matched (ASR
    dropped "hey", or a weak "a cubit" rendering); ``None`` = no wake
    phrase (same contract as :func:`strip_wake`). The KWS-hit and
    armed-window paths never consult this — only the unarmed ASR-fallback
    probe, where weak evidence must arm rather than execute.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    low = cleaned.lower()
    for phrase in (aliases or list(WAKE_ALIASES)):
        m = re.match(_FILLER_RE + re.escape(phrase.lower()) + r"\b", low)
        if m:
            first = phrase.lower().split(" ", 1)[0]
            return "full" if first in _FULL_WAKE_FIRST_WORDS else "bare"
    if _BARE_WAKE_RE.match(low):
        return "bare"
    return None


# ---------------------------------------------------------------------------
# Lane 1 (2026-10-03): bare-confirm bypass predicates.
#
# A bare "yes" answering a staged confirmation used to die on the wake-word /
# arm gate when the arm window (12 s) expired while the owner listened to the
# prompt — even with a live unexpired confirmation (120 s TTL). The open
# confirmation IS the consent context, so the loop checks these BEFORE the
# wake-word/arm discard. No third vocabulary is invented: every predicate
# below delegates to the existing consumer that will settle the turn —
# ``desktop_helper`` (helper in-process pending, e.g. the timer) and
# ``chat_transport`` (CT registry/task journal pending) for affirmatives,
# plus the shared intent ``classifier`` CANCEL patterns for negatives.
# ---------------------------------------------------------------------------
def _normalize_confirm(text: str) -> str:
    """Alphanumeric core of a confirmation utterance (helper convention).

    Whisper emits conversational punctuation ("Yes, please.") while verbatim
    mode strips it — match on the core either way, exactly like
    ``desktop_helper._affirmative`` / ``_negative``.
    """
    low = (text or "").strip().lower().strip(" .,!?:;")
    return re.sub(r"[^a-z0-9 ]", "", low)


def _helper_says_affirmative(text: str) -> bool:
    """True when the helper's existing pending branch would settle this."""
    try:
        from ..desktop_helper import DesktopHelper as _H
        return bool(_H._affirmative(text))
    except Exception:
        return False


def _ct_says_affirmative(text: str) -> bool:
    """True when the chat-transport T4/T5 gate would settle this."""
    try:
        from .. import chat_transport as _CT
        return bool(_CT._is_affirmative(text))
    except Exception:
        return False


def is_bare_affirmative(text: Any) -> bool:
    """True when a transcript IS a bare affirmation (nothing else).

    Union of the two existing consumers (helper pending branch +
    chat-transport T4/T5 settle gate) — a longer message that merely
    contains "yes" never qualifies.
    """
    try:
        t = str(text or "")
    except Exception:
        return False
    if not t.strip():
        return False
    return _helper_says_affirmative(t) or _ct_says_affirmative(t)


def _helper_says_negative(text: str) -> bool:
    """True when the helper's existing pending branch would cancel this."""
    try:
        from ..desktop_helper import DesktopHelper as _H
        return bool(_H._negative(text))
    except Exception:
        return False


def _classifier_says_cancel(text: str) -> bool:
    """True when the shared intent classifier reads this as a cancel."""
    try:
        from ..intent.classifier import CANCEL_PATTERNS
        return any(re.match(p, text or "") for p in CANCEL_PATTERNS)
    except Exception:
        return False


def is_bare_negative(text: Any) -> bool:
    """True when a transcript IS a bare cancellation (nothing else)."""
    try:
        t = str(text or "")
    except Exception:
        return False
    if not t.strip():
        return False
    return _helper_says_negative(t) or _classifier_says_cancel(t)


def _voice_session_id(cfg: Any) -> str:
    """The stable spoken-conversation session (same as the service).

    Mirrors ``VoiceEditService._voice_session``: the dedicated voice-chat
    session on the one canonical store. Never raises.
    """
    try:
        from ..agent import resolve_session_id
        return str(resolve_session_id(cfg, "voice-chat") or "voice-chat")
    except Exception:
        return "voice-chat"


def _live_voice_pending(cfg: Any) -> str:
    """Live CT confirmation kind for the voice session: registry/task/"".

    Read-only over the shared durable journal (the same ``_pending_registry``
    / ``_pending_task`` the turn T4/T5 settle path consults, in the same
    order). Expired records are already filtered by the journal TTL, so an
    expired "yes" degrades to no-pending. Never raises: an unreadable
    journal degrades to "" and the turn falls through to today's behavior.
    """
    try:
        from .. import chat_transport as _CT
        sid = _voice_session_id(cfg)
        try:
            if _CT._pending_registry(cfg, sid):
                return "registry"
        except Exception:
            pass
        try:
            if _CT._pending_task(cfg, sid):
                return "task"
        except Exception:
            pass
    except Exception:
        pass
    return ""


def _settle_voice_pending(cfg: Any, kind: str) -> Dict[str, Any]:
    """Settle the voice session's live CT confirmation once (call, not copy).

    Delegates to the existing ``_settle_affirmed`` (registry) /
    ``_settle_task_affirmed`` (task) — the same settle-once journal path the
    turn and Slice-1b settle through, so a second "yes" finds nothing. Never
    raises: failure maps to a typed dict, never an execution.
    """
    try:
        from .. import chat_transport as _CT
        st = _CT.store(cfg)
        sid = _voice_session_id(cfg)
        if kind == "registry":
            return dict(_CT._settle_affirmed(cfg, st, sid) or {})
        return dict(_CT._settle_task_affirmed(cfg, st, sid) or {})
    except Exception as exc:
        return {"ok": False, "state": "FAILED", "code": "SETTLE_UNAVAILABLE",
                "detail": str(exc)[:160]}


def _deny_voice_pending(cfg: Any, kind: str) -> bool:
    """Cancel the voice session's live CT confirmation (call, not copy).

    Registry → the existing ``voice_commands.cancel_pending``; task → the
    existing journal ``respond(id, "deny")`` on the latest record (the same
    op the settle path uses for cleanup). Returns True when a record was
    denied. Never raises.
    """
    try:
        from .. import chat_transport as _CT
        sid = _voice_session_id(cfg)
        if kind == "registry":
            from .. import voice_commands as VC
            ctx = VC.make_ctx(cfg, actor="voice:user", session=sid,
                              source="user")
            out = VC.cancel_pending(ctx)
            return str(getattr(out, "kind", "") or "") == "executed"
        journal = _CT._task_journal(cfg)
        if journal is None:
            return False
        pending = _CT._pending_task(cfg, sid)
        if not pending:
            return False
        cid = str(getattr(pending[-1], "id", "") or "")
        if not cid:
            return False
        try:
            journal.respond(cid, "deny")
        except Exception:
            return False
        _trace("confirm_cancelled", via="task", session=sid)
        return True
    except Exception:
        return False


def _normalize_echo(text: str) -> str:
    return re.sub(r"\s+", " ",
                  re.sub(r"[^\w\s]", "", (text or "").lower())).strip()


def _word_tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _wake_vocab() -> frozenset:
    """Every token the wake matcher itself can produce.

    Built from the alias table + bare forms + the leading-filler words so
    the echo rule below stays in sync with ``strip_wake`` by construction
    instead of a second hardcoded list.
    """
    toks: set = set()
    for phrase in WAKE_ALIASES:
        toks.update(_word_tokens(phrase))
    toks.update(["qubit", "cubit", "qbit", "cupid", "cubid", "cue", "bit",
                 "cube", "it", "hey", "hi", "ok", "okay", "so", "um", "uh",
                 "eh", "and", "a"])
    return frozenset(toks)


_WAKE_VOCAB = _wake_vocab()


#: Cache for the runtime initial-prompt read: the guard runs per
#: segment, but the file only changes on config writes. Keyed by path +
#: mtime; a loud trace fires if the file ever goes unreadable (the guard
#: would otherwise fail closed to OFF with no signal — the exact shape of
#: the §66 false-fire saga).
_RUNTIME_PROMPT_CACHE: Dict[str, Any] = {"path": "", "mtime": -1.0,
                                         "prompt": ""}


def _runtime_initial_prompt() -> str:
    """The live whisper ``stt.local.initial_prompt`` from the runtime home.

    The priming prompt lives in the RUNTIME ``config.yaml``
    (``stt.local.initial_prompt``), not in ``voice.yaml`` — the guard used
    to read only the voice tree, so it never fired live (0 skips in 11,247
    probes while the exact prompt parrot fired 100+ times). Fail-closed:
    unreadable file / missing key / no YAML -> "" (the guard degrades to
    the voice-tree prompt). Read-only: nothing here is ever written.
    Cached by file mtime (the guard runs per segment); an unreadable file
    traces loudly instead of silently disabling the guard.
    """
    try:
        home = os.environ.get("QUBIT_HOME", "") or ""
        if not home:
            try:
                from ..qubit_runtime import default_home
                home = str(default_home())
            except Exception:
                home = ""
        if not home:
            return ""
        path = os.path.join(home, "config.yaml")
        try:
            mtime = os.path.getmtime(path)
        except OSError as exc:
            _trace("prompt_unreadable", detail=str(exc)[:120])
            return ""
        cached = _RUNTIME_PROMPT_CACHE
        if cached.get("path") == path and cached.get("mtime") == mtime:
            return str(cached.get("prompt") or "")
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        node = doc.get("stt", {})
        local = node.get("local", {}) if isinstance(node, dict) else {}
        prompt = local.get("initial_prompt", "") if isinstance(local, dict) else ""
        prompt = str(prompt or "")
        cached["path"] = path
        cached["mtime"] = mtime
        cached["prompt"] = prompt
        return prompt
    except Exception as exc:
        _trace("prompt_unreadable", detail=str(exc)[:120])
        return ""


def is_prompt_echo(cfg: Any, text: str) -> bool:
    """True when a transcript merely parrots the whisper initial_prompt.

    faster-whisper is primed with ``stt.local.initial_prompt`` as vocabulary
    bias; on room noise it hallucinates that exact string back (observed live
    100+: "Qubit, hey Qubit, voice assistant." and trimmed variants like
    "hey Qubit, voice assistant."). The echo contains "hey Qubit", so without
    this guard it false-fires the wake path (beep + spoken greeting) on
    silence. A real utterance always carries words beyond the wake + prompt
    vocabulary, so the rule is: exact match OR (every token is a wake/prompt
    token AND at least one prompt-filler token is present). A bare "hey
    qubit" has no filler, so pause-then-command arming is unaffected.
    """
    try:
        prompt = str(_cfg(cfg, "stt", "local", "initial_prompt",
                           default="") or "")
    except Exception:
        prompt = ""
    if not prompt.strip():
        # The priming prompt does not live in the voice tree — consult the
        # runtime home (the same file the transcriber reads).
        prompt = _runtime_initial_prompt()
    if not prompt.strip() or not (text or "").strip():
        return False
    if _normalize_echo(text) == _normalize_echo(prompt):
        return True
    toks = _word_tokens(text)
    if not toks:
        return False
    prompt_toks = _word_tokens(prompt)
    filler = prompt_toks - _WAKE_VOCAB
    return toks <= (_WAKE_VOCAB | prompt_toks) and bool(toks & filler)


class EnergyEndpointer:
    """Very cheap RMS endpointer (no model load; one pass per frame)."""

    def __init__(self, *, frame_ms: int = 300, speech_ms: int = 300,
                 silence_ms: int = 900, max_s: float = 25.0,
                 rms_gate: float = 0.0018, pre_roll_frames: int = 1) -> None:
        self._speech_frames = max(1, int(speech_ms / frame_ms))
        self._silence_frames = max(1, int(silence_ms / frame_ms))
        self._max_frames = max(1, int(max_s * 1000 / frame_ms))
        self._rms_gate = float(rms_gate)
        self.pre_roll_frames = max(0, int(pre_roll_frames))
        self.reset()

    def reset(self) -> None:
        self.state = "idle"
        self.voiced = 0
        self.silent = 0
        self.total = 0

    def add_frame(self, pcm: bytes) -> str:
        self.total += 1
        try:
            a = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
            rms = float(np.sqrt((a ** 2).mean()) / 32768.0) if a.size else 0.0
        except Exception:
            rms = 0.0
        is_voiced = rms >= self._rms_gate
        if self.state == "idle":
            self.voiced = self.voiced + 1 if is_voiced else 0
            if self.voiced >= self._speech_frames:
                self.state = "speech"
                self.silent = 0
            return self.state
        if self.state == "speech":
            self.silent = 0 if is_voiced else self.silent + 1
            if self.silent >= self._silence_frames or self.total >= self._max_frames:
                self.state = "done"
            return self.state
        return self.state


class BufferMic:
    """Finite mic over an in-memory PCM buffer.

    ``on_exhausted`` fires as soon as the last frame is handed out so the STT
    sender finalizes immediately instead of idling.
    """

    def __init__(self, pcm: bytes, *, frame_bytes: int = 9600,
                 on_exhausted: Optional[Callable[[], None]] = None) -> None:
        self._frames: List[bytes] = [pcm[i:i + frame_bytes]
                                     for i in range(0, len(pcm), frame_bytes)]
        self._i = 0
        self._on_exhausted = on_exhausted

    def start(self) -> None:
        pass

    def read_frame(self, timeout: float = 0.2) -> Optional[bytes]:
        if self._i >= len(self._frames):
            if self._on_exhausted:
                self._on_exhausted()
            return None
        frame = self._frames[self._i]
        self._i += 1
        if self._i >= len(self._frames) and self._on_exhausted:
            self._on_exhausted()
        return frame

    def stop(self) -> None:
        pass


#: Segmentation backends for the wake loop. ``auto`` tries the neural
#: VAD chain (silero, webrtc fallback inside the audio module) and degrades
#: to the legacy energy endpointer when neural segmentation is unavailable —
#: the wake listener must never die because a model file is missing.
VAD_MODES = ("auto", "silero", "webrtc", "energy")


def _wake_vad_mode(cfg: Any) -> str:
    try:
        mode = str(_cfg(cfg, "voice_edit", "wake", "vad",
                        default="auto") or "auto").lower()
    except Exception:
        return "auto"
    return mode if mode in VAD_MODES else "auto"


class _AudioVadSegmenter:
    """Neural-VAD endpointer behind the wake loop's 300 ms frame contract.

    The audio endpointer works on 30 ms subframes; the wake mic delivers
    300 ms frames, so each frame is split (with a small carry buffer for
    odd sizes) and the endpointer state after the last subframe is the
    segment state. Buffering semantics match ``EnergyEndpointer``: the
    caller appends whole frames while the state is ``speech``/``done``.
    """

    _SUBFRAME_BYTES = 960  # 30 ms @ 16 kHz mono s16

    def __init__(self, audio_endpointer: Any, backend: str) -> None:
        self._ep = audio_endpointer
        self.backend = backend
        self._tail = b""

    def reset(self) -> None:
        self._tail = b""
        self._ep.reset()

    def add_frame(self, pcm: bytes) -> str:
        data = self._tail + (pcm or b"")
        step = self._SUBFRAME_BYTES
        n_full = len(data) // step
        for i in range(n_full):
            self._ep.add_frame(data[i * step:(i + 1) * step])
        self._tail = data[n_full * step:]
        return self._ep.state


def _build_audio_segmenter(cfg: Any) -> _AudioVadSegmenter:
    """Build the neural-VAD segmenter from the live wake config.

    Provenance: no upstream code is copied and no model is downloaded —
    this drives the in-repo ``voicebridge.audio.vad`` endpointer (Silero
    via the already-installed ``sherpa_onnx``, ``webrtcvad`` fallback) over
    the weights file already on disk
    (``models/silero_vad_v5.onnx``, arrived via the existing
    faster-whisper dependency chain). Raises when neural segmentation is
    unavailable so the caller can degrade to the energy endpointer.
    """
    from ..audio.vad import Endpointer as _AudioEndpointer
    mode = _wake_vad_mode(cfg)
    backend = mode if mode in ("silero", "webrtc") else "silero"
    ep = _AudioEndpointer(
        speech_start_ms=300,
        silence_timeout_s=float(_cfg(cfg, "voice_edit", "wake", "silence_ms",
                                     default=900) or 900) / 1000.0,
        max_utterance_s=_wake_max_s(cfg),
        rms_gate=float(_cfg(cfg, "voice_edit", "wake", "rms_gate",
                            default=0.0018) or 0.0018),
        vad_backend=backend,
    )
    return _AudioVadSegmenter(ep, backend=backend)


def _make_segmenter(cfg: Any,
                    segmenter_factory: Optional[Callable[[Any], Any]] = None) -> Any:
    """Return the wake loop's segmenter: injected factory, neural VAD, energy.

    ``segmenter_factory`` is the test seam (same pattern as the monitor's
    ``mic_factory``/``transcribe``/``kws`` injections). Otherwise ``auto``
    prefers neural segmentation and degrades to ``EnergyEndpointer``;
    explicit ``energy`` skips neural entirely.
    """
    if segmenter_factory is not None:
        return segmenter_factory(cfg)
    if _wake_vad_mode(cfg) == "energy":
        return EnergyEndpointer(
            silence_ms=int(_cfg(cfg, "voice_edit", "wake", "silence_ms",
                                default=900) or 900),
            max_s=_wake_max_s(cfg),
            rms_gate=float(_cfg(cfg, "voice_edit", "wake", "rms_gate",
                                default=0.0018) or 0.0018))
    try:
        return _build_audio_segmenter(cfg)
    except Exception as exc:
        _trace("vad.unavailable", detail=str(exc)[:160], fallback="energy")
        return EnergyEndpointer(
            silence_ms=int(_cfg(cfg, "voice_edit", "wake", "silence_ms",
                                default=900) or 900),
            max_s=_wake_max_s(cfg),
            rms_gate=float(_cfg(cfg, "voice_edit", "wake", "rms_gate",
                                default=0.0018) or 0.0018))


def _segment_capped(cfg: Any, nbytes: int) -> bool:
    """True when a segment reaches the wake cap (its tail may be cut).

    The segmenter completes a continuously-voiced room at ``wake.max_s``
    (default 15.0 s of 16 kHz mono s16); anything at/near that length was
    ended by the cap, not by trailing silence, so a long command's tail
    is missing while its head (the route word) is intact. One-frame
    (300 ms) tolerance: a capped segment carries pre-roll + the done
    frame on top of the cap. Never raises.
    """
    try:
        cap = int(_wake_max_s(cfg) * 16000 * 2)
    except Exception:
        cap = int(15.0 * 16000 * 2)
    try:
        return int(nbytes) >= cap - 9600
    except Exception:
        return False


#: Minimum seconds between two discard hints on the status line: the
#: first hint teaches the protocol ("say hey qubit first"); repeats
#: inside the window would strobe the line on every chatter blob.
_HINT_COOLDOWN_S = 30.0


def _is_command_shaped(cfg: Any, text: str) -> bool:
    """True when unarmed non-wake text looks like a command worth a hint.

    2026-10-03 (silent-discard fix): every predicate below is REUSED from
    the existing turn pipeline — no new matching is invented:

    * ``chat_transport.parse_route_word`` — the ``in chat:`` / ``quick:``
      prefixes ``ask()`` honors (``voicebridge/chat_transport.py``);
    * ``VoiceEditService.is_sleep_command`` / ``is_briefing_request`` /
      ``is_digest_request`` — the sleep/brief/digest triggers ``ask()``
      checks (``voice_edit/service.py``);
    * ``voice_edit.parser.parse`` claimed as a non-dictation action —
      the transform triggers incl. the loose ``redo <anything> for me``
      fallback (``voice_edit/parser.py`` + ``voice_edit/schema.ACTIONS``,
      the same claim ``_transform_pre_route`` makes);
    * ``voice_edit.tool_task.propose_tool`` resolving a ``local.*`` tool —
      the timer/open/tab/document shapes the confirmed task path runs;
    * ``voice_edit.send.parse_reply`` — the reply-draft chain's own parser;
    * the weather/time shapes ``_direct_fact_answer`` answers verbatim
      (same regexes, ``voice_edit/service.py``).

    Pure: regex/resolver matching over config only, no subprocess, no
    network, no STT. Never raises (an unreadable predicate degrades to
    False — chatter silence, never a hint).
    """
    t = (text or "").strip()
    if not t:
        return False
    try:
        from ..chat_transport import parse_route_word as _parse_rw
        if _parse_rw(t) is not None:
            return True
    except Exception:
        pass
    try:
        from .service import VoiceEditService as _Svc
        if (_Svc.is_sleep_command(t) or _Svc.is_briefing_request(t)
                or _Svc.is_digest_request(t)):
            return True
    except Exception:
        pass
    try:
        from .parser import parse as _parse_t
        from .schema import ACTIONS as _ACTIONS
        _cmd = _parse_t(t, selected_text="")
        _action = str(getattr(_cmd, "action", "") or "")
        if (not getattr(_cmd, "needs_clarification", True)
                and _action and _action != "dictate_text"
                and _action in _ACTIONS):
            return True
    except Exception:
        pass
    try:
        from .tool_task import propose_tool as _propose
        _proposal = _propose(cfg, t)
        if _proposal and str(_proposal.get("tool") or "").startswith("local."):
            return True
    except Exception:
        pass
    try:
        from .send import parse_reply as _parse_reply
        if _parse_reply(t) is not None:
            return True
    except Exception:
        pass
    try:
        if re.search(r"\bweather\b|\bforecast\b|\btemperature\b|\btemp\b",
                     t, re.IGNORECASE):
            return True
        if re.search(r"\bwhat time\b|\bcurrent time\b|\btime is it\b"
                     r"|\bwhat'?s the time\b|\btell me the time\b",
                     t, re.IGNORECASE):
            return True
        if re.search(r"\bwhat day is it\b|\bwhat date\b"
                     r"|\bwhat'?s (the |today'?s )?(date|day|today)\b"
                     r"|\bwhat is today\b|\bwhat'?s today\b",
                     t, re.IGNORECASE):
            return True
    except Exception:
        pass
    return False


class WakeMonitor:
    """Always-listening wake detector feeding the existing STT authority."""

    def __init__(self, cfg: Any, *, on_command: Callable[[str], None],
                 is_busy: Callable[[], bool],
                 mic_factory: Optional[Callable[[], Any]] = None,
                 transcribe: Optional[Callable[[bytes], str]] = None,
                 kws: Optional[Any] = None,
                 phrase: str = "",
                 arm_window_s: float = 6.0,
                 segmenter_factory: Optional[Callable[[Any], Any]] = None,
                 pending_probe: Optional[Callable[[], bool]] = None,
                 on_hint: Optional[Callable[[str], None]] = None) -> None:
        self.cfg = cfg
        self.on_command = on_command
        self.is_busy = is_busy
        self.phrase = phrase or str(_cfg(cfg, "voice_edit", "wake", "phrase",
                                         default=DEFAULT_PHRASE) or DEFAULT_PHRASE)
        self.arm_window_s = float(_cfg(cfg, "voice_edit", "wake", "arm_window_s",
                                       default=arm_window_s) or arm_window_s)
        self._mic_factory = mic_factory
        self._transcribe = transcribe
        self._segmenter_factory = segmenter_factory
        # Lane 1 (2026-10-03): read-only probe for a live unexpired
        # helper-staged confirmation (service.task-mode staging, e.g. the
        # timer — in-process state the journal cannot see). The helper wires
        # it; when absent the bypass still covers CT journal pending below.
        # Never raises (a broken probe degrades to no-pending, never a
        # settle), never writes.
        self._pending_probe = pending_probe
        # 2026-10-03 (silent-discard fix): optional visible-hint sink for
        # unarmed command-shaped discards. The helper wires this to its
        # existing heard-preview path (``_safe_show`` + ``_ui_status("heard",
        # ...)`` — the same surface §67 used for wake heard words), so the
        # monitor never builds UI of its own. None (default) keeps today's
        # journal-trace-only behavior; never raises when absent or faulty.
        self._on_hint = on_hint
        # Monotonic timestamp of the last posted hint: at most one hint per
        # _HINT_COOLDOWN_S so room chatter can never strobe the status line.
        self._last_hint_at = 0.0
        # Wake engine: a dedicated keyword spotter when available (default),
        # else the ASR-phrase fallback. The spotter never transcribes; ASR runs
        # for the utterance that triggered the keyword, plus one bounded
        # fallback probe per short KWS-miss segment (see _asr_fallback_allows).
        self.engine = str(_cfg(cfg, "voice_edit", "wake", "engine",
                               default="auto") or "auto").lower()
        self._kws = kws
        if self._kws is None and self.engine in ("auto", "kws"):
            try:
                from .kws import KwsDetector, find_model_dir
                model_dir = find_model_dir(cfg)
                if model_dir is not None:
                    self._kws = KwsDetector(
                        model_dir,
                        threshold=float(_cfg(cfg, "voice_edit", "wake",
                                             "kws_threshold", default=0.25) or
                                        0.25),
                        score=float(_cfg(cfg, "voice_edit", "wake",
                                         "kws_score", default=1.0) or 1.0))
            except Exception as exc:
                self.last_error = f"kws_init: {exc}"[:160]
                self._kws = None
        if self._kws is not None:
            self.engine = "kws"
        elif self.engine == "auto":
            self.engine = "asr"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._armed_until = 0.0
        self._lock = threading.Lock()
        self.last_error = ""

    # -- lifecycle --------------------------------------------------------
    def enabled(self) -> bool:
        return bool(_cfg(self.cfg, "voice_edit", "wake", "enabled",
                         default=False))

    def start(self) -> bool:
        if not self.enabled():
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="voice-edit-wake")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def expect_reply(self, timeout_s: Optional[float] = None) -> None:
        """Re-arm the listener for a spoken reply (conversational turn).

        The arm window starts at the original wake phrase and usually
        expires during brain + TTS latency, so a bare "yes" answering a
        confirmation prompt would be discarded as non-wake speech. The
        helper calls this whenever it asks something ("say yes to run",
        a clarification question) so the next utterance routes without
        needing "hey qubit" again. Expires on its own; never raises.
        """
        try:
            wait = float(timeout_s) if timeout_s else self.arm_window_s
        except Exception:
            wait = self.arm_window_s
        try:
            with self._lock:
                self._armed_until = time.monotonic() + max(1.0, wait)
        except Exception:
            pass

    def _new_mic(self) -> Any:
        if self._mic_factory is not None:
            return self._mic_factory()
        from .stt_client import ParecMic
        from .noise import resolve_capture_device
        device = resolve_capture_device(self.cfg)
        gain = float(_cfg(self.cfg, "voice_edit", "mic_gain", default=12.0) or 12.0)
        return ParecMic(device=device, frame_ms=300, gain=gain,
                        target_peak=_mic_target_peak(self.cfg, default=0.5),
                        agc=bool(_cfg(self.cfg, "voice_edit", "agc",
                                      default=True)))

    def _do_transcribe(self, pcm: bytes) -> str:
        # Honest consumer of ``voice_edit.noise_suppression``: the software
        # NS/AGC stage runs on the captured utterance before either STT path
        # (accurate or streaming). OFF → pcm unchanged (see _maybe_ns_filter).
        pcm = _maybe_ns_filter(pcm, self.cfg)
        if self._transcribe is not None:
            return self._transcribe(pcm)
        # Accurate whole-utterance pass first (default); the streaming socket
        # remains the live path and the fallback when the accurate engine is
        # unavailable or yields nothing.
        mode = str(_cfg(self.cfg, "voice_edit", "stt_mode",
                        default="accurate") or "accurate").lower()
        if mode == "accurate":
            ready, reason = _accurate_ready(self.cfg)
            if ready:
                text, fail = "", ""
                try:
                    from .stt_client import transcribe_accurate
                    text, fail = transcribe_accurate(
                        self.cfg, _normalize_pcm(
                            pcm, target_peak=_mic_target_peak(self.cfg)))
                except Exception as exc:
                    fail = str(exc)[:160] or "accurate failed"
                if text.strip():
                    return text
                _trace("accurate_fallback", reason=fail or "empty transcript")
            else:
                _trace("accurate_fallback", reason=reason or "unavailable")
        from .stt_client import SttClient, transcribe_once
        from .stt_client import SttError
        device = str(_cfg(self.cfg, "voice_edit", "asr_device", default="") or "")
        ticket = _mint_ticket(self.cfg)
        if not ticket:
            return ""
        url = _ws_url(self.cfg)
        engine = str(_cfg(self.cfg, "voice_edit", "stt_engine", default="") or "")
        client = SttClient(url=url, ticket=ticket, engine=engine,
                           target_peak=_mic_target_peak(self.cfg, default=0.5))
        pcm = _normalize_pcm(pcm, target_peak=_mic_target_peak(self.cfg))
        ev = threading.Event()
        mic = BufferMic(pcm, on_exhausted=lambda: ev.set())
        try:
            return transcribe_once(client, mic, ev) or ""
        except Exception:
            return ""

    # -- loop -------------------------------------------------------------
    def _loop(self) -> None:
        mic = None
        ep = _make_segmenter(self.cfg, self._segmenter_factory)
        try:
            mic = self._new_mic()
            mic.start()
        except Exception as exc:
            self.last_error = str(exc)[:160]
            _trace("mic_error", detail=self.last_error)
            return
        _trace("started", phrase=self.phrase, engine=self.engine,
               segmenter=getattr(ep, "backend", "energy"))
        buf: List[bytes] = []
        # 600 ms pre-roll: the endpointer only buffers frames AFTER it
        # flips to speech, so up to ~300 ms of a soft onset ("hey" starting
        # mid-frame) never reached ASR. The previous two frames are
        # prepended when a segment opens (whisper's own VAD trims the
        # silence padding). Cleared on use and on reset so one segment's
        # tail can never leak into the next.
        prev: List[bytes] = []
        kws_hit = False
        try:
            while not self._stop.is_set():
                # Yield the microphone and STT to any active Right-Ctrl session.
                if self.is_busy():
                    ep.reset()
                    buf.clear()
                    prev.clear()
                    kws_hit = False
                    now = time.monotonic()
                    if now - getattr(self, "_last_busy_log", 0.0) > 30.0:
                        self._last_busy_log = now
                        _trace("busy_skip")
                    if self._kws is not None:
                        try:
                            self._kws.reset()
                        except Exception:
                            pass
                    time.sleep(0.1)
                    continue
                frame = mic.read_frame(timeout=0.2)
                if frame is None:
                    continue
                state = ep.add_frame(frame)
                if self._kws is not None:
                    try:
                        samples = (np.frombuffer(frame, dtype="<i2")
                                   .astype(np.float32) / 32768.0)
                        n = getattr(self, "_dbg_frames", 0) + 1
                        self._dbg_frames = n
                        if n % 100 == 0:
                            _trace("kws.frames", n=n,
                                   peak=round(float(np.abs(samples).max()), 3))
                        if self._kws.feed(samples):
                            kws_hit = True
                            _trace("kws.hit")
                            beep(self.cfg, "wake")
                    except Exception as exc:
                        now = time.monotonic()
                        if now - getattr(self, "_last_kws_err", 0.0) > 5.0:
                            self._last_kws_err = now
                            _trace("kws.error", detail=str(exc)[:120])
                if state in ("speech", "done"):
                    if not buf:
                        # Segment-open edge: the mic is capturing the
                        # owner's voice RIGHT NOW. The orb's listening
                        # state reads this (2026-10-04: nothing fired
                        # while the owner talked — only the accepted
                        # command did, so mid-speech the orb sat idle).
                        buf.extend(prev)
                        prev.clear()
                        _trace("capturing")
                        self._last_capturing_log = time.monotonic()
                    else:
                        # Long segments (up to 15 s) would outlast the
                        # orb's 6 s listen window — re-fire about every
                        # 2 s while voiced frames keep arriving. Voice
                        # only, never on silence/drone.
                        try:
                            last = float(
                                getattr(self, "_last_capturing_log", 0.0)
                                or 0.0)
                        except (TypeError, ValueError):
                            last = 0.0
                        if time.monotonic() - last >= 2.0:
                            _trace("capturing")
                            self._last_capturing_log = time.monotonic()
                    buf.append(frame)
                if state != "done":
                    prev.append(frame)
                    del prev[:-2]
                    continue
                pcm = b"".join(buf)
                buf.clear()
                prev.clear()
                ep.reset()
                hit = kws_hit
                kws_hit = False
                if self._kws is not None:
                    if hit:
                        # Reset ONLY on a hit (latch clear). Resetting after
                        # every segment wiped the spotter's running context,
                        # so a lone "hey qubit" following any other sound was
                        # systematically missed (calibrated: first occurrence
                        # missed, repetitions caught). The stream consumes
                        # audio incrementally, so no reset is needed between
                        # misses.
                        try:
                            self._kws.reset()
                        except Exception:
                            pass
                    with self._lock:
                        armed = time.monotonic() < self._armed_until
                    if not hit and not armed:
                        # ASR-gated fallback for KWS misses: the spotter is
                        # deaf to some voices at every threshold while local
                        # ASR hears them fine. A bounded probe keeps the cost
                        # honest (short wake utterances only); a fallback hit
                        # emits through the same strip/arm path as asr-engine
                        # mode, and a miss is discarded, never typed.
                        if _asr_fallback_allows(self.cfg, pcm):
                            _trace("asr_fallback_probe", bytes=len(pcm))
                            self._handle_segment(pcm, forced=False)
                        else:
                            _trace("asr_fallback_skip", bytes=len(pcm))
                        continue
                self._handle_segment(pcm, forced=hit)
        finally:
            try:
                mic.stop()
            except Exception:
                pass

    def _maybe_confirm_bypass(self, text: str) -> bool:
        """Settle a bare confirmation against live pending (Lane 1).

        Returns True when the utterance was consumed as a confirmation
        (the caller must return without touching the wake/arm gate).
        Returns False when nothing was done — the caller falls through to
        today's behavior byte-identical.

        Matrix (open confirmation IS the consent context; bounded by the
        120 s TTL + settle-once journal, so a stray "yes" later cannot
        fire):

        * helper in-process pending live + helper-affirmative/negative →
          emit to the helper (it settles + speaks + audits through its
          existing pending branch, including CT journal work via
          service.task Slice-1b);
        * CT journal pending live for the voice session + CT-affirmative →
          settle directly through the existing
          ``_settle_affirmed`` / ``_settle_task_affirmed`` (audited there;
          the reply row lands in the voice session);
        * CT pending live + bare negative → cancel (deny) + audit,
          nothing emitted, nothing run;
        * else (no live pending anywhere) → False: an unarmed bare "yes"
          is still discarded as before.

        The arm is consumed on any bypass so one utterance never settles
        twice. Never raises; never invents a confirmation id.
        """
        try:
            affirmative = is_bare_affirmative(text)
            negative = False if affirmative else is_bare_negative(text)
        except Exception:
            return False
        if not affirmative and not negative:
            return False
        try:
            probe = self._pending_probe
            helper_live = bool(probe()) if probe is not None else False
        except Exception:
            helper_live = False
        ct_kind = _live_voice_pending(self.cfg)
        if not helper_live and not ct_kind:
            return False
        with self._lock:
            self._armed_until = 0.0
        chars = len(text or "")
        if helper_live and ((affirmative and _helper_says_affirmative(text))
                            or (negative and _helper_says_negative(text))):
            # The helper owns this confirmation (and the speech): route the
            # bare utterance through its existing settle/cancel branch. The
            # emit is gated on the helper's own predicates so a text it
            # would not settle can never clear its pending ("no by default"
            # supersede stays intact). The helper settle runs the staged
            # task through service.task(confirm=True), which settles CT
            # journal work first (Slice-1b) — one settle, no double.
            _trace("confirm_bypass", kind="affirmative" if affirmative
                   else "negative", via="helper", chars=chars)
            self._emit(text)
            return True
        if ct_kind and ((affirmative and _ct_says_affirmative(text))
                        or negative):
            # CT journal pending for the voice session: settle/cancel
            # directly through the existing settle-once path (audited
            # there; the reply row lands in the voice session). A helper
            # staging (if any) is untouched — this utterance was not its
            # confirmation.
            _trace("confirm_bypass", kind="affirmative" if affirmative
                   else "negative", via=ct_kind, chars=chars)
            if negative:
                _deny_voice_pending(self.cfg, ct_kind)
            else:
                settled = _settle_voice_pending(self.cfg, ct_kind)
                _trace("confirm_settled", via=ct_kind,
                       ok=bool((settled or {}).get("ok")),
                       code=str((settled or {}).get("code") or ""))
            return True
        return False

    def _handle_segment(self, pcm: bytes, *, forced: bool = False) -> None:
        if len(pcm) < 4800:
            _trace("segment_too_short", bytes=len(pcm))
            return
        _trace("segment", bytes=len(pcm), forced=forced,
               engine=self.engine, capped=_segment_capped(self.cfg, len(pcm)))
        try:
            text = self._do_transcribe(pcm).strip()
        except Exception as exc:
            self.last_error = str(exc)[:160]
            _trace("transcribe_error", detail=self.last_error)
            return
        _trace("transcript", chars=len(text), engine=self.engine,
               **(_dbg.text_fields(self.cfg, text=text)))
        if not text:
            return
        if is_prompt_echo(self.cfg, text):
            _trace("prompt_echo_skip", chars=len(text))
            return  # whisper parroted its initial_prompt on noise: hallucination
        now = time.monotonic()
        with self._lock:
            armed = now < self._armed_until
        aliases = [self.phrase, *WAKE_ALIASES]
        if forced:
            # The keyword spotter fired: whatever ASR heard is the command.
            # Strip the wake phrase if ASR rendered it, else use the whole text.
            remainder = strip_wake(text, aliases=aliases)
            cmd = remainder if remainder is not None else text
            if cmd.strip():
                # P1-4: a fired intent consumes any open arm, exactly like
                # the armed branch below — otherwise one intent (bare wake
                # + KWS-hit turn) emits twice.
                with self._lock:
                    self._armed_until = 0.0
                self._emit(cmd)
            else:
                with self._lock:
                    self._armed_until = time.monotonic() + self.arm_window_s
            return
        # Lane 1 (2026-10-03): a bare "yes"/"no" answering a staged
        # confirmation settles WITHOUT needing "hey qubit" first. The open
        # confirmation IS the consent context (bounded by the 120 s TTL +
        # settle-once journal), so this precheck runs BEFORE the wake-word /
        # arm discard: with no live pending it returns False and the turn
        # falls through to today's behavior byte-identical. The forced
        # (KWS-hit) path above already owns its turn and never reaches here.
        if self._maybe_confirm_bypass(text):
            return
        if armed:
            remainder = strip_wake(text, aliases=aliases)
            cmd = remainder if remainder is not None else text
            if not cmd.strip():
                # 2026-10-03 (journal 19:17): a bare-wake repeat ("Hey
                # Qubit." after "Hey Qubit!") strips to empty. Consuming
                # the arm here killed the window silently and the real
                # command seconds later died unarmed — keep the arm,
                # re-beep as the audible "still listening" cue, emit
                # nothing. Non-empty path below is byte-identical.
                _trace("armed_empty_repeat", chars=len(text))
                beep(self.cfg, "wake")
                return
            with self._lock:
                self._armed_until = 0.0
            self._emit(cmd)
            return
        remainder = strip_wake(text, aliases=aliases)
        if remainder is None:
            _trace("discarded", chars=len(text),
                   **(_dbg.text_fields(self.cfg, text=text)))
            self._maybe_hint(text)
            return  # not a wake phrase: discard, never type it
        if wake_match_kind(text, aliases=aliases) == "bare":
            # Weak evidence on the unarmed fallback path: a bare "Qubit"
            # without hey is as likely room chatter (or our own TTS echo)
            # as direction, so drop it silently — no beep, no arm, no
            # execute. Saying "hey qubit" stays the way to direct it; the
            # KWS-hit path still serves acoustic bare detections.
            _trace("bare_wake_drop", chars=len(text))
            return
        beep(self.cfg, "wake")
        if remainder.strip():
            self._emit(remainder)
        else:
            with self._lock:
                self._armed_until = time.monotonic() + self.arm_window_s

    def _emit(self, command: str) -> None:
        cmd = (command or "").strip()
        if cmd:
            _trace("command", chars=len(cmd),
                   **(_dbg.text_fields(self.cfg, command=cmd)))
            try:
                self.on_command(cmd)
            except Exception as exc:
                self.last_error = str(exc)[:160]

    def _maybe_hint(self, text: str) -> None:
        """Post one visible hint for a command-shaped unarmed discard.

        2026-10-03 (journal 19:16:43 / 19:17:12+18): perfect "Redo this
        text for me" discards left only a journal trace — the owner
        heard/saw nothing. When the discarded text is strongly
        command-shaped (see :func:`_is_command_shaped`), the injected
        ``on_hint`` sink shows ``heard: <text> — say hey qubit first``
        on the existing heard-preview/status line. Constraints, all
        pinned by tests: NO beep, NO speech (the hook only sets status
        text), rate-limited to one hint per ``_HINT_COOLDOWN_S``, and
        plain chatter stays fully silent. Never raises.
        """
        hook = self._on_hint
        if hook is None:
            return
        try:
            if not _is_command_shaped(self.cfg, text):
                return
        except Exception:
            return
        now = time.monotonic()
        try:
            last = float(self._last_hint_at)
        except Exception:
            last = 0.0
        if now - last < _HINT_COOLDOWN_S:
            _trace("hint_suppressed", chars=len(text or ""))
            return
        self._last_hint_at = now
        detail = "heard: %s — say hey qubit first" % (text or "").strip()[:160]
        _trace("heard_hint", chars=len(text or ""))
        try:
            hook(detail)
        except Exception:
            pass


def _mic_target_peak(cfg: Any, default: float = 0.6) -> float:
    """Configured ``voice_edit.mic_target_peak``, clamped to 0.05..0.95.

    Mirrors the ``update_voice_edit`` clamp in settings.py (not imported to
    avoid a cycle). Absent/unparseable → ``default`` (0.6 preserves the
    historical ``_normalize_pcm`` default; capture clients pass 0.5 to match
    the settings default and the ``ParecMic`` constructor default).
    """
    try:
        raw = _cfg(cfg, "voice_edit", "mic_target_peak", default=None)
        if raw is None:
            return float(default)
        return max(0.05, min(float(raw), 0.95))
    except Exception:
        try:
            return float(default)
        except Exception:
            return 0.6


def _wake_max_s(cfg: Any, default: float = 15.0) -> float:
    """Longest wake segment in seconds (default 15.0, clamped 2..25).

    A continuously-voiced room must not accumulate one unbounded segment:
    capping at the fallback probe bound means EVERY completed segment fits
    a probe, so nothing is ever silently skipped for length. A deliberate
    "hey qubit" after a short pause still opens its own segment (silence
    ends the previous one), so the cap only bounds the noisy case.

    15 s (was 8.0): the 8 s cap cut slow deliberate speech mid-word
    (2026-10-04 journal: 7.8 s segment, sentence unfinished). Silero VAD
    kills room drone (zero drone segments since it went live), so the
    anti-drone justification for the tight cap is gone; warm
    faster-whisper-small decodes ~0.03x realtime (10.9 s -> 0.33 s on
    this host), so a 15 s probe costs ~0.5 s inside a ~2 s turn.
    """
    try:
        v = float(_cfg(cfg, "voice_edit", "wake", "max_s",
                       default=default) or default)
    except Exception:
        v = default
    return max(2.0, min(v, 25.0))


def _asr_fallback_allows(cfg: Any, pcm: bytes) -> bool:
    """Whether a KWS-miss segment gets one ASR-gated fallback probe.

    Default ON: ``wake.asr_fallback`` (bool, default True). The probe is
    bounded to short utterances — ``wake.asr_fallback_max_s`` seconds
    (default 15.0, clamped 2..25) of 16 kHz mono s16 — because a wake phrase
    with its command is short; a long segment is skipped (the spotter-hit
    path still covers it when the spotter fires). Never raises: unreadable
    config falls back to the defaults.

    The bound moves with the segment cap (both default 15.0): every capped
    segment still fits a probe, so nothing is ever silently skipped for
    length.
    """
    try:
        on = _cfg(cfg, "voice_edit", "wake", "asr_fallback", default=True)
        if on is None:
            on = True
        if not bool(on):
            return False
    except Exception:
        pass
    try:
        max_s = float(_cfg(cfg, "voice_edit", "wake", "asr_fallback_max_s",
                            default=15.0) or 15.0)
    except Exception:
        max_s = 15.0
    max_s = max(2.0, min(max_s, 25.0))
    return len(pcm) <= int(max_s * 16000 * 2)


def _maybe_ns_filter(pcm: bytes, cfg: Any) -> bytes:
    """Run the software NS/AGC stage when ``voice_edit.noise_suppression`` is on.

    OFF (or unreadable cfg) → ``pcm`` returned unchanged. Never raises, so a
    filter fault can never break capture. Unit-observable: ns ON audibly
    changes a noisy utterance, OFF is byte-identical.
    """
    try:
        from .noise import apply_capture_filter, enabled as _ns_on
        if _ns_on(cfg):
            return apply_capture_filter(pcm, True)
    except Exception:
        pass
    return pcm


def _normalize_pcm(pcm: bytes, target_peak: float = 0.6,
                   max_gain: float = 24.0) -> bytes:
    """Peak-normalize one utterance so ASR sees a healthy level."""
    try:
        a = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        if a.size == 0:
            return pcm
        peak = float(np.max(np.abs(a))) / 32768.0
        if peak <= 0.001:
            return pcm
        factor = min(max_gain, target_peak / peak)
        if factor <= 1.01:
            return pcm
        return np.clip(a * factor, -32768, 32767).astype("<i2").tobytes()
    except Exception:
        return pcm


def _accurate_ready(cfg: Any) -> tuple[bool, str]:
    """Whether the accurate transcriber is importable and reports available."""
    try:
        from . import asr_accurate
    except Exception as exc:
        return False, f"accurate unavailable: {exc}"[:160]
    try:
        if asr_accurate.is_available(cfg):
            return True, ""
    except Exception as exc:
        return False, f"accurate unavailable: {exc}"[:160]
    return False, "accurate unavailable"


def _cfg(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default)
    except Exception:
        return default


def _mint_ticket(cfg: Any) -> str:
    try:
        from ..desktop_helper import HelperClient, resolve_service_base, resolve_token
        client = HelperClient(resolve_service_base(cfg), resolve_token(cfg))
        return client.mint_stt_ticket()
    except Exception:
        return ""


def _ws_url(cfg: Any) -> str:
    try:
        from ..desktop_helper import resolve_service_base
        base = resolve_service_base(cfg)
        return base.replace("http://", "ws://").replace("https://", "wss://") \
            + "/agent/api/audio/stt/ws"
    except Exception:
        return "ws://127.0.0.1:8890/agent/api/audio/stt/ws"


__all__ = ["WakeMonitor", "strip_wake", "wake_match_kind",
           "EnergyEndpointer", "BufferMic",
           "DEFAULT_PHRASE", "WAKE_ALIASES", "VAD_MODES"]
