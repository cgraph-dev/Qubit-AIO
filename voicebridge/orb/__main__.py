"""Launch the OS-level voice orb: ``python -m voicebridge.orb``.

- default: show the orb and run until stopped. The window carries NO
  chrome (owner 2026-10-04: no close/minimize buttons) — exits are
  ``--quit`` / ``--toggle`` below and the service stop only.
- ``--hidden``: start withdrawn (proof mode / keybinding daemons).
- ``--quit-after S``: exit cleanly by itself after S seconds (proof:
  never leaves a stray window on the owner's screen).
- ``--once``: no window at all -- one status poll, resolve the state,
  print JSON, exit (proves the feed logic against the live service).
- ``--probe-window``: hidden Tk root only -- report which WM
  attributes the live Tk/WM accepted, exit (no window shown).
- ``--toggle``: ask a running orb to show/hide (SIGUSR1 to the pid in
  our own pid file; the pid is validated before signalling, so owner
  processes are never touched). Exit 2 when no orb runs.
- ``--quit``: ask a running orb to exit cleanly (SIGTERM, validated).

No autostart is enabled by anything here; the shipped unit file
``systemd/qubit-voice-orb.service`` is inert until the owner enables it
(``systemctl --user enable --now qubit-voice-orb.service``). Without a
DISPLAY the run command exits 0 (graceful no-op) so the unit never
restart-storms on a headless host. Nothing is printed that carries a
secret.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from . import persist as _persist


def _read_pid() -> Optional[int]:
    try:
        text = _persist.pid_path().read_text(encoding="utf-8").strip()
        pid = int(text.split()[0])
        return pid if pid > 0 else None
    except Exception:
        return None


def _pid_is_our_orb(pid: int) -> bool:
    """True only when the pid is alive AND runs this orb module.

    Guards --toggle/--quit so a recycled pid (owner process) is never
    signalled.
    """
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "replace")
    except Exception:
        return False
    return "voicebridge.orb" in cmdline or "voicebridge/orb" in cmdline


def _write_pid() -> None:
    try:
        path = _persist.pid_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def _clear_pid() -> None:
    try:
        _persist.pid_path().unlink()
    except OSError:
        pass


def cmd_once() -> int:
    from . import client as _client
    from . import state as _state
    try:
        cli = _client.OrbClient.from_config(_client.load_cfg())
        base = cli.base
    except Exception:
        print(json.dumps({"ok": False, "code": "ORB_NO_CONFIG"}))
        return 3
    try:
        status = cli.get_status()
    except _client.OrbError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "state": "idle"}))
        return 3
    try:
        log = cli.get_log(60)
        events = log.get("events", []) if isinstance(log, dict) else []
    except _client.OrbError:
        events = []
    print(json.dumps({"ok": True, "base": base,
                      "state": _state.resolve_orb_state(status, events, time.time())}))
    return 0


def cmd_probe_window() -> int:
    from . import app as _app
    out = dict(_app.probe_window())
    try:
        from . import qt_compat as _qc
        out["qt_importable"] = bool(_qc.qt_importable())
        out["qt_usable_on_x11"] = bool(_qc.qt_usable_on_x11())
    except Exception:
        out["qt_importable"] = False
        out["qt_usable_on_x11"] = False
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


def cmd_toggle() -> int:
    pid = _read_pid()
    if pid is None or not _pid_is_our_orb(pid):
        print("ORB_NOT_RUNNING: no orb pid file (launch: python -m voicebridge.orb)",
              file=sys.stderr)
        return 2
    os.kill(pid, signal.SIGUSR1)
    print(json.dumps({"ok": True, "toggled_pid": pid}))
    return 0


def cmd_quit() -> int:
    pid = _read_pid()
    if pid is None or not _pid_is_our_orb(pid):
        print("ORB_NOT_RUNNING", file=sys.stderr)
        return 2
    os.kill(pid, signal.SIGTERM)
    print(json.dumps({"ok": True, "quit_pid": pid}))
    return 0


def _resolve_backend() -> str:
    """``qt`` or ``tk``. Env ``QUBIT_ORB_BACKEND`` (qt/tk/auto) wins;
    ``auto`` (default) takes Qt when it is expected to survive
    construction, else Tk. Never raises, never opens a window."""
    try:
        from . import qt_app as _qa
        from . import qt_compat as _qc
        want = _qa.backend_wanted()
        if want == "tk":
            return "tk"
        if want == "qt":
            return "qt" if _qc.qt_usable_on_x11() else "tk"
        return "qt" if _qc.qt_usable_on_x11() else "tk"
    except Exception:
        return "tk"


def cmd_run_qt(hidden: bool, quit_after: float,
               tray: bool = True, settings: bool = False) -> int:
    from PySide6.QtWidgets import QApplication
    from . import options as _options
    from . import qt_app as _qa
    app = QApplication([])
    orb = _qa.QtVoiceOrb(poll=True, options=_options.load_options(),
                         enable_tray=bool(tray))

    import signal as _sig
    def _on_usr1(signum: int, frame: Any) -> None:
        orb.request_toggle()

    def _on_term(signum: int, frame: Any) -> None:
        orb.request_quit()

    try:
        _sig.signal(_sig.SIGUSR1, _on_usr1)
        _sig.signal(_sig.SIGTERM, _on_term)
    except Exception:
        pass
    _write_pid()
    if quit_after > 0:
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(int(quit_after * 1000), orb.request_quit)
        except Exception:
            pass
    if not hidden:
        orb.show()
    if settings:
        try:
            orb.open_settings()
        except Exception:
            pass
    t0 = time.monotonic()
    try:
        app.exec()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            secs = time.monotonic() - t0
            fps = (float(orb.frames) / secs) if secs > 0 else 0.0
            payload = {
                "event": "orb.qt_stats",
                "frames": int(orb.frames),
                "secs": round(secs, 2),
                "fps": round(fps, 1),
            }
            try:
                stats = orb.frame_time_stats()
                payload["frame_p50_ms"] = round(float(stats.get("p50", 0.0)), 1)
                payload["frame_p95_ms"] = round(float(stats.get("p95", 0.0)), 1)
                payload["frame_max_ms"] = round(float(stats.get("max", 0.0)), 1)
                payload["frame_count"] = int(stats.get("count", 0))
            except Exception:
                pass
            sys.stderr.write(json.dumps(payload) + "\n")
        except Exception:
            pass
        orb.close()
        _clear_pid()
    return 0


def cmd_run(hidden: bool, quit_after: float,
            tray: bool = True, settings: bool = False) -> int:
    # Headless/GUI-safe: with no display there is no window to show.
    # Exit 0 (not failure) so Restart=on-failure never storms on a
    # headless host; the orb simply stays inactive until a display exists.
    try:
        # Share the one Qubit event journal (same pattern as the desktop
        # helper): without this the orb's transition rows are silently
        # dropped (_EVENT_FILE stays None) and stuck-state reports are
        # undecidable (2026-10-04).
        from ..config import canonical_state_dir
        from ..logging_setup import setup as _setup_logging
        _setup_logging(state_dir=canonical_state_dir())
    except Exception:
        pass
    if not os.environ.get("DISPLAY"):
        print(json.dumps({"ok": False, "code": "ORB_NO_DISPLAY",
                           "state": "idle"}))
        return 0
    backend = _resolve_backend()
    if backend == "qt":
        try:
            return cmd_run_qt(hidden, quit_after, tray=tray, settings=settings)
        except Exception as exc:
            sys.stderr.write(json.dumps({
                "event": "orb.qt_fallback",
                "error": f"{type(exc).__name__}",
            }) + "\n")
    from . import app as _app
    orb = _app.VoiceOrb(poll=True)

    def _on_usr1(signum: int, frame: Any) -> None:
        orb.request_toggle()

    def _on_term(signum: int, frame: Any) -> None:
        orb.request_quit()

    try:
        signal.signal(signal.SIGUSR1, _on_usr1)
        signal.signal(signal.SIGTERM, _on_term)
    except Exception:
        pass
    _write_pid()
    if quit_after > 0:
        try:
            orb.root.after(int(quit_after * 1000), orb.request_quit)
        except Exception:
            pass
    if not hidden:
        orb.show()
    try:
        orb.root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        orb.close()
        _clear_pid()
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m voicebridge.orb",
                                      description="Qubit floating voice orb (X11 overlay, Qt preferred, Tk fallback)")
    parser.add_argument("--hidden", action="store_true",
                        help="start withdrawn (proof mode)")
    parser.add_argument("--quit-after", type=float, default=0,
                        help="exit cleanly after N seconds (proof mode)")
    parser.add_argument("--once", action="store_true",
                        help="one status poll + state, no window")
    parser.add_argument("--probe-window", action="store_true",
                        help="hidden Tk root: report accepted WM attributes")
    parser.add_argument("--toggle", action="store_true",
                        help="ask a running orb to show/hide")
    parser.add_argument("--quit", action="store_true",
                        help="ask a running orb to exit cleanly")
    parser.add_argument("--backend", choices=("auto", "qt", "tk"),
                        default=None,
                        help="renderer backend (default: $QUBIT_ORB_BACKEND or auto->qt)")
    parser.add_argument("--no-tray", action="store_true",
                        help="run without the system-tray icon")
    parser.add_argument("--settings", action="store_true",
                        help="open the settings panel on start")
    args = parser.parse_args(argv)
    if args.once:
        return cmd_once()
    if args.probe_window:
        return cmd_probe_window()
    if args.toggle:
        return cmd_toggle()
    if args.quit:
        return cmd_quit()
    if args.backend:
        os.environ["QUBIT_ORB_BACKEND"] = args.backend
    return cmd_run(args.hidden, args.quit_after,
                   tray=not args.no_tray, settings=args.settings)


if __name__ == "__main__":
    raise SystemExit(main())
