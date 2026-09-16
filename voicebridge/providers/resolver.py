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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .policy import FrontierPolicy

LOCAL, API, REALTIME = "local", "api", "realtime"
KINDS = (LOCAL, API, REALTIME)
ROLES = ("chat", "hard", "coding", "tool", "summarize", "vision", "classify")
DEFAULT_ROLE = "chat"
DEFAULT_LOCAL_ID = "qubit-brain4"
REGISTRY_KEY = "model_profiles"
SESSIONS_FILE = "model_profile_sessions.json"
SCHEMA = "qubit-model-profile/1"
SESSIONS_SCHEMA = "qubit-model-profile-sessions/1"

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
        if self.kind in (API, REALTIME) and self.locality != "external":
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
        if self.kind == API and not self.base_url:
            return {"state": "FAILED", "code": "MISSING_BASE_URL",
                    "detail": "api profile needs a base_url"}
        if self.kind == API and not self.key_ref:
            return {"state": "FAILED", "code": "MISSING_SECRET_REF",
                    "detail": "api profile needs a secret reference"}
        if self.default and self.kind == API:
            return {"state": "FAILED", "code": "EXTERNAL_DEFAULT",
                    "detail": "an external API profile may not be the "
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
            roles=list(ROLES), default=True, rollback_to=DEFAULT_LOCAL_ID,
            provenance="ollama://qubit-brain4 (shipped default record)",
            license="Apache-2.0 (Qwen3-4B-Instruct-2507-derived)"),
    ]


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
    items = []
    for p in load_profiles(cfg):
        gate = p.validate(cfg)
        items.append({
            "id": p.id, "kind": p.kind, "provider": p.provider,
            "model": p.model, "locality": p.locality,
            "egress_consent": p.egress_consent, "roles": p.roles,
            "default": p.default, "enabled": p.enabled,
            "has_secret_ref": bool(p.key_ref),
            "base_url": p.base_url,
            "allowlisted": f"{p.provider}/{p.model}" in policy.allowlist,
            "gate": gate["state"], "gate_code": gate["code"],
            "rollback_to": p.rollback_to, "fallback_to": p.fallback_to,
        })
    return {"schema": SCHEMA, "profiles": items,
            "count": len(items), "dry_run": False}


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


def _effective(cfg: Any, profile: ModelProfile, *, source: str
               ) -> EffectiveProfile:
    policy = _policy(cfg)
    allowlisted = f"{profile.provider}/{profile.model}" in policy.allowlist
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
                          and policy.enabled and allowlisted)
    return EffectiveProfile(
        profile_id=profile.id, provider=profile.provider, model=profile.model,
        kind=profile.kind, route=route, locality=profile.locality,
        source=source, egress_required=egress_required,
        egress_allowed=egress_allowed, allowlisted=allowlisted,
        policy_enabled=bool(policy.enabled), executable=executable,
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
        key_ref=str(profile.key_ref or "").strip())


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
           "source": eff.source}
    if not eff.executable:
        out["ok"] = False
        out["code"] = ("EGRESS_DENIED" if eff.egress_required
                       and not eff.egress_allowed else "PROFILE_NOT_EXECUTABLE")
        out["detail"] = (
            f"profile {eff.profile_id!r} is not executable: "
            f"policy_enabled={eff.policy_enabled} allowlisted={eff.allowlisted} "
            f"egress_allowed={eff.egress_allowed}")
    return out


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
    "LOCAL", "API", "REALTIME", "KINDS", "ROLES", "DEFAULT_ROLE",
    "DEFAULT_LOCAL_ID", "ResolverError", "ModelProfile", "EffectiveProfile",
    "default_profiles", "load_profiles", "get_profile", "list_profiles",
    "show_profile", "upsert_model_profile", "remove_model_profile",
    "clone_model_profile", "session_overrides", "set_session_profile",
    "clear_session_profile", "resolve", "resolve_model", "test_profile",
    "is_secret_ref", "looks_like_raw_secret",
    "list_speech_profiles", "show_speech_profile", "upsert_speech_profile",
    "remove_speech_profile", "clone_speech_profile", "resolve_speech_in",
    "resolve_speech_out", "resolve_realtime",
]
