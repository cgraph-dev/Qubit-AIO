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

REQUEST_TTL_S = 300.0

_QUESTION_STARTERS = (
    "what", "who", "when", "where", "why", "how", "which", "whose",
    "is ", "are ", "do ", "does ", "did ", "can ", "could ", "would ",
    "should ", "will ", "tell me", "show me", "what's", "whats",
)


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
            "active_requests": len(self._reqs),
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
        self._audit("voice_edit.begin", {"request_id": rid,
                                         "session_id": session_id or "",
                                         "target_app": target_app or ""})
        return {"ok": True, "request_id": rid, "session_id": session_id,
                "ttl_s": self.ttl()}

    def _get(self, request_id: str) -> _Request:
        with self._lock:
            self._prune()
            req = self._reqs.get(request_id)
        if req is None:
            raise VoiceEditError("VOICE_EDIT_UNKNOWN_REQUEST",
                                 "unknown or expired request id", status=404)
        return req

    def _prune(self) -> None:
        now = time.monotonic()
        ttl = self.ttl()
        for rid in [r for r, x in self._reqs.items()
                    if now - x.created_at > ttl]:
            self._reqs.pop(rid, None)

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
        }

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
        has_selection = bool(
            req.selection is not None and req.selection.available
            and (req.original or "").strip())
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
            out = self.transform(request_id, transcript)
            out["decision"] = decision_wire
            return out
        if decision.mode in ("answer", "chat"):
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
        return {"ok": True, "mode": "applied", "request_id": req.request_id,
                "insertion": ins.public(), "applied": req.proposed}

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

    def _voice_session(self) -> str:
        """The stable session id for the spoken conversation.

        A dedicated session keeps the voice dialogue coherent instead of
        mixing it with unrelated dashboard/chat turns (and test probes) in the
        shared default session; it is still the SAME one canonical store.
        """
        try:
            from ..agent import resolve_session_id
            return resolve_session_id(self.cfg, "voice-chat")
        except Exception:
            return "voice-chat"

    def _conversation_context(self, pending: str) -> str:
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
                    store.messages(self._voice_session(), limit=200)
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
        if _re.search(r"\bwhat time\b|\bcurrent time\b|\btime is it\b"
                      r"|\bwhat day is it\b|\bwhat date\b"
                      r"|\bwhat's today\b|\bwhat is today\b",
                      q, _re.IGNORECASE):
            try:
                return f"It is {_time.strftime('%A %H:%M')}."
            except Exception:
                return ""
        return ""

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
        question = (text or "").strip()
        if not question:
            raise VoiceEditError("VOICE_EDIT_EMPTY_QUESTION",
                                 "no question to answer")
        self._require_estop_clear()
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
        try:
            from ..providers import gateway as GW
            # Conversation memory: pass the bounded prior transcript as the
            # structured conversation_prompt (the adapter formats dialogue
            # correctly); folding it into the prompt string made the small
            # local model echo the last answer.
            convo = self._conversation_context(question)
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
            res = GW.run_role(question, self.cfg, role="chat",
                              profile_id=profile or None,
                              model=model_override,
                              max_tokens=max_tokens,
                              timeout_s=30.0,
                              reasoning=reasoning,
                              conversation_prompt=convo,
                              context=ctx)
        except Exception as exc:
            return {"ok": False, "code": "VOICE_EDIT_ASK_FAILED",
                    "detail": str(exc)[:160]}
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
        # brain answer returns exactly as before.
        if not ok or not answer or _looks_like_brain_refusal(answer):
            full = self._full_turn_answer(question, request_id=request_id,
                                          mode=mode)
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
                            mode: str = "answer") -> Optional[Dict[str, Any]]:
        """Decision-26 fallback: answer through the full agent turn.

        Dispatches through ``chat_transport.turn`` — the same persisted,
        broker/policy/audit/confirm/e-stop turn ``/qubit-chat`` uses, with
        its web/local/registry/agent_task branches and the voice-local
        model. The turn persists itself to the canonical voice session, so
        the caller must NOT also ``_remember_ask_exchange`` (that would
        double-write the exchange). Returns the ask-shaped dict, or None
        when the full turn cannot answer (caller keeps the brain outcome).

        Staged confirmations (``CONFIRM_REQUIRED`` + ``confirmation_id``)
        deliberately fall through to None: voice "yes" settles through the
        service task path, which cannot settle a chat-transport-staged
        confirmation yet (Slice-1b) — speaking an unsettleable "say yes"
        would be a lie, so the brain answer stands instead.
        """
        try:
            from .. import chat_transport as CT
        except Exception as exc:
            self._audit("voice_edit.full_turn_error", {
                "request_id": request_id, "error": f"import: {exc}"[:160]})
            return None
        try:
            model = self._select_model(
                _read_cfg(self.cfg, "voice_edit", "chat_model", default=""))
            res = CT.turn(self.cfg, question, session="voice-chat",
                          model=model or None, actor="voice:user",
                          timeout_s=120.0)
        except Exception as exc:
            self._audit("voice_edit.full_turn_error", {
                "request_id": request_id, "chars": len(question),
                "error": str(exc)[:160]})
            return None
        if not isinstance(res, dict):
            self._audit("voice_edit.full_turn_malformed", {
                "request_id": request_id})
            return None
        if res.get("ok"):
            text = str(res.get("reply") or res.get("text") or "").strip()
            if text:
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
        if not confirm:
            self._audit("voice_edit.task.confirm_required", {
                "request_id": request_id, "chars": len(clean),
                **(_dbg.text_fields(self.cfg, task=clean))})
            raise VoiceEditError(
                "VOICE_EDIT_CONFIRM_REQUIRED",
                "the task needs explicit confirmation before it runs",
                status=409)
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
            res = run_tool_task(self.cfg, clean, confirmed=True,
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
