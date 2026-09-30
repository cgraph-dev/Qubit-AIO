"""The ONE voice decision router for the voice-typing / edit surface.

Part B of the voice-typing feature: given one spoken utterance, classify it into
exactly one :data:`MODES` outcome. It deliberately **reuses** the existing
authorities instead of inventing a second classifier:

* the deterministic edit parser (:mod:`voicebridge.voice_edit.parser`) for
  dictation and selection transforms;
* the shared intent classifier (:mod:`voicebridge.intent.classifier`) for
  task / chat / clarify routing;
* the optional :class:`~voicebridge.voice_edit.brain.BrainRouter` only to map an
  ambiguous *selection* command to an existing transform action.

The router is a pure decision function: it performs no egress, no model call of
its own (the classifier/brain are injectable seams), and ``decide()`` never
raises. Escalation is a recorded decision (:attr:`RouteDecision.escalate`); the
service audits it and owns the actual provider call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from .parser import extract_dictation, has_dictation_cue, parse

# The complete set of outcomes. Exactly one is chosen per utterance.
MODES = ("dictate", "transform", "answer", "task", "chat", "ignore")

# A conservative action/agent verb pattern used only when the classifier cannot
# be constructed at all (import/construction failure). Task if it matches,
# otherwise chat -- never a fabricated capability.
_ACTION_VERB_RE = re.compile(
    r"\b(open|close|create|write|add|edit|modify|update|refactor|rename|move|"
    r"delete|remove|implement|build|fix|patch|run|test|install|upgrade|"
    r"downgrade|configure|setup|search|find|list|show|count|analyze|generate|"
    r"scaffold|summarize|report|describe|explain|review|debug|deploy|commit|"
    r"push|pull|merge|migrate|route|delegate|ask|tell|have|let|get)\b",
    re.IGNORECASE)

# Local fallback for the question check, kept in lock-step with the canonical
# ``voicebridge.voice_edit.service._looks_like_question`` used at runtime.
_QUESTION_STARTERS = (
    "what", "who", "when", "where", "why", "how", "which", "whose",
    "is ", "are ", "do ", "does ", "did ", "can ", "could ", "would ",
    "should ", "will ", "tell me", "show me", "what's", "whats",
)

# The shared classifier labels ``system`` with a loose substring search, so a
# greeting that merely contains a keyword ("thanks for your help today") is
# tagged ``system``. The router accepts ``system`` as a task ONLY when the whole
# utterance is a short, anchored system command. These regexes mirror the
# classifier's ``SYSTEM_COMMANDS`` intents but anchored end-to-end.
_SYSTEM_ANCHORED_RE = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:qubit[,.]?\s+)?(?:"
    r"status|system\s+status|bridge\s+status|"
    r"mute|unmute|un-mute|"
    r"help|what\s+can\s+you\s+do|how\s+do\s+i\s+use\s+(?:you|this)|"
    r"stop|halt|freeze|quit\s+that|never\s?mind|"
    r"continue|resume|keep\s+going|go\s+on|carry\s+on|pick\s+it\s+back\s+up|"
    r"cancel|abort|forget\s+it|scrap\s+(?:that|it)|"
    r"model|use\s+(?:turbo|normal|slow|hard|pro|fast|deepseek|default)|"
    r"switch\s+(?:to|back\s+to)\s+"
    r"(?:turbo|normal|slow|hard|pro|fast|deepseek|default)|"
    r"set\s+(?:the\s+)?model\s+(?:to\s+)?"
    r"(?:turbo|normal|slow|hard|pro|fast|deepseek|default)|"
    r"quit|exit|goodbye|good\s+night|shut\s?down"
    r")[\s.!?]*$",
    re.IGNORECASE)

# A greeting / pleasantry anywhere in the utterance is never a system command.
_GREETING_RE = re.compile(
    r"\b(?:good\s+(?:morning|afternoon|evening|night)|thanks|thank\s+you|"
    r"how\s+are\s+you|nice\s+work|well\s+done)\b",
    re.IGNORECASE)

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _system_is_actionable(text: str) -> bool:
    """True only for a short, anchored system command -- never a greeting.

    Pure and self-contained (no classifier, no config): the classifier's
    ``system`` kind is necessary but not sufficient, because its patterns match
    keywords as substrings.
    """
    t = " ".join((text or "").split())
    if not t:
        return False
    if _GREETING_RE.search(t):
        return False
    if len(_WORD_RE.findall(t)) > 5:
        return False
    return bool(_SYSTEM_ANCHORED_RE.match(t))


def _cfg(cfg: Any, *path: str, default: Any = None) -> Any:
    """Read a config value through the one ``.get(*path, default=)`` contract."""
    if cfg is None:
        return default
    try:
        return cfg.get(*path, default=default)
    except Exception:
        return default


def _looks_like_question(text: str) -> bool:
    """Reuse the canonical question check; fall back to the local copy.

    Imported lazily so this module never participates in an import cycle with
    the service (which may import the router).
    """
    try:
        from .service import _looks_like_question as canonical
        return bool(canonical(text))
    except Exception:
        t = (text or "").strip()
        if not t:
            return False
        low = t.lower()
        return t.endswith("?") or low.startswith(_QUESTION_STARTERS)


@dataclass
class RouteDecision:
    """The single routing outcome for one utterance.

    ``escalate`` is a deterministic, recorded preference to send *this turn* to
    the API profile. It is never a silent/automatic egress: the service audits
    it and performs the actual call. It is only ever set for ``task``/``chat``/
    ``answer`` and never for ``dictate``/``transform``.
    """

    mode: str
    reason: str = ""
    task_text: str = ""
    intent: str = ""            # classifier kind when one was used
    escalate: bool = False
    params: dict = field(default_factory=dict)


class VoiceRouter:
    """Classify one utterance into exactly one :data:`MODES` outcome.

    ``classifier`` and ``brain`` are injectable test seams. When ``None`` they
    are built lazily from the shared authorities, and any construction failure
    degrades safely (a minimal heuristic for the classifier; the deterministic
    clarification for the brain) rather than raising.
    """

    def __init__(self, cfg: Any, *, classifier: Any = None, brain: Any = None
                 ) -> None:
        self.cfg = cfg
        self._classifier = classifier
        self._brain = brain
        self._classifier_broken = False

    # -- config helpers --------------------------------------------------
    def _router_mode(self) -> str:
        return str(_cfg(self.cfg, "voice_edit", "router",
                        default="deterministic") or "deterministic").lower()

    def _brain_timeout(self) -> float:
        try:
            return float(_cfg(self.cfg, "voice_edit", "brain_timeout_s",
                              default=4.0))
        except (TypeError, ValueError):
            return 4.0

    # -- lazy seams ------------------------------------------------------
    def _get_classifier(self) -> Any:
        if self._classifier is not None:
            return self._classifier
        if self._classifier_broken:
            return None
        try:
            from ..intent.classifier import Classifier
            from ..intent.state import ConfirmationState, ConversationState
            # Build a PLAIN dict: the Config object's ``.get`` differs from a
            # dict's, and the classifier expects the dict contract.
            ccfg = {
                "llm_classifier": self._router_mode() == "brain",
                "classify_timeout_s": 4.0,
            }
            self._classifier = Classifier(
                ccfg, ConfirmationState(), ConversationState())
            return self._classifier
        except Exception:
            self._classifier_broken = True
            return None

    def _get_brain(self) -> Any:
        if self._brain is None:
            from .brain import BrainRouter
            self._brain = BrainRouter()
        return self._brain

    # -- main entry ------------------------------------------------------
    def decide(self, transcript: str, *, has_selection: bool) -> RouteDecision:
        """Classify one utterance. Never raises."""
        text = (transcript or "").strip()
        if not text:
            return RouteDecision("ignore", reason="empty")
        try:
            return self._decide(text, has_selection=bool(has_selection))
        except Exception:
            # A non-selection input must never raise; with a selection the
            # transform path owns clarification and will not reach here.
            return RouteDecision("chat", reason="error_fallback")

    def _decide(self, text: str, *, has_selection: bool) -> RouteDecision:
        # Rule 2 -- explicit verbatim dictation wins over everything.
        try:
            dictated = extract_dictation(text)
        except Exception:
            dictated = None
        if dictated is not None or has_dictation_cue(text):
            return RouteDecision("dictate", reason="dictation_cue",
                                 task_text=dictated or "")

        # Rule 3 -- with a selection the transform path owns the turn
        # (including clarification); never fall through to task/chat.
        if has_selection:
            return self._decide_selection(text)

        # Rule 4 -- no selection, a question is answered, never typed. An
        # anchored system ask ("what can you do", "how do i use you") is a
        # command rather than a question, so it falls through to the classifier
        # and can be routed as a task.
        if _looks_like_question(text) and not _system_is_actionable(text):
            return RouteDecision("answer", reason="question", task_text=text)

        # Rule 5 -- classify with the shared intent classifier and map.
        return self._decide_classify(text)

    # -- rule 3 ----------------------------------------------------------
    def _decide_selection(self, text: str) -> RouteDecision:
        try:
            cmd = parse(text, selected_text="x", request_id="r")
        except Exception:
            return RouteDecision("transform", reason="clarify",
                                 params={"clarification": ""})
        if not cmd.needs_clarification:
            return RouteDecision(
                "transform", reason="edit_command",
                params={"action": cmd.action, "style": cmd.style})
        params: dict = {"action": cmd.action, "style": cmd.style,
                        "clarification": cmd.clarification}
        if self._router_mode() == "brain":
            decided = None
            try:
                decided = self._get_brain().classify(
                    self.cfg, text, has_selection=True,
                    timeout_s=self._brain_timeout())
            except Exception:
                decided = None
            if decided:
                params["decision"] = dict(decided)
                params["action"] = str(decided.get("action", "") or "")
                params["style"] = str(decided.get("style", "") or "")
                return RouteDecision("transform", reason="brain",
                                     params=params)
        return RouteDecision("transform", reason="clarify", params=params)

    # -- rule 5 ----------------------------------------------------------
    def _decide_classify(self, text: str) -> RouteDecision:
        clf = self._get_classifier()
        if clf is None:
            kind = self._heuristic_kind(text)
            return self._map_kind(kind, text, reason="heuristic", intent=kind)
        try:
            intent = clf.classify(text)
        except Exception:
            return RouteDecision("chat", reason="classifier_error",
                                 task_text=text)
        if intent is None:
            return RouteDecision("chat", reason="intent:default",
                                 task_text=text)
        kind = str(getattr(intent, "kind", "") or "")
        task = (getattr(intent, "task_text", "") or "").strip() or text
        return self._map_kind(kind, task, reason=f"intent:{kind}", intent=kind)

    def _map_kind(self, kind: str, task: str, *, reason: str,
                  intent: str) -> RouteDecision:
        if kind in ("agent", "coding", "local"):
            d = RouteDecision("task", reason=reason, task_text=task,
                              intent=intent)
        elif kind == "system":
            # The classifier's ``system`` kind is a loose substring match. Only
            # a genuine anchored system command is a task; a greeting that
            # merely contains a keyword is a chat (or an answer if it asks).
            if _system_is_actionable(task):
                d = RouteDecision("task", reason=reason, task_text=task,
                                  intent=intent)
            elif _looks_like_question(task):
                d = RouteDecision("answer", reason="question", task_text=task,
                                  intent=intent)
            elif self._strong_task_signal(task):
                # A genuine actionable request that merely contained a system
                # keyword ("help me write a python function") is still a task.
                d = RouteDecision("task", reason="system_non_actionable_action",
                                  task_text=task, intent=intent)
            else:
                d = RouteDecision("chat", reason="system_non_actionable",
                                  task_text=task, intent=intent)
        elif kind == "conversation":
            d = RouteDecision("chat", reason=reason, task_text=task,
                              intent=intent)
        elif kind in ("clarify", "confirm", "cancel"):
            d = RouteDecision("ignore", reason=reason, task_text=task,
                              intent=intent)
        else:
            d = RouteDecision("chat", reason=reason or "intent:default",
                              task_text=task, intent=intent)
        self._apply_escalation(d)
        return d

    def _heuristic_kind(self, text: str) -> str:
        return "agent" if _ACTION_VERB_RE.search(text or "") else "conversation"

    def _strong_task_signal(self, text: str) -> bool:
        """A strong task hint from the shared classifier (score >= 1.0).

        Reuses the ONE classifier's own ``_task_hint_score`` when exposed;
        a classifier that does not expose it yields no signal (conservative),
        so a pleasantry that merely matched a system keyword stays chat.
        """
        clf = self._classifier
        if clf is None or not hasattr(clf, "_task_hint_score"):
            clf = self._get_classifier()
        try:
            return float(clf._task_hint_score(text)) >= 1.0
        except Exception:
            return False

    # -- rule 7 ----------------------------------------------------------
    def _apply_escalation(self, d: RouteDecision) -> None:
        if d.mode not in ("task", "chat", "answer"):
            return
        if not bool(_cfg(self.cfg, "voice_edit", "allow_api", default=True)):
            return
        routing = str(_cfg(self.cfg, "voice_edit", "routing",
                           default="auto") or "auto").lower()
        if routing == "api" or (routing == "auto" and d.mode == "task"):
            d.escalate = True
            d.params["escalation_reason"] = f"routing={routing}"


__all__ = ["MODES", "RouteDecision", "VoiceRouter"]
