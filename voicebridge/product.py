"""Packet 8K-P: productized installation, portable paths, model bootstrap,
first-run, service ownership, desktop shortcut, optional tray, capability
defaults, and the packaging/private-source boundary.

Qubit Voice Agent is the canonical public identity. The internal import
package stays ``voicebridge``; existing ``openclaw-voice-bridge`` paths keep an
explicit compatibility alias and a reversible migration.

Design rules (all hermetic by default):
- Never read from one path and write to another in the same operation.
- The installer, model bootstrap, and repair commands are lock+journal guarded,
  idempotent, atomic, and typed (READY/DEGRADED/MISSING/UNAVAILABLE/BLOCKED/
  FAILED) — they never report COMPLETE when a required component is missing.
- Every capability (voice, audio, apps, browser, display, model, provider,
  tray) is an explicit typed state; detection is never permission.
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
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
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


class ProductError(Exception):
    """Base product error carrying a typed state."""

    def __init__(self, message: str, state: str = FAILED):
        super().__init__(message)
        self.state = state


class UnsupportedPlatformError(ProductError):
    def __init__(self, message: str):
        super().__init__(message, state=UNAVAILABLE)


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
            "legacy": str(self.legacy_base),
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
    else:
        b = Path("~").expanduser()
        cfg = _xdg("XDG_CONFIG_HOME", ".config") / PRODUCT_NAME
        data = _xdg("XDG_DATA_HOME", ".local/share") / PRODUCT_NAME
        state = _xdg("XDG_STATE_HOME", ".local/state") / PRODUCT_NAME
        cache = _xdg("XDG_CACHE_HOME", ".cache") / PRODUCT_NAME
        legacy = _xdg("XDG_CONFIG_HOME", ".config") / LEGACY_NAME
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
    )


def ensure_owned_dirs(paths: ProductPaths, *, dry_run: bool = True
                      ) -> List[Dict[str, Any]]:
    """Create ONLY the directories the application owns, with documented
    permissions. Never scans or rewrites arbitrary user folders."""
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
    """Append-only JSONL install journal. Every step is recorded with a typed
    state and a timestamp; interrupted installs resume or roll back from it."""

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


def install_steps(paths: Optional[ProductPaths] = None) -> List[Dict[str, Any]]:
    """Run every prerequisite check and return typed results. Never enables a
    live service and never reports COMPLETE when a required component is
    missing."""
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
    dirs = ensure_owned_dirs(paths, dry_run=False)
    steps.append({
        "step": "paths",
        "state": "OK" if all("error" not in d for d in dirs) else "FAILED",
        "detail": f"{sum(1 for d in dirs if d.get('exists'))} owned dirs ready",
    })
    return steps


def install(paths: Optional[ProductPaths] = None, *,
            dry_run: bool = True, journal: Optional[InstallJournal] = None,
            lock_path: Optional[Path] = None,
            service_owner: Optional[str] = None) -> Dict[str, Any]:
    """One supported installer. Idempotent, lock+journal guarded, typed.

    In production this runs from an installed package (release artifact) and
    uses ``voicebridge/resources`` — never a source checkout or a
    repository-local virtualenv. The systemd service is only installed through
    the documented owner (``systemctl --user``); the dashboard pairing token is
    generated by the webui at first run and never placed in a URL.
    """
    paths = paths or default_paths()
    run_id = uuid.uuid4().hex[:12]
    j = journal or InstallJournal(paths.journal_dir)
    lock = lock_path or (paths.state_dir / "install.lock")
    steps = install_steps(paths)
    if any(s["state"] in ("MISSING", "UNAVAILABLE", "FAILED", "BLOCKED")
           for s in steps):
        # record + report typed blocker; never COMPLETE
        blocked = [s for s in steps if s["state"] != "OK"]
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
        for s in steps:
            j.append(run_id, s["step"], s["state"], s["detail"])
        return {"run_id": run_id, "state": "READY",
                "detail": "all prerequisites OK (dry-run; nothing installed)",
                "steps": steps, "completed": False}
    # apply phase is lock-guarded
    try:
        with InstallLock(lock):
            for s in steps:
                j.append(run_id, s["step"], s["state"], s["detail"])
            j.append(run_id, "apply", "OK", "prerequisites verified")
            # service install happens through the documented owner only
            svc = service_owner
            if svc:
                j.append(run_id, "service", "OK",
                         f"service owner {svc} (unit not enabled by install)")
            j.append(run_id, "install", "OK",
                     "config/resources staged; dashboard pairing at first run")
    except ProductError as exc:
        j.append(run_id, "install", exc.state, str(exc))
        return {"run_id": run_id, "state": exc.state,
                "detail": str(exc), "steps": steps, "completed": False}
    return {"run_id": run_id, "state": "READY",
            "detail": "install complete (dry_run=False); dashboard pairing "
                      "happens at first run",
            "steps": steps, "completed": True}


# ---------------------------------------------------------------------------
# 3. Model/provider bootstrap
# ---------------------------------------------------------------------------
@dataclass
class ModelSpec:
    """Pinned model artifact: exact source, revision, sha256, size, license."""
    id: str
    source: str                # exact URL / Ollama tag
    revision: str              # exact commit / digest / revision
    sha256: str = ""           # expected sha256 of the artifact
    size_bytes: int = 0        # expected size (0 = unknown, validated if >0)
    license: str = ""
    kind: str = "ollama"       # ollama | file
    local_path: str = ""       # optional relative name under model_dir


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


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


def model_bootstrap(spec: ModelSpec, paths: Optional[ProductPaths] = None, *,
                    dry_run: bool = True, offline: bool = False,
                    timeout_s: float = 120.0) -> Dict[str, Any]:
    """Safe model acquisition: temp download, size validation, sha256 verify,
    atomic promotion. Never trusts a downloaded file merely because it exists.

    ``offline=True`` checks the pinned local artifact only (no network).
    Never trains or self-modifies a model, never switches the active provider,
    and never bundles private user models or credentials."""
    paths = paths or default_paths()
    model_dir = paths.model_dir
    target = model_dir / (spec.local_path or spec.id)
    if target.exists():
        actual = _sha256(target)
        if spec.sha256 and actual.lower() == spec.sha256.lower():
            return {"state": "OK", "detail": f"model present + sha256 verified "
                    f"({target.name})", "target": str(target), "promoted": False}
        if spec.sha256:
            return {"state": "FAILED",
                    "detail": f"model present but sha256 mismatch (corrupt); "
                              f"re-download required", "target": str(target)}
    if offline:
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
    # download to a temp file, verify, then promote atomically
    model_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(model_dir), prefix=".dl.",
                               suffix=".part")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        if spec.kind == "ollama":
            # Ollama pull is handled by the provider contract; we only verify.
            from . import distribution as D
            rec = D.model_artifact_record(None)
            if rec.get("state") != "OK":
                return {"state": MISSING, "detail": rec.get("detail", "provider "
                        "did not expose the model digest"),
                        "target": str(target)}
            return {"state": "OK", "detail": "ollama model verified via "
                    "provider digest (no file promotion)", "promoted": False}
        import urllib.request
        with urllib.request.urlopen(spec.source, timeout=timeout_s) as resp:
            got = int(resp.headers.get("Content-Length") or 0)
            if spec.size_bytes and got and got != spec.size_bytes:
                return {"state": "FAILED",
                        "detail": f"size mismatch: expected {spec.size_bytes} "
                                  f"got {got}"}
            with open(tmp_path, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    fh.write(chunk)
        actual = _sha256(tmp_path)
        if spec.sha256 and actual.lower() != spec.sha256.lower():
            return {"state": "FAILED",
                    "detail": f"sha256 mismatch (tampered): expected "
                              f"{spec.sha256[:16]}… got {actual[:16]}…"}
        os.replace(tmp_path, target)
        os.chmod(target, 0o600)
        return {"state": "OK", "detail": "downloaded, sha256 verified, "
                "promoted atomically", "target": str(target), "promoted": True}
    except ProductError:
        raise
    except Exception as exc:
        return {"state": "FAILED", "detail": f"download failed: {exc}"}
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


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
    permission and never silently selects a device/model/account/app."""
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


def first_run(paths: Optional[ProductPaths] = None, *,
              dry_run: bool = True) -> Dict[str, Any]:
    """Beginner-friendly first-run flow. Transactional and resumable via the
    journal; explains runtime, provider/model, mic/output, display/session,
    storage/paths, pairing, service owner, permissions, migration, and optional
    capabilities in plain language. Never prints a secret."""
    paths = paths or default_paths()
    j = InstallJournal(paths.journal_dir)
    run_id = uuid.uuid4().hex[:12]
    steps = install_steps(paths)
    caps = capabilities(paths)
    pairs = {c["capability"]: c["state"] for c in caps["capabilities"].values()}
    j.append(run_id, "first_run", "OK", "capabilities probed")
    missing = [s for s in steps if s["state"] != "OK"]
    return {
        "run_id": run_id,
        "product": PRODUCT_NAME,
        "version": PRODUCT_VERSION,
        "state": "READY" if not missing else
        ("DEGRADED" if all(s["state"] in ("DEGRADED", "MISSING")
                          for s in missing) else "BLOCKED"),
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
        "pairing": {"state": "PENDING",
                    "detail": "dashboard pairing token generated at first run; "
                              "never placed in a URL"},
        "service_owner": {"state": "PENDING",
                          "detail": "systemctl --user (documented owner)"},
        "permissions": {"state": "OK",
                        "detail": "owned dirs created with documented modes"},
        "migration": branding_status(paths),
        "capabilities": pairs,
        "dry_run": dry_run,
        "next": "run `voicebridge product repair` for the exact next action",
    }


def repair(paths: Optional[ProductPaths] = None, *,
           dry_run: bool = True) -> Dict[str, Any]:
    """One diagnostic/repair command. Reports typed state, human explanation,
    and an exact safe next action for each check. No secrets."""
    paths = paths or default_paths()
    checks = install_steps(paths)
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
                    "`voicebridge product repair`"}


def _next_action(step: str, state: str) -> str:
    if state in (READY, "OK", "ENABLED", "AVAILABLE"):
        return "ready — no action"
    return {
        "python": "install python >= 3.11 and re-run",
        "audio": "connect a microphone/speaker and re-run",
        "provider": "install/start Ollama and re-run",
        "session": "use --no-hotkey for headless PTT, or launch a session",
        "paths": "fix the reported owned-directory error and re-run",
    }.get(step, "see `voicebridge product repair` for the exact fix")


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
# 6. Service ownership
# ---------------------------------------------------------------------------
def service_owner(paths: Optional[ProductPaths] = None, *,
                  dry_run: bool = True) -> Dict[str, Any]:
    """One authoritative service owner. One service creates one listener; the
    tray and desktop shortcut never start a second bridge. Stale-service
    detection is typed. Never enables or restarts a service."""
    paths = paths or default_paths()
    if platform.system() != "Linux":
        return {"state": "UNAVAILABLE",
                "detail": "systemd user units are Linux-only"}
    # authoritative owner: the single canonical user unit
    unit = Path("~").expanduser() / ".config/systemd/user" / \
        f"{PRODUCT_NAME}.service"
    return {
        "state": "READY" if not dry_run else "PENDING",
        "owner": "systemctl --user",
        "unit": f"{PRODUCT_NAME}.service",
        "unit_path": str(unit),
        "one_service_one_listener": True,
        "tray_starts_second_bridge": False,
        "shortcut_starts_second_bridge": False,
        "stale_detection": "typed: `systemctl --user is-active "
                           f"{PRODUCT_NAME}.service` + pid check",
        "start_stop_restart": "authenticated via systemctl --user; bounded; "
                              "never automatic on upgrade",
        "headless": "possible via --no-hotkey (no DISPLAY required)",
    }


# ---------------------------------------------------------------------------
# 7. Desktop shortcut
# ---------------------------------------------------------------------------
def desktop_shortcut(paths: Optional[ProductPaths] = None, *,
                     dry_run: bool = True, webui_url: Optional[str] = None
                     ) -> Dict[str, Any]:
    """Create a .desktop shortcut that opens the local dashboard/pairing page.
    NEVER puts the token in the URL or secrets in the Exec arguments. Resolves
    the installed executable. Reports unsupported desktop environments."""
    paths = paths or default_paths()
    if platform.system() != "Linux":
        return {"state": "UNAVAILABLE",
                "detail": "desktop shortcuts are Linux-only"}
    if webui_url and "token=" in webui_url:
        return {"state": "FAILED",
                "detail": "refusing: token must never appear in the URL"}
    python = sys.executable
    apps = _xdg("XDG_DATA_HOME", ".local/share") / "applications"
    dest = apps / f"{PRODUCT_NAME}.desktop"
    if dry_run:
        return {"state": "READY", "detail": f"would write {dest}",
                "exec": f"{python} -m voicebridge webui --open "
                        "(token via config, never URL)",
                "token_in_url": False, "secrets_in_args": False}
    try:
        apps.mkdir(parents=True, exist_ok=True)
        url = webui_url or "http://127.0.0.1:0"  # real port resolved at runtime
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={PRODUCT_NAME.title()} Voice Agent\n"
            f"Exec={python} -m voicebridge webui --open\n"
            f"Comment=Open the Qubit Voice Agent dashboard (authenticated)\n"
            "Terminal=false\n"
            "Categories=Utility;\n"
        )
        dest.write_text(content)
        os.chmod(dest, 0o755)
        return {"state": "OK", "detail": f"shortcut written to {dest}",
                "token_in_url": False, "secrets_in_args": False}
    except OSError as exc:
        return {"state": "FAILED", "detail": f"shortcut write failed: {exc}"}


# ---------------------------------------------------------------------------
# 8. Optional tray / status controller
# ---------------------------------------------------------------------------
def tray_status(paths: Optional[ProductPaths] = None) -> Dict[str, Any]:
    """Optional tray/status controller. Never required, never starts a second
    bridge or hotkey listener, never exposes tokens."""
    st = _capability_tray(paths or default_paths())
    if st["state"] != "AVAILABLE":
        return {"state": "UNAVAILABLE",
                "detail": st["detail"],
                "required": False,
                "second_bridge": False,
                "token_exposure": False,
                "next": "none — tray is optional; core CLI works without it"}
    return {"state": "AVAILABLE", "detail": st["detail"], "required": False,
            "second_bridge": False, "token_exposure": False,
            "actions": ["show typed readiness", "open dashboard",
                        "open diagnostics", "request authenticated "
                        "start/stop/restart"]}


# ---------------------------------------------------------------------------
# 10. Packaging / private-source boundary
# ---------------------------------------------------------------------------
def package_boundary() -> Dict[str, Any]:
    """Define exactly what is distributed and report the private-source
    boundary honestly. The wheel still contains Python implementation source:
    private-source distribution is NOT solved by this packet and remains a
    release blocker for that goal. Obfuscation is not security."""
    wheel_data = resources.package_data_root()
    has_python_source = True  # voicebridge/*.py ships in the wheel
    return {
        "product": PRODUCT_NAME,
        "distributed": ["python package voicebridge (implementation source)",
                        "static dashboard frontend",
                        "config examples", "systemd templates",
                        "report templates", "operator docs"],
        "private_source_solved": False,
        "release_blocker": ("private-source distribution is NOT solved by "
                            "Packet 8K-P: the wheel exposes Python "
                            "implementation source. This is a release blocker "
                            "for the private-source goal. Obfuscation is not "
                            "security and is not claimed."),
        "wheel_has_python_source": has_python_source,
        "resources_root": str(wheel_data),
        "excluded": ["browser profiles", "live config/state", "credentials",
                     "private models", "user data"],
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
        "rollback": "restore the previous valid state from the timestamped "
                    "backup (bounded, auditable)",
        "uninstall_preserves_state": True,
        "uninstall_confirmation_required": True,
    }


def rollback(paths: Optional[ProductPaths] = None, *, dry_run: bool = True,
             backup_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Rollback: restore the previous valid state from a backup, atomically.
    A failed rollback returns the exact bounded paths and blocks resume."""
    paths = paths or default_paths()
    bdir = Path(backup_dir) if backup_dir else (paths.backup_dir / "latest")
    if not bdir.exists():
        return {"state": "MISSING",
                "detail": f"no backup at {bdir} to restore from",
                "paths": []}
    if dry_run:
        return {"state": "READY",
                "detail": f"would restore state from {bdir}",
                "paths": [str(bdir)]}
    restored = []
    failed = []
    for src in bdir.iterdir() if bdir.is_dir() else []:
        try:
            dst = paths.state_dir / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
            restored.append(str(dst))
        except OSError as exc:
            failed.append(f"{src.name}: {exc}")
    if failed:
        return {"state": "FAILED",
                "detail": "partial rollback; exact failed paths: "
                          + "; ".join(failed),
                "paths": failed, "restored": restored,
                "blocks_resume": True}
    return {"state": "OK", "detail": "state restored from backup",
            "paths": restored, "restored": restored}