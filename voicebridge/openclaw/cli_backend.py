"""Fallback OpenClaw backend: the `openclaw agent` CLI subprocess.

Used automatically when the gateway WebSocket route is unavailable (gateway
down, token missing). It shells out to the installed CLI with the same
session/auth the user already has, so agent behavior is identical:

    openclaw agent --agent main --session-key <key> --message-file <f> --json --timeout N

Limitations vs the WS route: no live event stream, no abort-by-runId, and a
subprocess per task (cold bootstrap each time). Prefer the WS route.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..logging_setup import log_event


@dataclass
class CliRunResult:
    status: str
    reply: str = ""
    elapsed_s: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)


class CliBackend:
    """Minimal synchronous backend mirroring the GatewaySync surface."""

    name = "cli"

    def __init__(self, agent: str = "main", timeout_s: int = 900) -> None:
        self.agent = agent
        self.timeout_s = timeout_s
        self.connected = bool(shutil.which("openclaw"))

    def connect(self, timeout: float = 10.0) -> bool:
        self.connected = bool(shutil.which("openclaw"))
        return self.connected

    def disconnect(self, timeout: float = 5.0) -> None:
        pass

    def run_task(self, session_key: str, message: str,
                 model: Optional[str] = None) -> CliRunResult:
        if not self.connected:
            return CliRunResult(status="failed", reply="openclaw CLI not found")
        if shutil.which("openclaw") is None:
            return CliRunResult(status="failed", reply="openclaw CLI not found")
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
            tf.write(message)
            msg_path = tf.name
        cmd = ["openclaw", "agent", "--agent", self.agent,
               "--session-key", session_key, "--message-file", msg_path,
               "--json", "--timeout", str(self.timeout_s)]
        if model:
            cmd += ["--model", model]
        t0 = time.time()
        log_event("openclaw.cli.run", agent=self.agent, session_key=session_key,
                  model=model)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s + 30)
        except subprocess.TimeoutExpired:
            log_event("openclaw.cli.timeout", session_key=session_key, timeout_s=self.timeout_s)
            return CliRunResult(status="timeout", reply="agent timed out", elapsed_s=self.timeout_s)
        finally:
            Path(msg_path).unlink(missing_ok=True)
        elapsed = time.time() - t0
        log_event("openclaw.cli.done", session_key=session_key, exit=proc.returncode,
                  elapsed_s=round(elapsed, 2))
        try:
            data = json.loads(proc.stdout or "{}")
            text = data.get("reply") or data.get("text") or data.get("response") or ""
        except Exception:
            text = (proc.stdout or proc.stderr or "")[-4000:]
        if proc.returncode != 0:
            return CliRunResult(status="failed", reply=str(text or proc.stderr)[-4000:],
                                elapsed_s=elapsed, raw={"exit": proc.returncode})
        return CliRunResult(status="ok", reply=text, elapsed_s=elapsed,
                            raw=data)
