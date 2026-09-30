"""Idempotent, backed-up config/schema migration for the Qubit update path.

An update can ship a config shape change (a retired section folded into its
canonical one, a missing schema marker). The updater must apply that migration
exactly once, reversibly, and never touch the read-only reference home or copy
secrets.

The entry point delegates to the existing Qubit config migration surface
(``voicebridge.config._migrate_legacy_sections``) rather than reinventing it,
and adds the schema marker the update lifecycle records. When there is nothing
to migrate the call is a pure no-op. A write always happens through a timestamped
backup + atomic replace, so an interrupted migration cannot leave a half-written
config.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

MIGRATION_FAILED_CODE = "UPDATE_MIGRATION_FAILED"
SCHEMA_KEY = "qubit_config_schema"
SCHEMA_VERSION = 1


def _get(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default) if hasattr(cfg, "get") else default
    except Exception:  # noqa: BLE001
        return default


def _under_test() -> bool:
    if os.environ.get("QUBIT_UPDATE_DISABLED") == "1":
        return True
    try:
        from . import qubit_runtime

        return bool(qubit_runtime.under_test())
    except Exception:  # noqa: BLE001
        return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def resolve_config_path(cfg: Any, override: Any = None) -> Optional[Path]:
    """The config file to migrate: explicit override, ``cfg.path``, then the
    canonical product config location."""
    if override:
        return Path(override).expanduser()
    configured = str(_get(cfg, "update", "config_path", default="") or "").strip()
    if configured:
        return Path(configured).expanduser()
    path = getattr(cfg, "path", None)
    if path:
        return Path(path)
    try:
        from . import product as P

        return Path(P.default_paths().config_dir) / "voice.yaml"
    except Exception:  # noqa: BLE001
        return None


def _explicit(cfg: Any, override: Any) -> bool:
    if override:
        return True
    if str(_get(cfg, "update", "config_path", default="") or "").strip():
        return True
    return bool(getattr(cfg, "path", None))


def _backup(path: Path) -> Optional[str]:
    try:
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = path.with_name(f"{path.name}.bak-{stamp}")
        shutil.copy2(path, dest)
        return str(dest)
    except OSError:
        return None


def migrate_config(cfg: Any, *, dry_run: bool = False, override: Any = None
                   ) -> Dict[str, Any]:
    """Apply the Qubit config/schema migration idempotently.

    ``dry_run`` reports what *would* change without writing. A migration that
    resolves no config file, or whose resolved path is the operator's canonical
    config while running under a test (and was not explicitly named), is skipped
    rather than written — the reference/live config is never mutated by a test.
    """
    path = resolve_config_path(cfg, override)
    base: Dict[str, Any] = {
        "ok": True, "state": "OK", "code": "", "path": str(path) if path else None,
        "migrated": False, "dry_run": bool(dry_run), "backup": None,
        "changed_keys": [], "skipped": False, "reason": "",
    }
    if path is None:
        base.update({"skipped": True, "reason": "no config file resolved"})
        return base
    if not path.is_file():
        base.update({"skipped": True,
                     "reason": f"no config file at {path}"})
        return base
    if _under_test() and not _explicit(cfg, override):
        base.update({"skipped": True,
                     "reason": "test environment without an explicit config path"})
        return base

    try:
        import yaml
    except Exception as exc:  # noqa: BLE001
        return {**base, "ok": False, "state": "FAILED",
                "code": MIGRATION_FAILED_CODE,
                "detail": f"yaml unavailable: {exc}"}

    try:
        original = path.read_text(encoding="utf-8-sig")
        loaded = yaml.safe_load(original) or {}
    except (OSError, ValueError) as exc:
        return {**base, "ok": False, "state": "FAILED",
                "code": MIGRATION_FAILED_CODE,
                "detail": f"cannot read config {path}: {exc}"}
    if not isinstance(loaded, dict):
        return {**base, "ok": False, "state": "FAILED",
                "code": MIGRATION_FAILED_CODE,
                "detail": f"config {path} is not a mapping"}

    before_keys = set(loaded)
    migrated = dict(loaded)

    # Delegate to the existing Qubit migration surface (fold retired sections).
    try:
        from .config import _migrate_legacy_sections

        migrated = _migrate_legacy_sections(migrated)
    except Exception:  # noqa: BLE001 - absent surface means nothing to fold
        pass
    if not isinstance(migrated, dict):
        migrated = dict(loaded)

    if migrated.get(SCHEMA_KEY) != SCHEMA_VERSION:
        migrated[SCHEMA_KEY] = SCHEMA_VERSION

    if migrated == loaded:
        return base

    changed = sorted((set(migrated) - before_keys)
                     | {k for k in before_keys
                        if migrated.get(k) != loaded.get(k)})
    base["changed_keys"] = changed
    if dry_run:
        base.update({"state": "DRY_RUN", "would_migrate": changed})
        return base

    backup = _backup(path)
    if backup is None:
        return {**base, "ok": False, "state": "FAILED",
                "code": MIGRATION_FAILED_CODE,
                "detail": f"cannot back up config before migration: {path}"}
    try:
        text = yaml.safe_dump(migrated, sort_keys=False)
        if not text.endswith("\n"):
            text += "\n"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        return {**base, "ok": False, "state": "FAILED",
                "code": MIGRATION_FAILED_CODE,
                "detail": f"cannot write migrated config {path}: {exc}",
                "backup": backup}

    base.update({"migrated": True, "backup": backup,
                 "reason": "migrated " + ", ".join(changed)})
    return base


__all__ = ["MIGRATION_FAILED_CODE", "SCHEMA_KEY", "SCHEMA_VERSION",
           "resolve_config_path", "migrate_config"]
