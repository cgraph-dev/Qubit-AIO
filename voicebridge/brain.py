"""Packet 8T-F — Qubit Brain profiles: multi-principal identity layer.

Implements the code-side core of ``deintegratecuritate.md`` (the Brain
design authority): separate security principals (owner persons,
organizations, employees) with memberships that never merge silently;
per-principal per-identity profiles whose every field carries owner,
purpose, source/trust, and retention; account records bound to approved
origins (homographs refused, same origin may hold many accounts, the
selected identity is always shown); a vault of secret REFERENCES ONLY
(raw values are refused — values live in the OS keyring/manager, Qubit
stores references like everywhere else); explicit forget with
retention sweeping; redacted export; and a guided onboarding wizard
(required/optional/skippable sections, preview before saving, offline).

Enforcement posture (audited, never silent):

* one employee never receives another principal's data: every read is
  scoped to an explicit principal + identity;
* company administration manages memberships, never member secrets:
  the store holds no secret values at all;
* web/model/tool output can never widen authority: imports of profile
  data from untrusted text are refused (structured CLI entry only);
* secrets, cookies, browser profiles, and raw audio are refused at the
  field gate (``BRAIN_SECRET_CLASS``) with a pointer to vault references;
* audit events carry principal/field metadata only — values stay out of
  journals, receipts, and errors (redaction choke point).

Form-fill and message-send consumption of Brain data arrives with the
browser/desktop capability packets; this module is the authoritative
registry they will read (single source of truth, no second store).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

SCHEMA = "qubit-brain/1"
STORE_NAME = "brain.json"
MAX_FILE_BYTES = 2_000_000
MAX_FIELD_VALUE = 2000
MAX_FIELDS_PER_PROFILE = 256

KINDS = ("person", "organization", "employee")
TRUST = ("user", "observed", "inferred")
SECRET_CLASS_KEYS = ("password", "passwd", "secret", "token", "api_key",
                     "apikey", "cookie", "private_key", "recovery_code",
                     "seed_phrase", "pin", "ssn", "card_number")

CONFIRM_CODE = "BRAIN_CONFIRM_REQUIRED"
ESTOP_CODE = "BRAIN_ESTOP"
NOT_FOUND_CODE = "BRAIN_NOT_FOUND"
EXISTS_CODE = "BRAIN_EXISTS"
BAD_INPUT_CODE = "BRAIN_BAD_INPUT"
SECRET_CLASS_CODE = "BRAIN_SECRET_CLASS"
HOMOGRAPH_CODE = "BRAIN_HOMOGRAPH"
CORRUPT_CODE = "BRAIN_STORE_CORRUPT"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.,@()/\-—–:]{0,79}$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class BrainError(Exception):
    """Typed Brain failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> float:
    return time.time()


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "principals": {}, "memberships": [],
            "profiles": {}, "accounts": {}, "vault": {}}


def _load(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.exists():
        return _blank()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise BrainError(CORRUPT_CODE, "brain store too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except BrainError:
        raise
    except (OSError, ValueError) as exc:
        raise BrainError(CORRUPT_CODE,
                         f"brain store unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise BrainError(CORRUPT_CODE, "brain store has an unknown schema")
    for key in ("principals", "profiles", "accounts", "vault"):
        raw.setdefault(key, {})
    raw.setdefault("memberships", [])
    return raw


def _save(cfg: Any, state: Dict[str, Any]) -> None:
    path = _store_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{STORE_NAME}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        raise BrainError(CORRUPT_CODE,
                         f"brain store write failed: {exc}") from exc


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"brain.{op}", actor=actor, **detail)
    except Exception:
        pass


def _check_id(text: str, what: str = "id") -> str:
    clean = (text or "").strip().lower()
    if not _ID_RE.match(clean):
        raise BrainError(BAD_INPUT_CODE,
                         f"{what} must match ^[a-z0-9][a-z0-9_.-]{{0,63}}$")
    return clean


def _check_label(label: str) -> str:
    text = (label or "").strip()
    if not _LABEL_RE.match(text):
        raise BrainError(BAD_INPUT_CODE, "label is required (≤80 chars)")
    return text


def _check_field_key(key: str) -> str:
    clean = (key or "").strip().lower()
    if not _FIELD_RE.match(clean):
        raise BrainError(BAD_INPUT_CODE,
                         "field must match ^[a-z][a-z0-9_]{1,63}$")
    lowered = clean.replace("-", "_")
    if any(marker in lowered for marker in SECRET_CLASS_KEYS):
        raise BrainError(SECRET_CLASS_CODE,
                         f"field {key!r} is secret-class: values are never "
                         f"stored — keep the value in the OS keyring/manager "
                         f"and record a vault reference instead")
    return clean


def _get_principal(state: Dict[str, Any], pid: str) -> Dict[str, Any]:
    rec = state["principals"].get(pid)
    if rec is None:
        raise BrainError(NOT_FOUND_CODE, f"no principal {pid!r}")
    return rec


def _profile_key(principal: str, identity: str) -> str:
    return f"{principal}::{identity or 'default'}"


def _public_field(field: Dict[str, Any], reveal: bool = False) -> Dict[str, Any]:
    out = {"key": field["key"], "purpose": field.get("purpose", ""),
           "source": field.get("source", "user"),
           "trust": field.get("trust", "user"),
           "retention_days": field.get("retention_days"),
           "created_at": field.get("created_at"),
           "updated_at": field.get("updated_at")}
    if reveal:
        out["value"] = field.get("value", "")
    else:
        # Masked listing (like a banking app): presence + provenance are
        # shown, values only via export (owner portability path).
        out["value"] = "…" if field.get("value") else ""
    return out


# ---------------------------------------------------------------- principals
def principal_add(cfg: Any, pid: str, kind: str, label: str, *,
                  requested_by: str = "cli:user",
                  confirmed: bool = False) -> Dict[str, Any]:
    """Register a security principal (never merged with another)."""
    if not confirmed:
        raise BrainError(CONFIRM_CODE,
                         "principal add requires explicit --confirm")
    clean_id = _check_id(pid, "principal")
    if kind not in KINDS:
        raise BrainError(BAD_INPUT_CODE, f"kind must be one of {KINDS}")
    state = _load(cfg)
    if clean_id in state["principals"]:
        raise BrainError(EXISTS_CODE, f"principal {clean_id!r} exists")
    rec = {"id": clean_id, "kind": kind, "label": _check_label(label),
           "created_at": _now(), "updated_by": requested_by}
    state["principals"][clean_id] = rec
    _save(cfg, state)
    _audit("principal.add", requested_by,
           {"principal": clean_id, "kind": kind})
    return {"state": "OK", "principal": rec}


def principal_list(cfg: Any) -> Dict[str, Any]:
    """List principals (labels only)."""
    state = _load(cfg)
    return {"state": "OK", "principals": [
        state["principals"][k] for k in sorted(state["principals"])]}


def member_add(cfg: Any, org: str, employee: str, *,
               requested_by: str = "cli:user",
               confirmed: bool = False) -> Dict[str, Any]:
    """Explicit membership link (the only way two principals join)."""
    if not confirmed:
        raise BrainError(CONFIRM_CODE,
                         "member add requires explicit --confirm")
    state = _load(cfg)
    org_rec = _get_principal(state, _check_id(org, "organization"))
    emp_rec = _get_principal(state, _check_id(employee, "employee"))
    if org_rec["kind"] != "organization":
        raise BrainError(BAD_INPUT_CODE, f"{org!r} is not an organization")
    if emp_rec["kind"] != "employee":
        raise BrainError(BAD_INPUT_CODE, f"{employee!r} is not an employee")
    link = {"org": org_rec["id"], "employee": emp_rec["id"],
            "since": _now(), "by": requested_by}
    if link in [{"org": m["org"], "employee": m["employee"]}
                for m in state["memberships"]]:
        raise BrainError(EXISTS_CODE, "membership already recorded")
    state["memberships"].append(link)
    _save(cfg, state)
    _audit("member.add", requested_by,
           {"org": org_rec["id"], "employee": emp_rec["id"]})
    return {"state": "OK", "membership": link}


def member_remove(cfg: Any, org: str, employee: str, *,
                  requested_by: str = "cli:user",
                  confirmed: bool = False) -> Dict[str, Any]:
    """Remove a membership; member data and audit history are preserved."""
    if not confirmed:
        raise BrainError(CONFIRM_CODE,
                         "member remove requires explicit --confirm")
    state = _load(cfg)
    before = len(state["memberships"])
    state["memberships"] = [
        m for m in state["memberships"]
        if not (m.get("org") == _check_id(org, "organization")
                and m.get("employee") == _check_id(employee, "employee"))]
    if len(state["memberships"]) == before:
        raise BrainError(NOT_FOUND_CODE, "no such membership")
    _save(cfg, state)
    _audit("member.remove", requested_by, {"org": org, "employee": employee})
    return {"state": "OK",
            "removed": {"org": org, "employee": employee},
            "note": "member data and audit history are preserved"}


# ---------------------------------------------------------------- profiles
def profile_set(cfg: Any, principal: str, key: str, value: str, *,
                identity: str = "default", purpose: str = "",
                trust: str = "user", retention_days: Optional[int] = None,
                requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Set one profile field with provenance (scoped to principal+identity)."""
    if not confirmed:
        raise BrainError(CONFIRM_CODE,
                         "profile set requires explicit --confirm")
    clean_key = _check_field_key(key)
    text = value or ""
    if len(text) > MAX_FIELD_VALUE:
        raise BrainError(BAD_INPUT_CODE,
                         f"value exceeds {MAX_FIELD_VALUE} chars")
    if trust not in TRUST:
        raise BrainError(BAD_INPUT_CODE, f"trust must be one of {TRUST}")
    if retention_days is not None:
        try:
            retention_days = int(retention_days)
        except (TypeError, ValueError):
            raise BrainError(BAD_INPUT_CODE,
                             "retention_days must be an integer") from None
        if retention_days < 0:
            raise BrainError(BAD_INPUT_CODE, "retention_days must be >= 0")
    state = _load(cfg)
    _get_principal(state, _check_id(principal, "principal"))
    pkey = _profile_key(state["principals"][_check_id(principal)]["id"],
                        _check_id(identity or "default", "identity"))
    profile = state["profiles"].setdefault(pkey, {"fields": {}})
    if len(profile["fields"]) >= MAX_FIELDS_PER_PROFILE \
            and clean_key not in profile["fields"]:
        raise BrainError(BAD_INPUT_CODE, "profile field limit reached")
    profile["fields"][clean_key] = {
        "key": clean_key, "value": text, "purpose": (purpose or "")[:200],
        "source": "user" if trust == "user" else trust,
        "trust": trust, "retention_days": retention_days,
        "created_at": profile["fields"].get(clean_key, {}).get(
            "created_at", _now()),
        "updated_at": _now()}
    _save(cfg, state)
    _audit("profile.set", requested_by,
           {"principal": principal, "identity": identity, "key": clean_key})
    return {"state": "OK",
            "field": _public_field(profile["fields"][clean_key])}


def profile_show(cfg: Any, principal: str, *,
                 identity: str = "default") -> Dict[str, Any]:
    """Show one scoped profile (values masked in the listing)."""
    state = _load(cfg)
    _get_principal(state, _check_id(principal, "principal"))
    pkey = _profile_key(_check_id(principal), _check_id(identity or "default",
                                                        "identity"))
    profile = state["profiles"].get(pkey, {"fields": {}})
    return {"state": "OK", "principal": principal, "identity": identity,
            "fields": [_public_field(f) for _, f in
                       sorted(profile.get("fields", {}).items())]}


def profile_forget(cfg: Any, principal: str, *, identity: str = "",
                   key: str = "", requested_by: str = "cli:user",
                   confirmed: bool = False) -> Dict[str, Any]:
    """Delete a field, an identity, or a whole principal profile."""
    if not confirmed:
        raise BrainError(CONFIRM_CODE,
                         "profile forget requires explicit --confirm")
    state = _load(cfg)
    _get_principal(state, _check_id(principal, "principal"))
    pid = _check_id(principal)
    if key:
        pkey = _profile_key(pid, _check_id(identity or "default", "identity"))
        profile = state["profiles"].get(pkey, {"fields": {}})
        clean = _check_field_key(key)
        if clean not in profile.get("fields", {}):
            raise BrainError(NOT_FOUND_CODE, f"no field {key!r}")
        del profile["fields"][clean]
        _save(cfg, state)
        _audit("profile.forget_field", requested_by,
               {"principal": pid, "key": clean})
        return {"state": "OK", "forgotten": f"{pid}::{clean}"}
    doomed = [k for k in state["profiles"]
              if k == pid or k.startswith(pid + "::")]
    if identity:
        doomed = [k for k in doomed
                  if k == _profile_key(pid, _check_id(identity, "identity"))]
    for k in doomed:
        del state["profiles"][k]
    _save(cfg, state)
    _audit("profile.forget", requested_by,
           {"principal": pid, "identity": identity or "*"})
    return {"state": "OK", "forgotten_profiles": sorted(doomed)}


def profile_sweep(cfg: Any, *, requested_by: str = "cli:user",
                  confirmed: bool = False) -> Dict[str, Any]:
    """Delete fields past retention (explicit; audited per field)."""
    if not confirmed:
        raise BrainError(CONFIRM_CODE,
                         "profile sweep requires explicit --confirm")
    state = _load(cfg)
    now = _now()
    removed = []
    for pkey, profile in state["profiles"].items():
        for key, field in list(profile.get("fields", {}).items()):
            retention = field.get("retention_days")
            if isinstance(retention, int) and retention >= 0 and \
                    now - float(field.get("created_at", now)) > retention * 86400:
                del profile["fields"][key]
                removed.append(f"{pkey}::{key}")
    _save(cfg, state)
    _audit("profile.sweep", requested_by, {"removed": len(removed)})
    return {"state": "OK", "removed": sorted(removed)}


def profile_export(cfg: Any, principal: str, *,
                   identity: str = "") -> Dict[str, Any]:
    """Export scoped profile data (values included for the owner’s own
    portability; secrets never live here by construction)."""
    state = _load(cfg)
    _get_principal(state, _check_id(principal, "principal"))
    pid = _check_id(principal)
    out = {}
    for pkey, profile in state["profiles"].items():
        if not (pkey == pid or pkey.startswith(pid + "::")):
            continue
        ident = pkey.split("::", 1)[1] if "::" in pkey else "default"
        if identity and ident != _check_id(identity, "identity"):
            continue
        out[ident] = {k: {"value": f.get("value", ""),
                          "purpose": f.get("purpose", ""),
                          "trust": f.get("trust", "user"),
                          "retention_days": f.get("retention_days")}
                      for k, f in profile.get("fields", {}).items()}
    return {"state": "OK", "principal": pid, "profiles": out}


# ---------------------------------------------------------------- accounts
def _check_origin(origin: str) -> str:
    raw = (origin or "").strip()
    try:
        parts = urlparse(raw if "://" in raw else f"https://{raw}")
    except ValueError as exc:
        raise BrainError(BAD_INPUT_CODE, f"bad origin: {exc}") from exc
    host = (parts.hostname or "").lower()
    if not host or "." not in host:
        raise BrainError(BAD_INPUT_CODE, "origin must be a valid host")
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        raise BrainError(HOMOGRAPH_CODE,
                         "non-ASCII hosts refused (homograph risk); add an "
                         "ASCII origin after manual review") from None
    if "@" in raw or " " in raw:
        raise BrainError(BAD_INPUT_CODE, "origin must be a bare host")
    return host


def account_add(cfg: Any, alias: str, origin: str, principal: str, *,
                identity: str = "default", label: str = "",
                requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Bind an account alias to an approved origin + identity."""
    if not confirmed:
        raise BrainError(CONFIRM_CODE,
                         "account add requires explicit --confirm")
    clean_alias = _check_id(alias, "account")
    host = _check_origin(origin)
    state = _load(cfg)
    _get_principal(state, _check_id(principal, "principal"))
    if clean_alias in state["accounts"]:
        raise BrainError(EXISTS_CODE, f"account {clean_alias!r} exists")
    rec = {"alias": clean_alias, "origin": host,
           "principal": _check_id(principal),
           "identity": _check_id(identity or "default", "identity"),
           "label": (label or "").strip()[:80],
           "created_at": _now(), "updated_by": requested_by}
    state["accounts"][clean_alias] = rec
    _save(cfg, state)
    _audit("account.add", requested_by,
           {"account": clean_alias, "origin": host,
            "principal": rec["principal"]})
    return {"state": "OK", "account": rec}


def account_list(cfg: Any, *, principal: str = "") -> Dict[str, Any]:
    """List account bindings (same origin may hold many accounts)."""
    state = _load(cfg)
    recs = sorted(state["accounts"].values(), key=lambda r: r.get("alias", ""))
    if (principal or "").strip():
        recs = [r for r in recs
                if r.get("principal") == _check_id(principal, "principal")]
    return {"state": "OK", "accounts": recs}


def account_remove(cfg: Any, alias: str, *, requested_by: str = "cli:user",
                   confirmed: bool = False) -> Dict[str, Any]:
    """Remove an account binding (profile data is preserved)."""
    if not confirmed:
        raise BrainError(CONFIRM_CODE,
                         "account remove requires explicit --confirm")
    state = _load(cfg)
    clean = _check_id(alias, "account")
    if clean not in state["accounts"]:
        raise BrainError(NOT_FOUND_CODE, f"no account {alias!r}")
    del state["accounts"][clean]
    _save(cfg, state)
    _audit("account.remove", requested_by, {"account": clean})
    return {"state": "OK", "removed": clean}


# ---------------------------------------------------------------- vault
def vault_set(cfg: Any, name: str, ref: str, *, principal: str = "",
              requested_by: str = "cli:user",
              confirmed: bool = False) -> Dict[str, Any]:
    """Record a secret REFERENCE (raw values are refused, never stored)."""
    if not confirmed:
        raise BrainError(CONFIRM_CODE,
                         "vault set requires explicit --confirm")
    clean = _check_id(name, "vault entry")
    text = (ref or "").strip()
    if not (text.startswith("env:") or text.startswith("file:")
            or text.startswith("keyring:")):
        raise BrainError(SECRET_CLASS_CODE,
                         "vault takes references only (env:VAR, file:PATH, "
                         "keyring:NAME); raw values are never stored")
    if len(text) > 260 or " " in text:
        raise BrainError(BAD_INPUT_CODE, "malformed reference")
    state = _load(cfg)
    if principal:
        _get_principal(state, _check_id(principal, "principal"))
    vault = state["vault"]
    if clean in vault:
        raise BrainError(EXISTS_CODE, f"vault entry {clean!r} exists")
    kind = text.split(":", 1)[0]
    vault[clean] = {"name": clean,
                    "reference": f"{kind}:<reference>",
                    "principal": (principal or "").strip().lower(),
                    "created_at": _now(), "updated_by": requested_by}
    # The live reference itself (paths/names) stays out of outputs; only
    # the kind is shown. Store kind + opaque handle, never the raw ref.
    vault[clean]["_handle"] = text
    _save(cfg, state)
    _audit("vault.set", requested_by,
           {"entry": clean, "kind": kind,
            "principal": (principal or "").strip().lower()})
    public = dict(vault[clean])
    public.pop("_handle", None)
    return {"state": "OK", "entry": public}


def vault_list(cfg: Any, *, principal: str = "") -> Dict[str, Any]:
    """List vault references (kinds only, never handles)."""
    state = _load(cfg)
    recs = []
    for rec in sorted(state["vault"].values(), key=lambda r: r.get("name", "")):
        if (principal or "").strip() and \
                rec.get("principal") != _check_id(principal, "principal"):
            continue
        public = dict(rec)
        public.pop("_handle", None)
        recs.append(public)
    return {"state": "OK", "entries": recs}


def vault_remove(cfg: Any, name: str, *, requested_by: str = "cli:user",
                 confirmed: bool = False) -> Dict[str, Any]:
    """Delete a vault reference (the external secret is untouched)."""
    if not confirmed:
        raise BrainError(CONFIRM_CODE,
                         "vault remove requires explicit --confirm")
    state = _load(cfg)
    clean = _check_id(name, "vault entry")
    if clean not in state["vault"]:
        raise BrainError(NOT_FOUND_CODE, f"no vault entry {name!r}")
    del state["vault"][clean]
    _save(cfg, state)
    _audit("vault.remove", requested_by, {"entry": clean})
    return {"state": "OK", "removed": clean}


# ---------------------------------------------------------------- onboarding
def onboard_plan() -> Dict[str, Any]:
    """First-run onboarding plan (static; works offline; skippable)."""
    return {"state": "OK", "schema": "qubit-brain-onboard/1",
            "sections": [
                {"id": "personal", "required": False,
                 "fields": ["display_name", "email", "phone", "timezone",
                            "language"],
                 "why": "addressing you correctly and routing locale-aware defaults"},
                {"id": "organization", "required": False,
                 "fields": ["company", "role"],
                 "why": "separating company from personal data"},
                {"id": "accounts", "required": False,
                 "fields": ["origin", "account_alias", "identity"],
                 "why": "binding approved sites to the right identity"},
                {"id": "vault", "required": False,
                 "fields": ["entry", "reference"],
                 "why": "recording where secrets live (references only)"},
                {"id": "voice", "required": False,
                 "fields": ["voice_alias", "locale"],
                 "why": "spoken aliases and accessibility preferences"}],
            "rules": ["every section is skippable and completable later",
                      "nothing is saved without preview + explicit confirm",
                      "secret-class fields are refused (use vault references)",
                      "runs fully offline"]}


def onboard_apply(cfg: Any, answers: Dict[str, Dict[str, str]], *,
                  requested_by: str = "cli:user",
                  confirmed: bool = False,
                  dry_run: bool = False) -> Dict[str, Any]:
    """Apply onboarding answers (preview by default; offline)."""
    plan = onboard_plan()
    known = {s["id"] for s in plan["sections"]}
    staged = []
    for section, fields in (answers or {}).items():
        if section not in known:
            raise BrainError(BAD_INPUT_CODE,
                             f"unknown section {section!r}")
        if not isinstance(fields, dict):
            raise BrainError(BAD_INPUT_CODE,
                             f"section {section!r} must be a mapping")
        if section == "personal":
            staged.append(("personal", dict(fields)))
        elif section == "organization":
            staged.append(("organization", dict(fields)))
        elif section == "accounts":
            staged.append(("accounts", dict(fields)))
        elif section == "vault":
            staged.append(("vault", dict(fields)))
        elif section == "voice":
            staged.append(("voice", dict(fields)))
    if dry_run or not confirmed:
        return {"state": "preview" if not confirmed else "preview",
                "would_create": [{"section": s, "fields": sorted(f)}
                                 for s, f in staged],
                "note": "nothing saved; pass --confirm to persist"}
    applied: List[str] = []
    state = _load(cfg)
    try:
        for section, fields in staged:
            if section == "personal":
                pid = "owner"
                if pid not in state["principals"]:
                    state["principals"][pid] = {
                        "id": pid, "kind": "person", "label": "Owner",
                        "created_at": _now(), "updated_by": requested_by}
                pkey = _profile_key(pid, "default")
                profile = state["profiles"].setdefault(pkey, {"fields": {}})
                for key, value in fields.items():
                    clean = _check_field_key(key)
                    profile["fields"][clean] = {
                        "key": clean, "value": str(value)[:MAX_FIELD_VALUE],
                        "purpose": "onboarding", "source": "user",
                        "trust": "user", "retention_days": None,
                        "created_at": _now(), "updated_at": _now()}
                applied.append("personal")
            elif section == "organization":
                name = str(fields.get("company", "")).strip().lower().replace(
                    " ", "-") or "company"
                pid = _check_id(f"org-{name}", "principal")
                if pid not in state["principals"]:
                    state["principals"][pid] = {
                        "id": pid, "kind": "organization",
                        "label": str(fields.get("company", ""))[:80],
                        "created_at": _now(), "updated_by": requested_by}
                    applied.append("organization")
            elif section == "accounts":
                origin = _check_origin(str(fields.get("origin", "")))
                alias = _check_id(str(fields.get("account_alias",
                                                 f"acc-{origin.split('.')[0]}")),
                                  "account")
                if alias not in state["accounts"]:
                    state["accounts"][alias] = {
                        "alias": alias, "origin": origin,
                        "principal": "owner",
                        "identity": _check_id(str(fields.get("identity",
                                                             "default")),
                                              "identity"),
                        "label": "", "created_at": _now(),
                        "updated_by": requested_by}
                    applied.append("accounts")
            elif section == "vault":
                entry = _check_id(str(fields.get("entry", "main")),
                                  "vault entry")
                ref = str(fields.get("reference", "")).strip()
                if not (ref.startswith("env:") or ref.startswith("file:")
                        or ref.startswith("keyring:")):
                    raise BrainError(SECRET_CLASS_CODE,
                                     "vault takes references only")
                if entry not in state["vault"]:
                    state["vault"][entry] = {
                        "name": entry,
                        "reference": f"{ref.split(':', 1)[0]}:<reference>",
                        "_handle": ref, "principal": "owner",
                        "created_at": _now(), "updated_by": requested_by}
                    applied.append("vault")
            elif section == "voice":
                pkey = _profile_key("owner", "default")
                if "owner" not in state["principals"]:
                    state["principals"]["owner"] = {
                        "id": "owner", "kind": "person", "label": "Owner",
                        "created_at": _now(), "updated_by": requested_by}
                profile = state["profiles"].setdefault(pkey, {"fields": {}})
                for key in ("voice_alias", "locale"):
                    if fields.get(key):
                        clean = _check_field_key(key)
                        profile["fields"][clean] = {
                            "key": clean, "value": str(fields[key])[:200],
                            "purpose": "onboarding", "source": "user",
                            "trust": "user", "retention_days": None,
                            "created_at": _now(), "updated_at": _now()}
                applied.append("voice")
    except BrainError:
        raise
    _save(cfg, state)
    _audit("onboard.apply", requested_by, {"applied": applied})
    return {"state": "OK", "applied": applied}


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before Brain changes.",
            "owner": "qubit-policy",
            "rollback": "registry untouched; nothing to roll back"}
