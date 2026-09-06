"""Packet 8O — full-capability power profile and one policy path (hermetic).

This module exposes the explicit authenticated power profile that inventories
and controls every installed capability through ONE policy path used by voice,
dashboard, CLI, MCP, tray, and automation requests. It does NOT execute
anything and never touches live services, browsers, accounts, audio, timers,
or user config: it is a decision + inventory layer over the existing 7E
``PermissionsStore``/``PermissionPolicy``, the 8F ``ActionGate``/``ActionRunner``,
and the 8N ``SessionGrantStore``/``GhostGate`` machinery.

Core guarantees (each enforced by code and by the hermetic test suite):

* **Available is never authorized.** Every capability in the profile carries a
  typed ``state`` (``AVAILABLE``/``ENABLED``/``AUTHORIZED``/``DISABLED``) plus
  ``revocable``/``disableable`` controls; the profile never conflates "the
  adapter is present" with "this action is allowed".
* **Owner-authorized power grant.** One explicit, expiring, revocable,
  target-bound grant selected by the operator (browsers, apps, accounts,
  origins, directories, scopes, duration). Within the grant, routine
  read/search/navigation and exact-target non-sensitive form filling are
  auto-allowed. Drafting stays local and non-external. Posting, sending,
  publishing, purchasing, deleting, and modifying project code ALWAYS require
  a fresh human confirmation bound to the exact action hash — even inside the
  grant. The grant never widens silently: matching is exact on
  capability-tier, scope, origin/role/account/directory and expiry.
* **One policy path.** ``PowerPolicy.decide`` layers, in order:
  global emergency stop -> global disable -> per-adapter circuit breaker ->
  bounded concurrency -> capability known -> power-grant routine auto-allow ->
  the existing 7E/8F/8N gate (session grants + explicit grants + fresh
  confirmation). Model/page/skill/tool text can never carry authority.
* **PowerControl.** Global emergency stop (latched, cancels active runs),
  global disable, per-adapter circuit breakers (fail-closed when open),
  bounded concurrency, kill/reap tracking, and restart recovery.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .actions import (ActionSpec, ADAPTER_DESKTOP, ADAPTER_EDITOR,
                      ADAPTER_MAIL, ADAPTER_SOCIAL, action_spec)
from .capabilities import ConfirmationDenied, Tier
from .logging_setup import log_event
from .redaction import redact_text

# ---------------------------------------------------------------------------
# Domains + capability states (8O contract)
# ---------------------------------------------------------------------------
POWER_DOMAINS = ("memory", "browser", "desktop", "editor", "file", "network",
                 "shell", "account", "notifications", "audio", "model")

ST_AVAILABLE = "AVAILABLE"
ST_ENABLED = "ENABLED"
ST_AUTHORIZED = "AUTHORIZED"
ST_DISABLED = "DISABLED"
ST_DEGRADED = "DEGRADED"
ST_FAILED = "FAILED"
STATES = (ST_AVAILABLE, ST_ENABLED, ST_AUTHORIZED, ST_DISABLED, ST_DEGRADED,
          ST_FAILED)

# High-impact capability ids that ALWAYS require a fresh human confirmation,
# even inside an active owner power grant (8O: post/send/publish/purchase/
# delete/modify-project-code).
POWER_HIGH_IMPACT = {
    "act.mail.send", "act.social.post", "act.purchase",
    "act.editor.delete", "act.account.change", "act.system.setting",
    "ghost.input.click", "ghost.input.type", "ghost.clipboard.read",
    "ghost.shell",
}

# Directory/editor capabilities whose "project-code modification" surface must
# stay high-impact (8O: modifying project code always asks for confirmation).
_POWER_PROJECT_CODE = {"act.editor.write"}

POWER_SCHEMA_VERSION = 1

_WILDCARD = {"*", "", "any", "all"}
_GRANT_ID_RE = re.compile(r"^[a-z0-9]{16,40}$")
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_DIR_RE = re.compile(r"^/|^[A-Za-z]:[\\/]")
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")

DEFAULT_GRANT_TTL_S = 7 * 86400          # 7 days default operator duration
MAX_GRANT_TTL_S = 90 * 86400             # hard ceiling
DEFAULT_CONCURRENCY = 4                  # bounded concurrent actions per adapter


class PowerError(Exception):
    """Typed power-profile error (validation / store / policy)."""

    def __init__(self, message: str = "", code: str = "POWER_ERROR",
                 degraded: bool = False):
        super().__init__(message)
        self.code = code
        self.degraded = degraded


class PowerDenied(PowerError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def is_high_impact(action_id: str, spec: Optional[ActionSpec] = None) -> bool:
    """High-impact classification used by the grant + policy layers.

    Post/send/publish/purchase/delete and project-code modification are always
    high-impact regardless of tier; a mutating/destructive tier is always
    high-impact as well.
    """
    if action_id in POWER_HIGH_IMPACT or action_id in _POWER_PROJECT_CODE:
        return True
    if spec is None:
        return False
    return spec.tier in (Tier.MUTATING, Tier.DESTRUCTIVE)


def _valid_origin(url: str) -> bool:
    """Strict canonical-origin validation for grant ``origins`` entries.

    Accepts only http (port 80) or https (port 443) with an explicit
    multi-label DNS host, no user-info, no path, no query, no fragment, and no
    IP literal. A bare host, an IP literal, a non-default port, an explicit
    default-port-only mismatch (http on 443 / https on 80), a credential-bearing
    URL, and any path/query/fragment (only the origin is intended) are all
    rejected. Used for BOTH grant-bound origins and browser-role target
    matching so a grant can never be replayed against an out-of-origin target.
    """
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False
    host = parts.hostname or ""
    if not host or any(ch in host for ch in ("@", "/", "\\")):
        return False
    if not re.match(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$", host):
        return False
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        return False  # IP literals are never an approved origin
    labels = host.split(".")
    if len(labels) < 2 or any(len(lbl) < 1 for lbl in labels):
        return False  # require a canonical multi-label host, no empty labels
    if parts.port is not None:
        expected = 443 if scheme == "https" else 80
        if parts.port != expected:
            return False  # http:80 / https:443 only
    if parts.username or parts.password:
        return False
    if parts.path:
        return False  # only the origin is intended; any path is rejected
    if parts.query or parts.fragment:
        return False
    return True


def _origin_host(url: str) -> str:
    try:
        return (urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return ""


def _canonical_host(url: str) -> str:
    """Reduce an origin/host entry to a canonical host (strips scheme, port,
    path, query, fragment, credentials, and leading wildcard)."""
    u = (url or "").strip()
    if not u:
        return ""
    if "://" in u:
        try:
            h = urlsplit(u).hostname
            return (h or "").lower()
        except ValueError:
            return ""
    host = u.lower()
    while host.startswith("*."):
        host = host[2:]
    host = host.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.split("@", 1)[-1]
    if ":" in host:
        host = host.split(":", 1)[0]
    return host.strip()


def _host_matches(url: str, allowed: Tuple[str, ...], *,
                  allow_subdomains: bool = True) -> bool:
    """Canonical host allowlist match with exact subdomain boundaries.

    POLICY (explicit): subdomains ARE allowed by default, but ONLY as a proper
    suffix at a label boundary (``target == allowed`` or ``target`` ends with
    ``.<allowed>``). ``notgmail.com`` never matches ``gmail.com``; a bare TLD,
    an IP, or a host with a wildcard in the middle never match. Pass
    ``allow_subdomains=False`` to require an EXACT host match. The decision is
    explicit in every caller and documented; subdomains are never silently
    treated as exact matches or vice versa.
    """
    host = _origin_host(url)
    if not host or not allowed:
        return False
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        return False  # IP targets are never matched by an origin grant
    for a in allowed:
        a = _canonical_host(a)
        if not a or len(a.split(".")) < 2:
            continue
        if any(ch in a for ch in ("*", "@", "/", "\\", " ")):
            continue
        if host == a:
            return True
        if allow_subdomains and host.endswith("." + a):
            return True
    return False


def _now() -> float:
    return time.time()


def _new_id(prefix: str = "pwr") -> str:
    return f"{prefix}{uuid.uuid4().hex[:32]}"


def args_digest(args: Optional[Dict[str, Any]]) -> str:
    """Stable SHA-256 of canonicalized arguments (exact-hash binding)."""
    import hashlib
    canonical = json.dumps(dict(args or {}), sort_keys=True,
                           separators=(",", ":"), default=repr)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Shared, correctly scoped PowerControl/PowerPolicy instances
# ---------------------------------------------------------------------------
# One process-wide instance per state dir so emergency stop, disable, circuit
# breakers, concurrency, and grants affect EVERY channel (voice, dashboard,
# CLI, MCP, tray, automation, Ghost, actions) consistently.
_SHARED_LOCK = threading.Lock()
_SHARED_CONTROL: Dict[str, PowerControl] = {}
_SHARED_POLICY: Dict[str, "PowerPolicy"] = {}


def _scope_key(cfg: Any = None) -> str:
    if cfg is None:
        return "default"
    try:
        return str(os.fspath(cfg.state_dir()))
    except Exception:
        return "default"


# ---------------------------------------------------------------------------
# PowerCapability — one inventory row (typed; never conflates available/auth)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PowerCapability:
    domain: str
    capability: str          # capability/action id
    adapter: str
    adapter_version: str
    scope: str
    target_or_origin: str = ""
    risk_tier: str = ""
    state: str = ST_AVAILABLE
    expiry: Optional[float] = None
    resource_limits: Dict[str, Any] = field(default_factory=dict)
    audit_status: str = "none"
    revocable: bool = False
    disableable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"domain": self.domain, "capability": self.capability,
                "adapter": self.adapter,
                "adapter_version": self.adapter_version,
                "scope": self.scope,
                "target_or_origin": redact_text(self.target_or_origin),
                "risk_tier": self.risk_tier, "state": self.state,
                "expiry": self.expiry,
                "resource_limits": self.resource_limits,
                "audit_status": self.audit_status,
                "revocable": self.revocable, "disableable": self.disableable}


def _from_action_spec(spec: ActionSpec, domain: str, *,
                      state: str, target: str = "",
                      revocable: bool, disableable: bool) -> PowerCapability:
    limits = {"timeout_s": spec.timeout_s,
              "rollback_kind": getattr(spec, "rollback_kind", "none")}
    return PowerCapability(
        domain=domain, capability=spec.action_id, adapter=spec.adapter,
        adapter_version=spec.version,
        scope=spec.scopes[0] if spec.scopes else "",
        target_or_origin=target, risk_tier=spec.tier.value,
        state=state, resource_limits=limits, revocable=revocable,
        disableable=disableable,
        audit_status="adapter_contract" if state != ST_DISABLED else "none")


def _domain_for_action(action_id: str, adapter: str) -> str:
    """Map an action/capability id to one of the 11 power domains."""
    if action_id.startswith("ghost.comet."):
        return "network"
    if action_id.startswith("ghost.shell"):
        return "shell"
    if action_id.startswith("ghost.desktop.") or \
            action_id.startswith("ghost.window."):
        return "desktop"
    if action_id.startswith("ghost.browser.") or \
            action_id.startswith("ghost.input.") or \
            action_id.startswith("ghost.clipboard."):
        return "browser"
    if adapter == ADAPTER_EDITOR:
        return "editor"
    if adapter in (ADAPTER_MAIL, ADAPTER_SOCIAL) or \
            action_id.startswith("act.account."):
        return "account"
    if adapter == ADAPTER_DESKTOP or action_id.startswith("act.desktop."):
        return "desktop"
    if action_id.startswith("act.browser.") or adapter == "browser":
        return "browser"
    return "desktop"


def _declared_capabilities(cfg: Any) -> List[PowerCapability]:
    """Declared (non-action) capability rows for domains that are not action
    adapters: memory, notifications, audio, model, file.

    Truthfulness rule: ``AVAILABLE`` means the adapter is present AND enabled
    by config, but the row does NOT claim it is running — a live probe is the
    only way to claim ``ENABLED``. A disabled/absent feature is ``DISABLED``.
    ``configured != running`` is therefore never conflated.
    """
    from .profiles import current_profiles  # local import (no hard dep)

    caps: List[PowerCapability] = []
    mem_enabled = bool(cfg.get("memory", "enabled", default=True))
    mem_sem = bool(cfg.get("memory", "semantic", "enabled", default=False))
    caps.append(PowerCapability(
        domain="memory", capability="memory.core", adapter="memory",
        adapter_version="1.0.0", scope="memory",
        risk_tier=Tier.READ_ONLY.value,
        state=ST_AVAILABLE if mem_enabled else ST_DISABLED,
        revocable=False, disableable=True,
        audit_status="config_declared"))
    caps.append(PowerCapability(
        domain="memory", capability="memory.semantic", adapter="memory",
        adapter_version="1.0.0", scope="memory",
        risk_tier=Tier.READ_ONLY.value,
        state=ST_AVAILABLE if mem_sem else ST_DISABLED,
        revocable=False, disableable=True,
        audit_status="config_declared"))
    notif = cfg.get("communications", "notifications", default="desktop") or \
        "desktop"
    caps.append(PowerCapability(
        domain="notifications", capability="notifications.desktop",
        adapter="notifications", adapter_version="1.0.0",
        scope="notifications", risk_tier=Tier.REVERSIBLE.value,
        state=ST_AVAILABLE if notif else ST_DISABLED,
        revocable=False, disableable=True, audit_status="config_declared"))
    try:
        profs = current_profiles(cfg)
        brain = (profs.get("brain") or {}).get("selected") or "qubit-brain4"
        stt = (profs.get("stt") or {}).get("selected") or "sherpa"
        tts = (profs.get("tts") or {}).get("selected") or "piper"
    except Exception:
        brain, stt, tts = "qubit-brain4", "sherpa", "piper"
    # Model/STT/TTS: configured + present (AVAILABLE), never claimed running.
    caps.append(PowerCapability(
        domain="model", capability=f"model.brain:{brain}",
        adapter="profiles", adapter_version="1.0.0", scope="model",
        target_or_origin="local", risk_tier=Tier.REVERSIBLE.value,
        state=ST_AVAILABLE, revocable=False, disableable=True,
        audit_status="config_declared"))
    caps.append(PowerCapability(
        domain="model", capability=f"model.stt:{stt}",
        adapter="profiles", adapter_version="1.0.0", scope="model",
        target_or_origin="local", risk_tier=Tier.READ_ONLY.value,
        state=ST_AVAILABLE, revocable=False, disableable=True,
        audit_status="config_declared"))
    caps.append(PowerCapability(
        domain="model", capability=f"model.tts:{tts}",
        adapter="profiles", adapter_version="1.0.0", scope="model",
        target_or_origin="local", risk_tier=Tier.REVERSIBLE.value,
        state=ST_AVAILABLE, revocable=False, disableable=True,
        audit_status="config_declared"))
    mic = cfg.get("audio", "mic_name", default="") or "system default"
    out = cfg.get("audio", "output_name", default="") or "system default"
    caps.append(PowerCapability(
        domain="audio", capability="audio.capture", adapter="audio",
        adapter_version="1.0.0", scope="audio",
        target_or_origin=redact_text(mic), risk_tier=Tier.READ_ONLY.value,
        state=ST_AVAILABLE, revocable=False, disableable=True,
        audit_status="config_declared"))
    caps.append(PowerCapability(
        domain="audio", capability="audio.playback", adapter="audio",
        adapter_version="1.0.0", scope="audio",
        target_or_origin=redact_text(out), risk_tier=Tier.REVERSIBLE.value,
        state=ST_AVAILABLE, revocable=False, disableable=True,
        audit_status="config_declared"))
    caps.append(PowerCapability(
        domain="file", capability="file.workspace", adapter="file",
        adapter_version="1.0.0", scope="file",
        risk_tier=Tier.READ_ONLY.value, state=ST_AVAILABLE,
        revocable=False, disableable=False, audit_status="config_declared"))
    return caps


def _as_authorized(cap: PowerCapability) -> PowerCapability:
    """Return a copy of ``cap`` with state set to AUTHORIZED (truthful)."""
    return PowerCapability(
        domain=cap.domain, capability=cap.capability, adapter=cap.adapter,
        adapter_version=cap.adapter_version, scope=cap.scope,
        target_or_origin=cap.target_or_origin, risk_tier=cap.risk_tier,
        state=ST_AUTHORIZED, expiry=cap.expiry,
        resource_limits=cap.resource_limits, audit_status=cap.audit_status,
        revocable=cap.revocable, disableable=cap.disableable)


def power_profile(cfg: Any = None) -> Dict[str, Any]:
    """Build the full capability inventory across all 11 power domains.

    Rows come from the 8F action contract, the 8N Ghost contract, and the
    declared config surface (memory/notifications/audio/model/file). The
    returned dict never claims authorization from availability: each row
    carries its own typed ``state`` and ``revocable``/``disableable``
    controls, and ``authorized`` counts only rows whose state is AUTHORIZED.
    """
    register_ghost_specs_safe()
    caps: List[PowerCapability] = []
    # 8F action capabilities: the CONTRACT is registered but the live execution
    # adapter is DISABLED by default (Packet 8F). Truthfulness rule:
    # "available" describes an actually-executable adapter, not a registered
    # contract, so disabled/unavailable adapters report DISABLED. An operator
    # that enables the action adapter makes these AVAILABLE.
    from .actions import action_specs as _action_specs
    action_enabled = bool(cfg is not None and cfg.get(
        "actions", "enabled", default=False))
    for spec in _action_specs():
        domain = _domain_for_action(spec.action_id, spec.adapter)
        caps.append(_from_action_spec(
            spec, domain,
            state=ST_AVAILABLE if action_enabled else ST_DISABLED,
            target=redact_text(getattr(spec, "target", "")),
            revocable=spec.tier in (Tier.READ_ONLY, Tier.REVERSIBLE),
            disableable=True))
    # Ghost adapter is DISABLED by default (Packet 8N contract). A capability
    # of a disabled / adapter-absent adapter is never AVAILABLE: report the
    # honest DISABLED state so the inventory never implies live availability.
    from . import ghost as G
    ghost_shell = G.ghost_shell_enabled()
    ghost_adapter_state = ST_DISABLED
    if cfg is not None:
        try:
            ga = G.ghost_adapter(cfg)
            ghost_adapter_state = (ST_ENABLED if ga.state.enabled
                                   else ST_DISABLED)
        except Exception:
            ghost_adapter_state = ST_DISABLED
    for spec in G.ghost_action_specs(shell=ghost_shell):
        domain = _domain_for_action(spec.action_id, spec.adapter)
        caps.append(_from_action_spec(
            spec, domain, state=ghost_adapter_state,
            revocable=spec.tier in (Tier.READ_ONLY, Tier.REVERSIBLE),
            disableable=True))
    if cfg is not None:
        caps.extend(_declared_capabilities(cfg))
        # Reflect real grant authorization into the inventory: AUTHORIZED only
        # when an active grant actually names the capability.
        try:
            grant = power_grant_store(cfg).grant()
            if grant is not None:
                caps = [_as_authorized(c) if c.capability in grant.capabilities
                        else c for c in caps]
        except Exception:
            pass

    by_domain: Dict[str, List[Dict[str, Any]]] = {d: [] for d in POWER_DOMAINS}
    for c in caps:
        by_domain.setdefault(c.domain, []).append(c.to_dict())
    total = len(caps)
    available = sum(1 for c in caps if c.state in (ST_AVAILABLE, ST_ENABLED))
    authorized = sum(1 for c in caps if c.state == ST_AUTHORIZED)
    enabled = sum(1 for c in caps if c.state == ST_ENABLED)
    return {"state": "OK", "schema_version": POWER_SCHEMA_VERSION,
            "generated_at": _now(), "total": total,
            "available": available, "enabled": enabled, "authorized": authorized,
            "domains": {d: {"capabilities": by_domain.get(d, [])}
                        for d in POWER_DOMAINS},
            "capabilities": [c.to_dict() for c in caps]}


def _register_ghost_specs_safe() -> None:
    from . import ghost as G
    try:
        G.register_ghost_specs()
    except Exception:
        pass


def register_ghost_specs_safe() -> None:
    _register_ghost_specs_safe()


# ---------------------------------------------------------------------------
# PowerGrant — one explicit owner-authorized grant (target-bound, expiring)
# ---------------------------------------------------------------------------
@dataclass
class PowerGrant:
    grant_id: str
    owner: str = "user"                    # human only
    created_at: float = 0.0
    expires_at: float = 0.0
    browsers: Tuple[str, ...] = ()         # roles: chromium, comet
    apps: Tuple[str, ...] = ()
    accounts: Tuple[str, ...] = ()
    origins: Tuple[str, ...] = ()          # origin allowlist (http(s) only)
    directories: Tuple[str, ...] = ()
    scopes: Tuple[str, ...] = ()
    capabilities: Tuple[str, ...] = ()     # exact capability ids (required)
    arg_hashes: Tuple[str, ...] = ()       # exact argument hashes (optional)
    revoked: bool = False
    revoked_at: Optional[float] = None
    note: str = ""

    @property
    def is_active(self) -> bool:
        return (not self.revoked and 0 < self.expires_at and
                _now() < self.expires_at)

    def is_active_at(self, now: float) -> bool:
        return not self.revoked and 0 < self.expires_at and now < self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {"grant_id": self.grant_id, "owner": self.owner,
                "created_at": self.created_at, "expires_at": self.expires_at,
                "browsers": list(self.browsers), "apps": list(self.apps),
                "accounts": list(self.accounts), "origins": list(self.origins),
                "directories": list(self.directories),
                "scopes": list(self.scopes),
                "capabilities": list(self.capabilities),
                "arg_hashes": list(self.arg_hashes),
                "revoked": self.revoked,
                "revoked_at": self.revoked_at,
                "active": self.is_active,
                "note": redact_text(self.note)[:200]}

    def _to_record(self) -> Dict[str, Any]:
        """Persisted record: exactly the validated fields, no computed keys.
        The operator note is persisted redacted (never a credential)."""
        return {"grant_id": self.grant_id, "owner": self.owner,
                "created_at": self.created_at, "expires_at": self.expires_at,
                "browsers": list(self.browsers), "apps": list(self.apps),
                "accounts": list(self.accounts), "origins": list(self.origins),
                "directories": list(self.directories),
                "scopes": list(self.scopes),
                "capabilities": list(self.capabilities),
                "arg_hashes": list(self.arg_hashes),
                "revoked": self.revoked,
                "revoked_at": self.revoked_at,
                "note": redact_text(self.note)[:2000]}

    def has_target_binding(self) -> bool:
        return bool(self.browsers or self.apps or self.accounts or
                    self.origins or self.directories or self.scopes or
                    self.capabilities)

    def covers_action(self, capability: str, spec: Optional[ActionSpec],
                      *, scopes: Tuple[str, ...], target: str,
                      role: str, account_scope: str,
                      args_hash: str = "") -> bool:
        """Exact, fail-closed matching used ONLY for routine (non-high-impact)
        capabilities. High-impact capabilities are never auto-allowed here.
        The grant must name the exact capability (and, when bound, an exact
        argument hash); origin/role/account/directory/scope are matched with
        exact boundaries so the grant never widens silently."""
        if not self.is_active:
            return False
        if is_high_impact(capability, spec):
            return False
        if self.capabilities and capability not in self.capabilities:
            return False
        if self.arg_hashes and (not args_hash
                                or args_hash not in self.arg_hashes):
            return False
        if self.browsers and (not role or role not in self.browsers):
            return False
        if role and self.browsers and role not in self.browsers:
            return False
        if self.origins:
            if not target or not _host_matches(target, self.origins):
                return False
        if self.accounts:
            if not account_scope or account_scope not in self.accounts:
                return False
        if self.apps:
            if not (target and _matches_app(target, self.apps)):
                return False
        if self.directories:
            if not target or not _matches_dir(target, self.directories):
                return False
        if self.scopes:
            if not scopes or not set(scopes) <= set(self.scopes):
                return False
        return True


def _matches_app(target: str, apps: Tuple[str, ...]) -> bool:
    t = target.strip().lower()
    for a in apps:
        if a.strip().lower() == t:
            return True
    return False


def _matches_dir(target: str, dirs: Tuple[str, ...]) -> bool:
    t = os.path.normpath(target)
    for d in dirs:
        base = os.path.normpath(d)
        if t == base or t.startswith(base + os.sep):
            return True
    return False


def _validate_grant(raw: Dict[str, Any], *, clock: float,
                    allow_unknown: bool = True) -> Optional[str]:
    """Fail-closed validation of a grant record (issue + load). A loaded grant
    can never gain more authority than a freshly issued one."""
    if not isinstance(raw, dict):
        return "MALFORMED_RECORD"
    register_ghost_specs_safe()
    if allow_unknown:
        unknown = set(raw) - _GRANT_FIELDS
        if unknown:
            return "UNKNOWN_FIELD"
    gid = raw.get("grant_id")
    if not isinstance(gid, str) or not _GRANT_ID_RE.match(gid):
        return "BAD_GRANT_ID"
    if raw.get("owner") != "user":
        return "FORGED_OWNER"
    for key in ("browsers", "apps", "accounts", "origins", "directories",
                "scopes", "capabilities", "arg_hashes"):
        val = raw.get(key)
        if not isinstance(val, (list, tuple)):
            return f"BAD_{key.upper()}"
        for item in val:
            if not isinstance(item, str) or not item.strip():
                return f"BAD_{key.upper()}"
            if item.strip().lower() in _WILDCARD:
                return f"WILDCARD_{key.upper()}"
    browsers = tuple(raw.get("browsers") or ())
    for b in browsers:
        if b not in ("chromium", "comet"):
            return "INVALID_BROWSER"
    for o in raw.get("origins") or ():
        if not _valid_origin(o):
            return "BAD_ORIGIN"
    for s in raw.get("scopes") or ():
        if not _SCOPE_RE.match(s):
            return "INVALID_SCOPE"
    for cap in raw.get("capabilities") or ():
        if not _ID.match(cap):
            return "BAD_CAPABILITY"
        if action_spec(cap) is None:
            return "UNKNOWN_CAPABILITY"
    for h in raw.get("arg_hashes") or ():
        if not re.match(r"^[0-9a-f]{64}$", h):
            return "BAD_ARG_HASH"
    for d in raw.get("directories") or ():
        if not _DIR_RE.match(d) or ".." in d.split(os.sep):
            return "BAD_DIRECTORY"
    try:
        created = float(raw.get("created_at"))
        expires = float(raw.get("expires_at"))
    except (TypeError, ValueError):
        return "BAD_TIMESTAMP"
    if not (created > 0 and expires > 0):
        return "IMPOSSIBLE_TIMESTAMP"
    ttl = expires - created
    if ttl < 1.0 or ttl > MAX_GRANT_TTL_S:
        return "INVALID_TTL"
    if expires <= clock:
        return "EXPIRED"
    note = raw.get("note", "")
    if not isinstance(note, str) or len(note) > 2000:
        return "BAD_NOTE"
    return None


_GRANT_FIELDS = frozenset((
    "grant_id", "owner", "created_at", "expires_at", "browsers",
    "apps", "accounts", "origins", "directories", "scopes",
    "capabilities", "arg_hashes",
    "revoked", "revoked_at", "note"))


class PowerGrantStore:
    """Durable, atomic, cross-process-locked owner power-grant store.

    Exactly ONE active owner grant at a time (issuing a new grant supersedes
    the previous one). Persists to ``<state_dir>/power-grant.json`` with a
    single cross-process flock, unique exclusive temp file, file + directory
    fsync, chmod 0600, typed durability failures, and fail-closed load
    (a malformed/newer/unknown-field record never loads more authority than a
    fresh grant). Save failure surfaces a typed :class:`PowerError` and the
    grant is never reported as issued when persistence failed.
    """

    def __init__(self, path: str | os.PathLike,
                 *, clock: Callable[[], float] = time.time,
                 default_ttl_s: float = DEFAULT_GRANT_TTL_S) -> None:
        self._path = str(path)
        self._clock = clock
        self._ttl = float(default_ttl_s)
        self._lock = threading.RLock()
        self._grant: Optional[PowerGrant] = None
        self._warnings: List[str] = []
        self._degraded = False
        self._load()

    # ------------------------------------------------------------- parse/load
    def _parse_root(self, raw: Any) -> Tuple[Optional[PowerGrant], List[str]]:
        warnings: List[str] = []
        if not isinstance(raw, dict):
            raise PowerError("grant state is not an object",
                             "GRANT_STATE_NOT_OBJECT")
        unknown = set(raw) - {"schema_version", "grant"}
        if unknown:
            raise PowerError(
                "GRANT_STATE_UNKNOWN_TOP_LEVEL: " + ", ".join(sorted(unknown)),
                "GRANT_STATE_UNKNOWN_TOP_LEVEL")
        try:
            schema = int(raw.get("schema_version", POWER_SCHEMA_VERSION))
        except (TypeError, ValueError):
            schema = -1
        if schema > POWER_SCHEMA_VERSION:
            raise PowerError(
                f"GRANT_SCHEMA_NEWER: file schema {schema} > supported "
                f"{POWER_SCHEMA_VERSION}", "GRANT_SCHEMA_NEWER")
        rec = raw.get("grant")
        if rec is None:
            return None, warnings
        if not isinstance(rec, dict):
            raise PowerError("grant record is not an object",
                             "GRANT_RECORD_NOT_OBJECT")
        reason = _validate_grant(rec, clock=self._clock(), allow_unknown=True)
        if reason is not None:
            warnings.append(f"power grant rejected: {reason}")
            return None, warnings
        return PowerGrant(
            grant_id=rec["grant_id"], owner=rec["owner"],
            created_at=float(rec["created_at"]),
            expires_at=float(rec["expires_at"]),
            browsers=tuple(rec.get("browsers") or ()),
            apps=tuple(rec.get("apps") or ()),
            accounts=tuple(rec.get("accounts") or ()),
            origins=tuple(rec.get("origins") or ()),
            directories=tuple(rec.get("directories") or ()),
            scopes=tuple(rec.get("scopes") or ()),
            capabilities=tuple(rec.get("capabilities") or ()),
            arg_hashes=tuple(rec.get("arg_hashes") or ()),
            revoked=bool(rec.get("revoked", False)),
            revoked_at=rec.get("revoked_at"),
            note=str(rec.get("note", ""))), warnings

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError, TypeError) as exc:
            self._degraded = True
            self._warnings.append(f"GRANT_STATE_CORRUPT: "
                                  f"{redact_text(str(exc))[:200]}")
            log_event("power.grant.load.failed", code="GRANT_STATE_CORRUPT",
                      path=self._path)
            return
        try:
            grant, warnings = self._parse_root(raw)
        except PowerError as exc:
            self._degraded = True
            self._warnings.append(f"{exc.code}: {redact_text(str(exc))[:200]}")
            log_event("power.grant.load.failed", code=exc.code,
                      path=self._path)
            return
        self._grant = grant
        self._warnings = warnings
        if warnings:
            self._degraded = bool(warnings)
            log_event("power.grant.load.degraded", warnings=list(warnings),
                      path=self._path)

    # ------------------------------------------------------------- locking
    def _lockfd(self):
        import fcntl
        lock_path = self._path + ".lock"
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise PowerError(f"grant lock open failed: {exc}",
                             "GRANT_LOCK_FAILED") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            os.close(fd)
            raise
        return fd

    @staticmethod
    def _unlockfd(fd) -> None:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _read_latest_locked(self) -> Optional[PowerGrant]:
        if not os.path.exists(self._path):
            return None
        with open(self._path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        grant, _warnings = self._parse_root(raw)
        return grant

    def _write_payload_locked(self, grant: Optional[PowerGrant]) -> None:
        import fcntl  # noqa: F401  (imports kept together for clarity)
        d = self._path.rsplit("/", 1)[0] if "/" in self._path else "."
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as exc:
            raise PowerError(f"grant directory creation failed: {exc}",
                             "GRANT_MKDIR_FAILED") from exc
        payload = json.dumps(
            {"schema_version": POWER_SCHEMA_VERSION,
             "grant": grant._to_record() if grant else None},
            indent=2, sort_keys=True).encode("utf-8")
        tmp = (self._path + f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
        try:
            fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            self._cleanup_tmp(tmp)
            raise PowerError(f"grant write failed: {exc}",
                             "GRANT_WRITE_FAILED") from exc
        try:
            os.chmod(tmp, 0o600)
        except OSError as exc:
            self._cleanup_tmp(tmp)
            raise PowerError(f"grant chmod failed: {exc}",
                             "GRANT_CHMOD_FAILED") from exc
        try:
            os.replace(tmp, self._path)
        except OSError as exc:
            self._cleanup_tmp(tmp)
            raise PowerError(f"grant rename failed: {exc}",
                             "GRANT_RENAME_FAILED") from exc
        try:
            dir_fd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            raise PowerError(
                "grant directory fsync failed after rename: "
                f"{exc}; durability UNCERTAIN", "GRANT_DIR_FSYNC_FAILED",
            ) from exc

    @staticmethod
    def _cleanup_tmp(tmp: str) -> None:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass

    def _locked_update(self, mutate: Callable[
            [Optional[PowerGrant]], Optional[PowerGrant]]) -> Optional[PowerGrant]:
        """Cross-process locked read-modify-write. The flock is ALWAYS released
        (try/finally) on success, read failure, mutate failure, write failure,
        and exception paths — a failed operation can never hang the next one."""
        fd = self._lockfd()
        try:
            try:
                latest = self._read_latest_locked()
            except (OSError, PowerError, ValueError) as exc:
                code = getattr(exc, "code", "GRANT_STATE_CORRUPT")
                raise PowerError(
                    f"cannot read latest grant state: {exc}", code) from exc
            new = mutate(latest)
            self._write_payload_locked(new)
        except PowerDenied:
            # mutate() refused the change (e.g. UNKNOWN_GRANT): release the
            # lock and re-raise without persisting anything.
            raise
        except PowerError:
            raise
        except Exception as exc:
            raise PowerError(f"grant update failed: {exc}",
                             "GRANT_UPDATE_FAILED") from exc
        finally:
            self._unlockfd(fd)
        with self._lock:
            self._grant = new
        return new

    # ------------------------------------------------------------- public
    def issue(self, *, browsers: Tuple[str, ...] = (),
              apps: Tuple[str, ...] = (), accounts: Tuple[str, ...] = (),
              origins: Tuple[str, ...] = (), directories: Tuple[str, ...] = (),
              scopes: Tuple[str, ...] = (), capabilities: Tuple[str, ...] = (),
              arg_hashes: Tuple[str, ...] = (),
              owner: str = "user", issued_by: str = "user",
              ttl_s: Optional[float] = None,
              note: str = "") -> PowerGrant:
        """Issue the ONE owner power grant (supersedes any previous grant).

        Only an authenticated human/owner issuer may issue the grant. Model,
        page, tool, skill, arbitrary text, and automation issuers are rejected
        with ``CONFIRMATION_SOURCE_FORBIDDEN`` before any state changes.
        """
        from .actions import require_human_principal
        try:
            owner = require_human_principal(issued_by)
        except Exception as exc:
            code = getattr(exc, "code", None) or "CONFIRMATION_SOURCE_FORBIDDEN"
            raise PowerDenied(code, f"issuer refused: {exc}") from exc
        if owner != "user":
            raise PowerDenied("CONFIRMATION_SOURCE_FORBIDDEN",
                              "only a human may issue the power grant")
        if not capabilities:
            raise PowerDenied("NO_CAPABILITIES",
                              "power grant must name at least one exact "
                              "capability")
        ttl = float(ttl_s) if ttl_s is not None else self._ttl
        if not (1.0 <= ttl <= MAX_GRANT_TTL_S):
            raise PowerDenied("INVALID_TTL",
                              f"ttl outside [1, {MAX_GRANT_TTL_S}]")
        now = self._clock()
        grant = PowerGrant(
            grant_id=_new_id("pwr"), owner=owner, created_at=now,
            expires_at=now + ttl, browsers=tuple(browsers),
            apps=tuple(apps), accounts=tuple(accounts),
            origins=tuple(origins), directories=tuple(directories),
            scopes=tuple(scopes), capabilities=tuple(capabilities),
            arg_hashes=tuple(arg_hashes), note=note)
        reason = _validate_grant(grant._to_record(), clock=now,
                                 allow_unknown=False)
        if reason is not None:
            raise PowerDenied(reason, "grant failed validation")
        if not grant.has_target_binding():
            raise PowerDenied("NO_TARGET_BINDING",
                              "power grant must bind at least one browser/"
                              "app/account/origin/directory/scope/capability")
        issued = self._locked_update(lambda _prev: grant)
        log_event("power.grant.issued", grant_id=grant.grant_id,
                  expires_at=grant.expires_at, scopes=list(grant.scopes),
                  capabilities=list(grant.capabilities))
        return issued

    def revoke(self, grant_id: str, *, issued_by: str = "user") -> PowerGrant:
        from .actions import require_human_principal
        try:
            require_human_principal(issued_by)
        except Exception as exc:
            code = getattr(exc, "code", None) or "CONFIRMATION_SOURCE_FORBIDDEN"
            raise PowerDenied(code, f"issuer refused: {exc}") from exc

        def _mutate(prev: Optional[PowerGrant]) -> Optional[PowerGrant]:
            if prev is None or prev.grant_id != grant_id:
                raise PowerDenied("UNKNOWN_GRANT", f"no grant {grant_id}")
            prev.revoked = True
            prev.revoked_at = self._clock()
            return prev
        revoked = self._locked_update(_mutate)
        log_event("power.grant.revoked", grant_id=grant_id)
        return revoked

    def reset(self, *, issued_by: str = "user") -> PowerGrant:
        from .actions import require_human_principal
        try:
            require_human_principal(issued_by)
        except Exception as exc:
            code = getattr(exc, "code", None) or "CONFIRMATION_SOURCE_FORBIDDEN"
            raise PowerDenied(code, f"issuer refused: {exc}") from exc

        def _mutate(_prev: Optional[PowerGrant]) -> Optional[PowerGrant]:
            return None
        self._locked_update(_mutate)
        log_event("power.grant.reset")
        return PowerGrant(grant_id="", owner="user")

    def grant(self) -> Optional[PowerGrant]:
        """Return the active grant, re-reading the LATEST on-disk state so a
        grant issued through another store instance (dashboard POST vs CLI, or
        a separate process) is observed consistently. On a corrupt/newer/unreadable
        file the store degrades and returns None (never more authority)."""
        latest = None
        try:
            fd = self._lockfd()
            try:
                latest = self._read_latest_locked()
            finally:
                self._unlockfd(fd)
            with self._lock:
                self._grant = latest
        except (OSError, PowerError, ValueError) as exc:
            with self._lock:
                self._degraded = True
                self._grant = None
                warn = f"{getattr(exc, 'code', 'GRANT_STATE_CORRUPT')}: " \
                       f"{redact_text(str(exc))[:200]}"
                if warn not in self._warnings:
                    self._warnings.append(warn)
            log_event("power.grant.load.failed",
                      code=getattr(exc, "code", "GRANT_STATE_CORRUPT"),
                      path=self._path)
            return None
        g = latest if latest is not None else None
        if g is None:
            return None
        if g.revoked or (g.expires_at and self._clock() >= g.expires_at):
            return None
        return g

    def health(self) -> Dict[str, Any]:
        return {"degraded": self._degraded, "warnings": list(self._warnings),
                "path": self._path}


def power_grant_store(cfg: Any) -> PowerGrantStore:
    path = cfg.get("power", "grant_path", default="") or \
        (cfg.state_dir() / "power-grant.json")
    return PowerGrantStore(os.fspath(path))


def mcp_status(cfg: Any = None) -> Dict[str, Any]:
    """Typed MCP surface for Packet 8O.

    This repository ships no MCP server: the Comet research path is the only
    external research route and it is broker-bound (through GhostRunner), never
    a direct MCP call. Report a truthful typed UNAVAILABLE/DISABLED state and
    never a fake "connected" success.
    """
    from . import ghost as G
    comet_enabled = False
    try:
        if cfg is not None:
            comet_enabled = bool(G.ghost_adapter(cfg).state.enabled)
    except Exception:
        comet_enabled = False
    return {"state": "UNAVAILABLE" if not comet_enabled else "DISABLED",
            "server": "none", "detail": "no MCP server is installed; the "
                                        "Comet research adapter is broker-bound "
                                        "and disabled by default",
            "comet_bound": comet_enabled}


# ---------------------------------------------------------------------------
# PowerControlStore — durable, atomic, cross-process fail-closed control state
# ---------------------------------------------------------------------------
# The emergency-stop and global-disable latches MUST be observable across the
# running bridge, CLI, dashboard, voice, tray, Ghost, Comet/MCP, and automation
# processes, and MUST survive a restart of any one of them. A process-local
# singleton alone is insufficient: if the CLI sets emergency stop while the
# bridge is running in a separate process, the bridge must observe it. This
# store persists the latched control state to ``<state_dir>/power-control.json``
# under a single cross-process flock with unique exclusive temp files, file +
# directory fsync, chmod 0600, typed durability failures, and a FAIL-CLOSED
# load (corrupt / unknown-field / newer-schema / forged-author / stale records
# never load as "cleared" — an unreadable control state denies, it never
# silently un-latches an emergency stop). Circuit breakers and concurrency
# slots remain process-local runtime state (they track live in-process runs);
# the durable latches that must span processes and restarts are the emergency
# stop and the global disable.
POWER_CONTROL_SCHEMA_VERSION = 1
_CONTROL_FIELDS = frozenset((
    "schema_version", "emergency_stop", "global_disable",
    "updated_at", "updated_by", "latched_at"))


class PowerControlStore:
    """Durable, atomic, cross-process-locked latched control-state store.

    Persists ``emergency_stop`` / ``global_disable`` (the latches that must
    span processes and survive restart). Writes are atomic (unique exclusive
    temp + fsync + rename + dir fsync + 0600) under a cross-process flock that
    is ALWAYS released via try/finally on every success/failure path. Loading a
    corrupt / newer-schema / unknown-field / forged-author record NEVER clears
    a latch: it fails closed (the caller treats unreadable state as emergency
    stop). ``updated_by`` records the authenticated human principal; a
    non-human author is refused before any state change.
    """

    def __init__(self, path: str | os.PathLike, *,
                 clock: Callable[[], float] = time.time) -> None:
        self._path = str(path)
        self._clock = clock
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "emergency_stop": False, "global_disable": False}
        self._warnings: List[str] = []
        self._degraded = False

    # ------------------------------------------------------------- parse/load
    def _parse_root(self, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raise PowerError("control state is not an object",
                             "CONTROL_STATE_NOT_OBJECT")
        unknown = set(raw) - _CONTROL_FIELDS
        if unknown:
            raise PowerError(
                "CONTROL_STATE_UNKNOWN_FIELD: " + ", ".join(sorted(unknown)),
                "CONTROL_STATE_UNKNOWN_FIELD")
        try:
            schema = int(raw.get("schema_version",
                                 POWER_CONTROL_SCHEMA_VERSION))
        except (TypeError, ValueError):
            schema = -1
        if schema > POWER_CONTROL_SCHEMA_VERSION:
            raise PowerError(
                f"CONTROL_SCHEMA_NEWER: file schema {schema} > supported "
                f"{POWER_CONTROL_SCHEMA_VERSION}", "CONTROL_SCHEMA_NEWER")
        for key in ("emergency_stop", "global_disable"):
            val = raw.get(key)
            if not isinstance(val, bool):
                raise PowerError(f"CONTROL_STATE_BAD_{key.upper()}",
                                 f"CONTROL_STATE_BAD_{key.upper()}")
        if "updated_by" in raw and raw.get("updated_by") != "user":
            # Only the canonical authenticated human principal may have latched
            # the durable control state; a forged author record is corrupt and
            # fails closed.
            raise PowerError("CONTROL_STATE_FORGED_AUTHOR",
                             "CONTROL_STATE_FORGED_AUTHOR")
        for key in ("updated_at", "latched_at"):
            if key in raw:
                try:
                    ts = float(raw[key])
                except (TypeError, ValueError):
                    raise PowerError(f"CONTROL_STATE_BAD_{key.upper()}",
                                     f"CONTROL_STATE_BAD_{key.upper()}") from None
                if not (ts > 0 and ts == ts):  # reject NaN / non-positive
                    raise PowerError(f"CONTROL_STATE_BAD_{key.upper()}",
                                     f"CONTROL_STATE_BAD_{key.upper()}")
        # A durable record that carries a schema version but NO write timestamp
        # is incomplete/stale — never trust it as "cleared".
        if "schema_version" in raw and "updated_at" not in raw:
            raise PowerError("CONTROL_STATE_NO_TIMESTAMP",
                             "CONTROL_STATE_NO_TIMESTAMP")
        return {"emergency_stop": bool(raw.get("emergency_stop", False)),
                "global_disable": bool(raw.get("global_disable", False))}

    def load(self) -> Dict[str, Any]:
        """Load the latest durable latch state, FAILING CLOSED.

        Returns ``{"emergency_stop": True}`` (deny) when the file is corrupt,
        has an unknown/newer schema, or is otherwise unreadable — an unreadable
        control state must never silently clear an emergency stop. The durable
        file itself is read under the cross-process lock so a concurrent writer
        cannot tear a half-written record.
        """
        if not os.path.exists(self._path):
            return {"emergency_stop": False, "global_disable": False}
        fd = self._lockfd()
        try:
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                parsed = self._parse_root(raw)
                self._data = parsed
                self._degraded = False
                return parsed
            except (OSError, ValueError, TypeError, PowerError) as exc:
                self._degraded = True
                warn = f"{getattr(exc, 'code', 'CONTROL_STATE_CORRUPT')}: " \
                       f"{redact_text(str(exc))[:200]}"
                if warn not in self._warnings:
                    self._warnings.append(warn)
                log_event("power.control.load.failed",
                          code=getattr(exc, "code", "CONTROL_STATE_CORRUPT"),
                          path=self._path)
                # Fail closed: treat unreadable/newer/corrupt state as an
                # emergency stop so no execution is ever allowed on it.
                return {"emergency_stop": True, "global_disable": True}
        finally:
            self._unlockfd(fd)

    # ------------------------------------------------------------- locking
    def _lockfd(self):
        import fcntl
        lock_path = self._path + ".lock"
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise PowerError(f"control lock open failed: {exc}",
                             "CONTROL_LOCK_FAILED") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            os.close(fd)
            raise
        return fd

    @staticmethod
    def _unlockfd(fd) -> None:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _read_latest_locked(self) -> Dict[str, Any]:
        if not os.path.exists(self._path):
            return {"emergency_stop": False, "global_disable": False}
        with open(self._path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return self._parse_root(raw)

    # ------------------------------------------------------------- write
    def set(self, *, emergency_stop: bool, global_disable: bool,
            issued_by: str = "user") -> Dict[str, Any]:
        """Durably persist the latched control state (atomic + cross-process).

        ``issued_by`` must be an authenticated human principal; a model/page/
        tool/skill/automation/arbitrary author is refused with
        ``CONFIRMATION_SOURCE_FORBIDDEN`` before any write. The flock is always
        released (try/finally) on success, read failure, write failure, and
        exception paths.
        """
        from .actions import require_human_principal
        try:
            require_human_principal(issued_by)
        except Exception as exc:
            code = getattr(exc, "code", None) or "CONFIRMATION_SOURCE_FORBIDDEN"
            raise PowerDenied(code, f"control author refused: {exc}") from exc
        now = self._clock()
        payload = {"schema_version": POWER_CONTROL_SCHEMA_VERSION,
                   "emergency_stop": bool(emergency_stop),
                   "global_disable": bool(global_disable),
                   "updated_at": now, "updated_by": "user",
                   "latched_at": now}
        fd = self._lockfd()
        try:
            try:
                self._read_latest_locked()  # validate/parse under the lock
            except (OSError, ValueError, TypeError, PowerError) as exc:
                # Underlying state is corrupt/newer — refuse to "clear" it by
                # writing a fresh record: fail closed instead.
                code = getattr(exc, "code", "CONTROL_STATE_CORRUPT")
                raise PowerError(f"cannot write control over bad state: {exc}",
                                 code) from exc
            self._write_payload_locked(payload)
        finally:
            self._unlockfd(fd)
        with self._lock:
            self._data = {"emergency_stop": bool(emergency_stop),
                          "global_disable": bool(global_disable)}
            self._degraded = False
        return dict(self._data)

    def _write_payload_locked(self, payload: Dict[str, Any]) -> None:
        d = self._path.rsplit("/", 1)[0] if "/" in self._path else "."
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as exc:
            raise PowerError(f"control dir creation failed: {exc}",
                             "CONTROL_MKDIR_FAILED") from exc
        blob = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        tmp = (self._path + f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
        try:
            fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            self._cleanup_tmp(tmp)
            raise PowerError(f"control write failed: {exc}",
                             "CONTROL_WRITE_FAILED") from exc
        try:
            os.chmod(tmp, 0o600)
        except OSError as exc:
            self._cleanup_tmp(tmp)
            raise PowerError(f"control chmod failed: {exc}",
                             "CONTROL_CHMOD_FAILED") from exc
        try:
            os.replace(tmp, self._path)
        except OSError as exc:
            self._cleanup_tmp(tmp)
            raise PowerError(f"control rename failed: {exc}",
                             "CONTROL_RENAME_FAILED") from exc
        try:
            dir_fd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            raise PowerError(
                "control directory fsync failed after rename: "
                f"{exc}; durability UNCERTAIN", "CONTROL_DIR_FSYNC_FAILED",
            ) from exc

    @staticmethod
    def _cleanup_tmp(tmp: str) -> None:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass

    def health(self) -> Dict[str, Any]:
        return {"degraded": self._degraded, "warnings": list(self._warnings),
                "path": self._path}


def power_control_store(cfg: Any) -> PowerControlStore:
    path = cfg.get("power", "control_path", default="") or \
        (cfg.state_dir() / "power-control.json")
    return PowerControlStore(os.fspath(path))


# ---------------------------------------------------------------------------
# PowerControl — emergency stop, global disable, circuit breakers, concurrency
# ---------------------------------------------------------------------------
class _CircuitBreaker:
    """Per-adapter circuit breaker (fail-closed when open)."""

    def __init__(self, *, max_failures: int = 3, window_s: float = 60.0,
                 cooldown_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.max_failures = int(max_failures)
        self.window_s = float(window_s)
        self.cooldown_s = float(cooldown_s)
        self._clock = clock
        self._failures: List[float] = []
        self._open_since: Optional[float] = None
        self._lock = threading.Lock()

    def state(self) -> str:
        with self._lock:
            if self._open_since is not None:
                if self._clock() - self._open_since >= self.cooldown_s:
                    return "half_open"
                return "open"
            return "closed"

    def record_failure(self) -> None:
        now = self._clock()
        with self._lock:
            self._failures = [t for t in self._failures
                              if now - t < self.window_s]
            self._failures.append(now)
            if len(self._failures) >= self.max_failures:
                self._open_since = now
                self._failures = []

    def record_success(self) -> None:
        with self._lock:
            self._failures = []
            self._open_since = None

    def reset(self) -> None:
        with self._lock:
            self._failures = []
            self._open_since = None


class PowerControl:
    """Global power control: emergency stop latch, global disable, per-adapter
    circuit breakers, bounded concurrency, kill/reap tracking, restart reset.

    Hermetic: this object never spawns or kills anything itself. ``track``/
    ``untrack`` hold the cancel callbacks supplied by callers (the same broker
    cancel callback pattern Packet 8N uses), so emergency stop can cancel
    active runs through the caller's own machinery.

    Cross-process durability: when ``state_store`` is supplied (the production
    path via ``power_control(cfg)``), the emergency-stop and global-disable
    latches are persisted to a shared atomic ``0600`` file under a cross-process
    flock and re-loaded on construction, so a CLI/dashboard emergency stop is
    observed by the running bridge / voice / tray / Ghost / Comet / automation
    and survives a restart of any one of them. Mutations are authenticated:
    ``emergency_stop`` / ``global_disable`` / ``global_enable`` / ``reset``
    require an authenticated human principal (``user`` / ``cli:user`` /
    ``dashboard``); a model / page / tool / skill / automation / arbitrary-text
    author is refused with ``CONFIRMATION_SOURCE_FORBIDDEN``. Circuit breakers
    and concurrency slots remain process-local runtime state (they track live
    in-process runs); the durable latches that must span processes and restarts
    are the emergency stop and the global disable.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time,
                 max_concurrency: int = DEFAULT_CONCURRENCY,
                 state_store: Optional[PowerControlStore] = None,
                 fail_closed_on_load: bool = True) -> None:
        self._clock = clock
        self._max_concurrency = int(max_concurrency)
        self._lock = threading.RLock()
        self._breakers: Dict[str, _CircuitBreaker] = {}
        self._slots: Dict[str, int] = {}
        self._active: Dict[str, Callable[[], None]] = {}
        self._state_store = state_store
        self._state = {"enabled": False, "health": "disabled",
                       "emergency_stop": False, "global_disable": False,
                       "adapter": "power", "persisted": bool(state_store)}
        # Load any latched cross-process control state. FAIL CLOSED: an
        # unreadable / corrupt / newer-schema / unknown-field durable state is
        # treated as an emergency stop (never as "cleared"), so a restart can
        # never silently drop a prior emergency stop.
        if state_store is not None:
            try:
                persisted = state_store.load()
            except PowerError as exc:
                if fail_closed_on_load:
                    self._stopped = True
                    self._disabled = True
                    self._state.update(emergency_stop=True,
                                       global_disable=True, enabled=False,
                                       health="degraded")
                    log_event("power.control.load.failed.closed",
                              code=exc.code, path=state_store._path)
                    return
                persisted = {"emergency_stop": False, "global_disable": False}
            self._stopped = bool(persisted.get("emergency_stop", False))
            self._disabled = bool(persisted.get("global_disable", False))
            if self._stopped:
                self._state["emergency_stop"] = True
                self._state["health"] = "degraded"
                self._state["enabled"] = False
            if self._disabled:
                self._state["global_disable"] = True
                self._state["health"] = "disabled"
        else:
            self._stopped = False
            self._disabled = False

    @property
    def state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def refresh(self, *, ttl_s: float = 0.25) -> None:
        """Re-read the durable latch state so a latch set by ANOTHER process
        (e.g. CLI emergency-stop while the bridge runs) is observed here. Fails
        closed: unreadable/corrupt/newer durable state latches emergency stop.
        A short TTL avoids flock contention on hot decision/execution paths
        while keeping cross-process freshness within ~0.25s."""
        if self._state_store is None:
            return
        import time as _time
        now = _time.monotonic()
        if getattr(self, "_refresh_at", 0.0) + ttl_s > now:
            return
        self._refresh_at = now
        try:
            persisted = self._state_store.load()
        except PowerError as exc:
            persisted = {"emergency_stop": True, "global_disable": True}
            log_event("power.control.load.failed.closed",
                      code=exc.code, path=self._state_store._path)
        with self._lock:
            self._stopped = bool(persisted.get("emergency_stop", False))
            self._disabled = bool(persisted.get("global_disable", False))
            self._state["emergency_stop"] = self._stopped
            self._state["global_disable"] = self._disabled
            if self._stopped:
                self._state["health"] = "degraded"
                self._state["enabled"] = False
            elif self._disabled:
                self._state["health"] = "disabled"
            else:
                self._state["health"] = "disabled"

    def _require_human(self, issued_by: str) -> None:
        from .actions import require_human_principal
        try:
            require_human_principal(issued_by)
        except Exception as exc:
            code = getattr(exc, "code", None) or "CONFIRMATION_SOURCE_FORBIDDEN"
            raise PowerDenied(code, f"control issuer refused: {exc}") from exc

    def _persist(self, *, emergency_stop: bool, global_disable: bool,
                 issued_by: str) -> None:
        """Write the latches to the durable store when one is configured.
        A persistence failure is typed and surfaced — it is never silently
        swallowed, so a control mutation can never claim success while the
        durable latch was not recorded."""
        if self._state_store is None:
            return
        self._state_store.set(emergency_stop=emergency_stop,
                              global_disable=global_disable,
                              issued_by=issued_by)

    def emergency_stop(self, *, issued_by: str = "user") -> Dict[str, Any]:
        self._require_human(issued_by)
        self.refresh()
        self._persist(emergency_stop=True, global_disable=False,
                      issued_by=issued_by)
        with self._lock:
            self._stopped = True
            self._state["emergency_stop"] = True
            self._state["global_disable"] = False
            self._state["health"] = "degraded"
            self._state["enabled"] = False
            cancels = list(self._active.values())
            self._active.clear()
        for cancel in cancels:
            try:
                cancel()
            except Exception:
                pass
        log_event("power.emergency_stop", issued_by=issued_by)
        return {"state": "OK", "health": "degraded",
                "detail": "global emergency stop latched; reset required"}

    def reset(self, *, issued_by: str = "user") -> Dict[str, Any]:
        """Clear the latches. REQUIRES explicit human authorization: a model/
        page/tool/skill/automation/arbitrary author is refused and the durable
        latch is left untouched."""
        self._require_human(issued_by)
        self.refresh()
        self._persist(emergency_stop=False, global_disable=False,
                      issued_by=issued_by)
        with self._lock:
            self._stopped = False
            self._disabled = False
            self._state = {"enabled": False, "health": "disabled",
                           "emergency_stop": False, "global_disable": False,
                           "adapter": "power",
                           "persisted": bool(self._state_store)}
            for b in self._breakers.values():
                b.reset()
        log_event("power.reset", issued_by=issued_by)
        return {"state": "OK", "health": "disabled"}

    def global_disable(self, *, issued_by: str = "user") -> Dict[str, Any]:
        self._require_human(issued_by)
        self.refresh()
        self._persist(emergency_stop=False, global_disable=True,
                      issued_by=issued_by)
        with self._lock:
            self._disabled = True
            self._state["global_disable"] = True
            self._state["health"] = "disabled"
        log_event("power.global_disable", issued_by=issued_by)
        return {"state": "OK", "detail": "global disable latched"}

    def global_enable(self, *, issued_by: str = "user") -> Dict[str, Any]:
        self._require_human(issued_by)
        self.refresh()
        with self._lock:
            if self._stopped:
                return {"state": "denied", "code": "EMERGENCY_STOP",
                        "detail": "emergency stop is set; reset required"}
        self._persist(emergency_stop=False, global_disable=False,
                      issued_by=issued_by)
        with self._lock:
            self._disabled = False
            self._state["global_disable"] = False
            self._state["health"] = "enabled"
            self._state["enabled"] = True
        log_event("power.global_enable", issued_by=issued_by)
        return {"state": "OK", "health": "enabled"}

    # ------------------------------------------------------ circuit breakers
    def _breaker(self, adapter: str) -> _CircuitBreaker:
        with self._lock:
            b = self._breakers.get(adapter)
            if b is None:
                b = _CircuitBreaker()
                self._breakers[adapter] = b
            return b

    def breaker_state(self, adapter: str) -> str:
        return self._breaker(adapter).state()

    def record_failure(self, adapter: str) -> None:
        self._breaker(adapter).record_failure()
        log_event("power.circuit.failure", adapter=adapter)

    def record_success(self, adapter: str) -> None:
        self._breaker(adapter).record_success()

    # ------------------------------------------------------------ concurrency
    def acquire(self, adapter: str) -> bool:
        with self._lock:
            cur = self._slots.get(adapter, 0)
            if cur >= self._max_concurrency:
                return False
            self._slots[adapter] = cur + 1
            return True

    def release(self, adapter: str) -> None:
        with self._lock:
            cur = self._slots.get(adapter, 0)
            self._slots[adapter] = max(0, cur - 1)

    # ------------------------------------------------------------ kill/reap
    def track(self, correlation_id: str, cancel: Callable[[], None]) -> None:
        with self._lock:
            self._active[correlation_id] = cancel

    def untrack(self, correlation_id: str) -> None:
        with self._lock:
            self._active.pop(correlation_id, None)

    def active_runs(self) -> List[str]:
        with self._lock:
            return list(self._active)

    def circuit_status(self) -> Dict[str, str]:
        with self._lock:
            return {a: b.state() for a, b in self._breakers.items()}

    def status(self) -> Dict[str, Any]:
        return {"state": "OK", "emergency_stop": self._stopped,
                "global_disable": self._disabled,
                "persisted": bool(self._state_store),
                "max_concurrency": self._max_concurrency,
                "active": len(self._active),
                "circuits": self.circuit_status()}


def power_control(cfg: Any = None, *,
                  max_concurrency: int = DEFAULT_CONCURRENCY) -> PowerControl:
    """Return the shared process-wide PowerControl for a config scope.

    The shared control is bound to the durable cross-process control state
    (``<state_dir>/power-control.json``) so emergency stop / global disable set
    from the CLI, dashboard, or any channel are observed by every other channel
    and survive a restart of any one of them.
    """
    return _shared_control(cfg, max_concurrency=max_concurrency)


def _shared_control(cfg: Any = None, *,
                    max_concurrency: int = DEFAULT_CONCURRENCY) -> PowerControl:
    key = _scope_key(cfg)
    with _SHARED_LOCK:
        ctl = _SHARED_CONTROL.get(key)
        if ctl is None:
            store = None
            if cfg is not None:
                try:
                    store = power_control_store(cfg)
                except Exception:
                    store = None
            ctl = PowerControl(max_concurrency=max_concurrency,
                               state_store=store)
            _SHARED_CONTROL[key] = ctl
        return ctl


# ---------------------------------------------------------------------------
# PowerPolicy — ONE policy path (emergency stop -> disable -> breaker ->
# concurrency -> known -> grant routine -> existing 7E/8F/8N gate)
# ---------------------------------------------------------------------------
class PowerPolicy:
    """The single decision layer every channel (voice, dashboard, CLI, MCP,
    tray, automation) must call before executing a capability.

    ``decide`` returns a typed decision dict and NEVER executes anything. It
    composes the global ``PowerControl``, the owner ``PowerGrantStore``, and
    the existing 7E/8F/8N gate (session grants, explicit grants, fresh
    confirmation). High-impact capabilities always fall through to the gate's
    fresh-confirmation path — the power grant can never auto-allow them.
    """

    def __init__(self, store: Optional[PowerGrantStore] = None,
                 control: Optional[PowerControl] = None,
                 perms: Any = None,
                 session_grants: Any = None,
                 gate: Any = None,
                 confirmations: Any = None,
                 audit_path: Optional[str] = None):
        self.store = store
        self.control = control or PowerControl()
        self.perms = perms
        self.session_grants = session_grants
        self.gate = gate
        self.confirmations = confirmations
        self._audit_path = audit_path
        self._audit_lock = threading.Lock()

    # ------------------------------------------------------------- gate build
    def _ensure_gate(self) -> Any:
        if self.gate is not None:
            return self.gate
        if self.perms is None:
            raise PowerDenied("NO_PERMS_STORE",
                              "a 7E PermissionsStore or gate is required")
        from .actions import ActionGate
        self.gate = ActionGate(self.perms)
        return self.gate

    # ------------------------------------------------------------- decisions
    def decide(self, capability: str, *, scopes: Tuple[str, ...] = (),
               target: str = "", role: str = "", account_scope: str = "",
               source: str = "user", args: Optional[Dict[str, Any]] = None,
               confirmation_id: Optional[str] = None,
               dry_run: bool = False) -> Dict[str, Any]:
        """One policy path. Returns a typed decision dict; never executes."""
        # 0. Refresh the cross-process latch (no-op when the control has no
        #    durable store, e.g. hermetic bare PowerControl()). This lets an
        #    emergency stop / disable set by ANOTHER process (CLI while the
        #    bridge runs, dashboard while the CLI waits) be observed here.
        self.control.refresh()
        spec = self._resolve_spec(capability)
        # 1. Global emergency stop (hard latch).
        if self.control.state.get("emergency_stop"):
            return self._deny(capability, "EMERGENCY_STOP",
                              "global emergency stop is latched; reset required")
        # 2. Global disable.
        if self.control.state.get("global_disable"):
            return self._deny(capability, "POWER_DISABLED",
                              "global power disable is set")
        # 3. Capability must be known.
        if spec is None:
            return self._deny(capability, "UNKNOWN_CAPABILITY",
                              "unknown capability")
        # 4. Per-adapter circuit breaker.
        adapter = getattr(spec, "adapter", "power")
        if self.control.breaker_state(adapter) == "open":
            return self._deny(capability, "CIRCUIT_OPEN",
                              f"adapter {adapter} circuit breaker is open")
        # 5. Bounded concurrency (fail closed rather than queue unboundedly).
        if not dry_run and not self.control.acquire(adapter):
            return self._deny(capability, "CONCURRENCY_LIMIT",
                              f"adapter {adapter} concurrency limit reached")
        try:
            # 6. Owner power grant: routine auto-allow ONLY for an
            #    authenticated human/owner source (or the read-only "check"
            #    probe). model / automation / page / tool / skill / arbitrary
            #    text can NEVER consume a grant — it never silently becomes a
            #    human.
            grant = self.store.grant() if self.store else None
            if grant is not None and source in ("user", "check") and \
                    grant.covers_action(
                        capability, spec, scopes=scopes, target=target,
                        role=role, account_scope=account_scope,
                        args_hash=args_digest(args) if grant.arg_hashes else ""):
                self._audit(capability, "ALLOWED", source,
                            "owner power grant routine", scopes, target)
                return self._allow(capability, "ALLOWED",
                                   "owner power grant routine "
                                   "(read/search/navigate/fill)")
            # 7. High-impact always requires the existing fresh-confirmation
            #    path (never auto, never via grant).
            if is_high_impact(capability, spec):
                return self._gate_decision(capability, spec, scopes, target,
                                           role, account_scope, source,
                                           confirmation_id, dry_run, args)
            # 8. Everything else goes through the shared 7E/8F/8N gate.
            return self._gate_decision(capability, spec, scopes, target,
                                       role, account_scope, source,
                                       confirmation_id, dry_run, args)
        finally:
            if not dry_run:
                self.control.release(adapter)

    def check(self, capability: str, *, scopes: Tuple[str, ...] = (),
              target: str = "", role: str = "", account_scope: str = "",
              source: str = "user") -> Dict[str, Any]:
        return self.decide(capability, scopes=scopes, target=target, role=role,
                           account_scope=account_scope, source=source,
                           dry_run=True)

    # ------------------------------------------------------------- internals
    def _resolve_spec(self, capability: str) -> Optional[ActionSpec]:
        register_ghost_specs_safe()
        return action_spec(capability)

    def _gate_decision(self, capability: str, spec: ActionSpec,
                       scopes: Tuple[str, ...], target: str, role: str,
                       account_scope: str, source: str,
                       confirmation_id: Optional[str],
                       dry_run: bool,
                       args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Delegate to the shared 7E/8F/8N gate (session grants, explicit
        grants, fresh confirmation) preserving existing semantics exactly.
        High-impact confirmations are verified against the ConfirmationStore
        (existence, single-use, expiry, exact capability + args digest) BEFORE
        the gate allows — a bogus/stale/replayed/changed-argument id never
        passes, and nothing is consumed here (the broker consumes once at
        execution)."""
        gate = self._ensure_gate()
        scope = scopes[0] if scopes else (spec.scopes[0] if spec.scopes else "")
        # When the caller passed the full validated envelope (the gate adapter
        # does), use it VERBATIM so confirmation verification and the gate see
        # the exact same envelope the broker will consume. Otherwise build the
        # minimal envelope from the typed fields.
        if isinstance(args, dict) and args.get("action_id"):
            env = dict(args)
        else:
            env = {"action_id": spec.action_id, "scope": scope,
                   "target": target, "role": role,
                   "account_scope": account_scope, "args": dict(args or {})}
        if hasattr(gate, "ghost_grants") and self.session_grants is not None:
            gate.ghost_grants = self.session_grants
        if dry_run:
            translated = self._translate_gate(gate.decide(spec, env,
                                                          source="check"))
            self._audit(capability, translated.get("code", "DENIED"), source,
                        translated.get("detail", "")[:200], scopes, target)
            return translated
        if is_high_impact(capability, spec):
            if source != "user":
                return self._deny(capability, "CONFIRMATION_SOURCE_FORBIDDEN",
                                  f"source={source} cannot carry approvals")
            if not confirmation_id:
                return self._deny(capability, "CONFIRMATION_REQUIRED",
                                  "fresh human confirmation required immediately "
                                  "before execution")
            if self.confirmations is None:
                return self._deny(capability, "CONFIRMATION_UNAVAILABLE",
                                  "no confirmation store configured; high-impact "
                                  "actions fail closed")
            try:
                # The broker binds confirmations to the FULL envelope. The
                # adapter passes that exact envelope through as ``args``;
                # verification uses it (never re-wrapped), so a changed
                # target/origin/account/scope/args/action-hash can never reuse
                # a confirmation. Nothing is consumed here (the broker
                # consumes once at execution).
                verify_target = args if isinstance(args, dict) and \
                    args.get("action_id") else env
                self.confirmations.verify(confirmation_id, spec.action_id,
                                          verify_target)
            except ConfirmationDenied as exc:
                return self._deny(capability, exc.code,
                                  f"confirmation rejected: {exc.code}")
            return self._translate_gate(gate.decide(
                spec, env, source=source, confirmation_id=confirmation_id,
                confirmation_required=True))
        # Non-high-impact: preserve the ORIGINAL source through to the gate.
        # A non-human source reaches the underlying 7E/8F/8N gate as itself
        # (never coerced to "user"), so model/automation/tool text can never
        # reuse a human grant or bypass the confirmation rules.
        translated = self._translate_gate(gate.decide(spec, env, source=source))
        self._audit(capability, translated.get("code", "DENIED"), source,
                    translated.get("detail", "")[:200], scopes, target)
        return translated

    def _translate_gate(self, d: Dict[str, Any]) -> Dict[str, Any]:
        allowed = bool(d.get("allowed"))
        code = d.get("code", "ALLOWED" if allowed else "DENIED")
        return {"allowed": allowed, "code": code,
                "detail": d.get("detail", ""),
                "mode": d.get("mode", "restricted"),
                "confirmation_id": d.get("confirmation_id")}

    def _allow(self, capability: str, code: str, detail: str) -> Dict[str, Any]:
        return {"allowed": True, "code": code, "detail": detail,
                "mode": "power_grant"}

    def _deny(self, capability: str, code: str, detail: str) -> Dict[str, Any]:
        self._audit(capability, code, "policy", detail, (), "")
        return {"allowed": False, "code": code, "detail": detail,
                "mode": "restricted"}

    def _audit(self, capability: str, code: str, source: str, detail: str,
               scopes: Tuple[str, ...], target: str) -> None:
        """Durable, fail-closed audit for power decisions and grant state.

        Writes one redacted JSONL record to a ``0600`` file under a
        cross-process flock with file + directory fsync and complete-write
        accounting. A persistence failure raises a typed :class:`PowerError`
        — audit failures are NEVER silently swallowed (Packet 8O requirement).
        """
        rec = {"ts": _now(), "capability": capability, "code": code,
               "source": source, "detail": redact_text(detail)[:200],
               "scopes": sorted(scopes), "target": redact_text(target)[:120]}
        log_event("power.decision", **rec)
        if not self._audit_path:
            return
        import fcntl
        payload = (json.dumps(rec) + "\n").encode("utf-8")
        lock_path = self._audit_path + ".lock"
        try:
            os.makedirs(os.path.dirname(self._audit_path) or ".", exist_ok=True)
        except OSError as exc:
            raise PowerError(f"power audit dir failed: {exc}",
                             "AUDIT_MKDIR_FAILED", degraded=True) from exc
        try:
            with self._audit_lock:
                lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                    fd = os.open(self._audit_path, os.O_CREAT | os.O_WRONLY
                                 | os.O_APPEND, 0o600)
                    try:
                        written = os.write(fd, payload)
                        if written != len(payload):
                            raise OSError(
                                f"short write {written}/{len(payload)}")
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    dir_fd = os.open(
                        os.path.dirname(self._audit_path) or ".",
                        os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
        except OSError as exc:
            raise PowerError(f"power audit persistence failed: {exc}",
                             "AUDIT_PERSIST_FAILED", degraded=True) from exc


def power_policy(cfg: Any = None, *,
                 store: Optional[PowerGrantStore] = None,
                 control: Optional[PowerControl] = None,
                 perms: Any = None,
                 session_grants: Any = None,
                 gate: Any = None,
                 confirmations: Any = None,
                 audit_path: Optional[str] = None,
                 shared: bool = True) -> PowerPolicy:
    """Return the SHARED PowerPolicy for the config scope (one per process).

    Reuses the shared :class:`PowerControl` so every channel sees the same
    emergency-stop/disable/circuit/concurrency state. A durable audit JSONL is
    written under ``<state_dir>/power-audit.jsonl`` (fail closed on write
    error). Pass ``shared=False`` for isolated hermetic tests.
    """
    if shared and cfg is not None:
        key = _scope_key(cfg)
        with _SHARED_LOCK:
            cached = _SHARED_POLICY.get(key)
            if cached is not None:
                return cached
    if store is None and cfg is not None:
        store = power_grant_store(cfg)
    if perms is None and cfg is not None:
        from .permissions import permissions_store
        perms = permissions_store(cfg)
    if session_grants is None and cfg is not None:
        from . import ghost as G
        session_grants = G.session_grants_store(cfg)
    if confirmations is None and cfg is not None:
        from .actions import actions_store
        store_a = actions_store(cfg)
        from .actions import PersistentConfirmationStore
        confirmations = PersistentConfirmationStore(store_a)
    if audit_path is None and cfg is not None:
        audit_path = str(cfg.state_dir() / "power-audit.jsonl")
    policy = PowerPolicy(store=store,
                         control=control or power_control(cfg),
                         perms=perms, session_grants=session_grants,
                         gate=gate, confirmations=confirmations,
                         audit_path=audit_path)
    if shared and cfg is not None:
        with _SHARED_LOCK:
            _SHARED_POLICY[_scope_key(cfg)] = policy
    return policy


class PowerGateAdapter:
    """Gate-compatible adapter that routes runner decisions through PowerPolicy.

    Implements the same ``decide(spec, envelope, *, source, confirmation_id,
    confirmation_required)`` interface as the 8F ``ActionGate`` and 8N
    ``GhostGate``, so the shared :class:`PowerPolicy` can be used AS the gate
    of ``ActionRunner``/``GhostRunner``. Every preview/check/execute/retry/
    recover path then flows through the ONE policy path (emergency stop ->
    disable -> circuit -> concurrency -> grant routine -> existing gate)
    without changing any other executor behavior.
    """

    def __init__(self, policy: "PowerPolicy", inner_gate: Any = None):
        self.policy = policy
        self.inner_gate = inner_gate

    def decide(self, spec: ActionSpec, envelope: Dict[str, Any], *,
               source: str, confirmation_id: Optional[str] = None,
               confirmation_required: bool = False) -> Dict[str, Any]:
        args = dict((envelope.get("args") or {}) if isinstance(
            envelope, dict) else {})
        scope = str(envelope.get("scope", "")) if isinstance(
            envelope, dict) else ""
        target = str(envelope.get("target", "")) if isinstance(
            envelope, dict) else ""
        role = str(args.get("role", "")) if isinstance(args, dict) else \
            (str(envelope.get("role", "")) if isinstance(envelope, dict) else "")
        account = str(envelope.get("account_scope", "")) if isinstance(
            envelope, dict) else ""
        scopes = tuple(s.strip() for s in str(scope).split(",") if s.strip())
        dry = source == "check"
        # Source identity is PRESERVED through the policy path. model /
        # automation / page / tool / skill / arbitrary text must never be
        # converted to "user": the policy and the underlying gate deny them
        # (no grant auto-allow, no confirmation reuse, no high-impact
        # approval). Only an authenticated human channel reaches the gate as
        # "user"; "check" is the read-only probe source.
        d = self.policy.decide(
            spec.action_id, scopes=scopes, target=target, role=role,
            account_scope=account, source=source,
            args=dict(envelope) if isinstance(envelope, dict) else args,
            confirmation_id=confirmation_id, dry_run=dry)
        if d.get("allowed") is False and dry and d.get("code") == \
                "CONFIRMATION_REQUIRED":
            return {"allowed": False, "code": "CONFIRMATION_REQUIRED",
                    "mode": "restricted",
                    "detail": d.get("detail", "")}
        return {"allowed": bool(d.get("allowed")),
                "code": d.get("code", "ALLOWED" if d.get("allowed") else "DENIED"),
                "mode": d.get("mode", "restricted"),
                "detail": d.get("detail", ""),
                "confirmation_id": d.get("confirmation_id")}