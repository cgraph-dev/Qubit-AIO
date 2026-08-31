"""Push-to-talk hotkey listener (xinput XI2 backend + pynput fallback).

Hold-to-talk semantics:
  * hold_key down      -> PTT started
  * hold_key release   -> PTT ended (recording continues briefly for the tail)
  * tap_cancel key     -> cancel/stop (stop speaking, abort task)

Runs in its own thread and pushes events into an asyncio.Queue via
call_soon_threadsafe.

Display handling is portable AND validated:

- the listener only reports itself active when ``DISPLAY`` resolved by
  ``voicebridge.display`` is GENUINELY reachable (bounded xinput probe) and the
  ``xinput test-xi2`` child stays running past a short ready window (an xinput
  that cannot connect exits within milliseconds);
- a stale/dead child is restarted with BOUNDED exponential backoff, and a
  restart joins the previous listener thread before spawning — so the bridge
  never ends up with duplicate xinput listeners or a restart loop;
- headless / no graphical session -> typed ``hotkey.unavailable`` and manual
  Enter-toggle mode (the bridge must keep working headless).
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

    # Bounded reconnect backoff (seconds): a dead listener is retried after an
    # interval that grows from MIN to MAX; a successful start resets it.
    RESTART_MIN_INTERVAL = 2.0
    RESTART_MAX_INTERVAL = 30.0
    # How long _start_xinput waits to catch an xinput child that exits
    # immediately (cannot connect to the display).
    XINPUT_READY_S = 2.0

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
        # bounded reconnect state
        self._restart_interval = self.RESTART_MIN_INTERVAL
        self._next_restart_ts = 0.0

    def start(self, on_event) -> bool:
        """Start the listener; returns False if no backend can capture keys.

        Backends, in order: xinput XI2 (raw X events — reliable on Ubuntu 24.04,
        where pynput's XRecord silently delivers nothing) -> pynput -> None.
        Idempotent: a live xinput listener is never duplicated.
        """
        self.on_event = on_event
        if self._thread is not None and self._thread.is_alive():
            return True   # already listening — never create a second listener
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
        """Start the xinput child ONLY after the display is validated.

        Returns True only when the child survives the ready window (a child
        that cannot connect to the display exits almost immediately). Never
        starts a second child while a live one exists.
        """
        import subprocess
        from .display import display_reachable, ensure_session_display
        if self._thread is not None and self._thread.is_alive():
            return True   # a listener is already running
        ensure_session_display()
        disp = os.environ.get("DISPLAY")
        if not disp:
            log_event("hotkey.unavailable",
                      detail="no graphical session (headless or ambiguous)")
            return False
        if not display_reachable(disp):
            log_event("hotkey.unavailable",
                      detail=f"display {disp} is not reachable from this session",
                      display=disp)
            return False
        try:
            proc = subprocess.Popen(
                ["xinput", "test-xi2", "--root"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={**os.environ, "DISPLAY": disp},
                text=False, bufsize=0)
        except Exception as exc:
            log.warning("hotkeys: xinput unavailable (%s)", exc)
            return False
        # Bounded ready check: an xinput that cannot connect exits quickly.
        # It can also legitimately emit nothing while idle, so "still running"
        # after the window is the connected signal (never wait for output).
        ready_deadline = time.time() + float(getattr(
            self, "XINPUT_READY_S", self.XINPUT_READY_S))
        while time.time() < ready_deadline:
            rc = proc.poll()
            if rc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
                log_event("hotkey.unavailable",
                          detail=f"xinput could not connect to {disp}",
                          display=disp)
                return False
            time.sleep(0.05)
        self._proc = proc
        self._stopped = False
        self._xinput_state = {"press": False, "release": False}
        self._xinput_start_ts = time.time()
        self._last_xinput_line_ts = 0.0
        self._xinput_dead = False
        self._restart_interval = self.RESTART_MIN_INTERVAL
        self._next_restart_ts = 0.0
        self._thread = threading.Thread(target=self._xinput_loop, daemon=True,
                                        name="hotkey-xinput")
        self._thread.start()
        return True

    def _xinput_loop(self) -> None:
        import select as _select
        proc = self._proc   # local reference: a stale loop never kills a NEW child
        try:
            assert proc and proc.stdout is not None
            self._last_xinput_line_ts = time.time()
            # Non-blocking read with a heartbeat so a wedged child (alive but
            # not delivering) is detectable: ensure_alive checks this ts.
            fd = proc.stdout.fileno()
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
            try:
                proc.kill()
            except Exception:
                pass
            if self._proc is proc:
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
        if self._thread is not None:
            try:
                self._thread.join(timeout=2.0)
            except Exception:
                pass
            self._thread = None
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    @staticmethod
    def _key_name(key) -> str:
        return getattr(key, "name", None) or getattr(key, "char", None) or str(key)


    def _is_stale(self, now: float) -> bool:
        """A child that produced NO output since start for >30s is stale.

        A wedged child is alive but its stdout never delivers a line; the loop
        heartbeats via select (idle ticks keep the select loop alive but
        ``_last_xinput_line_ts`` only advances on real output). To avoid
        restarting an idle-but-healthy listener, the stale window is long and
        only applies when the child has emitted NOTHING since it started.
        """
        last = getattr(self, "_last_xinput_line_ts", 0.0) or 0.0
        if last != 0.0:
            return False
        return (now - getattr(self, "_xinput_start_ts", now)) > 30

    def ensure_alive(self) -> None:
        """Restart a dead/stale xinput listener with BOUNDED backoff.

        Never creates duplicate listeners: a restart joins the previous loop
        thread first, and a failed restart schedules the next attempt after an
        exponential interval (capped) instead of hot-looping.
        """
        now = time.time()
        if now < self._next_restart_ts:
            return   # bounded backoff window active
        dead = getattr(self, "_xinput_dead", False)
        stale = self._is_stale(now)
        if not (dead or stale or self._proc is None):
            return   # healthy listener
        if self._restart_xinput():
            self._restart_interval = self.RESTART_MIN_INTERVAL
            self._next_restart_ts = 0.0
        else:
            self._restart_interval = min(
                self.RESTART_MAX_INTERVAL, self._restart_interval * 2)
            self._next_restart_ts = now + self._restart_interval

    def _restart_xinput(self) -> bool:
        """Kill any stale child, join its loop thread, and start a fresh one.

        Returns True only when a new listener actually started. The previous
        thread is joined BEFORE spawning so two listeners never coexist.
        """
        try:
            old_proc = getattr(self, "_proc", None)
            if old_proc is not None:
                try:
                    old_proc.kill()
                except Exception:
                    pass
            old_thread = getattr(self, "_thread", None)
            if old_thread is not None and old_thread.is_alive():
                old_thread.join(timeout=1.0)
            self._proc = None
            ok = self._start_xinput()
            if ok:
                log_event("hotkey.xinput.restarted")
            return ok
        except Exception as exc:
            log.warning("hotkeys: xinput restart failed (%s)", exc)
            return False

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