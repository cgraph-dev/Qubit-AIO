"""Qubit supervisor for the adapted Hermes runtime worker.

Qubit starts exactly one worker subprocess, speaks turns to it over stdio
JSONL, and kills/reaps it on cancel, timeout, or emergency stop. The worker
inherits a MINIMAL environment (no full-environment copy, no secrets): only
PATH/PYTHONPATH/HOME-plus-locale plus QUBIT_RUNTIME_PROVIDER reach it.

Every turn is gated: e-stop engaged -> RuntimeDenied without spawning
anything. Every outcome (reply, typed degradation, cancel, timeout) emits a
redacted Qubit audit receipt via log_event; the receipt identifies the real
runtime ("qubit-hermes-adapted"), the session, and the action path.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .worker import HERMES_PIN, RUNTIME_NAME

DEFAULT_TIMEOUT_S = 120.0
MAX_REPLY_BYTES = 65536
MAX_LINE_BYTES = 262144


class RuntimeError(RuntimeError):
    """Typed runtime failure (never a raw subprocess traceback)."""


class RuntimeDenied(RuntimeError):
    """Turn refused before execution (e-stop or stopped runtime)."""


def _minimal_env(provider: str, repo_root: Path) -> Dict[str, str]:
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "PYTHONHASHSEED",
            "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
            "XDG_CACHE_HOME")
    env = {k: v for k, v in os.environ.items() if k in keep and v}
    env["PYTHONPATH"] = str(repo_root) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["QUBIT_RUNTIME_PROVIDER"] = provider
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _audit(event: str, **fields: Any) -> None:
    try:
        from voicebridge.logging_setup import log_event
        from voicebridge.redaction import redact_text

        safe = {k: (redact_text(v) if isinstance(v, str) else v)
                for k, v in fields.items()}
        log_event(f"runtime.{event}", runtime=RUNTIME_NAME, pin=HERMES_PIN,
                  **safe)
    except Exception:
        pass


class HermesRuntime:
    """One supervised adapted-Hermes worker. Not thread-safe for concurrent
    turns (one voice session speaks at a time); concurrent callers serialize
    on an internal lock and each turn carries its session id."""

    def __init__(self, *, provider: str = "fixture",
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 repo_root: Optional[Path] = None,
                 estop_engaged: Optional[Callable[[], bool]] = None) -> None:
        self.provider = provider
        self.timeout_s = timeout_s
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]
        self.estop_engaged = estop_engaged or (lambda: False)
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self._pending: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.pid: Optional[int] = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> int:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return self.pid or -1
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "voicebridge.runtime.worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                cwd=str(self.repo_root),
                env=_minimal_env(self.provider, self.repo_root))
            self.pid = self._proc.pid
            self._pending = queue.Queue()
            self._reader = threading.Thread(target=self._drain, daemon=True)
            self._reader.start()
            _audit("start", pid=self.pid, provider=self.provider)
            return self.pid or -1

    def _drain(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            if len(line) > MAX_LINE_BYTES:
                continue
            try:
                self._pending.put(json.loads(line))
            except Exception:
                continue

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _reap(self, proc: subprocess.Popen) -> None:
        try:
            if proc.poll() is None and proc.stdin is not None:
                try:
                    proc.stdin.write('{"op": "shutdown"}\n')
                    proc.stdin.flush()
                except Exception:
                    pass
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
            proc.wait(timeout=10)
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            proc, self._proc = self._proc, None
            pid, self.pid = self.pid, None
        if proc is None:
            return {"state": "STOPPED", "detail": "was not running"}
        self._reap(proc)
        _audit("stop", pid=pid)
        return {"state": "STOPPED", "pid": pid}

    def cancel(self) -> Dict[str, Any]:
        """Barge-in/cancel: kill the worker now, reap it, report typed state."""
        with self._lock:
            proc, self._proc = self._proc, None
            pid, self.pid = self.pid, None
        if proc is None or proc.poll() is not None:
            if proc is not None:
                self._reap(proc)
            return {"state": "CANCELLED", "detail": "nothing was running"}
        proc.kill()
        proc.wait(timeout=10)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        while not self._pending.empty():
            try:
                self._pending.get_nowait()
            except queue.Empty:
                break
        _audit("cancel", pid=pid)
        return {"state": "CANCELLED", "pid": pid}

    # -- turns ---------------------------------------------------------------
    def _send(self, payload: Dict[str, Any],
              timeout_s: Optional[float] = None) -> Dict[str, Any]:
        if self.estop_engaged():
            raise RuntimeDenied("EMERGENCY_STOP: turn refused, runtime untouched")
        self.start()
        assert self._proc is not None and self._proc.stdin is not None
        raw = json.dumps(payload)
        if len(raw) > MAX_LINE_BYTES:
            raise RuntimeError("request exceeds bounded size")
        with self._lock:
            try:
                self._proc.stdin.write(raw + "\n")
                self._proc.stdin.flush()
            except Exception as exc:
                raise RuntimeError(f"runtime write failed: {exc}") from exc
        try:
            reply = self._pending.get(timeout=timeout_s or self.timeout_s)
        except queue.Empty:
            self.cancel()
            raise RuntimeError("runtime turn timed out; worker cancelled")
        if not isinstance(reply, dict):
            raise RuntimeError("runtime returned malformed reply")
        return reply

    def ping(self) -> Dict[str, Any]:
        reply = self._send({"op": "ping"}, timeout_s=15)
        if not reply.get("ok"):
            raise RuntimeError(f"runtime ping failed: {reply}")
        return reply

    def turn(self, text: str, *, session: str = "voice",
             timeout_s: Optional[float] = None) -> Dict[str, Any]:
        """One user request into the runtime. Returns the worker reply plus
        a Qubit receipt identifying runtime/session/path. Degraded worker
        replies pass through typed (ok=false); transport failures raise."""
        started = time.monotonic()
        reply = self._send({"op": "turn", "text": text, "session": session},
                           timeout_s=timeout_s)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        receipt = {"runtime": RUNTIME_NAME, "pin": HERMES_PIN,
                   "session": reply.get("session", session),
                   "path": "voicebridge.runtime.turn",
                   "elapsed_ms": elapsed_ms,
                   "provider": reply.get("provider", self.provider)}
        _audit("turn", session=receipt["session"],
               elapsed_ms=elapsed_ms, ok=bool(reply.get("ok")),
               code=str(reply.get("code", "")))
        return {**reply, "receipt": receipt}

    def status(self) -> Dict[str, Any]:
        """Real probe (not a mock): ping the live worker if running."""
        if not self.alive:
            return {"state": "DOWN", "runtime": RUNTIME_NAME,
                    "pin": HERMES_PIN, "pid": None}
        try:
            pong = self.ping()
            return {"state": "LIVE", "runtime": pong.get("runtime"),
                    "pin": pong.get("pin"), "pid": self.pid,
                    "provider": self.provider}
        except Exception as exc:
            return {"state": "DEGRADED", "runtime": RUNTIME_NAME,
                    "pin": HERMES_PIN, "pid": self.pid,
                    "detail": str(exc)[:200]}
