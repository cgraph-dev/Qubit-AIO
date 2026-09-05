"""Deterministic memory journal + replay (Packet 8M).

ADAPT of the pinned GENOME journal contract
(docs/UPSTREAM_REUSE.md R23): an append-only JSONL of store mutations with a
monotonic sequence and a chained per-line SHA-256 ``line_hash`` so that a
removed, edited, or reordered line is detectable; torn-tail recovery; and
replay that reproduces the live store so ``verify`` can compare canonical
snapshot hashes.

Local divergence (documented in R23): stdlib-only (no NumPy, no GENOME
MemoryStore); ops are applied to the existing 5B ``MemoryStore`` (or the
8M ``SemanticMemory`` wrapper); the journal is written AFTER a successful
mutation (never ahead of the store), so a crash that loses the final line is
reported by ``verify`` as a divergence rather than silently trusted; snapshot
hashing excludes wall-clock fields (``created_at``/``updated_at``/``expires_at``)
because replay re-runs the same operations at a different wall time, while it
includes content, ids, scope, tiers, provenance, confidence, validity windows,
tombstones, and supersede edges.

Writing is serialized by an in-process lock plus an OS advisory lock on a
sidecar ``<journal>.lock`` so two processes never assign a duplicate ``seq``.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

GENESIS_LINE_HASH = "0" * 64

# Ops the journal understands (mirrors SemanticMemory mutation surface).
KNOWN_OPS = frozenset({
    "remember", "stage_inferred", "correct", "forget", "purge", "promote",
    "record_belief", "invalidate_belief",
})


class JournalCorruptionError(ValueError):
    """A journal line is unreadable and is NOT the final (torn) line."""


def _dumps(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _line_hash(op: Dict[str, Any], prev_hash: str) -> str:
    payload = {k: v for k, v in op.items() if k != "line_hash"}
    payload["prev_hash"] = prev_hash
    return hashlib.sha256(_dumps(payload).encode("utf-8")).hexdigest()


class _SidecarLock:
    """Cross-process advisory lock so seq assignment stays unique."""

    def __init__(self, target: Path) -> None:
        self._path = target.with_name(target.name + ".lock")
        self._fd: Optional[int] = None

    def __enter__(self) -> "_SidecarLock":
        import fcntl
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc: object) -> None:
        import fcntl
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _last_line(path: Path) -> Optional[Dict[str, Any]]:
    """The last complete record, found by growing the tail read window."""
    if not path.is_file():
        return None
    size = path.stat().st_size
    if size == 0:
        return None
    window = 64 * 1024
    while True:
        with open(path, "rb") as handle:
            handle.seek(max(0, size - window))
            chunk = handle.read()
        lines = chunk.splitlines()
        if window < size and len(lines) > 1:
            lines = lines[1:]
        for line in reversed(lines):
            if line.strip():
                try:
                    return json.loads(line)
                except ValueError:
                    continue
        if window >= size:
            return None
        window *= 4


@dataclass
class JournalRead:
    ops: List[Dict[str, Any]]
    torn: bool          # True when the final line was a partial (crash) write


class Journal:
    """Append-only, hash-chained, fsynced mutation log."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, op: Dict[str, Any]) -> Dict[str, Any]:
        """Append one mutation line. Returns the durable record (incl. seq)."""
        if op.get("op") not in KNOWN_OPS:
            raise ValueError(f"unknown journal op {op.get('op')!r}")
        with self._lock, _SidecarLock(self.path):
            previous = _last_line(self.path)
            seq = int(previous["seq"]) + 1 if previous else 1
            prev_hash = (previous or {}).get("line_hash") or GENESIS_LINE_HASH
            record = {"seq": seq, **op}
            record["line_hash"] = _line_hash(record, prev_hash)
            fd = os.open(str(self.path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.chmod(self.path, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as handle:
                    handle.write(_dumps(record) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
        return record

    @staticmethod
    def read(path: Path | str) -> JournalRead:
        """Parse the journal. A malformed LAST line is a torn tail (dropped);
        a malformed interior line raises :class:`JournalCorruptionError`."""
        ops: List[Dict[str, Any]] = []
        bad: Optional[tuple] = None
        with open(path, "rb") as handle:
            for lineno, raw in enumerate(handle, start=1):
                if bad is not None:
                    raise JournalCorruptionError(
                        f"journal line {bad[0]} of {path} is malformed and is NOT "
                        f"the final line ({bad[1]})")
                if not raw.strip():
                    continue
                try:
                    ops.append(json.loads(raw.decode("utf-8")))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    bad = (lineno, str(exc))
        return JournalRead(ops=ops, torn=bad is not None)


def verify_chain(path: Path | str, *,
                 expect_last_seq: Optional[int] = None,
                 expect_last_hash: Optional[str] = None,
                 ) -> tuple[bool, str]:
    """Check the hash chain: no line edited, removed, or reordered. Tail
    truncation is only detectable against an out-of-band checkpoint."""
    prev_hash = GENESIS_LINE_HASH
    expected_seq = 1
    try:
        read = Journal.read(path)
    except JournalCorruptionError as exc:
        return False, str(exc)
    except OSError as exc:
        return False, f"journal could not be read: {exc}"
    for op in read.ops:
        if "line_hash" not in op:
            return True, ("journal predates hash chaining; ordering cannot be "
                          "proven for this file")
        if op.get("seq") != expected_seq:
            return False, (f"sequence gap: expected {expected_seq}, found "
                           f"{op.get('seq')}")
        recomputed = _line_hash(op, prev_hash)
        if recomputed != str(op["line_hash"]):
            return False, (f"line {op['seq']} fails its hash (edited, reordered, "
                           "or a predecessor removed)")
        prev_hash = op["line_hash"]
        expected_seq += 1
    last_seq = expected_seq - 1
    if expect_last_seq is not None and last_seq != expect_last_seq:
        return False, (f"journal ends at seq {last_seq}, checkpoint says "
                       f"{expect_last_seq}: tail truncated")
    if expect_last_hash is not None and prev_hash != expect_last_hash:
        return False, "journal final hash does not match the checkpoint"
    if last_seq == 0 and expect_last_seq is None and expect_last_hash is None:
        return False, ("empty journal with no checkpoint cannot be told apart "
                       "from a truncated-to-nothing one")
    return True, f"journal intact: {last_seq} line(s) chained"


def snapshot_hash(store: Any) -> str:
    """Canonical hash of durable memory state (ids, content, scope, tiers,
    provenance, confidence, validity windows, tombstones, supersede edges).
    Wall-clock fields are excluded (replay re-runs at a different time).
    Accepts a MemoryStore or a SemanticMemory adapter (unwrapped)."""
    inner = getattr(store, "store", store)
    conn = getattr(inner, "_conn", None)
    if conn is None:
        return hashlib.sha256(b"[]").hexdigest()
    all_rows = conn.execute("SELECT * FROM memories ORDER BY id").fetchall()
    records = []
    for r in all_rows:
        d = {k: r[k] for k in r.keys()} if hasattr(r, "keys") else dict(r)
        d.pop("created_at", None)
        d.pop("updated_at", None)
        d.pop("expires_at", None)
        records.append(d)
    return hashlib.sha256(_dumps({"records": records}).encode("utf-8")).hexdigest()


def apply_op(store: Any, op: Dict[str, Any]) -> None:
    """Apply one journal op to a SemanticMemory (or MemoryStore) adapter.

    Ops carry enough to reproduce the mutation; ids assigned by the store are
    reproduced because replay applies the same op sequence to an empty store.
    """
    kind = op["op"]
    if kind == "remember":
        store.remember(op["text"], tier=op.get("tier", "recall"),
                       scope=op.get("scope", "default"),
                       confidence=op.get("confidence", 1.0),
                       source=op.get("source", "user"),
                       provenance=op.get("provenance"),
                       kind=op.get("kind", "fact"),
                       expires_at=op.get("expires_at"),
                       valid_from=op.get("valid_from"),
                       valid_until=op.get("valid_until"),
                       believed_by=op.get("believed_by"),
                       mkey=op.get("mkey"))
    elif kind == "stage_inferred":
        store.stage_inferred(op["text"], scope=op.get("scope", "default"),
                             confidence=op.get("confidence", 0.5),
                             source=op.get("source", "model"),
                             source_ids=op.get("source_ids"),
                             tier=op.get("tier", "recall"))
    elif kind == "correct":
        store.correct(op["old_text"], op["new_text"],
                      scope=op.get("scope", "default"),
                      source=op.get("source", "user"))
    elif kind == "forget":
        store.forget(op["id"], reason=op.get("reason", "journal"),
                     requested_by=op.get("requested_by", "user"))
    elif kind == "purge":
        store.purge(op["id"], requested_by=op.get("requested_by", "user"))
    elif kind == "promote":
        store.promote(op["id"], requested_by=op.get("requested_by", "user"))
    elif kind == "record_belief":
        store.record_belief(op["entity"], op["fact_type"], op["value"],
                            scope=op.get("scope", "default"),
                            valid_from=op.get("valid_from"),
                            valid_until=op.get("valid_until"),
                            confidence=op.get("confidence", 1.0),
                            source=op.get("source", "user"),
                            provenance=op.get("provenance"),
                            believed_by=op.get("believed_by"))
    elif kind == "invalidate_belief":
        store.invalidate_belief(op["id"], at=op.get("at"),
                                requested_by=op.get("requested_by", "user"))
    else:  # forward-compat: unknown op is a hard error, not a skip
        raise ValueError(f"unknown journal op {kind!r}")


def replay(path: Path | str, store: Any, *,
           until_seq: Optional[int] = None) -> int:
    """Rebuild ``store`` from the journal. ``until_seq`` replays a prefix
    (rollback / branch point). Returns the number of ops applied."""
    read = Journal.read(path)
    count = 0
    for op in read.ops:
        if until_seq is not None and op["seq"] > until_seq:
            break
        apply_op(store, op)
        count += 1
    return count


def verify_journal(path: Path | str, store: Any,
                   expect_last_seq: Optional[int] = None,
                   expect_last_hash: Optional[str] = None) -> tuple[bool, str]:
    """Prove the live store matches its own history: the chain is intact AND
    replay reproduces the canonical snapshot hash. Replay runs into a
    temporary store so the live database is never written."""
    intact, reason = verify_chain(path, expect_last_seq=expect_last_seq,
                                  expect_last_hash=expect_last_hash)
    if not intact:
        return False, reason
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="vb-mem-verify-")) / "replay.db"
    fresh = store.__class__(tmp)
    try:
        replay(path, fresh)
        if snapshot_hash(fresh) == snapshot_hash(store):
            return True, reason
        return False, "replay does not reproduce the live store snapshot"
    finally:
        try:
            fresh.close()
        except Exception:
            pass
        try:
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(tmp) + suffix)
                if p.exists():
                    p.unlink()
            Path(tmp).parent.rmdir()
        except OSError:
            pass