"""OpenClaw contractual-pin ``users.*`` family.

Qubit is a single-operator product: there is exactly one local operator
identity (the loopback session token) and no multi-user profile, role, email,
avatar, or per-user preference store. Every upstream ``users.*`` operation
therefore resolves either to the real single-operator counterpart in a
canonical Qubit module, or to a typed OPERATOR_ONLY refusal when it needs an
external service/credential Qubit does not run. Nothing is fabricated.

SAFE_EQUIVALENT — real local counterpart exists; the contract differs from
upstream's multi-user store:

* ``users.list`` / ``users.self`` — the one canonical loopback operator
  identity (``voicebridge.qubit_compat.h_auth_me``, the same identity the
  dashboard auth widget shows).
* ``users.mentionable`` — the same single mentionable operator; Qubit has no
  multi-user mention directory.
* ``users.prefs.get`` / ``users.prefs.set`` — the real persisted dashboard
  preference store (``voicebridge.qubit_compat`` font/theme handlers) instead
  of an arbitrary per-user key/value profile.
* ``users.listAuthLinks`` — the canonical credential-reference inventory
  (``voicebridge.auth_status.auth_status``).
* ``users.listModelAccounts`` — the canonical model-profile registry
  (``voicebridge.model_profiles_ops.list_model_profiles``).
* ``users.selectModelAccount`` — the canonical session->profile selection
  (``voicebridge.model_profiles_ops.select_session_model``, confirm-gated).
* ``users.linkAuthProfile`` / ``users.unlinkAuthProfile`` — the canonical
  credential-pool add/remove (``voicebridge.credentials.add_reference`` /
  ``remove``, confirm-gated, references only).
* ``users.authConnect.catalog`` — the configured provider registry
  (``voicebridge.providers.policy.FrontierPolicy.from_config``).

OPERATOR_ONLY — needs a live external service/credential Qubit does not run:

* ``users.github.*`` — the GitHub device-flow OAuth against github.com.
* ``users.authConnect.start|answer|status|cancel`` — an external provider
  OAuth connect dance.
* ``users.setAvatar`` — no local avatar/media store.
* ``users.linkEmail`` — no email identity service.
* ``users.setDisplayName`` / ``users.setRole`` — no multi-user profile/role
  store.

Refusal handlers intentionally perform no backend call: they emit their typed
code and name the exact prerequisite plus the real local path used instead.
"""
from __future__ import annotations

from typing import Any, Dict

from ..gateway_ws import GatewayError

#: Canonical bad-params code shared by every family.
USERS_BAD_PARAMS = "GATEWAY_BAD_PARAMS"

#: SAFE_EQUIVALENT typed codes (real single-operator counterpart).
USERS_LIST_LOCAL_SINGLE_OPERATOR = "USERS_LIST_LOCAL_SINGLE_OPERATOR"
USERS_SELF_LOCAL_OPERATOR = "USERS_SELF_LOCAL_OPERATOR"
USERS_MENTIONABLE_LOCAL_OPERATOR = "USERS_MENTIONABLE_LOCAL_OPERATOR"
USERS_PREFS_LOCAL_DASHBOARD = "USERS_PREFS_LOCAL_DASHBOARD"
USERS_AUTH_LINKS_LOCAL_CREDENTIALS = "USERS_AUTH_LINKS_LOCAL_CREDENTIALS"
USERS_MODEL_ACCOUNTS_LOCAL_PROFILES = "USERS_MODEL_ACCOUNTS_LOCAL_PROFILES"
USERS_MODEL_ACCOUNT_LOCAL_SELECT = "USERS_MODEL_ACCOUNT_LOCAL_SELECT"
USERS_LINK_AUTH_PROFILE_LOCAL_CREDENTIAL = \
    "USERS_LINK_AUTH_PROFILE_LOCAL_CREDENTIAL"
USERS_UNLINK_AUTH_PROFILE_LOCAL_CREDENTIAL = \
    "USERS_UNLINK_AUTH_PROFILE_LOCAL_CREDENTIAL"
USERS_AUTH_CONNECT_CATALOG_LOCAL_PROVIDERS = \
    "USERS_AUTH_CONNECT_CATALOG_LOCAL_PROVIDERS"

#: OPERATOR_ONLY typed codes (external prerequisite absent).
GITHUB_OPERATOR_REQUIRED = "GITHUB_OPERATOR_REQUIRED"
USERS_AUTH_CONNECT_OPERATOR_REQUIRED = "USERS_AUTH_CONNECT_OPERATOR_REQUIRED"
USERS_AVATAR_OPERATOR_REQUIRED = "USERS_AVATAR_OPERATOR_REQUIRED"
USERS_EMAIL_OPERATOR_REQUIRED = "USERS_EMAIL_OPERATOR_REQUIRED"
USERS_PROFILE_OPERATOR_REQUIRED = "USERS_PROFILE_OPERATOR_REQUIRED"


# ------------------------------------------------------------------ helpers
def _users_str(params: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _users_bool(params: Dict[str, Any]) -> bool:
    value = params.get("confirm", params.get("confirmed", False))
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "confirm")
    return False


def _operator_profile(cfg: Any) -> Dict[str, Any]:
    """The one canonical loopback operator identity, in profile shape."""
    from ...qubit_compat import h_auth_me

    _status, body = h_auth_me(cfg, {}, {}, {})
    uid = str(body.get("user_id") or "local-operator")
    return {
        "id": uid,
        "userId": uid,
        "displayName": str(body.get("display_name") or "Qubit Operator"),
        "email": str(body.get("email") or ""),
        "role": "operator",
        "orgId": str(body.get("org_id") or "local"),
        "provider": str(body.get("provider") or "loopback"),
        "mode": str(body.get("mode") or "loopback-token"),
        "authenticated": bool(body.get("authenticated", True)),
    }


def _refusal(code: str, detail: str, prerequisite: str,
             counterpart: str, **extra: Any) -> Dict[str, Any]:
    out = {"state": "UNAVAILABLE", "code": code, "detail": detail,
           "operator_prerequisite": prerequisite,
           "qubit_counterpart": counterpart}
    out.update(extra)
    return out


def _mark(out: Dict[str, Any], code: str) -> Dict[str, Any]:
    """Tag a canonical result with its local-counterpart code."""
    out.setdefault("code", code)
    out.setdefault("counterpart_code", code)
    return out


# ------------------------------------------------- single-operator identity
def _users_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "OK", "code": USERS_LIST_LOCAL_SINGLE_OPERATOR,
            "profiles": [_operator_profile(cfg)], "count": 1,
            "qubit_counterpart": ("voicebridge.qubit_compat.h_auth_me "
                                  "(single loopback operator)"),
            "authority": "voicebridge.qubit_compat.h_auth_me"}


def _users_self(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "OK", "code": USERS_SELF_LOCAL_OPERATOR,
            "profile": _operator_profile(cfg),
            "authenticated": True,
            "qubit_counterpart": ("voicebridge.qubit_compat.h_auth_me "
                                  "(loopback session identity)"),
            "authority": "voicebridge.qubit_compat.h_auth_me"}


def _users_mentionable(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "OK", "code": USERS_MENTIONABLE_LOCAL_OPERATOR,
            "mentionable": [_operator_profile(cfg)], "count": 1,
            "qubit_counterpart": ("voicebridge.qubit_compat.h_auth_me "
                                  "(the one mentionable operator)"),
            "authority": "voicebridge.qubit_compat.h_auth_me"}


# ------------------------------------------------------------ preferences
def _users_prefs_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...qubit_compat import h_font, h_themes

    _c1, font = h_font(cfg, {}, {}, {})
    _c2, themes = h_themes(cfg, {}, {}, {})
    entries: Dict[str, Any] = {
        "font": str(font.get("font") or "system"),
        "theme": str(themes.get("active") or "qubit"),
    }
    keys = params.get("keys")
    if isinstance(keys, list) and keys:
        wanted = {str(k) for k in keys}
        entries = {k: v for k, v in entries.items() if k in wanted}
    return {"state": "OK", "status": "ok", "code": USERS_PREFS_LOCAL_DASHBOARD,
            "entries": entries,
            "qubit_counterpart": ("voicebridge.qubit_compat persisted dashboard "
                                  "preferences (font/theme)"),
            "authority": "voicebridge.qubit_compat"}


def _users_prefs_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...qubit_compat import h_font, h_theme_set

    entries = params.get("entries")
    if not isinstance(entries, dict):
        raise GatewayError(USERS_BAD_PARAMS,
                           "users.prefs.set requires an 'entries' object")
    applied: Dict[str, Any] = {}
    if "font" in entries:
        _c, out = h_font(cfg, {}, {"font": entries.get("font")}, {},
                         setter=True)
        applied["font"] = out.get("font")
    if "theme" in entries:
        _c, out = h_theme_set(cfg, {}, {"theme": entries.get("theme")}, {})
        applied["theme"] = out.get("theme")
    return {"state": "OK", "status": "ok", "code": USERS_PREFS_LOCAL_DASHBOARD,
            "applied": applied,
            "unsupported": sorted(set(entries) - {"font", "theme"}),
            "qubit_counterpart": ("voicebridge.qubit_compat persisted dashboard "
                                  "preferences (font/theme only)"),
            "authority": "voicebridge.qubit_compat"}


# ------------------------------------------------------- auth links / models
def _users_list_auth_links(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ...auth_status import auth_status

    status = auth_status(cfg)
    providers = status.get("providers", [])
    return {"state": "OK", "code": USERS_AUTH_LINKS_LOCAL_CREDENTIALS,
            "links": providers, "count": len(providers),
            "envReferences": status.get("env_references", []),
            "qubit_counterpart": ("voicebridge.auth_status.auth_status "
                                  "(credential references, names only)"),
            "authority": "voicebridge.auth_status.auth_status"}


def _users_list_model_accounts(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    from ...model_profiles_ops import list_model_profiles

    out = list_model_profiles(cfg)
    profiles = out.get("profiles", [])
    return {"state": "OK", "code": USERS_MODEL_ACCOUNTS_LOCAL_PROFILES,
            "accounts": profiles, "count": len(profiles),
            "active": out.get("active", ""),
            "qubit_counterpart": ("voicebridge.model_profiles_ops."
                                  "list_model_profiles"),
            "authority": "voicebridge.model_profiles_ops.list_model_profiles"}


def _users_select_model_account(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    from ...model_profiles_ops import select_session_model

    session = _users_str(params, "session", "sessionKey", "session_key")
    profile_id = _users_str(params, "authProfileId", "profileId", "profile_id",
                            "accountId", "modelAccountId")
    if not session or not profile_id:
        raise GatewayError(USERS_BAD_PARAMS,
                           "users.selectModelAccount requires 'session' and "
                           "'authProfileId'")
    out = select_session_model(cfg, session, profile_id,
                               confirm=_users_bool(params))
    out = _mark(out, USERS_MODEL_ACCOUNT_LOCAL_SELECT)
    out["qubit_counterpart"] = ("voicebridge.model_profiles_ops."
                                "select_session_model (confirm-gated)")
    out["authority"] = "voicebridge.model_profiles_ops.select_session_model"
    return out


def _users_link_auth_profile(cfg: Any,
                             params: Dict[str, Any]) -> Dict[str, Any]:
    from ...credentials import add_reference

    provider = _users_str(params, "provider")
    key_ref = _users_str(params, "keyRef", "key_ref", "ref", "credentialRef",
                         "authProfileId")
    if not provider or not key_ref:
        raise GatewayError(USERS_BAD_PARAMS,
                           "users.linkAuthProfile requires 'provider' and a "
                           "credential reference (env:/file:)")
    out = add_reference(cfg, provider, key_ref,
                        label=_users_str(params, "label"),
                        confirmed=_users_bool(params),
                        requested_by="gateway:users")
    out = _mark(out, USERS_LINK_AUTH_PROFILE_LOCAL_CREDENTIAL)
    out["qubit_counterpart"] = ("voicebridge.credentials.add_reference "
                                "(confirm-gated; references only)")
    out["authority"] = "voicebridge.credentials.add_reference"
    return out


def _users_unlink_auth_profile(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    from ...credentials import remove

    provider = _users_str(params, "provider")
    index = params.get("index", params.get("entryIndex", params.get("idx")))
    if not provider or index is None:
        raise GatewayError(USERS_BAD_PARAMS,
                           "users.unlinkAuthProfile requires 'provider' and "
                           "'index'")
    out = remove(cfg, provider, index,
                 confirmed=_users_bool(params),
                 requested_by="gateway:users")
    out = _mark(out, USERS_UNLINK_AUTH_PROFILE_LOCAL_CREDENTIAL)
    out["qubit_counterpart"] = ("voicebridge.credentials.remove "
                                "(confirm-gated; references only)")
    out["authority"] = "voicebridge.credentials.remove"
    return out


def _users_auth_connect_catalog(cfg: Any,
                                params: Dict[str, Any]) -> Dict[str, Any]:
    from ...auth_login import portal_url
    from ...providers.policy import FrontierPolicy

    providers = FrontierPolicy.from_config(cfg).providers
    rows = []
    for name in sorted(providers):
        spec = providers.get(name) or {}
        rows.append({"provider": name,
                     "keyRef": str(spec.get("key_ref", "") or ""),
                     "portalUrl": portal_url(name)})
    return {"state": "OK", "code": USERS_AUTH_CONNECT_CATALOG_LOCAL_PROVIDERS,
            "providers": rows, "count": len(rows),
            "qubit_counterpart": ("voicebridge.providers.policy.FrontierPolicy "
                                  "configured providers + `qubit auth login`"),
            "authority": "voicebridge.providers.policy.FrontierPolicy"}


# --------------------------------------------------------- GitHub (operator)
def _users_github_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _refusal(
        GITHUB_OPERATOR_REQUIRED,
        "Qubit stores credential references only and runs no GitHub OAuth/app "
        "service, so no personal GitHub connection status is fetched.",
        "a GitHub credential reference (env:/file:) with the required scopes "
        "and network access",
        "CLI `qubit auth status` + an operator-managed credential reference")


def _users_github_authorize_start(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    return _refusal(
        GITHUB_OPERATOR_REQUIRED,
        "the GitHub device-flow authorization needs a live github.com OAuth "
        "app and network access; Qubit starts no device flow.",
        "a live GitHub OAuth app/device-flow service and network access",
        "CLI `qubit auth login --provider github` (manual handoff in the "
        "operator's browser)")


def _users_github_authorize_poll(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    return _refusal(
        GITHUB_OPERATOR_REQUIRED,
        "no GitHub device-flow authorization is in flight; Qubit polls no "
        "external OAuth service.",
        "a started GitHub device-flow authorization (requires the live OAuth "
        "app)",
        "CLI `qubit auth verify --provider github` (reference presence only)")


def _users_github_authorize_cancel(cfg: Any,
                                   params: Dict[str, Any]) -> Dict[str, Any]:
    return _refusal(
        GITHUB_OPERATOR_REQUIRED,
        "no GitHub device-flow authorization is in flight to cancel.",
        "a started GitHub device-flow authorization (requires the live OAuth "
        "app)",
        "nothing to cancel locally; remove the credential reference with the "
        "operator's own tooling")


def _users_github_disconnect(cfg: Any,
                             params: Dict[str, Any]) -> Dict[str, Any]:
    return _refusal(
        GITHUB_OPERATOR_REQUIRED,
        "Qubit holds no personal GitHub connection to disconnect.",
        "a linked GitHub account (requires the live OAuth app)",
        "remove the credential reference through the canonical credential "
        "store (`voicebridge.credentials.remove`, confirm-gated)")


# ------------------------------------------------- auth connect (operator)
def _users_auth_connect_refusal(method: str, detail: str) -> Dict[str, Any]:
    return _refusal(
        USERS_AUTH_CONNECT_OPERATOR_REQUIRED, detail,
        "a live external provider OAuth/connect service and network access",
        "CLI `qubit auth login --provider <name>` (manual handoff; Qubit "
        "never performs the OAuth dance)", method=method)


def _users_auth_connect_start(cfg: Any,
                              params: Dict[str, Any]) -> Dict[str, Any]:
    return _users_auth_connect_refusal(
        "users.authConnect.start",
        "starting an external provider auth-connect session needs a live "
        "OAuth service; Qubit starts none.")


def _users_auth_connect_answer(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    return _users_auth_connect_refusal(
        "users.authConnect.answer",
        "no external auth-connect session exists to answer.")


def _users_auth_connect_status(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    return _users_auth_connect_refusal(
        "users.authConnect.status",
        "no external auth-connect session exists to report.")


def _users_auth_connect_cancel(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    return _users_auth_connect_refusal(
        "users.authConnect.cancel",
        "no external auth-connect session exists to cancel.")


# ------------------------------------------- profile/identity (operator-only)
def _users_set_avatar(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _refusal(
        USERS_AVATAR_OPERATOR_REQUIRED,
        "Qubit keeps no operator avatar/media store and hosts no avatar "
        "media, so no avatar is written.",
        "an operator-managed avatar/media store and hosting",
        "the local device identity (`voicebridge.device.profile`); no avatar "
        "field exists")


def _users_link_email(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _refusal(
        USERS_EMAIL_OPERATOR_REQUIRED,
        "Qubit has no email identity service and stores no operator email, so "
        "no email is linked.",
        "an email/identity verification service",
        "the loopback operator identity (`voicebridge.qubit_compat.h_auth_me`; "
        "email is empty)")


def _users_set_display_name(cfg: Any,
                            params: Dict[str, Any]) -> Dict[str, Any]:
    return _refusal(
        USERS_PROFILE_OPERATOR_REQUIRED,
        "Qubit is single-operator with no mutable multi-user profile store, so "
        "no display name is written.",
        "a multi-user profile store (Qubit is single-operator)",
        "the fixed loopback operator display name "
        "(`voicebridge.qubit_compat.h_auth_me`)")


def _users_set_role(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _refusal(
        USERS_PROFILE_OPERATOR_REQUIRED,
        "Qubit has no multi-user role store; operator scopes are fixed, so no "
        "role is assigned.",
        "a multi-user profile/role store (Qubit is single-operator)",
        "the fixed operator scopes advertised by the gateway `connect` "
        "handshake")


# --------------------------------------------------------------- register
def register(registry: Any) -> None:
    """Register the ``users.*`` handlers on *registry*."""
    registry.register("users.list", _users_list)
    registry.register("users.self", _users_self)
    registry.register("users.mentionable", _users_mentionable)
    registry.register("users.prefs.get", _users_prefs_get)
    registry.register("users.prefs.set", _users_prefs_set)
    registry.register("users.listAuthLinks", _users_list_auth_links)
    registry.register("users.listModelAccounts", _users_list_model_accounts)
    registry.register("users.selectModelAccount", _users_select_model_account)
    registry.register("users.linkAuthProfile", _users_link_auth_profile)
    registry.register("users.unlinkAuthProfile", _users_unlink_auth_profile)
    registry.register("users.authConnect.catalog",
                      _users_auth_connect_catalog)
    registry.register("users.github.status", _users_github_status)
    registry.register("users.github.authorize.start",
                      _users_github_authorize_start)
    registry.register("users.github.authorize.poll",
                      _users_github_authorize_poll)
    registry.register("users.github.authorize.cancel",
                      _users_github_authorize_cancel)
    registry.register("users.github.disconnect", _users_github_disconnect)
    registry.register("users.authConnect.start", _users_auth_connect_start)
    registry.register("users.authConnect.answer", _users_auth_connect_answer)
    registry.register("users.authConnect.status", _users_auth_connect_status)
    registry.register("users.authConnect.cancel", _users_auth_connect_cancel)
    registry.register("users.setAvatar", _users_set_avatar)
    registry.register("users.linkEmail", _users_link_email)
    registry.register("users.setDisplayName", _users_set_display_name)
    registry.register("users.setRole", _users_set_role)
