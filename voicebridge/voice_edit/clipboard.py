"""System-clipboard copy for redo/rewrite transform outputs.

Owner ask (2026-10-04 voice-final lane): after a redo the result must sit in
the system clipboard so it can be pasted; when text is selected, THAT text is
redone.

Backend detection (X11 ``xclip``/``xsel`` vs Wayland ``wl-copy`` — whatever is
present; a missing clipboard is a LOUD audited failure, never a silent skip):

* X11 session (``DISPLAY`` set): prefer ``xclip``, fall back to ``xsel``.
* Wayland session (``WAYLAND_DISPLAY`` set, no ``DISPLAY``): ``wl-copy``.
* Anything present wins over session preference (headless hosts still copy
  when a tool happens to be installed); nothing present -> ``"none"``.

Selection source per path (the no-keys boundary — read before touching):

* PTT / capture path: the turn carries a real selection (explicit key-hold
  over a selection via the service capture). The SELECTED text is rewritten
  (trailing words never override it — the parser already prefers a present
  selection) and the result is copied to the clipboard. Paste-back into the
  app happens ONLY through the existing safe primitive
  (``DesktopAdapter.insert_text``: save/set/paste/restore); there is no other
  insertion path.
* Wake path: selection-less BY DESIGN (``_capture_selection_local`` builds a
  focus-only snapshot; the service capture would synthesize Ctrl+C into the
  focused window, which is SIGINT in a terminal — P0 2026-10-03). The redo
  suffix (trailing words) is the transform input, the result is copied to
  the clipboard and SPOKEN — replacement-in-place (retyping over a selection)
  is FORBIDDEN on the wake path. This module performs clipboard WRITES only
  and never synthesizes keys, so it is safe on both paths; it must never
  gain a capture/paste call.

No secrets: only char counts and backend names are reported, never text.
"""
from __future__ import annotations

import os as _os
import shutil as _shutil
import subprocess as _subprocess
from typing import Any, Callable, Dict, Optional

#: Typed codes for clipboard outcomes (audit-loud, never silent).
CLIPBOARD_UNAVAILABLE = "VOICE_EDIT_CLIPBOARD_UNAVAILABLE"
CLIPBOARD_SET_FAILED = "VOICE_EDIT_CLIPBOARD_SET_FAILED"
CLIPBOARD_EMPTY = "VOICE_EDIT_CLIPBOARD_EMPTY"


def detect_backend(*, which: Optional[Callable[[str], Optional[str]]] = None,
                   env: Optional[Dict[str, str]] = None) -> str:
    """Name the clipboard write backend: ``xclip`` | ``xsel`` | ``wl-copy``.

    Session-aware preference, presence-decided: the session picks the order,
    but only an installed tool wins. Returns ``"none"`` when nothing is
    present (the caller must fail LOUD, never silently skip).
    """
    _which = which or _shutil.which
    _env = env if env is not None else dict(_os.environ)
    try:
        disp = str(_env.get("DISPLAY", "") or "")
        wayland = str(_env.get("WAYLAND_DISPLAY", "") or "")
    except Exception:
        disp, wayland = "", ""
    if wayland and not disp:
        ordered = ("wl-copy", "xclip", "xsel")
    else:
        ordered = ("xclip", "xsel", "wl-copy")
    for name in ordered:
        try:
            if _which("wl-clipboard" if name == "wl-copy" else name):
                return name
            if name == "wl-copy" and _which("wl-copy"):
                return name
        except Exception:
            continue
    # ``wl-copy`` ships inside the ``wl-clipboard`` package on some distros
    # (binary ``wl-copy`` present while a bare ``wl-clipboard`` probe is
    # not, and vice versa) — the loop above already covers both probes.
    return "none"


def _direct_copy(backend: str, text: str, *,
                 run: Optional[Callable[..., Any]] = None,
                 env: Optional[Dict[str, str]] = None) -> bool:
    """Write ``text`` via a clipboard binary directly. Never raises."""
    data = (text or "").encode("utf-8")
    _env = env if env is not None else dict(_os.environ)
    try:
        if backend == "xclip":
            argv = ["xclip", "-selection", "clipboard", "-i",
                    "-t", "UTF8_STRING"]
        elif backend == "xsel":
            argv = ["xsel", "--clipboard", "--input"]
        elif backend == "wl-copy":
            argv = ["wl-copy"]
        else:
            return False
        if run is not None:
            rc, _, _ = run(argv, input_bytes=data, env=_env)
            return int(rc) == 0
        proc = _subprocess.run(argv, input=data,
                               stdout=_subprocess.DEVNULL,
                               stderr=_subprocess.DEVNULL,
                               timeout=4, env=_env)
        return int(proc.returncode) == 0
    except Exception:
        return False


def copy_text(text: str, *, desktop: Any = None,
              run: Optional[Callable[..., Any]] = None,
              which: Optional[Callable[[str], Optional[str]]] = None,
              env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Copy one transform output to the system clipboard. Never raises.

    Order: the injected desktop adapter first (the tested ``set_clipboard``
    path — xclip / wl-copy with owner-process plumbing), then a direct
    binary write for the backend :func:`detect_backend` names (covers an
    X11 host with only ``xsel``). Empty text is never a copy. A missing
    clipboard is ``ok: False`` with a typed code — the caller audits it
    LOUD and surfaces it; success is never claimed without a verified
    write (adapter True / direct rc 0).
    """
    if not (text or "").strip():
        return {"ok": False, "backend": "none", "code": CLIPBOARD_EMPTY}
    # 1. The desktop adapter (safe, plumbed, already session-aware).
    if desktop is not None:
        try:
            clip_tool = desktop._clip_tool() if hasattr(desktop, "_clip_tool") \
                else ""
        except Exception:
            clip_tool = ""
        try:
            if desktop.set_clipboard(text):
                backend = str(clip_tool or "desktop") or "desktop"
                return {"ok": True, "backend": backend, "code": ""}
        except Exception:
            pass
    # 2. Direct binary write (covers xsel-only X11; adapter covers the rest).
    backend = detect_backend(which=which, env=env)
    if backend != "none" and _direct_copy(backend, text, run=run, env=env):
        return {"ok": True, "backend": backend, "code": ""}
    # 3. LOUD failure — never a silent skip.
    code = CLIPBOARD_SET_FAILED if backend != "none" else CLIPBOARD_UNAVAILABLE
    return {"ok": False, "backend": backend, "code": code}


__all__ = ["detect_backend", "copy_text", "CLIPBOARD_UNAVAILABLE",
           "CLIPBOARD_SET_FAILED", "CLIPBOARD_EMPTY"]
