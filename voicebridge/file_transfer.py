"""Packet 8U / Qubit D3 — Qubit-safe node-host file transfer kernel.

Qubit implements the File Transfer delta (node directory list, bounded fetch,
stream planning, resumable chunks) entirely over the existing confined model:
enrolled nodes come from :mod:`voicebridge.node_host`, and every path is
resolved strictly inside the project folders approved by
:mod:`voicebridge.files`. No socket is opened, no raw directory is enumerated,
and no process is spawned — a non-loopback node is typed ``UNAVAILABLE``
because Qubit runs no live remote transport.

Every read is bounded and chunked with SHA-256 digests. ``stream_plan``
persists a resumable plan (0600, atomic) and ``resume`` re-validates path
confinement, the plan digest, and the next chunk digest before handing back its
spec, so a tampered plan, a changed file, or a replayed request is detected.
The emergency stop is the same durable latch the broker, CLI, dashboard, and
node host observe (fail closed).

Deliberate divergences from Qubit's transport (no listener, no remote
stream, confirmed reads) are recorded in ``docs/archive/2026-09-21/UPSTREAM_REUSE.md``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from . import files as FI
from . import node_host as NH

SCHEMA = "qubit-file-transfers/1"
STORE_NAME = "file-transfers.json"

FILE_TRANSFER_UNAVAILABLE = "FILE_TRANSFER_UNAVAILABLE"
FILE_TRANSFER_UNKNOWN_NODE = "FILE_TRANSFER_UNKNOWN_NODE"
FILE_TRANSFER_UNKNOWN_TRANSFER = "FILE_TRANSFER_UNKNOWN_TRANSFER"
FILE_TRANSFER_BAD_INPUT = "FILE_TRANSFER_BAD_INPUT"
FILE_TRANSFER_TOO_LARGE = "FILE_TRANSFER_TOO_LARGE"
FILE_TRANSFER_ESCAPE = "FILE_TRANSFER_ESCAPE"
FILE_TRANSFER_ESTOP = "FILE_TRANSFER_ESTOP"
FILE_TRANSFER_CONFIRM_REQUIRED = "FILE_TRANSFER_CONFIRM_REQUIRED"
FILE_TRANSFER_CORRUPT = "FILE_TRANSFER_CORRUPT"

CODES = frozenset((
    FILE_TRANSFER_UNAVAILABLE, FILE_TRANSFER_UNKNOWN_NODE,
    FILE_TRANSFER_UNKNOWN_TRANSFER, FILE_TRANSFER_BAD_INPUT,
    FILE_TRANSFER_TOO_LARGE, FILE_TRANSFER_ESCAPE, FILE_TRANSFER_ESTOP,
    FILE_TRANSFER_CONFIRM_REQUIRED, FILE_TRANSFER_CORRUPT,
))

MAX_LIST_ENTRIES = 200
MAX_LIST_SCAN = 5000

DEFAULT_FETCH_BYTES = 64 * 1024
MAX_FETCH_BYTES = 256 * 1024
DEFAULT_CHUNK_BYTES = 64 * 1024
MIN_CHUNK_BYTES = 16
MAX_CHUNK_BYTES = 256 * 1024
MAX_CHUNKS = 4096

MAX_TRANSFER_BYTES = 8 * 1024 * 1024
MAX_UNCONFIRMED_BYTES = 256 * 1024

MAX_PLANS = 128
MAX_STORE_BYTES = 2_000_000

SUPPORTED_OPS = ("list_directory", "fetch", "stream_plan", "resume")

_LOOPBACK_HOSTS = frozenset(("localhost", "127.0.0.1", "::1"))


class FileTransferError(Exception):
    """Typed file-transfer failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: Any) -> str:
    from .redaction import redact_text

    return redact_text(str(text if text is not None else ""))


def _now() -> float:
    return time.time()


def _audit(op: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"file_transfer.{op}",
                  **{k: (_redact(v)[:160] if isinstance(v, str) else v)
                     for k, v in detail.items()})
    except Exception:
        pass


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the node host (fail closed)."""
    return NH.estop_engaged(cfg)


def _estop(op: str) -> Dict[str, Any]:
    return {"state": "ESTOP", "code": FILE_TRANSFER_ESTOP, "operation": op,
            "detail": "emergency stop is latched; reset it before transfers",
            "owner": "qubit-policy",
            "rollback": "nothing was read or transferred; nothing to roll back"}


def _refusal(op: str, code: str, detail: str, *,
             state: str = "REFUSED") -> Dict[str, Any]:
    return {"state": state, "code": code, "operation": op,
            "detail": _redact(detail)[:300], "owner": "qubit-policy",
            "rollback": "nothing was read or transferred; nothing to roll back"}


def _unavailable(op: str, node_id: Any) -> Dict[str, Any]:
    return _refusal(
        op, FILE_TRANSFER_UNAVAILABLE,
        f"node {_redact(node_id)[:64]!r} has no live file-transfer transport; "
        "Qubit runs no remote actuator and only reads a confined node on the "
        "local host", state="UNAVAILABLE")


def _unknown_node(op: str, node_id: Any) -> Dict[str, Any]:
    return {"state": "UNKNOWN", "code": FILE_TRANSFER_UNKNOWN_NODE,
            "operation": op, "node_id": _redact(node_id)[:64],
            "detail": "no such enrolled node", "owner": "qubit-policy",
            "rollback": "nothing was read or transferred; nothing to roll back"}


def _unknown_transfer(op: str, transfer_id: Any) -> Dict[str, Any]:
    return {"state": "UNKNOWN", "code": FILE_TRANSFER_UNKNOWN_TRANSFER,
            "operation": op, "transfer_id": _redact(transfer_id)[:64],
            "detail": "no such resumable transfer plan", "owner": "qubit-policy",
            "rollback": "nothing was read or transferred; nothing to roll back"}


def _node_record(cfg: Any, node_id: Any) -> Optional[Dict[str, Any]]:
    try:
        state = NH._load(cfg)
    except NH.NodeHostError as exc:
        raise FileTransferError(
            FILE_TRANSFER_CORRUPT,
            f"node registry unreadable: {_redact(exc.detail)[:200]}") from exc
    return state["nodes"].get(NH._norm_id(node_id))


def _is_local(rec: Dict[str, Any]) -> bool:
    """A node is local only when its endpoint host is loopback."""
    try:
        host = (urlparse(str(rec.get("endpoint", ""))).hostname or "").lower()
    except ValueError:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    if host.startswith("127.") or host.endswith(".localhost"):
        return True
    return False


def _map_files_code(code: str) -> str:
    return {
        FI.OUTSIDE_ROOT_CODE: FILE_TRANSFER_ESCAPE,
        FI.BAD_INPUT_CODE: FILE_TRANSFER_BAD_INPUT,
        FI.NOT_FOUND_CODE: FILE_TRANSFER_BAD_INPUT,
        FI.TOO_LARGE_CODE: FILE_TRANSFER_TOO_LARGE,
        FI.BINARY_CODE: FILE_TRANSFER_BAD_INPUT,
        FI.IO_CODE: FILE_TRANSFER_BAD_INPUT,
        FI.CONFIRM_CODE: FILE_TRANSFER_CONFIRM_REQUIRED,
    }.get(code, FILE_TRANSFER_BAD_INPUT)


def _resolve(cfg: Any, path: Any) -> Path:
    """Resolve ``path`` strictly inside an approved root (or raise typed)."""
    if not isinstance(path, (str, os.PathLike)):
        raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                "a file or folder path is required")
    try:
        target = FI._resolve_inside(cfg, path)
    except FI.FilesError as exc:
        raise FileTransferError(_map_files_code(exc.code), exc.detail) from exc
    if not target.exists():
        raise FileTransferError(FILE_TRANSFER_BAD_INPUT, "no such file or folder")
    try:
        FI._refuse_special(target)
    except FI.FilesError as exc:
        raise FileTransferError(_map_files_code(exc.code), exc.detail) from exc
    return target


def _stat_size(target: Path) -> int:
    try:
        return int(target.stat().st_size)
    except OSError as exc:
        raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                f"cannot stat file ({exc})") from exc


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _blank_transfers() -> Dict[str, Any]:
    return {"schema": SCHEMA, "transfers": {}}


def _load_transfers(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.exists():
        return _blank_transfers()
    try:
        if path.stat().st_size > MAX_STORE_BYTES:
            raise FileTransferError(FILE_TRANSFER_CORRUPT,
                                    "transfer store too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileTransferError:
        raise
    except (OSError, ValueError) as exc:
        raise FileTransferError(
            FILE_TRANSFER_CORRUPT,
            f"transfer store unreadable: {_redact(exc)[:200]}") from exc
    if (not isinstance(raw, dict) or raw.get("schema") != SCHEMA
            or not isinstance(raw.get("transfers"), dict)):
        raise FileTransferError(FILE_TRANSFER_CORRUPT,
                                "transfer store has an unknown schema")
    return raw


def _save_transfers(cfg: Any, state: Dict[str, Any]) -> None:
    path = _store_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{STORE_NAME}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        raise FileTransferError(
            FILE_TRANSFER_CORRUPT,
            f"transfer store write failed: {_redact(exc)[:200]}") from exc


def _plan_digest(plan: Dict[str, Any]) -> str:
    core = {
        "node_id": plan.get("node_id"), "path": plan.get("path"),
        "size": plan.get("size"), "mtime": plan.get("mtime"),
        "chunk_size": plan.get("chunk_size"),
        "chunk_count": plan.get("chunk_count"),
        "digest": plan.get("digest"), "chunks": plan.get("chunks"),
    }
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _verify_plan(plan: Dict[str, Any]) -> None:
    expected = str(plan.get("plan_digest", ""))
    actual = _plan_digest(plan)
    if not expected or not hmac.compare_digest(expected, actual):
        raise FileTransferError(FILE_TRANSFER_CORRUPT,
                                "transfer plan digest does not match")


def _public_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {"transfer_id": plan["transfer_id"], "node": plan["node_id"],
            "path": _redact(plan.get("path", ""))[:300],
            "size": plan["size"], "chunk_size": plan["chunk_size"],
            "chunk_count": plan["chunk_count"], "digest": plan["digest"],
            "chunks": [dict(c) for c in plan["chunks"]],
            "created_at": plan["created_at"], "transport": "local-confined",
            "resumable": True,
            "authority": "digests only; no payload was transferred"}


def _check_bounded_int(value: Any, *, default: int, lo: int, hi: int,
                       name: str, too_large: bool = False) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise FileTransferError(FILE_TRANSFER_BAD_INPUT, f"{name} must be an int")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                f"{name} must be an int") from None
    if float(value) != number:
        raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                f"{name} must be a whole number")
    if number < lo:
        raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                f"{name} must be >= {lo}")
    if number > hi:
        code = FILE_TRANSFER_TOO_LARGE if too_large else FILE_TRANSFER_BAD_INPUT
        raise FileTransferError(code, f"{name} exceeds the cap ({hi})")
    return number


def status(cfg: Any) -> Dict[str, Any]:
    """Read-only transfer inventory; never raises on missing/corrupt state."""
    estop = estop_engaged(cfg)
    nodes = 0
    local_nodes = 0
    transfers = 0
    degraded: List[str] = []
    try:
        state = NH._load(cfg)
        recs = list(state["nodes"].values())
        nodes = len(recs)
        local_nodes = sum(1 for r in recs if _is_local(r))
    except Exception as exc:
        degraded.append(_redact(exc)[:200])
    try:
        transfers = len(_load_transfers(cfg)["transfers"])
    except Exception as exc:
        degraded.append(_redact(exc)[:200])
    out: Dict[str, Any] = {
        "state": "DEGRADED" if degraded else "OK", "schema": SCHEMA,
        "estop": bool(estop), "transport": "local-confined",
        "remote_transport": "UNAVAILABLE",
        "supported": list(SUPPORTED_OPS),
        "nodes": nodes, "local_nodes": local_nodes, "transfers": transfers,
        "authority": "read-only inventory; availability is not authorization",
        "max_transfer_bytes": MAX_TRANSFER_BYTES,
        "max_fetch_bytes": MAX_FETCH_BYTES,
        "default_fetch_bytes": DEFAULT_FETCH_BYTES,
        "max_chunk_bytes": MAX_CHUNK_BYTES,
        "default_chunk_bytes": DEFAULT_CHUNK_BYTES,
        "max_list_entries": MAX_LIST_ENTRIES,
        "transfer_state": "ESTOP" if estop else "READY",
    }
    if degraded:
        out["code"] = FILE_TRANSFER_CORRUPT
        out["detail"] = degraded
        out["owner"] = "qubit-policy"
    return out


def _eligible(cfg: Any, node_id: Any, op: str):
    rec = _node_record(cfg, node_id)
    if rec is None:
        return None, _unknown_node(op, node_id)
    if not _is_local(rec):
        return None, _unavailable(op, node_id)
    return rec, None


def list_directory(cfg: Any, node_id: str, path: str, *,
                   limit: int = MAX_LIST_ENTRIES) -> Dict[str, Any]:
    """List a confined folder for an enrolled local node (bounded)."""
    op = "list_directory"
    if estop_engaged(cfg):
        return _estop(op)
    try:
        _rec, refusal = _eligible(cfg, node_id, op)
        if refusal is not None:
            return refusal
        try:
            clean_limit = int(limit)
        except (TypeError, ValueError):
            raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                    "limit must be an int") from None
        if clean_limit < 1:
            raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                    "limit must be >= 1")
        clean_limit = min(clean_limit, MAX_LIST_ENTRIES)
        target = _resolve(cfg, path)
        if not target.is_dir():
            raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                    "not a folder; use fetch for a file")
        entries: List[Dict[str, Any]] = []
        scanned = 0
        truncated = False
        with os.scandir(target) as it:
            for entry in it:
                scanned += 1
                if scanned > MAX_LIST_SCAN:
                    truncated = True
                    break
                entries.append(_entry_meta(entry))
    except FileTransferError as exc:
        return _refusal(op, exc.code, exc.detail)
    entries.sort(key=lambda e: (e["type"], e["name"].lower()))
    if len(entries) > clean_limit:
        truncated = True
        entries = entries[:clean_limit]
    _audit("list", {"node": node_id, "count": len(entries),
                    "truncated": truncated})
    return {"state": "OK", "node": _redact(node_id)[:64],
            "path": _redact(str(target))[:300], "entries": entries,
            "count": len(entries), "limit": clean_limit,
            "truncated": truncated, "transport": "local-confined",
            "authority": "read-only metadata; confined to approved roots"}


def _entry_meta(entry: os.DirEntry) -> Dict[str, Any]:
    try:
        st = entry.stat(follow_symlinks=False)
        size = int(st.st_size)
        mtime = int(st.st_mtime)
    except OSError:
        size, mtime = 0, 0
    if entry.is_symlink():
        kind = "symlink"
    elif entry.is_dir(follow_symlinks=False):
        kind = "dir"
    elif entry.is_file(follow_symlinks=False):
        kind = "file"
    else:
        kind = "other"
    return {"name": _redact(entry.name)[:120], "type": kind,
            "size": size, "mtime": mtime}


def fetch(cfg: Any, node_id: str, path: str, *, offset: int = 0,
          max_bytes: int = DEFAULT_FETCH_BYTES,
          confirmed: bool = False) -> Dict[str, Any]:
    """Read one bounded chunk of a confined file (base64 + SHA-256)."""
    op = "fetch"
    if estop_engaged(cfg):
        return _estop(op)
    try:
        _rec, refusal = _eligible(cfg, node_id, op)
        if refusal is not None:
            return refusal
        off = _check_bounded_int(offset, default=0, lo=0,
                                 hi=MAX_TRANSFER_BYTES, name="offset")
        cap = _check_bounded_int(max_bytes, default=DEFAULT_FETCH_BYTES, lo=1,
                                 hi=MAX_FETCH_BYTES, name="max_bytes",
                                 too_large=True)
        target = _resolve(cfg, path)
        if target.is_dir():
            raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                    "not a file; use list_directory")
        size = _stat_size(target)
        if size > MAX_TRANSFER_BYTES:
            raise FileTransferError(
                FILE_TRANSFER_TOO_LARGE,
                f"file is {size} bytes (limit {MAX_TRANSFER_BYTES})")
        if off > size:
            raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                    f"offset {off} is beyond end of file")
        if not confirmed and size > MAX_UNCONFIRMED_BYTES:
            raise FileTransferError(
                FILE_TRANSFER_CONFIRM_REQUIRED,
                f"file is {size} bytes; confirm before fetching")
        length = min(cap, size - off)
        with open(target, "rb") as fh:
            fh.seek(off)
            data = fh.read(length)
    except FileTransferError as exc:
        _audit("fetch.refused", {"node": node_id, "code": exc.code})
        return _refusal(op, exc.code, exc.detail)
    except OSError as exc:
        return _refusal(op, FILE_TRANSFER_BAD_INPUT,
                        f"cannot read file ({_redact(exc)[:120]})")
    digest = hashlib.sha256(data).hexdigest()
    _audit("fetch", {"node": node_id, "bytes": len(data), "offset": off})
    return {"state": "OK", "node": _redact(node_id)[:64],
            "path": _redact(str(target))[:300], "offset": off,
            "length": len(data), "size": size, "eof": off + len(data) >= size,
            "encoding": "base64",
            "data": base64.b64encode(data).decode("ascii"),
            "sha256": digest, "trust": "untrusted-file-bytes",
            "transport": "local-confined",
            "authority": "bounded read; confined to approved roots"}


def stream_plan(cfg: Any, node_id: str, path: str, *,
                chunk_size: int = DEFAULT_CHUNK_BYTES) -> Dict[str, Any]:
    """Build and persist a resumable transfer plan (digests only)."""
    op = "stream_plan"
    if estop_engaged(cfg):
        return _estop(op)
    try:
        _rec, refusal = _eligible(cfg, node_id, op)
        if refusal is not None:
            return refusal
        size_hint = _check_bounded_int(chunk_size,
                                       default=DEFAULT_CHUNK_BYTES,
                                       lo=MIN_CHUNK_BYTES,
                                       hi=MAX_CHUNK_BYTES, name="chunk_size")
        target = _resolve(cfg, path)
        if target.is_dir():
            raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                    "not a file; use list_directory")
        size = _stat_size(target)
        if size > MAX_TRANSFER_BYTES:
            raise FileTransferError(
                FILE_TRANSFER_TOO_LARGE,
                f"file is {size} bytes (limit {MAX_TRANSFER_BYTES})")
        plan = _build_plan(target, node_id, size_hint, size)
        state = _load_transfers(cfg)
        _evict_if_full(state)
        state["transfers"][plan["transfer_id"]] = plan
        _save_transfers(cfg, state)
    except FileTransferError as exc:
        _audit("stream_plan.refused", {"node": node_id, "code": exc.code})
        return _refusal(op, exc.code, exc.detail)
    except OSError as exc:
        return _refusal(op, FILE_TRANSFER_BAD_INPUT,
                        f"cannot plan file ({_redact(exc)[:120]})")
    _audit("stream_plan", {"node": node_id, "transfer": plan["transfer_id"],
                           "chunks": plan["chunk_count"]})
    return {"state": "OK", **_public_plan(plan)}


def _build_plan(target: Path, node_id: str, chunk_size: int,
                size: int) -> Dict[str, Any]:
    chunks: List[Dict[str, Any]] = []
    whole = hashlib.sha256()
    index = 0
    offset = 0
    with open(target, "rb") as fh:
        while True:
            data = fh.read(chunk_size)
            if not data:
                break
            whole.update(data)
            chunks.append({"index": index, "offset": offset,
                           "length": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()})
            index += 1
            offset += len(data)
            if index > MAX_CHUNKS:
                raise FileTransferError(FILE_TRANSFER_TOO_LARGE,
                                        f"more than {MAX_CHUNKS} chunks")
    plan: Dict[str, Any] = {
        "transfer_id": "xfer_" + secrets.token_hex(8),
        "node_id": node_id, "path": str(target), "size": size,
        "mtime": int(target.stat().st_mtime), "chunk_size": chunk_size,
        "chunk_count": len(chunks), "digest": whole.hexdigest(),
        "chunks": chunks, "created_at": _now(), "served": [],
    }
    plan["plan_digest"] = _plan_digest(plan)
    return plan


def _evict_if_full(state: Dict[str, Any]) -> None:
    transfers = state["transfers"]
    while len(transfers) >= MAX_PLANS:
        oldest = min(transfers, key=lambda k: transfers[k].get("created_at", 0))
        del transfers[oldest]


def resume(cfg: Any, node_id: str, transfer_id: str, *,
           offset: int) -> Dict[str, Any]:
    """Validate a persisted plan and return the next chunk spec."""
    op = "resume"
    if estop_engaged(cfg):
        return _estop(op)
    try:
        _rec, refusal = _eligible(cfg, node_id, op)
        if refusal is not None:
            return refusal
        state = _load_transfers(cfg)
        plan = state["transfers"].get(str(transfer_id or "").strip())
        if plan is None or plan.get("node_id") != node_id:
            return _unknown_transfer(op, transfer_id)
        _verify_plan(plan)
        size = plan["size"]
        chunk_size = plan["chunk_size"]
        off = _check_bounded_int(offset, default=-1, lo=0, hi=size,
                                 name="offset")
        if off != size and off % chunk_size != 0:
            raise FileTransferError(
                FILE_TRANSFER_BAD_INPUT,
                f"offset {off} is not a chunk boundary ({chunk_size})")
        target = _resolve(cfg, plan["path"])
        if _stat_size(target) != size:
            raise FileTransferError(FILE_TRANSFER_CORRUPT,
                                    "file size changed since the plan")
        if off == size:
            return {"state": "COMPLETE", "code": "", "transfer_id":
                    plan["transfer_id"], "node": node_id, "offset": size,
                    "chunk": None, "next_offset": size, "eof": True,
                    "replayed": off in plan.get("served", []),
                    "transport": "local-confined"}
        index = off // chunk_size
        if index >= len(plan["chunks"]):
            raise FileTransferError(FILE_TRANSFER_CORRUPT,
                                    "plan is missing the requested chunk")
        stored = plan["chunks"][index]
        if stored["offset"] != off:
            raise FileTransferError(FILE_TRANSFER_CORRUPT,
                                    "plan chunk offset mismatch")
        with open(target, "rb") as fh:
            fh.seek(off)
            data = fh.read(stored["length"])
        if hashlib.sha256(data).hexdigest() != stored["sha256"]:
            raise FileTransferError(FILE_TRANSFER_CORRUPT,
                                    "chunk digest mismatch; file changed")
        served = plan.setdefault("served", [])
        replayed = off in served
        if not replayed:
            served.append(off)
            try:
                _save_transfers(cfg, state)
            except FileTransferError:
                pass
    except FileTransferError as exc:
        _audit("resume.refused", {"node": node_id, "code": exc.code})
        return _refusal(op, exc.code, exc.detail)
    except OSError as exc:
        return _refusal(op, FILE_TRANSFER_BAD_INPUT,
                        f"cannot read file ({_redact(exc)[:120]})")
    _audit("resume", {"node": node_id, "transfer": plan["transfer_id"],
                      "offset": off, "replayed": replayed})
    return {"state": "OK", "transfer_id": plan["transfer_id"],
            "node": node_id, "offset": off, "chunk": dict(stored),
            "next_offset": off + stored["length"],
            "eof": off + stored["length"] >= size, "replayed": replayed,
            "digest": stored["sha256"], "transport": "local-confined",
            "authority": "plan and chunk digests verified"}


def verify_transfer(cfg: Any, node_id: str, transfer_id: str, *,
                    received: Any) -> Dict[str, Any]:
    """Verify a completed resumable transfer without writing anything.

    ``received`` is the ordered set the receiver assembled, each entry
    ``{"index": int, "data": "<base64>"}``. Every plan chunk must be covered
    exactly once with a matching length and SHA-256, and the reconstructed
    whole-file digest must equal the plan digest. Read-only: no file is
    written, no socket is opened, and only digests/counters are returned.
    """
    op = "verify"
    if estop_engaged(cfg):
        return _estop(op)
    try:
        _rec, refusal = _eligible(cfg, node_id, op)
        if refusal is not None:
            return refusal
        state = _load_transfers(cfg)
        plan = state["transfers"].get(str(transfer_id or "").strip())
        if plan is None or plan.get("node_id") != node_id:
            return _unknown_transfer(op, transfer_id)
        _verify_plan(plan)
        chunks = plan["chunks"]
        if not isinstance(received, (list, tuple)):
            raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                    "received chunks must be a list")
        if len(received) != len(chunks):
            raise FileTransferError(
                FILE_TRANSFER_BAD_INPUT,
                f"expected {len(chunks)} received chunks, got {len(received)}")
        by_index: Dict[int, bytes] = {}
        total = 0
        for item in received:
            if not isinstance(item, dict):
                raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                        "each received chunk must be an object")
            if len(chunks) == 0:
                raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                        "empty plans take no chunks")
            index = _check_bounded_int(item.get("index"), default=-1, lo=0,
                                       hi=len(chunks) - 1, name="chunk index")
            if index in by_index:
                raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                        "chunk indexes must be unique")
            data = item.get("data")
            if not isinstance(data, str):
                raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                        "chunk data must be base64 text")
            try:
                raw = base64.b64decode(data.encode("ascii"), validate=True)
            except Exception:
                raise FileTransferError(
                    FILE_TRANSFER_BAD_INPUT,
                    "chunk data is not valid base64") from None
            total += len(raw)
            if total > MAX_TRANSFER_BYTES:
                raise FileTransferError(FILE_TRANSFER_TOO_LARGE,
                                        "received bytes exceed the cap")
            by_index[index] = raw
        if len(by_index) != len(chunks):
            raise FileTransferError(FILE_TRANSFER_BAD_INPUT,
                                    "received chunk set is incomplete")
        whole = hashlib.sha256()
        for chunk in chunks:
            raw = by_index[chunk["index"]]
            if len(raw) != chunk["length"]:
                raise FileTransferError(FILE_TRANSFER_CORRUPT,
                                        "chunk length mismatch")
            if hashlib.sha256(raw).hexdigest() != chunk["sha256"]:
                raise FileTransferError(FILE_TRANSFER_CORRUPT,
                                        "chunk digest mismatch")
            whole.update(raw)
        digest = whole.hexdigest()
        if digest != plan["digest"]:
            raise FileTransferError(FILE_TRANSFER_CORRUPT,
                                    "whole-file digest mismatch")
    except FileTransferError as exc:
        _audit("verify.refused", {"node": node_id, "code": exc.code})
        return _refusal(op, exc.code, exc.detail)
    _audit("verify", {"node": node_id, "transfer": plan["transfer_id"],
                      "chunks": len(chunks)})
    return {"state": "COMPLETE", "code": "", "transfer_id": plan["transfer_id"],
            "node": node_id, "size": plan["size"], "chunks": len(chunks),
            "digest": digest, "verified": True, "transport": "local-confined",
            "authority": "receiver bytes digest-verified against the plan"}


def refuse_unavailable(op: str = "file transfer",
                       detail: str = "") -> Dict[str, Any]:
    """Typed refusal for a file-transfer action with no live backend."""
    return _refusal(op, FILE_TRANSFER_UNAVAILABLE,
                    detail or "no live file-transfer transport is running")
