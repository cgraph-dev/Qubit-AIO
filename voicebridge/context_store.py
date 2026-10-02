"""Qubit local context engine — our agent memory (the Viking console backend).

100% Qubit-owned code. No upstream source is copied here: the module
implements the *concepts* (a filesystem-like context DB, tiered overviews,
session commit) from scratch against Qubit-owned state. No AGPL code is
used or vendored; if a future step ever needed it, that step stops BLOCKED.

What it is
----------
A LOCAL context store under the Qubit state home (``<state_dir>/context`` —
resolved from ``cfg.state_dir()``, i.e. ``QUBIT_HOME`` state; never
``~/.qubit`` by name, never the read-only reference home). Four groups::

    context/user/       memories, preferences, profile (seeded from copies)
    context/agent/      sessions committed to long-term, experience notes
    context/resources/  uploaded docs/files (confirm-gated, path-confined)
    context/skills/     skill-package index (read from the skills catalog)

Each group directory carries two tier files: ``.abstract.md`` (L0 one-liner)
and ``.overview.md`` (L1 summary). L2 is the live directory listing itself.

Rules (mirroring the canvas-host and binary-upload precedents)
--------------------------------------------------------------
* Everything runs locally: no network, no vector infra, no new DBs — flat
  files only (plus read-only queries against the existing runtime
  ``state.db`` for find/commit/sessions, fail-soft when absent).
* Seeding COPIES: ``USER.md``/``MEMORY.md`` are read from the runtime
  ``memories/`` dir into ``context/user/`` only when the copy is absent.
  Live originals are never moved, never written, never deleted.
* ``find`` is a one-shot keyword search over the local index (file walk) +
  the existing ``messages_fts`` FTS table when present. No new index infra.
* ``skills`` reads the skills catalog (``skill_catalog.catalog_entries``);
  no new registry is invented.
* Writes are confirm-gated (``confirmed`` kwarg, checked before any write)
  and e-stop gated, and append a metadata-only audit event. Secrets and
  content are never logged — audit carries names/counts only.
* ``viking://`` URIs are LOCAL scheme identifiers for tree paths
  (``viking://user/USER.md``); the scheme never leaves this host.

Qubit brand only.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# -- typed errors ----------------------------------------------------------

NOT_FOUND_CODE = "VIKING_NOT_FOUND"
BAD_PATH_CODE = "VIKING_BAD_PATH"
BAD_PARAMS_CODE = "VIKING_BAD_PARAMS"
TOO_LARGE_CODE = "VIKING_TOO_LARGE"
CONFIRM_CODE = "VIKING_CONFIRM_REQUIRED"
ESTOP_CODE = "VIKING_ESTOP"
IO_CODE = "VIKING_IO"

MAX_FILE_BYTES = 1024 * 1024  # 1 MiB, mirrors files.MAX_FILE_BYTES
MAX_LIST_ENTRIES = 200  # mirrors files.MAX_LIST_ENTRIES
MAX_PREVIEW_CHARS = 4000
MAX_FIND_HITS = 50
MAX_QUERY_LEN = 256
MAX_TEXT_LEN = 20000
MAX_NAME_LEN = 128
MAX_DEPTH = 4  # mirrors files.MAX_DEPTH

#: Local scheme identifier prefix for tree paths (display/identity only).
VIKING_SCHEME = "viking://"

#: Context groups in display order (mirrors the console tree client order).
GROUPS = ("user", "agent", "resources", "skills")

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ContextError(Exception):
    """Typed context-engine failure (never a traceback on the wire)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    """Metadata-only audit (names/counts, never content)."""
    try:
        from voicebridge.logging_setup import log_event

        log_event(f"context.{op}", actor=actor,
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def _estop_engaged(cfg: Any) -> bool:
    try:
        from voicebridge.plugins import estop_engaged as _engaged

        return bool(_engaged(cfg))
    except Exception:
        return False


def _require_confirmed(confirmed: bool, what: str) -> None:
    if not confirmed:
        raise ContextError(CONFIRM_CODE,
                            f"{what} requires explicit confirmation")


def _refuse_estop(what: str) -> None:
    raise ContextError(ESTOP_CODE,
                       f"emergency stop latched; {what} refused")


# -- layout ----------------------------------------------------------------

def root(cfg: Any) -> Path:
    """The Qubit-owned context root (created on first use)."""
    path = Path(cfg.state_dir()) / "context"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ContextError(IO_CODE, f"context root unavailable: {exc}")
    return path


def group_dir(cfg: Any, group: str) -> Path:
    """One context group directory (created on first use)."""
    if group not in GROUPS:
        raise ContextError(BAD_PARAMS_CODE, f"unknown context group {group!r}")
    path = root(cfg) / group
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ContextError(IO_CODE, f"context group unavailable: {exc}")
    return path


def to_viking_uri(path: str) -> str:
    """Local scheme identifier for a tree path (identity only)."""
    return VIKING_SCHEME + str(path or "").lstrip("/")


def _resolve(cfg: Any, rel: str) -> Path:
    """Confine a tree-relative path under the context root.

    Traversal (``..``, absolute paths, schemes, control chars) is refused
    with a typed error — nothing outside the root is ever touched.
    """
    raw = str(rel or "").strip().lstrip("/")
    if raw.startswith("viking://"):
        raw = raw[len("viking://"):]
    if not raw or raw in (".", "/"):
        raise ContextError(BAD_PATH_CODE, "path is required")
    if ("/../" in f"/{raw}/" or raw.startswith("../")
            or raw.endswith("/..") or "//" in raw):
        raise ContextError(BAD_PATH_CODE, f"path escapes the context root")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in raw):
        raise ContextError(BAD_PATH_CODE, "path carries control characters")
    if len(raw) > 1024:
        raise ContextError(BAD_PATH_CODE, "path is too long")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or len(parts) > MAX_DEPTH + 2:
        raise ContextError(BAD_PATH_CODE, "path is too deep")
    if parts[0] not in GROUPS:
        raise ContextError(BAD_PATH_CODE,
                           f"unknown context group {parts[0]!r}")
    for part in parts[1:]:
        if part == ".." or not _NAME_RE.match(part):
            raise ContextError(BAD_PATH_CODE, f"bad path segment {part!r}")
    base = root(cfg)
    target = (base / Path(*parts)).resolve()
    try:
        if not target.is_relative_to(base.resolve()):
            raise ContextError(BAD_PATH_CODE,
                               "path escapes the context root")
    except ContextError:
        raise
    except Exception:
        raise ContextError(BAD_PATH_CODE, "path escapes the context root")
    return target


# -- tier files (L0 abstract / L1 overview; L2 is the live listing) --------

_ABSTRACTS = {
    "user": "Qubit user memory: profile, preferences, and long-term notes.",
    "agent": "Qubit agent memory: committed session transcripts + experience.",
    "resources": "Qubit context resources: uploaded docs and files.",
    "skills": "Qubit skill packages: index of installed skill docs.",
}


def _refresh_tiers(cfg: Any, group: str) -> None:
    """(Re)write the L0/L1 tier files for one group (derived, always safe)."""
    gdir = group_dir(cfg, group)
    names: List[str] = []
    try:
        names = sorted(p.name for p in gdir.iterdir()
                       if p.is_file() and not p.name.startswith("."))
    except OSError:
        names = []
    lines = [f"# {group} — overview (L1)",
             "",
             _ABSTRACTS[group],
             "",
             f"files: {len(names)}",
             ""]
    for name in names[:MAX_LIST_ENTRIES]:
        lines.append(f"- {name}")
    try:
        (gdir / ".abstract.md").write_text(
            _ABSTRACTS[group] + "\n", encoding="utf-8")
        (gdir / ".overview.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ContextError(IO_CODE, f"tier files unavailable: {exc}")


def tier_files(cfg: Any, group: str) -> Dict[str, str]:
    """Render the L0/L1 tier files for one group (refresh-then-read)."""
    _refresh_tiers(cfg, group)
    gdir = group_dir(cfg, group)
    try:
        abstract = (gdir / ".abstract.md").read_text(encoding="utf-8")
        overview = (gdir / ".overview.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise ContextError(IO_CODE, f"tier files unreadable: {exc}")
    return {"abstract": abstract, "overview": overview,
            "levels": ["L0", "L1", "L2"]}


# -- seed (copy-only; live originals are read, never moved/written) --------

def seed(cfg: Any) -> Dict[str, Any]:
    """Copy seed content into the context tree (copies only, idempotent).

    Seeds ``USER.md``/``MEMORY.md`` from the runtime ``memories/`` dir into
    ``context/user/`` when the copy is absent, and (re)writes the derived
    ``context/skills/index.md`` from the skills catalog. Returns what was
    seeded; never touches a live original beyond reading it.
    """
    done: Dict[str, Any] = {"copied": [], "skills_indexed": 0}
    udir = group_dir(cfg, "user")
    candidates: List[Path] = []
    try:
        base = Path(cfg.state_dir())
        # Live runtime home first (``<state>/runtime/memories``), then the
        # state dir itself — both Qubit-owned state, never the reference.
        candidates = [base / "runtime" / "memories", base / "memories"]
    except Exception:
        candidates = []
    src_mem = next((c for c in candidates if c.is_dir()), None)
    if src_mem is not None:
        for name in ("USER.md", "MEMORY.md"):
            src = src_mem / name
            dst = udir / name
            try:
                if src.is_file() and not dst.exists():
                    dst.write_bytes(src.read_bytes())
                    done["copied"].append(name)
            except OSError:
                continue
    try:
        rows = skills_index(cfg).get("skills", [])
    except ContextError:
        rows = []
    lines = ["# skills — index (derived from the skills catalog)", "",
             f"skills: {len(rows)}", ""]
    for row in rows[:MAX_LIST_ENTRIES]:
        title = str(row.get("title") or row.get("id", ""))
        desc = str(row.get("description") or "")
        lines.append(f"- {row.get('id', '')} — {title}"
                     + (f": {desc[:120]}" if desc else ""))
    try:
        (group_dir(cfg, "skills") / "index.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        done["skills_indexed"] = len(rows)
    except OSError:
        pass
    return done


def ensure(cfg: Any) -> Dict[str, Any]:
    """Create the layout, seed copies, and refresh every tier file."""
    for group in GROUPS:
        group_dir(cfg, group)
    seeded = seed(cfg)
    for group in GROUPS:
        _refresh_tiers(cfg, group)
    counts = {g: _count(group_dir(cfg, g)) for g in GROUPS}
    return {"state": "OK", "root": "state-home context/",
            "groups": list(GROUPS), "counts": counts, "seeded": seeded,
            "authority": "qubit local context engine (state-home context/)"}


def _count(gdir: Path) -> int:
    try:
        return sum(1 for p in gdir.iterdir()
                   if p.is_file() and not p.name.startswith("."))
    except OSError:
        return 0


# -- reads -----------------------------------------------------------------

def status(cfg: Any) -> Dict[str, Any]:
    """Local engine status (read-only; creates the layout, seeds copies)."""
    info = ensure(cfg)
    return {"ok": True, "state": "OK", "connection": "local",
            "server_base_url": "", "engine": "qubit-local-context",
            "scheme": VIKING_SCHEME, "counts": info["counts"],
            "seeded": info["seeded"],
            "authority": "qubit local context engine (state-home context/)"}


def tree(cfg: Any) -> Dict[str, Any]:
    """Full context tree grouped for the console tree (read-only)."""
    ensure(cfg)
    base = root(cfg)
    groups = []
    for group in GROUPS:
        gdir = base / group
        nodes = []
        try:
            files = sorted(p for p in gdir.iterdir()
                           if p.is_file() and not p.name.startswith("."))
        except OSError:
            files = []
        for path in files[:MAX_LIST_ENTRIES]:
            rel = f"{group}/{path.name}"
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            nodes.append({"id": rel, "kind": "file", "label": path.name,
                          "path": rel, "viking_uri": to_viking_uri(rel),
                          "size": size})
        groups.append({"key": group, "label": group.replace("_", " ").title(),
                       "nodes": nodes})
    return {"state": "OK", "root": "state-home context/", "groups": groups,
            "authority": "qubit local context engine (state-home context/)"}


def read_file(cfg: Any, path: str, mode: str = "preview") -> Dict[str, Any]:
    """Read one tree file: Preview (truncated) or Source (full, bounded)."""
    target = _resolve(cfg, path)
    if not target.is_file():
        raise ContextError(NOT_FOUND_CODE, f"no context file {path!r}")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise ContextError(IO_CODE, f"context file unreadable: {exc}")
    if size > MAX_FILE_BYTES:
        raise ContextError(TOO_LARGE_CODE,
                           f"context file exceeds {MAX_FILE_BYTES} bytes")
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ContextError(IO_CODE, f"context file unreadable: {exc}")
    rel = target.resolve().relative_to(
        root(cfg).resolve()).as_posix()
    truncated = False
    if str(mode or "preview").lower() != "source" \
            and len(text) > MAX_PREVIEW_CHARS:
        text = text[:MAX_PREVIEW_CHARS]
        truncated = True
    digest = hashlib.sha256(
        target.read_bytes() if size <= MAX_FILE_BYTES else b"").hexdigest()[:16]
    return {"state": "OK", "path": rel, "viking_uri": to_viking_uri(rel),
            "content": text, "truncated": truncated, "size": size,
            "sha256_16": digest, "mode": "source" if not truncated
            and str(mode or "").lower() == "source" else "preview",
            "authority": "qubit local context engine (state-home context/)"}


# -- writes (confirm-gated, e-stop gated, audited, metadata-only) -----------

def _slug(raw: str, default: str = "item") -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw or "").strip())[:64]
    slug = slug.strip("-") or default
    if not _NAME_RE.match(slug):
        slug = default
    return slug


def add_resource(cfg: Any, uri: str, title: str = "", notes: str = "",
                 content: str = "", *, confirmed: bool = False,
                 requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Add a resource record under ``resources/`` (confirm-gated)."""
    _require_confirmed(confirmed, "resource-add")
    if _estop_engaged(cfg):
        _refuse_estop("resource-add")
    name_src = str(title or "").strip() or str(uri or "").strip()
    if not name_src:
        raise ContextError(BAD_PARAMS_CODE, "uri or title is required")
    if name_src.startswith("viking://"):
        name_src = name_src[len("viking://"):]
    base = name_src.rstrip("/").split("/")[-1]
    slug = _slug(base, "resource")
    if len(str(content or "")) > MAX_FILE_BYTES:
        raise ContextError(TOO_LARGE_CODE, "resource content exceeds 1 MiB")
    rdir = group_dir(cfg, "resources")
    target = rdir / f"{slug}.md"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    body = (f"# {title or slug}\n\n- uri: {uri}\n- added: {stamp}Z\n"
            + (f"- notes: {notes}\n" if notes else "")
            + "\n" + str(content or ""))
    try:
        target.write_text(body, encoding="utf-8")
    except OSError as exc:
        raise ContextError(IO_CODE, f"resource write failed: {exc}")
    _refresh_tiers(cfg, "resources")
    _audit("resource_add", requested_by,
           {"path": f"resources/{slug}.md", "size": str(len(body))})
    rel = f"resources/{slug}.md"
    return {"state": "OK", "path": rel, "viking_uri": to_viking_uri(rel),
            "size": len(body),
            "authority": "qubit local context engine (state-home context/)"}


def add_memory(cfg: Any, text: str, scope: str = "user", *,
               confirmed: bool = False,
               requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Append a memory note to ``user/memories.md`` (confirm-gated)."""
    _require_confirmed(confirmed, "memory-add")
    if _estop_engaged(cfg):
        _refuse_estop("memory-add")
    body = str(text or "").strip()
    if not body:
        raise ContextError(BAD_PARAMS_CODE, "text is required")
    if len(body) > MAX_TEXT_LEN:
        raise ContextError(TOO_LARGE_CODE, "memory text exceeds 20000 chars")
    udir = group_dir(cfg, "user")
    target = udir / "memories.md"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    entry = f"\n## {stamp}Z [{_slug(str(scope or 'user'))}]\n\n{body}\n"
    try:
        with target.open("a", encoding="utf-8") as fh:
            fh.write(entry)
        size = target.stat().st_size
    except OSError as exc:
        raise ContextError(IO_CODE, f"memory write failed: {exc}")
    _refresh_tiers(cfg, "user")
    _audit("memory_add", requested_by,
           {"path": "user/memories.md", "chars": str(len(body))})
    return {"state": "OK", "path": "user/memories.md",
            "viking_uri": to_viking_uri("user/memories.md"), "size": size,
            "authority": "qubit local context engine (state-home context/)"}


def _read_session_transcript(cfg: Any, session_id: str,
                             limit: int = 200) -> List[Dict[str, str]]:
    """Read a session transcript slice from the runtime state.db (read-only)."""
    try:
        db_path = Path(cfg.state_dir()) / "state.db"
    except Exception:
        return []
    if not db_path.is_file():
        return []
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = db.execute(
                "SELECT role, content FROM messages WHERE session_id = ? "
                "ORDER BY timestamp LIMIT ?", (session_id, limit)).fetchall()
        finally:
            db.close()
    except Exception:
        return []
    return [{"role": str(r or ""), "content": str(c or "")}
            for r, c in rows]


def commit_session(cfg: Any, session_id: str,
                   transcript: Optional[List[Dict[str, Any]]] = None,
                   note: str = "", *, confirmed: bool = False,
                   requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Commit a session transcript slice to agent long-term (confirm-gated).

    The transcript slice is taken from the explicit ``transcript`` argument
    or read from the runtime ``state.db`` (read-only); an experience note is
    appended to ``agent/experience.md`` alongside.
    """
    _require_confirmed(confirmed, "session commit")
    if _estop_engaged(cfg):
        _refuse_estop("session commit")
    sid = _slug(str(session_id or ""), "")
    if not sid:
        raise ContextError(BAD_PARAMS_CODE, "session_id is required")
    rows = list(transcript or []) or _read_session_transcript(cfg, sid)
    if not rows:
        raise ContextError(BAD_PARAMS_CODE,
                           f"no transcript for session {sid!r}")
    adir = group_dir(cfg, "agent")
    sdir = adir / "sessions"
    try:
        sdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ContextError(IO_CODE, f"sessions dir unavailable: {exc}")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    lines = [f"# session {sid} — committed {stamp}Z", ""]
    for row in rows[:200]:
        role = str((row or {}).get("role", "") or "?")[:32]
        content = str((row or {}).get("content", "") or "")[:2000]
        lines.append(f"### {role}\n\n{content}\n")
    try:
        (sdir / f"{sid}.md").write_text("\n".join(lines) + "\n",
                                        encoding="utf-8")
    except OSError as exc:
        raise ContextError(IO_CODE, f"session commit failed: {exc}")
    if str(note or "").strip():
        try:
            with (adir / "experience.md").open("a",
                                               encoding="utf-8") as fh:
                fh.write(f"\n## {stamp}Z [{sid}]\n\n"
                         f"{str(note).strip()[:2000]}\n")
        except OSError as exc:
            raise ContextError(IO_CODE, f"experience note failed: {exc}")
    _refresh_tiers(cfg, "agent")
    _audit("session_commit", requested_by,
           {"session": sid, "turns": str(min(len(rows), 200))})
    rel = f"agent/sessions/{sid}.md"
    return {"state": "OK", "session_id": sid, "path": rel,
            "viking_uri": to_viking_uri(rel),
            "turns": min(len(rows), 200),
            "authority": "qubit local context engine (state-home context/)"}


# -- find (one-shot keyword over the local index + session FTS) --------------

def _snippet(text: str, needle: str, width: int = 80) -> str:
    low = text.lower()
    at = low.find(needle.lower())
    if at < 0:
        return text[:2 * width].replace("\n", " ")
    start = max(0, at - width)
    return text[start:at + width].replace("\n", " ")


def _walk_hits(cfg: Any, needle: str, limit: int) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    base = root(cfg)
    try:
        files = sorted(base.rglob("*.md"))
    except OSError:
        return hits
    for path in files:
        if len(hits) >= limit:
            break
        if path.name.startswith("."):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle.lower() not in text.lower():
            continue
        rel = path.resolve().relative_to(base.resolve()).as_posix()
        hits.append({"id": rel, "title": path.name,
                     "snippet": _snippet(text, needle),
                     "path": rel, "viking_uri": to_viking_uri(rel),
                     "score": 1.0, "source": "context-file"})
    return hits


def _fts_hits(cfg: Any, needle: str, limit: int) -> List[Dict[str, Any]]:
    """One-shot keyword over the existing session FTS table (fail-soft)."""
    try:
        db_path = Path(cfg.state_dir()) / "state.db"
    except Exception:
        return []
    if not db_path.is_file():
        return []
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = db.execute(
                "SELECT session_id, content FROM messages_fts "
                "WHERE messages_fts MATCH ? LIMIT ?",
                (needle, limit)).fetchall()
        finally:
            db.close()
    except Exception:
        return []
    hits = []
    for sid, content in rows:
        text = str(content or "")
        hits.append({"id": f"session:{sid}", "title": f"session {sid}",
                     "snippet": _snippet(text, needle),
                     "path": "", "viking_uri": "",
                     "score": 0.5, "source": "session-fts"})
    return hits


def search(cfg: Any, query: str, limit: int = 20) -> Dict[str, Any]:
    """One-shot keyword find over the local index (no new vector infra)."""
    needle = str(query or "").strip()[:MAX_QUERY_LEN]
    if not needle:
        raise ContextError(BAD_PARAMS_CODE, "q is required")
    try:
        count = max(1, min(int(limit or 20), MAX_FIND_HITS))
    except (TypeError, ValueError):
        count = 20
    ensure(cfg)
    hits = _walk_hits(cfg, needle, count)
    if len(hits) < count:
        seen = {h["id"] for h in hits}
        for hit in _fts_hits(cfg, needle, count - len(hits)):
            if hit["id"] not in seen:
                hits.append(hit)
    return {"state": "OK", "query": needle, "hits": hits,
            "count": len(hits),
            "authority": "qubit local context engine (state-home context/)"}


def related(cfg: Any, path: str) -> Dict[str, Any]:
    """Related-context lookup: same-dir siblings + same-group keyword kin."""
    target = _resolve(cfg, path)
    if not target.is_file():
        raise ContextError(NOT_FOUND_CODE, f"no context file {path!r}")
    try:
        text = target.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        text = ""
    words = [w.lower() for w in re.findall(r"[A-Za-z]{5,}", text)]
    stop = {"about", "which", "their", "there", "these", "those", "would",
            "could", "should", "context", "qubit", "files", "overview"}
    keys = [w for w in dict.fromkeys(words) if w not in stop][:5]
    rel = target.resolve().relative_to(root(cfg).resolve()).as_posix()
    out: List[Dict[str, Any]] = []
    try:
        siblings = sorted(p for p in target.parent.iterdir()
                          if p.is_file() and not p.name.startswith(".")
                          and p.name != target.name)
    except OSError:
        siblings = []
    for sib in siblings[:10]:
        srel = sib.resolve().relative_to(
            root(cfg).resolve()).as_posix()
        out.append({"id": srel, "title": sib.name, "path": srel,
                    "viking_uri": to_viking_uri(srel),
                    "score": 0.8, "reason": "same-folder"})
    if keys:
        for hit in _walk_hits(cfg, keys[0], MAX_FIND_HITS):
            if hit["id"] != rel and hit["id"] not in {h["id"] for h in out}:
                hit["reason"] = "keyword-kin"
                out.append(hit)
            if len(out) >= 20:
                break
    return {"state": "OK", "path": rel,
            "viking_uri": to_viking_uri(rel), "items": out,
            "count": len(out),
            "authority": "qubit local context engine (state-home context/)"}


# -- sessions + skills (read existing stores; no new registries) -------------

def bot_sessions(cfg: Any, limit: int = 50) -> Dict[str, Any]:
    """Bot sessions from the runtime session store (read-only, bounded)."""
    try:
        count = max(1, min(int(limit or 50), 200))
    except (TypeError, ValueError):
        count = 50
    sessions: List[Dict[str, Any]] = []
    try:
        db_path = Path(cfg.state_dir()) / "state.db"
    except Exception:
        db_path = None
    if db_path is not None and db_path.is_file():
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                cols = [r[1] for r in db.execute(
                    "PRAGMA table_info(sessions)").fetchall()]
                pick = {"id": None, "title": None, "state": None,
                        "updated": None}
                for cand, keys in (
                        ("id", ("session_id", "id", "key")),
                        ("title", ("title", "name", "label")),
                        ("state", ("state", "status")),
                        ("updated", ("updated_at", "updated", "timestamp",
                                     "last_active"))):
                    for key in keys:
                        if key in cols:
                            pick[cand] = key
                            break
                if pick["id"]:
                    sel = ", ".join(c for c in
                                    (pick["id"], pick["title"], pick["state"],
                                     pick["updated"]) if c)
                    for row in db.execute(
                            f"SELECT {sel} FROM sessions LIMIT ?",
                            (count,)).fetchall():
                        entry: Dict[str, Any] = {
                            "id": str(row[0] if row else "")}
                        idx = 1
                        for slot in ("title", "state", "updated_at"):
                            key = {"title": pick["title"],
                                   "state": pick["state"],
                                   "updated_at": pick["updated"]}[slot]
                            if key and idx < len(row):
                                entry[slot] = str(row[idx] if row[idx]
                                                  is not None else "")
                                idx += 1
                        sessions.append(entry)
            finally:
                db.close()
        except Exception:
            sessions = []
    return {"state": "OK", "sessions": sessions, "count": len(sessions),
            "authority": "qubit local context engine (state-home context/)"}


def skills_list(cfg: Any) -> Dict[str, Any]:
    """Skills catalog rows (read the catalog; no new registry)."""
    rows: List[Dict[str, Any]] = []
    try:
        from voicebridge.skill_catalog import catalog_entries

        for entry in catalog_entries(cfg):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "") or "")
            if not name:
                continue
            rows.append({"id": name,
                         "title": str(entry.get("title", "") or name),
                         "description": str(entry.get("description", "") or
                                            "")[:300]})
    except Exception:
        rows = []
    return {"state": "OK", "skills": rows, "count": len(rows),
            "authority": "qubit skills catalog (read-only index)"}


def skills_index(cfg: Any) -> Dict[str, Any]:
    """Alias of :func:`skills_list` for the seeder (same read-only source)."""
    return skills_list(cfg)


# -- gateway entry points (registered by the family bridge, one wiring owner)

def _gw_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return status(cfg)


def _gw_tree(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return tree(cfg)


def _gw_file(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    path = str(params.get("path", "") or "")
    if not path.strip():
        raise ContextError(BAD_PARAMS_CODE, "path is required")
    return read_file(cfg, path, str(params.get("mode", "") or "preview"))


def _gw_search(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    query = str(params.get("q", params.get("query", "")) or "")
    if not query.strip():
        raise ContextError(BAD_PARAMS_CODE, "q is required")
    return search(cfg, query, params.get("limit", 20))


def _confirmed(params: Dict[str, Any]) -> bool:
    return bool(params.get("confirmed", False))


def _by(params: Dict[str, Any]) -> str:
    return str(params.get("requested_by", "") or "dashboard:user")


def _gw_resource_add(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return add_resource(cfg, str(params.get("uri", "") or ""),
                        str(params.get("title", "") or ""),
                        str(params.get("notes", "") or ""),
                        str(params.get("content", "") or ""),
                        confirmed=_confirmed(params),
                        requested_by=_by(params))


def _gw_memory_add(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return add_memory(cfg, str(params.get("text", "") or ""),
                      str(params.get("scope", "") or "user"),
                      confirmed=_confirmed(params),
                      requested_by=_by(params))


def _gw_related(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    path = str(params.get("path", "") or "")
    if not path.strip():
        raise ContextError(BAD_PARAMS_CODE, "path is required")
    return related(cfg, path)


def _gw_bot_sessions(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return bot_sessions(cfg, params.get("limit", 50))


def _gw_skills(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return skills_list(cfg)


#: Gateway method name -> handler (registered onto each bridge registry by
#: the family bridge in ``voicebridge/qubit_compat.py`` — the one wiring
#: owner). Reads execute immediately; the two POST rows are confirm-gated
#: mutations through the shared journal (409 before confirm, exactly-once
#: execute after).
VIKING_GATEWAY_METHODS = {
    "viking.status": _gw_status,
    "viking.tree": _gw_tree,
    "viking.file": _gw_file,
    "viking.search": _gw_search,
    "viking.resource_add": _gw_resource_add,
    "viking.memory_add": _gw_memory_add,
    "viking.related": _gw_related,
    "viking.bot_sessions": _gw_bot_sessions,
    "viking.skills": _gw_skills,
}
