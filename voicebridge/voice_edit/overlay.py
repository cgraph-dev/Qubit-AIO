"""Tkinter preview/status overlay for the desktop helper.

The overlay is presentation only: it never touches the model, the desktop, or
the clipboard. All actions are reported back through callbacks so the helper's
state machine owns every side effect. When tkinter or a display is unavailable
the helper falls back to :class:`NullOverlay` (status via notifications).

Design goals (2026-10-26):
* show the live interim transcript while the user is still speaking, not only
  the final sentence;
* make the review step legible: status pill, live voice meter, detected chips,
  a word-level diff with add/remove colours, and a clear primary action;
* keep history as a first-class, copyable record instead of a wall of text.
"""
from __future__ import annotations

import difflib
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# Palette: Qubit's dark obsidian surfaces with a cyan primary and violet
# secondary. Kept in one place so every card/state stays coherent.
_BG = "#0b0e14"
_CARD = "#11161f"
_CARD_HI = "#171e2a"
_BORDER = "#243041"
_FG = "#e6edf3"
_MUTED = "#93a4b8"
_FAINT = "#5d6b7d"
_ACCENT = "#22d3ee"
_ACCENT_DARK = "#0b3b45"
_VIOLET = "#a78bfa"
_GREEN = "#34d399"
_AMBER = "#fbbf24"
_RED = "#f87171"
_ADD_BG = "#12351f"
_ADD_FG = "#86efac"
_DEL_BG = "#3a1620"
_DEL_FG = "#fda4af"

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
    "idle": (_MUTED, _CARD_HI),
    "listening": (_ACCENT, _ACCENT_DARK),
    "processing": (_AMBER, "#3a2d0d"),
    "preview": (_VIOLET, "#2b2140"),
    "review": (_VIOLET, "#2b2140"),
    "done": (_GREEN, "#123a24"),
    "error": (_RED, "#3a1620"),
    "clarify": (_AMBER, "#3a2d0d"),
    "disabled": (_MUTED, _CARD_HI),
    "interrupted": (_AMBER, "#3a2d0d"),
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

_HISTORY_ICONS = {
    "dictate": "✎",
    "answer": "◆",
    "chat": "◆",
    "edit": "✦",
    "task": "⚙",
    "apply": "✓",
    "error": "!",
}


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
        """One compact history row: number/time/kind, then heard → result."""
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
            self._history_text.insert(
                "end", f"{stamp}  {icon} {kind_key}",
                f"kind-{kind_key}" if f"kind-{kind_key}" in self._history_text.tag_names()
                else "ts")
            self._history_text.insert("end", "\n    ", "ts")
            self._history_text.insert(
                "end", "“" + (_short(heard, 100) or "—") + "”", "heard")
            if result:
                self._history_text.insert("end", "\n    → ", "ts")
                self._history_text.insert("end", "“" + _short(result, 150) + "”",
                                          "result")
            self._history_text.see("end")
            self._history_text.configure(state="disabled")
        except Exception:
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
                 padx: int = 18, pady: int = 7, disabled_fg: str = _FAINT):
        import tkinter as tk

        self._command = command
        self._bg = bg
        self._fg = fg
        self._hover = hover_bg
        self._disabled_fg = disabled_fg
        self.widget = tk.Label(parent, text=text, bg=bg, fg=fg, padx=padx,
                               pady=pady, font=font, cursor="hand2",
                               highlightthickness=0, bd=0, takefocus=False)
        self.widget.bind("<Button-1>", self._on_click)
        self.widget.bind("<Enter>", self._on_enter)
        self.widget.bind("<Leave>", self._on_leave)

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

    def _on_click(self, _event) -> None:
        self.invoke()

    def _on_enter(self, _event) -> None:
        if self.widget.cget("state") == "normal":
            self.widget.configure(bg=self._hover)

    def _on_leave(self, _event) -> None:
        self.widget.configure(bg=self._bg)


class PreviewOverlay:
    """A focused, always-on-top review window with live dictation feedback."""

    def __init__(self, on_action: Callable[[str, Dict[str, Any]], None]):
        import tkinter as tk

        self._tk = tk
        self._on_action = on_action
        self.root = tk.Tk()
        self.root.title("Qubit Voice Edit")
        self.root.configure(bg=_BG)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self._close_button)
        self.root.bind("<Escape>", lambda e: self._act("cancel"))
        self.root.bind("<Control-Return>", lambda e: self._act("apply"))
        self.root.bind("<Control-r>", lambda e: self._act("retry"))
        self.root.bind("<Control-z>", lambda e: self._act("revert"))
        self.root.bind("<Control-BackSpace>", lambda e: self._act("cancel"))
        self.root.minsize(680, 560)
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
        self._state = "idle"
        self._has_preview = False
        self._listening = False
        self._pulse_job: Optional[str] = None
        self._pulse_on = False
        self._level_history: List[float] = [0.0] * 28
        self._history_count = 0
        self._build()
        self._set_actions_enabled(False)
        self.root.withdraw()

    # -- geometry ---------------------------------------------------------
    def _fit_screen(self) -> None:
        try:
            self.root.update_idletasks()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            w = min(860, max(700, sw // 2))
            h = min(730, max(560, sh - 200))
            self._window_width = w
            self._listening_height = min(600, h)
            self._preview_height = h
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 3)
            self._window_x, self._window_y = x, y
            self.root.geometry(f"{w}x{self._listening_height}+{x}+{y}")
        except Exception:
            self._window_width = 820
            self._listening_height = 600
            self._preview_height = 720
            self._window_x = self._window_y = 40
            self.root.geometry("820x600+40+40")

    def _resize(self, preview: bool) -> None:
        try:
            width = getattr(self, "_window_width", 820)
            height = self._preview_height if preview else self._listening_height
            self.root.geometry(f"{width}x{height}+{self._window_x}+{self._window_y}")
        except Exception:
            pass

    # -- small widget helpers --------------------------------------------
    def _label(self, parent, text="", *, font=None, fg=_FG, bg=_BG,
               size=10, weight="normal", anchor="w", **kw):
        return self._tk.Label(parent, text=text, bg=bg, fg=fg, anchor=anchor,
                              font=(font or self._ui_font, size, weight),
                              justify="left", **kw)

    def _card(self, parent, **pack):
        frame = self._tk.Frame(parent, bg=_CARD, highlightthickness=1,
                               highlightbackground=_BORDER,
                               highlightcolor=_BORDER)
        frame.pack(**pack)
        return frame

    def _text(self, parent, *, height=4, font=None, bg=_BG, **pack):
        text = self._tk.Text(
            parent, height=height, wrap="word", bg=bg, fg=_FG,
            insertbackground=_ACCENT, selectbackground="#1e3a4a",
            selectforeground=_FG, relief="flat", bd=0, padx=10, pady=8,
            font=(font or self._ui_font, 10), highlightthickness=1,
            highlightbackground=_BORDER, highlightcolor=_ACCENT,
            spacing1=1, spacing3=2,
        )
        text.pack(**pack)
        return text

    # -- layout -----------------------------------------------------------
    def _build(self) -> None:
        tk = self._tk
        outer = tk.Frame(self.root, bg=_BG, padx=18, pady=16)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=_BG)
        header.pack(fill="x")
        brand = tk.Frame(header, bg=_BG)
        brand.pack(side="left", anchor="w")
        tk.Label(brand, text="◆", bg=_BG, fg=_ACCENT,
                 font=(self._ui_font, 16, "bold")).pack(side="left", padx=(0, 8))
        titles = tk.Frame(brand, bg=_BG)
        titles.pack(side="left")
        self._label(titles, "Qubit Voice Edit", size=15, weight="bold").pack(anchor="w")
        self._label(titles, "Speak naturally · review before it lands",
                    size=9, fg=_MUTED).pack(anchor="w")

        right = tk.Frame(header, bg=_BG)
        right.pack(side="right", anchor="e")
        self._pill = tk.Label(right, text="●  Ready", bg=_CARD_HI, fg=_MUTED,
                              font=(self._ui_font, 9, "bold"), padx=12, pady=5)
        self._pill.pack(side="right")

        self._detail_label = self._label(
            outer, textvariable=self._detail_var, size=9, fg=_MUTED)
        self._detail_label.pack(fill="x", pady=(8, 0))

        # ---- live transcript -------------------------------------------
        live = self._card(outer, fill="both", expand=False, pady=(12, 0))
        live_head = tk.Frame(live, bg=_CARD)
        live_head.pack(fill="x", padx=12, pady=(10, 0))
        self._label(live_head, "YOU SAID", size=8, weight="bold", fg=_FAINT,
                    bg=_CARD).pack(side="left")
        self._live_badge = tk.Label(
            live_head, textvariable=self._live_badge_var, bg=_ACCENT_DARK,
            fg=_ACCENT, font=(self._ui_font, 8, "bold"), padx=8, pady=2)
        self._live_badge.pack(side="right")
        self._pulse_canvas = tk.Canvas(live_head, width=10, height=10, bg=_CARD,
                                       highlightthickness=0)
        self._pulse_canvas.pack(side="right", padx=(0, 8))
        self._pulse_dot = self._pulse_canvas.create_oval(2, 2, 8, 8,
                                                         fill=_FAINT, outline="")
        self._level_canvas = tk.Canvas(live_head, width=150, height=18, bg=_CARD,
                                       highlightthickness=0)
        self._level_canvas.pack(side="right", padx=(0, 10))
        self._level_bars: List[int] = []
        self._rebuild_level_bars()

        self.live_text = self._text(live, height=3, bg=_CARD,
                                    fill="both", expand=True, padx=12, pady=(2, 12))
        self.live_text.tag_configure("placeholder", foreground=_FAINT,
                                     font=(self._ui_font, 10, "italic"))
        self.live_text.tag_configure("final", foreground=_FG)
        self.live_text.configure(state="disabled")
        self._set_live_text("Hold Right-Ctrl and speak — your words appear here live.",
                            placeholder=True)

        # ---- preview ----------------------------------------------------
        self.preview_card = self._card(outer, fill="both", expand=True,
                                       pady=(10, 0))
        preview_head = tk.Frame(self.preview_card, bg=_CARD)
        preview_head.pack(fill="x", padx=12, pady=(10, 0))
        self._label(preview_head, "PROPOSED EDIT", size=8, weight="bold",
                    fg=_FAINT, bg=_CARD).pack(side="left")
        self._label(preview_head, textvariable=self._diff_stat_var, size=8,
                    fg=_MUTED, bg=_CARD).pack(side="right")
        self._meta_label = self._label(
            self.preview_card, textvariable=self._meta_var, size=9, fg=_MUTED,
            bg=_CARD)
        self._meta_label.pack(fill="x", padx=12, pady=(8, 0))
        self.text = self._text(self.preview_card, height=10, bg=_BG,
                               fill="both", expand=True, padx=12, pady=(6, 6))
        self.text.tag_configure("add", background=_ADD_BG, foreground=_ADD_FG)
        self.text.tag_configure("del", background=_DEL_BG, foreground=_DEL_FG,
                                overstrike=True)
        self.text.tag_configure("empty", foreground=_FAINT,
                                font=(self._ui_font, 10, "italic"))
        self.text.configure(state="disabled")
        self._set_preview_text("Nothing to review yet — a proposed edit appears here.",
                               empty=True)
        self._edit_entry = self._text(self.preview_card, height=4, bg=_BG)
        self._edit_entry.pack_forget()
        self._edit_entry.bind("<Control-Return>", lambda e: self._submit_edit())
        self._edit_entry.bind("<Escape>", lambda e: self._hide_edit())
        self.review_card = self.preview_card

        # ---- actions ----------------------------------------------------
        actions = tk.Frame(outer, bg=_BG)
        self._actions_row = actions
        actions.pack(fill="x", pady=(12, 0))
        self._buttons: Dict[str, Any] = {}
        self._add_button(actions, "Apply", "apply", primary=True)
        self._add_button(actions, "Copy", "copy")
        self._add_button(actions, "Edit", "edit")
        self._add_button(actions, "Retry", "retry", ghost=True)
        self._add_button(actions, "Revert", "revert", ghost=True)
        self._add_button(actions, "Cancel", "cancel", ghost=True)
        self._hint = self._label(
            outer,
            "Ctrl+Enter apply   ·   Ctrl+R retry   ·   Ctrl+Z revert   ·   Esc cancel",
            size=8, fg=_FAINT)
        self._hint.pack(fill="x", pady=(6, 0))

        # ---- history ----------------------------------------------------
        hist = self._card(outer, fill="both", expand=False, pady=(10, 0))
        self.history_card = hist
        hist_head = tk.Frame(hist, bg=_CARD)
        hist_head.pack(fill="x", padx=12, pady=(8, 0))
        self._label(hist_head, "HISTORY", size=8, weight="bold", fg=_FAINT,
                    bg=_CARD).pack(side="left")
        _FlatButton(hist_head, text="Clear", command=self._clear_history,
                    bg=_CARD_HI, fg=_MUTED, hover_bg=_BORDER,
                    font=(self._ui_font, 8), padx=8, pady=3).pack(side="right")
        body = tk.Frame(hist, bg=_CARD)
        body.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        self._history_text = self._tk.Text(
            body, height=6, wrap="word", bg=_BG, fg=_FG, relief="flat", bd=0,
            padx=10, pady=6, font=(self._mono_font, 9), spacing1=1, spacing3=3,
            highlightthickness=1, highlightbackground=_BORDER,
            selectbackground="#1e3a4a", selectforeground=_FG)
        self._history_text.pack(side="left", fill="both", expand=True)
        sb = self._tk.Scrollbar(body, command=self._history_text.yview,
                                bg=_CARD_HI, troughcolor=_CARD, bd=0,
                                highlightthickness=0, width=10)
        sb.pack(side="right", fill="y")
        self._history_text.configure(yscrollcommand=sb.set, state="disabled")
        self._history_text.tag_configure("ts", foreground=_FAINT)
        self._history_text.tag_configure("heard", foreground=_MUTED)
        self._history_text.tag_configure("result", foreground=_FG)
        for kind, colour in (("dictate", _ACCENT), ("answer", _VIOLET),
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
        self.preview_card.pack_forget()

    def _add_button(self, parent, label: str, action: str, *,
                    primary: bool = False, ghost: bool = False) -> None:
        bg = _ACCENT if primary else (_CARD if ghost else _CARD_HI)
        fg = _ACCENT_DARK if primary else (_MUTED if ghost else _FG)
        hover = "#67e8f9" if primary else _BORDER
        button = _FlatButton(
            parent, text=label, command=lambda a=action: self._act(a),
            bg=bg, fg=fg, hover_bg=hover,
            font=(self._ui_font, 9, "bold" if primary else "normal"))
        button.pack(side="left", padx=(0, 8))
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
                x, 16, x + bar_w, 18, fill=_BORDER, outline=""))
            x += bar_w + gap

    # -- live transcription ----------------------------------------------
    def _set_live_text(self, text: str, *, placeholder: bool = False) -> None:
        self.live_text.configure(state="normal")
        self.live_text.delete("1.0", "end")
        if text:
            self.live_text.insert("1.0", text,
                                  "placeholder" if placeholder else "final")
        self.live_text.configure(state="disabled")
        self.live_text.see("end")

    def set_live_transcript(self, text: str, final: bool = False) -> None:
        """Stream the interim hypothesis; the full text replaces the previous one.

        ``final`` marks the settled transcript (helper's accurate pass) so the
        badge stops pulsing and the label changes from LIVE to FINAL.
        """
        value = str(text or "").strip()
        if not final and not value:
            # A new turn opened: clear the previous review so old text cannot be
            # mistaken for this utterance.
            self.preview_card.pack_forget()
            self._has_preview = False
            self._set_actions_enabled(False)
            self._diff_stat_var.set("")
            self._meta_var.set("")
            self._edit_entry.pack_forget()
        if value:
            self._set_live_text(value)
        if final:
            self._live_badge_var.set("FINAL")
            self._live_badge.configure(bg=_CARD_HI, fg=_GREEN)
            self._set_listening(False)
            if value:
                self._detail_var.set("Heard you — preparing the next step.")
        else:
            self._live_badge_var.set("LIVE")
            self._live_badge.configure(bg=_ACCENT_DARK, fg=_ACCENT)
            self._set_listening(True)
            if value:
                self._detail_var.set("Listening — keep speaking.")
            self.show()

    def set_level(self, level: float) -> None:
        """Draw a smooth voice meter from 0..1 (no-op when the window is hidden)."""
        try:
            value = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            return
        self._level_history = self._level_history[1:] + [
            max(value, self._level_history[-1] * 0.72)]
        for idx, bar in enumerate(self._level_bars):
            sample = self._level_history[idx]
            height = 2 + int(sample * 14)
            colour = _ACCENT if sample > 0.18 else _BORDER
            if sample > 0.65:
                colour = _VIOLET
            self._level_canvas.coords(bar, *self._level_canvas.coords(bar)[:1],
                                      max(0, 18 - height),
                                      self._level_canvas.coords(bar)[2], 18)
            self._level_canvas.itemconfigure(bar, fill=colour)

    def _set_listening(self, listening: bool) -> None:
        self._listening = bool(listening)
        if self._listening:
            if self._pulse_job is None:
                self._pulse()
        else:
            if self._pulse_job is not None:
                try:
                    self.root.after_cancel(self._pulse_job)
                except Exception:
                    pass
                self._pulse_job = None
            try:
                self._pulse_canvas.itemconfigure(self._pulse_dot, fill=_GREEN)
            except Exception:
                pass

    def _pulse(self) -> None:
        if not self._listening:
            self._pulse_job = None
            return
        self._pulse_on = not self._pulse_on
        try:
            self._pulse_canvas.itemconfigure(
                self._pulse_dot, fill=_ACCENT if self._pulse_on else _ACCENT_DARK)
            self._pulse_job = self.root.after(520, self._pulse)
        except Exception:
            self._pulse_job = None

    # -- status -----------------------------------------------------------
    def set_status(self, state: str, detail: str = "") -> None:
        self._state = state or "idle"
        label = _STATUS_LABEL.get(self._state, self._state.replace("_", " ").title())
        icon = _ICONS.get(self._state, "●")
        fg, bg = _PILL.get(self._state, (_MUTED, _CARD_HI))
        try:
            self._pill.configure(text=f"{icon}  {label}", fg=fg, bg=bg)
        except Exception:
            pass
        self._status_var.set(label)
        if detail:
            self._detail_var.set(str(detail))
        if self._state == "listening":
            self._resize(False)
            self._set_listening(True)
            self._live_badge_var.set("LIVE")
            self._live_badge.configure(bg=_ACCENT_DARK, fg=_ACCENT)
        elif self._state in ("processing", "preview", "review", "done", "error"):
            self._set_listening(False)
        if self._state == "error":
            self._detail_label.configure(fg=_RED)
        elif self._state == "done":
            self._detail_label.configure(fg=_GREEN)
        else:
            self._detail_label.configure(fg=_MUTED)

    # -- preview ----------------------------------------------------------
    def _set_preview_text(self, text: str, *, empty: bool = False) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if text:
            self.text.insert("1.0", text, "empty" if empty else "")
        self.text.configure(state="disabled")

    def show_preview(self, payload: Dict[str, Any]) -> None:
        self._resize(True)
        if not self.preview_card.winfo_ismapped():
            self.preview_card.pack(fill="both", expand=True, pady=(10, 0),
                                   before=self._actions_row)
        self._state = "preview"
        self._has_preview = True
        self.set_status("preview")
        self._set_actions_enabled(True)
        self._set_listening(False)
        self._live_badge_var.set("FINAL")
        self._live_badge.configure(bg=_CARD_HI, fg=_GREEN)

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

        original = str(payload.get("original", ""))
        proposed = str(payload.get("proposed", ""))
        self._render_diff(original, proposed)
        self._edit_entry.pack_forget()

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
        self._edit_entry.pack_forget()

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
        """One structured history row: time, kind icon, heard → result."""
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
            self._history_text.insert("end", f"{icon} {kind_key}", f"kind-{kind_key}"
                                      if f"kind-{kind_key}" in self._history_text.tag_names()
                                      else "ts")
            self._history_text.insert(
                "end", f"   {stamp}\n    ", "ts")
            self._history_text.insert("end", _short(heard, 160) or "—", "heard")
            if result:
                self._history_text.insert("end", "\n    → ", "ts")
                self._history_text.insert("end", _short(result, 220), "result")
            self._history_text.see("end")
            self._history_text.configure(state="disabled")
        except Exception:
            pass

    def append_history(self, line: str) -> None:
        """Legacy plain-line append (kept for callers without structured data)."""
        try:
            self._history_text.configure(state="normal")
            self._history_empty_guard()
            self._history_seq = getattr(self, "_history_seq", 0) + 1
            stamp = time.strftime("%H:%M:%S")
            self._history_text.insert(
                "end", f"{self._history_seq:>2}  {stamp}   {line}\n", "result")
            self._history_text.see("end")
            self._history_text.configure(state="disabled")
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
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass

    def hide(self) -> None:
        self.root.withdraw()
        self._set_listening(False)

    def close(self) -> None:
        self._set_listening(False)
        # Break Tcl variable references while the interpreter is still alive:
        # destroying the root first makes their __del__ run against a dead
        # interpreter on a worker/gc thread, which raises an unraisable.
        for name in ("_status_var", "_detail_var", "_meta_var",
                     "_live_badge_var", "_diff_stat_var"):
            var = getattr(self, name, None)
            if var is not None:
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
