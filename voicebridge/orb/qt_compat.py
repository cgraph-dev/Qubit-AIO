"""Qt availability for the voice orb: xcb-cursor preload + import probe.

Context: Qt 6.5+ on X11 aborts at QApplication construction when
``libxcb-cursor.so.0`` is absent (this host has no system copy and no
sudo; copies exist inside the snap/flatpak runtimes). Pre-loading the
library with ``RTLD_GLOBAL`` lets Qt's xcb plugin resolve it through
the already-loaded globals — no ``LD_LIBRARY_PATH``, no unit-file or
autostart change, no new auth. Everything here is fail-closed and never
raises: when the shim or the import fails the caller stays on Tk.

This module never imports Qt itself, so it is safe headless.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
from typing import List

#: Absolute fallback locations for the cursor library (read-only probes,
#: first hit wins). Snap/flatpak runtime copies are ABI-compatible with
#: this host (same Ubuntu core) and only *read*, never executed directly
#: beyond the dynamic loader's own mapping.
_XCB_CURSOR_CANDIDATES: List[str] = [
    "/snap/kf6-core24/64/usr/lib/x86_64-linux-gnu/libxcb-cursor.so.0",
    "/var/lib/flatpak/runtime/org.kde.Platform/x86_64/6.9/"
    "d2e2a8be58d0fe3097d8aec1c5b09ebcf4976315712c4fbd44cd5036f35c2630"
    "/files/lib/x86_64-linux-gnu/libxcb-cursor.so.0",
]

_LIB_NAME = "libxcb-cursor.so.0"


def _try_load(path: str) -> bool:
    try:
        ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
        return True
    except Exception:
        return False


def ensure_xcb_cursor(extra_paths: List[str] | None = None) -> bool:
    """Make ``libxcb-cursor`` resolvable for Qt's xcb plugin.

    Tries the loader's normal search first (system install), then the
    read-only runtime copies. Pre-loads with ``RTLD_GLOBAL`` so the
    plugin's own link resolves. Returns True when a later
    ``CDLL("libxcb-cursor.so.0")`` — and therefore Qt — will find it.
    Never raises.
    """
    try:
        if _try_load(_LIB_NAME):
            return True
        found = None
        try:
            found = ctypes.util.find_library("xcb-cursor")
        except Exception:
            found = None
        if found and _try_load(found):
            return True
        candidates: List[str] = list(extra_paths or []) + list(
            _XCB_CURSOR_CANDIDATES)
        for path in candidates:
            try:
                if path and os.path.isfile(path) and _try_load(path):
                    return True
            except Exception:
                continue
        # Final check: maybe the preload above registered the soname.
        return _try_load(_LIB_NAME)
    except Exception:
        return False


def qt_importable() -> bool:
    """True when PySide6 imports (no display needed). Never raises."""
    try:
        import importlib.util as _ilu
        return _ilu.find_spec("PySide6.QtWidgets") is not None
    except Exception:
        return False


def qt_usable_on_x11() -> bool:
    """True when a Qt orb window is expected to survive construction.

    Requires a display, an importable PySide6, and a loadable
    xcb-cursor (preloaded as a side effect). Never raises, never opens
    a window, never imports Qt widget modules.
    """
    try:
        if not os.environ.get("DISPLAY"):
            return False
        if not qt_importable():
            return False
        return bool(ensure_xcb_cursor())
    except Exception:
        return False


__all__ = ["ensure_xcb_cursor", "qt_importable", "qt_usable_on_x11"]
