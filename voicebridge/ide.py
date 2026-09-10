"""Packet 8U — IDE launcher (honest open, never claimed control).

Detects installed editors on PATH, reports them (``status``), and opens
a workspace folder in the chosen editor as a detached process
(``open``). Opening is the whole capability: Qubit does not claim to
drive the editor — in-file work flows through the supervised coding
paths (``qubit task`` / ``qubit opencode``). Launching a desktop app is
a visible side effect, so ``open`` requires explicit confirmation and
is audited; without a graphical session the typed result says so
instead of failing silently.

Editor binaries and workspace folders are validated (no shell, argv
array only, folder must exist and be a directory). Remote names, flags,
and option smuggling in the editor/folder arguments are refused.
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIRM_CODE = "IDE_CONFIRM_REQUIRED"
ESTOP_CODE = "IDE_ESTOP"
NOT_FOUND_CODE = "IDE_NOT_FOUND"
BAD_INPUT_CODE = "IDE_BAD_INPUT"
NO_DESKTOP_CODE = "IDE_NO_DESKTOP"

KNOWN_EDITORS = ("code", "cursor", "zed", "windsurf", "subl", "gedit",
                 "code-insiders")


class IdeError(Exception):
    """Typed IDE failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def redact_spec(text: Any) -> str:
    try:
        from .redaction import redact_text as _redact
        return _redact(str(text or ""))[:300]
    except Exception:
        return "?"


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    return {"state": "denied", "code": ESTOP_CODE,
            "detail": f"emergency stop latched; {operation} refused",
            "owner": "qubit-policy",
            "rollback": "nothing launched; nothing to roll back"}


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event
        log_event(f"ide.{op}", actor=actor,
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def _which(editor: str) -> Optional[str]:
    found = shutil.which(editor)
    return found


def status(cfg: Any) -> Dict[str, Any]:
    """Detected editors on PATH plus the default (read-only)."""
    editors: List[Dict[str, Any]] = []
    for name in KNOWN_EDITORS:
        path = _which(name)
        if path:
            editors.append({"editor": name, "path": redact_spec(path)[:200]})
    try:
        configured = str(cfg.get("ide", "default", default="") or "")
    except Exception:
        configured = ""
    default = configured
    if not default or not _which(default):
        default = editors[0]["editor"] if editors else ""
    return {"state": "OK", "editors": editors, "default": default,
            "control": "launcher only; in-file work uses the supervised "
                       "coding paths"}


def _check_editor(name: str) -> str:
    text = str(name or "").strip().lower()
    if not text:
        return ""
    if text not in KNOWN_EDITORS:
        raise IdeError(BAD_INPUT_CODE,
                       f"unknown editor {text[:40]!r}; known: "
                       f"{', '.join(KNOWN_EDITORS)}")
    if _which(text) is None:
        raise IdeError(NOT_FOUND_CODE,
                       f"editor {text!r} is not installed on PATH")
    return text


def _workspace(cfg: Any, folder: str) -> str:
    text = str(folder or "").strip()
    if not text:
        from . import projects as PJ

        active = PJ.active_project(cfg)
        if active.get("state") == "OK" and active.get("project"):
            folders = active["project"].get("folders", []) or []
            if folders:
                text = str(folders[0])
    if not text:
        raise IdeError(BAD_INPUT_CODE,
                       "no workspace folder: pass one or set an active "
                       "project with `qubit project use <slug> --confirm`")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute() or "\x00" in text:
        raise IdeError(BAD_INPUT_CODE, "workspace must be an absolute path")
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise IdeError(BAD_INPUT_CODE, f"bad workspace ({exc})")
    if not resolved.is_dir():
        raise IdeError(NOT_FOUND_CODE, "workspace folder does not exist")
    return str(resolved)


def _desktop_present() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


# Adopted editor handles: launching detaches (start_new_session), so the
# bridge never blocks on an editor; handles stay referenced here (never
# garbage-collected mid-run, which keeps Popen destructors quiet) and
# exited children are reaped on the next launch. Zombies are bounded by
# launch count and cleared at bridge exit.
_DETACHED: List[Any] = []


def _reap_detached() -> None:
    live: List[Any] = []
    for proc in _DETACHED:
        try:
            if proc.poll() is None:
                live.append(proc)
        except Exception:
            pass
    _DETACHED[:] = live


try:
    atexit.register(_reap_detached)
except Exception:
    pass


def open_editor(cfg: Any, editor: str = "", folder: str = "", *,
                requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Open the workspace folder in the editor (confirmation required)."""
    if not confirmed:
        raise IdeError(CONFIRM_CODE,
                       "opening a desktop app requires explicit --confirm")
    if estop_engaged(cfg):
        return refuse_estop("ide open")
    want = _check_editor(editor)
    if not want:
        st = status(cfg)
        want = st["default"]
        if not want:
            raise IdeError(NOT_FOUND_CODE,
                           "no supported editor installed on PATH "
                           f"({', '.join(KNOWN_EDITORS)})")
    workspace = _workspace(cfg, folder)
    if not _desktop_present():
        return {"state": "UNAVAILABLE", "code": NO_DESKTOP_CODE,
                "editor": want,
                "workspace": redact_spec(workspace)[:300],
                "detail": "no graphical session (DISPLAY/WAYLAND_DISPLAY "
                          "unset); open the folder in the editor yourself, "
                          "or run this on the desktop session",
                "owner": "qubit-policy",
                "rollback": "nothing launched; nothing to roll back"}
    try:
        argv = [want, workspace]
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL,
                                    start_new_session=True)
        except OSError as exc:
            raise IdeError(NOT_FOUND_CODE, f"editor launch failed ({exc})")
        _reap_detached()
        proc.poll()
        _DETACHED.append(proc)
    except IdeError:
        raise
    except OSError as exc:
        raise IdeError(NOT_FOUND_CODE, f"editor launch failed ({exc})")
    _audit("open", requested_by,
           {"editor": want, "workspace": redact_spec(workspace)[:120]})
    return {"state": "OK", "editor": want,
            "workspace": redact_spec(workspace)[:300], "detached": True,
            "control": "launcher only; in-file work uses the supervised "
                       "coding paths"}
