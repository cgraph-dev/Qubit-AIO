"""9U — OpenClaw ``users.*`` contractual-pin family.

Static behavior proof (the pinned-manifest checks) plus runtime proof against
the canonical single-operator authorities: the loopback operator identity
(``voicebridge.qubit_compat.h_auth_me``), the persisted dashboard preferences,
the credential-reference inventory (``voicebridge.auth_status``), the model
registry (``voicebridge.model_profiles_ops``), and the credential pool
(``voicebridge.credentials``).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import qubit_upgrade_manifest as OUM  # noqa: E402

USERS_MODULE = "voicebridge/gateway/qubit_handlers/users.py"

SAFE = {
    "users.list": "USERS_LIST_LOCAL_SINGLE_OPERATOR",
    "users.self": "USERS_SELF_LOCAL_OPERATOR",
    "users.mentionable": "USERS_MENTIONABLE_LOCAL_OPERATOR",
    "users.prefs.get": "USERS_PREFS_LOCAL_DASHBOARD",
    "users.prefs.set": "USERS_PREFS_LOCAL_DASHBOARD",
    "users.listAuthLinks": "USERS_AUTH_LINKS_LOCAL_CREDENTIALS",
    "users.listModelAccounts": "USERS_MODEL_ACCOUNTS_LOCAL_PROFILES",
    "users.selectModelAccount": "USERS_MODEL_ACCOUNT_LOCAL_SELECT",
    "users.linkAuthProfile": "USERS_LINK_AUTH_PROFILE_LOCAL_CREDENTIAL",
    "users.unlinkAuthProfile": "USERS_UNLINK_AUTH_PROFILE_LOCAL_CREDENTIAL",
    "users.authConnect.catalog": "USERS_AUTH_CONNECT_CATALOG_LOCAL_PROVIDERS",
}
OPS = {
    "users.github.status": "GITHUB_OPERATOR_REQUIRED",
    "users.github.authorize.start": "GITHUB_OPERATOR_REQUIRED",
    "users.github.authorize.poll": "GITHUB_OPERATOR_REQUIRED",
    "users.github.authorize.cancel": "GITHUB_OPERATOR_REQUIRED",
    "users.github.disconnect": "GITHUB_OPERATOR_REQUIRED",
    "users.authConnect.start": "USERS_AUTH_CONNECT_OPERATOR_REQUIRED",
    "users.authConnect.answer": "USERS_AUTH_CONNECT_OPERATOR_REQUIRED",
    "users.authConnect.status": "USERS_AUTH_CONNECT_OPERATOR_REQUIRED",
    "users.authConnect.cancel": "USERS_AUTH_CONNECT_OPERATOR_REQUIRED",
    "users.setAvatar": "USERS_AVATAR_OPERATOR_REQUIRED",
    "users.linkEmail": "USERS_EMAIL_OPERATOR_REQUIRED",
    "users.setDisplayName": "USERS_PROFILE_OPERATOR_REQUIRED",
    "users.setRole": "USERS_PROFILE_OPERATOR_REQUIRED",
}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _registry():
    from voicebridge.gateway.rpc import MethodRegistry

    mod = importlib.import_module("voicebridge.gateway.qubit_handlers.users")
    reg = MethodRegistry()
    mod.register(reg)
    return reg


# ------------------------------------------------------------- static proof
def test_users_family_static_proof():
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(sources)
    ours = {m for m, origin in origins.items() if origin.endswith(USERS_MODULE)}
    assert ours == set(SAFE) | set(OPS), ours
    for method in sorted(ours):
        code = SAFE.get(method) or OPS.get(method)
        assert code, method
        assert OUM._emits_code(methods[method], funcs, code, consts), (
            f"{method} never emits {code!r}")


def test_users_family_registry_is_complete():
    reg = _registry()
    assert set(reg.names()) == set(SAFE) | set(OPS), reg.names()


# --------------------------------------------------------- identity runtime
def test_users_identity_reads(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    listed = reg.handle(cfg, "users.list", {})
    assert listed["code"] == "USERS_LIST_LOCAL_SINGLE_OPERATOR", listed
    assert listed["count"] == 1, listed
    assert listed["profiles"][0]["id"] == "local-operator", listed

    self_out = reg.handle(cfg, "users.self", {})
    assert self_out["code"] == "USERS_SELF_LOCAL_OPERATOR", self_out
    assert self_out["profile"]["displayName"] == "Qubit Operator", self_out

    ment = reg.handle(cfg, "users.mentionable", {})
    assert ment["code"] == "USERS_MENTIONABLE_LOCAL_OPERATOR", ment
    assert ment["count"] == 1, ment


# --------------------------------------------------------- prefs runtime
def test_users_prefs_roundtrip(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    got = reg.handle(cfg, "users.prefs.get", {})
    assert got["code"] == "USERS_PREFS_LOCAL_DASHBOARD", got
    assert got["entries"]["font"] == "system", got

    seto = reg.handle(cfg, "users.prefs.set", {"entries": {"font": "mono"}})
    assert seto["code"] == "USERS_PREFS_LOCAL_DASHBOARD", seto
    assert seto["applied"]["font"] == "mono", seto

    again = reg.handle(cfg, "users.prefs.get", {})
    assert again["entries"]["font"] == "mono", again

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "users.prefs.set", {})
    assert exc.value.code == "GATEWAY_BAD_PARAMS", exc.value


# ------------------------------------------------- auth links / models runtime
def test_users_auth_links_and_model_accounts(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    links = reg.handle(cfg, "users.listAuthLinks", {})
    assert links["code"] == "USERS_AUTH_LINKS_LOCAL_CREDENTIALS", links
    assert isinstance(links["links"], list), links

    cat = reg.handle(cfg, "users.authConnect.catalog", {})
    assert cat["code"] == "USERS_AUTH_CONNECT_CATALOG_LOCAL_PROVIDERS", cat
    assert isinstance(cat["providers"], list), cat

    accounts = reg.handle(cfg, "users.listModelAccounts", {})
    assert accounts["code"] == "USERS_MODEL_ACCOUNTS_LOCAL_PROFILES", accounts
    assert accounts["count"] >= 1, accounts


def test_users_select_model_account(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    dry = reg.handle(cfg, "users.selectModelAccount",
                     {"session": "t", "authProfileId": "qubit-brain4"})
    assert dry["code"] == "USERS_MODEL_ACCOUNT_LOCAL_SELECT", dry
    assert dry.get("dry_run") is True, dry

    real = reg.handle(cfg, "users.selectModelAccount",
                      {"session": "t", "authProfileId": "qubit-brain4",
                       "confirm": True})
    assert real["code"] == "USERS_MODEL_ACCOUNT_LOCAL_SELECT", real
    assert real.get("action") == "selected", real

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "users.selectModelAccount", {})
    assert exc.value.code == "GATEWAY_BAD_PARAMS", exc.value


def test_users_link_unlink_auth_profile(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    linked = reg.handle(cfg, "users.linkAuthProfile",
                        {"provider": "openai", "keyRef": "env:OPENAI_KEY"})
    assert linked["counterpart_code"] == \
        "USERS_LINK_AUTH_PROFILE_LOCAL_CREDENTIAL", linked
    assert linked["state"] == "denied", linked

    unlinked = reg.handle(cfg, "users.unlinkAuthProfile",
                          {"provider": "openai", "index": 1})
    assert unlinked["counterpart_code"] == \
        "USERS_UNLINK_AUTH_PROFILE_LOCAL_CREDENTIAL", unlinked
    assert unlinked["state"] == "denied", unlinked

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "users.unlinkAuthProfile", {"provider": "openai"})
    assert exc.value.code == "GATEWAY_BAD_PARAMS", exc.value


# ------------------------------------------------------ operator-only runtime
def test_users_operator_only_refusals(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    for method, code in OPS.items():
        out = reg.handle(cfg, method, {})
        assert out["code"] == code, (method, out)
        assert out["state"] == "UNAVAILABLE", (method, out)
        assert out.get("operator_prerequisite"), (method, out)
        assert out.get("qubit_counterpart"), (method, out)
