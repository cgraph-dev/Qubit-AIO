"""Qubit canvas file host — confined static host for the canvas family.

Serves the nine ``/api/qubit/canvas/*`` bridge rows from a Qubit-owned
state-home subdirectory (``<state_dir>/canvas`` — never an approved
project root, never the read-only reference home). Ported semantics, never
invented transports:

* confinement mirrors the plugin-asset precedent
  (``voicebridge/qubit_compat.py::resolve_plugin_asset`` — ``(base / rel)
  .resolve()`` then ``is_relative_to(base)``, traversal answered 403) and the
  scoped-inspection precedent (``voicebridge/files.py`` — typed ``FilesError``
  codes, special-file refusal, bounded sizes, metadata-only audit);
* entry-URL sanitizing + node capability-URL scoping mirror the canvas client
  (``frontend/qubit-web/src/lib/ocFamilyCanvasApi.ts`` — ``CANVAS_HOST_PATH``,
  ``sanitizeCanvasEntryUrl``, ``resolveCanvasPreviewUrl``), which itself
  mirrors the upstream local-host + capability-scope URL semantics;
* writes are confirm-gated + audited (the bridge stages a real 409 through the
  shared journal; the backend additionally requires ``confirmed`` and appends a
  metadata-only audit event — the same junction the binary-upload path uses);
* ``snapshot`` is an honest metadata proof (names + sizes + hashes); no
  screenshot capture exists and none is fabricated.

Load note: this file lives at ``voicebridge/files/canvas_host.py`` next to the
``voicebridge/files.py`` module, so the dotted path ``voicebridge.files.
canvas_host`` does NOT resolve (``files.py`` wins the submodule lookup — a
package would shadow the live ``files`` module imported across the codebase).
Consumers load it by file path (``importlib``) under the distinct name
``voicebridge.canvas_host``; nothing else changes.

Qubit brand only. Secrets never logged (audit carries names/sizes/hash
prefixes, never content).
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import re
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List

CONFIRM_CODE = "CANVAS_CONFIRM_REQUIRED"
NOT_FOUND_CODE = "CANVAS_NOT_FOUND"
BAD_PATH_CODE = "CANVAS_BAD_PATH"
TOO_LARGE_CODE = "CANVAS_TOO_LARGE"
ENTRY_REFUSED_CODE = "CANVAS_ENTRY_REFUSED"
IO_CODE = "CANVAS_IO"

MAX_FILE_BYTES = 1024 * 1024  # 1 MiB, mirrors files.MAX_FILE_BYTES
MAX_LIST_ENTRIES = 200  # mirrors files.MAX_LIST_ENTRIES
MAX_READ_CHARS = 20000  # mirrors files.MAX_READ_CHARS
MAX_DEPTH = 4  # mirrors files.MAX_DEPTH
MAX_NAME_LEN = 256
MAX_PATH_LEN = 1024
MAX_NODE_ID_LEN = 128
MAX_CONTENT_BYTES = MAX_FILE_BYTES

#: Served host path + scoped capability prefix (mirror the canvas client
#: constants, which mirror the upstream local-host shape, rebranded).
CANVAS_HOST_PATH = "/__qubit__/canvas"
CANVAS_SCOPED_PATH_PREFIX = "/__qubit__/cap"
#: Display-only root label (mirrors the client's CANVAS_ROOT_LABEL).
CANVAS_ROOT_LABEL = "state-home canvas/"

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


class CanvasError(Exception):
    """Typed canvas-host failure (never a traceback on the wire)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact_spec(text: Any) -> str:
    try:
        from voicebridge.redaction import redact_text as _redact

        return _redact(str(text or ""))
    except Exception:
        return "?"


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from voicebridge.logging_setup import log_event

        log_event(f"canvas.{op}", actor=actor,
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def _estop_engaged(cfg: Any) -> bool:
    try:
        from voicebridge.plugins import estop_engaged as _engaged

        return bool(_engaged(cfg))
    except Exception:
        return False


def root(cfg: Any) -> Path:
    """The Qubit-owned canvas root (created on first use)."""
    path = Path(cfg.state_dir()) / "canvas"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CanvasError(IO_CODE, f"canvas root unavailable: {exc}")
    return path


def _has_control(value: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)


def _check_name(name: str) -> str:
    """Validate one canvas-root relative name (no traversal, no absolute)."""
    text = str(name or "").strip().replace("\\", "/")
    if not text:
        raise CanvasError(BAD_PATH_CODE,
                          "a canvas file name is required (<=256 chars "
                          "per segment, <=1024 total)")
    if len(text) > MAX_PATH_LEN or "\x00" in text:
        raise CanvasError(BAD_PATH_CODE,
                          "a canvas file name is required (<=256 chars "
                          "per segment, <=1024 total)")
    if text.startswith("/") or text.startswith("//"):
        raise CanvasError(BAD_PATH_CODE,
                          "absolute paths are refused; pass a canvas-root "
                          "relative name")
    if _SCHEME_RE.match(text):
        raise CanvasError(BAD_PATH_CODE,
                          "URL schemes are refused; pass a canvas-root "
                          "relative name")
    parts = [p for p in text.split("/") if p not in ("",)]
    if not parts:
        raise CanvasError(BAD_PATH_CODE, "a canvas file name is required")
    for part in parts:
        if part in (".", "..") or ":" in part or _has_control(part) \
                or len(part) > MAX_NAME_LEN:
            raise CanvasError(BAD_PATH_CODE,
                              f"refused canvas name segment {part!r}: no "
                              "traversal, absolute paths, or odd segments")
    if len(parts) > MAX_DEPTH + 1:
        raise CanvasError(BAD_PATH_CODE,
                          f"canvas nesting is limited to {MAX_DEPTH} levels")
    return "/".join(parts)


def _resolve(cfg: Any, name: str) -> Path:
    """Resolve ``name`` strictly inside the canvas root (or raise)."""
    clean = _check_name(name)
    base = root(cfg).resolve()
    try:
        target = (base / clean).resolve()
    except (OSError, RuntimeError) as exc:
        raise CanvasError(IO_CODE, f"cannot resolve path ({exc})")
    try:
        target.relative_to(base)
    except ValueError:
        raise CanvasError(BAD_PATH_CODE,
                          "path escapes the canvas root; traversal refused")
    return target


def _refuse_special(path: Path, *, dir_ok: bool = False) -> None:
    try:
        import stat as _stat

        mode = path.lstat().st_mode
    except OSError as exc:
        raise CanvasError(IO_CODE, f"cannot stat path ({exc})")
    if _stat.S_ISREG(mode):
        return
    if dir_ok and _stat.S_ISDIR(mode):
        return
    raise CanvasError(BAD_PATH_CODE,
                      "only regular files may be served"
                      + (" or listed folders" if dir_ok else ""))


def _public(entry: Path, base: Path) -> Dict[str, Any]:
    try:
        size = entry.stat().st_size
        mtime = entry.stat().st_mtime
    except OSError:
        size, mtime = 0, 0.0
    rel = str(entry.relative_to(base))
    return {"name": entry.name[:120], "path": rel[:300], "size": int(size),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime(mtime))}


# ------------------------------------------------------------------ file ops
def list_files(cfg: Any) -> Dict[str, Any]:
    """List canvas-root files (read-only, bounded, recursive to MAX_DEPTH)."""
    base = root(cfg)
    entries: List[Dict[str, Any]] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(base):
        level = Path(dirpath).relative_to(base)
        if len(level.parts) > MAX_DEPTH:
            dirnames[:] = []
            continue
        dirnames.sort()
        for name in sorted(filenames):
            candidate = Path(dirpath) / name
            try:
                candidate.relative_to(base.resolve())
                _refuse_special(candidate)
            except CanvasError:
                continue
            entries.append(_public(candidate, base))
            if len(entries) >= MAX_LIST_ENTRIES:
                truncated = True
                break
        if truncated:
            break
        if len(level.parts) >= MAX_DEPTH:
            dirnames[:] = []
    _audit("list", "qubit", {"count": str(len(entries))})
    return {"state": "OK", "root": CANVAS_ROOT_LABEL, "files": entries,
            "truncated": truncated, "live_reload": False,
            "authority": "voicebridge canvas host (state-home canvas/)"}


def get_file(cfg: Any, path: str) -> Dict[str, Any]:
    """Read one canvas-root file (bounded text + full bytes as base64)."""
    target = _resolve(cfg, path)
    if not target.exists():
        raise CanvasError(NOT_FOUND_CODE, f"no canvas file {path!r}")
    _refuse_special(target)
    if target.is_dir():
        raise CanvasError(BAD_PATH_CODE, "not a file; use canvas list")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise CanvasError(IO_CODE, f"cannot stat file ({exc})")
    if size > MAX_FILE_BYTES:
        raise CanvasError(TOO_LARGE_CODE,
                          f"file is {size} bytes (limit {MAX_FILE_BYTES})")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise CanvasError(IO_CODE, f"cannot read file ({exc})")
    text = data.decode("utf-8", "replace")
    clipped = len(text) > MAX_READ_CHARS
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    digest = hashlib.sha256(data).hexdigest()
    _audit("read", "qubit", {"bytes": str(len(data)),
                             "sha256": digest[:16]})
    rel = str(target.relative_to(root(cfg).resolve()))
    return {"state": "OK", "path": rel[:300], "size": len(data),
            "sha256": digest, "mime": mime, "content": text[:MAX_READ_CHARS],
            "truncated": clipped,
            "data_base64": base64.b64encode(data).decode("ascii"),
            "authority": "voicebridge canvas host (state-home canvas/)"}


def live_state(cfg: Any) -> Dict[str, Any]:
    """Report the host live-reload state (read-only, honest).

    The confined host serves static bytes with no reload socket, so
    ``live_reload`` is False — re-fetch to refresh. Never fabricated True.
    """
    root(cfg)
    return {"state": "OK", "live_reload": False, "root": CANVAS_ROOT_LABEL,
            "note": "static host; re-fetch the entry to refresh",
            "authority": "voicebridge canvas host (state-home canvas/)"}


def _require_confirmed(confirmed: Any, op: str) -> None:
    if confirmed is True or str(confirmed or "").strip().lower() in (
            "1", "true", "yes", "on", "confirm", "confirmed"):
        return
    raise CanvasError(CONFIRM_CODE,
                      f"canvas {op} requires explicit confirmation: "
                      "re-POST with {\"confirm\": true}")


def _refuse_estop(op: str) -> None:
    raise CanvasError("EMERGENCY_STOP",
                      f"emergency stop latched; canvas {op} refused")


def add_file(cfg: Any, path: str, content: str, *,
             confirmed: bool = False,
             requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Add (or replace) one canvas-root file (confirm-gated, audited)."""
    _require_confirmed(confirmed, "add")
    if _estop_engaged(cfg):
        _refuse_estop("add")
    target = _resolve(cfg, path)
    if not isinstance(content, str):
        raise CanvasError(BAD_PATH_CODE, "content must be text")
    data = content.encode("utf-8")
    if len(data) > MAX_CONTENT_BYTES:
        raise CanvasError(TOO_LARGE_CODE,
                          f"content is {len(data)} bytes "
                          f"(limit {MAX_CONTENT_BYTES}); nothing was written")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except OSError as exc:
        raise CanvasError(IO_CODE, f"cannot write file ({exc})")
    digest = hashlib.sha256(data).hexdigest()
    _audit("add", requested_by,
           {"bytes": str(len(data)), "sha256": digest[:16]})
    rel = str(target.relative_to(root(cfg).resolve()))
    return {"state": "OK", "path": rel[:300], "size": len(data),
            "sha256": digest,
            "authority": "voicebridge canvas host (state-home canvas/)"}


def remove_file(cfg: Any, path: str, *, confirmed: bool = False,
                requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Remove one canvas-root file (confirm-gated, audited)."""
    _require_confirmed(confirmed, "remove")
    if _estop_engaged(cfg):
        _refuse_estop("remove")
    target = _resolve(cfg, path)
    if not target.exists():
        raise CanvasError(NOT_FOUND_CODE, f"no canvas file {path!r}")
    _refuse_special(target)
    if target.is_dir():
        raise CanvasError(BAD_PATH_CODE,
                          "only files may be removed; folders stay")
    if not target.exists():
        raise CanvasError(NOT_FOUND_CODE, f"no canvas file {path!r}")
    try:
        target.unlink()
    except OSError as exc:
        raise CanvasError(IO_CODE, f"cannot remove file ({exc})")
    _audit("remove", requested_by, {})
    rel = str(target.relative_to(root(cfg).resolve()))
    return {"state": "OK", "removed": rel[:300],
            "authority": "voicebridge canvas host (state-home canvas/)"}


# ------------------------------------------------------- node presentation
def _check_node_id(node_id: str) -> str:
    text = str(node_id or "").strip()
    if not text or len(text) > MAX_NODE_ID_LEN or "\x00" in text \
            or _has_control(text):
        raise CanvasError(BAD_PATH_CODE,
                          "a node id is required (<=128 chars)")
    return text


def sanitize_entry(raw: str) -> str:
    """Keep only same-host canvas entry names (ported client rule).

    Accepts ``/__qubit__/canvas/<name>[?query][#frag]`` or a bare
    ``<name>``; anything else (external URLs, non-canvas paths) is refused.
    Returns the sanitized ``/__qubit__/canvas/<name>`` string.
    """
    text = str(raw or "").strip()
    if not text:
        raise CanvasError(ENTRY_REFUSED_CODE, "a canvas entry is required")
    if _SCHEME_RE.match(text) or text.startswith("//"):
        raise CanvasError(ENTRY_REFUSED_CODE,
                          "external entries are refused; only same-host "
                          "canvas entries may be presented")
    if text.startswith("/"):
        if text != CANVAS_HOST_PATH and not text.startswith(
                CANVAS_HOST_PATH + "/"):
            raise CanvasError(ENTRY_REFUSED_CODE,
                              "only canvas-host paths may be presented; "
                              "external and non-canvas paths are refused")
        rest = text[len(CANVAS_HOST_PATH):].split("?", 1)[0].split("#", 1)[0]
        rest = rest.strip("/")
        name = _check_name(rest)
        return f"{CANVAS_HOST_PATH}/{name}"
    bare = text.split("?", 1)[0].split("#", 1)[0]
    name = _check_name(bare)
    return f"{CANVAS_HOST_PATH}/{name}"


def _entry_name(entry: str) -> str:
    """The canvas-root file name behind a sanitized entry string."""
    clean = sanitize_entry(entry)
    return clean[len(CANVAS_HOST_PATH) + 1:]


def scoped_preview_url(entry: str, scoped_base_url: Any = None) -> str:
    """Scope an entry under a node capability base (ported client rule).

    Mirrors ``resolveCanvasPreviewUrl``: without a valid scoped base (an
    http(s) URL whose path starts with ``/__qubit__/cap``) the sanitized
    same-host entry path is returned unchanged.
    """
    safe = sanitize_entry(entry)
    raw_base = str(scoped_base_url or "").strip()
    if not raw_base:
        return safe
    if _SCHEME_RE.match(raw_base) is None:
        return safe
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(raw_base)
    except ValueError:
        return safe
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return safe
    prefix = parts.path.rstrip("/")
    if not prefix.startswith(CANVAS_SCOPED_PATH_PREFIX):
        return safe
    tail = safe.split("?", 1)
    path_part, suffix = tail[0], ("?" + tail[1] if len(tail) > 1 else "")
    return f"{parts.scheme}://{parts.netloc}{prefix}{path_part}{suffix}"


def present(cfg: Any, node_id: str, entry: str, *,
            scoped_base_url: Any = None, confirmed: bool = False,
            requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Show a canvas entry on a node (confirm-gated, confined, audited).

    The entry must resolve to a file that exists in the canvas root
    (``CANVAS_NOT_FOUND`` otherwise); node identity itself is read through the
    existing node roster surface (no new node client here).
    """
    _require_confirmed(confirmed, "present")
    if _estop_engaged(cfg):
        _refuse_estop("present")
    node = _check_node_id(node_id)
    name = _entry_name(entry)
    target = _resolve(cfg, name)
    if not target.is_file():
        raise CanvasError(NOT_FOUND_CODE,
                          f"no canvas file {name!r}; add it first")
    url = scoped_preview_url(entry, scoped_base_url)
    _audit("present", requested_by, {"node": node[:64]})
    return {"state": "OK", "node_id": node,
            "entry": f"{CANVAS_HOST_PATH}/{name}", "preview_url": url,
            "authority": "voicebridge canvas host (state-home canvas/)"}


def hide(cfg: Any, node_id: str, *, confirmed: bool = False,
         requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Hide the node canvas (confirm-gated, audited)."""
    _require_confirmed(confirmed, "hide")
    if _estop_engaged(cfg):
        _refuse_estop("hide")
    node = _check_node_id(node_id)
    _audit("hide", requested_by, {"node": node[:64]})
    return {"state": "OK", "node_id": node, "hidden": True,
            "authority": "voicebridge canvas host (state-home canvas/)"}


def navigate(cfg: Any, node_id: str, entry: str, *,
             confirmed: bool = False,
             requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Open a canvas entry URL on the node canvas (confirm-gated, confined).

    Only canvas-host entries may be opened here; external URLs are refused
    (no open-URL transport is invented). ``exists`` reports whether the entry
    names a file already in the root.
    """
    _require_confirmed(confirmed, "navigate")
    if _estop_engaged(cfg):
        _refuse_estop("navigate")
    node = _check_node_id(node_id)
    name = _entry_name(entry)
    exists = _resolve(cfg, name).is_file()
    url = f"{CANVAS_HOST_PATH}/{name}"
    _audit("navigate", requested_by, {"node": node[:64]})
    return {"state": "OK", "node_id": node, "entry": url, "exists": exists,
            "authority": "voicebridge canvas host (state-home canvas/)"}


def snapshot(cfg: Any, node_id: str, *, confirmed: bool = False,
             requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Capture a metadata proof of the canvas root (confirm-gated, audited).

    Honest scope: names + sizes + hash prefixes + capture time. No screenshot
    capture exists and none is fabricated.
    """
    _require_confirmed(confirmed, "snapshot")
    if _estop_engaged(cfg):
        _refuse_estop("snapshot")
    node = _check_node_id(node_id)
    listing = list_files(cfg)
    proof = []
    for row in listing["files"]:
        target = _resolve(cfg, row["path"])
        try:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
        except OSError:
            digest = ""
        proof.append({"path": row["path"], "size": row["size"],
                      "sha256_16": digest, "updated_at": row["updated_at"]})
    _audit("snapshot", requested_by,
           {"node": node[:64], "count": str(len(proof))})
    return {"state": "OK", "node_id": node, "files": proof,
            "count": len(proof), "at": time.time(),
            "proof": "metadata-only; no screenshot capture exists",
            "authority": "voicebridge canvas host (state-home canvas/)"}


# ------------------------------------------------------- gateway entry points
def _gw_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return list_files(cfg)


def _gw_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    path = str(params.get("path", "") or "")
    if not path.strip():
        raise CanvasError(BAD_PATH_CODE, "path is required")
    return get_file(cfg, path)


def _gw_live(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return live_state(cfg)


def _confirmed(params: Dict[str, Any]) -> bool:
    return bool(params.get("confirmed", False))


def _by(params: Dict[str, Any]) -> str:
    return str(params.get("requested_by", "") or "dashboard:user")


def _gw_add(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return add_file(cfg, str(params.get("path", "") or ""),
                    params.get("content", ""), confirmed=_confirmed(params),
                    requested_by=_by(params))


def _gw_remove(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return remove_file(cfg, str(params.get("path", "") or ""),
                       confirmed=_confirmed(params),
                       requested_by=_by(params))


def _gw_present(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return present(cfg, str(params.get("node_id", "") or ""),
                   str(params.get("entry", "") or ""),
                   scoped_base_url=params.get("scoped_base_url"),
                   confirmed=_confirmed(params), requested_by=_by(params))


def _gw_hide(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return hide(cfg, str(params.get("node_id", "") or ""),
                confirmed=_confirmed(params), requested_by=_by(params))


def _gw_navigate(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return navigate(cfg, str(params.get("node_id", "") or ""),
                    str(params.get("entry", "") or ""),
                    confirmed=_confirmed(params), requested_by=_by(params))


def _gw_snapshot(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return snapshot(cfg, str(params.get("node_id", "") or ""),
                    confirmed=_confirmed(params), requested_by=_by(params))


#: Gateway method name -> handler (registered onto each bridge registry by the
#: family bridge in ``voicebridge/qubit_compat.py`` — the one wiring owner).
GATEWAY_METHODS = {
    "canvas.list": _gw_list,
    "canvas.get": _gw_get,
    "canvas.live": _gw_live,
    "canvas.add": _gw_add,
    "canvas.remove": _gw_remove,
    "canvas.present": _gw_present,
    "canvas.hide": _gw_hide,
    "canvas.navigate": _gw_navigate,
    "canvas.snapshot": _gw_snapshot,
}
