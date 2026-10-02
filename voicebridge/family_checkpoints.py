"""Qubit-native integrity checkpoints for the family bridge.

A checkpoint is a verifiable snapshot marker: ``checkpoints.create`` records
the session transcript hash plus per-file hashes of the Qubit workspace into
``<qubit_home>/checkpoints/<session>/<ts>/manifest.json`` (the same shadow
store the ``GET /api/ops/checkpoints`` journal reads, so created checkpoints
show up in the journal immediately). ``checkpoints.verify`` recomputes every
hash and reports per-file OK / CHANGED / MISSING plus the transcript match —
an integrity proof, never a bare boolean.

Lineage note: the checkpoints pages were ported from the upstream Hermes
console (hermes-agent lineage); the snapshot semantics here are reimplemented
Qubit-native (transcript + workspace hashes over local stores).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List

_MAX_FILES = 2000
_MAX_FILE_BYTES = 5 * 1024 * 1024
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist",
              "build", ".cache"}


def _gateway_error(code: str, detail: str) -> Any:
    from .gateway.gateway_ws import GatewayError

    return GatewayError(code, detail)


def _checkpoint_base(cfg: Any) -> Any:
    from . import qubit_compat as QC

    return QC._checkpoint_base(cfg)


def _transcript(cfg: Any, session: str) -> List[Dict[str, Any]]:
    from .qubit_session_store import QubitSessionStore

    store = QubitSessionStore(cfg)
    out = store.messages(session, limit=1000)
    if str(out.get("state", "")) != "OK":
        raise _gateway_error(str(out.get("code", "SESSION_NOT_FOUND")),
                             str(out.get("detail",
                                         f"no chat session {session!r}")))
    msgs = out.get("messages", [])
    return msgs if isinstance(msgs, list) else []


def _canonical_transcript(msgs: List[Dict[str, Any]]) -> str:
    parts = []
    for msg in msgs:
        if not isinstance(msg, dict):
            continue
        parts.append(json.dumps({
            "role": msg.get("role", ""),
            "content": msg.get("content", ""),
            "tool_name": msg.get("tool_name", ""),
            "timestamp": msg.get("timestamp", 0),
        }, sort_keys=True))
    return "\n".join(parts)


def _sha256_file(path: Any) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_snapshot(cfg: Any) -> Dict[str, Any]:
    try:
        from .memory.workspace import workspace_dir
        root = workspace_dir(cfg)
    except Exception:
        return {"root": "", "files": [], "skipped": 0,
                "detail": "no workspace dir for this config"}
    files: List[Dict[str, Any]] = []
    skipped = 0
    try:
        entries = sorted(root.rglob("*"))
    except OSError:
        return {"root": str(root), "files": [], "skipped": 0,
                "detail": "workspace unreadable"}
    for entry in entries:
        if len(files) >= _MAX_FILES:
            skipped += 1
            continue
        try:
            if entry.is_dir():
                if entry.name in _SKIP_DIRS:
                    skipped += len(list(entry.rglob("*")))
                continue
            if not entry.is_file() or entry.is_symlink():
                skipped += 1
                continue
            size = entry.stat().st_size
            if size > _MAX_FILE_BYTES:
                skipped += 1
                continue
            rel = str(entry.relative_to(root))
            files.append({"path": rel, "size": size,
                          "sha256": _sha256_file(entry)})
        except OSError:
            skipped += 1
    return {"root": str(root), "files": files, "skipped": skipped}


def _manifest_path(cfg: Any, session: str, stamp: str) -> Any:
    base = _checkpoint_base(cfg)
    safe_session = "".join(
        c if (c.isalnum() or c in ("-", "_")) else "_" for c in session)[:80]
    safe_stamp = "".join(
        c if (c.isalnum() or c in ("-", "_", ".")) else "_" for c in stamp)
    return base / safe_session / safe_stamp / "manifest.json"


def _gw_checkpoints_create(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    session = str(params.get("session", "") or "").strip()
    if not session:
        raise _gateway_error("CHECKPOINT_SESSION_REQUIRED",
                             "checkpoints/create needs a session key")
    msgs = _transcript(cfg, session)
    canonical = _canonical_transcript(msgs)
    snap = _workspace_snapshot(cfg)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    manifest = {
        "schema": "qubit-checkpoint/1",
        "session": session,
        "created_at": time.time(),
        "stamp": stamp,
        "note": str(params.get("note", "") or "")[:500],
        "transcript_messages": len(msgs),
        "transcript_sha256": hashlib.sha256(
            canonical.encode("utf-8")).hexdigest(),
        "workspace": snap,
        "authority": "local transcript + workspace hashes; verify "
                     "recomputes every hash",
    }
    path = _manifest_path(cfg, session, stamp)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / "manifest.json.tmp"
        tmp.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise _gateway_error("CHECKPOINT_STORE_UNAVAILABLE",
                             f"checkpoint store unwritable: {exc}"[:200])
    return {"state": "OK", "checkpoint": {
        "id": f"{session}/{stamp}", "session": session, "stamp": stamp,
        "transcript_messages": len(msgs),
        "files": len(snap.get("files", [])),
        "skipped": snap.get("skipped", 0),
        "path": str(path)}}


def _gw_checkpoints_verify(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    ident = str(params.get("id", params.get("checkpoint", "")) or "").strip()
    if "/" not in ident:
        raise _gateway_error("CHECKPOINT_ID_REQUIRED",
                             "checkpoints/verify needs an id "
                             "'<session>/<stamp>'")
    session, stamp = ident.split("/", 1)
    path = _manifest_path(cfg, session, stamp)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _gateway_error("CHECKPOINT_NOT_FOUND",
                             f"no checkpoint {ident!r}")
    if not isinstance(manifest, dict):
        raise _gateway_error("CHECKPOINT_NOT_FOUND",
                             f"no checkpoint {ident!r}")
    try:
        msgs = _transcript(cfg, session)
    except Exception:
        msgs = []
    current = hashlib.sha256(
        _canonical_transcript(msgs).encode("utf-8")).hexdigest()
    transcript_match = (current == manifest.get("transcript_sha256")
                        and len(msgs) == manifest.get("transcript_messages"))
    snap = manifest.get("workspace", {})
    root = str(snap.get("root", "") or "")
    results: List[Dict[str, Any]] = []
    for entry in snap.get("files", []) if isinstance(snap, dict) else []:
        if not isinstance(entry, dict):
            continue
        rel = str(entry.get("path", "") or "")
        if not rel or not root:
            results.append({"path": rel, "status": "SKIPPED"})
            continue
        from pathlib import Path
        target = Path(root) / rel
        try:
            if not target.is_file():
                results.append({"path": rel, "status": "MISSING"})
            elif _sha256_file(target) == entry.get("sha256"):
                results.append({"path": rel, "status": "OK"})
            else:
                results.append({"path": rel, "status": "CHANGED"})
        except OSError:
            results.append({"path": rel, "status": "UNREADABLE"})
    bad = sum(1 for r in results if r["status"] not in ("OK", "SKIPPED"))
    verdict = "OK" if (transcript_match and bad == 0) else "DRIFT"
    return {"state": "OK", "id": ident, "verdict": verdict,
            "transcript_match": transcript_match,
            "transcript_messages_then": manifest.get("transcript_messages"),
            "transcript_messages_now": len(msgs),
            "files_checked": len(results), "files_drifted": bad,
            "files": results[:200],
            "authority": "every hash recomputed from live local stores"}


#: Gateway method name -> handler (registered onto each bridge registry by the
#: family bridge in ``voicebridge/qubit_compat.py`` — the one wiring owner).
CHECKPOINTS_GATEWAY_METHODS: Dict[str, Any] = {
    "checkpoints.create": _gw_checkpoints_create,
    "checkpoints.verify": _gw_checkpoints_verify,
}
