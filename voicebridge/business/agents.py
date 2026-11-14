"""Per-agent control contract and durable service.

The Business OS owns the *agent* as a control-plane record, not a chat
session: identity, runtime choice, explicit permission scopes, budgets,
autonomy, approval requirements and lifecycle. This module is the one door a
panel or the office calls; it never starts a model turn and never touches the
session authority.

Design rules, matching the rest of ``voicebridge/business``:

* **Explicit, deny-by-default permissions.** Every permission category starts
  empty. A wildcard (``*``, ``all``, ``any`` …) is a typed refusal, not a
  convenience, and an unknown category/scope is refused by name.
* **Durability.** Agents, turns, usage and the operation log live in the same
  SQLite file as the rest of the Business OS (schema v2), with ``BEGIN
  IMMEDIATE`` writes, stable IDs and request fingerprints. The audit trail is
  the existing append-only ``bos_audit_event`` table.
* **One effect per idempotency key.** Replaying a create/update/lifecycle call
  returns the recorded result; reusing the key with a different request is a
  typed conflict.
* **Stop is honest.** Stopping a turn records ``interrupted``. It can never
  write ``completed``; a later completion must be a separate, explicit call on
  a turn that is still active.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import models
from .errors import (
    AGENT_APPROVAL_REQUIRED_CODE,
    AGENT_ARCHIVED_CODE,
    AGENT_AUTONOMY_DENIED_CODE,
    AGENT_BUDGET_EXCEEDED_CODE,
    AGENT_MODEL_NOT_READY_CODE,
    AGENT_NO_ACTIVE_TURN_CODE,
    AGENT_NOT_FOUND_CODE,
    AGENT_PAUSED_CODE,
    AGENT_PROFILE_INVALID_CODE,
    AGENT_PROFILE_SCOPE_CODE,
    AGENT_PROVIDER_NOT_READY_CODE,
    AGENT_RATE_LIMITED_CODE,
    AGENT_REVOKED_CODE,
    AGENT_TOKEN_LIMIT_CODE,
    AGENT_TOOL_DENIED_CODE,
    AGENT_TOOL_UNKNOWN_CODE,
    AGENT_TURN_LIMIT_CODE,
    AGENT_UNKNOWN_SCOPE_CODE,
    BAD_INPUT_CODE,
    BAD_STATE_CODE,
    IDEMPOTENCY_CONFLICT_CODE,
    BusinessError,
)
from .ids import fingerprint, new_id
from .store import BusinessStore
from .util import json_dumps, json_loads, now_iso, redact, require_text

AUTONOMY_LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5")
APPROVAL_LEVELS = frozenset({"L4", "L5"})
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high")
AGENT_STATUSES = ("active", "paused", "revoked", "archived")
AGENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "active": frozenset({"paused", "revoked", "archived"}),
    "paused": frozenset({"active", "revoked", "archived"}),
    "revoked": frozenset({"archived"}),
    "archived": frozenset(),
}
TURN_STATUSES = ("active", "interrupted", "completed", "failed")
APPROVAL_ACTIONS = ("spend", "legal", "publish", "contact", "irreversible")

PERMISSION_CATEGORIES = (
    "tools",
    "toolsets",
    "files",
    "browser",
    "network",
    "terminal",
    "email",
    "social",
    "etsy",
    "payments",
    "data",
)

_ACTION_SCOPES: dict[str, frozenset[str]] = {
    "browser": frozenset(
        {"read", "navigate", "click", "type", "download", "upload", "screenshot"}
    ),
    "network": frozenset({"read", "write", "http", "websocket"}),
    "terminal": frozenset({"read", "execute"}),
    "email": frozenset({"read", "draft", "send"}),
    "social": frozenset({"read", "draft", "publish", "reply"}),
    "etsy": frozenset({"read", "draft", "publish", "message", "refund"}),
    "payments": frozenset({"read", "charge", "refund", "payout"}),
    "data": frozenset({"read", "write", "export", "delete"}),
}
_WILDCARD_SCOPES = frozenset(
    {"*", "all", "any", "unrestricted", "everything", "full", "none"}
)
_SCOPE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,127}$")
_ROOT_PATHS = frozenset({"/", "~", "~/", "//", "\\"})

_SAFETY_FIELDS = (
    "autonomy",
    "daily_spend_minor",
    "monthly_spend_minor",
    "per_action_limit_minor",
    "max_turns_per_day",
    "max_tokens_per_turn",
    "rate_limit_per_minute",
    "approval_required",
    "escalation_target",
)
_UPDATE_FIELDS = frozenset(
    {
        "role_id",
        "department",
        "room",
        "desk",
        "display_name",
        "human_owner",
        "provider",
        "model",
        "reasoning_effort",
        "fallback_model",
        "fallback_provider",
        "temperature",
        "max_tokens",
        "schedule",
        "timezone",
        "max_concurrent_turns",
        "permissions",
        "safety",
        "metadata",
        "security_principal_id",
    }
)

#: Agent-profile selectors live in ``metadata_json``. They are *declarations*
#: (what an agent may use), never grants: authority still comes from
#: ``bos_grant`` only. Each list is bounded so a profile cannot become an
#: unbounded payload.
_PROFILE_LIMITS: dict[str, int] = {
    "skills": 64,
    "mcp_servers": 32,
    "memory_scopes": 16,
    "secret_refs": 64,
}
_MEMORY_SCOPES = ("agent", "session", "task", "department", "office", "tenant")
_BUDGET_PERIODS = ("daily", "weekly", "monthly", "quarterly", "yearly")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_REF_RE = re.compile(
    r"^(?:env|vault|secret|keychain|file):[A-Za-z0-9_.:/-]{1,120}$"
    r"|^[A-Z][A-Z0-9_]{1,120}$"
)
_APPEARANCE_FIELDS = (
    "character_profile_id",
    "portrait",
    "voice",
    "animation_preset",
    "skin",
    "theme",
)
#: Selector values are references/requirements, not credential-shaped values.
#: They are validated first and intentionally not run through the blanket key
#: redactor (which would mask the whole ``secret_refs`` list).
_SELECTOR_KEYS = frozenset(
    {
        "skills",
        "mcp_servers",
        "memory_scopes",
        "secret_refs",
        "budgets",
        "appearance",
        "model_policy",
        "schedule_ref",
        "policy_revision",
    }
)


# -- validation helpers --------------------------------------------------
def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _optional_text(value: Any, label: str, *, max_len: int = 4096) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _int(value: Any, label: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} must be an integer >= {minimum}"
        )
    return value


def _reasoning(value: Any) -> str:
    clean = _text(value, "reasoning effort", max_len=20).lower()
    if clean not in REASONING_EFFORTS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"unknown reasoning effort {clean!r}; expected one of "
            f"{', '.join(REASONING_EFFORTS)}",
        )
    return clean


def _temperature(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BusinessError(BAD_INPUT_CODE, "temperature must be a number or null")
    clean = float(value)
    if clean < 0.0 or clean > 2.0:
        raise BusinessError(BAD_INPUT_CODE, "temperature must be between 0 and 2")
    return clean


def _timezone(value: Any) -> str:
    clean = _text(value, "timezone", max_len=80)
    try:
        ZoneInfo(clean)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise BusinessError(
            BAD_INPUT_CODE, f"unknown timezone {clean!r}"
        ) from exc
    return clean


def _profile_error(detail: str) -> None:
    raise BusinessError(AGENT_PROFILE_INVALID_CODE, detail)


def _profile_list(value: Any, label: str, *, limit: int) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(
        value, (list, tuple, set, frozenset)
    ):
        _profile_error(f"{label} must be a list")
    items = list(value)
    if len(items) > limit:
        _profile_error(f"{label} may hold at most {limit} entries")
    return items


def _profile_token(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _profile_error(f"{label} must be a non-empty string")
    token = value.strip()
    if "*" in token or "?" in token or token.lower() in _WILDCARD_SCOPES:
        _profile_error(f"{label} {token!r} is a wildcard; name explicit values")
    if not _SCOPE_TOKEN_RE.match(token):
        _profile_error(f"{label} {token!r} is not a valid token")
    return token


def _skill_selectors(value: Any) -> list[dict[str, Any]]:
    selectors: list[dict[str, Any]] = []
    for raw in _profile_list(value, "skills", limit=_PROFILE_LIMITS["skills"]):
        if isinstance(raw, str):
            skill_id = _profile_token(raw, "skill id")
            entry: dict[str, Any] = {
                "skill_id": skill_id,
                "version": "",
                "requirements": [],
            }
        elif isinstance(raw, Mapping):
            if (
                raw.get("grants_capability") is True
                or raw.get("grants")
                or raw.get("capabilities")
            ):
                _profile_error(
                    "a skill may require capabilities but may never grant one"
                )
            skill_id = _profile_token(
                raw.get("skill_id") or raw.get("id") or raw.get("name"),
                "skill id",
            )
            version = _optional_text(
                raw.get("version") or None, "skill version", max_len=64
            )
            requirements = [
                _profile_token(item, "skill requirement")
                for item in _profile_list(
                    raw.get("requires_capabilities")
                    if "requires_capabilities" in raw
                    else raw.get("requirements"),
                    "skill requirements",
                    limit=32,
                )
            ]
            entry = {
                "skill_id": skill_id,
                "version": version or "",
                "requirements": sorted(dict.fromkeys(requirements)),
            }
        else:
            _profile_error("each skill selector must be a string or a mapping")
        selectors.append(entry)
    return selectors


def _mcp_selectors(value: Any) -> list[dict[str, Any]]:
    selectors: list[dict[str, Any]] = []
    for raw in _profile_list(
        value, "mcp_servers", limit=_PROFILE_LIMITS["mcp_servers"]
    ):
        if not isinstance(raw, Mapping):
            _profile_error("each MCP server selector must be a mapping")
        name = _profile_token(raw.get("name") or raw.get("server_id"), "MCP server")
        version = (
            _optional_text(raw.get("version") or None, "MCP version", max_len=64)
            or ""
        )
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            _profile_error(f"MCP server {name!r} enabled must be a boolean")
        schema_hash = raw.get("schema_hash")
        if schema_hash is not None:
            if not isinstance(schema_hash, str) or not _SHA256_RE.match(schema_hash):
                _profile_error(
                    f"MCP server {name!r} schema_hash must be a 64-character"
                    " lowercase SHA-256"
                )
        elif enabled:
            _profile_error(
                f"enabled MCP server {name!r} requires the complete tool-schema"
                " hash"
            )
        allowed = [
            _profile_token(item, "MCP allowed tool")
            for item in _profile_list(
                raw.get("allowed_tools"), "MCP allowed tools", limit=64
            )
        ]
        denied = [
            _profile_token(item, "MCP denied tool")
            for item in _profile_list(
                raw.get("denied_tools"), "MCP denied tools", limit=64
            )
        ]
        overlap = sorted(set(allowed) & set(denied))
        if overlap:
            _profile_error(
                f"MCP server {name!r} both allows and denies {overlap[0]!r}"
            )
        selectors.append(
            {
                "name": name,
                "version": version,
                "schema_hash": schema_hash,
                "enabled": enabled,
                "allowed_tools": sorted(dict.fromkeys(allowed)),
                "denied_tools": sorted(dict.fromkeys(denied)),
            }
        )
    return selectors


def _memory_selectors(value: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw in _profile_list(
        value, "memory_scopes", limit=_PROFILE_LIMITS["memory_scopes"]
    ):
        if isinstance(raw, str):
            scope = raw.strip()
            flags = {"read": True, "write": False, "append": False}
        elif isinstance(raw, Mapping):
            scope = raw.get("scope")
            if not isinstance(scope, str) or not scope.strip():
                _profile_error("each memory scope needs a scope name")
            scope = scope.strip()
            flags = {}
            for flag in ("read", "write", "append"):
                item = raw.get(flag, flag == "read")
                if not isinstance(item, bool):
                    _profile_error(
                        f"memory scope {scope!r} {flag} must be a boolean"
                    )
                flags[flag] = item
        else:
            _profile_error("each memory scope must be a string or a mapping")
        if scope not in _MEMORY_SCOPES:
            raise BusinessError(
                AGENT_UNKNOWN_SCOPE_CODE,
                f"unknown memory scope {scope!r}; expected one of"
                f" {', '.join(_MEMORY_SCOPES)}",
            )
        current = merged.get(scope)
        if current is None:
            merged[scope] = {"scope": scope, **flags}
        else:
            for flag in ("read", "write", "append"):
                current[flag] = current[flag] or flags[flag]
    return [merged[scope] for scope in _MEMORY_SCOPES if scope in merged]


def _secret_selectors(value: Any) -> list[str]:
    refs: list[str] = []
    for raw in _profile_list(
        value, "secret_refs", limit=_PROFILE_LIMITS["secret_refs"]
    ):
        if not isinstance(raw, str) or not raw.strip():
            _profile_error("each secret ref must be a non-empty string")
        ref = raw.strip()
        if len(ref) > 128 or not _SECRET_REF_RE.match(ref):
            _profile_error(
                "secret_refs are references only (env:NAME, vault:path or an"
                " env name); secret values are refused"
            )
        refs.append(ref)
    return sorted(dict.fromkeys(refs))


def _budget_selector(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        _profile_error("budgets must be a mapping")
    unknown = sorted(
        set(map(str, value))
        - {"currency", "period", "period_limit_minor", "per_turn_limit_minor"}
    )
    if unknown:
        _profile_error(f"unknown budget field {unknown[0]!r}")
    currency = value.get("currency", "USD")
    if not isinstance(currency, str) or len(currency.strip()) != 3:
        _profile_error("budget currency must be a 3-letter code")
    code = currency.strip().upper()
    if not code.isalpha():
        _profile_error("budget currency must be a 3-letter code")
    period = value.get("period", "monthly")
    if period not in _BUDGET_PERIODS:
        _profile_error(
            f"budget period must be one of {', '.join(_BUDGET_PERIODS)}"
        )
    limit = value.get("period_limit_minor", 0)
    per_turn = value.get("per_turn_limit_minor", 0)
    for label, item in (
        ("period_limit_minor", limit),
        ("per_turn_limit_minor", per_turn),
    ):
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            _profile_error(f"budget {label} must be a non-negative integer")
    if limit and per_turn > limit:
        _profile_error("budget per_turn_limit_minor cannot exceed period_limit_minor")
    return {
        "currency": code,
        "period": period,
        "period_limit_minor": limit,
        "per_turn_limit_minor": per_turn,
    }


def _appearance_selector(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        _profile_error("appearance must be a mapping of references")
    unknown = sorted(set(map(str, value)) - set(_APPEARANCE_FIELDS))
    if unknown:
        _profile_error(f"unknown appearance field {unknown[0]!r}")
    clean: dict[str, str] = {}
    for field in _APPEARANCE_FIELDS:
        if field in value:
            clean[field] = _optional_text(
                value[field] or None, f"appearance {field}", max_len=200
            ) or ""
    return clean


def _model_policy_selector(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        _profile_error("metadata.model_policy must be a mapping")
    allowed = {
        "reasoning_effort",
        "fallback_chain",
        "context_window",
        "latency_class",
        "data_residency",
    }
    unknown = sorted(set(map(str, value)) - allowed)
    if unknown:
        _profile_error(f"unknown model policy field {unknown[0]!r}")
    clean: dict[str, Any] = {}
    if "reasoning_effort" in value:
        clean["reasoning_effort"] = _reasoning(value["reasoning_effort"])
    if "context_window" in value:
        clean["context_window"] = _int(
            value["context_window"], "model policy context_window", minimum=256
        )
    for field in ("latency_class", "data_residency"):
        if field in value:
            clean[field] = _profile_token(value[field], f"model policy {field}")
    chain: list[dict[str, str]] = []
    for raw in _profile_list(
        value.get("fallback_chain"), "model policy fallback_chain", limit=8
    ):
        if not isinstance(raw, Mapping):
            _profile_error("each fallback route must be a mapping")
        provider = _profile_token(raw.get("provider"), "fallback provider")
        model = _profile_token(raw.get("model"), "fallback model")
        if not _configured(provider) or not _configured(model):
            _profile_error("each fallback route must pin provider and model")
        chain.append({"provider": provider, "model": model})
    if chain:
        clean["fallback_chain"] = chain
    return clean


def normalize_selectors(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalise the profile selectors in ``metadata``.

    Unknown keys are preserved (metadata stays metadata), but every selector
    the profile reads is validated here so a bad value can never reach an
    effective grant by accident.
    """
    clean = dict(metadata)
    if "skills" in clean:
        clean["skills"] = _skill_selectors(clean["skills"])
    if "mcp_servers" in clean:
        clean["mcp_servers"] = _mcp_selectors(clean["mcp_servers"])
    if "memory_scopes" in clean:
        clean["memory_scopes"] = _memory_selectors(clean["memory_scopes"])
    if "secret_refs" in clean:
        clean["secret_refs"] = _secret_selectors(clean["secret_refs"])
    if "budgets" in clean:
        clean["budgets"] = _budget_selector(clean["budgets"])
    if "appearance" in clean:
        clean["appearance"] = _appearance_selector(clean["appearance"])
    if "model_policy" in clean:
        clean["model_policy"] = _model_policy_selector(clean["model_policy"])
    if "schedule_ref" in clean:
        clean["schedule_ref"] = _profile_token(
            clean["schedule_ref"], "schedule ref"
        )
    if "policy_revision" in clean:
        clean["policy_revision"] = _int(
            clean["policy_revision"], "policy_revision", minimum=1
        )
    return clean


def _metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise BusinessError(BAD_INPUT_CODE, "metadata must be a mapping")
    clean = normalize_selectors(dict(value))
    return {
        key: (item if key in _SELECTOR_KEYS else redact(item))
        for key, item in clean.items()
    }


def _metadata_json(metadata: Mapping[str, Any]) -> str:
    return json_dumps(
        {
            key: (item if key in _SELECTOR_KEYS else redact(item))
            for key, item in metadata.items()
        }
    )


def _validate_model_route(
    provider: str,
    model: str,
    fallback_provider: str | None,
    fallback_model: str | None,
) -> None:
    """A fallback route is allowed only when both routes are pinned.

    An unpinned *primary* route is preserved (the existing preview path turns
    it into a typed ``AGENT_PROVIDER_NOT_READY``/``AGENT_MODEL_NOT_READY``), but
    a fallback may never be a silent hole where a provider is assumed.
    """
    for label, value in (
        ("provider", provider),
        ("model", model),
        ("fallback_provider", fallback_provider),
        ("fallback_model", fallback_model),
    ):
        if value and ("*" in value or "?" in value):
            _profile_error(f"{label} may not contain a wildcard")
    if (fallback_provider is None) != (fallback_model is None):
        _profile_error(
            "a fallback route must pin both provider and model; a half route"
            " would silently inherit another provider"
        )
    if fallback_provider is not None:
        if not _configured(provider) or not _configured(model):
            _profile_error(
                "a fallback route requires a pinned primary provider and model"
            )
        if not _configured(fallback_provider) or not _configured(fallback_model):
            _profile_error("the fallback route names an unconfigured provider/model")


def _require_security_principal(
    conn: sqlite3.Connection, tenant_id: str, principal_id: str | None
) -> str | None:
    """The security principal must exist in *this* tenant and be usable.

    Desk/room/department are never consulted: authorization is the principal.
    """
    if principal_id is None:
        return None
    row = conn.execute(
        "SELECT * FROM bos_principal WHERE tenant_id=? AND principal_id=?",
        (tenant_id, principal_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            AGENT_PROFILE_SCOPE_CODE,
            f"security principal {principal_id!r} does not exist in tenant"
            f" {tenant_id!r}",
        )
    if row["kind"] not in ("agent", "service"):
        raise BusinessError(
            AGENT_PROFILE_SCOPE_CODE,
            f"principal {principal_id!r} is a {row['kind']!r}; an agent needs"
            " an agent/service principal",
        )
    if row["status"] != "active":
        raise BusinessError(
            AGENT_PROFILE_SCOPE_CODE,
            f"security principal {principal_id!r} is {row['status']!r}",
        )
    return principal_id


def _scope_values(category: str, value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(
        value, (list, tuple, set, frozenset)
    ):
        raise BusinessError(
            AGENT_UNKNOWN_SCOPE_CODE,
            f"{category} scopes must be an explicit list of strings",
        )
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise BusinessError(
                AGENT_UNKNOWN_SCOPE_CODE,
                f"{category} scope entries must be non-empty strings",
            )
        token = item.strip()
        if "*" in token or token.lower() in _WILDCARD_SCOPES:
            raise BusinessError(
                AGENT_UNKNOWN_SCOPE_CODE,
                f"{category} scope {token!r} is a wildcard; name explicit scopes",
            )
        if category == "files":
            token = _file_scope(token)
        elif category in _ACTION_SCOPES:
            allowed = _ACTION_SCOPES[category]
            if token not in allowed:
                raise BusinessError(
                    AGENT_UNKNOWN_SCOPE_CODE,
                    f"unknown {category} scope {token!r}; expected one of "
                    f"{', '.join(sorted(allowed))}",
                )
        elif not _SCOPE_TOKEN_RE.match(token):
            raise BusinessError(
                AGENT_UNKNOWN_SCOPE_CODE,
                f"invalid {category} scope {token!r}",
            )
        cleaned.append(token)
    return tuple(sorted(dict.fromkeys(cleaned)))


def _file_scope(token: str) -> str:
    normalised = token.replace("\\", "/")
    if normalised in _ROOT_PATHS:
        raise BusinessError(
            AGENT_UNKNOWN_SCOPE_CODE,
            f"file scope {token!r} is unrestricted; name an explicit path",
        )
    if not (normalised.startswith("/") or normalised.startswith("~/")):
        raise BusinessError(
            AGENT_UNKNOWN_SCOPE_CODE,
            f"file scope {token!r} must be absolute or start with '~/'",
        )
    if any(part == ".." for part in normalised.split("/")):
        raise BusinessError(
            AGENT_UNKNOWN_SCOPE_CODE,
            f"file scope {token!r} may not traverse '..'",
        )
    if "*" in token:
        raise BusinessError(
            AGENT_UNKNOWN_SCOPE_CODE,
            f"file scope {token!r} is a wildcard; name explicit paths",
        )
    return normalised


@dataclass(frozen=True, slots=True)
class Permissions:
    """Deny-by-default permission scopes. Missing category means no access."""

    tools: tuple[str, ...] = ()
    toolsets: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    browser: tuple[str, ...] = ()
    network: tuple[str, ...] = ()
    terminal: tuple[str, ...] = ()
    email: tuple[str, ...] = ()
    social: tuple[str, ...] = ()
    etsy: tuple[str, ...] = ()
    payments: tuple[str, ...] = ()
    data: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls, value: Any, *, base: "Permissions | None" = None
    ) -> "Permissions":
        if value is None:
            return base or cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise BusinessError(
                BAD_INPUT_CODE, "permissions must be a mapping of scopes"
            )
        unknown = sorted(set(map(str, value)) - set(PERMISSION_CATEGORIES))
        if unknown:
            raise BusinessError(
                AGENT_UNKNOWN_SCOPE_CODE,
                f"unknown permission category {unknown[0]!r}; expected one of "
                f"{', '.join(PERMISSION_CATEGORIES)}",
            )
        current = base or cls()
        merged = {
            category: getattr(current, category)
            for category in PERMISSION_CATEGORIES
        }
        for category in PERMISSION_CATEGORIES:
            if category in value:
                merged[category] = _scope_values(category, value[category])
        return cls(**merged)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            category: list(getattr(self, category))
            for category in PERMISSION_CATEGORIES
        }

    def is_empty(self) -> bool:
        return not any(getattr(self, category) for category in PERMISSION_CATEGORIES)


@dataclass(frozen=True, slots=True)
class Safety:
    """Budgets, autonomy and human gates. Zero spend means no spend."""

    autonomy: str = "L0"
    daily_spend_minor: int = 0
    monthly_spend_minor: int = 0
    per_action_limit_minor: int = 0
    max_turns_per_day: int = 100
    max_tokens_per_turn: int = 8192
    rate_limit_per_minute: int = 30
    approval_required: tuple[str, ...] = (
        "spend",
        "legal",
        "publish",
        "contact",
        "irreversible",
    )
    escalation_target: str = "human-owner"

    @classmethod
    def from_mapping(cls, value: Any, *, base: "Safety | None" = None) -> "Safety":
        if value is None:
            return base or cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise BusinessError(BAD_INPUT_CODE, "safety must be a mapping")
        unknown = sorted(set(map(str, value)) - set(_SAFETY_FIELDS))
        if unknown:
            raise BusinessError(
                BAD_INPUT_CODE, f"unknown safety field {unknown[0]!r}"
            )
        current = base or cls()
        merged: dict[str, Any] = {
            field: getattr(current, field) for field in _SAFETY_FIELDS
        }
        if "autonomy" in value:
            autonomy = _text(value["autonomy"], "autonomy", max_len=8).upper()
            if autonomy not in AUTONOMY_LEVELS:
                raise BusinessError(
                    BAD_INPUT_CODE,
                    f"unknown autonomy {autonomy!r}; expected one of "
                    f"{', '.join(AUTONOMY_LEVELS)}",
                )
            merged["autonomy"] = autonomy
        for field in (
            "daily_spend_minor",
            "monthly_spend_minor",
            "per_action_limit_minor",
        ):
            if field in value:
                merged[field] = _int(value[field], field, minimum=0)
        if "max_turns_per_day" in value:
            merged["max_turns_per_day"] = _int(
                value["max_turns_per_day"], "max_turns_per_day", minimum=0
            )
        if "max_tokens_per_turn" in value:
            merged["max_tokens_per_turn"] = _int(
                value["max_tokens_per_turn"], "max_tokens_per_turn", minimum=1
            )
        if "rate_limit_per_minute" in value:
            merged["rate_limit_per_minute"] = _int(
                value["rate_limit_per_minute"], "rate_limit_per_minute", minimum=0
            )
        if "approval_required" in value:
            merged["approval_required"] = _approval_actions(
                value["approval_required"]
            )
        if "escalation_target" in value:
            merged["escalation_target"] = _text(
                value["escalation_target"], "escalation_target", max_len=200
            )
        return cls(**merged)

    def to_dict(self) -> dict[str, Any]:
        return {
            field: list(getattr(self, field))
            if field == "approval_required"
            else getattr(self, field)
            for field in _SAFETY_FIELDS
        }


def _approval_actions(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(
        value, (list, tuple, set, frozenset)
    ):
        raise BusinessError(
            BAD_INPUT_CODE, "approval_required must be a list of action names"
        )
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise BusinessError(
                BAD_INPUT_CODE, "approval_required entries must be strings"
            )
        token = item.strip().lower()
        if "*" in token or token in _WILDCARD_SCOPES:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"approval action {token!r} is a wildcard; name explicit actions",
            )
        if token not in APPROVAL_ACTIONS:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown approval action {token!r}; expected one of "
                f"{', '.join(APPROVAL_ACTIONS)}",
            )
        cleaned.append(token)
    return tuple(sorted(dict.fromkeys(cleaned)))


_UNREADY_VALUES = frozenset({"", "unset", "none", "null", "unknown", "-"})
_TOOL_APPROVAL_HINTS: dict[str, frozenset[str]] = {
    "spend": frozenset(
        {"charge", "payment", "pay", "invoice", "purchase", "buy", "price"}
    ),
    "publish": frozenset({"publish", "posting", "listing", "upload"}),
    "contact": frozenset(
        {"send", "email", "message", "contact", "reply", "dm", "outreach"}
    ),
    "legal": frozenset(
        {"legal", "contract", "terms", "agreement", "compliance", "policy"}
    ),
    "irreversible": frozenset(
        {"delete", "destroy", "remove", "refund", "deploy", "transfer", "purge"}
    ),
}


def _configured(value: Any) -> bool:
    """A provider/model is ready only if it names something real.

    This is a configuration check, never a network or model probe: a preview
    must not contact a provider to decide whether a prospective turn is
    admissible.
    """
    return isinstance(value, str) and value.strip().lower() not in _UNREADY_VALUES


def _requested_tools(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(
        value, (list, tuple, set, frozenset)
    ):
        raise BusinessError(
            BAD_INPUT_CODE,
            "requested_tools must be a list of explicit tool names",
        )
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise BusinessError(
                AGENT_TOOL_UNKNOWN_CODE,
                "requested tool entries must be non-empty strings",
            )
        token = item.strip()
        if "*" in token or token.lower() in _WILDCARD_SCOPES:
            raise BusinessError(
                AGENT_TOOL_UNKNOWN_CODE,
                f"requested tool {token!r} is a wildcard; name explicit tools",
            )
        if not _SCOPE_TOKEN_RE.match(token):
            raise BusinessError(
                AGENT_TOOL_UNKNOWN_CODE,
                f"requested tool {token!r} is not a valid tool name",
            )
        cleaned.append(token)
    return tuple(sorted(dict.fromkeys(cleaned)))


def _tool_approval_hints(tool: str) -> set[str]:
    flat = re.sub(r"[^a-z0-9]+", "_", tool.lower()).strip("_")
    parts = set(flat.split("_")) if flat else set()
    actions: set[str] = set()
    for action, hints in _TOOL_APPROVAL_HINTS.items():
        if parts & hints:
            actions.add(action)
    # A bare ``post`` tool publishes; ``draft_post`` does not. Drafting tools
    # stay draft-only unless they name an explicit acting verb above.
    if flat in {"post", "posts"}:
        actions.add("publish")
    return actions


# -- records ------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Agent:
    agent_id: str
    tenant_id: str
    business_id: str
    security_principal_id: str | None
    role_id: str
    department: str
    display_name: str
    human_owner: str
    room: str | None
    desk: str | None
    status: str
    provider: str
    model: str
    reasoning_effort: str
    fallback_model: str | None
    fallback_provider: str | None
    temperature: float | None
    max_tokens: int
    schedule: str
    timezone: str
    max_concurrent_turns: int
    permissions: Permissions
    safety: Safety
    metadata: dict[str, Any]
    taken_over_by: str | None
    taken_over_at: str | None
    revision: int
    created_at: str
    updated_at: str
    archived_at: str | None

    @classmethod
    def from_row(cls, row: Any) -> "Agent":
        data = dict(row)
        permissions = Permissions.from_mapping(
            json_loads(data.get("permissions_json"), {})
        )
        safety = Safety.from_mapping(json_loads(data.get("safety_json"), {}))
        return cls(
            agent_id=data["agent_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            security_principal_id=data.get("security_principal_id"),
            role_id=data["role_id"],
            department=data["department"],
            display_name=data["display_name"],
            human_owner=data["human_owner"],
            room=data["room"],
            desk=data["desk"],
            status=data["status"],
            provider=data["provider"],
            model=data["model"],
            reasoning_effort=data["reasoning_effort"],
            fallback_model=data["fallback_model"],
            fallback_provider=data["fallback_provider"],
            temperature=data["temperature"],
            max_tokens=int(data["max_tokens"]),
            schedule=data["schedule"],
            timezone=data["timezone"],
            max_concurrent_turns=int(data["max_concurrent_turns"]),
            permissions=permissions,
            safety=safety,
            metadata=json_loads(data.get("metadata_json"), {}),
            taken_over_by=data["taken_over_by"],
            taken_over_at=data["taken_over_at"],
            revision=int(data["revision"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            archived_at=data["archived_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "security_principal_id": self.security_principal_id,
            "role_id": self.role_id,
            "department": self.department,
            "display_name": self.display_name,
            "human_owner": self.human_owner,
            "room": self.room,
            "desk": self.desk,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "fallback_model": self.fallback_model,
            "fallback_provider": self.fallback_provider,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "schedule": self.schedule,
            "timezone": self.timezone,
            "max_concurrent_turns": self.max_concurrent_turns,
            "permissions": self.permissions.to_dict(),
            "safety": self.safety.to_dict(),
            "metadata": self.metadata,
            "taken_over_by": self.taken_over_by,
            "taken_over_at": self.taken_over_at,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Agent":
        return cls(
            agent_id=str(data["agent_id"]),
            tenant_id=str(data["tenant_id"]),
            business_id=str(data["business_id"]),
            security_principal_id=(
                str(data["security_principal_id"])
                if data.get("security_principal_id")
                else None
            ),
            role_id=str(data["role_id"]),
            department=str(data["department"]),
            display_name=str(data["display_name"]),
            human_owner=str(data["human_owner"]),
            room=data.get("room"),
            desk=data.get("desk"),
            status=str(data["status"]),
            provider=str(data["provider"]),
            model=str(data["model"]),
            reasoning_effort=str(data["reasoning_effort"]),
            fallback_model=data.get("fallback_model"),
            fallback_provider=data.get("fallback_provider"),
            temperature=data.get("temperature"),
            max_tokens=int(data["max_tokens"]),
            schedule=str(data["schedule"]),
            timezone=str(data["timezone"]),
            max_concurrent_turns=int(data["max_concurrent_turns"]),
            permissions=Permissions.from_mapping(data.get("permissions") or {}),
            safety=Safety.from_mapping(data.get("safety") or {}),
            metadata=dict(data.get("metadata") or {}),
            taken_over_by=data.get("taken_over_by"),
            taken_over_at=data.get("taken_over_at"),
            revision=int(data["revision"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            archived_at=data.get("archived_at"),
        )


def _resolve_profile_principal(
    conn: sqlite3.Connection, tenant_id: str, agent: Agent
) -> sqlite3.Row | None:
    """The agent's security principal, never a desk/room.

    ``security_principal_id`` is explicit when set. Read-old rows fall back to
    a principal whose ``external_ref`` is the agent id, which keeps existing
    installs usable without inventing authority.
    """
    if agent.security_principal_id:
        return conn.execute(
            "SELECT * FROM bos_principal WHERE tenant_id=? AND principal_id=?",
            (tenant_id, agent.security_principal_id),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM bos_principal WHERE tenant_id=? AND external_ref=?"
        " AND kind IN ('agent','service') ORDER BY created_at, principal_id"
        " LIMIT 1",
        (tenant_id, agent.agent_id),
    ).fetchone()


def _grant_entry(row: Any, *, source: str) -> dict[str, Any]:
    return {
        "grant_id": row["grant_id"],
        "role_id": row["role_id"],
        "capability": row["capability"],
        "resource": row["resource"],
        "action": row["action"],
        "data_class": row["data_class"],
        "environment": row["environment"],
        "effect": row["effect"],
        "approval_required": bool(row["approval_required"]),
        "expires_at": (
            float(row["expires_at"]) if row["expires_at"] is not None else None
        ),
        "source": source,
    }


def _profile_grants(
    conn: sqlite3.Connection,
    tenant_id: str,
    principal_id: str | None,
    *,
    now: float,
) -> list[dict[str, Any]]:
    """Expand explicit and role-inherited grants for one tenant subject.

    A grant is included only when it belongs to this tenant *and* this subject;
    a row for another tenant or principal can never become effective here.
    """
    if principal_id is None:
        return []
    rows = conn.execute(
        "SELECT * FROM bos_grant WHERE tenant_id=? AND subject_id=?"
        " ORDER BY created_at, grant_id",
        (tenant_id, principal_id),
    ).fetchall()
    entries: list[dict[str, Any]] = []
    for row in rows:
        if row["revoked_at"] is not None:
            continue
        if row["expires_at"] is not None and float(row["expires_at"]) <= now:
            continue
        if row["role_id"] is None:
            entries.append(_grant_entry(row, source="explicit"))
            continue
        role = conn.execute(
            "SELECT * FROM bos_role WHERE tenant_id=? AND role_id=?",
            (tenant_id, row["role_id"]),
        ).fetchone()
        expanded = False
        if role is not None:
            for item in json_loads(role["permissions_json"], []) or []:
                if not isinstance(item, Mapping):
                    continue
                candidate = {
                    "grant_id": row["grant_id"],
                    "role_id": row["role_id"],
                    "capability": item.get("capability"),
                    "resource": item.get("resource", row["resource"]),
                    "action": item.get("action", row["action"]),
                    "data_class": item.get("data_class", row["data_class"]),
                    "environment": item.get(
                        "environment", row["environment"]
                    ),
                    "effect": item.get("effect", "allow"),
                    "approval_required": bool(row["approval_required"]),
                    "expires_at": (
                        float(row["expires_at"])
                        if row["expires_at"] is not None
                        else None
                    ),
                }
                entries.append({**candidate, "source": "inherited"})
                expanded = True
        if not expanded:
            # A role grant with no readable role definition still carries its
            # own tuple. Deny-by-default is unchanged: no role row means the
            # row is the only declared candidate.
            entries.append(_grant_entry(row, source="inherited"))
    return entries


def _grant_key(entry: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        entry.get("capability"),
        entry.get("resource"),
        entry.get("action"),
        entry.get("data_class"),
        entry.get("environment"),
    )


@dataclass(frozen=True, slots=True)
class AgentTurn:
    turn_id: str
    tenant_id: str
    agent_id: str
    status: str
    started_at: str
    ended_at: str | None
    note: str

    @classmethod
    def from_row(cls, row: Any) -> "AgentTurn":
        data = dict(row)
        return cls(
            turn_id=data["turn_id"],
            tenant_id=data["tenant_id"],
            agent_id=data["agent_id"],
            status=data["status"],
            started_at=data["started_at"],
            ended_at=data["ended_at"],
            note=data["note"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentTurn":
        return cls(
            turn_id=str(data["turn_id"]),
            tenant_id=str(data["tenant_id"]),
            agent_id=str(data["agent_id"]),
            status=str(data["status"]),
            started_at=str(data["started_at"]),
            ended_at=data.get("ended_at"),
            note=str(data.get("note") or ""),
        )


@dataclass(frozen=True, slots=True)
class AgentUsage:
    usage_id: str
    tenant_id: str
    agent_id: str
    turn_id: str | None
    kind: str
    cost_minor: int
    tokens: int
    day: str
    month: str
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "AgentUsage":
        data = dict(row)
        return cls(
            usage_id=data["usage_id"],
            tenant_id=data["tenant_id"],
            agent_id=data["agent_id"],
            turn_id=data["turn_id"],
            kind=data["kind"],
            cost_minor=int(data["cost_minor"]),
            tokens=int(data["tokens"]),
            day=data["day"],
            month=data["month"],
            created_at=data["created_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "usage_id": self.usage_id,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "turn_id": self.turn_id,
            "kind": self.kind,
            "cost_minor": self.cost_minor,
            "tokens": self.tokens,
            "day": self.day,
            "month": self.month,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentUsage":
        return cls(
            usage_id=str(data["usage_id"]),
            tenant_id=str(data["tenant_id"]),
            agent_id=str(data["agent_id"]),
            turn_id=data.get("turn_id"),
            kind=str(data["kind"]),
            cost_minor=int(data["cost_minor"]),
            tokens=int(data["tokens"]),
            day=str(data["day"]),
            month=str(data["month"]),
            created_at=str(data["created_at"]),
        )


# -- service ------------------------------------------------------------
class AgentControlService:
    """Create, configure, gate and audit agents in the Business OS store."""

    def __init__(self, store: BusinessStore) -> None:
        self.store = store

    # -- reads ---------------------------------------------------------
    def get(self, tenant_id: str, agent_id: str) -> Agent:
        with self.store.read() as conn:
            return Agent.from_row(self._require_agent(conn, tenant_id, agent_id))

    def list(
        self,
        tenant_id: str,
        *,
        business_id: str | None = None,
        status: str | None = None,
        department: str | None = None,
        role_id: str | None = None,
    ) -> list[Agent]:
        if status is not None and status not in AGENT_STATUSES:
            raise BusinessError(BAD_INPUT_CODE, f"unknown agent status {status!r}")
        query = "SELECT * FROM bos_agent WHERE tenant_id=?"
        params: list[Any] = [tenant_id]
        if business_id is not None:
            query += " AND business_id=?"
            params.append(business_id)
        if status is not None:
            query += " AND status=?"
            params.append(status)
        if department is not None:
            query += " AND department=?"
            params.append(department)
        if role_id is not None:
            query += " AND role_id=?"
            params.append(role_id)
        query += " ORDER BY created_at, agent_id"
        with self.store.read() as conn:
            self.store._require_tenant(conn, tenant_id)
            rows = conn.execute(query, params).fetchall()
        return [Agent.from_row(row) for row in rows]

    def audit(
        self, tenant_id: str, agent_id: str, *, limit: int = 500
    ) -> list[models.AuditEvent]:
        self.get(tenant_id, agent_id)
        return self.store.list_audit_for_resource(
            tenant_id, "agent", agent_id, limit=limit
        )

    def effective_profile(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        catalog: Any = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Project the agent's explicit, inherited and effective selectors.

        The agent row supplies presentation and runtime declarations; identity
        grants supply authority. A skill or desk never becomes a grant. The
        returned profile keeps the frozen ``agent.schema.json`` core fields and
        adds the richer selectors P2-01 requires (grant diff, memory
        read/write/append, MCP tool allowlists).
        """
        moment = time.time() if now is None else float(now)
        with self.store.read() as conn:
            agent = Agent.from_row(self._require_agent(conn, tenant_id, agent_id))
            principal = _resolve_profile_principal(conn, tenant_id, agent)
            principal_id = principal["principal_id"] if principal is not None else None
            entries = _profile_grants(
                conn, tenant_id, principal_id, now=moment
            )

        explicit = [
            item
            for item in entries
            if item["source"] == "explicit" and item["effect"] == "allow"
        ]
        inherited = [
            item
            for item in entries
            if item["source"] == "inherited" and item["effect"] == "allow"
        ]
        denials = [item for item in entries if item["effect"] == "deny"]
        denied_keys = {_grant_key(item) for item in denials}
        effective: list[dict[str, Any]] = []
        for item in entries:
            if item["effect"] != "allow":
                continue
            if _grant_key(item) in denied_keys:
                continue
            if catalog is not None:
                capability = catalog.get(item["capability"])
                item = {
                    **item,
                    "risk": capability.risk,
                    "approval_policy": capability.approval_policy,
                    "capability_version": capability.version,
                    "capability_health": capability.health,
                }
            effective.append(item)

        metadata = normalize_selectors(agent.metadata)
        skills = metadata.get("skills", [])
        mcp_servers = metadata.get("mcp_servers", [])
        memory_access = metadata.get("memory_scopes", [])
        secret_refs = metadata.get("secret_refs", [])
        budgets = metadata.get("budgets")
        if budgets is None:
            budgets = {
                "currency": "USD",
                "period": "daily",
                "period_limit_minor": agent.safety.daily_spend_minor,
                "per_turn_limit_minor": agent.safety.per_action_limit_minor,
            }
        policy_revision = metadata.get("policy_revision", agent.revision)
        model_meta = metadata.get("model_policy", {})
        fallback_chain = list(model_meta.get("fallback_chain", []))
        if not fallback_chain and (
            agent.fallback_provider and agent.fallback_model
        ):
            fallback_chain = [
                {
                    "provider": agent.fallback_provider,
                    "model": agent.fallback_model,
                }
            ]
        pinned = _configured(agent.provider) and _configured(agent.model)
        return {
            "agent_id": agent.agent_id,
            "owner_id": agent.human_owner,
            "tenant_id": agent.tenant_id,
            "business_ids": [agent.business_id],
            "department_id": agent.department,
            "room_id": agent.room,
            "desk_id": agent.desk,
            "role_id": agent.role_id,
            "security_principal_id": principal_id,
            "principal_status": (
                principal["status"] if principal is not None else None
            ),
            "display_name": agent.display_name,
            "status": agent.status,
            "policy_revision": policy_revision,
            "autonomy_ceiling": agent.safety.autonomy,
            "model_policy": {
                "provider": agent.provider,
                "model": agent.model,
                "reasoning_effort": model_meta.get(
                    "reasoning_effort", agent.reasoning_effort
                ),
                "fallback_provider": agent.fallback_provider,
                "fallback_model": agent.fallback_model,
                "fallback_chain": fallback_chain,
                "temperature": agent.temperature,
                "max_tokens": agent.max_tokens,
                "pinned": pinned,
            },
            "capability_grants": sorted(
                {item["grant_id"] for item in effective}
            ),
            "grants": {
                "explicit": explicit,
                "inherited": inherited,
                "effective": effective,
                "denials": denials,
            },
            "skills": [item["skill_id"] for item in skills],
            "skill_requirements": skills,
            "mcp_servers": mcp_servers,
            "memory_scopes": [item["scope"] for item in memory_access],
            "memory_scope_access": memory_access,
            "secret_refs": secret_refs,
            "budgets": budgets,
            "safety": agent.safety.to_dict(),
            "schedule": {
                "cron": agent.schedule,
                "timezone": agent.timezone,
                "ref": metadata.get("schedule_ref"),
            },
            "appearance": metadata.get("appearance", {}),
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
        }

    # -- create / update ----------------------------------------------
    def create(
        self,
        tenant_id: str,
        business_id: str,
        *,
        role_id: str,
        department: str,
        display_name: str,
        human_owner: str,
        provider: str,
        model: str,
        idempotency_key: str,
        actor: str = "system",
        security_principal_id: str | None = None,
        room: str | None = None,
        desk: str | None = None,
        reasoning_effort: str = "medium",
        fallback_model: str | None = None,
        fallback_provider: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 8192,
        schedule: str = "manual",
        timezone: str = "UTC",
        max_concurrent_turns: int = 1,
        permissions: Permissions | Mapping[str, Any] | None = None,
        safety: Safety | Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[Agent, bool]:
        fields = {
            "role_id": _text(role_id, "role_id", max_len=120),
            "department": _text(department, "department", max_len=120),
            "display_name": _text(display_name, "display_name", max_len=200),
            "human_owner": _text(human_owner, "human_owner", max_len=200),
            "security_principal_id": _optional_text(
                security_principal_id, "security_principal_id", max_len=80
            ),
            "room": _optional_text(room, "room", max_len=120),
            "desk": _optional_text(desk, "desk", max_len=120),
            "provider": _text(provider, "provider", max_len=120),
            "model": _text(model, "model", max_len=200),
            "reasoning_effort": _reasoning(reasoning_effort),
            "fallback_model": _optional_text(
                fallback_model, "fallback_model", max_len=200
            ),
            "fallback_provider": _optional_text(
                fallback_provider, "fallback_provider", max_len=120
            ),
            "temperature": _temperature(temperature),
            "max_tokens": _int(max_tokens, "max_tokens", minimum=1),
            "schedule": _text(schedule, "schedule", max_len=200),
            "timezone": _timezone(timezone),
            "max_concurrent_turns": _int(
                max_concurrent_turns, "max_concurrent_turns", minimum=1
            ),
            "permissions": Permissions.from_mapping(permissions),
            "safety": Safety.from_mapping(safety),
            "metadata": _metadata(metadata),
        }
        _validate_model_route(
            fields["provider"],
            fields["model"],
            fields["fallback_provider"],
            fields["fallback_model"],
        )
        key = _text(idempotency_key, "idempotency key", max_len=300)
        request_hash = fingerprint(
            {
                "action": "agent.create",
                "tenant_id": tenant_id,
                "business_id": business_id,
                "fields": self._fields_payload(fields),
            }
        )
        agent_id = new_id("agt")
        created_at = now_iso()
        with self.store.transaction() as conn:
            self.store._require_business(conn, tenant_id, business_id)
            _require_security_principal(
                conn, tenant_id, fields["security_principal_id"]
            )
            existing = conn.execute(
                "SELECT * FROM bos_agent WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["request_hash"] != request_hash
                    or existing["business_id"] != business_id
                ):
                    raise BusinessError(
                        IDEMPOTENCY_CONFLICT_CODE,
                        f"idempotency key {key!r} was already used for a"
                        " different agent request",
                    )
                return Agent.from_row(existing), False
            try:
                conn.execute(
                    "INSERT INTO bos_agent(agent_id, tenant_id, business_id,"
                    " security_principal_id, role_id, department, display_name,"
                    " human_owner, room, desk,"
                    " status, provider, model, reasoning_effort, fallback_model,"
                    " fallback_provider, temperature, max_tokens, schedule,"
                    " timezone, max_concurrent_turns, permissions_json,"
                    " safety_json, metadata_json, idempotency_key, request_hash,"
                    " created_at, updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,'active',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        agent_id,
                        tenant_id,
                        business_id,
                        fields["security_principal_id"],
                        fields["role_id"],
                        fields["department"],
                        fields["display_name"],
                        fields["human_owner"],
                        fields["room"],
                        fields["desk"],
                        fields["provider"],
                        fields["model"],
                        fields["reasoning_effort"],
                        fields["fallback_model"],
                        fields["fallback_provider"],
                        fields["temperature"],
                        fields["max_tokens"],
                        fields["schedule"],
                        fields["timezone"],
                        fields["max_concurrent_turns"],
                        json_dumps(fields["permissions"].to_dict()),
                        json_dumps(fields["safety"].to_dict()),
                        _metadata_json(fields["metadata"]),
                        key,
                        request_hash,
                        created_at,
                        created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:  # concurrent identical create
                raise BusinessError(
                    IDEMPOTENCY_CONFLICT_CODE,
                    f"idempotency key {key!r} is already in use",
                ) from exc
            self.store._audit(
                conn,
                tenant_id,
                business_id,
                actor,
                "agent.create",
                "agent",
                agent_id,
                "ok",
                {
                    "role_id": fields["role_id"],
                    "department": fields["department"],
                    "display_name": fields["display_name"],
                    "autonomy": fields["safety"].autonomy,
                    "permissions": fields["permissions"].to_dict(),
                },
            )
            row = self._require_agent(conn, tenant_id, agent_id)
        return Agent.from_row(row), True

    def update(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        changes: Mapping[str, Any],
        idempotency_key: str,
        actor: str = "system",
    ) -> Agent:
        key = _text(idempotency_key, "idempotency key", max_len=300)
        with self.store.transaction() as conn:
            row = self._require_agent(conn, tenant_id, agent_id)
            current = Agent.from_row(row)
            fields = self._propose_update(current, changes)
            request_hash = fingerprint(
                {
                    "action": "agent.update",
                    "agent_id": agent_id,
                    "fields": self._fields_payload(fields),
                }
            )
            replay = self._replay_operation(conn, tenant_id, key, request_hash)
            if replay is not None:
                return Agent.from_dict(replay["agent"])
            _require_security_principal(
                conn, tenant_id, fields["security_principal_id"]
            )
            updated_at = now_iso()
            conn.execute(
                "UPDATE bos_agent SET security_principal_id=?, role_id=?,"
                " department=?, display_name=?,"
                " human_owner=?, room=?, desk=?, provider=?, model=?,"
                " reasoning_effort=?, fallback_model=?, fallback_provider=?,"
                " temperature=?, max_tokens=?, schedule=?, timezone=?,"
                " max_concurrent_turns=?, permissions_json=?, safety_json=?,"
                " metadata_json=?, revision=revision+1, updated_at=?"
                " WHERE tenant_id=? AND agent_id=?",
                (
                    fields["security_principal_id"],
                    fields["role_id"],
                    fields["department"],
                    fields["display_name"],
                    fields["human_owner"],
                    fields["room"],
                    fields["desk"],
                    fields["provider"],
                    fields["model"],
                    fields["reasoning_effort"],
                    fields["fallback_model"],
                    fields["fallback_provider"],
                    fields["temperature"],
                    fields["max_tokens"],
                    fields["schedule"],
                    fields["timezone"],
                    fields["max_concurrent_turns"],
                    json_dumps(fields["permissions"].to_dict()),
                    json_dumps(fields["safety"].to_dict()),
                    _metadata_json(fields["metadata"]),
                    updated_at,
                    tenant_id,
                    agent_id,
                ),
            )
            self.store._audit(
                conn,
                tenant_id,
                current.business_id,
                actor,
                "agent.update",
                "agent",
                agent_id,
                "ok",
                {"fields": sorted(map(str, changes))},
            )
            updated = Agent.from_row(self._require_agent(conn, tenant_id, agent_id))
            result = {"agent": updated.to_dict()}
            self._record_operation(
                conn, tenant_id, agent_id, "update", key, request_hash, result
            )
        return updated

    # -- lifecycle -----------------------------------------------------
    def pause(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        idempotency_key: str,
        actor: str = "system",
        note: str = "",
    ) -> Agent:
        return self._lifecycle(
            tenant_id,
            agent_id,
            target="paused",
            allowed_from=frozenset({"active"}),
            action="agent.pause",
            idempotency_key=idempotency_key,
            actor=actor,
            note=note,
        )

    def resume(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        idempotency_key: str,
        actor: str = "system",
        note: str = "",
    ) -> Agent:
        return self._lifecycle(
            tenant_id,
            agent_id,
            target="active",
            allowed_from=frozenset({"paused"}),
            action="agent.resume",
            idempotency_key=idempotency_key,
            actor=actor,
            note=note,
            clear_takeover=True,
        )

    def takeover(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        idempotency_key: str,
        actor: str,
        note: str = "",
    ) -> Agent:
        return self._lifecycle(
            tenant_id,
            agent_id,
            target="paused",
            allowed_from=frozenset({"active", "paused"}),
            action="agent.takeover",
            idempotency_key=idempotency_key,
            actor=actor,
            note=note,
            taken_over_by=actor,
        )

    def revoke(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        idempotency_key: str,
        actor: str = "system",
        scopes: Iterable[str] | None = None,
        note: str = "",
    ) -> Agent:
        """Revoke all or named permission categories.

        Revoking every category moves the agent to ``revoked``. Revoking a
        subset clears just those scopes and leaves the status unchanged.
        """
        key = _text(idempotency_key, "idempotency key", max_len=300)
        with self.store.transaction() as conn:
            row = self._require_agent(conn, tenant_id, agent_id)
            current = Agent.from_row(row)
            if current.status == "archived":
                raise BusinessError(
                    BAD_STATE_CODE, "an archived agent cannot be revoked"
                )
            removed = _revoked_categories(scopes)
            request_hash = fingerprint(
                {
                    "action": "agent.revoke",
                    "agent_id": agent_id,
                    "scopes": removed,
                    "note": note,
                }
            )
            replay = self._replay_operation(conn, tenant_id, key, request_hash)
            if replay is not None:
                return Agent.from_dict(replay["agent"])
            permissions = current.permissions.to_dict()
            for category in removed:
                permissions[category] = []
            all_revoked = not any(permissions.values())
            new_status = "revoked" if all_revoked else current.status
            if (
                new_status != current.status
                and new_status not in AGENT_TRANSITIONS[current.status]
            ):
                raise BusinessError(
                    BAD_STATE_CODE,
                    f"agent cannot move {current.status!r} -> {new_status!r}",
                )
            conn.execute(
                "UPDATE bos_agent SET permissions_json=?, status=?,"
                " revision=revision+1, updated_at=?"
                " WHERE tenant_id=? AND agent_id=?",
                (
                    json_dumps(permissions),
                    new_status,
                    now_iso(),
                    tenant_id,
                    agent_id,
                ),
            )
            self.store._audit(
                conn,
                tenant_id,
                current.business_id,
                actor,
                "agent.revoke",
                "agent",
                agent_id,
                "ok",
                {"scopes": removed, "status": new_status, "note": note},
            )
            updated = Agent.from_row(self._require_agent(conn, tenant_id, agent_id))
            result = {"agent": updated.to_dict()}
            self._record_operation(
                conn, tenant_id, agent_id, "revoke", key, request_hash, result
            )
        return updated

    def archive(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        idempotency_key: str,
        actor: str = "system",
        note: str = "",
    ) -> Agent:
        return self._lifecycle(
            tenant_id,
            agent_id,
            target="archived",
            allowed_from=frozenset({"active", "paused", "revoked"}),
            action="agent.archive",
            idempotency_key=idempotency_key,
            actor=actor,
            note=note,
        )

    def stop(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        idempotency_key: str,
        actor: str = "system",
        turn_id: str | None = None,
        note: str = "",
    ) -> AgentTurn:
        """Interrupt the current turn. This never records ``completed``."""
        key = _text(idempotency_key, "idempotency key", max_len=300)
        marker = (turn_id or "").strip()
        request_hash = fingerprint(
            {
                "action": "agent.stop",
                "agent_id": agent_id,
                "turn_id": marker or "auto",
                "note": note,
            }
        )
        with self.store.transaction() as conn:
            agent_row = self._require_agent(conn, tenant_id, agent_id)
            agent = Agent.from_row(agent_row)
            replay = self._replay_operation(conn, tenant_id, key, request_hash)
            if replay is not None:
                return AgentTurn.from_dict(replay["turn"])
            turn = self._resolve_active_turn(conn, tenant_id, agent_id, marker)
            ended_at = now_iso()
            conn.execute(
                "UPDATE bos_agent_turn SET status='interrupted', ended_at=?, note=?"
                " WHERE tenant_id=? AND turn_id=? AND status='active'",
                (ended_at, note, tenant_id, turn.turn_id),
            )
            self.store._audit(
                conn,
                tenant_id,
                agent.business_id,
                actor,
                "agent.turn.stop",
                "agent",
                agent_id,
                "ok",
                {
                    "turn_id": turn.turn_id,
                    "state": "interrupted",
                    "note": note,
                },
            )
            stopped = self._require_turn(conn, tenant_id, turn.turn_id)
            result = {
                "agent": agent.to_dict(),
                "turn": stopped.to_dict(),
            }
            self._record_operation(
                conn, tenant_id, agent_id, "stop", key, request_hash, result
            )
        return stopped

    # -- turns ---------------------------------------------------------
    def begin_turn(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        idempotency_key: str,
        turn_id: str | None = None,
        actor: str = "system",
    ) -> AgentTurn:
        key = _text(idempotency_key, "idempotency key", max_len=300)
        marker = (turn_id or "").strip() or "auto"
        request_hash = fingerprint(
            {
                "action": "agent.turn.begin",
                "agent_id": agent_id,
                "turn_id": marker,
            }
        )
        with self.store.transaction() as conn:
            agent = Agent.from_row(self._require_agent(conn, tenant_id, agent_id))
            replay = self._replay_operation(conn, tenant_id, key, request_hash)
            if replay is not None:
                return AgentTurn.from_dict(replay["turn"])
            if agent.status != "active":
                raise BusinessError(
                    BAD_STATE_CODE,
                    f"agent {agent_id!r} is {agent.status!r}; resume it before"
                    " starting a turn",
                )
            self._check_budgets(
                conn,
                agent,
                cost_minor=0,
                tokens=0,
                action=None,
                kind="turn",
                human_approved=False,
            )
            active = int(
                conn.execute(
                    "SELECT COUNT(*) FROM bos_agent_turn WHERE tenant_id=?"
                    " AND agent_id=? AND status='active'",
                    (tenant_id, agent_id),
                ).fetchone()[0]
            )
            if active >= agent.max_concurrent_turns:
                raise BusinessError(
                    AGENT_TURN_LIMIT_CODE,
                    f"agent already has {active} active turn(s); the limit is"
                    f" {agent.max_concurrent_turns}",
                )
            new_turn_id = turn_id.strip() if turn_id else new_id("trn")
            started_at = now_iso()
            conn.execute(
                "INSERT INTO bos_agent_turn(turn_id, tenant_id, agent_id, status,"
                " started_at, ended_at, note) VALUES(?,?,?,'active',?,NULL,'')",
                (new_turn_id, tenant_id, agent_id, started_at),
            )
            self.store._audit(
                conn,
                tenant_id,
                agent.business_id,
                actor,
                "agent.turn.begin",
                "agent",
                agent_id,
                "ok",
                {"turn_id": new_turn_id},
            )
            turn = self._require_turn(conn, tenant_id, new_turn_id)
            result = {"agent": agent.to_dict(), "turn": turn.to_dict()}
            self._record_operation(
                conn, tenant_id, agent_id, "begin_turn", key, request_hash, result
            )
        return turn

    def finish_turn(
        self,
        tenant_id: str,
        agent_id: str,
        turn_id: str,
        *,
        state: str,
        idempotency_key: str,
        actor: str = "system",
        note: str = "",
    ) -> AgentTurn:
        """Complete or fail a still-active turn.

        ``interrupted`` is deliberately refused here; it is only reachable
        through :meth:`stop`, so a control-panel stop can never masquerade as a
        normal completion.
        """
        target = _text(state, "turn state", max_len=20).lower()
        if target not in ("completed", "failed"):
            raise BusinessError(
                BAD_INPUT_CODE,
                "finish_turn state must be 'completed' or 'failed';"
                " use stop() to interrupt a turn",
            )
        key = _text(idempotency_key, "idempotency key", max_len=300)
        turn_clean = _text(turn_id, "turn_id", max_len=80)
        request_hash = fingerprint(
            {
                "action": "agent.turn.finish",
                "agent_id": agent_id,
                "turn_id": turn_clean,
                "state": target,
                "note": note,
            }
        )
        with self.store.transaction() as conn:
            agent = Agent.from_row(self._require_agent(conn, tenant_id, agent_id))
            replay = self._replay_operation(conn, tenant_id, key, request_hash)
            if replay is not None:
                return AgentTurn.from_dict(replay["turn"])
            turn = self._require_turn(conn, tenant_id, turn_clean)
            if turn.agent_id != agent_id:
                raise BusinessError(
                    AGENT_NOT_FOUND_CODE,
                    f"no turn {turn_clean!r} for agent {agent_id!r}",
                )
            if turn.status != "active":
                raise BusinessError(
                    BAD_STATE_CODE,
                    f"turn {turn_clean!r} is {turn.status!r}, not active",
                )
            conn.execute(
                "UPDATE bos_agent_turn SET status=?, ended_at=?, note=?"
                " WHERE tenant_id=? AND turn_id=?",
                (target, now_iso(), note, tenant_id, turn_clean),
            )
            self.store._audit(
                conn,
                tenant_id,
                agent.business_id,
                actor,
                "agent.turn.finish",
                "agent",
                agent_id,
                "ok",
                {"turn_id": turn_clean, "state": target, "note": note},
            )
            finished = self._require_turn(conn, tenant_id, turn_clean)
            result = {"agent": agent.to_dict(), "turn": finished.to_dict()}
            self._record_operation(
                conn, tenant_id, agent_id, "finish_turn", key, request_hash, result
            )
        return finished

    # -- budgets and usage --------------------------------------------
    def authorize_turn(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        cost_minor: int = 0,
        tokens: int = 0,
        action: str | None = None,
        human_approved: bool = False,
    ) -> dict[str, Any]:
        """Read-only admission check for one prospective turn/action."""
        cost = _int(cost_minor, "cost_minor", minimum=0)
        token_count = _int(tokens, "tokens", minimum=0)
        action_clean = _text(action, "action", max_len=80) if action else None
        with self.store.read() as conn:
            agent = Agent.from_row(self._require_agent(conn, tenant_id, agent_id))
            if agent.status != "active":
                raise BusinessError(
                    BAD_STATE_CODE,
                    f"agent {agent_id!r} is {agent.status!r}; resume it first",
                )
            remaining = self._check_budgets(
                conn,
                agent,
                cost_minor=cost,
                tokens=token_count,
                action=action_clean,
                kind="turn",
                human_approved=human_approved,
            )
        return {
            "allowed": True,
            "agent_id": agent_id,
            "autonomy": agent.safety.autonomy,
            "action": action_clean,
            "approval_required": bool(
                action_clean
                and (
                    action_clean in agent.safety.approval_required
                    or agent.safety.autonomy in APPROVAL_LEVELS
                )
            ),
            "remaining": remaining,
        }

    def preview_turn(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        prompt: Any,
        requested_tools: Any = None,
        spend_minor: int = 0,
        human_approved: bool = False,
    ) -> dict[str, Any]:
        """Plan one prospective turn without doing any of it.

        This is a read-only admission check over the same durable rows every
        real turn uses. It **never** starts a turn, records usage or an audit
        event, calls a model/provider, spends money, creates a job, or runs a
        tool. Refusals are the control plane's own typed codes so a panel can
        render the exact reason instead of guessing.
        """
        prompt_clean = _text(prompt, "prompt", max_len=20000)
        spend = _int(spend_minor, "spend_minor", minimum=0)
        tools = _requested_tools(requested_tools)
        approved = bool(human_approved)

        with self.store.read() as conn:
            agent = Agent.from_row(self._require_agent(conn, tenant_id, agent_id))
            self._require_previewable(agent)
            self._require_runtime_ready(agent)

            safety = agent.safety
            if (tools or spend) and safety.autonomy in ("L0", "L1"):
                raise BusinessError(
                    AGENT_AUTONOMY_DENIED_CODE,
                    f"autonomy {safety.autonomy} cannot act with tools or spend;"
                    " raise it to L2 or above",
                )

            granted = set(agent.permissions.tools)
            for tool in tools:
                if tool not in granted:
                    raise BusinessError(
                        AGENT_TOOL_DENIED_CODE,
                        f"tool {tool!r} is not granted; permissions are"
                        " deny-by-default, so name it in the agent's tools scopes",
                    )

            estimated_prompt_tokens = max(1, (len(prompt_clean) + 3) // 4)
            if agent.max_tokens > safety.max_tokens_per_turn:
                raise BusinessError(
                    AGENT_TOKEN_LIMIT_CODE,
                    f"the agent's max_tokens ({agent.max_tokens}) exceeds the"
                    f" per-turn limit of {safety.max_tokens_per_turn}",
                )
            if estimated_prompt_tokens > safety.max_tokens_per_turn:
                raise BusinessError(
                    AGENT_TOKEN_LIMIT_CODE,
                    f"the prompt is ~{estimated_prompt_tokens} tokens and exceeds"
                    f" the per-turn limit of {safety.max_tokens_per_turn}",
                )

            active = int(
                conn.execute(
                    "SELECT COUNT(*) FROM bos_agent_turn WHERE tenant_id=?"
                    " AND agent_id=? AND status='active'",
                    (tenant_id, agent_id),
                ).fetchone()[0]
            )
            if active >= agent.max_concurrent_turns:
                raise BusinessError(
                    AGENT_TURN_LIMIT_CODE,
                    f"agent already has {active} active turn(s); the limit is"
                    f" {agent.max_concurrent_turns}",
                )

            required = self._preview_approvals(agent, tools, spend)
            if required and not approved:
                raise BusinessError(
                    AGENT_APPROVAL_REQUIRED_CODE,
                    "this turn needs a human approval for: "
                    + ", ".join(required)
                    + "; preview it with human_approved once the owner signs off",
                )

            remaining = self._check_budgets(
                conn,
                agent,
                cost_minor=spend,
                tokens=agent.max_tokens,
                action=None,
                kind="turn",
                human_approved=approved,
            )

        permissions = agent.permissions.to_dict()
        return {
            "allowed": True,
            "state": "ALLOWED",
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "business_id": agent.business_id,
            "provider": agent.provider,
            "model": agent.model,
            "reasoning_effort": agent.reasoning_effort,
            "autonomy": agent.safety.autonomy,
            "max_tokens": agent.max_tokens,
            "estimated_prompt_tokens": estimated_prompt_tokens,
            "projected_tokens": estimated_prompt_tokens + agent.max_tokens,
            "requested_tools": list(tools),
            "tool_scopes": permissions,
            "approval_actions_required": required,
            "approvals_satisfied": not required or approved,
            "limits": agent.safety.to_dict(),
            "estimated": {
                "spend_minor": spend,
                "prompt_tokens": estimated_prompt_tokens,
                "output_tokens": agent.max_tokens,
                "active_turns": active,
                "remaining": remaining,
            },
            "side_effects": False,
            "reason": (
                "All control-plane checks passed. This is a preview only: no"
                " model call, spend, job, turn or tool side effect was made."
            ),
        }

    def _require_previewable(self, agent: Agent) -> None:
        status = agent.status
        if status == "paused":
            raise BusinessError(
                AGENT_PAUSED_CODE,
                f"agent {agent.agent_id!r} is paused; resume it before previewing"
                " a turn",
            )
        if status == "revoked":
            raise BusinessError(
                AGENT_REVOKED_CODE,
                f"agent {agent.agent_id!r} is revoked; restore explicit scopes"
                " before previewing a turn",
            )
        if status == "archived":
            raise BusinessError(
                AGENT_ARCHIVED_CODE,
                f"agent {agent.agent_id!r} is archived and cannot run or preview",
            )
        if status != "active":
            raise BusinessError(
                BAD_STATE_CODE,
                f"agent {agent.agent_id!r} is {status!r}, not active",
            )

    def _require_runtime_ready(self, agent: Agent) -> None:
        if not _configured(agent.provider):
            raise BusinessError(
                AGENT_PROVIDER_NOT_READY_CODE,
                f"agent {agent.agent_id!r} has no configured provider",
            )
        if not _configured(agent.model):
            raise BusinessError(
                AGENT_MODEL_NOT_READY_CODE,
                f"agent {agent.agent_id!r} has no configured model",
            )

    def _preview_approvals(
        self, agent: Agent, tools: tuple[str, ...], spend_minor: int
    ) -> list[str]:
        declared = set(agent.safety.approval_required)
        actions: set[str] = set()
        if spend_minor > 0:
            actions.add("spend")
        for tool in tools:
            actions |= _tool_approval_hints(tool)
        if (tools or spend_minor > 0) and agent.safety.autonomy in APPROVAL_LEVELS:
            actions |= declared
        return sorted(actions & declared)

    def record_usage(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        idempotency_key: str,
        cost_minor: int = 0,
        tokens: int = 0,
        kind: str = "turn",
        turn_id: str | None = None,
        action: str | None = None,
        human_approved: bool = False,
        actor: str = "system",
    ) -> AgentUsage:
        """Record metered usage, refusing an overflow before it is stored."""
        key = _text(idempotency_key, "idempotency key", max_len=300)
        cost = _int(cost_minor, "cost_minor", minimum=0)
        token_count = _int(tokens, "tokens", minimum=0)
        kind_clean = _text(kind, "usage kind", max_len=40).lower()
        turn_clean = (
            _text(turn_id, "turn_id", max_len=80) if turn_id is not None else None
        )
        action_clean = _text(action, "action", max_len=80) if action else None
        request_hash = fingerprint(
            {
                "operation": "agent.usage",
                "agent_id": agent_id,
                "kind": kind_clean,
                "turn_id": turn_clean,
                "cost_minor": cost,
                "tokens": token_count,
                "action": action_clean,
            }
        )
        with self.store.transaction() as conn:
            agent = Agent.from_row(self._require_agent(conn, tenant_id, agent_id))
            if turn_clean is not None:
                turn = self._require_turn(conn, tenant_id, turn_clean)
                if turn.agent_id != agent_id:
                    raise BusinessError(
                        AGENT_NOT_FOUND_CODE,
                        f"no turn {turn_clean!r} for agent {agent_id!r}",
                    )
            existing = conn.execute(
                "SELECT * FROM bos_agent_usage WHERE tenant_id=?"
                " AND idempotency_key=?",
                (tenant_id, key),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise BusinessError(
                        IDEMPOTENCY_CONFLICT_CODE,
                        f"idempotency key {key!r} was already used for a"
                        " different usage record",
                    )
                return AgentUsage.from_row(existing)
            self._check_budgets(
                conn,
                agent,
                cost_minor=cost,
                tokens=token_count,
                action=action_clean,
                kind=kind_clean,
                human_approved=human_approved,
            )
            stamp = now_iso()
            usage_id = new_id("usg")
            conn.execute(
                "INSERT INTO bos_agent_usage(usage_id, tenant_id, agent_id,"
                " turn_id, kind, cost_minor, tokens, day, month,"
                " idempotency_key, request_hash, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    usage_id,
                    tenant_id,
                    agent_id,
                    turn_clean,
                    kind_clean,
                    cost,
                    token_count,
                    stamp[:10],
                    stamp[:7],
                    key,
                    request_hash,
                    stamp,
                ),
            )
            self.store._audit(
                conn,
                tenant_id,
                agent.business_id,
                actor,
                "agent.usage",
                "agent",
                agent_id,
                "ok",
                {
                    "usage_id": usage_id,
                    "kind": kind_clean,
                    "turn_id": turn_clean,
                    "cost_minor": cost,
                },
            )
            row = conn.execute(
                "SELECT * FROM bos_agent_usage WHERE tenant_id=? AND usage_id=?",
                (tenant_id, usage_id),
            ).fetchone()
        return AgentUsage.from_row(row)

    # -- internals -----------------------------------------------------
    def _require_agent(
        self, conn: sqlite3.Connection, tenant_id: str, agent_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM bos_agent WHERE tenant_id=? AND agent_id=?",
            (tenant_id, agent_id),
        ).fetchone()
        if row is None:
            raise BusinessError(
                AGENT_NOT_FOUND_CODE,
                f"no agent {agent_id!r} in tenant {tenant_id!r}",
            )
        return row

    def _require_turn(
        self, conn: sqlite3.Connection, tenant_id: str, turn_id: str
    ) -> AgentTurn:
        row = conn.execute(
            "SELECT * FROM bos_agent_turn WHERE tenant_id=? AND turn_id=?",
            (tenant_id, turn_id),
        ).fetchone()
        if row is None:
            raise BusinessError(
                AGENT_NOT_FOUND_CODE,
                f"no turn {turn_id!r} in tenant {tenant_id!r}",
            )
        return AgentTurn.from_row(row)

    def _resolve_active_turn(
        self,
        conn: sqlite3.Connection,
        tenant_id: str,
        agent_id: str,
        turn_id: str,
    ) -> AgentTurn:
        if turn_id:
            turn = self._require_turn(conn, tenant_id, turn_id)
            if turn.agent_id != agent_id:
                raise BusinessError(
                    AGENT_NOT_FOUND_CODE,
                    f"no turn {turn_id!r} for agent {agent_id!r}",
                )
            if turn.status != "active":
                raise BusinessError(
                    BAD_STATE_CODE,
                    f"turn {turn_id!r} is {turn.status!r}, not active",
                )
            return turn
        rows = conn.execute(
            "SELECT * FROM bos_agent_turn WHERE tenant_id=? AND agent_id=?"
            " AND status='active' ORDER BY started_at, turn_id",
            (tenant_id, agent_id),
        ).fetchall()
        if not rows:
            raise BusinessError(
                AGENT_NO_ACTIVE_TURN_CODE,
                f"agent {agent_id!r} has no active turn to stop",
            )
        if len(rows) > 1:
            raise BusinessError(
                BAD_INPUT_CODE,
                "agent has multiple active turns; name the turn_id to stop",
            )
        return AgentTurn.from_row(rows[0])

    def _lifecycle(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        target: str,
        allowed_from: frozenset[str],
        action: str,
        idempotency_key: str,
        actor: str,
        note: str,
        clear_takeover: bool = False,
        taken_over_by: str | None = None,
    ) -> Agent:
        key = _text(idempotency_key, "idempotency key", max_len=300)
        request_hash = fingerprint(
            {
                "action": action,
                "agent_id": agent_id,
                "target": target,
                "note": note,
                "taken_over_by": taken_over_by,
            }
        )
        with self.store.transaction() as conn:
            row = self._require_agent(conn, tenant_id, agent_id)
            current = Agent.from_row(row)
            replay = self._replay_operation(conn, tenant_id, key, request_hash)
            if replay is not None:
                return Agent.from_dict(replay["agent"])
            if current.status not in allowed_from:
                raise BusinessError(
                    BAD_STATE_CODE,
                    f"agent cannot move {current.status!r} -> {target!r}",
                )
            stamp = now_iso()
            assignments = [
                "status=?",
                "revision=revision+1",
                "updated_at=?",
            ]
            params: list[Any] = [target, stamp]
            if clear_takeover:
                assignments.extend(["taken_over_by=NULL", "taken_over_at=NULL"])
            if taken_over_by is not None:
                assignments.extend(["taken_over_by=?", "taken_over_at=?"])
                params.extend([taken_over_by, stamp])
            if target == "archived":
                assignments.append("archived_at=?")
                params.append(stamp)
            params.extend([tenant_id, agent_id])
            conn.execute(
                f"UPDATE bos_agent SET {', '.join(assignments)}"
                " WHERE tenant_id=? AND agent_id=?",
                params,
            )
            self.store._audit(
                conn,
                tenant_id,
                current.business_id,
                actor,
                action,
                "agent",
                agent_id,
                "ok",
                {
                    "from": current.status,
                    "to": target,
                    "note": note,
                    "taken_over_by": taken_over_by,
                },
            )
            updated = Agent.from_row(self._require_agent(conn, tenant_id, agent_id))
            result = {"agent": updated.to_dict()}
            self._record_operation(
                conn,
                tenant_id,
                agent_id,
                action.removeprefix("agent."),
                key,
                request_hash,
                result,
            )
        return updated

    def _propose_update(
        self, current: Agent, changes: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(changes, Mapping):
            raise BusinessError(BAD_INPUT_CODE, "changes must be a mapping")
        unknown = sorted(set(map(str, changes)) - _UPDATE_FIELDS)
        if unknown:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown agent field {unknown[0]!r}; status and lifecycle"
                " changes go through the named operations",
            )
        fields: dict[str, Any] = {
            "role_id": current.role_id,
            "department": current.department,
            "display_name": current.display_name,
            "human_owner": current.human_owner,
            "room": current.room,
            "desk": current.desk,
            "provider": current.provider,
            "model": current.model,
            "reasoning_effort": current.reasoning_effort,
            "fallback_model": current.fallback_model,
            "fallback_provider": current.fallback_provider,
            "temperature": current.temperature,
            "max_tokens": current.max_tokens,
            "schedule": current.schedule,
            "timezone": current.timezone,
            "max_concurrent_turns": current.max_concurrent_turns,
            "permissions": current.permissions,
            "safety": current.safety,
            "metadata": current.metadata,
            "security_principal_id": current.security_principal_id,
        }
        for field in (
            "role_id",
            "department",
            "display_name",
            "human_owner",
            "provider",
            "model",
            "schedule",
        ):
            if field in changes:
                fields[field] = _text(changes[field], field, max_len=200)
        for field in ("room", "desk", "fallback_model", "fallback_provider"):
            if field in changes:
                fields[field] = _optional_text(changes[field], field, max_len=200)
        if "reasoning_effort" in changes:
            fields["reasoning_effort"] = _reasoning(changes["reasoning_effort"])
        if "temperature" in changes:
            fields["temperature"] = _temperature(changes["temperature"])
        if "max_tokens" in changes:
            fields["max_tokens"] = _int(
                changes["max_tokens"], "max_tokens", minimum=1
            )
        if "timezone" in changes:
            fields["timezone"] = _timezone(changes["timezone"])
        if "max_concurrent_turns" in changes:
            fields["max_concurrent_turns"] = _int(
                changes["max_concurrent_turns"],
                "max_concurrent_turns",
                minimum=1,
            )
        if "permissions" in changes:
            fields["permissions"] = Permissions.from_mapping(
                changes["permissions"], base=current.permissions
            )
        if "safety" in changes:
            fields["safety"] = Safety.from_mapping(
                changes["safety"], base=current.safety
            )
        if "metadata" in changes:
            fields["metadata"] = _metadata(changes["metadata"])
        if "security_principal_id" in changes:
            fields["security_principal_id"] = _optional_text(
                changes["security_principal_id"],
                "security_principal_id",
                max_len=80,
            )
        _validate_model_route(
            fields["provider"],
            fields["model"],
            fields["fallback_provider"],
            fields["fallback_model"],
        )
        return fields

    def _fields_payload(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "role_id": fields["role_id"],
            "department": fields["department"],
            "display_name": fields["display_name"],
            "human_owner": fields["human_owner"],
            "security_principal_id": fields["security_principal_id"],
            "room": fields["room"],
            "desk": fields["desk"],
            "provider": fields["provider"],
            "model": fields["model"],
            "reasoning_effort": fields["reasoning_effort"],
            "fallback_model": fields["fallback_model"],
            "fallback_provider": fields["fallback_provider"],
            "temperature": fields["temperature"],
            "max_tokens": fields["max_tokens"],
            "schedule": fields["schedule"],
            "timezone": fields["timezone"],
            "max_concurrent_turns": fields["max_concurrent_turns"],
            "permissions": fields["permissions"].to_dict(),
            "safety": fields["safety"].to_dict(),
            "metadata": _metadata_json(fields["metadata"]),
        }

    def _replay_operation(
        self,
        conn: sqlite3.Connection,
        tenant_id: str,
        key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM bos_agent_operation WHERE tenant_id=?"
            " AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise BusinessError(
                IDEMPOTENCY_CONFLICT_CODE,
                f"idempotency key {key!r} was already used for a different"
                " agent operation",
            )
        return json_loads(row["result_json"], {})

    def _record_operation(
        self,
        conn: sqlite3.Connection,
        tenant_id: str,
        agent_id: str,
        action: str,
        key: str,
        request_hash: str,
        result: Mapping[str, Any],
    ) -> None:
        try:
            conn.execute(
                "INSERT INTO bos_agent_operation(operation_id, tenant_id,"
                " agent_id, action, idempotency_key, request_hash, result_json,"
                " created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    new_id("opr"),
                    tenant_id,
                    agent_id,
                    action,
                    key,
                    request_hash,
                    json_dumps(_redact_strings(dict(result))),
                    now_iso(),
                ),
            )
        except sqlite3.IntegrityError as exc:  # concurrent identical operation
            raise BusinessError(
                IDEMPOTENCY_CONFLICT_CODE,
                f"idempotency key {key!r} is already in use",
            ) from exc

    def _check_budgets(
        self,
        conn: sqlite3.Connection,
        agent: Agent,
        *,
        cost_minor: int,
        tokens: int,
        action: str | None,
        kind: str,
        human_approved: bool,
    ) -> dict[str, int | None]:
        safety = agent.safety
        if tokens > safety.max_tokens_per_turn:
            raise BusinessError(
                AGENT_TOKEN_LIMIT_CODE,
                f"{tokens} tokens exceeds the per-turn limit of"
                f" {safety.max_tokens_per_turn}",
            )
        if cost_minor > safety.per_action_limit_minor:
            raise BusinessError(
                AGENT_BUDGET_EXCEEDED_CODE,
                f"{cost_minor} minor units exceeds the per-action limit of"
                f" {safety.per_action_limit_minor}",
            )
        requires_approval = bool(
            action
            and (
                action in safety.approval_required
                or safety.autonomy in APPROVAL_LEVELS
            )
        )
        if requires_approval and not human_approved:
            raise BusinessError(
                AGENT_APPROVAL_REQUIRED_CODE,
                f"action {action!r} at autonomy {safety.autonomy}"
                " needs a human approval",
            )
        stamp = now_iso()
        day = stamp[:10]
        month = stamp[:7]
        day_spent = int(
            conn.execute(
                "SELECT COALESCE(SUM(cost_minor), 0) FROM bos_agent_usage"
                " WHERE tenant_id=? AND agent_id=? AND day=?",
                (agent.tenant_id, agent.agent_id, day),
            ).fetchone()[0]
        )
        month_spent = int(
            conn.execute(
                "SELECT COALESCE(SUM(cost_minor), 0) FROM bos_agent_usage"
                " WHERE tenant_id=? AND agent_id=? AND month=?",
                (agent.tenant_id, agent.agent_id, month),
            ).fetchone()[0]
        )
        if day_spent + cost_minor > safety.daily_spend_minor:
            raise BusinessError(
                AGENT_BUDGET_EXCEEDED_CODE,
                f"daily spend {day_spent + cost_minor} exceeds the limit of"
                f" {safety.daily_spend_minor}",
            )
        if month_spent + cost_minor > safety.monthly_spend_minor:
            raise BusinessError(
                AGENT_BUDGET_EXCEEDED_CODE,
                f"monthly spend {month_spent + cost_minor} exceeds the limit"
                f" of {safety.monthly_spend_minor}",
            )
        turns_today = 0
        if kind == "turn":
            turns_today = int(
                conn.execute(
                    "SELECT COUNT(*) FROM bos_agent_usage"
                    " WHERE tenant_id=? AND agent_id=? AND kind='turn' AND day=?",
                    (agent.tenant_id, agent.agent_id, day),
                ).fetchone()[0]
            )
            if turns_today + 1 > safety.max_turns_per_day:
                raise BusinessError(
                    AGENT_TURN_LIMIT_CODE,
                    f"{turns_today + 1} turns today exceeds the limit of"
                    f" {safety.max_turns_per_day}",
                )
            recent = int(
                conn.execute(
                    "SELECT COUNT(*) FROM bos_agent_usage"
                    " WHERE tenant_id=? AND agent_id=? AND kind='turn'"
                    " AND created_at >= ?",
                    (
                        agent.tenant_id,
                        agent.agent_id,
                        now_iso(time.time() - 60.0),
                    ),
                ).fetchone()[0]
            )
            if recent >= safety.rate_limit_per_minute:
                raise BusinessError(
                    AGENT_RATE_LIMITED_CODE,
                    f"{recent} turns in the last minute reached the limit of"
                    f" {safety.rate_limit_per_minute}",
                )
        return {
            "daily_spend_remaining_minor": max(
                0, safety.daily_spend_minor - day_spent - cost_minor
            ),
            "monthly_spend_remaining_minor": max(
                0, safety.monthly_spend_minor - month_spent - cost_minor
            ),
            "turns_remaining_today": max(
                0, safety.max_turns_per_day - (turns_today if kind == "turn" else 0)
            ),
        }


def _redact_strings(value: Any) -> Any:
    """Scrub credential-shaped strings without masking numeric token counts.

    The shared ``redact`` masks a whole value when its *key* looks sensitive,
    which would turn ``max_tokens`` into a string. Operation snapshots need
    those numbers intact, so this only scrubs string leaves.
    """
    if isinstance(value, Mapping):
        return {str(key): _redact_strings(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact_strings(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


def _revoked_categories(scopes: Iterable[str] | None) -> list[str]:
    if scopes is None:
        return list(PERMISSION_CATEGORIES)
    if isinstance(scopes, str):
        scopes = [scopes]
    removed: list[str] = []
    for item in scopes:
        if not isinstance(item, str) or not item.strip():
            raise BusinessError(
                AGENT_UNKNOWN_SCOPE_CODE,
                "revoke scopes must be non-empty permission categories",
            )
        category = item.strip()
        if category not in PERMISSION_CATEGORIES:
            raise BusinessError(
                AGENT_UNKNOWN_SCOPE_CODE,
                f"unknown permission category {category!r}; expected one of"
                f" {', '.join(PERMISSION_CATEGORIES)}",
            )
        removed.append(category)
    unique = sorted(dict.fromkeys(removed))
    if not unique:
        raise BusinessError(
            AGENT_UNKNOWN_SCOPE_CODE,
            "revoke needs at least one category, or omit scopes to revoke all",
        )
    return unique


__all__ = [
    "AGENT_STATUSES",
    "AGENT_TRANSITIONS",
    "APPROVAL_ACTIONS",
    "APPROVAL_LEVELS",
    "AUTONOMY_LEVELS",
    "Agent",
    "AgentControlService",
    "AgentTurn",
    "AgentUsage",
    "PERMISSION_CATEGORIES",
    "Permissions",
    "REASONING_EFFORTS",
    "Safety",
]
