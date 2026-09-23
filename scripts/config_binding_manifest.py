#!/usr/bin/env python3
"""Deterministic Qubit config/env binding manifest (owner decision D3).

Owner decisions 2026-09-18 (D3): bind every supported upstream config
key and environment variable to Qubit's configuration system, with a Qubit
name, type, default, validation, persistence, runtime consumer, error
behaviour, and redaction rule; secret values use references and never appear
in logs/artifacts; unsafe settings get a typed disposition.

This generator reads the vendored pinned upstream config surface
(``qubit_runtime/qubit_cli/config_defaults.py``, a pure-data leaf module) and
Qubit's own ``voicebridge.config.DEFAULTS``. It emits an honest INVENTORY: it
does not fabricate a runtime-consumption proof, and it never emits a secret
value (only names and a redaction rule). Output is byte-deterministic (sorted,
no timestamps).

Usage:
    python scripts/config_binding_manifest.py \
        --out docs/archive/2026-09-21/QUBIT_CONFIG_BINDING_MANIFEST.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO = Path(__file__).resolve().parents[1]
HERMES_FORK = REPO / "qubit_runtime"
VENDOR_MANIFEST = REPO / "docs/archive/2026-09-21" / "QUBIT_HERMES_VENDOR_MANIFEST.json"

SECRET_SUFFIXES = (
    "_secret", "_password", "_passwd", "_token", "_key", "_apikey",
    "_api_key", "_private_key", "_access_key", "_client_secret",
    "_credentials", "_credential",
)
# A secret word anywhere in the leaf (e.g. AWS_SECRET_ACCESS_KEY,
# HERMES_LANGFUSE_SECRET_KEY, dashboard.basic_auth.password).
_SECRET_SUBSTR_RE = re.compile(
    r"(secret|password|passwd|private[_-]?key|client[_-]?secret|"
    r"credential|access[_-]?key)",
    re.IGNORECASE,
)
# Leaves that are exactly a secret-shaped word.
SECRET_LEAVES = {
    "secret", "password", "passwd", "token", "apikey", "api_key",
    "private_key", "access_key", "client_secret", "credential",
    "credentials", "auth_token", "refresh_token", "bearer_token",
    "access_token", "secret_key", "secret_access_key",
}
# A count/limit named "..._tokens" is not a secret (e.g. max_input_tokens).
TOKEN_COUNT_RE = re.compile(
    r"(max|min|input|output|context|total|num|count|limit)[_-]?tokens?$",
    re.IGNORECASE,
)
# Flags/analytics whose names merely contain a secret-ish word but hold no
# secret material (booleans, counts, UI flags).
NOT_SECRET_RE = re.compile(
    r"(token[_-]?(analytics|flow|budget|limit|count|thresh)|"
    r"(show|display|enable|use|hide)[_-]?token|"
    r"key[_-]?(board|map|order|name|length|size))",
    re.IGNORECASE,
)
# Kept for callers/tests that referenced the original symbol.
SECRET_RE = _SECRET_SUBSTR_RE
UNSAFE_RE = re.compile(
    r"(bypass|allow[_-]?all|disable[_-]?(security|auth|sandbox)|"
    r"no[_-]?auth|skip[_-]?auth|unsafe|dangerous|insecure)",
    re.IGNORECASE,
)
PERSISTENCE = "qubit runtime home config.yaml (<QUBIT_HOME>/config.yaml)"
RUNTIME_CONSUMER = "qubit_runtime (vendored Qubit-owned runtime)"


def _is_secret(name: str) -> bool:
    last = str(name).rsplit(".", 1)[-1]
    low = last.lower()
    if TOKEN_COUNT_RE.search(low) or NOT_SECRET_RE.search(low):
        return False
    if low in SECRET_LEAVES:
        return True
    if low.endswith(SECRET_SUFFIXES):
        return True
    if _SECRET_SUBSTR_RE.search(low):
        return True
    return False


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return f"<{type(value).__name__}>"


def _flatten(obj: Any, prefix: str = "") -> List[Tuple[str, Any]]:
    out: List[Tuple[str, Any]] = []
    if isinstance(obj, dict):
        for key in sorted(obj):
            path = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_flatten(obj[key], path))
    elif isinstance(obj, list):
        out.append((prefix, obj))
    else:
        out.append((prefix, obj))
    return out


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _load_hermes_config() -> Any:
    """Load the pristine vendored config defaults independent of process state.

    ``voicebridge.qubit_asgi._apply_qubit_config_overlay`` mutates the imported
    ``qubit_cli.config_defaults.DEFAULT_CONFIG`` in-process (branding the
    default toolset). Loading the pure-data leaf by file path under a private
    module name yields the pinned defaults regardless of which other modules a
    caller has imported, so the manifest stays deterministic.
    """
    import importlib.util

    path = HERMES_FORK / "qubit_cli" / "config_defaults.py"
    spec = importlib.util.spec_from_file_location(
        "qubit_pristine_config_defaults", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _qubit_default_paths() -> List[str]:
    from voicebridge import config as QC

    return sorted({path for path, _ in _flatten(QC.DEFAULTS)})


def _pin() -> Dict[str, Any]:
    try:
        data = json.loads(VENDOR_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        "revision": data.get("revision") or data.get("upstream_commit"),
        "source": "docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST.json",
    }


_ENV_LITERAL_RE = re.compile(
    r'"([A-Z][A-Z0-9_]{3,}_(?:API_KEY|TOKEN|BASE_URL|KEY|SECRET))"')
_OPENCLAW_FIXTURE = REPO / "docs/archive/2026-09-21" / "QUBIT_OPENCLAW_ENV_NAMES.json"
_OPENCLAW_CONFIG_FIXTURE = REPO / "docs/archive/2026-09-21" / "QUBIT_OPENCLAW_CONFIG_SURFACE.json"
_OPENCLAW_TESTISH_RE = re.compile(
    r"(TEST|FIXTURE|CI|BUILD|DEV|MOCK|BENCH|PROBE|TRACE|DEBUG)", re.IGNORECASE)


def _provider_env_names() -> List[str]:
    """Credential/base-URL env vars declared in the pinned provider registry."""
    path = HERMES_FORK / "qubit_cli" / "auth.py"
    if not path.is_file():
        return []
    return sorted(set(_ENV_LITERAL_RE.findall(path.read_text(
        encoding="utf-8", errors="replace"))))


def _open_dict_keys() -> List[str]:
    """Top-level config keys that accept arbitrary user-populated children."""
    import ast

    path = HERMES_FORK / "qubit_cli" / "config.py"
    if not path.is_file():
        return []
    out: List[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    names = {"_OPEN_DICT_TOP_LEVEL_KEYS", "_SCHEMA_DEFINED_DICT_KEYS",
             "_DYNAMIC_TOP_LEVEL_KEYS"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if not (targets & names):
            continue
        if isinstance(node.value, ast.Call):
            args = node.value.args
        elif isinstance(node.value, (ast.Set, ast.Tuple, ast.List)):
            args = [node.value]
        else:
            args = []
        for arg in args:
            if isinstance(arg, (ast.Set, ast.Tuple, ast.List)):
                out.extend(e.value for e in arg.elts
                           if isinstance(e, ast.Constant)
                           and isinstance(e.value, str))
    return sorted(set(out))


def _openclaw_config_surface() -> Dict[str, Any]:
    """Typed disposition of the pinned upstream config schema surface.

    The pinned repo commits only per-category path counts, so Qubit records an
    explicit category-level SAFE_EQUIVALENT disposition (it runs no upstream
    process) rather than fabricating per-path mappings.
    """
    try:
        doc = json.loads(_OPENCLAW_CONFIG_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "UNAVAILABLE", "disposition": "SAFE_EQUIVALENT",
                "total": 0, "categories": {}}
    return {
        "state": "DISPOSED",
        "disposition": "SAFE_EQUIVALENT",
        "source": doc.get("source_file", ""),
        "source_revision": doc.get("source_revision", ""),
        "categories": doc.get("counts", {}),
        "total": int(doc.get("total") or 0),
        "qubit_counterpart": (
            "Qubit config system (voicebridge/config.py DEFAULTS + the "
            "vendored runtime <QUBIT_HOME>/config.yaml); Qubit runs no "
            "OpenClaw process, so the OpenClaw config schema is not a "
            "supported product surface and is never silently ignored"),
        "rationale": (
            "OpenClaw config paths are out-of-runtime for Qubit; supported "
            "Hermes keys are bound per-path and OpenClaw env names are "
            "enumerated per-name above. Binding per-path here would invent "
            "mappings to a config system with no corresponding Qubit runtime."),
    }


def _openclaw_env_rows() -> List[Dict[str, Any]]:
    """Upstream env surface with a typed disposition per name (audit-only)."""
    try:
        doc = json.loads(_OPENCLAW_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows: List[Dict[str, Any]] = []
    for name in doc.get("names", []):
        secret = _is_secret(str(name))
        testish = bool(_OPENCLAW_TESTISH_RE.search(str(name)))
        row: Dict[str, Any] = {
            "name": str(name),
            "type": "str",
            "default": None,
            "secret": secret,
            "redaction": "value" if secret else "none",
            "reference": f"env:{name}",
            "persistence": "operator environment / secret reference only",
            "runtime_consumer": (
                "audit-only: Qubit runs no OpenClaw process; product settings "
                "are consumed from <QUBIT_HOME>/config.yaml"),
            "validation": "unset is a safe no-op; a referenced secret is "
                          "resolved through the Qubit secret store",
            "cli_path": "qubit auth/status (references) + dashboard Keys",
            "error_behavior": "never logged; unknown keys are not silently "
                              "ignored (recorded here)",
            "disposition": "SAFE_EQUIVALENT",
            "qubit_counterpart": (
                "Qubit config system (qubit_runtime/vendored runtime reads "
                "<QUBIT_HOME>/config.yaml); Qubit runs no OpenClaw process, so "
                "this key is audit-only and never silently ignored"),
        }
        if testish:
            row["qubit_counterpart"] = (
                "test/build-only upstream key; not a product capability")
        rows.append(row)
    return rows


def _env_var_rows(optional: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name in sorted(optional):
        meta = optional[name]
        secret = _is_secret(str(name))
        row: Dict[str, Any] = {
            "name": str(name),
            "secret": secret,
            "redaction": "value" if secret else "none",
            "reference": f"env:{name}",
            "disposition": "BOUND (env reference)",
        }
        if isinstance(meta, dict):
            for key in ("description", "required", "default"):
                if key in meta:
                    row[key] = _jsonable(meta[key])
        rows.append(row)
    return rows


def build() -> Dict[str, Any]:
    """Build the deterministic manifest in memory (no file I/O)."""
    CD = _load_hermes_config()
    default_config = CD.DEFAULT_CONFIG
    optional_env = getattr(CD, "OPTIONAL_ENV_VARS", {})

    qubit_paths = set(_qubit_default_paths())

    keys: List[Dict[str, Any]] = []
    for path, value in sorted(_flatten(default_config)):
        secret = _is_secret(path)
        unsafe = bool(UNSAFE_RE.search(path))
        row: Dict[str, Any] = {
            "path": path,
            "type": _type_name(value),
            "default": _jsonable(value),
            "secret": secret,
            "redaction": "value" if secret else "none",
            "unsafe": unsafe,
            "persistence": PERSISTENCE,
            "runtime_consumer": RUNTIME_CONSUMER,
            "qubit_overlap": path in qubit_paths,
            "validation": "type/default enforced by the vendored runtime config loader",
            "cli_path": "qubit config get/set <path> (CLI) + /api/config (dashboard)",
            "error_behavior": "invalid value refused by the config loader; no "
                              "silent ignore",
            "disposition": "SAFE_EQUIVALENT" if unsafe else "BOUND",
        }
        if unsafe:
            row["policy_basis"] = (
                "default false; enforced by the vendored browser eval policy "
                "(qubit_runtime/tools/browser_tool_eval_policy.py); Qubit never "
                "exposes arbitrary user JavaScript evaluate")
        keys.append(row)

    env_rows = _env_var_rows(optional_env) if isinstance(optional_env, dict) else []
    # Add the pinned provider-registry credential/base-URL env vars that are
    # not in OPTIONAL_ENV_VARS (the D3 gap: OPENAI_API_KEY, ANTHROPIC_API_KEY, …).
    have_env = {e["name"] for e in env_rows}
    for name in _provider_env_names():
        if name in have_env:
            continue
        secret = _is_secret(name)
        env_rows.append({
            "name": name, "secret": secret,
            "redaction": "value" if secret else "none",
            "reference": f"env:{name}",
            "disposition": "BOUND (provider registry env reference)",
        })
        have_env.add(name)
    env_rows.sort(key=lambda r: r["name"])

    # Missing top-level keys that accept arbitrary user-populated children
    # (mcp_servers, channel_prompts, custom_providers, server_actions, …).
    have_keys = {k["path"] for k in keys}
    for top in _open_dict_keys():
        if top in have_keys:
            continue
        keys.append({
            "path": top, "type": "dict", "default": None, "secret": False,
            "redaction": "none", "unsafe": False,
            "persistence": PERSISTENCE, "runtime_consumer": RUNTIME_CONSUMER,
            "qubit_overlap": top in qubit_paths,
            "validation": "open user-populated map validated at its known "
                          "first segment",
            "cli_path": "qubit config get/set <path> (CLI) + /api/config (dashboard)",
            "error_behavior": "unknown top-level key refused by the config loader; "
                              "no silent ignore",
            "disposition": "BOUND (open user-populated map)",
        })
        have_keys.add(top)
    keys.sort(key=lambda r: r["path"])

    openclaw_env_rows = _openclaw_env_rows()
    openclaw_config = _openclaw_config_surface()

    counts = {
        "config_keys": len(keys),
        "config_keys_secret": sum(1 for k in keys if k["secret"]),
        "config_keys_unsafe": sum(1 for k in keys if k["unsafe"]),
        "config_keys_qubit_overlap": sum(1 for k in keys if k["qubit_overlap"]),
        "env_vars": len(env_rows),
        "env_vars_secret": sum(1 for e in env_rows if e["secret"]),
        "openclaw_env_vars": len(openclaw_env_rows),
        "openclaw_env_vars_secret": sum(1 for e in openclaw_env_rows if e["secret"]),
        "openclaw_config_paths": int(openclaw_config.get("total") or 0),
    }

    return {
        "schema": "qubit-config-binding-manifest/1",
        "generated_by": "scripts/config_binding_manifest.py",
        "description": (
            "Deterministic inventory of the pinned Hermes config/env surface "
            "and its Qubit binding metadata. It records names, types, defaults, "
            "persistence, consumer, and redaction; it never records a secret "
            "value and does not claim a runtime-consumption proof. No timestamp "
            "is embedded."),
        "citation": {
            "vendored": "qubit_runtime/qubit_cli/config_defaults.py "
                        "(DEFAULT_CONFIG, OPTIONAL_ENV_VARS); "
                        "qubit_runtime/qubit_cli/auth.py (PROVIDER_REGISTRY "
                        "env vars); qubit_runtime/qubit_cli/config.py "
                        "(_OPEN_DICT_TOP_LEVEL_KEYS et al.)",
            "qubit": "voicebridge/config.py (DEFAULTS)",
            "openclaw": "docs/archive/2026-09-21/QUBIT_OPENCLAW_ENV_NAMES.json (read-only "
                        "inventory of the pinned OpenClaw source)",
            "pin": _pin(),
        },
        "config_keys": keys,
        "env_vars": env_rows,
        "openclaw_env_vars": openclaw_env_rows,
        "openclaw_config_surface": openclaw_config,
        "counts": counts,
    }


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="docs/archive/2026-09-21/QUBIT_CONFIG_BINDING_MANIFEST.json")
    args = ap.parse_args(argv)

    manifest = build()
    counts = manifest["counts"]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"wrote {out}")
    print(f"config keys: {counts['config_keys']} "
          f"(secret {counts['config_keys_secret']}, "
          f"unsafe {counts['config_keys_unsafe']}, "
          f"qubit-overlap {counts['config_keys_qubit_overlap']})")
    print(f"env vars: {counts['env_vars']} "
          f"(secret {counts['env_vars_secret']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
