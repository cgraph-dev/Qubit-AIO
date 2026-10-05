"""Tk voice orb: borderless always-on-top waveform window (X11).

Presentation only. The window polls the local voice-edit surface
(read-only GETs through :mod:`voicebridge.orb.client`) and draws one
of the four widget states (idle pulse / listening bars / thinking
chase / speaking mirrored wave; unknown -> idle). A click (no drag)
triggers the desktop helper's PTT capture (SIGUSR1; the helper owns
the mic — the orb captures nothing); with no helper live the click
falls back to staging/cancelling a voice request through the existing
begin/cancel endpoints; a drag moves the window; the position is
persisted in the Qubit state dir.

Focus discipline (load-bearing, pinned by
``tests/test_voice_orb9u.py::test_orb_window_never_takes_focus``):
no focus-forcing or grab calls anywhere; every
widget is created with ``takefocus=0``; showing the window uses
``deiconify`` + ``lift`` only, which does not move keyboard focus on
X11, so a click on the orb never steals typing.

``tkinter`` is imported lazily so this module imports cleanly with no
display (hermetic tests, ``--once`` proof mode).
"""
from __future__ import annotations

import math
import os
import queue
import signal
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from . import client as _client
from . import ember as _ember
from . import fullscreen as _fs
from . import geometry as _geo
from . import monitors as _mon
from . import persist as _persist
from . import state as _state

TEAL = (103, 230, 197)
PERI = (168, 184, 255)
GREY = (139, 148, 158)
AMBER = (227, 179, 65)

FRAME_MS = 33
POLL_MS = 1000
NOTICE_S = 5.0

TRANSPARENT_KEY = "#ff00ff"
FALLBACK_BG = "#11151c"


def _rgba(rgb: Tuple[int, int, int], alpha: float) -> str:
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def _reduced_motion() -> bool:
    """True when the owner asked for still frames (env opt-in, default off).

    Read-only, never raises. The orb has no settings dependency by design
    (presentation only); the env flag is the whole switch and ``render``
    freezes time when it is set.
    """
    try:
        return str(os.environ.get("QUBIT_REDUCED_MOTION", "") or "").strip().lower() in (
            "1", "true", "yes", "on")
    except Exception:
        return False


def draw_orb_frame(canvas: Any, size: int, state: Any, t: float, *,
                   collapsed: bool = False, offline: bool = False,
                   notice: bool = False,
                   reduced_motion: bool = False,
                   level: Any = None) -> Dict[str, Any]:
    """Draw one orb frame on a canvas-like object (no Tk needed by itself).

    Owner 2026-10-04: the animated states render through
    :mod:`voicebridge.orb.ember` (``ember.render``) — the old teal
    bar/chase/wave drawing is retired. State meaning is preserved from
    the orb feed (``voicebridge.orb.state``): idle dim, listening
    brighter/faster, thinking chase, speaking full amplitude; unknown
    states fall back to idle (``ember.resolve_params``). ``collapsed``
    draws the static dot only (pause-when-collapsed: no wave bands
    animate under it) — retained for API compatibility only; the live
    orb renders always-expanded (owner 2026-10-04: no minus button, so
    the collapsed state is unreachable and persisted ``collapsed=True``
    is migrated on load, never honoured). The window carries NO chrome:
    the collapse (minus) and close (x) buttons were removed outright
    (owner 2026-10-04) — no hit zones, no drawing. Exits remain the
    ``--quit`` / ``--toggle`` CLI verbs (validated pid signalling) and
    the service stop; there is no on-window close/minimize action. The
    offline/notice indicator dots are drawn here too, positioned for
    the passed window size (68px orb / 16px dot). Returns the ember
    frame stats plus ``collapsed``/``offline`` flags. Never raises.
    """
    try:
        size_i = max(8, int(size))
    except (TypeError, ValueError):
        size_i = _geo.ORB_PX
    if collapsed:
        try:
            r = size_i / 2 - 1
            canvas.create_oval(1, 1, size_i - 1, size_i - 1,
                               fill=_rgba(TEAL, 0.9), outline="")
        except Exception:
            pass
        return {"ovals": 1, "lines": 0, "points": 0,
                "min_y": 0.0, "max_y": float(size_i), "fills": [],
                "collapsed": True, "offline": bool(offline)}
    try:
        cx = cy = size_i / 2
        stats = _ember.render(canvas, cx, cy, max(4.0, size_i / 2 - 2),
                              t, state, reduced_motion=reduced_motion,
                              level=level)
    except Exception:
        stats = {"ovals": 0, "lines": 0, "points": 0,
                 "min_y": 0.0, "max_y": float(size_i), "fills": []}
    try:
        s = size_i / 96.0  # indicator scale factor (positions tuned at 96px)
        try:
            rr_, gg_, bb_ = (int(v) for v in _ember.INDICATOR_RED)
        except Exception:
            rr_, gg_, bb_ = (255, 60, 50)
        if offline:
            canvas.create_oval(cx + 24 * s, cy + 24 * s,
                               cx + 32 * s, cy + 32 * s,
                               fill=_rgba((rr_, gg_, bb_), 0.8), outline="")
        if notice:
            canvas.create_oval(cx + 24 * s, cy - 32 * s,
                               cx + 32 * s, cy - 24 * s,
                               fill=_rgba((rr_, gg_, bb_), 0.95), outline="")
    except Exception:
        pass
    stats = dict(stats)
    stats["collapsed"] = False
    stats["offline"] = bool(offline)
    return stats


def stylised_level(t: float, band: int) -> float:
    """Pseudo mic level per bar (same shape as the dashboard widget)."""
    v = (math.sin(t * 2.1 + band * 0.9) * 0.5
         + math.sin(t * 3.7 + band * 1.7) * 0.3
         + math.sin(t * 0.7 + band * 0.3) * 0.2)
    return min(1.0, max(0.08, 0.45 + v * 0.45))


class LevelSmoother:
    """Fast-attack slow-release follower for 2 s-poll talk-meter levels.

    A fresh loud poll opens the meter within one update (attack) while
    quiet polls decay slowly (release), so the 2 s poll cadence pumps
    visibly on speech instead of flapping idle<->loud. ``update(None)``
    holds the current value (no level available -> the caller renders
    the legacy loop). Pure math, no I/O; never raises.
    """

    def __init__(self, initial: float = 0.0, attack: float = 0.6,
                 release: float = 0.05) -> None:
        try:
            self.value = max(0.0, min(1.0, float(initial)))
        except (TypeError, ValueError):
            self.value = 0.0
        except Exception:
            self.value = 0.0
        try:
            self.attack = max(0.0, min(1.0, float(attack)))
        except Exception:
            self.attack = 0.6
        try:
            self.release = max(0.0, min(1.0, float(release)))
        except Exception:
            self.release = 0.05

    def update(self, target: Any) -> float:
        """Fold one poll's raw level in; return the smoothed level."""
        try:
            if target is None:
                return self.value
            want = max(0.0, min(1.0, float(target)))
        except (TypeError, ValueError):
            return self.value
        except Exception:
            return self.value
        try:
            rate = self.attack if want >= self.value else self.release
            self.value = self.value + (want - self.value) * rate
        except Exception:
            pass
        return self.value


class VoiceOrb:
    """Borderless orb window. Constructing requires a display."""

    def __init__(self, poll: bool = True,
                  client_factory: Optional[Callable[[], Any]] = None,
                  fullscreen_probe: Optional[Callable[[], bool]] = None) -> None:
        import tkinter as tk
        self._tk = tk
        self.state: str = "idle"
        self.offline = False
        self.notice_until = 0.0
        self.transparent_bg = False
        self.sticky = False
        # Fullscreen-except-games (owner 2026-10-04, X11 only): while the
        # FOCUSED window is EWMH-fullscreen the orb withdraws; when focus
        # leaves fullscreen it returns via show() (deiconify + lift, never
        # focus-forcing). _fs_auto_hidden tracks OUR hide so a window the
        # owner closed themselves is never forced back. Injected for tests.
        self._fullscreen_probe = (
            fullscreen_probe or _fs.active_window_is_fullscreen)
        self._fs_auto_hidden = False
        # In-game override (owner 2026-10-04, ``voice_edit.waveform_ingame``):
        # read through the existing poll (settings GET alongside status/log,
        # same auth, no new auth). Persisted owner state — transient focus
        # changes never write it, only read it. Missing/failed reads keep
        # the last value (default False = today's auto-hide).
        self.ingame_override = False
        self._pending_rid: Optional[str] = None
        self._pending_ts = 0.0
        self._client_factory = client_factory or _default_client
        self._client: Any = None
        # Talk-meter render state (additive): the smoothed 0..1 level the
        # frame tick passes to draw_orb_frame; None = no level available
        # (old helper/offline) -> exactly today's loop. Only the listening
        # render path reads it (ember gates by state).
        self._level_smoother = LevelSmoother()
        self._frame_level: Optional[float] = None
        self._toggle_requested = False
        self._quit_requested = False

        stored = _persist.load_position()
        self._pos = stored or _geo.OrbPosition(x=0, y=0)
        self._pos_is_default = stored is None

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Qubit Voice Orb")
        self.root.overrideredirect(True)
        try:
            self.root.configure(takefocus=0)
        except tk.TclError:
            pass
        # Never take focus: topmost keeps us above; lift/deiconify only.
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        # Splash type: no taskbar entry, sticky across workspaces on
        # most X11 WMs. Guarded: not every Tk/WM accepts it.
        try:
            self.root.attributes("-type", "splash")
            self.sticky = True
        except tk.TclError:
            self.sticky = False
        # Transparent background: accepted on compositors; otherwise
        # fall back to a solid dark square (still fully usable).
        try:
            self.root.configure(bg=TRANSPARENT_KEY)
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
            self.transparent_bg = True
        except tk.TclError:
            self.root.configure(bg=FALLBACK_BG)
            self.transparent_bg = False

        size = _geo.DOT_PX if self._pos.collapsed else _geo.ORB_PX
        self.canvas = tk.Canvas(
            self.root, width=size, height=size,
            bg=TRANSPARENT_KEY if self.transparent_bg else FALLBACK_BG,
            highlightthickness=0, bd=0, takefocus=0,
            cursor="hand2",
        )
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)

        self._press: Optional[Dict[str, Any]] = None
        self._t0 = time.monotonic()
        self._paint_t = 0.0
        self._paint_last_mono: Optional[float] = None
        try:
            self._display = _state.DisplayState(initial=self.state)
        except Exception:
            self._display = None
        self._place_initial()
        if poll:
            self._ensure_poll_worker()
            self._schedule_poll()
        self._schedule_frame()
        self._schedule_signal_check()

    # -- window ------------------------------------------------------------
    def _screen(self) -> Tuple[int, int]:
        try:
            return (self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        except Exception:
            return (1920, 1080)

    def _target_rect(self) -> Any:
        """Output rect the orb must live on (None = current behavior).

        Owner 2026-10-04: the VIRTUAL desktop spans every monitor
        (here 4500x1080), so validating against it parks the orb on
        the unwatched secondary. Prefer the PRIMARY output rect
        (else focused window's output, else first connected); None
        when xrandr is absent/foreign (Wayland) -- the caller then
        keeps today's virtual-desktop behavior. Never raises.
        """
        try:
            return _mon.target_rect()
        except Exception:
            return None

    def _place_initial(self) -> None:
        rect = self._target_rect()
        if rect is not None:
            if self._pos_is_default:
                self._pos = _geo.default_position_for_rect(rect)
            else:
                previous = self._pos
                resolved, reset, reason = _geo.resolve_initial_position_for_rect(
                    previous, rect)
                self._pos = resolved
                if reset:
                    self._audit_position_reset(
                        previous, reason, rect.w, rect.h)
            self._apply_geometry()
            return
        w, h = self._screen()
        if self._pos_is_default:
            pos = _geo.default_position(w, h)
            self._pos = _geo.OrbPosition(x=pos.x, y=pos.y, collapsed=False)
        else:
            previous = self._pos
            resolved, reset, reason = _geo.resolve_initial_position(
                previous, w, h)
            self._pos = resolved
            if reset:
                self._audit_position_reset(previous, reason, w, h)
        # The loaded/default position must reach the window: without this
        # the orb opens at the WM default (top-left) whatever is persisted.
        self._apply_geometry()

    def _audit_position_reset(self, previous: object, reason: str,
                              w: int, h: int) -> None:
        """Audit an offscreen-persisted reset (geometry only, no secrets)."""
        try:
            import json as _json
            import sys as _sys
            try:
                stored = {"x": int(previous.x), "y": int(previous.y),  # type: ignore[union-attr]
                          "collapsed": bool(getattr(previous, "collapsed", False))}
            except (TypeError, ValueError, AttributeError):
                stored = {"x": None, "y": None}
            _sys.stderr.write(_json.dumps({
                "event": "orb.position_reset",
                "reason": str(reason),
                "stored": stored,
                "screen": {"w": int(w), "h": int(h)},
                "default": {"x": int(self._pos.x), "y": int(self._pos.y)},
            }) + "\n")
        except Exception:
            pass
        self._apply_geometry()

    def _apply_geometry(self) -> None:
        size = _geo.DOT_PX if self._pos.collapsed else _geo.ORB_PX
        try:
            self.canvas.configure(width=size, height=size)
        except Exception:
            pass
        self.root.geometry(f"{size}x{size}+{self._pos.x}+{self._pos.y}")

    def show(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass

    def hide_to_background(self) -> None:
        """Withdraw, keep polling (tray-less).

        Owner 2026-10-04: the window has no close/minimize buttons, so
        this is reached only via the fullscreen auto-hide rule,
        ``toggle_visible``, and the ``--toggle`` CLI verb. Process exit
        is ``--quit`` / service stop only.
        """
        try:
            self.root.withdraw()
        except Exception:
            pass

    @property
    def visible(self) -> bool:
        try:
            return bool(self.root.winfo_viewable())
        except Exception:
            return False

    def toggle_visible(self) -> None:
        if self.visible:
            self.hide_to_background()
        else:
            self.show()

    # -- input: drag anywhere, click toggles ----------------------------------
    def _zone_at(self, x: float, y: float) -> str:
        """Hit zone: always ``"main"`` — the orb carries no chrome.

        Owner 2026-10-04: the x (close) and - (collapse) buttons were
        removed outright, so there are no chrome hit zones to report;
        every press is a voice-toggle-or-drag press. Kept (rather than
        deleted) so press/release/hover call sites and their tests keep
        one naming point. Never raises.
        """
        return "main"

    def _on_press(self, event: Any) -> None:
        try:
            self._press = {
                "x": float(event.x_root), "y": float(event.y_root),
                "ox": self._pos.x, "oy": self._pos.y,
                "zone": self._zone_at(float(event.x), float(event.y)),
                "dragging": False,
            }
        except Exception:
            self._press = None

    def _on_motion(self, event: Any) -> None:
        press = self._press
        if not press:
            return
        try:
            dx = float(event.x_root) - press["x"]
            dy = float(event.y_root) - press["y"]
        except Exception:
            return
        if not press["dragging"] and not _geo.is_drag(dx, dy):
            return
        press["dragging"] = True
        rect = self._target_rect()
        if rect is not None:
            clamped = _geo.clamp_to_rect(press["ox"] + int(dx),
                                         press["oy"] + int(dy), rect,
                                         collapsed=self._pos.collapsed)
        else:
            w, h = self._screen()
            clamped = _geo.clamp_position(press["ox"] + int(dx),
                                          press["oy"] + int(dy), w, h,
                                          collapsed=self._pos.collapsed)
        self._pos = _geo.OrbPosition(x=clamped.x, y=clamped.y,
                                     collapsed=self._pos.collapsed)
        self._apply_geometry()

    def _on_release(self, event: Any) -> None:
        press = self._press
        self._press = None
        if not press or press["dragging"]:
            if press and press["dragging"]:
                _persist.save_position(None, self._pos)
            return
        try:
            zone = self._zone_at(float(event.x), float(event.y))
        except Exception:
            return
        if zone != press["zone"]:
            return
        # No chrome actions: every click toggles voice (close/collapse
        # buttons removed outright, owner 2026-10-04).
        self.toggle_voice()

    def _on_hover(self, event: Any) -> None:
        try:
            self.canvas.configure(cursor="hand2")
        except Exception:
            pass

    # -- voice toggle (helper start-capture; stage-only fallback) ---------------
    def toggle_voice(self) -> None:
        now = time.time()
        action, rid = _state.plan_toggle(self._pending_rid, self._pending_ts, now)
        try:
            cli = self._get_client()
            if action == "cancel" and rid:
                cli.cancel_request(rid)
                self._pending_rid = None
                return
            # Click-to-talk: the helper owns the mic, so the click only
            # TRIGGERS its existing PTT path (start when idle, finish while
            # capturing). No staged request exists on this path, so a second
            # click re-triggers (finish), never overlaps.
            try:
                cli.trigger_helper_capture()
                self._pending_rid = None
            except Exception as exc:
                code = getattr(exc, "code", "")
                if code == "ORB_HELPER_NOT_RUNNING":
                    # Honest fallback: stage-only begin (next click cancels).
                    out = cli.begin_session()
                    new_rid = str(out.get("request_id", "") or "")
                    self._pending_rid = new_rid or None
                    self._pending_ts = now
                else:
                    raise
        except Exception:
            self.notice_until = now + NOTICE_S

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    # -- state feed --------------------------------------------------------------
    # -- state feed (worker thread + queue; mainloop never blocks) ---------
    def _ensure_poll_worker(self) -> None:
        """Start the daemon poll worker (once; HTTP + queue only).

        The worker owns all blocking HTTP; the mainloop only drains the
        queue in ``_poll_tick``. The worker never touches a Tk widget —
        that would race the mainloop. Safe to call repeatedly; a dead
        worker is restarted on the next tick.
        """
        try:
            if self._poll_worker_alive():
                return
        except Exception:
            pass
        try:
            if getattr(self, "_poll_queue", None) is None:
                self._poll_queue = queue.Queue(maxsize=1)
            worker = threading.Thread(target=self._poll_loop,
                                      name="orb-poll", daemon=True)
            worker.start()
            self._poll_thread = worker
        except Exception:
            pass

    def _poll_worker_alive(self) -> bool:
        try:
            worker = getattr(self, "_poll_thread", None)
            return bool(worker is not None and worker.is_alive())
        except Exception:
            return False

    def _poll_loop(self) -> None:
        """Worker body: fetch one round, hand it to the mainloop, sleep.

        Runs on the daemon thread — HTTP and queue only. Window handles,
        drawing surfaces, visibility flips, dwell state, and the
        fullscreen rule all stay on the mainloop apply path
        (``_apply_poll_payload``). Never raises off the thread (an
        exception would silently kill the daemon and freeze the state).
        """
        while True:
            try:
                payload = self._fetch_once()
            except Exception:
                payload = None
            try:
                if payload is not None:
                    _client.offer_latest(self._poll_queue, payload)
            except Exception:
                pass
            try:
                time.sleep(POLL_MS / 1000.0)
            except Exception:
                return

    def _drain_poll_queue(self) -> Optional[Dict[str, Any]]:
        try:
            pending = getattr(self, "_poll_queue", None)
            if pending is None:
                return None
            return _client.drain_latest(pending)
        except Exception:
            return None

    def _fetch_once(self) -> Dict[str, Any]:
        """One synchronous fetch round (worker, or worker-less fallback)."""
        try:
            tick = int(getattr(self, "_settings_tick", 0) or 0) + 1
        except (TypeError, ValueError):
            tick = 1
        self._settings_tick = tick
        try:
            cli = self._get_client()
        except Exception:
            return {"status": None, "events": [], "settings": None}
        try:
            return _client.fetch_poll_payload(cli, tick)
        except Exception:
            return {"status": None, "events": [], "settings": None}

    def _apply_poll_payload(self, payload: Dict[str, Any]) -> None:
        """Apply one fetched round on the mainloop (widgets allowed here)."""
        try:
            status = payload.get("status", None)
        except AttributeError:
            status = None
        try:
            events = payload.get("events", [])
            if not isinstance(events, list):
                events = []
        except AttributeError:
            events = []
        self.offline = status is None
        _prev_state = str(getattr(self, "state", ""))
        if status is not None:
            raw = _state.resolve_orb_state(status, events, time.time())
            try:
                if self._display is not None:
                    self.state = self._display.update(
                        raw, time.monotonic())
                else:
                    self.state = raw
            except Exception:
                self.state = raw
        else:
            try:
                if self._display is not None:
                    self.state = self._display.update(
                        "error", time.monotonic())
                else:
                    self.state = "error"
            except Exception:
                self.state = "error"
        # Transition-only journal row (same rule as the Qt view): runs on
        # every apply, live or offline, so idle->speaking->idle always
        # leaves its rows.
        try:
            _now_state = str(getattr(self, "state", ""))
            if _now_state and _now_state != _prev_state:
                from ..logging_setup import log_event as _log_event
                _log_event("orb.state", frm=_prev_state or "none",
                           to=_now_state)
        except Exception:
            pass
        # Talk-meter sample (no extra HTTP: the accessor parses the
        # status payload it already fetched, else the local helper
        # heartbeat file). Smoothed so 2 s polls don't flap; None
        # keeps today's loop. Fake clients without the accessor stay
        # on the legacy loop.
        try:
            try:
                _cli = self._get_client()
            except Exception:
                _cli = None
            get_level = getattr(_cli, "mic_level", None)
            raw = get_level(status) if callable(get_level) else None
            if raw is None:
                self._frame_level = None
            else:
                smoother = getattr(self, "_level_smoother", None)
                if smoother is None:
                    smoother = self._level_smoother = LevelSmoother()
                self._frame_level = smoother.update(raw)
        except Exception:
            pass
        # Ingame override: applied only when this round fetched settings
        # (every 5th tick, never while offline); otherwise the last value
        # stands — a slow bridge used to wedge the painted state for the
        # whole tick (2026-10-04: orb read as frozen while inputs
        # resolved). The fetch moved to the worker thread (2026-10-05);
        # this apply stays on the mainloop.
        try:
            settings = payload.get("settings", None)
        except AttributeError:
            settings = None
        if settings is not None:
            try:
                self.ingame_override = bool(settings.get(
                    "waveform_ingame", False))
            except Exception:
                pass

    def _schedule_poll(self) -> None:
        try:
            self.root.after(POLL_MS, self._poll_tick)
        except Exception:
            pass

    def _poll_tick(self) -> None:
        try:
            payload = self._drain_poll_queue()
            if payload is None and not self._poll_worker_alive():
                # No worker owns fetching (tests, worker-less paths):
                # fetch synchronously, exactly like the old tick.
                payload = self._fetch_once()
            if payload is not None:
                self._apply_poll_payload(payload)
        finally:
            self._apply_fullscreen_rule()
            self._schedule_poll()

    def _apply_fullscreen_rule(self) -> None:
        """Hide the orb while a fullscreen window is focused (X11, EWMH).

        Runs inside the existing state poll — no new threads/processes
        beyond the one cheap xprop the probe performs. Never raises and
        never touches focus: hiding is withdraw (polling continues),
        restoring is show() (deiconify + lift only). A window the owner
        closed themselves (withdrawn without our flag) is never forced
        back. Probe failure is a safe no-op (orb stays as it is).

        In-game override (owner 2026-10-04): while
        ``voice_edit.waveform_ingame`` is on, the auto-withdraw is skipped
        (the orb stays mapped over fullscreen games) and an orb WE hid is
        restored even before alt-tab. The override is read-only here —
        transient focus changes never persist it.
        """
        try:
            probe = getattr(self, "_fullscreen_probe", None)
            if probe is None:
                return
            focused_fs = bool(probe())
        except Exception:
            return
        try:
            override = bool(getattr(self, "ingame_override", False))
            action, auto = _fs.next_visibility(
                focused_fs, self.visible,
                bool(getattr(self, "_fs_auto_hidden", False)),
                ingame_override=override)
        except Exception:
            return
        try:
            if action == "hide":
                self.hide_to_background()
                self._fs_auto_hidden = True
            elif action == "show":
                self.show()
                self._fs_auto_hidden = False
            else:
                self._fs_auto_hidden = auto
        except Exception:
            pass

    def _schedule_frame(self) -> None:
        try:
            self.root.after(FRAME_MS, self._frame_tick)
        except Exception:
            pass

    def _frame_tick(self) -> None:
        try:
            if self.visible:
                try:
                    now_mono = time.monotonic()
                    self._paint_t = _ember.advance_render_time(
                        getattr(self, "_paint_t", 0.0),
                        getattr(self, "_paint_last_mono", None),
                        now_mono)
                    self._paint_last_mono = now_mono
                except Exception:
                    pass
                self._draw(float(getattr(self, "_paint_t", 0.0) or 0.0))
        finally:
            self._schedule_frame()

    def _schedule_signal_check(self) -> None:
        try:
            self.root.after(250, self._signal_tick)
        except Exception:
            pass

    def _signal_tick(self) -> None:
        if self._quit_requested:
            self.close()
            return
        if self._toggle_requested:
            self._toggle_requested = False
            self.toggle_visible()
        self._schedule_signal_check()

    def request_toggle(self) -> None:
        self._toggle_requested = True

    def request_quit(self) -> None:
        self._quit_requested = True

    # -- drawing (ember renderer; geometry owned by draw_orb_frame) -----------
    def _draw(self, t: float) -> None:
        c = self.canvas
        try:
            size = _geo.DOT_PX if self._pos.collapsed else _geo.ORB_PX
            c.delete("all")
            bg = TRANSPARENT_KEY if self.transparent_bg else FALLBACK_BG
            c.configure(bg=bg)
            draw_orb_frame(
                c, size, self.state, t,
                collapsed=self._pos.collapsed,
                offline=self.offline,
                notice=time.time() < self.notice_until,
                reduced_motion=_reduced_motion(),
                level=getattr(self, "_frame_level", None),
            )
        except Exception:
            pass

    # -- lifecycle ------------------------------------------------------------------
    def close(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass


def _default_client() -> Any:
    cfg = _client.load_cfg()
    return _client.OrbClient.from_config(cfg)


def probe_window() -> Dict[str, Any]:
    """Create a hidden root, test WM attributes, destroy. No window shown.

    Returns what the live Tk/WM accepted -- the honest basis for the
    transparency/sticky claims. Never raises; ``{"ok": False}`` with no
    display.
    """
    out: Dict[str, Any] = {"ok": False, "tk": None, "display": os.environ.get("DISPLAY", "")}
    try:
        import tkinter as tk
        out["tk"] = str(tk.TkVersion)
        root = tk.Tk()
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}"
        return out
    try:
        root.withdraw()
        try:
            root.overrideredirect(True)
            out["overrideredirect"] = True
        except tk.TclError:
            out["overrideredirect"] = False
        try:
            root.attributes("-topmost", True)
            out["topmost"] = True
        except tk.TclError:
            out["topmost"] = False
        try:
            root.attributes("-type", "splash")
            out["splash_type"] = True
        except tk.TclError:
            out["splash_type"] = False
        try:
            root.configure(bg=TRANSPARENT_KEY)
            root.attributes("-transparentcolor", TRANSPARENT_KEY)
            out["transparentcolor"] = True
        except tk.TclError:
            out["transparentcolor"] = False
        out["ok"] = True
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return out


__all__ = ["VoiceOrb", "probe_window", "stylised_level", "draw_orb_frame",
           "LevelSmoother"]
