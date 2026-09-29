"""Desktop selection / insertion adapter.

Strongest available method per platform, in order:

1. **app-native / accessibility** -- not available on this host (no AT-SPI
   ``gi``); the adapter reports that honestly instead of pretending.
2. **clipboard capture with Ctrl+C** -- the real path on X11: save the user's
   clipboard (every advertised target, binary-safe), synthesize Ctrl+C, read
   the new clipboard text, then restore the original clipboard exactly.
3. **safe clipboard fallback** for insertion -- set the clipboard, synthesize
   Ctrl+V (or Ctrl+Shift+V / Shift+Insert in terminals), then restore.

Wayland is supported only through ``wl-clipboard``/``wtype`` when installed;
without them the adapter returns a typed ``DESKTOP_SELECTION_UNAVAILABLE``.

The adapter never logs clipboard contents, selected text, window titles, or
synthesized text. All subprocess use is bounded by a timeout.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .schema import InsertionResult, SelectionSnapshot

_RUN_TIMEOUT = 4.0
_CLIP_READ_TIMEOUT = 2.0
_MAX_CLIP_BYTES = 4 << 20

# Applications whose focused surface must never be read or written.
_PROTECTED_CLASSES = {
    "keepassxc", "keepassx", "1password", "bitwarden", "seahorse",
    "gnome-keyring", "kwallet", "kwalletd5", "enpass", "bitwarden-bin",
    "polkit-gnome-authentication-agent-1", "lxpolkit", "mate-polkit",
}
_PROTECTED_TITLE_MARKERS = (
    "password", "passphrase", "unlock", "authentication required",
    "sign in", "secret key", "recovery phrase", "seed phrase",
)
# Terminal emulators need the terminal paste chord.
_TERMINAL_CLASSES = {
    "gnome-terminal", "gnome-terminal-server", "konsole", "xterm",
    "alacritty", "kitty", "terminator", "tilix", "xfce4-terminal",
    "org.gnome.terminal", "wezterm", "foot", "st", "urxvt", "rxvt",
}
# Electron apps where paste chords can differ.
_ELECTRON_HINTS = ("code", "code-oss", "vscodium", "slack", "discord",
                   "obsidian", "element", "signal", "notion", "figma")


def _looks_like_text(text: str) -> bool:
    """Reject binary-ish clipboard payloads (e.g. custom app MIME formats)."""
    if not text:
        return False
    if "\x00" in text:
        return False
    sample = text[:2000]
    printable = sum(1 for ch in sample
                    if ch.isprintable() or ch in "\n\r\t")
    return (printable / max(1, len(sample))) >= 0.85


@dataclass
class ClipboardBackup:
    targets: List[str] = field(default_factory=list)
    data: Dict[str, bytes] = field(default_factory=dict)
    text: str = ""
    complete: bool = True
    owners: List[Any] = field(default_factory=list)


class DesktopAdapter:
    """X11/Wayland selection and insertion via bounded external tools."""

    def __init__(self, *, run: Optional[Callable[..., Any]] = None,
                 popen: Optional[Callable[..., Any]] = None,
                 which: Optional[Callable[[str], Optional[str]]] = None,
                 env: Optional[Dict[str, str]] = None) -> None:
        self._run = run or self._default_run
        self._popen = popen or self._default_popen
        self._which = which or shutil.which
        self._env = env if env is not None else dict(os.environ)
        self._last_backup: Optional[ClipboardBackup] = None
        self._last_key_error = ""
        self._set_owner: Optional[Any] = None

    # -- process plumbing ------------------------------------------------
    @staticmethod
    def _default_run(argv: List[str], *, input_bytes: Optional[bytes] = None,
                     timeout: float = _RUN_TIMEOUT,
                     env: Optional[Dict[str, str]] = None) -> Tuple[int, bytes, bytes]:
        try:
            proc = subprocess.run(argv, input=input_bytes,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout, env=env)
            return proc.returncode, proc.stdout or b"", proc.stderr or b""
        except (subprocess.TimeoutExpired, OSError):
            return 124, b"", b""

    @staticmethod
    def _default_popen(argv: List[str], *, input_bytes: Optional[bytes] = None,
                       env: Optional[Dict[str, str]] = None) -> Any:
        try:
            return subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True, env=env)
        except OSError:
            return None

    def _tool(self, name: str) -> bool:
        return bool(self._which(name))

    def session(self) -> Dict[str, Any]:
        disp = str(self._env.get("DISPLAY", "") or "")
        wayland = str(self._env.get("WAYLAND_DISPLAY", "") or "")
        if wayland and not disp:
            return {"session_type": "wayland", "display": "", "x11": False}
        return {"session_type": "x11" if disp else "unknown",
                "display": disp, "x11": bool(disp)}

    # -- clipboard -------------------------------------------------------
    def _clip_tool(self) -> Optional[str]:
        if self._tool("xclip"):
            return "xclip"
        if self._tool("wl-clipboard") or self._tool("wl-copy"):
            return "wl"
        return None

    def save_clipboard(self) -> ClipboardBackup:
        tool = self._clip_tool()
        bak = ClipboardBackup()
        if tool is None:
            bak.complete = False
            return bak
        if tool == "xclip":
            rc, out, _ = self._run(["xclip", "-selection", "clipboard",
                                    "-t", "TARGETS", "-o"],
                                   timeout=_CLIP_READ_TIMEOUT, env=self._env)
            if rc != 0 or not out.strip():
                # Empty or unadvertised clipboard: nothing to preserve. This is
                # the common case and must not block capture/insertion.
                bak.complete = True
                return bak
            targets = [t.strip() for t in out.decode("latin-1").split() if t.strip()]
            for t in targets[:32]:
                rc, data, _ = self._run(["xclip", "-selection", "clipboard",
                                         "-t", t, "-o"],
                                        timeout=_CLIP_READ_TIMEOUT, env=self._env)
                if rc == 0 and data:
                    bak.targets.append(t)
                    bak.data[t] = data[:_MAX_CLIP_BYTES]
            if "UTF8_STRING" in bak.data:
                bak.text = bak.data["UTF8_STRING"].decode("utf-8", "replace")
            elif "STRING" in bak.data:
                bak.text = bak.data["STRING"].decode("utf-8", "replace")
        else:
            rc, out, _ = self._run(["wl-paste", "-n", "--list-types"],
                                   timeout=_CLIP_READ_TIMEOUT, env=self._env)
            if rc == 0:
                for t in out.decode("latin-1").split():
                    rc2, data, _ = self._run(["wl-paste", "-n", "-t", t],
                                             timeout=_CLIP_READ_TIMEOUT,
                                             env=self._env)
                    if rc2 == 0 and data:
                        bak.targets.append(t)
                        bak.data[t] = data[:_MAX_CLIP_BYTES]
            rc, out, _ = self._run(["wl-paste", "-n"], timeout=_CLIP_READ_TIMEOUT,
                                   env=self._env)
            if rc == 0:
                bak.text = out.decode("utf-8", "replace")
        self._last_backup = bak
        return bak

    def set_clipboard(self, text: str) -> bool:
        tool = self._clip_tool()
        data = (text or "").encode("utf-8")
        owner = None
        if tool == "xclip":
            owner = self._popen(["xclip", "-selection", "clipboard",
                                 "-i", "-t", "UTF8_STRING"], env=self._env)
        elif tool == "wl":
            owner = self._popen(["wl-copy"], env=self._env)
        if owner is None:
            return False
        try:
            owner.stdin.write(data)
            owner.stdin.close()
        except (BrokenPipeError, OSError, AttributeError):
            return False
        self._set_owner = owner
        return True

    def restore_clipboard(self, bak: Optional[ClipboardBackup] = None) -> bool:
        bak = bak if bak is not None else self._last_backup
        if bak is None:
            return True
        # Kill the clipboard owners we started (so the old value can return).
        owners = list(bak.owners)
        if self._set_owner is not None:
            owners.append(self._set_owner)
            self._set_owner = None
        for owner in owners:
            try:
                owner.kill()
            except Exception:
                pass
        tool = self._clip_tool()
        if tool is None:
            return not bak.data
        ok = True
        for t, data in bak.data.items():
            argv = (["xclip", "-selection", "clipboard", "-i", "-t", t]
                    if tool == "xclip" else ["wl-copy", "-t", t])
            owner = self._popen(argv, env=self._env)
            if owner is None:
                ok = False
                continue
            try:
                owner.stdin.write(data)
                owner.stdin.close()
                bak.owners.append(owner)
            except (BrokenPipeError, OSError, AttributeError):
                ok = False
        return ok

    # -- focus / protection ---------------------------------------------
    def focused_window(self) -> Dict[str, str]:
        if not self.session()["x11"] or not self._tool("xdotool"):
            return {"window_id": "", "app_class": "", "app_name": "",
                    "window_title": ""}
        rc, out, _ = self._run(["xdotool", "getactivewindow"],
                               timeout=_RUN_TIMEOUT, env=self._env)
        if rc != 0:
            return {"window_id": "", "app_class": "", "app_name": "",
                    "window_title": ""}
        wid = out.decode("latin-1").strip()
        app_class = ""
        rc, cls, _ = self._run(["xdotool", "getwindowclassname", wid],
                               timeout=_RUN_TIMEOUT, env=self._env)
        if rc == 0:
            app_class = cls.decode("latin-1").strip()
        # This xdotool build may lack `getwindowclassname`; xprop is the
        # portable fallback (WM_CLASS = "<instance>", "<class>").
        if not app_class and self._tool("xprop"):
            rc, wmc, _ = self._run(["xprop", "-id", wid, "WM_CLASS"],
                                   timeout=_RUN_TIMEOUT, env=self._env)
            if rc == 0:
                m = re.findall(r'"([^"]*)"', wmc.decode("utf-8", "replace"))
                if m:
                    app_class = (m[1] if len(m) > 1 else m[0]).split("(")[0]
        rc, nm, _ = self._run(["xdotool", "getwindowname", wid],
                              timeout=_RUN_TIMEOUT, env=self._env)
        title = nm.decode("utf-8", "replace").strip() if rc == 0 else ""
        return {"window_id": wid, "app_class": app_class.strip().lower(),
                "app_name": app_class.strip().lower(), "window_title": title}

    def is_protected(self, app_class: str, window_title: str) -> bool:
        cls = (app_class or "").lower()
        if cls in _PROTECTED_CLASSES:
            return True
        low_title = (window_title or "").lower()
        return any(m in low_title for m in _PROTECTED_TITLE_MARKERS)

    def is_terminal(self, app_class: str) -> bool:
        return (app_class or "").lower() in _TERMINAL_CLASSES

    def is_fullscreen(self, window_id: str = "") -> bool:
        """True when the focused window is fullscreen (a game/video surface).

        Text injection into a fullscreen app is almost always unintended, so
        the voice-edit surface refuses it by default. Uses
        ``_NET_WM_STATE_FULLSCREEN``; falls back to a geometry match.
        """
        if not self.session()["x11"]:
            return False
        wid = window_id or self.focused_window().get("window_id", "")
        if not wid:
            return False
        if self._tool("xprop"):
            rc, out, _ = self._run(["xprop", "-id", wid, "_NET_WM_STATE"],
                                   timeout=_RUN_TIMEOUT, env=self._env)
            if rc == 0 and out.strip():
                # Authoritative: a *maximized* window also covers the screen but
                # is NOT fullscreen, so never fall through to geometry here.
                return b"_NET_WM_STATE_FULLSCREEN" in out
        # Geometry fallback (only when _NET_WM_STATE is unavailable).
        try:
            rc, wout, _ = self._run(["xdotool", "getwindowgeometry", "--shell", wid],
                                    timeout=_RUN_TIMEOUT, env=self._env)
            rc2, sout, _ = self._run(["xdotool", "getdisplaygeometry"],
                                     timeout=_RUN_TIMEOUT, env=self._env)
            if rc == 0 and rc2 == 0:
                g = dict(line.split("=", 1) for line in
                         wout.decode().splitlines() if "=" in line)
                disp = sout.decode().split()
                if len(disp) >= 2 and g.get("WIDTH") and g.get("HEIGHT"):
                    return (int(g["WIDTH"]) >= int(disp[0]) and
                            int(g["HEIGHT"]) >= int(disp[1]))
        except (ValueError, KeyError, AttributeError):
            pass
        return False

    def activate_window(self, window_id: str) -> bool:
        """Restore focus to a captured window before insertion."""
        if not window_id or not self.session()["x11"] or \
                not self._tool("xdotool"):
            return False
        rc, _, _ = self._run(["xdotool", "windowactivate", "--sync",
                              window_id], timeout=_RUN_TIMEOUT, env=self._env)
        return rc == 0

    # -- key synthesis ----------------------------------------------------
    def _key(self, chord: str, *, clear_modifiers: bool = False) -> bool:
        # ``--clearmodifiers`` would release a held Right-Ctrl, which the global
        # hotkey listener sees as a PTT release. Default to NOT clearing: a
        # plain ``ctrl+c`` while Right-Ctrl is held is still Ctrl+C.
        if self.session()["x11"] and self._tool("xdotool"):
            argv = ["xdotool", "key"]
            if clear_modifiers:
                argv.append("--clearmodifiers")
            argv.append(chord)
            rc, _, err = self._run(argv, timeout=_RUN_TIMEOUT, env=self._env)
            if rc != 0:
                self._last_key_error = (err or b"")[-160:].decode(
                    "utf-8", "replace")
            return rc == 0
        if self._tool("wtype"):
            rc, _, _ = self._run(["wtype", "-M", "ctrl", "c"]
                                 if chord.endswith("+c") else
                                 ["wtype", "-M", "ctrl", "v"],
                                 timeout=_RUN_TIMEOUT, env=self._env)
            return rc == 0
        return False

    def _paste_chord(self, app_class: str) -> str:
        if self.is_terminal(app_class):
            return "Control_L+Shift_L+v" if self._tool("xdotool") else "shift+Insert"
        return "Control_L+v"

    def _type_text(self, text: str) -> bool:
        if self.session()["x11"] and self._tool("xdotool"):
            rc, _, _ = self._run(["xdotool", "type", "--clearmodifiers",
                                  "--delay", "0", "--", text],
                                 timeout=max(_RUN_TIMEOUT, len(text) / 200.0),
                                 env=self._env)
            return rc == 0
        return False

    # -- selection capture ------------------------------------------------
    def capture_selection(self, cfg: Any = None) -> SelectionSnapshot:
        focus = self.focused_window()
        snap = SelectionSnapshot(
            app_class=focus["app_class"], app_name=focus["app_name"],
            window_id=focus["window_id"], window_title=focus["window_title"],
            focused=bool(focus["window_id"]))
        if self.is_protected(focus["app_class"], focus["window_title"]):
            snap.protected = True
            snap.reason = "protected application (password or credential field)"
            snap.captured_via = "refused_protected"
            return snap
        if not self.session()["x11"]:
            snap.reason = ("selection capture requires an X11 session on this "
                           "host (Wayland without wl-clipboard is unsupported)")
            snap.captured_via = "unsupported_session"
            return snap
        if not self._tool("xdotool") or self._clip_tool() is None:
            snap.reason = ("selection capture requires xdotool + xclip "
                           "(or wl-clipboard) on PATH")
            snap.captured_via = "tools_missing"
            return snap

        bak = self.save_clipboard()
        if not bak.complete:
            snap.clipboard_restored = False
            snap.reason = "could not snapshot the clipboard safely; capture skipped"
            snap.captured_via = "clipboard_unavailable"
            return snap
        try:
            # Clear the clipboard first: if the app copies nothing (no
            # selection) the clipboard must stay empty, never fall back to the
            # stale previous contents.
            self.set_clipboard("")
            # Terminals use Ctrl+Shift+C for copy; a bare Ctrl+C is SIGINT.
            copy_chord = ("Control_L+Shift_L+c"
                          if self.is_terminal(focus["app_class"])
                          else "Control_L+c")
            if not self._key(copy_chord):
                snap.reason = ("could not synthesize Ctrl+C"
                               + (f": {self._last_key_error}"
                                  if self._last_key_error else ""))
                snap.captured_via = "key_synthesis_failed"
                return snap
            text = self._poll_clipboard_text(timeout=1.2)
        finally:
            restored = self.restore_clipboard(bak)
        snap.clipboard_restored = bool(restored)
        if not text:
            snap.reason = "no text selected (or the application did not copy)"
            snap.captured_via = "clipboard_empty"
            return snap
        if not _looks_like_text(text):
            snap.reason = "the clipboard held a non-text format; not used"
            snap.captured_via = "clipboard_nontext"
            return snap
        snap.available = True
        snap.text = text
        snap.char_count = len(text)
        snap.digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        snap.captured_via = "clipboard"
        return snap

    _TEXT_TARGETS = ("UTF8_STRING", "text/plain;charset=utf-8",
                     "text/plain", "STRING", "TEXT")

    def _read_clipboard_text(self) -> str:
        """Read only a TEXT target.

        Chromium/WhatsApp-style owners advertise custom binary formats
        (``application/whatsapp``) alongside text. A bare ``xclip -o`` can pick
        the wrong target, so text targets are requested explicitly first and a
        non-text result is discarded rather than fed to the model.
        """
        tool = self._clip_tool()
        if tool == "xclip":
            for t in self._TEXT_TARGETS:
                rc, out, _ = self._run(["xclip", "-selection", "clipboard",
                                        "-t", t, "-o"],
                                       timeout=_CLIP_READ_TIMEOUT, env=self._env)
                if rc == 0 and out:
                    return out.decode("utf-8", "replace")
            return ""
        if tool == "wl":
            for t in self._TEXT_TARGETS:
                rc, out, _ = self._run(["wl-paste", "-n", "-t", t],
                                       timeout=_CLIP_READ_TIMEOUT, env=self._env)
                if rc == 0 and out:
                    return out.decode("utf-8", "replace")
            rc, out, _ = self._run(["wl-paste", "-n"],
                                   timeout=_CLIP_READ_TIMEOUT, env=self._env)
            return out.decode("utf-8", "replace") if rc == 0 else ""
        return ""

    def _poll_clipboard_text(self, *, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            text = self._read_clipboard_text()
            if text:
                last = text
                if text.strip():
                    return text
            time.sleep(0.06)
        return last if last.strip() else ""

    # -- insertion --------------------------------------------------------
    def verify_focus(self, snapshot: SelectionSnapshot) -> bool:
        if not snapshot.window_id:
            return False
        now = self.focused_window()
        if not now["window_id"] or now["window_id"] != snapshot.window_id:
            return False
        if snapshot.app_class and now["app_class"] and \
                now["app_class"] != snapshot.app_class:
            return False
        return True

    def insert_text(self, text: str, snapshot: SelectionSnapshot, cfg: Any = None,
                    *, method: str = "") -> InsertionResult:
        if not text:
            return InsertionResult(ok=False, code="VOICE_EDIT_EMPTY_RESULT",
                                   detail="nothing to insert")
        if not self.verify_focus(snapshot):
            return InsertionResult(
                ok=False, code="VOICE_EDIT_FOCUS_LOST",
                detail="the original application is no longer focused; nothing "
                       "was inserted", app_class=snapshot.app_class)
        method = method or str(
            _cfg(cfg, "voice_edit", "insertion_method", default="clipboard_paste")
            or "clipboard_paste")
        if method in ("typed", "simulated_typing"):
            ok = self._type_text(text)
            return InsertionResult(
                ok=ok, code="" if ok else "VOICE_EDIT_TYPE_FAILED",
                detail="" if ok else "simulated typing failed",
                method="typed", app_class=snapshot.app_class,
                focus_restored=True,
                clipboard_restored=True,
                reason="" if ok else "the desktop refused synthetic keystrokes")
        # clipboard paste
        if self._clip_tool() is None:
            return InsertionResult(
                ok=False, code="VOICE_EDIT_CLIPBOARD_UNAVAILABLE",
                detail="no clipboard tool is installed", method="clipboard_paste",
                app_class=snapshot.app_class,
                reason="install xclip (X11) or wl-clipboard (Wayland)")
        bak = self.save_clipboard()
        if not bak.complete:
            return InsertionResult(
                ok=False, code="VOICE_EDIT_CLIPBOARD_UNSAFE",
                detail="could not snapshot the clipboard; insertion skipped",
                method="clipboard_paste", app_class=snapshot.app_class,
                clipboard_restored=False,
                reason="refusing to clobber the clipboard without a safe backup")
        if not self.set_clipboard(text):
            self.restore_clipboard(bak)
            return InsertionResult(
                ok=False, code="VOICE_EDIT_CLIPBOARD_SET_FAILED",
                detail="could not set the clipboard", method="clipboard_paste",
                app_class=snapshot.app_class)
        chord = self._paste_chord(snapshot.app_class)
        ok = self._key(chord)
        restored = self.restore_clipboard(bak)
        if not ok:
            return InsertionResult(
                ok=False, code="VOICE_EDIT_PASTE_FAILED",
                detail=f"could not synthesize {chord}",
                method="clipboard_paste", app_class=snapshot.app_class,
                clipboard_restored=restored,
                reason="the desktop refused synthetic keystrokes")
        return InsertionResult(
            ok=True, method="terminal_paste" if self.is_terminal(
                snapshot.app_class) else "clipboard_paste",
            app_class=snapshot.app_class, focus_restored=True,
            clipboard_restored=restored)

    def revert_last(self, snapshot: SelectionSnapshot, cfg: Any = None) -> InsertionResult:
        if not self.verify_focus(snapshot):
            return InsertionResult(
                ok=False, code="VOICE_EDIT_FOCUS_LOST",
                detail="the original application is no longer focused; revert "
                       "was not attempted", app_class=snapshot.app_class)
        ok = self._key("Control_L+z")
        return InsertionResult(
            ok=ok, code="" if ok else "VOICE_EDIT_UNDO_FAILED",
            detail="" if ok else "could not synthesize Ctrl+Z",
            method="undo", app_class=snapshot.app_class, reverted=ok,
            focus_restored=True,
            reason="" if ok else "application undo is the only available "
                   "revert; use Copy Original instead")


def _cfg(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default)
    except Exception:
        return default


__all__ = ["DesktopAdapter", "ClipboardBackup"]
