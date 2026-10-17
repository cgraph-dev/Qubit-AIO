"""Qt voice orb: borderless always-on-top waveform window (X11).

Same contract as the Tk view (``voicebridge.orb.app``), only the view
layer differs: per-pixel alpha (``WA_TranslucentBackground``) so the
window has no opaque square, and ``QPainter`` radial gradients + real
alpha blending so the ember renderer glows instead of dithering.

Untouched and reused verbatim: state feed/IPC (``client``), state
resolution (``state``), geometry/drag math (``geometry``), position
persist (``persist``), fullscreen rule (``fullscreen``), frame/poll
cadence, reduced-motion, focus discipline (no focus-forcing or grab
calls anywhere; ``WindowDoesNotAcceptFocus`` +
``WA_ShowWithoutActivating``; showing uses ``show`` + ``raise_`` only,
which restacks without moving keyboard focus on X11), and the
in-game override. The Tk code stays as the fallback
(``QUBIT_ORB_BACKEND=tk``).

``PySide6`` is imported lazily so this module imports cleanly with no
display and without Qt installed (hermetic tests, ``--once`` proof
mode): pure helpers (``zone_at``, ``band_pen``, ``core_stops``,
``ring_pen``, ``backend_wanted``) need neither.
"""
from __future__ import annotations

import math
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import client as _client
from . import activity as _activity
from . import ember as _ember
from . import mark as _mark
from . import fullscreen as _fs
from . import geometry as _geo
from . import monitors as _mon
from . import options as _options
from . import persist as _persist
from . import state as _state
from . import stream as _stream
from . import talk_state as _talk_state
from .app import LevelSmoother as _LevelSmoother

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
    from PySide6.QtWidgets import QApplication, QWidget
    _QT_IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # headless / Qt-less: pure helpers still work
    Qt = None  # type: ignore[assignment]
    QTimer = None  # type: ignore[assignment]
    QColor = QPainter = QPainterPath = None  # type: ignore[assignment]
    QPen = QRadialGradient = QApplication = QWidget = None  # type: ignore[assignment]
    _QT_IMPORT_ERROR = f"{type(exc).__name__}"

#: Same cadence as the Tk view (single source of truth lives here too;
#: values match ``app.FRAME_MS``/``app.POLL_MS`` and are pinned by test).
FRAME_MS = 16
POLL_MS = 500
NOTICE_S = 5.0

#: Collapsed dot fill (same contract as the Tk view).
TEAL = (103, 230, 197)
GREY = (139, 148, 158)
AMBER = (227, 179, 65)

_QT_BACKEND_ENV = "QUBIT_ORB_BACKEND"


def _orb_style() -> str:
    """Orb look: ``mark`` (the dashboard logo, default) or ``ember``."""
    import os as _os
    return str(_os.environ.get("QUBIT_ORB_STYLE", "mark") or "mark").strip().lower()


def _paint_mark(p: Any, cx: float, cy: float, R: float, t: float,
                state: Any, brightness: float, family: Any) -> None:
    """Paint the Qubit mark: hairline orbits + nucleus + orbiting particles.

    Same construction as the dashboard sidebar logo (QubitMark.tsx) and the Tk
    ``voicebridge.orb.mark`` renderer, which own the geometry constants — this
    only translates them to QPainter. Qt has real alpha and rotated paths, so
    the chromatic fringe is three offset pens instead of colour mixing, and the
    ellipse is a rotated path rather than a sampled polyline.
    """
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QPainterPath, QPen

    core = tuple(int(v) for v in (family.get("core") or (255, 84, 0)))
    rx = R * _mark.RX_FRAC
    ry = R * _mark.RY_FRAC
    ring_w = 1.0 if R < 64 else 1.6
    fringe = _mark.FRINGE_PX_AT_96 * (R / 32.0)

    def _ring(tilt_deg: float, alpha: float) -> None:
        rect = QRectF(cx - rx, cy - ry, rx * 2, ry * 2)
        path = QPainterPath()
        path.addEllipse(rect)
        # Rotate about the centre for the orbit tilt.
        from PySide6.QtGui import QTransform
        tr = QTransform()
        tr.translate(cx, cy)
        tr.rotate(tilt_deg)
        tr.translate(-cx, -cy)
        rotated = tr.map(path)
        p.setBrush(Qt.NoBrush)
        # Chromatic fringe: the same hairline offset in x, red left / blue
        # right, so overlap reads near-white and the edges split (the CSS
        # mark's technique, drawn with real alpha here).
        for dx, col in ((fringe, QColor(255, 45, 85)), (-fringe, QColor(45, 130, 255))):
            pen = QPen(col, ring_w)
            pen.setColor(QColor(col.red(), col.green(), col.blue(),
                                int(255 * min(1.0, alpha * 0.55))))
            p.setPen(pen)
            p.translate(dx, 0.0)
            p.drawPath(rotated)
            p.translate(-dx, 0.0)
        pen = QPen(QColor(core[0], core[1], core[2], int(255 * alpha)), ring_w)
        p.setPen(pen)
        p.drawPath(rotated)

    def _bead(x: float, y: float, rad: float, alpha: float,
              toward_white: float) -> None:
        c = QColor(
            int(core[0] + (255 - core[0]) * toward_white),
            int(core[1] + (255 - core[1]) * toward_white),
            int(core[2] + (255 - core[2]) * toward_white),
            int(255 * max(0.0, min(1.0, alpha))),
        )
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(x - rad, y - rad, rad * 2, rad * 2)

    try:
        t_val = float(t)
    except (TypeError, ValueError):
        t_val = 0.0
    tt = 0.0 if _reduced_motion() else t_val

    # Far field first (behind the nucleus), then nucleus, then near field.
    far_tilt = _mark.TILT_B + _mark.RING_SWAY_DEG * math.sin(
        tt * 2.0 * math.pi / _mark.RING_PERIOD_FAR)
    _ring(far_tilt, 0.20 * max(0.35, brightness))
    for orbit, seed, bead_r, dur in _mark.PARTICLES:
        if orbit != "b":
            continue
        ang = seed + (tt * 360.0 / max(0.1, dur))
        bx, by = _mark._orbit_point(cx, cy, rx, ry, far_tilt, ang)
        depth = 0.5 + 0.5 * math.cos(math.radians(ang))
        _bead(bx, by, max(0.7, bead_r * (R / 32.0) * (0.75 + 0.35 * depth)),
              0.30 + 0.45 * depth, 0.05 * depth)

    _bead(cx, cy, max(1.2, R * _mark.NUCLEUS_FRAC) * (0.92 + 0.08 * brightness),
          0.85 * max(0.35, brightness), 0.10)

    near_tilt = _mark.TILT_A + _mark.RING_SWAY_DEG * math.sin(
        tt * 2.0 * math.pi / _mark.RING_PERIOD_NEAR)
    _ring(near_tilt, 0.35 * max(0.35, brightness))
    for orbit, seed, bead_r, dur in _mark.PARTICLES:
        if orbit != "a":
            continue
        ang = seed + (tt * 360.0 / max(0.1, dur))
        bx, by = _mark._orbit_point(cx, cy, rx, ry, near_tilt, ang)
        depth = 0.5 + 0.5 * math.cos(math.radians(ang))
        _bead(bx, by, max(0.8, bead_r * (R / 32.0) * (0.8 + 0.45 * depth)),
              0.55 + 0.45 * depth, 0.10 + 0.25 * depth)


def backend_wanted() -> str:
    """``qt``/``tk`` from the env (default ``auto`` -> ``qt``)."""
    import os as _os
    try:
        return str(_os.environ.get(_QT_BACKEND_ENV, "") or "").strip().lower()
    except Exception:
        return ""


def _reduced_motion() -> bool:
    import os as _os
    try:
        return str(_os.environ.get("QUBIT_REDUCED_MOTION", "") or "").strip().lower() in (
            "1", "true", "yes", "on")
    except Exception:
        return False


def zone_at(x: float, y: float, size: int, collapsed: bool) -> str:
    """Hit zone: always ``"main"`` — the orb carries no chrome.

    Owner 2026-10-04: the x (close) and - (collapse) buttons were
    removed outright (drawing + hit zones, both view layers), so there
    is nothing to hit but the orb itself. Signature kept (rather than
    deleted) so press/release/hover call sites and their tests keep one
    naming point. Pure, never raises.
    """
    return "main"


def _is_listening_state(orb_state: Any) -> bool:
    """True only for the listening state (level modulates it alone)."""
    try:
        return str(orb_state or "").strip().lower() == "listening"
    except Exception:
        return False


def _is_voice_state(orb_state: Any) -> bool:
    """True for listening + speaking (the equalizer states, spec 2026-10-05)."""
    try:
        return str(orb_state or "").strip().lower() in (
            "listening", "speaking")
    except Exception:
        return False


def spec_bars_wanted(orb_state: Any, spectrum: Any) -> bool:
    """True when the Qt paint path draws equalizer bars (pure).

    Listening/speaking with a valid 12-float spectrum -> bars;
    thinking/idle/error (no audio to analyse) or a missing/malformed
    spectrum -> today's sine loop. Never raises.
    """
    try:
        if not _is_voice_state(orb_state):
            return False
        return _ember.spec_bar_rects(spectrum, None, 48, 48, 46) != []
    except Exception:
        return False


def band_pen(band_index: int, orb_state: Any,
             level: Any = None, family: Any = None,
             brightness_scale: Any = None
             ) -> Tuple[Tuple[int, int, int], float, int]:
    """(rgb, alpha 0..1, width px) for one ember wave band.

    Color reads the single-source per-state table
    (``ember.band_colors``), or ``family`` when a caller is cross-fading
    states (``ember.ColorTransition``). Opacity/width keep the
    ``ember.render`` rule (opacity x brightness x 3, wide top bands).
    ``level`` modulates ONLY the listening state's brightness;
    ``brightness_scale`` (from a spectrum-energy / mic level) modulates
    any state uniformly. ``None`` everywhere renders exactly today's
    loop (same contract as ``ember.render``). Pure, never raises.
    """
    try:
        params = _ember.resolve_params(orb_state)
        spec = _ember.BAND_SPECS[int(band_index) % len(_ember.BAND_SPECS)]
        op = float(spec[4])
        bright = float(params["brightness"])
        if brightness_scale is not None:
            bright *= float(brightness_scale)
        elif level is not None and _is_listening_state(orb_state):
            bright *= float(_ember.listening_brightness(level))
        fams = (_ember.bands_from_family(family) if family is not None
                else _ember.band_colors(orb_state))
        rgb = fams[int(band_index) % len(fams)]
        alpha = min(1.0, op * bright * 3.0)
        width = 2 if int(band_index) >= 3 else 1
        return (tuple(int(v) for v in rgb), alpha, width)
    except (TypeError, ValueError, IndexError):
        return ((255, 84, 0), 0.1, 1)


def core_stops(orb_state: Any, level: Any = None, family: Any = None,
               brightness_scale: Any = None
               ) -> List[Tuple[float, Tuple[int, int, int, int]]]:
    """Radial-gradient stops for the state heart (offset, rgba).

    Highlight/mid read the single-source per-state family
    (``ember.state_colors``), or ``family`` when cross-fading.
    ``level`` modulates ONLY the listening state's brightness;
    ``brightness_scale`` modulates any state uniformly. Pure, never
    raises.
    """
    try:
        fam = (dict(family) if family is not None
               else _ember.state_colors(orb_state))
        hi = tuple(int(v) for v in fam["hi"])
        core = tuple(int(v) for v in fam["core"])
        bright = float(_ember.resolve_params(orb_state)["brightness"])
        if brightness_scale is not None:
            bright *= float(brightness_scale)
        elif level is not None and _is_listening_state(orb_state):
            bright *= float(_ember.listening_brightness(level))
    except (TypeError, ValueError):
        hi, core, bright = (255, 200, 120), (255, 84, 0), 0.45
    try:
        hi_a = int(255 * min(1.0, 0.75 + 0.25 * bright))
        mid_a = int(255 * min(1.0, 0.55 + 0.35 * bright))
    except (TypeError, ValueError):
        hi_a, mid_a = 200, 180
    return [
        (0.0, (hi[0], hi[1], hi[2], hi_a)),
        (0.55, (core[0], core[1], core[2], mid_a)),
        (1.0, (core[0], core[1], core[2], 0)),
    ]


def ring_pen(phase01: float, index: int, orb_state: Any,
             level: Any = None, brightness_scale: Any = None
             ) -> Tuple[float, int]:
    """(alpha 0..1, width px) for one pulse ring.

    ``level`` modulates ONLY the listening state's brightness;
    ``brightness_scale`` modulates any state uniformly. Pure, never
    raises.
    """
    try:
        bright = float(_ember.resolve_params(orb_state)["brightness"])
        if brightness_scale is not None:
            bright *= float(brightness_scale)
        elif level is not None and _is_listening_state(orb_state):
            bright *= float(_ember.listening_brightness(level))
        alpha = _ember.ring_alpha(phase01, bright)
        return (min(1.0, 0.25 + float(alpha) * 2), 2 if int(index) == 0 else 1)
    except (TypeError, ValueError):
        return (0.25, 1)


if QWidget is not None:
    class QtVoiceOrb(QWidget):  # type: ignore[no-redef]
        """Borderless Qt orb window. Constructing requires a display."""

        def __init__(self, poll: bool = True,
                     client_factory: Optional[Callable[[], Any]] = None,
                     fullscreen_probe: Optional[Callable[[], bool]] = None,
                     options: Any = None,
                     enable_tray: bool = False) -> None:
            super().__init__(
                None,
                Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                | Qt.SplashScreen | Qt.WindowDoesNotAcceptFocus
                | Qt.BypassWindowManagerHint,
            )
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self.setFocusPolicy(Qt.NoFocus)
            self.setWindowTitle("Qubit Voice Orb")
            self.setMouseTracking(True)

            self.orb_state: str = "idle"
            self.offline = False
            self.notice_until = 0.0
            self.ingame_override = False
            # Talk-meter render state (same rule as the Tk view): the
            # smoothed 0..1 level the paint path passes to the pen
            # helpers; None = no level available (old helper/offline)
            # -> exactly today's loop. Only the listening render path
            # reads it (helpers gate by state).
            try:
                self._level_smoother = _LevelSmoother()
            except Exception:
                self._level_smoother = None
            self._frame_level: Optional[float] = None
            # Equalizer render state (same additive pattern as the Tk
            # view): smoothed 12-float spectrum + peak caps; None = no
            # spectrum yet -> today's sine loop. Listening reads live
            # bands, speaking reads the TTS timeline frame.
            try:
                self._spec_smoother = _ember.SpectrumSmoother()
            except Exception:
                self._spec_smoother = None
            self._frame_spec: Optional[list] = None
            self._frame_peaks: Optional[list] = None
            # Raw targets recorded by the poll; the paint path advances
            # the per-frame smoothers from these so bars/brightness glide.
            self._spec_target: Optional[list] = None
            self._level_target: Optional[float] = None
            self._anim_last: Optional[float] = None
            # App options (size / opacity / speed / lock / per-action
            # colors). None -> built-in defaults (tests never read the
            # user's on-disk file); ``__main__`` passes load_options().
            self._options = (_options.clamp_options(options)
                             if options is not None else _options.OrbOptions())
            try:
                _geo.set_orb_size(self._options.size_px)
            except Exception:
                pass
            self._speed = float(self._options.speed)
            self._test_state: Optional[str] = None
            self._test_until = 0.0
            self._panel: Any = None
            self._tray: Any = None
            # Fluid color: every state change cross-fades through Oklab,
            # honouring the user's per-action color overrides.
            try:
                self._color_transition = _ember.ColorTransition(
                    self.orb_state,
                    overrides=_options.color_overrides(self._options))
            except Exception:
                self._color_transition = None
            self._pending_rid: Optional[str] = None
            self._pending_ts = 0.0
            self._client_factory = client_factory or _default_client
            self._client: Any = None
            self._toggle_requested = False
            self._quit_requested = False
            self._fullscreen_probe = (
                fullscreen_probe or _fs.active_window_is_fullscreen)
            self._fs_auto_hidden = False
            self._press: Optional[Dict[str, Any]] = None
            self._t0 = time.monotonic()
            self.frames = 0
            # Wall-clock render clock (hitch-clamped) + hysteresis:
            # phase advances by clamped wall dt, never by frame count,
            # so dropped frames cannot judder the animation; the
            # displayed state holds MIN_DWELL_S (listening/error instant).
            self._paint_t = 0.0
            self._paint_last_mono: Optional[float] = None
            self._frame_dts: List[float] = []
            try:
                self._display = _state.DisplayState(initial=self.orb_state)
            except Exception:
                self._display = None

            stored = _persist.load_position()
            self._pos = stored or _geo.OrbPosition(x=0, y=0)
            self._pos_is_default = stored is None
            self._place_initial()

            self._frame_timer = QTimer(self)
            self._frame_timer.setInterval(FRAME_MS)
            self._frame_timer.timeout.connect(self._frame_tick)
            self._frame_timer.start()
            if poll:
                self._ensure_poll_worker()
                self._poll_timer = QTimer(self)
                self._poll_timer.setInterval(POLL_MS)
                self._poll_timer.timeout.connect(self._poll_tick)
                self._poll_timer.start()
            self._sig_timer = QTimer(self)
            self._sig_timer.setInterval(250)
            self._sig_timer.timeout.connect(self._signal_tick)
            self._sig_timer.start()
            # Live voice-journal tail: the orb reacts to physical voice
            # boundaries (mic/TTS/transcript) at append time instead of
            # waiting for the next HTTP poll. The poll stays for status /
            # settings / heartbeat; events come from the journal.
            self._journal = None
            self._stream_queue = None
            self._stream_timer = None
            self._last_status: Any = None
            self._last_heartbeat: Any = None
            # Output-activity tracker (OpenClaw port, 2026-10-06): keeps
            # the speaking state exact when a TTS journal event is missed.
            # A TTS frame observed -> mark_audio; a speak marker ->
            # mark_playback_started; speak_done/expiry -> mark_stream_ending.
            try:
                self._activity = _activity.OutputActivityTracker()
            except Exception:
                self._activity = None
            if poll:
                try:
                    self._stream_queue = queue.Queue(maxsize=1)
                    self._journal = _stream.JournalTail(
                        on_event=lambda _ev: self._mark_stream_dirty())
                    self._journal.start()
                    self._stream_timer = QTimer(self)
                    self._stream_timer.setInterval(100)
                    self._stream_timer.timeout.connect(self._stream_tick)
                    self._stream_timer.start()
                except Exception:
                    self._journal = None
            self._apply_window_opacity()
            if enable_tray:
                self._maybe_start_tray()

        # -- live event stream (journal tail) ------------------------------
        def _mark_stream_dirty(self) -> None:
            """Called from the tail thread: signal 'events changed'."""
            try:
                q = self._stream_queue
                if q is not None:
                    q.put_nowait(True)
            except Exception:
                pass

        def _stream_tick(self) -> None:
            """GUI-thread: on new journal events, re-resolve state instantly.

            Reuses the full apply path (state resolution + samples + test
            override) with the journal's recent events and the last polled
            status/heartbeat — no HTTP, so onset is ~100 ms instead of a
            poll interval.
            """
            try:
                q = self._stream_queue
                if q is None:
                    return
                dirty = False
                while True:
                    try:
                        q.get_nowait()
                        dirty = True
                    except Exception:
                        break
                if not dirty:
                    return
                events = self._journal.recent() if self._journal is not None else []
                self._apply_poll_payload({
                    "status": self._last_status,
                    "events": events,
                    "heartbeat": self._last_heartbeat,
                })
            except Exception:
                pass

        # -- app layer: options / tray / settings panel --------------------
        def _apply_window_opacity(self) -> None:
            try:
                self.setWindowOpacity(float(self._options.opacity))
            except Exception:
                pass

        def apply_options(self, opts: Any) -> None:
            """Clamp, persist and apply new options live. Never raises."""
            try:
                self._options = _options.clamp_options(opts)
                _options.save_options(self._options)
                _geo.set_orb_size(self._options.size_px)
                self._speed = float(self._options.speed)
                self._apply_window_opacity()
                try:
                    self._color_transition = _ember.ColorTransition(
                        self.orb_state,
                        overrides=_options.color_overrides(self._options))
                except Exception:
                    pass
                self._apply_geometry()
                tray = getattr(self, "_tray", None)
                if tray is not None:
                    try:
                        tray.set_locked(self._options.locked)
                    except Exception:
                        pass
            except Exception:
                pass

        def set_test_state(self, state: Any, seconds: Any = 4.0) -> None:
            """Force a state for a few seconds (tray/panel 'test' actions)."""
            try:
                key = str(state or "").strip().lower()
                if key not in _ember.STATE_CORES:
                    return
                self._test_state = key
                self._test_until = time.monotonic() + max(
                    0.5, min(30.0, float(seconds)))
            except Exception:
                pass

        def open_settings(self) -> None:
            """Show the settings panel (created once, lazily). Never raises."""
            try:
                from . import panel as _panel
                cls = getattr(_panel, "SettingsPanel", None)
                if cls is None:
                    return
                if getattr(self, "_panel", None) is None:
                    self._panel = cls(self._options,
                                      on_change=self.apply_options,
                                      on_test=self.set_test_state)
                try:
                    self._panel.set_options(self._options)
                except Exception:
                    pass
                self._panel.show()
                self._panel.raise_()
            except Exception:
                pass

        def _maybe_start_tray(self) -> None:
            try:
                if not self._options.show_tray:
                    return
                from . import tray as _tray
                self._tray = _tray.OrbTray(
                    on_toggle=self.request_toggle,
                    on_settings=self.open_settings,
                    on_quit=self.request_quit,
                    on_test=self.set_test_state,
                    on_lock=lambda locked: self.apply_options(
                        _options.updated(self._options, locked=bool(locked))),
                    options=self._options,
                    parent=self,
                )
                if not self._tray.start():
                    self._tray = None
            except Exception:
                self._tray = None

        # -- geometry (same rules as the Tk view) -------------------------
        def _target_rect(self) -> Any:
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
                    resolved, reset, _ = _geo.resolve_initial_position_for_rect(
                        self._pos, rect)
                    self._pos = resolved
                self._apply_geometry()
                return
            try:
                w, h = self.screen().availableGeometry().width(), \
                    self.screen().availableGeometry().height()
            except Exception:
                w, h = (1920, 1080)
            if self._pos_is_default:
                pos = _geo.default_position(w, h)
                self._pos = _geo.OrbPosition(x=pos.x, y=pos.y, collapsed=False)
            else:
                resolved, _reset, _ = _geo.resolve_initial_position(
                    self._pos, w, h)
                self._pos = resolved
            self._apply_geometry()

        def _apply_geometry(self) -> None:
            size = _geo.DOT_PX if self._pos.collapsed else _geo.ORB_PX
            try:
                self.setFixedSize(size, size)
                self.move(self._pos.x, self._pos.y)
            except Exception:
                pass

        def show(self) -> None:  # restack only, never focus-forcing
            try:
                super().show()
                self.raise_()
            except Exception:
                pass

        def hide_to_background(self) -> None:
            """Withdraw, keep polling (tray-less).

            Owner 2026-10-04: the window has no close/minimize buttons,
            so this is reached only via the fullscreen auto-hide rule,
            ``toggle_visible``, and the ``--toggle`` CLI verb. Process
            exit is ``--quit`` / service stop only.
            """
            try:
                super().hide()
            except Exception:
                pass

        @property
        def visible(self) -> bool:
            try:
                return bool(self.isVisible())
            except Exception:
                return False

        def toggle_visible(self) -> None:
            if self.visible:
                self.hide_to_background()
            else:
                self.show()

        # -- input: drag anywhere, click toggles ---------------------------
        def mousePressEvent(self, ev: Any) -> None:
            try:
                size = _geo.DOT_PX if self._pos.collapsed else _geo.ORB_PX
                self._press = {
                    "x": float(ev.globalPosition().x()),
                    "y": float(ev.globalPosition().y()),
                    "ox": self._pos.x, "oy": self._pos.y,
                    "zone": zone_at(float(ev.position().x()),
                                    float(ev.position().y()),
                                    size, self._pos.collapsed),
                    "dragging": False,
                }
            except Exception:
                self._press = None

        def mouseMoveEvent(self, ev: Any) -> None:
            press = self._press
            if not press:
                # No chrome: the whole orb is the voice surface.
                try:
                    self.setCursor(Qt.PointingHandCursor)
                except Exception:
                    pass
                return
            # Position lock (options): pressing still toggles voice on
            # release, but the window never drags.
            if getattr(self._options, "locked", False):
                return
            try:
                dx = float(ev.globalPosition().x()) - press["x"]
                dy = float(ev.globalPosition().y()) - press["y"]
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
                try:
                    w, h = self.screen().availableGeometry().width(), \
                        self.screen().availableGeometry().height()
                except Exception:
                    w, h = (1920, 1080)
                clamped = _geo.clamp_position(press["ox"] + int(dx),
                                              press["oy"] + int(dy), w, h,
                                              collapsed=self._pos.collapsed)
            self._pos = _geo.OrbPosition(x=clamped.x, y=clamped.y,
                                         collapsed=self._pos.collapsed)
            self._apply_geometry()

        def mouseReleaseEvent(self, ev: Any) -> None:
            press = self._press
            self._press = None
            if not press or press["dragging"]:
                if press and press["dragging"]:
                    _persist.save_position(None, self._pos)
                return
            try:
                size = _geo.DOT_PX if self._pos.collapsed else _geo.ORB_PX
                zone = zone_at(float(ev.position().x()),
                               float(ev.position().y()),
                               size, self._pos.collapsed)
            except Exception:
                return
            if zone != press["zone"]:
                return
            # No chrome actions: every click toggles voice (close/collapse
            # buttons removed outright, owner 2026-10-04).
            self.toggle_voice()

        # -- voice toggle (same plan as the Tk view) -----------------------
        def toggle_voice(self) -> None:
            now = time.time()
            action, rid = _state.plan_toggle(
                self._pending_rid, self._pending_ts, now)
            try:
                cli = self._get_client()
                if action == "cancel" and rid:
                    cli.cancel_request(rid)
                    self._pending_rid = None
                    return
                try:
                    cli.trigger_helper_capture()
                    self._pending_rid = None
                except Exception as exc:
                    code = getattr(exc, "code", "")
                    if code == "ORB_HELPER_NOT_RUNNING":
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

        # -- state feed (worker thread + queue; GUI thread never blocks) --
        def _ensure_poll_worker(self) -> None:
            """Start the daemon poll worker (once; HTTP + queue only).

            Same contract as the Tk view: the worker never touches a
            widget — all paint/state work happens in
            ``_apply_poll_payload`` on the GUI thread.
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
            """Worker body: fetch one round, hand it to the GUI thread.

            Runs on the daemon thread — HTTP and queue only. No widgets,
            no dwell/hysteresis object, no fullscreen rule (those run in
            ``_apply_poll_payload``/``_poll_tick`` on the GUI thread).
            Never raises off the thread.
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

        def _observe_output_activity(self, status: Any, events: Any,
                                     heartbeat: Any) -> None:
            """Feed one apply round into the output-activity tracker.

            Pure bookkeeping (no widget, no HTTP beyond the passed
            payload): a TTS/spectrum frame observed now -> ``mark_audio``
            (refreshes the activity tail); the newest speak marker newer
            than its done marker -> ``mark_playback_started`` (opening a
            new stream when the previous one had ended); a newer
            done marker or a passed tail with playback evidence ->
            ``mark_stream_ending``. Never raises.
            """
            try:
                act = getattr(self, "_activity", None)
                if act is None:
                    return
                now = time.time()
                speak_ts, done_ts = _state.speaking_markers(events, now)
                if speak_ts is not None and (done_ts is None
                                             or speak_ts > done_ts):
                    snap = act.snapshot()
                    if snap.get("streamEnding"):
                        act.mark_stream_opened()
                    act.mark_playback_started()
                elif done_ts is not None and (speak_ts is None
                                              or done_ts > speak_ts):
                    act.mark_stream_ending()
                # A TTS timeline frame under the play head is live audio.
                frame = None
                try:
                    frame = _client.spec_tts_frame_from_payload(status)
                except Exception:
                    frame = None
                if frame is None and heartbeat is not None:
                    try:
                        frame = _client.spec_tts_frame_from_payload(heartbeat)
                    except Exception:
                        frame = None
                if frame is not None:
                    act.mark_audio({"audioMs":
                                    _client.SPEC_TTS_FRAME_S * 1000.0})
                elif (act.snapshot().get("chunks", 0) > 0
                      and not act.is_active(now)):
                    # Expiry watchdog: audio was emitted but no frame has
                    # arrived for a full tail -> the stream has ended.
                    act.mark_stream_ending()
            except Exception:
                pass

        def _apply_poll_payload(self, payload: Dict[str, Any]) -> None:
            """Apply one fetched round on the GUI thread (widgets allowed)."""
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
            try:
                heartbeat = payload.get("heartbeat", None)
                if not isinstance(heartbeat, dict):
                    heartbeat = None
            except AttributeError:
                heartbeat = None
            # Remember the latest polled status/heartbeat so the journal
            # stream tick can re-resolve without another HTTP round-trip.
            self._last_status = status
            self._last_heartbeat = heartbeat
            # Advance output-activity tracking BEFORE resolving: a frame
            # observed this round makes activity live, which extends the
            # speaking span across a momentarily-empty TTS timeline.
            self._observe_output_activity(status, events, heartbeat)
            self.offline = status is None
            _prev_state = str(getattr(self, "orb_state", ""))
            # Authoritative talk events (voice_edit.talk.* journal rows):
            # when present they drive the orb state directly so it matches
            # the voice FSM exactly; when absent, resolution falls back to
            # the recency-window inference below, byte-identically.
            try:
                _talk_events = _talk_state.talk_events_from_journal(events)
            except Exception:
                _talk_events = None
            if status is not None:
                raw = _state.resolve_orb_state(
                    status, events, time.time(), heartbeat=heartbeat,
                    activity=getattr(self, "_activity", None),
                    talk_events=_talk_events)
                try:
                    if self._display is not None:
                        self.orb_state = self._display.update(
                            raw, time.monotonic())
                    else:
                        self.orb_state = raw
                except Exception:
                    self.orb_state = raw
            else:
                try:
                    if self._display is not None:
                        self.orb_state = self._display.update(
                            "error", time.monotonic())
                    else:
                        self.orb_state = "error"
                except Exception:
                    self.orb_state = "error"
            # Test override (tray/panel "Test animation"): hold the forced
            # state for its window, then release back to the live feed.
            try:
                if self._test_state is not None:
                    if time.monotonic() < self._test_until:
                        self.orb_state = self._test_state
                    else:
                        self._test_state = None
            except Exception:
                pass
            # Transition-only journal row (2026-10-04: "stuck green"
            # reports are undecidable without it — no row means the
            # poll itself stopped firing). Never raises, never text.
            try:
                _now_state = str(getattr(self, "orb_state", ""))
                if _now_state and _now_state != _prev_state:
                    from ..logging_setup import log_event as _log_event
                    _log_event("orb.state", frm=_prev_state or "none",
                               to=_now_state)
            except Exception:
                pass
            # Talk-meter + equalizer SAMPLES: the poll only records the
            # raw target here; the paint path advances the smoothers per
            # frame (60fps) so brightness and bars glide between the 1s
            # polls instead of stepping. None = no level/spectrum yet.
            try:
                try:
                    _cli = self._get_client()
                except Exception:
                    _cli = None
                get_level = getattr(_cli, "mic_level", None)
                self._level_target = (get_level(status)
                                      if callable(get_level) else None)
            except Exception:
                self._level_target = None
            try:
                try:
                    _cli = self._get_client()
                except Exception:
                    _cli = None
                cur = str(getattr(self, "orb_state", "") or ""
                          ).strip().lower()
                fresh: Optional[list] = None
                if cur == "listening":
                    get_spec = getattr(_cli, "spectrum", None)
                    if callable(get_spec):
                        fresh = get_spec(status)
                    else:
                        fresh = _client.spec_bands_from_payload(status)
                elif cur == "speaking":
                    get_tts = getattr(_cli, "tts_frame", None)
                    if callable(get_tts):
                        fresh = get_tts(status)
                    else:
                        fresh = _client.spec_tts_frame_from_payload(
                            status)
                self._spec_target = fresh
            except Exception:
                self._spec_target = None
            # Settings is a third blocking HTTP round-trip on the GUI
            # thread (a slow bridge wedged the painted state for the whole
            # tick), yet the ingame override flips rarely. It is fetched
            # every 5th round and never while offline — same rule as the
            # Tk view — and the fetch moved to the worker thread
            # (2026-10-05); this apply stays on the GUI thread.
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

        def _apply_fullscreen_rule(self) -> None:
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

        def _advance_anim(self, now_mono: float) -> None:
            """Advance the per-frame level/spectrum smoothers (60fps).

            Bars and brightness then glide continuously between the 1s
            polls instead of stepping. Never raises.
            """
            try:
                last = getattr(self, "_anim_last", None)
                self._anim_last = now_mono
                if last is None:
                    return
                dt = max(0.0, min(0.25, now_mono - float(last)))
                if dt <= 0.0:
                    return
                # Level: fast-attack / slow-release exponential follower.
                tgt = getattr(self, "_level_target", None)
                if tgt is None:
                    self._frame_level = None
                else:
                    cur = getattr(self, "_frame_level", None)
                    cur = 0.0 if cur is None else float(cur)
                    tau = 0.07 if float(tgt) >= cur else 0.28
                    a = 1.0 - math.exp(-dt / tau)
                    self._frame_level = cur + (float(tgt) - cur) * a
                # Spectrum: per-band fast-attack / slow-release + caps.
                smoother = getattr(self, "_spec_smoother", None)
                if smoother is None:
                    self._frame_spec = getattr(self, "_spec_target", None)
                    self._frame_peaks = None
                    return
                target = getattr(self, "_spec_target", None)
                if target is None and not bool(getattr(smoother, "fed", False)):
                    self._frame_spec = None
                    self._frame_peaks = None
                    return
                self._frame_spec = smoother.update(target, dt=dt)
                try:
                    self._frame_peaks = list(
                        getattr(smoother, "peaks", []) or [])
                except Exception:
                    self._frame_peaks = None
                try:
                    if target is None and max(
                            [0.0] + [float(v) for v in
                                     (self._frame_spec or [])]) < 1e-3:
                        self._frame_spec = None
                        self._frame_peaks = None
                except Exception:
                    pass
            except Exception:
                pass

        def _frame_tick(self) -> None:
            try:
                self._advance_anim(time.monotonic())
                if self.visible and _ember.needs_frame(
                        True, self._pos.collapsed):
                    self.update()
            except Exception:
                pass

        def _signal_tick(self) -> None:
            if self._quit_requested:
                try:
                    if self._journal is not None:
                        self._journal.stop()
                except Exception:
                    pass
                try:
                    tray = getattr(self, "_tray", None)
                    if tray is not None:
                        tray.stop()
                except Exception:
                    pass
                try:
                    self.close()
                finally:
                    try:
                        inst = QApplication.instance()
                        if inst is not None:
                            inst.quit()
                    except Exception:
                        pass
                return
            if self._toggle_requested:
                self._toggle_requested = False
                self.toggle_visible()

        def request_toggle(self) -> None:
            self._toggle_requested = True

        def request_quit(self) -> None:
            self._quit_requested = True

        def frame_time_stats(self) -> Dict[str, float]:
            """Inter-frame time distribution (ms) for fluidity proof.

            Reads the bounded paint-dt ring recorded in ``paintEvent``;
            hermetic shape ``{"count","p50","p95","max","mean"}``.
            Never raises.
            """
            try:
                return dict(_ember.frame_time_stats(
                    list(getattr(self, "_frame_dts", []) or [])))
            except Exception:
                return {"count": 0, "p50": 0.0, "p95": 0.0,
                        "max": 0.0, "mean": 0.0}

        # -- painting (Qt-native ember; same params as ember.render) -------
        def paintEvent(self, ev: Any) -> None:
            try:
                now_mono = time.monotonic()
                try:
                    last = self._paint_last_mono
                    if last is None:
                        step_ms = 0.0
                    else:
                        step_ms = max(0.0, (now_mono - float(last)) * 1000.0)
                    dts = getattr(self, "_frame_dts", None)
                    if isinstance(dts, list):
                        if step_ms > 0:
                            dts.append(step_ms)
                            del dts[:-600]
                    self._paint_t = _ember.advance_render_time(
                        getattr(self, "_paint_t", 0.0), last, now_mono)
                    self._paint_last_mono = now_mono
                except Exception:
                    pass
                t = float(getattr(self, "_paint_t", 0.0) or 0.0)
                if _reduced_motion():
                    t = 0.0
                else:
                    try:
                        t = t * float(getattr(self, "_speed", 1.0))
                    except Exception:
                        pass
                tr = getattr(self, "_color_transition", None)
                if tr is not None:
                    try:
                        fam = tr.update(self.orb_state, now_mono)
                    except Exception:
                        fam = _ember.state_colors(self.orb_state)
                else:
                    fam = _ember.state_colors(self.orb_state)
                size = _geo.DOT_PX if self._pos.collapsed else _geo.ORB_PX
                p = QPainter(self)
                p.setRenderHint(QPainter.Antialiasing, True)
                if self._pos.collapsed:
                    r, g, b = TEAL
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(r, g, b, int(255 * 0.9)))
                    p.drawEllipse(1, 1, size - 2, size - 2)
                    p.end()
                    self.frames += 1
                    return
                cx = cy = size / 2.0
                R = max(4.0, size / 2.0 - 2)
                params = _ember.resolve_params(self.orb_state)
                speed = params["speed"]
                bright = params["brightness"]
                gain = params["band_gain"]
                # Voice pump: listening reads the mic talk-meter, speaking
                # reads the TTS spectrum energy so Qubit's own voice
                # deforms the orb too. ``gain`` scales the bars; ``bscale``
                # scales brightness for the inline glow/ring math; the pen
                # helpers receive ``brightness_scale`` so their own bright
                # matches. ``_lvl`` stays None (no double-apply).
                _lvl = None
                _bscale = None
                try:
                    _key = str(self.orb_state or "").strip().lower()
                    if _key == "listening":
                        _lf = getattr(self, "_frame_level", None)
                        if _lf is not None:
                            _bscale = _ember.listening_brightness(_lf)
                            gain = float(gain) * _ember.listening_gain(_lf)
                    elif _key == "speaking":
                        _en = _ember.spectrum_energy(
                            getattr(self, "_frame_spec", None))
                        if _en is not None:
                            _bscale = _ember.listening_brightness(_en)
                            gain = float(gain) * _ember.listening_gain(_en)
                except Exception:
                    _bscale = None
                _bright_eff = float(bright) * (
                    float(_bscale) if _bscale is not None else 1.0)

                # Default look: the product mark (dashboard sidebar logo).
                # QUBIT_ORB_STYLE=ember restores the ember glow; a bad value
                # falls back to ember so the orb can never paint nothing.
                if _orb_style() == "mark":
                    try:
                        _paint_mark(p, cx, cy, R, t, self.orb_state,
                                    _bright_eff, fam)
                    except Exception:  # noqa: BLE001 - never blank the orb
                        pass
                    # indicators + bookkeeping below are shared with ember.
                    s = size / 96.0
                    try:
                        rr_, gg_, bb_ = (int(v) for v in _ember.INDICATOR_RED)
                    except Exception:
                        rr_, gg_, bb_ = (255, 60, 50)
                    p.setPen(Qt.NoPen)
                    if self.offline:
                        p.setBrush(QColor(rr_, gg_, bb_, int(255 * 0.8)))
                        p.drawEllipse(cx + 24 * s, cy + 24 * s, 8 * s, 8 * s)
                    if time.time() < self.notice_until:
                        p.setBrush(QColor(rr_, gg_, bb_, int(255 * 0.95)))
                        p.drawEllipse(cx + 24 * s, cy - 32 * s, 8 * s, 8 * s)
                    p.end()
                    self.frames += 1
                    return

                # ambient mesh: two drifting radial glows
                for gi in range(2):
                    dx = math.sin(t * 0.23) * R * 0.12 * (1 if gi == 0 else -1)
                    dy = math.cos(t * 0.19 + 1.3) * R * 0.12 * (1 if gi == 0 else -1)
                    a = (0.10 if gi == 0 else 0.07) * _bright_eff
                    cr_, cg_, cb_ = (int(v) for v in fam["core"])
                    grad = QRadialGradient(cx + dx, cy + dy, R * 1.1)
                    grad.setColorAt(0.0, QColor(cr_, cg_, cb_, int(255 * min(1.0, a * 2.2))))
                    grad.setColorAt(0.5, QColor(cr_, cg_, cb_, int(255 * a * 0.5)))
                    grad.setColorAt(1.0, QColor(cr_, cg_, cb_, 0))
                    p.fillRect(0, 0, size, size, grad)

                # pulse rings: expand 0 -> R, fading
                n = max(1, int(params["rings"]))
                wa_ = tuple(int(v) for v in fam["warm_a"])
                for i in range(n):
                    ph = _ember.ring_phase(t, i, n, params["ring_speed"])
                    rr = max(1.0, ph * R)
                    alpha, width = ring_pen(ph, i, self.orb_state,
                                            level=_lvl,
                                            brightness_scale=_bscale)
                    p.setPen(QPen(QColor(wa_[0], wa_[1], wa_[2], int(255 * alpha)), width))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(cx - rr, cy - rr, rr * 2, rr * 2)

                # equalizer bars XOR wave bands: a smoothed spectrum in
                # the listening/speaking states draws 12 log-scaled
                # vertical bars (+ peak-hold cap ticks) in the state's
                # color family; every other state (or no spectrum) draws
                # exactly today's sine loop (fail-soft).
                _spec = getattr(self, "_frame_spec", None)
                _peaks = getattr(self, "_frame_peaks", None)
                _rects = (_ember.spec_bar_rects(
                    _spec, _peaks, cx, cy, R, gain)
                    if spec_bars_wanted(self.orb_state, _spec) else [])
                if _rects:
                    for ri, rc in enumerate(_rects):
                        bar_path = QPainterPath()
                        bar_path.moveTo(rc["x"], rc["top"])
                        bar_path.lineTo(rc["x"], rc["bottom"])
                        rgb, alpha, wpx = band_pen(
                            ri, self.orb_state, level=_lvl, family=fam,
                            brightness_scale=_bscale)
                        p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2],
                                              int(255 * alpha)),
                                     max(wpx, 3)))
                        p.drawPath(bar_path)
                        cap_path = QPainterPath()
                        cap_path.moveTo(rc["x"] - rc["half_w"],
                                        rc["peak_top"])
                        cap_path.lineTo(rc["x"] + rc["half_w"],
                                        rc["peak_top"])
                        p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2],
                                              int(255 * alpha)), 1))
                        p.drawPath(cap_path)
                else:
                    # wave bands: summed sines x gaussian envelope, real alpha
                    width = R * 2.0
                    x0 = cx - R
                    for bi, spec in enumerate(_ember.BAND_SPECS):
                        path = QPainterPath()
                        x = 0.0
                        first = True
                        while x <= width:
                            y = _ember.band_point(
                                x, t, spec, width, cy, R, gain, speed)
                            if first:
                                path.moveTo(x0 + x, y)
                                first = False
                            else:
                                path.lineTo(x0 + x, y)
                            x += 4
                        rgb, alpha, wpx = band_pen(bi, self.orb_state,
                                                   level=_lvl, family=fam,
                                                   brightness_scale=_bscale)
                        p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2],
                                              int(255 * alpha)), wpx))
                        p.drawPath(path)

                # core disc: ember heart with transparent falloff
                cr = R * 0.40
                core = QRadialGradient(cx, cy, cr)
                for stop, rgba in core_stops(self.orb_state, level=_lvl,
                                             family=fam,
                                             brightness_scale=_bscale):
                    core.setColorAt(stop, QColor(*rgba))
                p.setPen(Qt.NoPen)
                p.setBrush(core)
                p.drawEllipse(cx - cr, cy - cr, cr * 2, cr * 2)

                # indicators: offline/notice dots only — the collapse/close
                # chrome was removed outright (owner 2026-10-04); exits
                # remain the --quit/--toggle CLI verbs + service stop.
                s = size / 96.0
                try:
                    rr_, gg_, bb_ = (int(v) for v in _ember.INDICATOR_RED)
                except Exception:
                    rr_, gg_, bb_ = (255, 60, 50)
                p.setPen(Qt.NoPen)
                if self.offline:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(rr_, gg_, bb_, int(255 * 0.8)))
                    p.drawEllipse(cx + 24 * s, cy + 24 * s, 8 * s, 8 * s)
                if time.time() < self.notice_until:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(rr_, gg_, bb_, int(255 * 0.95)))
                    p.drawEllipse(cx + 24 * s, cy - 32 * s, 8 * s, 8 * s)
                p.end()
                self.frames += 1
            except Exception:
                pass

        def close(self) -> None:
            try:
                super().close()
            except Exception:
                pass


def _default_client() -> Any:
    cfg = _client.load_cfg()
    return _client.OrbClient.from_config(cfg)


__all__ = [
    "QtVoiceOrb",
    "backend_wanted",
    "zone_at",
    "band_pen",
    "core_stops",
    "ring_pen",
    "spec_bars_wanted",
]
