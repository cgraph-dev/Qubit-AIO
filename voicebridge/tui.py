"""Qubit live terminal board.

Qubit's visual surface is the web dashboard and its interactive terminal
surface is the ``qubit-dev`` REPL, so the Qubit-native full-screen answer
is a live operations board on the SAME backend both use
(:func:`runtime_inventory`): product + runtime state, broker/e-stop,
providers, sessions, and capability counts, refreshed in place.

* :func:`render` is pure (inventory dict + width → text lines): every
  line is width-truncated, missing sections degrade to ``?``, and output
  is already redacted upstream by the inventory builders;
* :func:`run_loop` drives a curses screen (``q`` quits, ``r`` refreshes
  now, anything else is ignored; 1s refresh cadence);
* :func:`main` refuses with typed ``TUI_NO_TTY`` outside a real terminal
  (piped/CI use ``status`` or the dashboard instead) and
  ``TUI_NO_CURSES`` when curses is unavailable — never a traceback.

The board is read-only: it starts nothing, dispatches nothing, and
changes no state (no confirmation, e-stop, or rollback surface needed).
"""
from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, List

NO_TTY_CODE = "TUI_NO_TTY"
NO_CURSES_CODE = "TUI_NO_CURSES"
REFRESH_MS = 1000


def _cell(value: Any, width: int = 12) -> str:
    text = str(value if value is not None else "?")
    return text[:width]


def render(inv: Dict[str, Any], width: int = 80) -> List[str]:
    """Render the board as plain text lines (pure; never raises on shape)."""
    try:
        width = max(40, min(int(width or 80), 200))
    except (TypeError, ValueError):
        width = 80

    def line(text: str) -> str:
        return text[:width].ljust(width)

    try:
        product = inv.get("product", {}) if isinstance(inv, dict) else {}
        agent = inv.get("agent", {}) if isinstance(inv, dict) else {}
        broker = inv.get("broker", {}) if isinstance(inv, dict) else {}
        providers = inv.get("providers", {}) if isinstance(inv, dict) else {}
        sessions = inv.get("sessions", {}) if isinstance(inv, dict) else {}
        capabilities = inv.get("capabilities", {}) if isinstance(inv, dict) else {}
        auth = inv.get("auth", {}) if isinstance(inv, dict) else {}
        name = str(product.get("public_identity", "Qubit Agent") or "Qubit Agent")
        hstate = str(agent.get("runtime", "qubit-native"))
        estop = "LATCHED" if broker.get("emergency_stop") else "clear"
        lines = [
            line(f"{name} — live board  (q quit · r refresh)"),
            line(f"agent runtime  : {_cell(hstate, 24)}  "
                 f"emergency stop : {_cell(estop, 10)}"),
            line(f"resident model : {_cell(providers.get('resident_alias', '?'), 30)}"),
            line(f"frontier route : {_cell(providers.get('route', providers.get('frontier', '?')), 30)}"),
            line(f"sessions       : {_cell(sessions.get('count', sessions.get('state', '?')), 8)}  "
                 f"pending approvals : {_cell((inv.get('approvals', {}) or {}).get('pending', '?'), 6)}"),
            line(f"capabilities   : {_cell(capabilities.get('total', '?'), 8)}  "
                 f"providers configured : {_cell(len(auth.get('providers', []) if isinstance(auth.get('providers'), list) else []), 6)}"),
            line("dashboard and `qubit status` show the same backend."),
        ]
        states = capabilities.get("states") if isinstance(capabilities, dict) else None
        if isinstance(states, dict) and states:
            detail = "  ".join(f"{k}:{v}" for k, v in sorted(states.items()))
            lines.append(line(f"states: {detail}"))
        return lines
    except Exception:
        return [("Qubit Agent — live board unavailable "
                 "(inventory shape unexpected)")[:width]]


def refuse_no_tty() -> Dict[str, Any]:
    """Typed refusal outside a real terminal (piped/CI use)."""
    return {"state": "UNAVAILABLE", "code": NO_TTY_CODE,
            "detail": "no interactive terminal detected; use "
                      "`qubit status` for the same backend as typed "
                      "output, or open the web dashboard for the visual "
                      "surface.",
            "owner": "qubit-policy",
            "rollback": "nothing started; nothing to roll back"}


def inventory(cfg: Any) -> Dict[str, Any]:
    """Backend probe shared with status/dashboard (single authority)."""
    from .runtime_inventory import runtime_inventory

    return runtime_inventory(cfg)


def run_loop(stdscr: Any, get_inventory: Callable[[], Dict[str, Any]]) -> str:
    """Drive one board session; returns the quit reason (``quit``)."""
    try:
        stdscr.nodelay(False)
        stdscr.timeout(REFRESH_MS)
    except Exception:
        pass
    while True:
        try:
            height, width = stdscr.getmaxyx()
        except Exception:
            height, width = 24, 80
        try:
            inv = get_inventory()
        except Exception:
            inv = {}
        for row, text in enumerate(render(inv, width)):
            if row >= max(0, height - 1):
                break
            try:
                stdscr.addstr(row, 0, text)
            except Exception:
                break
        try:
            stdscr.refresh()
        except Exception:
            pass
        try:
            key = stdscr.getch()
        except Exception:
            return "input-error"
        if key in (ord("q"), ord("Q")):
            return "quit"
        if key in (ord("r"), ord("R"), 12):  # 12 = ^L / KEY_RESIZE-ish
            continue


def main(cfg: Any) -> Dict[str, Any]:
    """Enter the board, or typed-refuse without a terminal/curses."""
    try:
        is_tty = sys.stdin.isatty()
    except Exception:
        is_tty = False
    if not is_tty or not (os.environ.get("TERM") or "").strip():
        return refuse_no_tty()
    try:
        import curses
    except ImportError:
        out = refuse_no_tty()
        out.update(code=NO_CURSES_CODE,
                   detail="curses is unavailable; use `qubit status` "
                          "or the web dashboard instead.")
        return out

    def _run(stdscr: Any) -> str:
        return run_loop(stdscr, lambda: inventory(cfg))

    try:
        reason = curses.wrapper(_run)
    except Exception as exc:
        out = refuse_no_tty()
        out.update(code=NO_CURSES_CODE,
                   detail=f"terminal setup failed ({exc}); use "
                          f"`qubit status` instead."[:160])
        return out
    return {"state": "OK", "exit": reason}
