"""Typed data model for the Qubit system-wide voice typing / edit surface.

These are plain dataclasses with an explicit, stable wire shape. Nothing here
touches the filesystem, the model, the clipboard, or the desktop: the schema is
the shared vocabulary used by the parser, the prompt registry, the transform
engine, the desktop adapter, and the authenticated service.

Selected text is DATA. A command is metadata about what to do with it. The
schema never carries a system/developer instruction, so no selected-text
content can be promoted into policy.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enumerations (kept as literal sets so the wire shape stays plain JSON)
# ---------------------------------------------------------------------------
ACTIONS = frozenset({
    "rewrite", "clarify", "humanize", "professionalize", "make_funny",
    "shorten", "expand", "summarize", "correct_grammar", "translate",
    "bullet_points", "email", "technical", "casual", "improve_prompt",
    "custom_transform",
    # Verbatim dictation: "Qubit, type this for me: <text>". No model call.
    "dictate_text",
    # Deterministic case transforms: no model call, no egress.
    "uppercase", "lowercase", "title_case",
})

STYLES = frozenset({
    "clear", "human", "professional", "funny", "casual", "concise",
    "persuasive", "technical", "friendly", "formal", "direct", "creative",
})

AUDIENCES = frozenset({
    "llm", "technical", "executive", "general", "customer", "email",
})

PRIVACY_MODES = frozenset({"local", "api", "auto"})

# Ordered best-first per the platform policy.
SELECTION_METHODS = (
    "accessibility",
    "app_native",
    "clipboard",
    "clipboard_fallback",
)

INSERTION_METHODS = (
    "app_native",
    "clipboard_paste",
    "typed",
    "terminal_paste",
)


@dataclass
class EditCommand:
    """The typed parse of one spoken editing instruction.

    ``needs_clarification`` is set (with a human question) whenever the parser
    could not map the utterance to a supported action/style. A clarification is
    never silently upgraded to a transform.
    """

    action: str = "custom_transform"
    style: str = ""
    language: str = ""
    audience: str = ""
    constraints: List[str] = field(default_factory=list)
    preserve_facts: bool = True
    preserve_format: bool = True
    max_length: int = 0
    selected_text: str = ""
    request_id: str = ""
    session_id: str = ""
    target_application: str = ""
    custom_instruction: str = ""
    dictated_text: str = ""
    needs_clarification: bool = False
    clarification: str = ""
    transcript: str = ""
    router: str = "deterministic"   # deterministic | brain

    def to_wire(self, *, include_selected_text: bool = False) -> Dict[str, Any]:
        rec = asdict(self)
        if not include_selected_text:
            rec["selected_text_chars"] = len(self.selected_text or "")
            rec.pop("selected_text", None)
        return rec


@dataclass
class SelectionSnapshot:
    """A captured selection plus everything needed to prove it is unchanged."""

    available: bool = False
    text: str = ""
    char_count: int = 0
    digest: str = ""              # sha256 of the captured text
    captured_via: str = ""        # one of SELECTION_METHODS (or refusal name)
    app_class: str = ""
    app_name: str = ""
    window_id: str = ""
    window_title: str = ""
    focused: bool = False
    protected: bool = False       # password/credential surface -> never touch
    clipboard_restored: bool = True
    reason: str = ""              # typed reason when unavailable

    def public(self) -> Dict[str, Any]:
        """Wire shape: never carries the selected text or its raw title."""
        return {
            "available": self.available,
            "char_count": self.char_count,
            "digest": self.digest,
            "captured_via": self.captured_via,
            "app_class": self.app_class,
            "app_name": self.app_name,
            "focused": self.focused,
            "protected": self.protected,
            "clipboard_restored": self.clipboard_restored,
            "reason": self.reason,
        }


@dataclass
class TransformResult:
    """One model transformation plus honest provenance and privacy metadata."""

    ok: bool
    text: str = ""
    code: str = ""
    detail: str = ""
    action: str = ""
    style: str = ""
    model: str = ""
    provider: str = ""
    locality: str = ""            # local | external
    privacy_mode: str = ""
    elapsed_ms: int = 0
    prompt_version: str = ""
    injection_suspected: bool = False
    clarification_returned: bool = False
    facts_dropped: List[str] = field(default_factory=list)
    truncated: bool = False

    def public(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InsertionResult:
    """A real insertion attempt. ``ok=False`` must never be reported as success."""

    ok: bool
    code: str = ""
    detail: str = ""
    method: str = ""
    app_class: str = ""
    focus_restored: bool = False
    clipboard_restored: bool = True
    reverted: bool = False
    reason: str = ""

    def public(self) -> Dict[str, Any]:
        return asdict(self)


class VoiceEditError(Exception):
    """Typed refusal for the voice-edit surface (mapped to an HTTP/WS code)."""

    def __init__(self, code: str, detail: str = "", *, status: int = 400) -> None:
        super().__init__(detail or code)
        self.code = str(code)
        self.detail = str(detail or code)
        self.status = int(status)

    def public(self) -> Dict[str, Any]:
        return {"ok": False, "state": "UNAVAILABLE", "code": self.code,
                "detail": self.detail}


__all__ = [
    "ACTIONS", "STYLES", "AUDIENCES", "PRIVACY_MODES", "SELECTION_METHODS",
    "INSERTION_METHODS", "EditCommand", "SelectionSnapshot", "TransformResult",
    "InsertionResult", "VoiceEditError",
]
