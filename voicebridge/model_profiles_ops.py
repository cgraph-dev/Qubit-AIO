"""Canonical model/provider profile operations (dashboard/CLI/voice shared).

Thin, typed wrappers over :mod:`voicebridge.providers.resolver`. This module
owns no state: the resolver remains the single selection authority and the
existing versioned registry file remains the single store. Every operation
returns a plain typed dict (``state`` + ``code`` + ``detail`` when it fails)
so the dashboard op-dispatch, the CLI, and the voice registry can all call the
SAME function and return the SAME answer.

Safety contract:

* mutations are confirm-gated (``confirm=False`` is a dry run);
* credentials are secret **references** only (``env:NAME`` / ``file:PATH``):
  a raw key value is refused before anything is staged or written;
* reads never require confirmation; failures are typed, never fabricated.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .providers import resolver as R
from .providers.resolver import ResolverError

OP_GROUP = "profile"
PROFILE_PARITY = ("config", "profile")
VOICE_GUARD = "config_profiles"

LOCAL, API, REALTIME = R.LOCAL, R.API, R.REALTIME
KINDS = R.KINDS
ROLES = R.ROLES
DEFAULT_ROLE = R.DEFAULT_ROLE
DEFAULT_LOCAL_ID = R.DEFAULT_LOCAL_ID

_RAW_SECRET_DETAIL = (
    "raw secret refused: key_ref is a secret reference only "
    "(env:NAME or file:PATH); put the value in the environment or a file and "
    "reference it here")
_BAD_REF_DETAIL = "key_ref must be env:NAME or file:PATH"


def _state(out: Dict[str, Any], state: str = "OK") -> Dict[str, Any]:
    out.setdefault("state", state)
    return out


def _refused(code: str, detail: str) -> Dict[str, Any]:
    return {"state": "REFUSED", "code": code, "detail": detail}


def _err(exc: ResolverError, state: str = "FAILED") -> Dict[str, Any]:
    out = exc.to_dict()
    out["state"] = state
    return out


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _roles(value: Any) -> List[str]:
    if value is None or value == "":
        return [DEFAULT_ROLE]
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
    else:
        items = [p.strip() for p in str(value).replace(";", ",").split(",")]
    return [p for p in items if p] or [DEFAULT_ROLE]


def check_secret_ref(value: Any) -> Dict[str, Any]:
    """Typed guard: raw values refused, only ``env:``/``file:`` refs pass."""
    text = str(value or "").strip()
    if not text:
        return {"state": "OK", "code": "", "detail": "no credential needed"}
    if R.looks_like_raw_secret(text):
        return _refused("RAW_SECRET", _RAW_SECRET_DETAIL)
    if not R.is_secret_ref(text):
        return _refused("BAD_SECRET_REF", _BAD_REF_DETAIL)
    return {"state": "OK", "code": "", "detail": "secret reference accepted"}


# ---------------------------------------------------------------------------
# Reads (never confirm-gated)
# ---------------------------------------------------------------------------
def list_model_profiles(cfg: Any) -> Dict[str, Any]:
    out = R.list_profiles(cfg)
    out["state"] = "OK"
    return out


def show_model_profile(cfg: Any, profile_id: str) -> Dict[str, Any]:
    try:
        out = R.show_profile(cfg, str(profile_id))
    except ResolverError as exc:
        return _err(exc)
    out["state"] = "OK" if out.get("gate", {}).get("state") == "OK" \
        else "DEGRADED"
    return out


def test_model_profile(cfg: Any, profile_id: str = "",
                       probe: bool = False) -> Dict[str, Any]:
    pid = str(profile_id or "").strip()
    if not pid:
        try:
            pid = R.resolve(cfg).profile_id
        except ResolverError as exc:
            return _err(exc)
    try:
        out = R.test_profile(cfg, pid, probe=_as_bool(probe))
    except ResolverError as exc:
        return _err(exc)
    out.setdefault("state", "OK")
    return out


# ---------------------------------------------------------------------------
# Mutations (confirm-gated; dry-run otherwise)
# ---------------------------------------------------------------------------
def create_model_profile(
    cfg: Any, *,
    profile_id: str,
    kind: str = LOCAL,
    provider: str = "",
    model: str = "",
    base_url: str = "",
    key_ref: str = "",
    locality: Optional[str] = None,
    egress_consent: Optional[str] = None,
    context_tokens: Any = 4096,
    supports_tools: Any = False,
    supports_structured: Any = False,
    supports_streaming: Any = None,
    supports_vision: Any = False,
    roles: Any = None,
    fallback_to: str = "",
    rollback_to: str = "",
    confirm: bool = False,
) -> Dict[str, Any]:
    pid = str(profile_id or "").strip()
    if not pid:
        return {"state": "FAILED", "code": "MISSING_ID",
                "detail": "profile needs an id"}
    guard = check_secret_ref(key_ref)
    if guard["state"] != "OK":
        return guard
    k = str(kind or LOCAL).strip().lower()
    if k not in KINDS:
        return {"state": "FAILED", "code": "BAD_KIND",
                "detail": f"unknown kind {kind!r}"}
    prof = R.ModelProfile(
        id=pid, kind=k,
        provider=str(provider or ("ollama" if k == LOCAL else "")).strip(),
        model=str(model or pid).strip(),
        base_url=str(base_url or "").strip(),
        key_ref=str(key_ref or "").strip(),
        context_tokens=_as_int(context_tokens, 4096),
        supports_tools=_as_bool(supports_tools),
        supports_structured=_as_bool(supports_structured),
        # Honest default: the local runner streams; external API/realtime
        # adapters are request/response unless the operator declares otherwise.
        supports_streaming=_as_bool(supports_streaming, k == LOCAL),
        supports_vision=_as_bool(supports_vision),
        locality=str(locality or
                     ("local" if k == LOCAL else "external")).strip(),
        egress_consent=str(egress_consent or "deny").strip(),
        roles=_roles(roles),
        fallback_to=str(fallback_to or "").strip(),
        rollback_to=str(rollback_to or pid).strip() or pid,
    )
    try:
        out = R.upsert_model_profile(cfg, prof, confirm=bool(confirm))
    except ResolverError as exc:
        return _err(exc)
    return _state(out, "OK" if out.get("state") == "OK" else out.get("state"))


def clone_model_profile(cfg: Any, source_id: str, new_id: str, *,
                        confirm: bool = False) -> Dict[str, Any]:
    try:
        out = R.clone_model_profile(cfg, str(source_id), str(new_id),
                                    confirm=bool(confirm))
    except ResolverError as exc:
        return _err(exc)
    return _state(out, "OK" if out.get("state") == "OK" else out.get("state"))


def remove_model_profile(cfg: Any, profile_id: str, *,
                         confirm: bool = False) -> Dict[str, Any]:
    try:
        out = R.remove_model_profile(cfg, str(profile_id),
                                     confirm=bool(confirm))
    except ResolverError as exc:
        # Unknown / immutable are typed refusals, never a silent success.
        return _err(exc, "REFUSED")
    if out.get("state") == "DENIED":
        out["state"] = "REFUSED"
    return _state(out, "OK" if out.get("state") == "OK" else out.get("state"))


def select_session_model(cfg: Any, session: str, profile_id: str, *,
                         role: str = DEFAULT_ROLE,
                         confirm: bool = False) -> Dict[str, Any]:
    try:
        out = R.set_session_profile(cfg, str(session), str(profile_id),
                                    role=str(role or DEFAULT_ROLE),
                                    confirm=bool(confirm))
    except ResolverError as exc:
        return _err(exc)
    return _state(out, "OK" if out.get("state") == "OK" else out.get("state"))


def clear_session_model(cfg: Any, session: str, *,
                        role: str = "*", confirm: bool = False) -> Dict[str, Any]:
    if not confirm:
        return {"state": "OK", "dry_run": True, "action": "would clear",
                "session": str(session), "role": str(role or "*")}
    try:
        out = R.clear_session_profile(cfg, str(session),
                                      role=str(role or "*"))
    except ResolverError as exc:
        return _err(exc)
    return _state(out)


def rollback_model_profile(cfg: Any, profile_id: str, *,
                           session: str, role: str = DEFAULT_ROLE,
                           confirm: bool = False) -> Dict[str, Any]:
    """Select a profile's declared rollback target for this session."""
    p = R.get_profile(cfg, str(profile_id))
    if p is None:
        return {"state": "FAILED", "code": "UNKNOWN_PROFILE",
                "detail": f"no model profile {profile_id!r}"}
    target = p.rollback_to or p.fallback_to
    if not target or target == p.id:
        return {"state": "REFUSED", "code": "NO_ROLLBACK",
                "detail": f"profile {p.id!r} declares no rollback target"}
    if R.get_profile(cfg, target) is None:
        return {"state": "FAILED", "code": "ROLLBACK_TARGET_MISSING",
                "detail": f"rollback target {target!r} is not registered"}
    try:
        out = R.set_session_profile(cfg, str(session), target,
                                    role=str(role or DEFAULT_ROLE),
                                    confirm=bool(confirm))
    except ResolverError as exc:
        return _err(exc)
    out["rollback_to"] = target
    return _state(out, "OK" if out.get("state") == "OK" else out.get("state"))


__all__ = [
    "OP_GROUP", "PROFILE_PARITY", "VOICE_GUARD", "LOCAL", "API", "REALTIME",
    "KINDS", "ROLES", "DEFAULT_ROLE", "DEFAULT_LOCAL_ID",
    "check_secret_ref", "list_model_profiles", "show_model_profile",
    "create_model_profile", "clone_model_profile", "remove_model_profile",
    "test_model_profile", "select_session_model", "clear_session_model",
    "rollback_model_profile",
]
