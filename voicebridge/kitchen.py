"""Kitchen backend — decompose a staged source and map its capabilities.

Kitchen's UI (frontend/qubit-web/src/pages/KitchenPage.tsx) states a four-stage
pipeline: 1 Decompose, 2 Map capabilities, 3 Draft a 1:1 plan, 4 Absorb.

This module implements stages 1-2 for real, using REA (the reverse-engineering
MCP/CLI) as the analysis engine. REA is used ONLY here — it is not a general
Qubit capability.

Two intake modes, matching Kitchen's UI:
  * repo   — a git URL, cloned to a confined temp dir first
  * folder — a path already on disk

Nothing is uploaded anywhere. All analysis is local.

Honest limits (surfaced in the response, never hidden):
  * Native binary RE needs Ghidra/Hopper/IDA — NOT installed here. JS/Electron,
    .NET and artifact inspection work without any engine.
  * Stage 3 (integration plan) and stage 4 (absorb) are NOT implemented; the
    response says so rather than implying work that did not happen.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# REA is resolved from PATH (mise installs it at ~/.local/share/mise/.../bin/rea).
REA_BIN = shutil.which("rea") or "rea"
GIT_BIN = shutil.which("git") or "git"

# Clone confined under the Qubit runtime temp area, never the repo tree.
CLONE_ROOT = Path(
    os.environ.get("QUBIT_RUNTIME_DIR")
    or (Path.home() / ".local" / "state" / "qubit-voicebridge" / "runtime")
) / "kitchen"

_TIMEOUT_CLONE = 180
_TIMEOUT_ANALYZE = 300

# A safe git remote: https or git@, no local paths, no options.
_REPO_RE = re.compile(r"^(https://[^\s]+|git@[^\s:]+:[^\s]+)$")
# Directory names never worth walking for a capability map.
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".next", "target", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}


def _fail(detail: str, code: str = "KITCHEN_ERROR", status: int = 400):
    return status, {"ok": False, "code": code, "detail": detail}


def _run(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 60):
    """Run a command, never raising; return (rc, stdout, stderr)."""
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True,
            text=True, timeout=timeout,
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except FileNotFoundError as e:
        return 127, "", f"not found: {e}"


def _clone_repo(url: str) -> tuple[Optional[Path], Optional[str]]:
    """Shallow-clone into a confined temp dir. Returns (path, error)."""
    if not _REPO_RE.match(url):
        return None, (
            "Only https:// or git@ remotes are accepted (no local paths, no "
            "leading-dash arguments)."
        )
    CLONE_ROOT.mkdir(parents=True, exist_ok=True)
    dest = Path(tempfile.mkdtemp(prefix="src-", dir=str(CLONE_ROOT)))
    rc, _, err = _run(
        [GIT_BIN, "clone", "--depth", "1", "--", url, str(dest)],
        timeout=_TIMEOUT_CLONE,
    )
    if rc != 0:
        shutil.rmtree(dest, ignore_errors=True)
        return None, f"git clone failed (rc={rc}): {err.strip()[:400]}"
    return dest, None


def _inventory(root: Path, cap: int = 4000) -> Dict[str, Any]:
    """Cheap, deterministic file inventory + language mix (stage 1 'decompose')."""
    by_ext: Dict[str, int] = {}
    files: List[str] = []
    total_bytes = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            total_bytes += sz
            ext = p.suffix.lower() or "(none)"
            by_ext[ext] = by_ext.get(ext, 0) + 1
            if len(files) < cap:
                files.append(str(p.relative_to(root)))
    top = sorted(by_ext.items(), key=lambda kv: -kv[1])[:15]
    return {
        "file_count": sum(by_ext.values()),
        "total_bytes": total_bytes,
        "by_extension": [{"ext": e, "count": c} for e, c in top],
        "sample_files": files[:60],
    }


def _entry_points(root: Path) -> List[Dict[str, str]]:
    """Find likely entry points / manifests (stage 1 evidence)."""
    names = {
        "package.json": "node package",
        "pyproject.toml": "python package",
        "setup.py": "python package",
        "Cargo.toml": "rust crate",
        "go.mod": "go module",
        "manifest.json": "web extension / app manifest",
        "plugin.yaml": "plugin manifest",
        "SKILL.md": "agent skill",
        "Dockerfile": "container build",
        "README.md": "docs",
    }
    out: List[Dict[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn in names:
                rel = str((Path(dirpath) / fn).relative_to(root))
                if rel.count(os.sep) <= 2:  # shallow only
                    out.append({"path": rel, "kind": names[fn]})
        if len(out) >= 40:
            break
    return out


def _rea_analyze_js(target: Path) -> Dict[str, Any]:
    """Ask REA to statically analyse a JS/Electron tree. Local, no upload."""
    rc, out, err = _run(
        [REA_BIN, "analyze-javascript-application", str(target), "--json"],
        timeout=_TIMEOUT_ANALYZE,
    )
    if rc != 0:
        return {"ok": False, "rc": rc, "error": (err or out).strip()[:500]}
    # REA streams progress lines then a final JSON document.
    final = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "rea_progress" in d:
            continue
        final = d
    if not final:
        return {"ok": False, "rc": 0, "error": "no final JSON document returned"}
    return {"ok": True, "document": final}


def _capabilities() -> Dict[str, Any]:
    rc, out, _ = _run([REA_BIN, "capabilities", "--format", "json"], timeout=90)
    if rc != 0:
        return {"ok": False, "error": "rea capabilities failed"}
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        return {"ok": False, "error": "unparseable capabilities output"}
    caps = d.get("capabilities", [])
    return {
        "ok": True,
        "available": [c["operation"] for c in caps if c.get("available")],
        "unavailable": [
            {"operation": c["operation"], "code": c.get("availability_code")}
            for c in caps if not c.get("available")
        ],
    }


def handle(
    cfg: Any = None,
    q: Optional[Dict[str, str]] = None,
    b: Optional[Dict[str, Any]] = None,
    p: Optional[Dict[str, str]] = None,
) -> tuple[int, Any]:
    """POST /api/qubit/kitchen/analyze

    Body: {"mode": "repo"|"folder", "source": "<git url or absolute path>"}

    Returns stages 1-2 only. Stages 3-4 are reported as not implemented.
    """
    b = b or {}
    mode = str(b.get("mode") or "").strip().lower()
    source = str(b.get("source") or "").strip()

    if mode not in ("repo", "folder"):
        return _fail("mode must be 'repo' or 'folder'")
    if not source:
        return _fail("source is required")

    cloned = False
    root: Path
    if mode == "repo":
        cloned_root, err = _clone_repo(source)
        if err or cloned_root is None:
            return _fail(err or "clone produced no directory", code="KITCHEN_CLONE_FAILED")
        root = cloned_root
        cloned = True
    else:
        root = Path(source).expanduser()
        if not root.is_absolute():
            return _fail("folder mode requires an absolute path")
        if not root.is_dir():
            return _fail(f"not a directory: {root}", code="KITCHEN_NO_SOURCE")

    try:
        inv = _inventory(root)
        entries = _entry_points(root)
        rea_caps = _capabilities()

        # Only run the JS analyser when the tree actually looks like JS/Electron.
        js_like = any(
            e["ext"] in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")
            for e in inv["by_extension"]
        )
        rea_js = (
            _rea_analyze_js(root) if js_like
            else {"ok": False, "skipped": "no JavaScript/TypeScript sources in tree"}
        )

        stage3_note = (
            "Not implemented. Kitchen's stages 3 (draft a 1:1 plan) and 4 "
            "(absorb) are not wired — this endpoint returns stages 1-2 only. "
            "Nothing was written to the Qubit source tree."
        )

        return 200, {
            "ok": True,
            "mode": mode,
            "source": source,
            "root": str(root),
            "cloned": cloned,
            "stage1_decompose": {
                "inventory": inv,
                "entry_points": entries,
            },
            "stage2_map_capabilities": {
                "rea_javascript": rea_js,
                "rea_environment": rea_caps,
                "note": (
                    "REA analyses locally. Native binary analysis needs "
                    "Ghidra/Hopper/IDA, which are not installed here; "
                    "JS/Electron, .NET and artifact inspection need no engine."
                ),
            },
            "stage3_plan": {"implemented": False, "detail": stage3_note},
            "stage4_absorb": {"implemented": False, "detail": stage3_note},
        }
    finally:
        if cloned:
            shutil.rmtree(root, ignore_errors=True)
