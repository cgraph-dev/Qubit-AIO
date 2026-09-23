"""Canonical speech-in / speech-out / realtime profile registry.

Versioned, persisted speech profiles live in the **same** registry file that
:mod:`voicebridge.profiles` owns (``<state_dir>/profiles.json``) under the
additive keys ``speech_in_profiles`` / ``speech_out_profiles`` /
``realtime_profiles`` -- exactly alongside
:mod:`voicebridge.providers.resolver`'s ``model_profiles``. This module never
creates a second store.

It is the one authority that answers *which ASR engine, which TTS voice, and
which realtime adapter are effective for this session*, honoring the shared
:class:`~voicebridge.providers.policy.FrontierPolicy` for external egress.

Safety contract (mirrors the model resolver):

* credentials are secret **references** only (``env:NAME`` / ``file:PATH``); a
  raw key is refused at validation time;
* external (realtime / remote TTS) profiles require explicit egress consent,
  an enabled frontier policy, and an allowlist entry before they report
  ``executable``; locality boundaries are closed by default;
* unknown/disabled profiles are typed failures, never a silent fallback.

The shipped default records are migrated from the live config values
(``config.asr`` / ``config.tts`` / ``config.realtime``) so the current machine
profile is data, not hard-coded policy. Existing ``cfg.get("tts", ...)`` reads
keep working; the config keys are never removed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any, Dict, List, Optional, Tuple

from .resolver import (
    ResolverError,
    _atomic_json,
    _get,
    _policy,
    _read_registry,
    _read_sessions,
    _registry_file,
    _sessions_path,
    is_secret_ref,
    looks_like_raw_secret,
)

SPEECH_IN, SPEECH_OUT, REALTIME = "speech_in", "speech_out", "realtime"
SPEECH_KINDS = (SPEECH_IN, SPEECH_OUT, REALTIME)
SPEECH_SCHEMA = "qubit-speech-profile/1"

# PRA-08: an explicit speech-profile selection that cannot be resolved or is
# not executable must never be silently swallowed. The orchestrator records a
# typed error here; it is surfaced on the same status surface as the rest of the
# voice state (CLI `qubit status`, dashboard `/api/runtime`, voice status).
_RESOLUTION_ERRORS: Dict[str, Dict[str, Any]] = {}
_RESOLUTION_LOCK = threading.Lock()


def record_resolution_error(kind: str, code: str, detail: str, *,
                            profile_id: Optional[str] = None) -> None:
    with _RESOLUTION_LOCK:
        _RESOLUTION_ERRORS[str(kind)] = {
            "state": "UNAVAILABLE", "code": str(code), "kind": str(kind),
            "profile_id": profile_id, "detail": str(detail)[:400],
            "owner": "qubit-policy",
            "rollback": "select a resolvable profile or leave the shipped "
                        "default; the hardware config path is unchanged",
        }


def clear_resolution_error(kind: str) -> None:
    with _RESOLUTION_LOCK:
        _RESOLUTION_ERRORS.pop(str(kind), None)


def resolution_errors() -> Dict[str, Dict[str, Any]]:
    with _RESOLUTION_LOCK:
        return {k: dict(v) for k, v in _RESOLUTION_ERRORS.items()}

REGISTRY_KEYS = {
    SPEECH_IN: "speech_in_profiles",
    SPEECH_OUT: "speech_out_profiles",
    REALTIME: "realtime_profiles",
}
DEFAULT_SPEECH_IDS = {
    SPEECH_IN: "sherpa-parakeet-local",
    SPEECH_OUT: "piper-amy-local",
    REALTIME: "openai-realtime-lane",
}
SESSION_SCHEMA = "qubit-speech-profile-sessions/1"


# ---------------------------------------------------------------------------
# Typed profile record
# ---------------------------------------------------------------------------
@dataclass
class SpeechProfile:
    id: str
    kind: str = SPEECH_IN
    provider: str = ""
    model: str = ""
    voice: str = ""
    device: str = ""
    language: str = ""
    locality: str = "local"          # local | external
    egress_consent: str = "deny"     # deny | allow
    key_ref: str = ""                # "env:NAME" | "file:PATH" | ""
    base_url: str = ""
    # speech-in
    engine: str = ""
    provider_ep: str = ""
    num_threads: int = 0
    vad: bool = True
    vad_backend: str = "silero"
    ptt: bool = False
    wake: str = ""
    min_confidence: float = 0.0
    # speech-out
    style: str = ""
    latency: str = "balanced"
    quality: str = "standard"
    sample_rate: int = 22050
    # realtime
    duplex: bool = False
    # lifecycle
    enabled: bool = True
    default: bool = False
    fallback_to: str = ""
    rollback_to: str = ""
    provenance: str = ""
    license: str = ""

    def validate(self, cfg: Any = None) -> Dict[str, str]:
        if not self.id:
            return {"state": "FAILED", "code": "MISSING_ID",
                    "detail": "profile needs an id"}
        if self.kind not in SPEECH_KINDS:
            return {"state": "FAILED", "code": "BAD_KIND",
                    "detail": f"unknown kind {self.kind!r}"}
        if not self.provider:
            return {"state": "FAILED", "code": "MISSING_PROVIDER",
                    "detail": "profile needs a provider"}
        if self.kind == SPEECH_IN and not (self.model or self.engine):
            return {"state": "FAILED", "code": "MISSING_TARGET",
                    "detail": "speech_in profile needs a model/engine"}
        if self.kind == SPEECH_OUT and not self.voice:
            return {"state": "FAILED", "code": "MISSING_VOICE",
                    "detail": "speech_out profile needs a voice"}
        if self.kind == REALTIME and not self.model:
            return {"state": "FAILED", "code": "MISSING_MODEL",
                    "detail": "realtime profile needs a model"}
        if self.locality not in ("local", "external"):
            return {"state": "FAILED", "code": "BAD_LOCALITY",
                    "detail": f"bad locality {self.locality!r}"}
        if self.egress_consent not in ("deny", "allow"):
            return {"state": "FAILED", "code": "BAD_EGRESS",
                    "detail": f"bad egress_consent {self.egress_consent!r}"}
        if self.key_ref:
            if looks_like_raw_secret(self.key_ref):
                return {"state": "FAILED", "code": "RAW_SECRET",
                        "detail": "key_ref must be an env:/file: reference, "
                                  "never a raw key value"}
            if not is_secret_ref(self.key_ref):
                return {"state": "FAILED", "code": "BAD_SECRET_REF",
                        "detail": "key_ref must be env:NAME or file:PATH"}
        if self.kind == REALTIME and self.locality != "external":
            return {"state": "FAILED", "code": "BAD_LOCALITY",
                    "detail": "a realtime provider profile is external"}
        return {"state": "OK", "code": "", "detail": "profile valid"}

    def to_record(self) -> Dict[str, Any]:
        rec = dict(self.__dict__)
        rec["schema"] = SPEECH_SCHEMA
        return rec

    @classmethod
    def from_record(cls, rec: Dict[str, Any]) -> "SpeechProfile":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        data = {k: v for k, v in rec.items() if k in known}
        return cls(**data)  # type: ignore[arg-type]


def _allowlist_key(p: SpeechProfile) -> str:
    target = p.model or p.voice or p.engine
    return f"{p.provider}/{target}"


def _ref_from_key_env(key_env: Any) -> str:
    v = str(key_env or "").strip()
    if not v:
        return ""
    if is_secret_ref(v):
        return v
    return f"file:{v}"


# ---------------------------------------------------------------------------
# Shipped defaults (migrated from live config: data, not hard-coded policy)
# ---------------------------------------------------------------------------
def default_speech_profiles(cfg: Any = None) -> List[SpeechProfile]:
    asr_provider = str(_get(cfg, "asr", "provider", default="sherpa"))
    asr_engine = str(_get(cfg, "asr", "engine", default="parakeet"))
    asr_device = str(_get(cfg, "asr", "device", default="auto"))
    asr_lang = str(_get(cfg, "asr", "language", default="en"))
    asr_ep = str(_get(cfg, "asr", "provider_ep", default="cpu"))
    asr_threads = int(_get(cfg, "asr", "num_threads", default=0) or 0)
    vad_enabled = bool(_get(cfg, "vad", "enabled", default=True))
    vad_backend = str(_get(cfg, "vad", "backend", default="silero"))
    tts_provider = str(_get(cfg, "tts", "provider", default="piper"))
    tts_voice = str(_get(cfg, "tts", "voice", default="en_US-amy-medium"))
    tts_rate = int(_get(cfg, "tts", "sample_rate", default=22050) or 0)
    rt_model = str(_get(cfg, "realtime", "model",
                         default="gpt-4o-mini-realtime-preview"))
    rt_key = _ref_from_key_env(_get(cfg, "realtime", "key_env", default=""))
    return [
        SpeechProfile(
            id=DEFAULT_SPEECH_IDS[SPEECH_IN], kind=SPEECH_IN,
            provider=asr_provider, model=asr_engine, engine=asr_engine,
            device=asr_device, language=asr_lang, provider_ep=asr_ep,
            num_threads=asr_threads, vad=vad_enabled, vad_backend=vad_backend,
            ptt=False, wake="", locality="local", egress_consent="deny",
            default=True, rollback_to=DEFAULT_SPEECH_IDS[SPEECH_IN],
            provenance="sherpa-onnx local ASR adapter (Parakeet/Moonshine)",
            license="Apache-2.0 (sherpa-onnx); model license per upstream"),
        SpeechProfile(
            id=DEFAULT_SPEECH_IDS[SPEECH_OUT], kind=SPEECH_OUT,
            provider=tts_provider, voice=tts_voice, language=asr_lang,
            sample_rate=tts_rate, style="", latency="fast",
            quality="standard", locality="local", egress_consent="deny",
            default=True, rollback_to=DEFAULT_SPEECH_IDS[SPEECH_OUT],
            provenance="piper local ONNX TTS adapter",
            license="MIT (piper-tts); voice model license per upstream"),
        SpeechProfile(
            id=DEFAULT_SPEECH_IDS[REALTIME], kind=REALTIME, provider="openai",
            model=rt_model, locality="external", egress_consent="deny",
            key_ref=rt_key, duplex=True, default=True,
            rollback_to=DEFAULT_SPEECH_IDS[REALTIME],
            provenance="OpenAI Realtime API (external duplex adapter)",
            license="proprietary (provider terms)"),
    ]


# ---------------------------------------------------------------------------
# Persistence (same registry file; additive keys)
# ---------------------------------------------------------------------------
def load_speech_profiles(cfg: Any,
                         kind: Optional[str] = None) -> List[SpeechProfile]:
    """Shipped defaults plus user records (user record wins on id clash)."""
    kinds = (kind,) if kind in SPEECH_KINDS else SPEECH_KINDS
    records: Dict[str, SpeechProfile] = {}
    for p in default_speech_profiles(cfg):
        if p.kind in kinds:
            records[p.id] = p
    reg = _read_registry(cfg)
    for k in kinds:
        for rec in reg.get(REGISTRY_KEYS[k], []) or []:
            if not isinstance(rec, dict):
                continue
            try:
                p = SpeechProfile.from_record(rec)
            except (TypeError, ValueError):
                continue
            if p.kind in kinds:
                records[p.id] = p
    return [p for p in records.values() if p.kind in kinds]


def _save_speech_profiles(cfg: Any, profiles: List[SpeechProfile]):
    path = _registry_file(cfg)
    if path is None:
        raise ResolverError("NO_STATE_DIR", "cannot resolve the Qubit state dir")
    reg = _read_registry(cfg)
    for kind, key in REGISTRY_KEYS.items():
        reg[key] = [p.to_record() for p in profiles
                    if p.kind == kind and not p.default]
    reg.setdefault("version", 1)
    _atomic_json(path, reg)
    return path


def get_speech_profile(cfg: Any, profile_id: str) -> Optional[SpeechProfile]:
    for p in load_speech_profiles(cfg):
        if p.id == profile_id:
            return p
    return None


def list_speech_profiles(cfg: Any, kind: Optional[str] = None) -> Dict[str, Any]:
    if kind is not None and kind not in SPEECH_KINDS:
        raise ResolverError("BAD_KIND", f"unknown speech kind {kind!r}")
    policy = _policy(cfg)
    items = []
    for p in load_speech_profiles(cfg, kind):
        gate = p.validate(cfg)
        items.append({
            "id": p.id, "kind": p.kind, "provider": p.provider,
            "model": p.model, "voice": p.voice, "device": p.device,
            "language": p.language, "locality": p.locality,
            "egress_consent": p.egress_consent, "default": p.default,
            "enabled": p.enabled, "has_secret_ref": bool(p.key_ref),
            "allowlisted": _allowlist_key(p) in policy.allowlist,
            "gate": gate["state"], "gate_code": gate["code"],
            "fallback_to": p.fallback_to, "rollback_to": p.rollback_to,
        })
    return {"schema": SPEECH_SCHEMA, "profiles": items,
            "count": len(items), "dry_run": False}


def show_speech_profile(cfg: Any, profile_id: str) -> Dict[str, Any]:
    p = get_speech_profile(cfg, profile_id)
    if p is None:
        raise ResolverError("UNKNOWN_PROFILE",
                            f"no speech profile {profile_id!r}")
    gate = p.validate(cfg)
    eff = _effective_speech(cfg, p, source="explicit")
    return {"schema": SPEECH_SCHEMA, "profile": p.to_record(),
            "gate": gate, "effective": eff.to_public_dict()}


# ---------------------------------------------------------------------------
# Lifecycle (create/clone/remove/session override)
# ---------------------------------------------------------------------------
def upsert_speech_profile(cfg: Any, profile: SpeechProfile, *,
                          confirm: bool = False) -> Dict[str, Any]:
    gate = profile.validate(cfg)
    if gate["state"] != "OK":
        return gate
    if profile.default and profile.id not in DEFAULT_SPEECH_IDS.values():
        return {"state": "DENIED", "code": "IMMUTABLE_DEFAULT",
                "detail": "only a shipped default may set default=true"}
    if not confirm:
        return {"state": "OK", "dry_run": True, "action": "would persist",
                "profile": profile.id}
    existing = {p.id: p for p in load_speech_profiles(cfg)}
    existing[profile.id] = profile
    try:
        _save_speech_profiles(cfg, list(existing.values()))
    except ResolverError as exc:
        return {"state": "FAILED", "code": exc.code, "detail": exc.detail}
    except OSError as exc:
        return {"state": "FAILED", "code": "WRITE_FAILED", "detail": str(exc)}
    return {"state": "OK", "profile": profile.id, "action": "persisted"}


def remove_speech_profile(cfg: Any, profile_id: str, *,
                          confirm: bool = False) -> Dict[str, Any]:
    p = get_speech_profile(cfg, profile_id)
    if p is None:
        raise ResolverError("UNKNOWN_PROFILE",
                            f"no speech profile {profile_id!r}")
    if p.default:
        raise ResolverError("IMMUTABLE_DEFAULT",
                            "a shipped speech default profile cannot be removed")
    if profile_id in set(_all_speech_session_targets(cfg)):
        return {"state": "DENIED", "code": "PROFILE_IN_USE",
                "detail": "profile is selected by a session; clear that first"}
    if not confirm:
        return {"state": "OK", "dry_run": True, "action": "would remove",
                "profile": profile_id}
    remaining = [x for x in load_speech_profiles(cfg)
                 if x.id != profile_id or x.default]
    try:
        _save_speech_profiles(cfg, remaining)
    except OSError as exc:
        return {"state": "FAILED", "code": "WRITE_FAILED", "detail": str(exc)}
    return {"state": "OK", "profile": profile_id, "action": "removed"}


def clone_speech_profile(cfg: Any, source_id: str, new_id: str, *,
                         confirm: bool = False) -> Dict[str, Any]:
    src = get_speech_profile(cfg, source_id)
    if src is None:
        raise ResolverError("UNKNOWN_PROFILE",
                            f"no speech profile {source_id!r}")
    if get_speech_profile(cfg, new_id) is not None:
        raise ResolverError("PROFILE_EXISTS", f"profile {new_id!r} already exists")
    rec = src.to_record()
    rec.update({"id": new_id, "default": False, "rollback_to": source_id})
    return upsert_speech_profile(cfg, SpeechProfile.from_record(rec),
                                 confirm=confirm)


# ---------------------------------------------------------------------------
# Session overrides (same session file, speech-kind role keys)
# ---------------------------------------------------------------------------
def _iter_speech_session_overrides(cfg: Any) -> Dict[Tuple[str, str], str]:
    out: Dict[Tuple[str, str], str] = {}
    for sid, roles in (_read_sessions(cfg).get("sessions") or {}).items():
        if isinstance(roles, dict):
            for role, pid in roles.items():
                if role in SPEECH_KINDS and isinstance(pid, str) and pid:
                    out[(str(sid), str(role))] = pid
    return out


def _all_speech_session_targets(cfg: Any) -> List[str]:
    return list(_iter_speech_session_overrides(cfg).values())


def set_session_speech_profile(cfg: Any, session: str, kind: str,
                               profile_id: str, *,
                               confirm: bool = False) -> Dict[str, Any]:
    if kind not in SPEECH_KINDS:
        raise ResolverError("BAD_KIND", f"unknown speech kind {kind!r}")
    p = get_speech_profile(cfg, profile_id)
    if p is None:
        raise ResolverError("UNKNOWN_PROFILE",
                            f"no speech profile {profile_id!r}")
    if p.kind != kind:
        raise ResolverError("KIND_MISMATCH",
                            f"profile {profile_id!r} is {p.kind}, not {kind}")
    sid = str(session or "").strip()
    if not sid:
        raise ResolverError("MALFORMED", "session id required")
    if not confirm:
        return {"state": "OK", "dry_run": True, "session": sid,
                "kind": kind, "profile": profile_id}
    data = _read_sessions(cfg)
    sessions = data.setdefault("sessions", {})
    sessions.setdefault(sid, {})[kind] = profile_id
    data["schema"] = data.get("schema") or SESSION_SCHEMA
    _atomic_json(_sessions_path(cfg), data)
    return {"state": "OK", "session": sid, "kind": kind,
            "profile": profile_id, "action": "selected"}


def clear_session_speech_profile(cfg: Any, session: str, *,
                                 kind: str = None) -> Dict[str, Any]:
    sid = str(session or "").strip()
    kinds = (kind,) if kind in SPEECH_KINDS else SPEECH_KINDS
    targets = _sessions_path(cfg)
    if not targets.exists():
        return {"state": "OK", "session": sid, "changed": False}
    data = _read_sessions(cfg)
    sessions = data.get("sessions") or {}
    changed = False
    if sid in sessions and isinstance(sessions.get(sid), dict):
        for k in kinds:
            if k in sessions[sid]:
                sessions[sid].pop(k, None)
                changed = True
        if not sessions[sid]:
            sessions.pop(sid, None)
    if changed:
        data["sessions"] = sessions
        _atomic_json(targets, data)
    return {"state": "OK", "session": sid, "changed": changed}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EffectiveSpeech:
    profile_id: str
    kind: str
    provider: str
    model: str
    voice: str
    device: str
    language: str
    locality: str
    route: str
    source: str
    egress_required: bool
    egress_allowed: bool
    allowlisted: bool
    policy_enabled: bool
    executable: bool
    fallback_chain: Tuple[str, ...]
    rollback_to: str
    settings: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_local(self) -> bool:
        return self.locality == "local"

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id, "kind": self.kind,
            "provider": self.provider, "model": self.model,
            "voice": self.voice, "device": self.device,
            "language": self.language, "locality": self.locality,
            "route": self.route, "source": self.source,
            "egress_required": self.egress_required,
            "egress_allowed": self.egress_allowed,
            "allowlisted": self.allowlisted,
            "policy_enabled": self.policy_enabled,
            "executable": self.executable,
            "fallback_chain": list(self.fallback_chain),
            "rollback_to": self.rollback_to,
            "settings": dict(self.settings),
        }


def _speech_settings(p: SpeechProfile) -> Dict[str, Any]:
    base = {"device": p.device, "language": p.language}
    if p.kind == SPEECH_IN:
        base.update({
            "engine": p.engine or p.model, "provider_ep": p.provider_ep,
            "num_threads": p.num_threads, "vad": p.vad,
            "vad_backend": p.vad_backend, "ptt": p.ptt, "wake": p.wake,
            "min_confidence": p.min_confidence,
        })
    elif p.kind == SPEECH_OUT:
        base.update({
            "style": p.style, "latency": p.latency, "quality": p.quality,
            "sample_rate": p.sample_rate,
        })
    else:
        base.update({"duplex": p.duplex, "base_url": p.base_url})
    return base


def _fallback_chain(cfg: Any, profile: SpeechProfile) -> Tuple[str, ...]:
    chain: List[str] = []
    seen = {profile.id}
    cur = profile
    while cur.fallback_to and cur.fallback_to not in seen:
        nxt = get_speech_profile(cfg, cur.fallback_to)
        if nxt is None or nxt.kind != profile.kind:
            break
        seen.add(nxt.id)
        chain.append(nxt.id)
        cur = nxt
        if len(chain) >= 8:
            break
    return tuple(chain)


def _effective_speech(cfg: Any, profile: SpeechProfile, *, source: str
                      ) -> EffectiveSpeech:
    policy = _policy(cfg)
    allowlisted = _allowlist_key(profile) in policy.allowlist
    if profile.locality == "local":
        route = "local"
        egress_required = False
        egress_allowed = True
        executable = profile.enabled
    else:
        route = "external"
        egress_required = True
        egress_allowed = profile.egress_consent == "allow"
        executable = bool(profile.enabled and egress_allowed
                          and policy.enabled and allowlisted)
    return EffectiveSpeech(
        profile_id=profile.id, kind=profile.kind, provider=profile.provider,
        model=profile.model, voice=profile.voice, device=profile.device,
        language=profile.language, locality=profile.locality, route=route,
        source=source, egress_required=egress_required,
        egress_allowed=egress_allowed, allowlisted=allowlisted,
        policy_enabled=bool(policy.enabled), executable=executable,
        fallback_chain=_fallback_chain(cfg, profile),
        rollback_to=profile.rollback_to,
        settings=_speech_settings(profile))


def _pick_speech(cfg: Any, *, kind: str, profile_id: Optional[str],
                 session: Optional[str]) -> Tuple[SpeechProfile, str]:
    if profile_id:
        p = get_speech_profile(cfg, profile_id)
        if p is None:
            raise ResolverError("UNKNOWN_PROFILE",
                                f"no speech profile {profile_id!r}")
        if p.kind != kind:
            raise ResolverError("KIND_MISMATCH",
                                f"profile {profile_id!r} is {p.kind}, not {kind}")
        if not p.enabled:
            raise ResolverError("PROFILE_DISABLED", f"profile {p.id!r} disabled")
        return p, "explicit"
    if session:
        ov = _iter_speech_session_overrides(cfg)
        for key in ((str(session), kind), (str(session), "*")):
            pid = ov.get(key)
            if pid:
                p = get_speech_profile(cfg, pid)
                if p is None:
                    raise ResolverError(
                        "SESSION_PROFILE_MISSING",
                        f"session {session!r} selects missing profile {pid!r}")
                if p.kind != kind:
                    raise ResolverError(
                        "KIND_MISMATCH",
                        f"session {session!r} selects {p.kind} as {kind}")
                if p.enabled:
                    return p, "session"
    for p in load_speech_profiles(cfg, kind):
        if p.default and p.enabled:
            return p, "role-default"
    for p in default_speech_profiles(cfg):
        if p.kind == kind and p.default:
            return p, "shipped-default"
    raise ResolverError("NO_DEFAULT", f"no default {kind} profile available")


def _resolve_kind(cfg: Any, kind: str, profile_id: Optional[str],
                  session: Optional[str]) -> EffectiveSpeech:
    profile, source = _pick_speech(cfg, kind=kind, profile_id=profile_id,
                                   session=session)
    return _effective_speech(cfg, profile, source=source)


def resolve_speech_in(cfg: Any, profile_id: Optional[str] = None, *,
                      session: Optional[str] = None) -> EffectiveSpeech:
    """Resolve the effective speech-input (ASR) profile."""
    return _resolve_kind(cfg, SPEECH_IN, profile_id, session)


def resolve_speech_out(cfg: Any, profile_id: Optional[str] = None, *,
                       session: Optional[str] = None) -> EffectiveSpeech:
    """Resolve the effective speech-output (TTS) profile."""
    return _resolve_kind(cfg, SPEECH_OUT, profile_id, session)


def resolve_realtime(cfg: Any, profile_id: Optional[str] = None, *,
                     session: Optional[str] = None) -> EffectiveSpeech:
    """Resolve the effective realtime voice adapter profile."""
    return _resolve_kind(cfg, REALTIME, profile_id, session)


def resolve_speech(cfg: Any, kind: str, profile_id: Optional[str] = None, *,
                   session: Optional[str] = None) -> EffectiveSpeech:
    if kind not in SPEECH_KINDS:
        raise ResolverError("BAD_KIND", f"unknown speech kind {kind!r}")
    return _resolve_kind(cfg, kind, profile_id, session)


def resolve_speech_dict(cfg: Any, kind: str,
                        profile_id: Optional[str] = None, *,
                        session: Optional[str] = None) -> Dict[str, Any]:
    """Typed-dict resolution (no exception) for transport compat."""
    try:
        eff = resolve_speech(cfg, kind, profile_id, session=session)
    except ResolverError as exc:
        return {"ok": False, "code": exc.code, "detail": exc.detail}
    out = eff.to_public_dict()
    out["ok"] = eff.executable
    if not eff.executable:
        out["code"] = ("EGRESS_DENIED" if eff.egress_required
                       and not eff.egress_allowed else "PROFILE_NOT_EXECUTABLE")
        out["detail"] = (
            f"profile {eff.profile_id!r} is not executable: "
            f"policy_enabled={eff.policy_enabled} allowlisted={eff.allowlisted} "
            f"egress_allowed={eff.egress_allowed}")
    return out


def external_speech_guard(cfg: Any):
    """One canonical gate before an external (network) speech call.

    Returns a zero-argument callable. It blocks when the global emergency stop
    or global disable is latched, and when the operator explicitly sets
    ``tts.<provider>.egress_consent: deny``. Selecting an external speech
    provider in config is explicit operator consent (never silent); this guard
    adds the missing emergency-stop enforcement and a redacted audit receipt
    for every external call. It performs no network I/O itself.
    """
    def _guard() -> bool:
        from ..logging_setup import log_event
        try:
            from ..power import power_control
            st = power_control(cfg).status()
            if st.get("emergency_stop") or st.get("global_disable"):
                log_event("tts.egress_denied", reason="emergency_stop")
                return False
        except Exception:  # a broken control store fails closed
            log_event("tts.egress_denied", reason="control_state_unreadable")
            return False
        consent = "allow"
        try:
            consent = str(cfg.get("tts", "openai", "egress_consent",
                                  default="allow") or "allow").strip().lower()
        except Exception:
            consent = "allow"
        if consent in ("deny", "false", "0", "no", "off"):
            log_event("tts.egress_denied", reason="config_consent_deny")
            return False
        log_event("tts.egress_allowed", reason="explicit_provider_config")
        return True
    return _guard


__all__ = [
    "SPEECH_IN", "SPEECH_OUT", "REALTIME", "SPEECH_KINDS", "SPEECH_SCHEMA",
    "REGISTRY_KEYS", "DEFAULT_SPEECH_IDS", "ResolverError", "SpeechProfile",
    "EffectiveSpeech", "default_speech_profiles", "load_speech_profiles",
    "get_speech_profile", "list_speech_profiles", "show_speech_profile",
    "upsert_speech_profile", "remove_speech_profile", "clone_speech_profile",
    "set_session_speech_profile", "clear_session_speech_profile",
    "resolve_speech_in", "resolve_speech_out", "resolve_realtime",
    "resolve_speech", "resolve_speech_dict", "external_speech_guard",
    "record_resolution_error", "clear_resolution_error", "resolution_errors",
]
