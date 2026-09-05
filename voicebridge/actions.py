"""Packet 8F: typed, brokered, confirmation-gated browser/desktop/editor actions.

The action layer is deliberately separate from the read-only 8B monitor and
the 8E website discovery flow. It defines typed action contracts (read /
search / fill / draft / reversible-write / external-send / destructive /
system-wide / launcher), exact argument schemas, bounded previews, a stable
action-hash confirmation binding, cancellation, idempotency, persisted
receipts, typed failure states, recovery, and CLI/dashboard/voice parity.

Invariants enforced here (each has a test):

- Every action routes through the existing CapabilityBroker (decide + consume
  confirmation + audit + bounded child execution + cancellation) AND the
  Packet 7E PermissionsStore (mode + grants + scopes). ``restricted`` is the
  default; unrestricted can never auto-approve hard-deny capability patterns.
- Sending, publishing, purchasing, deleting, credential/account/security
  changes, and system-wide actions ALWAYS require a fresh human confirmation
  issued immediately before execution. The confirmation binds to the exact
  action hash (target, account, scope, arguments) via the broker's
  ConfirmationStore args-digest; the confirmation record itself carries the
  expiry, so a stale/expired/changed action can never execute. A model, page,
  skill, tool result, or generated plan can never confirm or widen a grant
  (issuer/source checks refuse them).
- Arbitrary JavaScript, free-form shell, coordinate-only scripts,
  page/model/skill instruction fields, secret-bearing URLs, hidden scopes,
  and unbounded loops are rejected by construction at schema validation.
- Receipts persist bounded redacted metadata (hash, preconditions, args
  digest, typed result, audit id) — never secrets or unnecessary private
  content (private bodies/values are masked).
- Remote rollback is reported honestly: sent messages, purchases, deletions,
  and account changes are ROLLBACK_IMPOSSIBLE by contract; draft/fill/write
  actions declare a compensating recovery operation.
- Live adapters are DISABLED: the only executors are synthetic fixtures
  (explicit operator path) or none (typed ADAPTER_DISABLED). Enabling a real
  action adapter is a separate approved rollout step.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .broker import CapabilityBroker, ExecResult, ToolBinding
from .capabilities import (CapabilitySpec, ConfirmationRecord,
                           ConfirmationStore, Tier)
from .logging_setup import log_event
from .permissions import (MODE_RESTRICTED, MODE_UNRESTRICTED,
                          is_hard_deny_auto, permissions_store)
from .redaction import redact_obj, redact_text

# ---------------------------------------------------------------------------
# Adapters and lifecycle constants
# ---------------------------------------------------------------------------
ADAPTER_BROWSER = "browser"
ADAPTER_EDITOR = "editor"
ADAPTER_DESKTOP = "desktop"
ADAPTER_MAIL = "mail"
ADAPTER_SOCIAL = "social"
ADAPTER_GHOST = "ghost"

ADAPTERS = (ADAPTER_BROWSER, ADAPTER_EDITOR, ADAPTER_DESKTOP,
            ADAPTER_MAIL, ADAPTER_SOCIAL, ADAPTER_GHOST)

# Typed outcome states (receipts / CLI / dashboard / voice share these).
ST_OK = "OK"
ST_PREVIEW = "preview"
ST_DENIED = "denied"
ST_EXPIRED = "expired"
ST_STALE_TARGET = "stale_target"
ST_REPLAYED = "replayed"
ST_AUTH_REQUIRED = "AUTH_REQUIRED"
ST_PARTIAL = "partial"
ST_FAILED = "failed"
ST_TIMEOUT = "timeout"
ST_CANCELLED = "cancelled"
ST_INTERRUPTED = "interrupted"
ST_RECOVERED = "recovered"
ST_ROLLED_BACK = "rolled_back"
ST_ROLLBACK_IMPOSSIBLE = "ROLLBACK_IMPOSSIBLE"
ST_IDEMPOTENT = "idempotent_replay"
ST_ADAPTER_DISABLED = "ADAPTER_DISABLED"

# rollback honesty contract
RB_NONE = "none"
RB_REVERSIBLE = "reversible"
RB_IMPOSSIBLE = "impossible"
RB_KINDS = (RB_NONE, RB_REVERSIBLE, RB_IMPOSSIBLE)

# Confirmation issuer handling. Only the canonical authenticated human
# principal may issue an action confirmation. The strict default-deny check
# (``require_human_principal``) normalizes the authenticated CLI/dashboard
# channels to this exact string and refuses model/page/skill/tool-result/
# generated-plan/arbitrary caller text for EVERY action — a confirmation
# record is always human authority.
HUMAN_PRINCIPAL = "user"
# Labels hard-coded by the authenticated human control surfaces themselves
# (CLI ``actions prepare``, dashboard ``/api/actions``); the web/CLI never
# accept an issuer label from client-supplied text.
HUMAN_CHANNELS = frozenset({HUMAN_PRINCIPAL, "cli:user", "dashboard"})

SCHEMA_VERSION = 1
AUDIT_CAP = 400
RECEIPT_CAP = 400

# Bounded limits for the action layer.
MAX_TARGET = 2048
MAX_SCOPE = 128
MAX_ACCOUNT = 128
MAX_IDEMPOTENCY_KEY = 128
MAX_PREVIEW_CHARS = 400
MAX_RECEIPT_DETAIL = 300
MAX_RECEIPT_ARGS = 8

# Shell metacharacters must never reach any adapter field (argv-only world).
_SHELL_META = re.compile(r"[;&|><`$]")
# Coordinate-only scripts are rejected: browser/editor actions use semantic
# selectors/targets, never raw pixel coordinates.
_COORDINATE_KEYS = frozenset({"x", "y", "coords", "coordinates",
                              "offset_x", "offset_y", "screen_x", "screen_y"})
# Unbounded loops / instruction fields are rejected by construction.
_LOOP_KEYS = frozenset({"loop", "loops", "while", "for", "repeat", "iterations",
                        "count", "until"})
_INSTRUCTION_KEYS = frozenset({"instructions", "prompt", "command", "script",
                               "code", "shell", "javascript", "js"})
_JAVASCRIPT_MARKERS = ("javascript:", "data:text/html", "<script",
                       "</script>", "onclick=", "onerror=", "onload=",
                       "vbscript:")

_APP_ALLOWLIST = frozenset({
    "browser", "editor", "vscode", "terminal", "discord", "telegram",
    "files", "spotify", "fl studio",
})
_SELECTOR_RE = re.compile(r"^[A-Za-z0-9_#.\-:\[\]=\"' ]{1,200}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_FILEPATH_RE = re.compile(r"^[A-Za-z0-9_./~ -]{1,300}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

# Fields whose values are private message/secret content; receipts mask them.
_PRIVATE_FIELDS = frozenset({"body", "value", "content", "text", "change",
                             "subject"})


class ActionError(Exception):
    """Typed failure for the action layer."""


class ActionValidationError(ActionError):
    """An action envelope/args failed validation and was NOT executed."""


class ActionDeniedError(ActionError):
    """A policy/7E gate denied the action (stable code)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _bound(text: Optional[str], limit: int) -> str:
    return (text or "")[:limit]


def require_human_principal(issued_by: str) -> str:
    """Strict, default-deny confirmation-issuer check for the action layer.

    Only the canonical authenticated human principal (``HUMAN_PRINCIPAL``) or
    an explicit label of an authenticated human control surface (CLI ``actions
    prepare``, dashboard ``/api/actions``) may issue an action confirmation.
    Everything else — model, page, skill, tool-result, generated-plan, empty,
    or arbitrary caller text — is refused with CONFIRMATION_SOURCE_FORBIDDEN
    so non-human output can never confirm or widen an action. The returned
    canonical principal is what the confirmation record and audit carry.
    """
    if issued_by not in HUMAN_CHANNELS:
        log_event("actions.issuer.refused",
                  issuer=redact_text(str(issued_by))[:40])
        raise ActionDeniedError(
            "CONFIRMATION_SOURCE_FORBIDDEN",
            f"issuer {issued_by!r} is not an authenticated human channel")
    if issued_by != HUMAN_PRINCIPAL:
        log_event("actions.issuer.channel", channel=issued_by,
                  principal=HUMAN_PRINCIPAL)
    return HUMAN_PRINCIPAL


# ---------------------------------------------------------------------------
# Action contract
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ActionSpec:
    """One typed action contract (separate capability ID + metrics)."""

    action_id: str            # capability id, e.g. act.mail.send
    version: str
    adapter: str
    tier: Tier
    sensitivity: str          # public | internal | personal | secret
    summary: str
    scopes: Tuple[str, ...]   # scope tags this action may request
    arg_schema: Dict[str, Dict[str, Any]]
    timeout_s: float
    side_effects: str
    rollback_kind: str        # none | reversible | impossible
    rollback: str             # human description of recovery/rollback
    preview_fields: Tuple[str, ...]
    app_control_claim: bool = False   # launcher-only adapters stay False

    def __post_init__(self) -> None:
        if not re.match(r"^[a-z][a-z0-9_.-]{2,63}$", self.action_id or ""):
            raise ActionError(f"bad action_id: {self.action_id!r}")
        if self.adapter not in ADAPTERS:
            raise ActionError(f"bad adapter: {self.adapter!r}")
        if not isinstance(self.tier, Tier):
            raise ActionError("tier must be a Tier")
        if self.sensitivity not in ("public", "internal", "personal", "secret"):
            raise ActionError(f"bad sensitivity: {self.sensitivity!r}")
        if not self.arg_schema:
            raise ActionError(f"{self.action_id}: arg_schema required")
        if self.rollback_kind not in RB_KINDS:
            raise ActionError(f"bad rollback_kind: {self.rollback_kind!r}")
        for fld, fspec in self.arg_schema.items():
            if not re.match(r"^[a-z][a-z0-9_]{0,31}$", fld):
                raise ActionError(f"{self.action_id}: bad field {fld!r}")
            if not isinstance(fspec, dict) or not fspec.get("type"):
                raise ActionError(f"{self.action_id}: schema for {fld!r} "
                                  "must declare a type")

    @property
    def needs_confirmation(self) -> bool:
        return (self.tier in (Tier.MUTATING, Tier.DESTRUCTIVE)
                or is_hard_deny_auto(self.action_id) is not None)

    def to_dict(self) -> Dict[str, Any]:
        return {"action_id": self.action_id, "version": self.version,
                "adapter": self.adapter, "tier": self.tier.value,
                "sensitivity": self.sensitivity, "summary": self.summary,
                "scopes": list(self.scopes),
                "arg_schema": self.arg_schema, "timeout_s": self.timeout_s,
                "side_effects": self.side_effects,
                "rollback_kind": self.rollback_kind,
                "rollback": self.rollback,
                "preview_fields": list(self.preview_fields),
                "app_control_claim": self.app_control_claim,
                "confirmation_required": self.needs_confirmation}


def _spec(action_id: str, version: str, adapter: str, tier: Tier,
          summary: str, scopes: Tuple[str, ...], arg_schema: Dict[str, Any],
          side_effects: str, rollback_kind: str, rollback: str,
          preview_fields: Tuple[str, ...], *,
          sensitivity: str = "personal", timeout_s: float = 30.0,
          app_control_claim: bool = False) -> ActionSpec:
    return ActionSpec(action_id=action_id, version=version, adapter=adapter,
                      tier=tier, sensitivity=sensitivity, summary=summary,
                      scopes=scopes, arg_schema=arg_schema,
                      timeout_s=timeout_s, side_effects=side_effects,
                      rollback_kind=rollback_kind, rollback=rollback,
                      preview_fields=preview_fields,
                      app_control_claim=app_control_claim)


def _browser_specs() -> Tuple[ActionSpec, ...]:
    sel = {"type": "str", "required": True, "max": 200, "kind": "selector"}
    return (
        _spec("act.browser.read", "1.0.0", ADAPTER_BROWSER, Tier.READ_ONLY,
              "read bounded text at a semantic selector on a page",
              ("web",),
              {"selector": sel},
              "reads page text via the approved CDP read boundary",
              RB_NONE, "no side effect; nothing to undo",
              ("selector",)),
        _spec("act.browser.search", "1.0.0", ADAPTER_BROWSER, Tier.REVERSIBLE,
              "perform a bounded web search and return redacted results",
              ("web",),
              {"query": {"type": "str", "required": True, "max": 200}},
              "opens a search result view (browser history side effect)",
              RB_REVERSIBLE, "navigate back / clear the search page",
              ("query",)),
        _spec("act.browser.fill", "1.0.0", ADAPTER_BROWSER, Tier.REVERSIBLE,
              "fill one form field at a semantic selector",
              ("web",),
              {"selector": sel,
               "value": {"type": "str", "required": True, "max": 2000}},
              "sets a form field value (no submit/send)",
              RB_REVERSIBLE, "clear the field / restore the prior value",
              ("selector", "value")),
        _spec("act.browser.draft", "1.0.0", ADAPTER_BROWSER, Tier.REVERSIBLE,
              "compose a draft (message/post) without sending",
              ("web",),
              {"recipient": {"type": "str", "required": False, "max": 320,
                             "kind": "email"},
               "subject": {"type": "str", "required": False, "max": 200},
               "body": {"type": "str", "required": True, "max": 4000}},
              "composes/saves a draft; nothing is sent or published",
              RB_REVERSIBLE, "discard the draft (no external side effect)",
              ("recipient", "subject", "body")),
        _spec("act.browser.write", "1.0.0", ADAPTER_BROWSER, Tier.REVERSIBLE,
              "apply a reversible in-page edit with an undo snapshot",
              ("web",),
              {"selector": sel,
               "value": {"type": "str", "required": True, "max": 4000}},
              "edits page content; the prior value is snapshotted for undo",
              RB_REVERSIBLE, "restore the pre-edit snapshot",
              ("selector", "value")),
    )


def _mail_social_specs() -> Tuple[ActionSpec, ...]:
    return (
        _spec("act.mail.send", "1.0.0", ADAPTER_MAIL, Tier.MUTATING,
              "send an email to a recipient (external communication)",
              ("mail",),
              {"recipient": {"type": "str", "required": True, "max": 320,
                             "kind": "email"},
               "subject": {"type": "str", "required": True, "max": 200},
               "body": {"type": "str", "required": True, "max": 4000}},
              "transmits an email to an external recipient",
              RB_IMPOSSIBLE, "a sent message cannot be recalled; no fake rollback",
              ("recipient", "subject", "body")),
        _spec("act.social.post", "1.0.0", ADAPTER_SOCIAL, Tier.MUTATING,
              "publish a post to an external account",
              ("social",),
              {"account": {"type": "str", "required": True, "max": 128},
               "text": {"type": "str", "required": True, "max": 500}},
              "publishes content publicly on an external account",
              RB_IMPOSSIBLE, "a published post cannot be reliably retracted",
              ("account", "text")),
        _spec("act.purchase", "1.0.0", ADAPTER_BROWSER, Tier.MUTATING,
              "execute an online purchase (financial action)",
              ("web",),
              {"item": {"type": "str", "required": True, "max": 200},
               "amount": {"type": "str", "required": True, "max": 40},
               "currency": {"type": "str", "required": True, "max": 8,
                            "kind": "currency"}},
              "charges money for an irreversible purchase",
              RB_IMPOSSIBLE, "a completed purchase cannot be rolled back",
              ("item", "amount", "currency"),
              sensitivity="secret"),
    )


def _editor_specs() -> Tuple[ActionSpec, ...]:
    return (
        _spec("act.editor.write", "1.0.0", ADAPTER_EDITOR, Tier.REVERSIBLE,
              "apply a reversible edit in a VS Code/editor workspace",
              ("workspace",),
              {"file_path": {"type": "str", "required": True, "max": 300,
                             "kind": "filepath"},
               "content": {"type": "str", "required": True, "max": 10000}},
              "writes a file in the selected workspace (undo available)",
              RB_REVERSIBLE, "restore the prior file content from the undo snapshot",
              ("file_path", "content")),
        _spec("act.editor.delete", "1.0.0", ADAPTER_EDITOR, Tier.DESTRUCTIVE,
              "delete a file in a VS Code/editor workspace (irreversible)",
              ("workspace",),
              {"file_path": {"type": "str", "required": True, "max": 300,
                             "kind": "filepath"}},
              "permanently deletes a workspace file",
              RB_IMPOSSIBLE, "deletion is irreversible unless the adapter "
                             "documents a genuine trash/versioning path",
              ("file_path",)),
    )


def _desktop_system_specs() -> Tuple[ActionSpec, ...]:
    return (
        _spec("act.desktop.launch", "1.0.0", ADAPTER_DESKTOP, Tier.REVERSIBLE,
              "launch a known desktop application (launcher only; no app control)",
              ("desktop",),
              {"app": {"type": "str", "required": True, "max": 60,
                       "kind": "app"}},
              "opens one allowlisted application; this launcher does NOT claim "
              "app control (no window/automation control)",
              RB_REVERSIBLE, "close the launched app (best effort; no control claim)",
              ("app",)),
        _spec("act.account.change", "1.0.0", ADAPTER_BROWSER, Tier.MUTATING,
              "change a credential/account/security setting",
              ("account",),
              {"change": {"type": "str", "required": True, "max": 200}},
              "modifies an account/security setting",
              RB_IMPOSSIBLE, "credential/account/security changes are "
                             "irreversible by nature",
              ("change",), sensitivity="secret"),
        _spec("act.system.setting", "1.0.0", ADAPTER_BROWSER, Tier.MUTATING,
              "change one system-wide setting (typed, bounded, reversible)",
              ("system",),
              {"setting": {"type": "str", "required": True, "max": 100},
               "value": {"type": "str", "required": True, "max": 200}},
              "changes a system-wide setting",
              RB_REVERSIBLE, "restore the prior system setting value",
              ("setting", "value"), sensitivity="secret"),
    )


def action_specs() -> Tuple[ActionSpec, ...]:
    return (_browser_specs() + _mail_social_specs()
            + _editor_specs() + _desktop_system_specs())


_SPECS: Dict[str, ActionSpec] = {s.action_id: s for s in action_specs()}


def action_spec(action_id: str) -> Optional[ActionSpec]:
    return _SPECS.get(action_id)


def register_action_spec(spec: ActionSpec) -> None:
    """Register one additional action contract (additive, idempotent).

    Packet 8N adds its Ghost capabilities this way so they are resolvable by
    ``action_spec()``/``validate_envelope`` (the same broker/gate/confirmation/
    receipt machinery) WITHOUT extending the 8F ``action_specs()`` surface:
    the 8F acceptance tests iterate that exact set, and Packet 8N must not
    silently reclassify 8F capabilities. A duplicate id or an unknown adapter
    fails closed.
    """
    if spec.action_id in _SPECS:
        raise ActionError(f"{spec.action_id}: already registered")
    if spec.adapter not in ADAPTERS:
        raise ActionError(f"{spec.action_id}: unknown adapter {spec.adapter!r}")
    if not re.match(r"^[a-z][a-z0-9_.-]{2,63}$", spec.action_id or ""):
        raise ActionError(f"bad action_id: {spec.action_id!r}")
    _SPECS[spec.action_id] = spec


# ---------------------------------------------------------------------------
# Exact argument schema validation (fail closed)
# ---------------------------------------------------------------------------
def _check_javascript(text: str, field: str) -> None:
    low = text.lower()
    for marker in _JAVASCRIPT_MARKERS:
        if marker in low:
            raise ActionValidationError(
                f"field {field!r} carries a JavaScript/payload marker "
                f"({marker!r}) — rejected")


def _reject_structural(args: Dict[str, Any], action_id: str) -> None:
    """Structural rejections shared by every validation path."""
    for key in _COORDINATE_KEYS:
        if key in args:
            raise ActionValidationError(
                f"{action_id}: coordinate-only field {key!r} is rejected "
                "(use semantic selectors/targets)")
    for key in _LOOP_KEYS:
        if key in args:
            raise ActionValidationError(
                f"{action_id}: unbounded-loop field {key!r} is rejected")
    for key in _INSTRUCTION_KEYS:
        if key in args:
            raise ActionValidationError(
                f"{action_id}: instruction field {key!r} cannot "
                "authority an action")


def validate_args(spec: ActionSpec, args: Any) -> Dict[str, Any]:
    """Validate one action's arguments against its EXACT schema.

    Rejects extra keys, coordinate-only fields, loop/instruction fields,
    shell metacharacters, JavaScript payload markers, secret-bearing targets,
    and out-of-bound values. Page/model/skill text can never widen this: the
    envelope has no instruction/prompt/script field by construction.
    """
    if not isinstance(args, dict):
        raise ActionValidationError(f"{spec.action_id}: args must be a mapping")
    _reject_structural(args, spec.action_id)
    unknown = set(args) - set(spec.arg_schema)
    if unknown:
        raise ActionValidationError(
            f"{spec.action_id}: undeclared args: {sorted(unknown)}")
    if len(args) > 16:
        raise ActionValidationError(f"{spec.action_id}: too many args")
    out: Dict[str, Any] = {}
    for field_name, fspec in spec.arg_schema.items():
        present = field_name in args
        if not present:
            if fspec.get("required"):
                raise ActionValidationError(
                    f"{spec.action_id}: missing required arg {field_name!r}")
            continue
        raw = args[field_name]
        kind = fspec.get("type", "str")
        if kind == "str":
            if not isinstance(raw, str):
                raise ActionValidationError(
                    f"{spec.action_id}: arg {field_name!r} must be a string")
            if len(raw) > int(fspec.get("max", 2000)):
                raise ActionValidationError(
                    f"{spec.action_id}: arg {field_name!r} exceeds "
                    f"{fspec.get('max')} chars")
            if _SHELL_META.search(raw):
                raise ActionValidationError(
                    f"{spec.action_id}: arg {field_name!r} contains shell "
                    "metacharacters — rejected")
            _check_javascript(raw, field_name)
            fkind = fspec.get("kind", "")
            if fkind == "url":
                from .websites import normalize_origin
                try:
                    normalize_origin(raw, resolve=False)
                except Exception as exc:
                    raise ActionValidationError(
                        f"{spec.action_id}: target URL rejected: {exc}") from None
            elif fkind == "selector" and not _SELECTOR_RE.match(raw):
                raise ActionValidationError(
                    f"{spec.action_id}: selector {field_name!r} is not a "
                    "bounded semantic selector")
            elif fkind == "email" and raw and not _EMAIL_RE.match(raw):
                raise ActionValidationError(
                    f"{spec.action_id}: invalid email in {field_name!r}")
            elif fkind == "filepath" and not _FILEPATH_RE.match(raw):
                raise ActionValidationError(
                    f"{spec.action_id}: invalid file path in {field_name!r}")
            elif fkind == "currency" and not _CURRENCY_RE.match(raw):
                raise ActionValidationError(
                    f"{spec.action_id}: invalid currency in {field_name!r}")
            elif fkind == "app" and raw.lower() not in _APP_ALLOWLIST:
                raise ActionValidationError(
                    f"{spec.action_id}: app {raw!r} is not on the launcher "
                    "allowlist")
            choices = fspec.get("choices")
            if choices and raw not in choices:
                raise ActionValidationError(
                    f"{spec.action_id}: {field_name!r} not in "
                    f"{sorted(choices)}")
            out[field_name] = raw
        elif kind == "int":
            if not isinstance(raw, int) or isinstance(raw, bool):
                raise ActionValidationError(
                    f"{spec.action_id}: arg {field_name!r} must be an int")
            lo = int(fspec.get("min", -(2 ** 31)))
            hi = int(fspec.get("max", 2 ** 31))
            if not (lo <= raw <= hi):
                raise ActionValidationError(
                    f"{spec.action_id}: arg {field_name!r} out of bounds")
            out[field_name] = raw
        elif kind == "bool":
            if not isinstance(raw, bool):
                raise ActionValidationError(
                    f"{spec.action_id}: arg {field_name!r} must be a bool")
            out[field_name] = raw
        else:
            raise ActionValidationError(
                f"{spec.action_id}: unknown schema kind {kind!r}")
    return out


def _validate_target(spec: ActionSpec, target: str) -> str:
    target = (target or "").strip()
    if not target:
        raise ActionValidationError(f"{spec.action_id}: target is required")
    if len(target) > MAX_TARGET:
        raise ActionValidationError("target too long")
    if _SHELL_META.search(target):
        raise ActionValidationError("target contains shell metacharacters")
    if spec.adapter in (ADAPTER_BROWSER, ADAPTER_MAIL, ADAPTER_SOCIAL):
        from .websites import normalize_origin
        try:
            origin, _clean, _name = normalize_origin(target, resolve=False)
        except Exception as exc:
            raise ActionValidationError(f"target URL rejected: {exc}") from None
        return origin
    if spec.adapter == ADAPTER_GHOST:
        # Ghost desktop/window targets are bounded app/window identifiers or a
        # loopback host; browser-role ghost operations carry a URL origin and
        # are validated by the ghost module's own target schema. Here we only
        # enforce the shared bounds (no shell meta, bounded length).
        return target
    if spec.adapter == ADAPTER_EDITOR:
        if not _FILEPATH_RE.match(target):
            raise ActionValidationError("editor target is not a bounded path")
        return target
    if spec.adapter == ADAPTER_DESKTOP:
        return target
    raise ActionValidationError(f"unknown adapter {spec.adapter!r}")


def _validate_scope(spec: ActionSpec, scope: str) -> str:
    scope = (scope or "").strip()
    if not scope:
        raise ActionValidationError(f"{spec.action_id}: scope is required")
    if len(scope) > MAX_SCOPE or not re.match(r"^[a-z][a-z0-9_./:-]{0,127}$",
                                              scope):
        raise ActionValidationError(f"invalid scope {scope!r}")
    if scope not in spec.scopes:
        raise ActionValidationError(
            f"{spec.action_id}: scope {scope!r} is not declared by the "
            f"contract (declared: {sorted(spec.scopes)})")
    return scope


# ---------------------------------------------------------------------------
# Envelope + stable action hash
# ---------------------------------------------------------------------------
_HASH_FIELDS = ("action_id", "version", "adapter", "target", "account_scope",
                "scope", "args", "idempotency_key")


def envelope_hash(envelope: Dict[str, Any]) -> str:
    """Stable action hash over the exact action identity.

    Covers action_id, version, adapter, target, account_scope, scope, args and
    idempotency_key. The hash is stable across preview/execute/confirm so a
    preview hash can be bound to a confirmation; any change to arguments,
    target, account or scope changes the hash, so a confirmation bound to one
    hash can never be replayed against a different action. Expiry is enforced
    by the ConfirmationStore record TTL, not by this hash.
    """
    payload = {k: envelope[k] for k in _HASH_FIELDS if k in envelope}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           default=repr, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def args_digest(args: Dict[str, Any]) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"),
                           default=repr, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_envelope(spec: ActionSpec, *, args: Dict[str, Any], target: str,
                   account_scope: str = "", scope: str = "",
                   idempotency_key: str = "") -> Dict[str, Any]:
    """Validate and build a canonical action envelope (fail closed)."""
    valid_args = validate_args(spec, args)
    valid_target = _validate_target(spec, target)
    valid_scope = _validate_scope(spec, scope)
    account = _bound(account_scope or "", MAX_ACCOUNT)
    if account and not re.match(r"^[a-z][a-z0-9_./:-]{0,127}$", account):
        raise ActionValidationError(f"invalid account_scope {account!r}")
    env = {"action_id": spec.action_id, "version": spec.version,
           "adapter": spec.adapter, "target": valid_target,
           "account_scope": account, "scope": valid_scope,
           "args": valid_args,
           "idempotency_key": _bound(idempotency_key or "",
                                     MAX_IDEMPOTENCY_KEY)}
    env["action_hash"] = envelope_hash(env)
    return env


def validate_envelope(envelope: Any, spec: Optional[ActionSpec] = None
                      ) -> Dict[str, Any]:
    """Validate a whole envelope (also used as the broker arg_validator).

    Recomputes the action hash and refuses any envelope whose hash does not
    match its fields (tamper/stale detection) or whose fields are malformed.
    """
    if not isinstance(envelope, dict):
        raise ActionValidationError("envelope must be a mapping")
    action_id = str(envelope.get("action_id", ""))
    spec = spec or action_spec(action_id)
    if spec is None:
        raise ActionValidationError(f"unknown action {action_id!r}")
    if envelope.get("version") != spec.version:
        raise ActionValidationError(
            f"{action_id}: version {envelope.get('version')!r} != "
            f"{spec.version}")
    if envelope.get("adapter") != spec.adapter:
        raise ActionValidationError(
            f"{action_id}: adapter mismatch {envelope.get('adapter')!r}")
    valid_args = validate_args(spec, envelope.get("args"))
    valid_target = _validate_target(spec, str(envelope.get("target", "")))
    valid_scope = _validate_scope(spec, str(envelope.get("scope", "")))
    account = _bound(str(envelope.get("account_scope", "")), MAX_ACCOUNT)
    if account and not re.match(r"^[a-z][a-z0-9_./:-]{0,127}$", account):
        raise ActionValidationError("invalid account_scope")
    env = {"action_id": action_id, "version": spec.version,
           "adapter": spec.adapter, "target": valid_target,
           "account_scope": account, "scope": valid_scope,
           "args": valid_args,
           "idempotency_key": _bound(str(envelope.get("idempotency_key", "")),
                                     MAX_IDEMPOTENCY_KEY)}
    claimed = envelope.get("action_hash", "")
    computed = envelope_hash(env)
    if claimed != computed:
        raise ActionValidationError(
            f"{action_id}: action_hash mismatch (stale/tampered envelope)")
    env["action_hash"] = computed
    return env


def _render_preview(spec: ActionSpec, envelope: Dict[str, Any],
                    max_chars: int = MAX_PREVIEW_CHARS,
                    mask_private: bool = False) -> str:
    """Bounded preview of the exact action.

    The USER-FACING preview (``mask_private=False``) shows the target, scope,
    account and the exact fields/recipients/content so the human can give
    informed consent (credential-like values are still redacted). Receipts use
    ``mask_private=True`` so private content is never persisted.
    """
    lines = [f"{spec.action_id} v{spec.version} on {envelope['target']}"]
    if envelope.get("account_scope"):
        lines.append(f"account: {envelope['account_scope']}")
    lines.append(f"scope: {envelope['scope']}")
    for fname in spec.preview_fields:
        if fname not in envelope.get("args", {}):
            continue
        value = envelope["args"][fname]
        if mask_private and fname in _PRIVATE_FIELDS:
            value = "[REDACTED]"
        lines.append(f"{fname}: {redact_text(str(value))[:160]}")
    preview = "\n".join(lines)
    return _bound(preview, max_chars)


def _receipt_args(spec: ActionSpec, args: Dict[str, Any]) -> Dict[str, Any]:
    """Bounded redacted args snapshot for receipts (preconditions + evidence).

    Private content fields (bodies, values, subjects in secret actions) are
    masked; structural preconditions (recipient, selector, file, app, item,
    setting) survive bounded so the receipt stays actionable evidence.
    """
    out: Dict[str, Any] = {}
    for k, v in args.items():
        if k in _PRIVATE_FIELDS:
            out[k] = "[REDACTED]"
        elif isinstance(v, str):
            out[k] = v[:160]
        else:
            out[k] = v
    return out


def _validate_recovery_args(spec: ActionSpec, args: Any) -> Dict[str, Any]:
    """Lenient validation for a compensation pass over stored redacted args.

    The receipt stores bounded redacted preconditions; redacted private
    fields may no longer match pattern schemas, so the recovery pass checks
    only structure (mapping, extra keys, bounds, shell/JS markers) — it never
    re-authors the original private content.
    """
    if not isinstance(args, dict):
        raise ActionValidationError(f"{spec.action_id}: recovery args must "
                                    "be a mapping")
    unknown = set(args) - set(spec.arg_schema)
    if unknown:
        raise ActionValidationError(
            f"{spec.action_id}: undeclared recovery args: {sorted(unknown)}")
    _reject_structural(args, spec.action_id)
    if len(args) > 16:
        raise ActionValidationError(f"{spec.action_id}: too many recovery args")
    out: Dict[str, Any] = {}
    for field_name, fspec in spec.arg_schema.items():
        if field_name not in args:
            continue
        raw = args[field_name]
        if fspec.get("type", "str") == "str":
            if not isinstance(raw, str):
                raise ActionValidationError(
                    f"{spec.action_id}: recovery arg {field_name!r} must be "
                    "a string")
            if len(raw) > int(fspec.get("max", 2000)) * 2:
                raise ActionValidationError(
                    f"{spec.action_id}: recovery arg {field_name!r} too long")
            if _SHELL_META.search(raw):
                raise ActionValidationError(
                    f"{spec.action_id}: recovery arg {field_name!r} contains "
                    "shell metacharacters")
            _check_javascript(raw, field_name)
            out[field_name] = raw
        else:
            out[field_name] = raw
    return out


# ---------------------------------------------------------------------------
# ActionBroker — deny-first confirmation for action capabilities
# ---------------------------------------------------------------------------
class ActionBroker(CapabilityBroker):
    """CapabilityBroker with deny-first confirmation for action capabilities.

    Uses the SAME single engine, registry, confirmation store, audit, bounded
    child execution, and cancellation as the host broker. The only difference:
    a confirmation-required ACTION never auto-issues a confirmation for itself
    — a fresh user-issued confirmation id must be presented (mirroring the
    DESTRUCTIVE two-step flow). This closes the direct-broker bypass for
    send/publish/purchase/delete/account/system actions: calling
    ``execute(action_id, …, source="user")`` without a confirmation id returns
    CONFIRMATION_REQUIRED and runs nothing.
    """

    def __init__(self, confirm_required: Tuple[str, ...] = (),
                 default_ttl_s: float = 300.0):
        super().__init__(default_ttl_s=default_ttl_s)
        self._confirm_required = frozenset(confirm_required)

    def execute(self, capability: str, args: Dict[str, Any], *, source: str,
                correlation_id: Optional[str] = None,
                confirmation_id: Optional[str] = None,
                dry_run: bool = False,
                timeout_s: Optional[float] = None,
                max_output: int = 200_000,
                utterance: str = "") -> ExecResult:
        if capability in self._confirm_required and not confirmation_id:
            log_event("broker.action.deny_first", capability=capability,
                      source=source, code="CONFIRMATION_REQUIRED")
            return ExecResult(denied="CONFIRMATION_REQUIRED")
        return super().execute(capability, args, source=source,
                               correlation_id=correlation_id,
                               confirmation_id=confirmation_id,
                               dry_run=dry_run, timeout_s=timeout_s,
                               max_output=max_output, utterance=utterance)


def actions_broker(cfg=None, *, executors: Optional[Dict[str, Callable]] = None,
                   enabled: bool = False) -> ActionBroker:
    """Build a broker with every action capability registered.

    Live adapters are DISABLED: without injected ``executors`` (synthetic
    fixtures) every action resolves to the typed ADAPTER_DISABLED result.
    """
    from .broker import build_host_broker
    host = build_host_broker()
    confirm_required = tuple(s.action_id for s in action_specs()
                             if s.needs_confirmation)
    broker = ActionBroker(confirm_required=confirm_required)
    broker.registry = host.registry
    broker.engine = host.engine
    for name, binding in host._bindings.items():
        broker._bindings[name] = binding
    runner_executors = dict(executors or {})
    for spec in action_specs():
        fn = runner_executors.get(spec.action_id) or \
            _disabled_executor(spec)
        broker.register(ToolBinding(
            spec=CapabilitySpec(
                name=spec.action_id, version=spec.version, tier=spec.tier,
                summary=spec.summary[:200], scopes=spec.scopes,
                timeout_s=spec.timeout_s,
                arg_validator=_envelope_validator(spec)),
            side_effects=spec.side_effects, fn=fn,
            preview=lambda _env, _s=spec: _render_preview(_s, _env)))
    return broker


def _envelope_validator(spec: ActionSpec):
    def _check(args: Dict[str, Any]) -> None:
        validate_envelope(args, spec)
    return _check


def _disabled_executor(spec: ActionSpec) -> Callable[[Dict[str, Any]], str]:
    def _run(_envelope: Dict[str, Any]) -> str:
        return json.dumps({
            "state": ST_ADAPTER_DISABLED,
            "action_id": spec.action_id,
            "detail": "live action adapter is not enabled; enabling a real "
                      "adapter is a separate approved rollout step",
            "rollback": spec.rollback_kind})
    return _run


# ---------------------------------------------------------------------------
# ActionGate — 7E store overlay (grants / mode / hard-deny)
# ---------------------------------------------------------------------------
class ActionGate:
    """Permission overlay for actions on top of the 7E PermissionsStore.

    Layered checks, in order: mode (restricted default) -> explicit grant
    requirement -> hard-deny confirmation gate.

    Divergence from ``PermissionsStore``'s ``PermissionPolicy`` (documented):
    the 7E policy absolute-denies hard-deny capabilities (HARD_DENIED) for
    website observation; the 8F action gate instead treats hard-deny
    capabilities as CONFIRMATION-GATED per plan §8F ("always require a fresh
    human confirmation immediately before execution"). The 7E store itself
    (mode/grants) is the source of truth for authorization; this gate only
    layers the action confirmation semantics on top.
    """

    def __init__(self, perms: Any):
        self.perms = perms

    def _matching_grant(self, action_id: str, scope: str) -> bool:
        now = _now()
        for g in self.perms.grants():
            if not g.is_active(now):
                continue
            if g.capability != action_id:
                continue
            if scope and scope not in g.scopes:
                continue
            return True
        return False

    def decide(self, spec: ActionSpec, envelope: Dict[str, Any], *,
               source: str, confirmation_id: Optional[str] = None,
               confirmation_required: bool = False) -> Dict[str, Any]:
        """Return a typed gate decision (never executes anything)."""
        scope = str(envelope.get("scope", ""))
        mode, mode_scopes, mode_expires = self.perms.mode()
        hard = is_hard_deny_auto(spec.action_id)
        in_mode_scopes = bool(scope) and scope in set(mode_scopes)

        # Unrestricted pre-authorization: routine NON-destructive work only,
        # inside user-selected scopes, never for hard-deny patterns.
        if mode == MODE_UNRESTRICTED and in_mode_scopes and hard is None \
                and spec.tier in (Tier.READ_ONLY, Tier.REVERSIBLE):
            return {"allowed": True, "code": "ALLOWED",
                    "mode": mode,
                    "detail": f"unrestricted pre-auth tier={spec.tier.value}"}

        # Restricted (default) or unrestricted-outside-scope / sensitive:
        # an explicit non-expired grant is required.
        if not self._matching_grant(spec.action_id, scope):
            return {"allowed": False, "code": "NOT_GRANTED", "mode": mode,
                    "detail": "no matching grant; default deny"}

        if hard is not None or spec.needs_confirmation:
            # source="check" is the read-only preview/check probe (always
            # human-facing); every other non-human source is refused as
            # confirmation authority, so a model/page/skill/tool result can
            # never confirm an action.
            if source not in ("user", "check"):
                return {"allowed": False, "code": "CONFIRMATION_SOURCE_FORBIDDEN",
                        "mode": mode,
                        "detail": f"source={source} cannot confirm actions"}
            if not confirmation_id:
                return {"allowed": False, "code": "CONFIRMATION_REQUIRED",
                        "mode": mode,
                        "detail": "fresh human confirmation is required "
                                  "immediately before execution"}
            return {"allowed": True, "code": "ALLOWED", "mode": mode,
                    "confirmation_id": confirmation_id,
                    "detail": "fresh user confirmation presented"}

        return {"allowed": True, "code": "ALLOWED", "mode": mode,
                "detail": f"granted tier={spec.tier.value}"}


# ---------------------------------------------------------------------------
# Receipt store (persistent, atomic, audited)
# ---------------------------------------------------------------------------
@dataclass
class ActionReceipt:
    receipt_id: str
    correlation_id: str
    action_id: str
    version: str
    adapter: str
    action_hash: str
    args_digest: str
    args_redacted: Dict[str, Any]
    target: str
    account_scope: str
    scope: str
    state: str
    code: str
    detail: str
    preview: str
    confirmation_id: str
    idempotency_key: str
    rollback_kind: str
    compensation: str
    created_at: float
    finished_at: Optional[float]
    duration_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return {"receipt_id": self.receipt_id,
                "correlation_id": self.correlation_id,
                "action_id": self.action_id, "version": self.version,
                "adapter": self.adapter, "action_hash": self.action_hash,
                "args_digest": self.args_digest,
                "args_redacted": redact_obj(self.args_redacted),
                "target": redact_text(self.target),
                "account_scope": redact_text(self.account_scope),
                "scope": self.scope, "state": self.state,
                "code": self.code, "detail": redact_text(self.detail),
                "preview": redact_text(self.preview),
                "confirmation_id": self.confirmation_id,
                "idempotency_key": self.idempotency_key,
                "rollback_kind": self.rollback_kind,
                "compensation": redact_text(self.compensation),
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "duration_ms": self.duration_ms}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActionReceipt":
        return cls(receipt_id=str(d["receipt_id"]),
                   correlation_id=str(d.get("correlation_id", "")),
                   action_id=str(d["action_id"]),
                   version=str(d.get("version", "")),
                   adapter=str(d.get("adapter", "")),
                   action_hash=str(d.get("action_hash", "")),
                   args_digest=str(d.get("args_digest", "")),
                   args_redacted=dict(d.get("args_redacted") or {}),
                   target=str(d.get("target", "")),
                   account_scope=str(d.get("account_scope", "")),
                   scope=str(d.get("scope", "")),
                   state=str(d.get("state", ST_FAILED)),
                   code=str(d.get("code", "")),
                   detail=str(d.get("detail", "")),
                   preview=str(d.get("preview", "")),
                   confirmation_id=str(d.get("confirmation_id", "")),
                   idempotency_key=str(d.get("idempotency_key", "")),
                   rollback_kind=str(d.get("rollback_kind", RB_NONE)),
                   compensation=str(d.get("compensation", "")),
                   created_at=float(d.get("created_at", 0.0)),
                   finished_at=d.get("finished_at"),
                   duration_ms=float(d.get("duration_ms", 0.0)))


class ActionStore:
    """Persistent receipts + audit for the action layer (private atomic JSON).

    Mirrors the 7E/8E store contract: timestamped backups, atomic tmp+replace,
    0600/0700 private modes, bounded audit, redacted output. A receipt left in
    ``running`` state across a restart is recovered to ``interrupted`` (typed,
    honest) so a crash can never be mistaken for a completed action.
    """

    def __init__(self, path: Path | str, *, private_files: bool = True,
                 clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path)
        self._private_files = bool(private_files)
        self._clock = clock
        self._lock = threading.RLock()
        self._state: Dict[str, Any] = {"schema": SCHEMA_VERSION,
                                       "receipts": {}, "audit": []}
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text())
                if isinstance(loaded, dict):
                    self._state = loaded
            except Exception:
                pass   # corrupt file -> fail closed to empty defaults
        if self._private_files:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(self._path.parent, 0o700)
            except OSError:
                pass
        self._recover_interrupted()

    # ------------------------------------------------------------ accessors
    @property
    def path(self) -> Path:
        return self._path

    def receipt(self, receipt_id: str) -> Optional[ActionReceipt]:
        with self._lock:
            rec = self._state.get("receipts", {}).get(receipt_id)
            return ActionReceipt.from_dict(rec) if rec else None

    def receipts(self, limit: int = 30) -> List[ActionReceipt]:
        with self._lock:
            rows = [ActionReceipt.from_dict(r)
                    for r in self._state.get("receipts", {}).values()]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)[
            :max(1, min(int(limit), RECEIPT_CAP))]

    def idempotent_receipt(self, action_id: str,
                           idempotency_key: str) -> Optional[ActionReceipt]:
        """Prior COMPLETED receipt for the same action+idempotency key."""
        if not idempotency_key:
            return None
        for r in self.receipts(RECEIPT_CAP):
            if r.action_id == action_id and r.idempotency_key == idempotency_key \
                    and r.state in (ST_OK, ST_PARTIAL, ST_RECOVERED,
                                    ST_ROLLED_BACK):
                return r
        return None

    def audit(self, limit: int = AUDIT_CAP) -> List[Dict[str, Any]]:
        with self._lock:
            return [redact_obj(e)
                    for e in list(self._state.get("audit", []))[-int(limit):]]

    # ------------------------------------------------------------ persistence
    def _recover_interrupted(self) -> None:
        """Mark stale running receipts as interrupted (crash recovery)."""
        changed = False
        with self._lock:
            for rid, raw in list(self._state.get("receipts", {}).items()):
                if raw.get("state") == "running":
                    raw["state"] = ST_INTERRUPTED
                    raw["code"] = "INTERRUPTED_RESTART"
                    raw["detail"] = "action left running across restart; " \
                                    "never treated as completed"
                    raw["finished_at"] = self._clock()
                    changed = True
        if changed:
            self._save()

    def _backup(self) -> Optional[Path]:
        try:
            bak = self._path.with_name(
                f"{self._path.name}.{int(self._clock())}.bak")
            bak.write_text(json.dumps(self._state, indent=2))
            if self._private_files:
                os.chmod(bak, 0o600)
            return bak
        except OSError:
            return None

    def _save(self) -> Path:
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent),
                                   prefix=f".{self._path.name}.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self._state, fh, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        if self._private_files:
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        return self._path

    def _audit(self, receipt: ActionReceipt) -> None:
        entry = {"ts": self._clock(), "receipt_id": receipt.receipt_id,
                 "action_id": receipt.action_id,
                 "action_hash": receipt.action_hash[:16],
                 "target": redact_text(receipt.target), "scope": receipt.scope,
                 "state": receipt.state, "code": receipt.code,
                 "rollback_kind": receipt.rollback_kind}
        entry = redact_obj(entry)
        with self._lock:
            self._state.setdefault("audit", []).append(entry)
            if len(self._state["audit"]) > AUDIT_CAP:
                del self._state["audit"][:len(self._state["audit"]) - AUDIT_CAP]
        log_event("actions." + receipt.state, receipt_id=receipt.receipt_id,
                  action_id=receipt.action_id,
                  action_hash=receipt.action_hash[:16],
                  scope=receipt.scope, state=receipt.state, code=receipt.code,
                  target=redact_text(receipt.target))

    def save_receipt(self, receipt: ActionReceipt) -> ActionReceipt:
        with self._lock:
            self._backup()
            self._state.setdefault("receipts", {})[receipt.receipt_id] = \
                receipt.to_dict()
            if len(self._state["receipts"]) > RECEIPT_CAP:
                overflow = sorted(self._state["receipts"].items(),
                                  key=lambda kv: kv[1].get("created_at", 0))
                for rid, _raw in overflow[:len(overflow) - RECEIPT_CAP]:
                    self._state["receipts"].pop(rid, None)
            self._save()
        self._audit(receipt)
        return receipt

    def close(self) -> None:
        pass

    # --------------------------------------------- confirmations (Packet 8F)
    def save_confirmation(self, record: ConfirmationRecord) -> None:
        """Persist one action confirmation so a ``prepare`` in one CLI process
        or dashboard request is consumable by a later ``execute`` (single-use,
        expiring, digest-bound). Only metadata is stored — the args/private
        content never reaches the file. Consumed records are kept so replays
        stay typed; expired records older than an hour are pruned and the
        journal is size-capped."""
        with self._lock:
            confs = self._state.setdefault("confirmations", {})
            confs[record.id] = {
                "id": record.id, "capability": record.capability,
                "args_digest": record.args_digest,
                "issued_at": record.issued_at,
                "expires_at": record.expires_at,
                "issued_by": record.issued_by,
                "consumed": bool(record.consumed),
                "consumed_at": record.consumed_at}
            self._prune_confirmations()
            self._save()

    def _prune_confirmations(self) -> None:
        now = self._clock()
        confs = self._state.get("confirmations", {})
        stale = [rid for rid, c in confs.items()
                 if not c.get("consumed")
                 and now - float(c.get("expires_at", 0)) > 3600]
        for rid in stale:
            confs.pop(rid, None)
        if len(confs) > 2000:
            oldest = sorted(confs, key=lambda r: confs[r].get("issued_at", 0))
            for rid in oldest[:len(confs) - 2000]:
                confs.pop(rid, None)

    def confirmations(self) -> List[Dict[str, Any]]:
        """Current persisted confirmation metadata (for store reloads)."""
        with self._lock:
            return [dict(c)
                    for c in self._state.get("confirmations", {}).values()]


def actions_store(cfg) -> ActionStore:
    """Open (or create) the action receipt store at the configured path."""
    path = cfg.get("actions", "state_path", default="") or \
        (cfg.state_dir() / "actions.json")
    return ActionStore(Path(path).expanduser())


class PersistentConfirmationStore(ConfirmationStore):
    """ConfirmationStore persisted through the ActionStore.

    The shared 1A store is in-memory, so a confirmation issued by
    ``actions prepare`` in one CLI process (or one dashboard request) could
    never be consumed by the later ``actions execute`` — the fresh broker in
    the second invocation would report UNKNOWN_CONFIRMATION_ID. This subclass
    keeps the exact single-use / expiry / digest-bound contract but persists
    metadata through the action receipt file (args are never persisted), so
    the two-step preview → prepare → execute flow works across process and
    request boundaries.
    """

    def __init__(self, store: ActionStore, *,
                 clock: Callable[[], float] = time.time,
                 default_ttl_s: float = 300.0):
        super().__init__(clock=clock, default_ttl_s=default_ttl_s)
        self._pstore = store
        for raw in store.confirmations():
            self._records[str(raw.get("id", ""))] = ConfirmationRecord(
                id=str(raw.get("id", "")),
                capability=str(raw.get("capability", "")),
                args_digest=str(raw.get("args_digest", "")),
                issued_at=float(raw.get("issued_at", 0.0)),
                expires_at=float(raw.get("expires_at", 0.0)),
                issued_by=str(raw.get("issued_by", "")),
                consumed=bool(raw.get("consumed", False)),
                consumed_at=raw.get("consumed_at"))

    def issue(self, capability: str, args: Dict[str, Any], *,
              issued_by: str, ttl_s: Optional[float] = None
              ) -> ConfirmationRecord:
        rec = super().issue(capability, args, issued_by=issued_by,
                            ttl_s=ttl_s)
        self._pstore.save_confirmation(rec)
        return rec

    def consume(self, confirmation_id: str, capability: str,
                args: Dict[str, Any]) -> ConfirmationRecord:
        rec = super().consume(confirmation_id, capability, args)
        self._pstore.save_confirmation(rec)
        return rec


# ---------------------------------------------------------------------------
# Synthetic fixture executors (explicit simulation; never live)
# ---------------------------------------------------------------------------
def load_fixture_outcomes(path: Path | str) -> Dict[str, Dict[str, Any]]:
    """Load synthetic adapter outcomes from a fixture file (fail closed).

    The fixture ONLY controls the simulated adapter OUTCOME (state/detail/
    compensation); the action contract, gate, confirmation, hash, receipts and
    audit are always real. Anything malformed fails closed.
    """
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ActionError("action fixture must be a JSON object")
    outcomes: Dict[str, Dict[str, Any]] = {}
    for action_id, out in raw.items():
        if str(action_id).startswith("_"):
            continue   # JSON object keys starting with _ are comments
        if action_spec(action_id) is None:
            raise ActionError(f"fixture references unknown action {action_id!r}")
        if not isinstance(out, dict):
            raise ActionError(f"fixture outcome for {action_id} must be an object")
        state = str(out.get("state") or ST_OK).lower()
        allowed = {s.lower(): s for s in
                   (ST_OK, ST_AUTH_REQUIRED, ST_PARTIAL, ST_FAILED)}
        if state not in allowed:
            raise ActionError(f"fixture outcome state {state!r} for "
                              f"{action_id} is not allowed")
        state = allowed[state]
        outcomes[action_id] = {"state": state,
                               "detail": _bound(str(out.get("detail", "")),
                                                MAX_RECEIPT_DETAIL),
                               "compensation": _bound(
                                   str(out.get("compensation", "")), 300),
                               "delay_s": max(0.0, float(out.get("delay_s", 0.0)))}
    return outcomes


def make_fixture_executors(outcomes: Dict[str, Dict[str, Any]],
                           clock: Callable[[], float] = time.time
                           ) -> Dict[str, Callable[[Dict[str, Any]], str]]:
    """Build executor callables from synthetic outcomes (test/ops only).

    A positive delay_s allows cancellation/partial tests. Executors are
    picklable closures so the broker's spawn-isolated child can run them.
    """
    executors: Dict[str, Callable[[Dict[str, Any]], str]] = {}

    def _mk(action_id: str, out: Dict[str, Any]):
        delay = float(out.get("delay_s", 0.0))

        def _run(envelope: Dict[str, Any]) -> str:
            started = clock()
            if delay > 0:
                time.sleep(delay)
            spec = action_spec(action_id)
            payload: Dict[str, Any] = {
                "state": out.get("state", ST_OK),
                "action_id": action_id,
                "version": spec.version if spec else "1.0.0",
                "adapter": spec.adapter if spec else "",
                "rollback_kind": spec.rollback_kind if spec else RB_NONE,
                "compensation": out.get("compensation", ""),
                "detail": out.get("detail", ""),
                "duration_ms": round((clock() - started) * 1000, 3)}
            if spec is not None and spec.rollback_kind == RB_IMPOSSIBLE:
                payload["rollback"] = RB_IMPOSSIBLE
                payload["rollback_reason"] = \
                    "remote rollback is impossible for this action type"
            return json.dumps(payload)

        return _run

    for action_id, out in outcomes.items():
        executors[action_id] = _mk(action_id, out)
    return executors


# ---------------------------------------------------------------------------
# ActionRunner — the public backend used by CLI, dashboard and voice
# ---------------------------------------------------------------------------
class ActionRunner:
    """Preview / prepare / execute / cancel / receipt / retry / recover.

    One backend contract shared by the authenticated CLI, the authenticated
    dashboard, and voice requests. Every side-effecting path requires the 7E
    gate plus (where the contract demands it) a fresh user confirmation bound
    to the exact action hash.
    """

    def __init__(self, cfg=None, *, store: Optional[ActionStore] = None,
                 perms: Optional[Any] = None,
                 broker: Optional[ActionBroker] = None,
                 executors: Optional[Dict[str, Callable]] = None,
                 enabled: bool = False,
                 clock: Callable[[], float] = time.time,
                 confirmation_ttl_s: Optional[float] = None,
                 max_preview_chars: int = MAX_PREVIEW_CHARS):
        self.cfg = cfg
        self.clock = clock
        self.max_preview_chars = int(max_preview_chars)
        self.enabled = bool(enabled)
        self.executors = dict(executors or {})
        self.store = store
        self.perms = perms
        if self.store is None:
            self.store = ActionStore(
                Path(cfg.state_dir() / "actions.json")
                if cfg is not None else Path("/tmp/vb-actions.json"),
                clock=clock)
        if self.perms is None:
            if cfg is None:
                raise ActionError("a 7E permission store is required")
            self.perms = permissions_store(cfg)
        self.broker = broker or actions_broker(
            cfg, executors=self.executors, enabled=self.enabled)
        self.gate = ActionGate(self.perms)
        self.confirmation_ttl_s = float(
            confirmation_ttl_s if confirmation_ttl_s is not None else (
                (cfg.get("actions", "confirmation_ttl_s", default=300.0)
                 if cfg is not None else 300.0)))
        # Confirmations must span CLI processes / dashboard requests: the 1A
        # in-memory store alone would reject a `prepare` confirmation on the
        # later `execute` (UNKNOWN_CONFIRMATION_ID). Persist metadata through
        # the ActionStore instead — the single-use/expiry/digest contract is
        # unchanged and args/private content never reach the file.
        self.broker.engine.confirmations = PersistentConfirmationStore(
            self.store, clock=self.clock,
            default_ttl_s=self.confirmation_ttl_s)

    # ------------------------------------------------------------ read-only
    def actions(self) -> List[Dict[str, Any]]:
        out = []
        for spec in action_specs():
            d = spec.to_dict()
            decision = self.check(spec.action_id, spec.scopes[0])
            d["effective"] = decision
            d["executor"] = ("fixture" if spec.action_id in self.executors
                             else ("disabled" if not self.enabled
                                   else "adapter"))
            out.append(d)
        return out

    def show(self, action_id: str) -> Dict[str, Any]:
        spec = action_spec(action_id)
        if spec is None:
            raise ActionError(f"unknown action {action_id!r}")
        return spec.to_dict()

    def check(self, action_id: str, scope: str = "") -> Dict[str, Any]:
        spec = action_spec(action_id)
        if spec is None:
            return {"allowed": False, "code": "UNKNOWN_CAPABILITY",
                    "action_id": action_id, "mode": MODE_RESTRICTED,
                    "detail": "unknown action"}
        scope = scope or (spec.scopes[0] if spec.scopes else "")
        try:
            scope = _validate_scope(spec, scope)
        except ActionValidationError as exc:
            return {"allowed": False, "code": "OUT_OF_SCOPE",
                    "action_id": action_id, "mode": MODE_RESTRICTED,
                    "scope": scope, "detail": str(exc)}
        env = {"action_id": spec.action_id, "scope": scope}
        d = self.gate.decide(spec, env, source="check")
        return {"action_id": action_id, "allowed": d["allowed"],
                "code": d["code"], "mode": d.get("mode", MODE_RESTRICTED),
                "scope": scope, "detail": d.get("detail", "")}

    # ------------------------------------------------------------- preview
    def preview(self, action_id: str, args: Dict[str, Any], *, target: str,
                account_scope: str = "", scope: str = "",
                idempotency_key: str = "") -> Dict[str, Any]:
        """Real no-side-effect preview: validated envelope + stable action
        hash + bounded redacted preview + confirmation requirement. Persists
        nothing (no receipt, no audit, no confirmation)."""
        spec = action_spec(action_id)
        if spec is None:
            raise ActionError(f"unknown action {action_id!r}")
        env = build_envelope(spec, args=dict(args or {}), target=target,
                             account_scope=account_scope, scope=scope,
                             idempotency_key=idempotency_key)
        d = self.gate.decide(spec, env, source="check")
        return {"state": ST_PREVIEW, "action_id": action_id,
                "version": spec.version, "adapter": spec.adapter,
                "action_hash": env["action_hash"], "target": env["target"],
                "account_scope": env["account_scope"], "scope": env["scope"],
                "confirmation_required": spec.needs_confirmation,
                "would_decision": d,
                "confirmation_ttl_s": self.confirmation_ttl_s,
                "preview": _render_preview(spec, env, self.max_preview_chars),
                "limits": {"timeout_s": spec.timeout_s,
                           "rollback_kind": spec.rollback_kind},
                "persisted": False}

    def prepare(self, action_id: str, envelope: Dict[str, Any], *,
                issued_by: str) -> Dict[str, Any]:
        """Issue a fresh, expiring, single-use confirmation bound to the exact
        action envelope. The strict default-deny issuer check
        (``require_human_principal``) normalizes the authenticated CLI/
        dashboard/voice channels to the canonical human principal and refuses
        model/page/skill/tool/arbitrary text for EVERY action — a
        confirmation record is always human authority, never widened."""
        spec = action_spec(action_id)
        if spec is None:
            raise ActionError(f"unknown action {action_id!r}")
        env = validate_envelope(envelope, spec)
        issued_by = require_human_principal(issued_by)
        rec = self.broker.issue_confirmation(
            action_id, env, issued_by=issued_by, ttl_s=self.confirmation_ttl_s)
        return {"state": "prepared", "confirmation_id": rec.id,
                "action_id": action_id, "action_hash": env["action_hash"],
                "issued_by": issued_by,
                "expires_at": rec.expires_at,
                "ttl_s": round(rec.expires_at - rec.issued_at, 3)}

    # ------------------------------------------------------------- execute
    def execute(self, action_id: str, *, args: Optional[Dict[str, Any]] = None,
                envelope: Optional[Dict[str, Any]] = None,
                target: str = "", account_scope: str = "",
                scope: str = "", idempotency_key: str = "",
                source: str = "user", confirmation_id: Optional[str] = None,
                confirmed: bool = False, dry_run: bool = False,
                expected_hash: str = "",
                timeout_s: Optional[float] = None) -> Dict[str, Any]:
        """Gate + confirm + execute one action through the broker.

        ``dry_run=True`` returns the real preview and persists nothing.
        ``confirmed=True`` is the caller's explicit fresh human confirmation
        (the broker issues and consumes a single-use hash-bound record).
        ``expected_hash`` (from a prior preview) enables stale-target refusal
        before any execution. ``timeout_s`` overrides the contract timeout for
        the bounded execution window (never extends the contract budget).
        """
        spec = action_spec(action_id)
        if spec is None:
            raise ActionError(f"unknown action {action_id!r}")
        if envelope is None:
            env = build_envelope(spec, args=dict(args or {}), target=target,
                                 account_scope=account_scope, scope=scope,
                                 idempotency_key=idempotency_key)
        else:
            env = validate_envelope(envelope, spec)
        if expected_hash and env["action_hash"] != expected_hash:
            return {"state": ST_STALE_TARGET, "code": "STALE_TARGET",
                    "action_id": action_id,
                    "detail": "target/args/scope changed after preview; "
                              "preview again and re-confirm"}

        if dry_run:
            d = self.gate.decide(spec, env, source=source)
            return {"state": ST_PREVIEW, "action_id": action_id,
                    "action_hash": env["action_hash"],
                    "preview": _render_preview(spec, env,
                                               self.max_preview_chars),
                    "confirmation_required": spec.needs_confirmation,
                    "would_decision": d, "persisted": False}

        # Idempotency: a completed prior receipt for the same key short-circuits.
        if env["idempotency_key"]:
            prior = self.store.idempotent_receipt(action_id,
                                                  env["idempotency_key"])
            if prior is not None:
                return {"state": ST_IDEMPOTENT, "receipt": prior.to_dict(),
                        "detail": "duplicate idempotency key; prior receipt "
                                  "returned, nothing re-executed"}

        # 7E gate (restricted default, grants, hard-deny -> fresh confirmation).
        gate = self.gate.decide(spec, env, source=source,
                                confirmation_id=confirmation_id)
        if not gate["allowed"]:
            if gate["code"] == "CONFIRMATION_REQUIRED" and confirmed \
                    and source == "user":
                rec = self.broker.issue_confirmation(
                    action_id, env, issued_by="user",
                    ttl_s=self.confirmation_ttl_s)
                confirmation_id = rec.id
                gate = self.gate.decide(spec, env, source=source,
                                        confirmation_id=confirmation_id)
            if not gate["allowed"]:
                return {"state": ST_DENIED, "code": gate["code"],
                        "action_id": action_id,
                        "action_hash": env["action_hash"],
                        "detail": gate.get("detail", "")}
        if gate.get("confirmation_id"):
            confirmation_id = gate["confirmation_id"]

        # Execution path (broker enforces confirmation consumption + audit +
        # bounded child + cancellation). Live adapters are disabled unless a
        # fixture executor is explicitly injected.
        if not self.enabled and action_id not in self.executors:
            return {"state": ST_DENIED, "code": ST_ADAPTER_DISABLED,
                    "action_id": action_id,
                    "action_hash": env["action_hash"],
                    "detail": "live action adapter is not enabled; enabling a "
                              "real adapter is a separate approved rollout step"}

        receipt = self._begin_receipt(spec, env, confirmation_id)
        cid = receipt.correlation_id
        res = self.broker.execute(
            action_id, env, source=source, correlation_id=cid,
            confirmation_id=confirmation_id,
            timeout_s=min(float(timeout_s) if timeout_s is not None
                          else spec.timeout_s, spec.timeout_s))
        outcome = self._outcome_from_exec(spec, env, res)
        updated = ActionReceipt.from_dict({
            **receipt.to_dict(),
            "state": outcome["state"], "code": outcome["code"],
            "detail": outcome.get("detail", "")[:MAX_RECEIPT_DETAIL],
            "compensation": outcome.get("compensation", ""),
            "rollback_kind": outcome.get("rollback_kind",
                                         spec.rollback_kind),
            "finished_at": self.clock(),
            "duration_ms": outcome.get("duration_ms", res.duration_ms)})
        self.store.save_receipt(updated)
        return updated.to_dict()

    def _begin_receipt(self, spec: ActionSpec, env: Dict[str, Any],
                       confirmation_id: Optional[str]) -> ActionReceipt:
        receipt = ActionReceipt(
            receipt_id=_new_id("actr"), correlation_id=uuid.uuid4().hex[:16],
            action_id=spec.action_id, version=spec.version,
            adapter=spec.adapter, action_hash=env["action_hash"],
            args_digest=args_digest(env.get("args", {})),
            args_redacted=_receipt_args(spec, env.get("args", {})),
            target=env["target"], account_scope=env["account_scope"],
            scope=env["scope"], state="running", code="", detail="",
            preview=_render_preview(spec, env, self.max_preview_chars,
                                    mask_private=True),
            confirmation_id=confirmation_id or "",
            idempotency_key=env.get("idempotency_key", ""),
            rollback_kind=spec.rollback_kind, compensation="",
            created_at=self.clock(), finished_at=None, duration_ms=0.0)
        self.store.save_receipt(receipt)
        return receipt

    def _outcome_from_exec(self, spec: ActionSpec, env: Dict[str, Any],
                           res: ExecResult) -> Dict[str, Any]:
        """Map a broker ExecResult to the typed action outcome vocabulary."""
        if res.denied:
            code = res.denied
            if code == "CONFIRMATION_REQUIRED":
                return {"state": ST_DENIED, "code": code,
                        "detail": "fresh human confirmation is required "
                                  "immediately before execution"}
            if code == "EXPIRED_CONFIRMATION":
                return {"state": ST_EXPIRED, "code": code,
                        "detail": "confirmation expired; preview and confirm "
                                  "again"}
            if code == "TARGET_MISMATCH":
                return {"state": ST_STALE_TARGET, "code": code,
                        "detail": "confirmation no longer matches the action "
                                  "hash (target/args/scope changed)"}
            if code == "REPLAYED_CONFIRMATION":
                return {"state": ST_REPLAYED, "code": code,
                        "detail": "confirmation already consumed"}
            if code == "UNKNOWN_CONFIRMATION_ID":
                return {"state": ST_DENIED, "code": code,
                        "detail": "unknown confirmation id"}
            if code == "CONFIRMATION_SOURCE_FORBIDDEN":
                return {"state": ST_DENIED, "code": code,
                        "detail": "non-human source cannot confirm actions"}
            if code == "HARD_DENIED":
                return {"state": ST_DENIED, "code": code,
                        "detail": "hard-deny capability pattern"}
            if code == "DESTRUCTIVE_SCREEN":
                return {"state": ST_DENIED, "code": code,
                        "detail": "destructive-intent screen tripped"}
            return {"state": ST_DENIED, "code": code,
                    "detail": res.error or "denied"}
        if res.cancelled:
            return {"state": ST_CANCELLED, "code": "CANCELLED",
                    "detail": "action cancelled",
                    "duration_ms": res.duration_ms}
        if res.timed_out:
            return {"state": ST_TIMEOUT, "code": "TIMEOUT",
                    "detail": "action exceeded its resource budget",
                    "duration_ms": res.duration_ms}
        if not res.ok:
            return {"state": ST_FAILED, "code": "EXEC_FAILED",
                    "detail": (res.error or "adapter failed")[:MAX_RECEIPT_DETAIL],
                    "duration_ms": res.duration_ms}
        try:
            payload = json.loads(res.output)
        except Exception:
            return {"state": ST_FAILED, "code": "BAD_ADAPTER_OUTPUT",
                    "detail": "adapter returned an unparseable result",
                    "duration_ms": res.duration_ms}
        if not isinstance(payload, dict):
            return {"state": ST_FAILED, "code": "BAD_ADAPTER_OUTPUT",
                    "detail": "adapter result was not an object",
                    "duration_ms": res.duration_ms}
        state = str(payload.get("state") or ST_OK)
        if state not in (ST_OK, ST_AUTH_REQUIRED, ST_PARTIAL, ST_FAILED):
            state = ST_FAILED
        return {"state": state,
                "code": str(payload.get("code") or state).upper(),
                "detail": str(payload.get("detail") or "")[
                    :MAX_RECEIPT_DETAIL],
                "compensation": str(payload.get("compensation") or ""),
                "rollback_kind": str(payload.get("rollback_kind")
                                     or spec.rollback_kind),
                "duration_ms": float(payload.get("duration_ms")
                                     or res.duration_ms)}

    # ------------------------------------------------------------- cancel
    def cancel(self, receipt_id: str) -> Dict[str, Any]:
        receipt = self.store.receipt(receipt_id)
        if receipt is None:
            raise ActionError(f"unknown receipt {receipt_id}")
        if receipt.state == "running":
            first = self.broker.cancel(receipt.correlation_id)
            finished = self.store.receipt(receipt_id)
            if finished is not None and finished.state == "running":
                updated = ActionReceipt.from_dict({
                    **finished.to_dict(),
                    "state": ST_CANCELLED, "code": "CANCELLED",
                    "detail": "action cancelled by operator",
                    "finished_at": self.clock()})
                self.store.save_receipt(updated)
                finished = updated
            return {"state": ST_CANCELLED, "receipt_id": receipt_id,
                    "first": first,
                    "detail": "cancel requested (idempotent)"}
        return {"state": receipt.state, "receipt_id": receipt_id,
                "detail": f"receipt is not running (state={receipt.state}); "
                          "nothing to cancel"}

    # ------------------------------------------------------- receipt access
    def receipt(self, receipt_id: str) -> Dict[str, Any]:
        receipt = self.store.receipt(receipt_id)
        if receipt is None:
            raise ActionError(f"unknown receipt {receipt_id}")
        return receipt.to_dict()

    def receipts(self, limit: int = 30) -> Dict[str, Any]:
        rows = [r.to_dict() for r in self.store.receipts(limit)]
        return {"state": "OK", "count": len(rows), "receipts": rows}

    # -------------------------------------------------------------- retry
    def retry(self, receipt_id: str, *, args: Dict[str, Any],
              target: str, scope: str, account_scope: str = "",
              source: str = "user", confirmation_id: Optional[str] = None,
              confirmed: bool = False) -> Dict[str, Any]:
        """Re-execute a failed/interrupted action with the SAME arguments.

        The caller must re-supply the exact arguments (receipts never store
        private content); if the re-supplied args differ from the receipt's
        args digest the retry is refused as stale. Sensitive actions require a
        fresh confirmation. The original idempotency key is preserved so a
        retry is still idempotent.
        """
        receipt = self.store.receipt(receipt_id)
        if receipt is None:
            raise ActionError(f"unknown receipt {receipt_id}")
        if receipt.state in (ST_OK, ST_PARTIAL, ST_RECOVERED, ST_ROLLED_BACK):
            return {"state": ST_IDEMPOTENT, "receipt": receipt.to_dict(),
                    "detail": "receipt already completed; use recover for "
                              "compensation, not retry"}
        spec = action_spec(receipt.action_id)
        if spec is None:
            raise ActionError(f"unknown action {receipt.action_id!r}")
        if receipt.args_digest and \
                args_digest(dict(args or {})) != receipt.args_digest:
            return {"state": ST_STALE_TARGET, "code": "ARGS_MISMATCH",
                    "receipt_id": receipt_id,
                    "detail": "retry args differ from the original receipt; "
                              "this would be a new action, not a retry"}
        if (receipt.target != (target or "")
                or receipt.account_scope != (account_scope or "")
                or receipt.scope != (scope or "")):
            return {"state": ST_STALE_TARGET, "code": "TARGET_SCOPE_MISMATCH",
                    "receipt_id": receipt_id,
                    "detail": "retry target/account/scope differ from the "
                              "original receipt; this would be a new action, "
                              "not a retry"}
        return self.execute(receipt.action_id, args=dict(args or {}),
                            target=target, account_scope=account_scope,
                            scope=scope,
                            idempotency_key=receipt.idempotency_key,
                            source=source, confirmation_id=confirmation_id,
                            confirmed=confirmed)

    # ------------------------------------------------------------- recover
    def recover(self, receipt_id: str, *, source: str = "user",
                confirmation_id: Optional[str] = None,
                confirmed: bool = False) -> Dict[str, Any]:
        """Compensating rollback for a completed action, reported honestly.

        ``rollback_kind=reversible`` actions run the declared compensation
        (through the same gate + confirmation path for sensitive actions);
        ``rollback_kind=impossible`` (sent messages, purchases, deletions,
        account changes) returns the typed ROLLBACK_IMPOSSIBLE result — the
        layer never fakes a rollback for a remote irreversible action.
        """
        receipt = self.store.receipt(receipt_id)
        if receipt is None:
            raise ActionError(f"unknown receipt {receipt_id}")
        spec = action_spec(receipt.action_id)
        if spec is None:
            raise ActionError(f"unknown action {receipt.action_id!r}")
        if spec.rollback_kind == RB_IMPOSSIBLE:
            return {"state": ST_ROLLBACK_IMPOSSIBLE,
                    "receipt_id": receipt_id,
                    "action_id": receipt.action_id,
                    "detail": "remote rollback is impossible for this action "
                              "type (sent message / purchase / deletion / "
                              "account change); no rollback is faked"}
        if spec.rollback_kind != RB_REVERSIBLE:
            return {"state": ST_ROLLBACK_IMPOSSIBLE,
                    "receipt_id": receipt_id,
                    "action_id": receipt.action_id,
                    "detail": "no compensating operation is declared"}
        if not self.enabled and receipt.action_id not in self.executors:
            return {"state": ST_DENIED, "code": ST_ADAPTER_DISABLED,
                    "receipt_id": receipt_id,
                    "detail": "recovery requires an enabled adapter (or an "
                              "explicit fixture)"}
        # Compensation pass: same action contract, lenient validation over the
        # stored redacted precondition args (never re-authors private content).
        stored = _receipt_args(spec, receipt.args_redacted or {})
        env = {"action_id": spec.action_id, "version": spec.version,
               "adapter": spec.adapter, "target": receipt.target,
               "account_scope": receipt.account_scope,
               "scope": receipt.scope,
               "args": _validate_recovery_args(spec, stored),
               "idempotency_key": f"recover:{receipt_id}"}
        env["action_hash"] = envelope_hash(env)
        gate = self.gate.decide(spec, env, source=source,
                                confirmation_id=confirmation_id)
        if not gate["allowed"]:
            if gate["code"] == "CONFIRMATION_REQUIRED" and confirmed \
                    and source == "user":
                rec = self.broker.issue_confirmation(
                    receipt.action_id, env, issued_by="user",
                    ttl_s=self.confirmation_ttl_s)
                confirmation_id = rec.id
                gate = self.gate.decide(spec, env, source=source,
                                        confirmation_id=confirmation_id)
            if not gate["allowed"]:
                return {"state": ST_DENIED, "code": gate["code"],
                        "receipt_id": receipt_id,
                        "detail": gate.get("detail", "")}
        if gate.get("confirmation_id"):
            confirmation_id = gate["confirmation_id"]
        res = self.broker.execute(receipt.action_id, env, source=source,
                                  correlation_id=uuid.uuid4().hex[:16],
                                  confirmation_id=confirmation_id,
                                  timeout_s=spec.timeout_s)
        if not res.ok or res.denied:
            return {"state": ST_FAILED, "code": "RECOVERY_FAILED",
                    "receipt_id": receipt_id,
                    "detail": (res.error or res.denied or "recovery failed")[
                        :MAX_RECEIPT_DETAIL]}
        comp = receipt.compensation or spec.rollback
        updated = ActionReceipt.from_dict({
            **receipt.to_dict(),
            "state": ST_RECOVERED, "code": "RECOVERED",
            "detail": f"compensation applied: {comp}"[:MAX_RECEIPT_DETAIL],
            "compensation": comp,
            "finished_at": self.clock()})
        self.store.save_receipt(updated)
        return {"state": ST_RECOVERED, "receipt_id": receipt_id,
                "action_id": receipt.action_id,
                "detail": f"compensation applied: {comp}",
                "receipt": updated.to_dict()}


def _sample_target(spec: ActionSpec) -> str:
    if spec.adapter in (ADAPTER_BROWSER, ADAPTER_MAIL, ADAPTER_SOCIAL):
        return "https://example.test"
    if spec.adapter == ADAPTER_GHOST:
        return "desktop"
    if spec.adapter == ADAPTER_EDITOR:
        return "workspace.example"
    return "desktop"


# ---------------------------------------------------------------------------
# Deterministic voice action requests (L0-style; no model, no router change)
# ---------------------------------------------------------------------------
# Only EXACTLY-typed phrasings are accepted: an email address and explicit
# content must be present. Anything fuzzy returns None and routes normally —
# the bridge never guesses action arguments from free text.
_VOICE_ACTION_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(
        r"^\s*draft\s+(?:an?\s+|a\s+)?(?:email|message|draft)\s+to\s+"
        r"(?P<recipient>[\w.+-]+@[\w.-]+\.[a-z]{2,})\s+(?:about|"
        r"with\s+subject|subject)\s+(?P<subject>.+?)\s*[.!]?$", re.I),
     "act.browser.draft", "https://mail.google.com", "web", "gmail:default"),
    (re.compile(
        r"^\s*send\s+(?:an?\s+|a\s+)?(?:email|message)\s+to\s+"
        r"(?P<recipient>[\w.+-]+@[\w.-]+\.[a-z]{2,})\s+"
        r"(?:(?:about|subject)\s+(?P<subject>.+?)\s+)?"
        r"(?:saying|with\s+body|body)\s+(?P<body>.+?)\s*[.!]?$", re.I),
     "act.mail.send", "https://mail.google.com", "mail", "gmail:default"),
    (re.compile(
        r"^\s*post\s+to\s+(?P<account>[a-z0-9_.-]+)\s*:\s*(?P<text>.+?)\s*[.!]?$",
        re.I),
     "act.social.post", "https://www.instagram.com", "social", "social:default"),
    (re.compile(
        r"^\s*post\s+(?P<account>[a-z0-9_.-]+)\s*:\s*(?P<text>.+?)\s*[.!]?$",
        re.I),
     "act.social.post", "https://www.instagram.com", "social", "social:default"),
)


def voice_action_request(text: str) -> Optional[Dict[str, Any]]:
    """Deterministically map an EXACT action phrasing to a typed request.

    Returns a dict with action_id/args/target/scope/account_scope, or None
    when the phrasing is not exactly typed. The caller then routes the result
    through the SAME ActionRunner preview + confirmation path as CLI and
    dashboard requests. A page/model/skill can never reach this matcher with
    authority: only the user's utterance text is inspected, and only
    structurally-exact requests produce an action.
    """
    t = (text or "").strip()
    for pattern, action_id, target, scope, account in _VOICE_ACTION_PATTERNS:
        m = pattern.match(t)
        if not m:
            continue
        spec = action_spec(action_id)
        if spec is None:
            continue
        if action_id == "act.browser.draft":
            # draft is reversible and never sent: subject comes from the
            # utterance, the body stays empty (a draft the user can finish).
            args = {"recipient": m.group("recipient"),
                    "subject": (m.group("subject") or "").strip(), "body": ""}
        elif action_id == "act.mail.send":
            # send requires BOTH explicit subject and explicit body from the
            # utterance; nothing is ever guessed or fabricated for a send.
            args = {"recipient": m.group("recipient"),
                    "subject": (m.group("subject") or "").strip(),
                    "body": (m.group("body") or "").strip()}
        elif action_id == "act.social.post":
            args = {"account": (m.group("account") or "").strip(),
                    "text": (m.group("text") or "").strip()}
        else:
            continue
        try:
            args = validate_args(spec, args)
            target = _validate_target(spec, target)
            scope = _validate_scope(spec, scope)
        except ActionValidationError:
            return None
        return {"action_id": action_id, "args": args, "target": target,
                "scope": scope, "account_scope": account}
    return None


def action_runner(cfg, *, executors: Optional[Dict[str, Callable]] = None,
                  enabled: bool = False) -> ActionRunner:
    """Open (or create) the action runner bound to the configured stores."""
    enabled = bool(enabled) or bool(
        cfg.get("actions", "enabled", default=False))
    return ActionRunner(cfg, executors=executors, enabled=enabled)