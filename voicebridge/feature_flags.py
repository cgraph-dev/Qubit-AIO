"""Packet 7B: typed feature-flag registry.

Every reversible Phase 0–7A behavior gate is declared here with its config
path, default, rollback value and a human description. Resolution is read-only
against a Config; applying a change writes the config atomically with a
timestamped backup so rollback restores the previous value without touching
user data (memory, reports, cursors live in the state dir and are never part
of a flag write).

The CLI (`voicebridge flags ...`) is dry-run by default and never edits the
live config unless --apply is given. Tests and rollback drills exercise flags
on throwaway configs, never the operator's live one.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_FLAG_STATE = Path("~/.local/state/openclaw-voice-bridge/flags.state.json").expanduser()


@dataclass(frozen=True)
class FeatureFlag:
    """One reversible behavior gate with typed metadata."""

    name: str
    config_path: Tuple[str, ...]
    default: Any
    rollback_value: Any
    category: str
    description: str


FLAGS: List[FeatureFlag] = [
    FeatureFlag(
        name="broker",
        config_path=("safety", "tool_broker"),
        default=True,
        rollback_value=True,
        category="phase3",
        description="Route local actions + local-script templates through the "
                    "capability broker (argv-only). False = LEGACY DIRECT shell "
                    "path (unsecured; never described as secured)."),
    FeatureFlag(
        name="compiler_5c",
        config_path=("intent", "compiler", "mode"),
        default="5c",
        rollback_value="4b",
        category="phase5",
        description="'5c' = bounded FTS5 context compiler wired into chat turns; "
                    "'4b' = old compiler (rollback flag)."),
    FeatureFlag(
        name="canary_q5",
        config_path=("intent", "canary", "q5_enabled"),
        default=False,
        rollback_value=False,
        category="phase4",
        description="Opt-in Q5 canary drives the SEPARATE qubit-cand-q5 alias. "
                    "Never replaces qubit-brain4; flip off to rollback."),
    FeatureFlag(
        name="router_l0",
        config_path=("intent", "router", "enabled"),
        default=False,
        rollback_value=False,
        category="phase4",
        description="Deterministic L0 routing pipeline before the classifier "
                    "(default OFF = pre-4C behavior)."),
    FeatureFlag(
        name="router_l1",
        config_path=("intent", "router", "l1_enabled"),
        default=False,
        rollback_value=False,
        category="phase4",
        description="Optional CPU semantic L1 matching (requires router_l0 AND "
                    "the measured embedding gate)."),
    FeatureFlag(
        name="llm_classifier",
        config_path=("intent", "llm_classifier"),
        default=True,
        rollback_value=True,
        category="phase0",
        description="Local-LLM intent classifier for ambiguous taps (True = "
                    "heuristic + LLM; False = heuristic only)."),
]


def _by_name(name: str) -> FeatureFlag:
    for f in FLAGS:
        if f.name == name:
            return f
    raise KeyError(f"unknown feature flag {name!r}; known: {', '.join(f.name for f in FLAGS)}")


def names() -> List[str]:
    return [f.name for f in FLAGS]


def effective(cfg: Any, name: str) -> Any:
    """Resolve a flag's current effective value from a Config (read-only)."""
    f = _by_name(name)
    return cfg.get(*f.config_path, default=f.default)


def effective_all(cfg: Any) -> Dict[str, Any]:
    return {f.name: effective(cfg, f.name) for f in FLAGS}


def env_key(f: FeatureFlag) -> str:
    """Env override key: VOICE_BRIDGE__<SEGMENT>__... (double-underscore)."""
    return "VOICE_BRIDGE__" + "__".join(str(p).upper() for p in f.config_path)


def env_override_value(f: FeatureFlag, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def to_env(f: FeatureFlag, value: Any) -> Dict[str, str]:
    """Env-var override that makes a flag effective without editing config."""
    return {env_key(f): env_override_value(f, value)}


def write_config_with_backup(path: Path, update: Dict[str, Any],
                             prefix: str = "flags") -> Path:
    """Atomically apply `update` (deep-merged) to a YAML config, keeping a
    timestamped backup next to it. Returns the backup path. Never touches the
    state dir. Raises on a missing/invalid existing file."""
    import yaml
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    for k, v in update.items():
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k] = {**data[k], **v}
        else:
            data[k] = v
    bak = path.with_name(f"{path.name}.{prefix}.{int(time.time())}.bak")
    shutil.copy2(path, bak)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump(data, fh, sort_keys=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return bak


def apply_flag(config_path: Path, name: str, value: Any) -> Path:
    """Write one flag to a config (with backup). Returns backup path."""
    f = _by_name(name)
    node: Dict[str, Any] = {f.config_path[-1]: value}
    for seg in reversed(f.config_path[:-1]):
        node = {seg: node}
    return write_config_with_backup(config_path, node)


def rollback_flag(config_path: Path, name: str) -> Path:
    """Restore a flag to its registered rollback value (with backup)."""
    f = _by_name(name)
    return apply_flag(config_path, name, f.rollback_value)


def state_snapshot(state_dir: Path) -> Dict[str, str]:
    """SHA-256 of every file under the state dir (memory, reports, cursors,
    journals). Used by rollback drills to prove user data is preserved."""
    import hashlib
    state_dir = Path(state_dir)
    out: Dict[str, str] = {}
    if not state_dir.exists():
        return out
    for p in sorted(state_dir.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(state_dir))] = hashlib.sha256(
                p.read_bytes()).hexdigest()
    return out


def flags_changed(cfg_a: Any, cfg_b: Any) -> List[str]:
    """Names whose effective value differs between two configs."""
    all_a, all_b = effective_all(cfg_a), effective_all(cfg_b)
    return [n for n in names() if all_a[n] != all_b[n]]


def redact_config_yaml(cfg: Any) -> str:
    """Serialize a config for a throwaway drill WITHOUT exposing secrets.

    Secret-shaped keys (token/secret/password/api_key/... and their env-file
    paths) are masked, so a drill that writes the config to a temp file never
    leaks the live token or credential paths.
    """
    from .redaction import redact_obj
    data = getattr(cfg, "data", None)
    if data is None:
        try:
            data = cfg.as_yaml()
        except Exception:
            data = {}
    safe = redact_obj(data)
    import yaml
    return yaml.safe_dump(safe, sort_keys=False)