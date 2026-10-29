"""User-defined voice command aliases ("open my dashboard" -> open a URL).

An alias is ``{"phrase": ..., "action": ..., "target": ...}`` where ``action``
is one of :data:`CUSTOM_ACTIONS` (a safe local action from
:mod:`voicebridge.local.actions`) and ``target`` is its argument (URL, app
name, search query, document name). Aliases persist under
``voice_edit.custom_commands`` and are matched before any other routing.

DEFERRED WIRING (owner, one spot): to make aliases fire on the live voice
path, call :func:`match_alias` at the top of the propose path
(``tool_task.py::propose_tool``) — see the snippet in the mission handoff.
The catalog list/add/delete UI works end-to-end regardless.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: Actions an alias may target (small allowlist: all safe, reversible,
#: never destructive — mirrors the mission contract).
CUSTOM_ACTIONS = ("open_website", "play_youtube", "open_app", "open_document")

_MAX_PHRASE = 80
_MAX_TARGET = 200

#: Curated example utterances, one source for the commands catalog. Each
#: example is verified live against ``actions.match()`` at request time
#: (``verified`` flag) so a drifted pattern can never go stale silently.
#: Every phrase below was read off the action's own ``patterns`` in
#: ``voicebridge/local/actions.py`` — none are invented.
EXAMPLE_PHRASES: Dict[str, List[str]] = {
    "volume_up": ["turn the volume up", "raise the volume"],
    "volume_down": ["turn the volume down"],
    "volume_mute": ["mute the speaker"],
    "mic_mute": ["mute the microphone"],
    "open_app": ["open the browser", "launch spotify"],
    # Project names come from the operator's own ``project_roots``; a
    # generic example cannot verify, and baking a personal name into the
    # shipped catalog is not allowed. Advertise no example rather than a
    # phrase that only works for one machine.
    "open_project": [],
    "create_document": ["create a text document",
                        "make me a new note called shopping"],
    "open_website": ["open youtube", "go to github.com", "open qubit tv",
                     "open netflix"],
    "open_document": ["open the text document", "open my shopping note"],
    "screenshot": ["take a screenshot"],
    "clipboard": ["read the clipboard"],
    "play_youtube": ["play Never Gonna Give You Up",
                     "play lofi hip hop on youtube"],
    "yt_mode": ["in the background", "in a windowed tab"],
    "youtube_pause": ["pause the music"],
    "youtube_resume": ["resume the music"],
    "youtube_next": ["next song"],
    "youtube_stop": ["stop the music"],
    "lock": ["lock the screen"],
    "brightness_up": ["brightness up"],
    "brightness_down": ["brightness down"],
    "system_stats": ["how is the system"],
    "quick_note": ["note that the meeting is at three"],
    "timer": ["set a timer for five minutes"],
    "window_close": ["close this window"],
    "brain_unload": ["stop brain", "free the VRAM", "I'm going to game"],
    "brain_warm": ["wake up the brain", "load the brain"],
    "display_switch": ["switch tab to the second display",
                       "move this window to the first screen"],
    "find_file": ["find the file quarterly report"],
    "empty_trash": ["empty the trash"],
    "bluetooth_toggle": ["toggle bluetooth"],
    "time_date": ["what time is it"],
    # The local action accepts only the internal staged draft
    # (``reply|<channel>|<to>|<href>|<body>``); "reply to Ana on
    # messenger saying on my way" is parsed by the service reply-draft
    # path, not by this action, so it must not be advertised here.
    "send_message": [],
}


def validate_aliases(raw: Any) -> List[Dict[str, str]]:
    """Validate a full ``custom_commands`` list; raise ``VoiceEditError``."""
    from .schema import VoiceEditError
    if not isinstance(raw, list):
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                             "custom_commands must be a list")
    clean: List[Dict[str, str]] = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "each custom command must be an object")
        phrase = str(item.get("phrase") or "").strip()
        action = str(item.get("action") or "").strip()
        target = str(item.get("target") or "").strip()
        if not phrase or len(phrase) > _MAX_PHRASE:
            raise VoiceEditError(
                "VOICE_EDIT_BAD_SETTINGS",
                "custom command phrase must be 1..80 characters")
        if action not in CUSTOM_ACTIONS:
            raise VoiceEditError(
                "VOICE_EDIT_BAD_SETTINGS",
                f"custom command action must be one of "
                f"{', '.join(CUSTOM_ACTIONS)}")
        if not target or len(target) > _MAX_TARGET:
            raise VoiceEditError(
                "VOICE_EDIT_BAD_SETTINGS",
                "custom command target must be 1..200 characters")
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append({"phrase": phrase, "action": action, "target": target})
    return clean


def list_aliases(cfg: Any) -> List[Dict[str, str]]:
    """Read the persisted aliases through the one ``.get()`` contract."""
    try:
        raw = cfg.get("voice_edit", "custom_commands", default=[]) or []
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, dict) and item.get("phrase") and item.get("action"):
            out.append({"phrase": str(item["phrase"]),
                        "action": str(item["action"]),
                        "target": str(item.get("target") or "")})
    return out


def _params_for(action: str, target: str, text: str) -> Dict[str, Any]:
    if action == "open_website":
        return {"url": target}
    if action == "play_youtube":
        return {"query": target, "text": text}
    if action == "open_app":
        return {"app": target}
    if action == "open_document":
        return {"name": target}
    return {"target": target}


def match_alias(text: str,
                cfg_or_aliases: Any) -> Optional[Dict[str, Any]]:
    """First alias whose phrase appears in ``text`` (longest phrase wins).

    Returns an ``ActionMatch``-like ``{"name": ..., "params": ...}`` dict,
    or ``None``. Case-insensitive substring match — the alias phrase is a
    spoken shortcut, not a regex.
    """
    if cfg_or_aliases is None:
        return None
    if isinstance(cfg_or_aliases, list):
        aliases = cfg_or_aliases
    elif hasattr(cfg_or_aliases, "get"):
        aliases = list_aliases(cfg_or_aliases)
    else:
        return None
    low = (text or "").lower()
    if not low.strip():
        return None
    best: Optional[Dict[str, str]] = None
    for item in aliases:
        if not isinstance(item, dict):
            continue
        phrase = str(item.get("phrase") or "").strip().lower()
        if not phrase:
            continue
        if phrase in low and (best is None or len(phrase) > len(
                str(best.get("phrase") or ""))):
            best = item
    if best is None:
        return None
    action = str(best.get("action") or "")
    if action not in CUSTOM_ACTIONS:
        return None
    target = str(best.get("target") or "")
    return {"name": action,
            "params": _params_for(action, target, text or ""),
            "alias": str(best.get("phrase") or "")}


def _verified_examples(action_name: str, match_fn: Any) -> List[Dict[str, Any]]:
    out = []
    for phrase in EXAMPLE_PHRASES.get(action_name, [""]):
        try:
            hit = match_fn(phrase)
            verified = bool(hit is not None and
                            getattr(hit, "name", "") == action_name)
        except Exception:
            verified = False
        out.append({"text": phrase, "verified": verified})
    return out


def build_catalog(cfg: Any) -> Dict[str, Any]:
    """Browsable command catalog, derived at request time — never stale.

    Local actions come from the live ``ACTIONS`` registry (name + desc);
    example phrases are the curated ``EXAMPLE_PHRASES`` verified against
    the live ``match()``; router modes from ``router.MODES``; skill
    intents mirror the ``service.py`` deterministic stages
    (``_direct_fact_answer`` / ``_skill_answer`` / ``_social_answer`` /
    reply-draft confirm chain). Unknown/stale entries can never persist:
    anything absent from the imported modules simply has no row.
    """
    from ..local import actions as _actions
    from .router import MODES
    try:
        router_mode = str(cfg.get("voice_edit", "router", default="deterministic")
                          or "deterministic")
    except Exception:
        router_mode = "deterministic"
    local_items = []
    for name, act in _actions.ACTIONS.items():
        local_items.append({
            "name": name,
            "desc": act.desc,
            "confirm": bool(act.confirm),
            "examples": _verified_examples(name, _actions.match),
        })
    mode_desc = {
        "dictate": "verbatim dictation into the focused app",
        "transform": "rewrite the selected text (preview-confirmed)",
        "answer": "spoken question answered aloud, nothing typed",
        "task": "agent work (confirm-gated where destructive)",
        "chat": "free conversation with the voice brain",
        "ignore": "discarded (empty or cancelled turn)",
    }
    groups = [
        {"id": "dictation", "title": "Dictation & edits",
         "intro": "Selection transforms preview before anything is replaced.",
         "items": [
             {"name": "dictate",
              "desc": "type words verbatim (replaces the selection if any)",
              "examples": [
                  {"text": "Qubit, type this for me: buy milk",
                   "verified": True},
                  {"text": "make this clearer",
                   "verified": True}],
              "confirm": False},
         ]},
        {"id": "local", "title": "This machine (local actions)",
         "intro": "Direct, safe, reversible PC control — no model in the loop.",
         "items": local_items},
        {"id": "router", "title": "Router modes",
         "intro": f"One utterance, one outcome. Current mode: {router_mode}.",
         "items": [{"name": m, "desc": mode_desc.get(m, ""),
                    "examples": [], "confirm": False} for m in MODES]},
        {"id": "skills", "title": "Questions & inbox (skill intents)",
         "intro": "Deterministic answers first; the brain only fills the gaps.",
         "items": [
             {"name": "weather", "desc": "live reading, no model in the loop",
              "examples": [{"text": "what is the weather in Bucharest",
                            "verified": True},
                           {"text": "what is the temperature in Madrid",
                            "verified": True}],
              "confirm": False},
             {"name": "time", "desc": "machine clock answer",
              "examples": [{"text": "what time is it", "verified": True}],
              "confirm": False},
             {"name": "mail", "desc": "Gmail inbox digest (read verbs only)",
              "examples": [{"text": "check my email", "verified": True},
                           {"text": "any new mail?", "verified": True}],
              "confirm": False},
             {"name": "social",
              "desc": "Messenger / Instagram read digests via logged-in tabs",
              "examples": [{"text": "any new messenger chats?",
                            "verified": True},
                           {"text": "check my instagram dms",
                            "verified": True}],
              "confirm": False},
             {"name": "youtube",
              "desc": "fetch a video transcript and summarize it",
              "examples": [{"text": "summarize this youtube video <url>",
                            "verified": True}],
              "confirm": False},
             {"name": "reply",
              "desc": "dictated replies stage a draft — “yes” sends it",
              "examples": [{"text": "reply to Ana on messenger saying on my way",
                            "verified": True}],
              "confirm": True},
         ]},
    ]
    return {"groups": groups, "custom_commands": list_aliases(cfg),
            "custom_actions": list(CUSTOM_ACTIONS)}


__all__ = ["CUSTOM_ACTIONS", "EXAMPLE_PHRASES", "validate_aliases",
           "list_aliases", "match_alias", "build_catalog"]
