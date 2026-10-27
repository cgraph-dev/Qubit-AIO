"""Tkinter preview/status overlay for the desktop helper.

The overlay is presentation only: it never touches the model, the desktop, or
the clipboard. All actions are reported back through callbacks so the helper's
state machine owns every side effect. When tkinter or a display is unavailable
the helper falls back to :class:`NullOverlay` (status via notifications).

Design (2026-10-27, Cyberpunk-2077 HUD restyle):
* deep black/violet shell, neon magenta/cyan/violet accents;
* the chrome is drawn on a Tk ``Canvas`` (clipped-corner polygons, layered
  outlines that fake glow, a grid, scanlines, a circular state gauge and
  segmented system meters) because Tk has no alpha and no CSS;
* the transcript, diff/editor and history stay *real* ``Text`` widgets so
  select/copy/edit continue to work;
* every decorative repaint is coalesced and capped at <=10 Hz, and all
  animation stops while the window is withdrawn.

The previous fixes stay in force: ``show()``/``hide()`` are idempotent, the
window is never re-lifted or re-geometried for a streaming partial, ``topmost``
is set once, and a hidden window schedules no work.
"""
from __future__ import annotations

import difflib
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Palette — Cyberpunk-2077-class neon on a black/violet shell.
# ---------------------------------------------------------------------------
_BG = "#05020b"          # deepest background
_SHELL = "#070410"       # outer panel shell (the clipped shape)
_PANEL = "#0b0716"       # panel interior
_PANEL_2 = "#100a1e"     # text wells
_PANEL_HI = "#171029"    # raised chips / buttons
_BORDER = "#2a1c46"
_GRID = "#120a20"
_SCAN = "#08040f"

_FG = "#eef6ff"
_MUTED = "#9aa7c7"
_FAINT = "#606a88"

_MAGENTA = "#ff2bd6"
_CYAN = "#00e6ff"
_VIOLET = "#9b5cff"
_GREEN = "#39ffa6"
_AMBER = "#ffc24b"
_RED = "#ff4d6d"

# Layered outline colours, outermost -> brightest, used to fake a glow.
_MAGENTA_GLOW = ("#2a0624", "#6b0a5c", "#b5129a", _MAGENTA)
_CYAN_GLOW = ("#04262e", "#075566", "#00a6ba", _CYAN)
_VIOLET_GLOW = ("#140a2c", "#2b1760", "#5a2fb8", _VIOLET)

_ADD_BG = "#0c2c1c"
_ADD_FG = "#7dffb0"
_DEL_BG = "#320b1e"
_DEL_FG = "#ff8fa8"

_ICONS = {
    "idle": "●",
    "listening": "●",
    "processing": "◐",
    "preview": "✦",
    "review": "✦",
    "done": "✓",
    "error": "!",
    "clarify": "?",
    "disabled": "·",
    "interrupted": "■",
}

_PILL = {
    "idle": (_MUTED, _PANEL_HI),
    "listening": (_CYAN, "#04262e"),
    "processing": (_AMBER, "#2e2408"),
    "preview": (_MAGENTA, "#2a0624"),
    "review": (_MAGENTA, "#2a0624"),
    "done": (_GREEN, "#06251a"),
    "error": (_RED, "#320b16"),
    "clarify": (_VIOLET, "#1b1038"),
    "disabled": (_MUTED, _PANEL_HI),
    "interrupted": (_AMBER, "#2e2408"),
}

_STATUS_LABEL = {
    "idle": "Ready",
    "listening": "Listening",
    "processing": "Thinking",
    "preview": "Review",
    "review": "Review",
    "done": "Done",
    "error": "Error",
    "clarify": "Needs you",
    "disabled": "Disabled",
    "interrupted": "Stopped",
}

_STATE_HUE = {
    "idle": _MUTED,
    "listening": _CYAN,
    "processing": _AMBER,
    "preview": _MAGENTA,
    "review": _MAGENTA,
    "done": _GREEN,
    "error": _RED,
    "clarify": _VIOLET,
    "disabled": _FAINT,
    "interrupted": _AMBER,
}

_HISTORY_ICONS = {
    "dictate": "✎",
    "answer": "◆",
    "chat": "◆",
    "edit": "✦",
    "task": "⚙",
    "apply": "✓",
    "error": "!",
}

#: Frames between decorative repaints. 120 ms == ~8.3 Hz (the brief caps the
#: HUD redraw at 10 Hz). Exposed for the regression test.
_TICK_MS = 120

#: Resize-driven static repaints are coalesced on the same cadence as the
#: animation so a drag cannot redraw the chrome faster than 10 Hz.
_RELAYOUT_MS = 100


def _present(*names: str) -> bool:
    try:
        import importlib
        for n in names:
            try:
                importlib.import_module(n)
                return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def _pick_font(*names: str, fallback: str = "Sans") -> str:
    try:
        import tkinter.font as tkfont

        available = {name.lower(): name for name in tkfont.families()}
        for cand in names:
            if cand.lower() in available:
                return available[cand.lower()]
    except Exception:
        pass
    return fallback


def _short(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: max(0, limit - 1)].rstrip() + "…"


def _mix(c1: str, c2: str, t: float) -> str:
    """Blend two #rrggbb colours (Tk has no alpha, so fades are pre-blended)."""
    try:
        t = max(0.0, min(1.0, float(t)))
        a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
        b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % tuple(
            int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))
    except Exception:
        return c1


def _cut_poly(x1: float, y1: float, x2: float, y2: float,
              cut: float) -> List[float]:
    """Points of a rectangle with clipped (45°) corners."""
    c = max(0.0, min(float(cut), (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    return [
        x1 + c, y1,
        x2 - c, y1,
        x2, y1 + c,
        x2, y2 - c,
        x2 - c, y2,
        x1 + c, y2,
        x1, y2 - c,
        x1, y1 + c,
    ]


class NullOverlay:
    """Headless status sink: no window, no callbacks lost."""

    def __init__(self, *, notify: Optional[Callable[[str, str], None]] = None):
        self._notify = notify

    def set_status(self, state: str, detail: str = "") -> None:
        if self._notify:
            try:
                self._notify(state, detail)
            except Exception:
                pass

    def set_live_transcript(self, text: str, final: bool = False) -> None:
        pass

    def set_level(self, level: float) -> None:
        pass

    def show_preview(self, payload: Dict[str, Any]) -> None:
        pass

    def append_history(self, line: str) -> None:
        pass

    def append_history_entry(self, kind: str, heard: str, result: str,
                             ts: str = "") -> None:
        pass

    def show_error(self, code: str, detail: str) -> None:
        self.set_status("error", f"{code}: {detail}")

    def show(self) -> None:
        pass

    def hide(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FlatButton:
    """A borderless Label button: Tk's native Button keeps a theme halo we cannot style."""

    def __init__(self, parent, *, text: str, command: Callable[[], None],
                 bg: str, fg: str, hover_bg: str, font: Any,
                 padx: int = 16, pady: int = 6, disabled_fg: str = _FAINT,
                 border: str = _BORDER):
        import tkinter as tk

        self._command = command
        self._bg = bg
        self._fg = fg
        self._hover = hover_bg
        self._disabled_fg = disabled_fg
        self.widget = tk.Label(parent, text=text, bg=bg, fg=fg, padx=padx,
                               pady=pady, font=font, cursor="hand2",
                               highlightthickness=1, highlightbackground=border,
                               highlightcolor=border, bd=0, takefocus=True)
        self.widget.bind("<Button-1>", self._on_click)
        self.widget.bind("<Enter>", self._on_enter)
        self.widget.bind("<Leave>", self._on_leave)
        self.widget.bind("<Return>", self._on_click)
        self.widget.bind("<space>", self._on_click)

    def pack(self, **kwargs):
        self.widget.pack(**kwargs)
        return self

    def cget(self, key: str):
        return self.widget.cget(key)

    def invoke(self) -> None:
        if self.widget.cget("state") == "normal":
            self._command()

    def set_enabled(self, enabled: bool) -> None:
        self.widget.configure(state="normal" if enabled else "disabled",
                              fg=self._fg if enabled else self._disabled_fg,
                              cursor="hand2" if enabled else "arrow")

    def _on_click(self, _event=None) -> None:
        self.invoke()

    def _on_enter(self, _event) -> None:
        if self.widget.cget("state") == "normal":
            self.widget.configure(bg=self._hover)

    def _on_leave(self, _event) -> None:
        self.widget.configure(bg=self._bg)


class PreviewOverlay:
    """A focused, always-on-top review window with a neon HUD shell."""

    def __init__(self, on_action: Callable[[str, Dict[str, Any]], None]):
        import tkinter as tk

        self._tk = tk
        self._on_action = on_action
        self.root = tk.Tk()
        self.root.title("Qubit Voice Edit")
        self.root.configure(bg=_BG)
        self._topmost_set = False
        self.root.protocol("WM_DELETE_WINDOW", self._close_button)
        self.root.bind("<Escape>", lambda e: self._act("cancel"))
        self.root.bind("<Control-Return>", lambda e: self._act("apply"))
        self.root.bind("<Control-r>", lambda e: self._act("retry"))
        self.root.bind("<Control-z>", lambda e: self._act("revert"))
        self.root.bind("<Control-BackSpace>", lambda e: self._act("cancel"))
        self.root.minsize(700, 560)
        self._fit_screen()

        self._ui_font = _pick_font("Inter", "Segoe UI", "Ubuntu", "Noto Sans",
                                   "Cantarell", "DejaVu Sans")
        self._mono_font = _pick_font("JetBrains Mono", "Fira Code",
                                     "Cascadia Code", "Source Code Pro",
                                     "DejaVu Sans Mono", fallback="TkFixedFont")
        self._status_var = tk.StringVar(value="Ready")
        self._detail_var = tk.StringVar(value="Hold Right-Ctrl and speak.")
        self._meta_var = tk.StringVar(value="")
        self._live_badge_var = tk.StringVar(value="LIVE")
        self._diff_stat_var = tk.StringVar(value="")
        self._clock_var = tk.StringVar(value=time.strftime("%H:%M:%S"))
        self._info_vars: Dict[str, Any] = {
            "model": tk.StringVar(value="native"),
            "privacy": tk.StringVar(value="auto / local"),
            "app": tk.StringVar(value="—"),
            "latency": tk.StringVar(value="—"),
        }
        self._state = "idle"
        self._has_preview = False
        self._listening = False
        self._pulse_job: Optional[str] = None
        self._pulse_on = False
        self._level_history: List[float] = [0.0] * 28
        self._level = 0.0
        self._last_level_ts = 0.0
        self._buffer_fill = 0.0
        self._history_fill = 0.0
        self._history_count = 0
        self._history_seq = 0

        self._hidden = True
        self._anim_job: Optional[str] = None
        self._relayout_job: Optional[str] = None
        self._dyn_job: Optional[str] = None
        self._last_dyn_draw = 0.0
        self._tick_count = 0
        self._last_clock_sec = 0
        self._hud_ok = True
        self._canvas_w = self._window_width
        self._canvas_h = self._window_height
        self._turn_started_at: Optional[float] = None
        self._latency_ms: Optional[int] = None
        self._info_value_chars = 16
        self._shells: List[Tuple[float, float, float, float, Tuple[str, ...]]] = []
        self._rects: Dict[str, Tuple[float, float, float, float]] = {}

        self._build()
        self._set_actions_enabled(False)
        self._clock_var.set(time.strftime("%H:%M:%S"))
        self._relayout()
        self.root.withdraw()

    # -- geometry ---------------------------------------------------------
    def _fit_screen(self) -> None:
        try:
            self.root.update_idletasks()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            w = min(900, max(700, int(sw * 0.52)))
            h = min(760, max(560, sh - 220))
            self._window_width = w
            self._window_height = h
            self._listening_height = h
            self._preview_height = h
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 3)
            self._window_x, self._window_y = x, y
            geometry = f"{w}x{h}+{x}+{y}"
            self._last_geometry = geometry
            self.root.geometry(geometry)
        except Exception:
            self._window_width = 820
            self._window_height = 680
            self._listening_height = 680
            self._preview_height = 680
            self._window_x = self._window_y = 40
            self._last_geometry = "820x680+40+40"
            self.root.geometry(self._last_geometry)

    def _resize(self, preview: bool) -> None:
        """Apply the one canonical size only when it actually differs.

        Listening and preview share one height, so a state transition is a
        no-op here; it exists so callers keep their historical contract.
        """
        try:
            width = getattr(self, "_window_width", 820)
            height = getattr(self, "_window_height", 680)
            geometry = f"{width}x{height}+{self._window_x}+{self._window_y}"
            if geometry == getattr(self, "_last_geometry", ""):
                return
            self._last_geometry = geometry
            self.root.geometry(geometry)
        except Exception:
            pass

    # -- small widget helpers --------------------------------------------
    def _label(self, parent, text="", *, font=None, fg=_FG, bg=_PANEL,
               size=10, weight="normal", anchor="w", **kw):
        return self._tk.Label(parent, text=text, bg=bg, fg=fg, anchor=anchor,
                              font=(font or self._ui_font, size, weight),
                              justify="left", **kw)

    def _mono_label(self, parent, text="", *, fg=_MUTED, bg=_PANEL,
                    size=9, weight="normal", anchor="w", **kw):
        return self._tk.Label(parent, text=text, bg=bg, fg=fg, anchor=anchor,
                              font=(self._mono_font, size, weight),
                              justify="left", **kw)

    def _make_text(self, parent, *, height=1, font=None, bg=_PANEL_2,
                   padx=10, pady=7, **kw):
        return self._tk.Text(
            parent, height=height, wrap="word", bg=bg, fg=_FG,
            insertbackground=_CYAN, selectbackground="#123a52",
            selectforeground=_FG, relief="flat", bd=0, padx=padx, pady=pady,
            font=(font or self._ui_font, 10), highlightthickness=1,
            highlightbackground=_BORDER, highlightcolor=_CYAN,
            spacing1=1, spacing3=2, **kw,
        )

    # -- layout -----------------------------------------------------------
    def _build(self) -> None:
        tk = self._tk
        self._hud = tk.Canvas(self.root, bg=_BG, highlightthickness=0, bd=0,
                              takefocus=False)
        self._hud.pack(fill="both", expand=True)
        self._hud.bind("<Configure>", self._on_configure)
        hud = self._hud

        def frame():
            return tk.Frame(hud, bg=_PANEL, bd=0, highlightthickness=0)

        self._top_frame = frame()
        self._live_frame = frame()
        self.preview_card = frame()
        self.review_card = self.preview_card
        self._info_frame = frame()
        self.history_card = frame()
        self._actions_row = frame()

        self._win_top = hud.create_window(0, 0, anchor="nw",
                                          window=self._top_frame)
        self._win_live = hud.create_window(0, 0, anchor="nw",
                                           window=self._live_frame)
        self._win_preview = hud.create_window(0, 0, anchor="nw",
                                              window=self.preview_card)
        self._win_info = hud.create_window(0, 0, anchor="nw",
                                           window=self._info_frame)
        self._win_history = hud.create_window(0, 0, anchor="nw",
                                              window=self.history_card)
        self._win_actions = hud.create_window(0, 0, anchor="nw",
                                              window=self._actions_row)

        self._build_top(self._top_frame)
        self._build_live(self._live_frame)
        self._build_preview(self.preview_card)
        self._build_info(self._info_frame)
        self._build_history(self.history_card)
        self._build_actions(self._actions_row)

        self._detail_item = hud.create_text(
            0, 0, anchor="w", fill=_MUTED, text=self._detail_var.get(),
            font=(self._mono_font, 9), width=200)
        self._detail_var.trace_add("write", self._on_detail_text)

    def _build_top(self, parent) -> None:
        tk = self._tk
        brand = tk.Frame(parent, bg=_PANEL)
        brand.pack(side="left", padx=(10, 0))
        tk.Label(brand, text="◆", bg=_PANEL, fg=_MAGENTA,
                 font=(self._ui_font, 14, "bold")).pack(side="left", padx=(0, 7))
        tk.Label(brand, text="QUBIT", bg=_PANEL, fg=_FG,
                 font=(self._ui_font, 12, "bold")).pack(side="left")
        tk.Label(brand, text="// VOICE EDIT", bg=_PANEL, fg=_CYAN,
                 font=(self._mono_font, 9, "bold")).pack(side="left", padx=(7, 0))

        right = tk.Frame(parent, bg=_PANEL)
        right.pack(side="right", padx=(0, 10))
        self._clock_label = self._mono_label(right, textvariable=self._clock_var,
                                             fg=_MUTED, size=9)
        self._clock_label.pack(side="right", padx=(12, 0))
        self._pill = tk.Label(right, text="●  Ready", bg=_PANEL_HI, fg=_MUTED,
                              font=(self._ui_font, 9, "bold"), padx=10, pady=3,
                              highlightthickness=1, highlightbackground=_BORDER,
                              highlightcolor=_BORDER)
        self._pill.pack(side="right")
        self._sys_label = self._mono_label(right, text="● SYS", fg=_GREEN, size=8)
        self._sys_label.pack(side="right", padx=(0, 10))

    def _build_live(self, parent) -> None:
        tk = self._tk
        head = tk.Frame(parent, bg=_PANEL)
        head.pack(fill="x", padx=10, pady=(8, 0))
        self._mono_label(head, text="YOU SAID // LIVE FEED", fg=_FAINT,
                         size=8, weight="bold").pack(side="left")
        self._live_badge = tk.Label(
            head, textvariable=self._live_badge_var, bg="#04262e",
            fg=_CYAN, font=(self._mono_font, 8, "bold"), padx=7, pady=1,
            highlightthickness=1, highlightbackground=_CYAN)
        self._live_badge.pack(side="right")
        self._pulse_canvas = tk.Canvas(head, width=10, height=10, bg=_PANEL,
                                       highlightthickness=0, bd=0)
        self._pulse_canvas.pack(side="right", padx=(0, 7))
        self._pulse_dot = self._pulse_canvas.create_oval(2, 2, 8, 8,
                                                         fill=_FAINT, outline="")
        self._level_canvas = tk.Canvas(head, width=132, height=16, bg=_PANEL,
                                       highlightthickness=0, bd=0)
        self._level_canvas.pack(side="right", padx=(0, 10))
        self._level_bars: List[int] = []
        self._rebuild_level_bars()

        self.live_text = self._make_text(parent, height=1, bg=_PANEL_2,
                                         padx=9, pady=5)
        self.live_text.pack(fill="both", expand=True, padx=10, pady=(3, 6))
        self.live_text.tag_configure("placeholder", foreground=_FAINT,
                                     font=(self._ui_font, 10, "italic"))
        self.live_text.tag_configure("final", foreground=_FG)
        self.live_text.configure(state="disabled")
        self._set_live_text("Hold Right-Ctrl and speak — your words appear here live.",
                            placeholder=True)

    def _build_preview(self, parent) -> None:
        tk = self._tk
        head = tk.Frame(parent, bg=_PANEL)
        head.pack(fill="x", padx=10, pady=(8, 0))
        self._mono_label(head, text="PROPOSED EDIT // DIFF", fg=_FAINT,
                         size=8, weight="bold").pack(side="left")
        self._diff_label = self._mono_label(head, textvariable=self._diff_stat_var,
                                            fg=_MUTED, size=8)
        self._diff_label.pack(side="right")
        self._meta_label = self._label(parent, textvariable=self._meta_var,
                                       size=8, fg=_MUTED, bg=_PANEL)
        self._meta_label.pack(fill="x", padx=10, pady=(3, 0))
        self.text = self._make_text(parent, height=1, bg=_PANEL_2,
                                    padx=9, pady=5)
        self.text.pack(fill="both", expand=True, padx=10, pady=(4, 6))
        self.text.tag_configure("add", background=_ADD_BG, foreground=_ADD_FG)
        self.text.tag_configure("del", background=_DEL_BG, foreground=_DEL_FG,
                                overstrike=True)
        self.text.tag_configure("empty", foreground=_FAINT,
                                font=(self._ui_font, 10, "italic"))
        self.text.configure(state="disabled")
        self._set_preview_text("Nothing to review yet — a proposed edit appears here.",
                               empty=True)
        # Real Text editor (kept un-packed until the user asks to edit).
        self._edit_entry = self._make_text(parent, height=3, bg=_PANEL_2)
        self._edit_entry.bind("<Control-Return>", lambda e: self._submit_edit())
        self._edit_entry.bind("<Escape>", lambda e: self._hide_edit())

    def _build_info(self, parent) -> None:
        tk = self._tk
        grid = tk.Frame(parent, bg=_PANEL)
        grid.pack(fill="both", expand=True, padx=10, pady=(5, 5))
        grid.grid_columnconfigure(1, weight=1)
        rows = (("MODEL", "model"), ("PRIVACY", "privacy"),
                ("APP", "app"), ("LATENCY", "latency"))
        self._info_labels: Dict[str, Any] = {}
        for i, (title, key) in enumerate(rows):
            self._mono_label(grid, text=title, fg=_FAINT, size=7,
                             weight="bold").grid(row=i, column=0, sticky="w",
                                                 padx=(0, 6), pady=0)
            val = tk.Label(grid, textvariable=self._info_vars[key], bg=_PANEL,
                           fg=_FG, anchor="w",
                           font=(self._mono_font, 9))
            val.grid(row=i, column=1, sticky="we", pady=0)
            self._info_labels[key] = val

    def _build_history(self, parent) -> None:
        tk = self._tk
        head = tk.Frame(parent, bg=_PANEL)
        head.pack(fill="x", padx=10, pady=(7, 0))
        self._mono_label(head, text="HISTORY // CONSOLE", fg=_FAINT,
                         size=8, weight="bold").pack(side="left")
        _FlatButton(head, text="Clear", command=self._clear_history,
                    bg=_PANEL_HI, fg=_MUTED, hover_bg=_BORDER,
                    font=(self._mono_font, 8), padx=7, pady=2,
                    border=_BORDER).pack(side="right")
        body = tk.Frame(parent, bg=_PANEL)
        body.pack(fill="both", expand=True, padx=10, pady=(3, 6))
        self._history_text = self._make_text(
            body, height=1, font=self._mono_font, bg=_PANEL_2)
        self._history_text.configure(spacing1=0, spacing3=1, padx=9, pady=4)
        self._history_text.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(body, command=self._history_text.yview,
                          bg=_PANEL_HI, troughcolor=_PANEL, bd=0,
                          highlightthickness=0, width=10)
        sb.pack(side="right", fill="y")
        self._history_text.configure(yscrollcommand=sb.set, state="disabled")
        self._history_text.tag_configure("ts", foreground=_FAINT)
        self._history_text.tag_configure("heard", foreground=_CYAN)
        self._history_text.tag_configure("result", foreground=_FG)
        for kind, colour in (("dictate", _CYAN), ("answer", _VIOLET),
                             ("chat", _VIOLET), ("edit", _GREEN),
                             ("task", _AMBER), ("apply", _GREEN),
                             ("error", _RED)):
            self._history_text.tag_configure(f"kind-{kind}", foreground=colour,
                                             font=(self._mono_font, 9, "bold"))
        self._history_text.tag_configure("empty", foreground=_FAINT,
                                         font=(self._ui_font, 9, "italic"))
        self._history_text.configure(state="normal")
        self._history_text.insert("end", "No interactions yet. A turn appears here "
                                         "after dictation, an answer, or an edit.\n",
                                  "empty")
        self._history_text.configure(state="disabled")

    def _build_actions(self, parent) -> None:
        tk = self._tk
        row = tk.Frame(parent, bg=_PANEL)
        row.pack(fill="x", padx=10, pady=(5, 0))
        self._buttons: Dict[str, Any] = {}
        self._add_button(row, "Apply", "apply", primary=True)
        self._add_button(row, "Copy", "copy", accent=_CYAN)
        self._add_button(row, "Edit", "edit", accent=_VIOLET)
        self._add_button(row, "Retry", "retry", ghost=True)
        self._add_button(row, "Revert", "revert", ghost=True)
        self._add_button(row, "Cancel", "cancel", ghost=True)
        self._hint = self._mono_label(
            parent,
            "Ctrl+Enter apply   ·   Ctrl+R retry   ·   Ctrl+Z revert   ·   Esc cancel",
            fg=_FAINT, size=8)
        self._hint.pack(fill="x", padx=12, pady=(0, 4))

    def _add_button(self, parent, label: str, action: str, *,
                    primary: bool = False, ghost: bool = False,
                    accent: str = "") -> None:
        if primary:
            bg, fg, hover, border = _MAGENTA, "#12000f", "#ff6ce4", "#ff8aea"
        elif ghost:
            bg, fg, hover, border = _PANEL, _MUTED, _PANEL_HI, _BORDER
        elif accent:
            bg, fg, hover, border = _PANEL_HI, accent, "#221838", accent
        else:
            bg, fg, hover, border = _PANEL_HI, _FG, _BORDER, _BORDER
        button = _FlatButton(
            parent, text=label, command=lambda a=action: self._act(a),
            bg=bg, fg=fg, hover_bg=hover,
            font=(self._ui_font, 9, "bold" if primary else "normal"),
            border=border)
        button.pack(side="left", padx=(0, 7))
        self._buttons[action] = button

    def _set_actions_enabled(self, enabled: bool) -> None:
        for action in ("apply", "copy", "edit", "retry", "revert"):
            button = self._buttons.get(action)
            if button is not None:
                button.set_enabled(enabled)
        cancel = self._buttons.get("cancel")
        if cancel is not None:
            cancel.set_enabled(True)

    def _rebuild_level_bars(self) -> None:
        self._level_canvas.delete("all")
        self._level_bars = []
        width = int(self._level_canvas.cget("width"))
        count = len(self._level_history)
        gap = 2
        bar_w = max(2, (width - gap * (count - 1)) // count)
        x = 0
        for _ in range(count):
            self._level_bars.append(self._level_canvas.create_rectangle(
                x, 14, x + bar_w, 16, fill=_BORDER, outline=""))
            x += bar_w + gap

    # -- canvas layout ----------------------------------------------------
    def _on_configure(self, event) -> None:
        try:
            if event.widget is not self._hud:
                return
            if event.width <= 1 or event.height <= 1:
                return
            if (event.width, event.height) == (self._canvas_w, self._canvas_h):
                return
            self._canvas_w = int(event.width)
            self._canvas_h = int(event.height)
        except Exception:
            return
        self._schedule_relayout()

    def _schedule_relayout(self) -> None:
        if self._relayout_job is not None:
            return
        try:
            self._relayout_job = self.root.after(_RELAYOUT_MS, self._relayout)
        except Exception:
            self._relayout_job = None

    def _layout_rects(self, W: int, H: int) -> None:
        pad = 12
        gap = 8
        cut = 9
        top_h = 44
        bottom_h = 74
        detail_h = 20
        hist_h = max(105, min(140, int(H * 0.19)))

        top = (pad, pad, W - pad, pad + top_h)
        content_y = top[3] + gap
        bottom = (pad, H - pad - bottom_h, W - pad, H - pad)
        detail_y = bottom[1] - gap - detail_h
        hist = (pad, detail_y - gap - hist_h, W - pad, detail_y - gap)
        content_bottom = hist[1] - gap
        rail_w = max(188, min(224, int(W * 0.245)))
        left_w = W - 2 * pad - rail_w - gap
        rail_x = pad + left_w + gap

        live_h = max(98, min(146, int((content_bottom - content_y) * 0.33)))
        live = (pad, content_y, pad + left_w, content_y + live_h)
        preview = (pad, live[3] + gap, pad + left_w, content_bottom)
        rail = (rail_x, content_y, W - pad, content_bottom)

        rail_h = rail[3] - rail[1]
        gauge_h = max(96, min(160, int(rail_h * 0.36)))
        info_h = max(100, min(140, int(rail_h * 0.38)))
        meters_h = rail_h - gauge_h - info_h - 2 * gap
        if meters_h < 44:
            info_h = max(96, info_h - (44 - meters_h))
            meters_h = rail_h - gauge_h - info_h - 2 * gap
        small_rail = (rail[2] - rail[0]) < 205
        self._info_value_chars = 14 if small_rail else 20
        try:
            for lbl in self._info_labels.values():
                lbl.configure(font=(self._mono_font,
                                    8 if small_rail else 9))
        except Exception:
            pass
        gauge = (rail[0], rail[1], rail[2], rail[1] + gauge_h)
        info = (rail[0], gauge[3] + gap, rail[2], gauge[3] + gap + info_h)
        meters = (rail[0], info[3] + gap, rail[2], rail[3])

        self._rects = {
            "top": top, "live": live, "preview": preview, "info": info,
            "history": hist, "actions": bottom, "gauge": gauge,
            "meters": meters, "detail": (pad, detail_y, W - pad,
                                          detail_y + detail_h),
        }
        self._shells = [
            (top[0], top[1], top[2], top[3], _MAGENTA_GLOW),
            (live[0], live[1], live[2], live[3], _CYAN_GLOW),
            (preview[0], preview[1], preview[2], preview[3], _MAGENTA_GLOW),
            (gauge[0], gauge[1], gauge[2], gauge[3], _VIOLET_GLOW),
            (info[0], info[1], info[2], info[3], _VIOLET_GLOW),
            (meters[0], meters[1], meters[2], meters[3], _VIOLET_GLOW),
            (hist[0], hist[1], hist[2], hist[3], _CYAN_GLOW),
            (bottom[0], bottom[1], bottom[2], bottom[3], _MAGENTA_GLOW),
        ]
        self._cut = cut

    def _relayout(self) -> None:
        self._relayout_job = None
        W = max(1, int(self._canvas_w))
        H = max(1, int(self._canvas_h))
        self._layout_rects(W, H)
        inset = self._cut + 2
        windows = (
            (self._win_top, self._rects["top"]),
            (self._win_live, self._rects["live"]),
            (self._win_preview, self._rects["preview"]),
            (self._win_info, self._rects["info"]),
            (self._win_history, self._rects["history"]),
            (self._win_actions, self._rects["actions"]),
        )
        for item, (x1, y1, x2, y2) in windows:
            try:
                self._hud.coords(item, x1 + inset, y1 + inset)
                self._hud.itemconfigure(item, width=max(1, int(x2 - x1 - 2 * inset)),
                                        height=max(1, int(y2 - y1 - 2 * inset)))
            except Exception:
                pass
        dx, dy, _, _ = self._rects["detail"]
        try:
            self._hud.coords(self._detail_item, dx + 74, dy + 11)
            self._hud.itemconfigure(self._detail_item,
                                    width=max(40, W - dx - 88))
        except Exception:
            pass
        self._redraw_static()
        self._draw_dynamic(safe=True)

    # -- decorative canvas ------------------------------------------------
    def _redraw_static(self) -> None:
        """Redraw the non-animated HUD. Failure degrades to a plain dark shell."""
        try:
            self._draw_static()
            self._hud_ok = True
        except Exception:
            self._hud_ok = False
            try:
                self._hud.delete("static")
                self._hud.configure(bg=_BG)
            except Exception:
                pass

    def _draw_static(self) -> None:
        hud = self._hud
        hud.delete("static")
        W = max(1, int(self._canvas_w))
        H = max(1, int(self._canvas_h))

        # Fake a vertical gradient with flat bands (Tk has no alpha).
        bands = 10
        for i in range(bands):
            y1 = int(H * i / bands)
            y2 = int(H * (i + 1) / bands)
            hud.create_rectangle(0, y1, W, y2, fill=_mix(_BG, "#0a0618",
                                                         i / bands),
                                 outline="", tags="static")
        # Grid.
        for x in range(0, W, 36):
            hud.create_line(x, 0, x, H, fill=_GRID, tags="static")
        for y in range(0, H, 36):
            hud.create_line(0, y, W, y, fill=_GRID, tags="static")
        # Scanlines (subtle, static; the moving beam is a separate dynamic item).
        for y in range(0, H, 4):
            hud.create_line(0, y, W, y, fill=_SCAN, tags="static")
        # Corner brackets.
        bl = 26
        for (cx, cy, dx, dy) in ((0, 0, 1, 1), (W, 0, -1, 1),
                                 (0, H, 1, -1), (W, H, -1, -1)):
            hud.create_line(cx + dx * 10, cy + dy * bl,
                            cx + dx * 10, cy + dy * 10,
                            cx + dx * bl, cy + dy * 10,
                            fill=_MAGENTA, width=2, tags="static")
            hud.create_line(cx + dx * 12, cy + dy * bl,
                            cx + dx * 12, cy + dy * 12,
                            cx + dx * bl, cy + dy * 12,
                            fill="#5c0a4e", width=1, tags="static")
        # Panels.
        for (x1, y1, x2, y2, glow) in self._shells:
            self._draw_shell(x1, y1, x2, y2, glow, tags="static")
        # Status strip chrome.
        dx, dy, ex, ey = self._rects["detail"]
        hud.create_line(dx, ey, ex, ey, fill=_BORDER, tags="static")
        hud.create_line(dx, dy, dx + 5, dy, fill=_MAGENTA, width=2,
                        tags="static")
        hud.create_text(dx + 8, dy + 11, anchor="w", text="▮ STATUS",
                        fill=_MAGENTA, font=(self._mono_font, 8, "bold"),
                        tags="static")
        # The status read-out is created once in _build(); the background is
        # redrawn later, so lift it back above the new static items.
        try:
            hud.tag_raise(self._detail_item)
        except Exception:
            pass

    def _draw_shell(self, x1: float, y1: float, x2: float, y2: float,
                    glow: Tuple[str, ...], *, tags: str = "static") -> None:
        hud = self._hud
        cut = getattr(self, "_cut", 9)
        hud.create_polygon(_cut_poly(x1, y1, x2, y2, cut), fill=_SHELL,
                           outline="", tags=tags)
        for pad, colour, width in ((3, glow[0], 1), (2, glow[1], 1),
                                   (1, glow[2], 1)):
            hud.create_polygon(
                _cut_poly(x1 - pad, y1 - pad, x2 + pad, y2 + pad, cut + pad),
                fill="", outline=colour, width=width, tags=tags)
        hud.create_polygon(_cut_poly(x1, y1, x2, y2, cut), fill="",
                           outline=glow[3], width=2, tags=tags)
        # Inner hairline for depth.
        hud.create_polygon(_cut_poly(x1 + 4, y1 + 4, x2 - 4, y2 - 4,
                                     max(2, cut - 3)),
                           fill="", outline=_mix(glow[3], _BORDER, 0.72),
                           width=1, tags=tags)
        # Bright corner ticks on the clipped edges.
        c = cut
        for (px, py, sx, sy) in ((x1, y1, 1, 1), (x2, y1, -1, 1),
                                 (x1, y2, 1, -1), (x2, y2, -1, -1)):
            hud.create_line(px + sx * c, py, px + sx * 2, py,
                            fill=glow[3], width=2, tags=tags)

    # -- animation --------------------------------------------------------
    def _schedule_tick(self) -> None:
        if self._hidden or self._anim_job is not None:
            return
        try:
            self._anim_job = self.root.after(_TICK_MS, self._tick)
        except Exception:
            self._anim_job = None

    def _stop_anim(self) -> None:
        for attr in ("_anim_job", "_dyn_job"):
            job = getattr(self, attr, None)
            setattr(self, attr, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass

    def _request_dynamic(self) -> None:
        """Redraw the HUD chrome at most once per 100 ms (event-driven)."""
        if self._hidden or self._dyn_job is not None:
            return
        delay = int(max(0.0, (self._last_dyn_draw + 0.1
                              - time.monotonic()) * 1000.0))
        try:
            self._dyn_job = self.root.after(delay, self._flush_dynamic)
        except Exception:
            self._dyn_job = None

    def _flush_dynamic(self) -> None:
        self._dyn_job = None
        if self._hidden:
            return
        self._last_dyn_draw = time.monotonic()
        self._draw_dynamic(safe=True)

    def _tick(self) -> None:
        self._anim_job = None
        if self._hidden:
            return
        if self._dyn_job is not None:
            try:
                self.root.after_cancel(self._dyn_job)
            except Exception:
                pass
            self._dyn_job = None
        self._tick_count += 1
        now = time.monotonic()
        if now - self._last_level_ts > 0.16:
            decayed = self._level_history[-1] * 0.86
            self._level_history = self._level_history[1:] + [
                max(0.0, decayed)]
            self._level = max(0.0, self._level * 0.86)
            self._render_level_bars()
        sec = int(now)
        if sec != self._last_clock_sec:
            self._last_clock_sec = sec
            try:
                self._clock_var.set(time.strftime("%H:%M:%S"))
            except Exception:
                pass
        if self._listening:
            self._pulse_on = not self._pulse_on
            try:
                self._pulse_canvas.itemconfigure(
                    self._pulse_dot,
                    fill=_CYAN if self._pulse_on else "#05424d")
            except Exception:
                pass
        self._last_dyn_draw = now
        self._draw_dynamic(safe=True)
        self._schedule_tick()

    def _draw_dynamic(self, *, safe: bool = False) -> None:
        if not self._hud_ok:
            return
        try:
            self._draw_gauge()
            self._draw_meters()
            self._draw_beam()
        except Exception:
            if not safe:
                raise
            self._hud_ok = False
            try:
                self._hud.delete("dyn")
            except Exception:
                pass

    def _draw_gauge(self) -> None:
        hud = self._hud
        hud.delete("gauge")
        gx1, gy1, gx2, gy2 = self._rects["gauge"]
        cx = (gx1 + gx2) / 2.0
        cy = (gy1 + gy2) / 2.0
        span = min(gx2 - gx1, gy2 - gy1)
        r = max(24.0, span / 2.0 - (10.0 if span < 130 else 14.0))
        hue = _STATE_HUE.get(self._state, _MUTED)
        t = self._tick_count
        phase = (t * 7.0) % 360.0

        hud.create_oval(cx - r, cy - r, cx + r, cy + r,
                        outline=_BORDER, width=2, tags=("dyn", "gauge"))
        hud.create_oval(cx - r + 5, cy - r + 5, cx + r - 5, cy + r - 5,
                        outline="#1d1233", width=1, tags=("dyn", "gauge"))
        # Tick ring.
        for i in range(36):
            ang = math.radians(i * 10 - 90)
            major = i % 6 == 0
            outer = r - 1
            inner = r - (8 if major else 4)
            hud.create_line(cx + math.cos(ang) * inner,
                            cy + math.sin(ang) * inner,
                            cx + math.cos(ang) * outer,
                            cy + math.sin(ang) * outer,
                            fill=hue if major else "#2b1c48",
                            width=2 if major else 1,
                            tags=("dyn", "gauge"))
        # Dim full ring, then the live sweep and its glow.
        hud.create_arc(cx - r + 10, cy - r + 10, cx + r - 10, cy + r - 10,
                       start=0, extent=359, style="arc", outline="#241738",
                       width=2, tags=("dyn", "gauge"))
        sweep = 96
        hud.create_arc(cx - r + 10, cy - r + 10, cx + r - 10, cy + r - 10,
                       start=-phase, extent=sweep, style="arc", outline=hue,
                       width=3, tags=("dyn", "gauge"))
        hud.create_arc(cx - r + 13, cy - r + 13, cx + r - 13, cy + r - 13,
                       start=-phase + 4, extent=max(8, sweep - 12),
                       style="arc", outline=_mix(hue, _BG, 0.55), width=1,
                       tags=("dyn", "gauge"))
        # Level-driven inner ring.
        lvl = max(0.0, min(1.0, self._level_history[-1]))
        ir = max(10.0, (r - 22) * (0.55 + 0.45 * lvl))
        hud.create_oval(cx - ir, cy - ir, cx + ir, cy + ir,
                        outline=_mix(hue, _BG, 0.45), width=2,
                        tags=("dyn", "gauge"))
        # Crosshair.
        hud.create_line(cx - 5, cy, cx + 5, cy, fill=hue,
                        tags=("dyn", "gauge"))
        hud.create_line(cx, cy - 5, cx, cy + 5, fill=hue,
                        tags=("dyn", "gauge"))
        # Centre state read-out.
        label = _STATUS_LABEL.get(self._state, self._state).upper()
        big = 9 if r >= 52 else 8
        small = 7 if r >= 44 else 6
        hud.create_text(cx, cy - (8 if r >= 44 else 2), text=label, fill=hue,
                        font=(self._mono_font, big, "bold"),
                        tags=("dyn", "gauge"))
        if r >= 44:
            hud.create_text(cx, cy + 6, text="SYS//STATE", fill=_FAINT,
                            font=(self._mono_font, small),
                            tags=("dyn", "gauge"))
        if gy2 - gy1 >= 104:
            hud.create_text(cx, gy2 - 5, text="◤ CORE ◢", fill=_VIOLET,
                            font=(self._mono_font, 7, "bold"),
                            tags=("dyn", "gauge"))

    def _draw_meters(self) -> None:
        hud = self._hud
        hud.delete("meters")
        mx1, my1, mx2, my2 = self._rects["meters"]
        hue = _STATE_HUE.get(self._state, _MUTED)
        rows = (
            ("INPUT", max(0.0, min(1.0, self._level_history[-1])), hue),
            ("BUFFER", max(0.0, min(1.0, self._buffer_fill)), _CYAN),
            ("LOG", max(0.0, min(1.0, self._history_fill)), _VIOLET),
        )
        x0 = mx1 + 10
        x1 = mx2 - 10
        top = my1 + 9
        row_h = max(12.0, (my2 - my1 - 16) / 3.0)
        segs = 12
        for i, (name, value, colour) in enumerate(rows):
            y = top + i * row_h
            hud.create_text(x0, y, anchor="nw", text=name, fill=_FAINT,
                            font=(self._mono_font, 7, "bold"),
                            tags=("dyn", "meters"))
            bar_x = x0 + 46
            bar_w = max(20.0, x1 - bar_x)
            hud.create_rectangle(bar_x, y + 1, x1, y + 7,
                                 outline="#1d1233", fill="#0a0614",
                                 tags=("dyn", "meters"))
            gap = 2
            seg_w = (bar_w - gap * (segs - 1)) / segs
            lit = int(round(value * segs))
            for s in range(segs):
                sx = bar_x + s * (seg_w + gap)
                if s < lit:
                    fill = colour if s < segs - 3 else _mix(colour, _FG, 0.25)
                else:
                    fill = "#1a1030"
                hud.create_rectangle(sx, y + 1, sx + seg_w, y + 7,
                                     fill=fill, outline="",
                                     tags=("dyn", "meters"))
            hud.create_text(x1, y + 4, anchor="e",
                            text=f"{int(round(value * 100)):02d}%",
                            fill=_MUTED, font=(self._mono_font, 7),
                            tags=("dyn", "meters"))

    def _draw_beam(self) -> None:
        hud = self._hud
        hud.delete("beam")
        W = max(1, int(self._canvas_w))
        H = max(1, int(self._canvas_h))
        y = (self._tick_count * 9) % max(1, H)
        hud.create_line(0, y, W, y, fill="#3a0a32", tags=("dyn", "beam"))
        hud.create_line(0, y + 1, W, y + 1, fill="#7a1068",
                        tags=("dyn", "beam"))

    # -- live transcription ----------------------------------------------
    def _set_live_text(self, text: str, *, placeholder: bool = False) -> None:
        self.live_text.configure(state="normal")
        self.live_text.delete("1.0", "end")
        if text:
            self.live_text.insert("1.0", text,
                                  "placeholder" if placeholder else "final")
        self.live_text.configure(state="disabled")
        self.live_text.see("end")

    def _reset_preview_placeholder(self) -> None:
        self._has_preview = False
        self._set_actions_enabled(False)
        self._diff_stat_var.set("")
        self._meta_var.set("")
        self._hide_edit()
        self._set_preview_text(
            "Nothing to review yet — a proposed edit appears here.", empty=True)
        for key, default in (("model", "native"), ("privacy", "auto / local"),
                             ("app", "—"), ("latency", "—")):
            try:
                self._info_vars[key].set(default)
            except Exception:
                pass
        self._latency_ms = None
        self._buffer_fill = 0.0

    def set_live_transcript(self, text: str, final: bool = False) -> None:
        """Stream the interim hypothesis; the full text replaces the previous one.

        ``final`` marks the settled transcript (helper's accurate pass) so the
        badge stops pulsing and the label changes from LIVE to FINAL.

        This never calls ``show()``, ``lift()`` or ``geometry()``: partials are
        just text, and re-showing the topmost window on every tick is what made
        it flicker over full-screen apps (f5042141e).
        """
        value = str(text or "").strip()
        if not final and not value:
            self._reset_preview_placeholder()
        if value:
            self._set_live_text(value)
            self._buffer_fill = min(1.0, len(value) / 220.0)
        if final:
            self._live_badge_var.set("FINAL")
            try:
                self._live_badge.configure(bg="#06251a", fg=_GREEN,
                                           highlightbackground=_GREEN)
            except Exception:
                pass
            self._set_listening(False)
            if value:
                self._detail_var.set("Heard you — preparing the next step.")
        else:
            self._live_badge_var.set("LIVE")
            try:
                self._live_badge.configure(bg="#04262e", fg=_CYAN,
                                           highlightbackground=_CYAN)
            except Exception:
                pass
            self._set_listening(True)
            if value:
                self._detail_var.set("Listening — keep speaking.")

    def set_level(self, level: float) -> None:
        """Draw a smooth voice meter from 0..1 (no-op when the window is hidden)."""
        try:
            value = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            return
        self._level = value
        self._last_level_ts = time.monotonic()
        self._level_history = self._level_history[1:] + [
            max(value, self._level_history[-1] * 0.72)]
        if not self._hidden:
            self._render_level_bars()

    def _render_level_bars(self) -> None:
        for idx, bar in enumerate(self._level_bars):
            sample = self._level_history[idx]
            height = 2 + int(sample * 12)
            colour = _CYAN if sample > 0.18 else _BORDER
            if sample > 0.65:
                colour = _VIOLET
            try:
                coords = self._level_canvas.coords(bar)
                self._level_canvas.coords(bar, coords[0], max(0, 16 - height),
                                          coords[2], 16)
                self._level_canvas.itemconfigure(bar, fill=colour)
            except Exception:
                pass

    def _set_listening(self, listening: bool) -> None:
        self._listening = bool(listening)
        if self._listening:
            return
        self._pulse_on = False
        try:
            self._pulse_canvas.itemconfigure(self._pulse_dot, fill=_GREEN)
        except Exception:
            pass

    # -- status -----------------------------------------------------------
    def set_status(self, state: str, detail: str = "") -> None:
        previous = getattr(self, "_state", "idle")
        self._state = state or "idle"
        label = _STATUS_LABEL.get(self._state, self._state.replace("_", " ").title())
        icon = _ICONS.get(self._state, "●")
        fg, bg = _PILL.get(self._state, (_MUTED, _PANEL_HI))
        try:
            self._pill.configure(text=f"{icon}  {label}", fg=fg, bg=bg,
                                 highlightbackground=fg, highlightcolor=fg)
        except Exception:
            pass
        self._status_var.set(label)
        if detail:
            self._detail_var.set(str(detail))
        if self._state == "listening":
            if previous != "listening":
                self._resize(False)
                self._turn_started_at = time.monotonic()
            self._set_listening(True)
            self._live_badge_var.set("LIVE")
            try:
                self._live_badge.configure(bg="#04262e", fg=_CYAN,
                                           highlightbackground=_CYAN)
            except Exception:
                pass
        elif self._state in ("processing", "preview", "review", "done", "error"):
            self._set_listening(False)
        if self._state == "error":
            self._detail_label_fg = _RED
        elif self._state == "done":
            self._detail_label_fg = _GREEN
        else:
            self._detail_label_fg = _MUTED
        try:
            self._hud.itemconfigure(self._detail_item,
                                    fill=self._detail_label_fg)
        except Exception:
            pass
        # Decorative repaints are throttled to <=10 Hz and coalesced.
        self._request_dynamic()
        self._schedule_tick()

    def _on_detail_text(self, *_args) -> None:
        try:
            item = getattr(self, "_detail_item", None)
            if item is not None:
                self._hud.itemconfigure(item, text=self._detail_var.get())
        except Exception:
            pass

    # -- preview ----------------------------------------------------------
    def _set_preview_text(self, text: str, *, empty: bool = False) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if text:
            self.text.insert("1.0", text, "empty" if empty else "")
        self.text.configure(state="disabled")

    def show_preview(self, payload: Dict[str, Any]) -> None:
        self._resize(True)
        self._state = "preview"
        self._has_preview = True
        self._set_actions_enabled(True)
        self._set_listening(False)
        self._live_badge_var.set("FINAL")
        try:
            self._live_badge.configure(bg="#06251a", fg=_GREEN,
                                       highlightbackground=_GREEN)
        except Exception:
            pass

        detected = payload.get("detected") or {}
        chips = [
            str(detected.get("action") or "rewrite"),
            str(detected.get("style") or ""),
            str(detected.get("language") or ""),
        ]
        model = str(payload.get("model") or "local")
        provider = str(payload.get("provider") or "")
        privacy = str(payload.get("privacy_mode") or "auto")
        locality = str(payload.get("locality") or "local")
        app = str(payload.get("target_app") or "")
        meta = [c for c in chips if c]
        meta.append(f"model {model}" + (f" ({provider})" if provider else ""))
        meta.append(f"privacy {privacy}/{locality}")
        if app:
            meta.append(f"app {app}")
        self._meta_var.set("   ·   ".join(meta))

        latency = payload.get("latency_ms")
        if latency is None and self._turn_started_at is not None:
            latency = int(round((time.monotonic() - self._turn_started_at) * 1000))
        if latency is not None:
            try:
                self._latency_ms = int(latency)
            except (TypeError, ValueError):
                self._latency_ms = None
        try:
            chars = int(getattr(self, "_info_value_chars", 16))
            self._info_vars["model"].set(_short(
                f"{model}" + (f" · {provider}" if provider else ""), chars))
            self._info_vars["privacy"].set(_short(
                f"{privacy}/{locality}", chars))
            self._info_vars["app"].set(_short(app or "desktop", chars))
            self._info_vars["latency"].set(
                f"{self._latency_ms} ms" if self._latency_ms is not None
                else "—")
        except Exception:
            pass

        original = str(payload.get("original", ""))
        proposed = str(payload.get("proposed", ""))
        self._render_diff(original, proposed)
        self._hide_edit()

        notes = []
        if payload.get("injection_suspected"):
            notes.append("Instruction-like text in the selection was treated as data.")
        if payload.get("clarification_returned"):
            notes.append("The model asked a question — edit or retry.")
        dropped = payload.get("facts_dropped") or []
        if dropped:
            notes.append(f"{len(dropped)} identifier(s) from the original were not "
                         "found in the result — review before applying.")
        if notes:
            self._detail_var.set("  ·  ".join(notes))
        else:
            self._detail_var.set("Review the edit, then apply it — or copy/edit/retry.")
        self.set_status("preview")
        self.show()

    def _render_diff(self, original: str, proposed: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        matcher = difflib.SequenceMatcher(None, original, proposed)
        added = removed = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                self.text.insert("end", proposed[j1:j2])
            elif tag == "delete":
                self.text.insert("end", original[i1:i2], "del")
                removed += i2 - i1
            elif tag == "insert":
                self.text.insert("end", proposed[j1:j2], "add")
                added += j2 - j1
            elif tag == "replace":
                self.text.insert("end", original[i1:i2], "del")
                self.text.insert("end", proposed[j1:j2], "add")
                removed += i2 - i1
                added += j2 - j1
        self.text.configure(state="disabled")
        words_before = len(original.split())
        words_after = len(proposed.split())
        delta = words_after - words_before
        delta_text = f"{delta:+d} word" + ("s" if abs(delta) != 1 else "")
        self._diff_stat_var.set(f"+{added} / −{removed} chars   ·   {delta_text}")

    def _hide_edit(self) -> None:
        try:
            self._edit_entry.pack_forget()
        except Exception:
            pass

    def _submit_edit(self) -> None:
        text = self._edit_entry.get("1.0", "end-1c")
        self._act("edit_submit", {"text": text})

    def show_error(self, code: str, detail: str) -> None:
        self._state = "error"
        self.set_status("error", f"{code}: {detail}")
        self._set_actions_enabled(self._has_preview)
        self.show()

    # -- history ----------------------------------------------------------
    def _history_empty_guard(self) -> None:
        try:
            tags = self._history_text.tag_names("1.0")
        except Exception:
            tags = ()
        if "empty" in tags:
            self._history_text.delete("1.0", "end")

    def append_history_entry(self, kind: str, heard: str, result: str,
                             ts: str = "") -> None:
        """One structured history row: number/time/kind, then heard → result."""
        try:
            self._history_text.configure(state="normal")
            self._history_empty_guard()
            self._history_count += 1
            stamp = ts or time.strftime("%H:%M:%S")
            kind_key = str(kind or "turn")
            icon = _HISTORY_ICONS.get(kind_key, "·")
            if self._history_text.index("end-1c") != "1.0":
                self._history_text.insert("end", "\n")
            self._history_text.insert("end", f"{self._history_count:>2}  ", "ts")
            self._history_text.insert("end", f"{stamp}  ", "ts")
            self._history_text.insert(
                "end", f"{icon} {kind_key}",
                f"kind-{kind_key}" if f"kind-{kind_key}" in self._history_text.tag_names()
                else "ts")
            self._history_text.insert("end", "\n    ", "ts")
            self._history_text.insert(
                "end", "“" + (_short(heard, 100) or "—") + "”", "heard")
            if result:
                self._history_text.insert("end", "  →  ", "ts")
                self._history_text.insert("end", "“" + _short(result, 150) + "”",
                                          "result")
            self._history_text.see("end")
            self._history_text.configure(state="disabled")
            self._history_fill = min(1.0, self._history_count / 12.0)
        except Exception:
            pass

    def append_history(self, line: str) -> None:
        """Legacy plain-line append (kept for callers without structured data)."""
        try:
            self._history_text.configure(state="normal")
            self._history_empty_guard()
            self._history_seq += 1
            stamp = time.strftime("%H:%M:%S")
            self._history_text.insert(
                "end", f"{self._history_seq:>2}  {stamp}   {line}\n", "result")
            self._history_text.see("end")
            self._history_text.configure(state="disabled")
            self._history_fill = min(1.0, (self._history_count +
                                           self._history_seq) / 12.0)
        except Exception:
            pass

    def _clear_history(self) -> None:
        try:
            self._history_text.configure(state="normal")
            self._history_text.delete("1.0", "end")
            self._history_text.insert(
                "end", "History cleared. A turn appears here after dictation, an "
                       "answer, or an edit.\n", "empty")
            self._history_text.configure(state="disabled")
            self._history_count = 0
            self._history_seq = 0
            self._history_fill = 0.0
        except Exception:
            pass

    # -- actions ----------------------------------------------------------
    def _act(self, action: str, payload: Optional[Dict[str, Any]] = None):
        self._on_action(action, payload or {})

    def _close_button(self) -> None:
        """WM_DELETE_WINDOW (title-bar X): hide first, then notify cancel."""
        try:
            self.hide()
        except Exception:
            pass
        self._act("cancel")

    # -- public API -------------------------------------------------------
    def show(self) -> None:
        """Show once; later calls only keep the animation alive.

        No lift, no topmost re-assert and no geometry: those are what made the
        panel fight a full-screen window on every partial.
        """
        try:
            withdrawn = self.root.state() == "withdrawn"
        except Exception:
            withdrawn = True
        if withdrawn:
            self.root.deiconify()
            try:
                self.root.lift()
            except Exception:
                pass
            if not getattr(self, "_topmost_set", False):
                try:
                    self.root.attributes("-topmost", True)
                    self._topmost_set = True
                except Exception:
                    pass
            self._hidden = False
            self._schedule_relayout()
            self._request_dynamic()
        else:
            self._hidden = False
        self._schedule_tick()

    def hide(self) -> None:
        if self.root.state() != "withdrawn":
            self.root.withdraw()
        self._hidden = True
        self._set_listening(False)
        self._stop_anim()

    def close(self) -> None:
        self._hidden = True
        self._stop_anim()
        for attr in ("_relayout_job", "_dyn_job"):
            job = getattr(self, attr, None)
            setattr(self, attr, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        # Break Tcl variable references while the interpreter is still alive:
        # destroying the root first makes their __del__ run against a dead
        # interpreter on a worker/gc thread, which raises an unraisable.
        for name in ("_status_var", "_detail_var", "_meta_var",
                     "_live_badge_var", "_diff_stat_var", "_clock_var"):
            var = getattr(self, name, None)
            if var is not None:
                try:
                    var._tk = None
                except Exception:
                    pass
        for var in getattr(self, "_info_vars", {}).values():
            try:
                var._tk = None
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass


def make_overlay(on_action: Callable[[str, Dict[str, Any]], None], *,
                 headless: bool = False):
    if headless or not _present("tkinter"):
        return NullOverlay()
    try:
        return PreviewOverlay(on_action)
    except Exception:
        return NullOverlay()


__all__ = ["PreviewOverlay", "NullOverlay", "make_overlay"]
