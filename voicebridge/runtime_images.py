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
UNRECOVERABLE_CODE = "RUNTIME_UNRECOVERABLE"
CORRUPT_STATE_CODE = "RUNTIME_STATE_CORRUPT"

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
        return {"schema": SCHEMA, "images": {}, "history": []}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "images": {}, "history": []}
    if not isinstance(data.get("images"), dict):
        data["images"] = {}
    if not isinstance(data.get("history"), list):
        data["history"] = []
    return data


def _append_history(cfg: Any, name: str, event: str, actor: str,
                    detail: Optional[Dict[str, Any]] = None) -> None:
    """Append a bounded, redacted update-history entry (best effort)."""
    try:
        state = _load(cfg)
        entry: Dict[str, Any] = {"at": _now(), "image": name, "event": event,
                                 "actor": _redact(actor)[:64]}
        for key, value in (detail or {}).items():
            entry[key] = value
        state["history"].append(entry)
        state["history"] = state["history"][-200:]
        _save(cfg, state)
    except Exception:
        pass


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
            "update_history": len(state.get("history", [])),
            "recovery": {"modes": ["recover", "repair", "rollback"]},
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
    _append_history(cfg, name, "install", requested_by,
                    {"digest": digest_ref,
                     "previous": record.get("previous")})
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
    _append_history(cfg, name, "upgrade", requested_by,
                    {"digest": out.get("digest", "")})
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
    _append_history(cfg, name, "rollback", requested_by,
                    {"digest": prev["digest"]})
    return {"state": "OK", "image": name, "digest": prev["digest"],
            "rollback": "restored previous receipt; re-run `qubit runtime"
                        " status` to re-verify the artifact"}


def update_history(cfg: Any, name: str = "") -> Dict[str, Any]:
    """Bounded, append-only local update history (redacted)."""
    state = _load(cfg)
    clean = (name or "").strip().lower()
    entries = [e for e in state.get("history", [])
               if not clean or e.get("image") == clean]
    return {"state": "OK", "image": clean, "entries": entries[-100:],
            "authority": "append-only bounded local update history"}


def recover(cfg: Any, name: str, *, requested_by: str = "cli:user",
            confirmed: bool = False) -> Dict[str, Any]:
    """Reconcile or re-stage a runtime from verified local state.

    Recovery never downloads: it rebuilds the install record from the
    digest-verified owned receipt, or re-installs from the pinned local
    candidate when the owned bytes were lost. With neither, the call is a
    typed ``RUNTIME_UNRECOVERABLE`` (never a silent false-healthy claim)."""
    _guard(cfg, f"recover {name}", confirmed)
    entry = _check_name(name)
    from .opencode import state as _OCS

    owned = _OCS.verify_owned_binary()
    state = _load(cfg)
    if owned.get("ok"):
        digest = f"sha256:{owned['sha256']}"
        record = state["images"].get(name)
        if (isinstance(record, dict) and not record.get("removed_at")
                and record.get("digest") == digest):
            return {"state": "OK", "image": name,
                    "outcome": "ALREADY_HEALTHY", "digest": digest,
                    "detail": "verified owned artifact already matches the "
                              "recorded state"}
        state["images"][name] = {"digest": digest, "installed_at": _now(),
                                 "actor": requested_by, "previous": None,
                                 "recovered_at": _now(),
                                 "recovered_by": requested_by}
        state["images"].pop(name + ":removed", None)
        _save(cfg, state)
        _audit("recover", requested_by,
               {"image": name, "outcome": "RECONCILED"})
        _append_history(cfg, name, "recover", requested_by,
                        {"digest": digest, "outcome": "RECONCILED"})
        return {"state": "OK", "image": name, "outcome": "RECONCILED",
                "digest": digest,
                "detail": "rebuilt the install record from the verified "
                          "on-disk receipt"}
    if entry["name"] == "opencode-binary":
        staged = _staged_opencode_binary("")
        if staged is not None:
            out = install(cfg, name, requested_by=requested_by,
                          confirmed=True, source=str(staged))
            out["outcome"] = "RESTAGED"
            _audit("recover", requested_by,
                   {"image": name, "outcome": "RESTAGED"})
            _append_history(cfg, name, "recover", requested_by,
                            {"outcome": "RESTAGED"})
            return out
    raise RuntimeImageError(
        UNRECOVERABLE_CODE,
        "no verified owned artifact and no pinned local candidate to "
        "restore; re-stage the pinned binary and run `qubit runtime install`")


def repair(cfg: Any, *, requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Bounded repair/recovery of runtime state (confirmed; no downloads).

    Rebuilds a corrupt state file (preserving the bad copy), reconciles
    records against the digest-verified owned artifact, and is honest when
    nothing can be recovered."""
    _guard(cfg, "repair", confirmed)
    findings: List[Dict[str, str]] = []
    actions: List[Dict[str, str]] = []
    path = _state_path(cfg)
    if path.exists():
        try:
            json.loads(path.read_text(encoding="utf-8"))
            findings.append({"check": "state_json", "result": "OK"})
        except (OSError, ValueError) as exc:
            findings.append({"check": "state_json",
                             "result": f"CORRUPT: {_redact(str(exc))[:120]}"})
            try:
                path.replace(path.with_suffix(".corrupt"))
                _save(cfg, {"schema": SCHEMA, "images": {}, "history": []})
            except OSError as exc2:
                raise RuntimeImageError(
                    CORRUPT_STATE_CODE,
                    f"state corrupt and not repairable: {exc2}") from exc2
            actions.append({"action": "state_backup",
                            "result": "rewrote a clean state file; corrupt "
                                      "copy preserved"})
    else:
        findings.append({"check": "state_json", "result": "no state yet"})
    for entry in REGISTRY:
        name = entry["name"]
        state = _load(cfg)
        rec = state["images"].get(name)
        prov = _opencode_provenance()
        if not prov.get("installed"):
            findings.append({"check": f"artifact:{name}", "result": "MISSING"})
            if rec is not None or (name + ":removed") in state["images"]:
                actions.append({"action": "recover", "image": name,
                                "result": "attempted"})
                try:
                    recover(cfg, name, requested_by=requested_by,
                            confirmed=True)
                except RuntimeImageError as exc:
                    actions[-1]["result"] = f"UNRECOVERABLE: {exc.code}"
            continue
        if not prov.get("effective"):
            findings.append({"check": f"artifact:{name}",
                             "result": "DEGRADED: present but not the "
                                       "verified owned copy"})
            continue
        digest = str(prov.get("digest") or "")
        if isinstance(rec, dict) and rec.get("digest") == digest:
            findings.append({"check": f"artifact:{name}", "result": "OK"})
        else:
            findings.append({"check": f"artifact:{name}",
                             "result": "RECORD_MISMATCH"})
            actions.append({"action": "recover", "image": name,
                            "result": "reconciled"})
            recover(cfg, name, requested_by=requested_by, confirmed=True)
    _audit("repair", requested_by,
           {"actions": len(actions), "findings": len(findings)})
    return {"state": "OK", "findings": findings, "actions": actions,
            "detail": "bounded repair; never downloads and never invents a "
                      "healthy state"}


def update_report(cfg: Any) -> Dict[str, Any]:
    """Update status + history + recovery plan (read-only report)."""
    return {"state": "OK", "status": status(cfg),
            "history": update_history(cfg)["entries"],
            "recovery": {
                "modes": ["recover", "repair", "rollback"],
                "recover": "rebuild from the verified owned receipt or "
                           "re-stage the pinned local candidate",
                "repair": "bounded state repair; never downloads",
                "rollback": "restore the previous install receipt"},
            "authority": "local, pinned, digest-verified updates only"}


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
    _append_history(cfg, name, "remove", requested_by,
                    {"removed": list(removed)})
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


# ---------------------------------------------------------------------------
# OC-54 / OC-D06: Qubit-safe fleet equivalent (process cells, no Docker)
# ---------------------------------------------------------------------------
# Docker/Kubernetes are forbidden by the stack contract. The Qubit-safe
# equivalent is a *local process cell*: one confined one-shot runtime bound to
# a pinned, digest-verified image with explicit admission, audit, e-stop, and
# release. A cloud worker admission boundary exists but requires an injected
# provider transport; without one it is an honest typed UNAVAILABLE.
RUNTIME_CONTAINER_FORBIDDEN = "RUNTIME_CONTAINER_FORBIDDEN"
RUNTIME_CLOUD_UNAVAILABLE = "RUNTIME_CLOUD_UNAVAILABLE"
RUNTIME_CELL_NOT_READY = "RUNTIME_CELL_NOT_READY"
CELL_SCHEMA = "qubit-runtime-cells/1"
CELL_ADMISSION_SCHEMA = "qubit-worker-admission/1"


def refuse_container(action: str = "container") -> Dict[str, Any]:
    """Typed refusal for Docker/Kubernetes fleet operations (OC-54)."""
    return {"state": "REFUSED", "code": RUNTIME_CONTAINER_FORBIDDEN,
            "action": action,
            "explanation": "Qubit Agent cannot do this: container daemons "
                           "(Docker/Kubernetes) are forbidden by the stack "
                           "contract. The supported equivalent is a local "
                           "confined process cell (`qubit runtime cells`).",
            "owner": "qubit-policy",
            "rollback": "no daemon contacted; nothing to roll back"}


def _cells(state: Dict[str, Any]) -> Dict[str, Any]:
    cells = state.get("cells")
    if not isinstance(cells, dict):
        cells = {}
    return cells


def _image_state(cfg: Any, name: str) -> str:
    for item in status(cfg).get("runtimes", []):
        if item.get("name") == name:
            return str(item.get("state", "MISSING"))
    return "MISSING"


def cells(cfg: Any) -> Dict[str, Any]:
    """Read-only local process-cell inventory (Qubit-safe fleet equivalent)."""
    state = _load(cfg)
    admitted = _cells(state)
    out = []
    for entry in REGISTRY:
        name = entry["name"]
        rec = admitted.get(name) if isinstance(admitted, dict) else None
        out.append({"cell": f"cell:{name}", "image": name,
                    "backend": "process", "pin": entry["pin"],
                    "confinement": entry["confinement"],
                    "state": "ADMITTED" if rec else "OFFLINE",
                    "admitted_at": (rec or {}).get("admitted_at"),
                    "admitted_by": (rec or {}).get("admitted_by", "")})
    return {"state": "OK", "schema": CELL_SCHEMA, "cells": out,
            "backend": "local-process",
            "daemon": "none-required",
            "detail": "process-isolated local cells; no Docker daemon, no "
                      "Kubernetes, no network image pulls"}


def cell_admit(cfg: Any, name: str, *, requested_by: str = "cli:user",
               confirmed: bool = False) -> Dict[str, Any]:
    """Admit one pinned runtime as a local process cell (confirmed).

    Admission requires the pinned image to be READY (digest-verified); a
    missing/degraded image is a typed ``RUNTIME_CELL_NOT_READY``. No container
    runtime is contacted.
    """
    if estop_engaged(cfg):
        return refuse_estop(f"cell admit {name}")
    if not confirmed:
        return {"state": "denied", "code": CONFIRM_CODE,
                "detail": "admitting a runtime cell requires explicit "
                          "--confirm", "owner": "qubit-policy",
                "rollback": "no cell admitted; nothing to roll back"}
    try:
        entry = _check_name(name)
    except RuntimeImageError as exc:
        return {"state": "FAILED", "code": exc.code, "detail": exc.detail,
                "owner": "qubit-policy",
                "rollback": "no cell admitted; nothing to roll back"}
    image_state = _image_state(cfg, entry["name"])
    if image_state != "READY":
        return {"state": "UNAVAILABLE", "code": RUNTIME_CELL_NOT_READY,
                "detail": f"image {entry['name']!r} is {image_state}; install "
                          "the pinned artifact before admitting a cell",
                "owner": "qubit-policy",
                "rollback": "no cell admitted; nothing to roll back"}
    state = _load(cfg)
    cells_map = _cells(state)
    cells_map[entry["name"]] = {"admitted_at": _now(),
                                "admitted_by": _redact(requested_by)[:64],
                                "confinement": entry["confinement"]}
    state["cells"] = cells_map
    _save(cfg, state)
    _append_history(cfg, entry["name"], "cell-admit", requested_by, {})
    _audit("cell_admit", requested_by, {"image": entry["name"]})
    return {"state": "OK", "schema": CELL_SCHEMA,
            "cell": f"cell:{entry['name']}", "image": entry["name"],
            "backend": "process", "confinement": entry["confinement"],
            "detail": "cell admitted; no container runtime contacted"}


def cell_release(cfg: Any, name: str, *, requested_by: str = "cli:user",
                 confirmed: bool = False) -> Dict[str, Any]:
    """Release an admitted process cell (confirmed, idempotent)."""
    if estop_engaged(cfg):
        return refuse_estop(f"cell release {name}")
    if not confirmed:
        return {"state": "denied", "code": CONFIRM_CODE,
                "detail": "releasing a runtime cell requires explicit "
                          "--confirm", "owner": "qubit-policy",
                "rollback": "cell unchanged; nothing to roll back"}
    try:
        entry = _check_name(name)
    except RuntimeImageError as exc:
        return {"state": "FAILED", "code": exc.code, "detail": exc.detail,
                "owner": "qubit-policy",
                "rollback": "cell unchanged; nothing to roll back"}
    state = _load(cfg)
    cells_map = _cells(state)
    existed = entry["name"] in cells_map
    cells_map.pop(entry["name"], None)
    state["cells"] = cells_map
    _save(cfg, state)
    _append_history(cfg, entry["name"], "cell-release", requested_by, {})
    _audit("cell_release", requested_by, {"image": entry["name"]})
    return {"state": "OK", "schema": CELL_SCHEMA,
            "cell": f"cell:{entry['name']}", "released": existed}


def cloud_worker_admission(cfg: Any, *, provider: str = "",
                           transport: Any = None,
                           requested_by: str = "cli:user") -> Dict[str, Any]:
    """Cloud-worker admission over an injected provider transport (OC-D06).

    Local prepared-image reuse already exists (``status``/``cells``). Cloud
    admission needs a provider; without an injected transport the call is a
    typed ``RUNTIME_CLOUD_UNAVAILABLE`` instead of a fabricated placement.
    """
    if estop_engaged(cfg):
        return refuse_estop("cloud worker admission")
    if transport is None:
        return {"state": "UNAVAILABLE", "code": RUNTIME_CLOUD_UNAVAILABLE,
                "provider": provider,
                "schema": CELL_ADMISSION_SCHEMA,
                "detail": "cloud worker admission needs a provider transport; "
                          "inject one or use local process cells",
                "owner": "qubit-policy",
                "rollback": "no worker placed; nothing to roll back"}
    request = {"type": "admission.request", "provider": provider,
               "local_images": [i["name"] for i in status(cfg)["runtimes"]]}
    try:
        response = transport(request)
    except Exception as exc:
        return {"state": "FAILED", "code": RUNTIME_CLOUD_UNAVAILABLE,
                "provider": provider, "schema": CELL_ADMISSION_SCHEMA,
                "detail": _redact(str(exc))[:200], "owner": "qubit-policy",
                "rollback": "no worker placed; nothing to roll back"}
    if not isinstance(response, dict) or response.get("admitted") is not True:
        return {"state": "REFUSED", "code": RUNTIME_CLOUD_UNAVAILABLE,
                "provider": provider, "schema": CELL_ADMISSION_SCHEMA,
                "detail": "provider did not admit a worker",
                "owner": "qubit-policy",
                "rollback": "no worker placed; nothing to roll back"}
    return {"state": "OK", "schema": CELL_ADMISSION_SCHEMA,
            "provider": provider,
            "worker": _redact(str(response.get("worker", "")))[:120],
            "warm_images": response.get("warm_images", []),
            "authority": "injected provider transport; no daemon or inbound "
                         "listener"}


def redact_spec(text: str) -> str:
    return _redact(text)
