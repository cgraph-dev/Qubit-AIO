#!/usr/bin/env python3
"""Deterministic upstream contractual-pin -> latest upgrade/diff manifest.

Owner decisions 2026-09-18 (upstream revision scope):

* preserve and support the contractual pinned revision;
* audit the latest release AND the latest default branch separately;
* keep a deterministic upgrade/diff manifest from the contractual pin to the
  latest supported revision.

This generator is read-only against a pristine upstream clone (it reads blobs
with ``git show <rev>:<path>`` and never checks anything out or writes to the
clone). It embeds no timestamps, so identical revisions produce byte-identical
output. It does not invent dispositions: every delta row is emitted ``OPEN`` so
the human/agent disposition pass can stamp IMPLEMENTED / SAFE_EQUIVALENT /
OPERATOR_ONLY with real proof, never a fabricated result.

Usage:
    python scripts/openclaw_upgrade_manifest.py \
        --clone /tmp/opencode/pinned/openclaw \
        --out docs/archive/2026-09-21/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json

Exit codes: 0 manifest written; 2 usage/verification error.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Revisions fixed by the owner decision: contractual pin, the audited observed
# pin, the latest release tag, and the latest default-branch head observed at
# generation time. The generator FAILS if a revision cannot be resolved, so a
# stale/unavailable tree can never silently produce a plausible manifest.
CONTRACTUAL_PIN = "3928bad9badfcb6c7d140530435e806fb8092190"  # v2026.9.2
OBSERVED_PIN = "3a9d69db306cd7f081e06254cb89c4bcc14a7107"      # v2026.9.4
LATEST_RELEASE_TAG = "v2026.9.5"
# Refreshed 2026-09-21 from live `git ls-remote`:
#   v2026.9.5^{} = ec9c1a13db8938e5a3eaa51fca2e981cde2395a9
#   HEAD (main)  = 395690e83c32a8e0547211a9b88a37470cf30d8b
# The previous values (c396abe6 / 135f934d) were stale: c396abe6 survives only
# on a validation branch, and 135f934d no longer exists upstream.
LATEST_RELEASE_COMMIT = "ec9c1a13db8938e5a3eaa51fca2e981cde2395a9"
LATEST_MAIN_COMMIT = "395690e83c32a8e0547211a9b88a37470cf30d8b"

GATEWAY_DESCRIPTOR = "src/gateway/methods/core-descriptors.ts"
# The canonical method surface is the ``CORE_GATEWAY_METHOD_SPECS`` table, not
# every quoted dotted string in the blob. A loose regex over the whole file
# captured the ``operator.read``/``write``/``admin``/... scope strings and
# dropped the non-dotted method names (``health``, ``connect``, ``send``, ...).
# Parse the array rows instead: each row starts with ``[`` and its first string
# literal is the method name (some rows are formatted multi-line).
ARRAY_START_RE = re.compile(r"CORE_GATEWAY_METHOD_SPECS\s*=\s*\[")
ARRAY_END = "\n] as const satisfies"
ROW_NAME_RE = re.compile(r'^\s*\[\s*"([^"]+)"', re.M)
# Qubit's real upstream-dialect RPC registry. A delta method is stamped
# IMPLEMENTED only on an EXACT registered-method match (mechanical, no hand
# waving); every other delta stays OPEN for the disposition pass.
QUBIT_RPC_REGISTRY_RE = re.compile(r'register\(\s*"([^"]+)"')
# The behavior-based dispositions (IMPLEMENTED / SAFE_EQUIVALENT /
# OPERATOR_ONLY, each with proof) live in
# ``voicebridge/gateway/openclaw_dispositions.py``; the generator reads that
# map instead of inferring from a bare registered-method name.
# Capability-bearing top-level areas (the manifest lists added files per area;
# dependency/build/vendor trees are intentionally excluded).
AREA_PREFIXES = ("src", "packages", "extensions", "skills", "config")

REVISION_LABELS = (
    ("contractual_pin", "v2026.9.2", CONTRACTUAL_PIN),
    ("observed_pin", "v2026.9.4", OBSERVED_PIN),
    ("latest_release", LATEST_RELEASE_TAG, LATEST_RELEASE_COMMIT),
    ("latest_main", "main", LATEST_MAIN_COMMIT),
)


def git(clone: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(clone), *args],
        capture_output=True, text=True, check=False,
    )
    if check and proc.returncode != 0:
        raise SystemExit(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.strip()[:300]}")
    return proc.stdout


def resolve(clone: Path, rev: str) -> str:
    out = git(clone, "rev-parse", f"{rev}^{{commit}}").strip()
    if not out:
        raise SystemExit(f"cannot resolve revision {rev!r} in {clone}")
    return out


def extract_methods(clone: Path, commit: str) -> list[str]:
    """Real method names of the ``CORE_GATEWAY_METHOD_SPECS`` table.

    Fails closed (SystemExit) if the canonical array cannot be located, so a
    renamed/moved descriptor can never silently yield an empty or partial
    surface. Returns names only: scope strings (``operator.*``), handler
    families, and policy objects are not methods.
    """
    blob = git(clone, "show", f"{commit}:{GATEWAY_DESCRIPTOR}")
    m = ARRAY_START_RE.search(blob)
    if m is None:
        raise SystemExit(
            f"{GATEWAY_DESCRIPTOR} at {commit[:12]} has no "
            "CORE_GATEWAY_METHOD_SPECS array")
    rest = blob[m.end():]
    end = rest.find(ARRAY_END)
    if end == -1:
        raise SystemExit(
            f"{GATEWAY_DESCRIPTOR} at {commit[:12]} has an unterminated "
            "CORE_GATEWAY_METHOD_SPECS array")
    names = sorted(set(ROW_NAME_RE.findall(rest[:end])))
    if not names:
        raise SystemExit(
            f"{GATEWAY_DESCRIPTOR} at {commit[:12]} parsed to zero methods")
    return names


def _as_paths(source) -> list[Path]:
    """Normalize a registry source (path, str, or iterable) to a path list."""
    if isinstance(source, (str, Path)):
        return [Path(source)]
    return [Path(p) for p in source]


def registry_sources(path: Path) -> list[Path]:
    """All registry files a disposition pass must scan for *path*.

    ``path`` is the canonical ``rpc.py``; the sibling
    ``openclaw_handlers`` package (one module per implemented family) is an
    extension of the same registry and must be scanned so each pinned method's
    evidence names the file that defines its handler.
    """
    paths = [path]
    package = path.parent / "openclaw_handlers"
    if package.is_dir():
        paths.extend(sorted(p for p in package.glob("*.py")
                            if p.name != "__init__.py"))
    return paths


def extract_qubit_registry(path) -> list[str]:
    """Registered method names across the Qubit upstream-dialect registries."""
    names: set = set()
    for p in _as_paths(path):
        if not p.is_file():
            continue
        names.update(QUBIT_RPC_REGISTRY_RE.findall(p.read_text(encoding="utf-8")))
    return sorted(names)


def _parse_rpc_handlers_multi(sources):
    """AST view across *sources*: ``method -> handler`` + funcs + consts + files.

    A bare registered method name is not proof. Returns the handler AST for
    each ``registry.register("method", <handler>)`` call, the merged map of
    locally-defined functions, module-level string constants, and the file that
    defines each method so IMPLEMENTED evidence is exact.
    """
    import ast

    funcs: dict = {}
    consts: dict = {}
    methods: dict = {}
    origins: dict = {}
    for path in _as_paths(sources):
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.setdefault(n.name, n)
        # Module-level ``NAME = "literal"`` constants, so a handler that
        # returns a named code constant is recognized as emitting that code.
        for n in tree.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                    and isinstance(n.targets[0], ast.Name) \
                    and isinstance(n.value, ast.Constant) \
                    and isinstance(n.value.value, str):
                consts.setdefault(n.targets[0].id, n.value.value)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "register"):
                continue
            if len(node.args) < 2:
                continue
            name = node.args[0]
            if not (isinstance(name, ast.Constant)
                    and isinstance(name.value, str)):
                continue
            handler = node.args[1]
            # A bare name resolves to its local FunctionDef so the behavior
            # checks walk the real body; a lambda/other expression is kept.
            if isinstance(handler, ast.Name) and handler.id in funcs:
                handler = funcs[handler.id]
            # Later files/registrations win (matches MethodRegistry.register).
            methods[name.value] = handler
            origins[name.value] = str(path).replace("\\", "/")
    return methods, funcs, consts, origins


def _parse_rpc_handlers(path: Path):
    """Backward-compatible 3-tuple view of a single registry file."""
    methods, funcs, consts, _ = _parse_rpc_handlers_multi(path)
    return methods, funcs, consts


def _reaches(node, funcs: dict, seen: set, predicate) -> bool:
    """True when *node* or any locally-called function satisfies *predicate*."""
    import ast

    for child in ast.walk(node):
        if predicate(child):
            return True
        if isinstance(child, ast.Call):
            fn = child.func
            name = fn.id if isinstance(fn, ast.Name) else None
            if name and name in funcs and name not in seen:
                seen.add(name)
                if _reaches(funcs[name], funcs, seen, predicate):
                    return True
    return False


def _emits_code(node, funcs: dict, code: str, consts: dict) -> bool:
    """True when the handler (or a local function it calls) returns *code*.

    Matches both a string literal and a module-level named constant assigned
    that literal (e.g. ``UPDATE_REPORT_CODE``), so the emitted-code proof
    cannot be satisfied by an unrelated constant name.
    """
    import ast

    def _pred(n):
        if isinstance(n, ast.Constant):
            return n.value == code
        return isinstance(n, ast.Name) and consts.get(n.id) == code

    return _reaches(node, funcs, set(), _pred)


def _is_non_trivial(node, funcs: dict) -> bool:
    """True when the handler does real work (a backend/attribute call/import).

    A stub that only constructs a literal dict is not an implementation.
    """
    import ast

    def _pred(n):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            return True
        return isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)

    return _reaches(node, funcs, set(), _pred)


def _load_dispositions():
    """The authoritative behavior record for the upstream delta methods.

    A method is only ``IMPLEMENTED`` when the behavior map says so AND the
    method is actually registered; a refusal row must carry a typed code. The
    map is data, so the generator stays deterministic and fails closed.
    """
    repo = Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from voicebridge.gateway import openclaw_dispositions as OD  # noqa: E402
    return OD.DISPOSITIONS


def _load_pinned_dispositions():
    """Behavior record for the contractual-pin method surface.

    Unlike the delta overlay, the pinned surface may derive ``IMPLEMENTED``
    mechanically: a pinned method that is actually registered in Qubit's RPC
    registry with a non-trivial handler is a real implementation. This map only
    carries explicit terminal records (and is expected to stay small); the
    default for the rest of the surface is ``OPEN``.
    """
    repo = Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from voicebridge.gateway import openclaw_dispositions as OD  # noqa: E402
    return getattr(OD, "PINNED_SURFACE_DISPOSITIONS", {})


def _architecture_gated():
    """Frozen architecture-gated OPERATOR_ONLY method names (owner amendment
    2026-09-22). Rows here are NOT terminal and stay OPEN until individually
    approved."""
    repo = Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from voicebridge.gateway import openclaw_dispositions as OD  # noqa: E402
    return getattr(OD, "OPERATOR_ONLY_ARCHITECTURE", frozenset())


def _display_source(sources) -> str:
    """Human-readable label for one or many registry sources."""
    paths = _as_paths(sources)
    return str(paths[0]).replace("\\", "/") if len(paths) == 1 else \
        ",".join(str(p).replace("\\", "/") for p in paths)


def build_overlay(added: list[str], registry: set[str],
                  registry_path) -> dict:
    """Behavior-based disposition overlay.

    Resolution order per delta method (each check is behavioral, not a name
    match):

    * an explicit behavior record whose status is IMPLEMENTED must resolve to a
      registered handler whose body performs real work (a backend attribute
      call or import), else it is downgraded to OPEN;
    * SAFE_EQUIVALENT / OPERATOR_ONLY records must declare a typed ``code`` AND
      the registered handler must actually emit that code string, else OPEN;
    * a method that is registered but has no behavior record stays OPEN (a bare
      name match is never proof of implementation);
    * everything else is OPEN.
    """
    dispositions = _load_dispositions()
    handler_nodes, funcs, consts, _ = _parse_rpc_handlers_multi(registry_path)
    label = _display_source(registry_path)
    overlay: dict = {}
    for method in added:
        rec = dispositions.get(method)
        if rec is not None:
            entry = dict(rec)
            status = str(entry.get("status", "OPEN"))
            if status == "OPERATOR_ONLY":
                # Owner amendment 2026-09-22: split the terminal external
                # prerequisite from the architecture-gated (non-terminal)
                # refusal; see _architecture_gated().
                entry = dict(entry)
                entry["status"] = (
                    "OPERATOR_ONLY_ARCHITECTURE"
                    if method in _architecture_gated()
                    else "OPERATOR_ONLY_EXTERNAL")
            node = handler_nodes.get(method)
            if status == "IMPLEMENTED" and method not in registry:
                entry = {
                    "status": "OPEN", "evidence": None,
                    "basis": ("behavior record claims IMPLEMENTED but no RPC "
                              "handler is registered; disposition pending"),
                }
            elif status == "IMPLEMENTED" and (
                    node is None or not _is_non_trivial(node, funcs)):
                entry = {
                    "status": "OPEN", "evidence": None,
                    "basis": ("behavior record claims IMPLEMENTED but the "
                              "registered handler performs no backend work "
                              "(stub or missing); disposition pending"),
                }
            elif status in ("SAFE_EQUIVALENT", "OPERATOR_ONLY") \
                    and not entry.get("code"):
                entry = {
                    "status": "OPEN", "evidence": None,
                    "basis": ("refusal disposition declares no typed code; "
                              "disposition pending"),
                }
            elif status in ("SAFE_EQUIVALENT", "OPERATOR_ONLY") \
                    and node is not None \
                    and not _emits_code(node, funcs, str(entry["code"]),
                                        consts):
                entry = {
                    "status": "OPEN", "evidence": None,
                    "basis": ("refusal disposition declares code "
                              f"{entry['code']!r} but the registered handler "
                              "never emits it; disposition pending"),
                }
            overlay[method] = entry
        elif method in registry:
            overlay[method] = {
                "status": "OPEN",
                "evidence": label,
                "basis": ("registered RPC method has no behavior record; "
                          "disposition pending"),
            }
        else:
            overlay[method] = {
                "status": "OPEN", "evidence": None,
                "basis": "no Qubit RPC registry match; disposition pending",
            }
    return overlay


def build_pinned_surface(pinned: list[str], registry: set[str],
                         registry_path) -> dict:
    """Full-surface coverage gate for the contractual-pin method table.

    Every pinned method gets exactly one status:

    * ``IMPLEMENTED`` — the method is registered in Qubit's canonical RPC
      registry AND its handler performs real backend work (a backend attribute
      call or import); a bare registered name is never enough.
    * ``SAFE_EQUIVALENT`` / ``OPERATOR_ONLY`` — an explicit record declares a
      typed ``code`` that the real registered handler actually emits (and, for
      a refusal, the exact counterpart/prerequisite); otherwise OPEN.
    * ``OPEN`` — no real registered handler backs it. This is the fail-closed
      default and is expected for most of the contractual-pin surface; it is
      reported, never hidden.
    """
    dispositions = _load_pinned_dispositions()
    handler_nodes, funcs, consts, origins = _parse_rpc_handlers_multi(
        registry_path)
    label = _display_source(registry_path)
    out: dict = {}
    for method in sorted(set(pinned)):
        rec = dispositions.get(method)
        node = handler_nodes.get(method)
        registered = method in registry and node is not None
        # An explicit terminal record wins over the mechanical default so a
        # real registered handler that returns a typed refusal (SAFE_EQUIVALENT
        # / OPERATOR_ONLY) is not mislabeled IMPLEMENTED. The record still has
        # to resolve to a registered handler that actually emits its code.
        if rec is not None:
            status = str(rec.get("status", "OPEN"))
            if status == "IMPLEMENTED":
                if registered and _is_non_trivial(node, funcs):
                    entry = {
                        "status": "IMPLEMENTED",
                        "evidence": rec.get("evidence",
                                            origins.get(method, label)),
                        "basis": rec.get("basis", (
                            "registered in the canonical Qubit gateway RPC "
                            "registry with a non-trivial backend handler")),
                    }
                    for key in ("qubit_counterpart",):
                        if rec.get(key):
                            entry[key] = rec[key]
                    out[method] = entry
                else:
                    out[method] = {
                        "status": "OPEN", "evidence": None,
                        "basis": ("explicit IMPLEMENTED record does not "
                                  "resolve to a registered non-trivial "
                                  "handler; disposition pending"),
                    }
                continue
            if status in ("SAFE_EQUIVALENT", "OPERATOR_ONLY"):
                entry = dict(rec)
                code = str(entry.get("code", ""))
                emits = node is not None and bool(code) and _emits_code(
                    node, funcs, code, consts)
                if emits:
                    if status == "OPERATOR_ONLY":
                        # Owner amendment 2026-09-22: architecture-gated
                        # refusals are NOT terminal; they stay OPEN until
                        # individually approved.
                        entry["status"] = (
                            "OPERATOR_ONLY_ARCHITECTURE"
                            if method in _architecture_gated()
                            else "OPERATOR_ONLY_EXTERNAL")
                    out[method] = entry
                else:
                    out[method] = {
                        "status": "OPEN", "evidence": None,
                        "basis": ("refusal disposition does not resolve to a "
                                  "registered handler emitting its typed "
                                  "code; disposition pending"),
                    }
                continue
            out[method] = {
                "status": "OPEN", "evidence": None,
                "basis": ("explicit disposal record is not backed by a "
                          "non-trivial registered handler; disposition "
                          "pending"),
            }
            continue
        if registered and _is_non_trivial(node, funcs):
            out[method] = {
                "status": "IMPLEMENTED",
                "evidence": origins.get(method, label),
                "basis": ("registered in the canonical Qubit gateway RPC "
                          "registry with a non-trivial backend handler"),
            }
            continue
        out[method] = {
            "status": "OPEN", "evidence": None,
            "basis": ("no registered Qubit RPC handler backed by a canonical "
                      "module; disposition pending"),
        }
    counts = {s: sum(1 for v in out.values() if v["status"] == s)
              for s in ("IMPLEMENTED", "SAFE_EQUIVALENT",
                        "OPERATOR_ONLY_EXTERNAL",
                        "OPERATOR_ONLY_ARCHITECTURE", "OPEN")}
    return {"counts": counts, "methods": out}


def added_files(clone: Path, base: str, head: str) -> dict:
    out = git(clone, "diff", "--name-status", "--diff-filter=A",
              base, head, "--", *AREA_PREFIXES)
    by_area: dict = {prefix: [] for prefix in AREA_PREFIXES}
    counts: dict = {prefix: 0 for prefix in AREA_PREFIXES}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        path = parts[-1]
        for prefix in AREA_PREFIXES:
            if path == prefix or path.startswith(prefix + "/"):
                counts[prefix] += 1
                if len(by_area[prefix]) < 500:
                    by_area[prefix].append(path)
                break
    return {"counts": counts, "files": by_area}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clone", required=True,
                    help="pristine read-only upstream clone")
    ap.add_argument("--out", default="docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json")
    ap.add_argument("--qubit-rpc-registry",
                    default="voicebridge/gateway/rpc.py",
                    help="Qubit upstream-dialect RPC registry source")
    args = ap.parse_args(argv)

    clone = Path(args.clone).resolve()
    if not (clone / ".git").exists():
        raise SystemExit(f"not a git clone: {clone}")

    revisions = {}
    for key, label, rev in REVISION_LABELS:
        revisions[key] = {"label": label, "requested": rev,
                          "commit": resolve(clone, rev)}
    if revisions["contractual_pin"]["commit"] != CONTRACTUAL_PIN:
        raise SystemExit("contractual pin mismatch")

    license_text = git(clone, "show", f"{CONTRACTUAL_PIN}:LICENSE")
    package_json = json.loads(
        git(clone, "show", f"{CONTRACTUAL_PIN}:package.json"))
    latest_package = json.loads(
        git(clone, "show", f"{LATEST_RELEASE_COMMIT}:package.json"))

    methods = {key: extract_methods(clone, info["commit"])
               for key, info in revisions.items()}

    contractual = set(methods["contractual_pin"])
    deltas = {}
    for key in ("observed_pin", "latest_release", "latest_main"):
        current = set(methods[key])
        deltas[key] = {
            "added": sorted(current - contractual),
            "removed": sorted(contractual - current),
        }

    # File-level delta base: contractual pin -> latest release.
    files_release = added_files(clone, CONTRACTUAL_PIN, LATEST_RELEASE_COMMIT)
    files_main = added_files(clone, CONTRACTUAL_PIN, LATEST_MAIN_COMMIT)

    registry_path = Path(args.qubit_rpc_registry)
    registry_paths = registry_sources(registry_path)
    registry = set(extract_qubit_registry(registry_paths))
    # Both the latest release AND the latest default branch are in scope
    # (owner decision 2026-09-18). Dispose their union so a newest-main-only
    # method can never be silently skipped.
    delta_union = sorted(set(deltas["latest_release"]["added"])
                         | set(deltas["latest_main"]["added"]))
    overlays = build_overlay(delta_union, registry, registry_paths)
    overlay_counts = {
        "IMPLEMENTED": sum(1 for v in overlays.values()
                           if v["status"] == "IMPLEMENTED"),
        "SAFE_EQUIVALENT": sum(1 for v in overlays.values()
                               if v["status"] == "SAFE_EQUIVALENT"),
        "OPERATOR_ONLY_EXTERNAL": sum(
            1 for v in overlays.values()
            if v["status"] == "OPERATOR_ONLY_EXTERNAL"),
        "OPERATOR_ONLY_ARCHITECTURE": sum(
            1 for v in overlays.values()
            if v["status"] == "OPERATOR_ONLY_ARCHITECTURE"),
        "OPEN": sum(1 for v in overlays.values() if v["status"] == "OPEN"),
    }

    # Full-surface coverage gate: the contractual-pin table is the contractual
    # surface. Every pinned method is disposed IMPLEMENTED / SAFE_EQUIVALENT /
    # OPERATOR_ONLY with real proof or explicitly OPEN; it is never silently
    # assumed covered by the delta overlay.
    pinned_surface = build_pinned_surface(
        methods["contractual_pin"], registry, registry_paths)
    pinned_counts = pinned_surface["counts"]

    manifest = {
        "schema": "qubit-openclaw-upgrade-manifest/1",
        "generated_by": "scripts/openclaw_upgrade_manifest.py",
        "description": (
            "Deterministic contractual-pin -> latest upgrade/diff manifest. "
            "Delta rows and the full contractual-pin method surface are "
            "disposed only with real proof; everything unproven is explicitly "
            "OPEN, never silently covered. No timestamp is embedded."),
        "license": {
            "id": "MIT",
            "copyright": license_text.splitlines()[0].strip()
            if license_text else "",
            "spdx_line": next((ln.strip() for ln in license_text.splitlines()
                               if "Copyright" in ln), ""),
        },
        "package_versions": {
            "contractual": package_json.get("version"),
            "latest_release": latest_package.get("version"),
        },
        "revisions": revisions,
        "gateway_method_surface": {
            "source": GATEWAY_DESCRIPTOR,
            "counts": {key: len(methods[key]) for key in methods},
            "contractual_pin": methods["contractual_pin"],
        },
        "delta": deltas,
        "added_files": {
            "contractual_to_latest_release": files_release,
            "contractual_to_latest_main": files_main,
        },
        "disposition_policy": {
            "allowed": ["IMPLEMENTED", "SAFE_EQUIVALENT",
                        "OPERATOR_ONLY_EXTERNAL",
                        "OPERATOR_ONLY_ARCHITECTURE"],
            "default": "OPEN",
            "note": ("A newer capability may only be stamped IMPLEMENTED / "
                     "SAFE_EQUIVALENT / OPERATOR_ONLY with a real Qubit "
                     "production caller and proof; OPEN is never a silent "
                     "substitute for a disposition."),
        },
        "qubit_rpc_registry": {
            "source": str(registry_path).replace("\\", "/"),
            "sources": [str(p).replace("\\", "/") for p in registry_paths],
            "methods": sorted(registry),
        },
        "disposition_overlay": {
            "scope": ("delta.latest_release.added \u222a "
                      "delta.latest_main.added"),
            "counts": overlay_counts,
            "methods": overlays,
        },
        "pinned_surface_disposition": {
            "scope": "gateway_method_surface.contractual_pin",
            "policy": {
                "terminal": ["IMPLEMENTED", "SAFE_EQUIVALENT",
                             "OPERATOR_ONLY_EXTERNAL"],
                "not_terminal": ["OPERATOR_ONLY_ARCHITECTURE"],
                "fail_closed_default": "OPEN",
                "note": ("Every contractual-pin method gets exactly one "
                         "status. IMPLEMENTED requires a registered handler "
                         "that performs real backend work; a refusal requires "
                         "a real registered handler that emits its typed "
                         "code. OPEN is reported and expected; it is not a "
                         "disposition."),
            },
            "counts": pinned_counts,
            "methods": pinned_surface["methods"],
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"wrote {out}")
    print(f"methods: contractual={len(methods['contractual_pin'])} "
          f"latest_release={len(methods['latest_release'])} "
          f"latest_main={len(methods['latest_main'])}")
    print(f"delta added: release={len(deltas['latest_release']['added'])} "
          f"main={len(deltas['latest_main']['added'])} "
          f"removed: release={len(deltas['latest_release']['removed'])} "
          f"main={len(deltas['latest_main']['removed'])}")
    print(f"overlay(latest_release): IMPLEMENTED={overlay_counts['IMPLEMENTED']} "
          f"OPEN={overlay_counts['OPEN']}")
    disposed = (pinned_counts["IMPLEMENTED"]
                + pinned_counts["SAFE_EQUIVALENT"]
                + pinned_counts["OPERATOR_ONLY_EXTERNAL"])
    total = len(methods["contractual_pin"])
    print(f"pinned surface: {disposed}/{total} terminal, "
          f"{total - disposed}/{total} OPEN (architecture-gated) "
          f"(IMPLEMENTED={pinned_counts['IMPLEMENTED']} "
          f"SAFE_EQUIVALENT={pinned_counts['SAFE_EQUIVALENT']} "
          f"OPERATOR_ONLY_EXTERNAL={pinned_counts['OPERATOR_ONLY_EXTERNAL']} "
          f"OPERATOR_ONLY_ARCHITECTURE={pinned_counts['OPERATOR_ONLY_ARCHITECTURE']} "
          f"OPEN={pinned_counts['OPEN']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
