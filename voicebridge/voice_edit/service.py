"""Voice-edit orchestration: capture -> parse -> transform -> preview -> apply.

One authority path, reused:
* e-stop comes from the single broker e-stop authority;
* audit goes through the single ``qubit_compat.audit_event`` writer;
* the transform engine uses the one provider gateway;
* the desktop adapter uses the one system clipboard / X11 path.

Selected text lives only in this process's memory for the lifetime of a request
(default 300 s) and is never written to disk or to the audit log. The preview
survives a target-window focus loss, because the request holds the text and the
apply step re-verifies focus before touching the desktop.
"""
from __future__ import annotations

import re as _re_module
import threading
import time
import uuid
from typing import Any, Dict, Optional

from . import debug as _dbg
from . import voice_sessions
from .brain import BrainRouter

from .desktop import DesktopAdapter
from .parser import extract_dictation, parse, strip_dictation_cue
from .schema import (EditCommand, InsertionResult, SelectionSnapshot,
                     TransformResult, VoiceEditError)
from .transform import TransformEngine, digest_text


#: Brain answers that are polite refusals, never real answers. A refusal is
#: never the desired final answer, so these escalate to the full agent turn
#: (worst case the full turn refuses too and the brain answer is kept).
#: Kept tight and substring-based: the cost of a false positive is only a
#: slower (still valid) full turn, never a wrong action.
_BRAIN_REFUSAL_RES = (
    _re_module.compile(r"\bi\s*(can'?t|cannot|couldn'?t)\b", _re_module.IGNORECASE),
    _re_module.compile(r"\bi'?m\s+not\s+able\s+to\b", _re_module.IGNORECASE),
    _re_module.compile(r"\bi'?m\s+unable\s+to\b", _re_module.IGNORECASE),
    _re_module.compile(r"\b(don'?t|do\s+not)\s+have\s+(the\s+ability|access|real-?time)",
                       _re_module.IGNORECASE),
    _re_module.compile(r"\bno\s+real-?time\b", _re_module.IGNORECASE),
    _re_module.compile(r"\bas\s+an\s+ai\b", _re_module.IGNORECASE),
)


def _looks_like_brain_refusal(text: str) -> bool:
    """True when a brain answer reads as a refusal rather than an answer."""
    t = str(text or "")
    if not t.strip():
        return False
    return any(rx.search(t) for rx in _BRAIN_REFUSAL_RES)


def _signal_helper_stop() -> bool:
    """Best-effort: tell a live desktop helper to stop its spoken reply.

    A cancel (orb STOP or ``/cancel``) must never be a no-op for audio: the
    helper owns TTS playback, so the service sends it SIGUSR2 (the helper's
    ``on_orb_stop`` -> ``stop_speaking`` handler). Only a heartbeat pid that
    is fresh AND cmdline-validated by
    :mod:`voicebridge.voice_edit.heartbeat` is ever signalled, so a recycled
    pid belonging to another process is never touched. Returns True when a
    signal was sent. Never raises; no helper / no ``/proc`` is a quiet
    ``False``.
    """
    try:
        import os
        import signal
        from . import heartbeat as _hb
        pid = _hb.other_live_pid(exclude=os.getpid())
        if not pid:
            return False
        os.kill(pid, signal.SIGUSR2)
        return True
    except Exception:
        return False


#: Worker narration shapes (Lane 2, 2026-10-03, journal test-4 09:54:53):
#: the headless worker ran an irrelevant tool (``local.open_app`` for a
#: create-file task) and summarized with assistant chatter
#: ("Qubit is available to assist with tasks. Let me know how I can
#: help.") — the T5-forbidden shape: narration instead of an honest
#: failure. A reply matching one of these with no action evidence behind
#: it is a typed ``VOICE_EDIT_WORKER_NARRATION`` failure, never a spoken
#: ok answer. Kept literal and narrow so a genuine report that happens to
#: thank ("Created X — let me know if you need more!") still passes (it
#: carries action evidence; see :func:`_full_turn_narration_only`).
_WORKER_NARRATION_RES = (
    _re_module.compile(r"\bavailable to assist\b", _re_module.IGNORECASE),
    _re_module.compile(r"\blet me know how i can help\b",
                       _re_module.IGNORECASE),
    _re_module.compile(r"\bhow can i (help|assist)( you)?\b",
                       _re_module.IGNORECASE),
    _re_module.compile(r"\bi'?m here to help\b", _re_module.IGNORECASE),
    _re_module.compile(r"\bwhat can i (do|help).{0,40}for you\b",
                       _re_module.IGNORECASE),
)

#: Action evidence that redeems a polite reply: past-tense acted verbs,
#: concrete file identities, or completed-work nouns. Mirrors the
#: ``chat_transport._ACTION_RE`` completion half (kept local so the voice
#: lane never imports the chat branch internals).
_WORKER_ACTION_RE = _re_module.compile(
    r"\b(built|created|wrote|written|saved|added|finished|completed|"
    r"updated|generated|delivered|attached|done|opened|sent|set|found|"
    r"playing|paused|resumed|stopped|closed)\b"
    r"|[A-Za-z0-9_][A-Za-z0-9_.\-]*\.(html?|js|jsx|ts|tsx|css|json|md|"
    r"txt|py|png|jpe?g|pdf)\b"
    r"|/(?:[\w.\-]+/)+[\w.\-]+",
    _re_module.IGNORECASE)


def _full_turn_narration_only(text: str) -> bool:
    """True when a worker reply is narration with no action evidence."""
    t = str(text or "").strip()
    if not t:
        return False
    if not any(rx.search(t) for rx in _WORKER_NARRATION_RES):
        return False
    return not bool(_WORKER_ACTION_RE.search(t))

REQUEST_TTL_S = 300.0

#: Minimum interval between opportunistic voice-session retention sweeps. The
#: sweep is best-effort and runs on a real turn (ask/task), so this keeps it off
#: the hot path without needing a scheduler.
_VOICE_PRUNE_INTERVAL_S = 600.0


def _counts_turn(fn):
    """Count a turn as in-flight for exactly its duration (never raises).

    ``status()["active_requests"]`` used to report begun-but-expired
    sessions (9 stale with nothing running, live 2026-10-04) — the orb
    read permanent "thinking". It now reports real in-flight turns only;
    nested calls (route() -> transform()) balance out. The stale-session
    prune in ``status()`` stays as hygiene.
    """
    import functools as _ft

    @_ft.wraps(fn)
    def _w(self, *a, **k):
        try:
            with self._lock:
                self._inflight += 1
        except Exception:
            pass
        try:
            return fn(self, *a, **k)
        finally:
            try:
                with self._lock:
                    self._inflight = max(0, self._inflight - 1)
            except Exception:
                pass

    return _w

_QUESTION_STARTERS = (
    "what", "who", "when", "where", "why", "how", "which", "whose",
    "is ", "are ", "do ", "does ", "did ", "can ", "could ", "would ",
    "should ", "will ", "tell me", "show me", "what's", "whats",
)

#: Manual sleep-mode trigger (owner decision 2026-10-03 §5 #1): "going to
#: sleep" / "I'm going to sleep" silences UNPROMPTED speech (completions,
#: briefings/proactive) until the next directed "hey qubit". Substring-based
#: and punctuation-tolerant; the sleep turn itself is on-request and always
#: answered.
_SLEEP_COMMAND_RE = _re_module.compile(
    r"\bgoing\s+to\s+sleep\b", _re_module.IGNORECASE)

#: Dashboard waveform visibility (voice-final lane): "show waveform" /
#: "hide waveform" flips ``voice_edit.waveform_visible`` through the SAME
#: ask-stage mechanism as sleep mode (deterministic, audited, remembered —
#: no parallel dispatcher). Owner-natural shapes (2026-10-04, journal:
#: "Waveform on." reached the helper with no flip): noun-first
#: "waveform on/off" (END-ANCHORED, punctuation-tolerant — so "waveform on
#: the screen" never fires), "turn on/off (the) waveform" and
#: "enable/disable (the) waveform" (verb adjacent to "waveform", optional
#: "the" between — so "show me the waveform settings" still never fires).
#: The in-game override regexes below accept the SAME owner-natural shapes
#: (show/hide, turn on/off, enable/disable, noun-first on/off — each with the
#: spaced "wave form" variant) but require the end-anchored ingame qualifier.
#: STT emits the spaced two-word "wave form" (journal 2026-10-04 10:17:47
#: owner-verbatim "Show Wave Form."): every ``waveform`` atom below also
#: accepts ``wave <space> form`` with the same adjacency guards.
#: ``_INGAME_QUAL`` is the shared qualifier tail (ingame / in-game / in game,
#: utterance-final with optional trailing punctuation — so "show waveform in
#: game settings" never fires the override).
_INGAME_QUAL = r"(ingame|in-game|in\s+game)\s*[.?!]*\s*$"
_WAVEFORM_SHOW_RE = _re_module.compile(
    r"\bshow\s+(the\s+)?wave(?:form|\s+form)\b"
    r"|\bturn\s+on\s+(the\s+)?wave(?:form|\s+form)\b"
    r"|\benable\s+(the\s+)?wave(?:form|\s+form)\b"
    r"|\bwave(?:form|\s+form)\s+on\s*[.?!]*\s*$", _re_module.IGNORECASE)
_WAVEFORM_HIDE_RE = _re_module.compile(
    r"\bhide\s+(the\s+)?wave(?:form|\s+form)\b"
    r"|\bturn\s+off\s+(the\s+)?wave(?:form|\s+form)\b"
    r"|\bdisable\s+(the\s+)?wave(?:form|\s+form)\b"
    r"|\bwave(?:form|\s+form)\s+off\s*[.?!]*\s*$", _re_module.IGNORECASE)

#: In-game orb override (owner 2026-10-04): "show waveform ingame" /
#: "hide waveform ingame" flips ``voice_edit.waveform_ingame`` ONLY (the
#: orb's fullscreen rule — never the dashboard ``waveform_visible`` widget).
#: Accepts the same owner-natural shapes as the widget pair (show/hide,
#: turn on/off, enable/disable, noun-first on/off, spaced "wave form"),
#: each requiring the end-anchored qualifier (optional trailing
#: punctuation), so "show waveform in game settings" never fires the
#: override. Checked BEFORE the bare widget regexes (which substring-match
#: the verb-first qualified phrases); the hey-prefix is handled upstream
#: (strip_wake) and inline (substring search), as proven for the bare pair.
_WAVEFORM_INGAME_SHOW_RE = _re_module.compile(
    r"\bshow\s+(the\s+)?wave(?:form|\s+form)\s+" + _INGAME_QUAL
    + r"|\bturn\s+on\s+(the\s+)?wave(?:form|\s+form)\s+" + _INGAME_QUAL
    + r"|\benable\s+(the\s+)?wave(?:form|\s+form)\s+" + _INGAME_QUAL
    + r"|\bwave(?:form|\s+form)\s+on\s+" + _INGAME_QUAL,
    _re_module.IGNORECASE)
_WAVEFORM_INGAME_HIDE_RE = _re_module.compile(
    r"\bhide\s+(the\s+)?wave(?:form|\s+form)\s+" + _INGAME_QUAL
    + r"|\bturn\s+off\s+(the\s+)?wave(?:form|\s+form)\s+" + _INGAME_QUAL
    + r"|\bdisable\s+(the\s+)?wave(?:form|\s+form)\s+" + _INGAME_QUAL
    + r"|\bwave(?:form|\s+form)\s+off\s+" + _INGAME_QUAL,
    _re_module.IGNORECASE)

#: On-request briefing trigger (Slice 4b, owner decision 2026-10-03 §5 #2a):
#: "brief me" / "morning briefing". Substring-based like the sleep trigger;
#: deliberately narrow (no bare "briefing" — "a briefing document" is not a
#: request for speech). The turn itself is on-request and never sleep-gated.
#: Lane 3 (2026-10-03): owner's ASR hears "brief me" as "breathe"/"breathe me"/
#: "brave me" — those misheard shapes are accepted end-anchored (case/punct
#: tolerant), so "breathe deeply" and "be brave" still do NOT match.
_BRIEF_ME_RE = _re_module.compile(
    r"\bbrief\s+me\b|\bmorning\s+briefing\b"
    r"|\b(?:breathe(?:\s+me)?|brave\s+me|brief)\s*[.?!]*\s*$",
    _re_module.IGNORECASE)

#: On-request away-digest trigger (Lane 3, 2026-10-03): "what did I miss" /
#: "while I was away" / "catch me up". Substring-based like the briefing
#: trigger; deliberately narrow — "I miss my old phone" is not a digest
#: request (the verb needs the what/away/catch frame). The turn itself is
#: on-request and never sleep-gated, mirroring brief_me(); only UNPROMPTED
#: digest delivery consults sleep (and currently has no callers — the
#: gate is in place for when a caller arrives).
#: Sweep lane (2026-10-05): the bare-newness branches ("anything new" /
#: "what's new") must not fire on topical newness — "what's new on YouTube"
#: and "is there anything new on Netflix?" are skill/entertainment turns,
#: not away-digests. Those two branches carry a negative lookahead for a
#: topic continuation (on/about/in/at/for/with/from); the framed branches
#: ("catch me up on Q3" IS a digest request) are untouched.
_DIGEST_REQUEST_RE = _re_module.compile(
    r"\bwhat\s+did\s+i\s+miss\b|\bwhile\s+i\s+was\s+away\b"
    r"|\bcatch\s+me\s+up\b"
    r"|\banything\s+new\b(?!\s+(on|about|in|at|for|with|from)\b)"
    r"|\bwhat'?s\s+new\b(?!\s+(on|about|in|at|for|with|from)\b)",
    _re_module.IGNORECASE)

#: Deterministic reread (sweep lane, 2026-10-05): "say that again" /
#: "read that back" / "repeat that" replays the last spoken answer instead
#: of being dropped (router ignore) or chattered about (brain). End-anchored
#: and punct-tolerant like the briefing shapes: "say that" (no again),
#: "read the news", and "repeat after me" never match. "what did you say"
#: is question-shaped but answered here, before the brain, so the replay is
#: verbatim instead of a paraphrase.
_REPEAT_RE = _re_module.compile(
    r"\bsay\s+(that|it)\s+again\s*[.?!]*\s*$"
    r"|\bread\s+(that|it)\s+back\s*[.?!]*\s*$"
    r"|\brepeat\s+(that|it)\s*[.?!]*\s*$"
    r"|\bwhat\s+did\s+you\s+(just\s+)?say\s*[?]?\s*$"
    r"|\bcome\s+again\s*[?]?\s*$",
    _re_module.IGNORECASE)

#: A defect report ("report a bug/issue/problem", "file an error report") is
#: not a request to summarize a chat session. The verb/noun match below is
#: adjacency-bounded but "report a bug in the session manager" still puts a
#: report-ish verb near a session noun, so this frame is excluded explicitly
#: and before the match — a bug report is routed as a normal ask, never as a
#: session summary.
_REPORT_DEFECT_RE = _re_module.compile(
    r"\breport\b(?:\s+(?:a|an|the))?\s+"
    r"(bug|issue|problem|error|defect|ticket|complaint|incident)\b"
    r"|\b(bug|issue|problem|error|defect|ticket|complaint|incident)\s+report\b",
    _re_module.IGNORECASE)

#: Deterministic chat-session report (report lane, 2026-10-06): "report on my
#: chat" / "summarize the chat session" / "what did we do in that session"
#: read the routed/current canonical transcript and speak a SHORT summary
#: (never the raw transcript). Conservative: a report-ish verb must sit ADJACENT
#: (within a few words) to a chat/session/conversation noun, or an explicit
#: "what did we do / what happened" frame must carry that noun — so ordinary
#: questions ("what time is it"), a document ask ("summarize this document"),
#: a YouTube/email ask, and a defect report ("report a bug in the session
#: manager", excluded by :data:`_REPORT_DEFECT_RE`) never match. The bounded
#: verb/noun gap cannot bridge two unrelated clauses.
_REPORT_CHAT_RE = _re_module.compile(
    r"\b(report|recap|summari[sz]e|summary)\b(?:\s+\w+){0,3}\s+"
    r"(chat|session|conversation)\b"
    r"|\b(chat|session|conversation)\b(?:\s+\w+){0,3}\s+"
    r"(report|recap|summari[sz]e|summary)\b"
    r"|\bwhat\s+(did\s+)?we\s+(do|talk\s+about|discuss)\b(?:\s+\w+){0,4}\s+"
    r"(chat|session|conversation)\b"
    r"|\bwhat\s+happened\b(?:\s+\w+){0,4}\s+(chat|session|conversation)\b",
    _re_module.IGNORECASE)


def _looks_like_question(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    return t.endswith("?") or low.startswith(_QUESTION_STARTERS)


class _Request:
    __slots__ = ("request_id", "session_id", "created_at", "selection",
                 "command", "original", "proposed", "result", "applied",
                 "applied_text", "target_app", "task_text")

    def __init__(self, request_id: str, session_id: str) -> None:
        self.request_id = request_id
        self.session_id = session_id
        self.created_at = time.monotonic()
        self.selection: Optional[SelectionSnapshot] = None
        self.command: Optional[EditCommand] = None
        self.original: str = ""
        self.proposed: str = ""
        self.result: Optional[TransformResult] = None
        self.applied: bool = False
        self.applied_text: str = ""
        self.target_app: str = ""
        self.task_text: str = ""


def _read_cfg(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default)
    except Exception:
        return default


# --- TTS provider memoization (voice-turn latency, 2026-10-07) -------------
#
# MEASURED: ``qubit_compat.h_audio_speak`` (POST /api/audio/speak, the ONE
# local TTS exit the desktop helper uses for every spoken reply) builds a
# fresh provider with ``_tts_from_cfg(cfg)`` and calls ``load()`` on EVERY
# request. For the piper/sherpa backend that is a full ONNX model reload:
# ~1000-1140 ms of fixed cost per spoken reply, while the actual synthesis
# is 40 ms (short reply) to ~300 ms (long). The voice-turn journal shows
# ``answer -> tts.synthesize`` at a 1095 ms median, dominated by that reload.
#
# One warm provider per (config identity, ``tts`` section, model dir) removes
# the fixed cost with NO audio change: the provider is stateless across
# ``synthesize`` calls (piper serializes synthesis under its own global
# lock) and this is exactly the object the orchestrator already reuses for
# dashboard speech. ``install_tts_provider_cache`` swaps
# ``qubit_compat._tts_from_cfg`` for a memoizing wrapper; it is idempotent
# and fail-open, and ``_tts_from_cfg`` has exactly one caller
# (``h_audio_speak``), so the wrapper's return contract is unchanged.
_TTS_CACHE_LOCK = threading.Lock()


def _tts_cache_key(cfg: Any) -> Any:
    """Signature that must change for a DIFFERENT provider to be warranted.

    Deliberately NOT ``id(cfg)``: the service and ``h_audio_speak`` may hold
    distinct but equivalent config objects for the same profile, and they
    must share ONE provider. Path + the whole ``tts`` section + model dir is
    the semantic identity (a voice/backend/provider change rebuilds).
    """
    try:
        return (str(getattr(cfg, "path", "") or ""),
                repr(_read_cfg(cfg, "tts", default={}) or {}),
                str(getattr(cfg, "model_dir", lambda: "")() or ""))
    except Exception:  # noqa: BLE001 - cache key only, never fatal
        return ("tts",)


def _make_cached_tts_provider(original: Any) -> Any:
    """Wrap a ``_tts_from_cfg(cfg)`` factory so each config builds ONCE.

    The factory's own ``load()`` still runs on first use (it is what warms
    the returned object); later callers get the SAME loaded object, so
    ``load()`` is a no-op and only real synthesis time remains.
    """
    cache: Dict[Any, Any] = {}

    def _cached(cfg: Any) -> Any:
        key = _tts_cache_key(cfg)
        with _TTS_CACHE_LOCK:
            tts = cache.get(key)
            if tts is None:
                tts = original(cfg)
                cache[key] = tts
            return tts

    _cached._qubit_tts_cached = True
    _cached._qubit_tts_original = original
    return _cached


def install_tts_provider_cache(module: Any = None) -> bool:
    """Install the shared-TTS wrapper on ``qubit_compat`` (default) or a
    test double. Idempotent; any failure leaves the original behavior."""
    if module is None:
        try:
            from .. import qubit_compat as _compat  # type: ignore
        except Exception:  # noqa: BLE001 - never break the caller
            return False
        module = _compat
    original = getattr(module, "_tts_from_cfg", None)
    if original is None:
        return False
    if getattr(original, "_qubit_tts_cached", False):
        return True
    try:
        module._tts_from_cfg = _make_cached_tts_provider(original)
    except Exception:  # noqa: BLE001
        return False
    return True


class VoiceEditService:
    def __init__(self, cfg: Any, *, engine: Optional[TransformEngine] = None,
                 desktop: Optional[DesktopAdapter] = None,
                 brain: Optional[BrainRouter] = None,
                 router: Optional[Any] = None,
                 audit: Any = None, estop: Any = None,
                 session_store: Any = None) -> None:
        self.cfg = cfg
        self.engine = engine or TransformEngine()
        self.desktop = desktop or DesktopAdapter()
        self.brain = brain or BrainRouter()
        # The ONE decision router. Injectable so tests exercise dispatch without
        # a model; production builds the shared :class:`VoiceRouter`.
        self.router = router
        self._audit_fn = audit
        self._estop_fn = estop
        # Optional canonical chat store override (tests). When None the ONE
        # canonical store the voice path writes is resolved lazily in
        # ``_remember_ask_exchange`` — never a second store.
        self._session_store = session_store
        self._lock = threading.Lock()
        self._reqs: Dict[str, _Request] = {}
        #: Ask-turn cancellation (orb STOP). One cancel event per in-flight
        #: ``/api/voice-edit/ask`` request id: the helper's stop hits
        #: ``/<rid>/cancel`` (:meth:`cancel`), which sets the event so
        #: ``ask``/``_full_turn_answer`` skip or abort the expensive model or
        #: agent call and never hand back a reply to be spoken. ``_ask_cancel_at``
        #: timestamps the entries so an unconsumed tombstone (a cancel with no
        #: in-flight ask) is pruned on the request TTL. Both guarded by ``_lock``.
        self._ask_cancels: Dict[str, threading.Event] = {}
        self._ask_cancel_at: Dict[str, float] = {}
        #: Real in-flight turns (see _counts_turn). Seeds 0; sessions that
        #: merely began (orb click staging, helper begins) are not turns.
        self._inflight = 0
        # Latency (2026-10-07): the first voice-edit request is the helper's
        # ``session`` (begin) call, which always precedes its ``/api/audio/speak``
        # call — so installing the shared-TTS wrapper here makes the very first
        # spoken reply use the warm provider instead of reloading the model.
        # Best-effort: a failure leaves the original per-request behavior.
        try:
            install_tts_provider_cache()
        except Exception:  # noqa: BLE001
            pass

    # -- authority helpers ----------------------------------------------
    def _estop(self) -> bool:
        if self._estop_fn is not None:
            try:
                return bool(self._estop_fn(self.cfg))
            except Exception:
                return True
        try:
            from ..broker import estop_engaged
            return bool(estop_engaged(self.cfg))
        except Exception:
            return True

    def _audit(self, op: str, detail: Dict[str, Any]) -> None:
        if self._audit_fn is not None:
            try:
                self._audit_fn(op, "voice-edit", detail)
                return
            except Exception:
                pass
        try:
            from ..qubit_compat import audit_event
            audit_event(op, "voice-edit", detail)
        except Exception:
            pass

    def _copy_transform_to_clipboard(self, text: str, *,
                                           request_id: str = "",
                                           audit_op: str = "") -> Dict[str, Any]:
        """Copy one successful transform output to the system clipboard.

        Owner ask (2026-10-04 voice-final lane): every redo/rewrite result
        lands in the clipboard so it can be pasted. Write-only clipboard
        use (no capture, no keys) — safe on the selection-less wake path
        AND the PTT path. A missing clipboard is audited LOUD (typed code,
        ``ok: False``) and returned to the caller — never silently skipped.
        Only char counts / backend names reach the audit, never text.
        """
        try:
            from .clipboard import copy_text as _copy_text
            res = _copy_text(text, desktop=self.desktop)
        except Exception:
            res = {"ok": False, "backend": "none",
                   "code": "VOICE_EDIT_CLIPBOARD_UNAVAILABLE"}
        try:
            self._audit(audit_op or "voice_edit.transform.clipboard", {
                "request_id": request_id or "",
                "ok": bool(res.get("ok")),
                "backend": str(res.get("backend", "") or ""),
                "code": str(res.get("code", "") or ""),
                "chars": len(text or "")})
        except Exception:
            pass
        return res

    def _canonical_store(self) -> Any:
        """The ONE canonical chat store the voice/PTT path already writes.

        Reuses exactly ``voicebridge.agent._canonical_session_store`` (over
        ``chat_transport.store`` -> :class:`QubitSessionStore`), the same store
        ``orchestrator._remember_voice_exchange`` and ``/qubit-chat`` use. An
        injected store (tests) wins; resolution is best-effort and never a
        second store.
        """
        if self._session_store is not None:
            return self._session_store
        try:
            from ..agent import _canonical_session_store
            return _canonical_session_store(self.cfg)
        except Exception as exc:  # noqa: BLE001 - persistence is additive
            self._audit("voice_edit.store.unavailable", {"error": str(exc)[:160]})
            return None

    def _remember_ask_exchange(self, question: str, answer: str, *,
                               model: str = "") -> None:
        """Record an ask exchange as a real user + assistant turn.

        Best-effort and additive: the answer was already computed, so a store
        fault is audited and never changes the return value or raises to the
        caller. The canonical session id is used so ``/qubit-chat`` shows the
        exchange through its existing feed (the same path a spoken turn takes).
        """
        if not answer:
            return
        try:
            from ..agent import _persist_session_turn
            store = self._canonical_store()
            if store is None:
                return
            sid = self._voice_session()
            _persist_session_turn(store, sid, "user", question,
                                  model=model or None)
            _persist_session_turn(store, sid, "assistant", answer,
                                  model=model or None)
        except Exception as exc:  # noqa: BLE001 - additive, non-fatal
            self._audit("voice_edit.store.persist_error",
                        {"error": str(exc)[:160]})

    def enabled(self) -> bool:
        return bool(_read_cfg(self.cfg, "voice_edit", "enabled", default=False))

    def _brain_enabled(self) -> bool:
        return bool(_read_cfg(self.cfg, "voice_edit", "brain_router",
                              default=True))

    def _block_fullscreen(self) -> bool:
        return bool(_read_cfg(self.cfg, "voice_edit", "block_fullscreen",
                              default=True))

    def _require_enabled(self) -> None:
        if not self.enabled():
            raise VoiceEditError(
                "VOICE_EDIT_DISABLED",
                "Qubit voice typing is disabled; enable it in Settings first",
                status=403)

    def _require_estop_clear(self) -> None:
        if self._estop():
            raise VoiceEditError("EMERGENCY_STOP",
                                 "emergency stop is engaged; voice edit refused",
                                 status=423)

    def ttl(self) -> float:
        try:
            return float(_read_cfg(self.cfg, "voice_edit", "request_ttl_s",
                                   default=REQUEST_TTL_S) or REQUEST_TTL_S)
        except (TypeError, ValueError):
            return REQUEST_TTL_S

    # -- lifecycle -------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        # Prune before counting: begun sessions die by TTL, and every wake
        # turn / helper begin / orb click begins one. Without this the
        # count only grew and the orb read permanent "thinking" (live
        # 2026-10-04: active_requests 9 with nothing in flight — the
        # yellow-green the owner reads as stuck).
        try:
            self._prune()
        except Exception:
            pass
        caps = self.desktop.session()
        try:
            from . import heartbeat as _hb
            helper_live = _hb.live()
            hb = _hb.read() or {}
        except Exception:
            helper_live = False
            hb = {}
        wake_enabled = bool(_read_cfg(self.cfg, "voice_edit", "wake", "enabled",
                                      default=False))
        return {
            "enabled": self.enabled(),
            "helper_live": helper_live,
            "wake_enabled": wake_enabled,
            "wake_live": bool(helper_live and hb.get("wake")),
            "wake_engine": str(hb.get("wake_engine", "") or ""),
            "estop": self._estop(),
            "session": caps,
            "tools": {
                "xdotool": bool(self.desktop._tool("xdotool")),
                "xclip": bool(self.desktop._tool("xclip")),
                "wtype": bool(self.desktop._tool("wtype")),
                "wl_copy": bool(self.desktop._tool("wl-copy")),
            },
            "routing": {
                "mode": str(_read_cfg(self.cfg, "voice_edit", "routing",
                                      default="auto") or "auto"),
                "allow_api": bool(_read_cfg(self.cfg, "voice_edit", "allow_api",
                                            default=True)),
                "local_profile": str(_read_cfg(
                    self.cfg, "voice_edit", "local_profile",
                    default="qubit-brain4") or "qubit-brain4"),
                "api_profile": str(_read_cfg(self.cfg, "voice_edit",
                                             "api_profile", default="") or ""),
            },
            "confirm_before_replace": bool(_read_cfg(
                self.cfg, "voice_edit", "confirm_before_replace",
                default=True)),
            "auto_send": bool(_read_cfg(self.cfg, "voice_edit", "auto_send",
                                        default=False)),
            "request_ttl_s": self.ttl(),
            "active_requests": int(getattr(self, "_inflight", 0) or 0),
            "privacy": (
                "debug logging is ON: spoken transcript and dictated text "
                "are recorded locally"
                if _dbg.enabled(self.cfg)
                else "no selected text is logged or persisted"
            ),
        }

    def begin(self, *, session_id: str = "", request_id: str = "",
              target_app: str = "") -> Dict[str, Any]:
        self._require_enabled()
        self._require_estop_clear()
        rid = request_id or uuid.uuid4().hex
        req = _Request(rid, session_id)
        req.target_app = target_app
        with self._lock:
            self._prune()
            self._reqs[rid] = req
        # Owner directive (wave 5 TASK 2): every PTT / wake / voice interaction
        # must CREATE the session in ``/qubit-chat``. ``begin`` is the single
        # seam both the Right-Ctrl path and the wake path call
        # (``desktop_helper._open_request`` -> ``client.begin``), so ensuring the
        # canonical dashboard-source session here covers both. Best-effort and
        # additive — a store fault never blocks the turn (see
        # ``_ensure_voice_session``).
        ensured = self._ensure_voice_session(session_id)
        self._audit("voice_edit.begin", {"request_id": rid,
                                         "session_id": session_id or "",
                                         "target_app": target_app or "",
                                         "ensured_session": ensured.get(
                                             "session_id", ""),
                                         "ensured_state": ensured.get(
                                             "state", ""),
                                         "ensured_created": bool(
                                             ensured.get("created"))})
        return {"ok": True, "request_id": rid, "session_id": session_id,
                "ttl_s": self.ttl()}

    def _ensure_voice_session(self, routed_session_id: str = "") -> Dict[str, Any]:
        """Ensure the canonical dashboard-source session for this interaction.

        Delegates to :func:`voicebridge.voice_edit.voice_sessions.ensure_voice_session`
        on the SAME injectable canonical store the turn path writes
        (``self._canonical_store``), so a Right-Ctrl / wake / voice interaction
        creates (or reuses) a session ``/qubit-chat`` lists — never a second
        store, never a second identity. Best-effort: a fault is audited and the
        turn is unchanged.
        """
        try:
            return voice_sessions.ensure_voice_session(
                self.cfg, routed_session_id or None,
                store=self._canonical_store())
        except Exception as exc:  # noqa: BLE001 - never block a turn
            self._audit("voice_edit.ensure_session_error",
                        {"error": str(exc)[:160]})
            return {"ok": False, "state": "ERROR", "session_id": "",
                    "created": False}

    def _get(self, request_id: str) -> _Request:
        with self._lock:
            self._prune()
            req = self._reqs.get(request_id)
        if req is None:
            raise VoiceEditError("VOICE_EDIT_UNKNOWN_REQUEST",
                                 "unknown or expired request id", status=404)
        return req

    def _ask_cancel_event(self, request_id: str) -> Optional[threading.Event]:
        """The cancel event for an in-flight ask turn (created on first use).

        The orb STOP reaches the helper first; the helper asks the service to
        cancel the turn's ``request_id``. A cancel that arrives BEFORE ``ask``
        registered here still wins: :meth:`cancel` creates and sets the same
        event, so the turn observes it immediately. An empty request id
        returns ``None`` — a direct ``/ask`` with no ``begin`` has no id a
        cancel could name, so behavior stays byte-identical. Never raises.
        """
        rid = str(request_id or "").strip()
        if not rid:
            return None
        try:
            with self._lock:
                self._prune()
                ev = self._ask_cancels.get(rid)
                if ev is None:
                    ev = threading.Event()
                    self._ask_cancels[rid] = ev
                    self._ask_cancel_at[rid] = time.monotonic()
                return ev
        except Exception:  # noqa: BLE001 - cancellation is best-effort
            return threading.Event()

    def _release_ask_cancel(self, request_id: str,
                            ev: Optional[threading.Event]) -> None:
        """Drop this turn's cancel event once the turn has finished."""
        if ev is None:
            return
        rid = str(request_id or "").strip()
        try:
            with self._lock:
                if self._ask_cancels.get(rid) is ev:
                    self._ask_cancels.pop(rid, None)
                    self._ask_cancel_at.pop(rid, None)
        except Exception:  # noqa: BLE001 - bookkeeping only
            pass

    @staticmethod
    def _ask_stopped(ev: Optional[threading.Event]) -> bool:
        """True when this turn's cancel event has been set. Never raises."""
        try:
            return bool(ev is not None and ev.is_set())
        except Exception:  # noqa: BLE001
            return False

    def _ask_cancelled(self, request_id: str, mode: str) -> Dict[str, Any]:
        """Typed outcome for a turn stopped mid-flight (orb STOP).

        Same plain-dict shape as every other refusal (``ok``/``code``/
        ``detail``), so the route returns 400 and the helper renders a clean
        stop instead of an answer it would then speak.
        """
        try:
            self._audit("voice_edit.ask.cancelled", {
                "request_id": request_id, "mode": mode})
        except Exception:  # noqa: BLE001 - audit is additive
            pass
        return {"ok": False, "mode": mode,
                "code": "VOICE_EDIT_ASK_CANCELLED",
                "detail": "the turn was cancelled before it completed"}

    def _prune(self) -> None:
        now = time.monotonic()
        ttl = self.ttl()
        for rid in [r for r, x in self._reqs.items()
                    if now - x.created_at > ttl]:
            self._reqs.pop(rid, None)
        # Unconsumed ask-cancel tombstones (a cancel naming no in-flight
        # ask) share the request TTL so the bookkeeping cannot grow.
        for rid in [r for r, ts in self._ask_cancel_at.items()
                    if now - ts > ttl]:
            self._ask_cancels.pop(rid, None)
            self._ask_cancel_at.pop(rid, None)

    # -- capture / parse --------------------------------------------------
    def capture(self, request_id: str) -> Dict[str, Any]:
        req = self._get(request_id)
        self._require_estop_clear()
        snap = self.desktop.capture_selection(self.cfg)
        req.selection = snap
        req.original = snap.text
        now = self.focused_app(snap)
        if now and not req.target_app:
            req.target_app = now
        self._audit("voice_edit.capture", {
            "request_id": req.request_id,
            "available": snap.available,
            "captured_via": snap.captured_via,
            "chars": snap.char_count,
            "app_class": snap.app_class,
            "protected": snap.protected,
            "clipboard_restored": snap.clipboard_restored})
        return {"ok": True, "selection": snap.public(),
                "target_app": req.target_app}

    @staticmethod
    def focused_app(snap: SelectionSnapshot) -> str:
        return snap.app_class or snap.app_name or ""

    @_counts_turn
    def transform(self, request_id: str, transcript: str, *,
                  privacy_override: str = "",
                  action_override: str = "",
                  style_override: str = "") -> Dict[str, Any]:
        req = self._get(request_id)
        self._require_estop_clear()
        if req.selection is None:
            raise VoiceEditError("VOICE_EDIT_NO_CAPTURE",
                                 "capture the selection before transforming")
        # Strip a leading wake phrase ("Hey Qubit, ...") if the speaker said it
        # while holding Right-Ctrl. A bare wake phrase must never be typed.
        if not action_override:
            try:
                from .wake import WAKE_ALIASES, strip_wake
                phrase = str(_read_cfg(self.cfg, "voice_edit", "wake",
                                       "phrase", default="hey qubit") or
                             "hey qubit")
                stripped = strip_wake(transcript,
                                      aliases=[phrase, *WAKE_ALIASES])
                if stripped is not None:
                    if not stripped.strip():
                        self._audit("voice_edit.noop", {
                            "request_id": req.request_id,
                            "reason": "wake_only"})
                        return {"ok": True, "mode": "ignore",
                                "request_id": req.request_id}
                    transcript = stripped
            except Exception:
                pass
        # Explicit verbatim dictation ("Qubit, type this for me: ..."): insert
        # exactly what was spoken after the command. No model, no egress, no
        # preview; an active selection is replaced by the paste.
        dictated = extract_dictation(transcript)
        if dictated is not None:
            if not dictated.strip():
                return {"ok": True, "mode": "clarify",
                        "question": ("What should I type? Say 'Qubit, type "
                                     "this for me: your text'."),
                        "request_id": req.request_id}
            self._audit("voice_edit.dictate", {
                "request_id": req.request_id, "chars": len(dictated),
                "explicit": True,
                **(_dbg.text_fields(self.cfg, dictated=dictated))})
            return {"ok": True, "mode": "dictate", "transcript": dictated,
                    "request_id": req.request_id}
        # Loose fallback: ASR may garble the exact cue ("type this for me" ->
        # "type is for me"). Strip the cue so the command words are never typed.
        if dictated is None:
            cue = strip_dictation_cue(transcript)
            if cue is not None:
                if cue.strip():
                    self._audit("voice_edit.dictate", {
                        "request_id": req.request_id, "chars": len(cue),
                        "explicit": True, "cue": "loose",
                        **(_dbg.text_fields(self.cfg, dictated=cue))})
                    return {"ok": True, "mode": "dictate",
                            "transcript": cue, "request_id": req.request_id}
                return {"ok": True, "mode": "clarify",
                        "question": ("What should I type? Say 'Qubit, type "
                                     "this for me: your text'."),
                        "request_id": req.request_id}
        if not req.selection.available or not req.original.strip():
            # No selection. By default the helper must NOT type spontaneous
            # speech: only an explicit "type this ..." command types. Anything
            # else is classified here (question -> answer; other -> clarify).
            text_now = (transcript or "").strip()
            if not bool(_read_cfg(self.cfg, "voice_edit", "text_commands",
                                  default=False)):
                from .parser import has_dictation_cue
                if has_dictation_cue(text_now):
                    return {"ok": True, "mode": "dictate",
                            "transcript": text_now,
                            "request_id": req.request_id}
                # Not a dictation command: answer questions, else ask.
                if _looks_like_question(text_now):
                    return {"ok": True, "mode": "answer",
                            "question": text_now,
                            "request_id": req.request_id}
                self._audit("voice_edit.noop", {
                    "request_id": req.request_id, "reason": "no_command",
                    "chars": len(text_now),
                    **(_dbg.text_fields(self.cfg, heard=text_now))})
                return {"ok": True, "mode": "ignore",
                        "request_id": req.request_id}
            cmd = EditCommand(selected_text="", request_id=req.request_id,
                              session_id=req.session_id,
                              target_application=req.target_app,
                              transcript=text_now, action="custom_transform")
            self._audit("voice_edit.dictate", {
                "request_id": req.request_id, "chars": len(text_now)})
            return {"ok": True, "mode": "dictate", "transcript": text_now,
                    "request_id": req.request_id}

        if action_override:
            cmd = EditCommand(
                action=action_override, style=style_override,
                selected_text=req.original, request_id=req.request_id,
                session_id=req.session_id, target_application=req.target_app,
                transcript=transcript)
        else:
            cmd = parse(transcript, selected_text=req.original,
                        request_id=req.request_id, session_id=req.session_id,
                        target_application=req.target_app)

        # Deterministic parsing is the fast path. When it is uncertain, the
        # optional brain router maps free-form speech to a supported action.
        # It can only choose an existing transform action; it never inserts
        # verbatim text and never bypasses the preview/confirmation.
        if cmd.needs_clarification and self._brain_enabled():
            decided = self.brain.classify(
                self.cfg, transcript,
                has_selection=bool((req.original or "").strip()),
                timeout_s=float(_read_cfg(self.cfg, "voice_edit",
                                          "brain_timeout_s", default=4.0)))
            if decided:
                cmd = EditCommand(
                    action=decided["action"], style=decided.get("style", ""),
                    selected_text=req.original, request_id=req.request_id,
                    session_id=req.session_id,
                    target_application=req.target_app, transcript=transcript,
                    router="brain")
        if cmd.needs_clarification:
            fallback = self._default_action_fallback(transcript)
            if not fallback:
                req.command = cmd
                self._audit("voice_edit.clarify", {
                    "request_id": req.request_id,
                    "chars": req.selection.char_count})
                return {"ok": True, "mode": "clarify",
                        "question": cmd.clarification,
                        "request_id": req.request_id,
                        "command": cmd.to_wire()}
            # The router yielded no action and nothing was spoken: run the
            # configured default action instead of asking a question the
            # operator cannot answer with speech alone.
            cmd = EditCommand(
                action=fallback, style=cmd.style,
                selected_text=req.original, request_id=req.request_id,
                session_id=req.session_id,
                target_application=req.target_app, transcript=transcript,
                router="default_action")
            self._audit("voice_edit.default_action", {
                "request_id": req.request_id, "action": fallback})

        # Prefer a per-application action override when the user set one.
        if not action_override:
            from .schema import ACTIONS
            per_app = _read_cfg(self.cfg, "voice_edit", "per_app", default={}) or {}
            if isinstance(per_app, dict) and req.target_app:
                pinned = str(per_app.get(req.target_app, "") or "")
                if pinned in ACTIONS:
                    cmd.action = pinned
        req.command = cmd
        result = self.engine.transform(
            self.cfg, cmd, selected_text=req.original,
            privacy_override=privacy_override)
        self._audit("voice_edit.transform", {
            "request_id": req.request_id,
            "action": cmd.action, "style": cmd.style, "router": cmd.router,
            "chars_in": req.selection.char_count,
            "chars_out": len(result.text or ""),
            "digest": (req.selection.digest or "")[:16],
            "model": result.model, "provider": result.provider,
            "locality": result.locality, "privacy_mode": result.privacy_mode,
            "ok": result.ok, "code": result.code or "",
            "injection_suspected": result.injection_suspected,
            "facts_dropped": len(result.facts_dropped),
            **(_dbg.text_fields(self.cfg, transcript=transcript))})
        if not result.ok:
            return {"ok": False, "mode": "error", "code": result.code,
                    "detail": result.detail, "request_id": req.request_id}
        req.proposed = result.text
        req.result = result
        # Owner ask (2026-10-04): the successful output is copied to the
        # system clipboard at preview time (write-only; the selection itself
        # is untouched — replacement still needs the confirm-gated apply
        # through the safe paste primitive). Loud on failure, never silent.
        _clip = self._copy_transform_to_clipboard(
            result.text, request_id=req.request_id,
            audit_op="voice_edit.transform.clipboard")
        return {
            "ok": True, "mode": "preview", "request_id": req.request_id,
            "original": req.original, "proposed": result.text,
            "command": cmd.to_wire(),
            "detected": {"action": cmd.action, "style": cmd.style,
                         "language": cmd.language, "audience": cmd.audience},
            "model": result.model, "provider": result.provider,
            "locality": result.locality, "privacy_mode": result.privacy_mode,
            "injection_suspected": result.injection_suspected,
            "clarification_returned": result.clarification_returned,
            "facts_dropped": result.facts_dropped,
            "elapsed_ms": result.elapsed_ms,
            "prompt_version": result.prompt_version,
            "selection_digest": req.selection.digest,
            "target_app": req.target_app,
            "clipboard_ok": bool(_clip.get("ok")),
            "clipboard_backend": str(_clip.get("backend", "") or ""),
            "clipboard_code": str(_clip.get("code", "") or ""),
        }

    @_counts_turn
    def route(self, request_id: str, transcript: str) -> Dict[str, Any]:
        """Classify one utterance with the ONE router and dispatch it.

        This is the single decision point the desktop helper and wake monitor
        call. It reuses :class:`~voicebridge.voice_edit.router.VoiceRouter`
        (which itself reuses the shared intent classifier) and never invents a
        second classifier. Dictation and selection transforms delegate to
        :meth:`transform` unchanged; questions/chat are answered by the brain
        (never typed); actionable requests become a confirm-gated task.
        """
        req = self._get(request_id)
        self._require_estop_clear()
        # Voice-final repair lane (2026-10-04): waveform commands ("show /
        # hide waveform" + owner shapes "waveform on/off", "turn on/off",
        # "enable/disable" — see _WAVEFORM_SHOW_RE) must reach ask() (which flips the setting deterministically)
        # instead of the intent classifier — the classifier sends "show
        # waveform" to task/coding (confirm-gated proposal, never flips).
        # Parsed before the route words below; ask() owns the flip + speak.
        # In-game override (owner 2026-10-04) first: "show waveform ingame"
        # also substring-matches the bare widget regex, so the qualified
        # command must win here (ask() applies the same order).
        if self.waveform_ingame_command(transcript) is not None:
            self._audit("voice_edit.router.waveform_override", {
                "request_id": req.request_id, "mode": "answer",
                "reason": "waveform-ingame",
                "intent": "waveform-ingame", "escalate": False,
                "has_selection": False})
            return {"ok": True, "mode": "answer",
                    "question": (transcript or "").strip(),
                    "escalate": False,
                    "decision": {"mode": "answer",
                                 "reason": "waveform-ingame",
                                 "intent": "waveform-ingame",
                                 "escalate": False},
                    "request_id": req.request_id}
        if self.waveform_command(transcript) is not None:
            self._audit("voice_edit.router.waveform_override", {
                "request_id": req.request_id, "mode": "answer",
                "reason": "waveform-visible",
                "intent": "waveform-visible", "escalate": False,
                "has_selection": False})
            return {"ok": True, "mode": "answer",
                    "question": (transcript or "").strip(),
                    "escalate": False,
                    "decision": {"mode": "answer",
                                 "reason": "waveform-visible",
                                 "intent": "waveform-visible",
                                 "escalate": False},
                    "request_id": req.request_id}
        # Slice 1 route words: "in chat:" must reach ask() (which dispatches
        # the headless /chat worker directly) instead of the voice-level
        # task-confirm staging, and "quick:" must reach ask() for a direct
        # brain-only answer. Both map to answer mode with the original text;
        # ask() strips the prefix. Unprefixed text keeps the router path.
        try:
            from ..chat_transport import parse_route_word as _parse_rw
            _rw = _parse_rw(transcript)
        except Exception:
            _rw = None
        if _rw is not None:
            self._audit("voice_edit.router", {
                "request_id": req.request_id, "mode": "answer",
                "reason": f"route-word:{_rw['route']}",
                "intent": "route-word", "escalate": False,
                "has_selection": False})
            return {"ok": True, "mode": "answer",
                    "question": (transcript or "").strip(),
                    "escalate": False,
                    "decision": {"mode": "answer",
                                 "reason": f"route-word:{_rw['route']}",
                                 "intent": "route-word", "escalate": False},
                    "request_id": req.request_id}
        has_selection = bool(
            req.selection is not None and req.selection.available
            and (req.original or "").strip())
        # Lane 2 (2026-10-03): transform-shaped speech with no selection
        # ("Redo this text for me, ..." carries its own trailing text) must
        # not reach the intent classifier — it called the journal sentence
        # ``coding`` and staged a nonsense web.search. Steer it to the
        # answer path so the helper asks (which transforms directly); the
        # router still decides everything else first.
        if not has_selection:
            try:
                _pre = self._transform_pre_route(transcript)
            except Exception:
                _pre = None
            if _pre is not None:
                self._audit("voice_edit.router.transform_override", {
                    "request_id": req.request_id, "mode": "answer",
                    "reason": "transform-pre-route",
                    "intent": "transform-pre-route", "escalate": False,
                    "has_selection": False})
                decision_wire = {
                    "mode": "answer", "reason": "transform-pre-route",
                    "intent": "transform-pre-route", "escalate": False}
                return {"ok": True, "mode": "answer",
                        "question": (transcript or "").strip(),
                        "escalate": False,
                        "decision": decision_wire,
                        "request_id": req.request_id}
        from .router import VoiceRouter
        router = self.router or VoiceRouter(self.cfg)
        decision = router.decide(
            transcript, has_selection=has_selection)
        decision_wire = {
            "mode": decision.mode, "reason": decision.reason,
            "intent": decision.intent, "escalate": decision.escalate}
        self._audit("voice_edit.router", {
            "request_id": req.request_id, "mode": decision.mode,
            "reason": decision.reason, "intent": decision.intent,
            "escalate": decision.escalate,
            "has_selection": has_selection,
            **(_dbg.text_fields(self.cfg, transcript=transcript))})
        if decision.mode in ("dictate", "transform"):
            if req.selection is None:
                # The wake path never captures by design (service-side
                # Ctrl+C into the focused window is SIGINT in a terminal),
                # but transform() gates on a capture. Seed the same
                # explicit unavailable snapshot the helper builds locally
                # so dictate — which carries its own payload and never
                # needs a selection — executes, while selection transforms
                # degrade to clarify instead of raising NO_CAPTURE.
                # Right-Ctrl always arrives with a capture; untouched.
                from .schema import SelectionSnapshot
                req.selection = SelectionSnapshot(
                    available=False, captured_via="wake_no_capture",
                    reason=("wake path never captures "
                            "(no Ctrl+C into focused window)"))
            out = self.transform(request_id, transcript)
            out["decision"] = decision_wire
            return out
        if decision.mode in ("answer", "chat"):
            # Lane-3 net (2026-10-03): the intent classifier misses
            # tool-shaped utterances ("Set THE timer for one minute." reached
            # ask() as conversation and the brain narrated an unsettleable
            # "Confirm?" — journal 04:53 UTC). A deterministic local-tool hit
            # stages through propose_task instead, so the helper speaks the
            # staged confirmation and re-arms for voice "yes". The router
            # still decides first; this only repairs its misses, and reply
            # drafts / briefings / direct facts keep their own paths (the
            # guard inside _tool_shaped_proposal + ask() ordering).
            if self._tool_shaped_proposal(transcript) is not None:
                self._audit("voice_edit.router.tool_override", {
                    "request_id": req.request_id,
                    "from_mode": decision.mode,
                    "reason": decision.reason,
                    "chars": len((transcript or "").strip()),
                    **(_dbg.text_fields(self.cfg, transcript=transcript))})
                task = self.propose_task(request_id, transcript)
                task["decision"] = decision_wire
                return task
            # The helper answers both through :meth:`ask` (spoken, never typed);
            # this branch only carries the classification back.
            return {"ok": True, "mode": decision.mode,
                    "question": (transcript or "").strip(),
                    "escalate": decision.escalate,
                    "decision": decision_wire,
                    "request_id": req.request_id}
        if decision.mode == "task":
            task = self.propose_task(request_id, transcript)
            task["decision"] = decision_wire
            return task
        self._audit("voice_edit.noop", {
            "request_id": req.request_id, "reason": decision.reason,
            "chars": len((transcript or "").strip()),
            **(_dbg.text_fields(self.cfg, heard=transcript))})
        return {"ok": True, "mode": "ignore", "decision": decision_wire,
                "request_id": req.request_id}

    def edit(self, request_id: str, text: str) -> Dict[str, Any]:
        req = self._get(request_id)
        if not (req.proposed or req.result):
            raise VoiceEditError("VOICE_EDIT_NO_PREVIEW",
                                 "there is no preview to edit")
        req.proposed = str(text or "")
        self._audit("voice_edit.edit", {"request_id": req.request_id,
                                        "chars": len(req.proposed)})
        return {"ok": True, "request_id": req.request_id, "proposed": req.proposed}

    def retry(self, request_id: str, transcript: str = "",
              *, privacy_override: str = "") -> Dict[str, Any]:
        req = self._get(request_id)
        if req.selection is None:
            raise VoiceEditError("VOICE_EDIT_NO_CAPTURE", "nothing to retry")
        spoken = transcript or (req.command.transcript if req.command else "")
        return self.transform(request_id, spoken,
                              privacy_override=privacy_override)

    # -- apply / revert ---------------------------------------------------
    def apply(self, request_id: str, *, confirm: bool = False,
              method: str = "") -> Dict[str, Any]:
        req = self._get(request_id)
        self._require_estop_clear()
        if req.selection is None or not req.original.strip():
            raise VoiceEditError("VOICE_EDIT_NOTHING_SELECTED",
                                 "no selection was captured to replace")
        if not req.proposed:
            raise VoiceEditError("VOICE_EDIT_NO_PREVIEW",
                                 "run a transformation before applying")
        if self._block_fullscreen() and \
                self.desktop.is_fullscreen(req.selection.window_id):
            raise VoiceEditError(
                "VOICE_EDIT_FULLSCREEN_TARGET",
                "the focused window is fullscreen; insertion refused "
                "(set voice_edit.block_fullscreen=false to allow)", status=409)
        require_confirm = bool(_read_cfg(
            self.cfg, "voice_edit", "confirm_before_replace", default=True))
        if require_confirm and not confirm:
            raise VoiceEditError(
                "VOICE_EDIT_CONFIRM_REQUIRED",
                "replacement needs explicit confirmation", status=409)

        # Stale-selection protection: re-capture and compare the digest before
        # touching the desktop. A changed selection is a typed refusal unless the
        # caller confirms; the original text is left untouched.
        current = self.desktop.capture_selection(self.cfg)
        if not current.available:
            raise VoiceEditError(
                "VOICE_EDIT_STALE_SELECTION",
                "the selection could not be re-read; reselect and try again",
                status=409)
        if current.digest != req.selection.digest:
            if not confirm:
                raise VoiceEditError(
                    "VOICE_EDIT_STALE_SELECTION",
                    "the selection changed while processing; reselect or "
                    "confirm to replace the current selection", status=409)
            req.selection = current
            req.original = current.text
        ins = self.desktop.insert_text(req.proposed, req.selection, self.cfg,
                                       method=method)
        if ins.ok:
            req.applied = True
            req.applied_text = req.proposed
        self._audit("voice_edit.apply", {
            "request_id": req.request_id, "ok": ins.ok, "code": ins.code or "",
            "method": ins.method, "app_class": ins.app_class,
            "chars": len(req.proposed),
            "clipboard_restored": ins.clipboard_restored,
            "focus_restored": ins.focus_restored})
        if not ins.ok:
            return {"ok": False, "mode": "error", "code": ins.code,
                    "detail": ins.detail, "reason": ins.reason,
                    "request_id": req.request_id, "insertion": ins.public()}
        # Owner ask (2026-10-04): the applied redo result stays in the system
        # clipboard (``insert_text`` restores the user's original clipboard,
        # so re-copy the final text here — write-only, selection already
        # replaced through the safe paste primitive). Loud on failure.
        _applied_clip = self._copy_transform_to_clipboard(
            req.proposed, request_id=req.request_id,
            audit_op="voice_edit.apply.clipboard")
        return {"ok": True, "mode": "applied", "request_id": req.request_id,
                "insertion": ins.public(), "applied": req.proposed,
                "clipboard_ok": bool(_applied_clip.get("ok")),
                "clipboard_backend": str(_applied_clip.get("backend", "") or ""),
                "clipboard_code": str(_applied_clip.get("code", "") or "")}

    def revert(self, request_id: str) -> Dict[str, Any]:
        req = self._get(request_id)
        self._require_estop_clear()
        if not req.applied:
            raise VoiceEditError("VOICE_EDIT_NOTHING_APPLIED",
                                 "nothing has been applied to revert")
        ins = self.desktop.revert_last(req.selection, self.cfg)
        if ins.ok:
            req.applied = False
        self._audit("voice_edit.revert", {
            "request_id": req.request_id, "ok": ins.ok, "code": ins.code or ""})
        if not ins.ok:
            return {"ok": False, "mode": "error", "code": ins.code,
                    "detail": ins.detail, "reason": ins.reason,
                    "request_id": req.request_id, "insertion": ins.public()}
        return {"ok": True, "mode": "reverted", "request_id": req.request_id,
                "insertion": ins.public()}

    def cancel(self, request_id: str) -> Dict[str, Any]:
        # A cancel is never a no-op for audio (2026-10-19): the helper owns
        # the spoken reply, so best-effort stop it before dropping the
        # staged request. Junk-safe and signature-stable — the request
        # bookkeeping below is unchanged.
        _signal_helper_stop()
        # Orb STOP / explicit cancel of an in-flight ask turn: flip the turn's
        # cancel event BEFORE the staged-request bookkeeping. A tombstone is
        # created when no ask has registered yet, so a cancel that arrives
        # early still wins (ask picks up the already-set event). The service
        # then skips/aborts the model or agent call and returns no reply for it.
        rid = str(request_id or "")
        if rid:
            try:
                with self._lock:
                    self._prune()
                    _ev = self._ask_cancels.get(rid)
                    if _ev is None:
                        _ev = threading.Event()
                        self._ask_cancels[rid] = _ev
                        self._ask_cancel_at[rid] = time.monotonic()
                    _ev.set()
            except Exception:  # noqa: BLE001 - cancellation is best-effort
                pass
        with self._lock:
            req = self._reqs.pop(request_id, None)
        if req is None:
            return {"ok": True, "cancelled": False, "detail": "unknown request"}
        self._audit("voice_edit.cancel", {"request_id": request_id,
                                          "applied": bool(req.applied)})
        return {"ok": True, "cancelled": True,
                "discarded_interim_only": not req.applied,
                "request_id": request_id}

    _OLLAMA_TAGS: Dict[str, Any] = {}

    def _ollama_installed(self) -> set:
        """Names of locally installed Ollama models (cached ~30s)."""
        import json
        import time as _t
        import urllib.request
        now = _t.monotonic()
        cached = self._OLLAMA_TAGS.get("v")
        if cached and now - cached[0] < 30:
            return cached[1]
        try:
            from ..runner_contract import resolve as _rc
            base = (_rc(self.cfg).url or "http://127.0.0.1:11434").rstrip("/")
            with urllib.request.urlopen(base + "/api/tags", timeout=3) as r:
                names = {str(m.get("name", "")) for m in
                         (json.loads(r.read().decode()).get("models") or [])}
        except Exception:
            names = set()
        if names:
            self._OLLAMA_TAGS["v"] = (now, names)
        return names

    def _select_model(self, configured: Any) -> str:
        """Resolve a spoken-answer model, portably.

        ``""`` or ``"auto"`` -> the machine's configured local model (the
        provider resolver). A configured name is used only when it is actually
        installed; otherwise we fall back so a weaker/stronger PC, or a missing
        model, still works with no code change.
        """
        name = str(configured or "").strip()
        if not name or name.lower() == "auto":
            return ""
        installed = self._ollama_installed()
        if not installed:
            return name  # cannot verify; let the runner try it
        if name in installed or any(i.split(":")[0] == name for i in installed):
            return name
        return ""  # configured but not installed -> portable fallback

    def _style_for(self, question: str) -> "tuple[int, str, str]":
        """(max_tokens, reasoning, style_instruction) for a spoken answer.

        Short, simple questions get one natural sentence; an explicit ask for
        detail gets room for a fuller answer. The cue list is
        ``voice_edit.detail_cues`` (optional) plus a sensible default; the
        instruction also tells the model to vary its wording.
        """
        import re
        cues = _read_cfg(self.cfg, "voice_edit", "detail_cues", default=None)
        extra = ""
        if isinstance(cues, (list, tuple)) and cues:
            extra = "|".join(re.escape(str(c).strip()) for c in cues
                             if str(c).strip())
        pattern = (
            r"\b(explain|in detail|details?|report|why|how (do|does|to)|steps?|"
            r"describe|elaborate|compare|summari[sz]e|walk me through|"
            r"tell me (more|about)|pros and cons|breakdown|"
            # creative asks need the stronger model (the fast small one is weak)
            r"joke|story|poem|riddle|funny|laugh|rhyme|invent|imagine|"
            r"roleplay|pretend|write me|compose"
            + (f"|{extra}" if extra else "") + r")\b")
        detail = bool(re.search(pattern, question or "", re.I))
        if detail:
            return 500, "medium", (
                "Answer in a clear, well-structured way with the key points. "
                "Vary your phrasing each turn; don't reuse a stock opener/closer.")
        return 80, "minimal", (
            "Answer in ONE short, natural sentence — say only what's needed. "
            "Vary your wording every turn; never repeat the same opener or "
            "closer (e.g. don't end every reply with 'anything else').")

    def _personal_context(self) -> str:
        """The operator's canonical personal knowledge (bounded).

        Grounds a spoken answer so the model never invents facts about the
        user (the local system prompt says not to, but it needs the facts).
        Reads the same ``USER.md`` / ``MEMORY.md`` the orchestrator's context
        compiler feeds the local chat model.
        """
        try:
            from pathlib import Path
            root = Path(self.cfg.state_dir())
            for base in (root / "runtime" / "memories", root / "memories"):
                if not base.is_dir():
                    continue
                parts = []
                # Bound the per-turn prompt: identity facts (USER.md) plus a
                # short MEMORY.md slice — enough to ground the answer without
                # paying prompt-processing latency for the whole file.
                for name, cap in (("USER.md", 1500), ("MEMORY.md", 800)):
                    f = base / name
                    if f.is_file():
                        parts.append(f.read_text(
                            encoding="utf-8", errors="replace")[:cap])
                if parts:
                    return "\n\n".join(parts)
        except Exception:  # noqa: BLE001 - additive, never fatal
            pass
        return ""

    def _voice_session(self, request_id: str = "") -> str:
        """The session id for the spoken conversation.

        Delegates to :mod:`voicebridge.voice_edit.voice_sessions` so the
        configured mode applies: ``single`` (default) returns one stable
        ``voice-chat`` session so the voice dialogue stays coherent instead of
        overlaying ``/chat``/dashboard sessions; ``per_command`` derives a
        deterministic ``voice-<stamp>-<hash>`` id per request. Never raises.
        """
        try:
            return voice_sessions.voice_session_id(
                self.cfg, request_id=request_id or None)
        except Exception:  # noqa: BLE001 - always a usable voice id
            return voice_sessions.SINGLE_SESSION_ID

    def _maybe_prune_voice_sessions(self, request_id: str = "") -> None:
        """Opportunistically prune old voice sessions (throttled, best-effort).

        Runs on a real turn so retention does not need a scheduler. Throttled
        to :data:`_VOICE_PRUNE_INTERVAL_S`; never raises, never blocks a turn.
        The session the in-flight turn is using is always protected, so
        retention can never delete the transcript being read/written (and the
        stable ``voice-chat`` session is protected unconditionally by
        :func:`voicebridge.voice_edit.voice_sessions.prune`).
        """
        try:
            now = time.monotonic()
            last = float(getattr(self, "_voice_pruned_at", 0.0) or 0.0)
            if last and (now - last) < _VOICE_PRUNE_INTERVAL_S:
                return
            self._voice_pruned_at = now
            active = self._request_session(request_id) or self._voice_session(
                request_id)
            voice_sessions.prune(self.cfg, store=self._session_store,
                                 protect=[active])
        except Exception:  # noqa: BLE001 - retention is non-essential
            pass

    def _request_session(self, request_id: str) -> str:
        """The routed session key bound to this request, else "".

        The wake-routing path binds the target's ``sessionKey`` on the request
        at :meth:`begin` (``desktop_helper._open_request`` passes
        ``session_id=route_session``); ask/task read it back here so a routed
        wake command runs its full-turn/tool dispatch on that session instead
        of the default voice session. An empty/absent/expired request — or a
        malformed (non-string, un-stringable) id — returns "" so callers keep
        today's byte-identical default. Never raises.
        """
        rid = str(request_id or "")
        if not rid:
            return ""
        try:
            with self._lock:
                req = self._reqs.get(rid)
            if req is None:
                return ""
            return str(getattr(req, "session_id", "") or "")
        except Exception:  # noqa: BLE001 - routing is additive, never fatal
            return ""

    def _conversation_context(self, pending: str, *,
                              request_id: str = "") -> str:
        """Recency-weighted prior-turn context from the ONE canonical store.

        The most recent turns are rendered verbatim and LAST, with an explicit
        "the last user line is the current topic / resolve pronouns to it"
        directive; older turns are folded into a short background gist. This
        keeps a follow-up ("and what is it known for?") anchored to the current
        subject instead of an older, louder topic.
        """
        try:
            store = self._canonical_store()
            if store is None:
                return ""
            msgs = [m for m in
                    store.messages(self._voice_session(request_id), limit=200)
                    .get("messages", [])
                    if isinstance(m, dict)
                    and str(m.get("content") or "").strip()]
            if not msgs:
                return ""
            try:
                recent_n = int(_read_cfg(self.cfg, "voice_edit",
                                         "voice_recent_turns",
                                         default=4) or 4)
            except (TypeError, ValueError):
                recent_n = 4
            recent_n = max(2, min(recent_n, 40))
            recent = msgs[-recent_n:]
            older = msgs[:-recent_n]
            parts: list = []
            if older:
                gist = " | ".join(
                    f"{'U' if m.get('role') == 'user' else 'Q'}: "
                    f"{' '.join(str(m.get('content', '')).split())[:70]}"
                    for m in older[-12:])
                parts.append("[earlier background — NOT the current topic]\n"
                             + gist)
            parts.append("[most recent turns — the LAST user line is the "
                         "CURRENT topic; resolve 'it'/'that' to it]")
            for m in recent:
                role = "User" if m.get("role") == "user" else "Qubit"
                txt = " ".join(str(m.get("content", "")).split())[:800]
                parts.append(f"{role}: {txt}")
            return "\n".join(parts)
        except Exception:  # noqa: BLE001 - context is additive, non-fatal
            return ""

    def _known_model_profile(self, profile_id: str) -> bool:
        """True when ``profile_id`` names a real configured model profile.

        ``run_role(profile_id=...)`` expects a provider/model profile, not a
        Qubit CLI profile; handing it an unknown id fails the whole turn, so an
        unconfigured target degrades to the local route (audited).
        """
        try:
            from ..providers import resolver as _resolver
            return _resolver.get_profile(self.cfg, str(profile_id)) is not None
        except Exception:
            return False

    def _escalate_for(self, mode: str) -> tuple[bool, str]:
        """The recorded local-vs-API decision for a non-edit turn.

        Local is the default. ``routing=api`` escalates; ``routing=auto``
        escalates only a task. Missing/denied API config degrades to local.
        Never applies to dictate/transform (handled by the transform engine).
        """
        if mode not in ("task", "chat", "answer"):
            return False, "not_escalatable"
        if not bool(_read_cfg(self.cfg, "voice_edit", "allow_api", default=True)):
            return False, "allow_api=false"
        routing = str(_read_cfg(self.cfg, "voice_edit", "routing",
                                default="auto") or "auto").lower()
        if routing == "api" or (routing == "auto" and mode == "task"):
            return True, f"routing={routing}"
        return False, f"routing={routing}"

    def _local_profile(self) -> str:
        """Configured local model profile for non-escalated turns."""
        return str(_read_cfg(self.cfg, "voice_edit", "local_profile",
                             default="qubit-brain4") or "qubit-brain4")

    # -- sleep mode (Slice 4a) -------------------------------------------
    def sleeping(self) -> bool:
        """True while manual sleep mode is on (unprompted speech gated)."""
        return bool(_read_cfg(self.cfg, "voice_edit", "sleep_mode",
                              default=False))

    def set_sleep_mode(self, on: bool) -> bool:
        """Persist sleep mode through the canonical settings writer.

        Uses ``settings.update_voice_edit`` (atomic write + live-cfg sync);
        on any persist fault the live data is mutated directly so the gate
        still holds for this process, and the fault is audited.
        """
        on = bool(on)
        try:
            from .settings import update_voice_edit
            update_voice_edit(self.cfg, {"sleep_mode": on})
        except Exception as exc:  # noqa: BLE001 - gate must hold anyway
            try:
                data = getattr(self.cfg, "data", None)
                if isinstance(data, dict):
                    ve = data.setdefault("voice_edit", {})
                    if isinstance(ve, dict):
                        ve["sleep_mode"] = on
                    else:
                        data["voice_edit"] = {"sleep_mode": on}
            except Exception:
                pass
            self._audit("voice_edit.sleep.persist_fallback",
                        {"sleep_mode": on, "error": str(exc)[:160]})
        return on

    def should_suppress_unprompted(self) -> bool:
        """True when completions/briefings/proactive turns must stay silent.

        Slice-4a contract for future briefing/completion code: sleep mode
        gates (b) unprompted speech, never (a) on-request answers (owner
        decision §5 #2). Direct ``ask()``/``task()`` turns never consult
        this — they always run.
        """
        return self.sleeping()

    def may_speak_unprompted(self) -> bool:
        """Inverse of :meth:`should_suppress_unprompted` (readability)."""
        return not self.sleeping()

    @staticmethod
    def is_sleep_command(text: str) -> bool:
        """True when the utterance asks for sleep mode."""
        return bool(_SLEEP_COMMAND_RE.search(text or ""))

    # -- dashboard waveform visibility --------------------------------------
    def waveform_visible(self) -> bool:
        """True while the dashboard waveform widget is visible."""
        # Default False (owner 2026-10-04): absent key means HIDDEN.
        return bool(_read_cfg(self.cfg, "voice_edit", "waveform_visible",
                              default=False))

    def set_waveform_visible(self, on: bool) -> bool:
        """Persist waveform visibility through the canonical settings writer.

        Uses ``settings.update_voice_edit`` (atomic write + live-cfg sync);
        on any persist fault the live data is mutated directly so the gate
        still holds for this process, and the fault is audited.
        """
        on = bool(on)
        try:
            from .settings import update_voice_edit
            update_voice_edit(self.cfg, {"waveform_visible": on})
        except Exception as exc:  # noqa: BLE001 - gate must hold anyway
            try:
                data = getattr(self.cfg, "data", None)
                if isinstance(data, dict):
                    ve = data.setdefault("voice_edit", {})
                    if isinstance(ve, dict):
                        ve["waveform_visible"] = on
                    else:
                        data["voice_edit"] = {"waveform_visible": on}
            except Exception:
                pass
            self._audit("voice_edit.waveform.persist_fallback",
                        {"waveform_visible": on, "error": str(exc)[:160]})
        return on

    @staticmethod
    def waveform_command(text: str) -> Optional[str]:
        """"show" / "hide" when the utterance flips waveform visibility.

        Accepts verb-first "show/hide (the) waveform", "turn on/off (the)
        waveform", "enable/disable (the) waveform", and end-anchored
        noun-first "waveform on/off" (owner 2026-10-04) — each with the
        spaced two-word "wave form" shape STT emits (journal 2026-10-04
        "Show Wave Form."). None otherwise —
        in particular "show me the waveform settings" (verb not adjacent
        to "waveform") and "waveform on the screen" (noun-first shape not
        utterance-final) never match.
        Sweep lane (2026-10-05): any utterance about settings ("show
        waveform in game settings", "open the waveform settings") is a
        settings question, never a visibility flip — the old substring
        match flipped the widget on the settings question (§84).
        """
        t = text or ""
        if _re_module.search(r"\bsettings?\b", t, _re_module.IGNORECASE):
            return None
        if _WAVEFORM_SHOW_RE.search(t):
            return "show"
        if _WAVEFORM_HIDE_RE.search(t):
            return "hide"
        return None

    # -- in-game orb override -------------------------------------------------
    def waveform_ingame(self) -> bool:
        """True while the orb stays mapped over fullscreen games."""
        # Default False (owner 2026-10-04): absent key means auto-hide.
        return bool(_read_cfg(self.cfg, "voice_edit", "waveform_ingame",
                              default=False))

    def set_waveform_ingame(self, on: bool) -> bool:
        """Persist the in-game override through the canonical settings writer.

        Same shape as :meth:`set_waveform_visible` (atomic write + live-cfg
        sync; persist-fault fallback mutates live data so the gate still
        holds for this process, and the fault is audited distinctly).
        """
        on = bool(on)
        try:
            from .settings import update_voice_edit
            update_voice_edit(self.cfg, {"waveform_ingame": on})
        except Exception as exc:  # noqa: BLE001 - gate must hold anyway
            try:
                data = getattr(self.cfg, "data", None)
                if isinstance(data, dict):
                    ve = data.setdefault("voice_edit", {})
                    if isinstance(ve, dict):
                        ve["waveform_ingame"] = on
                    else:
                        data["voice_edit"] = {"waveform_ingame": on}
            except Exception:
                pass
            self._audit("voice_edit.waveform_ingame.persist_fallback",
                        {"waveform_ingame": on, "error": str(exc)[:160]})
        return on

    @staticmethod
    def waveform_ingame_command(text: str) -> Optional[str]:
        """"show" / "hide" when the utterance flips the in-game override.

        Accepts the same owner-natural shapes as :meth:`waveform_command`
        (show/hide, turn on/off, enable/disable, noun-first on/off — each
        with the spaced "wave form" variant), each requiring the
        end-anchored ingame qualifier ("ingame" / "in-game" / "in game",
        optional trailing punctuation).

        None otherwise — in particular bare "show/hide waveform" (no ingame
        qualifier: the dashboard widget's command) and "show waveform in
        game settings" (qualifier not utterance-final: a settings question,
        not a command) never match.
        """
        t = text or ""
        if _WAVEFORM_INGAME_SHOW_RE.search(t):
            return "show"
        if _WAVEFORM_INGAME_HIDE_RE.search(t):
            return "hide"
        return None

    def _is_directed_wake(self, text: str) -> bool:
        """True when the utterance starts with a wake phrase ("hey qubit")."""
        try:
            from .wake import strip_wake
            return strip_wake(text or "") is not None
        except Exception:
            return "hey qubit" in (text or "").lower()

    # -- briefings (Slice 4b) ------------------------------------------------
    # Owner decisions 2026-10-03 §5 #2: briefings are BOTH (a) on-request
    # ("brief me") AND (b) unprompted spoken completions ("report's ready").
    # Sleep mode gates (b), never (a): brief_me() never consults sleep;
    # speak_completion() consults may_speak_unprompted() and stays silent
    # in sleep. Neither path uses a model (deterministic, local-first).
    @staticmethod
    def is_briefing_request(text: str) -> bool:
        """True when the utterance asks for a spoken briefing.

        Sweep lane (2026-10-05): an explicit brevity instruction ("be
        brief", "please be brief.") is a style constraint on the NEXT
        answer, never a request to hear the briefing — without this guard
        it spoke a full briefing. The pinned mishear shapes ("brief",
        "breathe.", "Breathe me.", "Brave me.") contain no "be brief"
        and are unaffected.
        """
        t = text or ""
        if _re_module.search(r"\bbe\s+brief\b", t, _re_module.IGNORECASE):
            return False
        return bool(_BRIEF_ME_RE.search(t))

    @staticmethod
    def is_digest_request(text: str) -> bool:
        """True when the utterance asks for the "while away" digest."""
        return bool(_DIGEST_REQUEST_RE.search(text or ""))

    @staticmethod
    def is_repeat_request(text: str) -> bool:
        """True when the utterance asks to hear the last answer again."""
        return bool(_REPEAT_RE.search(text or ""))

    @staticmethod
    def is_report_request(text: str) -> bool:
        """True when the utterance asks for a report on a chat session.

        Conservative by design (see :data:`_REPORT_CHAT_RE`): a report-ish
        verb adjacent to a chat/session/conversation noun, or an explicit
        "what did we do / what happened" frame carrying that noun. Ordinary
        questions, a document summarize, mail/YouTube asks, and defect
        reports ("report a bug/issue/problem", see :data:`_REPORT_DEFECT_RE`)
        never match.
        """
        raw = text or ""
        if _REPORT_DEFECT_RE.search(raw):
            return False
        return bool(_REPORT_CHAT_RE.search(raw))

    def _repeat_last_answer(self) -> "tuple[str, str]":
        """The last spoken answer, verbatim, or ``("", "")`` to fall through.

        Reads the ONE canonical store's voice session (same read
        ``_conversation_context`` uses) and returns the newest non-empty
        assistant message. Pure read: nothing is written, so a repeat never
        duplicates the transcript. Never raises; an empty history falls
        through to the brain/full-turn path byte-identical.
        """
        try:
            store = self._canonical_store()
            if store is None:
                return "", ""
            msgs = [m for m in
                    store.messages(self._voice_session(), limit=50)
                    .get("messages", [])
                    if isinstance(m, dict)
                    and str(m.get("role") or "") == "assistant"
                    and str(m.get("content") or "").strip()]
            if not msgs:
                return "", ""
            return str(msgs[-1].get("content") or "").strip(), "repeat"
        except Exception:
            return "", ""

    def _tool_shaped_proposal(self, text: str) -> Optional[Dict[str, Any]]:
        """Deterministic local tool for ``text``, or None (Lane-3 net).

        Coordinates via ``tool_task.propose_tool`` (the SAME resolver the
        confirmed task path executes) — never a second classifier, never the
        model. Only ``local.*`` tools stage here: ``web.*`` intents stay on
        the answer path (direct facts / skills / full turn own them), and
        reply-shaped text is excluded (``parse_reply`` is pure parsing, no
        I/O) so drafts keep their skill-social staging. Pure + bounded:
        regex matching over config only, no subprocess, no network.
        """
        clean = (text or "").strip()
        if not clean:
            return None
        try:
            from . import send as _S
            if _S.parse_reply(clean) is not None:
                return None
        except Exception:
            pass
        try:
            from .tool_task import propose_tool as _propose
            proposal = _propose(self.cfg, clean)
        except Exception:
            return None
        if not proposal:
            return None
        if not str(proposal.get("tool") or "").startswith("local."):
            return None
        # Deterministic instant answers win over staging: a clock question
        # ("what time is it" also resolves to local.time_date) must answer
        # immediately, never ask "say yes to hear the time". Weather-shaped
        # text never reaches here with a local hit (propose_tool yields no
        # local tool for it), so this guard costs at most a strftime.
        try:
            if self._direct_fact_answer(clean):
                return None
        except Exception:
            pass
        return proposal

    def _transform_pre_route(self, text: str) -> Any:
        """Deterministic transform claim on one utterance, or None.

        Lane 2 (2026-10-03, journal test-2 09:52:38): "Redo this text for
        me, ..." classified as ``intent:coding`` and ran ``web.search`` on
        the whole sentence. The deterministic edit parser already knows
        every transform trigger (exact rewrite phrases plus the loose
        ``redo <anything> for me`` fallback) — reuse it here (read-only;
        the parser module itself is untouched) so transform-shaped speech
        reaches TRANSFORM with selection-or-trailing-text, never a worker.
        A trailing payload after the trigger (the redo suffix) becomes the
        transform input; dictation wins elsewhere and is excluded here.
        Pure: no model, no I/O, never raises.
        """
        clean = (text or "").strip()
        if not clean:
            return None
        try:
            from .parser import parse
            cmd = parse(clean, selected_text="")
        except Exception:
            return None
        if getattr(cmd, "needs_clarification", True):
            return None
        action = str(getattr(cmd, "action", "") or "")
        if not action or action == "dictate_text":
            return None
        try:
            from .schema import ACTIONS
            if action not in ACTIONS:
                return None
        except Exception:
            return None
        return cmd

    def _ask_transform_answer(self, cmd: Any, question: str, *,
                               request_id: str = "",
                               mode: str = "answer") -> Optional[Dict[str, Any]]:
        """Run a pre-routed transform command as a spoken answer.

        No worker, no confirm theater: the transform engine (detail tier)
        rewrites the selection-or-trailing-text and the rewrite is spoken
        + remembered like any direct answer. An engine FAULT still falls
        through (None) so the caller keeps today's path — and so does a
        non-ok engine RESULT: the pre-router claims bare triggers too
        ("summarize this for me" parses as summarize with no input), and
        with nothing to transform the engine fails while the brain, the
        escalation target, or a skill (YouTube) could still answer. The
        failure is audited (voice_edit.ask.transform_failed) but never
        terminal. Never raises.
        """
        try:
            selected = str(getattr(cmd, "selected_text", "") or "")
            res = self.engine.transform(
                self.cfg, cmd, selected_text=selected)
        except Exception:
            return None
        if not getattr(res, "ok", False):
            code = str(getattr(res, "code", "") or "VOICE_EDIT_TRANSFORM_FAILED")
            self._audit("voice_edit.ask.transform_failed", {
                "request_id": request_id, "mode": mode,
                "action": str(getattr(cmd, "action", "") or ""),
                "code": code,
                "chars": len(question)})
            # Fall through to the normal ask pipeline (direct facts, skills,
            # brain) — a transform with no input is not an answer, and the
            # failure detail is audit-only, never spoken.
            return None
        answer = str(getattr(res, "text", "") or "").strip()
        if not answer:
            self._audit("voice_edit.ask.transform_failed", {
                "request_id": request_id, "mode": mode,
                "action": str(getattr(cmd, "action", "") or ""),
                "code": "VOICE_EDIT_TRANSFORM_EMPTY",
                "chars": len(question)})
            return {"ok": False, "code": "VOICE_EDIT_TRANSFORM_EMPTY",
                    "detail": "the rewrite came back empty; nothing was done",
                    "mode": mode, "request_id": request_id}
        self._audit("voice_edit.ask", {
            "request_id": request_id, "mode": mode,
            "chars": len(question), "ok": True,
            "answer_chars": len(answer), "provider": "local",
            "model": "transform"})
        self._remember_ask_exchange(question, answer, model="transform")
        # Owner ask (2026-10-04): the redo result lands in the system
        # clipboard (write-only — safe on the selection-less wake path; no
        # capture, no keys, no replacement-in-place). The answer itself is
        # returned unchanged; the helper speaks the short "copied" receipt.
        clip = self._copy_transform_to_clipboard(
            answer, request_id=request_id,
            audit_op="voice_edit.ask.transform_clipboard")
        return {"ok": True, "answer": answer, "mode": mode,
                "provider": "local", "model": "transform",
                "spoken": False,
                "clipboard_ok": bool(clip.get("ok")),
                "clipboard_backend": str(clip.get("backend", "") or ""),
                "clipboard_code": str(clip.get("code", "") or "")}

    def _briefing_weather(self) -> str:
        """Live weather line via the existing no-key script path (or "")."""
        try:
            line = self._live_weather("weather")
            return str(line or "").strip()
        except Exception:
            return ""

    def _briefing_clock(self) -> str:
        """One-line machine-clock readout (no egress, never raises)."""
        try:
            import time as _time
            return f"It is {_time.strftime('%A %H:%M')}."
        except Exception:
            return ""

    def _briefing_cron(self) -> str:
        """Today's schedule count from the canonical store (or "")."""
        try:
            from .. import schedules as _S
            jobs = _S.list_jobs(self.cfg)
            items = jobs if isinstance(jobs, list) else (
                jobs.get("jobs") if isinstance(jobs, dict) else [])
            n = len(items or [])
            if n <= 0:
                return ""
            return (f"{n} scheduled job{'s' if n != 1 else ''} on the list "
                    f"today.")
        except Exception:
            return ""

    def _briefing_unread(self) -> str:
        """Unread-mail count via the skill reader (or "" when unreachable)."""
        try:
            rows = self._read_gmail_rows()
            if not rows:
                return ""
            n = len(rows)
            return (f"{n} unread email{'s' if n != 1 else ''} in the inbox.")
        except Exception:
            return ""

    def brief_me(self, *, request_id: str = "") -> Dict[str, Any]:
        """On-request briefing: need-to-know first, max 3 short items.

        Order is unread → cron → weather → clock (capped at 3); empty
        seams are skipped, never spoken as "unknown". Sleep NEVER gates
        this path (owner decision §5 #2) — the turn is answered and
        remembered like any direct answer. Never raises: total failure
        yields an honest empty-house answer.
        """
        self._require_estop_clear()
        items = [line for line in (
            self._briefing_unread(), self._briefing_cron(),
            self._briefing_weather(), self._briefing_clock())
            if str(line or "").strip()][:3]
        if items:
            answer = " ".join(items)
        else:
            answer = ("Nothing needs your attention right now — no unread "
                      "mail, no schedules, and the weather service is "
                      "unreachable.")
        self._audit("voice_edit.briefing", {
            "request_id": request_id, "items": len(items),
            "answer_chars": len(answer)})
        self._remember_ask_exchange("brief me", answer, model="briefing")
        return {"ok": True, "answer": answer, "mode": "briefing",
                "provider": "local", "model": "briefing",
                "spoken": False, "items": items}

    def speak_completion(self, summary: str, *,
                         request_id: str = "") -> Dict[str, Any]:
        """Unprompted completion speech hook ("report's ready").

        Consults :meth:`may_speak_unprompted`: silent (typed
        ``suppressed``) in sleep mode, spoken otherwise. The service
        owns the gate and the History row; the caller owns the TTS.
        Never prints secrets: the audit carries chars only, never text.
        """
        text = str(summary or "").strip()
        if not text:
            return {"ok": False, "code": "VOICE_EDIT_EMPTY_COMPLETION",
                    "detail": "no completion text to speak"}
        if not self.may_speak_unprompted():
            self._audit("voice_edit.completion.suppressed", {
                "request_id": request_id, "chars": len(text)})
            return {"ok": True, "spoken": False, "suppressed": True,
                    "answer": "", "mode": "completion",
                    "provider": "local", "model": "completion"}
        self._audit("voice_edit.completion.spoken", {
            "request_id": request_id, "chars": len(text)})
        self._remember_ask_exchange("task completed", text,
                                    model="completion")
        return {"ok": True, "spoken": True, "suppressed": False,
                "answer": text, "mode": "completion",
                "provider": "local", "model": "completion"}

    def _recent_completions(self, *, limit: int = 2) -> list:
        """Latest spoken completion texts from the voice-session History.

        Reads the ONE canonical store (the same rows
        :meth:`speak_completion` writes with question ``"task completed"``)
        via the existing ``store.messages`` seam — no new data source, no
        model. Latest first-window, chronological, capped at ``limit``.
        Fail-closed: an unreadable store yields ``[]``, never raises.
        """
        try:
            store = self._canonical_store()
            if store is None:
                return []
            msgs = store.messages(
                self._voice_session(), limit=200).get("messages", [])
            if not isinstance(msgs, list):
                return []
            found: list = []
            for i, m in enumerate(msgs):
                if not isinstance(m, dict):
                    continue
                if (str(m.get("role") or "") == "user"
                        and str(m.get("content") or "").strip().lower()
                        == "task completed"):
                    nxt = msgs[i + 1] if i + 1 < len(msgs) else None
                    if (isinstance(nxt, dict)
                            and str(nxt.get("role") or "") == "assistant"):
                        txt = " ".join(
                            str(nxt.get("content") or "").split())
                        if txt:
                            found.append(txt)
            n = max(1, int(limit or 1))
            return found[-n:]
        except Exception:  # noqa: BLE001 - digest seam is additive
            return []

    def away_digest(self, *, unprompted: bool = False,
                    request_id: str = "") -> Dict[str, Any]:
        """Batched "while you were away" digest (Slice 4 composer).

        Combines recent spoken completions (History) + the unread/cron
        seams, need-to-know first (unread → completions → cron), max 3
        items. Deterministic, local-first, no model. Sleep gates
        UNPROMPTED delivery only (``unprompted=True`` stays silent while
        sleeping, mirroring :meth:`speak_completion`); on-request answers
        sleeping or not (mirroring :meth:`brief_me`). An unprompted digest
        with nothing to report stays silent rather than narrating emptiness.
        Never raises: total failure yields the honest empty-house answer.
        """
        self._require_estop_clear()
        if unprompted and not self.may_speak_unprompted():
            self._audit("voice_edit.away_digest.suppressed", {
                "request_id": request_id})
            return {"ok": True, "spoken": False, "suppressed": True,
                    "answer": "", "mode": "away-digest",
                    "provider": "local", "model": "away-digest", "items": []}
        items = []
        try:
            unread = self._briefing_unread()
            if str(unread or "").strip():
                items.append(str(unread).strip())
            for done in self._recent_completions(limit=2):
                items.append(f"Completed: {done}")
            cron = self._briefing_cron()
            if str(cron or "").strip():
                items.append(str(cron).strip())
        except Exception:  # noqa: BLE001 - seams are fail-closed already
            pass
        items = [i for i in items if str(i or "").strip()][:3]
        if not items:
            if unprompted:
                self._audit("voice_edit.away_digest.empty", {
                    "request_id": request_id})
                return {"ok": True, "spoken": False, "suppressed": False,
                        "empty": True, "answer": "", "mode": "away-digest",
                        "provider": "local", "model": "away-digest",
                        "items": []}
            answer = ("Nothing new while you were away — no completions, "
                      "no unread mail, and nothing on the schedule.")
            self._audit("voice_edit.away_digest", {
                "request_id": request_id, "items": 0,
                "answer_chars": len(answer)})
            self._remember_ask_exchange("while you were away", answer,
                                        model="away-digest")
            return {"ok": True, "answer": answer, "mode": "away-digest",
                    "provider": "local", "model": "away-digest",
                    "spoken": False, "suppressed": False, "items": []}
        answer = " ".join(items)
        self._audit("voice_edit.away_digest", {
            "request_id": request_id, "items": len(items),
            "answer_chars": len(answer)})
        self._remember_ask_exchange("while you were away", answer,
                                    model="away-digest")
        return {"ok": True, "answer": answer, "mode": "away-digest",
                "provider": "local", "model": "away-digest",
                "spoken": bool(unprompted), "suppressed": False,
                "items": items}

    def _default_action_fallback(self, transcript: str) -> str:
        """Configured default action when the router yields no action.

        Applies ONLY when nothing was actually spoken (empty transcript): a
        real utterance the parser cannot map still gets a clarifying
        question, never a silent substitution. Returns "" when the default
        is "clarify" (the shipped default — behavior unchanged) or invalid.
        """
        if (transcript or "").strip():
            return ""
        try:
            from .schema import ACTIONS
        except Exception:
            return ""
        fallback = str(_read_cfg(self.cfg, "voice_edit", "default_action",
                                 default="clarify") or "clarify")
        if fallback == "clarify" or fallback not in ACTIONS:
            return ""
        return fallback

    def _live_facts(self, question: str) -> str:
        """Live facts the small local brain cannot know (clock, weather).

        The answer path never uses tools, so time/date questions and
        weather questions fail on a bare local model. This injects two
        cheap, bounded, local-first facts into the prompt context instead:
        the machine clock (always — one line, no egress) and a live
        wttr.in read via the existing ``scripts/weather.py`` (weather
        intent only, 10 s timeout, no key). A script failure yields "",
        and the brain answers from its weights as before. Never raises.
        """
        import re as _re
        import time as _time
        q = (question or "").strip()
        if not q:
            return ""
        lines = []
        try:
            now = _time.strftime("%A %Y-%m-%d %H:%M %Z")
            if now.strip():
                lines.append(f"Current local time: {now}.")
        except Exception:
            pass
        if _re.search(r"\bweather\b|\bforecast\b|\btemperature\b|\btemp\b", q, _re.IGNORECASE):
            lines.append(self._live_weather(q))
        return "\n".join(l for l in lines if l)

    def _live_weather(self, question: str) -> str:
        """One bounded call to the existing weather script (no key)."""
        import subprocess as _sp
        import sys as _sys
        from pathlib import Path as _Path
        try:
            script = (_Path(__file__).resolve().parents[2]
                      / "scripts" / "weather.py")
            if not script.is_file():
                return ""
            proc = _sp.run([_sys.executable, str(script), question],
                           capture_output=True, text=True, timeout=10)
            if proc.returncode != 0:
                return ""
            return str(proc.stdout or "").strip()
        except Exception:
            return ""

    _YT_URL_RE = None  # compiled lazily (see _youtube_url)

    def _reply_draft(self, question: str) -> Any:
        """Stage a voice reply draft (nothing sent, nothing staged yet).

        Parses channel/recipient/body, resolves the recipient fresh
        (gmail rows, messenger hrefs, instagram names). Returns
        ``("draft", (canonical, summary))`` on exactly-one match,
        ``("unknown", names)`` when the read succeeded but nobody
        matches, ``("unavailable", channel)`` when the read itself
        failed (browser busy — never let the brain narrate a phantom
        action), or None to fall through (unparseable). Never raises.
        Sending happens only after voice "yes" through the
        confirm-gated task path.
        """
        from . import send as _S
        parsed = _S.parse_reply(question or "")
        if parsed is None:
            return None
        channel = parsed["channel"]
        try:
            if parsed.get("address"):
                addr = parsed["address"]
                canonical = _S.build_draft(channel, addr, f"addr:{addr}",
                                           parsed["body"])
                body = parsed["body"]
                preview = (body[:80] + "…") if len(body) > 80 else body
                summary = (f"Email {addr}: '{preview}' — say yes to send, "
                           f"no to cancel.")
                return ("draft", (canonical, summary))
            if channel == "gmail":
                rows = self._read_gmail_rows()
                if rows is None:
                    return ("unavailable", channel)
                dicts = [{"sender": s} for s, _t in rows]
            elif channel == "messenger":
                dicts = self._run_social_script("read_messenger.py")
                if dicts is None:
                    return ("unavailable", channel)
            else:
                dicts = self._run_social_script("read_instagram.py")
                if dicts is None:
                    return ("unavailable", channel)
            hit = _S.resolve(channel, parsed["to_hint"],
                             readers={channel: lambda: dicts})
            if hit is None:
                names = []
                for it in dicts or []:
                    if isinstance(it, dict):
                        n = str(it.get("name") or it.get("sender") or ""
                                ).strip()[:30]
                    elif isinstance(it, (list, tuple)) and it:
                        n = str(it[0] or "").strip()[:30]
                    else:
                        n = ""
                    if n:
                        names.append(n)
                return ("unknown", names[:6])
            name, href = hit
            canonical = _S.build_draft(channel, name, href, parsed["body"])
            body = parsed["body"]
            preview = (body[:80] + "…") if len(body) > 80 else body
            summary = (f"Reply to {name} on {channel}: "
                       f"'{preview}' — say yes to send, no to cancel.")
            return ("draft", (canonical, summary))
        except Exception:
            return None

    def _skill_answer(self, question: str) -> "tuple[str, str]":
        """Skill-backed answers: mail digest, YouTube summary.

        Returns ``(answer, model)``; ``("", "")`` falls through to the
        brain. Mail is deterministic (envelope subjects, no brain); a
        YouTube URL is fetched then summarized by the local brain. Send/
        draft/reply mail verbs are NEVER handled here (confirm-gated
        agent territory). Never raises.
        """
        import re as _re
        q = (question or "").strip()
        if not q:
            return "", ""
        if _re.search(r"\b(e-?mail|gmail|inbox|mailbox|mail)\b", q, _re.IGNORECASE):
            if _re.search(r"\b(send|compose|write|draft|reply|forward)\b",
                           q, _re.IGNORECASE):
                return "", ""
            if _re.search(r"\b(check|new|unread|any|latest|recent|read|show"
                           r"|inbox|mailbox|waiting)\b", q, _re.IGNORECASE):
                return self._mail_answer()
        url = self._youtube_url(q)
        if url:
            return self._youtube_answer(url)
        social = self._social_answer(q)
        if social:
            return social
        return "", ""

    @staticmethod
    def _session_transcript(rows: Any, *, limit: int = 40,
                            per_line: int = 500) -> str:
        """Compact "USER:/ASSISTANT:" transcript from canonical message rows.

        Bounded (last ``limit`` rows, ``per_line`` chars each), user/assistant
        only, whitespace-collapsed. Never raises; a malformed row is skipped.
        """
        out = []
        try:
            selected = list(rows)[-max(1, int(limit)):]
        except Exception:
            return ""
        for m in selected:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "").strip().lower()
            if role == "user":
                speaker = "USER"
            elif role == "assistant":
                speaker = "ASSISTANT"
            else:
                continue
            content = " ".join(str(m.get("content") or "").split())
            if not content:
                continue
            out.append(f"{speaker}: {content[:per_line]}")
        return "\n".join(out)

    def _report_answer(self, question: str, *,
                       request_id: str = "") -> "tuple[str, str]":
        """Spoken summary of a chat session's transcript (deterministic).

        Recognizes the report/summarize-a-chat intent, resolves the session
        (the routed ``req.session_id`` when set, else the current voice
        session), reads the canonical transcript bounded to the last 40 rows,
        and summarizes it with the existing :func:`chat_transport._summarize_report`
        (bounded words). The RAW transcript is never returned/spoken: an empty
        or missing transcript yields an honest answer, and a summarizer fault
        yields a short literal. Returns ``(answer, "skill-report")`` or
        ``("", "")`` to fall through. Never raises.
        """
        q = (question or "").strip()
        if not q or not self.is_report_request(q):
            return "", ""
        session = self._request_session(request_id) or self._voice_session(
            request_id)
        rows: Any = []
        try:
            from .. import chat_transport as _CT
            data = _CT.store(self.cfg).messages(session, limit=40)
            if isinstance(data, dict):
                rows = [m for m in (data.get("messages") or [])
                        if isinstance(m, dict)]
        except Exception:  # noqa: BLE001 - a store fault falls through
            return "", ""
        transcript = self._session_transcript(rows)
        if not transcript:
            return ("There's no session transcript to report on yet.",
                    "skill-report")
        summary = ""
        try:
            from .. import chat_transport as _CT
            summary = str(_CT._summarize_report(transcript, self.cfg) or ""
                          ).strip()
        except Exception:  # noqa: BLE001 - degrade to a short literal
            summary = ""
        if not summary:
            return ("I couldn't summarize that session right now.",
                    "skill-report")
        try:
            from ..summarizer import clamp_words
            summary = clamp_words(summary, 40)
        except Exception:  # noqa: BLE001 - summary is already bounded upstream
            pass
        return summary, "skill-report"

    def _gmail_configured(self) -> bool:
        """A Gmail-capable connector exists (himalaya account file)."""
        from pathlib import Path as _Path
        import os as _os
        try:
            return (_Path(_os.path.expanduser("~/.config/himalaya"))
                    .joinpath("config.toml").is_file())
        except Exception:
            return False

    def _mail_answer(self) -> "tuple[str, str]":
        """Spoken inbox digest, or the honest not-connected answer.

        Source priority follows the skill docs: the logged-in browser
        session (CDP `read_gmail.py`) first, the himalaya CLI account
        second. Unconfigured everywhere → the setup answer naming
        `himalaya account configure` (and the browser relaunch needed
        for CDP). Never raises.
        """
        rows = self._read_gmail_rows()
        if rows:
            return self._spoken_digest(rows), "skill-mail"
        if not self._gmail_configured():
            return ("No mail account is connected yet, so I can't read "
                    "your mail. Either keep Gmail open in Chromium "
                    "relaunched with --remote-debugging-port=9222, or run "
                    "`himalaya account configure` in a terminal, then ask "
                    "me again.", "skill-mail")
        env = self._gmail_envelopes()
        if env is None:
            return "", ""  # fetch failed: fall through to the brain
        if not env:
            return ("Your Gmail inbox is empty.", "skill-mail")
        return self._spoken_digest(env), "skill-mail"

    @staticmethod
    def _spoken_digest(rows: Any) -> str:
        bits = []
        for sender, subject in list(rows)[:5]:
            bits.append(f"{subject[:80]} from {sender[:40]}")
        return (f"Latest {len(list(rows)[:5])} in Gmail: "
                + "; ".join(bits) + ".")

    def _social_answer(self, question: str) -> "tuple[str, str]":
        """Spoken digests for Messenger / Instagram via logged-in tabs.

        Read-only DOM extraction over CDP (the skill's reader scripts);
        send/reply/write verbs fall through (sending is confirm-gated
        agent territory AND computer-control is DISABLED by policy).
        Returns ``(answer, "skill-social")`` or ``("", "")``. Never raises.
        """
        import re as _re
        q = (question or "").strip()
        if not q:
            return "", ""
        if _re.search(r"\b(send|reply|write|answer back|respond)\b",
                       q, _re.IGNORECASE):
            return "", ""
        verbs = (r"\b(check|new|unread|any|latest|recent|read|show|messages"
                 r"|chats|dms|direct|inbox)\b")
        if not _re.search(verbs, q, _re.IGNORECASE):
            return "", ""
        if _re.search(r"\bmessenger\b|\bfacebook\b|\bfb\b", q, _re.IGNORECASE):
            rows = self._run_social_script("read_messenger.py")
            if rows:
                bits = []
                for it in rows[:4]:
                    n = str(it.get("name") or "").strip()[:40]
                    p = str(it.get("preview") or it.get("text") or ""
                            ).strip()[:100]
                    if n or p:
                        bits.append(f"{n or 'unknown'} — {p or '(no preview)'}")
                if bits:
                    return ("Messenger: " + "; ".join(bits) + ".",
                            "skill-social")
            return "", ""
        if _re.search(r"\binstagram\b|\binsta\b", q, _re.IGNORECASE):
            rows = self._run_social_script("read_instagram.py")
            if rows:
                bits = []
                for it in rows[:4]:
                    n = str(it.get("name") or "").strip()[:40]
                    p = str(it.get("preview") or it.get("text") or ""
                            ).strip()[:100]
                    if n or p:
                        bits.append(f"{n or 'unknown'} — {p or '(no preview)'}")
                if bits:
                    return ("Instagram: " + "; ".join(bits) + ".",
                            "skill-social")
            return "", ""
        return "", ""

    def _run_social_script(self, name: str) -> Any:
        """Raw JSON list from a social reader script (dicts), or None.

        One retry on transport failure: the CDP socket flakes while the
        owner drives the browser. Never raises.
        """
        import json as _json
        import subprocess as _sp
        import sys as _sys
        import time as _time
        script = self._skill_script(
            f"communication/social-inbox-check/scripts/{name}")
        if not script:
            return None
        for attempt in (1, 2):
            try:
                proc = _sp.run([_sys.executable, script, "3"],
                               capture_output=True, text=True, timeout=45)
                if proc.returncode == 0:
                    data = _json.loads(proc.stdout or "")
                    return data if isinstance(data, list) else None
            except Exception:
                pass
            if attempt == 1:
                try:
                    _time.sleep(2)
                except Exception:
                    pass
        return None

    def _skill_script(self, relative: str) -> str:
        """Absolute path of a skill helper script under QUBIT_HOME, or ""."""
        from pathlib import Path as _Path
        try:
            from .. import qubit_runtime as _HR
            cand = _Path(_HR.runtime_home_for_cfg(self.cfg)).joinpath(
                *(["skills"] + str(relative).split("/")))
            if cand.is_file():
                return str(cand)
        except Exception:
            pass
        return ""

    def _read_gmail_rows(self) -> Any:
        """Top inbox rows via the logged-in Chromium session (CDP).

        Runs the skill's `read_gmail.py` (bounded 45 s, one retry on
        transport flake); returns ``[(sender, subject)]`` or ``None``
        when CDP is down, no Gmail tab is open, or nothing parses.
        Never raises.
        """
        import json as _json
        import subprocess as _sp
        import sys as _sys
        import time as _time
        script = self._skill_script(
            "communication/social-inbox-check/scripts/read_gmail.py")
        if not script:
            return None
        for attempt in (1, 2):
            try:
                proc = _sp.run([_sys.executable, script, "3"],
                               capture_output=True, text=True, timeout=45)
                if proc.returncode == 0:
                    break
            except Exception:
                proc = None
            if attempt == 1:
                try:
                    _time.sleep(2)
                except Exception:
                    pass
        else:
            return None
        try:
            data = _json.loads((proc.stdout if proc else "") or "")
            rows = data if isinstance(data, list) else []
            out = []
            for it in rows:
                if not isinstance(it, dict):
                    continue
                subj = str(it.get("subject") or "").strip()
                sender = str(it.get("sender") or "").strip()
                if subj or sender:
                    out.append((sender or "unknown sender",
                                subj or "(no subject)"))
            return out or None
        except Exception:
            return None

    def _gmail_envelopes(self) -> Any:
        """Newest envelopes via the himalaya CLI (bounded, read-only).

        Returns a list of ``(sender, subject)`` or ``None`` on any
        failure (never raises, never fabricates). Empty list = connected
        but no mail.
        """
        import shutil as _sh
        import subprocess as _sp
        try:
            exe = _sh.which("himalaya")
            if not exe:
                from pathlib import Path as _Path
                import os as _os
                cand = _Path(_os.path.expanduser("~/.local/bin/himalaya"))
                exe = str(cand) if cand.is_file() else ""
            if not exe:
                return None
            proc = _sp.run([exe, "envelope", "list", "-s", "5", "--json",
                            "--log-level", "off"],
                           capture_output=True, text=True, timeout=15)
            if proc.returncode != 0:
                return None
            return self._parse_envelopes(proc.stdout or "")
        except Exception:
            return None

    @staticmethod
    def _parse_envelopes(raw: str) -> Any:
        """Parse himalaya envelope JSON defensively (shape varies).

        Returns ``[(sender, subject)]``; ``None`` when nothing parseable
        (fetch-level failure is reported upstream by returncode).
        """
        import json as _json
        try:
            data = _json.loads(raw or "")
        except Exception:
            return None
        items = data if isinstance(data, list) else data.get("envelopes",
                  data.get("messages", [])) if isinstance(data, dict) else []
        rows = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            subj = str(it.get("subject") or "").strip()
            sender = ""
            for key in ("from", "sender", "from_address", "author"):
                val = it.get(key)
                if isinstance(val, dict):
                    val = val.get("address") or val.get("name") or ""
                if val:
                    sender = str(val).strip()
                    break
            if subj or sender:
                rows.append((sender or "unknown sender", subj or "(no subject)"))
        return rows

    @staticmethod
    def _youtube_url(question: str) -> str:
        """First YouTube URL in the question, or ""."""
        import re as _re
        m = _re.search(r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^\s]*"
                       r"|shorts/\S+|embed/\S+)|youtu\.be/\S+)",
                       question or "", _re.IGNORECASE)
        return (m.group(0).rstrip(").,!?;:") if m else "")

    def _youtube_script(self) -> str:
        """Absolute path of the vendored transcript fetcher, or ""."""
        from pathlib import Path as _Path
        try:
            from .. import qubit_runtime as _HR
            home = _Path(_HR.runtime_home_for_cfg(self.cfg))
            cand = (home / "skills" / "media" / "youtube-content"
                    / "scripts" / "fetch_transcript.py")
            if cand.is_file():
                return str(cand)
        except Exception:
            pass
        return ""

    def _youtube_answer(self, url: str) -> "tuple[str, str]":
        """Fetch a public-video transcript, summarize locally, speak it."""
        import subprocess as _sp
        import sys as _sys
        script = self._youtube_script()
        if not script:
            return "", ""
        try:
            proc = _sp.run([_sys.executable, script, url, "--text-only"],
                           capture_output=True, text=True, timeout=25)
            if proc.returncode != 0:
                return "", ""
            text = str(proc.stdout or "").strip()
            if not text:
                return "", ""
        except Exception:
            return "", ""
        try:
            from ..providers import gateway as GW
            model_override = self._select_model(
                _read_cfg(self.cfg, "voice_edit", "detail_model", default=""))
            res = GW.run_role(
                f"Video transcript:\n{text[:6000]}\n\nWhat is this video "
                f"about? Answer in two short spoken sentences.",
                self.cfg, role="chat", profile_id=self._local_profile(),
                model=model_override,
                max_tokens=200, timeout_s=60)
            answer = str(getattr(res, "text", "") or "").strip()
            if getattr(res, "ok", False) and answer:
                return answer, str(getattr(res, "model", "") or "local")
        except Exception:
            pass
        return "", ""

    def _direct_fact_answer(self, question: str) -> str:
        """Deterministic answers for clock/weather (no model in the loop).

        The small local brain ignores injected facts and emits stock
        refusals ("I don't have real-time weather data") even with the
        reading in its prompt. Facts don't need a model: a weather
        question with a live script reading, or a time question with the
        machine clock, is answered verbatim. Anything else (or a failed
        weather read) returns "" and the turn falls through to the brain
        with the same facts in context. Never raises.
        """
        import re as _re
        import time as _time
        q = (question or "").strip()
        if not q:
            return ""
        if _re.search(r"\bweather\b|\bforecast\b|\btemperature\b|\btemp\b", q, _re.IGNORECASE):
            return self._live_weather(q)
        # Lane 3 (2026-10-03): STT emits contractions ("What's the time in
        # Bucharest right now?", "What's the date now in Romania?") which
        # missed the contiguous "what time"/"what date" patterns and fell to
        # the brain — which then spoke wrong facts ("Bucharest: 12:34 PM",
        # "Today is April 3, 2025"; journal 03:31 + 04:31 UTC). Match the
        # contraction shapes explicitly. Date questions get the calendar
        # date (a clock time does not answer "what date"); time questions
        # keep the existing "It is %A %H:%M." shape.
        if _re.search(r"\bwhat time\b|\bcurrent time\b|\btime is it\b"
                       r"|\bwhat'?s the time\b|\btell me the time\b",
                       q, _re.IGNORECASE):
            try:
                return f"It is {_time.strftime('%A %H:%M')}."
            except Exception:
                return ""
        if _re.search(r"\bwhat day is it\b|\bwhat date\b"
                       r"|\bwhat'?s (the |today'?s )?(date|day|today)\b"
                       r"|\bwhat is today\b|\bwhat'?s today\b",
                       q, _re.IGNORECASE):
            try:
                return (f"It is {_time.strftime('%A, %B')} "
                        f"{int(_time.strftime('%d'))}, "
                        f"{_time.strftime('%Y')}.")
            except Exception:
                return ""
        return ""

    @_counts_turn
    def ask(self, text: str, *, request_id: str = "",
            mode: str = "answer") -> Dict[str, Any]:
        """Answer a spoken question/chat turn through the one Qubit brain.

        Used by the desktop helper so a question spoken while a text field is
        focused reaches the brain and returns an answer instead of typing the
        question into the field. Never uses tools. The configured local
        profile (Brain4) is the default, so no egress is required: a
        non-escalated turn resolves the local profile explicitly (the same
        local runner the transform path uses) instead of falling through to
        the resolver default, which is an API profile that fails closed on
        stock config. When the recorded routing decision escalates
        (``routing=api`` / a task under ``auto``), the configured API profile
        is used and the decision is audited; an escalation with no configured
        target degrades to local, never to the resolver default.
        """
        # Orb STOP / explicit cancel: register a per-request cancel event so
        # the model or agent call and any service-side speech for this turn
        # can be stopped — not just the helper's own playback. The event is
        # released on every exit (one per request id); an empty request id
        # has nothing a cancel can name and stays byte-identical.
        _cancel = self._ask_cancel_event(request_id)
        try:
            return self._answer_question_text(
                text, request_id=request_id, mode=mode, _cancel=_cancel)
        finally:
            self._release_ask_cancel(
                str(request_id or "").strip(), _cancel)

    def _answer_question_text(self, text: str, *, request_id: str = "",
                              mode: str = "answer",
                              _cancel: Optional[threading.Event] = None
                              ) -> Dict[str, Any]:
        question = (text or "").strip()
        if not question:
            raise VoiceEditError("VOICE_EDIT_EMPTY_QUESTION",
                                 "no question to answer")
        self._require_estop_clear()
        if self._ask_stopped(_cancel):
            return self._ask_cancelled(request_id, mode)
        self._maybe_prune_voice_sessions(request_id)
        # Slice 4a — manual sleep mode (owner decision 2026-10-03 §5 #1/#2).
        # "going to sleep" sets the mode (this turn is on-request: answered,
        # remembered, spoken like any direct answer). A directed "hey qubit"
        # clears it and is answered normally. Anything else — sleeping or
        # not — falls through: sleep NEVER gates on-request answers, only
        # unprompted speech via should_suppress_unprompted().
        if self.is_sleep_command(question):
            self.set_sleep_mode(True)
            answer = ("Sleep mode on — I'll stay quiet until you say hey "
                      "qubit.")
            self._audit("voice_edit.sleep.set", {
                "request_id": request_id, "mode": mode,
                "chars": len(question)})
            self._remember_ask_exchange(question, answer, model="sleep-mode")
            return {"ok": True, "answer": answer, "mode": "sleep",
                    "provider": "local", "model": "sleep-mode",
                    "spoken": False}
        if self.sleeping() and self._is_directed_wake(question):
            self.set_sleep_mode(False)
            self._audit("voice_edit.sleep.cleared", {
                "request_id": request_id, "mode": mode,
                "chars": len(question)})
        # Dashboard waveform visibility (voice-final lane): waveform commands
        # flip the setting through the canonical writer.
        # This turn is on-request: answered, remembered, spoken like any
        # direct answer. Parsed with the sleep block (which always wins) and
        # before the route words (a deterministic settings flip beats dispatch).
        # In-game override (owner 2026-10-04) first: the qualified phrases
        # also match the bare widget regex, so they must win here — they flip
        # ONLY voice_edit.waveform_ingame (the orb's fullscreen rule) with
        # the unambiguous confirms "Game overlay on/off." (never "Waveform").
        _ingame = self.waveform_ingame_command(question)
        if _ingame is not None:
            on = _ingame == "show"
            self.set_waveform_ingame(on)
            answer = ("Game overlay on." if on else "Game overlay off.")
            self._audit("voice_edit.waveform_ingame.set", {
                "request_id": request_id, "mode": mode,
                "ingame": on, "chars": len(question)})
            self._remember_ask_exchange(question, answer,
                                        model="waveform-ingame")
            return {"ok": True, "answer": answer, "mode": "waveform-ingame",
                    "provider": "local", "model": "waveform-ingame",
                    "spoken": False}
        _waveform = self.waveform_command(question)
        if _waveform is not None:
            visible = _waveform == "show"
            self.set_waveform_visible(visible)
            answer = ("Waveform on." if visible else "Waveform off.")
            self._audit("voice_edit.waveform.set", {
                "request_id": request_id, "mode": mode,
                "visible": visible, "chars": len(question)})
            self._remember_ask_exchange(question, answer,
                                        model="waveform-visible")
            return {"ok": True, "answer": answer, "mode": "waveform",
                    "provider": "local", "model": "waveform-visible",
                    "spoken": False}
        # Slice 1 route words, parsed BEFORE the deterministic stages.
        # "in chat:" skips reply drafts, live facts, and skills entirely and
        # dispatches straight to the full agent turn (headless /chat worker).
        # When the worker cannot answer, fall through to the direct path on
        # the stripped text (a CT-staged confirmation settles via voice-"yes";
        # never an unsettleable promise, never double-executed — the
        # settle-once journal owns it). "quick:" keeps today's direct
        # behavior with the worker fallback disabled (brain-only).
        try:
            from ..chat_transport import parse_route_word as _parse_ask_rw
            _ask_rw = _parse_ask_rw(question)
        except Exception:
            _ask_rw = None
        _quick_only = False
        if _ask_rw is not None and _ask_rw["route"] == "in-chat":
            question = _ask_rw["task_text"]
            _full_first = self._full_turn_answer(
                question, request_id=request_id, mode=mode, _cancel=_cancel)
            if _full_first is not None:
                return _full_first
        elif _ask_rw is not None and _ask_rw["route"] == "quick":
            question = _ask_rw["task_text"]
            _quick_only = True
        # Lane 2 (2026-10-03): transform-shaped speech reaches TRANSFORM
        # here, never a worker. Runs on the stripped question (after the
        # route words above) and before the deterministic answer stages, so
        # a rewrite can never become a task.run/web.search again. Coding
        # tasks are unaffected (the parser leaves them unclaimed).
        try:
            _tcmd = self._transform_pre_route(question)
        except Exception:
            _tcmd = None
        if _tcmd is not None:
            _tout = self._ask_transform_answer(
                _tcmd, question, request_id=request_id, mode=mode)
            if _tout is not None:
                return _tout
        # Slice 4b — on-request briefing (owner decision 2026-10-03 §5 #2a).
        # Parsed AFTER the sleep command (which always wins) and the route
        # words (a "quick:" prefix is honored; an "in chat:" prefix already
        # dispatched above). Sleep NEVER gates this: brief_me() answers
        # sleeping or not; only unprompted speech consults sleep mode.
        if self.is_briefing_request(question):
            return self.brief_me(request_id=request_id)
        # Lane 3 — on-request away digest ("what did I miss", 2026-10-03).
        # Parsed with the briefing block: on-request, so sleep NEVER gates
        # it (away_digest answers sleeping or not; only unprompted delivery
        # consults sleep, and that path currently has no callers). Before
        # reply drafts: a digest question is never a reply draft.
        if self.is_digest_request(question):
            return self.away_digest(request_id=request_id)
        # Sweep lane (2026-10-05) — deterministic reread. "say that again"
        # reached ask() as conversation and the brain paraphrased (or the
        # router dropped it as ignore before it ever got here); the last
        # spoken answer replays verbatim with no model in the loop. Runs
        # with the briefing/digest block (on-request, never sleep-gated)
        # and before reply drafts (no shape overlap — parse_reply needs a
        # channel/recipient/body). Empty history falls through (None path
        # below is brain/full-turn byte-identical). The replay is a pure
        # re-speak: nothing is persisted, so the transcript never duplicates.
        if self.is_repeat_request(question):
            repeat_answer, repeat_model = self._repeat_last_answer()
            if repeat_answer:
                self._audit("voice_edit.ask", {
                    "request_id": request_id, "mode": mode,
                    "chars": len(question), "ok": True,
                    "answer_chars": len(repeat_answer), "provider": "local",
                    "model": repeat_model})
                return {"ok": True, "answer": repeat_answer, "mode": mode,
                        "provider": "local", "model": repeat_model,
                        "spoken": False}
        staged = self._reply_draft(question)
        if staged is not None:
            kind, payload = staged
            if kind == "draft":
                canonical, summary = payload
                self._audit("voice_edit.reply.staged", {
                    "request_id": request_id, "mode": mode,
                    "chars": len(question),
                    **(_dbg.text_fields(self.cfg, draft=summary))})
                return {"ok": True, "mode": "task",
                        "requires_confirmation": True,
                        "task_text": canonical, "summary": summary,
                        "request_id": request_id,
                        "detail": "confirm to send this reply"}
            if kind == "unavailable":
                self._audit("voice_edit.reply.unavailable", {
                    "request_id": request_id, "mode": mode,
                    "chars": len(question)})
                answer = (f"I can't reach {payload} right now — the "
                          f"browser tab is busy. Try again in a moment.")
                self._remember_ask_exchange(question, answer,
                                            model="skill-social")
                return {"ok": True, "answer": answer, "mode": mode,
                        "provider": "local", "model": "skill-social",
                        "spoken": False}
            names = ", ".join(payload) if payload else "nobody found"
            self._audit("voice_edit.reply.unknown", {
                "request_id": request_id, "mode": mode,
                "chars": len(question)})
            answer = (f"I couldn't find that person — recent chats show: "
                      f"{names}. Who exactly should I reply to?")
            self._remember_ask_exchange(question, answer,
                                        model="skill-social")
            return {"ok": True, "answer": answer, "mode": mode,
                    "provider": "local", "model": "skill-social",
                    "spoken": False}
        direct = self._direct_fact_answer(question)
        if direct:
            self._audit("voice_edit.ask", {
                "request_id": request_id, "mode": mode,
                "chars": len(question), "ok": True,
                "answer_chars": len(direct), "provider": "local",
                "model": "live-facts"})
            self._remember_ask_exchange(question, direct, model="live-facts")
            return {"ok": True, "answer": direct, "mode": mode,
                    "provider": "local", "model": "live-facts",
                    "spoken": False}
        skill_answer, skill_model = self._skill_answer(question)
        if skill_answer:
            self._audit("voice_edit.ask", {
                "request_id": request_id, "mode": mode,
                "chars": len(question), "ok": True,
                "answer_chars": len(skill_answer), "provider": "local",
                "model": skill_model})
            self._remember_ask_exchange(question, skill_answer,
                                        model=skill_model)
            return {"ok": True, "answer": skill_answer, "mode": mode,
                    "provider": "local", "model": skill_model,
                    "spoken": False}
        # Report lane (2026-10-06): "report on my chat" / "summarize the chat
        # session" reads the routed/current canonical transcript and speaks a
        # SHORT summary, deterministically — before the brain/full-turn, so it
        # never becomes a long model answer. The audit carries counts and the
        # resolved session id only, never transcript text.
        report_answer, report_model = self._report_answer(
            question, request_id=request_id)
        if report_answer:
            self._audit("voice_edit.report_chat", {
                "request_id": request_id, "mode": mode,
                "chars": len(question), "ok": True,
                "answer_chars": len(report_answer), "provider": "local",
                "model": report_model,
                "session": self._request_session(request_id) or ""})
            self._remember_ask_exchange(question, report_answer,
                                        model=report_model)
            return {"ok": True, "answer": report_answer, "mode": mode,
                    "provider": "local", "model": report_model,
                    "spoken": False}
        # Lane-3 net (2026-10-03): tool-shaped misses that reach ask() as
        # conversation must STAGE, never let the brain narrate a
        # confirmation with no backing task (the 04:53 UTC "Set THE timer"
        # case: brain "Confirm?" spoken, then "Yes." discarded unarmed).
        # Runs AFTER reply drafts, direct facts, and skills (those deterministic
        # answers keep priority) and only for local.* tools — the brain never
        # sees tool-shaped text. The returned shape is the same staged-task
        # shape propose_task emits, so the helper speaks the confirmation and
        # re-arms for voice "yes" (see _answer_question). Staging is pure
        # dict-building over the live request id when one exists; a direct
        # /ask call without begin() still stages (nothing executes).
        proposal = self._tool_shaped_proposal(question)
        if proposal is not None:
            tool = str(proposal.get("tool") or "")
            self._audit("voice_edit.task.proposed", {
                "request_id": request_id, "chars": len(question),
                "tool": tool, "via": "ask-net",
                **(_dbg.text_fields(self.cfg, task=question))})
            summary = (f"{question} (runs {tool} after yes)"
                       if tool else question)
            return {"ok": True, "mode": "task",
                    "requires_confirmation": True,
                    "task_text": question, "summary": summary,
                    "request_id": request_id,
                    "detail": "confirm to run this task"}
        escalate, why = self._escalate_for(mode)
        profile = ""
        if escalate:
            profile = str(_read_cfg(self.cfg, "voice_edit", "api_profile",
                                    default="") or "")
            if not profile:
                try:
                    from .. import context_policy as CTX
                    profile = str(CTX.escalation_target(self.cfg, {}) or "")
                except Exception:
                    profile = ""
            if profile and not self._known_model_profile(profile):
                # A configured target that is not a real model profile must not
                # be handed to run_role; stay local and record the decision.
                profile, escalate, why = "", False, "unknown_api_target"
            elif not profile:
                # Escalation recorded but no API target configured: stay
                # local instead of falling through to the resolver default.
                escalate, why = False, "no_api_profile"
            self._audit("voice_edit.escalation", {
                "request_id": request_id, "mode": mode, "reason": why,
                "profile": profile or "(none configured)",
                "escalate": bool(profile)})
        if not escalate:
            # Non-escalated turns (routing=local, or auto answer/chat) run on
            # the configured local profile — never profile None (the resolver
            # default, an API profile that fails closed on stock config).
            profile = self._local_profile()
        # A STOP that landed during the cheap deterministic stages (facts,
        # skills, reply drafts) must never reach the expensive brain call.
        if self._ask_stopped(_cancel):
            return self._ask_cancelled(request_id, mode)

        def _stopped() -> bool:
            """Real abort hook for the brain call (event set -> stop)."""
            return self._ask_stopped(_cancel)

        try:
            from ..providers import gateway as GW
            # Conversation memory: pass the bounded prior transcript as the
            # structured conversation_prompt (the adapter formats dialogue
            # correctly); folding it into the prompt string made the small
            # local model echo the last answer.
            convo = self._conversation_context(question, request_id=request_id)
            # Shorter, more varied spoken answers: simple questions get one
            # natural sentence; an explicit "explain/in detail/report" gets room
            # for a fuller answer. A per-turn style hint discourages repeating
            # stock openers/closers.
            max_tokens, reasoning, style = self._style_for(question)
            # Model selection is config-driven and portable. An empty value or
            # "auto" uses the machine's configured local model (the provider
            # resolver); a configured name is used only if it is actually
            # installed, else we fall back to the configured model. So a weak
            # PC, a strong PC, or a missing model all keep working.
            if reasoning == "medium":
                model_override = self._select_model(
                    _read_cfg(self.cfg, "voice_edit", "detail_model",
                              default=""))
            else:
                model_override = self._select_model(
                    _read_cfg(self.cfg, "voice_edit", "chat_model", default=""))
            ctx = self._personal_context()
            live = self._live_facts(question)
            if live:
                directive = ("LIVE FACTS (current, answer from these; "
                             "never claim no live data when they are present):")
                ctx = f"{ctx}\n\n{directive}\n{live}" if ctx else f"{directive}\n{live}"
            if style:
                ctx = f"{ctx}\n\nSTYLE: {style}" if ctx else f"STYLE: {style}"
            # ``cancel`` aborts the remote/frontier path; ``should_stop``
            # aborts the local streaming adapter mid-generation. Both read the
            # same event, so a normal (uncancelled) turn is byte-identical.
            res = GW.run_role(question, self.cfg, role="chat",
                              profile_id=profile or None,
                              model=model_override,
                              max_tokens=max_tokens,
                              timeout_s=30.0,
                              reasoning=reasoning,
                              conversation_prompt=convo,
                              context=ctx,
                              cancel=_stopped, should_stop=_stopped)
        except Exception as exc:
            return {"ok": False, "code": "VOICE_EDIT_ASK_FAILED",
                    "detail": str(exc)[:160]}
        # The stop can arrive while the model call runs: never hand the reply
        # back to be spoken (and never trigger the full-turn fallback) once the
        # turn has been cancelled.
        if self._ask_stopped(_cancel):
            return self._ask_cancelled(request_id, mode)
        spoken = False
        ok = bool(getattr(res, "ok", False))
        answer = str(getattr(res, "text", "") or "").strip()
        self._audit("voice_edit.ask", {
            "request_id": request_id, "mode": mode, "chars": len(question),
            "ok": ok, "answer_chars": len(answer),
            "provider": str(getattr(res, "provider", "") or ""),
            "model": str(getattr(res, "model", "") or ""),
            **(_dbg.text_fields(self.cfg, question=question, answer=answer))})
        # Decision 26: the tool-less brain is no longer the ceiling. When it
        # fails, stays silent, or answers with a polite refusal, the question
        # gets a second chance through the same persisted agent turn
        # /qubit-chat uses (branches + worker + voice-local model). A working
        # brain answer returns exactly as before. "quick:" route words are
        # brain-only by contract: no worker dispatch, failure stays typed.
        if not _quick_only and (
                not ok or not answer or _looks_like_brain_refusal(answer)):
            full = self._full_turn_answer(question, request_id=request_id,
                                          mode=mode, _cancel=_cancel)
            if full is not None:
                return full
        if not ok or not answer:
            return {"ok": False,
                    "code": str(getattr(res, "code", "") or
                                "VOICE_EDIT_ASK_EMPTY"),
                    "detail": str(getattr(res, "message", "") or "")[:160]}
        # Best-effort: surface the answer on the canonical chat session so
        # ``/qubit-chat`` receives it live, exactly like a spoken turn.
        self._remember_ask_exchange(
            question, answer, model=str(getattr(res, "model", "") or ""))
        return {"ok": True, "answer": answer, "mode": mode,
                "provider": str(getattr(res, "provider", "") or ""),
                "model": str(getattr(res, "model", "") or ""),
                "spoken": bool(spoken)}

    def _full_turn_answer(self, question: str, *, request_id: str = "",
                            mode: str = "answer",
                            _cancel: Optional[threading.Event] = None
                            ) -> Optional[Dict[str, Any]]:
        """Decision-26 fallback: answer through the full agent turn.

        Dispatches through ``chat_transport.turn`` — the same persisted,
        broker/policy/audit/confirm/e-stop turn ``/qubit-chat`` uses, with
        its web/local/registry/agent_task branches and the voice-local
        model. The turn persists itself to the canonical voice session, so
        the caller must NOT also ``_remember_ask_exchange`` (that would
        double-write the exchange). Returns the ask-shaped dict, or None
        when the full turn cannot answer (caller keeps the brain outcome).

        A STOP keyed to this turn's ``request_id`` reaches the agent branch
        through the existing ``chat_transport`` cancel transport: the
        ``cancel`` callable reads the same event, so the agent turn aborts
        and this method returns the typed cancelled outcome instead of a
        reply.

        Staged confirmations (``CONFIRM_REQUIRED`` + ``confirmation_id``)
        deliberately fall through to None: the helper speaks staged
        confirmations for service.task mode, where voice "yes" settles the
        CT-staged record (Slice-1b) — speaking "say yes" here would
        double-promise the settle, so the brain answer stands instead.
        """
        if self._ask_stopped(_cancel):
            return self._ask_cancelled(request_id, mode)
        try:
            from .. import chat_transport as CT
        except Exception as exc:
            self._audit("voice_edit.full_turn_error", {
                "request_id": request_id, "error": f"import: {exc}"[:160]})
            return None
        # Wake routing: a routed target's sessionKey (bound on the request at
        # begin) runs this full turn on that session; empty/absent keeps the
        # default voice session byte-identical. Only the full-turn dispatch is
        # routed — the brain/deterministic fast paths stay on the voice session.
        routed_session = self._request_session(request_id)
        session = routed_session or self._voice_session(request_id)
        if routed_session:
            self._audit("voice_edit.full_turn_session", {
                "request_id": request_id, "session_id": routed_session})
        if self._ask_stopped(_cancel):
            return self._ask_cancelled(request_id, mode)

        def _stopped() -> bool:
            return self._ask_stopped(_cancel)

        try:
            model = self._select_model(
                _read_cfg(self.cfg, "voice_edit", "chat_model", default=""))
            res = CT.turn(self.cfg, question, session=session,
                          model=model or None, actor="voice:user",
                          timeout_s=120.0, cancel=_stopped)
        except Exception as exc:
            self._audit("voice_edit.full_turn_error", {
                "request_id": request_id, "chars": len(question),
                "error": str(exc)[:160]})
            return None
        if self._ask_stopped(_cancel):
            return self._ask_cancelled(request_id, mode)
        if not isinstance(res, dict):
            self._audit("voice_edit.full_turn_malformed", {
                "request_id": request_id})
            return None
        if str(res.get("code") or "") == "CANCELLED":
            return self._ask_cancelled(request_id, mode)
        if res.get("ok"):
            text = str(res.get("reply") or res.get("text") or "").strip()
            if text:
                # Lane 2 (2026-10-03, journal test-4 09:54:53): the worker
                # ran an irrelevant tool (local.open_app for a create-file
                # task) and summarized with assistant chatter ("Qubit is
                # available to assist ...") — the T5-forbidden shape. A
                # narration-shaped reply with no action evidence is a TYPED
                # failure (what failed + what was tried), never a spoken ok.
                if _full_turn_narration_only(text):
                    tool_used = str(res.get("tool_used")
                                    or res.get("route") or "worker")
                    self._audit("voice_edit.full_turn_narration", {
                        "request_id": request_id,
                        "tool_used": tool_used,
                        "steps": res.get("steps", ""),
                        "reply_chars": len(text)})
                    return {
                        "ok": False, "mode": mode,
                        "code": "VOICE_EDIT_WORKER_NARRATION",
                        "detail": (
                            f"the {tool_used} task finished without doing "
                            f"anything — no file was created, opened, or "
                            f"acted on"),
                        "answer": (
                            f"The {tool_used} task finished without doing "
                            f"anything — no file was created and nothing "
                            f"was opened. Please try again, for example "
                            f"'create VoiceTest dot txt in documents'."),
                        "tool_used": tool_used,
                        "task_text": question,
                        "request_id": request_id}
                self._audit("voice_edit.ask", {
                    "request_id": request_id, "mode": mode,
                    "chars": len(question), "ok": True,
                    "answer_chars": len(text), "provider": "local",
                    "model": "full-turn",
                    **(_dbg.text_fields(self.cfg, question=question,
                                        answer=text))})
                return {"ok": True, "answer": text, "mode": mode,
                        "provider": "local", "model": "full-turn",
                        "spoken": False}
            self._audit("voice_edit.full_turn_empty", {
                "request_id": request_id})
            return None
        code = str(res.get("code") or "")
        if code == "CONFIRM_REQUIRED":
            self._audit("voice_edit.full_turn_staged", {
                "request_id": request_id, "code": code,
                "confirmation_id": str(res.get("confirmation_id") or "")})
            return None
        self._audit("voice_edit.full_turn_unok", {
            "request_id": request_id, "code": code,
            "detail": str(res.get("detail") or "")[:160]})
        return None

    # -- confirm-gated tasks ---------------------------------------------
    def propose_task(self, request_id: str, text: str) -> Dict[str, Any]:
        """Stage an actionable request; NOTHING executes before confirmation."""
        req = self._get(request_id)
        clean = (text or "").strip()
        if not clean:
            raise VoiceEditError("VOICE_EDIT_EMPTY_TASK", "no task to propose")
        if len(clean) > 2000:
            clean = clean[:2000]
            truncated = True
        else:
            truncated = False
        req.task_text = clean
        self._audit("voice_edit.task.proposed", {
            "request_id": req.request_id, "chars": len(clean),
            **(_dbg.text_fields(self.cfg, task=clean))})
        summary = clean + (" (truncated to 2000 chars)" if truncated else "")
        return {"ok": True, "mode": "task", "requires_confirmation": True,
                "task_text": clean, "summary": summary,
                "request_id": req.request_id,
                "detail": "confirm to run this task"}

    def _map_settled_confirmation(self, settled: Any, *, kind: str,
                                        session_id: str, task_text: str,
                                        request_id: str) -> Dict[str, Any]:
        """Map a CT settle outcome onto the task_result shape.

        ``settled`` is the verbatim dict from ``_settle_affirmed`` (registry)
        or ``_settle_task_affirmed`` (task): success carries
        ``{ok, text/reply, session_id, route, ...}``; failure carries
        ``{ok False, code, detail, session_id, route}``. Neither carries
        ``tool_used`` — the voice turn executed through the full agent turn,
        so ``tool_used`` is ``"full-turn"``. Audits
        ``voice_edit.task.settled`` with the confirmation kind. Never raises:
        a malformed settle dict maps to a typed failure, never a phantom ok.
        """
        try:
            settled = dict(settled or {})
        except Exception:
            settled = {}
        route = str(settled.get("route") or kind or "")
        self._audit("voice_edit.task.settled", {
            "request_id": request_id, "kind": kind, "route": route,
            "code": str(settled.get("code") or ""),
            "confirmation_id": str(settled.get("confirmation_id") or ""),
            "entry": str(settled.get("entry") or settled.get("task") or "")})
        sid = str(settled.get("session_id") or settled.get("session")
                  or session_id or "")
        if settled.get("ok"):
            reply = str(settled.get("reply") or settled.get("text")
                        or settled.get("report") or "").strip()
            if not reply:
                return {"ok": False, "mode": "task_result",
                        "code": "VOICE_EDIT_TASK_EMPTY",
                        "detail": "settled confirmation returned no reply",
                        "reply": "", "tool_used": "full-turn",
                        "task_text": task_text, "request_id": request_id,
                        "session_id": sid, "route": route}
            return {"ok": True, "mode": "task_result", "reply": reply,
                    "tool_used": "full-turn", "task_text": task_text,
                    "request_id": request_id, "session_id": sid,
                    "route": route}
        code = str(settled.get("code") or "VOICE_EDIT_TASK_EMPTY")
        detail = str(settled.get("detail") or "")[:200]
        reply = str(settled.get("reply") or settled.get("text") or "")
        return {"ok": False, "mode": "task_result", "code": code,
                "detail": detail, "reply": reply, "tool_used": "full-turn",
                "task_text": task_text, "request_id": request_id,
                "session_id": sid, "route": route}

    @_counts_turn
    def task(self, text: str, *, confirm: bool = False,
             request_id: str = "") -> Dict[str, Any]:
        """Run an actionable request through the one agent turn path.

        Confirm-gated: without ``confirm=True`` this refuses typed and runs
        nothing. On confirmation it uses the SAME persisted agent turn the
        dashboard/``/qubit-chat`` use (``chat_transport.turn``), so the result
        lands in the one canonical session store and the one audit journal.
        The result is returned for the caller to speak/show — never typed.
        """
        clean = (text or "").strip()
        if not clean:
            raise VoiceEditError("VOICE_EDIT_EMPTY_TASK", "no task to run")
        self._require_estop_clear()
        self._maybe_prune_voice_sessions(request_id)
        if not confirm:
            self._audit("voice_edit.task.confirm_required", {
                "request_id": request_id, "chars": len(clean),
                **(_dbg.text_fields(self.cfg, task=clean))})
            raise VoiceEditError(
                "VOICE_EDIT_CONFIRM_REQUIRED",
                "the task needs explicit confirmation before it runs",
                status=409)
        # Wake routing: a routed target's sessionKey runs this task on that
        # session; empty/absent keeps the default voice session byte-identical.
        # The routed key also scopes the CT-staged settle below, so a
        # confirmation staged in the routed session settles in the same one.
        routed_session = self._request_session(request_id)
        if routed_session:
            self._audit("voice_edit.task_session", {
                "request_id": request_id, "session_id": routed_session})
        # Slice-1b: a voice "yes" settles a CT-staged confirmation first.
        # ask()'s _full_turn_answer leaves CONFIRM_REQUIRED unspoken (it
        # returns None so the brain answer stands) so voice never promises an
        # unsettleable "say yes"; the helper speaks staged confirmations for
        # service.task mode and the user answers "yes" -> task(confirm=True)
        # lands here. run_tool_task knows nothing of the CT-staged id, so check the voice session
        # for pending CT work (registry first, then task — mirror turn()'s
        # T4/T5 order) and settle exactly once through the shared
        # settle-once journal path (ids come from the pending record only,
        # never invented). Nothing pending -> fall through byte-identical.
        try:
            from .. import chat_transport as _CT
            _sid = routed_session or self._voice_session(request_id)
            _st = self._canonical_store()
            if _st is not None:
                try:
                    _pending_reg = _CT._pending_registry(self.cfg, _sid)
                except Exception:
                    _pending_reg = []
                if _pending_reg:
                    _settled = _CT._settle_affirmed(self.cfg, _st, _sid)
                    return self._map_settled_confirmation(
                        _settled, kind="registry", session_id=_sid,
                        task_text=clean, request_id=request_id)
                try:
                    _pending_t = _CT._pending_task(self.cfg, _sid)
                except Exception:
                    _pending_t = []
                if _pending_t:
                    _settled = _CT._settle_task_affirmed(
                        self.cfg, _st, _sid)
                    return self._map_settled_confirmation(
                        _settled, kind="task", session_id=_sid,
                        task_text=clean, request_id=request_id)
        except Exception:
            pass
        escalate, why = self._escalate_for("task")
        self._audit("voice_edit.task.run", {
            "request_id": request_id, "chars": len(clean),
            "escalate": escalate, "reason": why,
            **(_dbg.text_fields(self.cfg, task=clean))})
        # A task actually runs: the typed planner proposes one whitelisted
        # tool and the ONE capability broker executes it (source="model", the
        # same junction the orchestrator's agent path uses). Mutating tools
        # fail closed; read-only/reversible ones run. Both turns persist to the
        # one canonical session store (inside run_session) and are audited.
        try:
            from .tool_task import run_tool_task
            res = run_tool_task(
                self.cfg, clean,
                session=routed_session or self._voice_session(request_id),
                confirmed=True,
                estop_engaged=self._estop)
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "VOICE_EDIT_TASK_FAILED")
            self._audit("voice_edit.task.error", {
                "request_id": request_id, "code": code,
                "detail": str(exc)[:160]})
            return {"ok": False, "mode": "task_result", "code": code,
                    "detail": str(exc)[:200]}
        state = str(res.get("state", "") or "")
        ok = state == "OK"
        reply = str(res.get("reply") or res.get("text") or "").strip()
        tool_used = str(res.get("tool_used", "") or "")
        if ok and not reply:
            ok = False
        self._audit("voice_edit.task.result", {
            "request_id": request_id, "ok": ok, "tool_used": tool_used,
            "reply_chars": len(reply),
            **(_dbg.text_fields(self.cfg, reply=reply))})
        if not ok:
            return {"ok": False, "mode": "task_result",
                    "code": str(res.get("code") or "VOICE_EDIT_TASK_EMPTY"),
                    "detail": str(res.get("detail", "") or "")[:200],
                    "reply": reply, "tool_used": tool_used,
                    "task_text": clean, "request_id": request_id}
        return {"ok": True, "mode": "task_result", "reply": reply,
                "tool_used": tool_used, "task_text": clean,
                "request_id": request_id,
                "session_id": str(res.get("session_id",
                                          res.get("session", "")) or "")}

    def public_result(self, request_id: str) -> Dict[str, Any]:
        req = self._get(request_id)
        if not req.proposed:
            raise VoiceEditError("VOICE_EDIT_NO_PREVIEW", "no preview available")
        return {"ok": True, "request_id": request_id, "proposed": req.proposed}


__all__ = ["VoiceEditService", "REQUEST_TTL_S"]
