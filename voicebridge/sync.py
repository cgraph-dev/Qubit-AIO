"""Qubit device sync via device-bound encrypted handoff.

Automatic
replication would exfiltrate vault-adjacent state, so Qubit sync is
an opt-in, user-carried, encrypted handoff on top of the 7C
portable-state primitives (audited OpenSSL envelopes, passphrase/approved
key, integrity hash, pre-restore backups):

* ``offer`` — export an encrypted bundle into the local outbox
  (``state/sync-outbox/<id>.vbp``, 0600) with a manifest sidecar (classes
  present, bytes, sha256, destination device, timestamps). The passphrase
  travels out-of-band with the user, never in Qubit output, logs, or the
  manifest. Explicit ``--confirm``; passphrase floor enforced.
* ``list``/``show`` — outbox manifests (redacted; never bundle bytes).
* ``revoke`` — tombstone the offer and delete its bundle bytes (confirmed,
  audited); revoked offers can no longer be accepted.
* ``accept`` — decrypt + integrity-verify a bundle (wrong passphrase and
  corruption are typed), preview the restore plan by default, and apply
  only with ``--apply`` + ``--confirm`` on an enrolled device
  (``device enroll``). When ``--offer`` names an outbox offer, revocation
  and destination-device binding are enforced before anything is read.

Conflict handling, merge semantics, and pre-restore backups are inherited
from the 7C ``plan_restore``/``restore_state`` path (no second
implementation). Nothing here touches the network: the user carries the
bundle file and the passphrase between PCs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "qubit-sync-offer/1"
OUTBOX_NAME = "sync-outbox"
PASSPHRASE_MIN_LEN = 12
MAX_BUNDLE_BYTES = 100_000_000

CONFIRM_CODE = "SYNC_CONFIRM_REQUIRED"
ESTOP_CODE = "SYNC_ESTOP"
NOT_FOUND_CODE = "SYNC_NOT_FOUND"
REVOKED_CODE = "SYNC_REVOKED"
DEVICE_MISMATCH_CODE = "SYNC_DEVICE_MISMATCH"
WEAK_PASSPHRASE_CODE = "SYNC_WEAK_PASSPHRASE"
NO_PASSPHRASE_CODE = "SYNC_NO_PASSPHRASE"
WRONG_PASSPHRASE_CODE = "SYNC_WRONG_PASSPHRASE"
CORRUPT_CODE = "SYNC_CORRUPT_BUNDLE"
EXPORT_CODE = "SYNC_EXPORT_FAILED"

_OFFER_RE = re.compile(r"^sync_[0-9a-f]{16}$")


class SyncError(Exception):
    """Typed sync failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _outbox(cfg: Any) -> Path:
    path = Path(cfg.state_dir()) / OUTBOX_NAME
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _passphrase(args_passphrase: str = "") -> str:
    pw = (args_passphrase or "").strip() or os.environ.get(
        "VOICE_BRIDGE_PASSPHRASE", "").strip()
    if not pw:
        raise SyncError(NO_PASSPHRASE_CODE,
                        "passphrase required (--passphrase or "
                        "VOICE_BRIDGE_PASSPHRASE)")
    if len(pw) < PASSPHRASE_MIN_LEN:
        raise SyncError(WEAK_PASSPHRASE_CODE,
                        f"passphrase must be >={PASSPHRASE_MIN_LEN} chars")
    return pw


def _manifest_path(cfg: Any, offer_id: str) -> Path:
    return _outbox(cfg) / f"{offer_id}.json"


def _bundle_path(cfg: Any, offer_id: str) -> Path:
    return _outbox(cfg) / f"{offer_id}.vbp"


def _read_manifest(cfg: Any, offer_id: str) -> Dict[str, Any]:
    clean = (offer_id or "").strip()
    if not _OFFER_RE.match(clean):
        raise SyncError(NOT_FOUND_CODE, f"no offer {offer_id!r}")
    try:
        raw = json.loads(_manifest_path(cfg, clean).read_text(
            encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SyncError(NOT_FOUND_CODE,
                        f"no offer {clean!r}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise SyncError(CORRUPT_CODE, f"offer {clean!r} is corrupt")
    return raw


def _write_manifest(cfg: Any, manifest: Dict[str, Any]) -> None:
    path = _manifest_path(cfg, manifest["offer_id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"sync.{op}", actor=actor, **detail)
    except Exception:
        pass


def _now() -> float:
    return time.time()


def _machine_id() -> str:
    from .device import machine_id

    return machine_id()


# ---------------------------------------------------------------- operations
def offer(cfg: Any, passphrase: str, *, for_device: str = "",
          requested_by: str = "cli:user",
          confirmed: bool = False) -> Dict[str, Any]:
    """Export an encrypted bundle into the outbox (user carries it)."""
    if not confirmed:
        raise SyncError(CONFIRM_CODE,
                        "sync offer requires explicit --confirm")
    from . import portable as P

    pw = _passphrase(passphrase)
    try:
        bundle = P.export_bundle(cfg, pw)
    except Exception as exc:
        raise SyncError(EXPORT_CODE,
                        f"bundle export failed: {exc}") from exc
    if len(bundle) > MAX_BUNDLE_BYTES:
        raise SyncError(EXPORT_CODE, "bundle exceeds 100MB cap")
    try:
        payload = P.read_bundle(bundle, pw)
    except Exception as exc:
        raise SyncError(EXPORT_CODE,
                        f"fresh bundle failed self-verify: {exc}") from exc
    state = payload.get("state") or {}
    classes = sorted(k for k, v in state.items() if v not in (None, [], {}, ""))
    offer_id = "sync_" + secrets.token_hex(8)
    manifest = {"schema": SCHEMA, "offer_id": offer_id,
                "created_at": _now(), "created_by": requested_by,
                "source_device": _machine_id(),
                "for_device": (for_device or "").strip(),
                "classes": classes,
                "bytes": len(bundle),
                "sha256": hashlib.sha256(bundle).hexdigest(),
                "revoked": False, "consumed_at": None}
    bundle_path = _bundle_path(cfg, offer_id)
    bundle_path.write_bytes(bundle)
    os.chmod(bundle_path, 0o600)
    _write_manifest(cfg, manifest)
    _audit("offer", requested_by,
           {"offer_id": offer_id, "bytes": len(bundle), "classes": classes,
            "for_device": bool(for_device)})
    return {"state": "OK", "offer": manifest,
            "handoff": "carry the .vbp file and the passphrase out-of-band; "
                       "the passphrase appears nowhere in Qubit output"}


def list_offers(cfg: Any) -> Dict[str, Any]:
    """Outbox manifests (redacted; never bundle bytes)."""
    out = []
    try:
        paths = sorted(_outbox(cfg).glob("sync_*.json"))
    except OSError:
        paths = []
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict) and raw.get("schema") == SCHEMA:
            out.append(raw)
    return {"state": "OK", "offers": out}


def show(cfg: Any, offer_id: str) -> Dict[str, Any]:
    """Show one offer manifest (redacted)."""
    return {"state": "OK", "offer": _read_manifest(cfg, offer_id)}


def revoke(cfg: Any, offer_id: str, *, requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Tombstone an offer and delete its bundle bytes (confirmed, audited)."""
    if not confirmed:
        raise SyncError(CONFIRM_CODE,
                        "sync revoke requires explicit --confirm")
    manifest = _read_manifest(cfg, offer_id)
    manifest["revoked"] = True
    manifest["revoked_at"] = _now()
    manifest["revoked_by"] = requested_by
    try:
        _bundle_path(cfg, manifest["offer_id"]).unlink()
    except OSError:
        pass
    _write_manifest(cfg, manifest)
    _audit("revoke", requested_by, {"offer_id": manifest["offer_id"]})
    return {"state": "OK", "revoked": manifest["offer_id"]}


def _check_offer_binding(cfg: Any, offer_id: str) -> Dict[str, Any]:
    manifest = _read_manifest(cfg, offer_id)
    if manifest.get("revoked"):
        raise SyncError(REVOKED_CODE,
                        f"offer {manifest['offer_id']!r} was revoked")
    want = (manifest.get("for_device") or "").strip()
    if want and want != _machine_id():
        raise SyncError(DEVICE_MISMATCH_CODE,
                        "offer is bound to another device")
    bundle_path = _bundle_path(cfg, manifest["offer_id"])
    try:
        bundle = bundle_path.read_bytes()
    except OSError as exc:
        raise SyncError(CORRUPT_CODE,
                        f"offer bundle missing: {exc}") from exc
    if hashlib.sha256(bundle).hexdigest() != manifest.get("sha256"):
        raise SyncError(CORRUPT_CODE, "offer bundle hash mismatch")
    return manifest


def accept(cfg: Any, bundle: str, passphrase: str, *, offer_id: str = "",
           apply: bool = False, requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Decrypt, verify, preview, and optionally apply a bundle.

    Preview (default) never writes. ``--apply`` additionally requires
    ``--confirm``, an enrolled device, and (for offer-bound bundles) a
    live revocation/device check. Merge/backup semantics come from the 7C
    restore path.
    """
    from . import device as Dev
    from . import portable as P

    pw = _passphrase(passphrase)
    manifest: Optional[Dict[str, Any]] = None
    if (offer_id or "").strip():
        manifest = _check_offer_binding(cfg, offer_id)
        raw_bundle: Optional[bytes] = None
    else:
        try:
            raw_bundle = Path(os.path.expanduser(bundle)).read_bytes()
        except OSError as exc:
            raise SyncError(CORRUPT_CODE,
                            f"bundle unreadable: {exc}") from exc
        if len(raw_bundle) > MAX_BUNDLE_BYTES:
            raise SyncError(CORRUPT_CODE, "bundle exceeds 100MB cap")
    if manifest is not None:
        try:
            raw_bundle = _bundle_path(cfg, manifest["offer_id"]).read_bytes()
        except OSError as exc:
            raise SyncError(CORRUPT_CODE,
                            f"offer bundle missing: {exc}") from exc
    assert raw_bundle is not None
    try:
        payload = P.read_bundle(raw_bundle, pw)
    except P.WrongPassphraseError as exc:
        raise SyncError(WRONG_PASSPHRASE_CODE,
                        f"wrong passphrase: {exc}") from exc
    except (P.CorruptedBundleError, P.UnsupportedSchemaError,
            P.LowDiskError, P.PortableError) as exc:
        raise SyncError(CORRUPT_CODE, f"bundle unusable: {exc}") from exc
    plan = P.plan_restore(cfg, payload, dry_run=True)
    preview = {"actions": [str(a)[:200] for a in plan.actions[:50]]}
    if not apply:
        return {"state": "OK", "applied": False, "plan": preview,
                "offer_id": manifest["offer_id"] if manifest else None}
    if not confirmed:
        raise SyncError(CONFIRM_CODE,
                        "sync accept --apply requires explicit --confirm")
    try:
        Dev.require_enrolled(cfg)
    except Dev.DeviceNotEnrolledError:
        raise SyncError(DEVICE_MISMATCH_CODE,
                        "destination device is not enrolled: run "
                        "`voicebridge device enroll` first") from None
    if manifest is not None:
        manifest = _check_offer_binding(cfg, manifest["offer_id"])
    result = P.restore_state(cfg, payload, dry_run=False)
    if manifest is not None:
        manifest["consumed_at"] = _now()
        _write_manifest(cfg, manifest)
    _audit("accept", requested_by,
           {"applied": True,
            "offer_id": manifest["offer_id"] if manifest else None})
    import json as _json

    try:
        result_text = _json.dumps(result, default=str)[:2000]
    except (TypeError, ValueError):
        result_text = str(result)[:2000]
    return {"state": "OK", "applied": True, "result": result_text,
            "offer_id": manifest["offer_id"] if manifest else None}


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before syncing.",
            "owner": "qubit-policy",
            "rollback": "nothing exported or restored; nothing to roll back"}
