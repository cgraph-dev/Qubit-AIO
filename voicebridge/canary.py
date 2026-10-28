"""Opt-in Q5 canary (Packet 5A companion, 4D-handoff requirement).

Reversible feature flag: intent.canary.q5_enabled (default false).
When OFF, the canary is inert: zero logging, zero behavior change, the
production alias is never referenced. When ON, a bounded runner drives the
SEPARATE qubit-cand-q5 alias through warm/cold latency, errors, planner
decisions, safety denials and audio probes and appends one JSONL record per
turn. Production alias qubit-brain is NEVER loaded, unloaded or modified;
no promotion logic exists anywhere in this module.

Rollback state is recorded per run (flag value, aliases seen, prod digest
observed, any drift), and rollback = flip the flag off + delete the log.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

CANARY_ALIAS = "qubit-cand-q5"
PRODUCTION_ALIAS = "qubit-brain"

REQUIRED_FIELDS = ("alias", "timestamp", "warm_cold", "latency_ms",
                   "errors", "planner_decision", "safety_denied",
                   "audio_failure", "rollback_state")


@dataclass
class CanaryTurn:
    alias: str
    warm_cold: str
    latency_ms: float
    errors: List[str] = field(default_factory=list)
    planner_decision: Optional[str] = None
    safety_denied: bool = False
    audio_failure: Optional[str] = None


class CanaryLogger:
    """Append-only JSONL canary log under the private state root."""

    def __init__(self, state_dir: Path, flag: bool = False) -> None:
        self.enabled = flag
        self._dir = Path(state_dir) / "canary"
        self._path = self._dir / "q5_canary.jsonl"
        if self.enabled:
            self._dir.mkdir(parents=True, exist_ok=True)
            try:
                import os
                os.chmod(self._dir, 0o700)
            except OSError:
                pass

    def log_turn(self, turn: CanaryTurn,
                 rollback_state: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        rec = {k: getattr(turn, k, None) for k in REQUIRED_FIELDS}
        rec["timestamp"] = round(time.time(), 3)
        rec["rollback_state"] = rollback_state
        try:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
        except OSError:
            pass

    def counts(self) -> Dict[str, int]:
        if not self._path.exists():
            return {"turns": 0}
        n = 0
        with self._path.open(encoding="utf-8") as fh:
            n = sum(1 for _ in fh)
        return {"turns": n}

    def rollback(self) -> None:
        """Reversible flag flip: stop logging and close the log."""
        self.enabled = False


def canary_enabled(cfg: Any) -> bool:
    try:
        return bool(cfg.get("intent", "canary", "q5_enabled", default=False))
    except Exception:
        return False


def safe_prod_digest(url: str) -> Optional[str]:
    """Observe the production alias digest WITHOUT loading or unloading it."""
    try:
        import urllib.request
        req = urllib.request.Request(f"{url.rstrip('/')}/api/show",
                                     data=json.dumps(
                                         {"model": PRODUCTION_ALIAS}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.headers.get("X-Ollama-Digest")
    except Exception:
        return None