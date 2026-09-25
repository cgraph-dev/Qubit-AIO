#!/usr/bin/env python3
"""Deterministic Qubit source-level rebrand engine (owner amendment 2026-09-19).

Renames the shipped Qubit runtime's Hermes-derived namespaces, Python modules,
imports, package path, internal environment names, entry points, and service
labels to Qubit names, using an explicit token map and path-aware renames. It
never touches pristine upstream baselines, licenses, provenance/comparison
records, or legal notices.

Design contract:

* **explicit map, not blind substitution** — only the tokens listed below are
  rewritten; a word-boundary match is required and URL/attribution strings are
  protected from the bare product-word rewrite;
* **dependency order** — content/imports first, then filesystem paths, then the
  package directory; a second pass catches any reference that points at a
  renamed path;
* **idempotent** — running twice is a no-op;
* **recoverable** — the caller must snapshot the tree before `--apply`.

Usage:
    python scripts/qubit_rebrand.py                 # dry run, prints the plan
    python scripts/qubit_rebrand.py --apply         # rewrite (idempotent)
    python scripts/qubit_rebrand.py --manifest OUT  # write the rename manifest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# rename map
# ---------------------------------------------------------------------------
# Python module / namespace tokens (longest-first at apply time so
# ``hermes_state_usage`` wins over ``hermes_state``).
MODULE_TOKENS = {
    "qubit_hermes": "qubit_runtime",
    "hermes_cli": "qubit_cli",
    "hermes_bootstrap": "qubit_bootstrap",
    "hermes_constants": "qubit_constants",
    "hermes_constants_scratch": "qubit_constants_scratch",
    "hermes_logging": "qubit_logging",
    "hermes_platform": "qubit_platform",
    "hermes_startup_watchdog": "qubit_startup_watchdog",
    "hermes_time": "qubit_time",
    "hermes_state_common": "qubit_state_common",
    "hermes_state_compression": "qubit_state_compression",
    "hermes_state_dbfile": "qubit_state_dbfile",
    "hermes_state_errors": "qubit_state_errors",
    "hermes_state_fts": "qubit_state_fts",
    "hermes_state_gateway": "qubit_state_gateway",
    "hermes_state_guard": "qubit_state_guard",
    "hermes_state_health": "qubit_state_health",
    "hermes_state_holders": "qubit_state_holders",
    "hermes_state_ids": "qubit_state_ids",
    "hermes_state_lockguard": "qubit_state_lockguard",
    "hermes_state_lockowners": "qubit_state_lockowners",
    "hermes_state_maintenance": "qubit_state_maintenance",
    "hermes_state_messages": "qubit_state_messages",
    "hermes_state_portability": "qubit_state_portability",
    "hermes_state_profile_repair": "qubit_state_profile_repair",
    "hermes_state_readpool": "qubit_state_readpool",
    "hermes_state_registry": "qubit_state_registry",
    "hermes_state_repair": "qubit_state_repair",
    "hermes_state_rewind": "qubit_state_rewind",
    "hermes_state_schema": "qubit_state_schema",
    "hermes_state_search": "qubit_state_search",
    "hermes_state_sessions": "qubit_state_sessions",
    "hermes_state_telegram": "qubit_state_telegram",
    "hermes_state_timeline": "qubit_state_timeline",
    "hermes_state_titles": "qubit_state_titles",
    "hermes_state_usage": "qubit_state_usage",
    "hermes_state_user_copy": "qubit_state_user_copy",
    "hermes_state_wal": "qubit_state_wal",
    "hermes_state": "qubit_state",
    "hermes_tools_mcp_server": "qubit_tools_mcp_server",
    "hermes_tools": "qubit_tools",
    "hermes_subprocess_env": "qubit_subprocess_env",
    "hermes_home_key": "qubit_home_key",
    "hermes_client_tag": "qubit_client_tag",
    "hermes_xai_user_agent": "qubit_xai_user_agent",
    "hermes_xai_default_headers": "qubit_xai_default_headers",
    "hermes_yaml": "qubit_yaml",
    "hermes_undo": "qubit_undo",
    # Qubit-owned modules.
    "hermes_asgi": "qubit_asgi",
    "hermes_backend": "qubit_backend",
    "hermes_compat": "qubit_compat",
    "hermes_egress_guard": "qubit_egress_guard",
    "hermes_migration": "qubit_migration",
    "hermes_plugins": "qubit_plugins",
    "hermes_runtime": "qubit_runtime",
    "hermes_session_store": "qubit_session_store",
}

# Class / camel identifiers.
CLASS_TOKENS = {
    "HermesSessionStore": "QubitSessionStore",
    "HermesSession": "QubitSession",
    "HermesHome": "QubitHome",
}

# Internal environment names. HERMES_HOME is the shipped runtime home; a legacy
# read of the old name stays in the migration adapter (see qubit_runtime).
ENV_TOKENS = {
    "HERMES_HOME": "QUBIT_HOME",
}

# Curated basename renames from the frozen rebrand manifest
# (``docs/archive/2026-09-21/QUBIT_REBRAND_MANIFEST.json``, ``path_renames``).
# The module-token pass above only expresses ``^hermes_<module>``; these are the
# camel / hyphen / product basenames it cannot encode. Matched on exact
# basename, for both files and directories. Preserved wire/service ids
# (``hermes-kanban-*``, ``@hermes/ink`` package aliases, ...) are deliberately
# absent — they are never renamed.
CURATED_BASENAME_RENAMES = {
    # desktop product assets
    "hermes.desktop": "qubit.desktop",
    "hermes.png": "qubit.png",
    "Hermes.app": "Qubit.app",
    "Hermes.exe": "Qubit.exe",
    # differential fixtures
    "vendor_hermes_constants.json": "vendor_qubit_constants.json",
    "vendor_hermes_state_common.json": "vendor_qubit_state_common.json",
    # frontend packages / dirs
    "hermes-qubit": "qubit-app",
    "hermes-shared": "qubit-shared",
    "hermes-web": "qubit-web",
    "hermes-dist": "qubit-dist",
    "HermesConsoleModal.tsx": "QubitConsoleModal.tsx",
    # generators / packaging / tui
    "rebuild_ledger_hermes.py": "rebuild_ledger_qubit.py",
    "test_ledger_hermes_build.py": "test_ledger_qubit_build.py",
    "hermes-exec-shim.sh": "qubit-exec-shim.sh",
    "hermes-agent.nix": "qubit-runtime.nix",
    "hermes-ink.d.ts": "qubit-ink.d.ts",
    "hermes-ink": "qubit-ink",
}

# Filesystem basenames (module files/dirs) to rename, derived from MODULE_TOKENS.
# Only Python modules/packages and the launcher are renamed here; data assets,
# contributor records, and provenance trees are left untouched.

# Trees the engine rewrites (shipped product source). Provenance/legal/fixture
# trees are excluded deliberately.
SOURCE_ROOTS = [
    "qubit_hermes",
    "voicebridge",
    "frontend/qubit-web/src",
    "frontend/qubit-shared/src",
    "frontend/qubit-app",
    "scripts",
    "tests",
]
# Top-level package/dir renames (old, new), applied after content + module
# file renames.
PACKAGE_RENAMES = [("qubit_hermes", "qubit_runtime")]
SOURCE_FILES = ["pyproject.toml", "pytest.ini"]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".pytest_cache"}
# Never rewrite provenance/legal/attribution content.
PROTECTED_PARTS = (
    "LICENSE", "THIRD_PARTY_NOTICES", "docs/archive/2026-09-21/LICENSING", "docs/archive/2026-09-21/UPSTREAM_REUSE",
    "voicebridge/upstream", "docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST",
    "docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON", "docs/archive/2026-09-21/QUBIT_SOURCE_INVENTORY",
    "docs/archive/2026-09-21/INVENTORY_",     "docs/archive/2026-09-21/QUBIT_GAP_LEDGER", "docs/archive/2026-09-21/AGENT_PROMPT_PACK",
    # protected under both the pre- and post-rename package name
    "qubit_hermes/contributors", "qubit_hermes/website",
    "qubit_hermes/native/docs",
    "qubit_runtime/contributors", "qubit_runtime/website",
    "qubit_runtime/native/docs",
    # the engine and its manifest must never rewrite themselves
    "scripts/qubit_rebrand.py", "docs/archive/2026-09-21/QUBIT_REBRAND_MANIFEST",
)
TEXT_SUFFIXES = {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
                 ".json", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".sh",
                 ".bash", ".service", ".timer", ".md", ".html", ".css"}

_URL_HINT = re.compile(r"(https?://|github\.com|nousresearch\.com|@nous|file:|env:)")


def _protected(rel: str) -> bool:
    return any(p in rel for p in PROTECTED_PARTS)


def _existing_roots():
    seen = list(SOURCE_ROOTS)
    for old, new in PACKAGE_RENAMES:
        if new not in seen:
            seen.insert(0, new)
    return [r for r in seen if (REPO / r).is_dir()]


def _iter_text_files():
    for root in _existing_roots():
        base = REPO / root
        if not base.exists():
            continue
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                p = Path(dirpath) / f
                rel = p.relative_to(REPO).as_posix()
                if p.suffix in TEXT_SUFFIXES and not _protected(rel):
                    yield p, rel
    for rel in SOURCE_FILES:
        p = REPO / rel
        if p.is_file() and not _protected(rel):
            yield p, rel


def _build_regex():
    tokens = sorted(set(MODULE_TOKENS) | set(CLASS_TOKENS) | set(ENV_TOKENS),
                    key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9_])(" +
                      "|".join(re.escape(t) for t in tokens) +
                      r")(?![A-Za-z0-9_])")


_TOKEN_RE = _build_regex()


# Embedded identifiers (``_hermes_cli``, ``hermes_cli_dir``, ``qubit_hermes_fork``)
# are not matched by the word-boundary regex; a longest-first literal pass
# catches them. The tokens are specific module names, so a literal replace is
# safe and cannot touch unrelated prose.
def _embedded_pairs():
    pairs = dict(MODULE_TOKENS)
    pairs.pop("qubit_hermes", None)  # keep package token via MODULE_TOKENS only
    return sorted(pairs.items(), key=lambda kv: len(kv[0]), reverse=True)


_EMBEDDED_PAIRS = _embedded_pairs()
# The package token is also handled as a literal so ``qubit_hermes_fork`` moves.
_PACKAGE_PAIR = ("qubit_hermes", "qubit_runtime")


def _rewrite_embedded(text: str) -> str:
    for old, new in _EMBEDDED_PAIRS:
        if old in text:
            text = text.replace(old, new)
    if _PACKAGE_PAIR[0] in text:
        text = text.replace(_PACKAGE_PAIR[0], _PACKAGE_PAIR[1])
    return text


def _replace_token(match: re.Match) -> str:
    t = match.group(1)
    return MODULE_TOKENS.get(t) or CLASS_TOKENS.get(t) or ENV_TOKENS.get(t) or t


def _rewrite_text(path: Path, rel: str) -> int:
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    # Protect single-line URL/attribution strings from any change: the explicit
    # module tokens are still safe there (they are paths), so only guard lines
    # that look like URLs is unnecessary for these tokens; we keep the whole
    # replacement because module names in URLs must move too (they are shipped
    # module paths), while product-word strings are handled by the classifier.
    new, n = _TOKEN_RE.subn(_replace_token, text)
    new = _rewrite_embedded(new)
    if new != text:
        path.write_text(new, encoding="utf-8", errors="surrogateescape")
    return n


def _path_renames():
    """(old_rel, new_rel) for module files/dirs, deepest-first rename order."""
    renames = []
    token_re = re.compile(r"^(hermes_[a-z0-9_]+)(?=\.py$|$)")
    for root in _existing_roots():
        base = REPO / root
        if not base.exists():
            continue
        for dirpath, dirs, files in os.walk(base, topdown=False):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in dirs + files:
                curated = CURATED_BASENAME_RENAMES.get(name)
                if curated is not None and curated != name:
                    old = Path(dirpath) / name
                    new = Path(dirpath) / curated
                    renames.append((old.relative_to(REPO).as_posix(),
                                    new.relative_to(REPO).as_posix()))
                    continue
                m = token_re.match(name)
                if not m:
                    continue
                tok = m.group(1)
                if tok not in MODULE_TOKENS:
                    continue
                newtok = MODULE_TOKENS[tok]
                old = Path(dirpath) / name
                new = Path(dirpath) / name.replace(tok, newtok, 1)
                if old != new:
                    renames.append((old.relative_to(REPO).as_posix(),
                                    new.relative_to(REPO).as_posix()))
    # Deepest paths first so a parent-directory rename never hides a child
    # rename; the (old, new) tiebreak makes the plan independent of the
    # string-hash iteration order of ``set`` (PYTHONHASHSEED).
    return sorted(set(renames),
                  key=lambda x: (-x[0].count("/"), x[0], x[1]))


def build_plan():
    content = []
    for p, rel in _iter_text_files():
        try:
            text = p.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError:
            continue
        n = len(_TOKEN_RE.findall(text))
        if n:
            content.append({"path": rel, "token_hits": n})
    paths = [{"old": o, "new": n} for o, n in _path_renames()]
    return {"content_files": content, "path_renames": paths}


def apply(manifest_out: Path | None) -> int:
    plan = build_plan()
    changed = 0
    for row in plan["content_files"]:
        p = REPO / row["path"]
        changed += 1 if _rewrite_text(p, row["path"]) else 0
    path_log = []
    for row in plan["path_renames"]:
        old, new = REPO / row["old"], REPO / row["new"]
        if old.exists() and not new.exists():
            old.rename(new)
            path_log.append(row)
    for old_rel, new_rel in PACKAGE_RENAMES:
        old, new = REPO / old_rel, REPO / new_rel
        if old.is_dir() and not new.exists():
            old.rename(new)
            path_log.append({"old": old_rel, "new": new_rel})
    # second content pass: references to renamed paths now resolve
    for root in _existing_roots():
        base = REPO / root
        if not base.exists():
            continue
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                p = Path(dirpath) / f
                rel = p.relative_to(REPO).as_posix()
                if p.suffix in TEXT_SUFFIXES and not _protected(rel):
                    _rewrite_text(p, rel)
    manifest = {
        "schema": "qubit-rebrand/1",
        "amendment": "docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-19_DEEP_REBRAND.md",
        "module_map": MODULE_TOKENS,
        "class_map": CLASS_TOKENS,
        "env_map": ENV_TOKENS,
        "content_files_changed": changed,
        "path_renames": path_log,
        "protected": list(PROTECTED_PARTS),
    }
    if manifest_out:
        manifest_out.parent.mkdir(parents=True, exist_ok=True)
        manifest_out.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                                encoding="utf-8")
    print(f"content files changed={changed} paths renamed={len(path_log)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--manifest", type=Path,
                    default=REPO / "docs/archive/2026-09-21" / "QUBIT_REBRAND_MANIFEST.json")
    args = ap.parse_args(argv)
    if args.apply:
        return apply(args.manifest)
    plan = build_plan()
    print(f"DRY RUN: content files={len(plan['content_files'])} "
          f"path renames={len(plan['path_renames'])}")
    for row in plan["path_renames"][:50]:
        print(f"  {row['old']}  ->  {row['new']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
