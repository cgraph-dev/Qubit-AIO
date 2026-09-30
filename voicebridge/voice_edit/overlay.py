"""Tkinter preview/status overlay for the desktop helper.

The overlay is presentation only: it never touches the model, the desktop, or
the clipboard. All actions are reported back through callbacks so the helper's
state machine owns every side effect. When tkinter or a display is unavailable
the helper falls back to :class:`NullOverlay` (status via notifications).
"""
from __future__ import annotations

import difflib
from typing import Any, Callable, Dict, List, Optional

_BG = "#11151c"
_FG = "#e6edf3"
_MUTED = "#8b949e"
_ADD = "#1f6f3a"
_DEL = "#7a2230"
_ACCENT = "#1f6feb"


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

    def show_preview(self, payload: Dict[str, Any]) -> None:
        pass

    def show_error(self, code: str, detail: str) -> None:
        self.set_status("error", f"{code}: {detail}")

    def show(self) -> None:
        pass

    def hide(self) -> None:
        pass

    def close(self) -> None:
        pass


class PreviewOverlay:
    """A small always-on-top preview window with the full action set."""

    def __init__(self, on_action: Callable[[str, Dict[str, Any]], None]):
        import tkinter as tk
        self._tk = tk
        self._on_action = on_action
        self.root = tk.Tk()
        self.root.title("Qubit Voice Edit")
        self.root.configure(bg=_BG)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", lambda: self._act("cancel"))
        self.root.bind("<Escape>", lambda e: self._act("cancel"))
        # Keyboard shortcuts so the preview is usable without a pointer and can
        # be driven by the helper's own automation/tests.
        self.root.bind("<Control-Return>", lambda e: self._act("apply"))
        self.root.bind("<Control-r>", lambda e: self._act("retry"))
        self.root.bind("<Control-z>", lambda e: self._act("revert"))
        self.root.bind("<Control-BackSpace>", lambda e: self._act("cancel"))
        self._status_var = tk.StringVar(value="ready")
        self._meta_var = tk.StringVar(value="")
        self._build()
        self.root.withdraw()

    # -- layout -----------------------------------------------------------
    def _build(self) -> None:
        tk = self._tk
        outer = tk.Frame(self.root, bg=_BG, padx=12, pady=10)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, textvariable=self._status_var, bg=_BG, fg=_FG,
                 font=("Sans", 12, "bold"), anchor="w").pack(fill="x")
        tk.Label(outer, textvariable=self._meta_var, bg=_BG, fg=_MUTED,
                 font=("Sans", 9), anchor="w", justify="left").pack(fill="x")
        body = tk.Frame(outer, bg=_BG)
        body.pack(fill="both", expand=True, pady=(8, 8))
        self.text = tk.Text(body, height=12, width=72, wrap="word", bg="#0b0f14",
                            fg=_FG, insertbackground=_FG, relief="flat")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("add", background=_ADD)
        self.text.tag_configure("del", background=_DEL, foreground=_MUTED)
        self.text.configure(state="disabled")
        btns = tk.Frame(outer, bg=_BG)
        btns.pack(fill="x")
        self._buttons: List[Any] = []
        for label, action in (("Apply", "apply"), ("Copy", "copy"),
                              ("Edit", "edit"), ("Retry", "retry"),
                              ("Revert", "revert"), ("Cancel", "cancel")):
            b = tk.Button(btns, text=label, command=lambda a=action: self._act(a),
                          bg="#21262d", fg=_FG, relief="flat", padx=10)
            b.pack(side="left", padx=(0, 6))
            self._buttons.append(b)
        self._edit_entry = tk.Text(outer, height=4, wrap="word", bg="#0b0f14",
                                   fg=_FG, relief="flat")
        self._edit_entry.pack(fill="x", pady=(8, 0))
        self._edit_entry.pack_forget()
        self._edit_entry.bind("<Control-Return>", lambda e: self._submit_edit())

    def _act(self, action: str, payload: Optional[Dict[str, Any]] = None):
        self._on_action(action, payload or {})

    def _submit_edit(self) -> None:
        text = self._edit_entry.get("1.0", "end-1c")
        self._act("edit_submit", {"text": text})

    # -- public API -------------------------------------------------------
    def set_status(self, state: str, detail: str = "") -> None:
        self._status_var.set(f"Qubit: {state}")
        if detail:
            self._meta_var.set(detail)

    def show_preview(self, payload: Dict[str, Any]) -> None:
        self._status_var.set("Qubit: review")
        meta = (f"command: {payload.get('detected', {}).get('action', '')} "
                f"{payload.get('detected', {}).get('style', '')}  |  "
                f"model: {payload.get('model', '')} "
                f"({payload.get('provider', '')}, {payload.get('privacy_mode', '')})"
                f"  |  app: {payload.get('target_app', '')}")
        if payload.get("injection_suspected"):
            meta += "  |  instruction-like text detected in selection (treated as data)"
        if payload.get("clarification_returned"):
            meta += "  |  model asked a question — edit or retry"
        if payload.get("facts_dropped"):
            meta += f"  |  {len(payload['facts_dropped'])} identifier(s) not found in result"
        self._meta_var.set(meta)
        self._render_diff(str(payload.get("original", "")),
                          str(payload.get("proposed", "")))
        self._edit_entry.pack_forget()
        self.show()

    def _render_diff(self, original: str, proposed: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        sm = difflib.SequenceMatcher(None, original, proposed)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                self.text.insert("end", proposed[j1:j2])
            elif tag == "delete":
                self.text.insert("end", original[i1:i2], "del")
            elif tag == "insert":
                self.text.insert("end", proposed[j1:j2], "add")
            elif tag == "replace":
                self.text.insert("end", original[i1:i2], "del")
                self.text.insert("end", proposed[j1:j2], "add")
        self.text.configure(state="disabled")

    def show_error(self, code: str, detail: str) -> None:
        self._status_var.set(f"Qubit: error ({code})")
        self._meta_var.set(detail)
        self.show()

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass

    def hide(self) -> None:
        self.root.withdraw()

    def close(self) -> None:
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
