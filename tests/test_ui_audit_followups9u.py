"""UI-audit 2026-09-29 follow-ups (independent-verifier defects D1–D4).

D1 — ``/api/env`` advertised ``is_set:true`` for an ``env:``/``file:``
     reference whose target does not resolve. Root cause: the ``webui`` read
     boundary re-upgraded ``is_set`` from ``present_names`` (a registry row
     exists) instead of ``resolved_names`` (the credential resolves).
D2 — the pending-approvals banner settled a staged ``cron.create`` on the
     shared journal, but ``cron_create`` returned the job shape without an
     ``ok`` key, so ``dashboard_ops.confirm_compat_op`` reported
     ``BACKEND_REFUSED`` although the job WAS created (a false failure).
D3 — ``/model/info`` mixed the configured provider with a terminal session's
     stored model id from a different profile (``iv-endpoint`` + ``qubit-brain4``).
D4 — deleting a custom endpoint left its registered model profile + active
     pointer, so ``/model/info`` still targeted the deleted endpoint.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def test_d1_resolved_names_excludes_unresolvable_ref(tmp_path, monkeypatch):
    from voicebridge import env_store as E

    cfg = _cfg(tmp_path, monkeypatch)
    E.set_ref(cfg, "QA_ABSENT", "env:QA_FOLLOWUP_ABSENT_VAR")
    E.set_ref(cfg, "QA_PRESENT", "env:HOME")
    present = E.present_names(cfg)
    resolved = E.resolved_names(cfg)
    # The registry row exists for both ...
    assert "QA_ABSENT" in present and "QA_PRESENT" in present
    # ... but only the resolvable one is "set".
    assert "QA_ABSENT" not in resolved
    assert "QA_PRESENT" in resolved


def test_d2_cron_create_carries_ok_for_banner_settle(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    staged = HC.cron_create(
        cfg, {}, {"name": "qa-d2", "prompt": "t", "when": "every 30 minutes"},
        {})
    assert staged[0] == 409 and staged[1]["confirmation_id"], staged
    code, done = HC.cron_create(
        cfg, {},
        {"name": "qa-d2", "prompt": "t", "when": "every 30 minutes",
         "confirmation_id": staged[1]["confirmation_id"]}, {})
    assert code == 200, done
    # ``confirm_compat_op`` requires body["ok"]; without it the banner reported
    # BACKEND_REFUSED though the job was created.
    assert done.get("ok") is True, done


def test_d3_model_info_session_profile_supplies_provider(tmp_path, monkeypatch):
    from voicebridge import agent as AG
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC
    from voicebridge.providers import resolver as R

    cfg = _cfg(
        tmp_path, monkeypatch,
        "intent:\n  frontier:\n    enabled: true\n"
        "    allowlist: ['opencode-go/deepseek-v4-flash']\n")
    out = R.upsert_model_profile(cfg, R.ModelProfile(
        id="qa-endpoint", kind=R.API, provider="opencode-go",
        model="deepseek-v4-flash", base_url="https://opencode.ai/zen/go/v1",
        key_ref="env:OPENCODE_GO_API_KEY", locality="external",
        egress_consent="allow", roles=["chat"], context_tokens=200000),
        confirm=True)
    assert out.get("state") == "OK", out
    R.set_active_profile(cfg, "qa-endpoint", confirm=True)
    # A stale terminal session stores the shipped-default profile id instead.
    CT.store(cfg).ensure(AG.canonical_session_id(cfg), model="qubit-brain4")
    code, info = HC.h_model_info(cfg, {}, {}, {})
    assert code == 200, info
    assert info["model"] == "qubit-brain4"
    # The provider must match the model's own profile, never the endpoint's.
    assert info["provider"] == "ollama", info


def test_d4_delete_endpoint_clears_profile_and_active(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge.providers import endpoints as EP
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    body = {"id": "qa-del", "name": "qa-del",
            "base_url": "http://127.0.0.1:9/v1", "model": "m"}
    assert HC.h_custom_endpoints_write(cfg, {}, {
        **body, "confirm": True}, {})[0] == 200
    assert HC.h_custom_endpoints_activate(
        cfg, {}, {"confirm": True}, {"id": "qa-del"})[0] == 200
    assert R.get_profile(cfg, "qa-del") is not None

    code, out = HC.h_custom_endpoints_delete(
        cfg, {}, {"confirm": True}, {"id": "qa-del"})
    assert code == 200 and out["removed"] is True, out
    # The profile and the active pointer are gone: /model/info no longer
    # targets the deleted endpoint.
    assert R.get_profile(cfg, "qa-del") is None
    assert str(R.get_active_profile(cfg).get("active") or "") != "qa-del"
    code, info = HC.h_model_info(cfg, {}, {}, {})
    assert info.get("provider") != "qa-del", info
    # R1: the runtime config.yaml model slot no longer names the endpoint.
    import yaml

    from voicebridge import qubit_runtime

    rc_path = (Path(qubit_runtime.runtime_home_for_cfg(cfg)) / "config.yaml")
    if rc_path.is_file():
        data = yaml.safe_load(rc_path.read_text()) or {}
        node = data.get("model")
        assert not (isinstance(node, dict) and node.get("model") == "m4"), data
    # The cleanup helper is idempotent.
    assert EP.remove_endpoint(cfg, "qa-del")["profile_removed"] is False
