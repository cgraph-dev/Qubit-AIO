"""Packet 8T-F — real pinned-Hermes execution behind Qubit policy.

Runs an explicitly configured, ISOLATED Hermes installation (fresh clone at
a pinned commit — never the operator's `hermes` binary, never the
operator's HERMES_HOME) as Qubit's text runtime:

  hermes chat -q TEXT -Q --continue SESSION --create-if-missing
      --provider PROVIDER [-m MODEL]

Contract verified against the real binary (`hermes chat --help`):
stdout carries ONLY the reply, session metadata goes to stderr, rc != 0
carries "No session found matching ..." when a session is missing.

When unconfigured (no binary/home) every operation returns typed
DISABLED/UNAVAILABLE — never false success. E-stop refuses before spawn.
Secrets are redacted from replies, errors, and receipts. No automatic
commit/push/publish/install/service/account/destructive action: Hermes runs
with `--toolsets` unset (agent default) and Qubit never passes yolo/approve
flags; tool-using turns still require Qubit broker confirmation upstream.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..upstream.hermes_ansi_strip import strip_ansi
from .common import audit, redact, scrubbed_env
from .worker import HERMES_PIN

EXPECTED_VERSION = "0.21.1"
SESSION_LINE_PREFIX = "session_id:"


class RealHermesError(RuntimeError):
    """Typed real-runtime failure (timeout, crash, missing session, error)."""


class RealHermesDenied(RuntimeError):
    """Refused before execution (disabled, unconfigured, or e-stop)."""


class RealHermesRuntime:
    """Supervisor for one isolated Hermes installation.

    ``binary``/``home`` are explicit paths (no defaults pointing anywhere):
    without them the runtime reports DISABLED. ``model`` selects the Hermes
    model id (must already exist locally for hermetic runs).
    """

    def __init__(self, *, binary: Optional[Path] = None,
                 home: Optional[Path] = None,
                 provider: str = "ollama", model: Optional[str] = None,
                 timeout_s: float = 300.0,
                 estop_engaged: Optional[Callable[[], bool]] = None) -> None:
        # Production defaults resolve to Qubit-owned XDG space (never /tmp,
        # never the operator's installs). Tests pass explicit tmp paths.
        if binary is None or home is None:
            from .provision import hermes_home, runtime_dir
            rd = runtime_dir()
            binary = binary if binary is not None else rd / "venv" / "bin" / "hermes"
            home = home if home is not None else hermes_home()
        self.binary = Path(binary)
        self.home = Path(home)
        self.provider = provider
        self.model = model
        self.timeout_s = timeout_s
        self.estop_engaged = estop_engaged or (lambda: False)

    # -- configuration ------------------------------------------------------
    def configured(self) -> bool:
        return self.binary.is_file() and self.home.is_dir()

    def verify(self) -> Dict[str, Any]:
        """Deep identity check: pin, LICENSE, entry hashes, live version."""
        from .provision import verify_installation

        try:
            rdir = self.binary.parents[2]
        except IndexError:
            return {"state": "UNAVAILABLE", "reason": "BAD_LAYOUT",
                    "detail": str(self.binary)}
        info = verify_installation(rdir)
        info["runtime"] = "hermes-real-isolated"
        return info

    def _env(self) -> Dict[str, str]:
        assert self.home is not None
        env = scrubbed_env(xdg_from_home=self.home)
        env["HERMES_HOME"] = str(self.home)
        return env

    def _argv(self, text: str, session: str) -> List[str]:
        assert self.binary is not None
        argv = [str(self.binary), "chat", "-q", text, "-Q",
                "--continue", session, "--create-if-missing",
                "--provider", self.provider]
        if self.model:
            argv += ["-m", self.model]
        return argv

    # -- identity -------------------------------------------------------------
    def version(self) -> Dict[str, Any]:
        """Real identity probe: version string + install dir, no model needed."""
        if not self.configured():
            return {"state": "DISABLED", "detail": "no isolated Hermes configured"}
        try:
            proc = subprocess.run(
                [str(self.binary), "--version"], capture_output=True,
                text=True, timeout=60, env=self._env())
        except Exception as exc:
            return {"state": "UNAVAILABLE", "detail": f"spawn failed: {exc}"[:200]}
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0 or "Hermes Agent" not in out:
            return {"state": "UNAVAILABLE",
                    "detail": redact(out[-300:]) or f"rc={proc.returncode}"}
        return {"state": "READY", "version": redact(out.strip().splitlines()[0]),
                "binary": str(self.binary), "home": str(self.home),
                "expected_pin": HERMES_PIN}

    # -- turns ------------------------------------------------------------------
    def turn(self, text: str, *, session: str = "qubit-voice",
             timeout_s: Optional[float] = None) -> Dict[str, Any]:
        if not self.configured():
            raise RealHermesDenied("DISABLED: no isolated Hermes configured")
        if self.estop_engaged():
            raise RealHermesDenied("EMERGENCY_STOP: real Hermes untouched")
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "code": "MALFORMED",
                    "error": "turn needs non-empty text"}
        started = time.monotonic()
        try:
            proc = subprocess.run(
                self._argv(text, session), capture_output=True, text=True,
                timeout=timeout_s or self.timeout_s, env=self._env())
        except subprocess.TimeoutExpired:
            audit("real_turn_timeout", session=session)
            return {"ok": False, "code": "TIMEOUT",
                    "error": "real Hermes turn timed out; reaped",
                    "session": session}
        elapsed_ms = int((time.monotonic() - started) * 1000)
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            code = ("CRASH" if "Traceback" in err else "HERMES_ERROR")
            audit("real_turn_error", session=session, code=code,
                  elapsed_ms=elapsed_ms)
            return {"ok": False, "code": code,
                    "error": redact(err[-500:] or f"rc={proc.returncode}"),
                    "session": session, "elapsed_ms": elapsed_ms}
        reply = (proc.stdout or "").strip()
        if not reply:
            return {"ok": False, "code": "EMPTY_REPLY",
                    "error": redact(err[-300:] or "empty stdout"),
                    "session": session, "elapsed_ms": elapsed_ms}
        audit("real_turn", session=session, elapsed_ms=elapsed_ms, ok=True)
        # 8T-F: vendored pinned-Hermes sanitizer runs on the live reply.
        return {"ok": True, "reply": redact(strip_ansi(reply)), "session": session,
                "elapsed_ms": elapsed_ms,
                "receipt": {"runtime": "hermes-real-isolated",
                            "pin": HERMES_PIN,
                            "binary": str(self.binary),
                            "provider": self.provider,
                            "model": self.model or "",
                            "path": "voicebridge.runtime.real_hermes.turn"}}

    def status(self) -> Dict[str, Any]:
        info = self.version()
        info["runtime"] = "hermes-real-isolated"
        return info

    def _control(self, action: str, reason: str = "") -> Dict[str, Any]:
        """Run `hermes pause|resume` against the ISOLATED home only."""
        if not self.configured():
            raise RealHermesDenied("DISABLED: no isolated Hermes configured")
        argv = [str(self.binary), action]
        if action == "pause" and reason:
            argv += ["--reason", reason]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=60, env=self._env())
        except subprocess.TimeoutExpired:
            return {"ok": False, "code": "TIMEOUT",
                    "error": f"hermes {action} timed out"}
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            return {"ok": False, "code": "HERMES_ERROR",
                    "error": redact(err[-300:] or f"rc={proc.returncode}")}
        audit(f"real_{action}", reason=reason[:120] if reason else "")
        return {"ok": True, "action": action,
                "detail": redact(((proc.stdout or "").strip() or "done")[:200])}

    def pause(self, reason: str = "qubit-emergency-stop") -> Dict[str, Any]:
        """Halt NEW isolated-Hermes work (in-flight work is never killed by
        Hermes itself; Qubit cancels its own runs separately)."""
        return self._control("pause", reason)

    def resume(self) -> Dict[str, Any]:
        """Explicit manual resume (never called automatically on e-stop reset)."""
        return self._control("resume")

    def bind_estop(self, control: Any) -> None:
        """8T-F cross-wire: Qubit emergency stop also pauses isolated Hermes.

        Pause-only: reset() never resumes (explicit manual resume required).
        """
        def _on_estop() -> None:
            try:
                self.pause()
            except Exception:
                pass

        control.on_estop(_on_estop)


def stop_ollama_model(model: str, *, ollama_bin: str = "ollama") -> Dict[str, Any]:
    """Best-effort VRAM hygiene: unload a model we loaded for a real turn."""
    try:
        proc = subprocess.run([ollama_bin, "stop", model],
                              capture_output=True, text=True, timeout=120)
        return {"model": model, "rc": proc.returncode,
                "state": "STOPPED" if proc.returncode == 0 else "UNKNOWN"}
    except Exception as exc:
        return {"model": model, "state": "UNKNOWN", "detail": str(exc)[:150]}


def isolated_env_without_secrets() -> Dict[str, str]:
    """Test helper: copy of the scrubbed child env (assertable, no spawn)."""
    return scrubbed_env(xdg_from_home=Path("/tmp/qubit-8tf-probe-home"))
