"""Local PC actions: direct, safe, reversible machine control.

Only whitelisted actions live here. Everything risky (rm, git reset --hard,
package installs, browser actions with logins/payments, service restarts,
file deletion) is *not* implemented locally — those route to OpenClaw where
the safety policy + spoken confirmations apply.

Each action returns a short spoken status string.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..logging_setup import get_logger, log_event

log = get_logger()


@dataclass
class ActionMatch:
    name: str
    params: Dict[str, Any] = field(default_factory=dict)
    text: str = ""


@dataclass
class LocalAction:
    name: str
    desc: str
    fn: Callable[[Dict[str, Any]], str]
    patterns: List[str] = field(default_factory=list)
    confirm: bool = False  # local actions are all safe by construction; kept for future


def _run(args: List[str], timeout: float = 10.0) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.stdout.strip()


def _pactl(args: List[str]) -> str:
    return _run(["pactl"] + args)


def _a_volume_up(_: Dict[str, Any]) -> str:
    _pactl(["set-sink-volume", "@DEFAULT_SINK@", "+10%"])
    return "Volume up."


def _a_volume_down(_: Dict[str, Any]) -> str:
    _pactl(["set-sink-volume", "@DEFAULT_SINK@", "-10%"])
    return "Volume down."


def _a_volume_mute(_: Dict[str, Any]) -> str:
    _pactl(["set-sink-mute", "@DEFAULT_SINK@", "toggle"])
    return "Speaker mute toggled."


def _a_mic_mute(_: Dict[str, Any]) -> str:
    _pactl(["set-source-mute", "@DEFAULT_SOURCE@", "toggle"])
    return "Microphone mute toggled."


def _a_open_app(params: Dict[str, Any]) -> str:
    app = params.get("app", "")
    targets = {
        "browser": ["xdg-open", "https://duckduckgo.com"],
        "editor": ["xdg-open", "/home/trick/Projects/Repos"],
        "vscode": ["code", "/home/trick/Projects/Repos"],
        "terminal": ["gnome-terminal"],
        "discord": ["discord"],
        "telegram": ["telegram-desktop"],
        "files": ["xdg-open", "/home/trick"],
        "fl studio": ["wine", "/home/trick/.wine/drive_c/Program Files/Image-Line/FL Studio 21/FL64.exe"],
        "spotify": ["spotify"],
    }
    argv = targets.get(app)
    if not argv:
        return f"I don't have a launcher for {app or 'that'}."
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Opening {app}."
    except Exception as exc:
        return f"Could not open {app}: {exc}"


def _a_screenshot(_: Dict[str, Any]) -> str:
    out = "/home/trick/Pictures/screenshot-%s.png" % __import__("time").strftime("%Y%m%d-%H%M%S")
    if shutil.which("gnome-screenshot"):
        _run(["gnome-screenshot", "-f", out])
    elif shutil.which("scrot"):
        _run(["scrot", out])
    else:
        return "No screenshot tool found."
    return "Screenshot saved."


def _a_media_playpause(_: Dict[str, Any]) -> str:
    if shutil.which("playerctl"):
        _run(["playerctl", "play-pause"])
        return "Media toggled."
    return "playerctl not installed."


def _a_clipboard(_: Dict[str, Any]) -> str:
    if shutil.which("xclip"):
        return _run(["xclip", "-o", "-selection", "clipboard"])[:300] or "Clipboard is empty."
    return "xclip not installed."


def _a_lock(_: Dict[str, Any]) -> str:
    _run(["loginctl", "lock-session"])
    return "Locking the screen."


# ---------------------------------------------------------------------------
ACTIONS: Dict[str, LocalAction] = {
    "volume_up": LocalAction("volume_up", "raise speaker volume", _a_volume_up,
                             patterns=[r"\b(volume|sound|audio|speaker)[s]?\s+(up|louder|increase|raise|boost)\b",
                                       r"\b(raise|increase|boost)\s+(the\s+)?(volume|sound)\b",
                                       r"\bturn (it |the (volume|sound) )?up\b"]),
    "volume_down": LocalAction("volume_down", "lower speaker volume", _a_volume_down,
                               patterns=[r"\b(volume|sound|audio|speaker)[s]?\s+(down|lower|decrease|reduce|quieter)\b",
                                         r"\b(lower|decrease|reduce)\s+(the\s+)?(volume|sound)\b",
                                         r"\bturn (it |the (volume|sound) )?down\b"]),
    "volume_mute": LocalAction("volume_mute", "toggle speaker mute", _a_volume_mute,
                               patterns=[r"\b(mute|unmute) (the )?(speaker|sound|volume)\b",
                                         r"\b(speaker|sound) (mute|off)\b"]),
    "mic_mute": LocalAction("mic_mute", "toggle microphone mute", _a_mic_mute,
                            patterns=[r"\b(mute|unmute) (the )?(microphone|mic)\b"]),
    "open_app": LocalAction("open_app", "launch an application", _a_open_app,
                            patterns=[r"\b(open|launch|start|run) (the )?(browser|editor|vscode|terminal|discord|telegram|files|fl studio|spotify)\b"]),
    "screenshot": LocalAction("screenshot", "take a screenshot", _a_screenshot,
                              patterns=[r"\b(take|grab) (a |an? )?(screenshot|screen ?shot)\b"]),
    "media_playpause": LocalAction("media_playpause", "play/pause media", _a_media_playpause,
                                   patterns=[r"\b(play|pause|resume) (the )?(music|media|song|video)\b",
                                             r"\b(play|pause) it\b"]),
    "clipboard": LocalAction("clipboard", "read the clipboard", _a_clipboard,
                             patterns=[r"\b(read|what'?s on|what is on) (the )?clipboard\b"]),
    "lock": LocalAction("lock", "lock the screen", _a_lock,
                        patterns=[r"\b(lock|lock the screen)\b"]),
}

APP_NAMES = {
    "browser": "browser", "chrome": "browser", "firefox": "browser",
    "editor": "editor", "vscode": "vscode", "vs code": "vscode", "code": "vscode",
    "terminal": "terminal", "console": "terminal", "discord": "discord",
    "telegram": "telegram", "files": "files", "file manager": "files",
    "fl studio": "fl studio", "fl": "fl studio", "spotify": "spotify",
}


def match(text: str) -> Optional[ActionMatch]:
    """Return the first matching local action, or None."""
    low = text.lower().strip()
    for name, act in ACTIONS.items():
        for pat in act.patterns:
            import re
            m = re.search(pat, low)
            if m:
                params: Dict[str, Any] = {}
                if name == "open_app":
                    for word, canon in APP_NAMES.items():
                        if word in low:
                            params["app"] = canon
                            break
                return ActionMatch(name=name, params=params, text=text)
    return None


def execute(m: ActionMatch) -> str:
    act = ACTIONS.get(m.name)
    if act is None:
        return "Unknown action."
    try:
        msg = act.fn(m.params)
        log_event("local.action", action=m.name, result=msg[:200])
        return msg
    except Exception as exc:
        log_event("local.action.error", action=m.name, error=str(exc)[:160])
        return f"Action failed: {exc}"
