"""Canonical model/provider profile resolver (Qubit one-authority selection).

Single place that answers **which provider/model and which routing, locality,
capability, and budget policy are effective for this session and task role**.

It does not create a second registry. Named model profiles are stored in the
SAME versioned registry file that :mod:`voicebridge.profiles` already owns
(``<state_dir>/profiles.json``) under the additive ``model_profiles`` key;
session-scoped selections live in the same state tree
(``<state_dir>/model_profile_sessions.json``). The existing
:mod:`voicebridge.providers.gateway` remains the execution authority; this
resolver decides the effective profile and exposes a typed preflight so chat,
CLI, dashboard, voice, planner, and summarizer can share one answer.

Safety contract:

* credentials are secret **references** only (``env:NAME`` / ``file:PATH``);
  a raw key is refused at validation time;
* external (API/realtime) profiles require explicit egress consent and a
  gateway allowlist entry before they are reported ``executable``;
* unknown/disabled profiles are typed failures, never a silent fallback to a
  different model.

Qubit-Brain4 is the shipped default *profile record*, not a hard-wired code
path: it can be cloned, replaced, deactivated, rolled back, or removed like any
other profile (see :func:`remove_model_profile`).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .policy import FrontierPolicy

LOCAL, API, REALTIME, PLUGIN = "local", "api", "realtime", "plugin"
KINDS = (LOCAL, API, REALTIME, PLUGIN)
EMBEDDING = "embedding"
ROLES = ("chat", "hard", "coding", "tool", "summarize", "vision", "classify",
         EMBEDDING)
DEFAULT_ROLE = "chat"
DEFAULT_LOCAL_ID = "qubit-brain4"
# The canonical embedding role resolves to a local Ollama embedding adapter.
# It is a synthetic default (not listed in ``default_profiles``) so adding the
# role never changes the shipped model-profile inventory/count.
DEFAULT_EMBED_ID = "qubit-embed"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
# Qubit's Radius provider plugin is an organization-scoped model gateway.
# It is represented as a ``plugin`` kind record with an ``env:`` credential
# reference so it is configurable without storing a raw key.
RADIUS_PROVIDER = "radius"
RADIUS_BASE_URL = "https://radius.pi.dev/v1"
RADIUS_KEY_REF = "env:RADIUS_API_KEY"
REGISTRY_KEY = "model_profiles"
SESSIONS_FILE = "model_profile_sessions.json"
SCHEMA = "qubit-model-profile/1"
SESSIONS_SCHEMA = "qubit-model-profile-sessions/1"
# Additive key in the SAME registry file: the persisted owner "active"
# provider/model profile. Explicit requests and per-session overrides still
# win; this is the default selection shared by CLI, dashboard, and voice.
ACTIVE_KEY = "active_model_profile"
ACTIVE_SCHEMA = "qubit-model-profile-active/1"

# /chat is the long-context terminal surface. It must never silently truncate,
# so its resolved model profile is required to declare at least this many
# context tokens. The floor is configurable (``terminal.min_context_tokens``)
# but defaults to 65536; a profile below it is a typed configuration failure,
# not a silent fall back to a short-context local model.
TERMINAL_MIN_CONTEXT_TOKENS = 65536

# A credential reference, never a credential value.
_SECRET_REF_RE = re.compile(r"^(env|file):[A-Za-z0-9_./~-]+$")
_RAW_SECRET_HINTS = re.compile(
    r"^(sk-|sk_|ghp_|glpat-|xox[baprs]-|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,})")


class ResolverError(Exception):
    """Typed resolver failure (never a fabricated success)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": False, "code": self.code, "detail": self.detail}


def is_secret_ref(value: str) -> bool:
    return bool(_SECRET_REF_RE.match(str(value or "")))


def looks_like_raw_secret(value: str) -> bool:
    v = str(value or "").strip()
    if not v:
        return False
    if is_secret_ref(v):
        return False
    return bool(_RAW_SECRET_HINTS.match(v)) or (
        len(v) >= 32 and re.fullmatch(r"[A-Za-z0-9_\-./+=]{32,}", v) is not None)


def _get(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default)
    except Exception:
        return default


def _policy(cfg: Any) -> FrontierPolicy:
    try:
        return FrontierPolicy.from_config(cfg)
    except Exception:
        return FrontierPolicy()


# ---------------------------------------------------------------------------
# Typed profile record
# ---------------------------------------------------------------------------
@dataclass
class ModelProfile:
    id: str
    kind: str = LOCAL
    provider: str = "ollama"
    model: str = DEFAULT_LOCAL_ID
    base_url: str = ""
    key_ref: str = ""               # "env:NAME" | "file:PATH" | "" (local)
    context_tokens: int = 4096
    supports_tools: bool = False
    supports_structured: bool = False
    # Honest default: only the local runner streams (the API/frontier
    # transport is request/response). Local profiles set this True in
    # ``default_profiles``; an operator may opt an external profile in when
    # its adapter really streams.
    supports_streaming: bool = False
    supports_vision: bool = False
    locality: str = "local"         # local | external
    egress_consent: str = "deny"    # deny | allow
    price_in_per_1k: float = 0.0
    price_out_per_1k: float = 0.0
    roles: List[str] = field(default_factory=lambda: [DEFAULT_ROLE])
    default: bool = False
    enabled: bool = True
    fallback_to: str = ""
    rollback_to: str = ""
    provenance: str = ""
    license: str = ""

    def validate(self, cfg: Any = None) -> Dict[str, str]:
        if not self.id:
            return {"state": "FAILED", "code": "MISSING_ID",
                    "detail": "profile needs an id"}
        if self.kind not in KINDS:
            return {"state": "FAILED", "code": "BAD_KIND",
                    "detail": f"unknown kind {self.kind!r}"}
        if not self.provider or not self.model:
            return {"state": "FAILED", "code": "MISSING_TARGET",
                    "detail": "profile needs provider and model"}
        if self.locality not in ("local", "external"):
            return {"state": "FAILED", "code": "BAD_LOCALITY",
                    "detail": f"bad locality {self.locality!r}"}
        if self.egress_consent not in ("deny", "allow"):
            return {"state": "FAILED", "code": "BAD_EGRESS",
                    "detail": f"bad egress_consent {self.egress_consent!r}"}
        if self.kind == LOCAL and self.locality != "local":
            return {"state": "FAILED", "code": "BAD_LOCALITY",
                    "detail": "local profile must have locality=local"}
        if self.kind in (API, REALTIME, PLUGIN) and self.locality != "external":
            return {"state": "FAILED", "code": "BAD_LOCALITY",
                    "detail": f"{self.kind} profile must have locality=external"}
        if self.key_ref:
            if looks_like_raw_secret(self.key_ref):
                return {"state": "FAILED", "code": "RAW_SECRET",
                        "detail": "key_ref must be an env:/file: reference, "
                                  "never a raw key value"}
            if not is_secret_ref(self.key_ref):
                return {"state": "FAILED", "code": "BAD_SECRET_REF",
                        "detail": "key_ref must be env:NAME or file:PATH"}
        if self.kind in (API, PLUGIN) and not self.base_url:
            return {"state": "FAILED", "code": "MISSING_BASE_URL",
                    "detail": f"{self.kind} profile needs a base_url"}
        if self.kind == API and not self.key_ref:
            return {"state": "FAILED", "code": "MISSING_SECRET_REF",
                    "detail": "api profile needs a secret reference"}
        # A provider plugin may be configured with an ``env:`` key reference
        # but is valid WITHOUT one (config-only): it is inert until the
        # referenced value is present at execution time, where the gateway
        # fails closed with AUTH_MISSING instead of storing/echoing a key.
        if self.default and self.kind != LOCAL:
            return {"state": "FAILED", "code": "EXTERNAL_DEFAULT",
                    "detail": "an external profile may not be the "
                              "shipped default"}
        return {"state": "OK", "code": "", "detail": "profile valid"}

    def to_record(self) -> Dict[str, Any]:
        rec = dict(self.__dict__)
        rec["schema"] = SCHEMA
        return rec

    @classmethod
    def from_record(cls, rec: Dict[str, Any]) -> "ModelProfile":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        data = {k: v for k, v in rec.items() if k in known}
        if isinstance(data.get("roles"), str):
            data["roles"] = [data["roles"]]
        return cls(**data)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Shipped defaults (data, not hard-wired policy)
# ---------------------------------------------------------------------------
def default_profiles() -> List[ModelProfile]:
    return [
        ModelProfile(
            id=DEFAULT_LOCAL_ID, kind=LOCAL, provider="ollama",
            model=DEFAULT_LOCAL_ID, locality="local", egress_consent="deny",
            context_tokens=4096, supports_streaming=True,
            roles=[r for r in ROLES if r != EMBEDDING], default=True,
            rollback_to=DEFAULT_LOCAL_ID,
            provenance="ollama://qubit-brain4 (shipped default record)",
            license="Apache-2.0 (Qwen3-4B-Instruct-2507-derived)"),
    ]


def _embedding_model(cfg: Any = None) -> str:
    """Effective embedding model id from canonical config, else the default.

    ``memory.semantic.embed_model`` wins over ``intent.router.embed_model`` so
    the same model is used however the caller reached the embedding role.
    """
    for path in (("memory", "semantic", "embed_model"),
                 ("intent", "router", "embed_model")):
        value = _get(cfg, *path)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return DEFAULT_EMBED_MODEL


def default_embedding_profile(cfg: Any = None) -> ModelProfile:
    """Synthetic local embedding default for the ``embedding`` role.

    Deliberately NOT part of :func:`default_profiles`/``load_profiles`` so the
    shipped model-profile inventory and count do not change; the embedding
    role still resolves through the canonical resolver and the shared local
    embedding adapter.
    """
    return ModelProfile(
        id=DEFAULT_EMBED_ID, kind=LOCAL, provider="ollama",
        model=_embedding_model(cfg), locality="local", egress_consent="deny",
        context_tokens=8192, supports_streaming=False,
        roles=[EMBEDDING], default=True, rollback_to=DEFAULT_EMBED_ID,
        provenance="ollama://nomic-embed-text (canonical embedding role)",
        license="Apache-2.0 (nomic-embed-text)")


def radius_profile(profile_id: str = RADIUS_PROVIDER, *,
                   model: str = "balanced", roles: Optional[List[str]] = None,
                   key_ref: str = RADIUS_KEY_REF,
                   base_url: str = RADIUS_BASE_URL,
                   egress_consent: str = "deny") -> ModelProfile:
    """Build a provider-plugin record for Qubit's Radius gateway adapter.

    Credentials are an ``env:``/``file:`` reference only (default
    ``env:RADIUS_API_KEY``); the record is valid without a present key
    (config-only) and refuses execution until the reference resolves.
    """
    return ModelProfile(
        id=profile_id, kind=PLUGIN, provider=RADIUS_PROVIDER, model=model,
        base_url=base_url, key_ref=key_ref, locality="external",
        egress_consent=egress_consent, supports_streaming=False,
        supports_tools=True, roles=list(roles or [DEFAULT_ROLE]),
        provenance="qubit://extensions/radius (@openclaw/radius-provider)",
        license="MIT (Qubit Foundation); see THIRD_PARTY_NOTICES.md")


# ---------------------------------------------------------------------------
# Persistence (same registry file; additive key)
# ---------------------------------------------------------------------------
def _registry_file(cfg: Any) -> Optional[Path]:
    try:
        from .. import profiles as _p
        return _p._registry_path(cfg)
    except Exception:
        return None


def _read_registry(cfg: Any) -> Dict[str, Any]:
    path = _registry_file(cfg)
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name,
                               suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def load_profiles(cfg: Any) -> List[ModelProfile]:
    """Shipped defaults plus user records (user record wins on id clash)."""
    records: Dict[str, ModelProfile] = {p.id: p for p in default_profiles()}
    reg = _read_registry(cfg)
    for rec in reg.get(REGISTRY_KEY, []) or []:
        if not isinstance(rec, dict):
            continue
        try:
            p = ModelProfile.from_record(rec)
        except (TypeError, ValueError):
            continue
        records[p.id] = p
    return list(records.values())


def _save_profiles(cfg: Any, profiles: List[ModelProfile]) -> Path:
    path = _registry_file(cfg)
    if path is None:
        raise ResolverError("NO_STATE_DIR", "cannot resolve the Qubit state dir")
    reg = _read_registry(cfg)
    reg[REGISTRY_KEY] = [p.to_record() for p in profiles if not p.default]
    reg.setdefault("version", 1)
    _atomic_json(path, reg)
    return path


def get_profile(cfg: Any, profile_id: str) -> Optional[ModelProfile]:
    for p in load_profiles(cfg):
        if p.id == profile_id:
            return p
    return None


def list_profiles(cfg: Any) -> Dict[str, Any]:
    policy = _policy(cfg)
    active_id = str(_read_active_record(cfg).get("profile", "") or "")
    items = []
    for p in load_profiles(cfg):
        gate = p.validate(cfg)
        overrides = _model_overrides(cfg, p.id)
        allowlisted = bool(overrides.get(
            "allowlisted", f"{p.provider}/{p.model}" in policy.allowlist))
        context_tokens = int(overrides.get("context_tokens")
                             or p.context_tokens or 0)
        items.append({
            "id": p.id, "kind": p.kind, "provider": p.provider,
            "model": p.model, "locality": p.locality,
            "egress_consent": p.egress_consent, "roles": p.roles,
            "default": p.default, "enabled": p.enabled,
            "active": p.id == active_id,
            "has_secret_ref": bool(p.key_ref),
            "base_url": p.base_url,
            "allowlisted": allowlisted,
            "gate": gate["state"], "gate_code": gate["code"],
            "rollback_to": p.rollback_to, "fallback_to": p.fallback_to,
            "context_tokens": context_tokens,
        })
    return {"schema": SCHEMA, "profiles": items,
            "active": active_id, "count": len(items), "dry_run": False}


def show_profile(cfg: Any, profile_id: str) -> Dict[str, Any]:
    p = get_profile(cfg, profile_id)
    if p is None:
        raise ResolverError("UNKNOWN_PROFILE", f"no model profile {profile_id!r}")
    gate = p.validate(cfg)
    eff = _effective(cfg, p, source="explicit")
    return {"schema": SCHEMA, "profile": p.to_record(),
            "gate": gate, "effective": eff.to_public_dict()}


# ---------------------------------------------------------------------------
# Lifecycle (create/clone/edit/remove/session override)
# ---------------------------------------------------------------------------
def upsert_model_profile(cfg: Any, profile: ModelProfile,
                         *, confirm: bool = False) -> Dict[str, Any]:
    """Create or replace a named model profile. dry_run unless confirmed."""
    gate = profile.validate(cfg)
    if gate["state"] != "OK":
        return {"state": gate["state"], "code": gate["code"],
                "detail": gate["detail"]}
    if profile.default and profile.id != DEFAULT_LOCAL_ID:
        return {"state": "DENIED", "code": "IMMUTABLE_DEFAULT",
                "detail": "only the shipped default may set default=true"}
    if not confirm:
        return {"state": "OK", "dry_run": True, "action": "would persist",
                "profile": profile.id}
    existing = {p.id: p for p in load_profiles(cfg)}
    existing[profile.id] = profile
    try:
        _save_profiles(cfg, list(existing.values()))
    except ResolverError as exc:
        return {"state": "FAILED", "code": exc.code, "detail": exc.detail}
    except OSError as exc:
        return {"state": "FAILED", "code": "WRITE_FAILED", "detail": str(exc)}
    return {"state": "OK", "profile": profile.id, "action": "persisted"}


def remove_model_profile(cfg: Any, profile_id: str, *,
                         confirm: bool = False) -> Dict[str, Any]:
    p = get_profile(cfg, profile_id)
    if p is None:
        raise ResolverError("UNKNOWN_PROFILE", f"no model profile {profile_id!r}")
    if p.default:
        raise ResolverError("IMMUTABLE_DEFAULT",
                            "the shipped default profile cannot be removed")
    if profile_id in set(_all_session_targets(cfg)):
        return {"state": "DENIED", "code": "PROFILE_IN_USE",
                "detail": "profile is selected by a session; clear that first"}
    if profile_id == str(_read_active_record(cfg).get("profile", "") or ""):
        return {"state": "DENIED", "code": "PROFILE_IN_USE",
                "detail": "profile is the persisted active selection; clear "
                          "that first"}
    if not confirm:
        return {"state": "OK", "dry_run": True, "action": "would remove",
                "profile": profile_id}
    remaining = [x for x in load_profiles(cfg) if x.id != profile_id or x.default]
    try:
        _save_profiles(cfg, remaining)
    except OSError as exc:
        return {"state": "FAILED", "code": "WRITE_FAILED", "detail": str(exc)}
    return {"state": "OK", "profile": profile_id, "action": "removed"}


def clone_model_profile(cfg: Any, source_id: str, new_id: str, *,
                        confirm: bool = False) -> Dict[str, Any]:
    src = get_profile(cfg, source_id)
    if src is None:
        raise ResolverError("UNKNOWN_PROFILE", f"no model profile {source_id!r}")
    if get_profile(cfg, new_id) is not None:
        raise ResolverError("PROFILE_EXISTS", f"profile {new_id!r} already exists")
    rec = src.to_record()
    rec.update({"id": new_id, "default": False, "rollback_to": source_id})
    clone = ModelProfile.from_record(rec)
    return upsert_model_profile(cfg, clone, confirm=confirm)


# ---------------------------------------------------------------------------
# Session overrides
# ---------------------------------------------------------------------------
def _sessions_path(cfg: Any) -> Path:
    state = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(".")
    return Path(state) / SESSIONS_FILE


def _read_sessions(cfg: Any) -> Dict[str, Any]:
    path = _sessions_path(cfg)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _iter_session_overrides(cfg: Any) -> Dict[Tuple[str, str], str]:
    """Model-role session overrides only.

    Speech profiles share the same session-override file under their own role
    keys (``speech_in``/``speech_out``/``realtime``); filtering to the model
    roles here prevents a speech selection from being mistaken for a model
    selection (e.g. a false ``PROFILE_IN_USE`` on model-profile removal).
    """
    out: Dict[Tuple[str, str], str] = {}
    for sid, roles in (_read_sessions(cfg).get("sessions") or {}).items():
        if isinstance(roles, dict):
            for role, prof in roles.items():
                if role in ROLES or role == "*":
                    if isinstance(prof, str) and prof:
                        out[(str(sid), str(role))] = prof
    return out


def _all_session_targets(cfg: Any) -> List[str]:
    return list(_iter_session_overrides(cfg).values())


def session_overrides(cfg: Any) -> Dict[str, Any]:
    return {"schema": SESSIONS_SCHEMA,
            "sessions": _read_sessions(cfg).get("sessions", {})}


def set_session_profile(cfg: Any, session: str, profile_id: str, *,
                        role: str = DEFAULT_ROLE,
                        confirm: bool = False) -> Dict[str, Any]:
    p = get_profile(cfg, profile_id)
    if p is None:
        raise ResolverError("UNKNOWN_PROFILE", f"no model profile {profile_id!r}")
    sid = str(session or "").strip()
    if not sid:
        raise ResolverError("MALFORMED", "session id required")
    if role not in ROLES and role != "*":
        raise ResolverError("BAD_ROLE", f"unknown role {role!r}")
    if not confirm:
        return {"state": "OK", "dry_run": True, "session": sid,
                "role": role, "profile": profile_id}
    data = _read_sessions(cfg)
    sessions = data.setdefault("sessions", {})
    sessions.setdefault(sid, {})[role] = profile_id
    data["schema"] = SESSIONS_SCHEMA
    _atomic_json(_sessions_path(cfg), data)
    return {"state": "OK", "session": sid, "role": role,
            "profile": profile_id, "action": "selected"}


def clear_session_profile(cfg: Any, session: str, *,
                          role: str = DEFAULT_ROLE) -> Dict[str, Any]:
    sid = str(session or "").strip()
    data = _read_sessions(cfg)
    sessions = data.get("sessions") or {}
    changed = False
    if sid in sessions:
        if role == "*":
            sessions.pop(sid, None)
            changed = True
        elif isinstance(sessions.get(sid), dict) and role in sessions[sid]:
            sessions[sid].pop(role, None)
            changed = True
            if not sessions[sid]:
                sessions.pop(sid, None)
    if changed:
        data["sessions"] = sessions
        data["schema"] = SESSIONS_SCHEMA
        _atomic_json(_sessions_path(cfg), data)
    return {"state": "OK", "session": sid, "role": role, "changed": changed}


# ---------------------------------------------------------------------------
# Persisted active selection (set-active / get-active / clear-active)
# ---------------------------------------------------------------------------
def _read_active_record(cfg: Any) -> Dict[str, Any]:
    rec = _read_registry(cfg).get(ACTIVE_KEY)
    return rec if isinstance(rec, dict) else {}


def _write_active_record(cfg: Any, rec: Optional[Dict[str, Any]]) -> Path:
    path = _registry_file(cfg)
    if path is None:
        raise ResolverError("NO_STATE_DIR", "cannot resolve the Qubit state dir")
    reg = _read_registry(cfg)
    if rec is None:
        reg.pop(ACTIVE_KEY, None)
    else:
        reg[ACTIVE_KEY] = rec
    reg.setdefault("version", 1)
    _atomic_json(path, reg)
    return path


def get_active_profile(cfg: Any) -> Dict[str, Any]:
    """The persisted active provider/model profile (typed read, never raises)."""
    rec = _read_active_record(cfg)
    pid = str(rec.get("profile", "") or "")
    role = str(rec.get("role", "") or DEFAULT_ROLE)
    if not pid:
        return {"schema": ACTIVE_SCHEMA, "state": "UNSET", "active": "",
                "role": role}
    p = get_profile(cfg, pid)
    if p is None:
        return {"schema": ACTIVE_SCHEMA, "state": "MISSING", "active": pid,
                "role": role, "code": "UNKNOWN_PROFILE",
                "detail": f"active profile {pid!r} is no longer registered"}
    gate = p.validate(cfg)
    return {"schema": ACTIVE_SCHEMA, "state": "OK" if gate["state"] == "OK"
            else "DEGRADED", "active": pid, "role": role,
            "profile": {"id": p.id, "kind": p.kind, "provider": p.provider,
                        "model": p.model, "locality": p.locality,
                        "egress_consent": p.egress_consent,
                        "enabled": p.enabled, "default": p.default,
                        "gate": gate["state"], "gate_code": gate["code"]}}


def set_active_profile(cfg: Any, profile_id: str, *,
                       role: str = DEFAULT_ROLE,
                       confirm: bool = False) -> Dict[str, Any]:
    """Persist the active provider/model profile (confirm-gated).

    This is the canonical selection shared by CLI, dashboard, and voice: the
    resolver consults it as the default after an explicit request or session
    override. A missing/disabled/invalid profile is a typed refusal and the
    previous selection is left unchanged (never a silent substitution).
    """
    pid = str(profile_id or "").strip()
    if not pid:
        raise ResolverError("MALFORMED", "profile id required")
    if role not in ROLES and role != "*":
        raise ResolverError("BAD_ROLE", f"unknown role {role!r}")
    p = get_profile(cfg, pid)
    if p is None:
        raise ResolverError("UNKNOWN_PROFILE", f"no model profile {pid!r}")
    if not p.enabled:
        raise ResolverError("PROFILE_DISABLED", f"profile {p.id!r} disabled")
    gate = p.validate(cfg)
    if gate["state"] != "OK":
        return {"state": gate["state"], "code": gate["code"],
                "detail": gate["detail"], "active": pid, "role": role}
    if not confirm:
        return {"state": "OK", "dry_run": True, "action": "would set active",
                "active": pid, "role": role}
    _write_active_record(cfg, {"schema": ACTIVE_SCHEMA, "role": role,
                               "profile": pid})
    return {"state": "OK", "active": pid, "role": role,
            "action": "set-active"}


def clear_active_profile(cfg: Any, *, role: str = DEFAULT_ROLE,
                         confirm: bool = False) -> Dict[str, Any]:
    """Clear the persisted active selection (dry-run unless confirmed)."""
    rec = _read_active_record(cfg)
    current = str(rec.get("profile", "") or "")
    if not current:
        return {"state": "OK", "active": "", "role": role,
                "action": "already-unset"}
    if not confirm:
        return {"state": "OK", "dry_run": True, "action": "would clear active",
                "active": current, "role": role}
    _write_active_record(cfg, None)
    return {"state": "OK", "active": "", "role": role, "action": "cleared"}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EffectiveProfile:
    profile_id: str
    provider: str
    model: str
    kind: str
    route: str
    locality: str
    source: str
    egress_required: bool
    egress_allowed: bool
    allowlisted: bool
    policy_enabled: bool
    executable: bool
    capabilities: Dict[str, bool]
    budgets: Dict[str, float]
    fallback_chain: Tuple[str, ...]
    rollback_to: str
    # The profile's own endpoint and credential *reference* (never a key
    # value). Forwarded so a selected API profile is executed against the
    # endpoint/credential it declares instead of the policy provider default.
    base_url: str = ""
    key_ref: str = ""
    # Declared context window for the profile (0 when unknown). The terminal
    # surface enforces a minimum against this.
    context_tokens: int = 0

    @property
    def is_local(self) -> bool:
        return self.locality == "local"

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id, "provider": self.provider,
            "model": self.model, "kind": self.kind, "route": self.route,
            "locality": self.locality, "source": self.source,
            "egress_required": self.egress_required,
            "egress_allowed": self.egress_allowed,
            "allowlisted": self.allowlisted,
            "policy_enabled": self.policy_enabled,
            "executable": self.executable,
            "capabilities": dict(self.capabilities),
            "budgets": dict(self.budgets),
            "fallback_chain": list(self.fallback_chain),
            "rollback_to": self.rollback_to,
            "base_url": self.base_url,
            "key_ref": self.key_ref,
            "context_tokens": int(self.context_tokens or 0),
        }


def _fallback_chain(cfg: Any, profile: ModelProfile) -> Tuple[str, ...]:
    chain: List[str] = []
    seen = {profile.id}
    cur = profile
    while cur.fallback_to and cur.fallback_to not in seen:
        nxt = get_profile(cfg, cur.fallback_to)
        if nxt is None:
            break
        seen.add(nxt.id)
        chain.append(nxt.id)
        cur = nxt
        if len(chain) >= 8:
            break
    return tuple(chain)


def _model_overrides(cfg: Any, profile_id: str) -> Dict[str, Any]:
    """Validated per-profile selection overrides, or ``{}`` (fail-closed).

    Delegates to :func:`voicebridge.config.model_profile_overrides`; a config
    without the key (or any read error) leaves global policy/allowlist and the
    profile's declared context untouched.
    """
    try:
        from .. import config as _config
        return _config.model_profile_overrides(cfg, profile_id)
    except Exception:  # noqa: BLE001 - a missing override is the safe default
        return {}


def _model_token(raw: Any) -> Optional[str]:
    """Extract a model id from a config ``model`` setting shape."""
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("default", "model", "name"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


#: (path -> (mtime_ns, token)) so a resolution hot path does not re-parse the
#: runtime config on every call; the mtime guard keeps an edit immediately live.
_RUNTIME_MODEL_CACHE: Dict[str, Tuple[int, Optional[str]]] = {}
_RUNTIME_MODEL_LOCK = threading.Lock()


def _runtime_configured_model() -> Optional[str]:
    """Configured default model from the Qubit-owned runtime ``config.yaml``.

    The reference resolves its default from the config's ``model`` section
    (``default``/``name`` + ``provider``). Qubit's vendored runtime keeps that
    setting in its own home (``<state>/qubit-voicebridge/runtime/config.yaml``),
    which the dashboard ``/api/config`` surface serves verbatim and the TUI
    reads. Reading it here is what lets the resolver, ``/api/model/info`` and
    ``/qubit-chat`` turns agree with that config surface instead of reporting a
    stale shipped default. Uses the XDG-derived runtime home only — never the
    operator's external ``~/.hermes``. Best-effort: any failure yields ``None``.
    """
    try:
        from .. import qubit_runtime as _qr

        path = _qr.default_home() / "config.yaml"
        if not path.is_file():
            return None
        key = str(path)
        try:
            mtime = int(path.stat().st_mtime_ns)
        except OSError:
            return None
        with _RUNTIME_MODEL_LOCK:
            hit = _RUNTIME_MODEL_CACHE.get(key)
            if hit is not None and hit[0] == mtime:
                return hit[1]
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        token = _model_token(data.get("model")) if isinstance(data, dict) else None
        with _RUNTIME_MODEL_LOCK:
            _RUNTIME_MODEL_CACHE[key] = (mtime, token)
        return token
    except Exception:  # noqa: BLE001 - absent/unreadable config is not fatal
        return None


def configured_default_model(cfg: Any) -> Optional[str]:
    """Operator-configured default chat model, or ``None``.

    Reads the canonical Qubit config shapes the dashboard config surface
    reports: the legacy ``agent.model`` override, then the vendored-style
    ``model`` setting (a bare string, or a mapping carrying
    ``default``/``model``/``name``), then the Qubit-owned runtime
    ``config.yaml`` ``model`` default (the reference's resolution, and the same
    source ``/api/config`` serves). The value is a *candidate*: the resolver
    only returns it when it maps to a registered, role-eligible profile, so a
    configured default can never be reported by one surface while the turn
    path silently refuses or substitutes another model.
    """
    if cfg is None:
        return None
    try:
        explicit = _get(cfg, "agent", "model")
    except Exception:  # noqa: BLE001 - a get-less stand-in has no setting
        explicit = None
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    try:
        raw = _get(cfg, "model")
    except Exception:  # noqa: BLE001
        raw = None
    token = _model_token(raw)
    if token:
        return token
    return _runtime_configured_model()


def _profile_for_model(cfg: Any, token: str,
                       role: str) -> Optional[ModelProfile]:
    """Match a configured model id/name to a registered, role-eligible profile."""
    token = str(token or "").strip()
    if not token:
        return None
    for profile in load_profiles(cfg):
        if not profile.enabled:
            continue
        if role not in (profile.roles or [role]):
            continue
        if profile.id == token or profile.model == token:
            return profile
    return None


def _effective(cfg: Any, profile: ModelProfile, *, source: str
               ) -> EffectiveProfile:
    policy = _policy(cfg)
    overrides = _model_overrides(cfg, profile.id)
    allowlisted = bool(overrides.get(
        "allowlisted", f"{profile.provider}/{profile.model}" in policy.allowlist))
    policy_enabled = bool(overrides.get("policy_enabled", policy.enabled))
    context_tokens = int(overrides.get("context_tokens")
                         or profile.context_tokens or 0)
    if profile.kind == LOCAL:
        route = "local"
        egress_required = False
        egress_allowed = True
        executable = profile.enabled
    elif profile.kind == REALTIME:
        route = "realtime"
        egress_required = True
        egress_allowed = profile.egress_consent == "allow"
        executable = bool(profile.enabled and egress_allowed)
    else:
        route = "frontier"
        egress_required = True
        egress_allowed = profile.egress_consent == "allow"
        executable = bool(profile.enabled and egress_allowed
                          and policy_enabled and allowlisted)
    return EffectiveProfile(
        profile_id=profile.id, provider=profile.provider, model=profile.model,
        kind=profile.kind, route=route, locality=profile.locality,
        source=source, egress_required=egress_required,
        egress_allowed=egress_allowed, allowlisted=allowlisted,
        policy_enabled=policy_enabled, executable=executable,
        capabilities={
            "tools": profile.supports_tools,
            "structured": profile.supports_structured,
            "streaming": profile.supports_streaming,
            "vision": profile.supports_vision,
        },
        budgets={
            "max_tokens": float(policy.max_tokens),
            "max_context_chars": float(policy.max_context_chars),
            "daily_usd": float(policy.daily_usd),
            "monthly_usd": float(policy.monthly_usd),
            "timeout_s": float(policy.timeout_s),
        },
        fallback_chain=_fallback_chain(cfg, profile),
        rollback_to=profile.rollback_to,
        base_url=str(profile.base_url or "").strip(),
        key_ref=str(profile.key_ref or "").strip(),
        context_tokens=context_tokens)


def _pick(cfg: Any, *, role: str, profile_id: Optional[str],
          session: Optional[str]) -> Tuple[ModelProfile, str]:
    if profile_id:
        p = get_profile(cfg, profile_id)
        if p is None:
            raise ResolverError("UNKNOWN_PROFILE",
                                f"no model profile {profile_id!r}")
        if not p.enabled:
            raise ResolverError("PROFILE_DISABLED", f"profile {p.id!r} disabled")
        return p, "explicit"
    if session:
        ov = _iter_session_overrides(cfg)
        for key in ((str(session), role), (str(session), "*")):
            pid = ov.get(key)
            if pid:
                p = get_profile(cfg, pid)
                if p is None:
                    raise ResolverError(
                        "SESSION_PROFILE_MISSING",
                        f"session {session!r} selects missing profile {pid!r}")
                if p.enabled:
                    return p, "session"
    # Persisted owner "active" selection (canonical set-active op shared by
    # CLI, dashboard, and voice). Explicit requests and session overrides win;
    # a missing/disabled active profile falls through rather than breaking the
    # default path.
    active = _read_active_record(cfg)
    apid = str(active.get("profile", "") or "")
    if apid and str(active.get("role", "") or DEFAULT_ROLE) in (role, "*"):
        p = get_profile(cfg, apid)
        if p is not None and p.enabled:
            return p, "active"
    # The embedding role is a local embedding adapter, not the generative
    # default. Resolve it canonically without adding a second shipped profile
    # record (keeps the model-profile inventory unchanged).
    if role == EMBEDDING:
        return default_embedding_profile(cfg), "embedding-default"
    # Operator-configured default (the reference's resolution): the config's
    # ``model`` default wins over the shipped default, but only when it maps to
    # a registered, role-eligible profile — a configured default can never be
    # silently substituted into an unregistered model or bypass egress policy.
    configured = configured_default_model(cfg)
    if configured:
        p = _profile_for_model(cfg, configured, role)
        if p is not None and p.enabled:
            return p, "configured-default"
    for p in load_profiles(cfg):
        if p.default and role in p.roles and p.enabled:
            return p, "role-default"
    for p in default_profiles():
        if p.default:
            return p, "shipped-default"
    raise ResolverError("NO_DEFAULT", "no default model profile available")


def resolve(cfg: Any, *, role: str = DEFAULT_ROLE,
            profile_id: Optional[str] = None,
            session: Optional[str] = None) -> EffectiveProfile:
    """Resolve one effective model/provider profile. Raises ResolverError."""
    if role not in ROLES:
        raise ResolverError("BAD_ROLE", f"unknown role {role!r}")
    profile, source = _pick(cfg, role=role, profile_id=profile_id,
                            session=session)
    return _effective(cfg, profile, source=source)


def resolve_embedding(cfg: Any, profile_id: Optional[str] = None, *,
                      session: Optional[str] = None) -> EffectiveProfile:
    """Resolve the effective embedding profile (canonical embedding role)."""
    return resolve(cfg, role=EMBEDDING, profile_id=profile_id,
                   session=session)


def resolve_model(cfg: Any, requested: Optional[str] = None, *,
                  role: str = DEFAULT_ROLE,
                  session: Optional[str] = None) -> Dict[str, Any]:
    """Chat-transport compatible resolution (typed dict, no exception)."""
    requested_id = (requested or "").strip() or None
    try:
        eff = resolve(cfg, role=role, profile_id=requested_id, session=session)
    except ResolverError as exc:
        return {"ok": False, "code": exc.code, "detail": exc.detail}
    out = {"ok": True, "model": eff.model, "provider": eff.provider,
           "profile_id": eff.profile_id, "kind": eff.kind, "route": eff.route,
           "locality": eff.locality, "executable": eff.executable,
           "egress_required": eff.egress_required,
           "egress_allowed": eff.egress_allowed,
           "source": eff.source,
           "context_tokens": int(eff.context_tokens or 0)}
    if not eff.executable:
        out["ok"] = False
        out["code"] = ("EGRESS_DENIED" if eff.egress_required
                       and not eff.egress_allowed else "PROFILE_NOT_EXECUTABLE")
        out["detail"] = (
            f"profile {eff.profile_id!r} is not executable: "
            f"policy_enabled={eff.policy_enabled} allowlisted={eff.allowlisted} "
            f"egress_allowed={eff.egress_allowed}")
    return out


def terminal_min_context_tokens(cfg: Any) -> int:
    """Effective /chat context floor (``terminal.min_context_tokens``)."""
    raw = _get(cfg, "terminal", "min_context_tokens", default=None)
    if raw is None:
        return TERMINAL_MIN_CONTEXT_TOKENS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return TERMINAL_MIN_CONTEXT_TOKENS
    return value if value > 0 else TERMINAL_MIN_CONTEXT_TOKENS


CONTEXT_VERIFICATION_MODES = ("when_available", "required", "declared")


def terminal_context_verification(cfg: Any) -> str:
    """How ``/chat`` cross-checks a profile's *declared* context window.

    * ``when_available`` (default) cross-checks the declared value against the
      vendored resolver's real window for the model. A resolved window below the
      floor is a typed refusal; when no window can be resolved (offline, never
      used yet) the declared value is accepted but flagged
      ``context_verified=False`` so the choice is auditable, not silent.
    * ``required`` additionally refuses (``TERMINAL_CONTEXT_UNVERIFIED``) when no
      real window can be resolved — strict fail-closed.
    * ``declared`` restores the legacy trust-the-profile behavior (explicit,
      auditable opt-out).

    ``terminal.context_window_verification`` selects the mode; unknown values
    fail safe to ``when_available``.
    """
    raw = str(_get(cfg, "terminal", "context_window_verification",
                   default="") or "").strip().lower()
    return raw if raw in CONTEXT_VERIFICATION_MODES else "when_available"


#: Hard bound on the live context-window probe so a selected profile pointing
#: at a black-holed endpoint can never turn a working ``/chat`` open into a hang.
_CONTEXT_PROBE_TIMEOUT_S = 3.0


def _probe_real_context_window(model: str, base_url: str, provider: str,
                               *, timeout_s: float = _CONTEXT_PROBE_TIMEOUT_S
                               ) -> Tuple[Optional[int], str]:
    """Run the vendored live resolution under a hard time bound.

    A daemon thread (not an executor: a thread wedged in a connect timeout must
    never delay interpreter exit) performs ``get_model_context_length``; on
    timeout the caller gets an honest ``(None, "probe-timeout")`` and the guard
    stays in its declared-but-unverified state rather than blocking.
    """
    try:
        from agent import model_metadata as _mm  # type: ignore
    except Exception as exc:  # noqa: BLE001 - optional capability
        return None, f"resolver-unavailable:{type(exc).__name__}"

    box: Dict[str, Any] = {}

    def _run() -> None:
        try:
            box["value"] = _mm.get_model_context_length(
                model, base_url=base_url, provider=provider)
        except Exception as exc:  # noqa: BLE001 - typed below
            box["error"] = type(exc).__name__

    thread = threading.Thread(target=_run, name="qubit-context-probe",
                              daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        return None, "probe-timeout"
    if "error" in box:
        return None, f"probe-failed:{box['error']}"
    resolved = box.get("value")
    if not (isinstance(resolved, int) and resolved > 0):
        return None, "unresolved"
    # ``get_model_context_length`` is documented to fall back to a generic
    # 256K default when NO real provider/model metadata resolves. That value is
    # not a real window, so trusting it would let an unknown model satisfy
    # ``required`` mode's strict fail-closed check. Treat the generic fallback
    # as unverified; the declared value stays flagged (``context_verified=False``)
    # and ``required`` mode refuses it. A cached value never hits this path:
    # the fallback is returned, not persisted, so a cache hit is real metadata.
    if resolved == int(getattr(_mm, "DEFAULT_FALLBACK_CONTEXT", 0) or 0):
        return None, "fallback"
    return resolved, "resolved"


def _real_context_window(eff: "EffectiveProfile", *,
                         live: bool = False) -> Tuple[Optional[int], str]:
    """Best-effort real model context window from the vendored resolver.

    Returns ``(tokens, source)`` when a positive window is resolved, else
    ``(None, reason)``. Never raises and never mutates the checkout.

    The persistent context cache is consulted first (cheap). On a cache miss the
    real window is resolved through the vendored resolver — the reference's
    behavior, so ``/chat`` reports the window the runtime actually uses instead
    of a permanently ``not-cached`` declared value. The resolution is hard
    time-bounded, so a failing/absent network yields an honest unverified
    result rather than hanging the guard. ``live`` is accepted for callers that
    want to make the cross-check intent explicit; the bounded probe runs either
    way (``required`` mode additionally fails closed on an unverified window).
    """
    model = str(getattr(eff, "model", "") or "")
    base_url = str(getattr(eff, "base_url", "") or "")
    provider = str(getattr(eff, "provider", "") or "")
    if not model:
        return None, "no-model"
    try:
        from .. import qubit_runtime
        qubit_runtime.ensure_path()
        from agent import model_metadata as _mm  # type: ignore
    except Exception as exc:  # noqa: BLE001 - optional capability
        return None, f"resolver-unavailable:{type(exc).__name__}"
    try:
        cached = _mm.get_cached_context_length(model, base_url)
    except Exception:  # noqa: BLE001
        cached = None
    if isinstance(cached, int) and cached > 0:
        return cached, "cache"
    return _probe_real_context_window(model, base_url, provider)


def terminal_context_guard(cfg: Any, *, profile_id: Optional[str] = None,
                           session: Optional[str] = None) -> Dict[str, Any]:
    """Fail-closed preflight for the ``/chat`` long-context terminal surface.

    Resolves the effective chat profile (honouring ``terminal.profile`` when
    set, then explicit / session / active / default selection) and requires its
    declared context window to meet ``terminal.min_context_tokens``. It never
    falls back to a shorter-context model: a below-floor or non-executable
    profile returns a typed configuration failure the caller surfaces.
    """
    minimum = terminal_min_context_tokens(cfg)
    requested = (profile_id or "").strip() or None
    if requested is None:
        configured = _get(cfg, "terminal", "profile", default=None)
        requested = str(configured or "").strip() or None
    try:
        eff = resolve(cfg, role=DEFAULT_ROLE, profile_id=requested,
                      session=session)
    except ResolverError as exc:
        # Typed refusal, never a silent substitution: the requested id is
        # echoed (non-secret) so the caller can journal which selection failed
        # even when it named no known Qubit model profile.
        return {"ok": False, "code": exc.code, "detail": exc.detail,
                "profile_id": requested or "",
                "requested_profile": requested or "",
                "min_context_tokens": minimum}
    context = int(getattr(eff, "context_tokens", 0) or 0)
    base: Dict[str, Any] = {
        "profile_id": eff.profile_id, "provider": eff.provider,
        "model": eff.model, "context_tokens": context,
        "min_context_tokens": minimum, "source": eff.source,
        "kind": eff.kind, "locality": eff.locality,
        "base_url": str(getattr(eff, "base_url", "") or ""),
        "key_ref": str(getattr(eff, "key_ref", "") or ""),
    }
    if context < minimum:
        base.update({
            "ok": False,
            "code": "TERMINAL_CONTEXT_BELOW_MINIMUM",
            "detail": (
                f"/chat requires a model with at least {minimum} context "
                f"tokens, but profile {eff.profile_id!r} "
                f"({eff.provider}/{eff.model}) declares {context}. Select an "
                f"API provider/model with enough context (for example OpenCode "
                f"or OpenRouter) via 'qubit profile model' or the dashboard; "
                f"terminal.min_context_tokens changes the floor."),
        })
        return base
    if not eff.executable:
        base.update({
            "ok": False,
            "code": "TERMINAL_PROFILE_NOT_EXECUTABLE",
            "detail": (
                f"/chat profile {eff.profile_id!r} is not executable "
                f"(policy_enabled={eff.policy_enabled} "
                f"allowlisted={eff.allowlisted} "
                f"egress_allowed={eff.egress_allowed}); configure egress "
                f"consent/allowlist or select another profile."),
        })
        return base
    # Cross-check the declared window against the real model window. A profile
    # declaring >= the floor on paper must not open /chat if the real model
    # window is smaller; and an unverifiable window is reported (not silently
    # trusted) so the operator can see the difference.
    mode = terminal_context_verification(cfg)
    base["context_verification"] = mode
    if mode != "declared":
        real, real_src = _real_context_window(eff, live=(mode == "required"))
        base["context_window_source"] = real_src
        if real is not None:
            # Honest, runtime-consistent report: the resolved real window is
            # what the runtime serves, so surface it as the effective window
            # (the declared profile value is kept alongside for audit).
            base["real_context_tokens"] = real
            base["context_verified"] = True
            base["declared_context_tokens"] = context
            base["context_tokens"] = real
            if real < minimum:
                base.update({
                    "ok": False,
                    "code": "TERMINAL_CONTEXT_BELOW_MINIMUM",
                    "context_tokens": real,
                    "declared_context_tokens": context,
                    "detail": (
                        f"/chat profile {eff.profile_id!r} "
                        f"({eff.provider}/{eff.model}) declares {context} "
                        f"context tokens, but the resolved model window is "
                        f"{real}, below the required {minimum}. Select a model "
                        f"whose real window meets the floor; "
                        f"terminal.min_context_tokens changes the floor."),
                })
                return base
        elif mode == "required":
            base.update({
                "ok": False,
                "code": "TERMINAL_CONTEXT_UNVERIFIED",
                "declared_context_tokens": context,
                "context_verified": False,
                "detail": (
                    f"/chat profile {eff.profile_id!r} "
                    f"({eff.provider}/{eff.model}) declares {context} context "
                    f"tokens, but the real model window could not be resolved "
                    f"({real_src}); terminal.context_window_verification="
                    f"required refuses unverified windows."),
            })
            return base
        else:
            base["context_verified"] = False
    base["ok"] = True
    return base


def test_profile(cfg: Any, profile_id: str, *,
                 probe: bool = False) -> Dict[str, Any]:
    """Validate (+ optional reachability probe) without executing a task."""
    p = get_profile(cfg, profile_id)
    if p is None:
        raise ResolverError("UNKNOWN_PROFILE", f"no model profile {profile_id!r}")
    gate = p.validate(cfg)
    eff = _effective(cfg, p, source="test")
    out: Dict[str, Any] = {"schema": SCHEMA, "profile": profile_id,
                           "gate": gate, "effective": eff.to_public_dict(),
                           "state": gate["state"] if gate["state"] == "FAILED"
                           else ("OK" if eff.executable else "DEGRADED")}
    if p.kind == LOCAL:
        try:
            from ..runner_contract import resolve as _runner
            rc = _runner(cfg)
            out["runner"] = {"alias": rc.alias, "url": rc.url,
                             "num_ctx": rc.num_ctx}
        except Exception as exc:  # noqa: BLE001
            out["runner_error"] = str(exc)[:160]
    if probe and p.kind == LOCAL:
        try:
            from ..local_llm import chat_local
            from ..runner_contract import resolve as _runner
            rc = _runner(cfg)
            txt = chat_local("ping", model=rc.alias, url=rc.url,
                             timeout_s=8.0, max_tokens=4)
            out["reachable"] = bool((txt or "").strip())
        except Exception as exc:  # noqa: BLE001
            out["reachable"] = False
            out["probe_error"] = str(exc)[:160]
    return out


# ---------------------------------------------------------------------------
# Speech-in / speech-out / realtime profiles (same canonical resolver surface)
# ---------------------------------------------------------------------------
# The speech registry lives in :mod:`voicebridge.providers.speech` (same
# registry file, additive keys). These thin wrappers make the resolver the one
# import surface for every profile kind; the import is lazy so the two modules
# never form an import cycle.
def _speech_mod():
    from . import speech
    return speech


def list_speech_profiles(cfg: Any, kind: Optional[str] = None) -> Dict[str, Any]:
    return _speech_mod().list_speech_profiles(cfg, kind)


def show_speech_profile(cfg: Any, profile_id: str) -> Dict[str, Any]:
    return _speech_mod().show_speech_profile(cfg, profile_id)


def upsert_speech_profile(cfg: Any, profile: Any, *,
                          confirm: bool = False) -> Dict[str, Any]:
    return _speech_mod().upsert_speech_profile(cfg, profile, confirm=confirm)


def remove_speech_profile(cfg: Any, profile_id: str, *,
                          confirm: bool = False) -> Dict[str, Any]:
    return _speech_mod().remove_speech_profile(cfg, profile_id, confirm=confirm)


def clone_speech_profile(cfg: Any, source_id: str, new_id: str, *,
                         confirm: bool = False) -> Dict[str, Any]:
    return _speech_mod().clone_speech_profile(cfg, source_id, new_id,
                                              confirm=confirm)


def resolve_speech_in(cfg: Any, profile_id: Optional[str] = None, *,
                      session: Optional[str] = None) -> Any:
    return _speech_mod().resolve_speech_in(cfg, profile_id, session=session)


def resolve_speech_out(cfg: Any, profile_id: Optional[str] = None, *,
                       session: Optional[str] = None) -> Any:
    return _speech_mod().resolve_speech_out(cfg, profile_id, session=session)


def resolve_realtime(cfg: Any, profile_id: Optional[str] = None, *,
                     session: Optional[str] = None) -> Any:
    return _speech_mod().resolve_realtime(cfg, profile_id, session=session)


__all__ = [
    "LOCAL", "API", "REALTIME", "PLUGIN", "KINDS", "ROLES", "DEFAULT_ROLE",
    "EMBEDDING", "DEFAULT_LOCAL_ID", "DEFAULT_EMBED_ID", "DEFAULT_EMBED_MODEL",
    "RADIUS_PROVIDER", "RADIUS_BASE_URL", "RADIUS_KEY_REF",
    "ResolverError", "ModelProfile", "EffectiveProfile",
    "default_profiles", "default_embedding_profile", "radius_profile",
    "load_profiles", "get_profile", "list_profiles",
    "show_profile", "upsert_model_profile", "remove_model_profile",
    "clone_model_profile", "session_overrides", "set_session_profile",
    "clear_session_profile", "get_active_profile", "set_active_profile",
    "clear_active_profile",
    "resolve", "resolve_model", "resolve_embedding",
    "test_profile",
    "is_secret_ref", "looks_like_raw_secret",
    "list_speech_profiles", "show_speech_profile", "upsert_speech_profile",
    "remove_speech_profile", "clone_speech_profile", "resolve_speech_in",
    "resolve_speech_out", "resolve_realtime",
]
