"""System-tray controller for the floating voice orb.

The Qt orb (``voicebridge.orb.qt_app``) is a borderless always-on-top
overlay with no chrome: owner 2026-10-04 removed the close/collapse
buttons outright, so its only exits were ``--quit`` / ``--toggle`` and
the service stop. This module gives it a real app surface: a
``QSystemTrayIcon`` with Show/Hide, Lock position, Settings…, a
Test-animation submenu (one entry per orb state) and Quit.

Design rules (same as the rest of the orb package):

- ``PySide6`` is imported lazily, inside ``OrbTray.start`` only, so this
  module imports cleanly with no display and without Qt installed
  (hermetic tests, ``--once`` proof mode). Nothing at import time can
  raise: the top-level Qt import is wrapped and the pure helpers need no
  Qt at all.
- Every public method is fail-soft. Unavailable Qt / no display / no
  system tray -> ``start`` returns ``False``; ``stop``,
  ``set_locked`` and ``set_visible_state`` never raise, even on a tray
  that was never started.
- The menu structure (``menu_spec``) and the action dispatch
  (``invoke``) are PURE helpers, so tests exercise the real contract
  with no Qt and no display. ``invoke`` is the seam the tray wires to
  each ``QAction.triggered`` signal.
- Nothing prints and nothing shells out. Menu labels are static strings
  plus orb state names — no secret, no path, no transcript can reach
  them.

Menu ids are stable and exact: ``toggle`` (Show orb / Hide orb),
``lock`` (checkable "Lock position"), ``settings`` ("Settings…"),
``test`` (submenu "Test animation" with one child per state,
``test:<state>``) and ``quit`` ("Quit orb").
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from . import options as _options

#: Canonical orb states in display order (single source: the options
#: color table; the local tuple is only a fail-soft fallback).
ORB_STATES: Tuple[str, ...] = tuple(
    getattr(_options, "_COLOR_STATES",
            ("idle", "listening", "thinking", "working", "speaking", "error")))

TOGGLE_SHOW_LABEL = "Show orb"
TOGGLE_HIDE_LABEL = "Hide orb"
LOCK_LABEL = "Lock position"
SETTINGS_LABEL = "Settings\u2026"  # "Settings…"
TEST_LABEL = "Test animation"
QUIT_LABEL = "Quit orb"
TRAY_TOOLTIP = "Qubit Voice Orb"


def state_labels() -> Tuple[str, ...]:
    """The six orb states, in display order. Pure, never raises."""
    try:
        return tuple(str(s) for s in ORB_STATES)
    except Exception:
        return ("idle", "listening", "thinking", "working", "speaking",
                "error")


def menu_spec(options: Any = None) -> List[Dict[str, Any]]:
    """Deterministic static tray-menu structure (pure, never raises).

    Returns one dict per item::

        {"id", "label", "kind": "action"|"check"|"submenu",
         optional "checked": bool, optional "children": [...]}

    ``options`` (a ``voicebridge.orb.options.OrbOptions`` or ``None``)
    only feeds the checkable Lock item's initial state; every other
    field is static, so the shipped shape is byte-stable and testable
    without Qt. The ``test`` submenu has one child per ``state_labels()``
    entry with id ``"test:<state>"``.
    """
    checked = False
    try:
        checked = bool(getattr(options, "locked", False))
    except Exception:
        checked = False
    children: List[Dict[str, Any]] = [
        {"id": "test:" + state, "label": state, "kind": "action"}
        for state in state_labels()
    ]
    return [
        {"id": "toggle", "label": TOGGLE_SHOW_LABEL, "kind": "action"},
        {"id": "lock", "label": LOCK_LABEL, "kind": "check",
         "checked": checked},
        {"id": "settings", "label": SETTINGS_LABEL, "kind": "action"},
        {"id": "test", "label": TEST_LABEL, "kind": "submenu",
         "children": children},
        {"id": "quit", "label": QUIT_LABEL, "kind": "action"},
    ]


def _callback_for(callbacks: Any, key: str) -> Optional[Callable[..., Any]]:
    """Look up ``key`` (or ``on_<key>``) in a callbacks mapping.

    Accepts both the action-id spelling (``"toggle"``) and the
    constructor spelling (``"on_toggle"``). Pure, never raises.
    """
    try:
        if not isinstance(callbacks, dict):
            return None
        cb = callbacks.get(key)
        if cb is None:
            cb = callbacks.get("on_" + key)
        return cb if callable(cb) else None
    except Exception:
        return None


def _retry_zero_arg(cb: Callable[..., Any]) -> bool:
    """Call a zero-arg callback after an arity TypeError. Never raises."""
    try:
        cb()
        return True
    except Exception:
        return False


def invoke(action_id: Any, callbacks: Any, value: Any = None) -> bool:
    """Dispatch one tray action id to its callback (pure, never raises).

    Maps ``"toggle"``/``"lock"``/``"settings"``/``"quit"`` to the
    matching callback and ``"test:<state>"`` to the test callback with
    the state name. The lock callback receives ``bool(value)`` (the
    checkable action's new state); the other actions are called with no
    arguments. Returns ``True`` iff a callback was found and invoked,
    ``False`` otherwise (unknown id, missing callback, or a callback
    that raised). A zero-argument callback is still dispatched when an
    argument would otherwise be passed, so both call styles are safe.

    This is the seam tests use with no Qt: ``OrbTray`` wires it to each
    ``QAction.triggered`` signal.
    """
    try:
        aid = str(action_id or "").strip()
    except Exception:
        return False
    if not aid:
        return False
    if aid in ("toggle", "lock", "settings", "quit"):
        cb = _callback_for(callbacks, aid)
        if cb is None:
            return False
        try:
            if aid == "lock":
                cb(bool(value))
            else:
                cb()
            return True
        except TypeError:
            return _retry_zero_arg(cb)
        except Exception:
            return False
    if aid.startswith("test:"):
        cb = _callback_for(callbacks, "test")
        if cb is None:
            return False
        state = aid[len("test:"):]
        try:
            cb(state)
            return True
        except TypeError:
            return _retry_zero_arg(cb)
        except Exception:
            return False
    return False


def _import_qt() -> Optional[Tuple[Any, Any, Any, Any]]:
    """Lazy PySide6 import: ``(QApplication, QMenu, QSystemTrayIcon,
    QAction)`` or ``None``. Never raises."""
    try:
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
        return QApplication, QMenu, QSystemTrayIcon, QAction
    except Exception:
        return None


def _make_action(QAction: Any, label: str, parent: Any,
                 checkable: bool = False, checked: bool = False) -> Any:
    """Build one ``QAction`` (label + optional checkable state)."""
    action = QAction(str(label), parent)
    if checkable:
        action.setCheckable(True)
        action.setChecked(bool(checked))
    return action


class OrbTray:
    """``QSystemTrayIcon`` wrapper around the orb's app-level actions.

    Construct it with the callbacks the app wants to receive; call
    ``start`` once a ``QApplication`` exists. Every method is fail-soft:
    when Qt or the system tray is unavailable the tray simply stays
    inert and ``start`` reports ``False``.
    """

    def __init__(
        self,
        *,
        on_toggle: Optional[Callable[..., Any]] = None,
        on_settings: Optional[Callable[..., Any]] = None,
        on_quit: Optional[Callable[..., Any]] = None,
        on_test: Optional[Callable[..., Any]] = None,
        on_lock: Optional[Callable[..., Any]] = None,
        options: Any = None,
        parent: Any = None,
    ) -> None:
        self.options = options
        self._parent = parent
        #: Action-id -> callback, the mapping ``invoke`` consumes.
        self._callbacks: Dict[str, Optional[Callable[..., Any]]] = {
            "toggle": on_toggle,
            "lock": on_lock,
            "settings": on_settings,
            "quit": on_quit,
            "test": on_test,
        }
        self._icon: Any = None
        self._menu: Any = None
        self._actions: Dict[str, Any] = {}
        self._toggle_action: Any = None
        self._lock_action: Any = None
        self._started = False

    # -- lifecycle -----------------------------------------------------
    def start(self) -> bool:
        """Create the tray icon + menu; ``True`` on success.

        Returns ``False`` (never raises) when PySide6 is missing, no
        ``QApplication`` exists yet, no system tray is available, or
        construction fails. A failed attempt tears down any partial
        state so a later retry starts clean.
        """
        if self._icon is not None:
            return True
        try:
            qt = _import_qt()
            if qt is None:
                return False
            QApplication, QMenu, QSystemTrayIcon, QAction = qt
            if QApplication.instance() is None:
                return False
            try:
                if not QSystemTrayIcon.isSystemTrayAvailable():
                    return False
            except Exception:
                return False
            menu = QMenu(self._parent)
            for spec in menu_spec(self.options):
                kind = str(spec.get("kind", "action"))
                if kind == "submenu":
                    submenu = QMenu(str(spec.get("label", "")), menu)
                    for child in spec.get("children", []) or []:
                        child_id = str(child.get("id", ""))
                        action = _make_action(
                            QAction, str(child.get("label", "")), submenu)
                        action.triggered.connect(
                            self._make_dispatch(child_id, is_check=False))
                        submenu.addAction(action)
                        self._actions[child_id] = action
                    menu.addMenu(submenu)
                    continue
                action_id = str(spec.get("id", ""))
                is_check = kind == "check"
                action = _make_action(
                    QAction, str(spec.get("label", "")), menu,
                    checkable=is_check, checked=bool(spec.get("checked", False)))
                action.triggered.connect(
                    self._make_dispatch(action_id, is_check=is_check))
                menu.addAction(action)
                self._actions[action_id] = action
                if action_id == "toggle":
                    self._toggle_action = action
                elif action_id == "lock":
                    self._lock_action = action
            icon = QSystemTrayIcon(self._parent)
            # Programmatic ember icon (no asset file): a soft orange orb.
            try:
                from PySide6.QtCore import Qt as _Qt
                from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
                pm = QPixmap(64, 64)
                pm.fill(_Qt.transparent)
                pt = QPainter(pm)
                pt.setRenderHint(QPainter.Antialiasing, True)
                pt.setPen(_Qt.NoPen)
                pt.setBrush(QColor(255, 84, 0, 70))
                pt.drawEllipse(1, 1, 62, 62)
                pt.setBrush(QColor(255, 110, 30, 230))
                pt.drawEllipse(11, 11, 42, 42)
                pt.setBrush(QColor(255, 205, 140))
                pt.drawEllipse(24, 24, 16, 16)
                pt.end()
                icon.setIcon(QIcon(pm))
            except Exception:
                pass
            icon.setToolTip(TRAY_TOOLTIP)
            icon.setContextMenu(menu)
            icon.show()
            self._icon = icon
            self._menu = menu
            self._started = True
            return True
        except Exception:
            self.stop()
            return False

    def stop(self) -> None:
        """Hide and destroy the tray icon. Never raises.

        Safe on a tray that was never started (no-op) and on a tray
        whose Qt objects already went away.
        """
        try:
            icon = self._icon
            if icon is not None:
                try:
                    icon.hide()
                except Exception:
                    pass
                try:
                    icon.setContextMenu(None)
                except Exception:
                    pass
                try:
                    icon.deleteLater()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self._icon = None
            self._menu = None
            self._actions = {}
            self._toggle_action = None
            self._lock_action = None
            self._started = False

    # -- runtime state -------------------------------------------------
    def set_locked(self, locked: bool) -> None:
        """Update the checkable Lock action; never raises."""
        try:
            action = self._lock_action
            if action is not None:
                action.setChecked(bool(locked))
        except Exception:
            pass

    def set_visible_state(self, visible: bool) -> None:
        """Update the Show/Hide label to match orb visibility; never raises."""
        try:
            action = self._toggle_action
            if action is not None:
                action.setText(
                    TOGGLE_HIDE_LABEL if visible else TOGGLE_SHOW_LABEL)
        except Exception:
            pass

    # -- internal ------------------------------------------------------
    def _make_dispatch(self, action_id: str,
                       is_check: bool) -> Callable[..., None]:
        """Wire one action id to ``invoke`` on the real callback map."""
        def _on_triggered(checked: bool = False) -> None:
            try:
                value = bool(checked) if (is_check or action_id == "lock") \
                    else None
                invoke(action_id, self._callbacks, value=value)
            except Exception:
                pass
        return _on_triggered


__all__ = [
    "OrbTray",
    "ORB_STATES",
    "TOGGLE_SHOW_LABEL",
    "TOGGLE_HIDE_LABEL",
    "LOCK_LABEL",
    "SETTINGS_LABEL",
    "TEST_LABEL",
    "QUIT_LABEL",
    "TRAY_TOOLTIP",
    "state_labels",
    "menu_spec",
    "invoke",
]
