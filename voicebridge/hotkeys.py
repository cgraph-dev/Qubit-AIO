"""Push-to-talk hotkey listener (pynput, X11).

Hold-to-talk semantics:
  * hold_key down      -> PTT started
  * hold_key release   -> PTT ended (recording continues briefly for the tail)
  * tap_cancel key     -> cancel/stop (stop speaking, abort task)

Runs in its own thread and pushes events into an asyncio.Queue via
call_soon_threadsafe. If pynput cannot connect (no DISPLAY / Wayland without
X11 bridge), the listener degrades to None and the orchestrator offers a
manual Enter-toggle mode instead — the bridge must keep working headless.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

from .logging_setup import get_logger, log_event

log = get_logger()


class HotkeyListener:
    """Thin wrapper; emits ('ptt_start'|'ptt_end'|'cancel'|'quit', key)."""

    def __init__(self, hold_key: str = "ctrl_r", tap_cancel: str = "esc",
                 alt_hold_key: str = "") -> None:
        self.hold_key = hold_key
        self.alt_hold_key = alt_hold_key
        self.tap_cancel = tap_cancel
        self.on_event = None          # callback(event: str, key)
        self._listener = None
        self._proc = None
        self._xinput_state = {"press": False, "release": False}
        self._held = set()
        self._thread: Optional[threading.Thread] = None
        self._stopped = False
        self._last_xinput_line_ts = 0.0
        self._xinput_dead = False

    def start(self, on_event) -> bool:
        """Start the listener; returns False if no backend can capture keys.

        Backends, in order: xinput XI2 (raw X events — reliable on Ubuntu 24.04,
        where pynput's XRecord silently delivers nothing) -> pynput -> None.
        """
        self.on_event = on_event
        # 1) xinput XI2 backend (subprocess; no XRecord dependency)
        if self._start_xinput():
            return True
        # 2) pynput backend
        try:
            from pynput import keyboard
        except Exception as exc:
            log.warning("hotkeys disabled (pynput/X unavailable: %s) — use manual PTT mode", exc)
            return False
        try:
            self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception as exc:
            log.warning("hotkeys disabled (listener failed: %s)", exc)
            self._listener = None
            return False

    # keycode -> name map used by the xinput backend
    _KEYCODES = {37: "ctrl_l", 105: "ctrl_r", 9: "esc", 133: "super_l",
                 134: "super_r", 64: "alt_l", 108: "alt_r", 50: "shift_l",
                 62: "shift_r", 65: "space"}

    def _start_xinput(self) -> bool:
        import os
        import subprocess
        if not os.environ.get("DISPLAY"):
            return False
        try:
            self._proc = subprocess.Popen(
                ["xinput", "test-xi2", "--root"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={**os.environ, "DISPLAY": os.environ["DISPLAY"]},
                text=False, bufsize=0)
        except Exception as exc:
            log.warning("hotkeys: xinput unavailable (%s)", exc)
            return False
        self._stopped = False
        self._xinput_state = {"press": False, "release": False}
        self._xinput_start_ts = time.time()
        self._last_xinput_line_ts = 0.0
        self._xinput_dead = False
        self._thread = threading.Thread(target=self._xinput_loop, daemon=True,
                                        name="hotkey-xinput")
        self._thread.start()
        return True

    def _xinput_loop(self) -> None:
        import select as _select
        try:
            assert self._proc and self._proc.stdout is not None
            self._last_xinput_line_ts = time.time()
            # Non-blocking read with a heartbeat so a wedged child (alive but
            # not delivering) is detectable: ensure_alive checks this ts.
            fd = self._proc.stdout.fileno()
            buf = ""
            while True:
                r, _, _ = _select.select([fd], [], [], 1.0)
                if self._stopped:
                    break
                if not r:
                    continue          # idle tick; heartbeat stays fresh via start
                chunk = os.read(fd, 4096).decode(errors="replace")
                if not chunk:
                    break             # EOF -> child exited
                buf += chunk
                self._last_xinput_line_ts = time.time()
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if line.startswith("EVENT type 13"):    # RawKeyPress
                        self._xinput_state["press"] = True
                    elif line.startswith("EVENT type 14"):  # RawKeyRelease
                        self._xinput_state["release"] = True
                    elif line.startswith("detail:"):
                        try:
                            code = int(line.split(":")[1].strip())
                        except ValueError:
                            code = 0
                        if self._xinput_state["press"]:
                            self._xinput_state["press"] = False
                            self._press(self._KEYCODES.get(code, str(code)))
                        elif self._xinput_state["release"]:
                            self._xinput_state["release"] = False
                            self._release(self._KEYCODES.get(code, str(code)))
        except Exception as exc:
            log.warning("hotkeys: xinput loop ended (%s) — will auto-restart on next PTT check", exc)
        finally:
            if self._proc is not None:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
            # mark for lazy restart in start()-check or next emit cycle
            self._xinput_dead = True  # type: ignore[attr-defined]

    def stop(self) -> None:
        self._stopped = True
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    @staticmethod
    def _key_name(key) -> str:
        return getattr(key, "name", None) or getattr(key, "char", None) or str(key)


    def ensure_alive(self) -> None:
        """Restart xinput if the loop died or the child is wedged (alive but
        not delivering events for a long time). Never leave hotkey dead."""
        now = time.time()
        dead = getattr(self, "_xinput_dead", False)
        if not dead and self._proc is not None:
            last = getattr(self, "_last_xinput_line_ts", 0.0) or 0.0
            # A wedged xinput child is alive but its stdout never delivers a
            # line. The loop now heartbeats via select (idle ticks keep the
            # select loop alive but _last_xinput_line_ts only advances on real
            # output). To avoid restarting an idle-but-healthy listener, the
            # stale window is long and only applies when the child has emitted
            # NOTHING at all since it started (last==0) for a long time, or
            # when we previously saw output and then silence for a very long
            # window AND the operator armed the watchdog (config flag).
            stale = (last == 0.0 and (now - getattr(self, "_xinput_start_ts",
                                                    now)) > 30)
            if stale:
                log.warning("hotkeys: xinput child produced no output since "
                            "start; restarting listener")
                self._restart_xinput()
        if dead and self.on_event is not None:
            self._xinput_dead = False  # type: ignore[attr-defined]
            self._restart_xinput()

    def _restart_xinput(self) -> None:
        """Kill any stale child and start a fresh xinput listener."""
        try:
            if self._proc is not None:
                try:
                    self._proc.kill()
                except Exception:
                    pass
                self._proc = None
            if self._start_xinput():
                log_event("hotkey.xinput.restarted")
        except Exception as exc:
            log.warning("hotkeys: xinput restart failed (%s)", exc)
    def _press(self, key) -> None:
        name = self._key_name(key)
        if name in (self.hold_key, self.alt_hold_key):
            if name not in self._held:
                self._held.add(name)
                self._emit("ptt_start", name)
        elif name == self.tap_cancel:
            self._emit("cancel", name)

    def _release(self, key) -> None:
        name = self._key_name(key)
        if name in (self.hold_key, self.alt_hold_key):
            self._held.discard(name)
            if not self._held:
                self._emit("ptt_end", name)

    def _emit(self, event: str, key: str) -> None:
        if self.on_event:
            try:
                self.on_event(event, key)
            except Exception:
                log.exception("hotkey callback failed")
        # note: 'action' not 'event' — log_event uses the 'event' key for its
        # own record name; using it here would clobber the journal field.
        log_event("hotkey", action=event, key=key)
