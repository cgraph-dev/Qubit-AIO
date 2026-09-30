"""Focused repair tests for the /models + /env findings (agent fix-models).

Covers the three fixable-in-module defects:

* custom-endpoint activation must apply the REAL runtime model (resolver
  profile registered + active + runtime ``config.yaml`` model), not a
  fabricated ``_active`` JSON flag;
* ``env_store`` must report ``is_set`` truth (a reference to an absent secret
  is not set) and reject malformed reference names;
* the shared ``_compat_confirm`` gate (which the qubit_compat custom-endpoint
  handlers are patched to use) refuses while the e-stop is latched and does not
  treat the string ``"false"``/``"0"`` as confirmation.

The qubit_compat.py handler edits are delivered as a patch spec
(``/tmp/opencode/qubit-audit-2026-09-29/patches/fix-models-qubit_compat.patch``)
because that file is coordinator-owned; these tests pin the behaviour those
handlers delegate to.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from voicebridge import config as C
from voicebridge import env_store as ES
from voicebridge.providers import endpoints as EP
from voicebridge.providers import resolver as R

#: The coordinator-owned qubit_compat.py edits are delivered as a patch spec.
#: When that patch is present (the audit workspace) the patched handlers are
#: loaded and exercised end-to-end; elsewhere these tests skip cleanly.
_PATCH = Path("/tmp/opencode/qubit-audit-2026-09-29/patches/"
              "fix-models-qubit_compat.patch")
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    data = {
        "logging": {"jsonl": True, "jsonl_path": str(state)},
        "intent": {"frontier": {"profile_overrides": {}}},
    }
    return C.Config(data=data, path=tmp_path / "voice.yaml")


# ---------------------------------------------------------------------------
# Defect 2 — endpoint activation applies the real model
# ---------------------------------------------------------------------------

def test_build_endpoint_profile_local_and_external():
    local = EP.build_endpoint_profile(
        "local-ep", {"base_url": "http://127.0.0.1:1234/v1", "model": "m"})
    assert local.kind == R.LOCAL and local.locality == "local"
    remote = EP.build_endpoint_profile(
        "remote-ep", {"base_url": "https://api.example.com/v1",
                      "model": "gpt-x", "key_ref": "env:EX_API_KEY"})
    assert remote.kind == R.API and remote.locality == "external"
    keyless = EP.build_endpoint_profile(
        "keyless", {"base_url": "https://api.example.com/v1", "model": "gpt-x"})
    assert keyless.kind == R.PLUGIN and keyless.locality == "external"


def test_activate_endpoint_applies_real_runtime_model(tmp_path):
    cfg = _cfg(tmp_path)
    record = {"base_url": "https://api.example.com/v1", "model": "gpt-x",
              "key_ref": "env:EX_API_KEY"}

    before = R.resolve_model(cfg)
    assert before["provider"] != "example"

    out = EP.activate_endpoint(cfg, "example-ep", record, confirm=True)
    assert out["ok"] is True, out
    assert out["active"] == "example-ep"

    # The resolver now reports the activated endpoint as the effective model.
    eff = R.resolve_model(cfg)
    assert eff["ok"] is True, eff
    assert eff["profile_id"] == "example-ep"
    assert eff["provider"] == "example-ep"
    assert eff["model"] == "gpt-x"

    # Persisted active selection survives a fresh registry read.
    assert R.get_active_profile(cfg)["active"] == "example-ep"
    registry = json.loads((cfg.state_dir() / "profiles.json").read_text())
    ids = [rec["id"] for rec in registry.get("model_profiles", [])]
    assert "example-ep" in ids

    # The runtime config.yaml model slot is mirrored (reference parity).
    runtime_cfg = (tmp_path / "state" / "runtime" / "config.yaml")
    assert runtime_cfg.is_file()
    assert "gpt-x" in runtime_cfg.read_text()


def test_activate_endpoint_confirm_false_is_dry_run(tmp_path):
    cfg = _cfg(tmp_path)
    out = EP.activate_endpoint(
        cfg, "dry", {"base_url": "https://api.example.com/v1", "model": "m"},
        confirm=False)
    assert out.get("dry_run") is True
    assert not (cfg.state_dir() / "profiles.json").exists()


def test_select_model_creates_and_activates(tmp_path):
    cfg = _cfg(tmp_path)
    out = EP.select_model(
        cfg, "opencode-go", "deepseek-v4-flash",
        base_url="https://api.opencode.example/v1", key_ref="env:OC_KEY")
    assert out["ok"] is True, out
    eff = R.resolve_model(cfg)
    assert eff["ok"] is True and eff["model"] == "deepseek-v4-flash"


def test_select_model_requires_a_real_endpoint(tmp_path):
    cfg = _cfg(tmp_path)
    out = EP.select_model(cfg, "openrouter", "some/model")
    assert out["ok"] is False
    assert out["code"] in ("MODEL_ENDPOINT_REQUIRED", "MODEL_CREDENTIAL_REQUIRED")
    assert R.get_active_profile(cfg)["state"] == "UNSET"


# ---------------------------------------------------------------------------
# Defect 3 — is_set truth + name validation live in env_store
# ---------------------------------------------------------------------------

def test_is_resolved_false_for_absent_secret(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.delenv("QA_FIX_ABSENT_TOKEN", raising=False)
    ES.set_ref(cfg, "QA_FIX_ABSENT", "env:QA_FIX_ABSENT_TOKEN")
    assert ES.is_resolved("env:QA_FIX_ABSENT_TOKEN") is False
    # The reference row exists but the credential does not resolve.
    assert "QA_FIX_ABSENT" not in ES.resolved_names(cfg)


def test_is_resolved_true_for_present_env(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("QA_FIX_PRESENT_TOKEN", "present")
    ES.set_ref(cfg, "QA_FIX_PRESENT", "env:QA_FIX_PRESENT_TOKEN")
    assert ES.is_resolved("env:QA_FIX_PRESENT_TOKEN") is True
    assert "QA_FIX_PRESENT" in ES.resolved_names(cfg)


def test_is_resolved_file_and_vault(tmp_path):
    secret = tmp_path / "secret.key"
    secret.write_text("value\n")
    assert ES.is_resolved(f"file:{secret}") is True
    assert ES.is_resolved(f"file:{tmp_path / 'missing.key'}") is False
    # No Qubit-side resolver for a vault entry: honest not-set.
    assert ES.is_resolved("vault:team/key") is False
    assert ES.is_resolved("ref:legacy") is False


def test_set_ref_rejects_malformed_name(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError):
        ES.set_ref(cfg, "bad name; DROP", "env:X")
    assert ES.valid_name("GOOD_NAME") is True
    assert ES.valid_name("bad-name") is False


# ---------------------------------------------------------------------------
# Defect 1 — the shared confirm gate the patched handlers will use
# ---------------------------------------------------------------------------

def test_compat_confirm_estop_and_truthiness(tmp_path):
    from voicebridge import power as PW
    from voicebridge import qubit_compat as QC

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    cfg = C.Config(data={"logging": {"jsonl": True, "jsonl_path": str(state)}},
                   path=tmp_path / "voice.yaml")

    # Latch the e-stop: a confirm-gated mutation must refuse before staging.
    # ``dashboard`` is the authenticated human channel the live power route uses.
    PW.power_control(cfg).emergency_stop(issued_by="dashboard")
    assert QC._estop(cfg) is True
    code, out = QC._compat_confirm(
        cfg, "custom_endpoints_write", "d", {}, {"confirm": True})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", (code, out)

    # Reset: the string "false"/"0" must NOT be read as confirmation.
    PW.power_control(cfg).reset(issued_by="dashboard")
    for value in ("false", "0", "", False, None):
        code, out = QC._compat_confirm(
            cfg, "custom_endpoints_write", "d", {}, {"confirm": value})
        assert code == 409, (value, code, out)
        assert out["code"] == "CONFIRM_REQUIRED"
        assert out["confirmation_id"], out
    # An explicit confirm proceeds (None = allowed).
    assert QC._compat_confirm(
        cfg, "custom_endpoints_write", "d", {}, {"confirm": True}) is None


# ---------------------------------------------------------------------------
# Patched qubit_compat handlers (the coordinator-owned file)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def patched_qc(tmp_path_factory):
    if _PATCH.is_file():
        work = tmp_path_factory.mktemp("patched")
        (work / "voicebridge").mkdir(parents=True)
        shutil.copy(_REPO_ROOT / "voicebridge" / "qubit_compat.py",
                    work / "voicebridge" / "qubit_compat.py")
        proc = subprocess.run(["patch", "-p1", "-i", str(_PATCH)], cwd=work,
                              capture_output=True, text=True)
        if proc.returncode == 0:
            spec = importlib.util.spec_from_file_location(
                "voicebridge.qubit_compat_patched",
                work / "voicebridge" / "qubit_compat.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        # The coordinator may have already applied the spec to the live tree;
        # in that case the copied source already carries the fix and ``patch``
        # has nothing to do. Only a genuinely unapplied spec is an error.
        already = "activate_endpoint" in (
            work / "voicebridge" / "qubit_compat.py").read_text()
        assert already, proc.stderr
    # The 2026-09-29 audit workspace is gone and its spec lives in the tree:
    # exercise the live handlers directly. A skip here hid 5 real tests; if
    # the live tree ever loses these handlers, fail loudly instead.
    from voicebridge import qubit_compat as live

    for _name in ("h_custom_endpoints_write", "h_custom_endpoints_activate",
                  "env_write", "h_env", "h_model_info", "h_model_set"):
        assert hasattr(live, _name), f"live qubit_compat lost {_name}"
    return live


def _patched_cfg(tmp_path):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return C.Config(
        data={"logging": {"jsonl": True, "jsonl_path": str(state)},
              "intent": {"frontier": {"profile_overrides": {}}}},
        path=tmp_path / "voice.yaml")


def test_patched_endpoint_write_estop_and_confirm(patched_qc, tmp_path):
    from voicebridge import power as PW

    cfg = _patched_cfg(tmp_path)
    body = {"id": "ep1", "name": "ep1",
            "base_url": "https://api.example.com/v1", "model": "m1"}

    # The string "false" must NOT execute a write.
    code, out = patched_qc.h_custom_endpoints_write(
        cfg, {}, {**body, "confirm": "false"}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert out["confirmation_id"], out
    assert not (cfg.state_dir() / "qubit_custom_endpoints.json").exists()

    # Under the e-stop the write refuses before persisting.
    PW.power_control(cfg).emergency_stop(issued_by="dashboard")
    code, out = patched_qc.h_custom_endpoints_write(
        cfg, {}, {**body, "confirm": True}, {})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", (code, out)
    PW.power_control(cfg).reset(issued_by="dashboard")

    # A real confirm persists and a bare stage now carries a usable id.
    code, out = patched_qc.h_custom_endpoints_write(
        cfg, {}, {**body, "confirm": True}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    stored = json.loads(
        (cfg.state_dir() / "qubit_custom_endpoints.json").read_text())
    assert stored["ep1"]["model"] == "m1"


def test_patched_endpoint_activate_applies_real_model(patched_qc, tmp_path):
    cfg = _patched_cfg(tmp_path)
    patched_qc.h_custom_endpoints_write(
        cfg, {}, {"id": "ep2", "base_url": "https://api.example.com/v1",
                  "model": "gpt-z", "confirm": True}, {})
    code, out = patched_qc.h_custom_endpoints_activate(
        cfg, {}, {"id": "ep2", "confirm": True}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    eff = R.resolve_model(cfg)
    assert eff["ok"] is True and eff["provider"] == "ep2"
    assert eff["model"] == "gpt-z"
    _, info = patched_qc.h_model_info(cfg, {}, {}, {})
    assert info["provider"] == "ep2" and info["model"] == "gpt-z"


def test_patched_env_is_set_truth(patched_qc, tmp_path, monkeypatch):
    cfg = _patched_cfg(tmp_path)
    monkeypatch.delenv("QA_PATCH_ABSENT", raising=False)
    patched_qc.env_write(
        cfg, {}, {"key": "QA_PATCH_ABSENT_REF",
                  "value": "env:QA_PATCH_ABSENT", "confirm": True}, {})
    _, env = patched_qc.h_env(cfg, {}, {}, {})
    assert env["QA_PATCH_ABSENT_REF"]["is_set"] is False

    monkeypatch.setenv("QA_PATCH_PRESENT", "present")
    patched_qc.env_write(
        cfg, {}, {"key": "QA_PATCH_PRESENT_REF",
                  "value": "env:QA_PATCH_PRESENT", "confirm": True}, {})
    _, env = patched_qc.h_env(cfg, {}, {}, {})
    assert env["QA_PATCH_PRESENT_REF"]["is_set"] is True


def test_patched_env_write_rejects_bad_name(patched_qc, tmp_path):
    cfg = _patched_cfg(tmp_path)
    code, out = patched_qc.env_write(
        cfg, {}, {"key": "bad name; DROP", "value": "env:X", "confirm": True},
        {})
    assert code == 400 and out["code"] == "ENV_NAME_INVALID", (code, out)


def test_patched_model_set_applies_catalog_pick(patched_qc, tmp_path):
    cfg = _patched_cfg(tmp_path)
    body = {"scope": "main", "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4",
            "base_url": "https://openrouter.ai/api/v1",
            "key_ref": "env:OPENROUTER_API_KEY"}
    code, out = patched_qc.h_model_set(cfg, {}, body, {})
    assert code == 200 and out.get("confirm_required") is True, (code, out)
    code, out = patched_qc.h_model_set(
        cfg, {}, {**body, "confirm_expensive_model": True}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    eff = R.resolve_model(cfg)
    assert eff["ok"] is True and eff["model"] == "anthropic/claude-sonnet-4"
    # Auxiliary pins stay a typed refusal.
    code, _ = patched_qc.h_model_set(
        cfg, {}, {"scope": "auxiliary", "model": "x"}, {})
    assert code == 400

