"""Canonical custom-endpoint store + activation authority.

The dashboard Settings provider console (``POST/PUT/DELETE`` on
``/api/providers/custom-endpoints``) persists custom OpenAI-compatible
endpoints in ``<state_dir>/qubit_custom_endpoints.json`` (read-old name
``hermes_custom_endpoints.json``). This module is the ONE reader/validator for
that store so the route handler, the model resolver, and the activation path
agree on what an endpoint is and what "active" means.

Activation is real: :func:`register_and_activate` validates the stored entry,
registers it with the canonical model-profile resolver, and makes it the
persisted active profile. It returns a typed result and never reports success
when the resolver write failed. Local (loopback) endpoints become executable
LOCAL profiles; remote endpoints become API/PLUGIN profiles whose egress the
frontier policy still gates (a remote endpoint is recorded as active, but the
turn path keeps honouring the egress allowlist rather than silently bypassing
it).

This module deliberately holds no credential values; a stored ``key_ref`` is an
``env:``/``file:`` reference string only.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

STORE_NAME = "qubit_custom_endpoints.json"
#: pre-rename name; read once when only it exists, never written again.
LEGACY_STORE_NAME = "hermes_custom_endpoints.json"
STORE_SCHEMA = "qubit-custom-endpoints/1"
ACTIVE_KEY = "_active"


def _base(cfg: Any) -> Path:
    try:
        return Path(cfg.state_dir())
    except Exception:  # noqa: BLE001 - a config-less caller gets a safe temp
        return Path("/tmp")


def store_path(cfg: Any) -> Path:
    """The canonical store path (the write target) for *cfg*."""
    return _base(cfg) / STORE_NAME


def read_path(cfg: Any) -> Path:
    """Read source: canonical when present, else the legacy-named store."""
    new = store_path(cfg)
    if new.exists():
        return new
    legacy = _base(cfg) / LEGACY_STORE_NAME
    return legacy if legacy.exists() else new


def load(cfg: Any) -> Dict[str, Any]:
    """Read the store (endpoint records + the ``_active`` marker)."""
    try:
        raw = json.loads(read_path(cfg).read_text())
    except (OSError, ValueError):
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def slug(raw: str, fallback: str = "custom") -> str:
    """The canonical endpoint id for a raw id/name (matches the route)."""
    text = re.sub(r"[^a-z0-9_.-]+", "-", str(raw or "").strip().lower())
    return text.strip("-")[:64] or fallback


def is_loopback(url: str) -> bool:
    """True when *url* addresses this host (a local endpoint, no egress)."""
    try:
        host = (urllib.parse.urlparse(str(url or "")).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def validate(record: Any) -> Tuple[bool, str]:
    """Validate an endpoint record; returns ``(ok, detail)``.

    Mirrors the reference ``_write_custom_endpoint``: a name is the store key
    (not part of the record body check), ``base_url`` must carry a scheme and
    host, and a ``model`` is required.
    """
    if not isinstance(record, dict):
        return False, "endpoint record must be an object"
    base_url = str(record.get("base_url", record.get("url", "")) or "").strip()
    if not base_url:
        return False, "base_url required"
    parsed = urllib.parse.urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        return False, "base_url must include scheme and host"
    if not str(record.get("model", "") or "").strip():
        return False, "model required"
    return True, ""


def active_ident(cfg: Any) -> str:
    """The stored active endpoint id, or ``""``."""
    return str(load(cfg).get(ACTIVE_KEY, "") or "").strip()


def active_selection(cfg: Any) -> Optional[Dict[str, str]]:
    """The active endpoint's provider/model/base_url, or ``None``.

    Used by read surfaces that must report the runtime's real selection rather
    than trusting a stale ``_active`` marker pointing at a removed or
    incomplete endpoint.
    """
    store = load(cfg)
    ident = str(store.get(ACTIVE_KEY, "") or "").strip()
    if not ident:
        return None
    record = store.get(ident)
    if not isinstance(record, dict) or not validate(record)[0]:
        return None
    return {
        "id": ident,
        "provider": ident,
        "model": str(record.get("model", "") or "").strip(),
        "base_url": str(record.get("base_url", record.get("url", ""))
                        or "").strip(),
        "key_ref": str(record.get("key_ref", "") or "").strip(),
    }


def register_and_activate(cfg: Any, ident: str,
                          record: Any) -> Dict[str, Any]:
    """Register *record* as a model profile and set it active.

    Returns a typed dict: ``{"state": "OK", ...}`` on success, else
    ``{"state": "FAILED"|"REFUSED", "code": ..., "detail": ...}``. The resolver
    registry is the single selection authority; a failed write is reported, not
    swallowed. The profile kind follows the endpoint: a loopback endpoint is a
    LOCAL profile (executable without egress consent), a remote endpoint is an
    API profile when it carries a ``key_ref`` and a PLUGIN profile otherwise,
    with ``egress_consent="deny"`` so the frontier policy still gates its use.
    """
    ok, detail = validate(record)
    if not ok:
        return {"state": "REFUSED", "code": "ENDPOINT_INCOMPLETE",
                "detail": detail}
    ident = str(ident or "").strip()
    if not ident:
        return {"state": "FAILED", "code": "MISSING_ID",
                "detail": "endpoint id required"}
    from . import resolver as R

    base_url = str(record.get("base_url", record.get("url", ""))
                   or "").strip().rstrip("/")
    model = str(record.get("model", "") or "").strip()
    key_ref = str(record.get("key_ref", "") or "").strip()
    local = is_loopback(base_url)
    kind = R.LOCAL if local else (R.API if key_ref else R.PLUGIN)
    profile = R.ModelProfile(
        id=ident, kind=kind, provider=ident, model=model, base_url=base_url,
        key_ref=key_ref, locality="local" if local else "external",
        egress_consent="deny", roles=[R.DEFAULT_ROLE], enabled=True,
        rollback_to=ident,
        provenance=f"custom-endpoint://{ident}")
    try:
        written = R.upsert_model_profile(cfg, profile, confirm=True)
    except Exception as exc:  # noqa: BLE001 - typed failure, never fabricated
        return {"state": "FAILED", "code": "PROFILE_WRITE_FAILED",
                "detail": str(exc)[:300]}
    if written.get("state") != "OK":
        return {"state": "FAILED",
                "code": written.get("code", "PROFILE_WRITE_FAILED"),
                "detail": written.get("detail", "profile write failed")}
    try:
        active = R.set_active_profile(cfg, ident, confirm=True)
    except Exception as exc:  # noqa: BLE001
        return {"state": "FAILED", "code": "ACTIVE_WRITE_FAILED",
                "detail": str(exc)[:300]}
    if active.get("state") != "OK":
        return {"state": "FAILED",
                "code": active.get("code", "ACTIVE_WRITE_FAILED"),
                "detail": active.get("detail", "active selection failed")}
    return {"state": "OK", "id": ident, "active": ident, "kind": kind,
            "model": model, "base_url": base_url, "local": local}
