"""Orb settings panel: pure control spec + an optional Qt widget.

Two layers, deliberately split:

- The pure spec/mapping layer (``panel_rows``, ``opacity_to_slider``,
  ``slider_to_opacity``, ``speed_to_slider``, ``slider_to_speed``,
  ``apply_row_change``, ``state_hex``) is hermetic: no Qt, no display,
  and never raises. It is the single place the panel's rows and their
  option mappings live, so the widget below is a thin adapter and all
  the logic is provable headless.
- ``SettingsPanel`` is a thin ``QWidget`` built from that spec. It is
  defined only when PySide6 imports, so this module imports cleanly with
  no Qt installed and without a display (same lazy-import contract as
  :mod:`voicebridge.orb.qt_app`).

Persisting the returned :class:`~voicebridge.orb.options.OrbOptions` is
the caller's job (``options.save_options``); the panel only ever reads
and returns validated copies.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from . import ember as _ember
from . import options as _options
from .options import (
    OPACITY_MAX, OPACITY_MIN, SIZE_MAX, SIZE_MIN, SPEED_MAX, SPEED_MIN,
    OrbOptions,
)

#: States that get a color row + a test button, in display order.
COLOR_STATES = ("idle", "listening", "thinking", "working", "speaking",
                "error")

#: Integer slider spans exposed in the UI. These are the widget's own
#: coordinates; the helpers below convert to/from the float option
#: ranges in ``options`` (0.35..1.0 opacity, 0.4..2.0 speed).
OPACITY_SLIDER_MIN, OPACITY_SLIDER_MAX = 0, 100
SPEED_SLIDER_MIN, SPEED_SLIDER_MAX = 40, 200

_BOOL_IDS = ("locked", "click_through", "show_tray")


# -- pure mapping helpers (round-trip safe; never raise) ------------------

def opacity_to_slider(opacity: Any) -> int:
    """Option opacity (0.35..1.0) -> integer slider position 0..100.

    Clamps out-of-range input; unparseable input reads as the maximum
    (the option default is 1.0). Never raises.
    """
    try:
        v = max(OPACITY_MIN, min(OPACITY_MAX, float(opacity)))
        span = OPACITY_MAX - OPACITY_MIN
        return int(round((v - OPACITY_MIN) / span * (OPACITY_SLIDER_MAX
                                                     - OPACITY_SLIDER_MIN)))
    except (TypeError, ValueError):
        return OPACITY_SLIDER_MAX
    except Exception:
        return OPACITY_SLIDER_MAX


def slider_to_opacity(slider: Any) -> float:
    """Integer slider position (clamped 0..100) -> option opacity float.

    Unparseable input reads as the maximum (matching the option
    default). Never raises.
    """
    try:
        pos = max(OPACITY_SLIDER_MIN,
                  min(OPACITY_SLIDER_MAX, float(slider)))
    except (TypeError, ValueError):
        pos = OPACITY_SLIDER_MAX
    except Exception:
        pos = OPACITY_SLIDER_MAX
    span = OPACITY_SLIDER_MAX - OPACITY_SLIDER_MIN
    return OPACITY_MIN + (pos - OPACITY_SLIDER_MIN) / span * (
        OPACITY_MAX - OPACITY_MIN)


def readout_setter(label: Any, fmt: Any) -> Any:
    """Return a ``valueChanged`` slot that renders a slider readout label."""
    def _apply(pos: Any) -> None:
        try:
            label.setText(fmt(int(pos)))
        except Exception:
            pass
    return _apply

def speed_to_slider(speed: Any) -> int:
    """Option speed (0.4..2.0) -> integer slider position 40..200.

    Clamps out-of-range input; unparseable input reads mid-range (the
    option default is 1.0). Never raises.
    """
    try:
        v = max(SPEED_MIN, min(SPEED_MAX, float(speed)))
        span = SPEED_MAX - SPEED_MIN
        return int(round(SPEED_SLIDER_MIN + (v - SPEED_MIN) / span * (
            SPEED_SLIDER_MAX - SPEED_SLIDER_MIN)))
    except (TypeError, ValueError):
        return int(round((SPEED_SLIDER_MIN + SPEED_SLIDER_MAX) / 2))
    except Exception:
        return int(round((SPEED_SLIDER_MIN + SPEED_SLIDER_MAX) / 2))


def slider_to_speed(slider: Any) -> float:
    """Integer slider position (clamped 40..200) -> option speed float.

    Unparseable input reads mid-range (matching the option default).
    Never raises.
    """
    try:
        pos = max(SPEED_SLIDER_MIN, min(SPEED_SLIDER_MAX, float(slider)))
    except (TypeError, ValueError):
        pos = (SPEED_SLIDER_MIN + SPEED_SLIDER_MAX) / 2
    except Exception:
        pos = (SPEED_SLIDER_MIN + SPEED_SLIDER_MAX) / 2
    span = SPEED_SLIDER_MAX - SPEED_SLIDER_MIN
    return SPEED_MIN + (pos - SPEED_SLIDER_MIN) / span * (
        SPEED_MAX - SPEED_MIN)


def _valid_hex(value: Any) -> Optional[str]:
    """'#RRGGBB' (upper-cased) for a valid hex color, else None."""
    try:
        s = str(value).strip()
    except Exception:
        return None
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6:
        return None
    try:
        int(s, 16)
    except ValueError:
        return None
    return "#" + s.upper()


def state_hex(options: Any, state: Any) -> str:
    """Current hex for a state: the user override if set, else ember's.

    Unknown states fall back to idle; never raises.
    """
    try:
        rgb = _options.color_overrides(options).get(str(state))
        if rgb is not None:
            return "#{:02X}{:02X}{:02X}".format(int(rgb[0]), int(rgb[1]),
                                                int(rgb[2]))
    except Exception:
        pass
    try:
        core = _ember.STATE_CORES.get(str(state))
        if core is None:
            core = _ember.STATE_CORES["idle"]
        return "#{:02X}{:02X}{:02X}".format(int(core[0]), int(core[1]),
                                            int(core[2]))
    except Exception:
        return "#FF5400"


def panel_rows(options: Any) -> List[Dict[str, Any]]:
    """Deterministic control descriptors for the settings panel.

    Every row carries ``id``/``kind``/``label`` plus the kind-specific
    keys: sliders ``min``/``max``/``value`` (widget coordinates), checks
    ``checked``, colors ``hex``, and test buttons ``state``. Pure; a
    ``None``/malformed options object reads as the defaults. Never raises.
    """
    opts = _options.clamp_options(options)
    rows: List[Dict[str, Any]] = [
        {"id": "size_px", "kind": "slider", "label": "Orb size",
         "min": SIZE_MIN, "max": SIZE_MAX, "value": int(opts.size_px)},
        {"id": "opacity", "kind": "slider", "label": "Opacity",
         "min": OPACITY_SLIDER_MIN, "max": OPACITY_SLIDER_MAX,
         "value": opacity_to_slider(opts.opacity)},
        {"id": "speed", "kind": "slider", "label": "Motion speed",
         "min": SPEED_SLIDER_MIN, "max": SPEED_SLIDER_MAX,
         "value": speed_to_slider(opts.speed)},
        {"id": "locked", "kind": "check", "label": "Lock position",
         "checked": bool(opts.locked)},
        {"id": "click_through", "kind": "check", "label": "Click-through",
         "checked": bool(opts.click_through)},
        {"id": "show_tray", "kind": "check", "label": "Show tray icon",
         "checked": bool(opts.show_tray)},
    ]
    for state in COLOR_STATES:
        rows.append({"id": "color:" + state, "kind": "color",
                     "label": state.capitalize() + " color",
                     "hex": state_hex(opts, state)})
        rows.append({"id": "test:" + state, "kind": "button",
                     "label": "Test", "state": state})
    return rows


def apply_row_change(options: Any, row_id: Any, value: Any) -> OrbOptions:
    """Return a clamped copy with one panel edit applied.

    ``size_px`` -> int, ``opacity`` -> slider coordinate into a float,
    ``speed`` -> slider coordinate into a float, the boolean rows ->
    bool, ``color:<state>`` -> the hex override (an invalid hex is
    ignored, leaving the options unchanged). Unknown ids and any
    malformed input leave the (clamped) options unchanged. Never raises.
    """
    try:
        opts = _options.clamp_options(options)
    except Exception:
        opts = OrbOptions()
    try:
        rid = str(row_id)
        if rid == "size_px":
            try:
                size = int(round(float(value)))
            except (TypeError, ValueError):
                return opts
            except Exception:
                return opts
            return _options.updated(opts, size_px=size)
        if rid == "opacity":
            return _options.updated(opts, opacity=slider_to_opacity(value))
        if rid == "speed":
            return _options.updated(opts, speed=slider_to_speed(value))
        if rid in _BOOL_IDS:
            return _options.updated(opts, **{rid: bool(value)})
        if rid.startswith("color:"):
            state = rid.split(":", 1)[1]
            if state not in COLOR_STATES:
                return opts
            hexv = _valid_hex(value)
            if hexv is None:
                return opts
            colors = dict(opts.colors or {})
            colors[state] = hexv
            return _options.updated(opts, colors=colors)
        return opts
    except Exception:
        return opts


# -- optional Qt widget (defined only when PySide6 imports) ---------------

try:  # headless / Qt-less: the pure layer above still works
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QCheckBox, QColorDialog, QHBoxLayout, QLabel, QPushButton,
        QSlider, QVBoxLayout, QWidget,
    )
    _QT_IMPORT_ERROR: Optional[str] = None
except Exception as exc:
    Qt = None  # type: ignore[assignment]
    QColor = None  # type: ignore[assignment]
    QCheckBox = QColorDialog = QHBoxLayout = QLabel = None  # type: ignore[assignment]
    QPushButton = QSlider = QVBoxLayout = QWidget = None  # type: ignore[assignment]
    _QT_IMPORT_ERROR = f"{type(exc).__name__}"


if QWidget is not None:
    class SettingsPanel(QWidget):  # type: ignore[no-redef]
        """Qt settings panel built from :func:`panel_rows`.

        Thin adapter only: it reads its widgets back through
        :func:`apply_row_change` and reports the resulting
        :class:`OrbOptions`. ``on_change`` fires on any edit, ``on_test``
        on a per-state test button. Constructing requires a display;
        every handler is wrapped so a widget callback can never raise.
        """

        def __init__(self, options: Any = None, *,
                     on_change: Optional[Callable[[OrbOptions], Any]] = None,
                     on_test: Optional[Callable[[str], Any]] = None,
                     parent: Any = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Qubit Voice Orb")
            self._on_change = on_change
            self._on_test = on_test
            #: Guards the edit callbacks while ``set_options`` pushes
            #: values in (a programmatic change must not self-report).
            self._loading = False
            self._options = _options.clamp_options(options)
            self._sliders: Dict[str, Any] = {}
            self._checks: Dict[str, Any] = {}
            self._colors: Dict[str, str] = {}
            self._color_buttons: Dict[str, Any] = {}
            try:
                self._build()
            except Exception:
                pass

        # -- construction -------------------------------------------------
        def _build(self) -> None:
            layout = QVBoxLayout(self)
            for row in panel_rows(self._options):
                rid = row["id"]
                kind = row["kind"]
                if kind == "slider":
                    label = QLabel(str(row["label"]))
                    sld = QSlider(Qt.Horizontal, self)
                    sld.setRange(int(row["min"]), int(row["max"]))
                    # A bare 40..200 slider gives no clue where "slow" is: the
                    # owner set 96 and reasonably read it as slow when it is
                    # essentially the 1.00x default. Each slider shows the value
                    # it actually applies, live.
                    sld.setTickPosition(QSlider.TicksBelow)
                    sld.setTickInterval(max(1, (int(row["max"]) - int(row["min"])) // 8))
                    sld.setValue(int(row["value"]))
                    readout = QLabel("")
                    readout.setMinimumWidth(56)

                    def _fmt(pos: int, _rid: str = rid) -> str:
                        try:
                            if _rid == "speed":
                                return f"{slider_to_speed(pos):.2f}\u00d7"
                            if _rid == "opacity":
                                return f"{slider_to_opacity(pos) * 100:.0f}%"
                            return str(int(pos))
                        except Exception:
                            return ""

                    readout.setText(_fmt(int(row["value"])))
                    sld.valueChanged.connect(readout_setter(readout, _fmt))
                    sld.valueChanged.connect(self._on_edit)
                    self._sliders[rid] = sld
                    row_layout = QHBoxLayout()
                    row_layout.addWidget(sld, 1)
                    row_layout.addWidget(readout, 0)
                    layout.addWidget(label)
                    layout.addLayout(row_layout)
                elif kind == "check":
                    chk = QCheckBox(str(row["label"]), self)
                    chk.setChecked(bool(row["checked"]))
                    chk.toggled.connect(self._on_edit)
                    self._checks[rid] = chk
                    layout.addWidget(chk)
                elif kind == "color":
                    state = rid.split(":", 1)[1]
                    self._colors[state] = str(row["hex"])
                    btn = QPushButton(str(row["hex"]), self)
                    btn.clicked.connect(
                        lambda _checked=False, s=state: self._choose_color(s))
                    self._color_buttons[state] = btn
                    box = QHBoxLayout()
                    box.addWidget(QLabel(str(row["label"])))
                    box.addWidget(btn)
                    layout.addLayout(box)
                elif kind == "button":
                    state = rid.split(":", 1)[1]
                    btn = QPushButton("Test " + state, self)
                    btn.clicked.connect(
                        lambda _checked=False, s=state: self._emit_test(s))
                    layout.addWidget(btn)

        # -- callbacks (never raise) -------------------------------------
        def _on_edit(self, *_args: Any) -> None:
            try:
                if self._loading:
                    return
                opts = self.current_options()
                self._options = opts
                cb = self._on_change
                if callable(cb):
                    cb(opts)
            except Exception:
                pass

        def _emit_test(self, state: str) -> None:
            try:
                cb = self._on_test
                if callable(cb):
                    cb(str(state))
            except Exception:
                pass

        def _choose_color(self, state: str) -> None:
            try:
                current = self._colors.get(state) or state_hex(
                    self._options, state)
                chosen = QColorDialog.getColor(QColor(current), self)
                if chosen is not None and chosen.isValid():
                    self._colors[state] = str(chosen.name()).upper()
                    btn = self._color_buttons.get(state)
                    if btn is not None:
                        btn.setText(self._colors[state])
                    self._on_edit()
            except Exception:
                pass

        # -- read / write -------------------------------------------------
        def current_options(self) -> OrbOptions:
            """Read the widgets back into a clamped OrbOptions. Never raises."""
            try:
                changes: Dict[str, Any] = {}
                try:
                    changes["size_px"] = int(self._sliders["size_px"].value())
                except Exception:
                    pass
                try:
                    changes["opacity"] = slider_to_opacity(
                        self._sliders["opacity"].value())
                except Exception:
                    pass
                try:
                    changes["speed"] = slider_to_speed(
                        self._sliders["speed"].value())
                except Exception:
                    pass
                for name in _BOOL_IDS:
                    try:
                        changes[name] = bool(self._checks[name].isChecked())
                    except Exception:
                        pass
                changes["colors"] = dict(self._colors)
                return _options.updated(self._options, **changes)
            except Exception:
                return self._options

        def set_options(self, options: Any) -> None:
            """Push options into the widgets (no edit callback). Never raises."""
            try:
                opts = _options.clamp_options(options)
            except Exception:
                return
            self._options = opts
            self._loading = True
            try:
                try:
                    self._sliders["size_px"].setValue(int(opts.size_px))
                except Exception:
                    pass
                try:
                    self._sliders["opacity"].setValue(
                        opacity_to_slider(opts.opacity))
                except Exception:
                    pass
                try:
                    self._sliders["speed"].setValue(
                        speed_to_slider(opts.speed))
                except Exception:
                    pass
                for name in _BOOL_IDS:
                    try:
                        self._checks[name].setChecked(
                            bool(getattr(opts, name)))
                    except Exception:
                        pass
                for state in COLOR_STATES:
                    try:
                        self._colors[state] = state_hex(opts, state)
                        btn = self._color_buttons.get(state)
                        if btn is not None:
                            btn.setText(self._colors[state])
                    except Exception:
                        pass
            finally:
                self._loading = False
else:
    #: Qt-less builds expose the name as ``None`` so attribute access
    #: (and ``from ... import SettingsPanel``) stays safe.
    SettingsPanel = None  # type: ignore[assignment]


__all__ = [
    "COLOR_STATES",
    "SETTINGS_PANEL_QT_IMPORT_ERROR",
    "opacity_to_slider",
    "slider_to_opacity",
    "speed_to_slider",
    "slider_to_speed",
    "state_hex",
    "panel_rows",
    "apply_row_change",
    "SettingsPanel",
]

#: Human-readable reason when the Qt layer was not importable (else None).
SETTINGS_PANEL_QT_IMPORT_ERROR = _QT_IMPORT_ERROR
