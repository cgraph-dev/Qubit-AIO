"""Packet 7C: verified download-and-run distribution surface.

Defines the versioned package manifest, the hardware/resource doctor, the
model-artifact acquisition record (revision/checksum/license metadata), the
launcher contract, and the upgrade/uninstall behavior. Everything here is
read-only/diagnostic: it never installs, enables, restarts, or mutates a live
service, browser, config, or state. Platform-specific adapters are explicit
and Windows/macOS remain documented future targets.

The manifest + doctor are consumed by the CLI (`voicebridge package ...`) and
by the portable-state/device-enrollment flow so a fresh supported Ubuntu host
can verify hardware, acquire the model artifact with pinned metadata, launch,
upgrade, and uninstall without depending on the developer home directory.
"""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PACKAGE_NAME = "qubit-voicebridge"
PACKAGE_VERSION = "1.0.0"
SUPPORTED_TARGET = {
    "os": "ubuntu",
    "arches": ["x86_64", "amd64"],
    "python_min": "3.11",
    "notes": "Ubuntu is the first supported download-and-run target; Windows "
             "and macOS are documented future targets, not a universal binary.",
}
MIN_DISK_FREE_GIB = 2.0
MIN_RAM_GIB = 4.0
VRAM_CEILING_MIB = 5 * 1024          # whole-agent 5 GB VRAM budget (plan 6C)
OPENSSL_REQUIRED = "3.0.0"

# The canonical local model artifact. The revision/checksum are filled in by
# the doctor from the running Ollama (`/api/show` digest) — never baked as a
# fake value. The record here is the ACCEPTED alias + license contract.
MODEL_ARTIFACT = {
    "alias": "qubit-brain",
    "kind": "ollama-model",
    "license": "Apache-2.0 (Qwen3-4B-Instruct-2507-derived; see "
               "docs/archive/2026-09-21/UPSTREAM_REUSE.md)",
    "revision": "resolved-at-runtime",   # replaced by doctor with /api/show digest
    "checksum": "resolved-at-runtime",   # replaced by doctor with X-Ollama-Digest
    "disk_cost_mib": "measured-at-runtime",
}


def manifest() -> Dict[str, Any]:
    """The versioned package manifest (read-only)."""
    return {
        "name": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "target": SUPPORTED_TARGET,
        "stack": ["python", "static-js", "ollama", "systemd", "bubblewrap",
                  "piper", "sherpa-onnx"],
        "model": dict(MODEL_ARTIFACT),
        "launcher": "scripts/run.sh or systemd unit (ExecStart "
                    "python -m voicebridge run)",
        "upgrade": "re-run install.sh / install_systemd_unit.sh; rendered "
                   "units preserve machine overrides with .bak (Packet 0B)",
        "uninstall": "remove the systemd unit, venv, and optional state only "
                     "after explicit operator confirmation (never silently)",
        "portable_state": "voicebridge export-state / import-state (encrypted, "
                          "versioned, audited mechanism)",
        "resource_guardrails": {
            "min_disk_free_gib": MIN_DISK_FREE_GIB,
            "min_ram_gib": MIN_RAM_GIB,
            "vram_ceiling_mib": VRAM_CEILING_MIB,
        },
    }


def _run(cmd: List[str], timeout: float = 5.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def os_target_ok() -> bool:
    return platform.system() == "Linux" and "ubuntu" in (
        platform.version() + " " + platform.release()).lower()


def ram_total_gib() -> float:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024 * 1024)
    except OSError:
        pass
    return 0.0


def disk_free_gib(path: Optional[Path] = None) -> float:
    p = path or Path.home()
    try:
        import shutil as _sh
        return _sh.disk_usage(p).free / (1024 ** 3)
    except OSError:
        return 0.0


def openssl_version() -> str:
    return _run(["openssl", "version"])


def openssl_ok() -> bool:
    ver = openssl_version()
    if not ver:
        return False
    m = re.search(r"OpenSSL\s+(\d+\.\d+\.\d+)", ver)
    if not m:
        return False
    return [int(x) for x in m.group(1).split(".")] >= [
        int(x) for x in OPENSSL_REQUIRED.split(".")]


def ollama_available(url: str = "http://127.0.0.1:11434") -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def chromium_cdp_available() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version",
                                    timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def audio_devices() -> int:
    try:
        import sounddevice as sd
        return len(sd.query_devices())
    except Exception:
        return 0


def free_vram_mib() -> Optional[int]:
    try:
        from .vram_guard import free_vram_mib as _f
        return _f(timeout_s=3.0)
    except Exception:
        return None


def gpu_present() -> bool:
    v = free_vram_mib()
    return v is not None and v >= 0


def model_artifact_record(cfg: Any) -> Dict[str, Any]:
    """Resolve the model artifact contract: config alias + live Ollama digest
    from /api/show. FAILS CLOSED: if the digest cannot be obtained the record
    reports a typed MISSING state with the reason — never a fabricated
    revision/checksum. Records license and source provenance."""
    from .runner_contract import resolve, _fetch_digest
    rc = resolve(cfg)
    rec = dict(MODEL_ARTIFACT)
    rec["alias"] = rc.alias or rec["alias"]
    rec["url"] = rc.url
    rec["num_ctx"] = rc.num_ctx
    rec["keep_alive"] = rc.keep_alive
    digest = _fetch_digest(rc.url, rc.alias, timeout_s=3.0) or rc.digest
    if not digest:
        rec["state"] = "MISSING"
        rec["revision"] = None
        rec["checksum"] = None
        rec["detail"] = f"model digest unavailable from {rc.url} for " \
                        f"{rc.alias!r} (fail closed)"
        rec["license"] = MODEL_ARTIFACT.get("license", "")
        rec["source"] = "resolved-at-runtime (Ollama /api/show digest)"
        return rec
    rec["state"] = "OK"
    rec["revision"] = digest
    rec["checksum"] = digest
    rec["detail"] = f"digest verified via {rc.url} (X-Ollama-Digest)"
    rec["license"] = MODEL_ARTIFACT.get("license", "")
    rec["source"] = "resolved-at-runtime (Ollama /api/show digest)"
    return rec


def artifact_checksum(path: Path) -> Optional[str]:
    """SHA-256 of a downloaded model artifact file; None when missing."""
    try:
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def verify_artifact(path: Path, expected_sha256: Optional[str]) -> Dict[str, str]:
    """Checksum verification for a downloaded artifact.
    Returns typed {state, detail}: OK / MISSING / FAIL."""
    if not Path(path).exists():
        return {"state": "MISSING",
                "detail": f"artifact missing: {path}"}
    actual = artifact_checksum(path)
    if expected_sha256 is None:
        return {"state": "OK", "detail": "no pinned checksum to compare"}
    if actual == expected_sha256.lower():
        return {"state": "OK", "detail": "sha256 verified"}
    return {"state": "FAIL",
            "detail": f"sha256 mismatch (tampered): expected "
                      f"{expected_sha256[:16]}… got {actual[:16]}…"}


def doctor(cfg: Any) -> List[Dict[str, Any]]:
    """Typed hardware/resource checks. Every entry: name, state, detail.
    States: OK | DEGRADED | MISSING | NOT_SUPPORTED."""
    checks: List[Dict[str, Any]] = []
    checks.append({
        "name": "distro",
        "state": "OK" if os_target_ok() else "NOT_SUPPORTED",
        "detail": f"{platform.system()} {platform.release()}",
    })
    checks.append({
        "name": "arch",
        "state": "OK" if platform.machine() in ("x86_64", "amd64") else "NOT_SUPPORTED",
        "detail": platform.machine(),
    })
    ram = ram_total_gib()
    checks.append({
        "name": "ram",
        "state": "OK" if ram >= MIN_RAM_GIB else "DEGRADED",
        "detail": f"{ram:.1f} GiB (min {MIN_RAM_GIB})",
    })
    disk = disk_free_gib()
    checks.append({
        "name": "disk",
        "state": "OK" if disk >= MIN_DISK_FREE_GIB else "DEGRADED",
        "detail": f"{disk:.1f} GiB free (min {MIN_DISK_FREE_GIB})",
    })
    checks.append({
        "name": "gpu",
        "state": "OK" if gpu_present() else "MISSING",
        "detail": f"free VRAM {free_vram_mib()} MiB (ceiling {VRAM_CEILING_MIB})",
    })
    checks.append({
        "name": "ollama",
        "state": "OK" if ollama_available() else "MISSING",
        "detail": "127.0.0.1:11434",
    })
    model_rec = model_artifact_record(cfg)
    checks.append({
        "name": "model",
        "state": model_rec.get("state", "MISSING"),
        "detail": f"{model_rec.get('alias')} digest="
                  f"{(model_rec.get('checksum') or 'unavailable')[:16]}… "
                  f"({model_rec.get('detail', '')})",
    })
    checks.append({
        "name": "browser_cdp",
        "state": "OK" if chromium_cdp_available() else "MISSING",
        "detail": "127.0.0.1:9222 loopback CDP (inbox/communications)",
    })
    checks.append({
        "name": "audio",
        "state": "OK" if audio_devices() > 0 else "MISSING",
        "detail": f"{audio_devices()} devices",
    })
    checks.append({
        "name": "openssl",
        "state": "OK" if openssl_ok() else "DEGRADED",
        "detail": openssl_version() or "openssl not found",
    })
    checks.append({
        "name": "isolation",
        "state": "OK" if shutil.which("bwrap") else "MISSING",
        "detail": "bubblewrap",
    })
    return checks


def doctor_summary(checks: List[Dict[str, Any]]) -> str:
    bad = [c["name"] for c in checks if c["state"] in ("DEGRADED", "MISSING", "NOT_SUPPORTED")]
    if not bad:
        return "all hardware/resource checks OK"
    return f"degraded/missing: {', '.join(bad)}"


def launcher_command(cfg: Any, *, no_hotkey: bool = False) -> List[str]:
    """The verified launch argv for this host (read-only contract)."""
    cmd = [sys.executable, "-m", "voicebridge", "run"]
    if no_hotkey:
        cmd.append("--no-hotkey")
    return cmd


def clean_install_required_steps(cfg: Any) -> List[str]:
    """Clean-install diagnostics: what a fresh supported host must verify
    before launch. Read-only."""
    return [
        "python >= 3.11 venv installed from pyproject.toml",
        "declared deps only (check_clean_imports / clean_venv_check --check)",
        "config present at ~/.config/qubit-voicebridge/voice.yaml",
        "model artifact pulled: qubit-brain (digest recorded by doctor)",
        "piper voice downloaded under model_dir",
        "systemd user unit rendered with machine overrides preserved",
        "config doctor --static passes (no undeclared imports, no home paths)",
    ]


def upgrade_plan(cfg: Any) -> Dict[str, Any]:
    """Upgrade behavior: what a re-run changes and what it preserves. Never
    enables a service or overwrites a tuned unit silently."""
    from .feature_flags import names as _flag_names
    return {
        "action": "upgrade",
        "steps": [
            "re-run scripts/install.sh (idempotent venv/deps/models)",
            "render systemd unit via install_systemd_unit.sh; existing tuned "
            "unit PRESERVED with .bak unless INSTALL_FORCE_UNIT=1",
            "re-run config doctor --static + --reconcile",
            f"feature flags untouched: {', '.join(_flag_names())}",
        ],
        "does_not": ["enable/restart live services silently",
                     "overwrite live user config",
                     "delete state/memory/reports/cursors"],
        "rollback": "restore the .bak that install_systemd_unit.sh keeps",
    }


def uninstall_plan(cfg: Any) -> Dict[str, Any]:
    """Uninstall behavior: explicit, non-destructive by default; state is
    preserved and removal requires operator confirmation."""
    return {
        "action": "uninstall",
        "requires_confirmation": True,
        "steps_if_confirmed": [
            "disable + remove only the qubit-voicebridge user unit",
            "remove the .venv and repo checkout",
        ],
        "preserves_by_default": ["state dir (memory/reports/cursors)",
                                 "user config",
                                 "Ollama model artifacts (shared)",
                                 "browser profiles"],
        "rollback": "re-run install.sh after re-cloning; state dir untouched",
    }


# ---------------------------------------------------------------------------
# Distributable artifact completeness (Packet 7C remediation)
# ---------------------------------------------------------------------------
DISTRIBUTABLE_FILES = [
    ("scripts/install.sh", "installer"),
    ("scripts/install_systemd_unit.sh", "installer"),
    ("scripts/run.sh", "launcher"),
    ("config/voice.example.yaml", "config-template"),
    ("systemd/qubit-voicebridge.service", "systemd-template"),
    ("pyproject.toml", "metadata"),
]


def distributable_manifest(repo: Optional[Path] = None) -> Dict[str, Any]:
    """The explicit list of files a download-and-run distribution must carry:
    launcher, installer, config template, systemd template, and metadata.
    No universal binary is claimed; Ubuntu is the single supported target."""
    root = Path(repo) if repo else Path(__file__).resolve().parents[1]
    entries = []
    for rel, role in DISTRIBUTABLE_FILES:
        p = root / rel
        entries.append({
            "path": rel,
            "role": role,
            "present": p.exists(),
            "sha256": artifact_checksum(p) if p.exists() else None,
        })
    return {
        "name": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "target": SUPPORTED_TARGET,
        "binary_claim": "none (not a universal binary; Ubuntu x86_64 only)",
        "files": entries,
        "complete": all(e["present"] for e in entries),
    }


def verify_distributable(repo: Optional[Path] = None) -> Dict[str, str]:
    """Typed completeness check for the distributable artifact."""
    m = distributable_manifest(repo)
    missing = [e["path"] for e in m["files"] if not e["present"]]
    if not m["complete"]:
        return {"state": "MISSING",
                "detail": "missing required files: " + ", ".join(missing)}
    bad = [e for e in m["files"] if e["sha256"] is None]
    if bad:
        return {"state": "DEGRADED",
                "detail": f"{len(bad)} files unreadable for checksum"}
    return {"state": "OK", "detail": f"{len(m['files'])} files present with "
                                     f"sha256; version {m['version']}"}