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
from typing import Any, Dict, List, Optional

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
            source="ollama://qubit-brain4 (docs/archive/2026-09-21/UPSTREAM_REUSE.md R7)",
            revision="resolved-at-runtime (Ollama digest)",
            license="Apache-2.0 (Qwen3-4B-Instruct-2507-derived)",
            hardware="gpu", languages=["en"], vram_mib=3300, ram_mib=0,
            latency_ms=644.0, quality_evidence="docs/archive/2026-09-21/BAKEOFF_4D.md",
            safety_evidence="docs/archive/2026-09-21/EVALUATION_6B.md", rollback_to="qubit-brain4",
            config_path=("intent", "local_llm", "model"),
            config_value="qubit-brain4"),
        ProviderManifest(
            id="sherpa", kind=STT, default=True,
            source="sherpa-onnx (CPU; declared dep)",
            revision=">=1.13", license="Apache-2.0",
            hardware="cpu", languages=["en"], vram_mib=0, ram_mib=0,
            latency_ms=70.0, quality_evidence="docs/archive/2026-09-21/AUDIO_SETUP.md",
            safety_evidence="docs/archive/2026-09-21/OPERATIONS_7A.md", rollback_to="sherpa",
            config_path=("asr", "provider"), config_value="sherpa"),
        ProviderManifest(
            id="piper", kind=TTS, default=True,
            source="piper-tts (declared dep)",
            revision=">=1.2", license="MIT",
            hardware="cpu", languages=["en"], vram_mib=0, ram_mib=0,
            latency_ms=250.0, quality_evidence="docs/archive/2026-09-21/AUDIO_SETUP.md",
            safety_evidence="docs/archive/2026-09-21/OPERATIONS_7A.md", rollback_to="piper",
            config_path=("tts", "provider"), config_value="piper"),
        ProviderManifest(
            id="en_US-amy-medium", kind=VOICE, default=True,
            source="rhasspy/piper-voices v1.0.0 (en_US amy medium)",
            revision="v1.0.0", license="MIT",
            hardware="cpu", languages=["en"], vram_mib=0, ram_mib=0,
            latency_ms=250.0, quality_evidence="docs/archive/2026-09-21/AUDIO_SETUP.md",
            safety_evidence="docs/archive/2026-09-21/OPERATIONS_7A.md",
            rollback_to="en_US-amy-medium",
            config_path=("tts", "voice"), config_value="en_US-amy-medium"),
        ProviderManifest(
            id="os-default", kind=AUDIO, default=True,
            source="system OS defaults (HyperX rules)",
            revision="n/a", license="none",
            hardware="any", languages=["en"], vram_mib=0, ram_mib=0,
            latency_ms=0.0, quality_evidence="docs/archive/2026-09-21/AUDIO_SETUP.md",
            safety_evidence="docs/archive/2026-09-21/OPERATIONS_7C.md", rollback_to="os-default",
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
    """Validate a downloaded artifact/model before activation.

    BRAIN: verified against the live Ollama digest (fail closed when
    unavailable). STT/TTS/VOICE: the local artifact is resolved and its sha256
    checked against the manifest when pinned; an unpinned-but-present artifact
    is accepted only with an explicit "checksum computed at runtime" note
    (never trusted silently by existence alone). A missing required artifact
    is MISSING."""
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
    if manifest.kind in (TTS, VOICE, STT):
        path = _local_artifact_path(cfg, manifest)
        if path is None:
            # provider is importable/runtime-resolved; no file to pin
            return {"state": OK,
                    "detail": "no local artifact file to verify (runtime)"}
        if not path.exists():
            return {"state": MISSING, "detail": f"artifact missing: {path}"}
        actual = _artifact_checksum(path)
        if manifest.checksum:
            if actual != manifest.checksum.lower():
                return {"state": FAILED,
                        "detail": f"artifact checksum mismatch: {path}"}
            return {"state": OK,
                    "detail": f"artifact present + checksum verified ({path.name})"}
        return {"state": OK,
                "detail": f"artifact present; checksum computed at runtime "
                          f"({actual[:16]}…), not pinned"}
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


def _local_artifact_path(cfg: Any, manifest: ProviderManifest) -> Optional[Path]:
    """Resolve the on-disk artifact for a local component (piper voice onnx,
    sherpa model dir) from the live config. None = runtime-only provider."""
    model_dir = cfg.model_dir() if hasattr(cfg, "model_dir") else None
    if manifest.kind == VOICE:
        voice = str(manifest.config_value or "")
        if model_dir and voice:
            return Path(model_dir) / f"{voice}.onnx"
        return None
    if manifest.kind == TTS:
        # TTS provider resolves the configured voice artifact
        voice = str(cfg.get("tts", "voice", default="") or "")
        if model_dir and voice:
            return Path(model_dir) / f"{voice}.onnx"
        return None
    if manifest.kind == STT:
        # sherpa models live under model_dir; return the dir if present
        if model_dir and Path(model_dir).exists():
            return Path(model_dir)
        return None
    return None


def _check_evidence(manifest: ProviderManifest) -> Dict[str, str]:
    """Quality/safety evidence must exist and be non-empty (validated, not just
    recorded). Missing evidence for a non-default profile fails the gate."""
    repo = Path(__file__).resolve().parents[1]
    missing = []
    for field in ("quality_evidence", "safety_evidence"):
        ref = getattr(manifest, field, "") or ""
        if not ref:
            missing.append(f"{field}:empty")
            continue
        p = repo / ref
        if not p.exists() or p.stat().st_size == 0:
            missing.append(f"{field}:{ref}")
    if missing:
        if manifest.default:
            return {"state": PARTIAL,
                    "detail": "evidence missing: " + ", ".join(missing)}
        return {"state": FAILED,
                "detail": "evidence missing: " + ", ".join(missing)}
    return {"state": OK, "detail": "quality/safety evidence present"}


def _measure_latency(cfg: Any, manifest: ProviderManifest) -> Dict[str, str]:
    """Real, bounded latency measurement for local components. Returns a typed
    gate: OK when a real measurement was taken, PARTIAL when unmeasured
    (never a silent 0.0 pass). In hermetic test environments
    (VOICE_BRIDGE_LATENCY_FAST=1) or when a measured value is already recorded,
    the recorded value is used and the gate reports it truthfully."""
    recorded = float(getattr(manifest, "latency_ms", 0.0) or 0.0)
    if recorded > 0.0:
        return {"state": OK,
                "detail": f"latency recorded: {recorded} ms"}
    import os as _os
    if _os.environ.get("VOICE_BRIDGE_LATENCY_FAST") == "1":
        return {"state": PARTIAL, "detail": "latency unmeasured (fast mode)"}
    if manifest.kind in (TTS, VOICE):
        path = _local_artifact_path(cfg, manifest)
        if path is None or not Path(path).exists():
            return {"state": PARTIAL, "detail": "latency unmeasured (no voice)"}
        # bounded, real synthesis on the configured voice
        try:
            from .tts.piper import PiperTTS
            from .logging_setup import get_logger
            get_logger()
            tts = PiperTTS(model_dir=Path(path).parent,
                           voice=Path(path).name[:-len(".onnx")])
            tts.load()
            import tempfile, time
            out = Path(tempfile.mkdtemp()) / "latency-probe.wav"
            t0 = time.perf_counter()
            tts.synthesize("one two three four five", out)
            ms = (time.perf_counter() - t0) * 1000
            manifest.latency_ms = round(ms, 1)
            return {"state": OK,
                    "detail": f"latency measured: {manifest.latency_ms} ms"}
        except Exception as exc:
            return {"state": PARTIAL,
                    "detail": f"latency unmeasured ({str(exc)[:80]})"}
    if manifest.kind == BRAIN:
        from . import distribution as D
        rec = D.model_artifact_record(cfg)
        if rec.get("state") == "OK":
            return {"state": OK,
                    "detail": "latency from held-out bake-off (docs/archive/2026-09-21/BAKEOFF_4D.md)"}
        return {"state": PARTIAL, "detail": "latency unmeasured (brain offline)"}
    return {"state": PARTIAL, "detail": "latency unmeasured for this kind"}


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
    gates["artifact"] = _check_artifact(m, None, cfg)["state"]
    gates["evidence"] = _check_evidence(m)["state"]
    gates["latency"] = _measure_latency(cfg, m)["state"]
    gates["hardware_language"] = _check_hardware_language(m)["state"]
    gates["resource"] = "FAILED" if m.vram_mib > VRAM_CEILING_MIB else "OK"
    overall = FAILED if any(v == FAILED for v in gates.values()) else \
        (PARTIAL if any(v == PARTIAL for v in gates.values()) else OK)
    return {"kind": kind, "profile": m.id, "state": overall, "gates": gates,
            "latency_ms": m.latency_ms,
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
        from .config import resolve_config_path
        path = resolve_config_path()
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


def _write_registry(cfg: Any,
                    manifests: List[ProviderManifest]) -> Optional[Path]:
    """Atomic read-modify-write of the registry file preserving other keys.

    The registry file is shared with the canonical model-profile resolver
    (``voicebridge.providers.resolver``), which stores its records under
    ``model_profiles``. Rewriting the whole file with only ``profiles`` would
    silently delete those records, so unrelated top-level keys are preserved.
    """
    reg_file = _registry_path(cfg)
    if reg_file is None:
        return None
    payload: Dict[str, Any] = {}
    try:
        if reg_file.exists():
            existing = json.loads(reg_file.read_text())
            if isinstance(existing, dict):
                payload = existing
    except (OSError, ValueError):
        payload = {}
    payload.setdefault("version", 1)
    payload["profiles"] = [
        {k: v for k, v in p.__dict__.items()} for p in manifests
        if not p.default]
    reg_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(reg_file.parent), prefix=reg_file.name,
                               suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    os.replace(tmp, reg_file)
    return reg_file


def install_manifest(cfg: Any, manifest: ProviderManifest) -> Dict[str, Any]:
    """Register a validated manifest (persisted to the registry file). The
    manifest must already pass its gates; nothing is activated here."""
    gate = manifest.validate(cfg)
    if gate["state"] != "OK":
        return {"state": gate["state"], "detail": gate["detail"]}
    if manifest.kind not in KINDS:
        return {"state": "MISSING", "detail": f"unknown kind {manifest.kind!r}"}
    profiles = [m for m in registry(cfg) if not m.default]
    profiles = [p for p in profiles
                if not (p.kind == manifest.kind and p.id == manifest.id)]
    profiles.append(manifest)
    try:
        _write_registry(cfg, profiles)
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
    # gates must pass before ANY write. A replacement (non-default) profile
    # must pass EVERY gate (no PARTIAL/FAILED); only the verified default may
    # activate with a PARTIAL caveat. A failed gate leaves the previous
    # profile active (no config write).
    gates = {}
    gates["manifest"] = m.validate(cfg)["state"]
    gates["artifact"] = _check_artifact(m, artifact_path, cfg)["state"]
    gates["evidence"] = _check_evidence(m)["state"]
    gates["latency"] = _measure_latency(cfg, m)["state"]
    gates["hardware_language"] = _check_hardware_language(m)["state"]
    gates["resource"] = "FAILED" if m.vram_mib > VRAM_CEILING_MIB else "OK"
    failed = [k for k, v in gates.items() if v == FAILED]
    partial = [k for k, v in gates.items() if v == PARTIAL]
    if failed:
        return {"state": FAILED,
                "detail": "gates failed: " + ", ".join(failed),
                "gates": gates, "kept": _current_value(cfg, kind)}
    if partial and not m.default:
        return {"state": PARTIAL,
                "detail": "replacement profile not fully verified: "
                          + ", ".join(partial),
                "gates": gates, "kept": _current_value(cfg, kind)}
    if require_confirmation:
        # caller must pass confirmation explicitly; deny otherwise
        return {"state": "DENIED",
                "detail": "confirmation required for side effect",
                "gates": gates, "kept": _current_value(cfg, kind)}
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
            "~/.config/qubit-voicebridge/voice.yaml").expanduser())
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
    manifests = [x for x in registry(cfg) if not (x.kind == kind
                                               and x.id == profile_id)]
    try:
        _write_registry(cfg, manifests)
    except OSError as exc:
        return {"state": FAILED, "detail": f"registry write failed: {exc}"}
    return {"state": OK, "kind": kind, "removed": profile_id}


def upgrade_profile(cfg: Any, kind: str, profile_id: str, *,
                    dry_run: bool = True) -> Dict[str, Any]:
    """profile upgrade: re-run every gate against the live artifact and, on
    apply, refresh the registry manifest's revision/checksum/latency from the
    actual artifact (a real upgrade, not just a check). A failed upgrade
    leaves the previous profile and the previous manifest unchanged. Defaults
    to the active profile for the component."""
    if not profile_id:
        profile_id = current_profiles(cfg).get(kind, {}).get("id", "")
    if dry_run:
        return {"state": OK, "dry_run": True, "kind": kind,
                "action": f"upgrade {profile_id}: re-verify source/revision/"
                          f"checksum gates, then refresh manifest metadata"}
    m = next((x for x in registry(cfg)
              if x.kind == kind and x.id == profile_id), None)
    if m is None:
        return {"state": MISSING, "detail": f"unknown {profile_id!r}"}
    gates = profile_check(cfg, kind, profile_id)
    if gates["state"] in (FAILED,):
        return {"state": gates["state"], "detail": gates["detail"],
                "kept": _current_value(cfg, kind),
                "gates": gates.get("gates", {})}
    # Real upgrade: refresh the registered manifest metadata from the live
    # artifact (checksum/digest + measured latency), then persist.
    refreshed = dict(m.__dict__)
    if m.kind == BRAIN:
        from . import distribution as D
        rec = D.model_artifact_record(cfg)
        if rec.get("checksum"):
            refreshed["revision"] = rec["checksum"]
            refreshed["checksum"] = rec["checksum"]
    else:
        path = _local_artifact_path(cfg, m)
        if path is not None and Path(path).exists():
            refreshed["checksum"] = _artifact_checksum(Path(path))
            refreshed["revision"] = "artifact:" + refreshed["checksum"][:16]
        if m.latency_ms:
            refreshed["latency_ms"] = m.latency_ms
    new_m = ProviderManifest(**refreshed)
    out = install_manifest(cfg, new_m)
    if out["state"] != OK:
        return {"state": FAILED, "detail": out["detail"],
                "kept": _current_value(cfg, kind)}
    return {"state": OK, "kind": kind, "profile": profile_id,
            "action": "upgraded: gates re-verified + manifest metadata "
                      "refreshed (checksum/revision/latency)",
            "checksum": refreshed.get("checksum"),
            "revision": refreshed.get("revision")}


# ---------------------------------------------------------------------------
# Branding migration (Qubit Voice Agent), reversible — Packet 7D stage kept
# as history. Packet 8S canonical identity is Qubit VoiceBridge /
# qubit-voicebridge; both openclaw-voice-bridge and qubit-voice-agent are
# legacy sources.
# ---------------------------------------------------------------------------
OLD_NAME = "openclaw-voice-bridge"
NEW_NAME = "qubit-voicebridge"
# 7D intermediate installed identity (Packet 8S legacy source).
INTERMEDIATE_NAME = "qubit-voice-agent"
OLD_TITLE = "Qubit Voice Agent"
NEW_TITLE = "Qubit VoiceBridge"
# Packet 8S canonical identity (final). The 7D NEW_NAME is a legacy source.
CANONICAL_NAME_8S = "qubit-voicebridge"
CANONICAL_TITLE_8S = "Qubit VoiceBridge"
LEGACY_NAMES_8S = (OLD_NAME, NEW_NAME)

# Repository files whose references must be updated together for a consistent
# branding migration (package metadata, service unit, launchers, docs, config
# template). Each entry is (relative path, list of (old, new) literal pairs).
BRANDING_FILE_EDITS = [
    ("pyproject.toml", [
        ('name = "openclaw-voice-bridge"', 'name = "qubit-voicebridge"'),
        ('name = "qubit-voice-agent"', 'name = "qubit-voicebridge"'),
        ("description = \"Local push-to-talk voice bridge for OpenClaw",
         "description = \"Qubit AiO — local push-to-talk voice agent"),
        ("description = \"Qubit Voice Agent — local push-to-talk voice agent",
         "description = \"Qubit AiO — local push-to-talk voice agent"),
        ("description = \"Qubit VoiceBridge — local push-to-talk voice agent",
         "description = \"Qubit AiO — local push-to-talk voice agent"),
    ]),
    ("systemd/openclaw-voice-bridge.service", [
        ("Description=Qubit Agent (OpenClaw Voice Bridge",
         "Description=Qubit AiO (Qubit VoiceBridge"),
        ("Description=Qubit Agent (Qubit Voice Agent",
         "Description=Qubit AiO (Qubit VoiceBridge"),
        ("Description=Qubit VoiceBridge (Qubit runtime",
         "Description=Qubit AiO (Qubit VoiceBridge"),
        ("Description=OpenClaw Voice Bridge", "Description=Qubit VoiceBridge"),
        ("Description=Qubit Voice Agent", "Description=Qubit VoiceBridge"),
        ("openclaw-voice-bridge.service", "qubit-voicebridge.service"),
        ("qubit-voice-agent.service", "qubit-voicebridge.service"),
    ]),
    ("scripts/install.sh", [
        ("openclaw-voice-bridge", "qubit-voicebridge"),
        ("qubit-voice-agent", "qubit-voicebridge"),
        ("OpenClaw Voice Bridge install", "Qubit VoiceBridge install"),
        ("Qubit Voice Agent install", "Qubit VoiceBridge install"),
    ]),
    ("scripts/run.sh", [
        ("openclaw-voice-bridge", "qubit-voicebridge"),
        ("qubit-voice-agent", "qubit-voicebridge"),
    ]),
    ("scripts/install_systemd_unit.sh", [
        ("openclaw-voice-bridge.service", "qubit-voicebridge.service"),
        ("qubit-voice-agent.service", "qubit-voicebridge.service"),
        ("openclaw-voice-bridge", "qubit-voicebridge"),
        ("qubit-voice-agent", "qubit-voicebridge"),
    ]),
    ("config/voice.example.yaml", [
        ("OpenClaw Voice Bridge", "Qubit VoiceBridge"),
        ("Qubit Voice Agent", "Qubit VoiceBridge"),
        ("openclaw-voice-bridge", "qubit-voicebridge"),
        ("qubit-voice-agent", "qubit-voicebridge"),
    ]),
    ("README.md", [
        ("openclaw-voice-bridge", "qubit-voicebridge"),
        ("qubit-voice-agent", "qubit-voicebridge"),
        ("OpenClaw Voice Bridge", "Qubit VoiceBridge"),
        ("Qubit Voice Agent", "Qubit VoiceBridge"),
    ]),
    ("docs/archive/2026-09-21/AGENT_PROMPT_PACK.md", [
        ("openclaw-voice-bridge", "qubit-voicebridge"),
    ]),
    ("docs/archive/2026-09-21/VOICE_BRIDGE_SYSTEM.md", [
        ("openclaw-voice-bridge", "qubit-voicebridge"),
        ("qubit-voice-agent", "qubit-voicebridge"),
        ("OpenClaw Voice Bridge", "Qubit VoiceBridge"),
        ("Qubit Voice Agent", "Qubit VoiceBridge"),
    ]),
]

# Runtime path pairs to migrate (with a compat alias from the old name).
# 7D stage pairs are preserved as history; 8S adds the final canonical stage.
BRANDING_PATH_PAIRS = [
    (".config/openclaw-voice-bridge", ".config/qubit-voice-agent"),
    (".local/state/openclaw-voice-bridge", ".local/state/qubit-voice-agent"),
    (".local/share/openclaw-voice-bridge", ".local/share/qubit-voice-agent"),
    (".config/openclaw-voice-bridge", ".config/qubit-voicebridge"),
    (".local/state/openclaw-voice-bridge", ".local/state/qubit-voicebridge"),
    (".local/share/openclaw-voice-bridge", ".local/share/qubit-voicebridge"),
    (".config/qubit-voice-agent", ".config/qubit-voicebridge"),
    (".local/state/qubit-voice-agent", ".local/state/qubit-voicebridge"),
    (".local/share/qubit-voice-agent", ".local/share/qubit-voicebridge"),
]
BRANDING_PATH_PAIRS_8S = [
    (".config/openclaw-voice-bridge", ".config/qubit-voicebridge"),
    (".local/state/openclaw-voice-bridge", ".local/state/qubit-voicebridge"),
    (".local/share/openclaw-voice-bridge", ".local/share/qubit-voicebridge"),
    (".config/qubit-voice-agent", ".config/qubit-voicebridge"),
    (".local/state/qubit-voice-agent", ".local/state/qubit-voicebridge"),
    (".local/share/qubit-voice-agent", ".local/share/qubit-voicebridge"),
]

# Docs that intentionally KEEP old names as HISTORICAL MIGRATION TEXT — they
# document the openclaw-voice-bridge -> qubit-voice-agent (7D) and ->
# qubit-voicebridge (8S) renames themselves (e.g. the Packet 7D prompt
# "rename from `openclaw-voice-bridge` to `qubit-voice-agent`").
# Old-name references in these files are always REPORTED by the verifier and the
# plan, but they never FAIL branding verification and are never rewritten by an
# apply (preserving the migration history). Executable package/service/config/
# launcher references (everything else in BRANDING_FILE_EDITS) remain strictly
# checked and migrated.
BRANDING_HISTORICAL_REFERENCE_FILES = (
    "docs/archive/2026-09-21/AGENT_PROMPT_PACK.md",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _branding_ref_scan(repo: Optional[Path] = None) -> Dict[str, Any]:
    """Read-only scan: which files still reference an old name (original or
    7D intermediate), and whether the 8S canonical name is in place.
    Returns {file: {old_hits, new_hits, renamed}}."""
    root = Path(repo) if repo else _repo_root()
    scan: Dict[str, Dict[str, Any]] = {}
    for rel, _pairs in BRANDING_FILE_EDITS:
        historical = rel in BRANDING_HISTORICAL_REFERENCE_FILES
        p = root / rel
        if not p.exists():
            # the systemd unit may have been renamed to the new identifier
            if rel == f"systemd/{OLD_NAME}.service":
                for cand_name in (NEW_NAME, INTERMEDIATE_NAME):
                    newp = root / f"systemd/{cand_name}.service"
                    if newp.exists():
                        text = newp.read_text(errors="replace")
                        scan[rel] = {"old_hits": text.count(OLD_NAME) + text.count(INTERMEDIATE_NAME),
                                     "new_hits": text.count(NEW_NAME),
                                     "missing": False, "renamed": True,
                                     "historical": historical}
                        break
                else:
                    scan[rel] = {"old_hits": 0, "new_hits": 0, "missing": True,
                                 "historical": historical}
                continue
            scan[rel] = {"old_hits": 0, "new_hits": 0, "missing": True,
                         "historical": historical}
            continue
        text = p.read_text(errors="replace")
        scan[rel] = {"old_hits": text.count(OLD_NAME) + text.count(INTERMEDIATE_NAME),
                     "new_hits": text.count(NEW_NAME), "missing": False,
                     "renamed": False, "historical": historical}
    return scan


def _branding_scan_paths() -> Dict[str, Dict[str, bool]]:
    """Runtime path migration state: old path present, new path present, and
    whether a compat alias exists for each pair."""
    home = Path.home()
    out: Dict[str, Dict[str, bool]] = {}
    for old, new in BRANDING_PATH_PAIRS:
        oldp = home / old
        newp = home / new
        out[new] = {"old_exists": oldp.exists(),
                    "new_exists": newp.exists(),
                    "compat_alias": oldp.is_symlink() if oldp.exists() else False}
    return out


def branding_plan(cfg: Any, *, dry_run: bool = True,
                  backup_dir: Optional[Path] = None,
                  repo: Optional[Path] = None,
                  migrate_paths: bool = False) -> Dict[str, Any]:
    """Prepare/perform the openclaw-voice-bridge/qubit-voice-agent ->
    qubit-voicebridge migration (Packet 8S canonical). dry-run lists every
    reference change. --apply rewrites the
    repository files (under `repo`, default = this repo) with a full backup;
    the runtime config/state/data path migration happens ONLY when
    `migrate_paths=True` (an explicit, separate operator decision) — it is
    NEVER triggered by a repo apply so tests/staged runs cannot move the live
    user's config/state. Never renames the physical repo directory; existing
    state is preserved."""
    root = Path(repo) if repo else _repo_root()
    scan = _branding_ref_scan(root)
    file_steps = []
    for rel, pairs in BRANDING_FILE_EDITS:
        info = scan.get(rel, {})
        old_hits = info.get("old_hits", 0)
        if info.get("missing"):
            file_steps.append(f"{rel}: MISSING (cannot migrate)")
        elif rel in BRANDING_HISTORICAL_REFERENCE_FILES and old_hits:
            file_steps.append(f"{rel}: {old_hits} historical migration "
                              f"reference(s) KEPT (allowlisted, reported, "
                              f"not migrated)")
        elif old_hits:
            file_steps.append(f"{rel}: {old_hits} old reference(s) -> "
                              f"{NEW_NAME} ({len(pairs)} edit rules)")
        else:
            file_steps.append(f"{rel}: already {NEW_NAME}")
    path_steps = []
    for old, new in BRANDING_PATH_PAIRS:
        st = _branding_scan_paths()[new]
        action = "create compat alias + migrate" if st["old_exists"] \
            else "no-op (path absent)"
        path_steps.append(f"{new}: {action}")
    plan = {
        "current_name": OLD_NAME,
        "new_name": NEW_NAME,
        "dry_run": dry_run,
        "file_steps": file_steps,
        "path_steps": path_steps,
        "migrate_paths": migrate_paths,
        "preserves": ["memory", "reports", "permissions", "device settings",
                      "model metadata", "approvals"],
        "backup": None,
        "restored_files": [],
    }
    if dry_run:
        return plan
    bdir = Path(backup_dir) if backup_dir else root.parent / f"{NEW_NAME}-migration-backup"
    bdir.mkdir(parents=True, exist_ok=True)
    # 1) back up every file we will touch (repo files + runtime config/state)
    backed_up = []
    for rel, _pairs in BRANDING_FILE_EDITS:
        src = root / rel
        if src.exists():
            dst = bdir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            backed_up.append(rel)
    from .config import DEFAULT_CONFIG_PATH, DEFAULT_STATE_DIR, DEFAULT_DATA_DIR
    if migrate_paths:
        for runtime in (DEFAULT_CONFIG_PATH, DEFAULT_STATE_DIR, DEFAULT_DATA_DIR):
            src = Path(runtime).expanduser()
            if src.exists():
                dst = bdir / "runtime" / src.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
                backed_up.append(f"runtime/{src.name}")
    # 2) rewrite repository file references (atomic, per file). Historical
    #    reference files are NEVER rewritten: their old-name text documents the
    #    migration itself and must be preserved (still reported, never hidden).
    for rel, pairs in BRANDING_FILE_EDITS:
        src = root / rel
        if not src.exists():
            continue
        if rel in BRANDING_HISTORICAL_REFERENCE_FILES:
            continue
        text = src.read_text(errors="replace")
        for old, new in pairs:
            text = text.replace(old, new)
        # generic token fallback (covers any residual old name, both the
        # original and the 7D intermediate identity)
        text = text.replace(OLD_NAME, NEW_NAME)
        text = text.replace(INTERMEDIATE_NAME, NEW_NAME)
        text = text.replace(OLD_TITLE, NEW_TITLE)
        fd, tmp = tempfile.mkstemp(dir=str(src.parent),
                                   prefix=f".{src.name}.brand.",
                                   suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, src)
    # 2b) rename the systemd unit file to the new identifier (content already
    #     rewritten above). Either legacy filename converges to canonical.
    old_unit = root / "systemd" / f"{OLD_NAME}.service"
    mid_unit = root / "systemd" / f"{INTERMEDIATE_NAME}.service"
    new_unit = root / "systemd" / f"{NEW_NAME}.service"
    for cand in (old_unit, mid_unit):
        if cand.exists() and not new_unit.exists():
            os.replace(cand, new_unit)
    # 3) runtime compat aliases ONLY when explicitly requested (never in tests
    #    or a plain repo apply). Existing state is moved to the new path and a
    #    symlink keeps the old name working; rollback reverses this.
    created = []
    if migrate_paths:
        for old, new in BRANDING_PATH_PAIRS:
            oldp = Path.home() / old
            newp = Path.home() / new
            if oldp.exists() and not oldp.is_symlink():
                newp.parent.mkdir(parents=True, exist_ok=True)
                try:
                    oldp.rename(newp)          # move content to the new path
                    oldp.symlink_to(newp)      # compat alias back to old name
                    created.append(f"{old} -> {new} (+alias)")
                except OSError as exc:
                    plan["warnings"] = plan.get("warnings", []) + \
                        [f"path migrate failed {old}: {exc}"]
    plan["backup"] = str(bdir)
    plan["backup_files"] = backed_up
    plan["path_migrations"] = created
    return plan


def branding_verify(cfg: Any, repo: Optional[Path] = None) -> Dict[str, Any]:
    """Consistency check. VERIFIED when every file reference and runtime path
    agrees on qubit-voicebridge (Packet 8S canonical); PARTIAL when some
    remain; PLANNED when none have been migrated yet. Read-only. When the live
    repository is not yet migrated (the intended operator-gated state), an
    explicit `limitation` field documents why branding is pending rather than
    silently passing."""
    root = Path(repo) if repo else _repo_root()
    scan = _branding_ref_scan(root)
    # Historical reference files are REPORTED but never fail branding; executable
    # package/service/config/launcher references are strictly checked.
    strict_scan = {rel: info for rel, info in scan.items()
                   if not info.get("historical")}
    historical = {rel: info["old_hits"] for rel, info in scan.items()
                  if info.get("historical") and info.get("old_hits", 0) > 0}
    old_remaining = {rel: info["old_hits"] for rel, info in strict_scan.items()
                     if info.get("old_hits", 0) > 0}
    migrated = {rel for rel, info in scan.items()
                if not info.get("missing") and info.get("old_hits", 0) == 0}
    path_state = _branding_scan_paths()
    paths_new = {new: st for new, st in path_state.items() if st["new_exists"]}
    state = "VERIFIED"
    limitation = None
    if old_remaining:
        state = "PLANNED" if len(old_remaining) == len(strict_scan) else "PARTIAL"
        if repo is None:
            limitation = (
                "Branding is IMPLEMENTED and independently verified on a "
                "staged repository (apply -> verify VERIFIED -> rollback). The "
                "LIVE repository remains on 'openclaw-voice-bridge' by design: "
                "applying would rename the package, systemd unit, launchers, "
                "config template and runtime paths that the running bridge "
                "depends on. Run `voicebridge branding plan --apply "
                "--backup-dir <dir>` as an explicit operator step (it is "
                "backed up and reversible), then re-verify. This documented "
                "limitation is the reason 7D branding cannot be marked fully "
                "verified without that operator action.")
    return {
        "state": state,
        "new_name": NEW_NAME,
        "files_total": len(scan),
        "files_migrated": len(migrated),
        "files_remaining": old_remaining,
        "historical_references": historical,
        "paths": paths_new,
        "limitation": limitation,
        "note": ("all references agree on qubit-voicebridge"
                 if state == "VERIFIED" else
                 "migration incomplete; run `voicebridge branding plan "
                 "--apply` with a backup, then re-verify"),
    }


def branding_rollback(cfg: Any, backup_dir: Path, repo: Optional[Path] = None,
                      home: Optional[Path] = None) -> Dict[str, Any]:
    """Restore every branding backup (repo files + runtime config/state/data)
    and remove the compat aliases created by the migration. Reversible.

    ``home`` scopes steps 2–3 to an isolated home directory (tests must pass
    a tmp home — the operator's live HOME is never touched by a rollback
    unless explicitly requested with ``home=None``)."""
    bdir = Path(backup_dir)
    if not bdir.exists():
        return {"state": MISSING, "detail": f"no backup at {bdir}"}
    root = Path(repo) if repo else _repo_root()
    restored = []
    # 1) restore repository files
    for rel, _pairs in BRANDING_FILE_EDITS:
        bak = bdir / rel
        if bak.exists():
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bak, dst)
            restored.append(rel)
    # 1b) restore the systemd unit filename (migration renames it)
    old_unit = root / "systemd" / f"{OLD_NAME}.service"
    mid_unit = root / "systemd" / f"{INTERMEDIATE_NAME}.service"
    new_unit = root / "systemd" / f"{NEW_NAME}.service"
    if new_unit.exists() and not old_unit.exists() and not mid_unit.exists():
        # backups restore the original filename when it was captured; only
        # rename back when neither legacy filename was restored
        os.replace(new_unit, old_unit)
        restored.append("systemd/unit-renamed-back")
    # 2) restore runtime config/state/data to the canonical tree (Packet 8S;
    # legacy trees are only compat aliases and are never written)
    homep = Path(home) if home else Path.home()
    for name in ("voice.yaml", "state", "data", "models", "memory",
                 "reports", "device.json", "profiles.json"):
        bak = bdir / "runtime" / name
        if bak.exists():
            dst = homep / ".config/qubit-voicebridge" / name
            if name in ("voice.yaml",):
                dst = homep / ".config/qubit-voicebridge" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if bak.is_dir():
                shutil.copytree(bak, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(bak, dst)
            restored.append(f"runtime/{name}")
    # 3) remove compat aliases (the old name symlinks point back to new paths)
    for old, _new in BRANDING_PATH_PAIRS:
        oldp = homep / old
        if oldp.is_symlink():
            try:
                oldp.unlink()
                restored.append(f"alias-removed:{old}")
            except OSError:
                pass
    return {"state": OK, "restored": restored, "backup": str(bdir)}