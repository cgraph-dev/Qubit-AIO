"""Cloud-worker adapter: provider config + snapshot build ops (A42 / OC-D06).

Qubit's supported fleet is the local confined process cell (see
``runtime_images.cells``). Cloud placement is an optional adapter that requires
an injected provider transport: this module persists the provider configuration
(reference-only credentials, never a plaintext secret) and offers a snapshot
build that is an honest typed ``RUNTIME_CLOUD_UNAVAILABLE`` without a transport.

No Docker/Kubernetes daemon is ever contacted; no secret value is stored or
returned.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA = "qubit-cloud-workers/1"
STATE_FILE = "cloud_workers.json"
UNAVAILABLE_CODE = "RUNTIME_CLOUD_UNAVAILABLE"
CONFIRM_CODE = "CLOUD_WORKER_CONFIRM_REQUIRED"
ESTOP_CODE = "CLOUD_WORKER_ESTOP"
BAD_INPUT_CODE = "CLOUD_WORKER_BAD_INPUT"
SECRET_CODE = "CLOUD_WORKER_SECRET_REFUSED"

_REF_RE = re.compile(r"^(env|file):[A-Za-z0-9_./~-]{1,200}$")
_SECRET_VALUE_RE = re.compile(
    r"sk-[A-Za-z0-9]{10,}|pplx-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN ")


class CloudWorkerError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: Any) -> str:
    try:
        from .redaction import redact_text

        return redact_text(str(text))
    except Exception:
        return str(text)


def _now() -> float:
    return time.time()


def _path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STATE_FILE


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "config": {}, "history": []}


def _load(cfg: Any) -> Dict[str, Any]:
    try:
        data = json.loads(_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _blank()
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return _blank()
    if not isinstance(data.get("config"), dict):
        data["config"] = {}
    if not isinstance(data.get("history"), list):
        data["history"] = []
    return data


def _save(cfg: Any, data: Dict[str, Any]) -> None:
    path = _path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _estop(cfg: Any) -> bool:
    try:
        from .plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:
        return True


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"cloud_workers.{op}", actor=actor, **detail)
    except Exception:
        pass


def config_get(cfg: Any) -> Dict[str, Any]:
    """Read the persisted cloud-worker provider config (references only)."""
    return {"state": "OK", "schema": SCHEMA, "config": _load(cfg)["config"],
            "authority": "provider config with env:/file: key references; no "
                         "secret value is stored or returned"}


def config_set(cfg: Any, *, provider: str, endpoint: str = "",
               key_ref: str = "", enabled: bool = True,
               confirmed: bool = False,
               requested_by: str = "cli:user") -> Dict[str, Any]:
    """Persist cloud-worker provider config (confirmed, reference-only)."""
    if _estop(cfg):
        return {"state": "ESTOP", "code": ESTOP_CODE,
                "detail": "emergency stop latched; config not changed"}
    clean_provider = str(provider or "").strip()
    if not clean_provider or len(clean_provider) > 64 or \
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", clean_provider):
        return {"state": "FAILED", "code": BAD_INPUT_CODE,
                "detail": "a bounded provider id is required"}
    clean_ref = str(key_ref or "").strip()
    if clean_ref:
        if _SECRET_VALUE_RE.search(clean_ref) or not _REF_RE.match(clean_ref):
            return {"state": "REFUSED", "code": SECRET_CODE,
                    "detail": "key_ref must be an env:/file: reference; "
                              "plaintext secret values are refused"}
    if not confirmed:
        return {"state": "denied", "code": CONFIRM_CODE,
                "detail": "cloud-worker config requires explicit --confirm",
                "would_change": {"provider": clean_provider,
                                 "enabled": bool(enabled)}}
    data = _load(cfg)
    data["config"] = {
        "provider": clean_provider,
        "endpoint": _redact(endpoint)[:300],
        "key_ref": clean_ref,
        "enabled": bool(enabled),
        "updated_at": _now(),
    }
    data["history"].append({"at": _now(), "event": "config_set",
                            "provider": clean_provider,
                            "actor": _redact(requested_by)[:64]})
    data["history"] = data["history"][-200:]
    try:
        _save(cfg, data)
    except OSError as exc:
        return {"state": "FAILED", "code": BAD_INPUT_CODE,
                "detail": f"config could not be persisted ({exc})"}
    _audit("config_set", requested_by, {"provider": clean_provider,
                                        "enabled": bool(enabled)})
    return {"state": "OK", "schema": SCHEMA, "config": data["config"],
            "authority": "persisted locally; cloud placement still requires a "
                         "provider transport"}


def snapshot_build(cfg: Any, *, name: str = "", confirmed: bool = False,
                   transport: Optional[Any] = None,
                   requested_by: str = "cli:user") -> Dict[str, Any]:
    """Request a cloud-worker snapshot build over an injected transport.

    Without a provider transport this is a typed ``RUNTIME_CLOUD_UNAVAILABLE``
    (never a fabricated snapshot).
    """
    if _estop(cfg):
        return {"state": "ESTOP", "code": ESTOP_CODE,
                "detail": "emergency stop latched; snapshot refused"}
    if not confirmed:
        return {"state": "denied", "code": CONFIRM_CODE,
                "detail": "building a cloud-worker snapshot requires explicit "
                          "--confirm"}
    if transport is None:
        return {"state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
                "schema": SCHEMA, "name": str(name or ""),
                "detail": "cloud-worker snapshot build needs a provider "
                          "transport; use local process cells "
                          "(`qubit runtime cells`) without one",
                "owner": "qubit-policy",
                "rollback": "no snapshot built; nothing to roll back"}
    from . import runtime_images as RI

    request = {"type": "snapshot.build", "name": str(name or ""),
               "provider": _load(cfg)["config"].get("provider", ""),
               "local_images": [i["name"] for i in RI.status(cfg)["runtimes"]]}
    try:
        response = transport(request)
    except Exception as exc:
        return {"state": "FAILED", "code": UNAVAILABLE_CODE,
                "schema": SCHEMA, "name": str(name or ""),
                "detail": _redact(str(exc))[:200],
                "rollback": "no snapshot built; nothing to roll back"}
    if not isinstance(response, dict) or response.get("built") is not True:
        return {"state": "REFUSED", "code": UNAVAILABLE_CODE,
                "schema": SCHEMA, "name": str(name or ""),
                "detail": "provider did not build a snapshot"}
    return {"state": "OK", "schema": SCHEMA, "name": str(name or ""),
            "snapshot": _redact(response.get("snapshot_id", ""))[:120],
            "provider": _redact(response.get("provider", ""))[:120]}
