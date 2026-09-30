"""Packet 8K: release certification — portability, packaging, security.

This module is the *hermetic, read-only* certification surface. It never
builds, installs, enables, restarts, mutates config/state, or touches live
services. The orchestrator ``scripts/release_qualify.py`` drives the opt-in
build/install steps (network) and collects the typed results here.

Checks provided (each returns a typed record list; ``certify`` assembles the
release report):

- provenance: version, git commit, dirty/untracked files, toolchain;
- dependency contract: declared constraints, dependency scan of installed code;
- licenses/notices: project license status + THIRD_PARTY_NOTICES presence;
- SBOM: declared deps + installed versions;
- secret scan and machine-specific-path scan over shipped code;
- artifact inspection: wheel/sdist members, forbidden/dirty members, content
  secret/machine-path scan, hashes;
- filesystem modes: portable templates carry no secrets/machine values, scripts
  are executable, restrictive 0600 state-write contract present;
- resource ceilings: recorded guardrails (disk/RAM/VRAM, oneshot runtime);
- backup/migration: MIGRATIONS present, newer-schema refusal, atomic restore;
- operator runbooks: required documentation present and non-empty;
- unit verification: ``systemd-analyze verify`` on rendered units + a
  repository-level security review;
- upgrade/rollback/uninstall contract reuse (distribution.py), including
  state-preserving uninstall by default with separate confirmation for data
  deletion;
- reproducibility: deterministic artifact hash helper + comparison record.

Everything here is bounded: subprocesses carry timeouts, file reads are capped,
and results are redacted (never include secret values).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
def git_provenance(repo: Optional[Path] = None) -> Dict[str, Any]:
    """Version + commit + dirty/untracked-file record (bounded lists).

    The dirty/untracked lists are parsed from ``git status --porcelain=v1 -z``
    so the first modified path is reported EXACTLY (a plain ``.strip()`` of the
    porcelain text would remove the leading status byte and truncate the first
    character of the first path)."""
    root = Path(repo) if repo else REPO
    rec: Dict[str, Any] = {
        "version": _package_version(),
        "python": f"{platform.python_version()} ({platform.python_implementation()})",
        "platform": f"{platform.system()} {platform.machine()}",
    }
    commit = _git(root, ["rev-parse", "HEAD"])
    rec["commit"] = commit
    rec["branch"] = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"]) or None
    entries = _status_entries(root)
    if commit is None and not entries:
        rec["git_repo"] = False
        rec["dirty"] = False
        rec["dirty_files"] = []
        rec["untracked_files"] = []
        return rec
    modified = []
    untracked = []
    for e in entries:
        code = e["code"]
        path = e["path"]
        if code == "??":
            untracked.append(path)
        else:
            modified.append(path)
    rec["git_repo"] = True
    rec["dirty"] = bool(modified or untracked)
    rec["dirty_files"] = modified[:200]
    rec["untracked_files"] = untracked[:200]
    return rec


def _package_version() -> str:
    try:
        import tomllib
        data = tomllib.loads((REPO / "pyproject.toml").read_text())
        return str(data.get("project", {}).get("version", "unknown"))
    except Exception:
        return "unknown"


def _git(repo: Path, args: List[str]) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(repo)] + args,
                             capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return (out.stdout or "").strip()
    except Exception:
        return None


def _git_bytes(repo: Path, args: List[str]) -> Optional[bytes]:
    """Run a git command and return raw stdout bytes (no text stripping).

    Used by the ``--porcelain=v1 -z`` provenance parser so path names are
    never corrupted by the leading status byte or by newline handling."""
    try:
        out = subprocess.run(["git", "-C", str(repo)] + args,
                             capture_output=True, timeout=10)
        if out.returncode != 0:
            return None
        return out.stdout
    except Exception:
        return None


def _status_entries(repo: Path) -> List[Dict[str, str]]:
    """Parse ``git status --porcelain=v1 -z`` exactly.

    Returns one dict per entry with ``code`` (the two status bytes, first byte
    possibly a space), and ``path`` (and ``dest`` for rename/copy). The NUL
    terminator means paths with spaces, Unicode, or quotes are preserved and the
    leading status byte of the FIRST entry is never stripped."""
    out = _git_bytes(repo, ["status", "--porcelain=v1", "-z"])
    if out is None:
        return []
    fields = out.split(b"\x00")
    entries: List[Dict[str, str]] = []
    i = 0
    while i < len(fields):
        f = fields[i]
        if not f:
            i += 1
            continue
        code = f[:2].decode("utf-8", "replace")
        # porcelain v1 keeps the space separator: "XY PATH\0" (also under -z)
        path = f[3:].decode("utf-8", "replace") if len(f) > 2 and f[2:3] == b" " \
            else f[2:].decode("utf-8", "replace")
        entry: Dict[str, str] = {"code": code, "path": path}
        if code[0] in ("R", "C") and i + 1 < len(fields):
            entry["dest"] = fields[i + 1].decode("utf-8", "replace")
            i += 1
        entries.append(entry)
        i += 1
    return entries


def toolchain() -> Dict[str, str]:
    """Build/install toolchain identities for reproducibility notes."""
    out: Dict[str, str] = {}
    probes = [
        ([sys.executable, "--version"], "python"),
        ([sys.executable, "-m", "pip", "--version"], "pip"),
        (["systemd-analyze", "--version"], "systemd-analyze"),
        (["openssl", "version"], "openssl"),
    ]
    for cmd, key in probes:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            out[key] = (r.stdout or r.stderr or "").strip().splitlines()[0][:160]
        except Exception:
            out[key] = ""
    return out


# ---------------------------------------------------------------------------
# Secret + machine-path scans
# ---------------------------------------------------------------------------
# Real secret VALUES — flagged in every file type.
SECRET_SHAPES = (
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github_token", re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)

# The generic ``key|token|secret|password = <value>`` assign pattern applies
# only to config/envelope files (yaml/env/json/systemd/toml/shell), where a
# value is present. Python variables named ``token``, and prose/templates in
# HTML/JS/markdown that say ``token=``, are NOT secrets.
SECRET_SHAPES_ALL = SECRET_SHAPES + (
    ("assign_secret", re.compile(
        r"(?i)\b(api[_-]?key|token|secret|password|passwd|bearer)\s*[=:]\s*"
        r"(?:\"[^\"]+\"|'[^']+'|[^\s\"']\S*)")),
)

_CONFIG_EXT = {".yaml", ".yml", ".env", ".service", ".timer", ".json",
               ".toml", ".sh", ".cfg", ".conf", ".ini"}

# Machine-specific PATH tokens in shipped code. Device-name tokens
# (Scarlett/HyperX/AT2020) are NOT here: they are the documented stable
# device-name matching feature (docs/archive/2026-09-21/AUDIO_SETUP.md), not path dependencies —
# the portable-CONFIG leak check for those tokens lives in
# filesystem_modes_check, mirroring the config doctor.
MACHINE_PATH_SHAPES = (
    re.compile(r"/home/[A-Za-z0-9_.-]+"),
    # a concrete project path (segments may contain spaces) — prose like
    # "under ~/Projects/Repos and report" is NOT a path (no slash after
    # Repos) and is not flagged.
    re.compile(r"~/Projects/Repos/[A-Za-z0-9_.&'()~-]+(?: [A-Za-z0-9_.&'()~-]+)*"),
    re.compile(r"/home/[A-Za-z0-9_.-]+/\.hermes"),
)

# Device-name tokens that must never leak into PORTABLE config examples or
# systemd templates (config-doctor convention).
DEVICE_TOKEN_SHAPES = (re.compile(r"\bScarlett\b|\bHyperX\b|\bAT2020\b"),)

# Documented, opt-in user-personalization surfaces (PACKET_0A PATH-04; the
# developer's personal task-template default). Values here are operator-editable
# personalization, not core portable dependencies; the certification records
# them as a separate typed category. `voicebridge/local/actions.py` is NOT on
# this list since Packet 8K removed personal launcher targets from it — they
# moved to the operator-local override ~/.config/qubit-voicebridge/
# local-actions.yaml, outside the repository.
PERSONALIZATION_FILES = (
    "config/task-templates.yaml",
)


def scan_text_secrets(text: str, *, is_python: bool = False,
                      is_config: bool = False) -> List[str]:
    """Shape names found in ``text`` (never the values). In Python source and
    in non-config prose/templates (HTML/JS/MD/README), the generic
    ``token = …`` assign pattern is NOT flagged: those are variable names and
    prose. Real secret VALUES (sk-, ghp_, AKIA, JWT, private keys) are flagged
    everywhere. The assign pattern is only meaningful in config/envelope
    files where a value is actually assigned."""
    if is_python or not is_config:
        shapes = SECRET_SHAPES
    else:
        shapes = SECRET_SHAPES_ALL
    return [name for name, rx in shapes if rx.search(text)]


def scan_text_machine_paths(text: str) -> List[str]:
    """Machine-specific path tokens found in ``text`` (redacted to the
    machine/user component)."""
    hits = set()
    for rx in MACHINE_PATH_SHAPES:
        for m in rx.finditer(text):
            v = m.group(0)
            if v.startswith("/home/"):
                hits.add(f"/home/{v.split('/')[2]}")
            elif v.startswith("~/"):
                hits.add(v.split("/", 2)[1])  # e.g. "Projects"
            else:
                hits.add(v[:40])
    return sorted(hits)


def _walk_py_files(root: Path) -> List[Path]:
    if not root.exists():
        return []
    files = sorted(root.rglob("*.py")) if root.is_dir() else [root]
    return [p for p in files if p.is_file()]


def _read_capped(path: Path, max_bytes: int = 1 << 20) -> str:
    try:
        if path.stat().st_size > max_bytes:
            return ""
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _is_skipped(rel: str, skip: set) -> bool:
    """Segment-aware skip: a path is skipped when it equals a skip entry
    (possibly a multi-segment prefix) or contains a skipped directory as a
    path segment. This keeps nested trees (e.g. vendored
    ``frontend/qubit-web/node_modules``) out of the shipped-code scans."""
    if rel in skip:
        return True
    parts = rel.split("/")
    for s in skip:
        s = s.rstrip("/")
        if not s:
            continue
        if "/" in s:
            if rel == s or rel.startswith(s + "/"):
                return True
        elif s in parts:
            return True
    return False


# Pinned upstream fork vendored into the product (Qubit-owned Qubit runtime,
# see docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md). Every file is
# byte-identical to the pinned upstream and carries per-file provenance in
# docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST.json. Upstream test fixtures legitimately
# contain secret-shaped strings and absolute example paths, so the Qubit
# secret/machine-path gates never scan it; the fork's own provenance gate
# (tests/test_qubit_runtime_vendor8u.py) covers it instead.
_VENDORED_UPSTREAM = {"qubit_runtime"}


def _iter_tree(root: Path, pattern: str, skip: set):
    """Yield files matching ``pattern`` while pruning skipped directories.

    ``Path.rglob`` enumerates the whole tree (the vendored ``qubit_runtime/``
    fork is ~12k files) before filtering, which made the static release scan
    slow enough to time out the dashboard. Pruning at the directory level
    keeps the shipped-code scans fast without changing what they skip."""
    import fnmatch
    import os

    root_s = str(root)
    for dirpath, dirnames, filenames in os.walk(root_s):
        rel_dir = os.path.relpath(dirpath, root_s).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = [
            d for d in dirnames
            if not _is_skipped(f"{rel_dir}/{d}" if rel_dir else d, skip)
        ]
        for fn in filenames:
            if not fnmatch.fnmatch(fn, pattern):
                continue
            rel = f"{rel_dir}/{fn}" if rel_dir else fn
            if _is_skipped(rel, skip):
                continue
            yield Path(dirpath) / fn


def secret_scan(root: Path, *, include_py: bool = True,
                include_yaml: bool = True, include_docs: bool = False,
                exclude_tests: bool = True
                ) -> Dict[str, List[str]]:
    """Scan a tree for secret-shaped values. Returns
    {path: [shape names]}. Skips the developer's live config, private
    browser state, and (by default) test fixtures, which intentionally carry
    FAKE secrets. Reads are size-capped."""
    results: Dict[str, List[str]] = {}
    skip = _private_paths() | _VENDORED_UPSTREAM
    if exclude_tests:
        skip.add("tests")
    # voicebridge/evaluation/ is the documented private eval corpus (plan
    # 4A/6B): sample utterances intentionally carry FAKE credential-shaped
    # strings. Recorded separately, never treated as live secrets.
    skip.add("voicebridge/evaluation")
    # Historical and generated documentation trees are not shipped product
    # surfaces: docs/archive/ is historical-only (Owner Decision #24) and
    # docs/generated/ is derived audit/inventory output whose fixtures carry
    # fake/canary secret shapes (sk-LEAKCANARY, <redacted>, apiKey: Type...).
    # The sdist already excludes the archival/generated trees; each is covered
    # by its own provenance/brand gate, so the product secret gate must not
    # flag them as live secrets.
    skip |= {"docs/archive", "docs/generated"}
    patterns = ["*.py"] if include_py else []
    if include_yaml:
        patterns += ["*.yaml", "*.yml", "*.service", "*.timer", "*.json",
                     "*.toml", "*.sh", "*.env", ".env*"]
    for pattern in patterns:
        for p in sorted(_iter_tree(root, pattern, skip)):
            rel = p.relative_to(root).as_posix()
            if not p.is_file() or _is_skipped(rel, skip):
                continue
            is_py = rel.endswith(".py")
            is_cfg = Path(p).suffix.lower() in _CONFIG_EXT
            hits = scan_text_secrets(_read_capped(p), is_python=is_py,
                                     is_config=is_cfg)
            if hits:
                results[rel] = hits
    return results


def machine_path_scan(root: Path) -> Dict[str, List[str]]:
    """Machine-specific path tokens in shipped code. Returns
    {relpath: [tokens]}. Personalization surfaces are returned separately in
    ``personalization`` so the gate is not weakened while the audit trail stays
    complete."""
    core: Dict[str, List[str]] = {}
    personal: Dict[str, List[str]] = {}
    skip = (_private_paths() | _VENDORED_UPSTREAM
            | {"frontend/", "docs/", "benchmarks/", "tests/"})
    for pattern in ("*.py", "*.sh", "*.service", "*.timer", "*.yaml"):
        for p in sorted(_iter_tree(root, pattern, skip)):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if _is_skipped(rel, skip):
                continue
            hits = scan_text_machine_paths(_read_capped(p))
            if not hits:
                continue
            if rel in PERSONALIZATION_FILES:
                personal[rel] = hits
            else:
                core[rel] = hits
    return {"core": core, "personalization": personal}


def _private_paths() -> set[str]:
    """Paths that must never be scanned as shipped code or shipped at all."""
    return {
        "scripts/browser_profiles", "scripts/browser_auto_state",
        ".playwright-mcp",
        ".git", ".venv", "__pycache__", ".pytest_cache", "graft", "dist",
        ".claude", ".cursor", ".gemini", ".github", ".kiro", ".vscode",
        ".windsurf", ".adal", "node_modules", ".mcp.json", "opencode.json",
    }


# ---------------------------------------------------------------------------
# Dependency contract + SBOM
# ---------------------------------------------------------------------------
def declared_dependencies(pyproject: Optional[Path] = None
                          ) -> Dict[str, List[Dict[str, str]]]:
    """Parse pyproject dependency constraints (main + optional extras). An
    absent pyproject yields empty records (caller decides how to degrade)."""
    pp = Path(pyproject) if pyproject else (REPO / "pyproject.toml")
    if not pp.exists():
        return {"main": [], "extras": {}}
    data = json.loads(pp.read_text()) if pp.suffix == ".json" else _toml(pp)
    project = data.get("project", {})
    main = [_req(r) for r in project.get("dependencies", [])]
    extras = {
        name: [_req(r) for r in reqs]
        for name, reqs in (project.get("optional-dependencies") or {}).items()
    }
    return {"main": main, "extras": extras}


def _req(r: str) -> Dict[str, str]:
    name = re.split(r"[<>=!\[;\s]", r.strip(), maxsplit=1)[0]
    return {"name": name, "spec": r.strip()}


def _toml(path: Path) -> Dict[str, Any]:
    import tomllib
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _project_license_text(repo: Optional[Path] = None) -> str:
    """The canonical project-license declaration read from ``pyproject.toml``
    (``[project].license``). Falls back to the root LICENSE summary when the
    metadata is missing — never fabricated, and never a hardcoded claim that
    can drift from the real license boundary."""
    root = Path(repo) if repo else REPO
    try:
        data = _toml(root / "pyproject.toml")
        lic = (data.get("project") or {}).get("license")
        if isinstance(lic, dict) and lic.get("text"):
            return lic["text"].strip()
        if isinstance(lic, str) and lic.strip():
            return lic.strip()
    except OSError:
        pass
    return ("Qubit VoiceBridge Educational Project License — see the root "
            "LICENSE; applies only to original Qubit material. Third-party/"
            "upstream-derived material remains under its original license per "
            "THIRD_PARTY_NOTICES.md and docs/UPSTREAM_REUSE.md.")


def sbom(repo: Optional[Path] = None) -> Dict[str, Any]:
    """Declared dependency record. Installed versions are resolved only when
    the distributions are present (importlib.metadata); otherwise recorded as
    ``unknown`` — never fabricated."""
    root = Path(repo) if repo else REPO
    deps = declared_dependencies(root / "pyproject.toml")
    installed = _installed_versions()
    recs = []
    for d in deps["main"]:
        recs.append({"name": d["name"], "constraint": d["spec"],
                     "installed": installed.get(d["name"], "unknown")})
    return {
        "package": "qubit-voicebridge",
        "version": _package_version(),
        "python": platform.python_version(),
        "main": recs,
        "optional": deps["extras"],
        "notices": "THIRD_PARTY_NOTICES.md + docs/archive/2026-09-21/UPSTREAM_REUSE.md",
        "project_license": _project_license_text(root),
    }


def _installed_versions() -> Dict[str, str]:
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover
        return {}
    out: Dict[str, str] = {}
    for d in metadata.distributions():
        try:
            name = (d.metadata.get("Name") or "").lower()
        except Exception:
            continue
        if name:
            out[name] = d.version
    return out


def _vendored_top_level_names(base: Path) -> set[str]:
    """Top-level import names of the installed vendored Qubit runtime.

    ``base`` is the installed ``voicebridge`` package directory; the fork ships
    as package data under ``base/_data/qubit_runtime``. Each sub-directory with
    an ``__init__.py`` and each top-level ``*.py`` module is a valid import name
    once ``qubit_runtime.ensure_path()`` puts that tree on ``sys.path``, so it
    is a local module, not an undeclared PyPI dependency.
    """
    root = Path(base) / "_data" / "qubit_runtime"
    names: set[str] = set()
    if not root.is_dir():
        return names
    for entry in root.iterdir():
        if entry.is_dir():
            if (entry / "__init__.py").is_file():
                names.add(entry.name)
        elif entry.suffix == ".py" and entry.name != "__init__.py":
            names.add(entry.stem)
    return names


def import_scan_installed(base: Path, declared: set[str],
                          stdlib: Optional[set[str]] = None,
                          guarded: Optional[set[str]] = None) -> List[str]:
    """AST-scan ``base`` (e.g. the installed ``voicebridge`` package) for
    third-party imports not in ``declared`` or the stdlib. Returns
    ``"module at relpath:line"`` records. This is the exact scan the clean-venv
    check runs against the INSTALLED package so a missing wheel member cannot
    be masked by a source checkout on ``sys.path``. Guarded optional imports
    (``OPTIONAL_GUARDED``) are tolerated — they degrade at runtime."""
    stdlib = stdlib if stdlib is not None else set(sys.stdlib_module_names)
    guarded = guarded if guarded is not None else OPTIONAL_GUARDED
    declared = {_normalize_import(d) for d in declared}
    import ast
    local = {"voicebridge", "scripts", "browser_auto",
             # Qubit-owned vendored Qubit fork (qubit_runtime/): a local
             # source tree, not a PyPI distribution. See the owner amendment
             # docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md.
             # ``toolsets`` is a vendored top-level module reached through the
             # runtime path shim from ``voicebridge/qubit_asgi.py`` (guarded).
             "qubit_cli", "toolsets"}
    # Every top-level module of the installed vendored runtime is a local
    # module reached through ``qubit_runtime.ensure_path()`` (agent, tools,
    # providers, qubit_state, …), never a PyPI dependency. Derive the names
    # from the shipped ``_data/qubit_runtime`` tree so the allowlist cannot
    # drift from the vendored source.
    local |= _vendored_top_level_names(base)
    failures: List[str] = []
    for py in _walk_py_files(base):
        rel_path = py.relative_to(base)
        # ``_data`` is shipped package *data*: the Qubit-owned vendored Qubit
        # runtime plus completions/templates. It is a separate source tree with
        # its own pinned dependency contract (qubit_runtime/pyproject.toml,
        # verified by tests/test_qbpv_pkg5_packaging.py) and imports its own
        # local top-level modules (agent, tools, toolsets, qubit_cli, …). It is
        # not part of the voicebridge import contract, so scanning it here would
        # report thousands of false undeclared-import failures.
        if "_data" in rel_path.parts:
            continue
        rel = rel_path.as_posix()
        try:
            tree = ast.parse(_read_capped(py))
        except SyntaxError as exc:
            failures.append(f"<syntax> at {rel}:{exc.lineno}")
            continue
        for node in ast.walk(tree):
            mods: List[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            for m in mods:
                top = m.split(".")[0]
                if top in stdlib | declared | local | guarded:
                    continue
                failures.append(f"{top} at {rel}:{node.lineno}")
    return failures


def declared_module_names(pyproject: Optional[Path] = None) -> set[str]:
    """Import names allowed by pyproject (used by clean-venv + certifier)."""
    data = declared_dependencies(pyproject)
    names = {d["name"] for d in data["main"]}
    for reqs in data["extras"].values():
        names |= {d["name"] for d in reqs}
    names |= _DIST_ALIASES.keys() | set(_DIST_ALIASES.values())
    return {_normalize_import(n) for n in names}


_DIST_ALIASES = {"pyyaml": "yaml", "piper_tts": "piper"}

# Modules imported through guarded optional paths (try/except ImportError) as
# inventoried in docs/archive/2026-09-21/PACKET_0A_MISMATCH_REPORT.md section D. They degrade
# gracefully, so their absence is a warning, not a fresh-install failure
# (mirrors scripts/config_doctor.OPTIONAL_GUARDED).
OPTIONAL_GUARDED = {"psutil", "onnx", "prompt_toolkit", "websocket",
                    "pystray"}


def _normalize_import(name: str) -> str:
    return name.replace("-", "_").split(".")[0].lower()


def declared_module_names_from_metadata(dist_name: str = "qubit-voicebridge"
                                        ) -> set[str]:
    """Import names allowed by the INSTALLED package metadata (Requires-Dist
    including extras with markers). Used by the in-venv clean checks so a
    missing dependency cannot be masked by a source checkout."""
    from importlib import metadata
    names = set()
    for req in metadata.requires(dist_name) or []:
        base = re.split(r"[<>=!\[;\s]", req.strip(), maxsplit=1)[0]
        base = base.split("/")[-1] if "/" in base else base
        if base:
            names.add(base)
    names |= _DIST_ALIASES.keys() | set(_DIST_ALIASES.values())
    return {_normalize_import(n) for n in names}


# ---------------------------------------------------------------------------
# Artifact inspection
# ---------------------------------------------------------------------------
ARTIFACT_FORBIDDEN = (
    "browser_profiles", "browser_auto_state", ".env", ".venv",
    "__pycache__", ".pyc", "106", "DESIGN.md", "PRODUCT.md",
    ".claude", ".cursor", ".gemini", ".github", ".kiro", ".vscode",
    ".windsurf", ".adal", "config/voice.yaml", "config/voice.local.yaml",
)


def artifact_members(artifact: Path) -> List[str]:
    """Member names inside a wheel (.whl zip) or sdist (.tar.gz)."""
    if str(artifact).endswith(".whl") or str(artifact).endswith(".zip"):
        with zipfile.ZipFile(artifact) as z:
            return z.namelist()
    if str(artifact).endswith((".tar.gz", ".tgz")):
        with tarfile.open(artifact, "r:gz") as t:
            return t.getnames()
    raise ValueError(f"unsupported artifact: {artifact}")


def artifact_member_hits(artifact: Path, forbidden: Tuple[str, ...] = ARTIFACT_FORBIDDEN
                         ) -> List[str]:
    """Members that match a forbidden private/dirty-file pattern."""
    hits = []
    for name in artifact_members(artifact):
        low = name.lstrip("./").lower()
        if low.endswith(("/", "")) and not low.rstrip("/"):
            continue
        for pat in forbidden:
            pl = pat.lower()
            # Exact path-segment match for file/dir names; a multi-segment
            # pattern (e.g. ``config/voice.yaml``) matches as a suffix. Never a
            # raw ``endswith`` on a bare name: that falsely flagged
            # ``responsive-design.md`` for the ``design.md`` pattern.
            if pl in low.split("/") or (
                    "/" in pl and (low == pl or low.endswith("/" + pl))):
                hits.append(name)
                break
    return hits


# An sdist source-root directory is ``<project>-<version>`` (setuptools
# normalizes ``-`` to ``_`` in the distribution name). Matched generically so
# the classifier survives a project rename; repo top-level dirs (voicebridge,
# tests, docs, config, …) never match ``name-<digit>``.
_SDIST_ROOT_RE = re.compile(r"^[A-Za-z0-9_.]+-\d")


def artifact_member_class(name: str) -> str:
    """Classify an artifact member for gating:
    ``fixture`` — tests/ and the eval corpus (intentionally fake secrets);
    ``personalization`` — the documented operator personalization module;
    ``core`` — shipped runtime code that must be clean;
    ``noncore`` — docs, frontend UI, templates, metadata, model files."""
    low = name.lstrip("./")
    parts = low.split("/")
    if len(parts) > 1 and _SDIST_ROOT_RE.match(parts[0]):
        # sdist members carry the ``<name>-<version>`` source-root prefix
        # (setuptools normalizes the project name, e.g. ``qubit_voicebridge-``
        # or the historical ``qubit_voice_agent-``). Strip it generically so
        # core/fixture classification is not tied to one stale project name.
        low = "/".join(parts[1:])
    if "/tests/" in low or low.split("/")[0] == "tests" \
            or low.startswith("voicebridge/evaluation/"):
        return "fixture"
    if "config/task-templates.yaml" in low:
        return "personalization"
    if low.startswith("voicebridge/"):
        return "core"
    return "noncore"


def artifact_content_scan(artifact: Path) -> Dict[str, Dict[str, List[str]]]:
    """Scan artifact member contents for secret shapes and machine-specific
    paths, classified for the gate (see :func:`artifact_member_class`).
    Returns ``{"secrets", "fixture_secrets", "machine_paths",
    "machine_paths_noncore"}``; values are shape names / redacted tokens,
    never secret values. Reads are size-capped."""
    secrets: Dict[str, List[str]] = {}
    fixture_secrets: Dict[str, List[str]] = {}
    machine: Dict[str, List[str]] = {}
    machine_noncore: Dict[str, List[str]] = {}

    def _handle(name: str, text: str) -> None:
        is_py = name.endswith(".py")
        is_cfg = Path(name).suffix.lower() in _CONFIG_EXT
        sh = scan_text_secrets(text, is_python=is_py, is_config=is_cfg)
        mh = scan_text_machine_paths(text)
        cls = artifact_member_class(name)
        if sh:
            (fixture_secrets if cls == "fixture" else secrets)[name] = sh
        if mh:
            (machine if cls == "core" else machine_noncore)[name] = mh

    if str(artifact).endswith((".whl", ".zip")):
        with zipfile.ZipFile(artifact) as z:
            for info in z.infolist():
                if info.is_dir() or info.file_size > (1 << 20):
                    continue
                try:
                    data = z.read(info).decode("utf-8", "replace")
                except Exception:
                    continue
                _handle(info.filename, data)
    elif str(artifact).endswith((".tar.gz", ".tgz")):
        with tarfile.open(artifact, "r:gz") as t:
            for member in t.getmembers():
                if not member.isfile() or member.size > (1 << 20):
                    continue
                try:
                    f = t.extractfile(member)
                    if f is None:
                        continue
                    data = f.read().decode("utf-8", "replace")
                except Exception:
                    continue
                _handle(member.name, data)
    return {"secrets": secrets, "fixture_secrets": fixture_secrets,
            "machine_paths": machine, "machine_paths_noncore": machine_noncore}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Filesystem modes + resource ceilings + runbooks + migration
# ---------------------------------------------------------------------------
def filesystem_modes_check(root: Optional[Path] = None) -> List[Dict[str, str]]:
    """Portable templates carry no secrets/machine values; launchers are
    executable; the restrictive 0600 state-write contract is present in code."""
    root = Path(root) if root else REPO
    results: List[Dict[str, str]] = []

    for rel in ("scripts/install.sh", "scripts/run.sh",
                "scripts/install_systemd_unit.sh"):
        p = root / rel
        exe = os.access(p, os.X_OK) if p.exists() else False
        results.append({
            "name": f"mode.executable.{rel}",
            "state": "OK" if exe else ("MISSING" if not p.exists() else "FAIL"),
            "detail": f"executable={exe}",
        })

    example = root / "config/voice.example.yaml"
    if example.exists():
        tokens = scan_text_machine_paths(_read_capped(example))
        device = []
        for rx in DEVICE_TOKEN_SHAPES:
            device += rx.findall(_read_capped(example))
        results.append({
            "name": "mode.portable_config",
            "state": "OK" if not tokens and not device else "FAIL",
            "detail": ("no machine tokens" if not tokens and not device else
                       f"machine tokens: {tokens + sorted(set(device))}"),
        })
    else:
        results.append({"name": "mode.portable_config", "state": "MISSING",
                        "detail": "config/voice.example.yaml absent"})

    # Packet 8K: the shipped operator-local override EXAMPLE must be portable
    # (no absolute home-directory paths, no device tokens), so a fresh install
    # never copies the developer's personal launcher targets.
    la_example = root / "config/local-actions.example.yaml"
    if la_example.exists():
        la_tokens = scan_text_machine_paths(_read_capped(la_example))
        la_device = []
        for rx in DEVICE_TOKEN_SHAPES:
            la_device += rx.findall(_read_capped(la_example))
        results.append({
            "name": "mode.portable_local_actions",
            "state": "OK" if not la_tokens and not la_device else "FAIL",
            "detail": ("no machine tokens" if not la_tokens and not la_device else
                       f"machine tokens: {la_tokens + sorted(set(la_device))}"),
        })
    else:
        results.append({"name": "mode.portable_local_actions",
                        "state": "MISSING",
                        "detail": "config/local-actions.example.yaml absent"})

    # systemd templates must not carry machine paths, secrets, or (for the
    # portable voice-agent template) hardcoded resource directives (Packet 7A
    # convention). The monitor oneshot documents its bounds as repository
    # defaults, so the hardcoded-resource gate applies only to the agent unit.
    for unit in sorted(root.glob("systemd/*.service")) + sorted(root.glob("systemd/*.timer")):
        text = _read_capped(unit)
        bad = []
        if scan_text_machine_paths(text):
            bad.append("machine path")
        if scan_text_secrets(text):
            bad.append("secret")
        if unit.name == "qubit-voicebridge.service" and re.search(
                r"^Memory(Max|High)|^Nice=|^CPUQuota=", text, re.M):
            bad.append("hardcoded resource directive")
        results.append({
            "name": f"mode.unit.{unit.name}",
            "state": "OK" if not bad else "FAIL",
            "detail": "clean" if not bad else "; ".join(bad),
        })

    # restrictive state-write contract: private state must be written 0600.
    mode_contract = False
    for mod in ("voicebridge/device.py", "voicebridge/portable.py"):
        text = _read_capped(root / mod)
        if "0o600" in text or "0600" in text:
            mode_contract = True
    results.append({
        "name": "mode.private_state_0600",
        "state": "OK" if mode_contract else "FAIL",
        "detail": "0600 state-write contract present in device.py/portable.py",
    })
    return results


def resource_ceilings(root: Optional[Path] = None) -> Dict[str, Any]:
    """Recorded guardrails: distribution constants + oneshot runtime ceiling."""
    root = Path(root) if root else REPO
    ceilings: Dict[str, Any] = {}
    text = _read_capped(root / "voicebridge/distribution.py")
    for name in ("MIN_DISK_FREE_GIB", "MIN_RAM_GIB", "VRAM_CEILING_MIB"):
        m = re.search(rf"^{name}\s*=\s*([\d\.\s\*]+?)(?:\n|#)", text, re.M)
        if m:
            try:
                value = 1.0
                for tok in re.findall(r"[\d.]+", m.group(1)):
                    value *= float(tok)
                ceilings[name] = value
            except Exception:
                ceilings[name] = None
        else:
            ceilings[name] = None
    svc = _read_capped(root / "systemd/qubit-communications-monitor.service")
    t = re.search(r"^TimeoutStartSec=(\d+)", svc, re.M)
    ceilings["monitor_oneshot_timeout_s"] = int(t.group(1)) if t else None
    mh = re.search(r"^MemoryMax=(\S+)", svc, re.M)
    ceilings["monitor_memory_max"] = mh.group(1) if mh else None
    return ceilings


def operator_runbook_check(root: Optional[Path] = None) -> List[Dict[str, str]]:
    """Required operator documentation present and non-empty."""
    root = Path(root) if root else REPO
    required = [
        "README.md",
        "LICENSE",
        "docs/archive/2026-09-21/VOICE_BRIDGE_SYSTEM.md",
        "docs/archive/2026-09-21/AUDIO_SETUP.md",
        "docs/archive/2026-09-21/VOICE_BRIDGE_TROUBLESHOOTING.md",
        "docs/archive/2026-09-21/OPERATIONS_8J.md",
        "docs/archive/2026-09-21/OPERATIONS_8K.md",
        "docs/archive/2026-09-21/MIGRATIONS.md",
        "docs/archive/2026-09-21/UPSTREAM_REUSE.md",
        "THIRD_PARTY_NOTICES.md",
    ]
    results: List[Dict[str, str]] = []
    for rel in required:
        p = root / rel
        if not p.exists():
            results.append({"name": f"docs.{rel}", "state": "MISSING",
                            "detail": "absent"})
            continue
        size = p.stat().st_size
        results.append({"name": f"docs.{rel}", "state": "OK" if size > 200 else "FAIL",
                        "detail": f"{size} bytes"})
    return results


def backup_migration_check(root: Optional[Path] = None) -> List[Dict[str, str]]:
    """Migration history + newer-schema refusal + atomic restore contract."""
    root = Path(root) if root else REPO
    results: List[Dict[str, str]] = []
    mig = root / "docs/archive/2026-09-21/MIGRATIONS.md"
    results.append({
        "name": "migration.history",
        "state": "OK" if mig.exists() else "MISSING",
        "detail": "docs/archive/2026-09-21/MIGRATIONS.md" if mig.exists() else "absent",
    })
    portable_text = _read_capped(root / "voicebridge/portable.py")
    recov_text = _read_capped(root / "voicebridge/communications/recovery_qualify.py")
    has_newer_schema = ("UnsupportedSchemaError" in portable_text) and (
        "_guard_newer_schema" in recov_text or "newer" in recov_text.lower())
    results.append({
        "name": "migration.newer_schema_refusal",
        "state": "OK" if has_newer_schema else "FAIL",
        "detail": ("portable.py + recovery_qualify refuse newer schema"
                   if has_newer_schema else "no newer-schema refusal found"),
    })
    has_atomic_restore = ("os.replace" in portable_text
                          and "fsync" in portable_text)
    results.append({
        "name": "migration.atomic_restore",
        "state": "OK" if has_atomic_restore else "FAIL",
        "detail": "atomic temp+fsync+rename restore present" if has_atomic_restore
                  else "atomic restore not found",
    })
    return results


# ---------------------------------------------------------------------------
# Unit verification (systemd-analyze verify + security review)
# ---------------------------------------------------------------------------
def unit_verify(root: Optional[Path] = None, workdir: Optional[Path] = None,
                stub_venv_python: bool = True) -> Dict[str, Any]:
    """Render repository units (resolve __PROJECT_DIR__), stub the ExecStart
    python so `systemd-analyze verify` can resolve it, run the verifier, and
    return per-unit results. Units resolve from the checkout ``systemd/`` or,
    in an installed package, from the packaged ``_data/systemd`` (Packet 8K
    resource contract). Falls back to a repository security review when
    systemd-analyze is unavailable (typed DEGRADED, never a silent pass)."""
    root = Path(root) if root else REPO
    unit_dirs = []
    if (root / "systemd").exists():
        unit_dirs.append(root / "systemd")
    try:
        from .resources import resource_path
        packaged = resource_path("systemd")
        if packaged.is_dir() and packaged not in unit_dirs:
            unit_dirs.append(packaged)
    except Exception:
        pass
    units = []
    for d in unit_dirs:
        units += sorted(d.glob("*.service")) + sorted(d.glob("*.timer"))
    units = sorted(set(units))
    if not units:
        return {"available": True, "systemd_analyze": False, "units": [],
                "error": "no units found under systemd/ or packaged _data/systemd",
                "all_ok": False}
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="vb-unit-verify-"))
    proj = tmp / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (proj / "scripts").mkdir(parents=True, exist_ok=True)
    if stub_venv_python:
        stub = proj / ".venv" / "bin" / "python"
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
        mon = proj / "scripts" / "communications_monitor.py"
        mon.write_text("#!/bin/sh\nexit 0\n")
        mon.chmod(0o755)
    rendered: List[Path] = []
    for u in units:
        dst = tmp / u.name
        dst.write_text(u.read_text().replace("__PROJECT_DIR__", str(proj)))
        rendered.append(dst)

    per_unit: List[Dict[str, str]] = []
    if _which("systemd-analyze"):
        try:
            r = subprocess.run(
                ["systemd-analyze", "verify"] + [str(p) for p in rendered],
                capture_output=True, text=True, timeout=60)
            lines = [ln for ln in (r.stdout or "").splitlines()
                     if ln.strip()] + [ln for ln in (r.stderr or "").splitlines()
                                       if ln.strip()]
            # map the raw output back to per-unit status
            for u in units:
                relevant = [ln for ln in lines if u.name in ln]
                ok = not any("bad unit file" in ln or "fatal error" in ln
                             for ln in relevant)
                per_unit.append({
                    "unit": u.name,
                    "state": "OK" if ok else "FAIL",
                    "detail": " ".join(relevant)[:300] or "clean",
                })
            all_ok = all(x["state"] == "OK" for x in per_unit)
            return {"available": True, "systemd_analyze": True,
                    "units": per_unit, "all_ok": all_ok,
                    "toolchain": toolchain()["systemd-analyze"]}
        except subprocess.TimeoutExpired:
            return {"available": True, "systemd_analyze": True,
                    "units": [], "error": "systemd-analyze verify timed out",
                    "all_ok": False}
    # fallback security review (no systemd-analyze)
    return {"available": True, "systemd_analyze": False, "all_ok": False,
            "units": [_unit_security_review(u) for u in units],
            "error": "systemd-analyze unavailable; repository security review "
                     "used (DEGRADED)"}


def _unit_security_review(unit: Path) -> Dict[str, str]:
    text = _read_capped(unit)
    bad = []
    # __PROJECT_DIR__ is the documented render placeholder, not a security issue
    if scan_text_secrets(text):
        bad.append("secret-shaped value")
    if re.search(r"/home/[A-Za-z0-9_.-]+", text):
        bad.append("machine home path")
    if re.search(r"^\s*Environment=.*(sk-[A-Za-z0-9]{20,}|ghp_|AKIA)", text, re.M):
        bad.append("Environment carries a credential value")
    return {"unit": unit.name, "state": "OK" if not bad else "FAIL",
            "detail": "; ".join(bad) if bad else "clean (security review)"}


def _which(name: str) -> Optional[str]:
    import shutil
    return shutil.which(name)


# ---------------------------------------------------------------------------
# Upgrade / rollback / uninstall contract (reuse, never weaken)
# ---------------------------------------------------------------------------
def upgrade_uninstall_contract(cfg: Any) -> Dict[str, Any]:
    """Reuse distribution upgrade/uninstall contracts and assert the 8K
    invariants: uninstall preserves user state by default and requires a
    separate explicit confirmation for data deletion; upgrade preserves state,
    config, flags, and never silently restarts/enables."""
    from . import distribution as D
    up = D.upgrade_plan(cfg)
    un = D.uninstall_plan(cfg)
    joined_up = " ".join(str(x) for x in up.get("does_not", [])).lower()
    joined_un = " ".join(str(x) for x in un.get("preserves_by_default", [])).lower()
    checks = {
        "upgrade.action": up.get("action") == "upgrade",
        # the plan must DOCUMENT that it does not delete state
        "upgrade.preserves_state": "delete state" in joined_up,
        "upgrade.rollback": bool(up.get("rollback")),
        "uninstall.requires_confirmation": un.get("requires_confirmation") is True,
        "uninstall.preserves_state_by_default": "state dir" in joined_un,
        "uninstall.data_deletion_separate": bool(un.get("steps_if_confirmed")),
    }
    return {
        "plan": {"upgrade": up, "uninstall": un},
        "checks": checks,
        "pass": all(checks.values()),
    }


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
def reproducibility_record(artifact_a: Path, artifact_b: Optional[Path] = None
                           ) -> Dict[str, Any]:
    """Artifact hash record + (optional) reproducibility comparison. A
    mismatch is reported truthfully with both hashes, never hidden."""
    rec: Dict[str, Any] = {
        "artifact": artifact_a.name,
        "size_bytes": artifact_a.stat().st_size if artifact_a.exists() else None,
        "sha256": sha256(artifact_a) if artifact_a.exists() else None,
    }
    if artifact_b is not None:
        if not artifact_b.exists():
            rec["reproducible"] = False
            rec["comparison"] = "second build missing"
        else:
            rec["second_sha256"] = sha256(artifact_b)
            rec["reproducible"] = rec["sha256"] == rec["second_sha256"]
    return rec


# ---------------------------------------------------------------------------
# Certification report
# ---------------------------------------------------------------------------
def support_matrix_check(root: Optional[Path] = None) -> List[Dict[str, str]]:
    """Verify the RUNTIME platform/python against the documented support
    matrix (distribution.SUPPORTED_TARGET), and that display/audio support is
    documented (AUDIO_SETUP / VOICE_BRIDGE_SYSTEM). A runtime outside the
    matrix is a typed NOT_SUPPORTED — never a silent pass."""
    root = Path(root) if root else REPO
    try:
        from .distribution import SUPPORTED_TARGET
        target = dict(SUPPORTED_TARGET)
    except Exception:
        target = {}
    results: List[Dict[str, str]] = []
    os_ok = platform.system() == "Linux"
    arch_ok = platform.machine() in tuple(target.get("arches", []) or [])
    results.append({
        "name": "support.os",
        "state": "OK" if os_ok else "NOT_SUPPORTED",
        "detail": f"{platform.system()} (documented: "
                  f"{target.get('os', 'linux')})",
    })
    results.append({
        "name": "support.arch",
        "state": "OK" if arch_ok else "NOT_SUPPORTED",
        "detail": platform.machine(),
    })
    py_ok = sys.version_info >= (3, 11)
    results.append({
        "name": "support.python",
        "state": "OK" if py_ok else "NOT_SUPPORTED",
        "detail": f"{platform.python_version()} (documented: >= "
                  f"{target.get('python_min', '3.11')})",
    })
    for doc in ("docs/archive/2026-09-21/AUDIO_SETUP.md", "docs/archive/2026-09-21/VOICE_BRIDGE_SYSTEM.md",
                "docs/archive/2026-09-21/VOICE_BRIDGE_TROUBLESHOOTING.md"):
        p = root / doc
        results.append({
            "name": f"support.documented.{Path(doc).stem}",
            "state": "OK" if p.exists() and p.stat().st_size > 200 else "MISSING",
            "detail": doc,
        })
    return results


def bounded_logs_check(root: Optional[Path] = None) -> List[Dict[str, str]]:
    """Bounded-logs contract (Packet 8K): the structured JSONL journal must be
    valid-by-construction, bounded/recoverable, and covered by a no-secret-leak
    contract. Reads are size-capped."""
    root = Path(root) if root else REPO
    results: List[Dict[str, str]] = []
    logging_text = _read_capped(root / "voicebridge/logging_setup.py")
    has_coercion = "_json_safe" in logging_text
    results.append({
        "name": "logs.journal_valid_coercion",
        "state": "OK" if has_coercion else "FAIL",
        "detail": ("_json_safe typed coercion keeps the JSONL journal valid"
                   if has_coercion else "no journal coercion found"),
    })
    recov_text = _read_capped(root / "voicebridge/communications/recovery_qualify.py")
    recov_crash = _read_capped(root / "voicebridge/communications/recovery_crash.py")
    bounded = "jsonl_bounded" in recov_text and "truncated" in recov_text.lower()
    results.append({
        "name": "logs.journal_bounded_recovery",
        "state": "OK" if bounded else "FAIL",
        "detail": ("8J recovery qualifies journal boundedness + truncated-line "
                   "tolerance" if bounded else "no bounded-journal qualification"),
    })
    no_secret = ("no_secret_in_journal" in recov_text
                 or "secret" in recov_crash.lower())
    results.append({
        "name": "logs.journal_no_secret_leak",
        "state": "OK" if no_secret else "FAIL",
        "detail": "journal no-secret-leak contract present" if no_secret
                  else "no journal no-secret contract found",
    })
    return results


def approved_signer_fingerprint(env: Optional[Dict[str, str]] = None) -> str:
    """The single approved release-signer fingerprint (Packet 8K closure).

    Read from ``$RELEASE_SIGNER_FINGERPRINT`` (or the explicit ``env`` dict)
    or from the repository ``config/release-signer.fingerprint`` file (a
    single 40-hex-char line). A full 40-character fingerprint is REQUIRED;
    short key IDs are never trusted. Returns ``""`` when not configured."""
    src = os.environ if env is None else env
    fp = src.get("RELEASE_SIGNER_FINGERPRINT", "").strip()
    if not fp:
        try:
            p = REPO / "config" / "release-signer.fingerprint"
            if p.is_file():
                fp = p.read_text(errors="replace").strip()
        except OSError:
            pass
    fp = "".join(fp.split()).upper()
    if not re.fullmatch(r"[0-9A-F]{40}", fp):
        return ""
    return fp


def artifact_signature_status() -> Dict[str, Any]:
    """Honest SIGNING-CAPABILITY status (Packet 8K closure).

    This is STATIC capability detection only and must never claim that an
    artifact is signed. It reports ``SIGNING_CAPABLE`` when a release-signing
    mechanism is configured (gpg available AND an approved full signer
    fingerprint is set), ``NOT_CONFIGURED`` otherwise. Only
    :func:`verify_artifact_signature` — which verifies an ACTUAL detached
    signature against the approved fingerprint — may report ``SIGNED``. The
    presence of the gpg binary alone is NOT signing capability."""
    import shutil
    gpg = shutil.which("gpg")
    fp = approved_signer_fingerprint()
    tools = {
        "gpg": bool(gpg),
        "twine": bool(shutil.which("twine")),
        "sigstore": bool(shutil.which("sigstore")),
    }
    capable = bool(gpg) and bool(fp)
    return {
        "state": "SIGNING_CAPABLE" if capable else "NOT_CONFIGURED",
        "detail": ("signing capability present (gpg + approved full signer "
                   "fingerprint configured); artifacts are only SIGNED after "
                   "detached-signature verification" if capable else
                   "no approved release-signer fingerprint configured and/or "
                   "gpg unavailable; artifacts remain NOT_SIGNED"),
        "approved_fingerprint_set": bool(fp),
        "gpg_available": bool(gpg),
        "tools": tools,
    }


def verify_artifact_signature(artifact: Path, sig: Optional[Path] = None,
                              expected_fp: Optional[str] = None,
                              env: Optional[Dict[str, str]] = None
                              ) -> Dict[str, Any]:
    """Verify an ACTUAL detached signature against the APPROVED signer.

    The signature only counts as ``SIGNED`` when ALL of these hold:

    - a detached signature exists for ``artifact`` (``<artifact>.asc`` /
      ``<artifact>.sig``, or the explicit ``sig`` path);
    - gpg is available and the signature cryptographically verifies;
    - gpg status reports a ``VALIDSIG`` whose FULL 40-hex fingerprint equals
      the approved signer fingerprint.

    Typed results: ``SIGNED``, ``INVALID`` (crypto failure or changed
    artifact), ``UNTRUSTED_SIGNER`` (valid signature from the wrong key),
    ``NOT_SIGNED`` (no signature file), ``DEGRADED`` (gpg missing / cannot
    run / no approved fingerprint). Nothing is ever fabricated and no secret
    key material is exposed."""
    import shutil
    if expected_fp is None:
        expected_fp = approved_signer_fingerprint()
    expected_fp = "".join((expected_fp or "").split()).upper()
    if not expected_fp:
        return {
            "artifact": artifact.name,
            "state": "DEGRADED",
            "detail": "no approved full signer fingerprint configured; "
                      "set RELEASE_SIGNER_FINGERPRINT (or "
                      "config/release-signer.fingerprint) to a 40-hex "
                      "fingerprint before verifying signatures",
        }
    if sig is None:
        for cand in (Path(str(artifact) + ".asc"), Path(str(artifact) + ".sig")):
            if cand.is_file():
                sig = cand
                break
    if sig is None or not sig.is_file():
        return {
            "artifact": artifact.name,
            "state": "NOT_SIGNED",
            "detail": "no detached signature file present "
                      "(expected <artifact>.asc or <artifact>.sig)",
        }
    gpg = shutil.which("gpg")
    if not gpg:
        return {
            "artifact": artifact.name,
            "state": "DEGRADED",
            "detail": f"signature present ({sig.name}) but gpg unavailable to verify",
            "signature": sig.name,
        }
    try:
        r = subprocess.run(
            [gpg, "--status-fd", "1", "--verify", str(sig), str(artifact)],
            capture_output=True, text=True, timeout=60,
            env=env if env is not None else os.environ.copy())
    except Exception as exc:
        return {"artifact": artifact.name, "state": "DEGRADED",
                "detail": f"gpg verification failed to run: {exc}",
                "signature": sig.name}
    status = r.stdout or ""
    err = r.stderr or ""
    # VALIDSIG <fingerprint> <date> <timestamp> <expire-timestamp> <version>
    # <reserved> <pubkey-algo> <hash-algo> ...
    validsigs = []
    for line in status.splitlines():
        if line.startswith("[GNUPG:] VALIDSIG "):
            parts = line.split()
            if len(parts) > 2:
                validsigs.append(parts[2])
    if r.returncode != 0 or not validsigs:
        tail = " ".join((err + " " + status).split())[:200]
        # a cryptographically bad signature must never be SIGNED
        return {"artifact": artifact.name, "state": "INVALID",
                "detail": f"signature does not verify: {tail}",
                "signature": sig.name}
    # cryptographically valid — now bind to the APPROVED signer
    for fp in validsigs:
        if fp == expected_fp:
            return {"artifact": artifact.name, "state": "SIGNED",
                    "detail": f"good signature from approved signer ({sig.name})",
                    "signature": sig.name, "signer_fingerprint": fp,
                    "approved": True}
    return {"artifact": artifact.name, "state": "UNTRUSTED_SIGNER",
            "detail": (f"valid signature but signer "
                       f"{', '.join(validsigs[:3])} is NOT the approved "
                       f"fingerprint"), "signature": sig.name,
            "signer_fingerprint": validsigs[0],
            "approved": False}


# ---------------------------------------------------------------------------
# Release signing + source-hygiene workflow (Packet 8K-P signing support)
#
# Signing is OPTIONAL and school-only compatible. This is the generation and
# verification surface for the documented detached-signature workflow:
#   - SHA256SUMS (SHA-256 checksums for every wheel/sdist);
#   - a signed release manifest (release-manifest.json + .asc);
#   - per-artifact detached ASCII-armored signatures (<artifact>.asc);
#   - gpg --verify based verification bound to the APPROVED public
#     fingerprint (RELEASE_SIGNER_FINGERPRINT / config/release-signer.fingerprint).
# The PUBLIC signer fingerprint is recorded; the PRIVATE key is never
# generated, committed, printed, uploaded, or exposed here.
# ---------------------------------------------------------------------------
SHA256SUMS_NAME = "SHA256SUMS"
RELEASE_MANIFEST_NAME = "release-manifest.json"
RELEASE_MANIFEST_SCHEMA = 1


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_artifacts(artifact_dir: Path) -> List[Path]:
    """Wheel + sdist artifacts in a directory (sorted, deterministic)."""
    d = Path(artifact_dir)
    if not d.is_dir():
        return []
    out: List[Path] = []
    for suffix in (".whl", ".tar.gz"):
        out.extend(sorted(d.glob(f"*{suffix}")))
    return out


def generate_checksums(artifact_dir: Path) -> Dict[str, Any]:
    """Write ``SHA256SUMS`` (``<sha256>  <filename>``) for every wheel/sdist.
    Always safe and unsigned. Typed: OK / MISSING / FAILED."""
    arts = collect_artifacts(artifact_dir)
    if not arts:
        return {"state": "MISSING",
                "detail": f"no wheel/sdist artifacts in {artifact_dir}",
                "path": None, "files": []}
    try:
        lines = [f"{_sha256_file(a)}  {a.name}" for a in arts]
        out = Path(artifact_dir) / SHA256SUMS_NAME
        out.write_text("\n".join(lines) + "\n")
        return {"state": "OK", "path": str(out),
                "files": [a.name for a in arts]}
    except OSError as exc:
        return {"state": "FAILED", "detail": f"checksum write failed: {exc}"}


def _read_checksums(artifact_dir: Path) -> Dict[str, str]:
    p = Path(artifact_dir) / SHA256SUMS_NAME
    if not p.is_file():
        return {}
    out: Dict[str, str] = {}
    for line in p.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            out[parts[1]] = parts[0].lower()
    return out


def verify_checksums(artifact_dir: Path) -> Dict[str, Any]:
    """Recompute artifact hashes and compare to ``SHA256SUMS``. Typed:
    OK / MISSING / FAILED (exact mismatches and missing files listed)."""
    expected = _read_checksums(artifact_dir)
    if not expected:
        return {"state": "MISSING",
                "detail": f"{SHA256SUMS_NAME} not present in {artifact_dir}"}
    mismatches: List[str] = []
    missing: List[str] = []
    for name, want in expected.items():
        p = Path(artifact_dir) / name
        if not p.exists():
            missing.append(name)
            continue
        if _sha256_file(p) != want:
            mismatches.append(name)
    if mismatches or missing:
        return {"state": "FAILED",
                "detail": "checksum mismatch (artifact modified): "
                          + ", ".join(mismatches + missing),
                "mismatches": mismatches, "missing": missing}
    return {"state": "OK",
            "detail": f"{len(expected)} artifact SHA-256 hashes match "
                      f"{SHA256SUMS_NAME}"}


def _gpg_env(homedir: Optional[Path] = None,
             env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    out = os.environ.copy() if env is None else dict(env)
    if homedir is not None:
        out["GNUPGHOME"] = str(homedir)
    return out


def sign_detached(artifact: Path, *, homedir: Optional[Path] = None,
                  env: Optional[Dict[str, str]] = None,
                  key: Optional[str] = None) -> Dict[str, Any]:
    """Create a detached ASCII-armored signature ``<artifact>.asc`` with gpg
    (``gpg --detach-sign --armor``). Never exposes key material. Typed:
    SIGNED / FAILED / DEGRADED."""
    import shutil
    gpg = shutil.which("gpg")
    if not gpg:
        return {"artifact": artifact.name, "state": "DEGRADED",
                "detail": "gpg unavailable; cannot create a detached signature"}
    sig = Path(str(artifact) + ".asc")
    cmd = [gpg, "--batch", "--yes", "--armor", "--detach-sign"]
    if key:
        cmd += ["--local-user", str(key)]
    cmd += ["--output", str(sig), str(artifact)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           env=_gpg_env(homedir, env))
    except Exception as exc:
        return {"artifact": artifact.name, "state": "DEGRADED",
                "detail": f"gpg failed to run: {exc}"}
    if r.returncode != 0 or not sig.is_file():
        return {"artifact": artifact.name, "state": "FAILED",
                "detail": ("gpg detached-sign failed: "
                           + " ".join((r.stderr or r.stdout or "").split())[:200])}
    return {"artifact": artifact.name, "state": "SIGNED",
            "signature": sig.name, "detail": "detached signature written"}


def release_manifest(artifact_dir: Path,
                     env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The signed release-manifest payload (public data only: names, hashes,
    sizes, public signer fingerprint). Never contains secret key material."""
    return {
        "schema_version": RELEASE_MANIFEST_SCHEMA,
        "project": "qubit-voicebridge",
        "checksums": SHA256SUMS_NAME,
        "signer_fingerprint": approved_signer_fingerprint(env),
        "artifacts": [
            {"name": a.name, "sha256": _sha256_file(a),
             "size_bytes": a.stat().st_size}
            for a in collect_artifacts(artifact_dir)
        ],
    }


def generate_signed_manifest(artifact_dir: Path, *, homedir: Optional[Path] = None,
                             env: Optional[Dict[str, str]] = None,
                             key: Optional[str] = None) -> Dict[str, Any]:
    """Write ``release-manifest.json`` and its detached signature
    ``release-manifest.json.asc``. The manifest itself is always written; only
    the detached signature needs gpg. Typed: SIGNED / DEGRADED / FAILED /
    MISSING."""
    if not collect_artifacts(artifact_dir):
        return {"state": "MISSING", "detail": "no artifacts",
                "path": None, "signature": None}
    mf = Path(artifact_dir) / RELEASE_MANIFEST_NAME
    try:
        mf.write_text(json.dumps(release_manifest(artifact_dir, env=env),
                                 indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        return {"state": "FAILED", "detail": f"manifest write failed: {exc}",
                "path": str(mf), "signature": None}
    sig = sign_detached(mf, homedir=homedir, env=env, key=key)
    return {"state": sig["state"], "path": str(mf),
            "signature": sig.get("signature") or None,
            "detail": sig["detail"],
            "signer_fingerprint": approved_signer_fingerprint(env)}


def sign_artifacts(artifact_dir: Path, *, homedir: Optional[Path] = None,
                   env: Optional[Dict[str, str]] = None,
                   key: Optional[str] = None) -> Dict[str, Any]:
    """Generate SHA-256 checksums, per-artifact detached signatures, and the
    signed release manifest for every wheel/sdist in ``artifact_dir``.
    Signing is OPTIONAL and never claims a result without verification —
    if any signature step fails the aggregate is DEGRADED, never SIGNED."""
    ck = generate_checksums(artifact_dir)
    if ck["state"] != "OK":
        return ck
    arts = collect_artifacts(artifact_dir)
    sig_results = [sign_detached(a, homedir=homedir, env=env, key=key)
                   for a in arts]
    mf = generate_signed_manifest(artifact_dir, homedir=homedir, env=env,
                                  key=key)
    all_signed = all(r["state"] == "SIGNED" for r in sig_results) and \
        mf["state"] == "SIGNED"
    return {"state": "SIGNED" if all_signed else "DEGRADED",
            "detail": ("all artifacts + manifest carry detached signatures"
                       if all_signed else
                       "checksums written; some detached signatures are "
                       "missing/failed (see per-artifact records)"),
            "checksums": ck, "signatures": sig_results, "manifest": mf}


def verify_release_signing(artifact_dir: Path, *,
                           expected_fp: Optional[str] = None,
                           homedir: Optional[Path] = None,
                           env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Verify checksums and every detached signature against the approved
    signer (``gpg --verify`` + VALIDSIG fingerprint binding). Never claims
    SIGNED unless ALL present artifacts verify against the approved
    fingerprint AND the checksums match.

    Typed aggregate: SIGNED / NOT_SIGNED / INVALID / UNTRUSTED_SIGNER /
    DEGRADED / FAILED / MISSING."""
    arts = collect_artifacts(artifact_dir)
    if not arts:
        return {"state": "MISSING",
                "detail": f"no wheel/sdist artifacts in {artifact_dir}"}
    ck = verify_checksums(artifact_dir)
    if ck["state"] != "OK":
        return {"state": "FAILED" if ck["state"] == "FAILED" else "MISSING",
                "detail": ck["detail"], "checksums": ck}
    v_env = _gpg_env(homedir, env)
    results = [verify_artifact_signature(a, expected_fp=expected_fp,
                                         env=v_env) for a in arts]
    states = [r["state"] for r in results]
    if "INVALID" in states or "UNTRUSTED_SIGNER" in states:
        return {"state": "INVALID" if "INVALID" in states
                else "UNTRUSTED_SIGNER",
                "detail": "; ".join(f"{r['artifact']}={r['state']}"
                                    for r in results),
                "artifacts": results, "checksums": ck}
    if all(s == "NOT_SIGNED" for s in states):
        return {"state": "NOT_SIGNED",
                "detail": "no detached signatures present; artifacts are "
                          "NOT_SIGNED", "artifacts": results, "checksums": ck}
    if "DEGRADED" in states or "FAILED" in states:
        return {"state": "DEGRADED",
                "detail": "; ".join(f"{r['artifact']}={r['state']}: "
                                    f"{r['detail']}" for r in results),
                "artifacts": results, "checksums": ck}
    if all(s == "SIGNED" for s in states):
        return {"state": "SIGNED",
                "detail": f"all {len(arts)} artifacts verify against the "
                          "approved signer (gpg --verify + VALIDSIG)",
                "artifacts": results, "checksums": ck}
    return {"state": "DEGRADED", "detail": "mixed signature states",
            "artifacts": results, "checksums": ck}


def certify(repo: Optional[Path] = None) -> Dict[str, Any]:
    """Hermetic static certification: provenance, scans, contracts, docs,
    units, ceilings. Does NOT build/install (that is the orchestrator's opt-in
    step). Returns the typed report."""
    root = Path(repo) if repo else REPO
    provenance = git_provenance(root)
    mpaths = machine_path_scan(root)
    secrets = secret_scan(root)
    report: Dict[str, Any] = {
        "packet": "8K",
        "provenance": provenance,
        "toolchain": toolchain(),
        "dependency": sbom(root),
        "machine_paths": {
            "core": mpaths["core"],
            "personalization": mpaths["personalization"],
        },
        "secrets": secrets,
        "signature": artifact_signature_status(),
        "filesystem_modes": filesystem_modes_check(root),
        "resource_ceilings": resource_ceilings(root),
        "operator_runbooks": operator_runbook_check(root),
        "backup_migration": backup_migration_check(root),
        "bounded_logs": bounded_logs_check(root),
        "units": unit_verify(root),
        "support_matrix": support_matrix_check(root),
    }
    return report


def report_pass(report: Dict[str, Any]) -> bool:
    """True when every required gate is OK (core machine paths, secrets,
    filesystem modes, runbooks, migration, units). Personalization machine
    values are recorded but not a gate failure."""
    if report["machine_paths"]["core"]:
        return False
    if report["secrets"]:
        return False
    for check in report["filesystem_modes"]:
        if check["state"] in ("FAIL", "MISSING"):
            return False
    for check in report["operator_runbooks"]:
        if check["state"] in ("FAIL", "MISSING"):
            return False
    for check in report["backup_migration"]:
        if check["state"] in ("FAIL", "MISSING"):
            return False
    for check in report["bounded_logs"]:
        if check["state"] in ("FAIL", "MISSING"):
            return False
    for check in report["support_matrix"]:
        if check["state"] in ("FAIL", "MISSING", "NOT_SUPPORTED"):
            return False
    if not report["units"].get("all_ok", False):
        return False
    return True


# --------------------------------------------- policy workspace conformance
def workspace_conformance(root: Optional[Path] = None, *,
                          rules: Optional[List[Dict[str, Any]]] = None
                          ) -> Dict[str, Any]:
    """Policy workspace-conformance doctor (OC-D02).

    Thin, shared entry point over
    :mod:`voicebridge.evaluation.policy_conformance` so reports, the CLI, and
    the dashboard all evaluate the same ruleset and produce the same findings.
    """
    from .evaluation.policy_conformance import doctor

    return doctor(Path(root) if root else REPO, rules)


def workspace_attestation(root: Optional[Path] = None, *,
                          generated_at: str = "") -> Dict[str, Any]:
    """Content-addressed conformance attestation for a workspace (OC-D02)."""
    from .evaluation.policy_conformance import attestation

    return attestation(Path(root) if root else REPO,
                       generated_at=generated_at)
