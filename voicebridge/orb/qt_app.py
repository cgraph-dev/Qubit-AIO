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
from . import ember as _ember
from . import fullscreen as _fs
from . import geometry as _geo
from . import monitors as _mon
from . import persist as _persist
from . import state as _state

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
FRAME_MS = 33
POLL_MS = 1000
NOTICE_S = 5.0

#: Collapsed dot fill (same contract as the Tk view).
TEAL = (103, 230, 197)
GREY = (139, 148, 158)
AMBER = (227, 179, 65)

_QT_BACKEND_ENV = "QUBIT_ORB_BACKEND"


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


def band_pen(band_index: int, orb_state: Any) -> Tuple[Tuple[int, int, int], float, int]:
    """(rgb, alpha 0..1, width px) for one ember wave band.

    Color reads the single-source per-state table
    (``ember.band_colors``); opacity/width keep the ``ember.render``
    rule (opacity x brightness x 3, wide top bands). Pure, never raises.
    """
    try:
        params = _ember.resolve_params(orb_state)
        spec = _ember.BAND_SPECS[int(band_index) % len(_ember.BAND_SPECS)]
        op = float(spec[4])
        bright = float(params["brightness"])
        fams = _ember.band_colors(orb_state)
        rgb = fams[int(band_index) % len(fams)]
        alpha = min(1.0, op * bright * 3.0)
        width = 2 if int(band_index) >= 3 else 1
        return (tuple(int(v) for v in rgb), alpha, width)
    except (TypeError, ValueError, IndexError):
        return ((255, 84, 0), 0.1, 1)


def core_stops(orb_state: Any) -> List[Tuple[float, Tuple[int, int, int, int]]]:
    """Radial-gradient stops for the state heart (offset, rgba).

    Highlight/mid read the single-source per-state family
    (``ember.state_colors``): bright warm highlight at the center,
    full state core mid, transparent edge. Pure, never raises.
    """
    try:
        fam = _ember.state_colors(orb_state)
        hi = tuple(int(v) for v in fam["hi"])
        core = tuple(int(v) for v in fam["core"])
        bright = float(_ember.resolve_params(orb_state)["brightness"])
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


def ring_pen(phase01: float, index: int, orb_state: Any) -> Tuple[float, int]:
    """(alpha 0..1, width px) for one pulse ring. Pure, never raises."""
    try:
        bright = float(_ember.resolve_params(orb_state)["brightness"])
        alpha = _ember.ring_alpha(phase01, bright)
        return (min(1.0, 0.25 + float(alpha) * 2), 2 if int(index) == 0 else 1)
    except (TypeError, ValueError):
        return (0.25, 1)


if QWidget is not None:
    class QtVoiceOrb(QWidget):  # type: ignore[no-redef]
        """Borderless Qt orb window. Constructing requires a display."""

        def __init__(self, poll: bool = True,
                     client_factory: Optional[Callable[[], Any]] = None,
                     fullscreen_probe: Optional[Callable[[], bool]] = None) -> None:
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
            self.offline = status is None
            _prev_state = str(getattr(self, "orb_state", ""))
            if status is not None:
                raw = _state.resolve_orb_state(
                    status, events, time.time())
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

        def _frame_tick(self) -> None:
            try:
                if self.visible and _ember.needs_frame(
                        True, self._pos.collapsed):
                    self.update()
            except Exception:
                pass

        def _signal_tick(self) -> None:
            if self._quit_requested:
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

                # ambient mesh: two drifting radial glows
                for gi in range(2):
                    dx = math.sin(t * 0.23) * R * 0.12 * (1 if gi == 0 else -1)
                    dy = math.cos(t * 0.19 + 1.3) * R * 0.12 * (1 if gi == 0 else -1)
                    a = (0.10 if gi == 0 else 0.07) * bright
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
                    alpha, width = ring_pen(ph, i, self.orb_state)
                    p.setPen(QPen(QColor(wa_[0], wa_[1], wa_[2], int(255 * alpha)), width))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(cx - rr, cy - rr, rr * 2, rr * 2)

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
                    rgb, alpha, wpx = band_pen(bi, self.orb_state)
                    p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2],
                                          int(255 * alpha)), wpx))
                    p.drawPath(path)

                # core disc: ember heart with transparent falloff
                cr = R * 0.40
                core = QRadialGradient(cx, cy, cr)
                for stop, rgba in core_stops(self.orb_state):
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
]
