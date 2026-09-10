"""Qubit named journal checkpoints.

Records out-of-band positions (line count + sha256) over append-only JSONL
journals so truncation or modification is detectable later — the exact gap
`verify_chain` documents. Checkpoints are metadata only: creating them never
mutates the journal, pruning one deletes only the checkpoint record (with
explicit confirmation), and verification recomputes from disk. No restore
path is provided: replaying history over live state could resurrect
tombstoned facts, so recovery stays an explicit operator procedure.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class CheckpointError(ValueError):
    """Typed checkpoint failure (never silent)."""


def _store_path(state_dir: Path | str) -> Path:
    return Path(state_dir) / "checkpoints.json"


def _load(state_dir: Path | str) -> Dict[str, Any]:
    path = _store_path(state_dir)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise CheckpointError(f"checkpoint store corrupt: {exc}")
    if not isinstance(raw, dict):
        raise CheckpointError("checkpoint store corrupt: not an object")
    return raw


def _save(state_dir: Path | str, data: Dict[str, Any]) -> None:
    path = _store_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _digest(journal: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    count = 0
    with open(journal, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
            count += chunk.count(b"\n")
    return count, h.hexdigest()


def create(state_dir: Path | str, journal: Path | str, name: str) -> Dict[str, Any]:
    """Record the current tail position of a journal (never mutates it)."""
    if not NAME_RE.match(name or ""):
        raise CheckpointError(f"bad checkpoint name: {name!r}")
    journal = Path(journal)
    if not journal.is_file():
        raise CheckpointError(f"journal not found: {journal}")
    data = _load(state_dir)
    if name in data:
        raise CheckpointError(f"checkpoint exists: {name}")
    lines, digest = _digest(journal)
    data[name] = {"journal": str(journal), "lines": lines,
                  "sha256": digest, "created_at": time.time()}
    _save(state_dir, data)
    return {"state": "OK", "name": name, "lines": lines, "sha256": digest}


def list_all(state_dir: Path | str) -> List[Dict[str, Any]]:
    """List checkpoints (read-only)."""
    return [{"name": name, **rec} for name, rec in
            sorted(_load(state_dir).items())]


def verify(state_dir: Path | str, name: str) -> Dict[str, Any]:
    """Recompute the journal digest and compare (tamper-evident)."""
    data = _load(state_dir)
    rec = data.get(name)
    if rec is None:
        return {"state": "MISSING", "name": name}
    journal = Path(rec.get("journal", ""))
    if not journal.is_file():
        return {"state": "MISSING", "name": name,
                "detail": "journal file is gone"}
    lines, digest = _digest(journal)
    if digest != rec.get("sha256"):
        return {"state": "TAMPERED", "name": name,
                "recorded_lines": rec.get("lines"), "current_lines": lines}
    return {"state": "OK", "name": name, "lines": lines}


def prune(state_dir: Path | str, name: str, *, confirm: bool = False) -> Dict[str, Any]:
    """Delete a checkpoint record (metadata only; needs confirmation)."""
    if not confirm:
        return {"state": "DENIED",
                "detail": "prune needs explicit confirmation"}
    data = _load(state_dir)
    if name not in data:
        return {"state": "MISSING", "name": name}
    del data[name]
    _save(state_dir, data)
    return {"state": "OK", "name": name, "detail": "checkpoint record removed"}
