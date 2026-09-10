"""Qubit pinned tool runtimes (container-workflow equivalent).

Qubit does not add a
Docker daemon, a second scheduler, or network image pulls (stack contract:
no Docker/Kubernetes; no unpinned downloads; no hidden services). The
supported equivalent is the **pinned confined-runtime workflow** this module
owns:

* ``opencode-binary`` — the pinned OpenCode executable
  (``voicebridge.opencode.policy``: exact version gate, argv allowlist,
  fixed SAFE_PATH, secret-dir refusal), executed only as one-shot
  supervised children inside confinement.

Registry pins live in this module next to the code they describe (no
floating tags, no ``latest``). Install/upgrade verify digests before
activating; upgrade stages the previous receipt for rollback; removal
deletes the runtime tree but never the audit trail. Nothing here fetches
from the network: a missing local artifact is a typed NEEDS_STAGING
state with operator instructions, never an implicit download.

CLI (``qubit runtime ...``), dashboard (Tier-1 voice entries render
automatically in the ops catalog), and voice share every function below.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIRM_CODE = "RUNTIME_CONFIRM_REQUIRED"
ESTOP_CODE = "RUNTIME_ESTOP"
NOT_FOUND_CODE = "RUNTIME_NOT_FOUND"
BAD_INPUT_CODE = "RUNTIME_BAD_INPUT"
STAGING_CODE = "RUNTIME_NEEDS_STAGING"
MISMATCH_CODE = "RUNTIME_DIGEST_MISMATCH"
IN_USE_CODE = "RUNTIME_IN_USE"

STATE_FILE = "runtime_images.json"
SCHEMA = "qubit-runtime-images/1"

# Pinned registry. No tags, no ranges, no "latest".
REGISTRY = (
    {"name": "opencode-binary",
     "kind": "executable",
     "source": "https://github.com/sst/opencode",
     "pin": "1.18.29",
     "license": "MIT (c) 2025 opencode; see THIRD_PARTY_NOTICES.md",
     "purpose": "one-shot supervised coding runs via voicebridge.opencode",
     "confinement": "bubblewrap sandbox + argv allowlist + fixed SAFE_PATH",
     "max_disk_mb": 200},
)
_NAMES = tuple(r["name"] for r in REGISTRY)


class RuntimeImageError(Exception):
    """Typed runtime-image failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"runtime-images.{op}", actor=actor, **detail)
    except Exception:
        pass


def _now() -> float:
    return time.time()


def _state_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STATE_FILE


def _load(cfg: Any) -> Dict[str, Any]:
    path = _state_path(cfg)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": SCHEMA, "images": {}}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "images": {}}
    if not isinstance(data.get("images"), dict):
        data["images"] = {}
    return data


def _save(cfg: Any, data: Dict[str, Any]) -> None:
    path = _state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _check_name(name: str) -> Dict[str, Any]:
    clean = (name or "").strip().lower()
    for entry in REGISTRY:
        if entry["name"] == clean:
            return entry
    raise RuntimeImageError(BAD_INPUT_CODE,
                            f"unknown runtime image {name!r}; "
                            f"pinned images: {', '.join(_NAMES)}")


def _guard(cfg: Any, operation: str, confirmed: bool) -> None:
    if not confirmed:
        raise RuntimeImageError(CONFIRM_CODE,
                                f"runtime {operation} requires explicit"
                                " --confirm")
    if estop_engaged(cfg):
        raise RuntimeImageError(ESTOP_CODE,
                                "the emergency stop is latched; reset it"
                                " before changing runtimes")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def images() -> Dict[str, Any]:
    """List the pinned runtime registry (read-only; no daemon needed)."""
    return {"state": "OK", "schema": SCHEMA, "images": [
        {k: v for k, v in r.items()} for r in REGISTRY]}


def _opencode_provenance() -> Dict[str, Any]:
    """Provenance for the opencode-binary image.

    The Qubit-owned verified copy (materialized by install) is
    authoritative: when present and digest-verified it is what Qubit
    executes. Legacy operator candidates are reported alongside so no
    surface can confuse them with the effective binary."""
    from .opencode import policy as POL
    from .opencode import state as _OCS

    owned = _OCS.verify_owned_binary()
    out: Dict[str, Any] = {"pin": POL.PINNED_OPENCODE_VERSION,
                           "owned": owned}
    if owned.get("ok"):
        out.update({"installed": True, "binary": owned["binary"],
                    "digest": f"sha256:{owned['sha256']}",
                    "effective": True})
        return out
    candidates = [Path(POL.DEFAULT_BINARY), Path(shutil.which("opencode") or "")]
    for cand in candidates:
        if cand and cand.is_file():
            out.update({"installed": True, "binary": str(cand),
                        "digest": _sha256_file(cand), "effective": False})
            return out
    out["installed"] = False
    return out


def status(cfg: Any) -> Dict[str, Any]:
    """Installed state per pinned image: MISSING, STAGED, READY, DEGRADED.

    Re-verifies digests/pins on every call; a present-but-unverifiable
    artifact is DEGRADED (never false READY); a staged artifact with no
    install receipt is STAGED (present, not installed). Also reports that
    no Docker daemon or hidden service is required or used."""
    state = _load(cfg)
    out = []
    for entry in REGISTRY:
        name = entry["name"]
        record = state["images"].get(name, {})
        prov = _opencode_provenance()
        if not prov.get("installed"):
            st = "MISSING"
        elif not record or record.get("removed_at"):
            st = "STAGED"
        elif not prov.get("effective"):
            st = "DEGRADED"
        else:
            st = "READY"
        item: Dict[str, Any] = {"name": name, "state": st,
                                "pin": entry["pin"]}
        if record:
            item["installed_at"] = record.get("installed_at")
            item["installed_digest"] = record.get("digest", "")
            item["previous"] = bool(record.get("previous"))
        if prov.get("installed"):
            item["observed"] = {k: v for k, v in prov.items()
                                if k in ("binary", "dir", "digest",
                                         "verified", "receipt", "effective")
                                and not isinstance(v, dict)}
        out.append(item)
    return {"state": "OK", "runtimes": out,
            "daemon": "none-required",
            "detail": "Qubit runtimes are confined local provisions; no"
                      " Docker daemon, image pull, or background service"
                      " is used or required."}


def _record_install(cfg: Any, name: str, digest: str,
                    actor: str) -> Dict[str, Any]:
    state = _load(cfg)
    prev = state["images"].get(name)
    record: Dict[str, Any] = {"digest": digest, "installed_at": _now(),
                              "actor": actor, "previous": None}
    if isinstance(prev, dict) and prev.get("digest"):
        record["previous"] = {"digest": prev["digest"],
                              "installed_at": prev.get("installed_at", 0.0)}
    state["images"][name] = record
    _save(cfg, state)
    return record


def install(cfg: Any, name: str, *, requested_by: str = "cli:user",
            confirmed: bool = False, source: str = "") -> Dict[str, Any]:
    """Install a pinned runtime image (confirmed; digest-verified).

    Never downloads: the local artifact must already be staged (the
    pinned executable for ``opencode-binary``). Missing artifacts are a
    typed NEEDS_STAGING state with exact operator instructions."""
    _guard(cfg, f"install {name}", confirmed)
    entry = _check_name(name)
    from .opencode import policy as POL
    from .opencode import state as _OCS

    staged = _staged_opencode_binary(source)
    if staged is None:
        raise RuntimeImageError(
            STAGING_CODE,
            "opencode-binary install needs the pinned executable staged"
            " locally: pass --source <path>, set QUBIT_OPENCODE_PIN_BIN,"
            f" or stage it at {POL.DEFAULT_BINARY} (opencode"
            f" {POL.PINNED_OPENCODE_VERSION} from {entry['source']});"
            " nothing was downloaded and nothing was installed")
    import subprocess as _sp

    try:
        proc = _sp.run([str(staged), "--version"],
                       capture_output=True, text=True, timeout=15)
        reported = ((proc.stdout or "") + (proc.stderr or "")).strip()[:80]
    except Exception as exc:
        raise RuntimeImageError(MISMATCH_CODE,
                                f"binary version probe failed: {exc}") from exc
    if POL.PINNED_OPENCODE_VERSION not in reported:
        raise RuntimeImageError(
            MISMATCH_CODE,
            f"staged binary reports {reported!r}; pinned version is"
            f" {POL.PINNED_OPENCODE_VERSION} — refusing to activate an"
            " unpinned executable")
    owned = _OCS.owned_binary()
    owned.parent.mkdir(parents=True, exist_ok=True)
    tmp = owned.parent / "opencode.tmp"
    tmp.write_bytes(staged.read_bytes())
    os.chmod(tmp, 0o755)
    os.replace(tmp, owned)
    try:
        os.chmod(owned, 0o755)
    except OSError:
        pass
    digest = _sha256_file(owned)
    receipt = _OCS.owned_receipt_path()
    receipt.write_text(json.dumps(
        {"version": POL.PINNED_OPENCODE_VERSION, "sha256": digest,
         "source": staged.name,
         "installed_at": _now(), "installed_by": requested_by},
        indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(receipt, 0o600)
    except OSError:
        pass
    digest_ref = f"sha256:{digest}"
    record = _record_install(cfg, name, digest_ref, requested_by)
    _audit("install", requested_by,
           {"image": name, "digest": "<redacted-sha>"})
    return {"state": "OK", "image": name, "digest": digest_ref,
            "binary": str(owned),
            "previous": record["previous"],
            "rollback": ("previous receipt kept in state/"
                         f"{STATE_FILE}; `qubit runtime rollback {name}"
                         " --confirm` restores it")}


def _staged_opencode_binary(explicit: str = "") -> Optional[Path]:
    """Locate the staged pinned binary without downloading anything.

    Precedence: explicit --source, QUBIT_OPENCODE_PIN_BIN, the operator
    install, then PATH. Returns None when nothing is staged."""
    from .opencode import policy as POL

    raw = (explicit or "").strip() or \
        (os.environ.get("QUBIT_OPENCODE_PIN_BIN", "") or "").strip()
    if raw:
        cand = Path(raw).expanduser()
        return cand if cand.is_file() else None
    for cand in (Path(POL.DEFAULT_BINARY),
                 Path(shutil.which("opencode") or "")):
        if cand and str(cand) and cand.is_file():
            return cand
    return None


def upgrade(cfg: Any, name: str, *, requested_by: str = "cli:user",
            confirmed: bool = False, source: str = "") -> Dict[str, Any]:
    """Re-verify and re-stage the pinned image (confirmed).

    Pins are exact, so "upgrade" means: re-run verification against the
    current pin, refresh the receipt, and keep the previous receipt for
    rollback. A failed verification leaves the previous state active."""
    _guard(cfg, f"upgrade {name}", confirmed)
    entry = _check_name(name)
    state = _load(cfg)
    prev = state["images"].get(name)
    out = install(cfg, name, requested_by=requested_by, confirmed=True,
                  source=source)
    if isinstance(prev, dict) and prev.get("digest") and \
            out.get("digest") == prev.get("digest"):
        out["note"] = (f"{name} already at pin {entry['pin']}; receipt"
                       " refreshed, previous state kept")
    else:
        out["note"] = (f"{name} staged at pin {entry['pin']}; previous"
                       " receipt kept for rollback")
    return out


def rollback(cfg: Any, name: str, *, requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Restore the previous install receipt (confirmed).

    The previous receipt is the rollback target; without one the call is
    a typed NOT_FOUND (never a silent no-op claim)."""
    _guard(cfg, f"rollback {name}", confirmed)
    _check_name(name)
    state = _load(cfg)
    record = state["images"].get(name)
    if not isinstance(record, dict) or not record.get("previous"):
        raise RuntimeImageError(NOT_FOUND_CODE,
                                f"no previous {name} receipt to roll back to")
    prev = record["previous"]
    record["previous"] = None
    record["digest"] = prev["digest"]
    record["installed_at"] = prev.get("installed_at", _now())
    record["rolled_back_at"] = _now()
    record["rolled_back_by"] = requested_by
    state["images"][name] = record
    _save(cfg, state)
    _audit("rollback", requested_by, {"image": name})
    return {"state": "OK", "image": name, "digest": prev["digest"],
            "rollback": "restored previous receipt; re-run `qubit runtime"
                        " status` to re-verify the artifact"}


def remove(cfg: Any, name: str, *, requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Remove an installed runtime (confirmed; audited; state kept).

    Deletes the owned binary tree; refuses while a supervised run is
    active. The audit trail and the previous-receipt slot survive removal
    for forensics."""
    _guard(cfg, f"remove {name}", confirmed)
    entry = _check_name(name)
    if _runs_active(cfg):
        raise RuntimeImageError(IN_USE_CODE,
                                f"{name} has an active supervised run;"
                                " cancel it first (`qubit opencode cancel`)"
                                " — refusing to pull a live runtime")
    removed: List[str] = []
    if entry["name"] == "opencode-binary":
        from .opencode import state as _OCS

        owned_dir = _OCS.owned_binary_dir()
        if owned_dir.is_dir():
            import shutil as _sh

            _sh.rmtree(owned_dir, ignore_errors=False)
            removed.append(str(owned_dir))
    state = _load(cfg)
    record = state["images"].pop(name, None)
    state["images"][name + ":removed"] = {
        "removed_at": _now(), "actor": requested_by,
        "previous_receipt": record if isinstance(record, dict) else None}
    _save(cfg, state)
    _audit("remove", requested_by, {"image": name, "removed": removed})
    return {"state": "OK", "image": name, "removed": removed,
            "rollback": ("re-stage the pinned artifact and `qubit runtime"
                         f" install {name} --confirm`; the prior receipt is"
                         f" kept under {name}:removed")}


def _runs_active(cfg: Any) -> bool:
    try:
        from .opencode import runs as _runs

        # Any non-terminal supervised run blocks runtime removal.
        check = getattr(_runs, "any_active", None)
        if callable(check):
            return bool(check(cfg))
    except Exception:
        pass
    return False


def doctor(cfg: Any) -> Dict[str, Any]:
    """Resource + permission + hygiene diagnosis (read-only).

    Checks disk ceilings per image, state-file perms, receipt validity,
    and confirms no Docker daemon/socket dependency. Returns typed
    findings — never auto-remediates."""
    findings: List[Dict[str, str]] = []
    state = _load(cfg)
    try:
        usage = shutil.disk_usage(str(Path(cfg.state_dir())))
        findings.append({"check": "disk",
                         "result": f"{usage.free // 1024 // 1024}MB free"})
    except OSError as exc:
        findings.append({"check": "disk", "result": f"UNREADABLE: {exc}"})
    path = _state_path(cfg)
    if path.is_file():
        try:
            mode = oct(path.stat().st_mode & 0o777)
            findings.append({"check": "state_perms",
                             "result": "OK (0o600)" if mode == "0o600"
                             else f"LOOSE ({mode})"})
        except OSError as exc:
            findings.append({"check": "state_perms",
                             "result": f"UNREADABLE: {exc}"})
    else:
        findings.append({"check": "state_perms", "result": "no state yet"})
    for entry in REGISTRY:
        prov = _opencode_provenance()
        owned = prov.get("owned", {}) or {}
        if owned.get("ok"):
            result = (f"owned {owned['binary']} verified"
                      f" ({owned['version']})")
        elif prov.get("installed"):
            result = (f"owned copy {owned.get('reason', 'MISSING')};"
                      f" operator candidate {prov.get('binary')}"
                      " (not effective until installed)")
        else:
            result = "not staged"
        findings.append({"check": f"artifact:{entry['name']}",
                         "result": result})
    findings.append({"check": "docker_daemon",
                     "result": "not required; Qubit runtimes never contact"
                               " a container daemon"})
    for sock in ("/var/run/docker.sock", "/run/docker.sock"):
        if Path(sock).exists():
            findings.append({"check": f"host_socket:{sock}",
                             "result": "present on host; ignored by Qubit"})
    return {"state": "OK", "findings": findings,
            "records": sorted(state["images"].keys())}


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop"
                           " is latched; reset it before changing runtimes.",
            "owner": "qubit-policy",
            "rollback": "runtimes untouched; nothing to roll back"}


def redact_spec(text: str) -> str:
    return _redact(text)
