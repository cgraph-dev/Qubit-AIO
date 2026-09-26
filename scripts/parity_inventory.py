#!/usr/bin/env python3
"""Deterministic parity-inventory generator (``qubit-parity-inventory/1``).

The parity inventory is the finite to-do list behind ``docs/PARITY.md``: every
route / CLI command / dashboard control / tool the read-only reference agent
exposes, diffed against the working Qubit surface and classified as
``PORTED`` / ``GATED`` / ``MISSING``. This script replaces the earlier ad-hoc
inventory (an agent wrote it by hand and it drifted) with a re-runnable
generator.

Classification semantics (unchanged from the schema):

* ``PORTED``  -- a Qubit counterpart is registered and dispatches to a real
  handler/backend. This is a *registration* claim, not behavioral proof:
  ``e2e_verified`` stays ``false`` for the whole artifact.
* ``GATED``   -- a typed policy refusal (``qubit_compat.GATED`` /
  ``cli_qubit_forward.NATIVE_REFUSALS`` / an audited ``voice_unsupported``
  guard) or a voice/interface gap. Treated OPEN.
* ``MISSING`` -- no Qubit counterpart found in the enumerated surface.

Enumeration sources (all read-only):

* reference routes  -- ``@router.<verb>("…")`` decorators across
  ``~/.hermes/hermes-agent/hermes_cli/web_routers/*.py`` + ``web_server.py``;
  matched with the *real* ``voicebridge.qubit_compat.match()``.
* reference CLI     -- recursive ``hermes <cmd> --help`` discovery.
* Qubit CLI         -- recursive ``qubit agent <cmd> --help`` discovery +
  ``cli_qubit_forward`` refusal tables.
* reference controls-- the reference dashboard ``web/src/App.tsx`` client
  routes + ``web/src/pages/*.tsx``.
* Qubit controls    -- ``voicebridge.dashboard_ops.catalog()``.
* tools             -- reference ``optional-mcps/`` + ``skills/`` +
  ``hermes tools list``; Qubit ``qubit_runtime/optional-mcps/`` + the runtime
  skills dir + ``qubit agent tools list``.

Determinism: every row is sorted by ``(surface, side, id)``, ``json.dumps``
uses ``sort_keys=True``, and no timestamps are emitted. ``voice_intent`` values
are seeded from a pre-existing artifact (they are unvalidated candidates that
the separate ``scripts/voice_inventory_coverage.py`` resolves) so re-running
this generator does not churn the downstream voice-coverage artifact; rows with
no seed get a deterministic derived intent.

Usage::

    python scripts/parity_inventory.py
    python scripts/parity_inventory.py --out /tmp/parity.json
    python scripts/parity_inventory.py --check
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# --- read-only reference (never modified) ---------------------------------
# Discovered at runtime, never hard-coded to an operator checkout: set
# ``QUBIT_PARITY_REF_HOME`` to override, otherwise the conventional home.
REF_HOME = Path(
    os.environ.get("QUBIT_PARITY_REF_HOME") or (Path.home() / ".hermes")
).expanduser()
REF_AGENT = REF_HOME / "hermes-agent"
REF_HERMES = REF_AGENT / "venv" / "bin" / "hermes"
REF_SKILLS = REF_HOME / "skills"
REF_OPTIONAL_MCPS = REF_AGENT / "optional-mcps"
REF_DASHBOARD = "http://127.0.0.1:9119/?profile=default"

# --- Qubit (this worktree) -------------------------------------------------
# Resolve the Qubit CLI robustly instead of assuming a checkout-local
# ``.venv``: the artifact and its evidence must not embed the absolute path of
# whichever checkout/interpreter happened to render it (the stale committed
# copy embedded ``/tmp/qubit-w4``). Preference order: explicit override env,
# the running interpreter's sibling console script (a venv install), the
# checkout venv, then ``$PATH``.
def _resolve_qubit_bin() -> str:
    candidates: List[Path] = []
    for env_name in ("QUBIT_PARITY_QUBIT_BIN", "QUBIT_BIN"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value).expanduser())
    candidates.append(Path(sys.executable).parent / "qubit")
    candidates.append(REPO_ROOT / ".venv" / "bin" / "qubit")
    which = shutil.which("qubit")
    if which:
        candidates.append(Path(which))
    for cand in candidates:
        try:
            if (cand.is_file() and os.access(cand, os.X_OK)
                    and cand.name.startswith("qubit")):
                return str(cand)
        except OSError:
            continue
    # Last-resort historical default; consumers see an empty CLI surface rather
    # than a traceback if even this is absent.
    return str(REPO_ROOT / ".venv" / "bin" / "qubit")


QUBIT_BIN = _resolve_qubit_bin()
# Checkout-neutral labels recorded in the artifact's evidence: the command name
# is stable across machines, the resolved path is not.
QUBIT_BIN_LABEL = "qubit"
REF_HERMES_LABEL = "hermes"
QUBIT_SKILLS_LABEL = "$QUBIT_HOME/skills"
QUBIT_OPTIONAL_MCPS = REPO_ROOT / "qubit_runtime" / "optional-mcps"
QUBIT_RUNTIME_SKILLS = (
    Path.home() / ".local" / "state" / "qubit-voicebridge" / "runtime" / "skills"
)
QUBIT_FRONTEND_APP = REPO_ROOT / "frontend" / "qubit-web" / "src" / "App.tsx"
QUBIT_FRONTEND_PAGES = REPO_ROOT / "frontend" / "qubit-web" / "src" / "pages"


def _home_relative(path: Path) -> str:
    """Render *path* as ``~/...`` when it lives under ``$HOME``.

    The artifact records where evidence was observed. Absolute host paths make
    the artifact non-reproducible across machines, so user-scoped paths are
    stored home-relative; an explicit out-of-home override is kept verbatim.
    """
    try:
        return "~/" + Path(path).relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


DEFAULT_OUT = REPO_ROOT / "docs" / "generated" / "parity_inventory.json"
SCHEMA = "qubit-parity-inventory/1"

# Reference->Qubit renames that are not identity but are real counterparts.
OPTIONAL_MCP_RENAME = {"n8n-official": "n8n"}
SKILL_RENAME = {"hermes-voice-mode": "qubit-voice-mode"}

# Vendored fork commands whose parser exists but whose handler is an audited
# operator-owned typed refusal (registered != working).
FORK_TYPED_REFUSALS = {
    ("auth", "upgrade"): "AUTH_UPGRADE_OPERATOR_OWNED",
}

# Native Qubit families that are typed refusals on the ``qubit`` surface (the
# refusal envelope names the Qubit-native counterpart). Imported at runtime from
# ``cli_qubit_forward``; this is the fallback used only if the import fails.
_NATIVE_REFUSAL_FALLBACK = (
    "serve", "resume", "dashboard", "uninstall", "login", "logout", "send",
    "whatsapp", "whatsapp-cloud",
)

_ROW_RE = re.compile(
    r"^    ([A-Za-z][A-Za-z0-9_\-]*)(?:\s+\([^)]*\))?(?:\s{2,}\S|\s*$)")
_REF_ROUTE_RE = re.compile(
    r'@\w+\.(get|post|put|delete|patch|websocket|route)\("([^"]+)"')
_REF_ROUTE_PATH_RE = re.compile(r"\{[^}]*\}")

_HELP_CACHE: Dict[Tuple[str, ...], str] = {}


# --------------------------------------------------------------------------- io


def _run(bin_argv: List[str], timeout: int = 45) -> str:
    """Run a help command and return stdout+stderr ("" on persistent failure).

    A transient subprocess failure (timeout under load, SIGKILL, empty I/O)
    must not silently erase a command from the inventory, because the artifact
    is compared byte-for-byte by ``--check`` and a single dropped ``--help``
    would flip rows to MISSING non-deterministically. ``--help``/``tools list``
    always produce output when they succeed, so an empty result is retried once
    before it is accepted as a real absence.
    """
    for _attempt in range(2):
        try:
            p = subprocess.run(bin_argv, capture_output=True, text=True,
                               timeout=timeout)
            out = p.stdout + p.stderr
            if out.strip():
                return out
        except Exception:
            continue
    return ""


def _help(bin_argv: Tuple[str, ...]) -> str:
    key = tuple(bin_argv)
    if key not in _HELP_CACHE:
        _HELP_CACHE[key] = _run(list(bin_argv) + ["--help"])
    return _HELP_CACHE[key]


def _prefetch_help(argv_list: List[Tuple[str, ...]]) -> None:
    """Fetch several ``--help`` outputs concurrently (deterministic content).

    The help text of an argparse command is a pure function of its argv, so
    fetching in parallel cannot change the result -- only the wall time. The
    cache is filled before any result is consumed, and consumers read by key.
    """
    pending = [a for a in argv_list if tuple(a) not in _HELP_CACHE]
    if not pending:
        return
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        for key, out in zip(pending, pool.map(
                lambda a: _run(list(a) + ["--help"]), pending)):
            _HELP_CACHE[tuple(key)] = out


def _subcommands(out: str) -> List[str]:
    """Parse the argparse ``positional arguments:`` block into subcommands."""
    subs: List[str] = []
    inpos = False
    for line in out.splitlines():
        if re.match(r"^positional arguments:", line):
            inpos = True
            continue
        if inpos and re.match(r"^[A-Za-z].*:$", line):
            break
        if inpos:
            m = _ROW_RE.match(line)
            if m:
                subs.append(m.group(1))
    return subs


def _cli_tree(base_argv: List[str]) -> List[Tuple[str, ...]]:
    """Recursively enumerate a CLI command tree via ``--help`` (no args run)."""
    tops = _subcommands(_help(tuple(base_argv)))
    _prefetch_help([tuple(base_argv + [t]) for t in tops])
    rows: List[Tuple[str, ...]] = [(t,) for t in tops]
    subs: List[Tuple[str, str]] = []
    for top in tops:
        for sub in _subcommands(_help(tuple(base_argv + [top]))):
            subs.append((top, sub))
    _prefetch_help([tuple(base_argv + [t, s]) for t, s in subs])
    rows += [(t, s) for t, s in subs]
    return rows


# ---------------------------------------------------------------- reference


def ref_route_files() -> List[Path]:
    files = sorted(glob.glob(str(REF_AGENT / "hermes_cli" / "web_routers" / "*.py")))
    files += [str(REF_AGENT / "hermes_cli" / "web_server.py")]
    return [Path(f) for f in files if Path(f).is_file()]


def ref_routes() -> List[Dict[str, Any]]:
    """Unique ``(method, path, file:line)`` reference route decorators."""
    seen: Dict[Tuple[str, str], str] = {}
    for f in ref_route_files():
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _REF_ROUTE_RE.finditer(text):
            method = m.group(1).upper()
            path = m.group(2)
            line = text.count("\n", 0, m.start()) + 1
            seen.setdefault((method, path), f"{_home_relative(f)}:{line}")
    return [{"method": m, "path": p, "evidence": ev}
            for (m, p), ev in sorted(seen.items(), key=lambda kv: (kv[0][0], kv[0][1]))]


def _probe(path: str) -> str:
    """Turn a FastAPI path template into a concrete sample path.

    Normalized exactly like ``qubit_compat.dispatch``: drop a query string and a
    trailing slash before substituting path params, so route classification
    consults the same canonical path the runtime dispatcher would serve. Without
    this, a decorator path with a trailing slash (or query) could be reported
    MISSING while ``qc.match`` resolves it -- the stale-artifact bug that showed
    ``GET /api/dashboard/plugins/catalog`` as MISSING after the real route had
    been registered.
    """
    clean = path.split("?", 1)[0]
    if len(clean) > 1:
        clean = clean.rstrip("/")
    return _REF_ROUTE_PATH_RE.sub("_p_", clean)


def _skill_dir_has_manifest(dirpath: Path) -> bool:
    """True when *dirpath* contributes at least one ``SKILL.md``.

    Mirrors the runtime's discovery (``agent.skill_utils.iter_skill_index_files``
    over ``SKILL.md``): a namespace directory with no skill manifest (e.g. the
    reference's empty ``skills/product/``) is NOT a skill and must not be
    enumerated as one. Falls back to a direct ``SKILL.md`` search when the
    vendored runtime cannot be imported.
    """
    try:
        from voicebridge import qubit_runtime

        qubit_runtime.ensure_path()
        from agent.skill_utils import iter_skill_index_files  # type: ignore

        return any(True for _ in iter_skill_index_files(dirpath, "SKILL.md"))
    except Exception:
        return any(dirpath.rglob("SKILL.md"))


def _skill_dirs(root: Path) -> List[Path]:
    """Non-hidden skill/category dirs under *root* that carry a manifest."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith(".")
                  and _skill_dir_has_manifest(p))


def ref_cli_rows() -> List[Tuple[str, ...]]:
    return _cli_tree([str(REF_HERMES)])


def ref_controls() -> List[Dict[str, Any]]:
    """Reference dashboard client routes + page modules."""
    rows: List[Dict[str, Any]] = []
    if (REF_AGENT / "web" / "src" / "App.tsx").is_file():
        text = (REF_AGENT / "web" / "src" / "App.tsx").read_text(
            encoding="utf-8", errors="replace")
        paths = set(re.findall(r'^\s*"(/[a-z-]+)":\s*\w+,', text, re.M))
        paths.add("/chat")
        for p in sorted(paths):
            rows.append({"kind": "client-route", "id": p})
    pages_dir = REF_AGENT / "web" / "src" / "pages"
    if pages_dir.is_dir():
        for p in sorted(pages_dir.glob("*.tsx")):
            if p.name.endswith(".test.tsx"):
                continue
            rows.append({"kind": "page", "id": p.name})
    return rows


def ref_toolsets() -> Dict[str, bool]:
    """Reference toolset name -> enabled (from ``hermes tools list``)."""
    out = _run([str(REF_HERMES), "tools", "list"])
    enabled: Dict[str, bool] = {}
    for line in out.splitlines():
        m = re.match(r"^\s*([✓✗])\s+(?:enabled|disabled)\s+(\S+)\s", line)
        if m:
            enabled[m.group(2)] = m.group(1) == "✓"
    return enabled


# -------------------------------------------------------------------- qubit


def qubit_routes():
    """The live Qubit route table (mirrors what ``dispatch`` consults)."""
    from voicebridge import qubit_compat as qc

    return qc


def qubit_catalog() -> List[Dict[str, Any]]:
    from voicebridge import dashboard_ops as ops

    return list(ops.catalog())


def qubit_toolsets() -> Dict[str, bool]:
    """Qubit toolset name -> enabled (from ``qubit agent tools list``)."""
    out = _run([str(QUBIT_BIN), "agent", "tools", "list"])
    enabled: Dict[str, bool] = {}
    for line in out.splitlines():
        m = re.match(r"^\s*([✓✗])\s+(?:enabled|disabled)\s+(\S+)\s", line)
        if m:
            enabled[m.group(2)] = m.group(1) == "✓"
    return enabled


def qubit_native_refusals() -> Dict[str, str]:
    try:
        from voicebridge import cli_qubit_forward as F

        return {k: v[0] for k, v in F.NATIVE_REFUSALS.items()}
    except Exception:
        return {k: "QUBIT_NATIVE_REFUSAL" for k in _NATIVE_REFUSAL_FALLBACK}


# ------------------------------------------------------------- voice intents


def _route_voice(path: str) -> str:
    segs = [s for s in path.split("/") if s and s != "api"]
    first = segs[0] if segs else "status"
    return "qubit " + first.replace("-", " ").replace("_", " ")


def _control_voice(op_id: str) -> str:
    rid = op_id[3:] if op_id.startswith("op ") else op_id
    return "qubit " + rid.replace("_", " ")


def _cmd_from_counterpart(counterpart: Optional[str]) -> str:
    if not counterpart:
        return ""
    return counterpart.split(" ->", 1)[0].strip()


# ----------------------------------------------------------------- classify


def build_routes(qc) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ref in ref_routes():
        method, path = ref["method"], ref["path"]
        probe = _probe(path)
        route, _params = qc.match(method, probe)
        if route is None and method == "WEBSOCKET":
            route, _params = qc.match("GET", probe)
        if route is None:
            classification = "MISSING"
            counterpart = None
            qev = "voicebridge/qubit_compat.py ROUTES: no match"
        else:
            classification = "GATED" if route["disposition"] == "gated" else "PORTED"
            counterpart = (f"{route['method']} {route['pattern']} -> "
                           f"{route['handler']}")
            qev = ("voicebridge/qubit_compat.py ROUTES "
                   f"disposition={route['disposition']} handler={route['handler']}")
        rows.append({
            "surface": "route",
            "side": "reference",
            "id": f"{method} {path}",
            "method": method,
            "classification": classification,
            "qubit_counterpart": counterpart,
            "voice_intent": _route_voice(path),
            "voice_intent_verified": False,
            "evidence": {"reference": ref["evidence"], "qubit": qev},
        })
    return rows


def build_cli() -> List[Dict[str, Any]]:
    ref_rows = ref_cli_rows()
    fork_rows = set(_cli_tree([str(QUBIT_BIN), "agent"]))
    native_refusals = qubit_native_refusals()
    rows: List[Dict[str, Any]] = []
    for parts in ref_rows:
        cmd = parts[0]
        if cmd in native_refusals:
            code = native_refusals[cmd]
            classification = "GATED"
            counterpart = (f"qubit {' '.join(parts)} -> typed refusal {code}")
        elif parts in FORK_TYPED_REFUSALS:
            code = FORK_TYPED_REFUSALS[parts]
            classification = "GATED"
            counterpart = (f"qubit agent {' '.join(parts)} -> typed refusal {code}")
        elif cmd == "chat":
            classification = "PORTED"
            counterpart = "qubit chat"
        elif cmd == "update":
            classification = "PORTED"
            counterpart = "qubit update"
        elif parts in fork_rows:
            classification = "PORTED"
            counterpart = f"qubit agent {' '.join(parts)}"
        else:
            classification = "MISSING"
            counterpart = None
        rows.append({
            "surface": "cli",
            "side": "reference",
            "id": "hermes " + " ".join(parts),
            "method": None,
            "classification": classification,
            "qubit_counterpart": counterpart,
            "voice_intent": _cmd_from_counterpart(counterpart),
            "voice_intent_verified": False,
            "evidence": {
                "reference": f"`{REF_HERMES_LABEL} {' '.join(parts)} --help`",
                "qubit": (f"`{QUBIT_BIN_LABEL} agent {' '.join(parts)} --help`"
                          if classification != "MISSING" else "no counterpart"),
            },
        })
    return rows


def build_controls(qc_unused=None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    app_text = (QUBIT_FRONTEND_APP.read_text(encoding="utf-8", errors="replace")
                if QUBIT_FRONTEND_APP.is_file() else "")
    for ref in ref_controls():
        if ref["kind"] == "client-route":
            path = ref["id"]
            present = f'"{path}":' in app_text
            classification = "PORTED" if present else "MISSING"
            counterpart = f"frontend/qubit-web/src/App.tsx route {path}" if present else None
            rows.append({
                "surface": "control",
                "side": "reference",
                "id": f"client-route {path}",
                "method": "GET",
                "classification": classification,
                "qubit_counterpart": counterpart,
                "voice_intent": "qubit " + path.strip("/").replace("-", " "),
                "voice_intent_verified": False,
                "evidence": {
                    "reference": f"{_home_relative(REF_AGENT)}/web/src/App.tsx",
                    "qubit": "frontend/qubit-web/src/App.tsx" if present else "no counterpart",
                },
            })
        else:
            name = ref["id"]
            present = (QUBIT_FRONTEND_PAGES / name).is_file()
            classification = "PORTED" if present else "MISSING"
            counterpart = f"frontend/qubit-web/src/pages/{name}" if present else None
            rows.append({
                "surface": "control",
                "side": "reference",
                "id": f"page {name}",
                "method": None,
                "classification": classification,
                "qubit_counterpart": counterpart,
                "voice_intent": "qubit " + name[:-4].lower(),
                "voice_intent_verified": False,
                "evidence": {
                    "reference": f"{_home_relative(REF_AGENT)}/web/src/pages/{name}",
                    "qubit": counterpart or "no counterpart",
                },
            })
    for op in qubit_catalog():
        interfaces = list(op.get("interfaces") or [])
        gated = "voice" not in interfaces
        rows.append({
            "surface": "control",
            "side": "qubit",
            "id": f"op {op['id']}",
            "method": None,
            "classification": "GATED" if gated else "PORTED",
            "qubit_counterpart": f"dashboard_ops.catalog[{op['id']}] backend={op.get('backend', '?')}",
            "voice_intent": _control_voice(f"op {op['id']}"),
            "voice_intent_verified": False,
            "interfaces": interfaces,
            "interface_reason": op.get("interface_reason") or None,
            "evidence": {
                "reference": "voicebridge/dashboard_ops.py catalog()",
                "qubit": f"interfaces={interfaces} backend=REGISTRY/EXTRA_OPS",
            },
        })
    return rows


def build_tools() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    q_optional = {p.name for p in QUBIT_OPTIONAL_MCPS.iterdir()
                  if p.is_dir() and not p.name.startswith(".")} \
        if QUBIT_OPTIONAL_MCPS.is_dir() else set()
    if REF_OPTIONAL_MCPS.is_dir():
        for p in sorted(REF_OPTIONAL_MCPS.iterdir()):
            if not p.is_dir() or p.name.startswith("."):
                continue
            name = p.name
            target = OPTIONAL_MCP_RENAME.get(name, name)
            present = target in q_optional
            rows.append({
                "surface": "tool", "side": "reference",
                "id": f"optional-mcp {name}", "method": None,
                "classification": "PORTED" if present else "MISSING",
                "qubit_counterpart": f"qubit_runtime/optional-mcps/{target}" if present else None,
                "voice_intent": f"qubit mcp install {name}",
                "voice_intent_verified": False,
                "evidence": {"reference": _home_relative(p),
                             "qubit": "qubit_runtime/optional-mcps/" if present else "no counterpart"},
            })
    q_skills = {p.name for p in _skill_dirs(QUBIT_RUNTIME_SKILLS)}
    for p in _skill_dirs(REF_SKILLS):
        name = p.name
        target = SKILL_RENAME.get(name, name)
        present = target in q_skills
        rows.append({
            "surface": "tool", "side": "reference",
            "id": f"skill {name}", "method": None,
            "classification": "PORTED" if present else "MISSING",
            "qubit_counterpart": f"skill dir {target}" if present else None,
            "voice_intent": f"qubit skills install {name}",
            "voice_intent_verified": False,
            "evidence": {"reference": _home_relative(p),
                         "qubit": f"{QUBIT_SKILLS_LABEL}/{target}" if present else "no counterpart"},
        })
    ref_ts = ref_toolsets()
    q_ts = qubit_toolsets()
    for name in sorted(ref_ts):
        if name not in q_ts:
            classification, counterpart = "MISSING", None
        elif q_ts[name]:
            classification, counterpart = "PORTED", f"toolset {name}"
        else:
            classification = "GATED"
            counterpart = f"toolset {name} (present, disabled in cli scope)"
        rows.append({
            "surface": "tool", "side": "reference",
            "id": f"toolset {name}", "method": None,
            "classification": classification,
            "qubit_counterpart": counterpart,
            "voice_intent": f"qubit tools enable {name}",
            "voice_intent_verified": False,
            "evidence": {
                "reference": f"`{REF_HERMES_LABEL} tools list`",
                "qubit": f"`{QUBIT_BIN_LABEL} agent tools list` enabled={q_ts.get(name)}",
            },
        })
    return rows


# ------------------------------------------------------------------ summary


def _reference_version() -> Tuple[str, str]:
    version = "unknown"
    try:
        m = re.search(r'^version\s*=\s*"([^"]+)"',
                      (REF_AGENT / "pyproject.toml").read_text(encoding="utf-8"),
                      re.M)
        if m:
            version = m.group(1)
    except OSError:
        pass
    sha = ""
    try:
        p = subprocess.run(["git", "-C", str(REF_AGENT), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=15)
        sha = p.stdout.strip()
    except Exception:
        sha = ""
    return version, sha


def _seed_intents(out: Path) -> Dict[Tuple[str, str], str]:
    """Reuse prior ``voice_intent`` candidates (unvalidated) when present."""
    seed: Dict[Tuple[str, str], str] = {}
    if not out.is_file():
        return seed
    try:
        prev = json.loads(out.read_text(encoding="utf-8"))
    except Exception:
        return seed
    for row in prev.get("rows") or []:
        key = (str(row.get("surface")), str(row.get("id")))
        val = row.get("voice_intent")
        if isinstance(val, str):
            seed[key] = val
    return seed


def _counts(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def bucket(items):
        b = {"total": len(items),
             "ported": sum(1 for r in items if r["classification"] == "PORTED"),
             "gated": sum(1 for r in items if r["classification"] == "GATED"),
             "missing": sum(1 for r in items if r["classification"] == "MISSING")}
        return b

    total = bucket(rows)
    total["by_surface"] = {
        s: bucket([r for r in rows if r["surface"] == s])
        for s in ("route", "cli", "control", "tool")
    }
    total["by_side"] = {
        s: bucket([r for r in rows if r["side"] == s])
        for s in ("reference", "qubit")
    }
    return total


def build() -> Dict[str, Any]:
    out = DEFAULT_OUT
    qc = qubit_routes()
    rows: List[Dict[str, Any]] = []
    rows += build_routes(qc)
    rows += build_cli()
    rows += build_controls()
    rows += build_tools()

    # Seed voice_intent candidates from the prior artifact so downstream
    # voice-coverage output only changes when the enumerated surface changes.
    seed = _seed_intents(out)
    for row in rows:
        key = (row["surface"], row["id"])
        if key in seed:
            row["voice_intent"] = seed[key]

    rows.sort(key=lambda r: (r["surface"], r["side"], str(r["id"])))

    version, sha = _reference_version()
    counts = _counts(rows)
    route_files = [p.name for p in ref_route_files()]
    page_cov = REPO_ROOT / "docs" / "generated" / "QUBIT_PAGE_COVERAGE_2026-09-21.json"
    page_rows = 0
    try:
        page_rows = int(json.loads(page_cov.read_text(encoding="utf-8"))
                        .get("summary", {}).get("rows", 0))
    except Exception:
        page_rows = 0

    from voicebridge import voice_commands as vc

    registry = getattr(vc, "REGISTRY", {})
    guards = getattr(vc, "VOICE_GUARDS", [])
    catalog = qubit_catalog()
    ref_cli = ref_cli_rows()
    ref_ts = ref_toolsets()

    qubit_surface_summary = {
        "qubit_compat.ROUTES": len(qc.ROUTES),
        "qubit_compat.GATED": sum(1 for r in qc.ROUTES if r["disposition"] == "gated"),
        "dashboard_ops.catalog": len(catalog),
        "dashboard_ops.EXTRA_OPS": len(getattr(
            __import__("voicebridge.dashboard_ops", fromlist=["x"]), "EXTRA_OPS", {})),
        "dashboard_ops.MODEL_PROFILE_OPS": len(getattr(
            __import__("voicebridge.dashboard_ops", fromlist=["x"]), "MODEL_PROFILE_OPS", {})),
        "voice_commands.REGISTRY": len(registry),
        "voice_commands.VOICE_GUARDS": len(guards),
        "qubit_agent_top": len(_subcommands(_help((str(QUBIT_BIN), "agent", "--help")))),
        "qubit_voice_top": len(_subcommands(_help((str(QUBIT_BIN), "--help")))),
        "reference_routes": len(ref_routes()),
        "reference_cli_top": len([p for p in ref_cli if len(p) == 1]),
        "reference_cli_sub": len([p for p in ref_cli if len(p) == 2]),
        "reference_skills": len(_skill_dirs(REF_SKILLS)),
        "reference_optional_mcps": len([p for p in REF_OPTIONAL_MCPS.iterdir()
                                        if p.is_dir() and not p.name.startswith(".")])
        if REF_OPTIONAL_MCPS.is_dir() else 0,
        "reference_toolsets": len(ref_ts),
        "page_coverage_rows": page_rows,
    }

    missing = [r["id"] for r in rows if r["classification"] == "MISSING"]
    gated_cli = [r["id"] for r in rows if r["surface"] == "cli"
                 and r["classification"] == "GATED"]
    known_gaps = [
        f"routes: {counts['by_surface']['route']['missing']} reference routes have no "
        "qubit_compat counterpart (see MISSING rows).",
        (f"cli: {counts['by_surface']['cli']['missing']} reference commands/subcommands "
         f"absent; {counts['by_surface']['cli']['gated']} are typed refusals on the "
         "Qubit-native surface."),
        "controls: Qubit dashboard_ops catalog rows lacking a voice interface are GATED "
        "under voice-for-everything.",
        f"tools: {counts['by_surface']['tool']['missing']} reference tool(s) absent; "
        f"{counts['by_surface']['tool']['gated']} toolset(s) present but disabled in the "
        "Qubit cli scope.",
        "e2e_verified is false for every row: registration/dispatch is not behavioral proof.",
    ]

    return {
        "schema": SCHEMA,
        "generated_from": {
            "reference": (f"hermes-agent {version} @ {sha} @ "
                          f"{_home_relative(REF_AGENT)} (read-only); "
                          f"live dashboard {REF_DASHBOARD}"),
            "reference_route_sources": route_files,
            "reference_cli": "`hermes --help` + per-command `--help` (recursive)",
            "qubit_sources": [
                "voicebridge/qubit_compat.py (ROUTES/GATED)",
                "voicebridge/dashboard_ops.py (catalog/EXTRA_OPS/MODEL_PROFILE_OPS)",
                "voicebridge/voice_commands.py (REGISTRY/VOICE_GUARDS)",
                "voicebridge/webui.py (QUBIT_WS_GATE_CODES)",
                "voicebridge/cli_qubit_forward.py (NATIVE_REFUSALS/FAMILY_FORWARD/CONFIRM_TABLE)",
                "`qubit agent --help` (recursive command tree)",
                "`qubit --help` (native top-level families)",
                "frontend/qubit-web/src/App.tsx + pages/",
            ],
            "generated_inputs": [
                str(page_cov.relative_to(REPO_ROOT)),
            ],
            "evidence_commands": [
                f"curl -s -o /dev/null -w '%{{http_code}}' {REF_DASHBOARD}  # 200",
                "rg '@\\w+\\.(get|post|put|delete|patch|websocket|route)\\(' "
                f"{_home_relative(REF_AGENT)}/hermes_cli/web_routers/*.py "
                f"{_home_relative(REF_AGENT)}/hermes_cli/web_server.py",
                f"{REF_HERMES_LABEL} --help  # recursive top + subcommands",
                f"{REF_HERMES_LABEL} tools list",
                f"ls {_home_relative(REF_SKILLS)}/",
                f"ls {_home_relative(REF_OPTIONAL_MCPS)}/",
                f"{QUBIT_BIN_LABEL} --help ; {QUBIT_BIN_LABEL} agent --help",
                f"{QUBIT_BIN_LABEL} agent tools list",
                "python -c 'from voicebridge import qubit_compat as qc; qc.match(method,path)'"
                "  # route classification",
            ],
        },
        "classification_semantics": {
            "PORTED": ("a Qubit counterpart is registered and dispatches to a real "
                       "handler/backend; NOT end-to-end verified"),
            "GATED": ("a typed policy refusal (qubit_compat.GATED / "
                      "cli_qubit_forward.NATIVE_REFUSALS / audited voice_unsupported "
                      "guard) or a voice/interface gap; treated OPEN"),
            "MISSING": "no Qubit counterpart found in the enumerated surface",
            "note": ("e2e_verified is false for the whole inventory; a page/catalog row/"
                     "registration is not behavioral proof"),
        },
        "e2e_verified": False,
        "counts": counts,
        "qubit_surface_summary": qubit_surface_summary,
        "known_gaps": known_gaps,
        "rows": rows,
    }


def dumps(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Regenerate the parity inventory.")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help=f"output path (default: {DEFAULT_OUT})")
    ap.add_argument("--check", action="store_true",
                    help="render in memory, compare with --out, write nothing")
    args = ap.parse_args(argv)

    report = build()
    payload = dumps(report)
    out = Path(args.out)

    if args.check:
        if not out.is_file():
            print(f"parity inventory missing: {out}", file=sys.stderr)
            return 1
        if out.read_text(encoding="utf-8") != payload:
            print(f"parity inventory stale: {out}", file=sys.stderr)
            return 1
        print(f"parity inventory up to date: {out}")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    c = report["counts"]
    print(f"parity inventory -> {out} | rows={c['total']} "
          f"ported={c['ported']} gated={c['gated']} missing={c['missing']} "
          f"by_surface={ {k: v['total'] for k, v in c['by_surface'].items()} }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
