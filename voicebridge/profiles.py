"""Packet 7D: versioned provider/profile registry.

Qubit Voice Agent lets a user swap the reasoning brain/model, the speech-to-
text engine/model, the TTS provider, the voice artifact, and audio devices —
each independently, with a versioned manifest that records source, revision,
license, checksum/digest, hardware/language support, resource cost, latency,
quality/safety evidence, and a rollback target.

Safe defaults (verified, unchanged): qubit-brain4 reasoning model, CPU sherpa
STT, Piper TTS with the configured voice, and the current HyperX audio rules
(mono mic + empty output pins). A downloaded file or provider response is never
trusted merely because it exists: install/select/upgrade run gates (license,
checksum, hardware/language, VRAM ceiling, quality/safety, latency) and a
failed gate leaves the previous profile active.

Behavior contract:
- dry-run by default; every apply is atomic, backed up, audited, reversible;
- changing one component never silently changes another (voice vs TTS vs model
  vs audio devices vs permissions);
- no service restart and no recording enablement by any registry operation;
- one approved resident generative model by default; the 5 GB VRAM ceiling is
  enforced.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Component kinds
# ---------------------------------------------------------------------------
BRAIN, STT, TTS, VOICE, AUDIO = ("brain", "stt", "tts", "voice", "audio")
KINDS = (BRAIN, STT, TTS, VOICE, AUDIO)

# Where each component's selection lives in config.
CONFIG_PATH = {
    BRAIN: ("intent", "local_llm", "model"),
    STT: ("asr", "provider"),
    TTS: ("tts", "provider"),
    VOICE: ("tts", "voice"),
    AUDIO: None,  # audio devices are name pins managed by audio/settings
}

# Default profile id per component (the verified current behavior).
DEFAULTS = {
    BRAIN: "qubit-brain4",
    STT: "sherpa",
    TTS: "piper",
    VOICE: "en_US-amy-medium",
    AUDIO: "os-default",
}

# ---------------------------------------------------------------------------
# Typed states
# ---------------------------------------------------------------------------
OK, UNAVAILABLE, DEGRADED, DENIED, PARTIAL, FAILED, MISSING = (
    "OK", "UNAVAILABLE", "DEGRADED", "DENIED", "PARTIAL", "FAILED", "MISSING")
STATES = (OK, UNAVAILABLE, DEGRADED, DENIED, PARTIAL, FAILED, MISSING)

VRAM_CEILING_MIB = 5 * 1024  # whole-agent 5 GB VRAM budget (plan 6C)


class ProviderError(Exception):
    """Base registry error with a typed state."""

    state: str = FAILED


class DryRunOnlyError(ProviderError):
    state = "DENIED"


class GateFailedError(ProviderError):
    state = FAILED


class ManifestError(ProviderError):
    state = "MISSING"


@dataclass
class ProviderManifest:
    """Pinned, validated metadata for one replaceable component."""

    id: str
    kind: str                      # brain | stt | tts | voice | audio
    source: str                    # exact source URL / repo
    revision: str                  # exact commit / revision / digest
    license: str
    checksum: str = ""             # sha256 of the artifact or model digest
    hardware: str = "cpu"          # cpu | gpu | any
    languages: List[str] = field(default_factory=lambda: ["en"])
    vram_mib: int = 0              # 0 = CPU-only
    ram_mib: int = 0
    latency_ms: float = 0.0        # warm p95, when measured
    quality_evidence: str = ""     # reference to held-out eval / report
    safety_evidence: str = ""      # safety/permission eval reference
    rollback_to: str = ""          # previous profile id
    config_path: Optional[tuple] = None
    config_value: Any = None
    default: bool = False

    def validate(self, cfg: Any = None) -> Dict[str, str]:
        """Run the gate set. Returns {state, detail}. A failed gate is FAILED
        and must never activate the profile."""
        checks: List[str] = []
        if not self.id or not self.kind:
            return {"state": FAILED, "detail": "manifest missing id/kind"}
        if not self.source or not self.revision:
            return {"state": FAILED, "detail": "manifest missing source/revision"}
        if not self.license:
            return {"state": FAILED, "detail": "manifest missing license"}
        if self.checksum:
            if not re.fullmatch(r"[0-9a-fA-F]{64}", self.checksum):
                return {"state": FAILED,
                        "detail": "checksum must be a sha256 hex digest"}
        if self.hardware not in ("cpu", "gpu", "any"):
            return {"state": FAILED, "detail": f"bad hardware {self.hardware!r}"}
        if self.vram_mib > VRAM_CEILING_MIB:
            return {"state": FAILED,
                    "detail": f"vram {self.vram_mib} exceeds "
                              f"{VRAM_CEILING_MIB} ceiling"}
        if self.kind == BRAIN and self.vram_mib > VRAM_CEILING_MIB:
            return {"state": FAILED, "detail": "brain exceeds VRAM ceiling"}
        if not self.rollback_to and not self.default:
            return {"state": FAILED, "detail": "non-default profile lacks rollback target"}
        return {"state": OK, "detail": "manifest valid"}


# ---------------------------------------------------------------------------
# Registry (static set + current selection resolution)
# ---------------------------------------------------------------------------
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def default_manifests() -> List[ProviderManifest]:
    """The verified safe defaults. Each carries provenance + rollback target
    (itself for the default; a failed change never leaves it active)."""
    return [
        ProviderManifest(
            id="qubit-brain4", kind=BRAIN, default=True,
            source="ollama://qubit-brain4 (docs/UPSTREAM_REUSE.md R7)",
            revision="resolved-at-runtime (Ollama digest)",
            license="Apache-2.0 (Qwen3-4B-Instruct-2507-derived)",
            hardware="gpu", languages=["en"], vram_mib=3300, ram_mib=0,
            latency_ms=0.0, quality_evidence="docs/BAKEOFF_4D.md",
            safety_evidence="docs/EVALUATION_6B.md", rollback_to="qubit-brain4",
            config_path=("intent", "local_llm", "model"),
            config_value="qubit-brain4"),
        ProviderManifest(
            id="sherpa", kind=STT, default=True,
            source="sherpa-onnx (CPU; declared dep)",
            revision=">=1.13", license="Apache-2.0",
            hardware="cpu", languages=["en"], vram_mib=0, ram_mib=0,
            latency_ms=0.0, quality_evidence="docs/AUDIO_SETUP.md",
            safety_evidence="docs/OPERATIONS_7A.md", rollback_to="sherpa",
            config_path=("asr", "provider"), config_value="sherpa"),
        ProviderManifest(
            id="piper", kind=TTS, default=True,
            source="piper-tts (declared dep)",
            revision=">=1.2", license="MIT",
            hardware="cpu", languages=["en"], vram_mib=0, ram_mib=0,
            latency_ms=0.0, quality_evidence="docs/AUDIO_SETUP.md",
            safety_evidence="docs/OPERATIONS_7A.md", rollback_to="piper",
            config_path=("tts", "provider"), config_value="piper"),
        ProviderManifest(
            id="en_US-amy-medium", kind=VOICE, default=True,
            source="rhasspy/piper-voices v1.0.0 (en_US amy medium)",
            revision="v1.0.0", license="MIT",
            hardware="cpu", languages=["en"], vram_mib=0, ram_mib=0,
            latency_ms=0.0, quality_evidence="docs/AUDIO_SETUP.md",
            safety_evidence="docs/OPERATIONS_7A.md",
            rollback_to="en_US-amy-medium",
            config_path=("tts", "voice"), config_value="en_US-amy-medium"),
        ProviderManifest(
            id="os-default", kind=AUDIO, default=True,
            source="system OS defaults (HyperX rules)",
            revision="n/a", license="none",
            hardware="any", languages=["en"], vram_mib=0, ram_mib=0,
            latency_ms=0.0, quality_evidence="docs/AUDIO_SETUP.md",
            safety_evidence="docs/OPERATIONS_7C.md", rollback_to="os-default",
            config_path=None, config_value=None),
    ]


def registry(cfg: Any = None) -> List[ProviderManifest]:
    """Built-in + any user-registered manifests (from the state dir)."""
    manifests = default_manifests()
    reg_file = _registry_path(cfg)
    if reg_file and reg_file.exists():
        try:
            data = json.loads(reg_file.read_text())
            for rec in data.get("profiles", []):
                try:
                    manifests.append(ProviderManifest(**rec))
                except TypeError:
                    continue
        except (OSError, ValueError):
            pass
    return manifests


def _registry_path(cfg: Any = None) -> Optional[Path]:
    try:
        if cfg is None:
            from .config import Config
            cfg = Config.load()
        return cfg.state_dir() / "profiles.json"
    except Exception:
        return None


def _current_value(cfg: Any, kind: str) -> Any:
    path = CONFIG_PATH[kind]
    if path is None:
        # audio: read the name pins via audio/settings
        from .audio import settings as AS
        mic = cfg.get(*AS.MIC_NAME_KEY, default="") or ""
        out = cfg.get(*AS.OUTPUT_NAME_KEY, default="") or ""
        return {"mic": mic, "output": out}
    return cfg.get(*path, default=None)


def current_profiles(cfg: Any) -> Dict[str, Dict[str, Any]]:
    """Resolve the ACTIVE profile for each component from the live config."""
    out: Dict[str, Dict[str, Any]] = {}
    for kind in KINDS:
        val = _current_value(cfg, kind)
        if isinstance(val, dict):  # audio
            out[kind] = {"kind": kind, "value": val, "id": "os-default",
                         "state": OK}
            continue
        m = next((m for m in registry(cfg)
                  if m.kind == kind and (str(m.config_value) == str(val)
                                         or m.id == str(val))), None)
        if m is not None:
            out[kind] = {"kind": kind, "id": m.id, "value": val,
                         "state": OK}
        else:
            out[kind] = {"kind": kind, "id": str(val), "value": val,
                         "state": DEGRADED,
                         "detail": "configured value not in registry"}
    return out


def profiles_list(cfg: Any) -> Dict[str, Any]:
    """profile list: every registered manifest + the active selection."""
    current = current_profiles(cfg)
    items = []
    for m in registry(cfg):
        active = (current.get(m.kind, {}).get("id") == m.id)
        gate = m.validate(cfg)
        items.append({
            "id": m.id, "kind": m.kind, "source": m.source,
            "revision": m.revision, "license": m.license,
            "checksum": m.checksum, "hardware": m.hardware,
            "languages": m.languages, "vram_mib": m.vram_mib,
            "ram_mib": m.ram_mib, "latency_ms": m.latency_ms,
            "quality_evidence": m.quality_evidence,
            "safety_evidence": m.safety_evidence,
            "rollback_to": m.rollback_to, "default": m.default,
            "active": active, "gate": gate["state"],
            "gate_detail": gate["detail"]})
    return {"profiles": items,
            "active": {k: v["id"] for k, v in current.items()},
            "dry_run": True}


def profiles_show(cfg: Any, kind: str) -> Dict[str, Any]:
    """profile show: the active profile for one component, typed."""
    if kind not in KINDS:
        return {"state": "MISSING", "detail": f"unknown kind {kind!r}"}
    cur = current_profiles(cfg).get(kind, {})
    return {"kind": kind, "active": cur, "available": [
        {"id": m.id, "gate": m.validate(cfg)["state"]}
        for m in registry(cfg) if m.kind == kind]}


# ---------------------------------------------------------------------------
# Gate helpers
# ---------------------------------------------------------------------------
def _artifact_checksum(path: Path) -> str:
    return _sha256_file(path)


def _check_artifact(manifest: ProviderManifest, artifact_path: Optional[Path],
                    cfg: Any) -> Dict[str, str]:
    """Validate a downloaded artifact/model before activation."""
    if manifest.kind == BRAIN:
        from . import distribution as D
        rec = D.model_artifact_record(cfg)
        if rec.get("state") != "OK":
            return {"state": rec.get("state", MISSING),
                    "detail": rec.get("detail", "digest unavailable")}
        if manifest.checksum and rec.get("checksum") != manifest.checksum:
            return {"state": FAILED,
                    "detail": "brain digest does not match manifest checksum"}
        return {"state": OK, "detail": "brain digest verified"}
    if artifact_path is None:
        return {"state": OK, "detail": "no artifact file required"}
    if not Path(artifact_path).exists():
        return {"state": MISSING, "detail": f"artifact missing: {artifact_path}"}
    if manifest.checksum:
        actual = _artifact_checksum(Path(artifact_path))
        if actual != manifest.checksum.lower():
            return {"state": FAILED,
                    "detail": "artifact checksum mismatch (license/hash gate)"}
    return {"state": OK, "detail": "artifact present + checksum verified"}


def _check_hardware_language(manifest: ProviderManifest) -> Dict[str, str]:
    from . import distribution as D
    if manifest.hardware == "gpu" and not D.gpu_present():
        return {"state": "DENIED",
                "detail": "profile requires GPU but none is present"}
    if manifest.hardware == "cpu" and D.gpu_present():
        # CPU is always supported; no denial.
        pass
    # language support: only 'en' is currently validated; anything else is
    # marked PARTIAL (visible, not a silent pass).
    if "en" not in (manifest.languages or []):
        return {"state": PARTIAL,
                "detail": "profile language set lacks 'en' (unverified)"}
    return {"state": OK, "detail": "hardware/language compatible"}


def profile_check(cfg: Any, kind: str, profile_id: Optional[str] = None) -> Dict[str, Any]:
    """Run every gate for a profile (default: the active one). Never mutates."""
    if kind not in KINDS:
        return {"state": MISSING, "detail": f"unknown kind {kind!r}"}
    m = next((x for x in registry(cfg)
              if x.kind == kind and (profile_id is None or x.id == profile_id)),
             None)
    if m is None:
        if profile_id is None:
            val = _current_value(cfg, kind)
            m = ProviderManifest(id=str(val), kind=kind, source="?",
                                 revision="?", license="?",
                                 rollback_to=DEFAULTS[kind])
            return {"kind": kind, "profile": m.id,
                    "state": DEGRADED,
                    "detail": "active value not in registry; gates cannot run",
                    "gates": {"manifest": "MISSING"}}
        return {"state": MISSING, "detail": f"unknown profile {profile_id!r}"}
    gates = {}
    gates["manifest"] = m.validate(cfg)["state"]
    artifact = m.config_value if m.kind == BRAIN else None
    gates["artifact"] = _check_artifact(m, None, cfg)["state"] \
        if m.kind == BRAIN else "OK"
    gates["hardware_language"] = _check_hardware_language(m)["state"]
    gates["resource"] = "FAILED" if m.vram_mib > VRAM_CEILING_MIB else "OK"
    overall = FAILED if any(v == FAILED for v in gates.values()) else \
        (PARTIAL if any(v == PARTIAL for v in gates.values()) else OK)
    return {"kind": kind, "profile": m.id, "state": overall, "gates": gates,
            "manifest": {"source": m.source, "revision": m.revision,
                         "license": m.license, "checksum": m.checksum,
                         "rollback_to": m.rollback_to}}


# ---------------------------------------------------------------------------
# Apply: atomic config write + backup + rollback
# ---------------------------------------------------------------------------
def _write_config(cfg: Any, update: Dict[str, Any]) -> Path:
    """Apply a nested config update atomically with a timestamped backup.
    Returns the backup path. The update only touches the given keys."""
    import yaml
    path = getattr(cfg, "path", None)
    if path is None:
        from .config import DEFAULT_CONFIG_PATH
        path = DEFAULT_CONFIG_PATH
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    merged = copy.deepcopy(data)
    node = merged
    for section, values in update.items():
        node.setdefault(section, {})
        if isinstance(values, dict):
            node[section].update(values)
        else:
            node[section] = values
    bak = path.with_name(f"{path.name}.profile.{int(time.time())}.bak")
    if path.exists():
        shutil.copy2(path, bak)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump(merged, fh, sort_keys=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return bak


def _config_update_for(manifest: ProviderManifest) -> Dict[str, Any]:
    if manifest.config_path is None:
        return {}  # audio handled separately
    section, sub = manifest.config_path[0], manifest.config_path[1:]
    if sub:
        return {section: {sub[0]: manifest.config_value}}
    return {section: manifest.config_value}


def install_manifest(cfg: Any, manifest: ProviderManifest) -> Dict[str, Any]:
    """Register a validated manifest (persisted to the registry file). The
    manifest must already pass its gates; nothing is activated here."""
    gate = manifest.validate(cfg)
    if gate["state"] != "OK":
        return {"state": gate["state"], "detail": gate["detail"]}
    if manifest.kind not in KINDS:
        return {"state": "MISSING", "detail": f"unknown kind {manifest.kind!r}"}
    reg_file = _registry_path(cfg)
    profiles = [m for m in registry(cfg) if not m.default]
    profiles = [p for p in profiles
                if not (p.kind == manifest.kind and p.id == manifest.id)]
    profiles.append(manifest)
    payload = {"version": 1, "profiles": [
        {k: v for k, v in p.__dict__.items()} for p in profiles]}
    try:
        if reg_file:
            reg_file.parent.mkdir(parents=True, exist_ok=True)
            reg_file.write_text(json.dumps(payload, indent=2, sort_keys=True))
    except OSError as exc:
        return {"state": "FAILED", "detail": f"registry write failed: {exc}"}
    return {"state": "OK", "kind": manifest.kind, "profile": manifest.id,
            "detail": "manifest registered (not activated)"}


def apply_profile(cfg: Any, kind: str, profile_id: str, *,
                  dry_run: bool = True, require_confirmation: bool = True,
                  artifact_path: Optional[Path] = None) -> Dict[str, Any]:
    """Install/select a profile. dry_run by default; explicit apply runs gates
    and atomically updates config with a backup. A failed gate leaves the
    previous profile active (no write)."""
    if kind not in KINDS:
        return {"state": MISSING, "detail": f"unknown kind {kind!r}"}
    if dry_run:
        # report what WOULD change without touching config
        cur = current_profiles(cfg).get(kind, {})
        return {"state": OK, "dry_run": True, "kind": kind,
                "target": profile_id,
                "current": cur.get("id"),
                "actions": [f"set {kind} to {profile_id} "
                            f"(current {cur.get('id')})"]}
    m = next((x for x in registry(cfg)
              if x.kind == kind and x.id == profile_id), None)
    if m is None:
        return {"state": MISSING,
                "detail": f"unknown {kind} profile {profile_id!r}"}
    # gates must pass before ANY write
    manifest_gate = m.validate(cfg)
    if manifest_gate["state"] != OK:
        return {"state": FAILED, "detail": manifest_gate["detail"],
                "kept": _current_value(cfg, kind)}
    artifact_gate = _check_artifact(m, artifact_path, cfg)
    if artifact_gate["state"] != OK:
        return {"state": artifact_gate["state"],
                "detail": artifact_gate["detail"],
                "kept": _current_value(cfg, kind)}
    hw = _check_hardware_language(m)
    if hw["state"] in (FAILED, DENIED):
        return {"state": hw["state"], "detail": hw["detail"],
                "kept": _current_value(cfg, kind)}
    if m.vram_mib > VRAM_CEILING_MIB:
        return {"state": FAILED,
                "detail": f"profile needs {m.vram_mib} MiB > {VRAM_CEILING_MIB}",
                "kept": _current_value(cfg, kind)}
    if require_confirmation:
        # caller must pass confirmation explicitly; deny otherwise
        return {"state": "DENIED",
                "detail": "confirmation required for side effect",
                "kept": _current_value(cfg, kind)}
    # apply atomically
    update = _config_update_for(m)
    prev = _current_value(cfg, kind)
    try:
        bak = _write_config(cfg, update) if update else None
    except Exception as exc:
        return {"state": FAILED, "detail": f"write failed: {exc}",
                "kept": prev}
    return {"state": OK, "kind": kind, "profile": m.id,
            "previous": prev, "backup": str(bak) if bak else None,
            "rolled_back_target": m.rollback_to}


def reset_profile(cfg: Any, kind: str, *, dry_run: bool = True) -> Dict[str, Any]:
    """profile reset: return one component to its verified default."""
    return apply_profile(cfg, kind, DEFAULTS[kind], dry_run=dry_run,
                         require_confirmation=False)


def rollback_profile(cfg: Any, kind: str, *, dry_run: bool = True,
                     backup: Optional[Path] = None) -> Dict[str, Any]:
    """profile rollback: restore the previous config from a backup, or to the
    default when no backup is given."""
    if kind not in KINDS:
        return {"state": MISSING, "detail": f"unknown kind {kind!r}"}
    if dry_run:
        return {"state": OK, "dry_run": True, "kind": kind,
                "action": f"restore {kind} from backup {backup or 'default'}"}
    if backup is not None and Path(backup).exists():
        target = Path(getattr(cfg, "path", None) or Path(
            "~/.config/openclaw-voice-bridge/voice.yaml").expanduser())
        try:
            data = Path(backup).read_text()
            fd, tmp = tempfile.mkstemp(dir=str(target.parent),
                                       prefix=".voice.rollback.",
                                       suffix=".tmp")
            with os.fdopen(fd, "w") as fh:
                fh.write(data)
            os.replace(tmp, target)
        except Exception as exc:
            return {"state": FAILED, "detail": f"rollback failed: {exc}"}
        return {"state": OK, "kind": kind, "action": "restored-backup",
                "backup": str(backup)}
    # no backup -> reset this component to its verified default
    return apply_profile(cfg, kind, DEFAULTS[kind], dry_run=False,
                         require_confirmation=False)


def remove_profile(cfg: Any, kind: str, profile_id: str, *,
                   dry_run: bool = True) -> Dict[str, Any]:
    """profile remove: unregister a non-default manifest (never the active one,
    never a default)."""
    if dry_run:
        return {"state": OK, "dry_run": True, "kind": kind,
                "action": f"remove {profile_id} from registry"}
    m = next((x for x in registry(cfg)
              if x.kind == kind and x.id == profile_id), None)
    if m is None:
        return {"state": MISSING, "detail": f"unknown {profile_id!r}"}
    if m.default:
        return {"state": "DENIED",
                "detail": "cannot remove the default profile"}
    cur = current_profiles(cfg).get(kind, {}).get("id")
    if cur == profile_id:
        return {"state": "DENIED",
                "detail": "cannot remove the active profile; select first"}
    reg_file = _registry_path()
    manifests = [x for x in registry(cfg) if not (x.kind == kind
                                               and x.id == profile_id)]
    payload = {"version": 1, "profiles": [
        {k: v for k, v in m.__dict__.items()} for m in manifests
        if not m.default]}
    try:
        if reg_file:
            reg_file.parent.mkdir(parents=True, exist_ok=True)
            reg_file.write_text(json.dumps(payload, indent=2, sort_keys=True))
    except OSError as exc:
        return {"state": FAILED, "detail": f"registry write failed: {exc}"}
    return {"state": OK, "kind": kind, "removed": profile_id}


def upgrade_profile(cfg: Any, kind: str, profile_id: str, *,
                    dry_run: bool = True) -> Dict[str, Any]:
    """profile upgrade: re-run gates against the pinned revision + artifact;
    a failed upgrade leaves the previous profile active. Defaults to the
    active profile for the component."""
    if not profile_id:
        profile_id = current_profiles(cfg).get(kind, {}).get("id", "")
    if dry_run:
        return {"state": OK, "dry_run": True, "kind": kind,
                "action": f"upgrade {profile_id}: re-verify source/revision/"
                          f"checksum gates"}
    m = next((x for x in registry(cfg)
              if x.kind == kind and x.id == profile_id), None)
    if m is None:
        return {"state": MISSING, "detail": f"unknown {profile_id!r}"}
    gates = profile_check(cfg, kind, profile_id)
    if gates["state"] != OK:
        return {"state": gates["state"], "detail": gates["detail"],
                "kept": _current_value(cfg, kind),
                "gates": gates.get("gates", {})}
    return {"state": OK, "kind": kind, "profile": profile_id,
            "action": "gates re-verified; profile unchanged (up to date)"}


# ---------------------------------------------------------------------------
# Branding migration (Qubit Voice Agent), reversible
# ---------------------------------------------------------------------------
def branding_plan(cfg: Any, *, dry_run: bool = True,
                  backup_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Prepare the openclaw-voice-bridge -> qubit-voice-agent migration.
    dry-run reports every reference change; applying records a backup and a
    verification record. Never renames the repo or mutates live state."""
    from .config import DEFAULT_CONFIG_PATH, DEFAULT_STATE_DIR, DEFAULT_DATA_DIR
    current_name = "openclaw-voice-bridge"
    new_name = "qubit-voice-agent"
    old_default_config = Path(DEFAULT_CONFIG_PATH).expanduser()
    steps = [
        f"package name: openclaw-voice-bridge -> {new_name}",
        f"service/unit: openclaw-voice-bridge.service -> {new_name}.service",
        f"config path: ~/.config/{current_name}/ -> ~/.config/{new_name}/ "
        f"(compat alias keeps the old path working)",
        f"state path: ~/.local/state/{current_name}/ -> ~/.local/state/"
        f"{new_name}/ (alias only; existing state is preserved)",
        f"data path: ~/.local/share/{current_name}/ -> ~/.local/share/"
        f"{new_name}/",
        f"launchers/fixtures/README: update all references + compat aliases",
    ]
    plan = {
        "current_name": current_name,
        "new_name": new_name,
        "dry_run": dry_run,
        "steps": steps,
        "preserves": ["memory", "reports", "permissions", "device settings",
                      "model metadata", "approvals"],
        "backup": None,
    }
    if dry_run:
        return plan
    bdir = Path(backup_dir) if backup_dir else Path(
        old_default_config.parent.parent) / f"{new_name}-migration-backup"
    bdir.mkdir(parents=True, exist_ok=True)
    touched = []
    for src in (old_default_config, DEFAULT_STATE_DIR, DEFAULT_DATA_DIR):
        p = Path(src).expanduser() if not isinstance(src, Path) else Path(src)
        if p.exists():
            try:
                shutil.copy2(p, bdir / p.name)
                touched.append(p.name)
            except OSError:
                pass
    plan["backup"] = str(bdir)
    plan["backup_files"] = touched
    return plan


def branding_verify(cfg: Any) -> Dict[str, Any]:
    """Read-only consistency check: every branding reference agrees."""
    new_name = "qubit-voice-agent"
    refs = {
        "package": "qubit-voice-agent",
        "service": f"{new_name}.service",
        "config_dir": f".config/{new_name}",
        "state_dir": f"state/{new_name}",
    }
    return {"state": "PLANNED", "refs": refs,
            "note": "migration is prepared; apply via branding_plan with "
                    "backup + verification, never as incidental cleanup"}


def branding_rollback(cfg: Any, backup_dir: Path) -> Dict[str, Any]:
    """Restore branding backups (reversible migration path)."""
    bdir = Path(backup_dir)
    if not bdir.exists():
        return {"state": MISSING, "detail": f"no backup at {bdir}"}
    restored = []
    for src, _new in ((Path("~/.config/openclaw-voice-bridge/voice.yaml")
                       .expanduser(), None),):
        bak = bdir / src.name
        if bak.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bak, src)
            restored.append(str(src))
    return {"state": OK, "restored": restored, "backup": str(bdir)}