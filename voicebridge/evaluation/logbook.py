"""OC-51 (no-hardware parts) — Logbook work-journal.

The hardware-free half of the upstream Logbook plugin: an append-only work
journal with typed entries, evidence references, and a windowed summary. The
periodic screen-capture / vision-summary half requires an actuator and a
vision provider, so :func:`capture` returns a *typed* ``UNAVAILABLE`` instead
of fabricating a snapshot. Entries can be added manually or by the existing
read-only monitor; summaries are derived from stored entries only.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = "qubit-logbook/1"
CAPTURE_UNAVAILABLE = "LOGBOOK_CAPTURE_UNAVAILABLE"
_BAD_RECORD = "LOGBOOK_BAD_RECORD"
MAX_SUMMARY = 400


class LogbookError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _redact(text: str) -> str:
    from ..redaction import redact_text

    return redact_text(str(text))


class LogbookEntry:
    """One work-journal entry (summary is redacted on the way in)."""

    __slots__ = ("kind", "summary", "evidence_refs", "source", "actor", "ts",
                 "entry_id")

    def __init__(self, *, kind: str, summary: str, evidence_refs: Any = (),
                 source: str = "user", actor: str = "user",
                 ts: Optional[float] = None, entry_id: str = "") -> None:
        self.kind = str(kind or "note")
        self.summary = _redact(str(summary or ""))[:MAX_SUMMARY]
        refs = evidence_refs if isinstance(evidence_refs, (list, tuple)) else []
        self.evidence_refs = [str(r) for r in refs][:32]
        self.source = str(source or "user")
        self.actor = str(actor or "user")
        self.ts = float(ts if ts is not None else time.time())
        self.entry_id = entry_id or _digest(self._body())

    def _body(self) -> Dict[str, Any]:
        return {"kind": self.kind, "summary": self.summary,
                "evidence_refs": self.evidence_refs, "source": self.source,
                "actor": self.actor, "ts": self.ts}

    def as_dict(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "entry_id": self.entry_id, **self._body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "LogbookEntry":
        return cls(**kwargs)


class Logbook:
    """Append-only JSONL work journal."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, entry: LogbookEntry) -> Dict[str, Any]:
        if not isinstance(entry, LogbookEntry):
            raise LogbookError(_BAD_RECORD, "expected a LogbookEntry")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.as_dict(), sort_keys=True) + "\n")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return entry.as_dict()

    def entries(self, *, limit: int = 500,
                window_s: Optional[float] = None) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out: List[Dict[str, Any]] = []
        cutoff = (time.time() - float(window_s)) if window_s else None
        for line in self.path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if cutoff is not None and float(data.get("ts", 0)) < cutoff:
                continue
            out.append(data)
        return out[-int(limit):]

    def summary(self, *, window_s: Optional[float] = None) -> Dict[str, Any]:
        items = self.entries(limit=100_000, window_s=window_s)
        by_kind: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        for it in items:
            by_kind[it.get("kind", "note")] = by_kind.get(it.get("kind", "note"), 0) + 1
            by_source[it.get("source", "user")] = \
                by_source.get(it.get("source", "user"), 0) + 1
        return {"schema": SCHEMA, "entries": len(items),
                "by_kind": by_kind, "by_source": by_source,
                "latest": items[-1]["entry_id"] if items else ""}


def capture() -> Dict[str, Any]:
    """Typed refusal: periodic screen capture needs hardware + vision."""
    return {
        "state": "REFUSED",
        "code": CAPTURE_UNAVAILABLE,
        "explanation": "Qubit Agent cannot do this: the Logbook screen capture "
                       "and vision summary need a real display actuator and a "
                       "vision provider; manual and monitor-sourced journal "
                       "entries are available instead.",
        "owner": "qubit-policy",
        "rollback": "nothing captured; no screen access attempted",
    }


def journal_from_items(items: Iterable[Any], *,
                       source: str = "monitor") -> List[LogbookEntry]:
    """Build journal entries from monitor items (read-only adapter)."""
    out: List[LogbookEntry] = []
    for it in items:
        if isinstance(it, dict):
            summary = str(it.get("subject") or it.get("summary") or "")
            refs = it.get("safe_url") or it.get("evidence_refs") or []
            kind = str(it.get("platform") or "item")
        else:
            summary = str(getattr(it, "subject", "") or "")
            refs = [getattr(it, "safe_url", "")] if getattr(it, "safe_url", "") else []
            kind = str(getattr(it, "platform", "item"))
        if isinstance(refs, str):
            refs = [refs] if refs else []
        out.append(LogbookEntry.create(kind=kind, summary=summary,
                                       evidence_refs=refs, source=source))
    return out
