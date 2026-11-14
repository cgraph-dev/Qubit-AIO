"""P1-07 — secret vault: refs, bindings, expiry, revocation and canary scans.

Hermetic: a throwaway vault root under ``tmp_path``; no network and no live
state or credentials.
"""
from __future__ import annotations

import os
import stat

import pytest

from voicebridge.business import secrets as vault_mod
from voicebridge.business.errors import BusinessError
from voicebridge.business.util import redact

TOOL = "browser"
OTHER_TOOL = "email"


def _vault(tmp_path):
    return vault_mod.SecretVault(tmp_path / "vault")


def _put(vault, *, value="s3cr3t-value-9f2a", tools=(TOOL,), ttl_seconds=None, now=None):
    return vault.put_secret(
        tenant_id="ten_1",
        owner_id="own_1",
        name="browser-login",
        value=value,
        tools=tools,
        ttl_seconds=ttl_seconds,
        now=now,
    )


def test_secret_value_never_appears_in_prompt_log_memory_workspace_or_artifact(tmp_path):
    vault = _vault(tmp_path)
    value = "canary-live-value-4b91c7"
    ref = _put(vault, value=value)
    canary = vault.canary(tenant_id="ten_1", now=1000.0)
    assert canary["kind"] == "canary"

    resolved = vault.resolve_for_tool(ref["ref"], TOOL, tenant_id="ten_1")
    assert resolved == value

    surfaces = {
        "prompt": f"Use this credential: {value}",
        "log": {"message": f"login with {value}", "level": "info"},
        "memory": {"note": [f"stored {value} for later"]},
        "workspace": "config: key=" + value,
        "artifact": {"stdout": f"token={value}"},
    }
    for name, payload in surfaces.items():
        scanned = vault.scan_payload(payload)
        assert value not in str(scanned), name
        assert vault_mod.REDACTED in str(scanned), name
        assert not vault.contains_secret(str(scanned)), name

    # The canary is a live vault value with only a ref/fingerprint exposed.
    assert vault.get_secret_ref(canary["ref"], tenant_id="ten_1")["fingerprint"]

    # The generic redactor still masks credential-shaped keys on the log path.
    masked = redact({"api_key": value, "message": "ok"})
    assert masked["api_key"] == "***REDACTED***"


def test_canary_value_is_redacted_and_never_listed(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    monkeypatch.setattr(vault_mod._secrets, "token_hex", lambda n: "deadbeef" * (n // 4))
    canary = vault.canary(tenant_id="ten_1")
    canary_value = "QUBIT-CANARY-" + "deadbeef" * 4

    listed = vault.list_secrets(tenant_id="ten_1")
    assert listed
    assert all("value" not in row for row in listed)
    assert any(row["kind"] == "canary" for row in listed)

    # The canary is not bound to a tool, so a tool cannot resolve it.
    with pytest.raises(BusinessError) as caught:
        vault.resolve_for_tool(canary["ref"], TOOL, tenant_id="ten_1")
    assert caught.value.code == vault_mod.SECRET_NOT_BOUND_CODE

    # A leaked canary is caught by the same exact-value scan as any secret.
    scanned = vault.scan_text(f"payload {canary_value} tail")
    assert scanned == "payload " + vault_mod.REDACTED + " tail"
    assert not vault.contains_secret(scanned)


def test_list_and_status_never_return_value_or_canary_secret(tmp_path):
    vault = _vault(tmp_path)
    ref = _put(vault)
    rows = vault.list_secrets()
    assert len(rows) == 1
    assert set(rows[0]) == {
        "ref", "tenant_id", "owner_id", "name", "kind", "fingerprint",
        "version", "tools", "created_at", "updated_at", "expires_at", "revoked",
    }
    status = vault.status(ref["ref"], tenant_id="ten_1")
    assert status["state"] == "active"
    assert "value" not in status
    assert status["fingerprint"].startswith("sha256:")


def test_a_tool_can_only_resolve_its_own_bound_secret(tmp_path):
    vault = _vault(tmp_path)
    ref = _put(vault, tools=(TOOL,))
    with pytest.raises(BusinessError) as caught:
        vault.resolve_for_tool(ref["ref"], OTHER_TOOL, tenant_id="ten_1")
    assert caught.value.code == vault_mod.SECRET_NOT_BOUND_CODE


def test_unbound_secret_cannot_be_resolved_by_any_tool(tmp_path):
    vault = _vault(tmp_path)
    ref = _put(vault, tools=())
    with pytest.raises(BusinessError) as caught:
        vault.resolve_for_tool(ref["ref"], TOOL, tenant_id="ten_1")
    assert caught.value.code == vault_mod.SECRET_NOT_BOUND_CODE


def test_expired_secret_is_refused_at_resolution(tmp_path):
    vault = _vault(tmp_path)
    ref = _put(vault, ttl_seconds=10, now=1000.0)
    assert vault.resolve_for_tool(ref["ref"], TOOL, tenant_id="ten_1", now=1005.0)
    with pytest.raises(BusinessError) as caught:
        vault.resolve_for_tool(ref["ref"], TOOL, tenant_id="ten_1", now=1010.0)
    assert caught.value.code == vault_mod.SECRET_EXPIRED_CODE
    assert vault.status(ref["ref"], tenant_id="ten_1", now=1010.0)["state"] == "expired"


def test_revoked_secret_is_refused_and_value_removed(tmp_path):
    vault = _vault(tmp_path)
    ref = _put(vault)
    vault.revoke(ref["ref"], tenant_id="ten_1")
    with pytest.raises(BusinessError) as caught:
        vault.resolve_for_tool(ref["ref"], TOOL, tenant_id="ten_1")
    assert caught.value.code == vault_mod.SECRET_REVOKED_CODE
    assert vault.status(ref["ref"], tenant_id="ten_1")["state"] == "revoked"
    assert not (tmp_path / "vault" / "secrets" / f"{ref['ref']}.value").exists()
    with pytest.raises(BusinessError):
        vault.rotate(ref["ref"], "new-value", tenant_id="ten_1")


def test_rotation_changes_fingerprint_and_version_but_not_ref(tmp_path):
    vault = _vault(tmp_path)
    ref = _put(vault)
    rotated = vault.rotate(ref["ref"], "brand-new-value", tenant_id="ten_1")
    assert rotated["ref"] == ref["ref"]
    assert rotated["version"] == 2
    assert rotated["fingerprint"] != ref["fingerprint"]
    assert vault.resolve_for_tool(ref["ref"], TOOL, tenant_id="ten_1") == "brand-new-value"


def test_tenant_scope_is_enforced(tmp_path):
    vault = _vault(tmp_path)
    ref = _put(vault)
    with pytest.raises(BusinessError) as caught:
        vault.get_secret_ref(ref["ref"], tenant_id="ten_other")
    assert caught.value.code == vault_mod.SECRET_NOT_FOUND_CODE
    assert vault.list_secrets(tenant_id="ten_other") == []


def test_vault_files_and_directories_are_private(tmp_path):
    vault = _vault(tmp_path)
    ref = _put(vault)
    root = tmp_path / "vault"
    secrets_dir = root / "secrets"
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(secrets_dir).st_mode) == 0o700
    for name in (f"{ref['ref']}.json", f"{ref['ref']}.value"):
        assert stat.S_IMODE(os.stat(secrets_dir / name).st_mode) == 0o600


def test_invalid_reference_is_a_typed_refusal(tmp_path):
    vault = _vault(tmp_path)
    for bad in ("", "not-a-ref", "../escape", "sec_/../x"):
        with pytest.raises(BusinessError) as caught:
            vault.get_secret_ref(bad)
        assert caught.value.code == vault_mod.SECRET_NOT_FOUND_CODE


def test_encoded_secret_variants_are_also_scanned(tmp_path):
    vault = _vault(tmp_path)
    value = "token/with+chars="
    _put(vault, value=value)
    leaked = "url?q=" + vault_mod.quote(value, safe="")
    assert value not in vault.scan_text(leaked)
    assert vault_mod.REDACTED in vault.scan_text(leaked)


def test_expired_secret_bytes_are_still_redacted(tmp_path):
    vault = _vault(tmp_path)
    value = "expired-but-sensitive-9911"
    _put(vault, value=value, ttl_seconds=1, now=1000.0)
    # Resolution is refused after expiry, but the stored bytes are still
    # sensitive and must never reappear in a payload.
    with pytest.raises(BusinessError):
        vault.resolve_for_tool(_put(vault, value=value, ttl_seconds=1, now=1000.0)["ref"],
                               TOOL, tenant_id="ten_1", now=1002.0)
    assert value not in vault.scan_text(f"leak {value}", now=1002.0)
