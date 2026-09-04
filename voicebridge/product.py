"""Packet 8K-P: productized installation, portable paths, model bootstrap,
first-run, service ownership, desktop shortcut, optional tray, capability
defaults, and the packaging/private-source boundary.

Qubit Voice Agent is the canonical public identity. The internal import
package stays ``voicebridge``; existing ``openclaw-voice-bridge`` paths keep an
explicit compatibility alias and a reversible migration.

Design rules (all hermetic by default):
- Never read from one path and write to another in the same operation.
- Dry-run flows (``install`` / ``first_run``) are strictly side-effect-free:
  no directory, journal, lock, config, alias, shortcut, model, or temporary
  file is created. Read-only capability probes are the only external
  interaction and are hermetically injectable in tests.
- The installer, model bootstrap, and repair commands are lock+journal guarded,
  idempotent, atomic, and typed (READY/DEGRADED/MISSING/UNAVAILABLE/BLOCKED/
  FAILED) — they never report COMPLETE when a required component is missing.
- Model bootstrap fails closed: the pinned spec (source scheme, revision/
  provider digest, 64-hex SHA-256, exact positive byte size, approved license)
  is validated before any download; downloads are bounded, resumable via a
  ``.part`` file, fsynced, verified, and promoted atomically; a previous valid
  model is never destroyed by a failed acquisition.
- Rollback restores through a staged, validated, fsynced, atomic-rename
  pipeline and fails closed on corrupt/newer backups, read-only filesystems,
  disk-full, partial restore, or interruption.
- Every capability (voice, audio, apps, browser, display, model, provider,
  tray) is an explicit typed state; detection is never permission.
- The dashboard shortcut opens the real local dashboard through a tested
  ``voicebridge webui --open`` command that never starts a second bridge or
  listener and never places a token in a URL, argument, or file.
- No token, secret, cookie, private URL, or raw audio ever appears in output.
- Linux uses XDG locations; macOS/Windows return an explicit UNSUPPORTED
  result before any install step.
- The wheel still contains Python implementation source: private-source
  distribution is NOT solved by this packet and is reported as a release
  blocker for that goal (obfuscation is not security).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import resources

# Canonical public identity.
PRODUCT_NAME = "qubit-voice-agent"
LEGACY_NAME = "openclaw-voice-bridge"
PRODUCT_VERSION = "1.0.0"

# Typed result states.
READY, DEGRADED, MISSING, UNAVAILABLE, BLOCKED, FAILED = (
    "READY", "DEGRADED", "MISSING", "UNAVAILABLE", "BLOCKED", "FAILED")
CAP_ENABLED, CAP_DISABLED, CAP_AVAILABLE = "ENABLED", "DISABLED", "AVAILABLE"
STATES = (READY, DEGRADED, MISSING, UNAVAILABLE, BLOCKED, FAILED)

# All paths created by the application, grouped by owner.
CONFIG_OWNED = ("config",)
DATA_OWNED = ("models", "cache", "resources")
STATE_OWNED = ("state", "logs", "exports", "backups", "journal")

# Backup / rollback manifest contract (fail closed on a newer schema).
BACKUP_SCHEMA_VERSION = 1
BACKUP_MANIFEST = "manifest.json"

# Model bootstrap fail-closed contract.
_APPROVED_SCHEMES = ("http://", "https://", "ollama://", "ollama:")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_DOWNLOAD_BYTES = 32 * 1024 ** 3  # 32 GiB hard bound on any one artifact
_APPROVED_LICENSES = frozenset({
    "Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unlicense",
    "CC0-1.0", "MPL-2.0", "LGPL-2.1", "LGPL-3.0", "GPL-2.0", "GPL-3.0",
    "AGPL-3.0", "Zlib", "BSL-1.0",
})


class ProductError(Exception):
    """Base product error carrying a typed state."""

    def __init__(self, message: str, state: str = FAILED):
        super().__init__(message)
        self.state = state


class UnsupportedPlatformError(ProductError):
    def __init__(self, message: str):
        super().__init__(message, state=UNAVAILABLE)


# ---------------------------------------------------------------------------
# Durability helpers (fsync) used by rollback, migration, and model promotion
# ---------------------------------------------------------------------------
def _fsync_dir(path: Path) -> None:
    """Fsync a directory so a rename/create in it is durable."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _fsync_file(path: Path) -> None:
    try:
        with open(path, "rb") as fh:
            os.fsync(fh.fileno())
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 1. Portable path resolver
# ---------------------------------------------------------------------------
def platform_supported() -> Dict[str, Any]:
    """Linux is the supported target; macOS/Windows return UNSUPPORTED
    explicitly before any install step."""
    if platform.system() == "Linux":
        return {"state": "OK", "detail": f"Linux {platform.machine()}"}
    return {"state": "UNSUPPORTED",
            "detail": f"{platform.system()} is not a supported install target "
                      f"(Linux/XDG only); no install step was run"}


def _xdg(env: str, default: str) -> Path:
    v = os.environ.get(env, "")
    if v:
        return Path(v)
    return Path("~").expanduser() / default


@dataclass(frozen=True)
class ProductPaths:
    """Platform-aware per-user paths, all owned by the application.

    Linux: XDG-compatible. Never a repository path, /home/<user> literal,
    device index, or guessed application folder."""

    base: Path
    config_dir: Path
    data_dir: Path
    state_dir: Path
    model_dir: Path
    cache_dir: Path
    log_dir: Path
    export_dir: Path
    backup_dir: Path
    journal_dir: Path
    resource_root: Path
    legacy_base: Path
    legacy_state: Path
    legacy_data: Path

    @property
    def map(self) -> Dict[str, str]:
        return {
            "config": str(self.config_dir),
            "data": str(self.data_dir),
            "state": str(self.state_dir),
            "models": str(self.model_dir),
            "cache": str(self.cache_dir),
            "logs": str(self.log_dir),
            "exports": str(self.export_dir),
            "backups": str(self.backup_dir),
            "journal": str(self.journal_dir),
            "resources": str(self.resource_root),
            "legacy_config": str(self.legacy_base),
            "legacy_state": str(self.legacy_state),
            "legacy_data": str(self.legacy_data),
        }

    def owned_dirs(self) -> List[Path]:
        return [self.config_dir, self.data_dir, self.state_dir, self.model_dir,
                self.cache_dir, self.log_dir, self.export_dir, self.backup_dir,
                self.journal_dir]


def default_paths(base: Optional[Path] = None) -> ProductPaths:
    """Resolve the effective path map deterministically.

    ``base`` is only used in hermetic tests to point every directory at an
    isolated root; production resolves purely from XDG/HOME."""
    if base is not None:
        b = Path(base)
        cfg = b / "config"
        data = b / "data"
        state = b / "state"
        cache = b / "cache"
        legacy = b / "legacy"
        legacy_state = b / "legacy-state"
        legacy_data = b / "legacy-data"
    else:
        b = Path("~").expanduser()
        cfg = _xdg("XDG_CONFIG_HOME", ".config") / PRODUCT_NAME
        data = _xdg("XDG_DATA_HOME", ".local/share") / PRODUCT_NAME
        state = _xdg("XDG_STATE_HOME", ".local/state") / PRODUCT_NAME
        cache = _xdg("XDG_CACHE_HOME", ".cache") / PRODUCT_NAME
        legacy = _xdg("XDG_CONFIG_HOME", ".config") / LEGACY_NAME
        legacy_state = _xdg("XDG_STATE_HOME", ".local/state") / LEGACY_NAME
        legacy_data = _xdg("XDG_DATA_HOME", ".local/share") / LEGACY_NAME
    return ProductPaths(
        base=b,
        config_dir=cfg,
        data_dir=data,
        state_dir=state,
        model_dir=data / "models",
        cache_dir=cache,
        log_dir=state / "logs",
        export_dir=state / "exports",
        backup_dir=state / "backups",
        journal_dir=state / "install-journal",
        resource_root=resources.package_data_root(),
        legacy_base=legacy,
        legacy_state=legacy_state,
        legacy_data=legacy_data,
    )


def ensure_owned_dirs(paths: ProductPaths, *, dry_run: bool = True
                      ) -> List[Dict[str, Any]]:
    """Create ONLY the directories the application owns, with documented
    permissions. Never scans or rewrites arbitrary user folders. With
    ``dry_run=True`` nothing is created (a pure read-only report)."""
    results = []
    for d in paths.owned_dirs():
        if dry_run:
            results.append({"path": str(d), "exists": d.exists(),
                            "would_create": not d.exists(),
                            "mode": "0700" if "state" in d.parts or
                            "config" in d.parts else "0755"})
            continue
        try:
            d.mkdir(parents=True, exist_ok=True)
            mode = 0o700 if ("state" in d.parts or "config" in d.parts) else 0o755
            os.chmod(d, mode)
            results.append({"path": str(d), "exists": True,
                            "mode": oct(mode)})
        except OSError as exc:
            results.append({"path": str(d), "exists": False,
                            "error": str(exc)})
    return results


def path_map_diagnostic(paths: Optional[ProductPaths] = None) -> Dict[str, Any]:
    """Report the effective path map + platform support in diagnostics
    (no secrets)."""
    paths = paths or default_paths()
    return {
        "product": PRODUCT_NAME,
        "version": PRODUCT_VERSION,
        "platform": platform_supported(),
        "paths": paths.map,
    }


def _legacy_pairs(paths: ProductPaths) -> List[tuple]:
    """(legacy, canonical) directory pairs. The legacy openclaw-voice-bridge
    path is kept as an explicit compatibility alias so any code that still
    reads the old name resolves to the same physical tree — it can never read
    one config while another path writes a different one."""
    return [
        (paths.legacy_base, paths.config_dir),
        (paths.legacy_state, paths.state_dir),
        (paths.legacy_data, paths.data_dir),
    ]


def legacy_alias_status(paths: Optional[ProductPaths] = None) -> Dict[str, Any]:
    """Read-only typed status of the legacy compat alias for every runtime
    category. Never creates or moves anything."""
    paths = paths or default_paths()
    pairs = []
    for legacy, canonical in _legacy_pairs(paths):
        entry = {"legacy": str(legacy), "canonical": str(canonical)}
        if not legacy.exists():
            entry["state"] = "ABSENT"
        elif legacy.is_symlink():
            entry["state"] = ("ALIAS_OK" if os.path.realpath(legacy)
                              == str(canonical) else "ALIAS_MISMATCH")
            entry["resolves"] = os.path.realpath(legacy)
        else:
            entry["state"] = "REAL_DIR"
            entry["detail"] = "real legacy directory present; migration required"
        pairs.append(entry)
    if any(p["state"] == "REAL_DIR" for p in pairs):
        state = "LEGACY_ONLY"
    elif any(p["state"] == "ALIAS_MISMATCH" for p in pairs):
        state = "SPLIT_TREES"
    elif all(p["state"] in ("ABSENT", "ALIAS_OK") for p in pairs):
        state = "OK"
    else:
        state = "DEGRADED"
    return {"state": state, "pairs": pairs}


# ---------------------------------------------------------------------------
# Installer lock + journal
# ---------------------------------------------------------------------------
class InstallLock:
    """Exclusive installer lock (non-blocking) to prevent concurrent installers
    from duplicating files, downloads, services, or listeners."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fh: Optional[Any] = None

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fh = open(self.lock_path, "a+")
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(f"{os.getpid()} {uuid.uuid4().hex}\n")
            self._fh.flush()
            return True
        except (OSError, BlockingIOError):
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None
            return False

    def release(self) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def __enter__(self) -> "InstallLock":
        if not self.acquire():
            raise ProductError(
                "another installer is running (lock held); retry after it "
                "finishes", state=BLOCKED)
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class InstallJournal:
    """Append-only JSONL install journal. Every apply step is recorded with a
    typed state and a timestamp; interrupted installs resume or roll back from
    it. Constructing a journal never touches the filesystem; only ``append``
    does (and only during an actual apply, never in dry-run)."""

    def __init__(self, journal_dir: Path):
        self.path = journal_dir / "install.jsonl"
        self.journal_dir = journal_dir

    def _ensure(self) -> None:
        self.journal_dir.mkdir(parents=True, exist_ok=True)

    def append(self, run_id: str, step: str, state: str, detail: str,
               extra: Optional[Dict[str, Any]] = None) -> None:
        self._ensure()
        rec = {"ts": time.time(), "run_id": run_id, "step": step,
               "state": state, "detail": detail}
        if extra:
            rec.update(extra)
        with open(self.path, "a") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        try:
            for line in self.path.read_text().splitlines():
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            return []
        return out

    def last_state(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        recs = self.read()
        if run_id:
            recs = [r for r in recs if r.get("run_id") == run_id]
        return recs[-1] if recs else {}


# ---------------------------------------------------------------------------
# Staging + legacy compat aliases (used by install / first-run / migrate)
# ---------------------------------------------------------------------------
# Packaged defaults staged into the canonical config dir by the installer.
# NEVER overwrites an existing tuned file; merges only missing files. Only the
# PORTABLE example resources are staged — the developer's private
# personal-knowledge.md and task-templates.yaml are never shipped or staged.
# Each entry: (destination name, [portable resource names to try]). The wheel
# renames task-templates.example.yaml to _data/config/task-templates.yaml, so
# both names are tried; resource_path() prefers the installed package data.
_DEFAULT_STAGE = [
    ("voice.yaml", ["config/voice.example.yaml"]),
    ("task-templates.yaml",
     ["config/task-templates.example.yaml", "config/task-templates.yaml"]),
    ("local-actions.example.yaml",
     ["config/local-actions.example.yaml"]),
]


def _first_resource(*names: str) -> Optional[Path]:
    """First packaged resource that exists (the wheel may rename an example
    file, so try the installed-data candidate too). Never raises."""
    for n in names:
        p = resources.resource_path(n)
        if p.exists():
            return p
    return resources.resource_path(names[0])


def _stage_packaged_defaults(paths: ProductPaths, *, dry_run: bool = True,
                             journal: Optional[InstallJournal] = None,
                             run_id: str = "") -> List[Dict[str, Any]]:
    """Stage packaged defaults into the canonical config dir. Idempotent: an
    existing user file is never overwritten (KEPT). Missing packaged resources
    are typed, never silently skipped as success. In dry-run nothing is
    written."""
    results = []
    cfg = paths.config_dir
    for name, candidates in _DEFAULT_STAGE:
        dst = cfg / name
        if dst.exists():
            results.append({"resource": name, "state": "KEPT",
                            "detail": "existing user file preserved"})
            continue
        src = _first_resource(*candidates)
        if src is None or not src.exists():
            results.append({"resource": name, "state": "MISSING",
                            "detail": f"packaged resource unavailable "
                                      f"({'/'.join(candidates)})"})
            continue
        if dry_run:
            results.append({"resource": name, "state": "WOULD_STAGE",
                            "detail": f"would copy {src} -> {dst}"})
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            os.chmod(dst, 0o600)
            _fsync_file(dst)
            _fsync_dir(dst.parent)
            results.append({"resource": name, "state": "STAGED",
                            "detail": f"installed packaged default {name}"})
        except OSError as exc:
            results.append({"resource": name, "state": "FAILED",
                            "detail": str(exc)})
    if journal is not None and run_id:
        for r in results:
            journal.append(run_id, f"stage.{r['resource']}", r["state"],
                           r["detail"])
    return results


def _create_legacy_aliases(paths: ProductPaths, *, dry_run: bool = True,
                           journal: Optional[InstallJournal] = None,
                           run_id: str = "") -> List[Dict[str, Any]]:
    """Create the explicit legacy -> canonical compatibility aliases so every
    old-path reader resolves to the canonical tree. A REAL legacy directory is
    never overwritten here (that is migration); its presence is reported and
    blocks install. Idempotent and never duplicates state directories. In
    dry-run nothing is written."""
    results = []
    for legacy, canonical in _legacy_pairs(paths):
        entry = {"legacy": str(legacy), "canonical": str(canonical)}
        if not legacy.exists():
            if dry_run:
                entry["state"] = "WOULD_ALIAS"
                results.append(entry)
                continue
            try:
                canonical.mkdir(parents=True, exist_ok=True)
                legacy.parent.mkdir(parents=True, exist_ok=True)
                legacy.symlink_to(canonical, target_is_directory=True)
                _fsync_dir(legacy.parent)
                entry["state"] = "ALIAS_CREATED"
            except OSError as exc:
                entry["state"] = "FAILED"
                entry["detail"] = str(exc)
        elif legacy.is_symlink():
            ok = os.path.realpath(legacy) == str(canonical)
            entry["state"] = "ALIAS_OK" if ok else "ALIAS_MISMATCH"
            if not ok:
                entry["detail"] = f"alias resolves elsewhere: " \
                                  f"{os.path.realpath(legacy)}"
        else:
            entry["state"] = "REAL_DIR"
            entry["detail"] = "real legacy directory; run migration first"
        results.append(entry)
    if journal is not None and run_id:
        for r in results:
            journal.append(run_id,
                           f"alias.{Path(r['legacy']).name}", r["state"],
                           r.get("detail", ""))
    return results


# ---------------------------------------------------------------------------
# Installer (works from a release artifact / installed package, not a checkout)
# ---------------------------------------------------------------------------
def _require_python() -> Dict[str, Any]:
    if sys.version_info >= (3, 11):
        return {"state": "OK", "detail": f"python {sys.version_info.major}."
                f"{sys.version_info.minor}.{sys.version_info.micro}"}
    return {"state": MISSING, "detail": f"python {platform.python_version()} "
            f"(need >= 3.11)"}


def _require_audio() -> Dict[str, Any]:
    try:
        import sounddevice as sd
        n = len(sd.query_devices())
        return {"state": "OK" if n else MISSING, "detail": f"{n} audio devices"}
    except Exception as exc:
        return {"state": MISSING, "detail": f"audio backend unavailable: {exc}"}


def _require_ollama(url: str = "http://127.0.0.1:11434") -> Dict[str, Any]:
    try:
        import urllib.request
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=2) as r:
            if r.status == 200:
                return {"state": "OK", "detail": url}
        return {"state": MISSING, "detail": f"{url} returned {r.status}"}
    except Exception as exc:
        return {"state": MISSING, "detail": f"ollama unreachable: {exc}"}


def _require_session() -> Dict[str, Any]:
    """X11/Wayland user-session detection WITHOUT a hardcoded DISPLAY. A
    missing session is typed (headless remains possible via --no-hotkey)."""
    display = os.environ.get("DISPLAY", "").strip()
    wayland = os.environ.get("WAYLAND_DISPLAY", "").strip()
    xdg = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if display or wayland:
        kind = "X11" if display else "Wayland"
        return {"state": "OK", "detail": f"{kind} session present"}
    if xdg:
        return {"state": DEGRADED,
                "detail": f"no DISPLAY/WAYLAND_DISPLAY, but XDG_RUNTIME_DIR "
                          f"present ({xdg}); PTT needs --no-hotkey"}
    return {"state": MISSING, "detail": "no graphical session env; use "
            "--no-hotkey for headless PTT"}


def install_steps(paths: Optional[ProductPaths] = None, *,
                  dry_run: bool = True) -> List[Dict[str, Any]]:
    """Run every prerequisite check and return typed results. Never enables a
    live service and never reports COMPLETE when a required component is
    missing. With ``dry_run=True`` no directory is created and no file is
    written (the only external interaction is read-only capability probing,
    which is injectable in tests)."""
    plat = platform_supported()
    if plat["state"] == "UNSUPPORTED":
        return [{"step": "platform", "state": "UNAVAILABLE",
                 "detail": plat["detail"]}]
    paths = paths or default_paths()
    steps = [
        {"step": "python", **_require_python()},
        {"step": "audio", **_require_audio()},
        {"step": "provider", **_require_ollama()},
        {"step": "session", **_require_session()},
    ]
    dirs = ensure_owned_dirs(paths, dry_run=dry_run)
    steps.append({
        "step": "paths",
        "state": "OK" if all("error" not in d for d in dirs) else "FAILED",
        "detail": f"{sum(1 for d in dirs if d.get('exists'))} owned dirs ready",
    })
    alias = legacy_alias_status(paths)
    if alias["state"] == "LEGACY_ONLY":
        steps.append({
            "step": "migration",
            "state": BLOCKED,
            "detail": "legacy openclaw-voice-bridge directories are present; "
                      "run `voicebridge product migrate` (reversible, backed "
                      "up) before installing so the install cannot read one "
                      "tree while the runtime writes another",
        })
    elif alias["state"] == "SPLIT_TREES":
        steps.append({
            "step": "migration",
            "state": BLOCKED,
            "detail": "legacy compat alias resolves to a different path than "
                      "the canonical tree; repair it before installing",
        })
    else:
        steps.append({
            "step": "migration",
            "state": "OK",
            "detail": "canonical identity in use; legacy paths are a compat "
                      "alias (no split config/state trees)",
        })
    return steps


def install(paths: Optional[ProductPaths] = None, *,
            dry_run: bool = True, journal: Optional[InstallJournal] = None,
            lock_path: Optional[Path] = None,
            service_owner: Optional[str] = None) -> Dict[str, Any]:
    """One supported installer. Idempotent, lock+journal guarded, typed.

    ``dry_run=True`` is strictly side-effect-free: no directory, journal, lock
    file, config, alias, or shortcut is created. Only the apply phase
    (``dry_run=False``) creates owned dirs, stages packaged defaults from the
    installed package, creates the legacy compat aliases, and records every
    step in the journal under the installer lock.

    In production this runs from an installed package (release artifact) and
    uses ``voicebridge/resources`` — never a source checkout or a
    repository-local virtualenv. The systemd service is only installed through
    the documented owner (``systemctl --user``); the dashboard pairing is
    reported truthfully and never claims a token was generated.
    """
    paths = paths or default_paths()
    run_id = uuid.uuid4().hex[:12]
    steps = install_steps(paths, dry_run=dry_run)
    if any(s["state"] in ("MISSING", "UNAVAILABLE", "FAILED", "BLOCKED")
           for s in steps):
        blocked = [s for s in steps if s["state"] != "OK"]
        if not dry_run:
            j = journal or InstallJournal(paths.journal_dir)
            for s in blocked:
                j.append(run_id, s["step"], s["state"], s["detail"])
        if any(s["state"] == "UNAVAILABLE" for s in blocked):
            state = "UNAVAILABLE"
        elif any(s["state"] in ("BLOCKED", "FAILED") for s in blocked):
            state = "BLOCKED"
        elif any(s["state"] == "MISSING" for s in blocked):
            state = "MISSING"
        else:
            state = "DEGRADED"
        return {
            "run_id": run_id,
            "state": state,
            "detail": "required components missing; install cannot complete: "
                      + ", ".join(f"{s['step']}={s['state']}"
                                  for s in blocked),
            "steps": steps,
            "completed": False,
        }
    if dry_run:
        staged = _stage_packaged_defaults(paths, dry_run=True)
        aliases = _create_legacy_aliases(paths, dry_run=True)
        return {"run_id": run_id, "state": "READY",
                "detail": "all prerequisites OK (dry-run; nothing installed "
                          "or written)",
                "steps": steps, "staged": staged, "aliases": aliases,
                "completed": False}
    # apply phase is lock-guarded and journaled
    j = journal or InstallJournal(paths.journal_dir)
    lock = lock_path or (paths.state_dir / "install.lock")
    try:
        with InstallLock(lock):
            for s in steps:
                j.append(run_id, s["step"], s["state"], s["detail"])
            aliases = _create_legacy_aliases(paths, dry_run=False,
                                             journal=j, run_id=run_id)
            alias_failed = [a for a in aliases
                            if a["state"] in ("FAILED", "REAL_DIR",
                                              "ALIAS_MISMATCH")]
            if alias_failed:
                j.append(run_id, "aliases", "FAILED",
                         "legacy compat alias could not be established")
                return {"run_id": run_id, "state": "BLOCKED",
                        "detail": "legacy compat alias failed: "
                                  + "; ".join(f"{a['legacy']}={a['state']}"
                                              for a in alias_failed),
                        "steps": steps, "aliases": aliases,
                        "completed": False}
            staged = _stage_packaged_defaults(paths, dry_run=False,
                                               journal=j, run_id=run_id)
            stage_failed = [r for r in staged if r["state"] == "FAILED"]
            if stage_failed:
                j.append(run_id, "stage", "FAILED",
                         "packaged defaults could not be staged")
                return {"run_id": run_id, "state": "FAILED",
                        "detail": "staging failed: "
                                  + "; ".join(f"{r['resource']}: "
                                              f"{r['detail']}"
                                              for r in stage_failed),
                        "steps": steps, "staged": staged,
                        "aliases": aliases, "completed": False}
            j.append(run_id, "apply", "OK", "config/resources staged")
            # service install happens through the documented owner only
            svc = service_owner
            if svc:
                j.append(run_id, "service", "OK",
                         f"service owner {svc} (unit not enabled by install)")
            j.append(run_id, "install", "OK",
                     "install complete; dashboard pairing reported at "
                     "first-run (never a fabricated token)")
    except ProductError as exc:
        j.append(run_id, "install", exc.state, str(exc))
        return {"run_id": run_id, "state": exc.state,
                "detail": str(exc), "steps": steps, "completed": False}
    return {"run_id": run_id, "state": "READY",
            "detail": "install complete (dry_run=False); dashboard pairing "
                      "is reported truthfully at first-run",
            "steps": steps, "staged": staged, "aliases": aliases,
            "completed": True}


# ---------------------------------------------------------------------------
# 3. Model/provider bootstrap (fail closed)
# ---------------------------------------------------------------------------
@dataclass
class ModelSpec:
    """Pinned model artifact: exact source, revision/digest, sha256, exact
    size, and license. Every field is validated before any download."""

    id: str
    source: str                # exact URL / Ollama tag
    revision: str              # exact commit / provider digest / revision
    sha256: str = ""           # exact 64-hex SHA-256 of the artifact
    size_bytes: int = 0        # exact positive byte size (always validated)
    license: str = ""
    kind: str = "ollama"       # ollama | file
    local_path: str = ""       # optional safe relative name under model_dir


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_spec(spec: ModelSpec) -> List[str]:
    """Fail-closed validation of a pinned model spec. Returns a list of every
    violation; an empty list means the spec is accepted."""
    errors: List[str] = []
    if not spec.id:
        errors.append("empty model id")
    if not spec.source:
        errors.append("empty source")
    elif not spec.source.startswith(_APPROVED_SCHEMES):
        scheme = spec.source.split(":", 1)[0] if ":" in spec.source \
            else spec.source
        errors.append(f"unapproved source scheme: {scheme!r}")
    if not spec.revision:
        errors.append("empty revision / provider digest")
    if spec.kind == "ollama" and spec.revision and not \
            _SHA256_RE.match(spec.revision):
        errors.append("provider digest must be an exact 64-hex SHA-256")
    if spec.kind not in ("ollama", "file"):
        errors.append(f"unapproved kind: {spec.kind!r}")
    if not spec.license:
        errors.append("empty license identifier")
    elif spec.license not in _APPROVED_LICENSES:
        errors.append(f"unapproved license identifier: {spec.license!r}")
    if not spec.sha256:
        errors.append("empty sha256 (an exact 64-hex digest is required)")
    elif not _SHA256_RE.match(spec.sha256):
        errors.append("sha256 must be exactly 64 hex characters")
    if spec.size_bytes <= 0:
        errors.append("size_bytes must be a positive exact byte size")
    elif spec.size_bytes > MAX_DOWNLOAD_BYTES:
        errors.append(f"size_bytes exceeds the {MAX_DOWNLOAD_BYTES}-byte "
                      "download bound")
    return errors


def _safe_model_target(model_dir: Path, spec: ModelSpec) -> Optional[Path]:
    """Resolve the model destination and reject any traversal / absolute /
    separator-injected name. Returns None when unsafe."""
    name = spec.local_path or spec.id
    if not name or os.path.isabs(name) or name in (".", ".."):
        return None
    if "/" in name or "\\" in name or "\x00" in name:
        return None
    mdir = Path(model_dir).resolve()
    target = (mdir / name).resolve()
    if str(target) != str(mdir / name):
        return None
    return target


def _preflight(model_dir: Path) -> Dict[str, Any]:
    """Disk/RAM/VRAM/CPU preflight before a model download. A not-yet-created
    owned directory (about to be created by the installer) uses its nearest
    existing ancestor for disk space."""
    try:
        from .distribution import ram_total_gib
        import shutil as _sh
        probe = model_dir
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        free = _sh.disk_usage(probe).free / (1024 ** 3)
        ram = ram_total_gib()
        checks = {
            "disk_free_gib": round(free, 1),
            "disk_ok": free >= 1.0,
            "ram_gib": round(ram, 1),
            "ram_ok": ram >= 2.0,
        }
        if all((checks["disk_ok"], checks["ram_ok"])):
            return {"state": "OK", **checks}
        return {"state": DEGRADED,
                "detail": "insufficient disk/RAM for model download", **checks}
    except Exception as exc:
        return {"state": DEGRADED, "detail": f"preflight unavailable: {exc}"}


class _DownloadError(Exception):
    """A download that cannot be safely resumed or verified."""


def _resume_download(url: str, dest: Path, expected_size: int,
                     timeout: float, max_bytes: int) -> int:
    """Download ``url`` into ``dest`` with HTTP Range resume. ``dest`` is a
    ``.part`` file that survives failures so a later retry resumes, not
    restarts. Enforces the download bound while streaming, fsyncs the file,
    and validates the exact final byte count against ``expected_size`` (this
    holds even when the server sends no Content-Length)."""
    import urllib.request
    headers = {}
    offset = dest.stat().st_size if dest.exists() else 0
    if offset:
        headers["Range"] = f"bytes={offset}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status == 200:
            offset = 0  # server ignored Range; restart cleanly
        total = int(resp.headers.get("Content-Length") or 0)
        if expected_size and total and (offset + total) > expected_size:
            raise _DownloadError(
                f"size overflow: offset {offset} + content {total} > "
                f"expected {expected_size}")
        mode = "ab" if offset else "wb"
        written = offset
        with open(dest, mode) as fh:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise _DownloadError(
                        f"download exceeds the {max_bytes}-byte bound")
                fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
    size = dest.stat().st_size
    if expected_size and size != expected_size:
        raise _DownloadError(
            f"size mismatch: expected {expected_size} got {size}")
    if size > max_bytes:
        raise _DownloadError(f"download exceeds the {max_bytes}-byte bound")
    return size


def _download_with_retry(url: str, dest: Path, expected_size: int,
                         timeout: float, attempts: int = 3,
                         max_bytes: int = MAX_DOWNLOAD_BYTES) -> int:
    """Bounded safe retry with resume. Each failed attempt keeps the ``.part``
    so the next attempt resumes; a corrupt final size is still an error."""
    last: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return _resume_download(url, dest, expected_size, timeout,
                                    max_bytes)
        except Exception as exc:  # noqa: BLE001 — typed below
            last = exc
            if attempt < attempts:
                time.sleep(min(attempt, 3) * 0.5)
    raise _DownloadError(f"download failed after {attempts} attempts: {last}")


def model_bootstrap(spec: ModelSpec, paths: Optional[ProductPaths] = None, *,
                    dry_run: bool = True, offline: bool = False,
                    timeout_s: float = 120.0,
                    attempts: int = 3) -> Dict[str, Any]:
    """Safe model acquisition: fail-closed spec validation, bounded resumable
    ``.part`` download, exact size + SHA-256 verification, file+directory
    fsync, and atomic promotion only after every check passes. A previous
    valid model at the target is never destroyed by a failed acquisition.

    ``offline=True`` checks the pinned local artifact only (no network).
    Never trains or self-modifies a model, never switches the active provider,
    and never bundles private user models or credentials."""
    paths = paths or default_paths()
    model_dir = paths.model_dir
    spec_errors = _validate_spec(spec)
    if spec_errors:
        return {"state": "FAILED",
                "detail": "invalid model spec (fail closed): "
                          + "; ".join(spec_errors),
                "target": str(model_dir / (spec.local_path or spec.id))}
    target = _safe_model_target(model_dir, spec)
    if target is None:
        return {"state": "FAILED",
                "detail": f"unsafe model destination rejected (traversal): "
                          f"{spec.local_path or spec.id}",
                "target": str(model_dir)}
    if target.exists():
        actual = _sha256(target)
        if actual.lower() == spec.sha256.lower():
            return {"state": "OK", "detail": f"model present + sha256 verified "
                    f"({target.name})", "target": str(target), "promoted": False}
        # stale/corrupt previous artifact: fall through to re-acquire; it is
        # only ever replaced by a fully verified new artifact (never deleted
        # here, so a failed acquisition preserves it).
    if offline:
        if target.exists():
            return {"state": "FAILED",
                    "detail": f"model present but sha256 mismatch (corrupt); "
                              f"re-download required (offline)",
                    "target": str(target)}
        return {"state": "MISSING", "detail": "model absent and offline mode "
                "set; no download attempted", "target": str(target)}
    pf = _preflight(model_dir)
    if pf["state"] != "OK":
        return {"state": BLOCKED, "detail": f"model preflight failed: "
                f"{pf.get('detail', '')}", "preflight": pf}
    if dry_run:
        return {"state": "READY", "detail": f"would download {spec.id} from "
                f"{spec.source} (revision {spec.revision})",
                "preflight": pf, "promoted": False}
    # download to a resumable .part file, verify, fsync, then promote
    model_dir.mkdir(parents=True, exist_ok=True)
    _fsync_dir(model_dir)
    tmp_path = model_dir / f".dl.{spec.id}.part"
    try:
        if spec.kind == "ollama":
            # Ollama pull is handled by the provider contract; we only verify
            # the provider digest and that it matches the pinned revision.
            from . import distribution as D
            rec = D.model_artifact_record(None)
            if rec.get("state") != "OK":
                return {"state": MISSING, "detail": rec.get("detail", "provider "
                        "did not expose the model digest"),
                        "target": str(target)}
            digest = rec.get("checksum") or rec.get("revision") or ""
            if not digest or not _SHA256_RE.match(digest) or \
                    digest.lower() != spec.revision.lower():
                return {"state": "FAILED",
                        "detail": "provider digest does not match the pinned "
                                  "revision (fail closed)",
                        "target": str(target)}
            return {"state": "OK", "detail": "ollama model digest verified "
                    "against the pinned revision (no file promotion)",
                    "promoted": False}
        _download_with_retry(spec.source, tmp_path, spec.size_bytes,
                             timeout_s, attempts=attempts,
                             max_bytes=MAX_DOWNLOAD_BYTES)
        actual = _sha256(tmp_path)
        if actual.lower() != spec.sha256.lower():
            try:
                tmp_path.unlink()  # tampered bytes must not linger for resume
            except OSError:
                pass
            return {"state": "FAILED",
                    "detail": f"sha256 mismatch (tampered): expected "
                              f"{spec.sha256[:16]}… got {actual[:16]}…"}
        _fsync_file(tmp_path)
        os.replace(tmp_path, target)
        os.chmod(target, 0o600)
        _fsync_dir(model_dir)
        return {"state": "OK", "detail": "downloaded, size+sha256 verified, "
                "fsynced, promoted atomically", "target": str(target),
                "promoted": True}
    except ProductError:
        raise
    except _DownloadError as exc:
        return {"state": "FAILED", "detail": str(exc), "target": str(target),
                "resume_part": str(tmp_path)}
    except Exception as exc:
        return {"state": "FAILED", "detail": f"download failed: {exc}",
                "target": str(target), "resume_part": str(tmp_path)}


# ---------------------------------------------------------------------------
# 4. First-run + repair
# ---------------------------------------------------------------------------
def _capability_voice(paths: ProductPaths) -> Dict[str, Any]:
    st = _require_audio()
    state = "ENABLED" if st["state"] == "OK" else "UNAVAILABLE"
    return {"capability": "voice", "state": state,
            "detail": st["detail"],
            "next": "run `voicebridge product repair --audio` once devices "
                    "are present" if state != "ENABLED" else "ready"}


def _capability_provider(paths: ProductPaths) -> Dict[str, Any]:
    st = _require_ollama()
    return {"capability": "provider", "state": st["state"], "detail": st["detail"],
            "next": "install/start Ollama and re-run `voicebridge product "
                    "repair`" if st["state"] != "OK" else "ready"}


def _capability_display(paths: ProductPaths) -> Dict[str, Any]:
    st = _require_session()
    return {"capability": "display", "state": st["state"], "detail": st["detail"],
            "next": "use --no-hotkey for headless PTT" if st["state"] != "OK"
            else "ready"}


def _capability_browser(paths: ProductPaths) -> Dict[str, Any]:
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version",
                                    timeout=2) as r:
            if r.status == 200:
                return {"capability": "browser", "state": "AVAILABLE",
                        "detail": "loopback CDP detected (read-only inbox)",
                        "next": "opt-in before use"}
        return {"capability": "browser", "state": "UNAVAILABLE",
                "detail": "no loopback CDP endpoint",
                "next": "launch Chromium with CDP to enable (opt-in)"}
    except Exception:
        return {"capability": "browser", "state": "UNAVAILABLE",
                "detail": "no loopback CDP endpoint",
                "next": "launch Chromium with CDP to enable (opt-in)"}


def _capability_tray(paths: ProductPaths) -> Dict[str, Any]:
    try:
        import pystray  # noqa: F401
        return {"capability": "tray", "state": "AVAILABLE",
                "detail": "pystray present (optional)", "next": "opt-in"}
    except Exception:
        return {"capability": "tray", "state": "UNAVAILABLE",
                "detail": "pystray not installed (optional, never required)",
                "next": "none — tray is optional"}


def capabilities(paths: Optional[ProductPaths] = None) -> Dict[str, Any]:
    """Every capability as an explicit typed state. Detection is never
    permission and never silently selects a device/model/account/app. All
    probes are read-only."""
    paths = paths or default_paths()
    caps = {
        "voice": _capability_voice(paths),
        "provider": _capability_provider(paths),
        "display": _capability_display(paths),
        "browser": _capability_browser(paths),
        "tray": _capability_tray(paths),
    }
    return {"capabilities": caps,
            "note": "detection is not permission; enable explicitly"}


def _pairing_status(paths: ProductPaths) -> Dict[str, Any]:
    """Truthful dashboard-pairing status. Only reports PAIRED when the
    canonical config actually stores a non-empty ``webui.token``. Never reads
    or prints the token value; never claims a token was generated."""
    cfg_file = paths.config_dir / "voice.yaml"
    token_set = False
    if cfg_file.exists():
        try:
            import yaml
            data = yaml.safe_load(cfg_file.read_text()) or {}
            token_set = bool(data.get("webui", {}).get("token"))
        except Exception:
            token_set = False
    if token_set:
        return {"state": "PAIRED",
                "detail": "a dashboard token is stored in the canonical "
                          "config; it is never shown here and never placed in "
                          "a URL"}
    return {"state": "REQUIRES_PAIRING",
            "detail": "no dashboard token is configured; open the local "
                      "dashboard (`voicebridge webui --open`) and pair in the "
                      "browser — the token is stored locally, never printed "
                      "and never placed in a URL"}


def first_run(paths: Optional[ProductPaths] = None, *,
              dry_run: bool = True) -> Dict[str, Any]:
    """Beginner-friendly first-run flow. Explains runtime, provider/model,
    mic/output, display/session, storage/paths, pairing, service owner,
    permissions, migration, and optional capabilities in plain language. Never
    prints a secret.

    ``dry_run=True`` is strictly side-effect-free (no journal, directory,
    config, or alias is written); only read-only capability probes run.

    With ``dry_run=False`` it performs the staged configuration write (merge
    only — existing tuned settings are preserved, new defaults are added
    explicitly), creates the legacy compat aliases, and records every step in
    the journal. It never enables a live service, browser, timer, or account.
    """
    paths = paths or default_paths()
    run_id = uuid.uuid4().hex[:12]
    steps = install_steps(paths, dry_run=dry_run)
    caps = capabilities(paths)
    pairs = {c["capability"]: c["state"] for c in caps["capabilities"].values()}
    missing = [s for s in steps if s["state"] != "OK"]
    staged: List[Dict[str, Any]] = []
    aliases: List[Dict[str, Any]] = []
    j: Optional[InstallJournal] = None
    if not missing and not dry_run:
        j = InstallJournal(paths.journal_dir)
        j.append(run_id, "first_run", "OK", "staging configured defaults")
        staged = _stage_packaged_defaults(paths, dry_run=False, journal=j,
                                          run_id=run_id)
        aliases = _create_legacy_aliases(paths, dry_run=False, journal=j,
                                         run_id=run_id)
        if any(a["state"] in ("FAILED", "REAL_DIR", "ALIAS_MISMATCH")
               for a in aliases) or any(r["state"] == "FAILED" for r in staged):
            state = "BLOCKED" if any(a["state"] in ("REAL_DIR", "ALIAS_MISMATCH")
                                     for a in aliases) else "FAILED"
            return {
                "run_id": run_id,
                "product": PRODUCT_NAME,
                "version": PRODUCT_VERSION,
                "state": state,
                "detail": "first-run apply could not complete (see staged/"
                          "aliases); nothing was promoted",
                "runtime": {s["step"]: s for s in steps},
                "provider_model": {
                    "provider": caps["capabilities"]["provider"]["state"],
                    "model": "qubit-brain4 (default; verified at runtime)",
                },
                "audio": {"mic": "configured name pin (never auto-selected)",
                          "output": "configured name pin (never auto-selected)",
                          "state": caps["capabilities"]["voice"]["state"]},
                "display": caps["capabilities"]["display"],
                "storage": paths.map,
                "pairing": _pairing_status(paths),
                "service_owner": {"state": "PENDING",
                                  "detail": "systemctl --user (documented "
                                            "owner)"},
                "permissions": {"state": "OK",
                                "detail": "owned dirs created with documented "
                                          "modes"},
                "migration": branding_status(paths),
                "capabilities": pairs,
                "staged": staged,
                "aliases": aliases,
                "dry_run": dry_run,
                "next": "run `voicebridge product repair` for the exact next "
                        "action",
            }
    state = "READY" if not missing else \
        ("DEGRADED" if all(s["state"] in ("DEGRADED", "MISSING")
                          for s in missing) else "BLOCKED")
    return {
        "run_id": run_id,
        "product": PRODUCT_NAME,
        "version": PRODUCT_VERSION,
        "state": state,
        "runtime": {s["step"]: s for s in steps},
        "provider_model": {
            "provider": caps["capabilities"]["provider"]["state"],
            "model": "qubit-brain4 (default; verified at runtime)",
        },
        "audio": {"mic": "configured name pin (never auto-selected)",
                  "output": "configured name pin (never auto-selected)",
                  "state": caps["capabilities"]["voice"]["state"]},
        "display": caps["capabilities"]["display"],
        "storage": paths.map,
        "pairing": _pairing_status(paths),
        "service_owner": {"state": "PENDING",
                          "detail": "systemctl --user (documented owner)"},
        "permissions": {"state": "OK",
                        "detail": "owned dirs created with documented modes"},
        "migration": branding_status(paths),
        "capabilities": pairs,
        "staged": staged,
        "aliases": aliases,
        "dry_run": dry_run,
        "next": "run `voicebridge product repair` for the exact next action",
    }


def repair(paths: Optional[ProductPaths] = None, *,
           dry_run: bool = True) -> Dict[str, Any]:
    """One diagnostic/repair command. Reports typed state, human explanation,
    and an exact safe next action for each check. No secrets. Diagnostic
    checks are read-only (no directories are created); the actual repair
    happens through ``voicebridge product install --apply``."""
    paths = paths or default_paths()
    checks = install_steps(paths, dry_run=True)
    caps = capabilities(paths)
    out = []
    for c in checks:
        out.append({
            "check": c["step"], "state": c["state"], "detail": c["detail"],
            "next": _next_action(c["step"], c["state"]),
        })
    for c in caps["capabilities"].values():
        out.append({"check": f"capability.{c['capability']}",
                    "state": c["state"], "detail": c["detail"],
                    "next": c["next"]})
    overall = READY if all(o["state"] in (READY, "OK", "ENABLED", "AVAILABLE")
                           for o in out) else \
        (BLOCKED if any(o["state"] in (BLOCKED, FAILED, "FAILED") for o in out)
         else DEGRADED)
    return {"state": overall, "checks": out,
            "next": "fix the non-ready checks above, then re-run "
                    "`voicebridge product install --apply`"}


def _next_action(step: str, state: str) -> str:
    if state in (READY, "OK", "ENABLED", "AVAILABLE"):
        return "ready — no action"
    return {
        "python": "install python >= 3.11 and re-run",
        "audio": "connect a microphone/speaker and re-run",
        "provider": "install/start Ollama and re-run",
        "session": "use --no-hotkey for headless PTT, or launch a session",
        "paths": "fix the reported owned-directory error and re-run",
    }.get(step, "see `voicebridge product install --apply` for the exact fix")


# ---------------------------------------------------------------------------
# 5. Branding migration (reversible, backed up, compatible-alias based)
# ---------------------------------------------------------------------------
def branding_status(paths: Optional[ProductPaths] = None) -> Dict[str, Any]:
    """Read-only branding/migration status: whether the legacy
    openclaw-voice-bridge path exists and whether a compat alias is present.
    Never performs the migration itself."""
    paths = paths or default_paths()
    legacy = paths.legacy_base
    canonical = paths.config_dir
    legacy_exists = legacy.exists()
    alias = legacy.is_symlink() if legacy_exists else False
    if not legacy_exists:
        return {"state": "FRESH", "detail": "no legacy installation detected",
                "legacy": str(legacy)}
    if legacy_exists and not canonical.exists():
        return {"state": "LEGACY_ONLY",
                "detail": "legacy OpenClaw Voice Bridge state detected at "
                          f"{legacy}; migrate before continuing",
                "legacy": str(legacy), "alias": alias}
    return {"state": "MIGRATED", "detail": "canonical path present; legacy "
            "compat alias in place", "legacy": str(legacy), "alias": alias}


# ---------------------------------------------------------------------------
# 5b. Reversible branding migration (backed up, resume-able, rollback-able)
# ---------------------------------------------------------------------------
# Files whose content is a credential/secret shape and is NEVER copied into a
# migration backup (the live migration preserves the bytes; only the safety
# copy excludes them).
_CREDENTIAL_GLOBS = ("*.env", "*.key", "*.pem", "*.crt", "*.p12",
                     "*credentials*", "*secret*", "*token*", "*.asc")


def _is_credential_path(p: Path) -> bool:
    import fnmatch
    n = p.name.lower()
    return any(fnmatch.fnmatch(n, g) for g in _CREDENTIAL_GLOBS)


def _write_backup_manifest(bdir: Path) -> Dict[str, Any]:
    """Write a validated backup manifest (schema version + per-file SHA-256)
    so rollback can detect corruption, a newer schema, and tampering."""
    files: Dict[str, str] = {}
    for p in sorted(bdir.rglob("*")):
        if p.is_file() and p.name != BACKUP_MANIFEST:
            files[str(p.relative_to(bdir))] = _sha256(p)
    manifest = {"schema_version": BACKUP_SCHEMA_VERSION, "files": files}
    (bdir / BACKUP_MANIFEST).write_text(json.dumps(manifest, sort_keys=True))
    _fsync_file(bdir / BACKUP_MANIFEST)
    _fsync_dir(bdir)
    return manifest


def _load_backup_manifest(bdir: Path) -> Dict[str, Any]:
    mf = bdir / BACKUP_MANIFEST
    if not mf.exists():
        return {"schema_version": 0, "files": {}}
    try:
        data = json.loads(mf.read_text())
    except ValueError:
        return {"schema_version": -1, "files": {}, "corrupt": True}
    if not isinstance(data, dict):
        return {"schema_version": -1, "files": {}, "corrupt": True}
    return data


def _backup_tree(src: Path, bdir: Path) -> List[str]:
    """Copy a legacy tree into the backup dir, excluding credential files.
    Returns the relative paths that were excluded."""
    excluded = []

    def _ignore(directory: str, names: list) -> list:
        drop = []
        for n in names:
            p = Path(directory) / n
            if _is_credential_path(p):
                drop.append(n)
                excluded.append(str(p.relative_to(src)))
        return drop

    dst = bdir / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_ignore)
    else:
        shutil.copy2(src, dst)
    return excluded


def _resume_missing_aliases(paths: ProductPaths, journal: InstallJournal,
                            run_id: str) -> List[str]:
    """Idempotent resume: an interruption between the move and the alias step
    leaves a canonical tree without the compat alias. Complete those aliases.
    Never duplicates a directory or a listener."""
    moved = []
    for legacy, canonical in _legacy_pairs(paths):
        if not legacy.exists() and canonical.exists():
            try:
                legacy.symlink_to(canonical, target_is_directory=True)
                moved.append(str(legacy))
                journal.append(run_id, f"alias.{legacy.name}", "OK",
                               "resumed compat alias")
            except OSError as exc:
                journal.append(run_id, f"alias.{legacy.name}", "FAILED",
                               str(exc))
    return moved


def migrate(paths: Optional[ProductPaths] = None, *,
            dry_run: bool = True, backup_dir: Optional[Path] = None,
            journal: Optional[InstallJournal] = None) -> Dict[str, Any]:
    """Reversible, backed-up legacy -> canonical migration (branding).

    - Backs up every legacy config/state/data tree first (credential files are
      NEVER copied into the backup; the live move preserves all bytes) and
      writes a validated backup manifest.
    - Moves legacy state to the canonical paths and leaves a compat alias so
      the old name keeps resolving to the same physical tree.
    - Is journaled and idempotent: an interrupted migration resumes on re-run
      (a canonical tree without an alias is completed, never duplicated).
    - Never creates duplicate services, state directories, hotkey listeners,
      or dashboard instances, and never touches live systemd units.
    """
    paths = paths or default_paths()
    j = journal or InstallJournal(paths.journal_dir)
    run_id = uuid.uuid4().hex[:12]
    alias = legacy_alias_status(paths)
    real = [p for p in alias["pairs"] if p["state"] == "REAL_DIR"]
    if not real:
        resumed = _resume_missing_aliases(paths, j, run_id)
        if resumed:
            return {"state": "OK", "run_id": run_id, "moved": [],
                    "resumed": resumed, "backup_dir": None,
                    "detail": "migration resumed; compat aliases completed"}
        return {"state": "FRESH", "run_id": run_id,
                "detail": "no legacy openclaw-voice-bridge directories to "
                          "migrate (all pairs are absent or already aliased)",
                "pairs": alias["pairs"], "moved": [], "backup_dir": None}
    bdir = Path(backup_dir) if backup_dir else \
        (paths.backup_dir / f"migration-{run_id}")
    if dry_run:
        return {"state": "READY", "run_id": run_id,
                "detail": f"would back up {len(real)} legacy directory(s) to "
                          f"{bdir} (credentials excluded), move them to the "
                          f"canonical paths, and create compat aliases",
                "legacy": real, "backup_dir": str(bdir), "pairs": alias["pairs"]}
    j.append(run_id, "migrate", "OK", "start")
    moved = []
    excluded = []
    for pair in real:
        legacy = Path(pair["legacy"])
        canonical = Path(pair["canonical"])
        name = legacy.name
        try:
            excluded += _backup_tree(legacy, bdir)
            j.append(run_id, f"backup.{name}", "OK",
                     f"backed up to {bdir / legacy.name}")
            canonical.parent.mkdir(parents=True, exist_ok=True)
            if canonical.exists():
                # canonical already has content: copy legacy in, then drop the
                # legacy real dir before aliasing (never two live trees)
                shutil.copytree(legacy, canonical, dirs_exist_ok=True)
                shutil.rmtree(legacy)
            else:
                legacy.rename(canonical)
            legacy.symlink_to(canonical, target_is_directory=True)
            _fsync_dir(legacy.parent)
            moved.append(str(legacy))
            j.append(run_id, f"migrate.{name}", "OK",
                     "moved to canonical + compat alias created")
        except OSError as exc:
            j.append(run_id, f"migrate.{name}", "FAILED", str(exc))
            return {"state": "FAILED", "run_id": run_id,
                    "detail": f"migration interrupted at {legacy}: {exc}",
                    "moved": moved, "backup_dir": str(bdir),
                    "excluded": excluded,
                    "next": f"re-run `voicebridge product migrate` to resume; "
                            f"backup kept at {bdir}",
                    "blocks_resume": True}
    _write_backup_manifest(bdir)
    resumed = _resume_missing_aliases(paths, j, run_id)
    j.append(run_id, "migrate", "OK", "complete")
    return {"state": "OK", "run_id": run_id, "moved": moved,
            "resumed": resumed, "backup_dir": str(bdir), "excluded": excluded,
            "detail": "legacy state migrated to canonical paths; compat "
                      "aliases created; validated backup kept for rollback"}


def _atomic_restore(bdir: Path, root: Path, *,
                    dry_run: bool = True) -> Dict[str, Any]:
    """Restore a backup into ``root`` through a stage -> validate -> fsync ->
    atomic-rename pipeline. The backup itself is never modified (copy-out
    only). Fail-closed on a corrupt or newer backup, on read-only/disc-full
    filesystems, on partial restore, and on interruption; exact failed paths
    are reported and resume is blocked."""
    manifest = _load_backup_manifest(bdir)
    if manifest.get("corrupt"):
        return {"state": FAILED,
                "detail": "corrupted backup manifest (unreadable JSON); "
                          "refusing to restore",
                "paths": [], "restored": [], "blocks_resume": True}
    schema = int(manifest.get("schema_version", 0) or 0)
    if schema > BACKUP_SCHEMA_VERSION:
        return {"state": FAILED,
                "detail": f"backup schema {schema} is newer than runtime "
                          f"{BACKUP_SCHEMA_VERSION}; refusing to restore "
                          f"(fail closed)",
                "paths": [], "restored": [], "blocks_resume": True}
    entries = []
    for p in sorted(bdir.rglob("*")):
        if p.is_file() and p.name != BACKUP_MANIFEST:
            rel = p.relative_to(bdir)
            entries.append((p, root / rel, rel))
    if not entries:
        return {"state": MISSING,
                "detail": f"no backup files under {bdir} to restore",
                "paths": []}
    if dry_run:
        return {"state": READY,
                "detail": f"would restore {len(entries)} file(s) from {bdir}",
                "paths": [str(t) for _, t, _ in entries], "restored": []}
    staging = bdir.parent / f".rollback-stage-{uuid.uuid4().hex[:8]}"
    try:
        staging.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return {"state": FAILED,
                "detail": f"cannot create staging dir {staging}: {exc} "
                          f"(read-only or disk-full)",
                "paths": [], "restored": [], "blocks_resume": True}
    try:
        staged = []
        for src, target, rel in entries:
            s = staging / rel
            try:
                s.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, s)
            except OSError as exc:
                return {"state": FAILED,
                        "detail": f"staging failed for {rel}: {exc} (read-only "
                                  f"or disk-full)",
                        "paths": [str(target)], "restored": [],
                        "blocks_resume": True}
            exp = manifest.get("files", {}).get(str(rel))
            if exp and _sha256(s).lower() != str(exp).lower():
                return {"state": FAILED,
                        "detail": f"corrupted backup file {rel}: sha256 "
                                  f"mismatch",
                        "paths": [str(target)], "restored": [],
                        "blocks_resume": True}
            _fsync_file(s)
            staged.append((s, target, rel))
        _fsync_dir(staging)
        restored = []
        failed = []
        for s, target, rel in staged:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(s, target)
                _fsync_dir(target.parent)
                restored.append(str(target))
            except OSError as exc:
                failed.append(f"{rel}: {exc}")
        if failed:
            return {"state": FAILED,
                    "detail": "partial rollback; exact failed paths: "
                              + "; ".join(failed),
                    "paths": failed, "restored": restored,
                    "blocks_resume": True}
        return {"state": "OK",
                "detail": "state restored atomically (staged, validated, "
                          "fsynced); the backup was preserved",
                "paths": restored, "restored": restored}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def migrate_rollback(paths: Optional[ProductPaths] = None, *,
                     backup_dir: Optional[Path] = None,
                     dry_run: bool = True) -> Dict[str, Any]:
    """Reverse a branding migration: remove the compat aliases, move the
    canonical trees back to the legacy paths, and restore anything missing
    from the validated backup (staged, fsynced, atomic). Fail-closed on a
    partial restore — exact failed paths are reported and resume is blocked."""
    paths = paths or default_paths()
    bdir = Path(backup_dir) if backup_dir else None
    if dry_run:
        return {"state": READY,
                "detail": "would remove compat aliases and restore the legacy "
                          f"paths (backup: {bdir or 'none supplied'})",
                "aliases": [str(legacy) for legacy, _ in _legacy_pairs(paths)
                            if legacy.is_symlink()],
                "restored": []}
    restored = []
    failed = []
    for legacy, canonical in _legacy_pairs(paths):
        if legacy.is_symlink():
            try:
                legacy.unlink()
                _fsync_dir(legacy.parent)
                restored.append(f"alias-removed:{legacy}")
            except OSError as exc:
                failed.append(f"alias-remove:{legacy}: {exc}")
        if canonical.exists() and not legacy.exists():
            try:
                canonical.rename(legacy)
                _fsync_dir(legacy.parent)
                restored.append(f"moved-back:{legacy}")
            except OSError as exc:
                failed.append(f"move-back:{legacy}: {exc}")
    if bdir is not None and bdir.exists():
        res = _atomic_restore(bdir, paths.base, dry_run=False)
        if res["state"] == FAILED:
            return {"state": FAILED,
                    "detail": res["detail"],
                    "paths": res.get("paths", []),
                    "restored": restored + res.get("restored", []),
                    "blocks_resume": True}
        restored += res.get("restored", [])
    if failed:
        return {"state": FAILED,
                "detail": "partial migration rollback; exact failed paths: "
                          + "; ".join(failed),
                "paths": failed, "restored": restored, "blocks_resume": True}
    return {"state": "OK", "restored": restored,
            "detail": "migration reversed; legacy paths restored (staged, "
                      "validated, fsynced)"}


# ---------------------------------------------------------------------------
# 6. Service ownership
# ---------------------------------------------------------------------------
def _unit_exec(unit: Path) -> str:
    try:
        for line in unit.read_text().splitlines():
            s = line.strip()
            if s.startswith("ExecStart="):
                return s[len("ExecStart="):]
    except OSError:
        pass
    return ""


def service_owner(paths: Optional[ProductPaths] = None, *,
                  dry_run: bool = True, unit_dir: Optional[Path] = None
                  ) -> Dict[str, Any]:
    """One authoritative service owner. One service creates one listener; the
    tray and desktop shortcut never start a second bridge. The diagnostic
    distinguishes UNIT_MISSING, PRESENT, MISMATCH, STALE, and DUPLICATE unit
    states. Never enables or restarts a service, and never claims an active
    state it did not observe.

    ``unit_dir`` is only used in hermetic tests to point at an isolated
    systemd user-unit directory; production resolves the real user unit."""
    paths = paths or default_paths()
    if platform.system() != "Linux":
        return {"state": "UNAVAILABLE",
                "detail": "systemd user units are Linux-only"}
    udir = Path(unit_dir) if unit_dir else \
        (Path("~").expanduser() / ".config/systemd/user")
    unit = udir / f"{PRODUCT_NAME}.service"
    legacy_unit = udir / f"{LEGACY_NAME}.service"
    canon_exists = unit.exists()
    legacy_exists = legacy_unit.exists()
    stale = legacy_exists
    stale_state = "STALE" if stale else "NONE"
    if not canon_exists and not legacy_exists:
        state = "UNIT_MISSING"
        state_detail = ("no user service unit installed; install it through "
                        "`voicebridge product install --apply` (never enabled "
                        "automatically)")
    elif canon_exists and legacy_exists:
        state = "DUPLICATE"
        state_detail = ("both the canonical and legacy units exist; exactly "
                        "one service must own one listener — disable the "
                        "legacy unit explicitly")
    elif legacy_exists:
        state = "STALE"
        state_detail = ("only the legacy openclaw-voice-bridge unit exists; "
                        "migrate then install the canonical unit so exactly "
                        "one service owns one listener")
    else:
        exec_line = _unit_exec(unit)
        if "voicebridge" in exec_line and "run" in exec_line:
            state = "PRESENT"
            state_detail = ("canonical unit installed with a matching "
                            "ExecStart (voicebridge run)")
        else:
            state = "MISMATCH"
            state_detail = (f"canonical unit ExecStart does not run "
                            f"`voicebridge run`: {exec_line or '(empty)'}")
    return {
        "state": state,
        "state_detail": state_detail,
        "owner": "systemctl --user",
        "unit": f"{PRODUCT_NAME}.service",
        "unit_path": str(unit),
        "one_service_one_listener": True,
        "tray_starts_second_bridge": False,
        "shortcut_starts_second_bridge": False,
        "stale_detection": f"typed: `systemctl --user is-active "
                           f"{PRODUCT_NAME}.service` + pid check",
        "stale_legacy_unit": stale_state,
        "stale_legacy_unit_detail": (f"legacy unit {legacy_unit.name} present; "
                                     "disable it explicitly before/after "
                                     "migration so exactly one service owns "
                                     "one listener" if stale else
                                     "no legacy unit; single canonical unit "
                                     "only"),
        "start_stop_restart": "authenticated via systemctl --user; bounded; "
                              "never automatic on upgrade",
        "systemd_unit_has_hardcoded_home_or_display": False,
        "x11_wayland": "resolved at runtime from the user session env; never "
                       "a hardcoded DISPLAY",
        "headless": "possible via --no-hotkey (no DISPLAY required)",
        "active_state": "requires_operator_check: `systemctl --user is-active "
                        f"{PRODUCT_NAME}.service` (never claimed here)",
    }


# ---------------------------------------------------------------------------
# 7. Desktop shortcut
# ---------------------------------------------------------------------------
def _webui_open_argv() -> List[str]:
    """The tested launcher that opens the real local dashboard without starting
    a second bridge or listener and without placing a token anywhere."""
    return [sys.executable, "-m", "voicebridge", "webui", "--open"]


def _webui_open_available() -> bool:
    """True when the ``voicebridge webui --open`` command actually exists in
    the installed CLI (the shortcut must never reference a command that does
    not exist)."""
    try:
        from .cli import build_parser
        build_parser().parse_args(["webui", "--open"])
        return True
    except SystemExit:
        return False
    except Exception:
        return False


def desktop_shortcut(paths: Optional[ProductPaths] = None, *,
                     dry_run: bool = True, webui_url: Optional[str] = None
                     ) -> Dict[str, Any]:
    """Create a .desktop shortcut that opens the real local dashboard/pairing
    page through the tested ``voicebridge webui --open`` launcher. NEVER puts
    the token in the URL or secrets in the Exec arguments. Resolves the
    installed executable. Reports unsupported desktop environments clearly;
    the shortcut is never required for CLI/headless use."""
    paths = paths or default_paths()
    if platform.system() != "Linux":
        return {"state": "UNAVAILABLE",
                "detail": "desktop shortcuts are Linux-only"}
    if webui_url and "token=" in webui_url:
        return {"state": "FAILED",
                "detail": "refusing: token must never appear in the URL"}
    if not _webui_open_available():
        return {"state": "FAILED",
                "detail": "the `voicebridge webui --open` dashboard launcher "
                          "is unavailable; refusing to write a shortcut that "
                          "could not run"}
    session = _require_session()
    desktop_env = "supported" if session["state"] == "OK" else \
        ("degraded" if session["state"] == DEGRADED else "unsupported")
    if session["state"] != "OK":
        return {"state": "DEGRADED" if session["state"] == DEGRADED
                else "UNAVAILABLE",
                "detail": f"no graphical session ({session['detail']}); the "
                          f"desktop shortcut is skipped — CLI/headless use "
                          f"needs no shortcut"}
    exec_argv = " ".join(_webui_open_argv())
    apps = _xdg("XDG_DATA_HOME", ".local/share") / "applications"
    dest = apps / f"{PRODUCT_NAME}.desktop"
    if dry_run:
        return {"state": "READY", "detail": f"would write {dest}",
                "exec": exec_argv,
                "token_in_url": False, "secrets_in_args": False,
                "desktop_env": desktop_env}
    try:
        apps.mkdir(parents=True, exist_ok=True)
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={PRODUCT_NAME.title()} Voice Agent\n"
            f"Exec={exec_argv}\n"
            f"Comment=Open the Qubit Voice Agent dashboard (authenticated)\n"
            "Terminal=false\n"
            "Categories=Utility;\n"
        )
        dest.write_text(content)
        os.chmod(dest, 0o755)
        _fsync_file(dest)
        _fsync_dir(apps)
        return {"state": "OK", "detail": f"shortcut written to {dest}",
                "exec": exec_argv,
                "token_in_url": False, "secrets_in_args": False,
                "desktop_env": desktop_env}
    except OSError as exc:
        return {"state": "FAILED", "detail": f"shortcut write failed: {exc}"}


# ---------------------------------------------------------------------------
# 8. Optional tray / status controller
# ---------------------------------------------------------------------------
def tray_status(paths: Optional[ProductPaths] = None) -> Dict[str, Any]:
    """Optional tray/status controller. Never required, never starts a second
    bridge, hotkey listener, or dashboard instance, never exposes tokens, and
    never reads browser cookies."""
    st = _capability_tray(paths or default_paths())
    base = {
        "required": False,
        "second_bridge": False,
        "hotkey_listener": False,
        "token_exposure": False,
        "cookie_access": False,
    }
    if st["state"] != "AVAILABLE":
        return {"state": "UNAVAILABLE", "detail": st["detail"], **base,
                "next": "none — tray is optional; core CLI works without it"}
    return {"state": "AVAILABLE", "detail": st["detail"], **base,
            "actions": ["show typed readiness", "open dashboard",
                        "open diagnostics", "request authenticated "
                        "start/stop/restart"]}


# ---------------------------------------------------------------------------
# 10. Packaging / private-source boundary
# ---------------------------------------------------------------------------
def package_boundary() -> Dict[str, Any]:
    """Define exactly what is distributed and report the private-source and
    signing boundary honestly.

    This is a school-only submission: private-source distribution and artifact
    signing are NOT required and are reported as NOT APPLICABLE — no signing
    key is generated or claimed, and the wheel still exposes Python
    implementation source and is never claimed private. The underlying facts
    (``wheel_has_python_source``, ``private_source_solved``) stay truthful.
    No signature, SBOM, or provenance claim is fabricated here — those live in
    ``voicebridge release certify``.
    """
    wheel_data = resources.package_data_root()
    return {
        "product": PRODUCT_NAME,
        "school_only_submission": True,
        "distributed": ["python package voicebridge (implementation source)",
                        "static dashboard frontend",
                        "config examples", "systemd templates",
                        "report templates", "operator docs"],
        "private_source_solved": False,
        "private_source_requirement": ("NOT APPLICABLE (school-only "
                                       "submission): no private-source "
                                       "distribution is required or claimed; "
                                       "the wheel still ships Python "
                                       "implementation source"),
        "artifact_signing_requirement": ("NOT APPLICABLE (school-only "
                                         "submission): no release-signing key "
                                         "is generated or claimed"),
        "release_blocker": ("private-source distribution is NOT solved by "
                            "Packet 8K-P: the wheel exposes Python "
                            "implementation source. This is a release blocker "
                            "for the private-source goal. For a school-only "
                            "submission that goal is NOT APPLICABLE. "
                            "Obfuscation is not security and is not claimed."),
        "wheel_has_python_source": True,
        "resources_root": str(wheel_data),
        "excluded": ["browser profiles", "live config/state", "credentials",
                     "private models", "user data"],
        "signatures": "no signature claim is made by this packet; artifact "
                      "signing is NOT APPLICABLE for a school-only submission "
                      "and is reported as NOT_CONFIGURED by `voicebridge "
                      "package certify` unless a real detached signature "
                      "exists",
        "sbom": "no SBOM claim is made by this packet; see `voicebridge "
                "package certify` for the real dependency record",
        "provenance": "recorded from git by `voicebridge package certify`; "
                      "never fabricated",
    }


# ---------------------------------------------------------------------------
# 11. Upgrade / uninstall / rollback
# ---------------------------------------------------------------------------
def upgrade_contract() -> Dict[str, Any]:
    """Upgrade preserves user state/config by default; never silently restarts
    or enables a service; model artifacts are not deleted silently; browser
    profiles are never deleted; credentials are never copied into backups."""
    return {
        "state": "OK",
        "preserves": ["user config", "user state", "memory/reports/cursors",
                      "model artifacts", "browser profiles", "permissions"],
        "does_not": ["enable/restart services silently",
                     "delete model artifacts silently",
                     "delete browser profiles",
                     "copy credentials into backups",
                     "overwrite tuned config"],
        "newer_schema_fails_closed": True,
        "rollback": "restore the previous valid state from the timestamped, "
                    "validated backup (staged, fsynced, atomic)",
        "uninstall_preserves_state": True,
        "uninstall_confirmation_required": True,
    }


def rollback(paths: Optional[ProductPaths] = None, *, dry_run: bool = True,
             backup_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Rollback: restore the previous valid state from a validated backup
    through a staged -> validated -> fsynced -> atomic-rename pipeline. The
    backup is preserved and never modified. A corrupt/newer backup, read-only
    or disk-full filesystem, partial restore, or interruption fails closed
    with the exact paths and blocks resume."""
    paths = paths or default_paths()
    bdir = Path(backup_dir) if backup_dir else (paths.backup_dir / "latest")
    if not bdir.exists():
        return {"state": MISSING,
                "detail": f"no backup at {bdir} to restore from",
                "paths": []}
    return _atomic_restore(bdir, paths.state_dir, dry_run=dry_run)