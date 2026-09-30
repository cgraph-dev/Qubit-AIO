"""Packet 8N: Ghost desktop/browser capability adapter (optional, supervised).

Evaluates and, where justified, integrates the Ghost computer-use contract as
an OPTIONAL supervised adapter through the existing CapabilityBroker and
ActionBroker. It never vendors the Rust project; it defines the typed
capability surface, two explicit browser roles (Chromium account browser and
Comet research browser), a bounded session-grant model, adapter health states,
emergency stop, redaction, and a research-only mcp-comet contract — all
registered through the exact Packet 8F action machinery (envelope, action
hash, confirmation, gate, receipts, cancellation, audit).

Invariants enforced here (each has a test):

- Every capability is registered through the existing broker with a scope,
  target/origin, risk tier, timeout, resource limit, cancellation path, audit
  receipt, and adapter health state. NO direct HTTP/MCP/voice bypass exists.
- All Ghost capabilities are DISABLED by default. Live execution requires an
  explicitly enabled adapter AND an injected executor; without it every call
  returns the typed ADAPTER_DISABLED result (mirroring 8F).
- ``GHOST_SHELL`` is ``off`` by default and the shell capability is simply NOT
  registered until the operator explicitly enables it. Coordinate-only actions,
  clipboard reads, browser/CDP control, account actions, file writes, external
  sends, purchases, deletes, and credential/security changes are high-impact
  and always require a fresh human confirmation bound to the exact action hash.
- Read-only/reversible actions may use a bounded user-issued session grant
  limited by target, origin, scope, time, and resource budget. There is no
  approve-everything switch.
- Two browser roles with DISTINCT loopback-only CDP ports, profiles, target
  registries and account/origin scopes. Cookies/local storage are never copied
  between them and there is no wrong-browser fallback.
- mcp-comet is research-only: connect, select mode, submit a bounded query,
  poll/wait, extract sources, extract readable page content, and bounded
  evidence screenshots. Its ``computer`` mode and write-capable tools are
  disabled until Packet 8O.
- Model/page/skill/tool output can never authorize or widen a grant; the
  session-grant store refuses non-human issuers and the ActionGate refuses
  non-human confirmation sources.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .actions import (
    ActionError, ActionSpec, ActionValidationError, ActionGate, ActionRunner,
    ActionStore, ADAPTER_GHOST, RB_IMPOSSIBLE, RB_NONE, RB_REVERSIBLE,
    ST_ADAPTER_DISABLED, ST_OK, _render_preview,
    register_action_spec,
)
from .broker import CapabilityBroker, ToolBinding
from .capabilities import CapabilitySpec, Tier
from .logging_setup import log_event
from .permissions import MODE_RESTRICTED
from .redaction import redact_text

# ---------------------------------------------------------------------------
# Ghost adapter identity + hard disable contract
# ---------------------------------------------------------------------------

GHOST_SHELL_ENV = "VOICE_BRIDGE_GHOST_SHELL"
GHOST_SHELL_DEFAULT = "off"
GHOST_SHELL_ON = ("on", "1", "true", "yes")


def ghost_shell_enabled(env: Optional[Dict[str, str]] = None) -> bool:
    """GHOST_SHELL is off unless the operator explicitly enables the adapter.

    The shell capability is only registered when this returns True; the packet
    requires a hard disable by default.
    """
    env = os.environ if env is None else env
    return (env.get(GHOST_SHELL_ENV, GHOST_SHELL_DEFAULT).strip().lower()
            in GHOST_SHELL_ON)


# ---------------------------------------------------------------------------
# Browser roles (two explicit, isolated roles)
# ---------------------------------------------------------------------------
ROLE_CHROMIUM = "chromium"
ROLE_COMET = "comet"
BROWSER_ROLES = (ROLE_CHROMIUM, ROLE_COMET)

# Distinct default loopback-only CDP ports. Never configurable off loopback.
_CHROMIUM_CDP_PORT = 9222
_COMET_CDP_PORT = 9223
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")

# Per-role approved account/origin scopes (documented, enforced at bind time).
_CHROMIUM_ORIGINS = ("gmail.com", "mail.google.com", "messenger.com",
                     "instagram.com", "facebook.com")
_COMET_ORIGINS = ("perplexity.ai",)


def _parse_origin(url: str) -> Tuple[str, str]:
    """Strictly parse a browser-role target URL into (scheme, canonical_host).

    Fail-closed rules (each tested):
      * only https (or http for loopback test fixtures) with an explicit host;
      * no user-info (``user:pass@``) anywhere;
      * no non-default port (https 443 / http 80 only);
      * no query string or fragment (credential/token-bearing targets are
        rejected where prohibited);
      * host must be a DNS name (no IP, no ``@``, no empty label).
    """
    if not isinstance(url, str) or not url.strip():
        raise ActionValidationError("browser-role target must be a URL")
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise ActionValidationError(f"malformed browser target {url!r}: {exc}")
    scheme = (parts.scheme or "").lower()
    if scheme != "https":
        raise ActionValidationError(
            f"unsupported scheme {parts.scheme!r}; only https allowed for "
            "browser-role origins")
    if not parts.hostname:
        raise ActionValidationError(f"browser target {url!r} has no host")
    if parts.username or parts.password:
        raise ActionValidationError(
            f"browser target {url!r} carries user-info; rejected")
    expected_port = 443 if scheme == "https" else 80
    if parts.port is not None and parts.port != expected_port:
        raise ActionValidationError(
            f"unexpected port {parts.port} in browser target {url!r}")
    if parts.query or parts.fragment:
        raise ActionValidationError(
            f"browser target {url!r} carries query/fragment; token-bearing "
            "targets are rejected")
    host = parts.hostname.rstrip(".").lower()
    if not host or any(not label for label in host.split(".")):
        raise ActionValidationError(f"invalid host in browser target {url!r}")
    if re.fullmatch(r"\d+(\.\d+){3}|[0-9a-f:]+", host):
        raise ActionValidationError(
            f"browser target {url!r} uses an IP literal; DNS host required")
    return scheme, host


def _host_in_approved(host: str, approved: Tuple[str, ...]) -> bool:
    """Exact host or a legitimate subdomain boundary of an approved origin.

    ``evilgmail.com``, ``gmail.com.evil.test`` and ``notmail.google.com`` are
    NOT matched; ``mail.gmail.com`` / ``www.gmail.com`` ARE. The approved entry
    ``mail.google.com`` also anchors its own subdomains.
    """
    low = host.lower().rstrip(".")
    for a in approved:
        a = a.lower().rstrip(".")
        if low == a:
            return True
        if low.endswith("." + a):
            return True
        # If the approved entry itself is a subdomain (e.g. mail.google.com),
        # the label immediately before it must belong to that subdomain only:
        # notmail.google.com is NOT mail.google.com. The endswith("."+a) above
        # already handles mail.google.com subdomains correctly, so no extra
        # logic is required here.
    return False


@dataclass(frozen=True)
class BrowserRole:
    """One isolated browser role: distinct CDP port, profile, target registry."""
    role: str
    cdp_port: int
    profile: str
    approved_origins: Tuple[str, ...]
    account_scopes: Tuple[str, ...] = ()

    def bind_origin(self, url: str) -> str:
        """Strictly validate a URL against this role's approved origins.

        Returns the canonical ``scheme://host``. Rejects user-info, malformed
        URLs, unexpected ports, unsupported schemes, query/fragment credential
        or token-bearing targets, bare/non-URL targets, IP literals, and any
        host that is not the exact approved origin or a legitimate subdomain
        boundary.
        """
        scheme, host = _parse_origin(url)
        if not _host_in_approved(host, self.approved_origins):
            raise ActionValidationError(
                f"origin {url!r} is not approved for browser role "
                f"{self.role!r} (approved: {', '.join(self.approved_origins)})")
        return f"{scheme}://{host}"

    @staticmethod
    def validate_port(port: int) -> int:
        port = int(port)
        if not (1024 <= port <= 65535):
            raise ActionValidationError(f"CDP port {port} out of range")
        return port

    def bind_account_scope(self, account_scope: str) -> str:
        """Validate an account scope against this role's declared scopes."""
        if not account_scope:
            return ""
        if account_scope not in self.account_scopes:
            raise ActionValidationError(
                f"account scope {account_scope!r} is not valid for browser "
                f"role {self.role!r} (allowed: "
                f"{', '.join(self.account_scopes)})")
        return account_scope

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "cdp_port": self.cdp_port,
                "profile": self.profile,
                "approved_origins": list(self.approved_origins),
                "account_scopes": list(self.account_scopes)}


def browser_roles() -> Dict[str, BrowserRole]:
    """The two explicit, distinct browser roles (never merged/fallback)."""
    return {
        ROLE_CHROMIUM: BrowserRole(
            role=ROLE_CHROMIUM, cdp_port=_CHROMIUM_CDP_PORT,
            profile="chromium-account", approved_origins=_CHROMIUM_ORIGINS,
            account_scopes=("gmail", "messenger", "instagram", "marketplace")),
        ROLE_COMET: BrowserRole(
            role=ROLE_COMET, cdp_port=_COMET_CDP_PORT,
            profile="comet-research", approved_origins=_COMET_ORIGINS,
            account_scopes=("research",)),
    }


def validate_role(role: str) -> BrowserRole:
    if role not in BROWSER_ROLES:
        raise ActionValidationError(
            f"unknown browser role {role!r}; expected one of {BROWSER_ROLES}")
    return browser_roles()[role]


def validate_cdp_endpoint(port: int, host: str = "127.0.0.1") -> None:
    """Loopback-only CDP enforcement: never a public listener."""
    host = (host or "").strip().lower()
    if host not in LOOPBACK_HOSTS:
        raise ActionValidationError(
            f"CDP host {host!r} must be loopback-only")
    BrowserRole.validate_port(port)


# ---------------------------------------------------------------------------
# Ghost capability contracts (registered via the 8F registry)
# ---------------------------------------------------------------------------
GHOST_NS = "ghost"

# Arg schemas (all bounded, no coordinates, no shell, no JS).
_DESKTOP_SCHEMA = {"query": {"type": "str", "required": False, "max": 200}}
_LAUNCH_SCHEMA = {"app": {"type": "str", "required": True, "max": 60,
                          "kind": "app"}}
_WINDOW_SCHEMA = {"window": {"type": "str", "required": True, "max": 200}}
_BROWSER_SCHEMA = {"role": {"type": "str", "required": True, "max": 16,
                            "choices": list(BROWSER_ROLES)}}
_CLICK_SCHEMA = {"role": {"type": "str", "required": True, "max": 16,
                          "choices": list(BROWSER_ROLES)},
                 "target": {"type": "str", "required": True, "max": 200},
                 "action": {"type": "str", "required": False, "max": 40}}
_TYPE_SCHEMA = {"role": {"type": "str", "required": True, "max": 16,
                         "choices": list(BROWSER_ROLES)},
                "target": {"type": "str", "required": True, "max": 200},
                "text": {"type": "str", "required": True, "max": 1000}}
_SCROLL_SCHEMA = {"role": {"type": "str", "required": True, "max": 16,
                           "choices": list(BROWSER_ROLES)},
                  "target": {"type": "str", "required": True, "max": 200},
                  "direction": {"type": "str", "required": False, "max": 16,
                                "choices": ["up", "down", "left", "right"]}}
_CLIPBOARD_SCHEMA = {"role": {"type": "str", "required": True, "max": 16,
                              "choices": list(BROWSER_ROLES)}}
_SCREENSHOT_SCHEMA = {"role": {"type": "str", "required": True, "max": 16,
                               "choices": list(BROWSER_ROLES)},
                      "target": {"type": "str", "required": False, "max": 200},
                      "clip": {"type": "str", "required": False, "max": 200}}
# Supervised desktop-control schemas (8V via the Ghost broker; all bounded,
# semantic targets only — never raw coordinates, never shell, never JS).
_DESKTOP_SCREENSHOT_SCHEMA = {
    "window": {"type": "str", "required": False, "max": 200},
    "clip": {"type": "str", "required": False, "max": 200}}
_NAMED_KEYS = ("Enter", "Tab", "Escape", "Backspace", "Delete",
               "Up", "Down", "Left", "Right", "Home", "End",
               "PageUp", "PageDown",
               "F1", "F2", "F3", "F4", "F5", "F6",
               "F7", "F8", "F9", "F10", "F11", "F12")
_KEY_SCHEMA = {"target": {"type": "str", "required": True, "max": 200},
               "key": {"type": "str", "required": True, "max": 16,
                       "choices": list(_NAMED_KEYS)}}
_CURSOR_SCHEMA = {"target": {"type": "str", "required": True, "max": 200}}
_WINDOW_CLOSE_SCHEMA = {"window": {"type": "str", "required": True,
                                   "max": 200}}
_SHELL_SCHEMA = {"argv": {"type": "str", "required": True, "max": 2000}}

# Comet research-only capability schemas (all bounded; research mode only).
_COMET_ASK_SCHEMA = {
    "mode": {"type": "str", "required": False, "max": 16,
             "choices": ["research"]},
    "query": {"type": "str", "required": True, "max": 2000},
    "max_sources": {"type": "int", "required": False, "min": 1, "max": 20},
    "max_chars": {"type": "int", "required": False, "min": 1, "max": 200000},
    "timeout_s": {"type": "int", "required": False, "min": 1, "max": 120},
    "capture_screenshot": {"type": "bool", "required": False},
    "screenshot_max_bytes": {"type": "int", "required": False,
                             "min": 1, "max": 1048576},
}
_COMET_WAIT_SCHEMA = {
    "wait_id": {"type": "str", "required": True, "max": 40},
    "max_wait_s": {"type": "int", "required": False, "min": 1, "max": 300},
}
_COMET_EXTRACT_SCHEMA = {
    "source": {"type": "str", "required": False, "max": 200000},
    "max_chars": {"type": "int", "required": False, "min": 1, "max": 200000},
}
_COMET_EVIDENCE_SCHEMA = {
    "data_b64": {"type": "str", "required": False, "max": 2000000},
    "max_bytes": {"type": "int", "required": False, "min": 1, "max": 1048576},
}
_COMET_MODE_SCHEMA = {
    "mode": {"type": "str", "required": True, "max": 16,
             "choices": ["research"]},
}
_COMET_CONNECT_SCHEMA = {
    "session": {"type": "str", "required": False, "max": 64},
}


def _g(action_id: str, version: str, tier: Tier, summary: str,
        scopes: Tuple[str, ...], arg_schema: Dict[str, Any],
        side_effects: str, rollback_kind: str, rollback: str,
        preview_fields: Tuple[str, ...], *,
        sensitivity: str = "personal", timeout_s: float = 30.0) -> ActionSpec:
    return ActionSpec(action_id=action_id, version=version, adapter=ADAPTER_GHOST,
                      tier=tier, sensitivity=sensitivity, summary=summary,
                      scopes=scopes, arg_schema=arg_schema,
                      timeout_s=timeout_s, side_effects=side_effects,
                      rollback_kind=rollback_kind, rollback=rollback,
                      preview_fields=preview_fields)


def ghost_action_specs(shell: bool = False) -> Tuple[ActionSpec, ...]:
    """Typed Ghost capabilities. ``shell`` only adds the shell capability when
    the operator explicitly enabled GHOST_SHELL; it is omitted by default."""
    base = (
        _g(f"{GHOST_NS}.desktop.discover", "1.0.0", Tier.READ_ONLY,
           "discover desktop windows/apps (bounded, read-only)",
           ("desktop",), _DESKTOP_SCHEMA,
           "reads a bounded desktop/window inventory",
           RB_NONE, "no side effect; nothing to undo", ("query",)),
        _g(f"{GHOST_NS}.desktop.launch", "1.0.0", Tier.REVERSIBLE,
           "launch an allowlisted desktop application (launcher only; no app control)",
           ("desktop",), _LAUNCH_SCHEMA,
           "opens one allowlisted application (no window/automation control)",
           RB_REVERSIBLE, "close the launched app (best effort; no control claim)",
           ("app",)),
        _g(f"{GHOST_NS}.window.anchor", "1.0.0", Tier.REVERSIBLE,
           "anchor/focus a window by bounded identifier (no coordinates)",
           ("desktop",), _WINDOW_SCHEMA,
           "brings a matching window into view; does not move/click",
           RB_REVERSIBLE, "restore prior focus (best effort)",
           ("window",)),
        _g(f"{GHOST_NS}.browser.tabs", "1.0.0", Tier.READ_ONLY,
           "inspect tabs of one isolated browser role (read-only)",
           ("web",), _BROWSER_SCHEMA,
           "reads a bounded tab inventory from the role's loopback CDP",
           RB_NONE, "no side effect; nothing to undo", ("role",)),
        _g(f"{GHOST_NS}.browser.screenshot", "1.0.0", Tier.READ_ONLY,
           "capture a clipped, redacted, hash-linked screenshot region",
           ("web",), _SCREENSHOT_SCHEMA,
           "captures a bounded clipped screenshot; stored redacted + hashed",
           RB_NONE, "no side effect; nothing to undo", ("role", "target",
                                                       "clip"),
           sensitivity="internal"),
        _g(f"{GHOST_NS}.input.click", "1.0.0", Tier.MUTATING,
           "explicitly permitted click at a semantic target (no coordinates)",
           ("web", "desktop"), _CLICK_SCHEMA,
           "sends one click to the exact semantic target (foreground input)",
           RB_REVERSIBLE, "no reliable undo for a click; verified receipt only",
           ("role", "target", "action")),
        _g(f"{GHOST_NS}.input.type", "1.0.0", Tier.MUTATING,
           "explicitly permitted typing into a semantic field (no coordinates)",
           ("web", "desktop"), _TYPE_SCHEMA,
           "types text into the exact semantic target (foreground input)",
           RB_REVERSIBLE, "clear the field / restore prior value",
           ("role", "target", "text")),
        _g(f"{GHOST_NS}.input.scroll", "1.0.0", Tier.REVERSIBLE,
           "scroll one semantic target in a bounded direction",
           ("web", "desktop"), _SCROLL_SCHEMA,
           "scrolls the exact semantic target; no coordinates",
           RB_REVERSIBLE, "scroll back (best effort)",
           ("role", "target", "direction")),
        _g(f"{GHOST_NS}.clipboard.read", "1.0.0", Tier.MUTATING,
           "read the clipboard of one browser role (high-impact, redacted)",
           ("web",), _CLIPBOARD_SCHEMA,
           "reads clipboard content; output is redacted and never persisted raw",
           RB_NONE, "no side effect; nothing to undo", ("role",),
           sensitivity="secret"),
        _g(f"{GHOST_NS}.desktop.screenshot", "1.0.0", Tier.READ_ONLY,
           "capture a clipped, redacted, hash-linked desktop screenshot",
           ("desktop",), _DESKTOP_SCREENSHOT_SCHEMA,
           "captures a bounded clipped desktop region; stored redacted +"
           " hashed; X11/Wayland backend selected from the live session",
           RB_NONE, "no side effect; nothing to undo", ("window", "clip"),
           sensitivity="internal"),
        _g(f"{GHOST_NS}.input.key", "1.0.0", Tier.MUTATING,
           "explicitly permitted named key press on a semantic target",
           ("web", "desktop"), _KEY_SCHEMA,
           "sends one named key (allowlisted, no chords, no text) to the"
           " exact semantic target (foreground input)",
           RB_REVERSIBLE, "no reliable undo for a key press; verified receipt"
                          " only",
           ("target", "key")),
        _g(f"{GHOST_NS}.input.cursor", "1.0.0", Tier.REVERSIBLE,
           "move the cursor to a semantic target (no click, no coordinates)",
           ("web", "desktop"), _CURSOR_SCHEMA,
           "moves the cursor to the exact semantic target; clicks nothing",
           RB_REVERSIBLE, "move the cursor back (best effort)",
           ("target",)),
        _g(f"{GHOST_NS}.window.close", "1.0.0", Tier.MUTATING,
           "close a window by bounded identifier (confirmed, grant-bound)",
           ("desktop",), _WINDOW_CLOSE_SCHEMA,
           "closes the matching window; unsaved work may be lost",
           RB_IMPOSSIBLE, "a closed window cannot be reliably restored",
           ("window",)),
        # Comet research-only operations (broker-bound; research mode only).
        _g(f"{GHOST_NS}.comet.connect", "1.0.0", Tier.READ_ONLY,
           "connect the research-only Comet adapter (disabled by default)",
           ("research",), _COMET_CONNECT_SCHEMA,
           "connects the research adapter (no account/browser access)",
           RB_NONE, "no persistent side effect; disconnect on request",
           ()),
        _g(f"{GHOST_NS}.comet.disconnect", "1.0.0", Tier.READ_ONLY,
           "disconnect the research-only Comet adapter",
           ("research",), _COMET_CONNECT_SCHEMA,
           "disconnects the research adapter",
           RB_NONE, "no persistent side effect; nothing to undo",
           ()),
        _g(f"{GHOST_NS}.comet.mode", "1.0.0", Tier.READ_ONLY,
           "select research mode (computer/labs/unknown modes are refused)",
           ("research",), _COMET_MODE_SCHEMA,
           "selects research mode only",
           RB_NONE, "no persistent mode change to undo", ("mode",)),
        _g(f"{GHOST_NS}.comet.ask", "1.0.0", Tier.READ_ONLY,
           "submit a bounded research query (research-only)",
           ("research",), _COMET_ASK_SCHEMA,
           "submits a bounded research query; nothing is sent externally",
           RB_NONE, "no side effect; nothing to undo",
           ("mode", "query", "max_sources")),
        _g(f"{GHOST_NS}.comet.wait", "1.0.0", Tier.READ_ONLY,
           "bounded research poll with typed timeout/cancel/unavailable states",
           ("research",), _COMET_WAIT_SCHEMA,
           "polls a bounded research result; never fakes completion",
           RB_NONE, "no side effect; nothing to undo", ("wait_id",)),
        _g(f"{GHOST_NS}.comet.extract", "1.0.0", Tier.READ_ONLY,
           "bounded redacted source/page extraction (untrusted evidence)",
           ("research",), _COMET_EXTRACT_SCHEMA,
           "extracts bounded redacted text; never authorizes an action",
           RB_NONE, "no side effect; nothing to undo", ("max_chars",)),
        _g(f"{GHOST_NS}.comet.evidence", "1.0.0", Tier.READ_ONLY,
           "bounded redacted evidence screenshot reference",
           ("research",), _COMET_EVIDENCE_SCHEMA,
           "stores a bounded hashed evidence reference; never raw bytes",
           RB_NONE, "no side effect; nothing to undo", ("max_bytes",),
           sensitivity="internal"),
    )
    if shell:
        base = base + (
            _g(f"{GHOST_NS}.shell", "1.0.0", Tier.MUTATING,
               "run an argv-only shell command (explicit operator enable only)",
               ("shell",), _SHELL_SCHEMA,
               "executes an argv-only command with a strict timeout",
               RB_IMPOSSIBLE, "arbitrary shell output cannot be rolled back",
               ("argv",), sensitivity="secret"),
        )
    return base


_GHOST_SPECS: Dict[str, ActionSpec] = {}


def register_ghost_specs(env: Optional[Dict[str, str]] = None) -> int:
    """Register the Ghost capability contracts (idempotent). Returns count.

    The shell capability is only registered when GHOST_SHELL is explicitly on;
    turning it off removes the capability from the live registry.
    """
    _GHOST_SPECS.clear()
    shell = ghost_shell_enabled(env)
    for spec in ghost_action_specs(shell=shell):
        _GHOST_SPECS[spec.action_id] = spec
        try:
            register_action_spec(spec)
        except Exception:
            pass  # already registered in this process
    return len(_GHOST_SPECS)


def ghost_spec(action_id: str) -> Optional[ActionSpec]:
    """Ghost-authoritative resolver: only currently-registered Ghost specs.

    Falls back to the shared 8F registry so a Ghost runner can resolve every
    action; ``ghost.shell`` is only present here while GHOST_SHELL is on, so
    toggling it off removes the capability from Ghost execution entirely.
    """
    spec = _GHOST_SPECS.get(action_id)
    if spec is not None:
        return spec
    if action_id.startswith(f"{GHOST_NS}."):
        return None  # a Ghost id not currently registered is never resolvable
    return action_spec(action_id)


def ghost_action_ids() -> Tuple[str, ...]:
    return tuple(sorted(_GHOST_SPECS))


# ---------------------------------------------------------------------------
# Session grants (bounded user-issued; read/reversible only)
# ---------------------------------------------------------------------------
class SessionGrantError(Exception):
    """Typed failure for the Ghost session-grant layer.

    ``code`` carries a stable machine-readable reason and ``degraded``/``path``
    give the recovery/rollback context for persistence failures.
    """

    def __init__(self, message: str, *, code: str = "GRANT_ERROR",
                 degraded: bool = False, path: str = ""):
        super().__init__(message)
        self.code = code
        self.degraded = bool(degraded)
        self.path = path


_GRANT_FIELDS = frozenset({
    "grant_id", "capability", "scopes", "target", "role", "issued_by",
    "created_at", "expires_at", "resource_budget", "active",
})
_GRANT_SCHEMA_VERSION = 1
_GRANT_FILE_FIELDS = frozenset({"schema_version", "grants"})
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_./:-]{0,127}$")
_GRANT_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_WILDCARD_TARGETS = frozenset({"", "*", "**", "any", "all"})


@dataclass(frozen=True)
class SessionGrant:
    grant_id: str
    capability: str          # one ghost capability id
    scopes: Tuple[str, ...]
    target: str              # origin or bounded window/app target
    role: str = ""           # browser role binding (empty = not browser-bound)
    issued_by: str = "user"  # human-only; model/automation refused
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    resource_budget: int = 1_000_000

    @property
    def is_active(self) -> bool:
        return time.time() < self.expires_at

    def is_active_at(self, now: float) -> bool:
        return now < self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {"grant_id": self.grant_id, "capability": self.capability,
                "scopes": list(self.scopes), "target": redact_text(self.target),
                "role": self.role, "issued_by": self.issued_by,
                "created_at": self.created_at, "expires_at": self.expires_at,
                "resource_budget": self.resource_budget,
                "active": self.is_active}

    def matches(self, capability: str, scopes: Tuple[str, ...],
                target: str, role: str) -> bool:
        return self.is_active_at(time.time()) and self.capability == capability \
            and (not self.role or not role or self.role == role) \
            and (not target or not self.target or self.target == target) \
            and set(self.scopes).issuperset(set(scopes))


def _validate_grant_record(raw: Dict[str, Any], *, clock: float,
                           allow_unknown: bool = True) -> Optional[str]:
    """Fail-closed validation of ONE persisted grant record.

    Returns a stable reason string when the record is invalid; returns None
    when the record is a safe, authoritative-equivalent grant. A loaded grant
    can never gain more authority than a freshly issued one: every field is
    re-validated here with the exact same rules ``issue`` enforces.
    """
    if not isinstance(raw, dict):
        return "MALFORMED_RECORD"
    if allow_unknown:
        unknown = set(raw) - _GRANT_FIELDS
        if unknown:
            return "UNKNOWN_FIELD"
    grant_id = raw.get("grant_id")
    if not isinstance(grant_id, str) or not _GRANT_ID_RE.match(grant_id):
        return "BAD_GRANT_ID"
    issued_by = raw.get("issued_by")
    if issued_by != "user":
        return "FORGED_ISSUER"
    capability = raw.get("capability")
    if not isinstance(capability, str) or not capability.startswith(GHOST_NS + "."):
        return "UNKNOWN_CAPABILITY"
    spec = ghost_spec(capability)
    if spec is None or spec.tier not in (Tier.READ_ONLY, Tier.REVERSIBLE):
        return "HIGH_IMPACT_CAPABILITY"
    target = raw.get("target")
    if not isinstance(target, str) or target in _WILDCARD_TARGETS:
        return "BAD_TARGET"
    if len(target) > 2048:
        return "TARGET_TOO_LONG"
    role = raw.get("role") or ""
    if not isinstance(role, str) or role not in ("", ROLE_CHROMIUM, ROLE_COMET):
        return "INVALID_ROLE"
    scopes = raw.get("scopes")
    if not isinstance(scopes, (list, tuple)) or not scopes:
        return "NO_SCOPE"
    for s in scopes:
        if not isinstance(s, str) or not _SCOPE_RE.match(s):
            return "INVALID_SCOPE"
        if s not in spec.scopes:
            return "SCOPE_NOT_DECLARED"
    try:
        created_at = float(raw.get("created_at"))
        expires_at = float(raw.get("expires_at"))
    except (TypeError, ValueError):
        return "BAD_TIMESTAMP"
    if not (created_at > 0 and expires_at > 0):
        return "IMPOSIBLE_TIMESTAMP"
    if expires_at <= created_at:
        return "BAD_TTL"
    if expires_at <= clock:
        return "EXPIRED"
    ttl = expires_at - created_at
    if not (1.0 <= ttl <= 86400):
        return "INVALID_TTL"
    budget = raw.get("resource_budget", 1_000_000)
    # Strict integer budget (bool is an int subclass but is not a budget).
    if not isinstance(budget, int) or isinstance(budget, bool):
        return "BAD_RESOURCE_BUDGET"
    if not (1 <= budget <= 10 ** 12):
        return "BAD_RESOURCE_BUDGET"
    if role and role not in ("", ROLE_CHROMIUM, ROLE_COMET):
        return "INVALID_ROLE"
    # Target sanity for browser-bound grants: a role-bound grant must carry a
    # valid approved origin for that role (strict), never a bare/wildcard
    # value, so a Chromium grant can never be replayed against Comet.
    if role:
        try:
            browser_roles()[role].bind_origin(target)
        except ActionValidationError:
            return "ROLE_TARGET_MISMATCH"
    return None


class SessionGrantStore:
    """Expiring, bounded, human-issued session grants for read/reversible work.

    Fail-closed durability (Packet 8N repair): load rejects forged/malformed/
    unknown/high-impact/expired/wildcard records (never loading more authority
    than a fresh grant); save uses a cross-process lock, a unique exclusive
    temp file, file+directory fsync, chmod 0600, and atomic replace, and every
    failure is surfaced as a typed :class:`SessionGrantError` — a grant is
    never reported as issued when persistence failed. The previous valid state
    is preserved when a pre-rename failure occurs.
    """

    def __init__(self, path: str | os.PathLike,
                 *, clock: Callable[[], float] = time.time,
                 default_ttl_s: float = 600.0) -> None:
        register_ghost_specs()   # capability registry needed for validation
        self._path = str(path)
        self._clock = clock
        self._ttl = float(default_ttl_s)
        self._lock = threading.RLock()
        self._grants: Dict[str, SessionGrant] = {}
        self._load_warnings: List[str] = []
        self._load_degraded = False
        self._load()

    # ------------------------------------------------------------ parse
    def _parse_root(self, raw: Any) -> Tuple[Dict[str, SessionGrant],
                                             List[str], bool]:
        """Parse+validate a raw persisted grant root (fail closed).

        Returns ``(grants, warnings, degraded)``. A malformed root — non-dict,
        unknown top-level fields, a newer schema, or a ``grants`` value that is
        not a list — raises a typed :class:`SessionGrantError` instead of a
        raw ``TypeError``/``AttributeError``/``ValueError``. Invalid individual
        records are skipped with warnings; the store is marked degraded but a
        valid subset is still usable (never loads more authority than a fresh
        grant).
        """
        warnings: List[str] = []
        if not isinstance(raw, dict):
            raise SessionGrantError(
                "grant state is not an object", code="GRANT_STATE_NOT_OBJECT",
                degraded=True, path=self._path)
        unknown_top = set(raw) - _GRANT_FILE_FIELDS
        if unknown_top:
            raise SessionGrantError(
                "GRANT_STATE_UNKNOWN_TOP_LEVEL: " + ", ".join(sorted(unknown_top)),
                code="GRANT_STATE_UNKNOWN_TOP_LEVEL", degraded=True,
                path=self._path)
        try:
            schema = int(raw.get("schema_version", _GRANT_SCHEMA_VERSION))
        except (TypeError, ValueError):
            schema = -1
        if schema > _GRANT_SCHEMA_VERSION:
            raise SessionGrantError(
                f"GRANT_SCHEMA_NEWER: file schema {schema} > supported "
                f"{_GRANT_SCHEMA_VERSION}", code="GRANT_SCHEMA_NEWER",
                degraded=True, path=self._path)
        grants_list = raw.get("grants")
        if not isinstance(grants_list, list):
            raise SessionGrantError(
                "grant state 'grants' is not a list",
                code="GRANT_STATE_BAD_GRANTS", degraded=True, path=self._path)
        now = self._clock()
        seen: Dict[str, bool] = {}
        grants: Dict[str, SessionGrant] = {}
        for record in grants_list:
            reason = _validate_grant_record(record, clock=now)
            if reason is not None:
                warnings.append(
                    f"grant {record.get('grant_id', '?') if isinstance(record, dict) else '?'} "
                    f"rejected: {reason}")
                continue
            grant_id = record["grant_id"]
            if grant_id in seen:
                warnings.append(f"duplicate grant id {grant_id} rejected")
                continue
            seen[grant_id] = True
            grants[grant_id] = SessionGrant(
                grant_id=grant_id,
                capability=record["capability"],
                scopes=tuple(record["scopes"]),
                target=record["target"],
                role=record.get("role") or "",
                issued_by="user",
                created_at=float(record["created_at"]),
                expires_at=float(record["expires_at"]),
                resource_budget=int(record["resource_budget"]))
        return grants, warnings, bool(warnings)

    # ------------------------------------------------------------ load
    def _load(self) -> None:
        import json
        import os as _os
        if not _os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError, TypeError) as exc:
            self._grants = {}
            self._load_degraded = True
            self._load_warnings.append(
                f"GRANT_STATE_CORRUPT: {redact_text(str(exc))[:200]}")
            log_event("ghost.grant.load.failed", code="GRANT_STATE_CORRUPT",
                      path=self._path)
            return
        try:
            grants, warnings, degraded = self._parse_root(raw)
        except SessionGrantError as exc:
            self._grants = {}
            self._load_degraded = True
            self._load_warnings.append(f"{exc.code}: {redact_text(str(exc))[:200]}")
            log_event("ghost.grant.load.failed", code=exc.code, path=self._path)
            return
        self._grants = grants
        self._load_warnings = warnings
        self._load_degraded = degraded
        if warnings:
            log_event("ghost.grant.load.degraded",
                      warnings=list(warnings), path=self._path)

    # ------------------------------------------------------- cross-process
    def _read_latest_locked(self) -> Dict[str, SessionGrant]:
        """Read + validate the current on-disk state (assumes the caller holds
        the cross-process lock). Returns the validated grants or ``{}`` when
        the file does not exist yet. A malformed root raises a typed
        :class:`SessionGrantError`."""
        import json
        import os as _os
        if not _os.path.exists(self._path):
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError, TypeError) as exc:
            raise SessionGrantError(
                f"GRANT_STATE_CORRUPT: {redact_text(str(exc))[:200]}",
                code="GRANT_STATE_CORRUPT", degraded=True, path=self._path) from exc
        grants, _warnings, _degraded = self._parse_root(raw)
        return grants

    def _write_payload_locked(self, grants: Dict[str, SessionGrant]) -> None:
        """Atomically persist ``grants`` (assumes the caller holds the flock).
        Every failure is a typed :class:`SessionGrantError`; only the current
        writer's unique temp file is cleaned; the previous valid state is
        preserved on a pre-rename failure."""
        import json
        import os as _os
        p = self._path
        d = p.rsplit("/", 1)[0] if "/" in p else "."
        try:
            _os.makedirs(d, exist_ok=True)
        except OSError as exc:
            raise SessionGrantError(
                f"grant directory creation failed: {exc}",
                code="GRANT_MKDIR_FAILED", path=p) from exc
        try:
            payload = json.dumps(
                {"schema_version": _GRANT_SCHEMA_VERSION,
                 "grants": [g.to_dict() for g in grants.values()]},
                indent=2, sort_keys=True).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SessionGrantError(
                f"grant serialization failed: {exc}",
                code="GRANT_SERIALIZE_FAILED", path=p) from exc
        tmp = (p + f".tmp.{_os.getpid()}.{uuid.uuid4().hex[:8]}")
        try:
            fd = _os.open(str(tmp), _os.O_CREAT | _os.O_EXCL
                          | _os.O_WRONLY, 0o600)
            with _os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                _os.fsync(fh.fileno())
        except OSError as exc:
            self._cleanup_tmp(tmp)
            raise SessionGrantError(
                f"grant write failed: {exc}", code="GRANT_WRITE_FAILED",
                path=p) from exc
        try:
            _os.chmod(tmp, 0o600)
        except OSError as exc:
            self._cleanup_tmp(tmp)
            raise SessionGrantError(
                f"grant chmod failed: {exc}", code="GRANT_CHMOD_FAILED",
                path=p) from exc
        try:
            _os.replace(tmp, p)
        except OSError as exc:
            self._cleanup_tmp(tmp)
            raise SessionGrantError(
                f"grant rename failed: {exc}", code="GRANT_RENAME_FAILED",
                path=p) from exc
        try:
            dir_fd = _os.open(d, _os.O_RDONLY)
            try:
                _os.fsync(dir_fd)
            finally:
                _os.close(dir_fd)
        except OSError as exc:
            # Rename already happened: the new file is in place but its
            # durability across a crash is uncertain. Report a typed
            # failure; never claim the previous state was preserved.
            raise SessionGrantError(
                "grant directory fsync failed after rename: "
                f"{exc}; the new file is in place but durability is "
                "UNCERTAIN",
                code="GRANT_DIR_FSYNC_FAILED", degraded=True, path=p) from exc

    def _lockfd(self):
        """Acquire the cross-process flock (returns an open fd; caller must
        close it). Never reentrant — the caller must not already hold it."""
        import fcntl
        import os as _os
        lock_path = self._path + ".lock"
        fd_lock = None
        try:
            fd_lock = _os.open(lock_path, _os.O_CREAT | _os.O_RDWR, 0o600)
            fcntl.flock(fd_lock, fcntl.LOCK_EX)
        except OSError as exc:
            if fd_lock is not None:
                try:
                    _os.close(fd_lock)
                except OSError:
                    pass
            raise SessionGrantError(
                f"grant lock failed: {exc}", code="GRANT_LOCK_FAILED",
                path=self._path) from exc
        return fd_lock

    @staticmethod
    def _unlockfd(fd_lock: int) -> None:
        import fcntl
        import os as _os
        try:
            fcntl.flock(fd_lock, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            _os.close(fd_lock)
        except OSError:
            pass

    def _locked_update(self, mutate: Callable[[Dict[str, SessionGrant]],
                                             Dict[str, SessionGrant]]) -> Dict[str, SessionGrant]:
        """Cross-process read-merge-write under a SINGLE flock acquisition.

        The flock is held across the read of the LATEST on-disk grants, the
        merge, and the atomic write — so two independent processes cannot
        clobber each other's grants (no lost updates). On any failure the
        in-memory map is restored exactly and the caller receives a typed
        :class:`SessionGrantError`; a grant is never reported issued when
        persistence failed.
        """
        with self._lock:  # process-local guard; the flock is the cross-proc lock
            previous = dict(self._grants)
            fd_lock = None
            try:
                fd_lock = self._lockfd()
                latest = self._read_latest_locked()   # safe: flock held
                merged = mutate(latest)
                self._write_payload_locked(merged)    # safe: flock held
            except SessionGrantError:
                self._grants = previous  # rollback in-memory exactly
                raise
            except Exception as exc:
                # Any unexpected failure must never leave a partial in-memory
                # state or silently report a grant as issued.
                self._grants = previous
                if isinstance(exc, SessionGrantError):
                    raise
                raise SessionGrantError(
                    f"grant update failed: {exc}", code="GRANT_UPDATE_FAILED",
                    path=self._path) from exc
            finally:
                if fd_lock is not None:
                    self._unlockfd(fd_lock)
            self._grants = merged
            return merged

    @staticmethod
    def _cleanup_tmp(tmp: str) -> None:
        import os as _os
        try:
            if _os.path.exists(tmp):
                _os.unlink(tmp)
        except OSError:
            pass

    # ------------------------------------------------------------ issue
    def issue(self, capability: str, *, scopes: Tuple[str, ...],
              target: str, role: str = "", issued_by: str = "user",
              ttl_s: Optional[float] = None,
              resource_budget: int = 1_000_000) -> SessionGrant:
        if issued_by != "user":
            raise SessionGrantError(
                "CONFIRMATION_SOURCE_FORBIDDEN: only a human may issue a "
                "session grant", code="CONFIRMATION_SOURCE_FORBIDDEN")
        spec = ghost_spec(capability)
        if spec is None or spec.tier not in (Tier.READ_ONLY, Tier.REVERSIBLE):
            raise SessionGrantError(
                f"{capability}: session grants only cover read-only/reversible "
                "Ghost capabilities", code="HIGH_IMPACT_CAPABILITY")
        if not isinstance(scopes, (list, tuple)) or not scopes:
            raise SessionGrantError(
                "session grant requires at least one scope", code="NO_SCOPE")
        scopes = tuple(scopes)
        for s in scopes:
            if not isinstance(s, str) or not _SCOPE_RE.match(s):
                raise SessionGrantError(f"invalid scope {s!r}",
                                        code="INVALID_SCOPE")
            if s not in spec.scopes:
                raise SessionGrantError(
                    f"scope {s!r} is not declared by capability {capability!r}",
                    code="SCOPE_NOT_DECLARED")
        if not target or target in _WILDCARD_TARGETS:
            raise SessionGrantError(
                "session grant target must be explicit, never empty/wildcard",
                code="BAD_TARGET")
        if role:
            try:
                validate_role(role)
                browser_roles()[role].bind_origin(target)
            except ActionValidationError as exc:
                raise SessionGrantError(
                    f"invalid role/target binding: {exc}",
                    code="ROLE_TARGET_MISMATCH") from exc
        if not isinstance(resource_budget, int) or isinstance(resource_budget, bool):
            raise SessionGrantError(
                "resource_budget must be an integer", code="BAD_RESOURCE_BUDGET")
        if not (1 <= resource_budget <= 10 ** 12):
            raise SessionGrantError(
                f"resource_budget {resource_budget} outside [1, 10^12]",
                code="BAD_RESOURCE_BUDGET")
        ttl = float(ttl_s if ttl_s is not None else self._ttl)
        if not (1.0 <= ttl <= 86400):
            raise SessionGrantError("ttl_s outside [1, 86400]",
                                    code="INVALID_TTL")
        now = self._clock()
        grant = SessionGrant(
            grant_id=f"g_{uuid.uuid4().hex[:16]}", capability=capability,
            scopes=scopes, target=target,
            role=role, issued_by="user", created_at=now,
            expires_at=now + ttl,
            resource_budget=resource_budget)

        def _mutate(latest: Dict[str, SessionGrant]) -> Dict[str, SessionGrant]:
            merged = dict(latest)
            merged[grant.grant_id] = grant
            return merged

        merged = self._locked_update(_mutate)  # raises typed error on failure
        log_event("ghost.grant.issued", grant_id=grant.grant_id,
                  capability=capability, scopes=list(scopes),
                  target=redact_text(target), role=role, ttl_s=ttl)
        return grant

    def revoke(self, grant_id: str) -> bool:
        def _mutate(latest: Dict[str, SessionGrant]) -> Dict[str, SessionGrant]:
            merged = dict(latest)
            merged.pop(grant_id, None)
            return merged
        before = dict(self._grants)
        merged = self._locked_update(_mutate)  # raises typed error on failure
        removed = len(before) > len(merged)
        if removed:
            log_event("ghost.grant.revoked", grant_id=grant_id)
        return removed

    def matches(self, capability: str, scopes: Tuple[str, ...],
                target: str, role: str = "") -> bool:
        now = self._clock()
        with self._lock:
            return any(
                g.is_active_at(now) and g.capability == capability
                and (not g.role or not role or g.role == role)
                and (not target or not g.target or g.target == target)
                and set(g.scopes).issuperset(set(scopes))
                for g in self._grants.values())

    def grants(self) -> List[SessionGrant]:
        with self._lock:
            return [g for g in self._grants.values()]

    def has_for_capability(self, capability: str) -> bool:
        """True when a session-grant record exists for this capability.

        The session-grant path is authoritative for a capability once ANY grant
        record exists: an expired/mismatched grant must deny, never silently
        fall back to a coarser 7E grant (the 7E mirror would otherwise stay
        active on real time and bypass the session grant's expiry/target).
        """
        with self._lock:
            return any(g.capability == capability for g in self._grants.values())

    def reset(self) -> None:
        def _mutate(latest: Dict[str, SessionGrant]) -> Dict[str, SessionGrant]:
            return {}
        self._locked_update(_mutate)

    def health(self) -> Dict[str, Any]:
        return {"state": "degraded" if (self._load_degraded
                                        or self._load_warnings) else "OK",
                "load_warnings": list(self._load_warnings),
                "load_degraded": self._load_degraded,
                "path": self._path}


def session_grants_store(cfg) -> SessionGrantStore:
    path = cfg.get("ghost", "grants_path", default="") or \
        (cfg.state_dir() / "ghost-grants.json")
    return SessionGrantStore(path)


# ---------------------------------------------------------------------------
# Adapter health + emergency stop
# ---------------------------------------------------------------------------
HEALTH_DISABLED = "disabled"
HEALTH_ENABLED = "enabled"
HEALTH_DEGRADED = "degraded"
HEALTH_FAILED = "failed"


@dataclass
class GhostAdapterState:
    """Typed, durable adapter health + emergency-stop state."""
    enabled: bool = False          # DISABLED by default
    shell: bool = False            # GHOST_SHELL=off by default
    health: str = HEALTH_DISABLED
    stopped: bool = False          # emergency stop latch
    running: bool = False          # supervised subprocess active (never by default)
    last_error: str = ""
    pid: Optional[int] = None      # supervised process pid (never spawned by default)

    def to_dict(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "shell": self.shell,
                "health": self.health, "emergency_stop": self.stopped,
                "running": self.running, "last_error": self.last_error[:200],
                "pid": self.pid}


class GhostAdapter:
    """Supervised adapter boundary: typed health, enable/disable, emergency stop.

    This object NEVER spawns a process, never touches the user's browser or
    accounts, and never executes anything on its own. It only holds the typed
    state + health contract; real execution goes through the broker with an
    explicitly injected executor (fixture for hermetic tests, or a future
    operator-approved supervised Ghost subprocess).
    """

    def __init__(self, *, shell: bool = False,
                 clock: Callable[[], float] = time.time,
                 power_control: Optional[Any] = None) -> None:
        self._state = GhostAdapterState(shell=bool(shell))
        self._clock = clock
        self._lock = threading.RLock()
        self._active_correlations: Dict[str, Callable[[], None]] = {}
        # Wave 4 (gap G3): when a shared PowerControl is supplied (production
        # builds one via ``ghost_adapter(cfg)``), the emergency stop / reset
        # latch the ONE durable authority the broker/dashboard/voice observe
        # instead of a second adapter-private latch.
        self._power = power_control

    @property
    def state(self) -> GhostAdapterState:
        return self._state

    def bind_power(self, control: Optional[Any]) -> None:
        """Bind the shared PowerControl when the adapter was built without one."""
        if self._power is None and control is not None:
            self._power = control

    def _shared_stopped(self) -> bool:
        """True when the ONE durable e-stop latch is engaged (fail closed)."""
        if self._power is None:
            return False
        try:
            self._power.refresh()
            return bool(self._power.state.get("emergency_stop"))
        except Exception:
            return True

    def enable(self, *, requested_by: str) -> Dict[str, Any]:
        if requested_by != "user":
            return {"state": "denied", "code": "CONFIRMATION_SOURCE_FORBIDDEN",
                    "detail": "only a human may enable the Ghost adapter"}
        shared = self._shared_stopped()
        with self._lock:
            if self._state.stopped or shared:
                return {"state": "denied", "code": "EMERGENCY_STOP",
                        "detail": "adapter is emergency-stopped; reset required"}
            self._state.enabled = True
            self._state.health = HEALTH_ENABLED
        log_event("ghost.adapter.enabled")
        return {"state": "OK", "health": self._state.health,
                "detail": "Ghost adapter enabled (capabilities still require "
                          "an operator-approved executor)"}

    def disable(self, *, requested_by: str) -> Dict[str, Any]:
        if requested_by != "user":
            return {"state": "denied", "code": "CONFIRMATION_SOURCE_FORBIDDEN",
                    "detail": "only a human may disable the Ghost adapter"}
        with self._lock:
            self._state.enabled = False
            self._state.health = HEALTH_DISABLED
        log_event("ghost.adapter.disabled")
        return {"state": "OK", "health": self._state.health}

    def emergency_stop(self) -> Dict[str, Any]:
        """Global, immediate, idempotent stop: latches the ONE shared durable
        emergency-stop authority (when one is wired), cancels every active run,
        and keeps the adapter-local view stopped until an explicit reset."""
        if self._power is not None:
            try:
                self._power.emergency_stop(issued_by="user")
            except Exception as exc:
                log_event("ghost.adapter.estop_shared_failed",
                          error=redact_text(str(exc))[:160])
                raise
        with self._lock:
            self._state.stopped = True
            self._state.health = HEALTH_DEGRADED
            self._state.enabled = False
            for cancel in list(self._active_correlations.values()):
                try:
                    cancel()
                except Exception:
                    pass
            self._active_correlations.clear()
        log_event("ghost.adapter.emergency_stop")
        return {"state": "OK", "health": self._state.health,
                "detail": "emergency stop latched; reset required"}

    def reset(self, *, requested_by: str) -> Dict[str, Any]:
        if requested_by != "user":
            return {"state": "denied", "code": "CONFIRMATION_SOURCE_FORBIDDEN"}
        if self._power is not None:
            try:
                self._power.reset(issued_by="user")
            except Exception as exc:
                log_event("ghost.adapter.reset_shared_failed",
                          error=redact_text(str(exc))[:160])
                return {"state": "denied", "code": "EMERGENCY_STOP",
                        "detail": "could not clear the shared emergency stop"}
        with self._lock:
            self._state.stopped = False
            self._state.enabled = False
            self._state.health = HEALTH_DISABLED
        log_event("ghost.adapter.reset")
        return {"state": "OK", "health": self._state.health}

    def mark_failure(self, error: str) -> None:
        with self._lock:
            self._state.health = HEALTH_FAILED
            self._state.last_error = str(error)[:200]
        log_event("ghost.adapter.failed", error=redact_text(str(error))[:120])

    def mark_reconnect(self) -> None:
        shared = self._shared_stopped()
        with self._lock:
            if self._state.enabled and not self._state.stopped and not shared:
                self._state.health = HEALTH_ENABLED
                self._state.last_error = ""
        log_event("ghost.adapter.reconnected")

    def track(self, correlation_id: str, cancel: Callable[[], None]) -> None:
        with self._lock:
            self._active_correlations[correlation_id] = cancel

    def untrack(self, correlation_id: str) -> None:
        with self._lock:
            self._active_correlations.pop(correlation_id, None)


def ghost_adapter(cfg=None, *, env: Optional[Dict[str, str]] = None) -> GhostAdapter:
    """Build the adapter with the effective shell setting (default off).

    When a config is available the adapter is bound to the shared, durable
    :class:`~voicebridge.power.PowerControl` so its emergency stop participates
    in the ONE global latch (gap G3) instead of a second adapter-private state.
    """
    power = None
    if cfg is not None:
        try:
            from .power import power_control

            power = power_control(cfg)
        except Exception:
            power = None
    return GhostAdapter(shell=ghost_shell_enabled(env), power_control=power)


# ---------------------------------------------------------------------------
# Ghost broker: ActionBroker with Ghost capabilities registered
# ---------------------------------------------------------------------------
def ghost_actions_broker(cfg=None, *,
                         executors: Optional[Dict[str, Callable]] = None,
                         enabled: bool = False,
                         power_control: Optional[Any] = None) -> "CapabilityBroker":
    """Build the SAME ActionBroker the 8F layer uses, plus Ghost capabilities.

    The host tools and 8F action bindings are registered exactly as
    ``actions.actions_broker`` does; Ghost bindings are added on top with the
    same envelope validator, preview, and disabled/fixture executor semantics.
    Without injected executors every Ghost capability resolves to the typed
    ADAPTER_DISABLED result (never a live launch). When ``power_control`` is
    provided it is passed into the base broker so Ghost/Comet executions share
    the 8O global control state.
    """
    from .actions import (_disabled_executor, _envelope_validator,
                          actions_broker)
    base = actions_broker(cfg, executors=executors, enabled=enabled,
                          power_control=power_control)
    register_ghost_specs()
    runner_executors = dict(executors or {})
    for spec in ghost_action_specs(shell=ghost_shell_enabled()):
        fn = runner_executors.get(spec.action_id) or \
            _disabled_executor(spec)
        base.register(ToolBinding(
            spec=CapabilitySpec(
                name=spec.action_id, version=spec.version, tier=spec.tier,
                summary=spec.summary[:200], scopes=spec.scopes,
                timeout_s=spec.timeout_s,
                arg_validator=_envelope_validator(spec)),
            side_effects=spec.side_effects, fn=fn,
            preview=lambda _env, _s=spec: _render_preview(_s, _env)))
    return base


# ---------------------------------------------------------------------------
# Ghost gate: session grants are authoritative for read/reversible Ghost caps
# ---------------------------------------------------------------------------
class GhostGate(ActionGate):
    """7E ActionGate with the Ghost session-grant layer layered on top.

    For a read-only/reversible Ghost capability, a matching active session
    grant authorizes the action directly (bounded, expiring, target/origin/
    role-bound) without requiring a separate 7E grant record. High-impact
    Ghost capabilities always route through the 7E gate (explicit grant +
    fresh confirmation) exactly as Packet 8F does. The session grant can never
    widen a grant: matching is exact on capability, scope, target and role.
    """

    def decide(self, spec: ActionSpec, envelope: Dict[str, Any], *,
               source: str, confirmation_id: Optional[str] = None,
               confirmation_required: bool = False) -> Dict[str, Any]:
        args = envelope.get("args") or {}
        role = str(args.get("role", "")) if isinstance(args, dict) else ""
        target = str(envelope.get("target", "") or "")
        account_scope = str(envelope.get("account_scope", "") or "")
        # Browser-role origin/account binding: a role-bound Ghost operation
        # must target a URL origin approved for that role. Enforced for EVERY
        # Ghost capability (read, reversible and high-impact) and can never be
        # bypassed by the session grant or a confirmation — there is no
        # wrong-browser fallback, and a missing/bare/malformed target fails
        # closed.
        if spec.adapter == ADAPTER_GHOST and role:
            try:
                ro = validate_role(role)
            except ActionValidationError as exc:
                return {"allowed": False, "code": "OUT_OF_SCOPE",
                        "mode": MODE_RESTRICTED,
                        "detail": str(exc)}
            if not target:
                return {"allowed": False, "code": "OUT_OF_SCOPE",
                        "mode": MODE_RESTRICTED,
                        "detail": "browser-role operation requires a URL "
                                  "target (bare/missing target refused)"}
            try:
                ro.bind_origin(target)
            except ActionValidationError as exc:
                return {"allowed": False, "code": "OUT_OF_SCOPE",
                        "mode": MODE_RESTRICTED,
                        "detail": f"browser-role origin binding: {exc}"}
            if account_scope:
                try:
                    ro.bind_account_scope(account_scope)
                except ActionValidationError as exc:
                    return {"allowed": False, "code": "OUT_OF_SCOPE",
                            "mode": MODE_RESTRICTED,
                            "detail": f"browser-role account binding: {exc}"}
        if (spec.adapter == ADAPTER_GHOST
                and spec.tier in (Tier.READ_ONLY, Tier.REVERSIBLE)
                and source in ("user", "check")):
            grants = getattr(self, "ghost_grants", None)
            if grants is not None:
                scopes = tuple(str(envelope.get("scope", "")).split(",")) \
                    if str(envelope.get("scope", "")) else (spec.scopes[0],)
                if grants.matches(spec.action_id, scopes, target, role):
                    return {"allowed": True, "code": "ALLOWED",
                            "mode": MODE_RESTRICTED,
                            "detail": "matched bounded Ghost session grant"}
            # A session grant for this capability exists but did not match the
            # requested target/origin/role: deny (never a wrong-browser fallback
            # and never a coarser 7E grant bypass).
            if grants is not None and grants.has_for_capability(spec.action_id):
                return {"allowed": False, "code": "NOT_GRANTED",
                        "mode": MODE_RESTRICTED,
                        "detail": "session grant exists but does not match "
                                  "requested target/origin/role (default deny)"}
        if spec.adapter == ADAPTER_GHOST:
            # Ghost read/reversible capabilities are NEVER auto-authorized by
            # unrestricted pre-auth: only a matching explicit grant (session
            # grant first, then a 7E grant) may authorize them. Unrestricted
            # mode must not widen a grant or bypass Ghost default deny, so we
            # never call the base ActionGate's unrestricted pre-auth branch.
            if spec.tier in (Tier.READ_ONLY, Tier.REVERSIBLE):
                if source not in ("user", "check"):
                    return {"allowed": False,
                            "code": "CONFIRMATION_SOURCE_FORBIDDEN",
                            "mode": MODE_RESTRICTED,
                            "detail": f"source={source} cannot authorize "
                                      "Ghost read/reversible actions"}
                if self._matching_grant(spec.action_id,
                                        str(envelope.get("scope", ""))):
                    return {"allowed": True, "code": "ALLOWED",
                            "mode": MODE_RESTRICTED,
                            "detail": "matched explicit Ghost grant"}
                return {"allowed": False, "code": "NOT_GRANTED",
                        "mode": MODE_RESTRICTED,
                        "detail": "no matching grant; Ghost default deny"}
        return super().decide(spec, envelope, source=source,
                              confirmation_id=confirmation_id,
                              confirmation_required=confirmation_required)


# ---------------------------------------------------------------------------
# Ghost runner: reuse the exact 8F ActionRunner machinery
# ---------------------------------------------------------------------------
class GhostRunner(ActionRunner):
    """ActionRunner bound to Ghost capabilities (8F machinery reused verbatim).

    preview/execute/retry/recover/cancel/receipts and the 7E gate, action-hash
    confirmation, receipt persistence, and audit all come from ActionRunner.
    Ghost capabilities are resolved through ``action_spec()`` after
    ``register_ghost_specs()``. Live execution is DISABLED by default: without
    injected fixture executors every call returns ADAPTER_DISABLED.

    Packet 8N repairs over the base runner:

    - ``role`` (browser role) is merged into the validated envelope ``args``
      so it participates in the stable action hash and stays bound through
      preview / prepare / execute / retry / recover / receipts / fixtures.
    - Every Ghost execution is tracked in the ``GhostAdapter`` with the real
      broker cancel callback; emergency stop cancels/kills active runs.
    - Comet operations are routed through the broker and require the bound
      adapter (no direct-object bypass).
    """

    def __init__(self, cfg=None, *, store: Optional[ActionStore] = None,
                 perms=None, broker: Optional[CapabilityBroker] = None,
                 executors: Optional[Dict[str, Callable]] = None,
                 enabled: bool = False,
                 clock: Callable[[], float] = time.time,
                 confirmation_ttl_s: Optional[float] = None,
                 session_grants: Optional[SessionGrantStore] = None,
                 adapter: Optional[GhostAdapter] = None,
                 comet: Optional[CometResearchAdapter] = None,
                 max_preview_chars: int = 400,
                 power: Optional[Any] = None):
        register_ghost_specs()
        super().__init__(cfg=cfg, store=store, perms=perms,
                         broker=broker or ghost_actions_broker(
                             cfg, executors=executors, enabled=enabled,
                             power_control=(power.control if power is not None
                                            else None)),
                         executors=executors, enabled=enabled, clock=clock,
                         confirmation_ttl_s=confirmation_ttl_s,
                         max_preview_chars=max_preview_chars,
                         power=power)
        self.ghost_grants = session_grants or (
            session_grants_store(cfg) if cfg is not None else None)
        self.adapter = adapter or ghost_adapter(cfg)
        if power is not None:
            # One authority: an internally-built adapter (cfg may be absent)
            # still participates in the shared latch the runner already uses.
            self.adapter.bind_power(getattr(power, "control", None))
        self.comet = comet
        if self.comet is not None:
            self.comet.bind(self)
        # Session grants are the authoritative read/reversible authorization
        # for Ghost capabilities; GhostGate layers them on the shared 7E gate.
        # Packet 8O: when a shared PowerPolicy is supplied, wrap the GhostGate
        # in the power gate adapter so every Ghost/Comet path flows through the
        # one policy path first.
        if power is not None:
            from .power import PowerGateAdapter
            inner = GhostGate(self.perms)
            inner.ghost_grants = self.ghost_grants
            self.gate = PowerGateAdapter(power, inner_gate=inner)
        else:
            self.gate = GhostGate(self.perms)
            self.gate.ghost_grants = self.ghost_grants
        self._install_broker_tracking()
        if self.comet is not None:
            self._bind_comet_executors()

    def _comet_executor(self, method: str) -> Callable[[Dict[str, Any]], str]:
        """Broker-bound comet executor: builds the typed request from the
        validated envelope args and invokes the bound adapter. The adapter
        itself refuses direct (unbound) object calls, so the ONLY path to a
        comet operation is this broker executor."""
        import json as _json

        def _run(env: Dict[str, Any]) -> str:
            args = env.get("args") or {}
            # The broker executor is the authorized context: set the
            # thread-local broker flag so the adapter accepts the call.
            self.comet._local.in_broker = True
            try:
                if method == "connect":
                    out = self.comet.connect(requested_by="user")
                elif method == "disconnect":
                    out = self.comet.disconnect(requested_by="user")
                elif method == "mode":
                    out = self.comet.select_mode(str(args.get("mode", "")),
                                                 requested_by="user")
                elif method == "ask":
                    req = CometResearchRequest(
                        query=str(args.get("query", "")),
                        mode=str(args.get("mode", "research")),
                        max_sources=int(args.get("max_sources", 8)),
                        max_chars=int(args.get("max_chars", 60_000)),
                        timeout_s=float(args.get("timeout_s", 60.0)),
                        capture_screenshot=bool(
                            args.get("capture_screenshot", False)),
                        screenshot_max_bytes=int(
                            args.get("screenshot_max_bytes", 128 * 1024)))
                    out = self.comet.ask(req, requested_by="user")
                elif method == "wait":
                    wait_out = self.comet.wait(
                        str(args.get("wait_id", "")),
                        max_wait_s=float(args.get("max_wait_s", 60.0)),
                        requested_by="user")
                    # The typed WAIT_* state is the adapter's outcome; wrap it
                    # in the broker's OK vocabulary and carry the typed state.
                    out = {"state": ST_OK,
                           "wait_id": wait_out.get("wait_id", ""),
                           "wait_state": wait_out.get("state", WAIT_CANCELLED),
                           "detail": wait_out.get("detail", "")}
                elif method == "extract":
                    source = str(args.get("source", ""))
                    if not source:
                        out = {"state": "failed", "code": "NO_SOURCE",
                               "detail": "no source text supplied for "
                                         "extraction (ignored request refused)"}
                    else:
                        extracted = self.comet.extract(
                            source, max_chars=int(args.get("max_chars", 60_000)),
                            requested_by="user")
                        # keep the executor's JSON within the broker's bounded
                        # output budget so the result is parseable (full
                        # redacted text is never returned to the wire)
                        out = {"state": ST_OK,
                               "extracted": extracted[:1500],
                               "extracted_chars": len(extracted),
                               "detail": "bounded redacted extraction"}
                elif method == "evidence":
                    import base64 as _b64
                    raw = str(args.get("data_b64", ""))
                    if not raw:
                        out = {"state": "failed", "code": "NO_EVIDENCE",
                               "detail": "no screenshot/evidence bytes supplied "
                                         "(ignored request refused)"}
                    else:
                        try:
                            data = _b64.b64decode(raw, validate=True)
                        except Exception:
                            out = {"state": "failed", "code": "BAD_EVIDENCE",
                                   "detail": "evidence data_b64 is malformed"}
                        else:
                            ev = self.comet.evidence(
                                data, max_bytes=int(
                                    args.get("max_bytes", 128 * 1024)),
                                requested_by="user")
                            out = {"state": ST_OK, "evidence": ev[:2000],
                                   "detail": "bounded evidence reference"}
                else:
                    raise ActionValidationError(
                        f"unknown comet method {method!r}")
            finally:
                self.comet._local.in_broker = False
            if not isinstance(out, str):
                out = _json.dumps(out, sort_keys=True)
            return str(out)

        return _run

    def _bind_comet_executors(self) -> None:
        """Register the bound comet adapter as the broker executor for the
        comet capabilities (unless a fixture executor was explicitly injected).
        The broker remains the single decide/audit/cancel boundary."""
        for spec in ghost_action_specs(shell=ghost_shell_enabled()):
            if not spec.action_id.startswith(f"{GHOST_NS}.comet."):
                continue
            if spec.action_id in self.executors:
                continue  # explicit fixture wins (synthetic tests)
            method = spec.action_id.rsplit(".", 1)[-1]
            binding = self.broker.get(spec.action_id)
            if binding is None:
                continue
            binding.fn = self._comet_executor(method)

    def _install_broker_tracking(self) -> None:
        """Wrap the broker so EVERY Ghost execution is tracked in the adapter
        with the real broker cancel callback.

        ``execute``/``retry``/``recover`` all reach ``self.broker.execute``;
        wrapping there means an emergency stop (from another thread) calls the
        tracked callback -> ``broker.cancel(correlation_id)`` -> the running
        child process group is SIGKILLed and the in-flight read-only path is
        cooperatively cancelled, so no late side effect can land. The wrapper
        is removed from the 8F surface (only ``ghost.*`` capabilities are
        tracked).
        """
        orig_execute = self.broker.execute

        def _tracked_execute(capability, args, *, source,
                             correlation_id=None, **kw):
            cid = correlation_id or uuid.uuid4().hex[:16]
            if capability.startswith(f"{GHOST_NS}."):
                self.adapter.track(cid, lambda: self.broker.cancel(cid))
                try:
                    return orig_execute(capability, args, source=source,
                                        correlation_id=cid, **kw)
                finally:
                    self.adapter.untrack(cid)
            return orig_execute(capability, args, source=source,
                                correlation_id=cid, **kw)

        self.broker.execute = _tracked_execute  # type: ignore[method-assign]

    # ------------------------------------------------------------ read-only
    def ghost_actions(self) -> List[Dict[str, Any]]:
        out = []
        for spec in ghost_action_specs(shell=ghost_shell_enabled()):
            d = spec.to_dict()
            decision = self.check(spec.action_id, spec.scopes[0])
            d["effective"] = decision
            d["executor"] = ("fixture" if spec.action_id in self.executors
                             else ("disabled" if not self.enabled
                                   else "adapter"))
            d["grantable"] = spec.tier in (Tier.READ_ONLY, Tier.REVERSIBLE)
            out.append(d)
        return out

    def ghost_status(self) -> Dict[str, Any]:
        return {"state": "OK", "adapter": self.adapter.state.to_dict(),
                "shell": ghost_shell_enabled(),
                "roles": {r: ro.to_dict()
                          for r, ro in browser_roles().items()},
                "grants": [g.to_dict() for g in self.ghost_grants.grants()]
                          if self.ghost_grants else [],
                "grant_health": self.ghost_grants.health()
                          if self.ghost_grants else {},
                "comet": self.comet.status() if self.comet is not None else
                         {"enabled": False, "connected": False,
                          "mode": "research", "bound": False},
                "capabilities": [s.action_id for s in
                                 ghost_action_specs(shell=ghost_shell_enabled())]}

    # ------------------------------------------------------------- grants
    def grant(self, capability: str, *, scopes: Tuple[str, ...], target: str,
              role: str = "", issued_by: str = "user",
              ttl_s: Optional[float] = None) -> Dict[str, Any]:
        if self.ghost_grants is None:
            raise ActionValidationError("no session-grant store configured")
        g = self.ghost_grants.issue(capability, scopes=scopes, target=target,
                                    role=role, issued_by=issued_by, ttl_s=ttl_s)
        return {"state": "OK", "grant": g.to_dict()}

    def revoke_grant(self, grant_id: str) -> Dict[str, Any]:
        if self.ghost_grants is None:
            raise ActionValidationError("no session-grant store configured")
        ok = self.ghost_grants.revoke(grant_id)
        return {"state": "OK" if ok else "MISSING", "grant_id": grant_id}

    # ------------------------------------------------------- envelope build
    def _merge_role(self, spec: ActionSpec, args: Optional[Dict[str, Any]],
                    role: str) -> Dict[str, Any]:
        """Merge the explicit browser ``role`` into the validated args.

        The role is a first-class binding field: it must be part of the
        envelope and therefore of the stable action hash. A caller-specified
        role that conflicts with the args role fails closed.
        """
        out = dict(args or {})
        if not role:
            return out
        if "role" in out and out.get("role") != role:
            raise ActionValidationError(
                f"{spec.action_id}: role argument {role!r} conflicts with "
                f"args role {out.get('role')!r}")
        if "role" not in spec.arg_schema:
            raise ActionValidationError(
                f"{spec.action_id}: capability does not accept a browser role")
        out["role"] = role
        return out

    # ------------------------------------------------------------- execute
    def execute(self, action_id: str, *, args: Optional[Dict[str, Any]] = None,
                envelope: Optional[Dict[str, Any]] = None,
                target: str = "", account_scope: str = "",
                scope: str = "", idempotency_key: str = "",
                source: str = "user", confirmation_id: Optional[str] = None,
                confirmed: bool = False, dry_run: bool = False,
                expected_hash: str = "",
                timeout_s: Optional[float] = None,
                role: str = "") -> Dict[str, Any]:
        spec = ghost_spec(action_id)
        if spec is None:
            return {"state": "denied", "code": "UNKNOWN_CAPABILITY",
                    "action_id": action_id,
                    "detail": "unknown Ghost capability"}

        # Emergency stop is a hard latch independent of the broker/gate.
        if self.adapter.state.stopped:
            return {"state": "denied", "code": "EMERGENCY_STOP",
                    "action_id": action_id,
                    "detail": "Ghost adapter is emergency-stopped"}

        # Comet operations require the bound adapter; otherwise they fail
        # closed (no direct-object bypass and no ghost-comet executor).
        if action_id.startswith(f"{GHOST_NS}.comet."):
            if self.comet is None:
                return {"state": "denied", "code": "COMET_UNAVAILABLE",
                        "action_id": action_id,
                        "detail": "Comet research adapter is not bound/"
                                  "configured"}
            if source != "user":
                # The adapter executor hardcodes the human principal; only an
                # authenticated user source may reach it. Model/page/tool
                # output can never operate the research adapter.
                return {"state": "denied", "code": "CONFIRMATION_SOURCE_FORBIDDEN",
                        "action_id": action_id,
                        "detail": "comet operations require an authenticated "
                                  "user source"}

        # High-impact Ghost capabilities always require a fresh confirmation
        # bound to the exact action hash; a session grant is never enough.
        if spec.needs_confirmation and not dry_run and not confirmed \
                and not confirmation_id:
            return {"state": "denied", "code": "CONFIRMATION_REQUIRED",
                    "action_id": action_id,
                    "detail": "fresh human confirmation required immediately "
                              "before execution"}

        # The browser role is a binding field: merge it into args so it is
        # part of the envelope + action hash (never ignored).
        if envelope is not None and role:
            env = dict(envelope)
            env["args"] = self._merge_role(spec, env.get("args"), role)
            envelope = env
        elif role:
            args = self._merge_role(spec, args, role)

        return super().execute(
            action_id, args=args, envelope=envelope, target=target,
            account_scope=account_scope, scope=scope,
            idempotency_key=idempotency_key, source=source,
            confirmation_id=confirmation_id, confirmed=confirmed,
            dry_run=dry_run, expected_hash=expected_hash, timeout_s=timeout_s)

    def preview(self, action_id: str, args: Dict[str, Any], *, target: str,
                account_scope: str = "", scope: str = "",
                idempotency_key: str = "", role: str = "") -> Dict[str, Any]:
        spec = ghost_spec(action_id)
        if spec is None:
            raise ActionValidationError(f"unknown Ghost capability {action_id!r}")
        if role:
            args = self._merge_role(spec, args, role)
        return super().preview(action_id, args, target=target,
                               account_scope=account_scope, scope=scope,
                               idempotency_key=idempotency_key)

    def retry(self, receipt_id: str, *, args: Dict[str, Any],
              target: str, scope: str, account_scope: str = "",
              source: str = "user", confirmation_id: Optional[str] = None,
              confirmed: bool = False,
              role: str = "") -> Dict[str, Any]:
        receipt = self.store.receipt(receipt_id)
        if receipt is None:
            raise ActionError(f"unknown receipt {receipt_id}")
        spec = ghost_spec(receipt.action_id)
        if spec is not None and role:
            args = self._merge_role(spec, args, role)
        # Re-execution uses the capability contract timeout (spec.timeout_s);
        # per-call timeout overrides live on execute, not on retry, so a
        # retry can never silently widen the original time bound.
        return super().retry(receipt_id, args=args, target=target,
                             scope=scope, account_scope=account_scope,
                             source=source, confirmation_id=confirmation_id,
                             confirmed=confirmed)

    # ------------------------------------------------------------- comet
    def comet_ask(self, query: str, *, mode: str = "research",
                  max_sources: int = 8, max_chars: int = 60_000,
                  timeout_s: float = 60.0,
                  capture_screenshot: bool = False,
                  screenshot_max_bytes: int = 128 * 1024,
                  scope: str = "research",
                  source: str = "user") -> Dict[str, Any]:
        """Submit a bounded research query THROUGH the broker path."""
        if self.comet is None:
            return {"state": "denied", "code": "COMET_UNAVAILABLE"}
        # Research-only + every bound is enforced by the capability schema and
        # the request validation; the broker decides/gates/audits, then the
        # bound adapter executes. No direct-object bypass.
        d = self.execute(
            f"{GHOST_NS}.comet.ask",
            args={"mode": "research", "query": query,
                  "max_sources": max_sources, "max_chars": max_chars,
                  "timeout_s": int(timeout_s),
                  "capture_screenshot": capture_screenshot,
                  "screenshot_max_bytes": screenshot_max_bytes},
            target="https://perplexity.ai", scope=scope, source=source)
        if d.get("state") != "OK":
            return d
        # Surface the typed wait id produced by the bound adapter so the
        # caller can poll with comet_wait().
        wait_id = ""
        for w in list(self.comet._active_wait):
            wait_id = w
        d["wait_id"] = wait_id
        return d

    def comet_wait(self, wait_id: str, *, max_wait_s: float = 60.0,
                   source: str = "user") -> Dict[str, Any]:
        if self.comet is None:
            return {"state": "denied", "code": "COMET_UNAVAILABLE"}
        d = self.execute(
            f"{GHOST_NS}.comet.wait",
            args={"wait_id": wait_id, "max_wait_s": int(max_wait_s)},
            target="https://perplexity.ai", scope="research", source=source)
        if d.get("state") != "OK":
            return d
        # The typed wait outcome was produced by the broker executor's
        # adapter call and retained by the adapter.
        state = str(self.comet._last_wait_outcome.pop(wait_id,
                                                      WAIT_CANCELLED))
        if state not in WAIT_STATES:
            state = WAIT_CANCELLED
        return {"state": state, "wait_id": wait_id,
                "detail": "research poll terminal state"}

    def comet_connect(self, *, source: str = "user") -> Dict[str, Any]:
        if self.comet is None:
            return {"state": "denied", "code": "COMET_UNAVAILABLE"}
        return self.execute(
            f"{GHOST_NS}.comet.connect", args={},
            target="https://perplexity.ai", scope="research", source=source)

    def comet_disconnect(self, *, source: str = "user") -> Dict[str, Any]:
        if self.comet is None:
            return {"state": "denied", "code": "COMET_UNAVAILABLE"}
        return self.execute(
            f"{GHOST_NS}.comet.disconnect", args={},
            target="https://perplexity.ai", scope="research", source=source)

    def comet_mode(self, mode: str, *, source: str = "user") -> Dict[str, Any]:
        if self.comet is None:
            return {"state": "denied", "code": "COMET_UNAVAILABLE"}
        return self.execute(
            f"{GHOST_NS}.comet.mode", args={"mode": mode},
            target="https://perplexity.ai", scope="research", source=source)


def ghost_runner(cfg=None, *, executors: Optional[Dict[str, Callable]] = None,
                 enabled: bool = False,
                 session_grants: Optional[SessionGrantStore] = None,
                 adapter: Optional[GhostAdapter] = None,
                 comet: Optional[CometResearchAdapter] = None,
                 power: Optional[Any] = None) -> GhostRunner:
    """Open (or create) the Ghost runner bound to the configured stores.

    When ``power`` (a shared 8O PowerPolicy) is provided it is wired as the
    runner gate and its PowerControl is passed to the underlying broker, so
    Ghost/Comet requests flow through the one policy path and observe global
    emergency stop / disable / circuit / cancellation state.
    """
    from .actions import action_runner
    base = action_runner(cfg, executors=executors, enabled=enabled,
                         power=power)
    runner = GhostRunner(cfg, store=base.store, perms=base.perms,
                         executors=executors, enabled=enabled,
                         session_grants=session_grants, adapter=adapter,
                         comet=comet, power=power)
    return runner


# ---------------------------------------------------------------------------
# mcp-comet research-only adapter contract
# ---------------------------------------------------------------------------
_COMET_MODES = ("research",)


@dataclass(frozen=True)
class CometResearchRequest:
    query: str
    mode: str = "research"       # ONLY "research" is allowed
    max_sources: int = 8
    max_chars: int = 60_000      # bounded page content budget
    timeout_s: float = 60.0
    max_wait_s: float = 60.0
    capture_screenshot: bool = False
    screenshot_max_bytes: int = 128 * 1024

    def __post_init__(self) -> None:
        # Strict research-only enforcement + every numeric bound. Malformed,
        # injected, empty, or unknown modes are rejected here AND in ask().
        if self.mode != "research":
            raise CometResearchError(
                f"mode {self.mode!r} is not allowed; only 'research' is "
                "available ('computer'/'labs'/unknown modes are disabled "
                "until Packet 8O)", code="MODE_DISABLED")
        if not isinstance(self.query, str) or not self.query.strip():
            raise CometResearchError("query must be a non-empty string",
                                     code="BAD_QUERY")
        if len(self.query) > 2000:
            raise CometResearchError("query exceeds 2000 chars",
                                     code="QUERY_TOO_LONG")
        if not (1 <= self.max_sources <= 20):
            raise CometResearchError("max_sources outside [1,20]",
                                     code="BAD_MAX_SOURCES")
        if not (0 < self.max_chars <= 200_000):
            raise CometResearchError("max_chars outside (0, 200000]",
                                     code="BAD_MAX_CHARS")
        if not (0 < self.timeout_s <= 120):
            raise CometResearchError("timeout_s outside (0, 120]",
                                     code="BAD_TIMEOUT")
        if not (0 < self.max_wait_s <= 300):
            raise CometResearchError("max_wait_s outside (0, 300]",
                                     code="BAD_MAX_WAIT")
        if not isinstance(self.capture_screenshot, bool):
            raise CometResearchError("capture_screenshot must be a bool",
                                     code="BAD_SCREENSHOT_FLAG")
        if self.capture_screenshot and \
                not (0 < self.screenshot_max_bytes <= 1024 * 1024):
            raise CometResearchError(
                "screenshot_max_bytes outside (0, 1048576]",
                code="BAD_SCREENSHOT_BYTES")


class CometResearchError(Exception):
    """Typed failure for the research-only mcp-comet adapter."""

    def __init__(self, message: str, *, code: str = "COMET_ERROR"):
        super().__init__(message)
        self.code = code


# Typed wait/poll lifecycle states.
WAIT_PENDING = "PENDING"
WAIT_RUNNING = "RUNNING"
WAIT_COMPLETED = "COMPLETED"
WAIT_TIMEOUT = "TIMEOUT"
WAIT_CANCELLED = "CANCELLED"
WAIT_UNAVAILABLE = "UNAVAILABLE"
WAIT_PROVIDER_ERROR = "PROVIDER_ERROR"
WAIT_DISCONNECTED = "DISCONNECTED"
WAIT_STATES = (WAIT_PENDING, WAIT_RUNNING, WAIT_COMPLETED, WAIT_TIMEOUT,
               WAIT_CANCELLED, WAIT_UNAVAILABLE, WAIT_PROVIDER_ERROR,
               WAIT_DISCONNECTED)


class CometResearchAdapter:
    """Research-only mcp-comet boundary (disabled by default).

    Allowed surface: connect, select mode (research only), submit a bounded
    query, bounded wait/poll, extract sources, extract readable page content,
    optionally capture bounded evidence. ``computer`` mode and any write-
    capable tool are REFUSED until Packet 8O.

    Broker-only execution: the adapter's privileged operations
    (connect/ask/wait/extract/evidence) execute ONLY when invoked by a bound
    ``GhostRunner`` broker executor. A direct object call — even on a bound
    adapter — fails closed with COMET_DIRECT_CALL_FORBIDDEN, so there is no
    way to reach a comet operation that bypasses the broker's decide/gate/
    audit/confirmation. This object never spawns a subprocess or connects to
    a remote MCP server; live use requires an operator-approved, pinned,
    authenticated subprocess that this packet does not start.
    """

    def __init__(self, *, enabled: bool = False,
                 clock: Callable[[], float] = time.time) -> None:
        self.enabled = bool(enabled)
        self._connected = False
        self._lock = threading.RLock()
        self._runner = None          # bound GhostRunner (broker path only)
        self._active_wait: Dict[str, str] = {}
        self._last_wait_outcome: Dict[str, str] = {}
        self._clock = clock
        self._local = threading.local()

    def bind(self, runner: Any) -> None:
        """Bind this adapter to ONE authenticated GhostRunner.

        Direct calls on an unbound adapter are refused; a bound adapter still
        only accepts calls made through the runner's broker executor (the
        thread-local broker context), so the broker remains the single
        decide/gate/audit boundary.
        """
        self._runner = runner

    def _require_broker(self, requested_by: str) -> None:
        """Fail closed unless the call comes from the bound runner's broker
        executor (thread-local context) with the human principal."""
        if self._runner is None:
            raise CometResearchError(
                "COMET_DIRECT_CALL_FORBIDDEN: comet operations execute only "
                "through the authenticated GhostRunner/ActionBroker path",
                code="COMET_DIRECT_CALL_FORBIDDEN")
        if not getattr(self._local, "in_broker", False):
            raise CometResearchError(
                "COMET_DIRECT_CALL_FORBIDDEN: comet operations execute only "
                "through the authenticated ActionBroker executor; a direct "
                "object call would bypass authorization",
                code="COMET_DIRECT_CALL_FORBIDDEN")
        if requested_by != "user":
            raise CometResearchError(
                "CONFIRMATION_SOURCE_FORBIDDEN: only a human may operate the "
                "research adapter", code="CONFIRMATION_SOURCE_FORBIDDEN")

    def _broker_call(self, fn: Callable[[], Any], *,
                     requested_by: str = "user") -> Any:
        """Set the thread-local broker context, run, and restore.

        The broker context is the authorization boundary: the adapter only
        executes while a bound GhostRunner's broker executor has entered this
        context (see ``_require_broker``). Direct object calls never enter it.
        """
        self._require_broker(requested_by)
        self._local.in_broker = True
        try:
            return fn()
        finally:
            self._local.in_broker = False

    def connect(self, *, requested_by: str) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            if not self.enabled:
                return {"state": ST_ADAPTER_DISABLED,
                        "detail": "research adapter is disabled by default"}
            with self._lock:
                self._connected = True
            log_event("comet.research.connected")
            return {"state": "OK",
                    "detail": "connected (research mode only)"}
        return self._broker_call(_run, requested_by=requested_by)

    def disconnect(self, *, requested_by: str = "user") -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            with self._lock:
                self._connected = False
            return {"state": "OK"}
        return self._broker_call(_run, requested_by=requested_by)

    def select_mode(self, mode: str, *, requested_by: str = "user"
                    ) -> Dict[str, Any]:
        # Hard gate: only research mode; computer mode is disabled until 8O.
        def _run() -> Dict[str, Any]:
            if mode != "research":
                return {"state": "denied", "code": "MODE_DISABLED",
                        "detail": "only 'research' mode is available; "
                                  "'computer' mode is disabled until Packet 8O"}
            return {"state": "OK", "mode": "research"}
        return self._broker_call(_run, requested_by=requested_by)

    def ask(self, request: CometResearchRequest, *,
            requested_by: str = "user") -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            if not self.enabled or not self._connected:
                return {"state": ST_ADAPTER_DISABLED,
                        "detail": "research adapter is not connected/enabled"}
            # Strict re-validation (bounds + research-only) even though the
            # dataclass validates on construction: capture_screenshot can
            # never silently bypass policy.
            if request.mode != "research":
                raise CometResearchError("MODE_DISABLED", code="MODE_DISABLED")
            if not request.query or len(request.query) > 2000:
                raise CometResearchError("BAD_QUERY", code="BAD_QUERY")
            if not (1 <= request.max_sources <= 20):
                raise CometResearchError("BAD_MAX_SOURCES",
                                         code="BAD_MAX_SOURCES")
            if not (0 < request.max_chars <= 200_000):
                raise CometResearchError("BAD_MAX_CHARS", code="BAD_MAX_CHARS")
            if not (0 < request.timeout_s <= 120):
                raise CometResearchError("BAD_TIMEOUT", code="BAD_TIMEOUT")
            if not isinstance(request.capture_screenshot, bool):
                raise CometResearchError("BAD_SCREENSHOT_FLAG",
                                         code="BAD_SCREENSHOT_FLAG")
            if request.capture_screenshot and \
                    not (0 < request.screenshot_max_bytes <= 1024 * 1024):
                raise CometResearchError("BAD_SCREENSHOT_BYTES",
                                         code="BAD_SCREENSHOT_BYTES")
            with self._lock:
                wait_id = f"w_{uuid.uuid4().hex[:16]}"
                self._active_wait[wait_id] = WAIT_PENDING
            log_event("comet.research.ask", mode=request.mode,
                      max_sources=request.max_sources,
                      query=redact_text(request.query)[:120])
            return {"state": ST_OK, "mode": request.mode,
                    "wait_id": wait_id,
                    "detail": "research query submitted (bounded)"}
        return self._broker_call(_run, requested_by=requested_by)

    def wait(self, wait_id: str, *, max_wait_s: float = 60.0,
             requested_by: str = "user") -> Dict[str, Any]:
        """Bounded poll with typed timeout, cancellation, reconnect,
        unavailable, and provider-error states.

        Polls the adapter's per-query state for up to ``max_wait_s`` seconds;
        returns one typed WAIT_* outcome and never fakes a completion. The
        poll loop is cancellable by the adapter's emergency stop (checked each
        iteration), so an active wait is actually interrupted, not left to
        report a late false success."""
        def _run() -> Dict[str, Any]:
            if not self.enabled or not self._connected:
                return {"state": WAIT_UNAVAILABLE, "wait_id": wait_id,
                        "detail": "research adapter unavailable"}
            if not (0 < max_wait_s <= 300):
                raise CometResearchError("BAD_MAX_WAIT", code="BAD_MAX_WAIT")
            with self._lock:
                state = self._active_wait.get(wait_id)
                if state is None:
                    return {"state": WAIT_CANCELLED, "wait_id": wait_id,
                            "detail": "unknown/expired wait id (treated as "
                                      "cancelled)"}
            deadline = self._clock() + max_wait_s
            final = WAIT_TIMEOUT
            while self._clock() < deadline:
                # Emergency stop must interrupt an active poll: it is latched
                # on the bound runner's adapter and checked every iteration.
                if self._runner is not None and \
                        self._runner.adapter.state.stopped:
                    final = WAIT_CANCELLED
                    break
                with self._lock:
                    state = self._active_wait.get(wait_id)
                    if state is None:
                        final = WAIT_CANCELLED
                        break
                if state in (WAIT_PENDING, WAIT_RUNNING):
                    time.sleep(0.02)
                    continue
                # terminal provider state (COMPLETED / PROVIDER_ERROR /
                # DISCONNECTED / ...)
                final = state
                break
            with self._lock:
                self._active_wait.pop(wait_id, None)
                self._last_wait_outcome[wait_id] = final
            return {"state": final, "wait_id": wait_id,
                    "detail": "research poll terminal state"}
        return self._broker_call(_run, requested_by=requested_by)

    def mark_wait(self, wait_id: str, state: str) -> None:
        """Advance a wait's typed state (fixture/provider contract)."""
        if state not in WAIT_STATES:
            raise CometResearchError(f"unknown wait state {state!r}",
                                     code="BAD_WAIT_STATE")
        with self._lock:
            if wait_id in self._active_wait:
                self._active_wait[wait_id] = state

    def cancel_wait(self, wait_id: str, *, requested_by: str = "user"
                    ) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            with self._lock:
                if wait_id in self._active_wait:
                    self._active_wait[wait_id] = WAIT_CANCELLED
            return {"state": WAIT_CANCELLED, "wait_id": wait_id,
                    "detail": "research wait cancelled"}
        return self._broker_call(_run, requested_by=requested_by)

    def extract(self, raw: str, *, max_chars: int = 60_000,
                requested_by: str = "user") -> str:
        """Treat extracted page/source text as UNTRUSTED evidence (redacted,
        bounded). It can never authorize an action."""
        def _run() -> str:
            if not (0 < max_chars <= 200_000):
                raise CometResearchError("BAD_MAX_CHARS", code="BAD_MAX_CHARS")
            return redact_text(raw)[:max_chars]
        return self._broker_call(_run, requested_by=requested_by)

    def evidence(self, data: bytes, *, max_bytes: int = 128 * 1024,
                 requested_by: str = "user") -> str:
        """Bounded, redacted evidence reference (never raw persisted bytes)."""
        def _run() -> str:
            if not (0 < max_bytes <= 1024 * 1024):
                raise CometResearchError("BAD_EVIDENCE_BYTES",
                                         code="BAD_EVIDENCE_BYTES")
            return redact_evidence_bytes(data, max_bytes=max_bytes)
        return self._broker_call(_run, requested_by=requested_by)

    def status(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "connected": self._connected,
                "mode": "research",
                "bound": self._runner is not None}


# ---------------------------------------------------------------------------
# Redaction helpers for screenshots / clipboard evidence
# ---------------------------------------------------------------------------
def redact_evidence_bytes(data: bytes, *, max_bytes: int = 128 * 1024) -> str:
    """Clipboard/screenshot evidence is stored only as a bounded, hashed
    reference plus an explicit reason — never the raw private bytes."""
    import hashlib
    digest = hashlib.sha256(data).hexdigest()
    size = len(data)
    if size > max_bytes:
        return json_evidence(digest=digest, size=size,
                             reason="TRUNCATED", clip=size)
    return json_evidence(digest=digest, size=size, reason="OK")


def json_evidence(*, digest: str, size: int, reason: str,
                  clip: int = 0) -> str:
    import json
    return json.dumps({"evidence": {"sha256": digest, "bytes": int(size),
                                    "reason": reason, "clip": int(clip)},
                       "stored": False, "detail": "evidence referenced only; "
                                                  "raw bytes never persisted"})