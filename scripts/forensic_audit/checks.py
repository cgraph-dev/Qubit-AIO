"""Deterministic Tier-0 checks for the Qubit-vs-upstream forensic audit.

Each check is a pure function ``fn(repo: Path, ctx: dict) -> list[Finding]``.
Checks must be **read-only** and **deterministic**: no wall-clock, no network,
no writes. Slow checks that shell out (generator ``--check`` runs, isolated
imports) are marked ``slow=True`` and can be skipped with ``--fast``.

The checks are deliberately conservative (low false positives) and each encodes
one recurring failure *class* from the 2026-09-25 forensic audit, so a re-run
catches a regression rather than re-deriving an opinion.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List

try:  # support both ``import forensic_audit.checks`` and direct-path import
    from .schema import finding
except ImportError:  # pragma: no cover
    from schema import finding  # type: ignore

# --------------------------------------------------------------------------- #
# constants encoding the audit contracts
# --------------------------------------------------------------------------- #
REQUIRED_CORE_TOOLS = [
    "browser_vault_list", "browser_vault_unlock", "browser_vault_fill",
    "browser_vault_save_login", "browser_vault_enter_code", "manage_connections",
]

# Overlay files that legitimately mention the reference home (guards / read-only
# migration sources). Anything else in the overlay is a fallback candidate.
HOME_FALLBACK_ALLOWLIST = {
    "voicebridge/qubit_runtime.py",     # pin_qubit_home boundary
    "voicebridge/qubit_legacy.py",      # equality guard, read-only source
    "voicebridge/update_channel.py",    # legacy-URL guard
    "voicebridge/qubit_session_store.py",
    "voicebridge/qubit_migration.py",   # migration input, read-only
}

HOME_DEFAULT_PATTERNS = [
    (re.compile(r"getenv\(\s*[\"']QUBIT_HOME[\"']\s*,[^\n]*[\"']\.hermes[\"']"),
     "QUBIT_HOME default -> .hermes"),
    (re.compile(r"Path\.home\(\)\s*/\s*[\"']\.hermes[\"']"),
     "Path.home()/'.hermes'"),
    (re.compile(r"LOCALAPPDATA[^\n]*[\"']hermes[\"']"),
     "LOCALAPPDATA/hermes"),
]

# user-facing credential guidance that must not point at the external home
CREDENTIAL_MESSAGE_RE = re.compile(r"\.hermes[/\\]\.env")

STALE_GENERATORS = [
    ("parity_inventory", "scripts/parity_inventory.py"),
    ("voice_coverage", "scripts/voice_inventory_coverage.py"),
    ("page_coverage", "scripts/page_coverage.py"),
    ("compat_handler_reachability", "scripts/compat_handler_reachability.py"),
]

_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache",
              "qubit-dist", "dist", ".ruff_cache", ".mypy_cache"}

CheckFn = Callable[[Path, Dict[str, Any]], List[Dict[str, Any]]]


def _iter_py(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def _rel(repo: Path, p: Path) -> str:
    try:
        return p.relative_to(repo).as_posix()
    except ValueError:
        return p.as_posix()


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _run(cmd: List[str], cwd: Path, timeout: int = 180,
         extra_env: Dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True,
                          text=True, timeout=timeout)


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def check_home_fallback(repo: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()

    # 1. overlay (voicebridge/) fallback candidates
    for p in _iter_py(repo / "voicebridge"):
        rel = _rel(repo, p)
        if rel in HOME_FALLBACK_ALLOWLIST:
            continue
        for i, line in enumerate(_read(p).splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            for pat, desc in HOME_DEFAULT_PATTERNS:
                if pat.search(line):
                    key = (rel, desc)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(finding(
                        "home_fallback", f"{rel}:{desc}".replace("/", "_"),
                        f"{desc} in overlay code",
                        path=rel, line=i, subsystem="independence",
                        detail=f"{rel}:{i}: {s[:160]}",
                        repro=f"rg -n '\\.hermes' {rel}",
                        severity="HIGH",
                        classification="MISCONFIGURED",
                        cause_type="accidental_reference_home_fallback"))

    # 2. vendored runtime default-home sites (targeted, not whole tree)
    vendored_targets = [
        "qubit_runtime/qubit_constants.py",
        "qubit_runtime/qubit_cli/env_loader.py",
        "qubit_runtime/tools/mcp_tool_config.py",
    ] + [ _rel(repo, Path(g)) for g in glob.glob(
        str(repo / "qubit_runtime" / "tools" / "bot_*.py")) ]
    for rel in vendored_targets:
        p = repo / rel
        if not p.is_file():
            continue
        for i, line in enumerate(_read(p).splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            for pat, desc in HOME_DEFAULT_PATTERNS:
                if pat.search(line):
                    key = (rel, desc)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(finding(
                        "home_fallback", f"{rel}:{desc}".replace("/", "_"),
                        f"vendored default home is the reference home ({desc})",
                        path=rel, line=i, subsystem="independence",
                        detail=f"{rel}:{i}: {s[:160]}",
                        repro=f"rg -n '\\.hermes' {rel}",
                        severity="HIGH",
                        classification="MISCONFIGURED",
                        cause_type="unrebranded_upstream_default"))
    return out


def check_credential_home_message(repo: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    root = repo / "qubit_runtime" / "qubit_cli"
    for p in _iter_py(root):
        rel = _rel(repo, p)
        for i, line in enumerate(_read(p).splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            if CREDENTIAL_MESSAGE_RE.search(line):
                out.append(finding(
                    "credential_home_message", rel.replace("/", "_"),
                    "user-facing credential guidance names the reference home",
                    path=rel, line=i, subsystem="brand",
                    detail=f"{rel}:{i}: {s[:160]}",
                    repro=f"rg -n '\\.hermes/\\.env' {rel}",
                    severity="MEDIUM",
                    classification="MISCONFIGURED",
                    cause_type="rebrand_incomplete"))
    return out


def check_version_skew(repo: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def ver(path: Path, pattern: str) -> str:
        m = re.search(pattern, _read(path))
        return m.group(1) if m else ""

    product = ver(repo / "pyproject.toml", r'(?m)^version\s*=\s*"([^"]+)"')
    runtime = ver(repo / "qubit_runtime" / "pyproject.toml",
                  r'(?m)^version\s*=\s*"([^"]+)"')
    runtime_dunder = ver(repo / "qubit_runtime" / "qubit_cli" / "__init__.py",
                         r"__version__\s*=\s*[\"']([^\"']+)[\"']")
    desktop = ver(repo / "qubit_runtime" / "apps" / "desktop" / "package.json",
                  r'"version"\s*:\s*"([^"]+)"')

    pin_tag = ""
    man = repo / "docs" / "generated" / "QUBIT_HERMES_VENDOR_MANIFEST.json"
    if man.is_file():
        import json
        try:
            pin_tag = (json.loads(_read(man)).get("upstream_tag") or "").lstrip("v")
        except ValueError:
            pin_tag = ""

    if runtime and pin_tag and runtime != pin_tag and runtime_dunder == runtime:
        out.append(finding(
            "version_skew", "runtime_vs_pin",
            f"vendored runtime reports {runtime} but the pin is v{pin_tag}",
            path="qubit_runtime/pyproject.toml",
            subsystem="packaging",
            detail=(f"product={product or '?'} runtime={runtime} "
                    f"__version__={runtime_dunder or '?'} pin=v{pin_tag or '?'} "
                    f"desktop={desktop or '?'}"),
            repro=("rg -n '^version' qubit_runtime/pyproject.toml; "
                   "rg -n '__version__' qubit_runtime/qubit_cli/__init__.py"),
            severity="MEDIUM", classification="MISCONFIGURED",
            cause_type="version_skew_after_pin_move"))
    return out


def check_cli_exit_code_contract(repo: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    src = _read(repo / "qubit_runtime" / "qubit_cli" / "oneshot.py")
    missing = [tok for tok in ("_oneshot_exit_code", "_INTERRUPTED_EXIT_CODE")
               if tok not in src]
    if not missing:
        return []
    return [finding(
        "cli_exit_code_contract", "oneshot",
        "qubit oneshot does not map interrupted/failed turns to exit codes",
        path="qubit_runtime/qubit_cli/oneshot.py",
        subsystem="cli",
        detail=("missing: " + ", ".join(missing) +
                "; an interrupted turn can exit 0 and a failed turn exits 1 not 2"),
        repro="rg -n '_oneshot_exit_code|_INTERRUPTED_EXIT_CODE' "
              "qubit_runtime/qubit_cli/oneshot.py",
        severity="HIGH", classification="BROKEN",
        cause_type="upstream_version_drift")]


def check_mcp_redaction_contract(repo: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    rel = "qubit_runtime/qubit_cli/mcp_config.py"
    src = _read(repo / rel)
    if not src:
        return []
    has_redactor = "redact_mcp_probe_text" in src or "redact_mcp_header_display" in src
    leaks = re.search(r"\[:4\]\s*\+\s*[\"']\*{3}[\"']\s*\+\s*\S+\[-4:\]", src)
    if has_redactor and not leaks:
        return []
    return [finding(
        "mcp_redaction_contract", "mcp_test",
        "qubit mcp test partially discloses credential material",
        path=rel, subsystem="mcp",
        detail=("no full redactor" if not has_redactor else "") +
               ("; first4+last4 header masking present" if leaks else ""),
        repro="rg -n 'redact_mcp_probe_text|\\[:4\\]' " + rel,
        severity="HIGH", classification="MISSING",
        cause_type="upstream_security_hardening_absent")]


def _find_function_span(src: str, name: str, limit: int = 140) -> str:
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"\s*def\s+{re.escape(name)}\s*\(", line):
            return "\n".join(lines[i:i + limit])
    return ""


def check_model_capability_contract(repo: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    rel = "voicebridge/qubit_compat.py"
    src = _read(repo / rel)
    span = _find_function_span(src, "h_model_info")
    if not span:
        return []
    derives = "get_model_capabilities" in span or "models_dev" in span
    hardcoded = re.search(r'"supports_reasoning"\s*:\s*False', span) or \
        re.search(r"'supports_reasoning'\s*:\s*False", span)
    if derives and not hardcoded:
        return []
    return [finding(
        "model_capability_contract", "model_info",
        "/api/model/info does not derive model capabilities from provider metadata",
        path=rel,
        subsystem="providers",
        detail=("hardcodes supports_reasoning/max_output/vision"
                if hardcoded else "does not call models_dev.get_model_capabilities"),
        repro="rg -n 'def h_model_info' -A 140 " + rel,
        severity="HIGH", classification="BROKEN",
        cause_type="hardcoded_value_replaces_provider_metadata")]


def check_default_tool_bundle(repo: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    script = (
        "import json,sys\n"
        "sys.path.insert(0,'qubit_runtime')\n"
        "try:\n"
        "    import toolsets as t\n"
        "    names=list(t.resolve_toolset('hermes-cli'))\n"
        "    print(json.dumps({'ok':True,'count':len(names),'names':names}))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'ok':False,'stage':'import','error':repr(e)}))\n"
    )
    with tempfile.TemporaryDirectory(prefix="qfa-") as tmp:
        env = {"QUBIT_HOME": tmp, "HOME": tmp}
        try:
            cp = _run([sys.executable, "-c", script], cwd=repo,
                      timeout=120, extra_env=env)
        except subprocess.TimeoutExpired:
            return [finding(
                "default_tool_bundle", "timeout",
                "could not resolve the default CLI tool bundle (timeout)",
                path="qubit_runtime/toolsets.py", subsystem="toolsets",
                severity="LOW",
                classification="UNKNOWN_REQUIRES_REPRODUCTION",
                cause_type="environment")]
        try:
            res = json.loads(cp.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return [finding(
                "default_tool_bundle", "unparseable",
                "could not resolve the default CLI tool bundle",
                path="qubit_runtime/toolsets.py", subsystem="toolsets",
                detail=(cp.stderr or "")[-300:],
                repro="PYTHONPATH=qubit_runtime python -c \"import toolsets;"
                      "print(len(toolsets.resolve_toolset('hermes-cli')))\"",
                severity="MEDIUM", classification="UNKNOWN_REQUIRES_REPRODUCTION",
                cause_type="import_or_environment")]
    if not res.get("ok"):
        return [finding(
            "default_tool_bundle", "unresolvable",
            "default CLI tool bundle could not be resolved",
            path="qubit_runtime/toolsets.py", subsystem="toolsets",
            detail=res.get("error", ""),
            repro="PYTHONPATH=qubit_runtime python -c \"import toolsets;"
                  "print(len(toolsets.resolve_toolset('hermes-cli')))\"",
            severity="MEDIUM", classification="DEPENDENCY_FAILURE",
            cause_type="import_or_environment")]
    names = set(res.get("names") or [])
    missing = [t for t in REQUIRED_CORE_TOOLS if t not in names]
    if missing:
        return [finding(
            "default_tool_bundle", "missing_registered_tools",
            "default CLI bundle omits registered tools",
            path="qubit_runtime/toolsets.py", subsystem="toolsets",
            detail=f"count={res.get('count')} missing={missing}",
            repro="PYTHONPATH=qubit_runtime python -c \"import toolsets;"
                  "print(sorted(toolsets.resolve_toolset('hermes-cli')))\"",
            severity="HIGH", classification="BROKEN",
            cause_type="upstream_version_drift")]
    return []


def check_installer_checksums(repo: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    rel = "scripts/install.sh"
    src = _read(repo / rel)
    if not src:
        return []
    downloads = len(re.findall(r"\b(curl|wget)\b", src))
    checksums = len(re.findall(r"sha256|shasum|checksum|gpg|minisign", src,
                               flags=re.IGNORECASE))
    if downloads and checksums == 0:
        return [finding(
            "installer_checksums", "downloads_unverified",
            "installer downloads artifacts without integrity verification",
            path=rel, subsystem="packaging",
            detail=f"{downloads} download command(s), 0 checksum checks",
            repro="rg -n 'sha256|shasum|checksum' scripts/install.sh",
            severity="MEDIUM", classification="MISCONFIGURED",
            cause_type="missing_integrity_verification")]
    return []


def check_dep_pinning(repo: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    src = _read(repo / "pyproject.toml")
    deps: List[str] = []
    in_deps = False
    for line in src.splitlines():
        if re.match(r"^\s*dependencies\s*=\s*\[", line):
            in_deps = True
        if in_deps:
            deps += re.findall(r'"([A-Za-z0-9_.\-]+)\s*(>=|<=|~=|>|<)[^"]*"', line)
            if line.strip() == "]":
                in_deps = False
    ranged = sorted({name for name, _op in deps})
    if len(ranged) < 5:
        return []
    return [finding(
        "dep_pinning", "ranged_deps",
        f"{len(ranged)} top-level dependencies are range-pinned",
        path="pyproject.toml", subsystem="packaging",
        detail="ranges: " + ", ".join(ranged[:20]),
        repro="rg -n '>=' pyproject.toml",
        severity="MEDIUM", classification="MISCONFIGURED",
        cause_type="non_reproducible_dependencies")]


def check_generated_staleness(repo: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, rel in STALE_GENERATORS:
        p = repo / rel
        if not p.is_file():
            continue
        try:
            cp = _run([sys.executable, rel, "--check"], cwd=repo, timeout=240)
        except subprocess.TimeoutExpired:
            out.append(finding(
                "generated_staleness", name,
                f"{name} --check timed out", path=rel, subsystem="generated_artifacts",
                severity="LOW", classification="UNKNOWN_REQUIRES_REPRODUCTION",
                cause_type="environment"))
            continue
        if cp.returncode != 0:
            out.append(finding(
                "generated_staleness", name,
                f"generated artifact is stale: {name}",
                path=rel, subsystem="generated_artifacts",
                detail=(cp.stderr or cp.stdout or "")[-200:],
                repro=f".venv/bin/python {rel} --check",
                severity="MEDIUM", classification="MISCONFIGURED",
                cause_type="stale_generated_manifest_or_concurrent_writers"))
    return out


CHECKS: List[Dict[str, Any]] = [
    {"id": "home_fallback", "tier": 0, "slow": False,
     "description": "reference-home (~/.hermes) default/fallback candidates",
     "fn": check_home_fallback},
    {"id": "credential_home_message", "tier": 0, "slow": False,
     "description": "user-facing credential guidance naming ~/.hermes",
     "fn": check_credential_home_message},
    {"id": "version_skew", "tier": 0, "slow": False,
     "description": "product/runtime/pin/desktop version skew",
     "fn": check_version_skew},
    {"id": "cli_exit_code_contract", "tier": 0, "slow": False,
     "description": "oneshot interrupted/failed exit-code contract",
     "fn": check_cli_exit_code_contract},
    {"id": "mcp_redaction_contract", "tier": 0, "slow": False,
     "description": "qubit mcp test credential redaction",
     "fn": check_mcp_redaction_contract},
    {"id": "model_capability_contract", "tier": 0, "slow": False,
     "description": "/api/model/info derives real capabilities",
     "fn": check_model_capability_contract},
    {"id": "default_tool_bundle", "tier": 0, "slow": True,
     "description": "default CLI bundle contains registered tools",
     "fn": check_default_tool_bundle},
    {"id": "installer_checksums", "tier": 0, "slow": False,
     "description": "installer download integrity verification",
     "fn": check_installer_checksums},
    {"id": "dep_pinning", "tier": 0, "slow": False,
     "description": "Qubit-owned dependency pinning",
     "fn": check_dep_pinning},
    {"id": "generated_staleness", "tier": 0, "slow": True,
     "description": "generated artifacts match current source",
     "fn": check_generated_staleness},
]

CHECK_BY_ID = {c["id"]: c for c in CHECKS}


def run_checks(repo: Path, *, fast: bool = False,
               only: List[str] | None = None,
               exclude: List[str] | None = None) -> List[Dict[str, Any]]:
    """Run the selected checks; a crashing check becomes a LOW finding."""
    out: List[Dict[str, Any]] = []
    for spec in CHECKS:
        cid = spec["id"]
        if only and cid not in only:
            continue
        if exclude and cid in exclude:
            continue
        if fast and spec["slow"]:
            continue
        try:
            out.extend(spec["fn"](repo, {}) or [])
        except Exception as exc:  # pragma: no cover - defensive
            out.append(finding(
                cid, "check_crashed",
                f"audit check {cid} crashed",
                subsystem="harness",
                detail=f"{type(exc).__name__}: {exc}",
                severity="LOW",
                classification="UNKNOWN_REQUIRES_REPRODUCTION",
                cause_type="harness_check_error"))
    return out
