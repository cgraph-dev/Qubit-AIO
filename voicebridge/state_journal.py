"""Packet 8P: shared append-only hash-chain journal.

A small, clearly owned, reusable component for the verified-skill and
controlled-evaluation stores. Each record is a ``{body, chain}`` line where
``chain = sha256(prev_chain + body)``. Appending is fsync'd and atomic per
line; ``verify`` replays the chain and reports a torn tail or a committed
chain mismatch as a typed failure. Torn-tail recovery ignores a partial final
line (a crash mid-append), because the committed chain must still reproduce.

Deliberately does NOT couple to any store's operations (unlike the 8M memory
journal): it records an opaque redacted ``body`` per mutation so the owning
store can replay/re-verify its own mutation history.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .logging_setup import log_event
from .redaction import redact_obj


class JournalError(Exception):
    """Typed journal failure."""


def _chain(prev: Optional[str], body: str) -> str:
    return hashlib.sha256(((prev or "") + body).encode("utf-8")).hexdigest()


class HashChainJournal:
    """Append-only, fsync'd, chained journal with a verify(replay) pass."""

    def __init__(self, path: Path | str, *,
                 event_prefix: str = "state",
                 clock: Any = time.time) -> None:
        self._path = Path(path)
        self._prefix = event_prefix
        self._clock = clock

    @property
    def path(self) -> Path:
        return self._path

    def _last_chain(self) -> Optional[str]:
        if not self._path.exists():
            return None
        last: Optional[str] = None
        try:
            with open(self._path, "r") as fh:
                for ln in fh:
                    if not ln.strip():
                        continue
                    try:
                        rec = json.loads(ln)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(rec, dict) and rec.get("chain"):
                        last = str(rec["chain"])
        except OSError:
            return last
        return last

    def append(self, action: str, payload: Dict[str, Any]) -> None:
        """Append one fsync'd, chain-linked record. Torn tail is impossible on
        append (a single write); the previous committed line's chain links."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass
        prev = self._last_chain()
        line = {"ts": self._clock(), "action": action,
                "payload": redact_obj(payload), "prev": prev}
        body = json.dumps(line, sort_keys=True)
        record = {"body": body, "chain": _chain(prev, body)}
        try:
            with open(self._path, "a") as fh:
                fh.write(json.dumps(record) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            raise JournalError(f"journal append failed: {exc}") from exc
        log_event(f"{self._prefix}.journal", action=action,
                  id=payload.get("id", ""))

    def verify(self) -> Dict[str, Any]:
        """Replay the chain. Returns a typed report; never returns secrets.

        Only a malformed FINAL non-empty line is a recoverable torn tail (a
        crash mid-append). A malformed earlier line is committed-history
        tampering and fails closed: silently truncating a committed chain would
        let an attacker delete/alter records and still see ``state: OK``.
        """
        if not self._path.exists():
            return {"state": "OK", "exists": False, "count": 0,
                    "torn_tail": False, "error": ""}
        count = 0
        prev: Optional[str] = None
        torn = False
        error = ""
        try:
            with open(self._path, "r") as fh:
                lines = [ln for ln in fh if ln.strip()]
        except OSError as exc:
            return {"state": "failed", "exists": True, "count": 0,
                    "torn_tail": False, "error": str(exc)}
        last = len(lines) - 1
        for index, ln in enumerate(lines):
            try:
                rec = json.loads(ln)
            except (ValueError, TypeError):
                rec = None
            if not isinstance(rec, dict):
                if index == last:
                    torn = True
                    break
                error = f"unparseable record {index + 1} (not a torn tail)"
                break
            body = rec.get("body", "")
            chain = rec.get("chain", "")
            if not isinstance(body, str) or not isinstance(chain, str):
                if index == last:
                    torn = True
                    break
                error = f"malformed record {index + 1} (not a torn tail)"
                break
            if _chain(prev, body) != chain:
                error = f"chain mismatch at record {count + 1}"
                break
            prev = chain
            count += 1
        if error:
            return {"state": "failed", "exists": True, "count": count,
                    "torn_tail": torn, "error": error}
        return {"state": "OK", "exists": True, "count": count,
                "torn_tail": torn, "error": ""}

    def replay_actions(self) -> list:
        """Return the ordered, redacted (body) actions for deterministic replay."""
        if not self._path.exists():
            return []
        out = []
        try:
            with open(self._path, "r") as fh:
                for ln in fh:
                    if not ln.strip():
                        continue
                    try:
                        rec = json.loads(ln)
                    except (ValueError, TypeError):
                        continue
                    try:
                        body = json.loads(rec["body"])
                    except (ValueError, TypeError, KeyError):
                        continue
                    out.append({"action": body.get("action"),
                                "payload": body.get("payload", {})})
        except OSError:
            return out
        return out
