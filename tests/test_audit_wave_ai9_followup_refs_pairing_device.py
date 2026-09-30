"""Audit wave 2026-09-23 (AI9) follow-up — the four owned modules.

Covers the four MED/LOW items scoped to ``env_store``, ``mcp_servers`` /
``mcp_client``, ``pairing`` and ``device``:

* env ref kinds widened to the operator model (``env:``/``vault:``/``file:``,
  ``ref:`` alias) with per-kind shape validation and raw-secret refusal;
* MCP capabilities + auth-reference round-trip through the canonical store;
* pairing approve/revoke resolve a request id or code id, idempotently, and
  every mutation passes the emergency-stop gate;
* the device inventory/status/tokens surface, with token issue/rotate/revoke
  audited and never echoing a token value.

Hermetic: every test builds a ``Config`` whose state lives under ``tmp_path``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ---------------------------------------------------------------------------
# item 1 — env reference kinds
# ---------------------------------------------------------------------------
def test_env_store_accepts_env_vault_file_and_ref(tmp_path, monkeypatch):
    from voicebridge import env_store as ES

    cfg = _cfg(tmp_path, monkeypatch)
    ES.set_ref(cfg, "A_ENV", "env:OPENCODE_GO_API_KEY")
    ES.set_ref(cfg, "A_VAULT", "vault:team/opencode")
    ES.set_ref(cfg, "A_REF", "ref:legacy-name")
    ES.set_ref(cfg, "A_FILE", "file:/etc/qubit/secret.key")

    stored = ES.load(cfg)
    assert stored == {
        "A_ENV": "env:OPENCODE_GO_API_KEY",
        "A_VAULT": "vault:team/opencode",
        "A_REF": "ref:legacy-name",
        "A_FILE": "file:/etc/qubit/secret.key",
    }
    assert ES.reference_kind("env:X") == "env"
    assert ES.reference_kind("vault:X") == "vault"
    assert ES.reference_kind("ref:X") == "ref"
    assert ES.reference_kind("file:/x") == "file"
    assert ES.reference_kind("sk-live-value") == ""
    assert ES.is_resolvable_reference("env:X") is True
    assert ES.is_resolvable_reference("file:/x") is True
    assert ES.is_resolvable_reference("vault:X") is False


def test_env_store_refuses_raw_secret_and_bad_shapes(tmp_path, monkeypatch):
    from voicebridge import env_store as ES

    cfg = _cfg(tmp_path, monkeypatch)
    for value in (
        "sk-live-plaintext-secret",
        "",
        "env:1BAD_NAME",
        "env:",
        "file:relative/path",
        "file:/etc/../shadow",
        "vault:",
    ):
        try:
            ES.set_ref(cfg, "X", value)
        except ValueError as exc:
            assert "env:/vault:/file:" in str(exc) or "reference" in str(exc)
        else:
            raise AssertionError(f"accepted bad reference {value!r}")
    assert ES.load(cfg) == {}


def test_env_store_refuses_browser_private_credential_files(tmp_path, monkeypatch):
    from voicebridge import env_store as ES

    cfg = _cfg(tmp_path, monkeypatch)
    for path in ("file:/home/u/.config/google-chrome/Default/Login Data",
                 "file:/home/u/.mozilla/firefox/abc.default/cookies.sqlite",
                 "file:/home/u/.local/share/opencode/auth.json"):
        ok, detail = ES.validate_reference(path)
        assert ok is False, (path, detail)


# ---------------------------------------------------------------------------
# item 2 — MCP capabilities + auth reference round-trip
# ---------------------------------------------------------------------------
def test_mcp_capabilities_and_enabled_round_trip(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "srv", {"transport": "stdio", "command": "mcp",
                           "capabilities": ["echo", "ping"], "enabled": True})
    row = next(s for s in MS.list_servers(cfg)["servers"]
               if s["name"] == "srv")
    assert row["capabilities"] == ["echo", "ping"]
    assert row["enabled"] is True
    assert row["invocable"] is True
    assert row["invocation_code"] == MS.INVOKE_READY_CODE

    # Edit without restating the allowlist/enabled: both are preserved.
    MS.upsert(cfg, "srv", {"transport": "stdio", "command": "mcp2"})
    row = next(s for s in MS.list_servers(cfg)["servers"]
               if s["name"] == "srv")
    assert row["command"] == "mcp2"
    assert row["capabilities"] == ["echo", "ping"]
    assert row["enabled"] is True


def test_mcp_auth_reference_round_trip(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "srv", {"transport": "http", "url": "https://mcp.invalid"})

    out = MS.set_auth_ref(cfg, "srv", "env:MCP_TEST_TOKEN")
    assert out["reference_set"] is True and out["auth"] == "header"
    status = MS.auth_status(cfg, "srv")
    assert status["reference"] == "env:MCP_TEST_TOKEN"
    assert status["reference_set"] is True
    assert status["resolvable"] is True

    # The reference surfaces through the canonical list (never a value).
    row = next(s for s in MS.list_servers(cfg)["servers"]
               if s["name"] == "srv")
    assert (row["env"] or {}).get("MCP_AUTH") == "env:MCP_TEST_TOKEN"

    # vault: round-trips as an opaque reference but is honestly unresolvable.
    MS.set_auth_ref(cfg, "srv", "vault:external")
    assert MS.auth_status(cfg, "srv")["resolvable"] is False

    try:
        MS.set_auth_ref(cfg, "srv", "sk-raw-token-value")
    except MS.McpServerError as exc:
        assert exc.code == "SECRET_WRITE_REFUSED"
    else:
        raise AssertionError("raw token accepted")


def test_mcp_gate_requires_a_declared_allowlist(tmp_path, monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    cfg = _cfg(tmp_path, monkeypatch)
    MS.upsert(cfg, "srv", {"transport": "stdio", "command": "mcp",
                           "enabled": True})
    rec = MS._stored_record(cfg, "srv")
    assert MC.is_invocable(rec) is False
    with pytest.raises(MC.McpClientError) as ei:
        MC.check_invocable(rec, "echo")
    assert ei.value.code == "MCP_TOOL_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# item 3 — pairing request-id/code resolution, idempotency, e-stop
# ---------------------------------------------------------------------------
def _create(cfg, label="owner", scopes=("chat",), **kw):
    from voicebridge import pairing as PA

    kw.setdefault("confirmed", True)
    return PA.create(cfg, label, list(scopes), **kw)


def test_pairing_request_id_resolves_and_is_idempotent(tmp_path, monkeypatch):
    from voicebridge import pairing as PA

    cfg = _cfg(tmp_path, monkeypatch)
    created = _create(cfg)
    code_id = created["code_id"]
    assert code_id.startswith("pair_")
    assert PA.looks_like_request_id(code_id) is True

    listed = PA.list_codes(cfg)["codes"][0]
    assert listed["request_id"] == code_id
    assert listed["approved"] is False

    first = PA.approve(cfg, code_id, confirmed=True)
    assert first["approved"] == code_id
    assert first.get("already_approved") is not True
    assert listed and PA.list_codes(cfg)["codes"][0]["approved"] is True

    again = PA.approve(cfg, code_id, confirmed=True)
    assert again["already_approved"] is True
    assert again["grant_id"] == first["grant_id"], "no second grant minted"

    revoked = PA.revoke(cfg, code_id, confirmed=True)
    assert revoked["revoked"] == code_id
    again_revoke = PA.revoke(cfg, code_id, confirmed=True)
    assert again_revoke["already_revoked"] is True

    with pytest.raises(PA.PairingError) as ei:
        PA.approve(cfg, code_id, confirmed=True)
    assert ei.value.code == PA.REVOKED_CODE

    with pytest.raises(PA.PairingError) as ei:
        PA.approve(cfg, "pair_deadbeefdeadbeef", confirmed=True)
    assert ei.value.code == PA.NOT_FOUND_CODE


def test_pairing_still_accepts_the_one_time_code(tmp_path, monkeypatch):
    from voicebridge import pairing as PA

    cfg = _cfg(tmp_path, monkeypatch)
    created = _create(cfg, account="team")
    approved = PA.approve(cfg, created["code"], confirmed=True)
    assert approved["approved"] == created["code_id"]
    # Revoke by the same one-time code resolves the same record.
    assert PA.revoke(cfg, created["code"], confirmed=True)["revoked"] == \
        created["code_id"]


def test_pairing_mutations_are_estop_gated(tmp_path, monkeypatch):
    from voicebridge import pairing as PA
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    created = _create(cfg)
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="cli:user")
    try:
        assert PA.estop_engaged(cfg) is True
        for call in (
            lambda: PA.create(cfg, "x", ["chat"], confirmed=True),
            lambda: PA.approve(cfg, created["code"], confirmed=True),
            lambda: PA.revoke(cfg, created["code_id"], confirmed=True),
        ):
            with pytest.raises(PA.PairingError) as ei:
                call()
            assert ei.value.code == PA.ESTOP_CODE
    finally:
        power_control_store(cfg).set(emergency_stop=False,
                                     global_disable=False,
                                     issued_by="cli:user")


# ---------------------------------------------------------------------------
# item 4 — device inventory/status/tokens (audited, redacted)
# ---------------------------------------------------------------------------
def test_device_inventory_exposes_tokens_without_values(tmp_path, monkeypatch):
    from voicebridge import device as Dev

    cfg = _cfg(tmp_path, monkeypatch)
    issued = Dev.token_issue(cfg, device_id="dev-1", scopes=["chat"],
                             confirmed=True)
    assert issued["state"] == "OK"
    token_id = issued["token_id"]
    raw_token = issued["token"]
    assert raw_token and len(raw_token) > 20

    inv = Dev.inventory(cfg)
    assert inv["state"] == "OK" and inv["schema"] == "qubit-device-inventory/1"
    assert inv["enrolled"] is False
    assert inv["token_summary"]["total"] == 1
    assert inv["token_summary"]["pending"] == 1
    assert inv["tokens"][0]["id"] == token_id

    # No token value/verifier/salt anywhere in the read surface.
    blob = repr(inv) + repr(Dev.token_list(cfg)) + repr(Dev.status(cfg))
    assert raw_token not in blob
    for secret_key in ("verifier", "salt_b64", "'token'"):
        assert secret_key not in blob


def test_device_token_rotate_and_revoke_are_audited(tmp_path, monkeypatch):
    from voicebridge import device as Dev
    from voicebridge import logging_setup as LS

    cfg = _cfg(tmp_path, monkeypatch)
    events = []
    real = LS.log_event

    def _recorder(op, **kw):
        events.append(op)
        return real(op, **kw)

    monkeypatch.setattr(LS, "log_event", _recorder)

    issued = Dev.token_issue(cfg, device_id="dev-2", scopes=["chat"],
                             confirmed=True)
    assert "device.token.issue" in events

    rotated = Dev.token_rotate(cfg, issued["token_id"], confirmed=True)
    assert rotated["state"] == "OK"
    assert rotated["rotated_from"] == issued["token_id"]
    assert "device.token.rotate" in events

    revoked = Dev.token_revoke(cfg, rotated["token_id"], confirmed=True)
    assert revoked["state"] == "OK"
    assert "device.token.revoke" in events

    # The predecessor was tombstoned during rotation; both reads stay redacted.
    tokens = {t["id"]: t for t in Dev.token_list(cfg)["tokens"]}
    assert tokens[issued["token_id"]]["status"] == "revoked"
    assert tokens[rotated["token_id"]]["status"] == "revoked"


def test_device_token_ops_refuse_without_confirm_and_under_estop(tmp_path,
                                                                monkeypatch):
    from voicebridge import device as Dev
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    denied = Dev.token_issue(cfg, device_id="dev-3", scopes=["chat"],
                             confirmed=False)
    assert denied["state"] == "denied"
    assert denied["code"] == Dev.TOKEN_CONFIRM_CODE

    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="cli:user")
    try:
        blocked = Dev.token_issue(cfg, device_id="dev-3", scopes=["chat"],
                                  confirmed=True)
        assert blocked["state"] == "ESTOP"
        assert blocked["code"] == Dev.TOKEN_ESTOP_CODE
    finally:
        power_control_store(cfg).set(emergency_stop=False,
                                     global_disable=False,
                                     issued_by="cli:user")
