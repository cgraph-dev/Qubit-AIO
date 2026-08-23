"""Hermes backend: route tasks to the local Hermes agent (`hermes chat -q`).

Hermes is a *second backend*, never a second memory owner — sessions resume
under a fixed name so continuity lives in Hermes' local session store on this
PC. The OpenClaw gateway remains the primary executor; Hermes is opt-in.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Dict, Optional

from ..logging_setup import get_logger, log_event

log = get_logger()


class HermesBackend:
    name = "hermes"

    def __init__(self, cmd: str = "hermes", resume_session: str = "voice",
                 timeout_s: float = 600.0, model: Optional[str] = None) -> None:
        self.cmd = cmd
        self.resume_session = resume_session
        self.timeout_s = timeout_s
        self.model = model
        self._available: Optional[bool] = None

    def available(self) -> bool:
        if self._available is None:
            self._available = shutil.which(self.cmd) is not None
        return self._available

    def run_task(self, text: str, timeout_s: Optional[float] = None) -> str:
        """Run one task through Hermes and return its final text reply."""
        if not self.available():
            raise RuntimeError("hermes binary not found in PATH")
        argv = [self.cmd, "chat", "-q", text, "-Q"]
        if self.model:
            argv += ["-m", self.model]
        if self.resume_session:
            argv += ["--continue", self.resume_session]
        log_event("hermes.task", text=text[:300], resume=self.resume_session)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout_s or self.timeout_s)
        except subprocess.TimeoutExpired:
            log_event("hermes.task.timeout", text=text[:200])
            raise TimeoutError(f"hermes task timed out after {(timeout_s or self.timeout_s):.0f}s")
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        # Hermes only checks --continue by name/ID and, when the session is gone
        # (e.g. it expired or was pruned), it prints the notice to STDOUT and
        # exits 1 — which made EVERY routed task fail with "hermes exited 1"
        # even though Hermes itself is fine. Detect that case and re-run with a
        # fresh session so the task completes instead of dying silently.
        if proc.returncode != 0 and self.resume_session \
                and ("no session found" in (out + err).lower()
                     or f"matching '{self.resume_session}'" in (out + err).lower()):
            log_event("hermes.task.session_missing", session=self.resume_session,
                      retry_fresh=True, text=text[:120])
            argv_fresh = [self.cmd, "chat", "-q", text, "-Q"]
            if self.model:
                argv_fresh += ["-m", self.model]
            try:
                proc = subprocess.run(argv_fresh, capture_output=True, text=True,
                                      timeout=timeout_s or self.timeout_s)
            except subprocess.TimeoutExpired:
                log_event("hermes.task.timeout", text=text[:200])
                raise TimeoutError(f"hermes task timed out after {(timeout_s or self.timeout_s):.0f}s")
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            err = err[-500:]
            log_event("hermes.task.error", code=proc.returncode, error=err[:200])
            raise RuntimeError(f"hermes exited {proc.returncode}: {err}")
        if not out:
            out = "(hermes returned no text)"
        log_event("hermes.task.done", reply=out[:300])
        return out
