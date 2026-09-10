"""Qubit foreign-agent import via quarantined inspection.

Qubit ``import-agent [{claude-code,codex}]`` maps another coding agent's
setup (instructions, permission allowlists, MCP servers, skills, memories)
into Qubit equivalents with a preview, never importing API keys. Reading
unreviewed third-party state into Qubit would violate the reviewed-code
policy, so the Qubit-native surface is split:

* ``inspect`` — quarantined read-only inventory of a foreign agent tree:
  only well-known filenames are opened (``CLAUDE.md``, ``AGENTS.md``,
  ``settings.json``, ``config.json``, ``mcp.json``), each bounded
  (:data:`MAX_FILE_BYTES`), content treated as untrusted evidence
  (redacted echoes, structural metadata only: file presence, line counts,
  declared-key names — never full values, never executed, never stored);
* ``import`` — typed ``IMPORT_AGENT_REVIEW_REQUIRED`` refusal: anything
  beyond inspection needs allowlist review with hash pin and explicit
  confirmation (the reviewed artifact then installs via ``skills
  install``); credentials are never imported.

The inspector never writes outside its returned dict, never follows
symlinks outside the inspected root, and never touches the network.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_FILE_BYTES = 262144
MAX_FILES = 64

REVIEW_CODE = "IMPORT_AGENT_REVIEW_REQUIRED"

KINDS = ("claude-code", "codex")

# Well-known filenames per foreign agent kind (relative to the source root).
# Nothing else is ever opened.
KNOWN_FILES = {
    "claude-code": ("CLAUDE.md", "settings.json", ".mcp.json", "mcp.json"),
    "codex": ("AGENTS.md", "config.json", "config.toml"),
}


class ImportAgentError(Exception):
    """Typed import-agent failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _detect(root: Path) -> Optional[str]:
    for kind, names in KNOWN_FILES.items():
        for name in names:
            try:
                if (root / name).is_file():
                    return kind
            except OSError:
                continue
    return None


def _safe_join(root: Path, name: str) -> Optional[Path]:
    try:
        candidate = (root / name).resolve(strict=False)
        root_resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    if candidate.is_symlink():
        return None
    return candidate if candidate.is_file() else None


def _summarize_text(blob: bytes, limit_lines: int = 400) -> Dict[str, Any]:
    try:
        text = blob.decode("utf-8", errors="replace")
    except Exception:
        return {"decodable": False, "lines": 0, "bytes": len(blob)}
    lines = text.splitlines()[:limit_lines]
    return {"decodable": True, "lines": len(text.splitlines()),
            "bytes": len(blob),
            "first_line": _redact(lines[0])[:120] if lines else ""}


def _summarize_json(blob: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(blob)
    except ValueError:
        return {"valid_json": False}
    if not isinstance(data, dict):
        return {"valid_json": True, "shape": type(data).__name__}
    return {"valid_json": True, "top_level_keys": sorted(str(k) for k in
                                                         list(data)[:32])}


def _default_source() -> str:
    """Auto-detect a foreign agent tree below home (existence only)."""
    home = Path("~").expanduser()
    for candidate in (home / ".claude", home / ".codex"):
        try:
            if candidate.is_dir() and _detect(candidate) is not None:
                return str(candidate)
        except OSError:
            continue
    raise ImportAgentError("IMPORT_AGENT_BAD_SOURCE",
                           "no foreign agent tree detected below home; pass "
                           "--source explicitly")


def inspect_tree(source: str, kind: str = "") -> Dict[str, Any]:
    """Quarantined inventory of a foreign agent tree (read-only)."""
    try:
        root = Path(os.path.expanduser((source or "").strip())).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ImportAgentError("IMPORT_AGENT_BAD_SOURCE",
                               f"source unreadable: {exc}") from exc
    if not root.is_dir():
        raise ImportAgentError("IMPORT_AGENT_BAD_SOURCE",
                               "source is not a directory")
    clean_kind = (kind or "").strip().lower()
    if clean_kind and clean_kind not in KINDS:
        raise ImportAgentError("IMPORT_AGENT_BAD_KIND",
                               f"kind must be one of {KINDS}")
    detected = _detect(root)
    if clean_kind and detected and clean_kind != detected:
        raise ImportAgentError("IMPORT_AGENT_BAD_KIND",
                               f"source looks like {detected}, not {clean_kind}")
    active = clean_kind or detected
    names = KNOWN_FILES.get(active, ()) if active else tuple(
        n for names in KNOWN_FILES.values() for n in names)
    files: List[Dict[str, Any]] = []
    for name in names[:MAX_FILES]:
        target = _safe_join(root, name)
        if target is None:
            continue
        try:
            if target.stat().st_size > MAX_FILE_BYTES:
                files.append({"file": name, "state": "TOO_LARGE",
                              "bytes": target.stat().st_size})
                continue
            blob = target.read_bytes()[:MAX_FILE_BYTES + 1]
        except OSError as exc:
            files.append({"file": name, "state": "UNREADABLE",
                          "detail": _redact(str(exc))[:120]})
            continue
        entry: Dict[str, Any] = {"file": name, "state": "OK"}
        if name.endswith(".md"):
            entry.update(_summarize_text(blob))
        else:
            entry.update(_summarize_json(blob))
        files.append(entry)
    return {"state": "OK", "schema": "qubit-import-inspect/1",
            "root": str(root), "detected_kind": detected,
            "inspected_kind": active,
            "files": files,
            "handoff": "structural inventory only: content is untrusted "
                       "evidence and is never imported, executed, or stored; "
                       "reviewed artifacts install via `skills install`"}


def refuse_import() -> Dict[str, Any]:
    """Typed refusal for content import (reviewed-code policy)."""
    return {"state": "REFUSED", "code": REVIEW_CODE,
            "explanation": "Qubit Agent cannot do this directly: importing "
                           "unreviewed third-party agent state would bypass "
                           "review; inspect it with `import-agent inspect`, "
                           "review the findings, then install reviewed "
                           "artifacts via `skills install --confirm`. "
                           "Credentials are never imported.",
            "owner": "qubit-policy",
            "rollback": "nothing imported; nothing to roll back"}
