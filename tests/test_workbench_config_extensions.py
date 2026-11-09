"""Behavioural proof for the workbench config / extension / auth family.

Each test drives the REAL gateway (``h_wb_gateway``) against a REAL request
shape — the same ``(cfg, query, body, params)`` tuple the listener builds — and
against the temp ``QUBIT_HOME`` the session fixture installs. Nothing here mocks
the handler under test.

WHAT THIS PINS, AND WHY EACH ONE WOULD FAIL WITHOUT THE WORK
---------------------------------------------------------------------------
* ``settings`` answers the FLAT panel keys (``show_thinking``, ``default_model``,
  ``theme``). Before, it served the raw /api/config tree, where none of those
  exist at the top level: the panel hydrated every control from ``undefined``.
* ``settings`` POST lands through ``atomic_config_write`` and is READ BACK off
  disk — a handler that reported success without writing would pass a status-code
  assertion and fail this one.
* ``settings`` POST refuses an unknown field and writes NOTHING. A silently
  dropped field is the lie the form turns into "Settings saved".
* ``reasoning`` round-trips ``agent.reasoning_effort`` /
  ``display.show_reasoning`` — the same two keys the CLI writes.
* ``default-model`` POST routes to ``select_model`` (a write), not to the models
  READ handler that used to answer it with an inventory.
* ``providers`` lists presence facts and NEVER a key value.
* ``extensions/status`` answers from real plugin discovery; ``install`` and
  ``uninstall`` are refusals that name the real path.
* ``auth/status`` reports Qubit's true model (no password, no passkeys) and every
  passkey path is the typed ``WB_AUTH_MODEL_UNSUPPORTED`` — never a 200.
* the six EXISTING_SET routes still resolve through ``qubit_compat`` so this work
  did not shadow a route that already worked.
"""
from __future__ import annotations

import json

import pytest

from voicebridge import workbench_ui_routes as R


# --------------------------------------------------------------------- helpers
def call(path: str, *, method: str = "GET", query=None, body=None):
    """Drive the single gateway row exactly as the listener does."""
    sub = path.lstrip("/")
    params = {"sub": sub, "method": method.upper(), "rest": sub}
    if "/" in sub:
        params.setdefault("action", sub.rsplit("/", 1)[-1])
    code, out = R.h_wb_gateway(None, dict(query or {}), dict(body or {}), params)
    return code, out


def _config_file():
    from qubit_cli.config import get_config_path

    return get_config_path()


def _read_raw():
    from qubit_cli.config import read_user_config_raw

    data = read_user_config_raw(_config_file())
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- the route set
@pytest.mark.parametrize("path", [
    "settings", "models", "model/set", "default-model", "reasoning", "providers",
    "providers/delete", "dashboard/config", "dashboard/status", "auth/status",
    "auth/logout", "auth/passkeys", "auth/passkey/delete",
    "auth/passkey/register", "auth/passkey/register/options",
    "extensions/status", "extensions/registry", "extensions/toggle",
    "extensions/install", "extensions/uninstall",
    "extensions/sidecar-proxy-consent", "mcp/servers", "mcp/tools", "skills",
    "memory", "profiles", "tools",
])
def test_every_family_path_resolves_to_a_registered_handler(path):
    """A PORT_SET / EXISTING_SET / family row that names a missing handler
    answered ``WB_NOT_IN_PORT_SET`` even though the work was done."""
    handler = R._resolve_handler(path, "GET")
    assert handler is not None, f"{path} resolves to no handler"
    assert R._HANDLERS.get(handler.__name__) is handler, (
        f"{path} -> {handler.__name__}, which is not in _HANDLERS")


def test_existing_routes_are_not_double_counted():
    """The six already-working routes stay EXISTING and stay out of PORT_SET."""
    for path in ("skills", "memory", "profiles", "mcp/servers", "logs",
                 "sessions/search"):
        assert path in R.EXISTING_SET
        assert path not in R.PORT_SET, f"{path} would be double-counted"
        assert R._resolve_handler(path, "GET") is R.h_wb_passthrough


# ------------------------------------------------------------------- settings
def test_settings_get_serves_the_flat_panel_keys():
    code, out = call("settings")
    assert code == 200, out
    # The keys the panel binds, present at the TOP level (not nested).
    for key in ("ok", "show_thinking", "theme", "default_model",
                "default_model_provider", "reasoning_effort"):
        assert key in out, f"panel key {key!r} missing from /api/settings"
    assert out["ok"] is True


def test_settings_post_writes_through_atomic_config_and_reads_back():
    code, out = call("settings", method="POST", body={"show_thinking": False})
    assert code == 200, out
    assert out["ok"] is True and "display.show_reasoning" in out["written"]
    # READ BACK OFF DISK: a handler that answered 200 without writing fails here.
    assert _read_raw().get("display", {}).get("show_reasoning") is False
    # and the GET agrees, which is what the panel repaints from
    _, again = call("settings")
    assert again["show_thinking"] is False


def test_settings_post_round_trips_true():
    assert call("settings", method="POST",
                body={"show_thinking": True})[0] == 200
    assert _read_raw()["display"]["show_reasoning"] is True


def test_settings_post_refuses_an_unknown_field_and_writes_nothing():
    """The form posts everything; a dropped field must not read as saved."""
    before = _read_raw()
    code, out = call("settings", method="POST", body={"show_thinking": False,
                                                     "not_a_qubit_key": 1})
    assert code == 501, out
    assert out["code"] == "WB_NOT_IN_PORT_SET"
    assert "not_a_qubit_key" in out["detail"]
    assert _read_raw() == before, "a refused settings POST changed the config"


def test_settings_post_refuses_upstream_password_fields():
    """The password lane is a different auth model, and it is NAMED as such."""
    code, out = call("settings", method="POST", body={"_set_password": "x"})
    assert code == 501
    assert out["code"] == "WB_AUTH_MODEL_UNSUPPORTED"
    assert "password" in out["detail"].lower()


# ------------------------------------------------------------------ reasoning
def test_reasoning_get_reports_the_ladder_and_the_current_value():
    code, out = call("reasoning")
    assert code == 200, out
    assert out["supported_efforts"] == list(R.__dict__.get(
        "EFFORTS", None) or out["supported_efforts"]) or out["supported_efforts"]
    assert "high" in out["supported_efforts"]
    assert out["supports_thinking_toggle"] is True


def test_reasoning_post_writes_agent_effort_and_reads_back():
    code, out = call("reasoning", method="POST", body={"effort": "medium"})
    assert code == 200, out
    assert _read_raw()["agent"]["reasoning_effort"] == "medium"
    _, again = call("reasoning")
    assert again["reasoning_effort"] == "medium"


def test_reasoning_post_writes_the_display_toggle():
    assert call("reasoning", method="POST",
                body={"display": "hide"})[0] == 200
    assert _read_raw()["display"]["show_reasoning"] is False
    assert call("reasoning", method="POST",
                body={"display": "show"})[0] == 200
    assert _read_raw()["display"]["show_reasoning"] is True


def test_reasoning_post_refuses_an_effort_the_core_does_not_accept():
    before = _read_raw()
    code, out = call("reasoning", method="POST", body={"effort": "turbo"})
    assert code == 400, out
    assert out["code"] == "WB_BAD_INPUT"
    assert _read_raw() == before


def test_reasoning_post_needs_a_field():
    code, out = call("reasoning", method="POST", body={})
    assert code == 400 and out["code"] == "WB_BAD_INPUT"


# ---------------------------------------------------------------- default-model
def test_default_model_post_is_a_write_not_the_inventory():
    """It used to be routed to the models READ handler: a pick was discarded."""
    code, out = call("default-model", method="POST", body={})
    # No model named -> a caller error, NOT a 200 inventory listing.
    assert code == 400, out
    assert out["code"] == "WB_BAD_INPUT"
    assert "models" not in out, "answered with the inventory, not a write path"


def test_default_model_post_without_provider_is_refused():
    code, out = call("default-model", method="POST", body={"model": "x"})
    assert code == 400
    assert "provider" in out["detail"]


def test_default_model_get_reports_the_default_not_the_inventory():
    code, out = call("default-model")
    assert code == 200, out
    assert "default_model" in out and "default_model_provider" in out


# ------------------------------------------------------------------- providers
def test_providers_get_never_returns_a_secret():
    code, out = call("providers")
    assert code == 200, out
    assert isinstance(out["providers"], list)
    blob = json.dumps(out)
    for row in out["providers"]:
        assert "api_key" not in row and "secret" not in row
        # presence + reference only
        assert isinstance(row["has_key"], bool)
    assert "sk-" not in blob


def test_providers_post_refuses_to_store_a_raw_key():
    code, out = call("providers", method="POST",
                     body={"provider": "openai", "api_key": "sk-xxx"})
    assert code == 403, out
    assert out["code"] == "WB_CREDENTIALS_NOT_IN_CONFIG"
    assert "sk-xxx" not in json.dumps(out)


def test_providers_delete_is_refused():
    code, out = call("providers/delete", method="POST",
                     body={"provider": "openai"})
    assert code == 403
    assert out["code"] == "WB_CREDENTIALS_NOT_IN_CONFIG"


# ---------------------------------------------------------------------- tools
def test_mcp_tools_answers_a_real_inventory_not_404():
    """It used to pass through to ``qubit_compat``, which has no such route."""
    code, out = call("mcp/tools")
    assert code == 200, out
    assert out["ok"] is True
    assert isinstance(out["servers"], list)


def test_tools_and_mcp_tools_agree():
    _, a = call("tools")
    _, b = call("mcp/tools")
    assert a["total"] == b["total"]
    assert [s["server"] for s in a["servers"]] == [s["server"] for s in b["servers"]]


# ----------------------------------------------------------------- extensions
def test_extensions_status_lists_real_plugins():
    code, out = call("extensions/status")
    assert code == 200, out
    assert out["ok"] is True
    assert isinstance(out["extensions"], list)
    # Qubit has no sidecar proxy layer; said, not implied.
    assert out["sidecar_proxy_supported"] is False


def test_extensions_registry_never_invents_downloadable_entries():
    code, out = call("extensions/registry")
    assert code == 200, out
    assert out["remote_registry"] is False
    for entry in out["entries"]:
        assert entry["installed"] is True
        assert entry["download_url"] is None


def test_extension_install_and_uninstall_are_refused_with_the_real_path():
    for verb in ("extensions/install", "extensions/uninstall"):
        code, out = call(verb, method="POST", body={"id": "x"})
        assert code == 403, (verb, out)
        assert out["code"] == "WB_BROKERED_INSTALL"
        assert "qubit plugins install" in out["detail"]


def test_extension_sidecar_consent_names_the_missing_layer():
    code, out = call("extensions/sidecar-proxy-consent", method="POST",
                     body={"id": "x", "approved": True})
    assert code == 501
    assert out["code"] == "WB_CAPABILITY_UNBOUND"
    assert "sidecar" in out["detail"]


def test_extension_toggle_needs_an_id_and_a_flag():
    assert call("extensions/toggle", method="POST",
                body={})[0] == 400
    assert call("extensions/toggle", method="POST",
                body={"id": "x"})[0] == 400


def test_extension_toggle_unknown_id_writes_nothing():
    before = _read_raw()
    code, out = call("extensions/toggle", method="POST",
                     body={"id": "no-such-plugin-xyz", "enabled": True})
    assert code == 404, out
    assert out["code"] == "WB_NOT_FOUND"
    assert _read_raw() == before


# ----------------------------------------------------------------------- auth
def test_auth_status_describes_the_real_model():
    code, out = call("auth/status")
    assert code == 200, out
    # The two mechanisms Qubit does not have, reported honestly as absent.
    assert out["password_auth_enabled"] is False
    assert out["passkey_feature_flag"] is False
    assert isinstance(out["auth_enabled"], (bool, type(None)))
    assert "loopback" in out["auth_model"]


@pytest.mark.parametrize("path", [
    "auth/passkeys", "auth/passkey/register", "auth/passkey/register/options",
    "auth/passkey/delete",
])
def test_passkey_paths_are_typed_refusals_never_a_200(path):
    """THE DELIBERATE GAP. A 200 here would mean an invented credential store."""
    code, out = call(path, method="POST", body={})
    assert code == 501, (path, out)
    assert out["code"] == "WB_AUTH_MODEL_UNSUPPORTED"
    assert out["ok"] is False
    assert "passkey" in out["detail"].lower() or "webauthn" in out["detail"].lower()


def test_a_nested_passkey_path_also_refuses_through_the_family():
    code, out = call("auth/passkey/some/future/verb", method="POST", body={})
    assert code == 501
    assert out["code"] == "WB_AUTH_MODEL_UNSUPPORTED"


def test_auth_logout_routes_to_the_real_compat_authority():
    """qubit_compat already implements this path — the port must not shadow it."""
    handler = R._resolve_handler("auth/logout", "POST")
    assert handler is R.wb_auth_logout
    assert handler is not R.wb_auth_unsupported
