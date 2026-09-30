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


MAX_CAPTURE_BYTES = 4 * 1024 * 1024


def capture_to(book: "Logbook", *, grabber: Any = None,
               evidence_dir: Any = None, vision: Any = None,
               source: str = "screen-journal", actor: str = "user",
               max_bytes: int = MAX_CAPTURE_BYTES,
               confirmed: bool = False) -> Dict[str, Any]:
    """The complete screen-snapshot capture path (OC-16 / OC-51).

    ``grabber`` is the *external boundary*: a zero-argument callable returning
    raw PNG bytes (or a mapping with ``png``/``data``/``image`` bytes). When it
    is absent (no display actuator available) the call degrades to the typed
    :func:`capture` refusal — a snapshot is never fabricated. When supplied,
    the bytes are size-capped, persisted 0600 under ``evidence_dir``, and an
    append-only ``screen.snapshot`` journal entry is written. ``vision`` is an
    optional callable ``(bytes) -> summary`` (mocked in tests).
    """
    if grabber is None:
        return capture()
    if not confirmed:
        return {"state": "denied", "code": "LOGBOOK_CONFIRM_REQUIRED",
                "detail": "screen capture requires explicit confirmation",
                "owner": "qubit-policy",
                "rollback": "nothing captured; no screen access attempted"}
    try:
        raw = grabber()
    except Exception as exc:  # boundary failure is typed, never a traceback
        return {"state": "FAILED", "code": "LOGBOOK_CAPTURE_FAILED",
                "detail": _redact(str(exc))[:200],
                "owner": "qubit-policy",
                "rollback": "nothing captured; no screen access attempted"}
    if isinstance(raw, dict):
        raw = raw.get("png") or raw.get("data") or raw.get("image") or b""
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        return {"state": "FAILED", "code": "LOGBOOK_BAD_CAPTURE",
                "detail": "grabber returned no image bytes",
                "owner": "qubit-policy",
                "rollback": "nothing captured; no screen access attempted"}
    payload = bytes(raw)
    try:
        cap = max(1024, min(int(max_bytes), MAX_CAPTURE_BYTES))
    except (TypeError, ValueError):
        cap = MAX_CAPTURE_BYTES
    if len(payload) > cap:
        return {"state": "FAILED", "code": "LOGBOOK_CAPTURE_TOO_LARGE",
                "detail": f"capture {len(payload)} bytes exceeds cap {cap}",
                "owner": "qubit-policy",
                "rollback": "nothing captured; no screen access attempted"}
    root = Path(evidence_dir) if evidence_dir else book.path.parent / "evidence"
    try:
        root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        img = root / f"snap-{stamp}-{int(time.time() * 1000) % 100000}.png"
        img.write_bytes(payload)
        try:
            img.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        return {"state": "FAILED", "code": "LOGBOOK_EVIDENCE_FAILED",
                "detail": _redact(str(exc))[:200],
                "owner": "qubit-policy",
                "rollback": "nothing journaled; no screen access attempted"}
    summary = "screen snapshot"
    if callable(vision):
        try:
            summary = _redact(str(vision(payload)))[:MAX_SUMMARY]
        except Exception as exc:
            summary = f"screen snapshot (vision failed: {_redact(str(exc))[:80]})"
    entry = LogbookEntry.create(kind="screen.snapshot", summary=summary,
                                evidence_refs=[str(img)], source=source,
                                actor=actor)
    book.append(entry)
    return {"state": "OK", "schema": SCHEMA, "entry_id": entry.entry_id,
            "evidence": str(img), "bytes": len(payload), "summary": summary,
            "source": source}


MAX_CAPTURE_SERIES = 60
MAX_CAPTURE_INTERVAL_S = 3600.0


def capture_series(book: "Logbook", *, grabber: Any = None, count: int = 1,
                   interval_s: float = 0.0, evidence_dir: Any = None,
                   vision: Any = None, source: str = "screen-journal",
                   actor: str = "user", max_bytes: int = MAX_CAPTURE_BYTES,
                   confirmed: bool = False,
                   sleep: Any = None) -> Dict[str, Any]:
    """Bounded periodic screen-capture loop over an injected grabber (OC-51).

    ``count`` captures are recorded through :func:`capture_to` (which enforces
    confirmation, the size cap, and typed failures). ``sleep`` is an optional
    injected delay boundary so a hermetic test drives the loop without
    waiting; a missing grabber degrades to the typed :func:`capture` refusal.
    """
    if grabber is None:
        return capture()
    try:
        budget = max(1, min(int(count), MAX_CAPTURE_SERIES))
    except (TypeError, ValueError):
        budget = 1
    try:
        delay = max(0.0, min(float(interval_s), MAX_CAPTURE_INTERVAL_S))
    except (TypeError, ValueError):
        delay = 0.0
    results: List[Dict[str, Any]] = []
    for idx in range(budget):
        if idx and delay and callable(sleep):
            sleep(delay)
        out = capture_to(book, grabber=grabber, evidence_dir=evidence_dir,
                         vision=vision, source=source, actor=actor,
                         max_bytes=max_bytes, confirmed=confirmed)
        results.append(out)
        if out.get("state") != "OK":
            break
    captured = sum(1 for r in results if r.get("state") == "OK")
    return {"state": "OK" if captured else "REFUSED", "schema": SCHEMA,
            "captured": captured, "attempted": len(results),
            "results": results}


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
